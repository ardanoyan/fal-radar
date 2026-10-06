from __future__ import annotations

import json
import shutil
import urllib.parse

import httpx
import pytest

from collector import pipeline, runlog
from collector.config import MissingToken, Paths
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
    "dora/readme-only": dict(
        id=5,
        stars=1,
        fork=False,
        desc="Notes",
        pushed="2026-10-01T10:00:00Z",
        login="dora",
        otype="User",
    ),
    "erin/my-fal-app": dict(
        id=6,
        stars=4,
        fork=False,
        desc="My app",
        pushed="2026-10-01T10:00:00Z",
        login="erin",
        otype="User",
        template="fal-ai/fal-nextjs-template",
    ),
}

# What /search/code returns for the "@fal-ai/client" query: (repo, path, fragment).
HITS = [
    ("alice/studio", "package.json", '"@fal-ai/client": "^1.10.1"'),
    (
        "alice/studio",
        "app/page.tsx",
        'import { fal } from "@fal-ai/client";\nfal.subscribe("fal-ai/flux/dev", {',
    ),
    ("bob/nextjs-starter", "package.json", '"@fal-ai/client": "^1.2.0"'),
    ("carol/studio", "package.json", '"@fal-ai/client": "^1.10.1"'),
    ("acme/bot", "package.json", '"@fal-ai/client": "1.9.0"'),
    ("erin/my-fal-app", "package.json", '"@fal-ai/client": "^1.10.1"'),
    # A README that names the client: a mention, not code.
    ("dora/readme-only", "README.md", 'npm i "@fal-ai/client"'),
    # Same words without the literal string: GitHub ignores punctuation, the collector does not.
    ("zed/unrelated", "notes.txt", "the fal ai client was slow"),
]
IDS = {**{name: r["id"] for name, r in REPOS.items()}, "zed/unrelated": 99}


def repo_body(name: str) -> dict:
    r = REPOS[name]
    owner = name.split("/")[0]
    body = {
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
    if r.get("template"):
        body["template_repository"] = {"full_name": r["template"]}
    if r["fork"]:
        body["source"] = {"full_name": "upstream/studio"}
    return body


class FakeGitHub:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.not_modified = False
        self.fail_repo: str | None = None
        self.gone: set[str] = set()
        self.hidden_from_search: set[str] = set()

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
                for name, file_path, fragment in HITS:
                    if name in self.hidden_from_search:
                        continue
                    items.append(
                        {
                            "name": file_path.rsplit("/", 1)[-1],
                            "path": file_path,
                            "repository": {"id": IDS[name], "full_name": name},
                            "text_matches": [{"fragment": fragment}],
                        }
                    )
            body = {"total_count": len(items), "incomplete_results": False, "items": items}
            return json_response(200, body)
        if path.startswith("/repos/"):
            name = path[len("/repos/") :]
            if name == self.fail_repo:
                return json_response(502, {"message": "Server Error"})
            if name in self.gone:
                return json_response(404, {"message": "Not Found"})
            if self.not_modified and request.headers.get("if-none-match"):
                return json_response(304, {})
            return json_response(200, repo_body(name), {"etag": f'W/"{name}"'})
        if path.startswith("/users/"):
            login = path[len("/users/") :]
            profile = {
                "login": login,
                "type": "User",
                "name": f"{login.title()} Example",
                "location": "Istanbul",
                "email": f"{login}@example.com",
                "blog": "https://x.example",
            }
            return json_response(200, profile, {"etag": f'W/"u-{login}"'})
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
    run(env, FakeGitHub())
    repos = load(env)
    assert set(repos) == set(REPOS)
    a = repos["alice/studio"]
    assert a["evidence"] == "code" and a["clients"] == ["js"]
    assert a["models"] == ["flux"]
    assert a["sources"] == ["code:@fal-ai/client"]
    assert a["notable"] and a["active"] and a["kind"] == "app"
    assert a["first_seen"] == "2026-10-05"
    assert a["stars_history"] == [{"date": "2026-10-05", "stars": 40}]
    assert a["stack"] == ["next"] and a["license"] == "MIT" and a["homepage"] is None
    assert repos["bob/nextjs-starter"]["kind"] == "template"
    assert repos["carol/studio"]["kind"] == "fork" and not repos["carol/studio"]["notable"]
    assert repos["carol/studio"]["fork_source"] == "upstream/studio"
    assert repos["erin/my-fal-app"]["template_source"] == "fal-ai/fal-nextjs-template"
    assert not repos["acme/bot"]["active"]


def test_totals_and_headline(env):
    summary = run(env, FakeGitHub())
    t = summary["totals"]
    assert t["code_tier"] == 5 and t["code_tier_non_fork"] == 4 and t["mention_tier"] == 1
    # Headline: fal in the code, not a fork, not a copy of one of fal's own templates.
    assert t["headline"] == 3 and t["fal_template_copies"] == 1
    q = summary["queries"][0]
    assert q["files"] == 8
    assert q["verified_files"] == 7 and q["unverified_files"] == 1 and q["doc_files"] == 1
    assert q["counted_repos"] == 6


def test_literal_check_and_docs(env):
    run(env, FakeGitHub())
    repos = load(env)
    assert "zed/unrelated" not in repos
    d = repos["dora/readme-only"]
    assert d["evidence"] == "mention" and d["clients"] == [] and d["models"] == []
    assert d["sources"] == ["code:@fal-ai/client:docs"]


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
    assert set(load(env)) == set(REPOS) - {"bob/nextjs-starter", "acme/bot"}
    looked_up = {r.url.path for r in fake.requests}
    assert "/repos/acme/bot" not in looked_up and "/users/acme" not in looked_up


def test_etags_file_holds_only_repo_and_user_urls(env):
    run(env, FakeGitHub())
    etags = json.loads(env.etags.read_text())
    assert etags and all("/repos/" in u or "/users/" in u for u in etags)


def test_run_log_records_queries_facts_and_finish(env):
    run(env, FakeGitHub())
    events = runlog.read_events(env.runs / "2026-10-05.jsonl")
    names = [e["event"] for e in events]
    assert names[0] == "run_started" and names[-1] == "run_finished"
    assert "query_finished" in names and "slice" in names
    assert any("4,000" in fact for fact in events[0]["api_facts"])
    summary = json.loads((env.runs / "2026-10-05.json").read_text())
    assert summary["totals"]["repos"] == 6
    assert any("ETag" in fact for fact in summary["api_facts"])


def test_week_two_with_304s_keeps_records_and_recomputes_dates(env):
    run(env, FakeGitHub())
    # A later week: no cache on the runner, only etags.json, and GitHub says nothing changed.
    shutil.rmtree(env.cache)
    fake = FakeGitHub()
    fake.not_modified = True
    run(env, fake, date="2027-01-18")
    a = load(env)["alice/studio"]
    assert a["first_seen"] == "2026-10-05" and a["last_seen"] == "2027-01-18"
    assert [p["date"] for p in a["stars_history"]] == ["2026-10-05", "2027-01-18"]
    # Pushed on 1 Oct 2026: active in October, not 109 days later, with no change on GitHub.
    assert not a["active"]
    assert a["owner"]["name"] == "Alice Example"
    sent = [
        r.headers.get("if-none-match") for r in fake.requests if r.url.path.startswith("/repos/")
    ]
    assert sent and all(sent)


def test_gone_repos_are_recorded_and_skipped_next_time(env):
    run(env, FakeGitHub())

    # Week 2: acme/bot was deleted. Search no longer returns it; the lookup answers 404.
    week2 = FakeGitHub()
    week2.gone = {"acme/bot"}
    week2.hidden_from_search = {"acme/bot"}
    run(env, week2, date="2026-10-12")
    assert json.loads(env.gone.read_text()) == {
        "acme/bot": {"id": 4, "status": 404, "since": "2026-10-12"}
    }
    assert "acme/bot" not in load(env)

    # Week 3: a stale index still returns it with the same id. It is not asked for again.
    week3 = FakeGitHub()
    week3.gone = {"acme/bot"}
    summary = run(env, week3, date="2026-10-19")
    assert "/repos/acme/bot" not in {r.url.path for r in week3.requests}
    assert summary["totals"]["gone_skipped_stale_hits"] == 1
    assert "acme/bot" in json.loads(env.gone.read_text())

    # Week 4: someone re-creates acme/bot (new id). It is looked up and leaves the list.
    IDS["acme/bot"] = 44
    REPOS["acme/bot"]["id"] = 44
    try:
        week4 = FakeGitHub()
        run(env, week4, date="2026-10-26")
    finally:
        IDS["acme/bot"] = 4
        REPOS["acme/bot"]["id"] = 4
    assert "/repos/acme/bot" in {r.url.path for r in week4.requests}
    assert json.loads(env.gone.read_text()) == {}
    assert "acme/bot" in load(env)


def test_failed_run_leaves_data_alone_and_resume_replays_the_cache(env):
    fake = FakeGitHub()
    fake.fail_repo = "carol/studio"
    with pytest.raises(GitHubError):
        run(env, fake)
    assert not env.repos.exists()
    assert runlog.latest_unfinished(env.runs) == "2026-10-05"

    retry = FakeGitHub()
    run(env, retry, resume=True)
    assert [r for r in retry.requests if r.url.path == "/search/code"] == []
    assert set(load(env)) == set(REPOS)
    assert runlog.latest_unfinished(env.runs) is None


def test_resume_without_an_unfinished_run_is_refused(env):
    with pytest.raises(pipeline.NothingToResume):
        run(env, FakeGitHub(), resume=True)


def test_missing_token_is_a_clear_error(env, monkeypatch):
    monkeypatch.delenv("GH_SEARCH_TOKEN")
    with pytest.raises(MissingToken):
        run(env, FakeGitHub())


def test_unknown_query_ids_are_refused():
    with pytest.raises(ValueError):
        pipeline.select_queries(["nope"])


def test_queries_check_literal_strings():
    from collector.queries import ALL_QUERIES, verify

    q = ALL_QUERIES["js-client"]
    assert verify(q, ['  "@fal-ai/client": "^1.0.0"'])
    assert not verify(q, ["fal ai client"])
    assert not verify(q, ['"@fal-ai/client-extra": "1"'])
    assert verify(ALL_QUERIES["py-requirements"], ["fal_client==1.0.3"])
    assert verify(ALL_QUERIES["int-litellm"], ['model="fal_ai/fal-ai/flux-pro/v1.1"'])
    assert all(q.needles for q in ALL_QUERIES.values() if q.kind == "code")
