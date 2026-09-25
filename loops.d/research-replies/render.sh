#!/usr/bin/env bash
# research-replies render.sh — deterministic snapshot page (docs/REPORT_PAGES.md)
# from this run's probe capture: every reply to date,
# call bookings, send progress. No model, no network.
set -euo pipefail
exec python3 "$LOOPS_ROOT/loops.d/research-replies/render_page.py" "$OUT_DIR/inputs/replies.json" \
  --loop "$LOOP_NAME" --run-id "$RUN_ID" -o "$PAGE_OUT"
