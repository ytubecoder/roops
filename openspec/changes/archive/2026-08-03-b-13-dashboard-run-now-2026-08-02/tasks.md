# B-13 tasks — console run trigger + dashboard run-now button

## 1. Console run worker + routes (`bin/console.py`)

- [x] 1.1 Add the module-level job slot: `threading.Lock` + status dict
      `{running, loop, started_at, finished_at, exit_code, ok, error}` (ISO-8601 UTC
      timestamps, nulls when idle) with a small reset/snapshot helper.
- [x] 1.2 Add the worker function: daemon thread body in `try/finally`; calls
      `_loopctl(root, ["run", name])`; records exit code verbatim + bounded stderr
      tail; `finally` writes terminal status and releases the slot; then
      `_regen_dashboard(root)` best-effort (warn on stderr, never alters outcome).
- [x] 1.3 Route `POST /api/loops/<name>/run` in `handle_request`: name regex
      `[A-Za-z0-9_-]+`; 404 unknown loop; uniform 400 on non-object body; 409 with
      running-loop payload when the slot is busy; 202 + status snapshot on start.
      No installed/enabled gate.
- [x] 1.4 Route `GET /api/run/status`: 200 + status dict; terminal snapshot persists
      until the next job starts.
- [x] 1.5 Confirm §13.1 origin gate covers both routes with zero changes (it wraps
      `_do` for every request) — add a test, not code, if true.

## 2. Console tests (`tests/test_console.py`, hermetic)

- [x] 2.1 Endpoint tests via `handle_request()` + `LoopsRoot`: 404 unknown loop,
      400 malformed body, 409 while busy (payload names loop + started_at),
      202 shape, status idle shape, terminal snapshot after completion.
- [x] 2.2 Worker tests with `console._loopctl` monkeypatched (no engine ever runs):
      exit-0 → ok true; non-zero → ok false + stderr tail; worker exception →
      slot released, `running` false (the strand-proof scenario); regen failure →
      outcome unchanged + stderr warning.
- [x] 2.3 Origin-gate test: non-loopback Host / wrong Content-Type on the run POST
      → 403, no worker started.

## 3. Dashboard control (`dashboard/generate.py`)

- [x] 3.1 Render the `con-run` button (走, aria-label "run <name> now") inside the
      existing `.con-cell` for EVERY loop row — no installed gating, inert + hidden
      like its siblings.
- [x] 3.2 Hydration script: after `api/state` success, fetch `api/run/status` once;
      if running, disable all 走 buttons and mark the running loop's.
- [x] 3.3 Click handler: POST via the existing `post()` normalizer; on 202 poll
      status every ~3s until `running` flips false, then `location.reload()`;
      on 409/failure surface via the existing alert path and re-enable.
- [x] 3.4 CSS for the button + its in-flight state in the generated stylesheet
      (kit-consistent, no new remote references).
- [x] 3.5 Dashboard tests: control renders for installed AND non-installed loops;
      hidden by default; `tests/html_selfcontained.py` still passes.

## 4. Contract + docs

- [x] 4.1 Amend `docs/INTERFACES.md` §13: two new rows in the endpoint table + new
      §13.3 defining the worker contract and the rule that this is the ONLY console
      path that fires a run (set-schedule "NEVER kickstart" unchanged). Same commit
      as the code.
- [x] 4.2 Note the new surface in `docs/ADS_LOOPS_FOLLOWUP_WARMSTART.md` (phase-1
      trigger now exists in the roops console) — and that the growth-console
      "run everything" button remains a separate, unbuilt thing.

## 5. Verify + close out

- [x] 5.1 `bash tests/run-tests.sh` fully green (hermetic, no network).
- [x] 5.2 Live check: `loopctl serve`, fire a cheap loop (e.g. hello-loop) from the
      page, watch 202 → poll → reload; confirm `skipped-overlap` when firing a loop
      already running from CLI.
- [x] 5.3 Move B-13 to review: `python3 ~/.claude/ticket-takeaway/tickets-cli.py
      move loops B-13 review`; commit + push per house rules.
