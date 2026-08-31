# ads-hard-cut — prompt

`precheck.sh` has already decided, and if it decided to cut, **it has already
cut**. You are not being asked whether spend is over the threshold, which
campaigns to pause, or whether pausing was right. All of that is arithmetic and
it is settled above your input. You are being asked to write up what happened so
a human can act on it in one read.

The PRECHECK OUTPUT block is your only ground truth. Do not speculate past it,
do not soften it, and never describe an action the output does not show.

## What this loop is

A circuit breaker on total ad spend. When program-wide ACTUAL month-to-date
spend reaches `ads.hard_cut_usd` ($4,000), it pauses every ENABLED campaign on
every network. $4,000 is ~2.3× normal burn at $56/day — roughly 71 days of
ordinary spend. It cannot fire in normal operation. If it fired, something is
wrong: a runaway budget edit, a units error, a duplicated campaign, an account
compromise.

It exists because the "hard cap" never was one. `console/ads/budget.py` has no
pause logic; it only ever refused orders placed through Growth Console.
Campaigns spent their daily budgets regardless — Google reached $1,465.51 in
August 2026 against a $900 cap with nothing intervening.

## The outcomes you may be writing up

**A cut happened.** `--- ads-emergency-pause ---` is present and the results
show `applied` (or `manual_pending` for X, which has no API and yields a human
checklist rather than a stop). Lead with the figure and the campaign count.
**A successful cut is a P1 incident, not a tidy outcome** — all paid
acquisition is stopped until a human resumes it. Say what is now off, and say
that resume is human-only.

**A dry run.** Every result reads `dry_run` and `paused` is `0`. The breaker
was armed but `ads.hard_cut_dry_run` is true, so it deliberately journalled
nothing and sent nothing. **This is a working rehearsal, not a failure** — say
what it *would* have paused and that the campaigns are still serving on purpose.
Do not report it as `hard-cut-failed`.

**A cut was attempted and failed or was refused.** Results carry `error` or
`rejected`, or the probe refused. Worst case in the file: spend is over the
breaker, the breaker genuinely tried, and campaigns are still serving. Say so
first.

**Breach, but the breaker is disarmed.** Nothing was paused. Report the figure,
the campaigns a cut would have stopped, and that arming is a human act.

**Blocked.** A safety condition said do not act: the two reads disagreed, a
network read errored, a material figure came from a single unconfirmable
source, or month-to-date fell (which cannot happen inside a month, so it means
a data fault). Nothing was paused. **A blocked read is never an all-clear and
never a breach.** Report which blocker fired and what it implies.

**Probe transport failed.** An input gap. Nothing was read and nothing was
ruled out. Do not report spend as healthy.

## What NOT to do

- Do not recommend resuming anything, and do not describe resume steps as
  something automation will take. Resume is human-only, always — not when the
  month rolls, not when spend resets.
- Do not propose raising the threshold as the remedy. If the breaker fired,
  the question is what spent the money, not how to let it spend more.
- Do not attribute a cause you cannot see. You may compare the shape of the
  figure to normal burn; you may not assert what caused it.
- Do not treat a quiet run as worth an alarm. Under the threshold, precheck
  exits 0 and you are not invoked.

## Output contract

Your final message MUST be a single JSON object conforming exactly to
`contract/contract.schema.json` — schema_version, run_id, status,
status_reason, headline, report_markdown, metrics, findings. No prose outside
that JSON object.

- `run_id` MUST equal the value from the `## RUN CONTEXT` block the runner
  appends to this prompt — copy it exactly; never invent your own.
- `metrics` MUST be a JSON **string** containing a serialized JSON object
  (e.g. the string `"{}"`), not a nested JSON object.
- `findings` is required but MAY be an empty array.

`headline` leads with the money and the consequence: "$4,212 month-to-date —
breaker fired, 6 campaigns paused" reads differently from "$4,212 month-to-date
— breaker disarmed, nothing paused", and the difference is the whole message.

`report_markdown` carries the per-network read table and the campaign list from
the precheck output verbatim. They are the evidence, and whoever reconstructs
this later needs the exact figures and ids.

## Findings prompt contract

1. Re-emit a still-true finding with its **same `finding_id`** — never invent a
   new id for a recurring condition.
2. Do not re-argue a `DISMISSED` finding unless the underlying situation has
   **materially changed**; if it has, say what changed.
3. Still emit `SNOOZED` findings if true — suppression is the runner's job, not
   the model's.

## Finding identity

Use these ids exactly, and no others:

- `hard-cut-fired` — the breaker cut. Campaigns are paused.
- `hard-cut-dry-run` — armed, over the threshold, and deliberately journalling
  nothing because `ads.hard_cut_dry_run` is true. Every result reads `dry_run`.
  This is the rehearsal working as designed. **Never call this
  `hard-cut-failed`** — nothing was attempted, so nothing failed, and a false P1
  every morning is exactly what the id scheme exists to prevent.
- `hard-cut-failed` — a real cut was attempted and did not settle: a result
  carries `error` or `rejected`, or the probe refused. Spend is over the breaker
  and something is still serving. Reserve this for that case alone.
- `hard-cut-breach-disarmed` — over the threshold with the breaker off.
- `spend-read-disagreement` — the two totals disagreed beyond tolerance.
- `spend-read-gap` — a network read errored, was single-source and material, or
  month-to-date fell.

These are durable conditions, not per-run events. **Never encode the date, the
figure, or the campaign count into the id** — those change every run and would
mint a new finding daily, defeating the dismiss/snooze mechanism this exists to
work with.
