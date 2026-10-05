from __future__ import annotations

from collector.models import FamilyIndex, extract_fal_ai_ids, load_families, tokenize


def test_tokenize_ignores_punctuation_like_code_search():
    assert tokenize("fal-ai/flux-2-pro/edit") == ("fal", "ai", "flux", "2", "pro", "edit")


def test_longest_pattern_wins():
    _, idx = load_families()
    assert idx.match("fal-ai/flux/dev") == "flux"
    assert idx.match("fal-ai/flux-lora-fast-training") == "flux-training"
    assert idx.match("fal-ai/bytedance/seedream/v4/edit") == "seedream"
    assert idx.match("fal-ai/bytedance/seedance/v1/pro/text-to-video") == "seedance"


def test_unknown_ids_have_no_family():
    _, idx = load_families()
    assert idx.match("fal-ai/definitely-not-a-model-zz9") is None


def test_every_endpoint_in_the_tsv_maps_to_its_family():
    _, idx = load_families()
    from pathlib import Path

    rows = Path(__file__).parents[1].joinpath("fal_endpoints.tsv").read_text().splitlines()
    data = [r.split("\t") for r in rows if r and not r.startswith("#") and not r.startswith("id\t")]
    assert len(data) > 1_000
    wrong = [(eid, fam, idx.match(eid)) for eid, fam, *_ in data if idx.match(eid) != fam]
    assert wrong == []


def test_models_yaml_has_enough_queried_families_with_urls():
    meta, idx = load_families()
    queried = [f for f in idx.families if f.discover]
    assert len(queried) >= 60
    assert all(f.url.startswith("https://fal.ai/models/") for f in idx.families)
    assert all(f.query and f.query.startswith('"fal-ai/') for f in queried)
    assert meta["read_on"]


def test_extract_ids_skips_npm_scopes_and_github_paths():
    text = (
        'import { fal } from "@fal-ai/client"; fal.subscribe("fal-ai/flux/dev", {}); '
        "see https://github.com/fal-ai/fal-js or `fal-ai/veo3.` and "
        "https://queue.fal.run/fal-ai/fast-sdxl/requests"
    )
    assert extract_fal_ai_ids(text) == [
        "fal-ai/flux/dev",
        "fal-ai/veo3",
        "fal-ai/fast-sdxl",
    ]


def test_extract_ids_cuts_queue_urls_but_keeps_stream_endpoints():
    text = (
        "https://queue.fal.run/fal-ai/flux/requests/7f3a-11/status "
        "https://fal.run/fal-ai/speech-to-text/stream wss://fal.run/xai/grok-voice/realtime "
        "https://fal.run/fal-ai/flux-lora/stream"
    )
    assert extract_fal_ai_ids(text) == [
        "fal-ai/flux",
        "fal-ai/speech-to-text/stream",
        "fal-ai/flux-lora/stream",
    ]


def test_family_index_rejects_a_pattern_claimed_twice():
    from collector.models import Family

    a = Family(
        id="a",
        name="A",
        media="image",
        patterns=("fal-ai/x",),
        url="https://fal.ai/models/fal-ai/x",
    )
    b = Family(
        id="b",
        name="B",
        media="image",
        patterns=("fal-ai/x",),
        url="https://fal.ai/models/fal-ai/x",
    )
    try:
        FamilyIndex([a, b])
    except ValueError:
        return
    raise AssertionError("expected a ValueError")
