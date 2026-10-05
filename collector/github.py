"""GitHub REST client: one request at a time, cached, paced, and patient with rate limits.

Rules it follows:
- Requests are serial. Each rate limit bucket has a minimum gap between requests.
- A response fetched in the current run is replayed from the disk cache, which is
  what makes an interrupted run resumable without spending the limit twice.
- On a later run, repository and user lookups are revalidated with If-None-Match.
- 403 or 429 with Retry-After: sleep that long plus 5 seconds.
  x-ratelimit-remaining 0: sleep until x-ratelimit-reset.
  Rate limited with neither: back off from one minute, doubling each time.
- The token is sent in the Authorization header and stored nowhere else.
"""

from __future__ import annotations

import time
import urllib.parse
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from . import config
from .cache import CacheEntry, DiskCache
from .runlog import utc_now_iso

# Seconds between requests per bucket: 10 per minute for code search, 30 per minute
# for other searches, 5,000 per hour for everything else, each with a little slack.
MIN_INTERVAL = {"code_search": 6.2, "search": 2.1, "core": 0.75}
ERROR_BACKOFF = (5, 15, 45, 120, 300)
MAX_RATE_WAITS = 8
MAX_SINGLE_WAIT = 3700
SECONDARY_BACKOFF_START = 60
SECONDARY_BACKOFF_MAX = 900
# Statuses a caller can act on (a deleted, blocked or unavailable repository).
TERMINAL_STATUSES = {403, 404, 410, 451}


class GitHubError(RuntimeError):
    def __init__(self, status: int, url: str, message: str) -> None:
        super().__init__(f"GitHub {status} for {url}: {message}")
        self.status = status
        self.url = url
        self.message = message


class AuthError(GitHubError):
    pass


class RateLimitGiveUp(GitHubError):
    pass


class OfflineMiss(RuntimeError):
    """Raised in offline mode when a response is not in the cache."""


@dataclass
class Response:
    url: str
    status: int
    data: Any
    etag: str | None = None
    from_cache: bool = False
    not_modified: bool = False


def build_url(path: str, params: dict[str, Any] | None = None) -> str:
    """Canonical URL: sorted query keys, so one request always maps to one cache file."""
    url = path if path.startswith("http") else f"{config.API_ROOT}{path}"
    if params:
        query = urllib.parse.urlencode(sorted((k, str(v)) for k, v in params.items()))
        url = f"{url}?{query}"
    return url


def _message(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200]
    if isinstance(body, dict):
        return str(body.get("message", ""))[:300]
    return ""


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


class GitHubClient:
    def __init__(
        self,
        token: str | None,
        cache: DiskCache,
        run_id: str,
        *,
        offline: bool = False,
        etags: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
        log: Callable[[str], None] = print,
    ) -> None:
        if not offline and not token:
            raise config.MissingToken(f"{config.TOKEN_ENV} is required unless --offline is set")
        self.cache = cache
        self.run_id = run_id
        self.offline = offline
        self.etags: dict[str, str] = dict(etags or {})
        self._sleep_fn = sleep
        self._now = now
        self._log = log
        self._last_request: dict[str, float] = {}
        self._remaining: dict[str, int] = {}
        self._reset: dict[str, int] = {}
        self.stats: dict[str, dict[str, float]] = defaultdict(
            lambda: {"requests": 0, "cache_hits": 0, "not_modified": 0, "retries": 0, "slept": 0.0}
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": config.API_VERSION,
            "User-Agent": config.USER_AGENT,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(
            headers=headers,
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    # -- public -----------------------------------------------------------------

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        bucket: str = "core",
        accept: str | None = None,
        refresh: bool = False,
        use_cache: bool = True,
    ) -> Response:
        """GET one resource. Returns terminal statuses (404 and friends) instead of raising."""
        url = build_url(path, params)
        entry = self.cache.get(url) if use_cache else None
        if entry is not None and entry.run_id == self.run_id and not refresh:
            self.stats[bucket]["cache_hits"] += 1
            return Response(
                url,
                entry.status,
                entry.body,
                entry.etag,
                from_cache=True,
                not_modified=entry.status == 304,
            )
        if self.offline:
            if entry is None:
                raise OfflineMiss(url)
            self.stats[bucket]["cache_hits"] += 1
            return Response(
                url,
                entry.status,
                entry.body,
                entry.etag,
                from_cache=True,
                not_modified=entry.status == 304,
            )

        headers: dict[str, str] = {}
        if accept:
            headers["Accept"] = accept
        etag = None
        if bucket == "core" and not refresh:
            if entry is not None and entry.status in (200, 304) and entry.etag:
                etag = entry.etag
            elif entry is None:
                etag = self.etags.get(url)
        if etag:
            headers["If-None-Match"] = etag

        resp = self._send(url, headers, bucket)
        status = resp.status_code

        if status == 304:
            self.stats[bucket]["not_modified"] += 1
            body = entry.body if entry is not None and entry.status == 200 else None
            kept_status = 200 if body is not None else 304
            self._store(url, kept_status, etag, body, use_cache)
            if etag:
                self.etags[url] = etag
            return Response(url, kept_status, body, etag, not_modified=True)

        if status == 200:
            data = resp.json()
            new_etag = resp.headers.get("etag")
            self._store(url, 200, new_etag, data, use_cache)
            if new_etag and bucket == "core":
                self.etags[url] = new_etag
            return Response(url, 200, data, new_etag)

        if status in TERMINAL_STATUSES:
            body = {"message": _message(resp)}
            self._store(url, status, None, body, use_cache)
            self.etags.pop(url, None)
            return Response(url, status, body)

        raise GitHubError(status, url, _message(resp))

    def rate_limit(self) -> dict[str, dict[str, int]]:
        """GET /rate_limit (free of charge). Also proves the token works."""
        resp = self.get("/rate_limit", bucket="meta", use_cache=False)
        resources = (resp.data or {}).get("resources", {})
        out = {}
        for name in ("core", "search", "code_search"):
            info = resources.get(name) or {}
            out[name] = {k: int(info.get(k, 0)) for k in ("limit", "remaining", "reset")}
            if info:
                self._remaining[name] = out[name]["remaining"]
                self._reset[name] = out[name]["reset"]
        return out

    # -- internals --------------------------------------------------------------

    def _store(self, url: str, status: int, etag: str | None, body: Any, use_cache: bool) -> None:
        if use_cache:
            self.cache.put(CacheEntry(url, status, etag, utc_now_iso(), self.run_id, body))

    def _sleep(self, seconds: float, reason: str, bucket: str) -> None:
        seconds = max(0.0, min(float(seconds), MAX_SINGLE_WAIT))
        if seconds >= 5:
            self._log(f"  waiting {int(seconds)} s: {reason}")
        self.stats[bucket]["slept"] += seconds
        if seconds > 0:
            self._sleep_fn(seconds)

    def _pace(self, bucket: str) -> None:
        remaining = self._remaining.get(bucket)
        reset = self._reset.get(bucket)
        if remaining == 0 and reset is not None and reset > self._now():
            self._sleep(
                reset - self._now() + 2, f"{bucket} limit used up, waiting for reset", bucket
            )
            self._remaining.pop(bucket, None)
        last = self._last_request.get(bucket)
        interval = MIN_INTERVAL.get(bucket, 0.0)
        if last is not None and interval:
            self._sleep(last + interval - self._now(), "pacing", bucket)
        self._last_request[bucket] = self._now()

    def _note_rate(self, resp: httpx.Response, bucket: str) -> None:
        resource = resp.headers.get("x-ratelimit-resource") or bucket
        remaining = _int(resp.headers.get("x-ratelimit-remaining"))
        reset = _int(resp.headers.get("x-ratelimit-reset"))
        if remaining is not None:
            self._remaining[resource] = remaining
        if reset is not None:
            self._reset[resource] = reset

    def _rate_limit_wait(self, resp: httpx.Response, waits_so_far: int) -> tuple[float, str] | None:
        """How long to wait when a 403 or 429 is a rate limit, or None when it is not one."""
        retry_after = _int(resp.headers.get("retry-after"))
        if retry_after is not None:
            return retry_after + 5, "rate limited, Retry-After"
        remaining = _int(resp.headers.get("x-ratelimit-remaining"))
        reset = _int(resp.headers.get("x-ratelimit-reset"))
        if remaining == 0 and reset is not None:
            return max(
                0.0, reset - self._now()
            ) + 2, "primary rate limit used up, waiting for reset"
        message = _message(resp).lower()
        if resp.status_code == 429 or "rate limit" in message or "abuse" in message:
            wait = min(SECONDARY_BACKOFF_START * (2**waits_so_far), SECONDARY_BACKOFF_MAX)
            return wait, "secondary rate limit without Retry-After, backing off"
        return None

    def _send(self, url: str, headers: dict[str, str], bucket: str) -> httpx.Response:
        rate_waits = 0
        errors = 0
        while True:
            self._pace(bucket)
            try:
                resp = self._http.get(url, headers=headers)
            except httpx.TransportError as exc:
                errors += 1
                self.stats[bucket]["retries"] += 1
                if errors > len(ERROR_BACKOFF):
                    raise GitHubError(
                        0, url, f"network error after {errors} tries: {type(exc).__name__}"
                    ) from exc
                self._sleep(
                    ERROR_BACKOFF[errors - 1], f"network error ({type(exc).__name__})", bucket
                )
                continue
            self.stats[bucket]["requests"] += 1
            self._note_rate(resp, bucket)
            status = resp.status_code

            if status == 401:
                raise AuthError(
                    status, url, "the token was rejected (expired, revoked or mistyped)"
                )
            if status in (403, 429):
                wait = self._rate_limit_wait(resp, rate_waits)
                if wait is not None:
                    rate_waits += 1
                    self.stats[bucket]["retries"] += 1
                    if rate_waits > MAX_RATE_WAITS:
                        raise RateLimitGiveUp(
                            status, url, f"still rate limited after {MAX_RATE_WAITS} waits"
                        )
                    self._sleep(wait[0], wait[1], bucket)
                    continue
                return resp
            if status >= 500:
                errors += 1
                self.stats[bucket]["retries"] += 1
                if errors > len(ERROR_BACKOFF):
                    raise GitHubError(status, url, f"server error after {errors} tries")
                self._sleep(ERROR_BACKOFF[errors - 1], f"server error {status}", bucket)
                continue
            return resp
