from __future__ import annotations

import json
import urllib.parse

import httpx
import pytest

from collector import pipeline, runlog
from collector.config import Paths
from collector.github import GitHubError

from .conftest import FakeClock, json_response

REPOS = {
    "alice/studio": dict(
        id=1,
        stars=40,
        fork=False,
        desc="An image studio on fal",
        pushed="2026-10-01T10:00:00Z",
        login="alice",
        otype="User",
    ),
    "bob/nextjs-starter": dict(
        id=2,
        stars=2,
        fork=False,
        desc="Starter template",
        pushed="2026-09-20T10:00:00Z",
        login="bob",
        otype="User",
    ),
    "carol/studio": dict(
        id=3,
        stars=0,
        fork=True,
        desc=None,
        pushed="2025-01-01T10:00:00Z",
        login="carol",
        otype="User",
    ),
    "acme/bot": dict(
        id=4,
        stars=9,
        fork=False,
        desc="Discord bot",
        pushed="2026-05-01T10:00:00Z",
        login="acme",
        otype="Organization",
    ),
}
FRAGMENTS = {
    "alice/studio": '"@fal-ai/client": "^1.10.1"',
    "bob/nextjs-starter": '"@fal-ai/client": "^1.2.0"',
    "carol/studio": '"@fal-ai/client": "^1.10.1"',
    "acme/bot": '"@fal-ai/client": "1.9.0"',
}


def repo_body(name: str) -> dict:
    r = REPOS[name]
    owner = name.split("/")[0]
    return {
        "id": r["id"],
        "name": name.split("/")[1],
        "full_name": name,
        "html_url": f"https://github.com/{name}",
        "description": r["desc"],
        "stargazers_count": r["stars"],
        "forks_count": 1,
        "language": "TypeScript",
        "topics": ["nextjs", "fal"],
        "license": {"spdx_id": "MIT"},
        "homepage": "",
        "created_at": "2026-09-01T00:00:00Z",
        "pushed_at": r["pushed"],
        "fork": r["fork"],
        "archived": False,
        "is_template": False,
        "owner": {
            "login": owner,
            "type": r["otype"],
            "avatar_url": f"https://avatars.githubusercontent.com/u/{r['id']}?v=4",
            "html_url": f"https://github.com/{owner}",
        },
    }


class FakeGitHub:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.not_modified = False
        self.fail_repo: str | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/rate_limit":
            res = {
                k: {"limit": v, "remaining": v, "reset": 1_900_000_000}
                for k, v in (("core", 5000), ("search", 30), ("code_search", 10))
            }
            return json_response(200, {"resources": res})
        if path == "/search/code":
            q = dict(urllib.parse.parse_qsl(request.url.query.decode()))["q"]
            items = []
            if "@fal-ai/client" in q:
                for name in REPOS:
                    items.append(
                        {
                            "name": "package.json",
                            "path": "package.json",
                            "repository": {"id": REPOS[name]["id"], "full_name": name},
                            "text_matches": [{"fragment": FRAGMENTS[name]}],
                        }
                    )
                items.append(
                    {
                        "name": "page.tsx",
                        "path": "app/page.tsx",
                        "repository": {"id": 1, "full_name": "alice/studio"},
                        "text_matches": [{"fragment": 'fal.subscribe("fal-ai/flux/dev", {'}],
                    }
                )
            return json_response(
                200, {"total_count": len(items), "incomplete_results": False, "items": items}
            )
        if path.startswith("/repos/"):
            name = path[len("/repos/") :]
            if name == self.fail_repo:
                return json_response(502, {"message": "Server Error"})
            if self.not_modified and request.headers.get("if-none-match"):
                return json_response(304, {})
            return json_response(200, repo_body(name), {"etag": f'W/"{name}"'})
        if path.startswith("/users/"):
            login = path[len("/users/") :]
            return json_response(
                200,
                {
                    "login": login,
                    "type": "User",
                    "name": f"{login.title()} Example",
                    "location": "Istanbul",
                    "email": f"{login}@example.com",
                    "blog": "https://x.example",
                },
                {"etag": f'W/"u-{login}"'},
            )
        return json_response(404, {"message": "Not Found"})


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GH_SEARCH_TOKEN", "test-" + "token")
    paths = Paths(tmp_path)
    (tmp_path / "data").mkdir()
    return paths


def run(paths, fake, *, date="2026-10-05", clock=None, **options):
    clock = clock or FakeClock()
    return pipeline.run(
        paths,
        pipeline.RunOptions(only=["js-client"], **options),
        transport=httpx.MockTransport(fake),
        sleep=clock.sleep,
        now=clock.now,
        log=lambda _m: None,
        run_date=date,
    )


def load(paths):
    return {r["full_name"]: r for r in json.loads(paths.repos.read_text())}


def test_first_run_builds_records(env):
    fake = FakeGitHub()
    summary = run(env, fake)
    repos = load(env)
    assert set(repos) == set(REPOS)
    a = repos["alice/studio"]
    assert a["evidence"] == "code" and a["clients"] == ["js"]
    assert a["models"] == ["flux"]
    assert a["sources"] == ["code:@fal-ai/client"]
    assert a["notable"] and a["active"] and a["kind"] == "app"
    assert a["first_seen"] == "2026-10-05" and a["stars_history"] == [
        {"date": "2026-10-05", "stars": 40}
    ]
    assert a["stack"] == ["next"] and a["license"] == "MIT" and a["homepage"] is None
    assert repos["bob/nextjs-starter"]["kind"] == "template"
    assert repos["carol/studio"]["kind"] == "fork" and not repos["carol/studio"]["notable"]
    assert not repos["acme/bot"]["active"]
    assert summary["totals"]["code_tier"] == 4 and summary["totals"]["code_tier_non_fork"] == 3
    assert summary["queries"][0]["files"] == 5 and summary["queries"][0]["repos"] == 4


def test_people_data_is_limited_to_public_profile_fields(env):
    run(env, FakeGitHub())
    owner = load(env)["alice/studio"]["owner"]
    assert set(owner) == {"login", "type", "name", "location", "avatar_url", "html_url"}
    raw = env.repos.read_text()
    assert "example.com" not in raw and "x.example" not in raw


def test_exclusions_drop_repos_and_owners(env):
    env.exclusions.write_text(json.dumps({"repos": ["Bob/NextJS-Starter"], "owners": ["acme"]}))
    fake = FakeGitHub()
    run(env, fake)
    assert set(load(env)) == {"alice/studio", "carol/studio"}
    looked_up = {r.url.path for r in fake.requests}
    assert "/repos/acme/bot" not in looked_up and "/users/acme" not in looked_up


def test_etags_file_holds_only_repo_and_user_urls(env):
    run(env, FakeGitHub())
    etags = json.loads(env.etags.read_text())
    assert etags and all("/repos/" in u or "/users/" in u for u in etags)


def test_run_log_records_queries_and_finish(env):
    run(env, FakeGitHub())
    events = runlog.read_events(env.runs / "2026-10-05.jsonl")
    names = [e["event"] for e in events]
    assert names[0] == "run_started" and names[-1] == "run_finished"
    assert "query_finished" in names and "slice" in names
    assert json.loads((env.runs / "2026-10-05.json").read_text())["totals"]["repos"] == 4


def test_week_two_with_304s_keeps_records_and_recomputes_dates(env, tmp_path):
    run(env, FakeGitHub())
    # Week two: no cache on the runner, only etags.json, and GitHub says nothing changed.
    import shutil

    shutil.rmtree(env.cache)
    fake = FakeGitHub()
    fake.not_modified = True
    run(env, fake, date="2027-01-18")
    repos = load(env)
    a = repos["alice/studio"]
    assert a["first_seen"] == "2026-10-05" and a["last_seen"] == "2027-01-18"
    assert [p["date"] for p in a["stars_history"]] == ["2026-10-05", "2027-01-18"]
    # Pushed on 1 Oct 2026: active in October, not 109 days later, with no change on GitHub.
    assert not a["active"]
    assert a["owner"]["name"] == "Alice Example"
    statuses = [
        r.headers.get("if-none-match") for r in fake.requests if r.url.path.startswith("/repos/")
    ]
    assert all(statuses)


def test_failed_run_leaves_data_alone_and_resume_replays_the_cache(env):
    fake = FakeGitHub()
    fake.fail_repo = "carol/studio"
    with pytest.raises(GitHubError):
        run(env, fake)
    assert not env.repos.exists()
    assert runlog.latest_unfinished(env.runs) == "2026-10-05"

    retry = FakeGitHub()
    run(env, retry, resume=True)
    searched = [r for r in retry.requests if r.url.path == "/search/code"]
    assert searched == []
    assert set(load(env)) == set(REPOS)
    assert runlog.latest_unfinished(env.runs) is None


def test_resume_without_an_unfinished_run_is_refused(env):
    with pytest.raises(pipeline.NothingToResume):
        run(env, FakeGitHub(), resume=True)


def test_missing_token_is_a_clear_error(env, monkeypatch):
    monkeypatch.delenv("GH_SEARCH_TOKEN")
    from collector.config import MissingToken

    with pytest.raises(MissingToken):
        run(env, FakeGitHub())


def test_unknown_query_ids_are_refused():
    with pytest.raises(ValueError):
        pipeline.select_queries(["nope"])
