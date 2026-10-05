"""Shared fakes. No test touches the network: every response comes from a fake transport."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from collector.cache import DiskCache
from collector.github import GitHubClient

# Built at runtime so the secret check never sees a token-shaped string in this file.
FAKE_TOKEN = "test-" + "token"


class FakeClock:
    """Time that only moves when the code under test sleeps."""

    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def json_response(
    status: int, body: object, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(body).encode(),
        headers={"content-type": "application/json", **(headers or {})},
    )


class Recorder:
    """A transport that answers with `handler` and remembers every request."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_client(tmp_path, clock):
    def _make(handler, *, run_id: str = "2026-10-05", etags=None, offline: bool = False):
        recorder = Recorder(handler)
        client = GitHubClient(
            None if offline else FAKE_TOKEN,
            DiskCache(tmp_path / "cache"),
            run_id,
            offline=offline,
            etags=etags,
            transport=recorder.transport,
            sleep=clock.sleep,
            now=clock.now,
            log=lambda _msg: None,
        )
        return client, recorder

    return _make
