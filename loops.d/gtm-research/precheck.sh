#!/usr/bin/env bash
set -euo pipefail
inputs="${OUT_DIR:?}/inputs"
mkdir -p "$inputs"
python3 "$LOOPS_ROOT/loops.d/gtm-research/collect.py" > "$inputs/research.json"
cat "$inputs/research.json"
