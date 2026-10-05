"""Disk cache for API responses: one gzipped JSON file per URL.

The cache stores response bodies and a few response headers. It never stores
request headers, so the token cannot end up in it.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class CacheEntry:
    url: str
    status: int
    etag: str | None
    fetched_at: str
    run_id: str
    body: Any


class DiskCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.root / digest[:2] / f"{digest}.json.gz"

    def get(self, url: str) -> CacheEntry | None:
        path = self._path(url)
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            return None
        except (OSError, EOFError, json.JSONDecodeError):
            # A file cut short by a crash is treated as missing and fetched again.
            return None
        if raw.get("url") != url:
            return None
        return CacheEntry(**raw)

    def put(self, entry: CacheEntry) -> None:
        path = self._path(entry.url)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as fh:
            json.dump(asdict(entry), fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
