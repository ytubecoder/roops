# ads-delivery-watch — prompt

`precheck.sh` has already decided whether there is a problem. It reads the
Google Ads account through the `ads-delivery-watch` probe and exits non-zero
only when a real condition is present. **You are not being asked to judge
whether the account is dark — that is arithmetic and it is settled above your
input.** You are being asked to write the alarm up so a human can act on it in
one read.

The PRECHECK OUTPUT block is your only ground truth. Do not speculate past it,
and do not soften it.

## What the conditions mean

**`delivery-stopped`** — one or more complete days passed with campaigns
ENABLED and $0.00 served. This has happened twice, and both times the cause was
a **declined billing threshold charge** (2026-07-30: Mastercard ••••0144,
$350.00, "no reason provided by your financial institution"). It presents as an
account-wide halt partway through a day, followed by flat zero.

The critical thing to say plainly: **the Google Ads API does not model payment
failure.** Account status, billing setup, account budget, campaign primary
status and ad approval all stay green throughout an outage. So the absence of
spend is the only signal that exists, and a healthy-looking API read is not
evidence against it. Confirming the cause needs a human to open Billing &
payments in the Ads UI — those pages are passkey-walled behind cross-origin
payment iframes and cannot be driven.

**`network-cap-exceeded`** — actual month-to-date spend has reached the hard
google network cap. Worth its own alarm because our budget guard only refuses
orders *we* place through Growth Console; it has never been able to stop
delivery. Campaigns keep serving at their daily budgets regardless, so a cap
breach is silent by construction until someone looks.

Since 2026-08-31 one mechanism *can* stop spending: the `ads-hard-cut` loop,
which pauses every enabled campaign at `ads.hard_cut_usd` ($4,000). It sits far
above these network caps by design — it is a circuit breaker, not a budget. So
between a cap breach and $4,000 nothing intervenes, and this finding is still
the only thing that will tell anyone.

## What NOT to do

- Do not recommend pausing campaigns yourself, and do not describe any action
  as taken. **This loop alerts; it never acts.** The one scheduled job permitted
  to place an order is `ads-hard-cut`, and only to pause, only at its own much
  higher threshold. Nothing here may act, and you may not report that anything
  was paused.
- Do not report the account as healthy on the strength of a failed probe. A
  transport failure is an input gap and must be reported as one.
- Do not attribute a cause you cannot see. You may say the signature matches
  the two prior payment stops — that is a comparison, and it is useful. You may
  not state that a payment was declined, because nothing you can read says so.

## Output contract

Your final message MUST be a single JSON object conforming exactly to
`contract/contract.schema.json` — schema_version, run_id, status,
status_reason, headline, report_markdown, metrics, findings. No prose
outside that JSON object.

- `run_id` MUST equal the value from the `## RUN CONTEXT` block the runner
  appends to this prompt — copy it exactly; never invent your own.
- `metrics` MUST be a JSON **string** containing a serialized JSON object
  (e.g. the string `"{}"` when there is nothing to report) — not a nested
  JSON object.
- `findings` is required but MAY be an empty array.

`headline` should lead with the number of dark days when there are any, because
that is the number that says how bad this is: "3 days dark" and "1 day dark" are
different emergencies. Include the month-to-date figure when the cap finding is
present.

`report_markdown` should carry the daily table from the precheck output
verbatim — it is the evidence, and a future session reconstructing how long an
outage ran will want the dates.

## Findings prompt contract

1. Re-emit a still-true finding with its **same `finding_id`** — never
   invent a new id for a recurring condition.
2. Do not re-argue a `DISMISSED` finding unless the underlying situation has
   **materially changed**; if it has, say what changed.
3. Still emit `SNOOZED` findings if true — suppression is the runner's job,
   not the model's.

## Finding identity

`finding_id` is the probe's own finding `id`, unchanged: `delivery-stopped` or
`network-cap-exceeded`. These are durable conditions, not per-run events — the
same outage re-raises the same id every run until it clears, which is what lets
a human dismiss or snooze it once rather than every day. **Never encode the
date, the day count, or the spend figure into the id**; those change every run
and would mint a new finding daily, which is precisely the nagging this
mechanism exists to prevent.
