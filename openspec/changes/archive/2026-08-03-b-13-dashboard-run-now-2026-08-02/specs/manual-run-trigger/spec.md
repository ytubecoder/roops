# manual-run-trigger — fire one supervised loop run from the dashboard

## ADDED Requirements

### Requirement: Console can start a supervised run in the background
`POST /api/loops/<name>/run` SHALL start `bin/loopctl run <name>` in a background
worker thread via the console's single `_loopctl()` code path, and SHALL respond
`202` with a status snapshot before the run completes. The request path MUST NOT
invoke `loopctl` synchronously. The route SHALL accept any loop present in
`loops.d`, regardless of its installed, enabled, or schedule state.

#### Scenario: Fire a non-installed loop
- **WHEN** a POST names a loop that exists in `loops.d` but has no launchd plist
- **THEN** the console responds 202 and the worker starts `loopctl run <name>`

#### Scenario: Unknown loop
- **WHEN** a POST names a loop not present in `loops.d`
- **THEN** the console responds 404 `{"error": "unknown loop: <name>"}` and no worker starts

#### Scenario: Malformed body
- **WHEN** the POST body is not a JSON object
- **THEN** the console responds 400 uniformly, same as the existing mutation endpoints

### Requirement: One run job at a time, console-wide
The console SHALL hold at most one run job. While a job is in flight, a further
`POST /api/loops/<name>/run` (any loop) SHALL be refused `409` with a payload that
names the running loop and its start time. Per-loop overlap with scheduled or CLI
runs remains the runner's own lock's concern (`skipped-overlap`).

#### Scenario: Second fire while busy
- **WHEN** a run job is in flight and a POST arrives for any loop
- **THEN** the console responds 409 and the payload identifies the running loop and `started_at`

### Requirement: Run status is pollable
`GET /api/run/status` SHALL return `200` with
`{running, loop, started_at, finished_at, exit_code, ok, error}`. When idle with no
prior job, non-applicable fields SHALL be null and `running` false. After a job
finishes, the terminal snapshot SHALL remain readable until the next job starts.

#### Scenario: Idle console
- **WHEN** no run job has been started since the console booted
- **THEN** status returns `running: false` with null job fields

#### Scenario: After completion
- **WHEN** a run job's `loopctl run` subprocess has exited
- **THEN** status returns `running: false`, the verbatim `exit_code`, `ok` true iff exit 0, and a bounded stderr tail in `error` when non-zero

### Requirement: Worker cannot strand the job slot
The worker SHALL write its terminal status and release the job slot in a `finally`
block, so that no exception path leaves `running` true while no subprocess is alive.

#### Scenario: Worker raises
- **WHEN** the worker body raises an unexpected exception
- **THEN** status becomes `running: false` with `ok` false and the slot accepts the next POST

### Requirement: Worker records, never interprets
The worker SHALL record the `loopctl run` exit code verbatim and SHALL NOT derive,
rewrite, or summarize the run's status — `state/` and the regenerated dashboard stay
authoritative. After the subprocess exits, the worker SHALL regenerate the dashboard
best-effort; a regen failure SHALL warn on stderr and MUST NOT change the recorded
outcome.

#### Scenario: Regen fails after a successful run
- **WHEN** the run subprocess exits 0 and dashboard regeneration raises
- **THEN** status still reports `ok` true and a warning is written to stderr

### Requirement: Only console path that fires a run
The run endpoint SHALL be the only console route that starts a loop run. The
existing endpoints' semantics are unchanged: `set-schedule` never kickstarts, and
`rounds` only pauses/resumes. The origin gate (§13.1) and the no-CORS standing rule
SHALL apply to both new routes unchanged.

#### Scenario: Cross-origin fire attempt
- **WHEN** a POST to the run endpoint carries a non-loopback `Host` or a non-JSON `Content-Type`
- **THEN** the console responds 403 before any worker starts

### Requirement: Dashboard exposes a per-loop run-now control
`dashboard/generate.py` SHALL render a run-now button inside each loop row's
existing console-controls cell, inert and hidden, for every loop — NOT gated on
installed state. Hydration (the existing `api/state` gate) SHALL unhide it; on
hydration the page SHALL fetch run status once and reflect any in-flight job. A
click SHALL POST the run endpoint, then poll status until the job ends, then reload
the page. Errors SHALL surface through the existing normalized failure path. Opened
as a plain file, page behavior SHALL remain byte-identical to a console-less
dashboard.

#### Scenario: Fire from the page
- **WHEN** the console is serving, the user clicks a loop's run-now button, and the POST returns 202
- **THEN** the button reflects the in-flight job and the page reloads after status reports the job finished

#### Scenario: Busy console
- **WHEN** the user clicks run-now while another loop's job is in flight
- **THEN** the 409 error is surfaced via the existing failure path and the page does not reload

#### Scenario: Static file open
- **WHEN** the generated dashboard is opened as `file://` with no console running
- **THEN** the run-now control never becomes visible or actionable
