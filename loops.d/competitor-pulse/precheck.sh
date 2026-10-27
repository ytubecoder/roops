#!/usr/bin/env bash
# competitor-pulse/precheck.sh — all network reads happen here (anonymous
# except the Tavily news search); the engine only interprets stdout.
# Empty stdout = nothing new and no errors -> engine skipped.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
# Read-only public-metadata GETs under the ytubecoder account (avoids the 60/h
# anonymous GitHub limit); the token is never printed.
# On a host without that gh login (firstparty) the same read-only token comes
# from the harness .env as COMPETITOR_GH_TOKEN, which run-loop.sh exports into
# this precheck only (the engine never sees .env). TAVILY_API_KEY arrives the
# same way; collect.py prefers it over the llm codex config.
GH_TOKEN="$(gh auth token -u ytubecoder 2>/dev/null || true)"
[[ -n "$GH_TOKEN" ]] || GH_TOKEN="${COMPETITOR_GH_TOKEN:-}"
if [[ -n "$GH_TOKEN" ]]; then export GITHUB_TOKEN="$GH_TOKEN"; fi
# Reddit mentions come from RedditGrow's /reddit/search. Only that one key is
# lifted from the repo .env (not the whole file); a missing key is reported by
# collect.py as "redditgrow: no key" per competitor, never a crash.
REPO="${MAGUYVA_REPO:-$HOME/projects/maguyva-marketing}"
if [[ -f "$REPO/.env" ]]; then
  RG_KEY="$(grep -E '^REDDITGROW_API_KEY=' "$REPO/.env" | head -n1 | cut -d= -f2- | tr -d '"'"'"'"' | tr -d '[:space:]')"
  if [[ -n "$RG_KEY" ]]; then export REDDITGROW_API_KEY="$RG_KEY"; fi
fi
exec python3 "$LOOPS_ROOT/loops.d/competitor-pulse/collect.py"
