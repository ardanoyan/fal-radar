from __future__ import annotations

import datetime as dt

from collector import classify

TODAY = dt.date(2026, 10, 5)


def test_kind_rules():
    k = classify.kind_of
    assert (
        k(name="nextjs-starter", description=None, fork=False, is_template=False, stars=0)
        == "template"
    )
    assert k(name="x", description=None, fork=False, is_template=True, stars=0) == "template"
    assert k(name="x", description="a thing", fork=True, stars=3, is_template=False) == "fork"
    assert k(name="x", description="a thing", fork=True, stars=300, is_template=False) == "app"
    assert (
        k(name="ComfyUI-fal-API", description="nodes", fork=False, is_template=False, stars=9)
        == "plugin"
    )
    assert (
        k(name="x", description="Code for our arxiv paper", fork=False, is_template=False, stars=1)
        == "research"
    )
    assert (
        k(name="studio", description="An image studio", fork=False, is_template=False, stars=1)
        == "app"
    )


def test_template_word_needs_a_word_start():
    # "redemption" contains "demo" only inside a word.
    assert (
        classify.kind_of(
            name="redemption", description=None, fork=False, is_template=False, stars=0
        )
        == "app"
    )


def test_active_and_notable():
    assert classify.is_active("2026-07-10T00:00:00Z", TODAY)
    assert not classify.is_active("2026-06-01T00:00:00Z", TODAY)
    assert not classify.is_active(None, TODAY)
    base = dict(
        fork=False, kind="app", description="An app", pushed_at="2026-10-01T00:00:00Z", today=TODAY
    )
    assert classify.is_notable(stars=3, **base)
    assert not classify.is_notable(stars=1, **base)
    assert classify.is_notable(stars=0, readme_bytes=900, **base)
    assert not classify.is_notable(stars=50, **{**base, "description": "  "})
    assert not classify.is_notable(stars=50, **{**base, "kind": "template"})


def test_stack_from_topics():
    assert classify.stack_from_topics(["nextjs", "react", "ai", "FastAPI"]) == [
        "fastapi",
        "next",
        "react",
    ]


def test_models_from_fragments():
    from collector.models import default_index

    fams, unknown = classify.models_from_fragments(
        [
            'fal.subscribe("fal-ai/flux/schnell")',
            "model: fal-ai/kling-video/v2/master/image-to-video",
            "fal-ai/zz-unknown-thing",
        ],
        default_index(),
    )
    assert fams == ["flux", "kling"]
    assert unknown == ["fal-ai/zz-unknown-thing"]
