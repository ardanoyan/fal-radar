"""Model families: load collector/models.yaml and map fal endpoint IDs to families.

A family is matched by token prefix. "fal-ai/flux-2-pro/edit" becomes the tokens
[fal, ai, flux, 2, pro, edit]; the pattern "fal-ai/flux" becomes [fal, ai, flux] and
matches because it is a prefix. The longest matching pattern wins, so
"fal-ai/flux-lora-fast-training" goes to the training family, not to flux.

This mirrors how GitHub code search treats a quoted phrase (it matches token
sequences, punctuation is ignored), so a family's file count and its repo mapping
are defined the same way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

MODELS_YAML = Path(__file__).with_name("models.yaml")

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

# The brief's rule for IDs in the fal-ai namespace. Partner namespaces (bria/, xai/,
# openai/ and so on) are only trusted inside repos that already have code evidence,
# because strings like "openai/..." are common outside fal.
# The lookbehind keeps npm scope names out ("@fal-ai/client" is a package, not a model).
FAL_AI_ID = re.compile(r"(?<![@a-z0-9_-])fal-ai/[a-z0-9][a-z0-9._/-]*")

# "github.com/fal-ai/fal-js" is a repository path, not an endpoint.
_NOT_AN_ENDPOINT_BEFORE = ("github.com/", "githubusercontent.com/", "npmjs.com/package/")

# Characters that end an ID in prose or code but are legal inside one.
_TRAILING = "./-_"


def tokenize(endpoint_id: str) -> tuple[str, ...]:
    """Split an endpoint ID or a pattern into lower-case alphanumeric tokens."""
    return tuple(t for t in _TOKEN_SPLIT.split(endpoint_id.lower()) if t)


# fal's queue URLs append /requests/<id>/status and the like to the endpoint ID.
# Only that suffix is cut: /stream and /realtime are parts of real endpoint IDs
# (fal-ai/speech-to-text/stream, xai/grok-voice/realtime).
_URL_SUFFIX = re.compile(r"/requests(?:/.*)?$")


def clean_id(raw: str) -> str:
    """Trim what a regex over source text tends to drag along: URL suffixes, a final dot."""
    return _URL_SUFFIX.sub("", raw.rstrip(_TRAILING)).rstrip(_TRAILING)


@dataclass(frozen=True)
class Family:
    id: str
    name: str
    media: str
    patterns: tuple[str, ...]
    url: str
    query: str | None = None
    discover: bool = False
    curated: bool = False
    endpoints: int = 0
    deprecated_endpoints: int = 0
    featured: tuple[str, ...] = ()
    categories: dict[str, int] = field(default_factory=dict, hash=False, compare=False)


class FamilyIndex:
    """Longest-token-prefix lookup from endpoint ID to family."""

    def __init__(self, families: list[Family]) -> None:
        self.families = families
        self.by_id = {f.id: f for f in families}
        if len(self.by_id) != len(families):
            raise ValueError("duplicate family id in models.yaml")
        self._patterns: list[tuple[tuple[str, ...], str]] = []
        seen: dict[tuple[str, ...], str] = {}
        for fam in families:
            for pattern in fam.patterns:
                toks = tokenize(pattern)
                if not toks:
                    raise ValueError(f"empty pattern in family {fam.id}")
                if toks in seen and seen[toks] != fam.id:
                    raise ValueError(f"pattern {pattern!r} is claimed by {seen[toks]} and {fam.id}")
                seen[toks] = fam.id
                self._patterns.append((toks, fam.id))
        # Longest first, so the first hit is the most specific one.
        self._patterns.sort(key=lambda p: (-len(p[0]), p[0]))

    def match(self, endpoint_id: str) -> str | None:
        """Family id for an endpoint ID, or None when no pattern is a token prefix of it."""
        toks = tokenize(endpoint_id)
        for pattern, family_id in self._patterns:
            if toks[: len(pattern)] == pattern:
                return family_id
        return None

    def namespaces(self) -> set[str]:
        return {p.split("/", 1)[0] for f in self.families for p in f.patterns}

    def discovery_queries(self) -> list[tuple[str, str]]:
        """(family id, code search phrase) for the families we query by model ID."""
        return [(f.id, f.query) for f in self.families if f.discover and f.query]


def extract_fal_ai_ids(text: str) -> list[str]:
    """Endpoint IDs in the fal-ai namespace found in a piece of source text, in order, deduped."""
    out: list[str] = []
    lowered = text.lower()
    for m in FAL_AI_ID.finditer(lowered):
        if lowered[: m.start()].endswith(_NOT_AN_ENDPOINT_BEFORE):
            continue
        cleaned = clean_id(m.group(0))
        if "/" in cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def _family_from_dict(d: dict) -> Family:
    return Family(
        id=d["id"],
        name=d["name"],
        media=d["media"],
        patterns=tuple(d["patterns"]),
        url=d["url"],
        query=d.get("query"),
        discover=bool(d.get("discover", False)),
        curated=bool(d.get("curated", False)),
        endpoints=int(d.get("endpoints", 0)),
        deprecated_endpoints=int(d.get("deprecated_endpoints", 0)),
        featured=tuple(d.get("featured", ())),
        categories=dict(d.get("categories", {})),
    )


def load_families(path: Path = MODELS_YAML) -> tuple[dict, FamilyIndex]:
    """Read models.yaml. Returns (meta, index)."""
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    families = [_family_from_dict(d) for d in doc["families"]]
    return doc.get("meta", {}), FamilyIndex(families)


@lru_cache(maxsize=1)
def default_index() -> FamilyIndex:
    return load_families()[1]
