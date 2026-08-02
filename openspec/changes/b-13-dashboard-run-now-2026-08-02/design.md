# B-13 design — console run trigger + dashboard run-now button

## Context

The console (`bin/console.py`, INTERFACES §13) is a loopback-only ThreadingHTTPServer
with a pure `handle_request()` core and exactly two mutation endpoints, both of which
delegate to `bin/loopctl` via the `_loopctl()` helper (one code path for CLI and
console). The dashboard renders console controls as inert hidden markup that hydrates
only when `fetch('api/state')` succeeds (§13.2). A supervised run takes minutes;
`_loopctl()` uses a blocking `subprocess.run`, so a run can never execute on the
request path. The DMP/CRO consoles in maguyva-marketing already solved this shape:
POST starts a daemon-thread worker behind a shared job lock, GET polls a status dict,
and an uncaught worker exception must never strand the status as running
(`console/dashboard/dmp_regen.py` :180 lesson).

## Goals / Non-Goals

**Goals:**
- Fire one supervised run (`loopctl run <name>`) from the dashboard, for ANY loop in
  `loops.d` — installed or not, paused or not. Manual firing of a paused or
  never-installed loop is a deliberate human act and is the entire point (the ads
  loops cannot be installed until the claude-under-launchd auth fix).
- Surface in-flight state and completion on the page without reloading blind.
- Keep `file://` behavior byte-identical to today.

**Non-Goals:**
- Install/uninstall from the page (§8.1 stays CLI-only), changes to the rounds
  switch or set-schedule ("NEVER kickstart" unchanged), a fleet-wide "run
  everything" button (later; composes from this primitive), run queueing or
  cancellation, and the ads reliability bug.

## Decisions

1. **Two routes, module-level job slot.**
   - `POST /api/loops/<name>/run` (body `{}` — must still be a JSON object, matching
     the uniform 400 rule): 202 + status snapshot on start; 404 unknown loop; 409 if
     the slot is busy (payload names the running loop + started_at); 400 malformed.
   - `GET /api/run/status`: 200 with the full status dict. Global, not per-loop,
     because the slot is global (mirrors `GET /api/dmp/regenerate/status`).
   - *Alternative rejected:* per-loop job slots — extra state machine for no phase-1
     benefit; the runner's own fcntl lock already refuses true overlap
     (`skipped-overlap`), and "run everything" later wants a queue, not N slots.
   - **Method pinning (amended after critic audit 2026-08-03):** the run route
     matches POST ONLY and the status route GET ONLY — any other method on either
     path is a route miss (404) and MUST NOT start a worker. §13.1's Content-Type
     gate applies only to POST, so a GET-matched run route would be reachable by a
     plain cross-origin browser GET with a valid Host: a CSRF that fires real runs.
   - **202 body shape (amended 2026-08-03):** nested, matching the sibling
     mutations: `{"ok": true, "state": <run-status dict>}`, where the dict is the
     same 7-field shape `GET /api/run/status` returns (there it is the bare dict).
     `state.running` is true and `state.loop`/`state.started_at` are populated in
     the 202 snapshot; `state.ok` stays null while running.

2. **One job at a time, console-wide.** `threading.Lock` acquired non-blocking on
   POST. Caps engine spend and keeps the status model a single dict. Status shape:
   `{running, loop, started_at, finished_at, exit_code, ok, error}` — timestamps
   ISO-8601 UTC, `error` carries a bounded stderr tail on failure, `null`s when idle.
   **(Amended 2026-08-03):** the stderr tail bound is 4096 characters, and it is a
   TAIL — the end of stderr survives, the head is dropped. Starting a new job
   RESETS the snapshot: `loop` becomes the new name and `finished_at`/`exit_code`/
   `ok`/`error` return to null for the duration of the new run; the 409 payload
   carries the running loop's `started_at` as a machine-readable field, not only
   prose inside the error string.

3. **Worker is a dumb executor.** `threading.Thread(daemon=True)`; the whole body in
   `try/finally`; `finally` writes the terminal status and releases the slot (the
   dmp_regen :180 lesson — a crash must never leave `running=True` holding the lock).
   It calls the existing `_loopctl(root, ["run", name])` (preserves the `--`
   separator rule and the one-code-path rule), records exit code verbatim, and does
   NOT interpret run status — `state/` and the regenerated dashboard are authoritative
   (the §4.5 "model-emitted metrics get believed" family of bugs stays impossible
   here). After the subprocess returns — REGARDLESS of exit code — 
   `_regen_dashboard(root)` runs best-effort, warns on stderr, never alters the
   recorded outcome.

4. **This is the ONLY console path that fires a run.** Stated in the INTERFACES
   amendment (new §13.3). set-schedule's "NEVER kickstart" rule and the rounds
   endpoint are untouched. The new GET leaks nothing beyond `/api/state`'s existing
   confidentiality class; §13.1's Host/Content-Type gate and the no-CORS standing
   rule apply unchanged. `<name>` route regex stays `[A-Za-z0-9_-]+`.

5. **Button lives inside the existing `.con-cell`.** A third control (`con-run`,
   text 走, aria-label "run <name> now") rendered for every loop row, NOT gated on
   installed — greying it like the rounds switch would recreate exactly the dead end
   B-13 exists to fix. Hydration: on `api/state` success, one `GET /api/run/status`
   reflects any in-flight job (disable all 走 buttons, mark the running loop's).
   Click → POST → on 202 poll status every ~3s; when `running` flips false,
   `location.reload()` (the worker already regenerated the page). On 409/4xx/5xx or
   transport failure reuse the existing `post()` normalization + `alert()` path —
   the reload is gated on the success path only; an error must surface, never
   silently reload. The click handler calls `ev.preventDefault()`: the controls sit
   inside the row's `<summary>`, so an ungated click toggles the garden accordion
   (the trap generate.py already documents for the sibling controls).
   Static-file mode: control stays hidden with the rest of the cell — no new
   hydration gate needed.

## Risks / Trade-offs

- [Console killed mid-run] → the daemon thread dies with the process but the loopctl
  child keeps running and the run completes + records normally; only the status dict
  is lost. Next console start reports idle — acceptable for v1; the dashboard's own
  "running" badge (state-derived) still shows the truth after regen.
- [Blocking `subprocess.run` in the worker, no timeout] → the runner owns its own
  process-group timeouts (§4.1); the console deliberately does not add a second
  timeout layer that could orphan a half-recorded run. Worst case the slot is held
  as long as the runner's own ceiling.
- [User fires a disabled/paused loop by accident] → single confirmation is the
  button's 202→polling UI making the action visible; a run is report-only by the
  harness invariant, so the blast radius is engine spend, not action.
- [Two consoles on different ports] → each has its own slot; true overlap on the
  same loop still collapses to `skipped-overlap` via the runner lock. Documented,
  not defended.

## Migration Plan

Pure addition: new routes + new markup. No state, schema, or config migration.
Rollback = revert the commit; generated dashboards regenerate on next mutation.

## Open Questions

(none — the kanji is 走, decided 2026-08-03; tests pin it literally, so a future
swap is a deliberate test+markup change, not a review-time freebie)

## Test seam

Reuse `tests/test_console.py`'s hermetic `LoopsRoot` + `LOOPS_LAUNCHCTL` recording
stub. Endpoint tests drive `handle_request()` directly; worker tests monkeypatch
`console._loopctl` (or point the root at a loop whose engine is the tests' stub) so
no engine ever runs. Dashboard additions are covered by the existing hydration +
self-containment scanners (`tests/html_selfcontained.py` — relative `fetch` stays
legal, nothing remote is referenced).
