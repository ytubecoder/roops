#!/usr/bin/env bash
# tailnet-zones render.sh — deterministic snapshot page from this run's model.
set -euo pipefail
exec python3 "$LOOPS_ROOT/loops.d/tailnet-zones/render_zones.py" \
  "$OUT_DIR/zones-model.json" \
  --loop "$LOOP_NAME" --run-id "$RUN_ID" -o "$PAGE_OUT"
