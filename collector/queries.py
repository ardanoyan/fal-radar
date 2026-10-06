"""The discovery queries. Each one names the evidence tier it proves and the client it implies.

GitHub's legacy code search ignores punctuation, so a quoted phrase like
"@fal-ai/client" matches the words "fal ai client" in sequence. Every code query
therefore carries `needles`: a hit only counts when one of its text-match fragments
contains one of these literal strings (case-insensitive).
"""

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
    client: str | None = None  # js, python, swift, kotlin, dart, http, integration
    needles: tuple[str, ...] = ()  # literal strings a fragment must contain to count
    max_pages: int | None = None  # None: fetch everything, slicing past the 1,000 cap


def _code(qid: str, q: str, source: str, client: str, needles: tuple[str, ...]) -> Query:
    return Query(qid, "code", q, "code", source, client, needles)


CODE_QUERIES: list[Query] = [
    # JavaScript and TypeScript manifests.
    _code(
        "js-client",
        '"@fal-ai/client" filename:package.json',
        "code:@fal-ai/client",
        "js",
        ('"@fal-ai/client"',),
    ),
    _code(
        "js-serverless-client",
        '"@fal-ai/serverless-client" filename:package.json',
        "code:@fal-ai/serverless-client",
        "js",
        ('"@fal-ai/serverless-client"',),
    ),
    _code(
        "js-server-proxy",
        '"@fal-ai/server-proxy" filename:package.json',
        "code:@fal-ai/server-proxy",
        "js",
        ('"@fal-ai/server-proxy"',),
    ),
    # Python. pip treats fal-client and fal_client as the same name.
    _code(
        "py-requirements",
        '"fal-client" filename:requirements.txt',
        "code:fal-client:requirements.txt",
        "python",
        ("fal-client", "fal_client"),
    ),
    _code(
        "py-pyproject",
        '"fal-client" filename:pyproject.toml',
        "code:fal-client:pyproject.toml",
        "python",
        ("fal-client", "fal_client"),
    ),
    _code(
        "py-import",
        '"import fal_client" language:python',
        "code:import fal_client",
        "python",
        ("import fal_client",),
    ),
    _code(
        "py-from-import",
        '"from fal_client" language:python',
        "code:from fal_client",
        "python",
        ("from fal_client",),
    ),
    # Swift, Kotlin, Dart.
    _code(
        "swift-package",
        '"FalClient" filename:Package.swift',
        "code:FalClient:Package.swift",
        "swift",
        ("falclient",),
    ),
    _code(
        "kotlin-gradle-kts",
        '"ai.fal.client" filename:build.gradle.kts',
        "code:ai.fal.client:build.gradle.kts",
        "kotlin",
        ("ai.fal.client",),
    ),
    _code(
        "kotlin-gradle",
        '"ai.fal.client" filename:build.gradle',
        "code:ai.fal.client:build.gradle",
        "kotlin",
        ("ai.fal.client",),
    ),
    _code(
        "dart-pubspec",
        '"fal_client" filename:pubspec.yaml',
        "code:fal_client:pubspec.yaml",
        "dart",
        ("fal_client",),
    ),
    # Direct HTTP calls.
    _code("http-queue", '"queue.fal.run"', "code:queue.fal.run", "http", ("queue.fal.run",)),
    _code("http-run", '"fal.run/fal-ai"', "code:fal.run/fal-ai", "http", ("fal.run/fal-ai",)),
    # Integration packages that call fal without a fal client dependency.
    _code(
        "int-ai-sdk",
        '"@ai-sdk/fal" filename:package.json',
        "code:@ai-sdk/fal",
        "integration",
        ('"@ai-sdk/fal"',),
    ),
    _code(
        "int-tanstack",
        '"@tanstack/ai-fal" filename:package.json',
        "code:@tanstack/ai-fal",
        "integration",
        ('"@tanstack/ai-fal"',),
    ),
    _code(
        "int-livekit-requirements",
        '"livekit-plugins-fal" filename:requirements.txt',
        "code:livekit-plugins-fal:requirements.txt",
        "integration",
        ("livekit-plugins-fal", "livekit_plugins_fal"),
    ),
    _code(
        "int-livekit-pyproject",
        '"livekit-plugins-fal" filename:pyproject.toml',
        "code:livekit-plugins-fal:pyproject.toml",
        "integration",
        ("livekit-plugins-fal", "livekit_plugins_fal"),
    ),
    _code(
        "int-litellm", '"fal_ai/fal-ai"', "code:litellm fal_ai/", "integration", ("fal_ai/fal-ai/",)
    ),
    _code(
        "int-n8n",
        '"@fal-ai/n8n-nodes-fal" filename:package.json',
        "code:@fal-ai/n8n-nodes-fal",
        "integration",
        ('"@fal-ai/n8n-nodes-fal"',),
    ),
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

# Mention repos with 20 or more stars: candidates for promotion to the code tier, which
# happens only through a scoped code search or a manifest hit.
PROMOTION_MIN_STARS = 20
PROMOTION_QUERIES: list[Query] = [
    Query(f"promote-{q.id}", "repo", f"{q.q} stars:>={PROMOTION_MIN_STARS}", "mention", q.source)
    for q in REPO_QUERIES
]

MODEL_QUERY_PAGES = 3


def model_queries() -> list[Query]:
    """One sampled code search per queried model family in models.yaml."""
    from .models import default_index

    out = []
    for fam in default_index().families:
        if not (fam.discover and fam.query):
            continue
        phrase = fam.query.strip('"')
        out.append(
            Query(
                f"model-{fam.id}",
                "code",
                f"{fam.query} {LOCKFILE_EXCLUSIONS}",
                "code",
                f"code:model:{fam.id}",
                None,
                (phrase,),
                MODEL_QUERY_PAGES,
            )
        )
    return out


MODEL_QUERIES: list[Query] = model_queries()

ALL_QUERIES: dict[str, Query] = {
    q.id: q for q in [*CODE_QUERIES, *MODEL_QUERIES, *REPO_QUERIES, *PROMOTION_QUERIES]
}

# Files whose text is documentation, not code. A hit in one of these is a mention.
DOC_EXTENSIONS = (".md", ".mdx", ".markdown", ".rst", ".adoc")


def is_doc_path(path: str) -> bool:
    return path.lower().endswith(DOC_EXTENSIONS)


def verify(query: Query, fragments: list[str]) -> bool:
    """True when a fragment contains one of the query's literal strings."""
    if not query.needles:
        return True
    lowered = [f.lower() for f in fragments]
    return any(n.lower() in f for n in query.needles for f in lowered)


# Facts about the search API that bound every count. Logged with each run.
API_FACTS = [
    "GitHub's REST search finds up to 4,000 matching repositories per search and returns "
    "results from those, so totals are lower bounds.",
    "Search responses carry no ETag, so conditional requests cannot save search quota; "
    "data/etags.json covers repository and user lookups only.",
    "The REST code search uses the legacy index: default branch only, files under 384 KB, "
    "repositories active in the last year, no archived repositories, forks only with more "
    "stars than their parent.",
    "Punctuation is ignored in code search phrases, so each hit is checked against the "
    "literal string in its text-match fragments.",
]
