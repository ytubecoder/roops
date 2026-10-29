#!/usr/bin/env bash
# ads-experiment-candidates/precheck.sh — THIS SCRIPT IS THE JOB (type=watchdog).
#
# One job: candidates. Build a short-lived dry-run request and call
# probe:ads-experiment-tick. Exit 0 is a quiet heartbeat when the receipt is
# ok or skipped because the candidates lock is held. Exit 1 asks the engine
# to write the failure up. AI drafts are proposals only. This script never
# places, changes or pauses an ad. The probe forces dry-run.
set -euo pipefail

INPUTS="${OUT_DIR:?OUT_DIR required (set by the runner)}/inputs"
mkdir -p "$INPUTS"

if [ -n "${RUN_ID:-}" ]; then
  RUN_TOKEN="$RUN_ID"
else
  RUN_TOKEN="$(date -u +%Y%m%dT%H%M%SZ)"
fi

results=()
fail=0

for job in candidates; do
  arg="$(python3 - "$RUN_TOKEN" "$job" <<'PY'
import base64, json, sys
from datetime import datetime, timedelta, timezone
run_id, job = sys.argv[1], sys.argv[2]
now = datetime.now(timezone.utc).replace(microsecond=0)
body = {
    "v": 1,
    "request_id": f"{run_id}:{job}",
    "job": job,
    "issued_at": now.isoformat().replace("+00:00", "Z"),
    "expires_at": (now + timedelta(minutes=9)).isoformat().replace("+00:00", "Z"),
    "dry_run": True,
}
raw = json.dumps(body, separators=(",", ":")).encode()
print(base64.urlsafe_b64encode(raw).decode().rstrip("="))
PY
)"
  errfile="$INPUTS/${job}.err"
  if ! "$LOOPS_ROOT/bin/probe" ads-experiment-tick "$arg" --out "$INPUTS/${job}.json" 2>"$errfile"; then
    detail="$(head -n 4 "$errfile" 2>/dev/null || true)"
    if [ -n "$detail" ]; then
      detail=" — ${detail//$'\n'/ }"
    fi
    results+=("PROBLEM: ${job}: transport failed${detail}")
    fail=1
    continue
  fi
  if line="$(python3 - "$job" "$INPUTS/${job}.json" <<'PY'
import json, sys
job, path = sys.argv[1], sys.argv[2]
try:
    with open(path) as fh:
        receipt = json.load(fh)
except Exception as exc:  # noqa: BLE001 — the receipt is ground truth or it is not
    print(f"PROBLEM: {job}: unreadable receipt ({exc})")
    raise SystemExit(1)
if not isinstance(receipt, dict):
    print(f"PROBLEM: {job}: receipt is not an object")
    raise SystemExit(1)
if receipt.get("ok") is True or receipt.get("skipped") == "locked":
    state = "skipped: locked" if receipt.get("skipped") == "locked" else "ok"
    print(f"{job}: {state}")
    raise SystemExit(0)
err = receipt.get("error") or receipt.get("stdout_tail") or f"tick_exit={receipt.get('tick_exit')}"
print(f"PROBLEM: {job}: {err}")
raise SystemExit(1)
PY
)"; then
    results+=("$line")
  else
    results+=("$line")
    fail=1
  fi
done

if [ "$fail" -eq 0 ]; then
  echo "ads-experiment-candidates: ok"
else
  echo "ads-experiment-candidates: failed"
fi
printf '%s\n' "${results[@]}"
if [ "$fail" -ne 0 ]; then
  echo
  echo "No ad was placed, changed or paused. Drafts are proposals only."
  exit 1
fi
exit 0
