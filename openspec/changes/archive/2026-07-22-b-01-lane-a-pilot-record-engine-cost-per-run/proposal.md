## Why

`loopctl findings <loop>` lets a human ack, dismiss, snooze, or reopen a finding,
and the runner filters suppressed findings out of promoted artifacts. That
behaviour is implemented (`bin/db.py: cmd_suppressed`, `cmd_dispose`) and
referenced from `docs/INTERFACES.md` §4.5, but it has no spec under
`openspec/specs/`.

This change backfills the existing behaviour as canonical requirements, derived
by reading the implementation rather than the docs, so that future changes to
suppression have something to be a delta *against*.

## What Changes

- Adds the `finding-suppression` capability, documenting the behaviour the
  harness must preserve: which dispositions suppress, how snooze expiry is
  evaluated, and what the runner receives.
- No code changes. This is a documentation-of-record change.

## Impact

- `bin/db.py` (`cmd_suppressed`, `cmd_dispose`) becomes spec-covered.
- Nothing at runtime changes.
