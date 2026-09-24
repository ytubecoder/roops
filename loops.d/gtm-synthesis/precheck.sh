#!/usr/bin/env bash
# gtm-synthesis/precheck.sh — deterministic gathering step (script->agent pattern,
# docs/INTERFACES.md §4.1/§6.2): this script does cheap, deterministic data
# gathering; its stdout is injected into the engine's prompt as ground truth.
# It must be idempotent and side-effect-free beyond read-only inspection. For
# type=watchdog loops, THIS SCRIPT IS THE JOB — a non-zero exit or a
# failure-shaped result escalates to the engine for diagnosis (§4.1).
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
print(f"GTM snapshots healthy: synthesis={d['synthesis']} files={len(d.get('files', []))}")
PY
