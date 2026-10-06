"""Read one dependency manifest per repository: what it depends on and whether it is a package.

Only dependency names and a few top-level fields are used. Nothing from a manifest is
stored except the labels derived here (stack, bot, library).
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass, field

import yaml

MANIFEST_NAMES = (
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "pubspec.yaml",
)
# Lower number wins when a repo has several manifests among its hits.
MANIFEST_PREFERENCE = {name: i for i, name in enumerate(MANIFEST_NAMES)}

# Dependency name (exact, or a prefix ending in "/" or "-") -> stack label.
STACK_DEPS = {
    "next": "next",
    "react": "react",
    "vue": "vue",
    "nuxt": "vue",
    "svelte": "svelte",
    "@sveltejs/kit": "svelte",
    "@remix-run/": "remix",
    "expo": "expo",
    "fastapi": "fastapi",
    "django": "django",
    "flask": "flask",
    "gradio": "gradio",
    "streamlit": "streamlit",
    "n8n-workflow": "n8n",
    "n8n-core": "n8n",
}
# Frameworks that make a JS package an app rather than a library.
APP_FRAMEWORKS = {
    "next",
    "nuxt",
    "@sveltejs/kit",
    "expo",
    "react-scripts",
    "astro",
    "gatsby",
    "@remix-run/react",
    "@remix-run/node",
    "electron",
    "vite",
    "@angular/core",
}
PY_APP_FRAMEWORKS = {"fastapi", "django", "flask", "gradio", "streamlit"}
BOT_DEPS = {
    "discord.js",
    "discord.py",
    "py-cord",
    "nextcord",
    "telegraf",
    "grammy",
    "node-telegram-bot-api",
    "python-telegram-bot",
    "aiogram",
    "pytelegrambotapi",
    "@slack/bolt",
    "slack-bolt",
    "slack_bolt",
}

_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
# Bigger manifests are skipped: real ones are small, and parsing untrusted text has limits.
MAX_MANIFEST_CHARS = 256_000
# Scripts that mark a package.json as something you run, not something you import.
RUN_SCRIPTS = {"start", "dev", "serve"}


@dataclass
class ManifestSignals:
    kind: str  # the manifest file name
    deps: set[str] = field(default_factory=set)
    is_package: bool = False  # declares itself as something to install (main/exports, [project])
    has_entry_point: bool = False  # bin, scripts, [project.scripts]
    private: bool = False
    flutter: bool = False

    @property
    def stack(self) -> list[str]:
        found = set()
        for dep in self.deps:
            for key, label in STACK_DEPS.items():
                if dep == key or (key.endswith(("/", "-")) and dep.startswith(key)):
                    found.add(label)
        if self.flutter:
            found.add("flutter")
        return sorted(found)

    @property
    def bot(self) -> bool:
        if self.kind in ("requirements.txt", "pyproject.toml"):
            return bool(self.deps & PY_BOT_DEPS)
        return bool(self.deps & BOT_DEPS)

    @property
    def library(self) -> bool:
        if self.kind == "package.json":
            return self.is_package and not self.private and not (self.deps & APP_FRAMEWORKS)
        if self.kind == "pyproject.toml":
            return (
                self.is_package
                and not self.has_entry_point
                and not (self.deps & PY_APP_FRAMEWORKS_NORM)
            )
        return False


def _norm_py(name: str) -> str:
    """PEP 503 normalisation: discord.py, discord_py and Discord-Py are one name."""
    return re.sub(r"[-_.]+", "-", name.strip().lower())


PY_BOT_DEPS = {_norm_py(d) for d in BOT_DEPS}
PY_APP_FRAMEWORKS_NORM = {_norm_py(d) for d in PY_APP_FRAMEWORKS}


def parse(name: str, text: str) -> ManifestSignals | None:
    """Signals from one manifest, or None when the file cannot be read as that format.

    Third-party input: any failure to parse (bad syntax, a byte order mark, deep nesting,
    odd types) gives None rather than an exception that would end the run.
    """
    text = text.removeprefix("\ufeff")
    if len(text) > MAX_MANIFEST_CHARS:
        return None
    try:
        if name == "package.json":
            return _package_json(text)
        if name == "pyproject.toml":
            return _pyproject(text)
        if name == "requirements.txt":
            return _requirements(text)
        if name == "pubspec.yaml":
            return _pubspec(text)
    except Exception:
        return None
    return None


def _package_json(text: str) -> ManifestSignals:
    pkg = json.loads(text)
    if not isinstance(pkg, dict):
        raise ValueError("package.json is not an object")
    deps: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        section = pkg.get(key)
        if isinstance(section, dict):
            deps.update(k.lower() for k in section)
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    runs = bool(RUN_SCRIPTS & set(scripts))
    # `npm init` writes "main" by default, so "main" alone is not a sign of a library.
    publishes = any(pkg.get(k) for k in ("exports", "module", "types", "typings", "files"))
    return ManifestSignals(
        kind="package.json",
        deps=deps,
        is_package=publishes or (bool(pkg.get("main")) and not runs),
        has_entry_point=bool(pkg.get("bin")),
        private=bool(pkg.get("private")),
    )


def _pyproject(text: str) -> ManifestSignals:
    doc = tomllib.loads(text)
    project = doc.get("project") or {}
    deps: set[str] = set()
    for spec in project.get("dependencies") or []:
        m = _REQ_NAME.match(str(spec))
        if m:
            deps.add(_norm_py(m.group(1)))
    poetry = ((doc.get("tool") or {}).get("poetry")) or {}
    for spec in poetry.get("dependencies") or {}:
        deps.add(_norm_py(spec))
    scripts = project.get("scripts") or poetry.get("scripts") or {}
    # A name alone is not enough (every pyproject has one); a build system is.
    builds = isinstance(doc.get("build-system"), dict)
    return ManifestSignals(
        kind="pyproject.toml",
        deps=deps,
        is_package=builds and bool(project.get("name") or poetry.get("name")),
        has_entry_point=bool(scripts),
    )


def _requirements(text: str) -> ManifestSignals:
    deps = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http")):
            continue
        m = _REQ_NAME.match(line)
        if m:
            deps.add(_norm_py(m.group(1)))
    return ManifestSignals(kind="requirements.txt", deps=deps)


def _pubspec(text: str) -> ManifestSignals:
    doc = yaml.safe_load(text) or {}
    deps = set()
    for key in ("dependencies", "dev_dependencies"):
        section = doc.get(key)
        if isinstance(section, dict):
            deps.update(str(k).lower() for k in section)
    return ManifestSignals(kind="pubspec.yaml", deps=deps, flutter="flutter" in deps)


def best_manifest_path(paths: list[str]) -> str | None:
    """The manifest to read for a repo: the preferred type, then the shallowest path."""
    candidates = [p for p in paths if p.rsplit("/", 1)[-1] in MANIFEST_PREFERENCE]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda p: (MANIFEST_PREFERENCE[p.rsplit("/", 1)[-1]], p.count("/"), p),
    )
