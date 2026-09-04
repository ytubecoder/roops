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

# The runner exports OUT_DIR (the run dir, INTERFACES.md §4.1); it never set
# LOOP_RUN_DIR, so the old fallback silently wrote every probe input to
# /tmp/inputs. Fail loudly instead of guessing a path.
INPUTS="${OUT_DIR:?OUT_DIR required (set by the runner)}/inputs"
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

DELIVERY_RC=0
python3 - "$INPUTS/delivery.json" <<'PY' || DELIVERY_RC=$?
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
print("billing threshold charge (2026-07-30 Mastercard ****0144 $350.00;")
print("2026-08-22 the same card, $500.00). The Google Ads API does not model")
print("payment failure, so every entity stays ENABLED/APPROVED/ELIGIBLE")
print("throughout — in the API, absence of spend is the only signal there is.")
print("The billing section below is read from the UI and may name the cause.")
raise SystemExit(1)
PY

# --- billing, read from the Ads UI --------------------------------------
# The API cannot see payments at all, so a second probe serves a daily
# snapshot of the billing pages scraped on the data host. Its value is
# EARLINESS: on 2026-08-22 the threshold charge declined and the balance sat
# visibly above the threshold for six days while ads kept serving. The block
# above can only ever fire once the money has already stopped.
#
# A failure here never cancels a delivery finding and never turns one into an
# all-clear — worst case the billing view is missing and says so.
echo
echo "## billing (scraped from the Ads UI on the data host)"
BILLING_RC=0
if ! "$LOOPS_ROOT/bin/probe" ads-billing-read --out "$INPUTS/billing.json" 2>"$INPUTS/billing.err"; then
  echo "BILLING PROBE TRANSPORT FAILED — the billing view is missing from this"
  echo "run. That is an input gap, not an all-clear: report it as one."
  sed -n '1,10p' "$INPUTS/billing.err" 2>/dev/null || true
  BILLING_RC=1
else
  python3 - "$INPUTS/billing.json" <<'PY' || BILLING_RC=$?
import json, sys

with open(sys.argv[1]) as fh:
    b = json.load(fh)

if not b.get("ok"):
    print(f"BILLING SNAPSHOT UNAVAILABLE: {b.get('reason')}")
    print("Input gap — do not read it as healthy.")
    raise SystemExit(1)

bal, thr = b.get("balance_usd"), b.get("threshold_usd")
print(f"snapshot {b.get('scraped_at')} ({b.get('age_hours')}h old)"
      f"{' — STALE' if b.get('stale') else ''}")
if bal is not None and thr is not None:
    print(f"balance ${bal:,.2f} against a ${thr:,.2f} payment threshold "
          f"(headroom ${b.get('headroom_usd'):,.2f})")
lp = b.get("last_payment") or {}
if lp:
    print(f"last payment: {lp.get('date')} ${lp.get('amount_usd')} "
          f"({lp.get('type')}, {lp.get('card')})")
pm = b.get("payment_method") or {}
print(f"primary {pm.get('primary')} · declined={pm.get('primary_declined')} · "
      f"backup={'yes' if pm.get('has_backup') else 'NONE'}")
ver = b.get("verification") or {}
if ver.get("deadline"):
    print(f"advertiser verification due {ver.get('deadline')} "
          f"({ver.get('days_left')}d) · outstanding: "
          f"{', '.join(ver.get('outstanding_tasks') or []) or 'none'}")
print(f"ledger pages scanned: {b.get('activity_pages_read')}")
print()

if not b.get("findings"):
    print("BILLING RESULT: ok — balance under threshold, no declines in the")
    print("scanned ledger, no account notices.")
    raise SystemExit(0)

for f in b["findings"]:
    print(f"[{f['severity'].upper()}] {f['id']}")
    print(f"  {f['detail']}")
    print()
raise SystemExit(1)
PY
fi

if [ "$DELIVERY_RC" -ne 0 ] || [ "$BILLING_RC" -ne 0 ]; then
  exit 1
fi

echo
echo "RESULT: ok — delivery served on the most recent complete day, spend is"
echo "inside its cap, and billing shows nothing outstanding."
