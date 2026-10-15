#!/usr/bin/env bash
# competitor-pulse/precheck.sh — all network reads happen here (anonymous
# except the Tavily news search); the engine only interprets stdout.
# Empty stdout = nothing new and no errors -> engine skipped.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
# Read-only public-metadata GETs under the ytubecoder account (avoids the 60/h
# anonymous GitHub limit); the token is never printed.
GH_TOKEN="$(gh auth token -u ytubecoder 2>/dev/null || true)"
if [[ -n "$GH_TOKEN" ]]; then export GITHUB_TOKEN="$GH_TOKEN"; fi
exec python3 "$LOOPS_ROOT/loops.d/competitor-pulse/collect.py"
