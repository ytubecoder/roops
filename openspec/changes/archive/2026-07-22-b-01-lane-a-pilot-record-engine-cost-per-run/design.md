## Context

The suppression rule lives in one place — `cmd_suppressed` in `bin/db.py` — and
is consumed by `bin/run-loop.sh`, which filters promoted artifacts and renders
the §4.5 footer. `docs/INTERFACES.md` is a frozen contract, so the spec must
describe what the code does, not what would be tidier.

## Decisions

**Derive from code, not from `docs/`.** `docs/INTERFACES.md` §4.5 is close but
compressed; the authoritative details (that `ack` never suppresses, that
`reopen` and no-disposition are equally excluded, that snooze uses strict `>`
against a normalised date) come from `cmd_suppressed`.

**Scope to the capability, not the subsystem.** Findings *storage*, run
lifecycle, and dashboard rendering are deliberately out of scope; only the
suppression decision is specified here.

**Snooze comparison is date-normalised and strict.** `_normalize_date_for_compare`
treats a bare date as inclusive through end-of-day, and the comparison is
`until > ts`, so a finding snoozed until today is still suppressed today. That is
existing behaviour and is specified as-is rather than "fixed" in a backfill.

## Risks

Backfills can enshrine a bug as a requirement. Mitigated by keeping the scope to
one function and stating the snooze-boundary behaviour explicitly, so a future
change to it is a visible `## MODIFIED Requirements` delta rather than a silent
drift.
