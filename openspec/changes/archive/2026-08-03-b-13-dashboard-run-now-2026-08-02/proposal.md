# B-13 — Dashboard run-now button (phase-1 manual trigger)

## Why

The garden dashboard can now pause/resume rounds and edit schedules (B-11 + console),
but there is no way to *fire* a run from the page. For the five ads loops this is the
blocking gap: they cannot be installed to launchd at all until the claude-engine
launchd-auth issue is fixed, so today the only way to get fresh output is
`loopctl run <name>` in a terminal. The accepted phase-1 bar for the ads fleet
(docs/_archive/warmstarts/ADS_LOOPS_FOLLOWUP_WARMSTART.md, generalissimo 2026-07-28) is exactly this:
*"i press a button to run everything … the runs work and produce the output."*
A per-loop run-now trigger on the dashboard closes that gap without touching
scheduling (phase 2, explicitly out of scope).

## What Changes

- `bin/console.py` gains a run-trigger surface: `POST /api/loops/<name>/run` starts a
  supervised run in a background worker; `GET /api/run/status` reports the worker's
  state for polling. The request path never shells out synchronously (the /schedules
  13.8s lesson); the worker invokes `bin/loopctl run <name>` — one code path for CLI
  and console, same as the existing mutations.
- One job at a time, console-wide (the DMP/CRO shared-job-lock shape from
  `maguyva-marketing` `console/dashboard/dmp_regen.py`); the worker is exception-safe
  so a crash can never strand the status as running and hold the lock (dmp_regen :180
  lesson). Per-loop overlap with a scheduled/CLI run is already handled by the
  runner's own fcntl lock (`skipped-overlap`).
- `dashboard/generate.py` renders a per-loop run-now button as inert, hidden markup,
  hydrated only when the existing `fetch('api/state')` gate succeeds (§13.2 pattern —
  identical behavior to the B-11 rounds switch and schedule pill; `file://` opens are
  byte-identical in behavior to today).
- `docs/INTERFACES.md` §13 is amended in the same change: the endpoint table gains the
  two routes and a subsection defines the worker contract, including that this is the
  ONLY console path permitted to fire a run (set-schedule's "NEVER kickstart" rule is
  unchanged).

Explicitly OUT of scope: launchd install/uninstall from the page (blocked on
claude-under-launchd auth; §8.1 keeps install CLI-only), any change to the rounds
switch, a fleet-wide "run everything" button (composes later from this primitive), and
the ads half-of-runs-die reliability issue (separate debugging work).

## Capabilities

### New Capabilities

- `manual-run-trigger`: a human can fire one supervised loop run from the dashboard —
  console run endpoint + status polling + worker semantics + the page button that
  drives them.

### Modified Capabilities

(none — `finding-suppression` is untouched; no existing spec's requirements change)

## Impact

- `bin/console.py`: two new routes + a worker module-level job slot; origin gate
  (§13.1) applies unchanged to both.
- `dashboard/generate.py`: one new control per loop row inside the existing
  `data-console-controls` cell + hydration script additions.
- `docs/INTERFACES.md`: §13 table + new §13.3 (amendment ships in the same commit as
  the code, per house rule).
- Tests: hermetic additions to the console handler tests (fake `loopctl` subprocess)
  and the dashboard self-containment/hydration tests. No network, macOS-safe shell.
- No schema, runner, or engine changes. Nothing in `state/` changes shape.
