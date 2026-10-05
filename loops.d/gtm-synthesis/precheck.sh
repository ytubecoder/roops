#!/usr/bin/env bash
# gtm-synthesis/precheck.sh — deterministic gathering step (script->agent pattern,
# docs/INTERFACES.md §4.1/§6.2): this script does cheap, deterministic data
# gathering; its stdout is injected into the engine's prompt as ground truth.
# The reviewed probe writes bounded private snapshots. The model only reports.
set -euo pipefail

INPUTS="${OUT_DIR:?OUT_DIR required}/inputs"
mkdir -p "$INPUTS"
if ! "$LOOPS_ROOT/bin/probe" gtm-refresh --out "$INPUTS/result.json"; then
  echo "gtm-refresh probe failed" >&2
  exit 1
fi
python3 - "$INPUTS/result.json" <<'PY'
import json, sys
d=json.load(open(sys.argv[1]))
if d.get("status") != "ok" or d.get("synthesis") not in {"updated", "unchanged"}:
    print(json.dumps(d, sort_keys=True))
    raise SystemExit(1)
PY
"$LOOPS_ROOT/bin/probe" gtm-learning-read --out "$INPUTS/digest.json"
python3 - "$INPUTS/digest.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
# Only GC's accepted last-good output suppresses another interpretation. A
# malformed/rejected model result cannot checkpoint its own hash.
previous = d.get("previous_assessment") or {}
if previous.get("input_hash") == d.get("input_hash"):
    sys.exit(0)
print(json.dumps(d, ensure_ascii=False))
PY
