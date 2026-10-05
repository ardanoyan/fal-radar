"""The discovery queries. Each one names the evidence tier it proves and the client it implies."""

from __future__ import annotations

from dataclasses import dataclass

LOCKFILE_EXCLUSIONS = "-filename:package-lock.json -filename:yarn.lock -filename:pnpm-lock.yaml"


@dataclass(frozen=True)
class Query:
    id: str
    kind: str  # "code" (GET /search/code) or "repo" (GET /search/repositories)
    q: str
    tier: str  # "code" or "mention"
    source: str  # label stored in a repo's "sources"
    client: str | None = None  # js, python, swift, kotlin, dart, http
    max_pages: int | None = None  # None: fetch everything, slicing past the 1,000 cap


CODE_QUERIES: list[Query] = [
    Query(
        "js-client",
        "code",
        '"@fal-ai/client" filename:package.json',
        "code",
        "code:@fal-ai/client",
        "js",
    ),
    Query(
        "js-serverless-client",
        "code",
        '"@fal-ai/serverless-client" filename:package.json',
        "code",
        "code:@fal-ai/serverless-client",
        "js",
    ),
    Query(
        "js-server-proxy",
        "code",
        '"@fal-ai/server-proxy" filename:package.json',
        "code",
        "code:@fal-ai/server-proxy",
        "js",
    ),
    Query(
        "py-requirements",
        "code",
        '"fal-client" filename:requirements.txt',
        "code",
        "code:fal-client:requirements.txt",
        "python",
    ),
    Query(
        "py-pyproject",
        "code",
        '"fal-client" filename:pyproject.toml',
        "code",
        "code:fal-client:pyproject.toml",
        "python",
    ),
    Query(
        "py-import",
        "code",
        '"import fal_client" language:python',
        "code",
        "code:import fal_client",
        "python",
    ),
    Query(
        "py-from-import",
        "code",
        '"from fal_client" language:python',
        "code",
        "code:from fal_client",
        "python",
    ),
    Query(
        "swift-package",
        "code",
        '"FalClient" filename:Package.swift',
        "code",
        "code:FalClient:Package.swift",
        "swift",
    ),
    Query(
        "kotlin-gradle-kts",
        "code",
        '"ai.fal.client" filename:build.gradle.kts',
        "code",
        "code:ai.fal.client:build.gradle.kts",
        "kotlin",
    ),
    Query(
        "kotlin-gradle",
        "code",
        '"ai.fal.client" filename:build.gradle',
        "code",
        "code:ai.fal.client:build.gradle",
        "kotlin",
    ),
    Query(
        "dart-pubspec",
        "code",
        '"fal_client" filename:pubspec.yaml',
        "code",
        "code:fal_client:pubspec.yaml",
        "dart",
    ),
    Query("http-queue", "code", '"queue.fal.run"', "code", "code:queue.fal.run", "http"),
    Query("http-run", "code", '"fal.run/fal-ai"', "code", "code:fal.run/fal-ai", "http"),
]

REPO_QUERIES: list[Query] = [
    Query(
        "mention-readme",
        "repo",
        "fal.ai in:readme,description",
        "mention",
        "repo:fal.ai in readme or description",
    ),
    Query(
        "mention-name",
        "repo",
        '"fal-ai" in:name,description',
        "mention",
        "repo:fal-ai in name or description",
    ),
    Query("topic-fal-ai", "repo", "topic:fal-ai", "mention", "repo:topic:fal-ai"),
    Query("topic-fal", "repo", "topic:fal", "mention", "repo:topic:fal"),
    Query("topic-falai", "repo", "topic:falai", "mention", "repo:topic:falai"),
]

ALL_QUERIES: dict[str, Query] = {q.id: q for q in [*CODE_QUERIES, *REPO_QUERIES]}
