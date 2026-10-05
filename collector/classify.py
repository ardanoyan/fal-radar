"""Heuristic labels. No LLM. Phase 0 covers what repository metadata alone can tell.

Still to come in Phase 1 (they need manifests or a README lookup): the library and bot
kinds, stack from manifests, and the README half of the notable rule.
"""

from __future__ import annotations

import datetime as dt
import re

from .models import FamilyIndex, extract_fal_ai_ids

ACTIVE_DAYS = 90
NOTABLE_RECENT_DAYS = 30
NOTABLE_MIN_STARS = 3
NOTABLE_MIN_README_BYTES = 500
FORK_KIND_MAX_STARS = 25

TEMPLATE_WORDS = ("starter", "template", "boilerplate", "example", "demo")
PLUGIN_WORDS = ("comfyui", "figma", "obsidian", "raycast", "blender")
RESEARCH_WORDS = ("paper", "arxiv", "benchmark")
STACK_TOPICS = (
    "next",
    "react",
    "vue",
    "svelte",
    "remix",
    "expo",
    "flutter",
    "fastapi",
    "django",
    "flask",
    "gradio",
    "streamlit",
    "comfyui",
    "n8n",
)
STACK_TOPIC_ALIASES = {
    "nextjs": "next",
    "next-js": "next",
    "reactjs": "react",
    "vuejs": "vue",
    "sveltekit": "svelte",
    "react-native": "expo",
}


def _has_word(text: str, words: tuple[str, ...]) -> bool:
    return any(re.search(rf"(?<![a-z0-9]){re.escape(w)}", text) for w in words)


def parse_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def days_since(value: str | None, today: dt.date) -> int | None:
    when = parse_time(value)
    return (today - when.date()).days if when else None


def kind_of(
    *, name: str, description: str | None, fork: bool, is_template: bool, stars: int
) -> str:
    text_name = name.lower()
    text_all = f"{text_name} {(description or '').lower()}"
    if is_template or _has_word(text_all, TEMPLATE_WORDS):
        return "template"
    if fork and stars < FORK_KIND_MAX_STARS:
        return "fork"
    if _has_word(text_all, PLUGIN_WORDS):
        return "plugin"
    if _has_word((description or "").lower(), RESEARCH_WORDS):
        return "research"
    return "app"


def is_active(pushed_at: str | None, today: dt.date) -> bool:
    age = days_since(pushed_at, today)
    return age is not None and age <= ACTIVE_DAYS


def is_notable(
    *,
    fork: bool,
    kind: str,
    description: str | None,
    stars: int,
    pushed_at: str | None,
    today: dt.date,
    readme_bytes: int | None = None,
) -> bool:
    """Not a fork, not a template, described, and either starred or recently pushed.

    Recently pushed only counts with a README over 500 bytes.

    readme_bytes is None until the README lookup exists, which makes this a lower bound.
    """
    if fork or kind == "template" or not (description or "").strip():
        return False
    if stars >= NOTABLE_MIN_STARS:
        return True
    age = days_since(pushed_at, today)
    recent = age is not None and age <= NOTABLE_RECENT_DAYS
    return recent and readme_bytes is not None and readme_bytes > NOTABLE_MIN_README_BYTES


def stack_from_topics(topics: list[str]) -> list[str]:
    found = set()
    for topic in topics:
        t = STACK_TOPIC_ALIASES.get(topic.lower(), topic.lower())
        if t in STACK_TOPICS:
            found.add(t)
    return sorted(found)


def models_from_fragments(fragments: list[str], index: FamilyIndex) -> tuple[list[str], list[str]]:
    """(families, unknown IDs) seen in code search fragments. A lower bound by construction."""
    families: set[str] = set()
    unknown: set[str] = set()
    for fragment in fragments:
        for endpoint_id in extract_fal_ai_ids(fragment):
            family = index.match(endpoint_id)
            if family:
                families.add(family)
            else:
                unknown.add(endpoint_id)
    return sorted(families), sorted(unknown)
