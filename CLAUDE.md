# CLAUDE.md: fal radar, technical brief

fal radar finds public GitHub projects built on fal (fal.ai, the generative media platform), classifies them, shows which fal models builders use, lists the builders, and drafts a weekly "Built on fal this week" digest.
It is an unofficial community project, not affiliated with fal.
Site title "fal radar", subtitle "Built on fal, mapped weekly. An unofficial community project."

This file is the technical brief for anyone, human or agent, working in this repo.
When this file and the maintainer's words in a conversation disagree, the maintainer wins.

---

## 1. Non-negotiables

1. **Honesty of numbers.** Every figure on the site states what it counts and when it was counted, for example "public GitHub repositories we found referencing fal's clients, endpoints or model IDs, as of <date>". Never say "all projects built on fal". Private repos, closed-source products, Discord-only demos and X-only posts are invisible to this tool; the About page says so. Counts come from the data files, never from hand-typed constants.
2. **Not affiliated.** Every page says "Unofficial. Not affiliated with fal." in the header or footer. The word "fal" is used only descriptively. No fal logo, wordmark files, brand colours or screenshots of fal.ai. No `fal.ai` or `fal.*` domain name.
3. **Sanctioned data access only.** Data comes from the GitHub REST API (code search, repository search, repositories, users, issues search) with a personal access token, respecting primary and secondary rate limits. No scraping of GitHub HTML pages such as `network/dependents` (robots.txt disallows them). No scraping of X, Discord or Reddit. fal's model list comes from fal's documented catalogue API (`api.fal.ai/v1/models`) and its public explore page.
4. **People.** Store only what GitHub shows on public profiles and public repos: login, display name, avatar URL, profile URL, public repo list, public location string. No emails, no scraping of linked sites. Removal path: the issue template "Remove my project or profile" and `data/exclusions.json`, which the collector applies before writing any output.
5. **Secrets.** The GitHub token and any fal key live only in `.env` (git-ignored) locally and in GitHub Actions secrets. The pre-commit hook (`.githooks/pre-commit`, enabled with `git config core.hooksPath .githooks`) runs `scripts/precommit-secrets.sh`, which fails on `ghp_` and other `gh*_` tokens, `github_pat_` tokens, a fal key variable assigned a literal value, and the shape UUID, colon, hex. It does not flag `fal_client`. The collector reads the token from the environment only (`GH_SEARCH_TOKEN`), never from a command-line flag. Tokens are never printed or logged.
6. **Copy rules.** No em dashes in our own copy and code (site source, collector, README, digest templates); use commas, colons, periods, parentheses. Plain, short sentences. None of: "passionate", "leverage", "cutting-edge", "seamless", "game-changing", emoji in UI copy, exclamation marks. Maintainer's name in ASCII: Arda Noyan Karasoglu. No email on the site. Third-party data (repo names, descriptions, topics) is stored and rendered verbatim, whatever characters it contains.
7. **No AI-template look.** Banned: Inter, Space Grotesk, Manrope, Geist, purple or blue gradients, glassmorphism, glow, gradient text, emoji bullets, animated counters, tilt cards, rounded cards with drop shadows on every block.
8. **Safety rails.** Work on `main`. Previews go through the Vercel CLI; the GitHub repo is connected to Vercel only on the maintainer's production go. Posting the digest anywhere is done by the maintainer. Nothing is bought. The collector always runs with its cache directory, so re-runs do not burn the rate limit.
9. **Commit identity.** Repo-local only: `user.name "Arda Noyan Karasoglu"`, `user.email "235611274+ardanoyan@users.noreply.github.com"`. No agent co-author lines.

---

## 2. Pages

| Route | Page | Content |
|---|---|---|
| `/` | Radar | stats (repos found, builders, new this week, data date); "This week" (new notable repos, risers by star delta, model movers); the full list with search, filters (client: JS, Python, Swift, Kotlin, Dart, HTTP, integration; model family; language; active in the last 90 days; non-template; a toggle "Include README and topic mentions", off by default) and sort (stars, recently pushed, first seen) |
| `/models` | What builders use | model families ranked by repos and by matching files, with week-over-week change; each family links to its fal model page and to the repos using it |
| `/builders` | Builders | owners ranked by repos and stars; filter by type (user, org); new builders this week; optional filter on the public location string |
| `/digest/[week]` | Weekly digest | the digest for ISO week `YYYY-Www`; buttons "Copy for Discord", "Copy for X" (thread, each post under 280 characters), "Copy for LinkedIn" |
| `/about` | Method and caveats | how repos are found, what is missed, freshness, removal process, "not affiliated", who built it and why (the maintainer's own words), repo link |
| `/submit` | Submit a project | explains the issue template "Add a project" and links to it |

Header: text wordmark "fal radar" (Marcellus); nav: Radar, Models, Builders, Digest, About.
Footer: "Unofficial. Not affiliated with fal. Data: public GitHub, updated weekly. Built by Arda Noyan Karasoglu." plus the data date.

---

## 3. Data pipeline (Python, `collector/`)

### 3.1 Evidence tiers

Each repo has `evidence: "code" | "mention"`.
**Code**: a fal client or integration package in a manifest, an import, a `queue.fal.run` or `fal.run` call, or a fal endpoint ID in a source file.
**Mention**: README, description or topic hits only.
The headline, the default list and the digest use the code tier, **non-forks only**; forks and copies of fal's own templates never count toward the headline.
Repos owned by fal itself (owners `fal-ai` and `fal-ai-community`) carry `owner_is_fal` and stay out of the headline, the default list, the digest and the builders page; About shows them under a small "From fal" note, and the list has an "Include fal's own repos" toggle.
Templates built by other people count in the headline but never in the digest's notable set.
Mentions have their own count and the toggle. `data/repos.json` holds the code tier; `data/mentions.json` holds the mention tier (slim records straight from repository search, no extra lookups).

Integration packages count as code evidence with client label `integration`: `@ai-sdk/fal` (Vercel AI SDK), `@tanstack/ai-fal`, `livekit-plugins-fal`, LiteLLM's `fal_ai/` model route, fal's n8n node `@fal-ai/n8n-nodes-fal`. About lists them in one line.

### 3.2 Discovery queries

The REST code search endpoint uses GitHub's **legacy** code search syntax:
- punctuation such as `. / @ : =` is ignored, so a quoted phrase matches a sequence of words, and every hit is checked client-side against the literal string in its text-match fragments before it counts;
- exclusions use the `-qualifier:value` form (`-filename:package-lock.json`); `NOT` applies only to search terms;
- at most 256 characters (qualifiers excluded) and five AND, OR, NOT operators;
- default branch only, files under 384 KB, repositories active in the last year, archived repositories not searchable, forks only when they have more stars than their parent.

Code search (`GET /search/code`, 10 requests per minute, `Accept: application/vnd.github.text-match+json`), one query per id in `collector/queries.py`:
- JS and TS manifests: `@fal-ai/client`, `@fal-ai/serverless-client`, `@fal-ai/server-proxy`
- Python: `fal-client` in requirements.txt and pyproject.toml, `import fal_client`, `from fal_client`
- Swift, Kotlin, Dart: `FalClient` in Package.swift, `ai.fal.client` in Gradle files, `fal_client` in pubspec.yaml
- direct HTTP: `queue.fal.run`, `fal.run/fal-ai`
- integrations: the packages in 3.1
- model IDs: one query per queried family in `collector/models.yaml`, with lock files excluded. The family's file count is the `total_count`; the repo mapping is sampled, at most 3 pages (300 files), and logged as sampled. For notable repos, one scoped query `"fal-ai/" repo:owner/name`, at most 500 repos per run (by stars, then most recent push). A scoped query for a repo that no longer exists answers 422; such repos are recorded and not queried again.

Paging and slicing: `per_page=100` up to the 1,000-result cap. Above it, code search is sliced by file `size:` ranges, bisected until each slice holds 1,000 or fewer; repository search is sliced by `created:` day ranges (UTC). Each slice is its own search. Results are deduped on (repository, path), and the sum of slice totals is logged next to the unsliced total.

Recorded in every run log and in About's method section:
- GitHub's REST search finds up to 4,000 matching repositories per search and returns results from those; totals are lower bounds.
- Search responses carry no ETag, so conditional requests cannot save search quota; `data/etags.json` only covers repository and user lookups.

Repository search (`GET /search/repositories`, 30 per minute, mention tier): `fal.ai in:readme,description`, `"fal-ai" in:name,description`, `topic:fal-ai`, `topic:fal`, `topic:falai`. Forks are excluded by default; archived repositories are included. A mention repo with 20 or more stars (`stars:>=20` inside the query) is promoted to the code tier only by a scoped query or a manifest hit.

### 3.3 Model families (`collector/models.yaml`)

Generated by `scripts/build_models.py`, never edited by hand. Sources: fal's catalogue API (all active endpoints with name, category, status), a lookup by ID for legacy and featured IDs (kept only when the catalogue returns a record), the explore page for prominence, the sitemap as a cross-check. Every family carries the `fal.ai/models/...` URL of a real endpoint.
Families match by token prefix, longest pattern first. IDs outside the `fal-ai/` namespace (bytedance/, xai/, openai/ and others, about a quarter of fal's endpoints) count only inside repos that already show fal in code. IDs that match no family (including retired ones such as Imagen or Haiper) are logged as unknown IDs. A `/requests/...` suffix is trimmed from IDs; `/stream` and `/realtime` stay, because real endpoint IDs end that way.
Wording on the site: "models seen in code, at least".

### 3.4 Enrichment

`GET /repos/{owner}/{repo}` for every repo, `GET /users/{login}` for every owner, with ETags sent as `If-None-Match` (an authorised 304 does not count against the primary limit; pacing stays the same because secondary limits still count requests). A renamed or transferred repo is followed by redirect and keyed by its numeric id. A repo that answers 404, 403 or 451 is recorded as gone and skipped in later runs unless a search finds it again (then it is looked up once more). If a name now answers with a different repository id, the old record is kept and nothing is copied to the other repository.
Saved ETags are sent only for lookups that may come back as a 304 without a body: repos already in `repos.json` and owners we already hold. `data/etags.json` keeps only those URLs. Manifests and READMEs are fetched in full.

### 3.5 Classification (heuristics, no LLM)

- `clients`: js, python, swift, kotlin, dart, http, integration.
- `models`: families from endpoint IDs in text-match fragments.
- `kind`: template, fork (fork and under 25 stars), app, library, bot, plugin, research.
- `stack`: next, react, vue, svelte, remix, expo, flutter, fastapi, django, flask, gradio, streamlit, comfyui, n8n, from topics and from one manifest per repo (the preferred manifest among its code-search hits: package.json, then pyproject.toml, requirements.txt, pubspec.yaml; shallowest path first). The same manifest decides `bot` (discord.js, discord.py, telegraf, python-telegram-bot, slack bolt and similar; Python names compared after PEP 503 normalisation) and `library` (a package.json that publishes something, through exports, module, types or files, or a main with no start or dev script, that is not private and has no app framework; or a pyproject with a build system, no entry point and no web framework). The labels are stored, so a run that reads no manifest keeps them.
- `active`: pushed in the last 90 days.
- `notable`: not a fork, not a template, has a description, and 3 or more stars or pushed in the last 30 days with a README over 500 bytes. The README size is looked up only for repos where it decides the answer, and never for mention-only repos (they also get no owner lookup).
- Documentation, counted as a mention: Markdown, reStructuredText, AsciiDoc, `.mdc`, `.txt` (except `requirements*` and `constraints*`), any file named README, CHANGELOG or llms*, and AI assistant rule files (`.cursorrules`, `.cursor/`, `.windsurf/`, `.clinerules`, `.github/instructions/`, `.github/prompts/`).
- Scoped searches also record which clients a repo shows; a manifest literal counts only inside its own file name (Dart's `fal_client` only in pubspec.yaml).

### 3.6 Snapshots, deltas, "this week"

- `data/repos.json`: current state per repo, with `first_seen` and `stars_history`.
- `data/snapshots/YYYY-MM-DD.json`: aggregates per run.
- New this week: `first_seen` in the last 7 days and `created_at` in the last 60 days. Risers: top 10 by star delta, at least 5 gained. Model movers: largest change in repo count.
- First digest (one snapshot only): New is code-tier repos created in the last 14 days; Rising and Model movers are replaced by "Most starred, created in the last 90 days" (top 5) and a static "Models this week" taken from the catalogue's creation dates, with the date range stated. Normal rules from the second week.

### 3.7 Exclusions, submissions, failure

- `data/exclusions.json` (repos, owners) is applied before any output.
- `data/submissions.json` holds repos added from the "Add a project" issue template after review.
- Every response is cached under `collector/.cache/` (gzipped, git-ignored, keyed by URL). `--offline` builds from cache; `--resume` continues the last unfinished run, replaying what it already fetched.
- Rate limits: Retry-After plus 5 seconds; a spent primary limit waits for `x-ratelimit-reset`; a secondary limit without headers backs off from one minute, doubling. Requests are serial.
- `data/` is written only when a run finishes, atomically. A failed run exits non-zero and leaves `data/` as it was. A run refuses to start with less than 1 GB free.
- A limited run (`--only`, `--limit-repos`, any `--skip-*`) writes `runs/<date>.limited.json`, no snapshot, keeps last week's mentions when it ran no repository search, and never counts for `--resume`, which continues the newest unfinished full run.
- A scoped search that fails for one repo is logged and skipped; a query that reaches its request budget stops and lists the slices it did not read.

### 3.8 Schema (`data/repos.json`)

Defined by `collector/schema.py` (pydantic) and mirrored by the site's zod schema. Owner fields are limited to login, type, name, location, avatar_url, html_url.

### 3.9 Digest (`collector/digest.py`)

Writes `data/digests/YYYY-Www.md` (sections: New this week, Rising, Models this week, A builder to know, By the numbers) and `data/digests/YYYY-Www.posts.json` with `discord` (under 2,000 characters), `x` (posts under 280 characters each, the first standalone) and `linkedin` (under 1,300 characters). Every variant ends with the radar link and "unofficial community project". The tool never posts anywhere.

### 3.10 Optional cover image

`collector/cover.py` makes one 1200x630 image per digest with `fal-ai/flux/schnell` only when the fal key is set and `--cover` is passed; otherwise a deterministic SVG of the week's model counts. Never blocks the pipeline.

---

## 4. Site (Next.js, `site/`)

- Static export; reads `data/*.json` at build time; no runtime API calls, no database. `scripts/check-data.ts` validates the data with zod before `next build`.
- Home ships the stats, "This week" and the first 50 rows in the HTML; the full list is a static JSON chunk under `public/data/` fetched after first paint.
- Avatars use GitHub's sized URLs (`avatar_url` plus `&s=80`) in a plain `img` with width, height and lazy loading.
- Radar: stats in mono with Marcellus for the big figure, each with a one-line definition in a `<details>`; list renders 50 rows then "Show more" in steps of 50; forks and templates hidden by default behind a visible toggle; everything works at 360px without horizontal scroll.
- Models: inline SVG bars, file counts in mono, delta since the previous snapshot, rows expand to the repos.
- Builders: avatar, name or login, type, notable repos, total stars, latest push; links open the GitHub profile in a new tab.
- Digest: Markdown rendered server-side with remark; copy buttons use `navigator.clipboard.writeText` in the click handler, falling back to a selected textarea and "Selected, press Ctrl+C or Cmd+C".
- About: what this is, method (tiers, queries, caps, slicing, sampling, the 4,000-repository scope cap, no search ETags, coverage table from the last run log), what is missing (code search blind spots; "fal" is also the Turkish word for fortune-telling, which is why mentions are not counted), cross-references (GitHub's dependents page and npm downloads, each with its date), freshness, removal and submissions, not affiliated, built by, colophon.
- SEO: Metadata API, `sitemap.ts`, `robots.ts`, OG images with `next/og`.

---

## 5. Design

Tokens: plaster `--paper #ecece9`, `--ink #151617`, `--ink-2 #55585c`, `--rule #c9c9c4`, sanguine `--chalk #a8432b`; night `--night #0b0c0d`, `--stone #e8e6e1`, `--dust #a19f9a`, `--chalk-night #d46e54`. Display Marcellus, text Instrument Sans, mono IBM Plex Mono, self-hosted with `next/font/google`. Easing `cubic-bezier(0.16, 1, 0.3, 1)`.

Light plaster wall on every page; "This week" and the digest cover sit on a night plate (2px radius, 1px inset hairline). Hairline rules between rows, mono for numbers and dates, sanguine only for deltas, active filters and the single accent bar in charts. No shadows. Radius 2px on plates, 999px on chips, 0 elsewhere. Focus ring `2px solid var(--chalk)` offset 3px. No motion beyond hover underline and plate image scale 1.025; `prefers-reduced-motion` respected. No dark mode in v1.

Charts: inline SVG, 1px ink outline, sanguine fill for the top family, `--ink-2` for the rest, mono labels, a text table alternative in a `<details>`.

---

## 6. Automation

- `.github/workflows/weekly.yml`: Mondays 06:00 UTC and `workflow_dispatch`; checkout, `astral-sh/setup-uv`, `uv sync`, `uv run collector run` with `GH_SEARCH_TOKEN` from the secret as an environment variable (fail fast when missing; never fall back to `GITHUB_TOKEN`, which has a lower limit and cannot run code search), `uv run collector digest`, optional cover, then commit `data/` and covers as "radar: week YYYY-Www" and push. Concurrency group `weekly`, `cancel-in-progress: false`, timeout 300 minutes as a guard.
- `.github/workflows/ci.yml`: ruff, pytest (fake transports, no network), `tsc`, eslint, `check-data`, `next build`.
- The first full run is local and resumable; `data/etags.json` is committed afterwards so weekly runs are mostly 304s on enrichment.

---

## 7. Repository layout

```
fal-radar/
  README.md, LICENSE (MIT), CLAUDE.md (this file)
  .githooks/                pre-commit (secret check)
  .github/workflows/        weekly.yml, ci.yml
  .github/ISSUE_TEMPLATE/   add-a-project.yml, remove-my-project.yml
  collector/                cli, github client, cache, search, enrich, classify, models, pipeline, schema, store, runlog, digest, cover, models.yaml, fal_endpoints.tsv, tests/
  data/                     repos.json, etags.json, snapshots/, digests/, runs/, exclusions.json, submissions.json
  site/                     Next.js app
  scripts/                  build_models.py, precommit-secrets.sh, check-data.ts
  notes/                    git-ignored, private
```

---

## 8. Stack

- Collector: Python 3.12, uv, httpx 0.28.1, pydantic 2.13.5, pyyaml 6.0.3, typer 0.27.2, pytest 9.1.1, ruff 0.16.10. GitHub REST API version header `2022-11-28` (supported until March 2028). Deterministic output ordering.
- Site: Next.js 16.3.8, React 19.3.0, TypeScript 5.x, ESLint 9.x, static export, CSS Modules plus `tokens.css` (no Tailwind), zod 4.6.5, remark 15 with remark-html 16, Vitest 5, Playwright 1.63 with the installed Chrome, `@axe-core/playwright` 4.13. Node 22.
- Hosting: Vercel hobby, Web Analytics only, no cookie banner.

Commands:

```bash
uv sync
uv run pytest
uv run collector check-token
uv run collector run --only js-client --skip-owners
uv run collector run --resume
uv run collector report
uv run python scripts/build_models.py
```

---

## 9. Acceptance criteria

- Mobile Lighthouse on `/`, `/models` and one digest: Performance 90 or more, Accessibility 100, Best Practices 95 or more, SEO 100. Home shell JS under 150 KB gzipped (the list chunk is excluded). No horizontal scroll at 360px. No console errors.
- Data: the first full run finds at least 1,500 unique non-fork code-tier repositories (if far fewer, report why before continuing); at least 25 model families with non-zero counts; the run log of every query (query, total_count, pages, slices, sampled or not) is committed under `data/runs/`.
- No em dash in `site/app`, `site/components`, `site/content`, `collector/`, `README.md` or the digest templates (never checked over `data/`, which stays verbatim); none of the banned words in our own rendered copy.
- Every number on the site comes from `data/`; the data date is visible on every page; "Unofficial. Not affiliated with fal." on every page; no fal brand assets.
- Adding a repo to `exclusions.json` removes it from the next build.
- `weekly.yml` has run green once via `workflow_dispatch` and committed data.
- A digest for the current ISO week exists with all three post variants under their length limits.
- Phone screenshots (390x844) of `/`, `/models`, `/builders`, one digest and `/about`, plus desktop 1440x900 of `/`. Lighthouse and axe results. Keyboard path through filters, sort and copy buttons works.
- README has screenshots, the method, the caveats and the one-command local run.
- The pre-commit secret check is installed and tested with a fake key.

---

## 10. Tools

- GitHub: push over SSH; repo settings and Actions secrets in the web UI. The read-only fine-grained token is for the API only.
- Vercel CLI for previews; production only on the maintainer's go.
- Screenshots: the Claude Code browser pane or Playwright with the installed Chrome. Lighthouse via `npx lighthouse <url> --preset=mobile`.
- Library docs: read the npm and PyPI registries and official docs directly; record versions in section 8.
- Machine limits: little free disk and high swap on the maintainer's Mac. One `node_modules`, gzipped API cache, the long collector job runs alone.
