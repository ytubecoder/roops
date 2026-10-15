#!/usr/bin/env bash
# forums-lane/precheck.sh — deterministic collect (script->agent).
# Unsandboxed, so this is where network I/O lives. The engine has
# perm_network=none. This script never posts, votes, messages, submits,
# or drives a browser. The collector reads Reddit only through RedditGrow
# or Reddit OAuth (scope=read), inside console.forums.
#
# Harness skip: exit 0 and empty stdout. The collector already prints
# nothing when there are zero candidates and not every adapter failed.
# FORUMS_LANE_ENABLED=0 is the kill switch and uses that same skip.
set -euo pipefail

if [ "${FORUMS_LANE_ENABLED:-}" = "0" ]; then
  exit 0
fi

: "${OUT_DIR:?OUT_DIR required}"
: "${MAGUYVA_REPO:?MAGUYVA_REPO required}"

# The collector appends /forums-lane/rules-cache to LOOP_DATA_DIR.
# The runner does not export LOOP_DATA_DIR to the precheck. Point it at
# the loop-data parent so the 7-day cache lands at
# $LOOPS_ROOT/state/loop-data/forums-lane/rules-cache, beside the batch
# the runner later commits into that same forums-lane directory.
if [ -z "${LOOP_DATA_DIR:-}" ]; then
  LOOP_DATA_DIR="${LOOPS_ROOT:-$HOME/projects/loops}/state/loop-data"
  export LOOP_DATA_DIR
fi
mkdir -p "$LOOP_DATA_DIR"

# The runner strips .env keys, including MAGUYVA_REPO, from the engine.
# A file is not stdout, so an empty collect still skips.
mkdir -p "$OUT_DIR/inputs"
printf '%s\n' "$MAGUYVA_REPO" > "$OUT_DIR/inputs/checkout.txt"

# Separate commands, not `cd && python`. Under set -e a failed cd in an
# && list is not the final command, so the shell would exit 0 and the
# harness would treat a missing checkout as a skip.
cd "$MAGUYVA_REPO/growth-console"
PYTHONPATH=. "$HOME/.venvs/forums/bin/python" -m console.forums collect --forum all --out "$OUT_DIR/inputs"
