"""Files the site renders, computed from data/: findings, builders to know, this week.

Every sentence here carries numbers taken from data/repos.json (and the run summary and
fal's catalogue), and each finding records the numbers and the query behind it. The site
renders these files; it computes no figures of its own beyond formatting.

    uv run collector site-data
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config, store
from .models import default_index
from .pipeline import is_headline
from .queries import ALL_QUERIES
from .schema import Repo

BUILDERS = 10
BUILDER_MIN_STARS = 20
ACTIVE_DAYS = 90
RECENT_DAYS = 30
BIG_STARS = 100
NEW_DAYS = 14  # week 1 rule: "New" is code-tier repos created in the last 14 days
NEW_MAX = 8
STARRED_DAYS = 90
STARRED_MAX = 5
MODELS_WEEK_DAYS = 7
DESCRIPTION_CHARS = 160

CLIENT_NAMES = {
    "js": "JavaScript",
    "python": "Python",
    "swift": "Swift",
    "kotlin": "Kotlin",
    "dart": "Dart",
    "http": "direct HTTP",
    "integration": "an integration package",
}


def n(x: int) -> str:
    return f"{x:,}"


def pct(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "0%"


def one_line(text: str | None) -> str | None:
    """The first line of a repo's own description, shortened at a word if long."""
    if not text:
        return None
    line = text.strip().splitlines()[0].strip()
    if len(line) <= DESCRIPTION_CHARS:
        return line
    cut = line[:DESCRIPTION_CHARS].rsplit(" ", 1)[0].rstrip(",;:")
    return cut + "..."


def parse_date(value: str | None) -> dt.date | None:
    return dt.date.fromisoformat(value[:10]) if value else None


@dataclass
class Context:
    paths: config.Paths
    repos: list[Repo]
    headline: list[Repo]
    data_date: dt.date
    run: dict[str, Any]
    queries_run: set[str]
    catalogue: dict[str, Any]

    @property
    def date_str(self) -> str:
        return self.data_date.isoformat()


def latest_full_run(paths: config.Paths) -> dict[str, Any]:
    """The newest full run summary (runs/<date>.json, not .limited.json)."""
    runs = sorted(p for p in paths.runs.glob("*.json") if not p.name.endswith(".limited.json"))
    for p in reversed(runs):
        doc = json.loads(p.read_text(encoding="utf-8"))
        if doc.get("coverage") or doc.get("queries"):
            return doc
    return {}


def load_context(paths: config.Paths = config.DEFAULT_PATHS) -> Context:
    repos = list(store.load_repos(paths.repos).values())
    if not repos:
        raise SystemExit("data/repos.json is empty: run the collector first")
    run = latest_full_run(paths)
    seen = [d for r in repos if (d := parse_date(r.last_seen))]
    data_date = parse_date(run.get("run_id")) or max(seen)
    queries_run = {q["id"] for q in (run.get("coverage") or run.get("queries") or [])}
    catalogue_path = paths.data / "fal_catalogue.json"
    catalogue = (
        json.loads(catalogue_path.read_text(encoding="utf-8")) if catalogue_path.exists() else {}
    )
    return Context(
        paths=paths,
        repos=repos,
        headline=[r for r in repos if is_headline(r)],
        data_date=data_date,
        run=run,
        queries_run=queries_run,
        catalogue=catalogue,
    )


# -- findings ----------------------------------------------------------------------------


def _age_days(ctx: Context, value: str | None) -> int | None:
    d = parse_date(value)
    return (ctx.data_date - d).days if d else None


def finding_clients(ctx: Context) -> dict | None:
    """JavaScript versus Python share. Needs both client queries in the run."""
    if not (
        {"js-client"} <= ctx.queries_run and {"py-import", "py-requirements"} & ctx.queries_run
    ):
        return None
    total = len(ctx.headline)
    js = sum(1 for r in ctx.headline if "js" in r.clients)
    py = sum(1 for r in ctx.headline if "python" in r.clients)
    both = sum(1 for r in ctx.headline if {"js", "python"} <= set(r.clients))
    if not (js and py):
        return None
    lead, lead_n, other, other_n = (
        ("JavaScript", js, "Python", py) if js >= py else ("Python", py, "JavaScript", js)
    )
    return {
        "id": "clients",
        "text": f"{lead} leads: {n(lead_n)} of the {n(total)} repos ({pct(lead_n, total)}) "
        f"use fal's {lead} client, against {n(other_n)} ({pct(other_n, total)}) for "
        f"{other}; {n(both)} use both.",
        "numbers": {"repos": total, "js": js, "python": py, "both": both},
        "query": "headline repos; js = 'js' in clients, python = 'python' in clients",
    }


def finding_models(ctx: Context) -> dict | None:
    """The top three model families and their share of repos where a model was seen."""
    with_model = [r for r in ctx.headline if r.models]
    counts = Counter(f for r in with_model for f in r.models)
    if len(counts) < 3 or len(with_model) < 10:
        return None
    index = default_index()
    top = counts.most_common(3)
    names = [index.by_id[f].name if f in index.by_id else f for f, _ in top]
    any_top = sum(1 for r in with_model if {f for f, _ in top} & set(r.models))
    return {
        "id": "models",
        "text": f"{names[0]}, {names[1]} and {names[2]} lead the models seen in code: "
        f"{n(top[0][1])}, {n(top[1][1])} and {n(top[2][1])} repos, and "
        f"{pct(any_top, len(with_model))} of the {n(len(with_model))} repos with a model "
        "in code use at least one of them.",
        "numbers": {
            "repos_with_model": len(with_model),
            "top": [{"family": f, "repos": c} for f, c in top],
            "repos_with_any_top3": any_top,
        },
        "query": "headline repos with at least one model family seen in code; "
        "families counted once per repo (a lower bound)",
    }


def finding_recent(ctx: Context) -> dict | None:
    total = len(ctx.headline)
    recent = [
        r
        for r in ctx.headline
        if (a := _age_days(ctx, r.created_at)) is not None and a <= RECENT_DAYS
    ]
    if not recent:
        return None
    return {
        "id": "recent",
        "text": f"{n(len(recent))} of the {n(total)} repos ({pct(len(recent), total)}) were "
        f"created in the {RECENT_DAYS} days before {ctx.date_str}.",
        "numbers": {"repos": total, "created_last_30_days": len(recent)},
        "query": f"headline repos with created_at within {RECENT_DAYS} days of the data date",
    }


def finding_big(ctx: Context) -> dict | None:
    total = len(ctx.headline)
    big = sorted((r for r in ctx.headline if r.stars >= BIG_STARS), key=lambda r: -r.stars)
    if not big:
        return None
    top = big[0]
    return {
        "id": "stars",
        "text": f"{n(len(big))} repos ({pct(len(big), total)}) have {BIG_STARS} or more "
        f"stars; the largest, {top.full_name}, has {n(top.stars)}.",
        "numbers": {
            "repos": total,
            "stars_100_plus": len(big),
            "largest": top.full_name,
            "largest_stars": top.stars,
        },
        "query": f"headline repos with stars >= {BIG_STARS}",
    }


def finding_active(ctx: Context) -> dict | None:
    total = len(ctx.headline)
    active = sum(
        1
        for r in ctx.headline
        if (a := _age_days(ctx, r.pushed_at)) is not None and a <= ACTIVE_DAYS
    )
    return {
        "id": "active",
        "text": f"{n(active)} of the {n(total)} repos ({pct(active, total)}) were pushed to "
        f"in the {ACTIVE_DAYS} days before {ctx.date_str}.",
        "numbers": {"repos": total, "pushed_last_90_days": active},
        "query": f"headline repos with pushed_at within {ACTIVE_DAYS} days of the data date",
    }


def finding_orgs(ctx: Context) -> dict | None:
    total = len(ctx.headline)
    orgs = sum(1 for r in ctx.headline if r.owner.type == "Organization")
    return {
        "id": "orgs",
        "text": f"{n(total - orgs)} repos ({pct(total - orgs, total)}) belong to individual "
        f"developers and {n(orgs)} ({pct(orgs, total)}) to organisations.",
        "numbers": {"repos": total, "organisations": orgs, "users": total - orgs},
        "query": "headline repos by owner.type",
    }


def finding_integrations(ctx: Context) -> dict | None:
    if not any(q.startswith("int-") for q in ctx.queries_run):
        return None
    total = len(ctx.headline)
    via = [r for r in ctx.headline if "integration" in r.clients]
    only = [r for r in via if set(r.clients) == {"integration"}]
    if not via:
        return None
    return {
        "id": "integrations",
        "text": f"{n(len(via))} repos reach fal through an integration package (Vercel AI "
        f"SDK, TanStack AI, LiveKit, LiteLLM or n8n), and {n(len(only))} of them use no fal "
        "client at all.",
        "numbers": {"repos": total, "via_integration": len(via), "integration_only": len(only)},
        "query": "headline repos with 'integration' in clients",
    }


def finding_years(ctx: Context) -> dict | None:
    total = len(ctx.headline)
    this_year = ctx.data_date.year
    by_year = Counter(r.created_at[:4] for r in ctx.headline)
    now, last = by_year.get(str(this_year), 0), by_year.get(str(this_year - 1), 0)
    older = total - now - last
    if not now:
        return None
    return {
        "id": "years",
        "text": f"{n(now)} repos ({pct(now, total)}) were created in {this_year} and "
        f"{n(last)} ({pct(last, total)}) in {this_year - 1}; only {n(older)} are older.",
        "numbers": {"repos": total, str(this_year): now, str(this_year - 1): last, "older": older},
        "query": "headline repos by the year of created_at",
    }


FINDINGS = [
    finding_clients,
    finding_models,
    finding_recent,
    finding_big,
    finding_active,
    finding_integrations,
    finding_years,
    finding_orgs,
]


def findings(ctx: Context, count: int = 5) -> list[dict]:
    out = []
    for fn in FINDINGS:
        f = fn(ctx)
        if f:
            if not re.search(r"\d", f["text"]):
                raise ValueError(f"finding {f['id']} has no number in its sentence")
            out.append(f)
        if len(out) == count:
            break
    return out


# -- builders ----------------------------------------------------------------------------


def builders(ctx: Context) -> list[dict]:
    """Owners (not fal) with a repo of 20+ stars pushed in the last 90 days, by best repo."""
    by_owner: dict[str, list[Repo]] = {}
    for r in ctx.headline:
        if r.kind == "template":
            continue
        by_owner.setdefault(r.owner.login.lower(), []).append(r)
    rows = []
    for repos in by_owner.values():
        qualifying = [
            r
            for r in repos
            if r.stars >= BUILDER_MIN_STARS
            and (a := _age_days(ctx, r.pushed_at)) is not None
            and a <= ACTIVE_DAYS
        ]
        if not qualifying:
            continue
        best = max(repos, key=lambda r: (r.stars, r.full_name.lower()))
        owner = best.owner
        rows.append(
            {
                "login": owner.login,
                "name": owner.name,
                "type": owner.type,
                "profile_url": owner.html_url,
                "avatar_url": owner.avatar_url,
                "repos": len(repos),
                "best": {
                    "full_name": best.full_name,
                    "url": best.html_url,
                    "description": one_line(best.description),
                    "stars": best.stars,
                    "models": best.models,
                    "pushed_at": best.pushed_at,
                },
            }
        )
    rows.sort(key=lambda b: (-b["best"]["stars"], b["login"].lower()))
    return rows[:BUILDERS]


# -- this week (week 1 rules until a second snapshot exists) ------------------------------


def _row(r: Repo) -> dict:
    return {
        "full_name": r.full_name,
        "url": r.html_url,
        "description": one_line(r.description),
        "stars": r.stars,
        "models": r.models,
        "created_at": r.created_at[:10],
        "pushed_at": (r.pushed_at or "")[:10] or None,
    }


def this_week(ctx: Context) -> dict:
    pool = [r for r in ctx.headline if r.kind != "template"]
    new = sorted(
        (r for r in pool if (a := _age_days(ctx, r.created_at)) is not None and a <= NEW_DAYS),
        key=lambda r: (-r.stars, r.full_name.lower()),
    )
    starred = sorted(
        (r for r in pool if (a := _age_days(ctx, r.created_at)) is not None and a <= STARRED_DAYS),
        key=lambda r: (-r.stars, r.full_name.lower()),
    )
    start = ctx.data_date - dt.timedelta(days=MODELS_WEEK_DAYS - 1)
    endpoints = [
        e
        for e in ctx.catalogue.get("endpoints", [])
        if e.get("created") and start.isoformat() <= e["created"] <= ctx.date_str
    ]
    endpoints.sort(key=lambda e: (e["created"], e["id"]), reverse=True)
    snapshots = sorted(ctx.paths.snapshots.glob("*.json"))
    return {
        "first_issue": len(snapshots) <= 1,
        "new": {"days": NEW_DAYS, "count": len(new), "repos": [_row(r) for r in new[:NEW_MAX]]},
        "most_starred": {"days": STARRED_DAYS, "repos": [_row(r) for r in starred[:STARRED_MAX]]},
        "models": {
            "from": start.isoformat(),
            "to": ctx.date_str,
            "count": len(endpoints),
            "catalogue_read_on": ctx.catalogue.get("read_on"),
            "endpoints": [
                {
                    "id": e["id"],
                    "name": e["name"],
                    "category": e["category"],
                    "created": e["created"],
                }
                for e in endpoints[:6]
            ],
        },
    }


# -- site summary --------------------------------------------------------------------------


def site_summary(ctx: Context) -> dict:
    h = ctx.headline
    mentions = store.load_mentions(ctx.paths.mentions)
    coverage = ctx.run.get("coverage") or []
    return {
        "data_date": ctx.date_str,
        "run_id": ctx.run.get("run_id"),
        "partial": bool(ctx.run.get("partial")) or not coverage,
        "queries_run": sorted(ctx.queries_run),
        "queries_total": len(ALL_QUERIES),
        "headline": len(h),
        "builders": len({r.owner.login.lower() for r in h}),
        "notable": sum(1 for r in h if r.notable),
        "active": sum(1 for r in h if r.active),
        "mentions": sum(1 for m in mentions.values() if not m.owner_is_fal),
        "from_fal": sum(1 for r in ctx.repos if r.evidence == "code" and r.owner_is_fal),
        "from_fal_repos": sorted(
            (r.full_name for r in ctx.repos if r.evidence == "code" and r.owner_is_fal),
            key=str.lower,
        ),
        "coverage": coverage,
        "api_facts": ctx.run.get("api_facts", []),
    }


def families(ctx: Context) -> dict:
    """Every model family with its fal page, repos seen using it, and GitHub's file estimate."""
    index = default_index()
    repos = Counter(f for r in ctx.headline for f in r.models)
    snap_path = ctx.paths.snapshots / f"{ctx.run.get('run_id')}.json"
    snap = json.loads(snap_path.read_text(encoding="utf-8")) if snap_path.exists() else {}
    files = snap.get("families", {})
    rows = []
    for fam in index.families:
        if not (fam.discover or repos.get(fam.id)):
            continue
        rows.append(
            {
                "id": fam.id,
                "name": fam.name,
                "media": fam.media,
                "url": fam.url,
                "repos": repos.get(fam.id, 0),
                "files": (files.get(fam.id) or {}).get("files"),
                "files_sampled": (files.get(fam.id) or {}).get("files_sampled"),
            }
        )
    rows.sort(key=lambda r: (-r["repos"], -(r["files"] or 0), r["name"].lower()))
    return {
        "data_date": ctx.date_str,
        "repos_with_model": sum(1 for r in ctx.headline if r.models),
        "repos": len(ctx.headline),
        "families": rows,
    }


def list_rows(ctx: Context) -> dict:
    """The headline repos, slim, most starred first: what the list on the home page shows."""
    rows = [
        {
            "n": r.full_name,
            "u": r.html_url,
            "d": one_line(r.description),
            "s": r.stars,
            "c": r.clients,
            "m": r.models,
            "l": r.language,
            "k": r.kind,
            "cr": r.created_at[:10],
            "p": (r.pushed_at or "")[:10] or None,
        }
        for r in sorted(ctx.headline, key=lambda r: (-r.stars, r.full_name.lower()))
    ]
    return {"data_date": ctx.date_str, "count": len(rows), "rows": rows}


def write_all(paths: config.Paths = config.DEFAULT_PATHS) -> dict:
    ctx = load_context(paths)
    out = {
        "list.json": list_rows(ctx),
        "families.json": families(ctx),
        "findings.json": {"data_date": ctx.date_str, "findings": findings(ctx)},
        "builders.json": {
            "data_date": ctx.date_str,
            "rule": (
                f"owners other than fal with a repo of {BUILDER_MIN_STARS}+ stars pushed in the "
                f"last {ACTIVE_DAYS} days, no forks or templates, ranked by the stars of their "
                "best fal repo"
            ),
            "builders": builders(ctx),
        },
        "this_week.json": {"data_date": ctx.date_str, **this_week(ctx)},
        "site.json": site_summary(ctx),
    }
    for name, doc in out.items():
        store.write_json_atomic(paths.data / name, doc)
    update_readme(paths.root / "README.md", out["findings.json"])
    return out


START, END = "<!-- findings:start -->", "<!-- findings:end -->"


def update_readme(path: Path, findings_doc: dict) -> None:
    """Refresh the findings block in the README from data/findings.json."""
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    if START not in text or END not in text:
        return
    lines = [f"Data date {findings_doc['data_date']}.", ""]
    lines += [f"{i}. {f['text']}" for i, f in enumerate(findings_doc["findings"], start=1)]
    block = START + "\n" + "\n".join(lines) + "\n" + END
    head, rest = text.split(START, 1)
    tail = rest.split(END, 1)[1]
    path.write_text(head + block + tail, encoding="utf-8")


def main_paths() -> Path:
    return config.DEFAULT_PATHS.data
