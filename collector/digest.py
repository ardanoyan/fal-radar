"""The weekly digest: data/digests/YYYY-Www.md and YYYY-Www.posts.json.

Week 1 (only one snapshot exists): New is code-tier repos created in the last 14 days,
"Most starred, created in the last 90 days" stands in for Rising, and "Models this week"
counts the endpoints fal's catalogue lists as created in the 7 days to the data date.
The tool never posts anywhere; the maintainer edits before posting.

    uv run collector digest
"""

from __future__ import annotations

import datetime as dt
import json

from . import config, store
from .models import default_index
from .site_data import Context, builders, load_context, n, this_week


def stars(count: int) -> str:
    return f"{n(count)} star" + ("" if count == 1 else "s")


SITE_URL = "https://fal-radar.vercel.app"
SIGN_OFF = f"fal radar, an unofficial community project: {SITE_URL}"
DISCORD_MAX = 2000
X_MAX = 280
LINKEDIN_MAX = 1300


def iso_week(d: dt.date) -> str:
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def _models(models: list[str], limit: int = 3) -> str:
    index = default_index()
    names = [index.by_id[m].name if m in index.by_id else m for m in models[:limit]]
    more = len(models) - limit
    return ", ".join(names) + (f" and {more} more" if more > 0 else "")


def _line(r: dict, *, with_models: bool = True) -> str:
    parts = [f"[{r['full_name']}]({r['url']})"]
    if r.get("description"):
        parts.append(r["description"])
    tail = [stars(r["stars"])]
    if with_models and r.get("models"):
        tail.append(f"models seen in code, at least: {_models(r['models'])}")
    return " - ".join(parts) + f" ({'; '.join(tail)})"


def markdown(ctx: Context, week: str, tw: dict, builder: dict | None, summary: dict) -> str:
    out = [
        "---",
        f"week: {week}",
        f"data_date: {ctx.date_str}",
        f"headline: {summary['headline']}",
        f"builders: {summary['builders']}",
        "---",
        "",
        f"# Built on fal, week {week[-2:].lstrip('0')} of {week[:4]}",
        "",
    ]
    if tw["first_issue"]:
        out += [
            "This is the first issue, so there are no week-over-week numbers yet. "
            "Rising repos and model movers start next week.",
            "",
        ]
    if summary["partial"]:
        out += [
            f"Stars and dates so far cover {n(summary['detailed'])} of the "
            f"{n(summary['headline'])} repos; the lists below come from those.",
            "",
        ]
    out += [f"## New this week (created in the last {tw['new']['days']} days)", ""]
    if tw["new"]["repos"]:
        out += [f"- {_line(r)}" for r in tw["new"]["repos"]]
    else:
        out.append("No new repos with fal in the code this week.")
    out += ["", f"## Most starred, created in the last {tw['most_starred']['days']} days", ""]
    out += [f"- {_line(r)}" for r in tw["most_starred"]["repos"]]
    m = tw["models"]
    out += [
        "",
        "## Models this week",
        "",
        f"fal's model catalogue lists {n(m['count'])} endpoints created between {m['from']} "
        f"and {m['to']}" + (":" if m["endpoints"] else "."),
        "",
    ]
    out += [f"- {e['name'] or e['id']} (`{e['id']}`, {e['category']})" for e in m["endpoints"]]
    if builder:
        b = builder["best"]
        who = builder["name"] or builder["login"]
        out += [
            "",
            "## A builder to know",
            "",
            f"[{who}]({builder['profile_url']}) has {n(builder['repos'])} public repos with "
            f"fal in the code. The biggest is [{b['full_name']}]({b['url']}), "
            f"{stars(b['stars'])}" + (f": {b['description']}" if b.get("description") else "."),
        ]
    out += [
        "",
        "## By the numbers",
        "",
        f"- {n(summary['headline'])} public GitHub repos with fal in the code "
        f"(not forks, not fal's own), from {n(summary['builders'])} builders",
        (
            f"- Of the {n(summary['detailed'])} repos looked up so far: "
            if summary["partial"]
            else "- "
        )
        + f"{n(summary['active'])} pushed in the last 90 days, {n(summary['notable'])} notable",
        f"- Data date {ctx.date_str}. Counts cover public GitHub repos that our searches "
        "found; private and closed-source work is invisible to this tool.",
        "",
        SIGN_OFF,
        "",
    ]
    return "\n".join(out)


def _first_builder(ctx: Context) -> dict | None:
    """A builder with two or more notable repos; week 1 picks by stars."""
    notable_by_owner: dict[str, int] = {}
    for r in ctx.headline:
        if r.notable:
            key = r.owner.login.lower()
            notable_by_owner[key] = notable_by_owner.get(key, 0) + 1
    for b in builders(ctx):
        if notable_by_owner.get(b["login"].lower(), 0) >= 2:
            return b
    return None


def posts(ctx: Context, week: str, tw: dict, builder: dict | None, summary: dict) -> dict:
    head = (
        f"Built on fal, week {week[-2:].lstrip('0')}: {n(summary['headline'])} public "
        f"GitHub repos with fal in the code, from {n(summary['builders'])} builders "
        f"(data {ctx.date_str})."
    )
    new = tw["new"]["repos"]
    starred = tw["most_starred"]["repos"]
    m = tw["models"]

    discord = [f"**{head}**", ""]
    if tw["first_issue"]:
        discord += ["First issue: week-over-week numbers start next week.", ""]
    if new:
        discord += [f"**New, created in the last {tw['new']['days']} days**"]
        discord += [f"- {_line(r, with_models=False)}" for r in new[:5]]
        discord.append("")
    discord += [f"**Most starred, created in the last {tw['most_starred']['days']} days**"]
    discord += [f"- {_line(r, with_models=False)}" for r in starred[:3]]
    discord += [
        "",
        f"**Models this week:** fal's catalogue lists {n(m['count'])} endpoints created "
        f"{m['from']} to {m['to']}.",
        "",
        SIGN_OFF,
    ]
    discord_text = "\n".join(discord)

    x = [head + " A thread:"]
    for r in new[:3]:
        x.append(f"New: {r['full_name']}, {stars(r['stars'])}. {r['url']}")
    for r in starred[:2]:
        x.append(
            f"Most starred of the last 90 days: {r['full_name']}, {stars(r['stars'])}. {r['url']}"
        )
    x.append(f"fal's catalogue lists {n(m['count'])} endpoints created {m['from']} to {m['to']}.")
    x.append(SIGN_OFF)

    li = [head, ""]
    if tw["first_issue"]:
        li += ["This is the first issue of a weekly map of what people build on fal.", ""]
    if new:
        li.append(
            "New this fortnight: "
            + "; ".join(f"{r['full_name']} ({stars(r['stars'])})" for r in new[:3])
            + "."
        )
    li.append(
        "Most starred of the last 90 days: "
        + "; ".join(f"{r['full_name']} ({stars(r['stars'])})" for r in starred[:3])
        + "."
    )
    if builder:
        li.append(
            f"A builder to know: {builder['name'] or builder['login']}, "
            f"{builder['best']['full_name']} ({stars(builder['best']['stars'])})."
        )
    li += ["", SIGN_OFF]
    linkedin_text = "\n".join(li)

    # Trim from the end of each list until it fits, never past the sign-off.
    while len(discord_text) > DISCORD_MAX and len(discord) > 4:
        discord.pop(-4)
        discord_text = "\n".join(discord)
    x = [p if len(p) <= X_MAX else p[: X_MAX - 3].rsplit(" ", 1)[0] + "..." for p in x]
    problems = []
    if len(discord_text) > DISCORD_MAX:
        problems.append(f"discord {len(discord_text)}")
    if len(linkedin_text) > LINKEDIN_MAX:
        problems.append(f"linkedin {len(linkedin_text)}")
    problems += [f"x[{i}] {len(p)}" for i, p in enumerate(x) if len(p) > X_MAX]
    if problems:
        raise ValueError("post variants over their limits: " + ", ".join(problems))
    return {
        "week": week,
        "data_date": ctx.date_str,
        "discord": discord_text,
        "x": x,
        "linkedin": linkedin_text,
        "lengths": {
            "discord": len(discord_text),
            "x": [len(p) for p in x],
            "linkedin": len(linkedin_text),
        },
    }


def write(paths: config.Paths = config.DEFAULT_PATHS, week: str | None = None) -> dict:
    ctx = load_context(paths)
    week = week or iso_week(ctx.data_date)
    tw = this_week(ctx)
    builder = _first_builder(ctx)
    h = ctx.headline
    summary = {
        "headline": ctx.total,
        "builders": ctx.discovery["headline_builders"]
        if ctx.discovery
        else len({r.owner.login.lower() for r in h}),
        "detailed": len(h),
        "partial": ctx.discovery is not None,
        "notable": sum(1 for r in h if r.notable),
        "active": sum(1 for r in h if r.active),
    }
    md = markdown(ctx, week, tw, builder, summary)
    post = posts(ctx, week, tw, builder, summary)
    folder = paths.data / "digests"
    folder.mkdir(parents=True, exist_ok=True)
    md_path = folder / f"{week}.md"
    tmp = md_path.with_name(f".{md_path.name}.tmp")
    tmp.write_text(md, encoding="utf-8")
    tmp.replace(md_path)
    store.write_json_atomic(folder / f"{week}.posts.json", post)
    return {
        "week": week,
        "markdown": str(md_path),
        "lengths": post["lengths"],
        "json": json.dumps(post["lengths"]),
    }
