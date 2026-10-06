"""Append-only run log (JSON lines) under data/runs/, plus helpers to find a run to resume."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_utc() -> str:
    return dt.datetime.now(dt.UTC).date().isoformat()


class RunLog:
    def __init__(self, runs_dir: Path, run_id: str) -> None:
        self.run_id = run_id
        self.path = runs_dir / f"{run_id}.jsonl"
        runs_dir.mkdir(parents=True, exist_ok=True)

    def event(self, name: str, **fields: Any) -> None:
        record = {"ts": utc_now_iso(), "event": name, **fields}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=False) + "\n")


def read_events(path: Path) -> list[dict]:
    events = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            # A line cut short by a crash; the run is unfinished either way.
            continue
    return events


def is_finished(path: Path) -> bool:
    """True when the last full-run start in the log was followed by a finish.

    Limited runs (--only, --limit-repos, a skipped stage, --offline) do not count, so a
    quick check on the same day cannot hide a crashed full run from --resume.
    """
    finished = True
    in_full = False
    for ev in read_events(path):
        kind = ev.get("event")
        if kind == "run_started":
            in_full = not ev.get("limited") and not ev.get("offline")
            if in_full:
                finished = False
        elif kind == "run_finished" and in_full:
            finished = True
    return finished


def has_full_run(path: Path) -> bool:
    return any(
        ev.get("event") == "run_started" and not ev.get("limited") and not ev.get("offline")
        for ev in read_events(path)
    )


def latest_unfinished(runs_dir: Path) -> str | None:
    """Run id of the newest log with a full run, if that full run never finished."""
    if not runs_dir.exists():
        return None
    for path in sorted(runs_dir.glob("*.jsonl"), reverse=True):
        if not has_full_run(path):
            continue
        return None if is_finished(path) else path.stem
    return None
