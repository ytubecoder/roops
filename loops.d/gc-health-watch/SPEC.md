# gc-health-watch — intake spec

1. Purpose & stop condition

Surface failures across the Growth Console stack that already sit unread on
the data host: an OpenTwins X-agent logout or a stalled Chrome launch cycle,
Postiz publish errors or a disabled integration, and any GC-tracked routine
in `error` or `overdue`. "Done" for a given run is a verdict on the probe's
deterministic findings list; "done" for the loop is that a logout, a dead
launch cycle, or a Postiz error is not again left unread for days. It
retires only if those conditions stop being possible.

Origin: the X agent was logged out 13 of 31 August days and for a 53-hour
stretch 2026-08-31 → 09-02; a deleted Chrome ownership marker then killed
every write for 2026-09-02 with the session healthy. The agent itself wrote
both conditions into its memory files every hour. Nobody read them.

2. Agentic pattern

Human-in-the-loop, alarm only. The loop never logs in, never opens a
browser, and never writes outside the run dir. The entire action space on
the far side of any finding (a re-login, a Postiz reconnect, a scraper
restart, `ot-chrome-start.sh --finish`) is human-only. There is no
iterate-across-invocations aspiration.

3. Type & data flow (precheck gathers vs engine interprets)

`type=watchdog`, so precheck.sh IS the job (INTERFACES.md §4.1). It calls
`probe:gc-health-read` on the data host. The probe reads three sections and
emits the complete findings list; the precheck adds nothing, renders the
JSON, and exits non-zero when that list is non-empty.

- **schedules** — Growth Console `collect_schedules` via the GC venv.
  Automated rows minus exclusions produce `gc:<slug>:error|overdue`.
  Exclusions: `gc cache warmer` (its last-run lives in the dashboard
  process's memory and always reads `never` out-of-process) and any name
  matching `^ads-[a-z]+ loop$` (llm-local loop state is dead by design
  since the fleet moved to firstparty on 2026-08-23; the fleet self-reports
  there). Manual rows are context only and never produce findings.
- **opentwins** — twitter-agent memory files, daemon logs, and
  `schedule.json` under `~/.opentwins` (reddit is retired). Session, launch
  cycle, and task-ledger findings.
- **postiz** — public API integrations + posts over a 14-day window.

The engine interprets nothing factual. It writes the alarm up from PRECHECK
OUTPUT. Splitting it this way is deliberate — whether the agent is logged
out is a regex over files the agent already wrote, and that should not be
delegated to a model that might hedge it.

4. Cadence

`daily:09:15` local, right after `ads-delivery-watch` at 09:00 so the two
morning alarms arrive together. The X agent checks its own session every
hour; the only human action on the far side of any finding here is a
same-day chore, so a morning read is the useful resolution.

5. Permissions

The fleet report-only floor: `perm_fs_write=report_only`,
`perm_network=none`, `perm_local_exec=none`, `perm_remote_mutation=none`.
The probe runs unsandboxed inside the precheck, which is the job for
`type=watchdog` and is not governed by `perm_network`. The engine only
ever reads already-captured precheck text.

6. Finding identity

Ids are the probe's own `id` values, unchanged, one finding per precheck
finding line. They are durable conditions, not per-run events — the same
outage re-raises the same id every run until it clears. **Never encode
counts or dates into an id**; those change every run and would mint a new
finding daily, which is precisely the nagging this mechanism exists to
prevent.

7. Out of scope

- The Postiz push having no timer (posting has had no timer since April;
  an empty queue is not a finding).
- The GC `/schedules` ads-loop rows reading llm-local state (a GC-side
  fix, tracked separately; excluded here by the `^ads-[a-z]+ loop$` rule).
- Reddit (retired 2026-07-25).
- Any loop that logs in, opens a browser, or writes outside the run dir.
