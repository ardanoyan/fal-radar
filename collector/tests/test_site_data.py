"""Findings, builders, this week and the digest, on a small synthetic data/ folder."""

from __future__ import annotations

import json
import re

import pytest

from collector import digest, site_data
from collector.config import Paths


def repo(
    i: int,
    name: str,
    *,
    stars=0,
    created="2026-01-10",
    pushed="2026-10-01",
    clients=("js",),
    models=(),
    kind="app",
    owner_type="User",
    fork=False,
    notable=None,
    owner=None,
):
    login = owner or name.split("/")[0]
    return {
        "id": i,
        "full_name": name,
        "html_url": f"https://github.com/{name}",
        "description": f"{name} does things. More words follow here.",
        "stars": stars,
        "forks": 0,
        "language": "TypeScript",
        "topics": [],
        "license": None,
        "homepage": None,
        "created_at": f"{created}T00:00:00Z",
        "pushed_at": f"{pushed}T00:00:00Z",
        "fork": fork,
        "archived": False,
        "is_template": False,
        "fork_source": None,
        "template_source": None,
        "owner": {
            "login": login,
            "type": owner_type,
            "name": None,
            "location": None,
            "avatar_url": f"https://avatars.githubusercontent.com/u/{i}?v=4",
            "html_url": f"https://github.com/{login}",
        },
        "owner_is_fal": login in ("fal-ai", "fal-ai-community"),
        "evidence": "code",
        "sources": ["code:@fal-ai/client"],
        "clients": list(clients),
        "models": list(models),
        "kind": kind,
        "stack": [],
        "active": True,
        "notable": stars >= 3 if notable is None else notable,
        "readme_bytes": None,
        "first_seen": "2026-10-06",
        "last_seen": "2026-10-06",
        "stars_history": [{"date": "2026-10-06", "stars": stars}],
    }


@pytest.fixture
def paths(tmp_path):
    p = Paths(tmp_path)
    (tmp_path / "data" / "runs").mkdir(parents=True)
    (tmp_path / "data" / "snapshots").mkdir()
    repos = [
        repo(1, "alice/studio", stars=500, models=("flux", "kling")),
        repo(2, "alice/other", stars=40, models=("flux",)),
        repo(
            3, "bob/py-thing", stars=25, clients=("python",), models=("veo",), pushed="2026-09-20"
        ),
        repo(4, "carol/old", stars=300, pushed="2025-01-01", models=("flux",)),
        repo(5, "dave/starter", stars=90, kind="template", models=("nano-banana",)),
        repo(6, "fal-ai-community/demo", stars=2000, models=("flux",)),
        repo(
            7,
            "erin/new",
            stars=7,
            created="2026-10-01",
            clients=("js", "python"),
            models=("nano-banana",),
            owner_type="Organization",
        ),
    ] + [
        repo(100 + i, f"u{i}/r{i}", stars=i % 4, models=("flux",) if i % 2 else ("kling",))
        for i in range(12)
    ]
    (tmp_path / "data" / "repos.json").write_text(json.dumps(repos))
    (tmp_path / "data" / "runs" / "2026-10-06.json").write_text(
        json.dumps(
            {
                "run_id": "2026-10-06",
                "partial": False,
                "coverage": [{"id": q} for q in ("js-client", "py-import", "int-tanstack")],
                "api_facts": ["fact"],
            }
        )
    )
    (tmp_path / "data" / "snapshots" / "2026-10-06.json").write_text(json.dumps({"families": {}}))
    (tmp_path / "data" / "fal_catalogue.json").write_text(
        json.dumps(
            {
                "read_on": "2026-10-06",
                "endpoints": [
                    {
                        "id": "fal-ai/new-model",
                        "name": "New",
                        "category": "text-to-image",
                        "created": "2026-10-05",
                        "status": "active",
                    },
                    {
                        "id": "fal-ai/old-model",
                        "name": "Old",
                        "category": "text-to-image",
                        "created": "2026-01-05",
                        "status": "active",
                    },
                ],
            }
        )
    )
    return p


def test_findings_have_numbers_and_queries(paths):
    out = site_data.write_all(paths)
    fs = out["findings.json"]["findings"]
    assert len(fs) == 5
    assert all(re.search(r"\d", f["text"]) and f["query"] and f["numbers"] for f in fs)
    assert fs[0]["id"] == "clients" and fs[1]["id"] == "models"
    # fal's own repo is not counted: 18 headline repos, not 19.
    assert fs[0]["numbers"]["repos"] == 18


def test_builders_follow_the_rule(paths):
    out = site_data.write_all(paths)
    rows = out["builders.json"]["builders"]
    logins = [b["login"] for b in rows]
    assert "fal-ai-community" not in logins  # fal's own
    assert "dave" not in logins  # only a template
    assert "carol" not in logins  # 300 stars but not pushed in 90 days
    assert logins[:2] == ["alice", "bob"]  # ranked by best repo stars
    assert rows[0]["best"]["full_name"] == "alice/studio" and rows[0]["repos"] == 2


def test_this_week_uses_week_one_rules(paths):
    tw = site_data.write_all(paths)["this_week.json"]
    assert tw["first_issue"]
    assert [r["full_name"] for r in tw["new"]["repos"]] == ["erin/new"]
    assert tw["models"]["count"] == 1 and tw["models"]["endpoints"][0]["id"] == "fal-ai/new-model"


def test_digest_variants_fit_their_limits(paths):
    out = digest.write(paths)
    post = json.loads((paths.data / "digests" / "2026-W41.posts.json").read_text())
    assert len(post["discord"]) <= 2000 and len(post["linkedin"]) <= 1300
    assert all(len(p) <= 280 for p in post["x"])
    for text in (post["discord"], post["linkedin"], post["x"][-1]):
        assert "unofficial community project" in text and digest.SITE_URL in text
    md = (paths.data / "digests" / "2026-W41.md").read_text()
    assert "first issue" in md and "erin/new" in md and chr(0x2014) not in md
    assert out["week"] == "2026-W41"


def test_readme_block_is_filled_from_findings(paths):
    (paths.root / "README.md").write_text(
        "x\n<!-- findings:start -->\nold\n<!-- findings:end -->\ny\n"
    )
    site_data.write_all(paths)
    text = (paths.root / "README.md").read_text()
    assert "old" not in text and "1. " in text and text.endswith("y\n")
