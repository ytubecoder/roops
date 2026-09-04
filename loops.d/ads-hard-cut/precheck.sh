#!/usr/bin/env bash
# ads-hard-cut/precheck.sh — THIS SCRIPT IS THE JOB (type=watchdog,
# docs/INTERFACES.md §4.1), and it is the entire cut path.
#
# No model is involved in the decision. This script reads two numbers, compares
# them to a threshold, and either does nothing or pauses the program. The engine
# runs afterwards and only writes up a decision that has already been made. That
# split is deliberate: a program-wide pause is arithmetic, and arithmetic must
# not be delegated to something that might hedge it.
#
# Exit 0 = quiet (under the threshold, nothing to say).
# Exit 1 = the engine must write an alarm. Every non-quiet outcome is exit 1,
#          INCLUDING a successful cut — a cut is a P1 incident, not a tidy
#          outcome (spec §3.6.8).
set -euo pipefail

# The runner exports OUT_DIR (the run dir, INTERFACES.md §4.1); it never set
# LOOP_RUN_DIR, so the old fallback silently wrote every probe input to
# /tmp/inputs. Fail loudly instead of guessing a path.
INPUTS="${OUT_DIR:?OUT_DIR required (set by the runner)}/inputs"
mkdir -p "$INPUTS"

echo "# ads-hard-cut precheck — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

if ! "$LOOPS_ROOT/bin/probe" ads-spend-read --out "$INPUTS/spend.json" 2>"$INPUTS/spend.err"; then
  echo "PROBE TRANSPORT FAILED — could not reach the probe host (llm)."
  echo "This is an input gap, NOT a breach and NOT an all-clear. Nothing was"
  echo "paused and nothing was ruled out."
  echo
  sed -n '1,20p' "$INPUTS/spend.err" 2>/dev/null || true
  exit 1
fi

# Decide. The exit code carries the verdict: 0 quiet, 1 alarm, 2 attempt a cut.
# `|| rc=$?` because set -e would otherwise kill the script on any non-zero.
rc=0
python3 - "$INPUTS/spend.json" "$INPUTS/payload.b64" <<'PY' || rc=$?
import base64, json, sys

src, payload_path = sys.argv[1], sys.argv[2]
with open(src) as fh:
    d = json.load(fh)

if d.get("error"):
    print(f"PROBE ERROR: {d['error']}")
    print("Treat as an input gap, not as an all-clear. Nothing was paused.")
    raise SystemExit(1)

cfg = d.get("config") or {}
prog = d.get("program") or {}
total_a = prog.get("mtd_a_usd", 0.0)
total_b = prog.get("mtd_b_usd", 0.0)
threshold = d.get("threshold_usd")
blockers = d.get("blockers") or []
campaigns = d.get("enabled_campaigns") or []

print(f"program actual month-to-date  ${total_a:,.2f}   (second read ${total_b:,.2f}, "
      f"difference ${prog.get('diff_usd', 0.0):,.2f}, tolerance ${prog.get('tolerance_usd', 0.0):,.2f})")
print(f"hard-cut threshold           " + (f"${threshold:,.2f}" if threshold else "UNSET"))
print(f"breaker armed                {d.get('armed')}   dry run: {d.get('dry_run')}")
print()
print("per network:")
for net, r in (d.get("networks") or {}).items():
    src_b = r.get("read_b_source") or "— (single source)"
    note = "" if r.get("reads_independent") else "   [SINGLE SOURCE]"
    err = f"   ERROR: {r['error']}" if r.get("error") else ""
    print(f"  {net:<7} A ${float(r.get('read_a_usd', 0)):>10,.2f}  "
          f"B ${float(r.get('read_b_usd', 0)):>10,.2f}{note}{err}")
    print(f"          A: {r.get('read_a_source')}")
    print(f"          B: {src_b}")
print()
print(f"enabled campaigns that a cut would pause: {len(campaigns)}")
for c in campaigns:
    b = c.get("daily_budget_usd")
    print(f"  {c['network']:<7} {c['campaign_external_id']:<14} "
          f"{(c.get('name') or '')[:34]:<34} "
          + (f"${b:,.2f}/day" if b is not None else "budget n/a"))
print()

if blockers:
    print("BLOCKED — one or more safety conditions say do not act:")
    for b in blockers:
        print(f"  [{b['id']}] {b['detail']}")
    print()
    print("Nothing was paused. A blocked read is never treated as a breach and")
    print("never as an all-clear.")
    raise SystemExit(1)

if not d.get("breach"):
    head = (threshold - total_a) if threshold else 0.0
    print(f"RESULT: ok — ${head:,.2f} of headroom below the ${threshold:,.2f} "
          "circuit breaker.")
    raise SystemExit(0)

# --- breach, clean read ---------------------------------------------------
print(f"BREACH — ${total_a:,.2f} actual month-to-date is at or over the "
      f"${threshold:,.2f} circuit breaker.")
print()
if not d.get("armed"):
    print("The breaker is DISARMED (ads.hard_cut_enabled=false), so NOTHING was")
    print("paused. The campaigns listed above are the ones a cut would stop.")
    print("Arming is a deliberate human act.")
    raise SystemExit(1)

payload = {
    "nonce": d["nonce"],
    "mtd_usd": total_a,
    "campaigns": [{"network": c["network"],
                   "campaign_external_id": c["campaign_external_id"],
                   "name": c.get("name") or ""} for c in campaigns],
    "reason": f"ads-hard-cut: program MTD ${total_a:,.2f} >= ${threshold:,.2f}",
}
with open(payload_path, "w") as fh:
    fh.write(base64.urlsafe_b64encode(
        json.dumps(payload).encode()).decode().rstrip("="))
print("The breaker is ARMED. Calling ads-emergency-pause.")
raise SystemExit(2)
PY

# 0 = quiet, 1 = alarm without a cut, 2 = attempt the cut.
if [ "$rc" -eq 0 ]; then exit 0; fi
if [ "$rc" -ne 2 ]; then exit 1; fi

echo
echo "--- ads-emergency-pause ---"
if ! "$LOOPS_ROOT/bin/probe" ads-emergency-pause "$(cat "$INPUTS/payload.b64")" \
     --out "$INPUTS/pause.json" 2>"$INPUTS/pause.err"; then
  echo "THE CUT FAILED OR WAS REFUSED. Spend is over the circuit breaker and"
  echo "campaigns are STILL SERVING. This needs a human now."
  echo
  cat "$INPUTS/pause.json" 2>/dev/null || true
  sed -n '1,20p' "$INPUTS/pause.err" 2>/dev/null || true
  exit 1
fi
cat "$INPUTS/pause.json"
echo
echo "A cut is a P1 incident, never a tidy outcome. Resume is human-only."
exit 1
