# ads-hard-cut — intake spec

## 1. Purpose & stop condition

Stop paid spend automatically when the program's total actual month-to-date
spend reaches a level that cannot be normal. "Done" for a run is a verdict on
one deterministic question. "Done" for the loop is that no runaway can spend
unbounded money while everyone is asleep.

It retires when a platform-side limit replaces it — Google stopping itself is
strictly better than a robot reaching in — but consumer-billing Google accounts
expose no API-settable account cap today (`account_budget` reads `INFINITE`, an
invoiced-account concept), so there is nothing to configure.

Origin: Generalissimo, 2026-08-31 — *"extend the hard cap to a loop that will
cut if it hits the hard cap and set it to $4000 which is enough to serve as a
sanity check but not enough to actually slow anything down judging by the other
numbers."* Full design:
`docs/superpowers/specs/2026-08-31-budget-reconcile-and-hard-cut-design.md` in
maguyva-marketing.

## 2. Agentic pattern

None in the decision. Human-in-the-loop after the fact. The engine writes up an
incident that has already been decided and, when it fired, already executed.
Recovery is entirely human.

## 3. Type & data flow

`type=watchdog`, so `precheck.sh` IS the job (INTERFACES.md §4.1) and is the
whole cut path.

```
firstparty                              llm
──────────                              ───
precheck.sh  ──(bin/probe)──────────▶   probes/ads-spend-read       (read-only)
  │                                       └─ actual MTD per network, twice,
  │                                          plus every ENABLED campaign
  │  compares two numbers
  └─ if breach ─(bin/probe)─────────▶    probes/ads-emergency-pause  (WRITING)
                                           └─ service.record_and_apply(pause)
                                              → budget guard → driver → journal
```

The Google Ads credentials, `ads.db` and the whole `console.ads` domain layer
live on llm; the fleet runs on firstparty. The probe channel is the only bridge,
and this is its second writing probe (`ticket-add` is the first). Both follow
the same shape: one base64url argv, an `.allow` file, argv never a shell.

## 4. Cadence

`daily:08:00` local — one hour ahead of `ads-delivery-watch`. Daily is the right
resolution: at $56/day the threshold is ~71 days of ordinary burn away, so no
plausible runaway crosses it and gets meaningfully worse inside a few hours, and
each extra run is two live API reads for a number that barely moves.

## 5. Scope & exclusions

**In scope:** every network with a configured driver — google, reddit, x — both
in the spend total and in the pause. A breaker that only covers one network is
not a breaker.

**Out of scope, deliberately:**
- *Deciding what the money should be spent on.* The Demand Gen question ($718
  lifetime, zero conversions, zero repo links) is a separate open decision. The
  breaker is indifferent to allocation.
- *The soft/committed caps.* Those gate orders we place and are unchanged.
- *Delivery outages.* Those drive spend to zero and can never trip this. That is
  `ads-delivery-watch`'s job, and the two must stay separate loops: folding the
  cutter into the alarm would let a bug in the alarm path pause the program.

## 6. Guardrails

Every one of these exists because of a specific way this can go wrong, and none
is optional. Blast radius, stated plainly: a wrong cut stops all paid
acquisition until a human resumes it, which is a worse day than a $4,000
overspend.

1. **Two reads must agree.** Each network is read from two different resources
   (google: account aggregate vs sum of campaign rows; reddit: report by ad
   group vs by campaign). Totals differing by more than 5% or $50, whichever is
   larger, block the cut.
2. **Never cut on a missing, errored or stale read.** An input gap is not a
   breach. A network contributing $200 or more from a single unconfirmable
   source blocks the cut; below that it is noted, because an understated figure
   can only cause a MISSED breach, never an invented one.
3. **Never cut on a falling reading.** Month-to-date is monotonic inside a
   month. A figure below the month's recorded high-water mark is a data fault.
4. **Idempotent.** `dedupe_key = hardcut:<YYYY-MM>:<network>:<campaign_id>`, so
   a second run in the same month is a journal no-op rather than a re-pause.
   Only campaigns the read saw as ENABLED are ever targeted.
5. **Kill switch.** `ads.hard_cut_enabled: false` disables the cutter alone.
   Do **not** use `ads.enabled` for this — that blocks every write including
   spend-REDUCING ones, which is the opposite of what a worried operator wants.
6. **Dry-run mode.** `ads.hard_cut_dry_run: true` runs every step, journals
   nothing, and logs exactly which campaigns would have been paused. Config may
   force dry run; a caller may only ever make it more conservative, never less.
7. **Resume is human-only, always.** The loop never un-pauses. Not when the
   month rolls, not when spend resets.
8. **Alarm even on success.** A cut exits non-zero like every other finding. A
   program-wide pause is a P1 incident, not a tidy outcome.
9. **Nonce-bound writes.** The writing probe refuses to act without a fresh
   nonce that the reading probe minted on the same host within 15 minutes, whose
   recorded figure matches, whose read found a breach with no blockers, and
   whose campaign list contains every target. A hand-made payload cannot pause
   the program.
10. **One verb.** The writing probe can only `pause`. It cannot start, resume,
    raise or create, and the networks it may touch are listed in
    `probes/ads-emergency-pause.allow`.

## 7. The standing rule this amends

`maguyva-marketing/CLAUDE.md` carried an absolute rule:
*no scheduled job calls `record_and_apply()` at any trust level.* Generalissimo
amended it narrowly on 2026-08-31:

> A scheduled job may place orders **only** to *stop* spending, **only** at the
> hard-cut threshold, and **only** in the pausing direction. No scheduled job
> may ever start, resume, raise or create anything.

The original rule exists so a robot cannot make *spending* decisions. A circuit
breaker only ever spends less, so it does not engage the risk the rule was
written to prevent. Both documents were updated in the same change; this loop
should not exist without that amendment on the record.

## 8. Known limitations, stated rather than hidden

- **X cannot actually be stopped.** It has no ads API, so its driver returns a
  human checklist and journals `manual_pending`. The breaker raises the task; a
  person does it. X currently reads $0.00 and is effectively ended, so this is
  theoretical today.
- **Campaign-level pausing is coarse by design.** CLAUDE.md warns against
  campaign-level pauses when eliminating a *variant*, because one campaign holds
  many. That warning does not apply here: stopping everything is the goal.
- **The X figure is a snapshot**, only as fresh as the last Ads Manager import.
  It can understate the total and therefore delay a cut. It cannot cause one.
- **It cannot tell a runaway from a legitimate scale-up.** At $4,000 that is an
  acceptable trade: any legitimate scale-up to 2.3× the configured monthly
  budget should involve a human first, and resuming is a few clicks.
