#!/usr/bin/env bash
# kagami render.sh — deterministic snapshot page from this run's matrix.json.
set -euo pipefail
exec python3 "$LOOPS_ROOT/loops.d/kagami/render_page.py" "$OUT_DIR/matrix.json" \
  --loop "$LOOP_NAME" --run-id "$RUN_ID" -o "$PAGE_OUT"
