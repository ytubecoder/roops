#!/usr/bin/env bash
# kagi-ban render.sh — deterministic snapshot page from this run's scan.json.
set -euo pipefail
exec python3 "$LOOPS_ROOT/loops.d/kagi-ban/render_page.py" "$OUT_DIR/scan.json" \
  --loop "$LOOP_NAME" --run-id "$RUN_ID" -o "$PAGE_OUT"
