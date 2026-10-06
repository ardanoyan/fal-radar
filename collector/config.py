"""Paths, constants and the one place where the token is read."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

API_ROOT = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "fal-radar/0.1 (+https://github.com/ardanoyan/fal-radar)"
TOKEN_ENV = "GH_SEARCH_TOKEN"


@dataclass(frozen=True)
class Paths:
    """Where the collector reads and writes. Tests point this at a temp folder."""

    root: Path

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def cache(self) -> Path:
        return self.root / "collector" / ".cache"

    @property
    def repos(self) -> Path:
        return self.data / "repos.json"

    @property
    def etags(self) -> Path:
        return self.data / "etags.json"

    @property
    def gone(self) -> Path:
        return self.data / "gone.json"

    @property
    def exclusions(self) -> Path:
        return self.data / "exclusions.json"

    @property
    def submissions(self) -> Path:
        return self.data / "submissions.json"

    @property
    def runs(self) -> Path:
        return self.data / "runs"

    @property
    def env_file(self) -> Path:
        return self.root / ".env"


DEFAULT_PATHS = Paths(ROOT)


class MissingToken(RuntimeError):
    pass


def load_dotenv(path: Path) -> None:
    """Copy KEY=VALUE lines from .env into the environment, without overriding or printing."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and value and not os.environ.get(key):
            os.environ[key] = value


def get_token(paths: Paths = DEFAULT_PATHS) -> str:
    """The GitHub token, from the environment only (a local .env counts). Never a flag."""
    load_dotenv(paths.env_file)
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        raise MissingToken(
            f"{TOKEN_ENV} is not set. Put it in {paths.env_file} as {TOKEN_ENV}=... "
            "or export it in the environment."
        )
    return token
