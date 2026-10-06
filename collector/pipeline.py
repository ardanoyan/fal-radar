"""One collector run, in stages. Nothing in data/ is written until the last stage.

A run is identified by its UTC date. Every response fetched during a run is cached
under that id, so running again with --resume replays what was already fetched and
only spends the rate limit on what is missing.

Stages:
  1. code queries (clients, integrations) and sampled model-family queries
  2. repository search for the mention tier, and the promotion candidates (20+ stars)
  3. repository lookups for the code tier
  4. one manifest per code-tier repo (kind and stack)
  5. README size, only where it decides "notable"
  6. owner profiles
  7. scoped "fal-ai/" searches for notable repos and promotion candidates (cap 500)
  8. write repos, mentions, snapshot, gone and unsearchable lists, etags
"""

from __future__ import annotations

import datetime as dt
import shutil
import time
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from . import __version__, classify, config, manifests, runlog, store
from .cache import DiskCache
from .enrich import fetch_file, fetch_owners, fetch_readme_size, fetch_repos
from .github import AuthError, GitHubClient, GitHubError, RateLimitGiveUp
from .models import default_index, extract_fal_ai_ids, extract_partner_ids
from .queries import (
    ALL_QUERIES,
    API_FACTS,
    CODE_QUERIES,
    MODEL_QUERIES,
    PROMOTION_QUERIES,
    REPO_QUERIES,
    Query,
    is_doc_path,
    is_vendored_path,
    verify,
)
from .schema import Mention, Owner, Repo, StarsPoint
from .search import QueryReport, code_search, repo_search

MAX_UNKNOWN_IDS_IN_SUMMARY = 200
SCOPED_CAP = 500
SCOPED_PAGES = 1
FAL_OWNERS = {"fal-ai", "fal-ai-community"}
PROGRESS_EVERY = 250


# A run caches a few hundred MB at most; stop early rather than fail hours in.
MIN_FREE_BYTES = 1_000_000_000


class NothingToResume(RuntimeError):
    pass


class LowDisk(RuntimeError):
    pass


@dataclass
class RunOptions:
    only: list[str] = field(default_factory=list)
    resume: bool = False
    offline: bool = False
    skip_owners: bool = False
    skip_manifests: bool = False
    skip_readmes: bool = False
    skip_scoped: bool = False
    limit_repos: int | None = None
    # Rebuild an earlier run from its cache (only what is missing is fetched).
    run_id: str | None = None


@dataclass
class _Hit:
    repo_id: int
    full_name: str
    sources: set[str] = field(default_factory=set)
    clients: set[str] = field(default_factory=set)
    tiers: set[str] = field(default_factory=set)
    fragments: list[str] = field(default_factory=list)
    manifest_paths: list[str] = field(default_factory=list)
    files: int = 0


@dataclass
class _MentionHit:
    item: dict
    sources: set[str] = field(default_factory=set)


def select_queries(only: list[str]) -> list[Query]:
    if not only:
        return [*CODE_QUERIES, *MODEL_QUERIES, *REPO_QUERIES, *PROMOTION_QUERIES]
    unknown = [q for q in only if q not in ALL_QUERIES]
    if unknown:
        raise ValueError(f"unknown query id: {', '.join(unknown)}. See `collector queries`.")
    return [ALL_QUERIES[q] for q in only]


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


def is_fal_owner(login: str) -> bool:
    return login.lower() in FAL_OWNERS


def _from_fal_template(repo: Repo) -> bool:
    for source in (repo.fork_source, repo.template_source):
        if source and is_fal_owner(source.split("/", 1)[0]):
            return True
    return False


def is_headline(repo: Repo) -> bool:
    """Counts toward the headline: fal in the code, not a fork, not fal's own repo, and not
    a copy of one of fal's templates.

    Templates built by other people do count; they are only kept out of the digest's notable set.
    """
    return (
        repo.evidence == "code"
        and not repo.fork
        and not repo.owner_is_fal
        and not _from_fal_template(repo)
    )


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
    limited = is_limited(options)

    if options.resume:
        run_id = runlog.latest_unfinished(paths.runs)
        if run_id is None:
            raise NothingToResume("no unfinished full run under data/runs/ to resume")
    else:
        run_id = options.run_id or run_date or runlog.today_utc()
    today = dt.date.fromisoformat(run_id)

    if not options.offline:
        paths.cache.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(paths.cache).free
        if free < MIN_FREE_BYTES:
            raise LowDisk(
                f"only {free / 1e9:.1f} GB free on the disk that holds {paths.cache}; "
                f"free at least {MIN_FREE_BYTES / 1e9:.0f} GB before a run"
            )

    journal = runlog.RunLog(paths.runs, run_id)
    journal.event(
        "run_started",
        run_id=run_id,
        collector=__version__,
        limited=limited,
        queries=[q.id for q in queries],
        resumed=options.resume,
        offline=options.offline,
        limit_repos=options.limit_repos,
        skip_owners=options.skip_owners,
        skip_manifests=options.skip_manifests,
        skip_readmes=options.skip_readmes,
        skip_scoped=options.skip_scoped,
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
        summary = _Run(paths, options, queries, client, journal, run_id, today, log).go()
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
    # A limited run (--only, --limit-repos, a skipped stage) never replaces the full
    # run's summary.
    name = f"{run_id}.limited.json" if limited else f"{run_id}.json"
    store.write_json_atomic(paths.runs / name, summary)
    return summary


def is_limited(options: RunOptions) -> bool:
    """A run that does not do everything: its outputs must not pass for a full week."""
    return bool(
        options.only
        or options.limit_repos is not None
        or options.skip_owners
        or options.skip_manifests
        or options.skip_readmes
        or options.skip_scoped
    )


class _Run:
    def __init__(
        self,
        paths: config.Paths,
        options: RunOptions,
        queries: list[Query],
        client: GitHubClient,
        journal: runlog.RunLog,
        run_id: str,
        today: dt.date,
        log: Callable[[str], None],
    ) -> None:
        self.paths = paths
        self.options = options
        self.queries = queries
        self.client = client
        self.journal = journal
        self.run_id = run_id
        self.today = today
        self.log = log
        self.index = default_index()
        self.exclusions = store.load_exclusions(paths.exclusions)
        self.previous = store.load_repos(paths.repos)
        # Owners we already hold a name and location for, by lower-case login.
        self.known_owners: dict[str, Owner] = {
            r.owner.login.lower(): r.owner for r in self.previous.values()
        }
        self.previous_mentions = store.load_mentions(paths.mentions)
        self.gone = store.load_gone(paths.gone)
        self.unsearchable = store.load_gone(paths.unsearchable)
        self.hits: dict[int, _Hit] = {}
        self.mention_hits: dict[int, _MentionHit] = {}
        self.promotion_ids: set[int] = set()
        self.reports: list[QueryReport] = []
        self.unknown_ids: Counter[str] = Counter()
        self.stats: dict[str, Any] = {}

    # -- helpers -------------------------------------------------------------------------

    def stage(self, name: str, **fields: Any) -> None:
        self.journal.event("stage", stage=name, **fields)
        self.log(f"stage: {name}" + (f" ({fields})" if fields else ""))

    def excluded(self, full_name: str) -> bool:
        return self.exclusions.drops(full_name, full_name.split("/", 1)[0])

    def families_from(self, fragments: list[str], *, partners: bool) -> set[str]:
        found: set[str] = set()
        for fragment in fragments:
            for endpoint_id in extract_fal_ai_ids(fragment):
                family = self.index.match(endpoint_id)
                if family:
                    found.add(family)
                else:
                    self.unknown_ids[endpoint_id] += 1
            if partners:
                for endpoint_id in extract_partner_ids(fragment):
                    family = self.index.match(endpoint_id)
                    if family:
                        found.add(family)
        return found

    # -- stage 1 and 2: discovery ----------------------------------------------------------

    def discover(self) -> None:
        code_queries = [q for q in self.queries if q.kind == "code"]
        repo_queries = [q for q in self.queries if q.kind == "repo"]
        self.stage("code search", queries=len(code_queries))
        for query in code_queries:
            self.code_query(query)
        if repo_queries:
            self.stage("repository search", queries=len(repo_queries))
        for query in repo_queries:
            self.repo_query(query)

    def code_query(self, query: Query) -> None:
        self.log(f"query {query.id}: {query.q}")
        report, items = code_search(
            self.client,
            query.id,
            query.q,
            max_pages=query.max_pages,
            on_slice=lambda s: self.journal.event("slice", query=query.id, **s.__dict__),
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
            path = item.get("path", "")
            if is_vendored_path(path):
                report.vendored_files += 1
                continue
            doc = is_doc_path(path)
            report.doc_files += int(doc)
            repo = item["repository"]
            rid = int(repo["id"])
            counted.add(rid)
            hit = self.hits.setdefault(rid, _Hit(rid, repo["full_name"]))
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
            if path.rsplit("/", 1)[-1] in manifests.MANIFEST_PREFERENCE:
                hit.manifest_paths.append(path)
        report.counted_repos = len(counted)
        self.journal.event("query_finished", **report.to_dict())
        self.reports.append(report)
        self.log(
            f"  total_count {report.total_count}, slices {len(report.slices)}, "
            f"files {report.files} (literal match {report.verified_files}, "
            f"dropped {report.unverified_files}, in docs {report.doc_files}, "
            f"vendored {report.vendored_files}), "
            f"repositories {report.counted_repos}" + (", sampled" if report.sampled else "")
        )

    def repo_query(self, query: Query) -> None:
        self.log(f"query {query.id}: {query.q}")
        report, items = repo_search(
            self.client,
            query.id,
            query.q,
            today=self.today,
            on_slice=lambda s: self.journal.event("slice", query=query.id, **s.__dict__),
        )
        for item in items:
            rid = int(item["id"])
            mh = self.mention_hits.setdefault(rid, _MentionHit(item))
            mh.sources.add(query.source)
            if query.id.startswith("promote-"):
                self.promotion_ids.add(rid)
        report.verified_files = report.files
        report.counted_repos = report.repos
        self.journal.event("query_finished", **report.to_dict())
        self.reports.append(report)
        self.log(
            f"  total_count {report.total_count}, slices {len(report.slices)}, "
            f"repositories {report.repos}"
        )

    # -- stage 3: repository lookups -------------------------------------------------------

    def lookup_targets(self) -> tuple[list[int], dict[int, str], bool]:
        """Every repo found this run, plus every repo we knew before and do not know as gone.

        A repo in gone.json that search returns again is looked up again (one request), so a
        repo that came back is not lost; if it is still gone it stays on the list.
        """
        rechecked = 0
        wanted: dict[int, str] = {}
        for h in self.hits.values():
            if h.full_name.lower() in self.gone:
                rechecked += 1
            wanted[h.repo_id] = h.full_name
        for repo in self.previous.values():
            if repo.full_name.lower() not in self.gone:
                wanted.setdefault(repo.id, repo.full_name)
        wanted = {rid: name for rid, name in wanted.items() if not self.excluded(name)}
        order = sorted(wanted, key=lambda rid: (wanted[rid].lower(), rid))
        partial = False
        if self.options.limit_repos is not None and len(order) > self.options.limit_repos:
            order = order[: self.options.limit_repos]
            partial = True
        self.stats["gone_rechecked"] = rechecked
        return order, wanted, partial

    @staticmethod
    def manifest_labels(
        signal: manifests.ManifestSignals | None, old: Repo | None
    ) -> tuple[bool | None, bool | None]:
        """(bot, library) from this run's manifest, else from the previous record."""
        if signal is not None:
            return signal.bot, signal.library
        if old is not None:
            return old.manifest_bot, old.manifest_library
        return None, None

    def code_possible(self, rid: int) -> bool:
        """Could this repo end up in the code tier? If not, it gets no owner or README lookup."""
        hit = self.hits.get(rid)
        old = self.previous.get(rid)
        return (
            (hit is not None and "code" in hit.tiers)
            or (old is not None and old.evidence == "code")
            or rid in self.promotion_ids
        )

    # -- stages 4 to 6 ---------------------------------------------------------------------

    def read_manifests(self, facts: dict, order: list[int], wanted: dict[int, str]) -> dict:
        signals: dict[int, manifests.ManifestSignals] = {}
        if self.options.skip_manifests:
            return signals
        targets = []
        for rid in order:
            fact = facts.get(wanted[rid])
            hit = self.hits.get(rid)
            if fact is None or fact.data is None or hit is None:
                continue
            path = manifests.best_manifest_path(hit.manifest_paths)
            if path:
                targets.append((rid, fact.data["full_name"], path))
        self.stage("manifests", repos=len(targets))
        read = 0
        for n, (rid, full_name, path) in enumerate(targets, start=1):
            text = fetch_file(self.client, full_name, path)
            parsed = manifests.parse(path.rsplit("/", 1)[-1], text) if text else None
            if parsed:
                signals[rid] = parsed
                read += 1
            if n % PROGRESS_EVERY == 0:
                self.log(f"  manifests {n}/{len(targets)}")
        self.stats["manifests_requested"] = len(targets)
        self.stats["manifests_read"] = read
        return signals

    def read_readmes(self, candidates: list[tuple[int, str]]) -> dict[int, int | None]:
        sizes: dict[int, int | None] = {}
        if self.options.skip_readmes:
            return sizes
        self.stage("readmes", repos=len(candidates))
        for rid, full_name in candidates:
            sizes[rid] = fetch_readme_size(self.client, full_name)
        self.stats["readmes_requested"] = len(candidates)
        return sizes

    # -- record building -------------------------------------------------------------------

    def build_record(
        self,
        data: dict,
        hit: _Hit | None,
        old: Repo | None,
        profile: dict | None,
        signal: manifests.ManifestSignals | None,
        readme_bytes: int | None,
        *,
        extra_sources: set[str] | None = None,
        extra_models: set[str] | None = None,
        extra_clients: set[str] | None = None,
        promoted: bool = False,
    ) -> Repo:
        full_name = data["full_name"]
        login = (data.get("owner") or {}).get("login", "")
        description = _clean(data.get("description"))
        stars = int(data.get("stargazers_count") or 0)
        topics = list(data.get("topics") or [])
        fork = bool(data.get("fork"))
        is_template = bool(data.get("is_template"))
        pushed_at = data.get("pushed_at")
        bot, library = self.manifest_labels(signal, old)
        kind = classify.kind_of(
            name=data.get("name") or full_name.split("/", 1)[-1],
            description=description,
            fork=fork,
            is_template=is_template,
            stars=stars,
            bot=bool(bot),
            library=bool(library),
            topics=topics,
        )
        code_tier = (
            promoted
            or (hit is not None and "code" in hit.tiers)
            or (old is not None and old.evidence == "code")
        )
        families = self.families_from(hit.fragments, partners=code_tier) if hit else set()
        sources = set(old.sources if old else []) | (hit.sources if hit else set())
        sources |= extra_sources or set()
        clients = set(old.clients if old else []) | (hit.clients if hit else set())
        clients |= extra_clients or set()
        models = set(old.models if old else []) | families | (extra_models or set())
        readme = readme_bytes if readme_bytes is not None else (old.readme_bytes if old else None)
        license_info = data.get("license") or {}
        return Repo(
            id=int(data["id"]),
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
            fork_source=((data.get("source") or {}).get("full_name")) if fork else None,
            template_source=(data.get("template_repository") or {}).get("full_name"),
            owner=_owner_from(
                data, profile, old.owner if old else self.known_owners.get(login.lower())
            ),
            owner_is_fal=is_fal_owner(login),
            evidence="code" if code_tier else "mention",
            sources=sorted(sources),
            clients=sorted(clients),
            models=sorted(models),
            kind=kind,
            stack=classify.stack_of(
                topics, signal.stack if signal else (old.stack if old else None)
            ),
            active=classify.is_active(pushed_at, self.today),
            notable=classify.is_notable(
                fork=fork,
                kind=kind,
                description=description,
                stars=stars,
                pushed_at=pushed_at,
                today=self.today,
                readme_bytes=readme,
            ),
            readme_bytes=readme,
            manifest_bot=bot,
            manifest_library=library,
            first_seen=old.first_seen if old else self.run_id,
            last_seen=self.run_id if (hit or promoted) else (old.last_seen if old else self.run_id),
            stars_history=_history(old.stars_history if old else [], self.run_id, stars),
        )

    def carry_forward(self, old: Repo, hit: _Hit | None) -> Repo:
        """GitHub said nothing changed (304, no body kept): recompute date- and hit-based fields."""
        families = self.families_from(hit.fragments, partners=True) if hit else set()
        return old.model_copy(
            update={
                "sources": sorted(set(old.sources) | (hit.sources if hit else set())),
                "clients": sorted(set(old.clients) | (hit.clients if hit else set())),
                "models": sorted(set(old.models) | families),
                "evidence": "code"
                if old.evidence == "code" or (hit is not None and "code" in hit.tiers)
                else "mention",
                "owner_is_fal": is_fal_owner(old.owner.login),
                "active": classify.is_active(old.pushed_at, self.today),
                "notable": classify.is_notable(
                    fork=old.fork,
                    kind=old.kind,
                    description=old.description,
                    stars=old.stars,
                    pushed_at=old.pushed_at,
                    today=self.today,
                    readme_bytes=old.readme_bytes,
                ),
                "last_seen": self.run_id if hit else old.last_seen,
                "stars_history": _history(old.stars_history, self.run_id, old.stars),
            }
        )

    # -- stage 7: scoped searches ----------------------------------------------------------

    def scoped(
        self, built: dict[int, Repo]
    ) -> tuple[dict[int, set[str]], dict[int, set[str]], set[int], dict]:
        """Model lists for notable repos, and promotion of mention repos with 20+ stars.

        Returns (models by repo, clients by repo, promoted repo ids, stats).
        """
        models: dict[int, set[str]] = defaultdict(set)
        clients: dict[int, set[str]] = defaultdict(set)
        promoted: set[int] = set()
        info = {"requested": 0, "with_hits": 0, "unsearchable": 0, "errors": 0, "capped": 0}
        if self.options.skip_scoped:
            return models, clients, promoted, info
        candidates: list[tuple[int, str, int, str]] = []
        for rid, repo in built.items():
            if repo.evidence == "code" and repo.notable:
                candidates.append((rid, repo.full_name, repo.stars, repo.pushed_at or ""))
        for rid in self.promotion_ids:
            if rid in built and built[rid].evidence == "code":
                continue
            mh = self.mention_hits.get(rid)
            if mh is None or self.excluded(mh.item["full_name"]):
                continue
            item = mh.item
            candidates.append(
                (
                    rid,
                    item["full_name"],
                    int(item.get("stargazers_count") or 0),
                    item.get("pushed_at") or "",
                )
            )
        candidates = [c for c in candidates if c[1].lower() not in self.unsearchable]
        candidates.sort(key=lambda c: (-c[2], _neg_date(c[3]), c[1].lower()))
        info["capped"] = max(0, len(candidates) - SCOPED_CAP)
        candidates = candidates[:SCOPED_CAP]
        self.stage("scoped searches", repos=len(candidates), capped=info["capped"])
        # (literal, client, file name the literal must sit in, or None for any code file)
        client_needles = [
            (n.lower(), q.client, _filename_of(q.q))
            for q in CODE_QUERIES
            for n in q.needles
            if q.client
        ]
        for n, (rid, full_name, _stars, _pushed) in enumerate(candidates, start=1):
            info["requested"] += 1
            try:
                _report, items = code_search(
                    self.client,
                    f"scoped:{full_name}",
                    f'"fal-ai/" repo:{full_name}',
                    max_pages=SCOPED_PAGES,
                )
            except GitHubError as exc:
                if exc.status == 422:
                    # The repository cannot be searched (deleted, renamed, or not indexed).
                    self.unsearchable[full_name.lower()] = {
                        "id": rid,
                        "status": 422,
                        "since": self.run_id,
                    }
                    info["unsearchable"] += 1
                    continue
                if isinstance(exc, AuthError | RateLimitGiveUp):
                    raise
                # One repo's failure (a 403 page, a 5xx after retries) is logged and skipped.
                self.journal.event(
                    "scoped_error", repo=full_name, status=exc.status, message=exc.message[:200]
                )
                info["errors"] += 1
                continue
            code_items = [
                i
                for i in items
                if not is_doc_path(i.get("path", "")) and not is_vendored_path(i.get("path", ""))
            ]
            fragments = [
                m["fragment"]
                for i in code_items
                for m in (i.get("text_matches") or [])
                if m.get("fragment")
            ]
            if code_items:
                info["with_hits"] += 1
            # Promotion needs a fal-ai/ model ID or a fal client literal; partner-namespace IDs
            # count only once the repo shows fal in its code.
            own = self.families_from(fragments, partners=False)
            partner = {
                fam
                for frag in fragments
                for pid in extract_partner_ids(frag)
                if (fam := self.index.match(pid))
            }
            seen_clients: set[str] = set()
            for i in code_items:
                base = i.get("path", "").rsplit("/", 1)[-1]
                texts = [
                    m["fragment"].lower()
                    for m in (i.get("text_matches") or [])
                    if m.get("fragment")
                ]
                for needle, client, fname in client_needles:
                    if (fname is None or base == fname) and any(needle in t for t in texts):
                        seen_clients.add(client)
            clients[rid] |= seen_clients
            already_code = rid in built and built[rid].evidence == "code"
            if not already_code and (own or seen_clients):
                promoted.add(rid)
            models[rid] |= own | (partner if (already_code or rid in promoted) else set())
            if n % PROGRESS_EVERY == 0:
                self.log(f"  scoped {n}/{len(candidates)}")
        return models, clients, promoted, info

    # -- the run -----------------------------------------------------------------------------

    def go(self) -> dict[str, Any]:
        if not self.client.offline:
            limits = self.client.rate_limit()
            self.journal.event("rate_limit", limits={k: v["limit"] for k, v in limits.items()})
            self.log(
                "token accepted. limits: "
                + ", ".join(f"{k} {v['remaining']}/{v['limit']}" for k, v in limits.items())
            )

        self.discover()

        order, wanted, partial = self.lookup_targets()
        self.stage("repository lookups", repos=len(order))
        # Only repos whose previous record we keep may come back as a body-less 304.
        facts, enrich_report = fetch_repos(
            self.client,
            [wanted[rid] for rid in order],
            conditional={r.full_name for r in self.previous.values()},
            log=self.log,
        )

        signals = self.read_manifests(facts, order, wanted)

        # README sizes only where they decide "notable" (recent push, few stars).
        readme_candidates = []
        for rid in order:
            fact = facts[wanted[rid]]
            if fact.data is None or not self.code_possible(rid):
                continue
            d = fact.data
            bot, library = self.manifest_labels(signals.get(rid), self.previous.get(rid))
            kind = classify.kind_of(
                name=d.get("name") or d["full_name"].split("/", 1)[-1],
                description=_clean(d.get("description")),
                fork=bool(d.get("fork")),
                is_template=bool(d.get("is_template")),
                stars=int(d.get("stargazers_count") or 0),
                bot=bool(bot),
                library=bool(library),
                topics=list(d.get("topics") or []),
            )
            if classify.needs_readme(
                fork=bool(d.get("fork")),
                kind=kind,
                description=_clean(d.get("description")),
                stars=int(d.get("stargazers_count") or 0),
                pushed_at=d.get("pushed_at"),
                today=self.today,
            ):
                readme_candidates.append((rid, d["full_name"]))
        readmes = self.read_readmes(readme_candidates)

        # Owners of repos that can be in the code tier; mentions store no profile fields.
        logins = sorted(
            {
                (facts[wanted[rid]].data.get("owner") or {}).get("login", "")
                for rid in order
                if facts[wanted[rid]].data and self.code_possible(rid)
            }
            - {""},
            key=str.lower,
        )
        logins = [lg for lg in logins if not self.exclusions.drops("", lg)]
        profiles: dict[str, dict | None] = {}
        if not self.options.skip_owners:
            self.stage("owners", owners=len(logins))
            profiles = fetch_owners(
                self.client, logins, conditional=set(self.known_owners), log=self.log
            )

        built: dict[int, Repo] = {}
        for rid in order:
            fact = facts[wanted[rid]]
            hit = self.hits.get(rid)
            old = self.previous.get(rid)
            if fact.data is None:
                if fact.not_modified and old is not None:
                    built[rid] = self.carry_forward(old, hit)
                continue
            data = fact.data
            real_id = int(data["id"])
            if real_id != rid:
                # The name now belongs to a different repository (renamed and re-created,
                # or deleted and re-created). Keep what we knew about rid; the other repo
                # gets its own record only through its own hit.
                self.journal.event("name_reused", repo=wanted[rid], id=rid, now_id=real_id)
                if old is not None:
                    built[rid] = self.carry_forward(old, hit)
                continue
            login = (data.get("owner") or {}).get("login", "")
            if self.exclusions.drops(data["full_name"], login):
                continue
            record = self.build_record(
                data, hit, old, profiles.get(login), signals.get(rid), readmes.get(rid)
            )
            if real_id in built:  # a renamed repo reached under two names: the id decides
                merged = built[real_id]
                record = record.model_copy(
                    update={
                        "sources": sorted(set(merged.sources) | set(record.sources)),
                        "clients": sorted(set(merged.clients) | set(record.clients)),
                        "models": sorted(set(merged.models) | set(record.models)),
                    }
                )
            built[real_id] = record

        # Stage 7: scoped searches, then the promoted repos get looked up like the rest.
        scoped_models, scoped_clients, promoted, scoped_info = self.scoped(built)
        for rid in set(scoped_models) | set(scoped_clients):
            if rid in built and built[rid].evidence == "code":
                rec = built[rid]
                built[rid] = rec.model_copy(
                    update={
                        "models": sorted(set(rec.models) | scoped_models.get(rid, set())),
                        "clients": sorted(set(rec.clients) | scoped_clients.get(rid, set())),
                    }
                )
        new_promoted = [
            rid
            for rid in promoted
            if rid not in built and not self.excluded(self.mention_hits[rid].item["full_name"])
        ]
        if new_promoted:
            names = [self.mention_hits[rid].item["full_name"] for rid in new_promoted]
            self.stage("promoted lookups", repos=len(names))
            pfacts, _ = fetch_repos(self.client, names, log=self.log)
            plogins = sorted(
                {(f.data.get("owner") or {}).get("login", "") for f in pfacts.values() if f.data}
                - {""}
                - set(profiles),
                key=str.lower,
            )
            plogins = [lg for lg in plogins if not self.exclusions.drops("", lg)]
            if plogins and not self.options.skip_owners:
                profiles.update(
                    fetch_owners(
                        self.client, plogins, conditional=set(self.known_owners), log=self.log
                    )
                )
            for rid, name in zip(new_promoted, names, strict=True):
                pf = pfacts.get(name)
                if pf is None or pf.data is None:
                    continue
                login = (pf.data.get("owner") or {}).get("login", "")
                if int(pf.data["id"]) != rid or self.exclusions.drops(pf.data["full_name"], login):
                    continue
                built[rid] = self.build_record(
                    pf.data,
                    None,
                    self.previous.get(rid),
                    profiles.get(login),
                    None,
                    None,
                    extra_sources={"code:scoped", *self.mention_hits[rid].sources},
                    extra_models=scoped_models.get(rid, set()),
                    extra_clients=scoped_clients.get(rid, set()),
                    promoted=True,
                )
        for rid in promoted:
            if rid in built and built[rid].evidence != "code":
                rec = built[rid]
                built[rid] = rec.model_copy(
                    update={
                        "evidence": "code",
                        "sources": sorted(set(rec.sources) | {"code:scoped"}),
                        "models": sorted(set(rec.models) | scoped_models.get(rid, set())),
                        "clients": sorted(set(rec.clients) | scoped_clients.get(rid, set())),
                    }
                )

        # Records we knew before and did not look up this time (a limited run) stay as they are.
        looked_up = set(order)
        gone_names = {n.lower() for n in enrich_report.gone}
        for rid, old in self.previous.items():
            if rid in built or rid in looked_up:
                continue
            if old.full_name.lower() in gone_names or self.excluded(old.full_name):
                continue
            built[rid] = old

        id_of = {wanted[rid].lower(): rid for rid in order}
        for name in enrich_report.gone:
            fact = facts.get(name)
            self.gone[name.lower()] = {
                "id": id_of.get(name.lower()),
                "status": fact.status if fact else None,
                "since": self.run_id,
            }
        for name, fact in facts.items():
            if fact.data is not None or fact.not_modified:
                self.gone.pop(name.lower(), None)

        mentions = self.build_mentions(built)
        promoted_count = sum(
            1 for rid in promoted if rid in built and built[rid].evidence == "code"
        )

        # Stage 8: write. repos.json holds the code tier; every mention is in mentions.json.
        repos = [r for r in built.values() if r.evidence == "code"]
        store.save_repos(self.paths.repos, repos)
        store.save_mentions(self.paths.mentions, mentions)
        store.save_gone(
            self.paths.gone, {k: v for k, v in self.gone.items() if not self.excluded(k)}
        )
        store.save_gone(
            self.paths.unsearchable,
            {k: v for k, v in self.unsearchable.items() if not self.excluded(k)},
        )
        store.save_etags(self.paths.etags, self.etags_to_keep(repos))
        snapshot = self.snapshot(repos, mentions)
        limited = is_limited(self.options)
        if not limited:
            # Only a full run is a week on the record (digests compare snapshots).
            store.write_json_atomic(self.paths.snapshots / f"{self.run_id}.json", snapshot)
        return self.summary(
            repos,
            mentions,
            snapshot,
            enrich_report,
            partial or limited,
            scoped_info,
            promoted_count,
            len(profiles),
        )

    def etags_to_keep(self, repos: list[Repo]) -> dict[str, str]:
        """ETags for next week: the code-tier repos and their owners, nothing else.

        Those are the only lookups that may come back as a body-less 304, because their
        previous record is in repos.json. Excluded repos and owners are never kept.
        """
        keep_repos = {f"{config.API_ROOT}/repos/{r.full_name}".lower() for r in repos}
        keep_users = {f"{config.API_ROOT}/users/{r.owner.login}".lower() for r in repos}
        return {
            url: etag
            for url, etag in self.client.etags.items()
            if url.lower() in keep_repos or url.lower() in keep_users
        }

    def build_mentions(self, built: dict[int, Repo]) -> list[Mention]:
        code_ids = {rid for rid, r in built.items() if r.evidence == "code"}
        out: dict[int, Mention] = {}
        for rid, mh in self.mention_hits.items():
            if rid in code_ids:
                continue
            item = mh.item
            if self.excluded(item["full_name"]):
                continue
            owner = item.get("owner") or {}
            old = self.previous_mentions.get(rid)
            out[rid] = Mention(
                id=rid,
                full_name=item["full_name"],
                html_url=item.get("html_url") or f"https://github.com/{item['full_name']}",
                description=_clean(item.get("description")),
                stars=int(item.get("stargazers_count") or 0),
                language=item.get("language"),
                topics=list(item.get("topics") or []),
                created_at=item["created_at"],
                pushed_at=item.get("pushed_at"),
                archived=bool(item.get("archived")),
                owner_login=owner.get("login", ""),
                owner_type=owner.get("type", "User"),
                owner_avatar_url=owner.get("avatar_url", ""),
                owner_is_fal=is_fal_owner(owner.get("login", "")),
                sources=sorted(mh.sources | (set(old.sources) if old else set())),
                first_seen=old.first_seen if old else self.run_id,
                last_seen=self.run_id,
            )
        # Docs-only code-search hits: looked up, built as mention records.
        for rid, rec in built.items():
            if rec.evidence != "mention" or rid in out:
                continue
            old = self.previous_mentions.get(rid)
            out[rid] = Mention(
                id=rid,
                full_name=rec.full_name,
                html_url=rec.html_url,
                description=rec.description,
                stars=rec.stars,
                language=rec.language,
                topics=rec.topics,
                created_at=rec.created_at,
                pushed_at=rec.pushed_at,
                archived=rec.archived,
                owner_login=rec.owner.login,
                owner_type=rec.owner.type,
                owner_avatar_url=rec.owner.avatar_url,
                owner_is_fal=rec.owner_is_fal,
                sources=sorted(set(rec.sources) | (set(old.sources) if old else set())),
                first_seen=old.first_seen if old else rec.first_seen,
                last_seen=rec.last_seen,
            )
        # A run without repository search (a limited run) keeps last week's mentions.
        if not any(q.kind == "repo" for q in self.queries):
            for rid, old in self.previous_mentions.items():
                if rid in out or rid in code_ids or self.excluded(old.full_name):
                    continue
                out[rid] = old
        return list(out.values())

    def snapshot(self, repos: list[Repo], mentions: list[Mention]) -> dict[str, Any]:
        headline = [r for r in repos if is_headline(r)]
        family_files = {
            r.id.removeprefix("model-"): {"files": r.total_count, "sampled": r.sampled}
            for r in self.reports
            if r.id.startswith("model-")
        }
        family_repos = Counter(f for r in headline for f in r.models)
        families = {}
        for fam in self.index.families:
            files = family_files.get(fam.id)
            if files is None and fam.id not in family_repos:
                continue
            families[fam.id] = {
                "repos": family_repos.get(fam.id, 0),
                "files": files["files"] if files else None,
                "files_sampled": files["sampled"] if files else None,
            }
        return {
            "date": self.run_id,
            "headline": len(headline),
            "builders": len({r.owner.login.lower() for r in headline}),
            "notable": sum(1 for r in headline if r.notable),
            "active": sum(1 for r in headline if r.active),
            "mentions": sum(1 for m in mentions if not m.owner_is_fal),
            "from_fal": sum(1 for r in repos if r.evidence == "code" and r.owner_is_fal),
            "from_fal_mentions": sum(1 for m in mentions if m.owner_is_fal),
            "clients": dict(sorted(Counter(c for r in headline for c in r.clients).items())),
            "kinds": dict(sorted(Counter(r.kind for r in headline).items())),
            "stacks": dict(sorted(Counter(s for r in headline for s in r.stack).items())),
            "languages": dict(Counter(r.language or "none" for r in headline).most_common(15)),
            "families": dict(sorted(families.items())),
        }

    def summary(
        self,
        repos,
        mentions,
        snapshot,
        enrich_report,
        partial,
        scoped_info,
        promoted_count,
        owners_looked_up,
    ) -> dict[str, Any]:
        code = [r for r in repos if r.evidence == "code"]
        headline = [r for r in code if is_headline(r)]
        totals = {
            "headline": len(headline),
            "headline_builders": len({r.owner.login.lower() for r in headline}),
            "headline_notable": sum(1 for r in headline if r.notable),
            "headline_active": sum(1 for r in headline if r.active),
            "code_tier": len(code),
            "code_tier_forks": sum(1 for r in code if r.fork),
            "fal_template_copies": sum(1 for r in code if _from_fal_template(r)),
            "from_fal": sum(1 for r in code if r.owner_is_fal),
            "mentions": sum(1 for m in mentions if not m.owner_is_fal),
            "from_fal_mentions": sum(1 for m in mentions if m.owner_is_fal),
            "promoted": promoted_count,
            "repos_in_file": len(repos),
            "found_this_run": len(self.hits),
            "gone": len(enrich_report.gone),
            "gone_rechecked": self.stats.get("gone_rechecked", 0),
            "renamed": len(enrich_report.renamed),
            "model_families_with_repos": sum(
                1 for v in snapshot["families"].values() if v["repos"]
            ),
        }
        coverage = [
            {
                "id": r.id,
                "kind": r.kind,
                "q": r.q,
                "total_count": r.total_count,
                "sum_of_slice_totals": r.sum_of_slice_totals,
                "slices": len(r.slices),
                "truncated_slices": r.truncated_slices,
                "sampled": r.sampled,
                "incomplete": r.incomplete,
                "files": r.files,
                "literal_match": r.verified_files,
                "dropped": r.unverified_files,
                "in_docs": r.doc_files,
                "vendored": r.vendored_files,
                "repos": r.counted_repos,
                "requests": r.requests,
            }
            for r in self.reports
        ]
        return {
            "run_id": self.run_id,
            "collector": __version__,
            "partial": partial,
            "resumed": self.options.resume,
            "offline": self.options.offline,
            "totals": totals,
            "coverage": coverage,
            "queries": [r.to_dict() for r in self.reports],
            "enrichment": {
                "requested": enrich_report.requested,
                "ok": enrich_report.ok,
                "not_modified": enrich_report.not_modified,
                "gone": sorted(enrich_report.gone, key=str.lower),
                "renamed": dict(sorted(enrich_report.renamed.items())),
                "owners_looked_up": owners_looked_up,
                "manifests_requested": self.stats.get("manifests_requested", 0),
                "manifests_read": self.stats.get("manifests_read", 0),
                "readmes_requested": self.stats.get("readmes_requested", 0),
            },
            "scoped": scoped_info,
            "unknown_model_ids": dict(self.unknown_ids.most_common(MAX_UNKNOWN_IDS_IN_SUMMARY)),
            "requests": {k: dict(v) for k, v in sorted(self.client.stats.items())},
            "api_facts": API_FACTS,
            "notes": [
                "headline: code tier, not a fork, not owned by fal, not a copy of a fal template.",
                "models: families seen in code search fragments, a lower bound.",
                "model families: file counts are GitHub's estimate for the unsliced query; "
                f"repo mapping sampled at {MODEL_QUERIES[0].max_pages if MODEL_QUERIES else 0} "
                "pages per family.",
                f"scoped searches: notable repos and mention repos with 20+ stars, "
                f"at most {SCOPED_CAP} per run, first page only.",
            ],
        }


def _filename_of(q: str) -> str | None:
    """The filename: qualifier of a query, if it has one."""
    for part in q.split():
        if part.startswith("filename:"):
            return part.split(":", 1)[1]
    return None


def _neg_date(value: str) -> str:
    """Sort key that puts later ISO dates first."""
    return "".join(chr(0x10FFFF - ord(c)) for c in value)
