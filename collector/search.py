"""Code search with paging, and slicing by file size past the 1,000 result cap.

GET /search/code returns at most 1,000 results per query and has no stable order.
When a query reports more, it is split into file size ranges (size:lo..hi), each range
bisected until it holds 1,000 results or fewer. Results are deduped on (repository,
path), and the sum of the slice totals is kept next to the unsliced total so the
coverage can be shown, not assumed.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from .github import GitHubClient, GitHubError

TEXT_MATCH = "application/vnd.github.text-match+json"
PER_PAGE = 100
RESULT_CAP = 1000
MAX_PAGES = RESULT_CAP // PER_PAGE
# The code search index only holds files smaller than 384 KB.
MAX_INDEXED_BYTES = 384 * 1024
MAX_INCOMPLETE_RETRIES = 2
MAX_REQUESTS_PER_QUERY = 600


@dataclass
class SliceReport:
    qualifier: str
    total_count: int
    pages: int
    fetched: int
    truncated: bool = False
    incomplete: bool = False


@dataclass
class QueryReport:
    id: str
    q: str
    kind: str = "code"
    total_count: int = 0
    sliced: bool = False
    sampled: bool = False
    incomplete: bool = False
    slices: list[SliceReport] = field(default_factory=list)
    sum_of_slice_totals: int = 0
    files: int = 0
    repos: int = 0
    truncated_slices: int = 0
    requests: int = 0
    # Filled in by the pipeline: hits whose fragments contain the literal string,
    # hits that do not (dropped), and hits in documentation files (mention tier).
    verified_files: int = 0
    unverified_files: int = 0
    doc_files: int = 0
    counted_repos: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _Budget:
    def __init__(self, limit: int) -> None:
        self.used = 0
        self.limit = limit

    def spend(self, q: str) -> None:
        self.used += 1
        if self.used > self.limit:
            raise GitHubError(
                0, "/search/code", f"query needs more than {self.limit} requests: {q}"
            )


def _fetch(
    client: GitHubClient, q: str, page: int, budget: _Budget, *, refresh: bool = False
) -> dict:
    budget.spend(q)
    resp = client.get(
        "/search/code",
        {"q": q, "per_page": PER_PAGE, "page": page},
        bucket="code_search",
        accept=TEXT_MATCH,
        refresh=refresh,
    )
    if resp.status != 200 or not isinstance(resp.data, dict):
        message = (resp.data or {}).get("message", "") if isinstance(resp.data, dict) else ""
        raise GitHubError(resp.status, resp.url, message or "code search did not return results")
    return resp.data


def _first_page(client: GitHubClient, q: str, budget: _Budget) -> dict:
    """Page 1, asked again when GitHub says the search timed out before finishing."""
    data = _fetch(client, q, 1, budget)
    tries = 0
    while data.get("incomplete_results") and tries < MAX_INCOMPLETE_RETRIES:
        tries += 1
        data = _fetch(client, q, 1, budget, refresh=True)
    return data


def _page_through(
    client: GitHubClient, q: str, first: dict, budget: _Budget, max_pages: int
) -> tuple[list[dict], int]:
    items = list(first.get("items", []))
    total = int(first.get("total_count", 0))
    last_page = min(math.ceil(min(total, RESULT_CAP) / PER_PAGE), max_pages)
    pages = 1
    for page in range(2, last_page + 1):
        try:
            data = _fetch(client, q, page, budget)
        except GitHubError as exc:
            # The total can shrink between pages; a page past the end answers 422.
            if exc.status == 422:
                break
            raise
        pages += 1
        batch = data.get("items", [])
        if not batch:
            break
        items.extend(batch)
    return items, pages


def code_search(
    client: GitHubClient,
    query_id: str,
    q: str,
    *,
    max_pages: int | None = None,
    on_slice: Callable[[SliceReport], None] | None = None,
) -> tuple[QueryReport, list[dict]]:
    """Run one code search to completion. Returns the report and the deduped items.

    With max_pages set the query is sampled: only that many pages of the unsliced
    query are read, and the report says so.
    """
    report = QueryReport(id=query_id, q=q)
    budget = _Budget(MAX_REQUESTS_PER_QUERY)
    seen: set[tuple[int, str]] = set()
    items: list[dict] = []

    def keep(batch: list[dict]) -> int:
        added = 0
        for item in batch:
            key = (int(item["repository"]["id"]), item["path"])
            if key not in seen:
                seen.add(key)
                items.append(item)
                added += 1
        return added

    def note(slice_report: SliceReport) -> None:
        report.slices.append(slice_report)
        if on_slice:
            on_slice(slice_report)

    first = _first_page(client, q, budget)
    report.total_count = int(first.get("total_count", 0))
    report.incomplete = bool(first.get("incomplete_results"))

    if max_pages is not None or report.total_count <= RESULT_CAP:
        limit = min(max_pages, MAX_PAGES) if max_pages is not None else MAX_PAGES
        batch, pages = _page_through(client, q, first, budget, limit)
        fetched = keep(batch)
        report.sampled = max_pages is not None and report.total_count > len(batch)
        note(SliceReport("", report.total_count, pages, fetched, incomplete=report.incomplete))
        report.sum_of_slice_totals = report.total_count
    else:
        report.sliced = True
        # Ranges are inclusive. The last one checks that nothing sits above the index limit.
        stack: list[tuple[int, int | None]] = [
            (MAX_INDEXED_BYTES, None),
            (0, MAX_INDEXED_BYTES - 1),
        ]
        while stack:
            lo, hi = stack.pop()
            qualifier = f"size:>={lo}" if hi is None else f"size:{lo}..{hi}"
            sliced_q = f"{q} {qualifier}"
            page_one = _first_page(client, sliced_q, budget)
            total = int(page_one.get("total_count", 0))
            incomplete = bool(page_one.get("incomplete_results"))
            can_split = hi is not None and hi > lo
            if total > RESULT_CAP and can_split:
                mid = (lo + hi) // 2
                stack.append((mid + 1, hi))
                stack.append((lo, mid))
                continue
            if total == 0:
                note(SliceReport(qualifier, 0, 1, 0, incomplete=incomplete))
                continue
            batch, pages = _page_through(client, sliced_q, page_one, budget, MAX_PAGES)
            fetched = keep(batch)
            truncated = total > RESULT_CAP
            report.sum_of_slice_totals += total
            report.truncated_slices += int(truncated)
            report.incomplete = report.incomplete or incomplete
            note(
                SliceReport(
                    qualifier, total, pages, fetched, truncated=truncated, incomplete=incomplete
                )
            )

    report.files = len(items)
    report.repos = len({int(i["repository"]["id"]) for i in items})
    report.requests = budget.used
    return report, items


# -- repository search ----------------------------------------------------------------

FIRST_CREATED = dt.date(2008, 1, 1)  # GitHub's launch year; no repository is older.


def _repo_fetch(client: GitHubClient, q: str, page: int, budget: _Budget) -> dict:
    budget.spend(q)
    resp = client.get(
        "/search/repositories",
        {"q": q, "per_page": PER_PAGE, "page": page, "sort": "stars", "order": "desc"},
        bucket="search",
    )
    if resp.status != 200 or not isinstance(resp.data, dict):
        message = (resp.data or {}).get("message", "") if isinstance(resp.data, dict) else ""
        raise GitHubError(resp.status, resp.url, message or "repository search failed")
    return resp.data


def _repo_pages(
    client: GitHubClient, q: str, first: dict, budget: _Budget
) -> tuple[list[dict], int]:
    items = list(first.get("items", []))
    total = int(first.get("total_count", 0))
    last_page = min(math.ceil(min(total, RESULT_CAP) / PER_PAGE), MAX_PAGES)
    pages = 1
    for page in range(2, last_page + 1):
        try:
            data = _repo_fetch(client, q, page, budget)
        except GitHubError as exc:
            if exc.status == 422:
                break
            raise
        pages += 1
        batch = data.get("items", [])
        if not batch:
            break
        items.extend(batch)
    return items, pages


def repo_search(
    client: GitHubClient,
    query_id: str,
    q: str,
    *,
    today: dt.date,
    on_slice: Callable[[SliceReport], None] | None = None,
) -> tuple[QueryReport, list[dict]]:
    """GET /search/repositories to completion, sliced by creation day (UTC) past the cap."""
    report = QueryReport(id=query_id, q=q, kind="repo")
    budget = _Budget(MAX_REQUESTS_PER_QUERY)
    seen: set[int] = set()
    items: list[dict] = []

    def keep(batch: list[dict]) -> int:
        added = 0
        for item in batch:
            rid = int(item["id"])
            if rid not in seen:
                seen.add(rid)
                items.append(item)
                added += 1
        return added

    def note(s: SliceReport) -> None:
        report.slices.append(s)
        if on_slice:
            on_slice(s)

    first = _repo_fetch(client, q, 1, budget)
    report.total_count = int(first.get("total_count", 0))
    report.incomplete = bool(first.get("incomplete_results"))
    if report.total_count <= RESULT_CAP:
        batch, pages = _repo_pages(client, q, first, budget)
        note(SliceReport("", report.total_count, pages, keep(batch), incomplete=report.incomplete))
        report.sum_of_slice_totals = report.total_count
    else:
        report.sliced = True
        stack: list[tuple[dt.date, dt.date]] = [(FIRST_CREATED, today)]
        while stack:
            lo, hi = stack.pop()
            qualifier = f"created:{lo.isoformat()}..{hi.isoformat()}"
            sliced_q = f"{q} {qualifier}"
            page_one = _repo_fetch(client, sliced_q, 1, budget)
            total = int(page_one.get("total_count", 0))
            incomplete = bool(page_one.get("incomplete_results"))
            if total > RESULT_CAP and hi > lo:
                mid = lo + (hi - lo) // 2
                stack.append((mid + dt.timedelta(days=1), hi))
                stack.append((lo, mid))
                continue
            if total == 0:
                note(SliceReport(qualifier, 0, 1, 0, incomplete=incomplete))
                continue
            batch, pages = _repo_pages(client, sliced_q, page_one, budget)
            truncated = total > RESULT_CAP
            report.sum_of_slice_totals += total
            report.truncated_slices += int(truncated)
            report.incomplete = report.incomplete or incomplete
            note(
                SliceReport(
                    qualifier, total, pages, keep(batch), truncated=truncated, incomplete=incomplete
                )
            )
    report.files = len(items)
    report.repos = len(items)
    report.requests = budget.used
    return report, items
