from __future__ import annotations

import base64
import json
import shutil
import urllib.parse

import httpx
import pytest

from collector import pipeline, runlog
from collector.config import MissingToken, Paths
from collector.github import GitHubError
from collector.schema import Repo

from .conftest import FakeClock, json_response

# Repos found by the "@fal-ai/client" code query.
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
    "fal-ai-community/demo": dict(
        id=7,
        stars=300,
        fork=False,
        desc="Official demo",
        pushed="2026-10-01T10:00:00Z",
        login="fal-ai-community",
        otype="Organization",
    ),
    "ivan/fal-sdk-wrapper": dict(
        id=8,
        stars=1,
        fork=False,
        desc="A small wrapper library",
        pushed="2026-10-02T10:00:00Z",
        login="ivan",
        otype="User",
    ),
}
CODE_TIER = set(REPOS) - {"dora/readme-only"}

# Repos found only by repository search (mention tier).
MENTION_REPOS = {
    "gina/big-mention": dict(id=20, stars=25, desc="Uses fal.ai for images"),
    "hank/small-mention": dict(id=21, stars=2, desc="Mentions fal.ai in the README"),
    "jill/gone-mention": dict(id=22, stars=40, desc="Mentions fal.ai"),
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
    ("fal-ai-community/demo", "package.json", '"@fal-ai/client": "^1.10.1"'),
    ("ivan/fal-sdk-wrapper", "package.json", '"@fal-ai/client": "^1.10.1"'),
    # A README that names the client: a mention, not code.
    ("dora/readme-only", "README.md", 'npm i "@fal-ai/client"'),
    # Same words without the literal string: GitHub ignores punctuation, the collector does not.
    ("zed/unrelated", "notes.txt", "the fal ai client was slow"),
    # A committed copy of an installed package: not evidence that the project calls fal.
    ("vic/vendored", "node_modules/@fal-ai/client/package.json", '"@fal-ai/client": "1.10.1"'),
]
IDS = {
    **{name: r["id"] for name, r in REPOS.items()},
    **{name: m["id"] for name, m in MENTION_REPOS.items()},
    "zed/unrelated": 99,
    "vic/vendored": 98,
}

# package.json bodies served by the contents API.
MANIFESTS = {
    "acme/bot": {"dependencies": {"@fal-ai/client": "1.9.0", "discord.js": "14"}},
    "alice/studio": {"private": True, "dependencies": {"@fal-ai/client": "^1", "next": "15"}},
    "ivan/fal-sdk-wrapper": {"main": "dist/index.js", "dependencies": {"@fal-ai/client": "^1"}},
}

# What a scoped "fal-ai/" repo:X search returns: (path, fragment) pairs, or 422.
SCOPED = {
    "alice/studio": [
        ("app/page.tsx", 'fal.subscribe("fal-ai/kling-video/v2/master/image-to-video")'),
        ("README.md", "We also tried fal-ai/veo3"),
    ],
    "gina/big-mention": [
        ("src/gen.py", 'import fal_client\nfal_client.subscribe("fal-ai/flux/schnell")')
    ],
    "jill/gone-mention": 422,
}


def repo_body(name: str) -> dict:
    owner = name.split("/")[0]
    if name in MENTION_REPOS:
        m = MENTION_REPOS[name]
        return {
            "id": m["id"],
            "name": name.split("/")[1],
            "full_name": name,
            "html_url": f"https://github.com/{name}",
            "description": m["desc"],
            "stargazers_count": m["stars"],
            "forks_count": 0,
            "language": "Python",
            "topics": [],
            "license": None,
            "homepage": None,
            "created_at": "2026-08-01T00:00:00Z",
            "pushed_at": "2026-10-01T00:00:00Z",
            "fork": False,
            "archived": False,
            "is_template": False,
            "owner": {
                "login": owner,
                "type": "User",
                "avatar_url": "https://a.example/x",
                "html_url": f"https://github.com/{owner}",
            },
        }
    r = REPOS[name]
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


def search_item(name: str, file_path: str, fragment: str) -> dict:
    return {
        "name": file_path.rsplit("/", 1)[-1],
        "path": file_path,
        "repository": {"id": IDS[name], "full_name": name},
        "text_matches": [{"fragment": fragment}],
    }


class FakeGitHub:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.not_modified = False
        self.fail_repo: str | None = None
        self.gone: set[str] = set()
        self.hidden_from_search: set[str] = set()
        self.scoped: list[str] = []
        self.readmes: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        params = dict(urllib.parse.parse_qsl(request.url.query.decode()))
        if path == "/rate_limit":
            res = {
                k: {"limit": v, "remaining": v, "reset": 1_900_000_000}
                for k, v in (("core", 5000), ("search", 30), ("code_search", 10))
            }
            return json_response(200, {"resources": res})
        if path == "/search/repositories":
            q = params["q"]
            items = []
            for name, m in MENTION_REPOS.items():
                if "stars:>=20" in q and m["stars"] < 20:
                    continue
                owner = name.split("/")[0]
                items.append(
                    {
                        "id": m["id"],
                        "full_name": name,
                        "html_url": f"https://github.com/{name}",
                        "description": m["desc"],
                        "stargazers_count": m["stars"],
                        "language": "Python",
                        "topics": [],
                        "created_at": "2026-08-01T00:00:00Z",
                        "pushed_at": "2026-10-01T00:00:00Z",
                        "archived": False,
                        "owner": {
                            "login": owner,
                            "type": "User",
                            "avatar_url": f"https://avatars.githubusercontent.com/u/{m['id']}",
                        },
                    }
                )
            body = {"total_count": len(items), "incomplete_results": False, "items": items}
            return json_response(200, body)
        if path == "/search/code":
            q = params["q"]
            items = []
            if "repo:" in q:
                name = q.split("repo:", 1)[1].strip()
                self.scoped.append(name)
                answer = SCOPED.get(name, [])
                if answer == 422:
                    return json_response(422, {"message": "Validation Failed"})
                if answer == 403:
                    return json_response(403, {"message": "Forbidden"})
                items = [search_item(name, p, f) for p, f in answer]
            elif "@fal-ai/client" in q:
                items = [
                    search_item(name, p, f)
                    for name, p, f in HITS
                    if name not in self.hidden_from_search
                ]
            body = {"total_count": len(items), "incomplete_results": False, "items": items}
            return json_response(200, body)
        if path.startswith("/repos/") and "/contents/" in path:
            name = path[len("/repos/") :].split("/contents/", 1)[0]
            if name not in MANIFESTS:
                return json_response(404, {"message": "Not Found"})
            content = base64.b64encode(json.dumps(MANIFESTS[name]).encode()).decode()
            return json_response(200, {"type": "file", "encoding": "base64", "content": content})
        if path.startswith("/repos/") and path.endswith("/readme"):
            self.readmes.append(path[len("/repos/") : -len("/readme")])
            return json_response(200, {"size": 1200, "name": "README.md"})
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


def run(paths, fake, *, date="2026-10-05", clock=None, only=("js-client",), full=False, **options):
    clock = clock or FakeClock()
    return pipeline.run(
        paths,
        pipeline.RunOptions(only=[] if full else list(only), **options),
        transport=httpx.MockTransport(fake),
        sleep=clock.sleep,
        now=clock.now,
        log=lambda _m: None,
        run_date=date,
    )


FULL = ("js-client", "mention-readme", "promote-mention-readme")


def load(paths):
    return {r["full_name"]: r for r in json.loads(paths.repos.read_text())}


def load_mentions(paths):
    return {m["full_name"]: m for m in json.loads(paths.mentions.read_text())}


def test_first_run_builds_records(env):
    run(env, FakeGitHub())
    repos = load(env)
    assert set(repos) == CODE_TIER
    a = repos["alice/studio"]
    assert a["evidence"] == "code" and a["clients"] == ["js"]
    assert a["models"] == ["flux", "kling"]  # flux from page.tsx, kling from the scoped search
    assert a["sources"] == ["code:@fal-ai/client"]
    assert a["notable"] and a["active"] and a["kind"] == "app"
    assert a["stack"] == ["next"]  # from the topic and from the manifest
    assert a["first_seen"] == "2026-10-05"
    assert a["stars_history"] == [{"date": "2026-10-05", "stars": 40}]
    assert a["license"] == "MIT" and a["homepage"] is None
    assert repos["bob/nextjs-starter"]["kind"] == "template"
    assert repos["carol/studio"]["kind"] == "fork" and not repos["carol/studio"]["notable"]
    assert repos["carol/studio"]["fork_source"] == "upstream/studio"
    assert repos["erin/my-fal-app"]["template_source"] == "fal-ai/fal-nextjs-template"
    assert not repos["acme/bot"]["active"]


def test_manifests_give_kind_and_stack(env):
    run(env, FakeGitHub())
    repos = load(env)
    assert repos["acme/bot"]["kind"] == "bot"
    assert repos["ivan/fal-sdk-wrapper"]["kind"] == "library"
    assert repos["alice/studio"]["kind"] == "app"  # private package with next: an app


def test_readme_size_decides_recent_low_star_repos(env):
    fake = FakeGitHub()
    run(env, fake)
    repos = load(env)
    # 1 star, pushed 3 days before the run, a description: notable only through its README.
    assert "ivan/fal-sdk-wrapper" in fake.readmes
    assert repos["ivan/fal-sdk-wrapper"]["readme_bytes"] == 1200
    assert repos["ivan/fal-sdk-wrapper"]["notable"]
    # 40 stars: already notable, so no README lookup.
    assert "alice/studio" not in fake.readmes


def test_totals_and_headline(env):
    summary = run(env, FakeGitHub())
    t = summary["totals"]
    assert t["code_tier"] == 7 and t["code_tier_forks"] == 1
    # Headline: not a fork, not fal's own, not a copy of one of fal's templates.
    assert t["headline"] == 4 and t["fal_template_copies"] == 1 and t["from_fal"] == 1
    assert t["mentions"] == 1  # dora's README hit
    q = summary["coverage"][0]
    assert q["files"] == 11 and q["literal_match"] == 10 and q["dropped"] == 1
    assert q["vendored"] == 1
    assert q["in_docs"] == 1 and q["repos"] == 8


def test_fal_owned_repos_are_flagged_and_kept_out_of_the_headline(env):
    run(env, FakeGitHub())
    demo = load(env)["fal-ai-community/demo"]
    assert demo["owner_is_fal"] and demo["evidence"] == "code"
    assert not pipeline.is_headline(Repo.model_validate(demo))


def test_literal_check_and_docs(env):
    run(env, FakeGitHub())
    assert "zed/unrelated" not in load(env)
    mentions = load_mentions(env)
    d = mentions["dora/readme-only"]
    assert d["sources"] == ["code:@fal-ai/client:docs"]
    assert "dora/readme-only" not in load(env)


def test_mentions_and_promotion_through_a_scoped_search(env):
    fake = FakeGitHub()
    summary = run(env, fake, only=FULL)
    repos, mentions = load(env), load_mentions(env)
    # gina: 25 stars, fal-ai/flux/schnell in a Python file: promoted to the code tier.
    g = repos["gina/big-mention"]
    assert g["evidence"] == "code" and g["models"] == ["flux"] and g["clients"] == ["python"]
    assert "code:scoped" in g["sources"] and "gina/big-mention" not in mentions
    # hank: 2 stars, never a promotion candidate.
    assert "hank/small-mention" in mentions and "hank/small-mention" not in fake.scoped
    # jill: 40 stars but the scoped search answers 422: recorded, stays a mention.
    assert "jill/gone-mention" in mentions
    unsearchable = json.loads(env.unsearchable.read_text())
    assert unsearchable["jill/gone-mention"]["status"] == 422
    assert summary["totals"]["promoted"] == 1
    assert summary["scoped"]["unsearchable"] == 1


def test_scoped_search_skips_unsearchable_repos_next_time(env):
    run(env, FakeGitHub(), only=FULL)
    fake = FakeGitHub()
    run(env, fake, only=FULL, date="2026-10-12")
    assert "jill/gone-mention" not in fake.scoped


def test_scoped_models_ignore_docs(env):
    run(env, FakeGitHub())
    # veo3 appears only in alice's README.
    assert "veo" not in load(env)["alice/studio"]["models"]


def test_snapshot_counts_headline_repos(env):
    run(env, FakeGitHub(), full=True)
    snap = json.loads((env.snapshots / "2026-10-05.json").read_text())
    # gina is promoted; dora (docs), hank and jill stay mentions.
    assert snap["headline"] == 5 and snap["from_fal"] == 1 and snap["mentions"] == 3
    # flux: alice (page.tsx) and gina (promoted); kling: alice (scoped search).
    assert snap["families"]["flux"]["repos"] == 2 and snap["families"]["kling"]["repos"] == 1
    assert snap["kinds"]["bot"] == 1 and snap["kinds"]["library"] == 1


def test_people_data_is_limited_to_public_profile_fields(env):
    run(env, FakeGitHub())
    owner = load(env)["alice/studio"]["owner"]
    assert set(owner) == {"login", "type", "name", "location", "avatar_url", "html_url"}
    raw = env.repos.read_text()
    assert "example.com" not in raw and "x.example" not in raw


def test_exclusions_drop_repos_and_owners(env):
    env.exclusions.write_text(
        json.dumps({"repos": ["Bob/NextJS-Starter", "gina/big-mention"], "owners": ["acme"]})
    )
    fake = FakeGitHub()
    run(env, fake, only=FULL)
    assert set(load(env)) == CODE_TIER - {"bob/nextjs-starter", "acme/bot"}
    assert "gina/big-mention" not in load_mentions(env)
    looked_up = {r.url.path for r in fake.requests}
    assert "/repos/acme/bot" not in looked_up and "/users/acme" not in looked_up
    assert "gina/big-mention" not in fake.scoped


def test_etags_file_holds_only_repo_and_user_urls(env):
    run(env, FakeGitHub())
    etags = json.loads(env.etags.read_text())
    assert etags and all("/repos/" in u or "/users/" in u for u in etags)


def test_run_log_records_queries_facts_and_finish(env):
    run(env, FakeGitHub())
    events = runlog.read_events(env.runs / "2026-10-05.jsonl")
    names = [e["event"] for e in events]
    assert names[0] == "run_started" and names[-1] == "run_finished"
    assert "query_finished" in names and "slice" in names and "stage" in names
    assert any("4,000" in fact for fact in events[0]["api_facts"])
    assert events[0]["limited"] is True
    summary = json.loads((env.runs / "2026-10-05.limited.json").read_text())
    assert summary["totals"]["repos_in_file"] == 7 and summary["partial"]
    assert any("ETag" in fact for fact in summary["api_facts"])
    # A limited run writes no snapshot and no full-run summary.
    assert not (env.runs / "2026-10-05.json").exists()
    assert not (env.snapshots / "2026-10-05.json").exists()


def test_full_run_writes_snapshot_and_summary(env):
    run(env, FakeGitHub(), full=True)
    summary = json.loads((env.runs / "2026-10-05.json").read_text())
    assert not summary["partial"]
    assert (env.snapshots / "2026-10-05.json").exists()


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
    assert a["kind"] == "app" and a["stack"] == ["next"]
    sent = {
        r.url.path[len("/repos/") :]: r.headers.get("if-none-match")
        for r in fake.requests
        if r.url.path.startswith("/repos/") and r.url.path.count("/") == 3
    }
    # Code-tier repos are revalidated; dora (a mention) is fetched in full.
    assert all(v for k, v in sent.items() if k != "dora/readme-only")
    assert sent["dora/readme-only"] is None
    # Manifests and READMEs never go conditional: a 304 there would have no body.
    assert not any(
        r.headers.get("if-none-match")
        for r in fake.requests
        if "/contents/" in r.url.path or r.url.path.endswith("/readme")
    )
    assert load(env)["acme/bot"]["kind"] == "bot"  # kept from the stored manifest label
    etags = json.loads(env.etags.read_text())
    assert all("/contents/" not in u and not u.endswith("/readme") for u in etags)
    assert "https://api.github.com/repos/dora/readme-only" not in etags


def test_gone_repos_are_recorded_and_rechecked_only_when_search_finds_them(env):
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

    # Week 3: search does not return it: it is not asked for.
    week3 = FakeGitHub()
    week3.hidden_from_search = {"acme/bot"}
    run(env, week3, date="2026-10-19")
    assert "/repos/acme/bot" not in {r.url.path for r in week3.requests}

    # Week 4: search returns it (a stale index): one lookup, still 404, stays on the list.
    week4 = FakeGitHub()
    week4.gone = {"acme/bot"}
    summary = run(env, week4, date="2026-10-26")
    assert "/repos/acme/bot" in {r.url.path for r in week4.requests}
    assert summary["totals"]["gone_rechecked"] == 1
    assert "acme/bot" in json.loads(env.gone.read_text())

    # Week 5: it is public again: it comes back and leaves the list.
    run(env, FakeGitHub(), date="2026-11-02")
    assert json.loads(env.gone.read_text()) == {}
    assert "acme/bot" in load(env)


def test_a_reused_name_does_not_inherit_the_old_record(env):
    run(env, FakeGitHub())
    week2 = FakeGitHub()
    week2.hidden_from_search = {"alice/studio"}
    real = week2.__call__

    def answer(request):
        if request.url.path == "/repos/alice/studio":
            body = repo_body("alice/studio")
            body.update(
                id=100,
                description="Unrelated notes",
                stargazers_count=0,
                created_at="2026-10-10T00:00:00Z",
            )
            return json_response(200, body)
        return real(request)

    run(env, answer, date="2026-10-12")
    repos = {r["id"]: r for r in json.loads(env.repos.read_text())}
    assert 100 not in repos
    assert repos[1]["full_name"] == "alice/studio" and repos[1]["models"] == ["flux", "kling"]


def test_failed_run_leaves_data_alone_and_resume_replays_the_cache(env):
    fake = FakeGitHub()
    fake.fail_repo = "carol/studio"
    with pytest.raises(GitHubError):
        run(env, fake, full=True)
    assert not env.repos.exists()
    assert runlog.latest_unfinished(env.runs) == "2026-10-05"

    # A quick limited run the same day must not hide the crashed full run.
    run(env, FakeGitHub())
    assert runlog.latest_unfinished(env.runs) == "2026-10-05"
    retry = FakeGitHub()
    run(env, retry, resume=True, full=True)
    # Discovery searches replay from the cache; only the stages after the crash are new.
    discovery = [
        r for r in retry.requests if r.url.path == "/search/code" and "repo%3A" not in str(r.url)
    ]
    assert discovery == []
    assert {"/repos/alice/studio", "/repos/acme/bot"}.isdisjoint(
        {r.url.path for r in retry.requests}
    )
    assert set(load(env)) == CODE_TIER | {"gina/big-mention"}
    assert runlog.latest_unfinished(env.runs) is None


def test_skip_flags_turn_stages_off(env):
    fake = FakeGitHub()
    run(env, fake, skip_scoped=True, skip_manifests=True, skip_readmes=True, skip_owners=True)
    paths = {r.url.path for r in fake.requests}
    assert not any(
        "/contents/" in p or p.endswith("/readme") or p.startswith("/users/") for p in paths
    )
    assert fake.scoped == []
    assert load(env)["acme/bot"]["kind"] == "app"  # no manifest, so no "bot"


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
    assert verify(ALL_QUERIES["model-flux"], ['fal.run("fal-ai/flux-pro/kontext")'])
    assert all(q.needles for q in ALL_QUERIES.values() if q.kind == "code")
    assert ALL_QUERIES["model-flux"].max_pages == 3
    assert "stars:>=20" in ALL_QUERIES["promote-mention-readme"].q


def test_excluded_owner_leaves_no_trace_in_committed_files(env):
    run(env, FakeGitHub(), full=True)
    env.exclusions.write_text(json.dumps({"repos": [], "owners": ["acme", "jill"]}))
    fake = FakeGitHub()
    run(env, fake, full=True, date="2026-10-12")
    for path in (env.repos, env.mentions, env.etags, env.gone, env.unsearchable):
        text = path.read_text().lower()
        assert "acme" not in text and "jill" not in text, path.name
    assert not any(r.url.path in ("/users/acme", "/users/jill") for r in fake.requests)


def test_docs_only_repos_get_no_owner_or_readme_lookup(env):
    fake = FakeGitHub()
    run(env, fake)
    paths = {r.url.path for r in fake.requests}
    assert "/repos/dora/readme-only" in paths  # stars and dates come from the lookup
    assert "/users/dora" not in paths and "dora/readme-only" not in fake.readmes


def test_fal_owned_mentions_are_not_counted(env, monkeypatch):
    MENTION_REPOS["fal-ai/some-demo"] = dict(id=30, stars=5, desc="fal demo")
    try:
        summary = run(env, FakeGitHub(), full=True)
    finally:
        del MENTION_REPOS["fal-ai/some-demo"]
    assert summary["totals"]["mentions"] == 3 and summary["totals"]["from_fal_mentions"] == 1


def test_a_partner_id_alone_does_not_promote(env):
    SCOPED["gina/big-mention"] = [
        ("src/x.py", 'MODEL = "openai/gpt-image-2"  # fal.ai or replicate')
    ]
    try:
        run(env, FakeGitHub(), full=True)
    finally:
        SCOPED["gina/big-mention"] = [
            ("src/gen.py", 'import fal_client\nfal_client.subscribe("fal-ai/flux/schnell")')
        ]
    assert "gina/big-mention" not in load(env)
    assert "gina/big-mention" in load_mentions(env)


def test_a_failing_scoped_search_is_skipped_not_fatal(env):
    SCOPED["gina/big-mention"] = 403
    try:
        summary = run(env, FakeGitHub(), full=True)
    finally:
        SCOPED["gina/big-mention"] = [
            ("src/gen.py", 'import fal_client\nfal_client.subscribe("fal-ai/flux/schnell")')
        ]
    assert summary["scoped"]["errors"] == 1


def test_low_disk_stops_the_run_before_it_starts(env, monkeypatch):
    import collections

    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(pipeline, "MIN_FREE_BYTES", 1_000_000_000)
    monkeypatch.setattr(pipeline.shutil, "disk_usage", lambda p: usage(10, 10, 100_000_000))
    with pytest.raises(pipeline.LowDisk):
        run(env, FakeGitHub())
    assert not (env.runs / "2026-10-05.jsonl").exists()


def test_vendored_hits_are_not_evidence(env):
    summary = run(env, FakeGitHub())
    assert "vic/vendored" not in load(env) and "vic/vendored" not in load_mentions(env)
    assert summary["coverage"][0]["vendored"] == 1


def test_vendored_paths():
    from collector.queries import is_vendored_path

    yes = [
        "node_modules/@fal-ai/client/src/x.ts",
        "app/.venv/lib/python3.12/site-packages/litellm/x.py",
        "vendor/litellm/fal.py",
        "web/dist/assets/index-abc.js",
        "static/app.min.js",
        "third_party/fal/client.py",
    ]
    no = [
        "src/app.ts",
        "package.json",
        "requirements.txt",
        "myvenv_notes.py",
        "builder/run.py",
        "distance/calc.py",
    ]
    assert all(is_vendored_path(p) for p in yes)
    assert not any(is_vendored_path(p) for p in no)


def test_run_id_rebuilds_an_earlier_run_from_its_cache(env):
    run(env, FakeGitHub(), full=True)
    again = FakeGitHub()
    pipeline.run(
        env,
        pipeline.RunOptions(run_id="2026-10-05"),
        transport=httpx.MockTransport(again),
        sleep=FakeClock().sleep,
        now=FakeClock().now,
        log=lambda _m: None,
        run_date="2026-10-20",
    )
    # Everything replays from the run's cache: only the free rate limit check goes out.
    assert {r.url.path for r in again.requests} <= {"/rate_limit"}
