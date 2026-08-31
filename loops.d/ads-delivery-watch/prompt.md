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
status and ad approval all stay green throughout an outage. So within the API
the absence of spend is the only signal that exists, and a healthy-looking API
read is not evidence against it.

## The billing block

Since 2026-08-31 the precheck also carries a **billing** section, scraped daily
from the Ads UI on the data host and served by `ads-billing-read`. This is
where a cause can actually be named, and where the warning arrives early:
on 2026-08-22 the threshold charge declined and the balance sat above the
threshold for **six days** while ads kept serving normally. The delivery block
above cannot fire until the money has already stopped; this one can.

Report the billing findings alongside the delivery ones, and lead with the
billing cause when there is one — "delivery stopped, and here is the declined
charge that explains it" is a far more useful headline than either half alone.

- **`billing-balance-over-threshold`** — Google charges at the threshold, so a
  balance above it means a charge is overdue or has already failed. Treat this
  as the leading indicator it is, even while delivery still looks fine.
- **`billing-charge-declined`** — the ledger names the amount, the card and a
  reference id. Quote them; they are what a human takes to the bank.
- **`billing-card-declined`** — the primary method is flagged refused right now.
- **`billing-no-backup-method`** — there is no backup card, which is the
  mechanism behind both outages: one refused charge and everything stops. This
  one is chronic rather than urgent, so keep re-emitting it, but do not let it
  crowd out an acute finding.
- **`advertiser-verification-due`** — an independent clock. Missing it pauses
  the account, and the review alone takes 1 to 10 days, so the effective
  deadline is earlier than the stated one. Say how many days are left.
- **`account-notice`** — whatever banner Google is showing on every Ads page.
  Quote it verbatim; it is Google's own words about the account.
- **`billing-session-expired`**, **`billing-snapshot-stale`**,
  **`billing-read-failed`** — the billing view is missing or old. These are
  input gaps: say the channel is dark and that a human must sign the data host
  back into Google. Never report billing as clean on the strength of one.

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
- Do not attribute a cause you cannot see. If the billing block names a
  declined charge, you may and should state it, quoting the date, amount, card
  and reference — that is a direct reading. If the billing block is absent,
  stale or failed, you may only say the signature matches the two prior payment
  stops, which is a comparison, not a diagnosis.

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

`finding_id` is the probe's own finding `id`, unchanged — `delivery-stopped`,
`network-cap-exceeded`, or any of the `billing-*` / `advertiser-verification-due`
/ `account-notice` ids from the billing block. These are durable conditions, not per-run events — the
same outage re-raises the same id every run until it clears, which is what lets
a human dismiss or snooze it once rather than every day. **Never encode the
date, the day count, or the spend figure into the id**; those change every run
and would mint a new finding daily, which is precisely the nagging this
mechanism exists to prevent.
