"""Code and repository search with paging, and slicing past the 1,000 result cap.

Both searches return at most 1,000 results per query, have no stable order, and report
a total_count that is an estimate and can grow between pages. So:
- paging continues while pages come back full, not only up to page 1's total;
- the largest total seen on any page is the slice's total;
- a query or slice whose total passes 1,000 is split (code search by file size,
  size:lo..hi; repository search by creation day, created:a..b), each slice its own search;
- results are deduped (code: repository and path; repositories: id), and the sum of
  slice totals is kept next to the unsliced total so coverage is shown, not assumed;
- a runaway query stops at MAX_REQUESTS_PER_QUERY and reports what it did not reach,
  instead of ending the whole run.
"""

from __future__ import annotations

import datetime as dt
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
FIRST_CREATED = dt.date(2008, 1, 1)  # GitHub's launch year; no repository is older.


class BudgetExhausted(Exception):
    """A single query used up its request budget."""


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
    # Set when the query hit MAX_REQUESTS_PER_QUERY; lists the slices it never read.
    budget_exhausted: bool = False
    unvisited: list[str] = field(default_factory=list)
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

    def spend(self) -> None:
        if self.used >= self.limit:
            raise BudgetExhausted
        self.used += 1


@dataclass
class _Paged:
    items: list[dict]
    pages: int
    total: int  # the largest total_count seen on any page
    full_to_cap: bool  # the last allowed page came back full: there may be more


Fetch = Callable[[str, int, bool], dict]


def _first_page(fetch: Fetch, q: str) -> dict:
    """Page 1, asked again when GitHub says the search timed out before finishing."""
    data = fetch(q, 1, False)
    tries = 0
    while data.get("incomplete_results") and tries < MAX_INCOMPLETE_RETRIES:
        tries += 1
        data = fetch(q, 1, True)
    return data


def _page_through(fetch: Fetch, q: str, first: dict, max_pages: int) -> _Paged:
    items = list(first.get("items", []))
    total = int(first.get("total_count", 0))
    last_full = len(first.get("items", [])) >= PER_PAGE
    page = 1
    while page < max_pages and (page * PER_PAGE < min(total, RESULT_CAP) or last_full):
        try:
            data = fetch(q, page + 1, False)
        except GitHubError as exc:
            # A page past the end of the results answers 422.
            if exc.status == 422:
                break
            raise
        page += 1
        batch = data.get("items", [])
        total = max(total, int(data.get("total_count", 0)))
        if not batch:
            break
        items.extend(batch)
        last_full = len(batch) >= PER_PAGE
    return _Paged(items, page, total, page >= max_pages and last_full)


def _code_fetcher(client: GitHubClient, budget: _Budget) -> Fetch:
    def fetch(q: str, page: int, refresh: bool) -> dict:
        budget.spend()
        resp = client.get(
            "/search/code",
            {"q": q, "per_page": PER_PAGE, "page": page},
            bucket="code_search",
            accept=TEXT_MATCH,
            refresh=refresh,
        )
        if resp.status != 200 or not isinstance(resp.data, dict):
            message = (resp.data or {}).get("message", "") if isinstance(resp.data, dict) else ""
            raise GitHubError(resp.status, resp.url, message or "code search failed")
        return resp.data

    return fetch


def _repo_fetcher(client: GitHubClient, budget: _Budget) -> Fetch:
    def fetch(q: str, page: int, refresh: bool) -> dict:
        budget.spend()
        resp = client.get(
            "/search/repositories",
            {"q": q, "per_page": PER_PAGE, "page": page, "sort": "stars", "order": "desc"},
            bucket="search",
            refresh=refresh,
        )
        if resp.status != 200 or not isinstance(resp.data, dict):
            message = (resp.data or {}).get("message", "") if isinstance(resp.data, dict) else ""
            raise GitHubError(resp.status, resp.url, message or "repository search failed")
        return resp.data

    return fetch


def _search(
    report: QueryReport,
    fetch: Fetch,
    q: str,
    key: Callable[[dict], Any],
    whole_range: tuple[Any, Any],
    split: Callable[[Any, Any], tuple[tuple[Any, Any], tuple[Any, Any]] | None],
    qualifier: Callable[[Any, Any], str],
    tail: tuple[Any, Any] | None,
    on_slice: Callable[[SliceReport], None] | None,
    max_pages: int | None,
) -> list[dict]:
    """Shared paging and slicing for both searches."""
    seen: set = set()
    items: list[dict] = []

    def keep(batch: list[dict]) -> int:
        added = 0
        for item in batch:
            k = key(item)
            if k not in seen:
                seen.add(k)
                items.append(item)
                added += 1
        return added

    def note(s: SliceReport) -> None:
        report.slices.append(s)
        if on_slice:
            on_slice(s)

    stack: list[tuple[Any, Any]] = []
    try:
        first = _first_page(fetch, q)
        report.total_count = int(first.get("total_count", 0))
        report.incomplete = bool(first.get("incomplete_results"))

        if max_pages is not None:
            paged = _page_through(fetch, q, first, min(max_pages, MAX_PAGES))
            report.total_count = max(report.total_count, paged.total)
            fetched = keep(paged.items)
            report.sampled = report.total_count > len(paged.items)
            note(
                SliceReport(
                    "", report.total_count, paged.pages, fetched, incomplete=report.incomplete
                )
            )
            report.sum_of_slice_totals = report.total_count
            return items

        if report.total_count <= RESULT_CAP:
            paged = _page_through(fetch, q, first, MAX_PAGES)
            report.total_count = max(report.total_count, paged.total)
            keep(paged.items)
            if report.total_count <= RESULT_CAP and not paged.full_to_cap:
                note(
                    SliceReport(
                        "",
                        report.total_count,
                        paged.pages,
                        len(items),
                        incomplete=report.incomplete,
                    )
                )
                report.sum_of_slice_totals = report.total_count
                return items
            # The total grew past the cap while paging: slice after all.

        report.sliced = True
        if tail is not None:
            stack.append(tail)
        stack.append(whole_range)
        while stack:
            lo, hi = stack.pop()
            label = qualifier(lo, hi)
            sliced_q = f"{q} {label}"
            page_one = _first_page(fetch, sliced_q)
            total = int(page_one.get("total_count", 0))
            incomplete = bool(page_one.get("incomplete_results"))
            halves = split(lo, hi)
            if total > RESULT_CAP and halves:
                stack.append(halves[1])
                stack.append(halves[0])
                continue
            if total == 0:
                note(SliceReport(label, 0, 1, 0, incomplete=incomplete))
                continue
            paged = _page_through(fetch, sliced_q, page_one, MAX_PAGES)
            fetched = keep(paged.items)
            total = max(total, paged.total)
            if (total > RESULT_CAP or paged.full_to_cap) and halves:
                # It grew past the cap while paging: read the halves too (dedupe makes
                # the overlap harmless).
                stack.append(halves[1])
                stack.append(halves[0])
                continue
            truncated = total > RESULT_CAP or paged.full_to_cap
            report.sum_of_slice_totals += total
            report.truncated_slices += int(truncated)
            report.incomplete = report.incomplete or incomplete
            note(
                SliceReport(
                    label, total, paged.pages, fetched, truncated=truncated, incomplete=incomplete
                )
            )
    except BudgetExhausted:
        report.budget_exhausted = True
        report.incomplete = True
        report.unvisited = [qualifier(lo, hi) for lo, hi in reversed(stack)]
    return items


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

    def split(lo: int, hi: int | None):
        if hi is None or hi <= lo:
            return None
        mid = (lo + hi) // 2
        return (lo, mid), (mid + 1, hi)

    def qualifier(lo: int, hi: int | None) -> str:
        return f"size:>={lo}" if hi is None else f"size:{lo}..{hi}"

    items = _search(
        report,
        _code_fetcher(client, budget),
        q,
        key=lambda i: (int(i["repository"]["id"]), i["path"]),
        whole_range=(0, MAX_INDEXED_BYTES - 1),
        split=split,
        qualifier=qualifier,
        # Ranges are inclusive. The tail checks that nothing sits above the index limit.
        tail=(MAX_INDEXED_BYTES, None),
        on_slice=on_slice,
        max_pages=max_pages,
    )
    report.files = len(items)
    report.repos = len({int(i["repository"]["id"]) for i in items})
    report.requests = budget.used
    return report, items


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

    def split(lo: dt.date, hi: dt.date):
        if hi <= lo:
            return None
        mid = lo + (hi - lo) // 2
        return (lo, mid), (mid + dt.timedelta(days=1), hi)

    def qualifier(lo: dt.date, hi: dt.date) -> str:
        return f"created:{lo.isoformat()}..{hi.isoformat()}"

    items = _search(
        report,
        _repo_fetcher(client, budget),
        q,
        key=lambda i: int(i["id"]),
        whole_range=(FIRST_CREATED, today),
        split=split,
        qualifier=qualifier,
        tail=None,
        on_slice=on_slice,
        max_pages=None,
    )
    report.files = len(items)
    report.repos = len(items)
    report.requests = budget.used
    return report, items
