#!/usr/bin/env python3
"""Write data/fal_catalogue.json: every endpoint in fal's public model catalogue with its
creation date, from the documented API https://api.fal.ai/v1/models (no key needed).

The digest's "Models this week" counts endpoints by these dates. Requests are spaced
four seconds apart and a 429 is answered by waiting for Retry-After.

    uv run python scripts/catalogue_dates.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "fal_catalogue.json"
URL = "https://api.fal.ai/v1/models"
USER_AGENT = "fal-radar/0.1 (+https://github.com/ardanoyan/fal-radar)"
PAGE = 500
GAP = 4.0


def fetch_all() -> list[dict]:
    models: list[dict] = []
    cursor = None
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=60) as client:
        for _ in range(50):
            params = {"limit": PAGE, **({"cursor": cursor} if cursor else {})}
            for _attempt in range(6):
                resp = client.get(URL, params=params)
                if resp.status_code == 429:
                    time.sleep(int(resp.headers.get("retry-after", "10") or "10") + 5)
                    continue
                break
            resp.raise_for_status()
            doc = resp.json()
            models.extend(doc.get("models", []))
            cursor = doc.get("next_cursor")
            time.sleep(GAP)
            if not doc.get("has_more") or not cursor:
                return models
    raise SystemExit("catalogue did not end after 50 pages")


def main() -> None:
    models = fetch_all()
    if len(models) < 500:
        raise SystemExit(f"only {len(models)} endpoints returned, refusing to write")
    rows = []
    for m in models:
        md = m.get("metadata") or {}
        rows.append(
            {
                "id": m["endpoint_id"],
                "name": (md.get("display_name") or "").strip() or None,
                "category": md.get("category"),
                "created": (md.get("date") or "")[:10] or None,
                "status": md.get("status"),
            }
        )
    rows.sort(key=lambda r: r["id"])
    doc = {
        "read_on": dt.datetime.now(dt.UTC).date().isoformat(),
        "source": URL,
        "endpoints": rows,
    }
    tmp = OUT.with_name(f".{OUT.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=0) + "\n", encoding="utf-8")
    os.replace(tmp, OUT)
    dated = sum(1 for r in rows if r["created"])
    print(f"wrote {OUT.relative_to(ROOT)}: {len(rows)} endpoints, {dated} with a date", file=sys.stderr)


if __name__ == "__main__":
    main()
