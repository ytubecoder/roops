#!/usr/bin/env bash
# competitor-rank/precheck.sh — local reads only, no network. assemble.py
# prints the ranking dossier and writes $OUT_DIR/rank-input.json; the engine
# only scores the printed block. A registry that cannot be read exits 1.
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
LOOPS_ROOT="${LOOPS_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
export LOOPS_ROOT
exec python3 "$LOOPS_ROOT/loops.d/competitor-rank/assemble.py"
