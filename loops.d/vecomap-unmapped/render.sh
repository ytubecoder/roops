#!/usr/bin/env bash
# vecomap-unmapped render.sh — deterministic snapshot page from this run's
# summary.json + trace overlays. No network, no model, no randomness.
# Overlay downscale uses the tracer venv (cv2), as required.
set -euo pipefail
PY="${TRACER_PY:-$HOME/.cache/vecomap-tracer/venv/bin/python}"
exec "$PY" "$LOOPS_ROOT/loops.d/vecomap-unmapped/render_page.py"
