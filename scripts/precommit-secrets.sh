#!/usr/bin/env bash
# Fails when the staged diff adds something that looks like a secret.
# Checks added lines only. Patterns: GitHub tokens (ghp_ and the other gh*_ kinds,
# github_pat_), a FAL_KEY assignment with a value, and the fal key shape (UUID, colon,
# hex). It does not flag fal_client, an empty assignment, or a key read from the
# environment or a secret. Matched values are never printed. Prose that puts a word
# right after the equals sign of that variable name also trips it; reword such prose.
# Usage: scripts/precommit-secrets.sh            (checks the staged diff)
#        scripts/precommit-secrets.sh FILE...     (checks whole files, for tests)
set -euo pipefail

patterns=(
  'ghp_[A-Za-z0-9]{36}'
  'gh[ousr]_[A-Za-z0-9]{36}'
  'github_pat_[A-Za-z0-9_]{22,}'
  'FAL_KEY[[:space:]]*[:=][[:space:]]*["'"'"']?[A-Za-z0-9]'
  '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}:[0-9a-fA-F]{16,}'
)
regex=$(IFS='|'; echo "${patterns[*]}")
allowed='FAL_KEY[[:space:]]*[:=][[:space:]]*["'"'"']?(\$|os\.environ|process\.env)'

found=0
check() {  # $1 = label, stdin = text
  local hits
  hits=$(grep -En -- "$regex" | grep -Ev -- "$allowed" | cut -d: -f1 || true)
  if [ -n "$hits" ]; then
    found=1
    echo "  $1: added line(s) $(echo $hits | tr ' ' ',') of the change" >&2
  fi
}

if [ "$#" -gt 0 ]; then
  for f in "$@"; do check "$f" < "$f"; done
else
  while IFS= read -r f; do
    case "$f" in
      .env.example|*/.env.example) ;;
      .env|.env.*|*/.env|*/.env.*)
        found=1; echo "  $f: a .env file is staged. Unstage it: git restore --staged $f" >&2; continue ;;
    esac
    added=$(git diff --cached --unified=0 --no-color -- "$f" | { grep -E '^\+' || true; } | { grep -Ev '^\+\+\+ ' || true; })
    if [ -n "$added" ]; then check "$f" <<< "$added"; fi
  done < <(git diff --cached --name-only --diff-filter=ACMR)
fi

if [ "$found" -ne 0 ]; then
  echo "secret check failed: the lines above look like secrets (values hidden)." >&2
  echo "Remove them, or keep the value in .env (git-ignored)." >&2
  exit 1
fi
