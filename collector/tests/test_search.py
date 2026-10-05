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
