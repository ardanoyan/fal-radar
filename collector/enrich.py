"""Repository and owner lookups (GET /repos/{owner}/{repo}, GET /users/{login})."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .github import GitHubClient

PROGRESS_EVERY = 250


@dataclass
class RepoFacts:
    """What one repository lookup told us.

    `data` is None when nothing changed (a 304 with no body kept).
    """

    requested: str
    status: int
    data: dict[str, Any] | None
    not_modified: bool = False


@dataclass
class EnrichReport:
    requested: int = 0
    ok: int = 0
    not_modified: int = 0
    gone: list[str] = field(default_factory=list)
    renamed: dict[str, str] = field(default_factory=dict)


def fetch_repos(
    client: GitHubClient,
    full_names: list[str],
    *,
    log: Callable[[str], None] = print,
) -> tuple[dict[str, RepoFacts], EnrichReport]:
    """Look up each repository once. Keys of the result are the names as requested."""
    report = EnrichReport(requested=len(full_names))
    out: dict[str, RepoFacts] = {}
    for n, name in enumerate(full_names, start=1):
        resp = client.get(f"/repos/{name}")
        if resp.status == 200 and isinstance(resp.data, dict):
            report.ok += 1
            actual = resp.data.get("full_name") or name
            if actual.lower() != name.lower():
                report.renamed[name] = actual
            out[name] = RepoFacts(name, 200, resp.data, resp.not_modified)
        elif resp.not_modified:
            report.not_modified += 1
            out[name] = RepoFacts(name, 304, None, True)
        else:
            # Deleted, made private, blocked or unavailable for legal reasons.
            report.gone.append(name)
            out[name] = RepoFacts(name, resp.status, None)
        if n % PROGRESS_EVERY == 0:
            log(f"  repositories {n}/{len(full_names)}")
    return out, report


def fetch_owners(
    client: GitHubClient,
    logins: list[str],
    *,
    log: Callable[[str], None] = print,
) -> dict[str, dict[str, Any] | None]:
    """Public profile per owner login. None when the profile is gone or unchanged without a body."""
    out: dict[str, dict[str, Any] | None] = {}
    for n, login in enumerate(logins, start=1):
        resp = client.get(f"/users/{login}")
        out[login] = resp.data if resp.status == 200 and isinstance(resp.data, dict) else None
        if n % PROGRESS_EVERY == 0:
            log(f"  owners {n}/{len(logins)}")
    return out


def fetch_file(client: GitHubClient, full_name: str, path: str) -> str | None:
    """Text of one file from the contents API, or None (missing, too large, not text)."""
    import base64
    import urllib.parse

    quoted = urllib.parse.quote(path, safe="/")
    resp = client.get(f"/repos/{full_name}/contents/{quoted}")
    data = resp.data
    if resp.status != 200 or not isinstance(data, dict) or data.get("type") != "file":
        return None
    if data.get("encoding") != "base64" or not data.get("content"):
        return None
    try:
        return base64.b64decode(data["content"]).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def fetch_readme_size(client: GitHubClient, full_name: str) -> int | None:
    """Size in bytes of a repository's README, 0 when it has none, None when unknown."""
    resp = client.get(f"/repos/{full_name}/readme")
    if resp.status == 404:
        return 0
    if resp.status == 200 and isinstance(resp.data, dict):
        size = resp.data.get("size")
        return int(size) if isinstance(size, int) else None
    return None
