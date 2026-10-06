"""Command line: `uv run collector run`, `queries`, `check-token`, `report`."""

from __future__ import annotations

from typing import Annotated

import typer

from . import config, pipeline, store
from .cache import DiskCache
from .github import AuthError, GitHubClient, GitHubError, OfflineMiss
from .queries import ALL_QUERIES
from .runlog import today_utc

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main() -> None:
    """fal radar collector. Unofficial, not affiliated with fal."""


@app.command()
def run(
    only: Annotated[
        list[str] | None,
        typer.Option("--only", help="Run only this query id. Repeat for several."),
    ] = None,
    resume: Annotated[
        bool, typer.Option("--resume", help="Continue the last unfinished run.")
    ] = False,
    offline: Annotated[
        bool, typer.Option("--offline", help="Build everything from the cache, no network.")
    ] = False,
    skip_owners: Annotated[
        bool, typer.Option("--skip-owners", help="Do not look up owner profiles.")
    ] = False,
    skip_manifests: Annotated[
        bool, typer.Option("--skip-manifests", help="Do not read manifests (kind, stack).")
    ] = False,
    skip_readmes: Annotated[
        bool, typer.Option("--skip-readmes", help="Do not look up README sizes.")
    ] = False,
    skip_scoped: Annotated[
        bool, typer.Option("--skip-scoped", help="Do not run scoped per-repo searches.")
    ] = False,
    limit_repos: Annotated[
        int | None,
        typer.Option("--limit-repos", min=1, help="Look up at most this many repositories."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option(
            "--run-id",
            help="Rebuild an earlier run (YYYY-MM-DD) from its cache, fetching what is missing.",
        ),
    ] = None,
) -> None:
    """Discover, enrich and write data/. The token comes from GH_SEARCH_TOKEN, never a flag."""
    options = pipeline.RunOptions(
        only=list(only or []),
        resume=resume,
        offline=offline,
        skip_owners=skip_owners,
        skip_manifests=skip_manifests,
        skip_readmes=skip_readmes,
        skip_scoped=skip_scoped,
        limit_repos=limit_repos,
        run_id=run_id,
    )
    try:
        summary = pipeline.run(config.DEFAULT_PATHS, options, log=typer.echo)
    except KeyboardInterrupt:
        typer.echo(
            "interrupted. Nothing in data/ was changed. Continue with: collector run --resume"
        )
        raise typer.Exit(130) from None
    except (config.MissingToken, pipeline.NothingToResume, ValueError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    except OfflineMiss as exc:
        typer.echo(f"error: not in the cache, cannot run offline: {exc}", err=True)
        raise typer.Exit(1) from None
    except (AuthError, GitHubError) as exc:
        typer.echo(f"error: {exc}", err=True)
        typer.echo("data/ was left as it was. Continue with: collector run --resume", err=True)
        raise typer.Exit(1) from None
    typer.echo("")
    _print_summary(summary)


@app.command("site-data")
def site_data_cmd() -> None:
    """Write findings.json, builders.json, this_week.json and site.json from data/."""
    from . import site_data

    out = site_data.write_all()
    for f in out["findings.json"]["findings"]:
        typer.echo(f"finding {f['id']}: {f['text']}")
    for b in out["builders.json"]["builders"]:
        typer.echo(f"builder {b['login']}: {b['best']['full_name']} {b['best']['stars']}")


@app.command()
def digest(
    week: Annotated[str | None, typer.Option("--week", help="ISO week, e.g. 2026-W41.")] = None,
) -> None:
    """Write data/digests/<week>.md and <week>.posts.json. Never posts anywhere."""
    from . import digest as digest_mod

    out = digest_mod.write(week=week)
    typer.echo(f"wrote {out['markdown']} and its posts.json; lengths {out['json']}")


@app.command()
def queries() -> None:
    """List the discovery queries and their ids."""
    for q in ALL_QUERIES.values():
        typer.echo(f"{q.id:22} {q.kind:5} {q.tier:8} {q.q}")


@app.command("check-token")
def check_token() -> None:
    """Ask GitHub for the token's rate limits. Prints the limits, never the token."""
    try:
        token = config.get_token()
    except config.MissingToken as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(2) from None
    client = GitHubClient(token, DiskCache(config.DEFAULT_PATHS.cache), today_utc(), log=typer.echo)
    try:
        limits = client.rate_limit()
    except GitHubError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None
    finally:
        client.close()
    for name, info in limits.items():
        typer.echo(f"{name:12} {info['remaining']}/{info['limit']}")


@app.command()
def report(
    top: Annotated[int, typer.Option("--top", min=1, help="How many repositories to list.")] = 20,
) -> None:
    """Counts and the most starred code-tier repositories, read from data/."""
    paths = config.DEFAULT_PATHS
    repos = list(store.load_repos(paths.repos).values())
    if not repos:
        typer.echo("data/repos.json is empty. Run the collector first.")
        raise typer.Exit(1)
    from .pipeline import is_headline

    head = [r for r in repos if is_headline(r)]
    typer.echo(f"repositories in data/repos.json: {len(repos)}")
    typer.echo(f"  headline (fal in the code, not a fork, not fal's own): {len(head)}")
    typer.echo(f"  builders (unique owners): {len({r.owner.login.lower() for r in head})}")
    typer.echo(
        f"  from fal (owner fal-ai or fal-ai-community): {sum(r.owner_is_fal for r in repos)}"
    )
    typer.echo("")
    ranked = sorted(head, key=lambda r: (-r.stars, r.full_name.lower()))
    for r in ranked[:top]:
        text = (r.description or "").replace("\n", " ")
        typer.echo(f"{r.stars:>7}  {r.full_name}  [{r.kind}]  {text[:90]}")


def _print_summary(summary: dict) -> None:
    t = summary["totals"]
    typer.echo(f"run {summary['run_id']} finished in {summary['seconds']} s")
    typer.echo("")
    typer.echo(
        f"{'query':28} {'total':>7} {'slices':>6} {'files':>6} {'literal':>7} "
        f"{'dropped':>7} {'docs':>5} {'repos':>6}"
    )
    for c in summary["coverage"]:
        flag = " sampled" if c["sampled"] else (" truncated" if c["truncated_slices"] else "")
        typer.echo(
            f"{c['id'][:28]:28} {c['total_count']:>7} {c['slices']:>6} {c['files']:>6} "
            f"{c['literal_match']:>7} {c['dropped']:>7} {c['in_docs']:>5} {c['repos']:>6}{flag}"
        )
    typer.echo("")
    typer.echo(
        f"headline (fal in the code, not a fork, not fal's own): {t['headline']} repos, "
        f"{t['headline_builders']} builders, {t['headline_notable']} notable, "
        f"{t['headline_active']} active"
    )
    typer.echo(
        f"also: forks {t['code_tier_forks']}, fal template copies {t['fal_template_copies']}, "
        f"from fal {t['from_fal']}, mentions {t['mentions']}, promoted {t['promoted']}"
    )
    if summary["partial"]:
        typer.echo(
            "note: this was a limited run (--limit-repos), not every repository was looked up"
        )
