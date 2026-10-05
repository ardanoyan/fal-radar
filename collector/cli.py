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
    limit_repos: Annotated[
        int | None,
        typer.Option("--limit-repos", min=1, help="Look up at most this many repositories."),
    ] = None,
) -> None:
    """Discover, enrich and write data/. The token comes from GH_SEARCH_TOKEN, never a flag."""
    options = pipeline.RunOptions(
        only=list(only or []),
        resume=resume,
        offline=offline,
        skip_owners=skip_owners,
        limit_repos=limit_repos,
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
    code = [r for r in repos if r.evidence == "code"]
    typer.echo(f"repositories in data/repos.json: {len(repos)}")
    typer.echo(f"  with fal in the code: {len(code)}")
    typer.echo(f"  of those, not forks: {sum(1 for r in code if not r.fork)}")
    typer.echo(f"  builders (unique owners): {len({r.owner.login.lower() for r in code})}")
    typer.echo("")
    ranked = sorted((r for r in code if not r.fork), key=lambda r: (-r.stars, r.full_name.lower()))
    for r in ranked[:top]:
        text = (r.description or "").replace("\n", " ")
        typer.echo(f"{r.stars:>7}  {r.full_name}  [{r.kind}]  {text[:90]}")


def _print_summary(summary: dict) -> None:
    totals = summary["totals"]
    typer.echo(f"run {summary['run_id']} finished in {summary['seconds']} s")
    for q in summary["queries"]:
        coverage = ""
        if q["sliced"]:
            coverage = f", slice totals add up to {q['sum_of_slice_totals']}"
        flags = "".join(f", {name}" for name in ("sampled", "incomplete") if q[name])
        typer.echo(
            f"  {q['id']}: total_count {q['total_count']}{coverage}, files {q['files']}, "
            f"repositories {q['repos']}, requests {q['requests']}{flags}"
        )
    typer.echo(
        f"repositories with fal in the code: {totals['code_tier']} "
        f"({totals['code_tier_non_fork']} not forks), builders {totals['owners']}"
    )
    if summary["partial"]:
        typer.echo(
            "note: this was a limited run (--limit-repos), not every repository was looked up"
        )
