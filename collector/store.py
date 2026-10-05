"""Reading and writing data/. Every write goes to a temp file first, then renames into place."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .schema import Exclusions, Repo


def write_json_atomic(path: Path, payload: Any, *, indent: int | None = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=indent)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_repos(path: Path) -> dict[int, Repo]:
    """Previous repos.json, keyed by repository id (ids survive renames)."""
    return {r.id: r for r in (Repo.model_validate(x) for x in read_json(path, []))}


def save_repos(path: Path, repos: list[Repo]) -> None:
    ordered = sorted(repos, key=lambda r: (r.full_name.lower(), r.id))
    write_json_atomic(path, [r.model_dump(mode="json") for r in ordered])


def load_exclusions(path: Path) -> Exclusions:
    return Exclusions.model_validate(read_json(path, {}))


def load_etags(path: Path) -> dict[str, str]:
    return dict(read_json(path, {}))


def save_etags(path: Path, etags: dict[str, str]) -> None:
    write_json_atomic(path, dict(sorted(etags.items())))
