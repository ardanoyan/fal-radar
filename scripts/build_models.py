#!/usr/bin/env python3
"""Build collector/models.yaml and collector/fal_endpoints.tsv from fal's public sources.

Nothing here is typed from memory into the output. An endpoint ID only enters the
files when fal itself returns it:

  https://api.fal.ai/v1/models    fal's documented model catalogue (no key needed):
                                  every active endpoint, with its name, category and status
  the same API, by endpoint_id    one lookup for legacy candidates, kept only when the
                                  API returns a record for exactly that ID
  https://fal.ai/explore          fal's featured sections, used as the sign of prominence
  https://fal.ai/sitemap.xml      cross-check of the catalogue count

fal.ai answers 200 for model pages that do not exist, so a page's status code proves
nothing; the catalogue record does. The two fal.ai pages are allowed by
fal.ai/robots.txt (read 5 Oct 2026). Requests are spaced four seconds apart and a
429 is answered by waiting for Retry-After.

The CURATED table below is our own grouping of real IDs into families. Edit it here,
then run:  uv run python scripts/build_models.py
Do not edit collector/models.yaml by hand.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collector.models import Family, FamilyIndex, tokenize  # noqa: E402

USER_AGENT = "fal-radar/0.1 (+https://github.com/ardanoyan/fal-radar)"
CATALOGUE_URL = "https://api.fal.ai/v1/models"
SITEMAP_URL = "https://fal.ai/sitemap.xml"
EXPLORE_URL = "https://fal.ai/explore"
MODEL_URL = "https://fal.ai/models/{id}"
CATALOGUE_PAGE_SIZE = 500
REQUEST_GAP_SECONDS = 4.0
PAGE_SUFFIXES = {"api", "examples", "playground"}
SKIP_SECTION_PREFIXES = ("Ready to",)
MIN_SECTION_SIZE = 3

# fal's own category tags, folded into the six media groups the site shows.
MEDIA_OF_CATEGORY = {
    "text-to-image": "image",
    "image-to-image": "image",
    "text-to-video": "video",
    "image-to-video": "video",
    "video-to-video": "video",
    "text-to-audio": "audio",
    "text-to-speech": "audio",
    "speech-to-text": "audio",
    "speech-to-speech": "audio",
    "audio-to-audio": "audio",
    "audio-to-text": "audio",
    "video-to-audio": "audio",
    "audio-to-video": "video",
    "image-to-3d": "3d",
    "text-to-3d": "3d",
    "3d-to-3d": "3d",
    "training": "training",
    "vision": "utility",
    "llm": "utility",
    "json": "utility",
    "image-to-json": "utility",
    "video-to-text": "utility",
    "unknown": "utility",
}

# Our grouping. Each row: id, display name, media, patterns. A pattern is a real ID
# prefix; it matches by token prefix (see collector/models.py). The first fal-ai
# pattern that has endpoints becomes the family's code search phrase. Patterns that
# match no known endpoint are dropped and reported, never kept on trust.
CURATED: list[tuple[str, str, str, list[str]]] = [
    # image
    ("flux", "FLUX", "image", ["fal-ai/flux", "blackforestlabs/flux"]),
    ("nano-banana", "Nano Banana", "image", ["fal-ai/nano-banana", "google/nano-banana"]),
    ("gpt-image", "GPT Image", "image", ["fal-ai/gpt-image", "openai/gpt-image"]),
    ("gemini", "Gemini", "multi", ["fal-ai/gemini", "google/gemini"]),
    ("imagen", "Imagen", "image", ["fal-ai/imagen4", "fal-ai/imagen3"]),
    ("ideogram", "Ideogram", "image", ["fal-ai/ideogram", "ideogram"]),
    ("recraft", "Recraft", "image", ["fal-ai/recraft", "recraft"]),
    ("qwen-image", "Qwen Image", "image", ["fal-ai/qwen-image", "alibaba/qwen-image"]),
    ("seedream", "Seedream", "image", ["fal-ai/bytedance/seedream", "bytedance/seedream"]),
    ("hidream", "HiDream", "image", ["fal-ai/hidream"]),
    ("z-image", "Z-Image", "image", ["fal-ai/z-image"]),
    ("hunyuan-image", "Hunyuan Image", "image", ["fal-ai/hunyuan-image"]),
    (
        "stable-diffusion",
        "Stable Diffusion",
        "image",
        [
            "fal-ai/stable-diffusion",
            "fal-ai/stable-cascade",
            "fal-ai/lora",
            "fal-ai/fast-lcm-diffusion",
            "fal-ai/lcm-sd15-i2i",
            "fal-ai/lcm",
            "fal-ai/fast-turbo-diffusion",
            "fal-ai/realistic-vision",
            "fal-ai/dreamshaper",
        ],
    ),
    (
        "sdxl",
        "SDXL",
        "image",
        [
            "fal-ai/fast-sdxl",
            "fal-ai/fast-lightning-sdxl",
            "fal-ai/fast-fooocus-sdxl",
            "fal-ai/sdxl-controlnet-union",
            "fal-ai/fooocus",
            "fal-ai/hyper-sdxl",
            "fal-ai/lightning-models",
        ],
    ),
    ("kolors", "Kolors", "image", ["fal-ai/kolors"]),
    ("sana", "Sana", "image", ["fal-ai/sana"]),
    ("omnigen", "OmniGen", "image", ["fal-ai/omnigen"]),
    ("aura-flow", "AuraFlow", "image", ["fal-ai/aura-flow"]),
    ("playground", "Playground v2.5", "image", ["fal-ai/playground"]),
    ("krea", "Krea", "image", ["fal-ai/krea", "krea"]),
    ("bria", "Bria", "image", ["fal-ai/bria", "bria"]),
    ("pulid", "PuLID", "image", ["fal-ai/pulid"]),
    ("fashn", "FASHN try-on", "image", ["fal-ai/fashn"]),
    ("image-editing", "Image editing apps", "image", ["fal-ai/image-editing", "fal-ai/image-apps"]),
    # video
    ("kling", "Kling", "video", ["fal-ai/kling"]),
    ("veo", "Veo", "video", ["fal-ai/veo3", "fal-ai/veo2"]),
    ("sora", "Sora", "video", ["fal-ai/sora"]),
    ("wan", "Wan", "video", ["fal-ai/wan", "wan", "alibaba/wan"]),
    ("ltx", "LTX Video", "video", ["fal-ai/ltx", "fal-ai/ltxv", "lightricks"]),
    ("minimax", "MiniMax", "multi", ["fal-ai/minimax", "minimax"]),
    ("luma", "Luma", "multi", ["fal-ai/luma", "luma"]),
    ("pixverse", "PixVerse", "video", ["fal-ai/pixverse", "pixverse"]),
    ("vidu", "Vidu", "video", ["fal-ai/vidu"]),
    ("pika", "Pika", "video", ["fal-ai/pika"]),
    ("seedance", "Seedance", "video", ["fal-ai/bytedance/seedance", "bytedance/seedance"]),
    ("hunyuan-video", "Hunyuan Video", "video", ["fal-ai/hunyuan-video"]),
    ("grok-imagine", "Grok Imagine", "multi", ["xai"]),
    ("heygen", "HeyGen", "video", ["fal-ai/heygen"]),
    ("sync-lipsync", "Sync Lipsync", "video", ["fal-ai/sync-lipsync"]),
    ("longcat", "LongCat", "video", ["fal-ai/longcat"]),
    ("cogvideox", "CogVideoX", "video", ["fal-ai/cogvideox"]),
    ("mochi", "Mochi", "video", ["fal-ai/mochi"]),
    ("haiper", "Haiper", "video", ["fal-ai/haiper"]),
    ("runway", "Runway", "video", ["fal-ai/runway"]),
    ("animatediff", "AnimateDiff", "video", ["fal-ai/fast-animatediff", "fal-ai/animatediff"]),
    ("stable-video", "Stable Video Diffusion", "video", ["fal-ai/stable-video", "fal-ai/fast-svd"]),
    ("live-portrait", "Live Portrait", "video", ["fal-ai/live-portrait"]),
    ("sadtalker", "SadTalker", "video", ["fal-ai/sadtalker"]),
    ("omnihuman", "OmniHuman", "video", ["fal-ai/bytedance/omnihuman"]),
    ("ai-avatar", "AI Avatar", "video", ["fal-ai/ai-avatar"]),
    ("happy-horse", "Happy Horse", "video", ["alibaba/happy-horse"]),
    # audio
    ("elevenlabs", "ElevenLabs", "audio", ["fal-ai/elevenlabs", "elevenlabs"]),
    (
        "whisper",
        "Whisper and speech to text",
        "audio",
        ["fal-ai/whisper", "fal-ai/wizper", "fal-ai/speech-to-text"],
    ),
    ("kokoro", "Kokoro", "audio", ["fal-ai/kokoro"]),
    ("stable-audio", "Stable Audio", "audio", ["fal-ai/stable-audio"]),
    ("f5-tts", "F5 TTS", "audio", ["fal-ai/f5-tts"]),
    ("chatterbox", "Chatterbox", "audio", ["fal-ai/chatterbox", "resemble-ai"]),
    ("playai", "PlayAI", "audio", ["fal-ai/playai", "fal-ai/playht"]),
    ("mmaudio", "MMAudio", "audio", ["fal-ai/mmaudio"]),
    ("ace-step", "ACE-Step", "audio", ["fal-ai/ace-step"]),
    ("lyria", "Lyria", "audio", ["fal-ai/lyria3", "fal-ai/lyria2", "google/lyria"]),
    ("dia-tts", "Dia TTS", "audio", ["fal-ai/dia-tts"]),
    ("qwen-tts", "Qwen TTS", "audio", ["fal-ai/qwen-3-tts", "alibaba/qwen-audio"]),
    # 3d
    ("hunyuan3d", "Hunyuan3D", "3d", ["fal-ai/hunyuan3d", "fal-ai/hunyuan-3d"]),
    ("trellis", "TRELLIS", "3d", ["fal-ai/trellis"]),
    ("hyper3d", "Hyper3D Rodin", "3d", ["fal-ai/hyper3d"]),
    ("meshy", "Meshy", "3d", ["fal-ai/meshy", "meshy"]),
    ("tripo", "Tripo", "3d", ["fal-ai/triposr", "fal-ai/tripo3d", "tripo3d"]),
    ("stable-fast-3d", "Stable Fast 3D", "3d", ["fal-ai/stable-fast-3d"]),
    # training
    (
        "flux-training",
        "FLUX LoRA training",
        "training",
        [
            "fal-ai/flux-lora-fast-training",
            "fal-ai/flux-lora-portrait-trainer",
            "fal-ai/flux-kontext-trainer",
            "fal-ai/flux-2-trainer",
            "fal-ai/flux-2-klein-9b-base-trainer",
            "fal-ai/flux-2-klein-4b-base-trainer",
            "fal-ai/turbo-flux-trainer",
        ],
    ),
    (
        "wan-training",
        "Wan LoRA training",
        "training",
        ["fal-ai/wan-trainer", "fal-ai/wan-22-trainer", "fal-ai/wan-22-image-trainer"],
    ),
    ("ltx-training", "LTX training", "training", ["fal-ai/ltx23"]),
    # utility
    ("birefnet", "BiRefNet background removal", "utility", ["fal-ai/birefnet"]),
    ("imageutils", "Image utilities", "utility", ["fal-ai/imageutils"]),
    ("esrgan", "ESRGAN upscaler", "utility", ["fal-ai/esrgan"]),
    ("clarity-upscaler", "Clarity Upscaler", "utility", ["fal-ai/clarity-upscaler"]),
    ("creative-upscaler", "Creative Upscaler", "utility", ["fal-ai/creative-upscaler"]),
    ("aura-sr", "AuraSR", "utility", ["fal-ai/aura-sr"]),
    ("ccsr", "CCSR upscaler", "utility", ["fal-ai/ccsr"]),
    ("topaz", "Topaz", "utility", ["topaz"]),
    ("seedvr", "SeedVR", "utility", ["fal-ai/seedvr"]),
    ("ffmpeg-api", "FFmpeg API", "utility", ["fal-ai/ffmpeg-api"]),
    ("workflow-utilities", "Workflow utilities", "utility", ["fal-ai/workflow-utilities"]),
    ("florence-2", "Florence-2", "utility", ["fal-ai/florence-2"]),
    (
        "moondream",
        "Moondream",
        "utility",
        ["fal-ai/moondream", "fal-ai/moondream2", "fal-ai/moondream3"],
    ),
    ("sam", "Segment Anything", "utility", ["fal-ai/sam", "fal-ai/sam2", "fal-ai/evf-sam"]),
    ("any-llm", "Any LLM", "utility", ["fal-ai/any-llm"]),
    ("llava", "LLaVA", "utility", ["fal-ai/llava", "fal-ai/llavav15"]),
    ("openrouter", "OpenRouter", "utility", ["openrouter"]),
    ("face-swap", "Face swap", "utility", ["fal-ai/face-swap"]),
]

# IDs that older code is likely to use but the sitemap no longer lists. Each one is
# looked up on fal.ai and kept only when its page has a model record for that exact ID.
LEGACY_CANDIDATES = [
    "fal-ai/whisper",
    "fal-ai/any-llm",
    "fal-ai/any-llm/vision",
    "fal-ai/veo2",
    "fal-ai/veo2/image-to-video",
    "fal-ai/veo3",
    "fal-ai/veo3/fast",
    "fal-ai/veo3/image-to-video",
    "fal-ai/sora-2/text-to-video",
    "fal-ai/sora-2/image-to-video",
    "fal-ai/imagen4/preview",
    "fal-ai/imagen4/preview/fast",
    "fal-ai/imagen4/preview/ultra",
    "fal-ai/imagen3",
    "fal-ai/imagen3/fast",
    "fal-ai/luma-dream-machine",
    "fal-ai/luma-dream-machine/image-to-video",
    "fal-ai/luma-dream-machine/ray-2",
    "fal-ai/minimax-video",
    "fal-ai/minimax-video/image-to-video",
    "fal-ai/mochi-v1",
    "fal-ai/haiper-video-v2",
    "fal-ai/haiper-video-v2/image-to-video",
    "fal-ai/runway-gen3/turbo/image-to-video",
    "fal-ai/recraft-v3",
    "fal-ai/recraft/v3/text-to-image",
    "fal-ai/flux-realism",
    "fal-ai/flux-pro/v1.1",
    "fal-ai/flux-pro/v1.1-ultra",
    "fal-ai/flux-pro/new",
    "fal-ai/face-swap",
    "fal-ai/ccsr",
    "fal-ai/llavav15-13b",
    "fal-ai/idm-vton",
    "fal-ai/stable-fast-3d",
    "fal-ai/sd15-depth-controlnet",
    "fal-ai/fast-turbo-diffusion",
    "fal-ai/lightning-models",
    "fal-ai/lcm",
    "fal-ai/hyper-sdxl",
    "fal-ai/playai/tts/v3",
    "fal-ai/playai/tts/dialog",
    "fal-ai/playht/tts/v3",
    "fal-ai/gemini-flash-edit",
    "fal-ai/kling-video/v1/standard/text-to-video",
    "fal-ai/kling-video/v1.6/pro/image-to-video",
    "fal-ai/kling-video/v2/master/image-to-video",
    "fal-ai/kling-video/v2.1/master/image-to-video",
    "fal-ai/bytedance/seedream/v3/text-to-image",
    "fal-ai/ideogram/v2",
    "fal-ai/ideogram/v3",
    "fal-ai/tripo3d/tripo/v2.5/image-to-3d",
    "fal-ai/animatediff-v2v",
    "fal-ai/face-to-sticker",
    "fal-ai/instantid",
    "fal-ai/illusion-diffusion",
    "fal-ai/stable-diffusion-v3-medium",
    "fal-ai/stable-diffusion-v35-large",
    "fal-ai/wan/v2.1/1.3b/text-to-video",
    "fal-ai/wan/v2.2-a14b/text-to-video",
    "fal-ai/hunyuan-video",
    "fal-ai/musicgen",
    "fal-ai/metavoice-v1",
]


def _cache_file(cache_dir: Path | None, name: str) -> Path | None:
    return cache_dir / re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_")[:150] if cache_dir else None


def fetch_text(client: httpx.Client, url: str, cache_dir: Path | None) -> str:
    """GET a page, with an optional on-disk copy so a rebuild does not refetch."""
    cached = _cache_file(cache_dir, url.split("://", 1)[1])
    if cached and cached.exists():
        return cached.read_text(encoding="utf-8", errors="replace")
    resp = client.get(url)
    time.sleep(REQUEST_GAP_SECONDS)
    if resp.status_code != 200:
        raise SystemExit(f"{url} answered {resp.status_code}, refusing to build")
    if cached:
        cached.write_text(resp.text, encoding="utf-8")
    return resp.text


def fetch_json(
    client: httpx.Client,
    params: list[tuple[str, str]],
    cache_dir: Path | None,
    name: str,
    *,
    missing_ok: bool = False,
) -> dict:
    """One catalogue request. Waits out a 429 as told, at most five times."""
    cached = _cache_file(cache_dir, name)
    if cached and cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    for _attempt in range(6):
        resp = client.get(CATALOGUE_URL, params=params)
        if resp.status_code == 429:
            wait = int(resp.headers.get("retry-after", "10") or "10")
            time.sleep(wait + 5)
            continue
        time.sleep(REQUEST_GAP_SECONDS)
        if resp.status_code == 404 and missing_ok:
            doc: dict = {"models": []}
        elif resp.status_code != 200:
            raise SystemExit(f"catalogue answered {resp.status_code}, refusing to build")
        else:
            doc = resp.json()
        if cached:
            cached.write_text(json.dumps(doc), encoding="utf-8")
        return doc
    raise SystemExit("catalogue kept answering 429, refusing to build")


def _row(model: dict, source: str) -> dict:
    """A catalogue record. A record without metadata is an endpoint fal no longer lists."""
    md = model.get("metadata") or {}
    status = md.get("status") or ("unlisted" if not md else "unknown")
    return {
        "source": source,
        "category": md.get("category"),
        "title": (md.get("display_name") or "").strip() or None,
        "kind": md.get("kind"),
        "status": status,
        "deprecated": status == "deprecated",
    }


def load_catalogue(client: httpx.Client, cache_dir: Path | None) -> dict[str, dict]:
    """Every endpoint the catalogue lists, by cursor until has_more is false."""
    out: dict[str, dict] = {}
    cursor: str | None = None
    for page in range(1, 50):
        params = [("limit", str(CATALOGUE_PAGE_SIZE))]
        if cursor:
            params.append(("cursor", cursor))
        doc = fetch_json(client, params, cache_dir, f"catalogue_page_{page}.json")
        for model in doc.get("models", []):
            out[model["endpoint_id"]] = _row(model, "catalogue")
        cursor = doc.get("next_cursor")
        if not doc.get("has_more") or not cursor:
            return out
    raise SystemExit("catalogue did not end after 50 pages, refusing to build")


def lookup_endpoints(
    client: httpx.Client, ids: list[str], cache_dir: Path | None
) -> dict[str, dict]:
    """Ask the catalogue for specific IDs, one at a time (an unknown ID answers 404)."""
    out: dict[str, dict] = {}
    for eid in ids:
        doc = fetch_json(
            client,
            [("endpoint_id", eid)],
            cache_dir,
            f"catalogue_lookup_{eid}.json",
            missing_ok=True,
        )
        for model in doc.get("models", []):
            if model.get("endpoint_id") == eid:
                out[eid] = _row(model, "lookup")
    return out


def parse_sitemap(xml: str) -> set[str]:
    ids = set()
    for path in re.findall(r"<loc>https://fal\.ai/models/([^<]+)</loc>", xml):
        parts = path.strip("/").split("/")
        if parts[-1] in PAGE_SUFFIXES:
            parts = parts[:-1]
        if len(parts) >= 2:
            ids.add("/".join(parts))
    return ids


def parse_explore(html: str) -> dict[str, list[str]]:
    """Which featured section of fal's explore page lists which endpoint ID."""
    events: list[tuple[int, str, str]] = []
    for m in re.finditer(r"<h[1-4][^>]*>(.*?)</h[1-4]>", html, flags=re.S):
        title = re.sub(r"<[^>]+>", "", m.group(1))
        title = re.sub(r"\s+", " ", title).strip()
        if title:
            events.append((m.start(), "h", title))
    for m in re.finditer(r'href="(?:https://fal\.ai)?/models/([^"#?]+)"', html):
        parts = m.group(1).strip("/").split("/")
        if parts[-1] in PAGE_SUFFIXES:
            continue
        events.append((m.start(), "a", "/".join(parts)))
    events.sort()
    sections: dict[str, list[str]] = {}
    current = ""
    for _, kind, value in events:
        if kind == "h":
            current = value
        elif current:
            ids = sections.setdefault(current, [])
            if value not in ids:
                ids.append(value)
    return {
        name: ids
        for name, ids in sections.items()
        if len(ids) >= MIN_SECTION_SIZE and not name.startswith(SKIP_SECTION_PREFIXES)
    }


def media_from_id(endpoint_id: str) -> str | None:
    """A media group read off the ID itself, when a path segment is one of fal's category slugs."""
    segments = endpoint_id.split("/")
    for seg in segments:
        if seg in MEDIA_OF_CATEGORY and seg != "unknown":
            return MEDIA_OF_CATEGORY[seg]
    joined = "/".join(segments[1:])
    if "trainer" in joined or "training" in joined:
        return "training"
    if "reference-to-video" in segments or "extend-video" in segments:
        return "video"
    if "tts" in tokenize(joined):
        return "audio"
    return None


def build(cache_dir: Path | None, read_on: str, skip_legacy: bool) -> tuple[dict, list[dict]]:
    client = httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60.0, follow_redirects=True)
    endpoints = load_catalogue(client, cache_dir)
    if len(endpoints) < 500:
        raise SystemExit(f"catalogue gave only {len(endpoints)} endpoints, refusing to build")
    catalogue_count = len(endpoints)
    sitemap_ids = parse_sitemap(fetch_text(client, SITEMAP_URL, cache_dir))
    sections = parse_explore(fetch_text(client, EXPLORE_URL, cache_dir))

    # IDs fal features but the catalogue list leaves out, plus our legacy candidates:
    # ask the catalogue for each by ID and keep only what it returns.
    featured_ids = {eid for ids in sections.values() for eid in ids}
    candidates = sorted(
        (
            featured_ids
            | (sitemap_ids if not skip_legacy else set())
            | (set(LEGACY_CANDIDATES) if not skip_legacy else set())
        )
        - set(endpoints)
    )
    found = lookup_endpoints(client, candidates, cache_dir) if candidates else {}
    client.close()
    legacy_checked = len(candidates)
    legacy_kept = len(found)
    for eid in candidates:
        if eid in found:
            endpoints[eid] = found[eid]
        else:
            print(f"  not in the catalogue, left out: {eid}")

    # Curated families first: longest token prefix wins.
    curated_patterns: list[tuple[tuple[str, ...], str, str]] = []
    for fid, _name, _media, patterns in CURATED:
        for p in patterns:
            curated_patterns.append((tokenize(p), p, fid))
    curated_patterns.sort(key=lambda x: (-len(x[0]), x[0]))

    assigned: dict[str, str] = {}
    used_patterns: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    leftovers: list[str] = []
    for eid in endpoints:
        toks = tokenize(eid)
        for ptoks, pattern, fid in curated_patterns:
            if toks[: len(ptoks)] == ptoks:
                assigned[eid] = fid
                used_patterns[fid][pattern] += 1
                break
        else:
            leftovers.append(eid)

    # Everything else: one family per first two path segments, merged when one
    # prefix is a token prefix of another (the code search phrase could not tell them apart).
    auto_prefix: dict[tuple[str, ...], str] = {}
    for eid in leftovers:
        prefix = "/".join(eid.split("/")[:2])
        auto_prefix.setdefault(tokenize(prefix), prefix)
    roots: dict[tuple[str, ...], str] = {}
    for toks in sorted(auto_prefix, key=lambda t: (len(t), t)):
        if not any(toks[: len(r)] == r for r in roots):
            roots[toks] = auto_prefix[toks]
    auto_families: dict[str, dict] = {}
    curated_ids = {c[0] for c in CURATED}
    for eid in leftovers:
        toks = tokenize(eid)
        root = max((r for r in roots if toks[: len(r)] == r), key=len)
        prefix = roots[root]
        ns, seg = prefix.split("/", 1)
        fid = seg if ns == "fal-ai" else f"{ns}-{seg}"
        fid = re.sub(r"[^a-z0-9.-]+", "-", fid.lower())
        if fid in curated_ids:
            fid = f"{fid}-other"
        auto_families.setdefault(fid, {"pattern": prefix})
        assigned[eid] = fid
        used_patterns[fid][prefix] += 1

    members: dict[str, list[str]] = collections.defaultdict(list)
    for eid, fid in assigned.items():
        members[fid].append(eid)

    section_of: dict[str, list[str]] = collections.defaultdict(list)
    for name, ids in sections.items():
        for eid in ids:
            section_of[eid].append(name)

    def representative(ids: list[str], primary: str | None) -> str:
        """The endpoint whose page stands for the family.

        Prefers the query pattern's own endpoints, then the most featured one.
        """
        ptoks = tokenize(primary) if primary else ()

        def rank(eid: str) -> tuple:
            row = endpoints[eid]
            return (
                tokenize(eid)[: len(ptoks)] != ptoks,
                bool(row["deprecated"]),
                -len(section_of.get(eid, [])),
                row["source"] != "catalogue",
                eid.count("/"),
                eid,
            )

        return min(ids, key=rank)

    families: list[dict] = []
    dropped: list[str] = []

    def family_row(
        fid: str, name: str | None, media: str | None, patterns: list[str], curated: bool
    ) -> dict | None:
        ids = sorted(members.get(fid, []))
        if not ids:
            return None
        live = [p for p in patterns if used_patterns[fid][p] > 0]
        dropped.extend(f"{fid}: {p}" for p in patterns if p not in live)
        rep = representative(ids, next((p for p in live if p.startswith("fal-ai/")), None))
        cats = collections.Counter(
            endpoints[e]["category"] for e in ids if endpoints[e]["category"]
        )
        basis = "curated"
        if media is None:
            votes = collections.Counter()
            for e in ids:
                cat = endpoints[e]["category"]
                if endpoints[e].get("kind") == "training":
                    m = "training"
                else:
                    m = MEDIA_OF_CATEGORY.get(cat) if cat else media_from_id(e)
                if m:
                    votes[m] += 1
            if cats:
                basis = "fal category tags"
            elif votes:
                basis = "endpoint id"
            else:
                basis = "none"
            media = votes.most_common(1)[0][0] if votes else "unknown"
        query_pattern = next((p for p in live if p.startswith("fal-ai/")), None)
        featured = []
        for e in ids:
            for s in section_of.get(e, []):
                if s not in featured:
                    featured.append(s)
        n_dep = sum(1 for e in ids if endpoints[e]["deprecated"])
        return {
            "id": fid,
            "name": name or endpoints[rep]["title"] or fid,
            "media": media,
            "media_basis": basis,
            "curated": curated,
            "query": f'"{query_pattern}"' if query_pattern else None,
            "discover": False,
            "patterns": live,
            "url": MODEL_URL.format(id=rep),
            "endpoints": len(ids) - n_dep,
            "deprecated_endpoints": n_dep,
            "featured": featured,
            "categories": dict(sorted(cats.items())),
        }

    for fid, name, media, patterns in CURATED:
        row = family_row(fid, name, media, patterns, curated=True)
        if row:
            families.append(row)
        else:
            dropped.append(f"{fid}: whole family (no endpoint found on fal.ai)")
    for fid, info in sorted(auto_families.items()):
        row = family_row(fid, None, None, [info["pattern"]], curated=False)
        if row:
            families.append(row)

    for row in families:
        prominent = row["curated"] or bool(row["featured"]) or row["endpoints"] >= 3
        row["discover"] = bool(row["query"]) and prominent
    families.sort(key=lambda r: r["id"])

    # Self-check: the shipped index must reproduce the assignment for every endpoint.
    index = FamilyIndex(
        [
            Family(
                id=r["id"],
                name=r["name"],
                media=r["media"],
                patterns=tuple(r["patterns"]),
                url=r["url"],
            )
            for r in families
        ]
    )
    wrong = [e for e in endpoints if index.match(e) != assigned[e]]
    if wrong:
        raise SystemExit(f"{len(wrong)} endpoints map inconsistently, e.g. {wrong[:5]}")

    namespaces = collections.Counter(e.split("/", 1)[0] for e in endpoints)
    meta = {
        "generated_by": "scripts/build_models.py",
        "read_on": read_on,
        "sources": {
            "catalogue": CATALOGUE_URL,
            "explore": EXPLORE_URL,
            "sitemap": SITEMAP_URL,
            "model_pages": MODEL_URL.format(id="<id>"),
        },
        "endpoints_total": len(endpoints),
        "endpoints_in_catalogue_list": catalogue_count,
        "endpoints_in_sitemap": len(sitemap_ids),
        "sitemap_ids_missing_from_catalogue_list": len(sitemap_ids - set(endpoints)),
        "endpoints_found_by_lookup": legacy_kept,
        "lookup_candidates": legacy_checked,
        "endpoints_deprecated": sum(1 for r in endpoints.values() if r["deprecated"]),
        "endpoints_outside_fal_ai_namespace": len(endpoints) - namespaces["fal-ai"],
        "namespaces": len(namespaces),
        "families": len(families),
        "families_curated": sum(1 for r in families if r["curated"]),
        "families_queried": sum(1 for r in families if r["discover"]),
        "explore_sections": list(sections),
        "notes": [
            "Patterns match by token prefix; the longest pattern wins.",
            "A family's query phrase also matches longer IDs that start with it, "
            "including IDs that map to a more specific family.",
            "Families without a query have no fal-ai pattern. Their IDs are only "
            "counted inside repos that already have code evidence.",
            "The catalogue list holds active endpoints. Deprecated or unlisted ones "
            "enter only when a lookup by ID returns a record for them.",
        ],
    }
    rows = [{"id": eid, "family": assigned[eid], **endpoints[eid]} for eid in sorted(endpoints)]
    if dropped:
        print("patterns without any endpoint on fal.ai (left out):")
        for d in dropped:
            print("  " + d)
    return {"meta": meta, "families": families}, rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache-dir", type=Path, help="reuse downloaded pages from this folder")
    ap.add_argument(
        "--date",
        default=dt.datetime.now(dt.UTC).date().isoformat(),
        help="the day the pages were read, YYYY-MM-DD (default: today, UTC)",
    )
    ap.add_argument("--skip-legacy", action="store_true", help="do not look up legacy candidates")
    args = ap.parse_args()
    if args.cache_dir:
        args.cache_dir.mkdir(parents=True, exist_ok=True)

    doc, rows = build(args.cache_dir, args.date, args.skip_legacy)

    header = (
        "# Generated by scripts/build_models.py. Do not edit by hand.\n"
        f"# Read from fal.ai on {args.date}: the sitemap, the explore page and, for\n"
        "# legacy endpoints, one model page each. Unofficial, not affiliated with fal.\n"
    )
    out_yaml = ROOT / "collector" / "models.yaml"
    out_yaml.write_text(
        header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    out_tsv = ROOT / "collector" / "fal_endpoints.tsv"
    lines = [header.rstrip("\n"), "id\tfamily\tsource\tstatus\tdeprecated\tcategory"]
    for r in rows:
        lines.append(
            "\t".join(
                [
                    r["id"],
                    r["family"],
                    r["source"],
                    r["status"] or "",
                    "yes" if r["deprecated"] else "no",
                    r["category"] or "",
                ]
            )
        )
    out_tsv.write_text("\n".join(lines) + "\n", encoding="utf-8")

    m = doc["meta"]
    print(
        f"endpoints {m['endpoints_total']}: catalogue list {m['endpoints_in_catalogue_list']}, "
        f"by lookup {m['endpoints_found_by_lookup']} of {m['lookup_candidates']} candidates, "
        f"deprecated {m['endpoints_deprecated']}"
    )
    print(
        f"sitemap cross-check: {m['endpoints_in_sitemap']} IDs, "
        f"{m['sitemap_ids_missing_from_catalogue_list']} of them not in the catalogue"
    )
    print(f"namespaces {m['namespaces']}, outside fal-ai {m['endpoints_outside_fal_ai_namespace']}")
    print(
        f"families {m['families']}: curated {m['families_curated']}, "
        f"queried {m['families_queried']}"
    )
    print(f"wrote {out_yaml.relative_to(ROOT)} and {out_tsv.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
