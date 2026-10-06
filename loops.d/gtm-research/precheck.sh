#!/usr/bin/env bash
set -euo pipefail
inputs="${OUT_DIR:?}/inputs"
mkdir -p "$inputs"
# Valid source failures travel as JSON. Transport failures become coverage gaps;
# never expose raw SSH/provider stderr or reuse an older probe output after failure.
set -- --public-search "$inputs/public-search.json"
if "$LOOPS_ROOT/bin/probe" gtm-public-search --out "$inputs/public-search.json" 2>/dev/null; then
  :
else
  code=$?
  if [[ "$code" = 124 ]]; then
    set -- "$@" --probe-error timeout
  else
    set -- "$@" --probe-error transport
  fi
fi
status=0
python3 "$LOOPS_ROOT/loops.d/gtm-research/collect.py" \
  "$@" \
  > "$inputs/research.json" || status=$?
cat "$inputs/research.json"
exit "$status"
