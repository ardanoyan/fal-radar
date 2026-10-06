from __future__ import annotations

import pytest

from collector.github import AuthError, OfflineMiss, RateLimitGiveUp

from .conftest import FAKE_TOKEN, json_response


def test_sends_auth_version_and_user_agent(make_client):
    client, rec = make_client(lambda r: json_response(200, {"ok": True}))
    client.get("/repos/a/b")
    req = rec.requests[0]
    assert req.headers["authorization"] == f"Bearer {FAKE_TOKEN}"
    assert req.headers["x-github-api-version"] == "2022-11-28"
    assert req.headers["user-agent"].startswith("fal-radar/")
    assert req.headers["accept"] == "application/vnd.github+json"


def test_token_is_not_written_to_the_cache(make_client, tmp_path):
    client, _ = make_client(lambda r: json_response(200, {"ok": True}, {"etag": 'W/"1"'}))
    client.get("/repos/a/b")
    import gzip

    blobs = [gzip.decompress(p.read_bytes()) for p in (tmp_path / "cache").rglob("*.gz")]
    assert blobs and all(FAKE_TOKEN.encode() not in b for b in blobs)


def test_same_run_replays_from_cache(make_client):
    client, rec = make_client(lambda r: json_response(200, {"n": 1}))
    first = client.get("/repos/a/b")
    second = client.get("/repos/a/b")
    assert len(rec.requests) == 1
    assert second.from_cache and second.data == first.data


def test_later_run_revalidates_with_etag_and_keeps_body_on_304(make_client):
    client, _ = make_client(lambda r: json_response(200, {"n": 1}, {"etag": 'W/"abc"'}))
    client.get("/repos/a/b")

    def handler(request):
        assert request.headers.get("if-none-match") == 'W/"abc"'
        return json_response(304, {})

    later, rec = make_client(handler, run_id="2026-10-12")
    resp = later.get("/repos/a/b")
    assert len(rec.requests) == 1
    assert resp.not_modified and resp.status == 200 and resp.data == {"n": 1}


def test_etag_file_is_used_when_the_cache_is_empty(make_client):
    url = "https://api.github.com/repos/a/b"
    seen = {}

    def handler(request):
        seen["etag"] = request.headers.get("if-none-match")
        return json_response(304, {})

    client, _ = make_client(handler, etags={url: 'W/"from-file"'})
    resp = client.get("/repos/a/b", conditional=True)
    assert seen["etag"] == 'W/"from-file"'
    assert resp.not_modified and resp.data is None


def test_saved_etags_are_not_sent_unless_the_caller_can_handle_a_bodiless_304(make_client):
    url = "https://api.github.com/repos/a/b"
    client, rec = make_client(lambda r: json_response(200, {"n": 1}), etags={url: 'W/"x"'})
    resp = client.get("/repos/a/b")
    assert "if-none-match" not in rec.requests[0].headers and resp.data == {"n": 1}


def test_search_errors_are_not_cached(make_client):
    answers = [json_response(403, {"message": "Forbidden"}), json_response(200, {"items": []})]
    client, rec = make_client(lambda r: answers.pop(0))
    assert client.get("/search/code", {"q": "x"}, bucket="code_search").status == 403
    assert client.get("/search/code", {"q": "x"}, bucket="code_search").status == 200
    assert len(rec.requests) == 2


def test_search_requests_never_send_if_none_match(make_client):
    client, rec = make_client(
        lambda r: json_response(200, {"total_count": 0, "items": []}),
        etags={"https://api.github.com/search/code?page=1&q=x": 'W/"1"'},
    )
    client.get("/search/code", {"q": "x", "page": 1}, bucket="code_search")
    assert "if-none-match" not in rec.requests[0].headers


def test_retry_after_is_honoured_plus_five_seconds(make_client, clock):
    answers = [
        json_response(403, {"message": "secondary"}, {"retry-after": "30"}),
        json_response(200, {"ok": 1}),
    ]
    client, rec = make_client(lambda r: answers.pop(0))
    resp = client.get("/repos/a/b")
    assert resp.status == 200 and len(rec.requests) == 2
    assert 35 in clock.sleeps


def test_primary_limit_waits_until_reset(make_client, clock):
    reset = int(clock.now()) + 600
    answers = [
        json_response(
            403,
            {"message": "API rate limit exceeded"},
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)},
        ),
        json_response(200, {"ok": 1}),
    ]
    client, _ = make_client(lambda r: answers.pop(0))
    client.get("/repos/a/b")
    assert any(590 <= s <= 610 for s in clock.sleeps)


def test_secondary_limit_without_headers_backs_off_from_a_minute(make_client, clock):
    answers = [
        json_response(403, {"message": "You have exceeded a secondary rate limit."}),
        json_response(429, {"message": "You have exceeded a secondary rate limit."}),
        json_response(200, {"ok": 1}),
    ]
    client, _ = make_client(lambda r: answers.pop(0))
    client.get("/repos/a/b")
    assert 60 in clock.sleeps and 120 in clock.sleeps


def test_gives_up_after_repeated_rate_limits(make_client):
    client, _ = make_client(
        lambda r: json_response(429, {"message": "rate limit"}, {"retry-after": "1"})
    )
    with pytest.raises(RateLimitGiveUp):
        client.get("/repos/a/b")


def test_plain_403_and_404_are_returned_not_retried(make_client):
    client, rec = make_client(lambda r: json_response(404, {"message": "Not Found"}))
    assert client.get("/repos/gone/away").status == 404
    assert len(rec.requests) == 1
    client2, rec2 = make_client(
        lambda r: json_response(403, {"message": "Repository access blocked"})
    )
    assert client2.get("/repos/x/y").status == 403
    assert len(rec2.requests) == 1


def test_401_raises_auth_error(make_client):
    client, _ = make_client(lambda r: json_response(401, {"message": "Bad credentials"}))
    with pytest.raises(AuthError):
        client.get("/repos/a/b")


def test_server_errors_are_retried_with_backoff(make_client, clock):
    answers = [json_response(502, {}), json_response(200, {"ok": 1})]
    client, _ = make_client(lambda r: answers.pop(0))
    assert client.get("/repos/a/b").status == 200
    assert 5 in clock.sleeps


def test_code_search_is_paced_at_ten_per_minute(make_client, clock):
    client, _ = make_client(lambda r: json_response(200, {"total_count": 0, "items": []}))
    for page in (1, 2, 3):
        client.get("/search/code", {"q": "x", "page": page}, bucket="code_search")
    gaps = [s for s in clock.sleeps if s > 0]
    assert len(gaps) == 2 and all(g >= 6.0 for g in gaps)


def test_offline_mode_reads_cache_and_refuses_misses(make_client):
    client, _ = make_client(lambda r: json_response(200, {"n": 1}))
    client.get("/repos/a/b")
    offline, rec = make_client(lambda r: json_response(500, {}), run_id="2026-10-12", offline=True)
    assert offline.get("/repos/a/b").data == {"n": 1}
    with pytest.raises(OfflineMiss):
        offline.get("/repos/not/cached")
    assert rec.requests == []
