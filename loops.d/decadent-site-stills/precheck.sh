#!/usr/bin/env bash
# decadent-site-stills/precheck.sh. type=watchdog: THIS SCRIPT IS THE JOB (docs/INTERFACES.md §4.1).
#
# 1. git fetch the dedicated clone ~/projects/decadent (svc's own clone; nobody edits in it).
# 2. origin/main already rendered cleanly (state/decadent-site-stills/last-ok)? Print so and exit 0.
# 3. Otherwise check main out clean, refresh npm deps and chromium when the lockfile moved, and run
#    decadent's scripts/stills-refresh.sh: it renders, and pushes site/stills-refresh when a still differs.
#    The site-stills workflow on GitHub then opens or refreshes the PR.
# Exit 0 = stills current (or pushed). Exit 1 = could not make them. Exit 2 = pushed, but a caption no
# longer matches the screen. Non-zero escalates to the diagnosis engine. A failed main is not recorded, so
# the next firing tries again and the alert stays until it renders.
set -uo pipefail

LOOP_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="${LOOPS_ROOT:-$(cd "$LOOP_DIR/../.." && pwd)}"
STATE="$ROOT/state/decadent-site-stills"
REPO="$HOME/projects/decadent"
LIBS="$HOME/.local/opt/chromium-libs/root"
mkdir -p "$STATE"

export LD_LIBRARY_PATH="$LIBS/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PATH="$LIBS/usr/bin:$HOME/.local/bin:$PATH"
export STILLS_PY="$HOME/.cache/decadent-stills/venv/bin/python"

cd "$REPO" 2>/dev/null || { echo "stills: no clone at $REPO"; exit 1; }
if ! out=$(git fetch -q --prune origin 2>&1); then
  echo "stills: git fetch failed"
  echo "$out" | tail -n 5
  exit 1
fi

sha=$(git rev-parse origin/main)
short=${sha:0:12}
if [ "$(cat "$STATE/last-ok" 2>/dev/null)" = "$sha" ]; then
  echo "stills: main unchanged at $short; nothing to re-make"
  exit 0
fi

git checkout -q --detach "$sha" && git reset -q --hard "$sha" && git clean -fdq -- public-site || {
  echo "stills: could not check out main at $short"; exit 1; }

lock=$(git hash-object public-site/package-lock.json)
if [ "$(cat "$STATE/lock" 2>/dev/null)" != "$lock" ]; then
  if ! out=$(cd public-site && npm ci --no-audit --no-fund -q 2>&1 && npx playwright install chromium 2>&1); then
    echo "stills: npm ci or playwright install failed at $short"
    echo "$out" | tail -n 20
    exit 1
  fi
  echo "$lock" > "$STATE/lock"
fi

bash scripts/stills-refresh.sh
code=$?
if [ "$code" = 0 ]; then echo "$sha" > "$STATE/last-ok"; fi
exit "$code"
