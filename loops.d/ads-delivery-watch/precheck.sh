#!/usr/bin/env bash
# ads-delivery-watch/precheck.sh — THIS SCRIPT IS THE JOB (type=watchdog,
# docs/INTERFACES.md §4.1). It reads one probe, decides pass/fail
# deterministically, and exits non-zero only when a real condition is present.
# The engine is never asked to judge whether the account is dark — that is
# arithmetic, not judgment. It is only asked to write the alarm up when the
# script has already decided there is one.
#
# Why this loop exists: the account went dark on a declined billing-threshold
# charge on 2026-07-30 (9 days) and again on 2026-08-28 (found 3 days late).
# The five ads loops ran normally through both. A dead account has zero spend,
# which passes every cap check, and zero impressions, which produces no CTR
# verdict — it scores perfectly on everything they watch.
set -euo pipefail

INPUTS="${LOOP_RUN_DIR:-/tmp}/inputs"
mkdir -p "$INPUTS"

echo "# ads-delivery-watch precheck — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

if ! "$LOOPS_ROOT/bin/probe" ads-delivery-watch --out "$INPUTS/delivery.json" 2>"$INPUTS/delivery.err"; then
  echo "PROBE TRANSPORT FAILED — could not reach the probe host (llm)."
  echo "This is an input gap, NOT evidence that delivery is fine. Do not"
  echo "report the account as healthy on the strength of a failed probe."
  echo
  sed -n '1,20p' "$INPUTS/delivery.err" 2>/dev/null || true
  exit 1
fi

python3 - "$INPUTS/delivery.json" <<'PY'
import json, sys

with open(sys.argv[1]) as fh:
    d = json.load(fh)

if d.get("error"):
    print(f"PROBE ERROR: {d['error']}")
    print("Treat as an input gap, not as an all-clear.")
    raise SystemExit(1)

status = d.get("status", "ok")
mtd = d.get("mtd_usd", 0.0)
cap = d.get("google_network_cap_usd", 0.0)
enabled = d.get("enabled_campaigns", [])
dark = d.get("consecutive_dark_days", 0)

print(f"account {d.get('customer_id')} · enabled campaigns {len(enabled)}")
print(f"month-to-date actual ${mtd:,.2f} against a ${cap:,.0f} google network cap")
print(f"consecutive complete days with $0.00 spend: {dark}")
print()
print("daily (complete days, newest first):")
for day, row in (d.get("daily") or {}).items():
    if row is None:
        print(f"  {day}  NO ROW — nothing served")
    else:
        print(f"  {day}  ${row['spend_usd']:>8,.2f}  {row['impressions']:>7,} impr  "
              f"{row['clicks']:>4,} clicks")
print()

if not d.get("findings"):
    print("RESULT: ok — the account served on the most recent complete day and")
    print("month-to-date spend is inside its cap.")
    raise SystemExit(0)

for f in d["findings"]:
    print(f"[{f['severity'].upper()}] {f['id']}")
    print(f"  {f['detail']}")
    print()

print("Known cause for delivery-stopped, both prior occurrences: a declined")
print("billing threshold charge (2026-07-30 was Mastercard ****0144, $350.00).")
print("The Google Ads API does not model payment failure, so every entity stays")
print("ENABLED/APPROVED/ELIGIBLE throughout — absence of spend is the only signal")
print("available. Confirming it requires a human to open Billing & payments for")
print("the account; the billing pages are passkey-walled and cannot be driven.")
raise SystemExit(1)
PY
