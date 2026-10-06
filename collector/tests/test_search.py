from __future__ import annotations

import re
import urllib.parse

from collector.search import RESULT_CAP, code_search

from .conftest import json_response


def corpus(n_files: int, *, repos: int = 400, step: int = 37) -> list[dict]:
    """Fake indexed files with spread-out sizes, spread over a number of repositories."""
    files = []
    for i in range(n_files):
        rid = i % repos
        files.append(
            {
                "size": (i * step) % 380_000,
                "item": {
                    "name": "package.json",
                    "path": f"dir{i}/package.json",
                    "repository": {"id": 1000 + rid, "full_name": f"owner{rid}/repo{rid}"},
                    "text_matches": [{"fragment": '"@fal-ai/client": "^1.2.0"'}],
                },
            }
        )
    return files


def search_handler(files: list[dict], *, total_inflation: int = 0, strict_end: bool = False):
    """Answers /search/code like GitHub: size filters, 100 per page, 1,000 result cap."""

    def handler(request):
        params = dict(urllib.parse.parse_qsl(request.url.query.decode()))
        q, page, per_page = params["q"], int(params["page"]), int(params["per_page"])
        lo, hi = 0, None
        m = re.search(r"size:(\d+)\.\.(\d+)", q)
        if m:
            lo, hi = int(m.group(1)), int(m.group(2))
        m = re.search(r"size:>=(\d+)", q)
        if m:
            lo = int(m.group(1))
        hits = [f["item"] for f in files if f["size"] >= lo and (hi is None or f["size"] <= hi)]
        start = (page - 1) * per_page
        if start >= RESULT_CAP or (strict_end and page > 1 and start >= len(hits)):
            return json_response(
                422, {"message": "Only the first 1000 search results are available"}
            )
        visible = hits[:RESULT_CAP]
        chunk = visible[(page - 1) * per_page : page * per_page]
        return json_response(
            200,
            {
                "total_count": len(hits) + total_inflation,
                "incomplete_results": False,
                "items": chunk,
            },
        )

    return handler


def test_small_query_is_paged_without_slicing(make_client):
    files = corpus(250)
    client, rec = make_client(search_handler(files))
    report, items = code_search(client, "js-client", '"@fal-ai/client" filename:package.json')
    assert not report.sliced and report.total_count == 250
    assert len(items) == 250 and len(rec.requests) == 3
    assert rec.requests[0].headers["accept"] == "application/vnd.github.text-match+json"


def test_large_query_is_sliced_and_every_file_found_once(make_client):
    files = corpus(3_500)
    client, _ = make_client(search_handler(files))
    report, items = code_search(client, "js-client", '"@fal-ai/client" filename:package.json')
    assert report.sliced
    assert report.total_count == 3_500
    assert report.sum_of_slice_totals == 3_500
    assert len(items) == 3_500
    assert len({(i["repository"]["id"], i["path"]) for i in items}) == 3_500
    assert all(s.total_count <= RESULT_CAP for s in report.slices)
    assert report.truncated_slices == 0
    assert report.repos == 400


def test_dedupes_files_seen_twice(make_client):
    files = corpus(120)
    files.append(dict(files[0]))  # the same (repository, path) again
    client, _ = make_client(search_handler(files))
    _, items = code_search(client, "q", "x")
    assert len(items) == 120


def test_sampled_query_reads_only_the_allowed_pages(make_client):
    files = corpus(5_000)
    client, rec = make_client(search_handler(files))
    report, items = code_search(client, "model-flux", '"fal-ai/flux"', max_pages=3)
    assert report.sampled and not report.sliced
    assert report.total_count == 5_000
    assert len(items) == 300 and len(rec.requests) == 3


def test_a_page_past_the_end_stops_paging_instead_of_failing(make_client):
    files = corpus(150)
    # Page 1 claims 950 results, then the total shrinks and page 3 answers 422.
    client, rec = make_client(search_handler(files, total_inflation=800, strict_end=True))
    report, items = code_search(client, "q", "x")
    assert not report.sliced and len(items) == 150
    statuses = [rec.handler(r).status_code for r in rec.requests]
    assert statuses[-1] == 422 and len(rec.requests) == 3


def growing_handler(files: list[dict], *, page1_total: int):
    """GitHub's estimate grows while paging: page 1 says page1_total, later pages say more."""
    base = search_handler(files)

    def handler(request):
        resp = base(request)
        if resp.status_code != 200:
            return resp
        import json as _json

        body = _json.loads(resp.content)
        params = dict(urllib.parse.parse_qsl(request.url.query.decode()))
        if int(params["page"]) == 1 and "size:" not in params["q"]:
            body["total_count"] = min(body["total_count"], page1_total)
        return json_response(200, body)

    return handler


def test_paging_continues_while_pages_are_full(make_client):
    # 760 matching files, but page 1 claims 650: a fixed page count would stop at page 7.
    files = corpus(760)
    client, rec = make_client(growing_handler(files, page1_total=650))
    report, items = code_search(client, "q", "x")
    assert len(items) == 760
    assert report.total_count == 760 and not report.sliced
    assert len(rec.requests) == 8


def test_an_unsliced_query_that_grows_past_the_cap_is_sliced(make_client):
    files = corpus(1_400)
    client, _ = make_client(growing_handler(files, page1_total=900))
    report, items = code_search(client, "q", "x")
    assert report.sliced
    assert len(items) == 1_400 and report.truncated_slices == 0


def test_a_budget_limit_truncates_the_query_instead_of_failing(make_client, monkeypatch):
    from collector import search

    monkeypatch.setattr(search, "MAX_REQUESTS_PER_QUERY", 12)
    files = corpus(3_500)
    client, rec = make_client(search_handler(files))
    report, items = code_search(client, "q", "x")
    assert report.budget_exhausted and report.incomplete
    assert report.unvisited  # the size ranges it never read are listed
    assert len(rec.requests) == 12 and 0 < len(items) < 3_500


def test_repository_search_is_sliced_by_creation_day(make_client):
    import datetime as dt

    from collector.search import repo_search

    repos = [
        {"id": i, "full_name": f"o/r{i}", "created_at": f"2025-{1 + i % 12:02d}-{1 + i % 28:02d}"}
        for i in range(2_300)
    ]

    def handler(request):
        params = dict(urllib.parse.parse_qsl(request.url.query.decode()))
        q, page = params["q"], int(params["page"])
        m = re.search(r"created:(\d{4}-\d\d-\d\d)\.\.(\d{4}-\d\d-\d\d)", q)
        hits = [r for r in repos if not m or m.group(1) <= r["created_at"] <= m.group(2)]
        if (page - 1) * 100 >= RESULT_CAP:
            return json_response(422, {"message": "Only the first 1000 search results"})
        chunk = hits[:RESULT_CAP][(page - 1) * 100 : page * 100]
        return json_response(200, {"total_count": len(hits), "items": chunk})

    client, _ = make_client(handler)
    report, items = repo_search(client, "mention", "fal.ai in:readme", today=dt.date(2026, 10, 6))
    assert report.sliced and len(items) == 2_300
    assert all(s.total_count <= RESULT_CAP for s in report.slices)


def test_paging_continues_on_full_pages_even_when_every_total_is_low(make_client):
    # Every page claims 650, yet there are 760 files: only "the last page was full" helps.
    files = corpus(760)
    base = search_handler(files)

    def handler(request):
        resp = base(request)
        if resp.status_code != 200:
            return resp
        import json as _json

        body = _json.loads(resp.content)
        body["total_count"] = 650
        return json_response(200, body)

    client, rec = make_client(handler)
    _, items = code_search(client, "q", "x")
    assert len(items) == 760 and len(rec.requests) == 8
