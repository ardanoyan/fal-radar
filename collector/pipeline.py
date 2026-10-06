"""One collector run: discover, enrich, classify, write. Nothing is written until the end.

A run is identified by its UTC date. Every response fetched during a run is cached
under that id, so running again with --resume replays what was already fetched and
only spends the rate limit on what is missing.
"""

from __future__ import annotations

import datetime as dt
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import __version__, classify, config, runlog, store
from .cache import DiskCache
from .enrich import fetch_owners, fetch_repos
from .github import GitHubClient
from .models import default_index
from .queries import ALL_QUERIES, API_FACTS, CODE_QUERIES, Query, is_doc_path, verify
from .schema import Owner, Repo, StarsPoint
from .search import code_search

MAX_UNKNOWN_IDS_IN_SUMMARY = 200


class NothingToResume(RuntimeError):
    pass


@dataclass
class RunOptions:
    only: list[str] = field(default_factory=list)
    resume: bool = False
    offline: bool = False
    skip_owners: bool = False
    limit_repos: int | None = None


@dataclass
class _Hit:
    repo_id: int
    full_name: str
    sources: set[str] = field(default_factory=set)
    clients: set[str] = field(default_factory=set)
    tiers: set[str] = field(default_factory=set)
    fragments: list[str] = field(default_factory=list)
    files: int = 0


def select_queries(only: list[str]) -> list[Query]:
    if not only:
        return list(CODE_QUERIES)
    unknown = [q for q in only if q not in ALL_QUERIES]
    if unknown:
        raise ValueError(f"unknown query id: {', '.join(unknown)}. See `collector queries`.")
    chosen = [ALL_QUERIES[q] for q in only]
    not_yet = [q.id for q in chosen if q.kind != "code"]
    if not_yet:
        raise ValueError(f"repository search is not wired up yet (Phase 1): {', '.join(not_yet)}")
    return chosen


def _clean(value: Any) -> str | None:
    text = (value or "").strip() if isinstance(value, str) else ""
    return text or None


def _owner_from(repo_data: dict, profile: dict | None, previous: Owner | None) -> Owner:
    base = repo_data.get("owner") or {}
    name = _clean(profile.get("name")) if profile else (previous.name if previous else None)
    location = (
        _clean(profile.get("location")) if profile else (previous.location if previous else None)
    )
    return Owner(
        login=base.get("login", ""),
        type=base.get("type", "User"),
        name=name,
        location=location,
        avatar_url=base.get("avatar_url", ""),
        html_url=base.get("html_url", ""),
    )


def _history(previous: list[StarsPoint], date: str, stars: int) -> list[StarsPoint]:
    points = [p for p in previous if p.date != date]
    points.append(StarsPoint(date=date, stars=stars))
    return sorted(points, key=lambda p: p.date)


FAL_OWNERS = {"fal-ai", "fal-ai-community"}


def _from_fal_template(repo: Repo) -> bool:
    for source in (repo.fork_source, repo.template_source):
        if source and source.split("/", 1)[0].lower() in FAL_OWNERS:
            return True
    return False


def is_headline(repo: Repo) -> bool:
    """Counts toward the headline: fal in the code, not a fork, not a copy of a fal template."""
    return repo.evidence == "code" and not repo.fork and not _from_fal_template(repo)


def run(
    paths: config.Paths,
    options: RunOptions,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
    log: Callable[[str], None] = print,
    run_date: str | None = None,
) -> dict[str, Any]:
    queries = select_queries(options.only)

    if options.resume:
        run_id = runlog.latest_unfinished(paths.runs)
        if run_id is None:
            raise NothingToResume("no unfinished run log under data/runs/ to resume")
    else:
        run_id = run_date or runlog.today_utc()
    today = dt.date.fromisoformat(run_id)

    journal = runlog.RunLog(paths.runs, run_id)
    journal.event(
        "run_started",
        run_id=run_id,
        collector=__version__,
        queries=[q.id for q in queries],
        resumed=options.resume,
        offline=options.offline,
        limit_repos=options.limit_repos,
        skip_owners=options.skip_owners,
        api_facts=API_FACTS,
    )
    started = now()
    token = None if options.offline else config.get_token(paths)
    client = GitHubClient(
        token,
        DiskCache(paths.cache),
        run_id,
        offline=options.offline,
        etags=store.load_etags(paths.etags),
        transport=transport,
        sleep=sleep,
        now=now,
        log=log,
    )
    try:
        summary = _run(paths, options, queries, client, journal, run_id, today, log)
    except KeyboardInterrupt:
        journal.event("run_interrupted")
        raise
    except Exception as exc:
        journal.event("run_failed", error=type(exc).__name__, message=str(exc)[:500])
        raise
    finally:
        client.close()
    summary["seconds"] = round(now() - started)
    journal.event("run_finished", totals=summary["totals"], seconds=summary["seconds"])
    store.write_json_atomic(paths.runs / f"{run_id}.json", summary)
    return summary


def _run(
    paths: config.Paths,
    options: RunOptions,
    queries: list[Query],
    client: GitHubClient,
    journal: runlog.RunLog,
    run_id: str,
    today: dt.date,
    log: Callable[[str], None],
) -> dict[str, Any]:
    index = default_index()
    exclusions = store.load_exclusions(paths.exclusions)
    previous = store.load_repos(paths.repos)

    if not options.offline:
        limits = client.rate_limit()
        journal.event("rate_limit", limits={k: v["limit"] for k, v in limits.items()})
        log(
            "token accepted. limits: "
            + ", ".join(f"{k} {v['remaining']}/{v['limit']}" for k, v in limits.items())
        )

    # 1. Discovery.
    hits: dict[int, _Hit] = {}
    reports = []
    for query in queries:
        log(f"query {query.id}: {query.q}")
        report, items = code_search(
            client,
            query.id,
            query.q,
            max_pages=query.max_pages,
            on_slice=lambda s, q=query: journal.event("slice", query=q.id, **s.__dict__),
        )
        counted: set[int] = set()
        for item in items:
            fragments = [
                m["fragment"] for m in (item.get("text_matches") or []) if m.get("fragment")
            ]
            if not verify(query, fragments):
                report.unverified_files += 1
                continue
            report.verified_files += 1
            doc = is_doc_path(item.get("path", ""))
            report.doc_files += int(doc)
            repo = item["repository"]
            rid = int(repo["id"])
            counted.add(rid)
            hit = hits.setdefault(rid, _Hit(rid, repo["full_name"]))
            hit.files += 1
            if doc:
                # A fal string in documentation is a mention, not code.
                hit.sources.add(f"{query.source}:docs")
                hit.tiers.add("mention")
                continue
            hit.sources.add(query.source)
            hit.tiers.add(query.tier)
            if query.client:
                hit.clients.add(query.client)
            hit.fragments.extend(fragments)
        report.counted_repos = len(counted)
        journal.event("query_finished", **report.to_dict())
        reports.append(report)
        log(
            f"  total_count {report.total_count}, slices {len(report.slices)}, "
            f"files {report.files} (literal match {report.verified_files}, "
            f"dropped {report.unverified_files}, in docs {report.doc_files}), "
            f"repositories {report.counted_repos}"
        )

    # 2. Which repositories to look up: everything found now, plus everything known before.
    # A repo recorded as gone is not asked for again while search still returns the same
    # repository id (a stale index). A new id under the same name is a new repo.
    gone = store.load_gone(paths.gone)
    stale_hits = 0
    wanted: dict[int, str] = {}
    for h in hits.values():
        if gone.get(h.full_name.lower(), {}).get("id") == h.repo_id:
            stale_hits += 1
            continue
        wanted[h.repo_id] = h.full_name
    for repo in previous.values():
        if repo.full_name.lower() not in gone:
            wanted.setdefault(repo.id, repo.full_name)
    wanted = {
        rid: name
        for rid, name in wanted.items()
        if not exclusions.drops(name, name.split("/", 1)[0])
    }
    order = sorted(wanted, key=lambda rid: (wanted[rid].lower(), rid))
    partial = False
    if options.limit_repos is not None and len(order) > options.limit_repos:
        order = order[: options.limit_repos]
        partial = True

    log(f"looking up {len(order)} repositories")
    facts, enrich_report = fetch_repos(client, [wanted[rid] for rid in order], log=log)

    # 3. Owners.
    logins: set[str] = set()
    for rid in order:
        fact = facts[wanted[rid]]
        if fact.data:
            logins.add((fact.data.get("owner") or {}).get("login", ""))
    logins.discard("")
    profiles: dict[str, dict | None] = {}
    if not options.skip_owners:
        log(f"looking up {len(logins)} owners")
        profiles = fetch_owners(client, sorted(logins, key=str.lower), log=log)

    # 4. Build the records.
    built: dict[int, Repo] = {}
    unknown_ids: Counter[str] = Counter()
    for rid in order:
        fact = facts[wanted[rid]]
        hit = hits.get(rid)
        old = previous.get(rid)
        if fact.data is None:
            # Unchanged on GitHub since the last run and no body kept: carry the old record
            # forward, but recompute what depends on today's date and on this run's hits.
            if fact.not_modified and old is not None:
                families, unknown = classify.models_from_fragments(
                    hit.fragments if hit else [], index
                )
                unknown_ids.update(unknown)
                built[rid] = old.model_copy(
                    update={
                        "sources": sorted(set(old.sources) | (hit.sources if hit else set())),
                        "clients": sorted(set(old.clients) | (hit.clients if hit else set())),
                        "models": sorted(set(old.models) | set(families)),
                        "evidence": "code"
                        if old.evidence == "code" or (hit is not None and "code" in hit.tiers)
                        else "mention",
                        "active": classify.is_active(old.pushed_at, today),
                        "notable": classify.is_notable(
                            fork=old.fork,
                            kind=old.kind,
                            description=old.description,
                            stars=old.stars,
                            pushed_at=old.pushed_at,
                            today=today,
                        ),
                        "last_seen": run_id if hit else old.last_seen,
                        "stars_history": _history(old.stars_history, run_id, old.stars),
                    }
                )
            continue
        data = fact.data
        real_id = int(data["id"])
        old = previous.get(real_id, old)
        login = (data.get("owner") or {}).get("login", "")
        full_name = data["full_name"]
        if exclusions.drops(full_name, login):
            continue
        description = _clean(data.get("description"))
        stars = int(data.get("stargazers_count") or 0)
        topics = list(data.get("topics") or [])
        fork = bool(data.get("fork"))
        is_template = bool(data.get("is_template"))
        pushed_at = data.get("pushed_at")
        families, unknown = classify.models_from_fragments(hit.fragments if hit else [], index)
        unknown_ids.update(unknown)
        kind = classify.kind_of(
            name=data.get("name") or full_name.split("/", 1)[-1],
            description=description,
            fork=fork,
            is_template=is_template,
            stars=stars,
        )
        sources = set(old.sources if old else []) | (hit.sources if hit else set())
        clients = set(old.clients if old else []) | (hit.clients if hit else set())
        models = set(old.models if old else []) | set(families)
        code_tier = (hit is not None and "code" in hit.tiers) or (
            old is not None and old.evidence == "code"
        )
        license_info = data.get("license") or {}
        fork_source = ((data.get("source") or {}).get("full_name")) if fork else None
        template_source = (data.get("template_repository") or {}).get("full_name")
        record = Repo(
            id=real_id,
            full_name=full_name,
            html_url=data.get("html_url") or f"https://github.com/{full_name}",
            description=description,
            stars=stars,
            forks=int(data.get("forks_count") or 0),
            language=data.get("language"),
            topics=topics,
            license=_clean(license_info.get("spdx_id")),
            homepage=_clean(data.get("homepage")),
            created_at=data["created_at"],
            pushed_at=pushed_at,
            fork=fork,
            archived=bool(data.get("archived")),
            is_template=is_template,
            fork_source=fork_source,
            template_source=template_source,
            owner=_owner_from(data, profiles.get(login), old.owner if old else None),
            evidence="code" if code_tier else "mention",
            sources=sorted(sources),
            clients=sorted(clients),
            models=sorted(models),
            kind=kind,
            stack=classify.stack_from_topics(topics),
            active=classify.is_active(pushed_at, today),
            notable=classify.is_notable(
                fork=fork,
                kind=kind,
                description=description,
                stars=stars,
                pushed_at=pushed_at,
                today=today,
            ),
            first_seen=old.first_seen if old else run_id,
            last_seen=run_id if hit else (old.last_seen if old else run_id),
            stars_history=_history(old.stars_history if old else [], run_id, stars),
        )
        # A renamed repository can be reached under two names; the id decides.
        if real_id in built:
            merged = built[real_id]
            record = record.model_copy(
                update={
                    "sources": sorted(set(merged.sources) | set(record.sources)),
                    "clients": sorted(set(merged.clients) | set(record.clients)),
                    "models": sorted(set(merged.models) | set(record.models)),
                }
            )
        built[real_id] = record

    # Records we knew before and did not look up this time (a limited run) stay as they are.
    looked_up = set(order)
    gone_names = {n.lower() for n in enrich_report.gone}
    for rid, old in previous.items():
        if rid in built or rid in looked_up:
            continue
        if old.full_name.lower() in gone_names or exclusions.drops(old.full_name, old.owner.login):
            continue
        built[rid] = old

    id_of = {wanted[rid].lower(): rid for rid in order}
    for name in enrich_report.gone:
        fact = facts.get(name)
        gone[name.lower()] = {
            "id": id_of.get(name.lower()),
            "status": fact.status if fact else None,
            "since": run_id,
        }
    for name, fact in facts.items():
        if fact.data is not None or fact.not_modified:
            gone.pop(name.lower(), None)

    repos = list(built.values())
    store.save_repos(paths.repos, repos)
    store.save_gone(paths.gone, gone)
    core_prefixes = (f"{config.API_ROOT}/repos/", f"{config.API_ROOT}/users/")
    store.save_etags(
        paths.etags, {u: e for u, e in client.etags.items() if u.startswith(core_prefixes)}
    )

    code = [r for r in repos if r.evidence == "code"]
    headline = [r for r in code if is_headline(r)]
    totals = {
        "headline": len(headline),
        "repos": len(repos),
        "code_tier": len(code),
        "mention_tier": len(repos) - len(code),
        "code_tier_non_fork": sum(1 for r in code if not r.fork),
        "code_tier_notable": sum(1 for r in code if r.notable),
        "code_tier_active": sum(1 for r in code if r.active),
        "owners": len({r.owner.login.lower() for r in code}),
        "headline_owners": len({r.owner.login.lower() for r in headline}),
        "fal_template_copies": sum(1 for r in code if _from_fal_template(r)),
        "found_this_run": len(hits),
        "gone": len(enrich_report.gone),
        "gone_skipped_stale_hits": stale_hits,
        "renamed": len(enrich_report.renamed),
    }
    return {
        "run_id": run_id,
        "collector": __version__,
        "partial": partial,
        "resumed": options.resume,
        "offline": options.offline,
        "queries": [r.to_dict() for r in reports],
        "totals": totals,
        "kinds": dict(sorted(Counter(r.kind for r in code).items())),
        "clients": dict(sorted(Counter(c for r in code for c in r.clients).items())),
        "enrichment": {
            "requested": enrich_report.requested,
            "ok": enrich_report.ok,
            "not_modified": enrich_report.not_modified,
            "gone": sorted(enrich_report.gone, key=str.lower),
            "renamed": dict(sorted(enrich_report.renamed.items())),
            "owners_looked_up": len(profiles),
        },
        "unknown_model_ids": dict(unknown_ids.most_common(MAX_UNKNOWN_IDS_IN_SUMMARY)),
        "requests": {k: dict(v) for k, v in sorted(client.stats.items())},
        "api_facts": API_FACTS,
        "notes": [
            "headline: code tier, not a fork, not generated from one of fal's own templates.",
            "notable is a lower bound until the README lookup exists (Phase 1).",
            "models are those seen in code search fragments: models seen in code, at least.",
        ],
    }
