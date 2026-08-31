# ads-delivery-watch — intake spec

1. Purpose & stop condition

Catch a Google Ads delivery halt within hours instead of days, and separately
flag actual spend crossing the hard network cap. "Done" for any given run is a
verdict on two deterministic questions; "done" for the loop is that no delivery
outage again runs for days unnoticed. It retires only if Google grows an API
surface for payment state, or if delivery-stopping stops being possible.

Origin: two outages, same cause, neither caught. 2026-07-30 ran nine days and
was noticed by Generalissimo; 2026-08-28 was found three days late, by hand,
while answering an unrelated question about ad spend value.

2. Agentic pattern

Human-in-the-loop, single-shot. The loop alarms; a human reads the billing page
and decides. There is deliberately no iterate-across-invocations aspiration
here — the entire action space on the far side of the alarm is human-only.

3. Type & data flow (precheck gathers vs engine interprets)

`type=watchdog`, so precheck.sh IS the job (INTERFACES.md §4.1). It calls the
`ads-delivery-watch` probe on llm, which runs two read-only GAQL queries
(daily spend over the lookback window; ENABLED campaigns) plus a month-to-date
total, compares against the `ads` caps in `scheduler/config.json`, and emits
findings. precheck exits non-zero when a finding exists.

The engine interprets nothing factual. It writes the alarm up: severity,
headline, the daily evidence table, and the standing explanation of why a
green API read is not evidence of health. Splitting it this way is deliberate —
whether the account is dark is arithmetic, and arithmetic should not be
delegated to a model that might hedge it.

4. Cadence

`daily:09:00,21:00` local. The failure is a mid-day account-wide halt, so useful
resolution is hours. Higher frequency buys nothing: the check reads only
COMPLETE days (today is excluded, because a partial day legitimately reads near
zero in the morning and would alarm every day), so a third daily run would
re-report the same verdict. Two reads bracket the ads loops' evening stagger.

5. Scope & exclusions

In scope: Google only. Its delivery can stop for reasons invisible to us, it
carries effectively all current program spend, and it is the account that has
actually failed twice.

Excluded: X (no ads API; its snapshot is a manual import and staleness is
already `ADX-INP-09`'s job) and Reddit (deliberately paused since 2026-08-10 —
zero spend there is the intended state, so the same rule would alarm forever).
Also excluded: any conversion, CTR or variant judgment. Those belong to the
five ads loops, and duplicating them here would produce two sources of truth
for the same question.

6. Guardrails

- **Alerts, never acts.** Pausing a campaign is an order, and no scheduled job
  places ad orders at any trust level (docs/ads-campaign-view-warmstart.md).
  That gate is the point, not an obstacle to route around.
- **A failed probe is an input gap, never an all-clear.** Reporting a dark
  account as healthy because the probe could not be reached would be worse than
  not running at all.
- **Never assert a payment was declined.** The signature may be compared to the
  two prior occurrences; the cause cannot be read from anything we have access
  to, and stating it as fact would put an invented finding on the record.
- **Finding ids stay stable** across runs (`delivery-stopped`,
  `network-cap-exceeded`). Encoding the day count or spend figure into the id
  would mint a new finding daily and defeat dismiss/snooze.

7. Known limitation, stated rather than hidden

This detects that delivery stopped; it cannot tell you why, and it cannot tell
the difference between a payments stop and any other cause that zeroes an
account while leaving every entity ENABLED. It also cannot cut spend when the
cap is breached — nothing on our side can, which is itself the finding. The
hard cap in `scheduler/config.json` refuses orders we place through Growth
Console and has never governed delivery. Making a hard cap actually bite would
require a platform-side limit set in Google, and that remains an open decision.
