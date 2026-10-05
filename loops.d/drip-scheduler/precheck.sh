#!/usr/bin/env bash
# drip-scheduler/precheck.sh — THIS SCRIPT IS THE JOB (type=watchdog, INTERFACES §4.1).
#
# Exit 0 = quiet tick (nothing failed, nothing turned off): green heartbeat, no engine.
# Exit 1 = the engine must write a finding: a failed send, a campaign turned off
#          automatically, a tick error, or the probe channel unreachable.
set -euo pipefail
INPUTS="${OUT_DIR:?OUT_DIR required (set by the runner)}/inputs"
mkdir -p "$INPUTS"

echo "# drip-scheduler precheck — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

if ! "$LOOPS_ROOT/bin/probe" drip-tick --out "$INPUTS/tick.json" 2>"$INPUTS/tick.err"; then
  echo "PROBE TRANSPORT FAILED — could not run drip-tick on llm."
  echo "No drip email was sent this tick, and no reply/bounce was reconciled."
  echo
  sed -n '1,20p' "$INPUTS/tick.err" 2>/dev/null || true
  exit 1
fi

python3 - "$INPUTS/tick.json" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception as exc:  # noqa: BLE001
    print(f"TICK OUTPUT UNREADABLE: {exc}")
    raise SystemExit(1)
if d.get("skipped") == "locked":
    print("tick skipped: a previous tick still holds the lock (still sending).")
    raise SystemExit(0)
sent = d.get("sent", 0)
failed = d.get("failed", 0)
off = d.get("turned_off") or []
print(f"sending: {d.get('sending', 'enabled')} · sent {sent} · failed {failed} · "
      f"held for canary {d.get('held_for_canary', 0)} · due remaining {d.get('due_remaining', 0)} · "
      f"export age {d.get('export_age_h')}h")
problems = []
if not d.get("ok", True) or d.get("error"):
    problems.append(f"tick error: {d.get('error') or d.get('stderr_tail') or 'tick exit ' + str(d.get('tick_exit'))}")
if failed:
    problems.append(f"{failed} send(s) failed this tick — see the GC Drips tab activity log")
for c in off:
    problems.append(f"campaign turned OFF automatically: {c} — see GC /drips for the reason; turning it back on is the operator's call")
if problems:
    print()
    for p in problems:
        print("PROBLEM:", p)
    print()
    print(json.dumps(d, indent=1, sort_keys=True))
    raise SystemExit(1)
raise SystemExit(0)
PY
