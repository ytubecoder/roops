# INTERFACES — frozen contracts for the loops harness

> **Status: FROZEN for the initial build.** Every component is built concurrently against this
> file. If you are a builder agent and something here is wrong or under-specified, **do not
> silently deviate** — report it; the controller amends this file and notifies the other builders.
>
> Source of truth for *design intent*: `docs/HARNESS_PLAN.md` (final, plan-checked 3×; do not
> relitigate). This file is the mechanical contract that makes that plan buildable in parallel.
>
> §7 (engine flag mapping) is filled in from empirical CLI probes — treat its tables as verified
> fact, not as guesses to "improve".

## 0. Ground rules

- **Bash + Python 3 only.** No node, no external Python packages (stdlib only: `sqlite3`, `json`,
  `fcntl`, `argparse`, `urllib`, …). `jq` and `sqlite3(1)` may be used from shell.
- **All paths `$HOME`-relative.** Never hardcode `/Users/llm` or `/home/...`. Scripts resolve the
  repo root as `LOOPS_ROOT="${LOOPS_ROOT:-$HOME/projects/loops}"`; a script may also derive it from
  its own location, but the env var wins when set.
- **macOS has no `flock` and no GNU `timeout`.** Use `bin/lock.py` and the runner's own
  process-group timeout. Never call `flock`, `timeout`, `gtimeout`, `realpath -m`, `sed -i` (GNU
  form), or `date -d`.
- **Portability:** must also run on WSL/Linux later. Prefer POSIX-ish bash (`#!/usr/bin/env bash`,
  `set -euo pipefail`) and Python stdlib.
- **Everything is report/propose-only.** No component ever commits, pushes, or mutates a project
  outside `$LOOPS_ROOT`. Enforcement is by engine permissions (§5, §7), never by prompt text alone.
- **File modes:** `state/`, `reports/`, `state/runs/` are `0700`; files written into them `0600`.
- **Timestamps:** ISO-8601 UTC with `Z` suffix, second precision — `2026-07-22T14:03:11Z`.
  In bash: `date -u +%Y-%m-%dT%H:%M:%SZ`. In Python: `datetime.now(timezone.utc)`.
- **Fresh engine session per firing.** Adapters must NOT use any resume/session-continuation flag
  (`codex exec resume`, `claude --resume`, …). Cross-run memory is mechanical (findings tables +
  PRIOR FINDINGS injection, §3/§6.2) — a resumed model session would be a second, unauditable
  memory channel and grows context per firing. A `SESSION_HINT` adapter env var is explicitly out
  of v1.
- **Token discipline (design intent, not new mechanism):** precheck gating (engine never invoked
  when there is nothing to interpret) and the script→agent pattern (precheck does deterministic
  gathering for ~0 tokens; the engine only interprets capped, redacted output) are the primary
  cost controls. Per-run tokens/cost land in sqlite and surface as 7-day spend on the dashboard.

## 1. Repository layout (authoritative)

```
$LOOPS_ROOT/
  bin/run-loop.sh                  # the runner (§4)
  bin/loopctl                      # CLI (§8)
  bin/lock.py                      # fcntl lock helper (§2)
  bin/db.py                        # sqlite schema + insert/query helpers (§3)
  bin/loopconf.py                  # loop.conf parser (§5.0) — single implementation
  bin/probe                        # probe client (§14)
  bin/probe-server                 # probe forced-command server (§14)
  bin/probe_core.py                # shared probe header/env/exec/list (§14)
  bin/schedule.py                  # schedule grammar parser (§5.1)
  bin/page_envelope.py             # report-page envelope check/extract (Amendment 2, §12)
  engines/codex.sh                 # default engine adapter (§6, §7)
  engines/claude.sh                # alternate engine adapter (§6, §7)
  engines/README.md                # adapter interface spec (mirrors §6)
  contract/contract.schema.json    # tier-1 schema, single source of truth (§9)
  pagekit/kit.css                  # shared page kit (Amendment 2)
  pagekit/README.md
  pagekit/reference/               # sanitized benchmark fixture + rendered reference page
  loops.d/<name>/                  # one dir per loop: loop.conf precheck.sh prompt.md dashboard.json
  loops.d/<name>/render.sh         # OPTIONAL page renderer (Amendment 2; executable = page-enabled)
  probes/<name>                    # named, reviewed probe scripts (§14); built-ins are not files
  examples/<name>/                 # pilot loops, same shape; NEVER installed to launchd
  .env                             # gitignored; machine-local host config (§3 / §5.0)
  state/loops.sqlite               # WAL; runs, heartbeats, metrics (§3)
  state/runs/<run_id>/             # contract.json, output.md, usage.json, engine.log, engine.status
  state/locks/<loop>.lock          # lock files (§2)
  state/tmp/                       # runner TMPDIR default (§4.1)
  state/probe-log/YYYY-MM-DD.log   # probe-server audit log, 0700/0600, 30-day prune (§14)
  state/loop-data/<name>/          # loop-private durable state, 0700/0600 (Amendment 2)
  reports/<name>/YYYY-MM-DD-HHMM.md
  reports/<name>/latest.md         # atomically promoted symlink-free copy
  reports/<name>/latest.json       # atomically promoted copy of contract.json
  reports/<name>/YYYY-MM-DD-HHMM.html + latest.html   # promoted report pages (Amendment 2)
  dashboard/generate.py            # → dashboard/loops.html (§10)
  launchd/com.loops.<name>.plist   # generated; gitignored
  docs/…
```

`state/`, `reports/`, `launchd/*.plist`, `dashboard/loops.html`, `.env` are gitignored — they are
runtime / machine-local artifacts. Every script must create the directories it needs (`mkdir -p`)
rather than assuming.

## 2. `bin/lock.py` — lock helper

Replaces `flock`. Advisory, per-loop, non-blocking by default.

```
bin/lock.py acquire --name <loop> [--root $LOOPS_ROOT] [--wait-s N]  # holds until stdin closes
bin/lock.py check   --name <loop>
```

**Contract:**
- Lock file: `$LOOPS_ROOT/state/locks/<loop>.lock`, `0600`, created with `mkdir -p` on the dir.
- Uses `fcntl.flock(fd, LOCK_EX | LOCK_NB)`.
- `acquire`: on success writes the holder's pid + ISO timestamp into the file, prints `ACQUIRED`
  to stdout, then **blocks reading stdin until EOF**, then releases and exits 0. This is the
  "hold the lock for the duration of a shell block" pattern — the runner keeps the helper alive on
  a coprocess/background fd and closes it to release. On contention exits **3** and prints
  `HELD_BY <pid> <since>` to stderr.
- `--wait-s N` retries acquisition for up to N seconds (0.25s poll) before exiting 3.
- `check`: exit 0 if free, exit 3 if held (prints holder info). Never modifies the lock.
- A stale lock whose pid no longer exists must NOT block: `check`/`acquire` verify the recorded pid
  with `os.kill(pid, 0)`; if the holder is gone the lock is taken over (flock releases on process
  death anyway — this is belt-and-braces for the pid annotation, and must never crash).

Exit codes: `0` ok, `3` contention, `2` usage error.

## 3. `bin/db.py` + SQLite schema

`state/loops.sqlite`, opened with `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;` **on every
connection**. Schema is created idempotently by `db.py init` (called by the runner and loopctl on
every invocation — it must be cheap and safe to re-run).

```sql
CREATE TABLE IF NOT EXISTS runs (
  run_id        TEXT PRIMARY KEY,
  loop_name     TEXT NOT NULL,
  started_at    TEXT NOT NULL,          -- ISO8601 Z
  finished_at   TEXT,
  duration_ms   INTEGER,
  engine        TEXT,
  model         TEXT,
  trigger       TEXT,                   -- launchd | manual | kickstart
  runner_status TEXT NOT NULL,          -- §4.3 enum
  loop_status   TEXT,                   -- ok | warn | alert | NULL — engine-emitted, verbatim
  effective_status TEXT,                -- ok | warn | alert | NULL — post-suppression (§4.5); the dashboard displays THIS
  status_reason TEXT,
  headline      TEXT,
  report_path   TEXT,                   -- repo-relative
  contract_path TEXT,                   -- repo-relative
  tokens_input  INTEGER,                -- nullable
  tokens_output INTEGER,                -- nullable
  tokens_total  INTEGER,                -- nullable
  cost_usd      REAL,                   -- nullable
  usage_raw     TEXT,                   -- raw engine usage JSON, verbatim
  attempts      INTEGER,                -- engine attempts incl. transient retries (§4.6); NULL if engine not invoked
  exit_code     INTEGER,
  error_detail  TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_loop_started ON runs(loop_name, started_at DESC);

CREATE TABLE IF NOT EXISTS heartbeats (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  loop_name TEXT NOT NULL,
  run_id    TEXT,
  ts        TEXT NOT NULL,
  ok        INTEGER NOT NULL,           -- 1 = probe healthy (incl. silent-green), 0 = probe failed
  detail    TEXT
);
CREATE INDEX IF NOT EXISTS idx_hb_loop_ts ON heartbeats(loop_name, ts DESC);

-- one row per scalar metric per run; powers trend panels
CREATE TABLE IF NOT EXISTS metrics (
  run_id    TEXT NOT NULL,
  loop_name TEXT NOT NULL,
  ts        TEXT NOT NULL,
  key       TEXT NOT NULL,
  num       REAL,                       -- set when the value is a number (or bool → 0/1)
  text      TEXT,                       -- JSON-encoded value when not a plain number
  PRIMARY KEY (run_id, key)
);
CREATE INDEX IF NOT EXISTS idx_metrics_loop_key_ts ON metrics(loop_name, key, ts);

-- Amendment 1: findings memory (initial schema, NOT a migration — schema_version stays 1)
CREATE TABLE IF NOT EXISTS findings (
  finding_id     TEXT NOT NULL,
  loop_name      TEXT NOT NULL,
  title          TEXT NOT NULL,
  severity       TEXT NOT NULL,
  first_seen_run TEXT NOT NULL,
  first_seen_at  TEXT NOT NULL,
  last_seen_run  TEXT NOT NULL,
  last_seen_at   TEXT NOT NULL,
  times_seen     INTEGER NOT NULL DEFAULT 1,
  resolved_at    TEXT,               -- set when a run stops reporting it
  PRIMARY KEY (loop_name, finding_id)
);

CREATE TABLE IF NOT EXISTS dispositions (
  loop_name    TEXT NOT NULL,
  finding_id   TEXT NOT NULL,
  action       TEXT NOT NULL,        -- ack | dismiss | snooze | reopen
  note         TEXT,
  snooze_until TEXT,
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_disp_loop_finding ON dispositions(loop_name, finding_id, created_at DESC);

-- Amendment 2: loop lifecycle event audit trail (additive, NOT a migration — schema_version stays 1)
CREATE TABLE IF NOT EXISTS loop_events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  loop_name TEXT NOT NULL,
  event     TEXT NOT NULL,        -- created | imported | installed | uninstalled | paused | resumed
  actor     TEXT NOT NULL,
  ts        TEXT NOT NULL,
  detail    TEXT                  -- optional JSON blob, opaque to the schema
);
CREATE INDEX IF NOT EXISTS idx_events_loop_ts ON loop_events(loop_name, ts DESC);

CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT);
-- schema_meta('schema_version') = '1'
```

**Loop event semantics (Amendment 2 — 2026-07-30):** `loop_events` is an append-only lifecycle
audit trail, separate from `runs`/findings. `validate` records no event (audit spam); events are
kept forever (no retention pruning); orphaned events for a since-deleted loop are historical record
and are never cleaned up.

**Findings semantics (Amendment 1):** on each valid run the runner upserts the emitted findings —
increment `times_seen`, update `last_seen_*` — and marks previously-open findings for that loop
that are absent from this run as `resolved_at = now`. A finding that reappears after resolution is
a new occurrence of a known id: `times_seen` continues, `resolved_at` clears. Dispositions are
**append-only history**; current state is the latest row per `(loop_name, finding_id)`. A
`reopen` row clears the effect of a prior `dismiss`/`snooze`.

**`db.py` CLI surface** (used by `run-loop.sh`; also importable as a module):
```
db.py init [--root R]
db.py start-run   --root R --run-id ID --loop NAME --engine E [--model M] --trigger T --started-at TS
db.py finish-run  --root R --run-id ID --runner-status S [--loop-status S]
                  [--effective-status S] [--attempts N] [--status-reason X]
                  [--headline H] [--report-path P] [--contract-path P] [--exit-code N]
                  [--error-detail X] [--usage-file F] [--finished-at TS]
db.py heartbeat   --root R --loop NAME [--run-id ID] --ok 0|1 [--detail X]
db.py record-metrics --root R --run-id ID --loop NAME --contract-file F   # flattens metrics (§9.3)
db.py upsert-findings --root R --run-id ID --loop NAME --contract-file F --ts TS
                                    # findings semantics above; prints JSON summary
                                    # {"upserted":n,"resolved":n} to stdout
db.py prior-findings  --root R --loop NAME
                                    # renders the PRIOR FINDINGS injection block (§6.2): one line
                                    # per non-resolved finding — id, "seen N× since <first_seen
                                    # date>", current disposition (open / ACKED <date> /
                                    # DISMISSED <date> ("note") / SNOOZED until <date>).
                                    # Empty stdout when there are none.
db.py suppressed      --root R --loop NAME --ts TS
                                    # JSON array of objects {finding_id, action, created_at,
                                    # note, snooze_until} for findings whose current disposition
                                    # is dismiss, or snooze with snooze_until > TS (§4.5) —
                                    # the runner uses finding_id for filtering and the rest for
                                    # the human-readable suppression footer
db.py dispose         --root R --loop L --finding-id ID --action ack|dismiss|snooze|reopen
                      [--note X] [--until TS]
                                    # appends a disposition row; dismiss REQUIRES --note;
                                    # snooze REQUIRES --until; unknown (loop,finding) → exit 1
db.py record-event    --root R --loop L --event E --actor A [--detail JSON]
                                    # (Amendment 2 — 2026-07-30) appends a loop_events row;
                                    # E validated against the enum above; --detail, if given,
                                    # must be valid JSON; unknown event or invalid JSON → exit 1
db.py query <name> [args...]        # named read queries; JSON to stdout. Names:
                                    #   loops-summary                     (latest run per loop)
                                    #   last-runs   --loop L --limit N
                                    #   metric-history --loop L --key K --days D
                                    #   open-findings  --loop L
                                    #   heartbeats  --loop L --limit N
                                    #   spend       --days D              (per-loop token/cost sums)
                                    #   loop-events [--loop L] [--limit N] [--events E1,E2]
                                    #     (Amendment 2 — 2026-07-30) newest-first (ts DESC, id
                                    #     DESC); --loop omitted → all loops; --events filters to a
                                    #     comma-list of event names, applied in SQL (WHERE event IN
                                    #     (...)) BEFORE the LIMIT — combine with --limit 1 to get
                                    #     the single most-recent match without risk of it being
                                    #     pushed out of a fixed-size window by later non-matching
                                    #     events; each entry validated against the event enum above
                                    #     (unknown → exit 1 stderr, consistent with record-event)
```
The dashboard MAY use `db.py query` or read sqlite directly with its own SQL — the §3 schema is
frozen either way.
`start-run` inserts immediately (so a crashed run still leaves a row); `finish-run` updates by
`run_id` and computes `duration_ms`. Both are idempotent-safe (`INSERT OR REPLACE` / `UPDATE`).
All writes wrapped in a single transaction with `busy_timeout`.

**Metric flattening (§9.3 rule):** `metrics` is a JSON object. Top-level keys become metric keys.
Nested objects flatten with `.` (`repos.dirty` → key `repos.dirty`), arrays do **not** flatten —
an array value is stored whole in `text` as JSON. Numbers and booleans go to `num` (bool → 0/1),
everything else to `text`. Flatten depth cap: 3; keys longer than 128 chars are truncated with a
`…` suffix; a run contributing more than 200 metrics is truncated (record
`metrics_truncated=1` as an extra metric).

## 4. `bin/run-loop.sh <name>` — the runner

```
bin/run-loop.sh <loop-name> [--trigger launchd|manual|kickstart] [--from examples|loops.d] [--dry-run]
```
Default `--from loops.d`; `--trigger manual`.

### 4.1 Algorithm (exact order — the plan's atomicity guarantees depend on it)

1. Resolve root, `db.py init`, load + validate `loop.conf` (§5). Refuse to run a loop with
   `enabled=false` unless `--trigger manual` (exit 0, no run row). **(Amendment 2 — 2026-07-30, fix
   round 3)** Same guard, same shape, for `schedule=manual`: refuse unless `--trigger manual` (exit
   0, no run row). `loopctl install` already refuses to bootstrap a `schedule=manual` loop, but a
   loop's live `loop.conf` can still read `schedule=manual` while an OLDER plist from before that
   change stays bootstrapped (e.g. `loopctl import --apply --overwrite` forces `schedule=manual` for
   an acknowledged-blocked skill without touching the plist) — every launchd-triggered firing always
   arrives here as `--trigger launchd` regardless of whether launchd fired it on schedule or via an
   explicit `kickstart`, so this guard is what actually stops a credential-blocked/manual-only loop
   from running unattended in that case.
2. **Acquire lock** (§2, non-blocking). On contention: insert a run row with
   `runner_status=skipped-overlap`, `started_at=finished_at=now`, and exit **0** (an overlap is not
   an error — it must not make launchd think the job is broken).
3. `run_id` = `<UTC>-<name>-<6 hex>`, e.g. `20260722T140311Z-hello-loop-a1b2c3`. `mkdir -p 0700
   state/runs/<run_id>`. `db.py start-run`. **(Amendment 2 — 2026-07-30)** Immediately after
   `start-run`, a best-effort "running now" dashboard regen: `bin/lock.py check --name
   _dashboard` (exit 0 only when free) gates a `dashboard/generate.py` call, both `|| true`d —
   a held lock silently skips the regen and nothing here may ever block or fail the run; the
   step 7 end-of-run regen (`--wait-s 30`) is unchanged and remains the authoritative regen.
3a. **Env load + host requirements (config-only).** After `start-run` (a run row exists) and
    before precheck: load `$LOOPS_ROOT/.env` via `loopconf.py env --root --json`. Malformed →
    `runner_status=harness-error`, `status_reason=env_file_invalid`, headline/error_detail from
    the loader's first stderr line, no engine. Each key is exported only if not already set in
    the process environment; every key the file declares (set or not) is recorded as the engine
    strip list. Then `requirements.py check --root --loop --from --no-live --json`: any unmet
    item → `runner_status=precheck-failed`, `loop_status=alert`,
    `error_detail="requirement unmet: <item> — <detail>"` (first unmet item in the headline;
    all of them in `error_detail`), **no precheck, no engine**. Exit 2 from the checker is a
    harness-error. The live `bin/probe --check` path is not used here (config-only).
4. **Precheck** (`precheck.sh`, if present and executable): run with the same process-group timeout
   discipline as the engine, capped at `min(timeout_s, 300)`. stdout captured to
   `state/runs/<id>/precheck.out` with a **64 KiB cap** (truncate + append a truncation marker);
   reject binary output (NUL byte → treat as failed precheck); run a redaction pass (§4.4).
   - `type=agent`: exit 0 **and empty stdout** → `runner_status=skipped-precheck`, loop_status
     `ok`, **amber** on the dashboard, write heartbeat `ok=1`, finish, exit 0. Non-empty stdout →
     injected into the prompt (§6.2). Non-zero exit → `runner_status=engine-failed` is wrong here;
     use `skipped-precheck` with `error_detail` **only** if exit code is 0; a non-zero precheck
     exit is a real failure → `runner_status=precheck-failed`, loop_status `alert`.
   - `type=watchdog`: **the precheck IS the job**. Always write a heartbeat row — `ok=1` on exit 0
     (this is the silent-green case; it must be distinguishable from a dead scheduler), `ok=0`
     otherwise. Exit 0 → finish with `runner_status=completed`, `loop_status=ok`, headline from the
     first line of stdout or `"probe ok"`; **no engine invocation**. Non-zero exit or output
     containing a failure → escalate: proceed to step 5 with the precheck output injected, so the
     agent produces a diagnosis. The **probe failure result is sticky**: even if the diagnosis run
     fails, the stored `loop_status` stays `alert` (§4.3 precedence).
5. **Prompt assembly + engine invocation** (`type=agent`, or watchdog escalation): compose
   `PROMPT_FILE` per §6.2 — `prompt.md`, then the runner-generated `PRIOR FINDINGS` block (from
   `db.py prior-findings`, omitted when empty), then `PRECHECK OUTPUT` when present. Exec
   `engines/<engine>.sh` with the env of §6 (keys named in `.env` are unset for the adapter via
   `env -u`; precheck and render keep the exported env), inside its **own process group** (`set -m` +
   background, or `setsid` where available). Runner-owned timeout: at `timeout_s` send `TERM` to
   the **process group**, wait a 10s grace, then `KILL`. Partial `engine.log` is preserved. The
   lock is released on every path (`trap ... EXIT`). Transient failures (adapter exit 12) are
   retried per §4.6; nothing else is ever retried.
6. **Contract validation + promotion** (the stale-green guarantee):
   - Adapter must have written `state/runs/<id>/contract.json.tmp`. Runner validates it against
     `contract/contract.schema.json` via `bin/validate_contract.py` (stdlib-only validator, §9.2).
   - Invalid / missing → `runner_status=contract-violation`, `loop_status=alert`, **no promotion**.
   - Valid → `os.rename` (same filesystem, atomic) to `contract.json`; then `db.py
     upsert-findings`; then apply **suppression** (§4.5) to produce the promoted artifacts:
     extract `report_markdown` and write it as `state/runs/<id>/output.md`; copy to
     `reports/<name>/<YYYY-MM-DD-HHMM>.md` (with the §4.5 suppression footer when applicable);
     then promote `reports/<name>/latest.md` and `latest.json` (suppression-filtered, §4.5) by
     **write-tmp-then-rename** in the same directory. Promotion happens **only**
     for a run that both completed and validated — a timed-out or failed engine leaves the previous
     `latest.*` untouched. `state/runs/<id>/contract.json` always keeps the engine emission
     verbatim (audit trail).
6.5. **Loop-data commit + report page render (Amendment 2 — only for a run that promoted
   in step 6; failures in this step NEVER change runner_status, loop_status, or the exit
   code — the step-7 dashboard-failure precedent):**
   - **Loop-data commit:** every regular file in `state/runs/<id>/loop-data.commit/` is
     moved (per-file rename) into `state/loop-data/<name>/` (`0700` dir, `0600` files,
     created on demand). This is the ONLY write path into `state/loop-data/` — prechecks
     read the previous state from there but write candidates into the run dir, so a run
     that fails before promotion never consumes state (at-least-once semantics).
   - **Render:** if `loops.d/<name>/render.sh` exists and is executable, run it with cwd
     `loops.d/<name>/`, own process group, timeout `min(timeout_s, 300)` (additive to the
     engine budget; `duration_ms` includes it), env: `LOOP_NAME`, `RUN_ID`, `LOOPS_ROOT`,
     `OUT_DIR`, `LATEST_JSON` (absolute path to the promoted `reports/<name>/latest.json`),
     `LOOP_DATA_DIR` (absolute; read-only by convention), `PAGEKIT` (absolute `pagekit/`),
     `PAGE_OUT` (absolute `state/runs/<id>/page.html`). stdout+stderr →
     `state/runs/<id>/page-render.log`, capped 64 KiB, redacted via `bin/redact.py`.
   - **Promotion gate** (all via `bin/page_envelope.py check`, §12): exit 0 required —
     file exists, non-empty, UTF-8, ≤ 8 MiB, exactly one `#report-data` envelope, required
     meta fields typed and parseable, `meta.run_id` == RUN_ID, `meta.loop` == loop name,
     no-external-fetch heuristic passes, redaction-clean (redacting the page is a no-op).
   - Gate pass → promote by write-tmp-then-rename inside `reports/<name>/`: dated
     `<YYYY-MM-DD-HHMM>.html` FIRST, then `latest.html`; print
     `page promoted: reports/<name>/<dated>.html` to stdout. Gate fail / render error /
     timeout → no promotion, previous `latest.html` untouched, reason appended to
     `page-render.log`.
   - Runs that do not reach step 6.5 (skips, failures, watchdog silent-green, `--dry-run`)
     never render.
7. `db.py finish-run` (incl. `effective_status`, §4.5) + `db.py record-metrics`; then regenerate the dashboard
   (`dashboard/generate.py`) under a short global lock (`state/locks/_dashboard.lock`, `--wait-s 30`);
   a dashboard failure is logged but must **not** change the run's status or the exit code.
8. Retention: prune `reports/<name>/*` and `state/runs/*` older than `retention_days` (default 30).
   `latest.md`, `latest.json`, `latest.html` never pruned (the runner's keep-list names all three explicitly — Amendment 2). SQLite rows are kept forever.

### 4.2 Runner exit codes
`0` = the run was recorded (including skipped/overlap/precheck cases — the common path);
`1` = the harness itself failed (bad conf, unwritable state, missing engine adapter);
`2` = usage error. **A loop reporting `alert` still exits 0** — the loop's finding is data, not a
runner failure. launchd must only ever see non-zero for harness breakage.
**Clarification (settled):** `auth-failed` / `tool-denied` / `contract-violation` /
`engine-failed` / `engine-timeout` are *recorded* definitive outcomes → exit 0; the dashboard's
harness-problem marker is their surfacing mechanism. Non-zero is reserved for failures that
prevented recording a run at all (plus `harness-error`, which exits 1 after best-effort
recording).

### 4.3 Status model
- **loop_status** (from the contract): `ok | warn | alert` — the engine emission, stored verbatim.
- **effective_status** (§4.5): loop_status after suppression — this is what the dashboard colours
  green / amber / red.
- **runner_status**, written by the runner: `completed | skipped-precheck | skipped-overlap |
  precheck-failed | engine-failed | engine-timeout | auth-failed | tool-denied |
  contract-violation | harness-error`. Derived by the dashboard only, never stored:
  `missed-schedule`, `stale`, `died` (§4.6).
- **Precedence for the displayed light:** any runner_status other than `completed` /
  `skipped-precheck` outranks effective_status and renders red — **except** `skipped-overlap`,
  which renders amber. `skipped-precheck` renders amber. `auth-failed` / `tool-denied` /
  `contract-violation` / `harness-error` render red with a distinct "harness problem" marker: they
  mean *fix the harness*, not *the loop found something*.
- **Watchdog stickiness:** if the probe failed (`heartbeat.ok=0`), the run's `loop_status` AND
  `effective_status` are `alert` regardless of what the diagnosis engine returns and regardless of
  suppression; the diagnosis's own failure is recorded in `runner_status` and `error_detail`.

### 4.5 Findings suppression + effective status (Amendment 1 — mechanical, post-validation)

The runner — never the model — filters findings whose current disposition is `dismiss`, or
`snooze` with `snooze_until > now` (from `db.py suppressed`):

- **Promoted `latest.json`**: the `findings` array contains unsuppressed findings only. The
  verbatim emission stays in `state/runs/<id>/contract.json`.
- **Promoted markdown** (`latest.md` + dated report): `report_markdown` is model prose and cannot
  be reliably excised, so it is promoted verbatim with a runner-appended footer —
  `---` + `Suppressed by disposition: <id> (dismissed 2026-06-01 "note"), …` — when any finding
  was suppressed. Machine-read surfaces (dashboard, `latest.json`, sqlite) are the authoritative,
  filtered views; the dashboard renders findings from sqlite + `latest.json` and NEVER parses
  markdown.
- **Effective status rule:** if the emitted `findings` array is **non-empty**, `effective_status`
  = max severity of the *unsuppressed* findings (`info`→`ok`, `warn`→`warn`, `alert`→`alert`; all
  suppressed → `ok`). If `findings` is **empty**, `effective_status = loop_status` (covers
  watchdog probes and loops without itemized findings). Watchdog probe-failure stickiness (§4.3)
  outranks this rule. Both values are stored on the run row.

### 4.6 Engine-error handling

- **Transient retry.** Adapters classify failures (§6.4): exit `12` = transient (HTTP 429/5xx,
  network unreachable/reset, provider "overloaded"). The runner retries **only** exit-12 failures,
  in-run: up to `retry_transient` attempts (§5, default 1, max 3), backoff 30s then 120s, all
  inside the run's `timeout_s` budget (if the budget would be exceeded, stop retrying). Every
  attempt is appended to `engine.log`; the attempt count is stored in `runs.attempts`. Retries
  exhausted → `runner_status=engine-failed` with the transient classification in `error_detail`.
  Auth (10), tool-denied (11), other (1), and contract violations are NEVER retried — they mean
  *fix the harness*.
- **`harness-error` catch-all.** Any unexpected runner error is trapped; the trap releases the
  lock and finishes the run row with `runner_status=harness-error` + traceback/context in
  `error_detail`, then exits 1. Harness bugs must surface on the dashboard, not only in launchd
  stderr files.
- **Died-run detection (derived, never stored).** `start-run` inserts immediately, so SIGKILL /
  power loss leaves a row with `finished_at IS NULL`. The dashboard renders such rows older than
  `timeout_s + 120s` grace as `died` (red, harness-problem marker), same family as stale
  detection.

### 4.4 Redaction pass
Applied to precheck output and to `engine.log` before they are written. Regex-replace with
`«redacted:<kind>»`, case-insensitive:
- `gh[pousr]_[A-Za-z0-9]{20,}` (GitHub tokens), `sk-[A-Za-z0-9_-]{20,}`, `xox[baprs]-[A-Za-z0-9-]{10,}`
- `AKIA[0-9A-Z]{16}`, `-----BEGIN [A-Z ]*PRIVATE KEY-----` … block
- lines matching `(?i)(api[_-]?key|secret|password|token|authorization)\s*[:=]\s*` → keep the key
  name, redact the REST OF THE LINE (multi-token values like `Bearer <jwt>` must not survive).
- `eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]+` (bare JWTs).
Implemented once in `bin/redact.py` (usable as filter: stdin → stdout) and used by both the runner
and the adapters. Redaction is best-effort defence in depth, never the primary control.

## 5. `loop.conf` — format and fields

### 5.0 `bin/loopconf.py` — the single parser
One implementation, used by everything: `parse(path) -> (conf: dict, errors: list[str])` applies
defaults and type/range checks (grammar + field table below; unknown keys are errors). CLI:
`loopconf.py parse --file F --json` (full dict + errors; exit 1 if errors) and
`loopconf.py get --file F --key K` (resolved value incl. defaults; empty + exit 1 if unknown).
Also `loopconf.py env --root R [--json]` — prints `$LOOPS_ROOT/.env` as JSON (or `KEY=value`
lines); missing file → `{}`; malformed → exit 1 with `{path}:{lineno}: {msg}` on stderr.
`load_env(root) -> dict` is the importable form. Env keys match `ENV_KEY_RE` =
`^[A-Z][A-Z0-9_]*$` (upper-case; `parse()` is not reused — its `KEY_RE` is lower-case). Max 64
keys; duplicate key is an error. `$HOME`/`~` expand in every value. `run-loop.sh` reads config
via `loopconf.py get`/`parse --json` and host env via `env --json`; `loopctl` and
`dashboard/generate.py` import it. Dangerous-combo checks (§5.2) live in `loopctl validate`, not
in the parser.

**Strict `KEY=value` grammar — the file is NEVER `source`d** (it is parsed by both bash and
Python, and sourcing arbitrary files is a code-execution footgun):
- One `KEY=value` per line. `KEY` matches `^[a-z][a-z0-9_]*$`.
- `#` starts a comment at line start or after whitespace; blank lines ignored.
- Value may be bare (no spaces) or double-quoted (`"..."` with `\"` escape). **No expansion of any
  kind** except a literal leading `$HOME` or `~` in path-typed fields, which is expanded at parse.
- Unknown keys → `loopctl validate` **fails** (catches typos rather than ignoring them).

| key | required | type / allowed values | default | notes |
|---|---|---|---|---|
| `name` | yes | `^[a-z][a-z0-9-]{1,40}$` | — | must equal the directory name |
| `description` | yes | string | — | one line, shown on the dashboard |
| `owner` | no* | `^[a-z][a-z0-9-]{1,40}$` | — (resolved `loops`, flagged assumed) | B-17 (2026-08-03): the project/process this loop belongs to. *Required-but-assumed: absence is NEVER a hard failure; every surface resolves it via `loopconf.resolve_owner()` (below). A present-but-malformed value IS a parse error like any field. |
| `type` | yes | `agent` \| `watchdog` | — | watchdog ⇒ `precheck.sh` required |
| `engine` | yes | `codex` \| `claude` | — | must have `engines/<engine>.sh` |
| `model` | no | string | engine default | passed through as `MODEL` |
| `schedule` | yes | §5.1 grammar | — | `manual` = never installed; runner also refuses to run it except `--trigger manual` (Amendment 2 — 2026-07-30, fix round 3), covering an already-bootstrapped plist left behind by an earlier, non-manual schedule |
| `workdir` | no | path | `$LOOPS_ROOT` | engine's working root |
| `timeout_s` | no | int 30–7200 | `900` | runner-owned, process-group |
| `enabled` | no | `true` \| `false` | `true` | false ⇒ only `--trigger manual` runs |
| `retention_days` | no | int 1–3650 | `30` | reports + run artifacts |
| `retry_transient` | no | int 0–3 | `1` | in-run retries for adapter exit 12 ONLY (§4.6) |
| `perm_fs_write` | no | `none` \| `report_only` \| `workdir` | `report_only` | §5.2 |
| `perm_network` | no | `none` \| `full` | `none` | §5.2 |
| `perm_local_exec` | no | `none` \| `allowlist` \| `full` | `none` | §5.2 |
| `perm_remote_mutation` | no | `none` \| `allowlist` | `none` | §5.2 |
| `exec_allowlist` | cond | quoted comma-separated command patterns | — | required when `perm_local_exec=allowlist` or `perm_remote_mutation=allowlist` |
| `credential_env` | no | comma-separated env var names | — | RESERVED — not implemented in v1: `loopctl validate` hard-fails a non-empty value (real passthrough needs a launchd-env design; do not fake it) |
| `remote_mutation_justification` | cond | string | — | **required** when `perm_remote_mutation != none` |
| `notes` | no | string | — | free text |
| `tags` | no | comma-separated, each `^[a-z][a-z0-9:_-]{1,40}$`, deduped order-preserving, max 8 | — | grouping/filtering only; exact-match filter (Amendment 2 — 2026-07-30) |
| `requires` | no | comma list of `kind:value`, max 16, kinds `os`/`bin`/`file`/`env`/`probe` | — | host needs (§5.3). Required-but-assumed: absence = portable, never an error. `os:` value must be `darwin` or `linux`. Deduped, order-preserving. |

**Owner resolution (B-17 — 2026-08-03).** `bin/loopconf.py` exports `DEFAULT_OWNER = "loops"` and
`resolve_owner(conf) -> (owner, assumed)`: an explicit owner passes through (`assumed=False`); a
missing one resolves to `DEFAULT_OWNER` (`assumed=True`). This is the ONLY implementation of the
rule — `loopctl` calls it directly; `dashboard/generate.py` carries a lockstep mirror (it must run
against roots where `bin/loopconf.py` doesn't exist — its lazy-seam doctrine) pinned by a drift
test in `tests/test_dashboard.py`, the same canonical-copy pattern as the §12 token blocks.
Owner is a label, not a path: nothing verifies it against the filesystem (portability), and no
`loop_events` row records owner changes — `loops.d/` is git-tracked; git history is the audit
trail (same as `set-schedule`). `loopctl new` and `import --apply` always stamp an explicit
`owner=` (default `DEFAULT_OWNER`, `--owner` flag on both), so tooling-scaffolded loops never
land assumed; `loopctl validate` surfaces assumed owners as non-fatal notices (§8).

### 5.1 Schedule grammar
| form | meaning | launchd | systemd | expected interval (staleness) |
|---|---|---|---|---|
| `manual` | never scheduled | — (install refuses) | — (`{}`) | ∞ |
| `interval:15m` / `interval:2h` | every N | `StartInterval` (seconds) | `OnUnitActiveSec=<N>s` | N |
| `daily:07:30` | every day at local 07:30 | `StartCalendarInterval{Hour,Minute}` | `OnCalendar=*-*-* 07:30:00` | 24h |
| `times:07:30,19:30` | those local times daily | array of `StartCalendarInterval` | list of `OnCalendar=` | 86400/count |
| `weekly:mon:08:00` | that weekday, local | `+Weekday` (0=Sun … 6=Sat) | `OnCalendar=Mon *-*-* 08:00:00` | 7d |
| `monthly:01:09:00` | that day-of-month, local | `+Day` | `OnCalendar=*-*-01 09:00:00` | 30d |
All calendar times are **local**, matching launchd semantics; systemd `OnCalendar=` is local by
default, so neither emitter converts a timezone. Parsing lives in one place:
`bin/schedule.py` (`parse(spec) -> {kind, launchd: {...}, systemd: {...}, expected_interval_s: int}`),
imported by `loopctl` and `dashboard/generate.py`. **launchd sleep semantics** (document, don't
fight): calendar events missed while asleep coalesce into a single firing at wake; `StartInterval`
firings during sleep are simply missed. "Next run" on the dashboard is therefore explicitly
best-effort.

**Amendment 2026-08-22 — the `systemd` emitter (B-24).** `parse()` gained a `systemd` key beside
`launchd`, for the Linux install backend (§8.1). The grammar itself is unchanged: no `loop.conf`,
no `SPEC.md`, and no existing `launchd` output moves, so the macOS fleet is untouched. Two rules
bind anything consuming it:
- **The dict carries the schedule and nothing else.** First-fire and catch-up policy
  (`OnBootSec=`, `Persistent=`) belongs to the unit writer, not to the grammar. This matters:
  a timer carrying only `OnUnitActiveSec=` **never fires until the service has run once**, so the
  install backend must supply the initial trigger itself. launchd's `StartInterval` has no such
  gap — the semantics genuinely differ, and the difference is not the parser's to paper over.
- **Weekdays are names in systemd and indices in launchd.** `WEEKDAYS` and `SYSTEMD_WEEKDAYS` sit
  next to each other in `bin/schedule.py` for that reason; change one and you are changing both.
  Sunday is where an off-by-one would hide (launchd `0`, systemd `Sun`).
The emitted forms were validated against a real `systemd-analyze calendar` on `firstparty`
(2026-08-22): all seven normalize to themselves.

### 5.2 Permission axes — semantics
Four independent axes. Defaults are the report-only floor: `report_only / none / none / none`.
- **`perm_fs_write`** — `none`: engine may not write anywhere; `report_only`: may write only inside
  its `state/runs/<run_id>` dir (the contract/report path); `workdir`: may write inside `workdir`
  (requires justification at review time; no loop in the initial fleet uses it).
- **`perm_network`** — `full` permits outbound network. Network access does **not** imply the right
  to mutate anything remote.
- **`perm_local_exec`** — `none`: no shell commands; `allowlist`: only commands matching
  `exec_allowlist`; `full`: unrestricted local commands (still bounded by `perm_fs_write`).
- **`perm_remote_mutation`** — the right to change remote state (push, open PRs, post, spend money).
  `none` is the fleet default and is enforced by allowlist + read-only credentials, not by prompt.

**Dangerous-combination checks (`loopctl validate` — HARD FAIL, not warnings):**
1. `perm_network=full` **and** `perm_local_exec != none` **and** `exec_allowlist` empty/absent.
2. `perm_remote_mutation != none` without a non-empty `remote_mutation_justification`.
3. `perm_local_exec=full` **and** `perm_network=full` (no loop needs this; requires an explicit
   config override key `i_accept_unrestricted=true` to pass).
4. An `exec_allowlist` entry naming a remote-capable CLI in a mutating form — the pattern must be
   command-scoped, not bare. Reject a bare tool name (`gh`, `git`, `npm`, `curl`, `aws`, `gcloud`,
   `vercel`, `wrangler`, `supabase`) and reject entries whose first two tokens are a known mutating
   verb (`gh pr create`, `gh release`, `git push`, `npm publish`, `aws … delete`, …). Accept
   scoped read forms (`gh run list`, `gh api -X GET`, `git status`, `npm outdated`).
5. `perm_fs_write=workdir` without `notes` explaining why.
6. `type=watchdog` without an executable `precheck.sh`; `schedule` unparseable; `engine` adapter
   missing; `name` ≠ directory name.
7. `engine=codex` **and** `perm_network=full` **and** `perm_fs_write != workdir` — codex cannot
   grant network under a read-only sandbox (§7.2); the combo is a config contradiction, not a
   softer sandbox.

### 5.3 Host requirements
Optional `requires=` on `loop.conf`. Grammar: item `^(os|bin|file|env|probe):[^,\s]+$`, max 16,
deduped. Unknown kind → parse error. Absence means portable.

Single implementation: `bin/requirements.py`. `check(root, conf, *, live, env=None) ->
list[(item, ok, detail)]`. CLI: `requirements.py check --root R --loop NAME [--from loops.d]
[--no-live] [--json]` (exit 0 all ok, 1 any unmet, 2 usage/parse/malformed `.env` / loop not
found). `env` is `{**os.environ` with `.env` applied where unset`}; the CLI builds that itself.

| kind | ok when | live vs config-only |
|---|---|---|
| `os:X` | `sys.platform.startswith(X)` (`linux` matches `linux*`) | either |
| `bin:NAME` | `shutil.which(NAME, path=runtime_path(home))` — the PATH a unit would get, not the caller's shell | either |
| `file:PATH` | `$HOME`/`~` expanded; `isfile` and readable | either |
| `env:KEY` | `KEY` non-empty in the merged env | either |
| `probe:NAME` | **live** (`install` / `validate` / `loopctl requirements` default): `[<root>/bin/probe, "--check", NAME]` exit 0 (30 s, cwd=root); missing `bin/probe` → unmet `bin/probe missing`. **config-only** (`run-loop.sh`, `--no-live`): never touches the network. Local (no `LOOPS_PROBE_HOST`): `probes/NAME` is an executable file. Remote (`LOOPS_PROBE_HOST` set): the key file (`LOOPS_PROBE_KEY` or `~/.ssh/loops-probe`) is a readable file. | live may use the network; config-only must not |

Enforcement:
- `loopctl validate` — live check; unmet items are a **notice**, never an exit-code failure.
- `loopctl install` — refuses (live) after validate and **before** the run-first precondition, one line per unmet item: `refusing to install <name>: requirement unmet — <item> (<detail>)`. Same refusal, verb swapped, for `resume` and `set-schedule` when the new spec is not `manual` (both would arm a timer). `pause` and `set-schedule manual` do not refuse.
- `run-loop.sh` — config-only check after `.env` load, before precheck; unmet → `precheck-failed` / `alert`, no precheck, no engine.
- `loopctl requirements [<name>…] [--json] [--no-live]` — fleet × requirement matrix for this host; zero names = every loop in `--from`; exit 1 if any in-scope loop is unmet.

`runtime_path(home)` lives in `bin/requirements.py`; `loopctl` aliases it as `_runtime_path`.

## 6. Engine adapter interface

`engines/<engine>.sh` is invoked by the runner as `engines/<engine>.sh` with **no arguments**;
everything arrives via environment. The adapter must be a thin, dumb translator: it maps the
permission axes to that CLI's flags, runs it, and writes four files. It contains no loop logic.

### 6.1 Input environment
| var | meaning |
|---|---|
| `LOOP_NAME` | loop name |
| `RUN_ID` | run id |
| `LOOPS_ROOT` | repo root |
| `WORKDIR` | engine working root (absolute, exists) |
| `PROMPT_FILE` | absolute path to the fully-composed prompt (§6.2) |
| `OUT_DIR` | absolute `state/runs/<run_id>` (exists, 0700) |
| `TIMEOUT_S` | advisory; the **runner** owns enforcement — the adapter must not add its own |
| `SCHEMA_FILE` | absolute path to `contract/contract.schema.json` |
| `MODEL` | may be empty ⇒ engine default |
| `PERM_FS_WRITE`, `PERM_NETWORK`, `PERM_LOCAL_EXEC`, `PERM_REMOTE_MUTATION` | axis values (§5.2) |
| `EXEC_ALLOWLIST` | comma-separated patterns, possibly empty |
| `LOOP_TYPE` | `agent` \| `watchdog` |

Keys named in `$LOOPS_ROOT/.env` are unset for the adapter (`env -u` on the child). Precheck and
render keep the exported env.

### 6.2 Prompt composition (runner-side, engine-neutral)
`PROMPT_FILE` = `loops.d/<name>/prompt.md`, followed ALWAYS by the run-context block (the engine
has no other way to learn the `run_id` it must echo — discovered in live pilot verification,
2026-07-22):
```

---
## RUN CONTEXT
(generated by the runner)

run_id: <RUN_ID>   ← copy this exact value into the contract's "run_id" field
```
followed — when `db.py prior-findings` produced output — by:
```

---
## PRIOR FINDINGS
(generated by the runner — authoritative; do not recompute)

```text
<output of db.py prior-findings, e.g.:
cookingapp:no-remote   seen 12× since 2026-05-04   DISMISSED 2026-06-01 ("intentional, local scratch repo")
stuntsclone:unpushed   seen 3× since 2026-07-08    open
claude-quality:no-remote  seen 9× since 2026-05-04  SNOOZED until 2026-09-01>
```
```
followed — when precheck produced output — by:
```

---
## PRECHECK OUTPUT
(deterministic gate output; treat as ground truth for this run)

```text
<captured, capped, redacted precheck stdout>
```
```
The runner appends nothing else. Contract instructions live in the loop's `prompt.md` (seeded by
`loopctl new`), because they are engine-neutral by design. The seeded template includes the three
**findings prompt-contract rules** every loop keeps:
1. Re-emit a still-true finding with its **same `finding_id`** — never invent a new id for a
   recurring condition.
2. Do not re-argue a `DISMISSED` finding unless the underlying situation has **materially
   changed**; if it has, say what changed.
3. Still emit `SNOOZED` findings if true — suppression is the runner's job, not the model's.

### 6.3 Output files (all inside `OUT_DIR`)
| file | required | content |
|---|---|---|
| `contract.json.tmp` | yes on success | exactly the engine's schema-conforming final message — a single JSON object, nothing else. The **runner** validates and renames it. |
| `usage.json` | best-effort | raw usage/telemetry as emitted by the CLI, verbatim; absent or `{}` if unavailable |
| `engine.log` | yes | the CLI's stdout+stderr (redacted via `bin/redact.py`) |
| `engine.status` | yes | one line: `status=<ok\|auth-failed\|tool-denied\|transient\|engine-failed> exit=<n>` |

### 6.4 Adapter exit codes
`0` success · `10` auth/credential failure · `11` a required tool was denied by the permission layer
· `12` transient failure (HTTP 429 rate-limit, 5xx, network unreachable/reset, provider
"overloaded" — the only class the runner retries, §4.6) · `1` any other engine failure. The runner
maps these to `completed` / `auth-failed` / `tool-denied` / (`engine-failed` after retries) /
`engine-failed`, and maps its own timeout kill to `engine-timeout`. The classification is recorded
in `engine.status` and, on failure, in the run row's `error_detail`.

## 7. Engine flag mapping (VERIFIED — see `docs/ENGINE_PROBES.md`)

> Filled in by the controller from live CLI probes on 2026-07-22 (`codex-cli 0.144.3`,
> `Claude Code 2.1.217`). Builders: use these exact forms.

### 7.1 Shared facts
- **One schema for both.** `codex --output-schema` uses OpenAI *strict* structured outputs:
  every object needs `additionalProperties:false` and all properties in `required`; free-form
  objects are rejected with a 400 (verified). Claude accepts that same strict subset, so
  `contract/contract.schema.json` is written to it. Consequence: **`metrics` is a JSON-string
  field** (§9.1), parsed by the runner. `enum` on strings and integers is verified OK on both.
- **Prompt via stdin.** codex: prompt arg + open stdin makes it read both — always
  `codex exec … - < "$PROMPT_FILE"`. claude: `claude -p … < "$PROMPT_FILE"` equally avoids
  ARG_MAX/quoting.
- **Fresh session flags (§0):** codex `--ephemeral`; claude `--no-session-persistence`. Never
  `resume`/`--resume`/`--continue`.
- **Minimal launchd env:** `HOME`, `PATH` ⊇ `/opt/homebrew/bin` + `$HOME/.local/bin`,
  `LOOPS_ROOT`. codex auth: `~/.codex/`; claude auth: keychain/`~/.claude` (kickstart-verify
  §8.1 is what proves it works under launchd).

### 7.2 codex adapter (`engines/codex.sh`)
```
codex exec --skip-git-repo-check --ephemeral -C "$WORKDIR" \
  -s <sandbox> [-c sandbox_workspace_write.network_access=true] [--add-dir …] \
  --output-schema "$SCHEMA_FILE" -o "$OUT_DIR/last-message.json" --json \
  ${MODEL:+-m "$MODEL"} - < "$PROMPT_FILE"
```
| axes | flags |
|---|---|
| `perm_fs_write=none|report_only` (floor) | `-s read-only` (the CLI itself writes `-o`; the model gets no write access — `report_only` needs nothing writable) |
| `perm_fs_write=workdir` | `-s workspace-write` (workdir = `-C` target) |
| `perm_network=full` | requires workspace-write + `-c sandbox_workspace_write.network_access=true` (key verified via `--strict-config`). **On codex this means `perm_network=full` is only valid with `perm_fs_write=workdir`** — the network key is ignored under a read-only sandbox. The adapter HARD-FAILS (exit 1, clear engine.status detail) on `PERM_NETWORK=full` with a non-workspace-write sandbox rather than silently no-opping the grant or silently widening writes; `loopctl validate` rejects the combo earlier for `engine=codex` (§5.2 check 7) |
| `perm_local_exec` | commands always run inside the selected sandbox; `allowlist` on codex is enforced by sandbox + credential scoping, not a flag (documented limitation — validate's dangerous-combo rules assume this) |
- Success: exit 0; `-o` file = exactly the final JSON object → copy to `contract.json.tmp`.
- Usage: JSONL `turn.completed` event `.usage` = `{input_tokens, cached_input_tokens,
  output_tokens, reasoning_output_tokens}` → `usage.json`; **no cost field** (`cost_usd` NULL).
- Failure: exit 1 + `error`/`turn.failed` events; no `-o` file. Classify from the `error`
  message: auth → 10; 429/5xx/network/overloaded → 12; else 1. Sandbox denials produce no
  distinct signal (11 is claude-primary).
- Empty `MODEL` ⇒ omit `-m` (config default, currently `gpt-5.5`).

### 7.3 claude adapter (`engines/claude.sh`)
```
claude -p --output-format json --json-schema "$(cat "$SCHEMA_FILE")" \
  ${MODEL:+--model "$MODEL"} --tools <set> [--allowedTools …] [--add-dir …] \
  --setting-sources "" --strict-mcp-config --no-session-persistence \
  --disable-slash-commands < "$PROMPT_FILE"
```
| axes | flags |
|---|---|
| floor (`report_only/none/none/none`) | `--tools ""` — no tools at all; pure interpretation (the adapter writes all files, the model none) |
| read-only exploration (loop-approved) | `--tools "Read,Glob,Grep"` + `--add-dir <scope>` |
| `perm_local_exec=allowlist` | `--tools "Bash"` + one `--allowedTools "Bash(<pattern>)"` per `EXEC_ALLOWLIST` entry; in `-p` mode un-allowed tool calls fail (nobody to ask) and land in `permission_denials` |
| `perm_network` | no dedicated flag — governed by which tools are exposed (no Bash/WebFetch ⇒ no network); `full` requires local_exec allowing a network-capable command |
- `--json-schema` takes the schema as a **JSON string** (verified) — pass file content.
- Success: exit 0; stdout = one JSON object; adapter extracts **`structured_output`** (parsed,
  schema-conformant — verified) → `contract.json.tmp`; whole object → `usage.json`
  (`total_cost_usd` is real — verified 0.018568 on the probe; also `usage`, `modelUsage`,
  `permission_denials`, `session_id`, `num_turns`).
- Failure: `is_error:true` / `subtype != "success"` / non-zero exit; `api_error_status` carries
  HTTP status. Classify: 401/403 → 10; 429/5xx/overloaded → 12; missing `structured_output`
  with non-empty `permission_denials` → 11; else 1.
- **Never pass `--dangerously-skip-permissions`** (the user's interactive alias adds it;
  scripts bypass aliases — invoke the plain binary).

## 8. `bin/loopctl` — CLI surface

```
loopctl                                                               # (Amendment 2 — 2026-07-30) bare, no verb: same
                                                                      #   as `status` — leading fleet line + per-loop
                                                                      #   table; exit 0 (content-first, not usage)
loopctl new <name> [--type agent|watchdog] [--engine codex|claude]   # scaffold from templates; --owner stamps an
    [--owner OWNER]                                                  #   explicit owner= (default `loops`; B-17)
loopctl validate [<name>|--all]                                      # §5 + §5.2 checks; exit 1 on any fail. B-17:
                                                                      #   assumed owner → non-fatal `note:` line
                                                                      #   (table) / `notices` list (--json rows are
                                                                      #   now {ok, errors, notices}); notices never
                                                                      #   touch the exit code
loopctl run <name> [--trigger manual]                                # foreground; streams progress
loopctl list [--tag TAG] [--owner OWNER]                             # table: name, owner, type, engine, schedule,
                                                                      #   enabled, installed?, tags (--tag: exact-match
                                                                      #   filter, Amendment 2 — 2026-07-30; --owner:
                                                                      #   exact match on the RESOLVED owner, B-17;
                                                                      #   filters compose); 0 loops prints
                                                                      #   "0 loops (<from-dir> empty)" (Amendment 2);
                                                                      #   a non-empty fleet with zero filter matches
                                                                      #   prints "0 loops matching <active filters>
                                                                      #   (N loops under <from-dir>)" instead — never
                                                                      #   the genuinely-empty message (fix round 3;
                                                                      #   B-17 extends it to name every active filter)
loopctl status [<name>]                                              # leading "fleet: N loops · ok X · warn Y ·
                                                                      #   alert Z · needs_attention W · spend7d $S"
                                                                      #   line (Amendment 2 — 2026-07-30), then last
                                                                      #   run/status/headline/next-run per loop
loopctl install <name>                                               # generate plist → bootstrap → kickstart-verify (§8.1)
loopctl uninstall <name>                                             # bootout + remove plist
loopctl pause <name> / resume <name>                                 # sets enabled= and bootout/bootstrap
loopctl set-schedule <name> <spec>                                   # §5.1-validate; rewrite conf; re-render+reload plist iff installed; NEVER kickstart; best-effort dashboard regen
loopctl set-owner <name> <owner>                                     # B-17: owner-grammar-validate BEFORE any write; rewrite conf key; best-effort dashboard regen; no launchd work (owner isn't in the plist), no loop_events row
loopctl dashboard                                                    # regenerate + print path
loopctl serve [--port PORT] [--allow-host HOST ...]                  # local console (§13), default port 8929; --allow-host extends the §13.1 Host allowlist (trusted proxy)
loopctl console [install|uninstall|status]                           # console service: generate+bootstrap the persistent unit (launchd label com.roops.console / systemd loops-console.service); status = unit present + loaded + GET /api/state 200
loopctl findings <loop>                                              # open findings: id, severity, age, times_seen, disposition
loopctl ack <loop> <finding_id> [--note …]                           # Amendment 1 disposition verbs —
loopctl dismiss <loop> <finding_id> --note …                         #   note REQUIRED (audit trail)
loopctl snooze <loop> <finding_id> --until YYYY-MM-DD                #   --until REQUIRED
loopctl reopen <loop> <finding_id>
loopctl import <skill-path> --analyze [--json]                       # Amendment 2 — 2026-07-30:
loopctl import <skill-path> --apply [--answers F] [--name N]         #   static gap analysis of an
    [--owner OWNER] [--overwrite]                                    #   existing Agent Skill /
                                                                      #   scaffold a loop from it —
                                                                      #   see docs/SKILL_IMPORT.md
loopctl requirements [<name>…] [--json] [--no-live]                  # host × requirement matrix
                                                                      #   (§5.3); zero names = every
                                                                      #   loop in --from; exit 1 if
                                                                      #   any in-scope loop unmet
loopctl probe [status|keygen]                                        # probe channel (§14); status
                                                                      #   is the default; never writes
                                                                      #   (keygen writes only the local
                                                                      #   key pair). Known verb.
```
Disposition verbs are thin wrappers over `db.py dispose` (+ dashboard regen so the change is
visible immediately). The dashboard stays static (Change 4, Option A — settled with generalissimo
2026-07-22): dispositions enter via this CLI only.
Global flags: `--root R` (default `$LOOPS_ROOT`), `--json` (machine-readable output where sensible),
`--from loops.d|examples`, `--actor A` (default `$USER`, or `unknown` if unset — Amendment 2 —
2026-07-30). Exit codes: `0` ok · `1` operation failed · `2` usage — **except** a bare, verb-less
invocation (Amendment 2 — 2026-07-30, content-first): that is no longer a usage error, it dispatches
to `status` and exits `0`. Everything else that used to be a usage error still is, at `2`: an
unrecognized verb (e.g. `loopctl frobnicate`), genuinely unrecognized arguments regardless of
whether a verb was given (fix round 2 — 2026-07-30: this check now runs unconditionally, before the
bare-invocation branch, not after it), and (fix round 2) a verb-less parse where a raw argv token
exactly matches a known verb name — meaning a preceding `--root`/`--actor`/`--from` almost certainly
swallowed it as that flag's value instead of it ever reaching the verb position (e.g. `loopctl
--actor status`) — refused as "ambiguous invocation" rather than silently defaulting to the default
root at exit `0`. `--help` is unaffected — it still prints usage and exits `0` without dispatching
anywhere.

**Bare invocation (Amendment 2 — 2026-07-30, fix round 1 — 2026-07-30):** `loopctl` and `loopctl
--root R` (no verb, in any flag placement) call the exact same code path as `loopctl status` — same
leading fleet line, same per-loop table/JSON, same exit 0.
  Mechanically, `main()`'s top-level parser `p` carries a *hidden* copy of `--root`/`--json`/
`--from`/`--actor` (real defaults, `help=argparse.SUPPRESS`) so a verb-less invocation still has
real values for them (`argparse`'s `dest="verb"` yields `None` before any subparser ever runs) and
`loopctl --help` stays unchanged (these four never appear in it — only per-verb `--help`, e.g.
`loopctl status --help`, shows them, exactly as before). Every subparser's own copy uses
`default=argparse.SUPPRESS` instead of a real default (fix round 1 — **verified Critical**: giving
both `p` and every subparser a REAL default for the same flags let a valid verb invocation with
flags placed *before* the verb — `loopctl --root R status --json` — silently resolve to the WRONG
root: argparse's `_SubParsersAction.__call__` parses the chosen subparser's trailing tokens into a
**fresh** sub-namespace, then unconditionally copies every key from it onto the outer namespace —
if the subparser's own `--root` wasn't repeated after the verb, that fresh sub-namespace's default
value silently overwrote `p`'s already-correct one, with no warning, at exit 0). `default=SUPPRESS`
on the subparser's copy means an unrepeated flag is simply absent from the sub-namespace, so the
clobbering copy loop never touches it and `p`'s resolved value survives; a flag that IS repeated
after the verb (the pre-existing, still most common convention — `status --root R`) is parsed for
real by the subparser and correctly wins, same as before. Both flag placements are equivalent for
all four flags — verified in `TestGlobalFlagPlacement`.
  **Fix round 2 — the swallowed-verb case:** an `extra`-free parse with `args.verb is None` isn't
always a genuine bare invocation — `--root`/`--actor`/`--from` each take a value, so a verb token
placed right after one of them (with nothing following it) is silently consumed AS that value
instead of ever reaching the subparsers positional: `loopctl --actor status` parses cleanly as
`actor="status"`, `verb=None`, `extra=[]` — nothing for the "unrecognized arguments" check to catch,
yet the intended `status` verb vanished, defaulting the root at exit `0`. Indistinguishable from a
genuinely-intended literal value by parsing alone, so `main()` treats any raw argv token that
exactly matches a known verb name (once `args.verb is None`) as a near-certain mistake and refuses
loudly (`"ambiguous invocation: '<token>' looks like a verb but was consumed as a flag's value"`,
exit `2`) rather than silently defaulting. `--flag=value` syntax (e.g. `--actor=status`) is the
escape hatch — it's a single argv token, never equal to a bare verb name, so a genuinely-intended
literal value survives. Covers `loopctl --root-dir=/sandbox` (a typo'd flag name — caught by the
hoisted `extra` check instead, same fix), `loopctl --actor status`, and `loopctl --root status`.

**`status` aggregates + blanking fix + `in_flight` (Amendment 2 — 2026-07-30, fix round 1 —
2026-07-30):** `status` (with or without `<name>`) prints a leading line before anything else:
`fleet: N loops · ok X · warn Y · alert Z · needs_attention W · spend7d $S`. `N` is the loop count
under `--from` (`loops.d` by default). `spend7d` sums `cost_usd` across `db.py query spend --days
7`. `--json` wraps the existing per-loop rows in an envelope: `{"fleet": {"loops", "ok", "warn",
"alert", "needs_attention", "spend7d"}, "loops": [...]}` — this applies whether or not `<name>` was
given. Table form is unchanged below the new leading line.
  **`ok`/`warn`/`alert`/`needs_attention` — "the dashboard is canonical" (fix round 1):** these are
computed by reapplying `dashboard/generate.py`'s own health formula (`compute_light` +
`is_stale`/`is_died`/`is_overdue`, ~:1275-1323) to each loop's **RAW** newest run row (from `db.py
query loops-summary`) — never the blanking-fix's fallback-resolved row. `ok`/`warn`/`alert` map the
dashboard's green/amber/red 1:1; a loop whose light is grey (never run, or a still-running run
within its own timeout budget) counts toward `N` but not toward any of the three. `needs_attention`
is the dashboard's own boolean — amber or red light, OR stale (overdue per the schedule), OR died
(past the harness timeout + grace) — so it necessarily agrees with what a human sees on the
dashboard for the same fixture, including that a `skipped-overlap`/`skipped-precheck` row is
unconditionally amber/needs-attention regardless of what the prior run was, and including staleness
and harness-death, neither of which the blanking-fix's fallback row alone would catch. A completed
run with a missing/unrecognized `effective_status` falls to grey (`compute_light`'s own documented
fallback) — uncounted in `ok`/`warn`/`alert`, not a silent special case of `status`'s own. Pinned
against the dashboard directly on identical fixture data in
`test_fleet_aggregate_agrees_with_dashboard_on_overlap_over_ok`.
  **Blanking fix (display text only, unaffected by the above):** a run row is "terminal" (safe to
display) only if `finished_at` is set AND `runner_status != "skipped-overlap"` — a `skipped-overlap`
row finishes immediately but never carries real status/headline data, and an unfinished row
(`finished_at IS NULL`) has none yet either; naively using "the newest row" for either case blanked
the display. `status` falls back to the newest *terminal* row (within the last 10) for
`runner_status`/`effective_status`/`headline`/`started_at` shown per loop; if none of the last 10 is
terminal, those fields stay `None`. This fallback is display-text only — it does NOT feed the health
counts above. Independently, each row also gains `"in_flight": true/false` — true whenever the
loop's actual newest run has `finished_at IS NULL` (a real in-progress run), regardless of whether a
fallback was needed for display. `next_run` estimation is unaffected — it always uses the true
newest row's `started_at`, never the fallback.

**Definitive empty states (Amendment 2 — 2026-07-30):** `list` (table form) with zero loops under
`--from` prints `0 loops (<from-dir> empty)` (e.g. `0 loops (loops.d empty)`) instead of the generic
table placeholder; `status` (no `<name>`) does the same beneath its leading fleet line when the
fleet is empty (fleet counts all zero, then the empty-state line). `findings <loop>` (table form)
with no open findings prints `0 open findings for <loop>`. All three still exit `0` — an empty fleet
or an empty findings list is not a failure. `--json` is unaffected (still `[]`/`{"fleet": …,
"loops": []}` as appropriate) — these are human-form-only messages.
  **`list --tag` no-match is a different claim from genuinely empty (fix round 3):** a `--tag` filter
that matches nothing on a NON-empty fleet must not print the genuinely-empty message — that would be
a false statement about a fleet that has loops, just none matching the filter. It prints `0 loops
matching --tag TAG (N loops under <from-dir>)` instead, naming the filter and the true fleet size; the
genuinely-empty message is reserved for an actually-empty `<from-dir>`. Both still exit `0`.

**`loopctl import` (Amendment 2 — 2026-07-30):** wraps `bin/skill_import.py`'s `parse_skill()` /
`analyze()` / `apply()` to convert an existing Agent Skill directory into a gap-analysis report or a
scaffolded loop. `--analyze` is static and zero-token: it prints (or, with `--json`, emits verbatim)
the `analyze()` dict — proposed name, type, engine, the permission-axes floor, detected flags, the
eleven-question intake rubric (`q1_purpose`..`q11_budget`, each bucketed
`answered`/`derived`/`missing`/`incompatible`), a precheck proposal whose every line is COMMENTED
(never live code — `[read-only?]` is a heuristic hint requiring human review, not a guarantee), and
the answers still needed to finish the intake. A blocked skill (credentials found, or an MCP
dependency with no CLI equivalent in the same file) still analyzes successfully — `blocked` is a
field in the output, never a CLI failure; only a missing/unparseable `SKILL.md`
(`skill_import.SkillParseError`) exits 1. `--analyze` and `--apply` form a required mutually exclusive
group (neither given, or both given, is a usage error — exit 2). Full design, the
rubric-id-to-`LOOP_AUTHORING.md`-§2 mapping, and the reshaping rules: `docs/SKILL_IMPORT.md`.

**`loopctl import --apply` semantics (Amendment 2 — 2026-07-30):** `--apply` requires `--answers
<path to answers.json>` (missing it is a usage error — exit 2); the file's shape is
`docs/SKILL_IMPORT.md` §7. `cmd_import` re-parses the skill and re-runs `analyze()` on every
invocation (never trusts a cached analysis), then calls `skill_import.apply(skill, analysis,
answers, dest_dir)`, which raises `skill_import.SkillApplyError` (message printed to stderr, exit 1)
for every refusal:
- **Stale answers:** `answers["skill_sha256"]` not equal to the freshly re-parsed skill's `sha256`,
  or `answers["analyzer_version"]` not equal to `analysis["analyzer_version"]` — re-run `--analyze`
  rather than hand-patching either value.
- **Blocked without acknowledgement:** `analysis["blocked"]` true and
  `answers["acknowledge_blocked"]` not true — the message names the blocking reasons.
- With `acknowledge_blocked: true`, a blocked skill scaffolds anyway, but `apply()` forces
  `schedule=manual` regardless of any `q4_cadence` answer and appends a
  `## BLOCKED — read before scheduling` section to `SPEC.md` naming the blockers.

**Collision handling is `cmd_import`'s, not `apply()`'s:** an existing `loops.d/<name>/` (`--name`
if given, else `analysis["proposed_name"]`) refuses with exit 1 unless `--overwrite` is passed; with
`--overwrite`, `apply()` runs and overwrites the five scaffold files in place, and the recorded
`imported` event's detail carries `"overwrite": true`. On success (never installs — same downstream
gates as any other loop: `validate` → `run` → `install`), `cmd_import` records the `imported` event
(`db.py record-event`) with detail `{"source_skill", "skill_sha256", "answers_provenance",
"overwrite"}`. Answer-precedence and template-reuse rules (an explicit `answers` entry always wins
over the rubric's own value, and `apply()` reuses `loopctl new`'s exact SPEC.md/prompt.md/
precheck.sh templates rather than duplicating the strings): `docs/SKILL_IMPORT.md` §7.
  **`--overwrite` refuses an INSTALLED target (Amendment 2 — 2026-07-30, fix round 3):** before
`apply()` ever runs, `cmd_import` checks `_is_installed(root, name)` (same plist-file + `launchctl
print` check `list`'s `installed` column uses) and refuses with exit 1, no files touched, no event
recorded, if the target is currently installed — even with `--overwrite`. Rewriting an installed
loop's `prompt.md`/`loop.conf`/`precheck.sh` in place would let the next launchd firing run the new
prompt with none of `validate` → supervised `run` → `install` re-applied, and (concretely) `apply()`
forcing `schedule=manual` for an acknowledged-blocked skill would leave the OLD plist bootstrapped
and still firing on its old schedule (closed by the matching `bin/run-loop.sh` guard, §4.1 step 1).
There is no force-past flag: the message names the required path back in — `loopctl uninstall <name>`,
then re-import, `loopctl validate`, `loopctl run`, `loopctl install`.

**Lifecycle events (Amendment 2 — 2026-07-30):** `new`, `install`, `uninstall`, `pause`, and
`resume` each append a `loop_events` row (via `db.py record-event`) on their success path, using
`--actor` as the actor: `new` → `created` (detail `{"type":…, "engine":…}`); `install` → `installed`
**only after** kickstart-verify passes (§8.1 step 5) — a failed/aborted install records nothing;
`uninstall` → `uninstalled`; `pause`/`resume` → `paused`/`resumed`, recorded even when the loop was
never installed (no plist present) — the event records the intent to pause/resume, not launchd
state. Recording is best-effort: a `record-event` failure is swallowed and never fails the verb
itself. `validate` records no event, per the audit-trail semantics in §3.

**Tags + provenance in JSON output (Amendment 2 — 2026-07-30):** `list --json` and `status --json`
rows each gain `"tags": [...]` (from `loop.conf`'s `tags=`, `conf.get("tags") or []`). `list --tag T`
filters rows to an exact match against a row's `tags` list (not a substring match — `--tag project`
does not match a tag of `project:x`). **(B-17 — 2026-08-03)** the same rows additionally gain
`"owner"` (resolved, never null) and `"owner_assumed"` (bool), both from
`loopconf.resolve_owner()`; `list --owner X` exact-matches the RESOLVED owner (assumed rows match
`loops`) and composes with `--tag`. `status --json` rows additionally gain `"provenance"`: the
most recent `created` or `imported` event for the loop (`db.py query loop-events --loop L --events
created,imported --limit 1` — the events filter, not a client-side scan of a limited row set, so a
loop's founding event is never lost behind a large number of later `paused`/`resumed`/etc. events),
shaped `{"event", "actor", "ts"}`, or `None` if no such event exists (e.g. loops that predate
lifecycle-event recording, or ones scaffolded by hand). `status` (table form) is unchanged by this
amendment — only its `--json` rows carry tags/provenance. (Amendment 2 — 2026-07-30: those rows now
live under the `"loops"` key of the `{"fleet": …, "loops": […]}` envelope described above, each also
carrying `"in_flight"`; the row shape itself — `tags`/`provenance` included — is otherwise
unchanged.)

**`loopctl new` scaffolding** additionally seeds `loops.d/<name>/SPEC.md` from the intake template
(`docs/LOOP_AUTHORING.md` carries the interview script). Template placeholders use the literal
marker `[FILL: <hint>]`.

**`loopctl validate` additions (Amendment 1 + intake):**
- `prompt.md` must contain a `## Finding identity` heading documenting the loop's `finding_id`
  derivation rule (seeded by the scaffold template) — missing heading = FAIL.
- `SPEC.md` still containing any `[FILL:` placeholder = FAIL.
- **Amendment 2:** `render.sh` present but not executable = FAIL (absent = fine, loop is
  simply not page-enabled).

### 8.1 Install must self-verify
`install` is not done when `launchctl bootstrap` returns. It must:
1. Refuse `schedule=manual` and refuse a loop that fails `validate`.
1a. **Requirements (live):** refuse when any `requires=` item is unmet on this host
    (`refusing to install <name>: requirement unmet — <item> (<detail>)`, one line per unmet
    item, exit 1). Runs after validate and **before** the run-first precondition, so a loop that
    cannot run here never reaches "run `loopctl run` first".
1b. **Host checks (systemd backend only):** after requirements and the run-first
    precondition, before any unit is written. `_host_checks(root)` returns human messages
    for every failed check (empty = ok). Any message → print each as
    `refusing to install: <msg>`, exit 1, nothing written. Checks:
    linger (`loginctl show-user <user> -p Linger` contains `Linger=yes`; seam `LOOPS_LOGINCTL`;
    non-zero exit = failed);
    timezone (only when `.env` has `LOOPS_EXPECT_TZ`: host zone from `os.readlink("/etc/localtime")`
    after `zoneinfo/`, else `timedatectl show -p Timezone --value`; seams `LOOPS_LOCALTIME_PATH` /
    `LOOPS_TIMEDATECTL`);
    XDG (`XDG_RUNTIME_DIR` set **or** `/run/user/<uid>` is a directory — then the check
    passes and `_systemctl` uses that default). Same gate on `loopctl console install`.
2. **(Amendment 2 — 2026-07-30) Run-first precondition:** refuse a loop with zero runs whose
   `runner_status` is `completed` or `skipped-precheck` already recorded (`_db_query(root,
   "last-runs", loop=name, limit=50)`), with a message telling the user to run `loopctl run <name>`
   first. This checks *existing* run rows already in the db — independent of, and prior to, the
   post-kickstart freshness poll in step 5 below, which verifies a *new* run after this install.
   Applies to ALL loops, not only imported ones: it makes the documented validate → supervised run
   → install gauntlet mechanical rather than advisory. There is deliberately no human approval gate
   on an agent adding a loop; this precondition is one of the two compensating controls (the other
   is provenance/observability) — an agent can satisfy it itself by running the loop first, it is
   not a human-in-the-loop gate.
   **Known limitation (spec-mandated, deferred at review):** the check only looks at the most
   recent 50 run rows. A loop that succeeded once, long ago, and has since accumulated 50+ newer
   non-`completed`/`skipped-precheck` runs (e.g. a long streak of failures after a working
   re-install) is falsely refused on re-install even though it has a real prior success — the
   fix (widen or drop the limit, or query for existence rather than a bounded window) is
   intentionally not built; the workaround is the same as the primary case: `loopctl run <name>`
   again to produce a fresh non-failed row inside the window.
3. Write `launchd/com.loops.<name>.plist` with **absolute** paths, `WorkingDirectory`,
   `EnvironmentVariables` (at minimum `HOME`, `PATH`, `LOOPS_ROOT`), `StandardOutPath` /
   `StandardErrorPath` under `state/`, and the schedule from §5.1.
4. `launchctl bootout gui/$UID/com.loops.<name>` (ignore failure) → `launchctl bootstrap
   gui/$UID <plist>`.
5. Fire the install trigger (`launchctl kickstart -p` / `systemctl start --no-block`) and then
   **verify a fresh run row appeared with a non-failed runner_status**; if not, report failure
   loudly and leave the job torn down. Failure strings are backend-neutral:
   `… after the install trigger — check the engine process under the scheduler ({backend})` /
   `… check engine auth/env under the scheduler ({backend})`. Env/auth breakage only
   surfaces in the real scheduler context — this step is the point.

**Amendment 2026-08-22 — install is platform-dispatched (B-24).** Steps 1, 2 and 5 are backend-
independent and unchanged. Steps 3 and 4 are the launchd *implementation* of "write the unit, arm
it"; `_install_backend()` chooses which implementation runs — `launchd` on darwin, `systemd`
everywhere else. Darwin keeps launchd so this repo's hermetic suite still runs on the macOS dev
box. The systemd sibling:

| launchd | systemd |
|---|---|
| `launchd/com.loops.<name>.plist` (in-repo, gitignored) | `~/.config/systemd/user/loops-<name>.{service,timer}` |
| `LOOPS_LAUNCHCTL` test seam | `LOOPS_SYSTEMCTL` test seam, plus `LOOPS_SYSTEMD_UNIT_DIR` so no test writes to the real unit dir |
| `bootout` + `bootstrap` | `daemon-reload` + `enable --now <timer>` |
| `kickstart -p` (step 5's trigger) | `start --no-block <service>` |
| `pause` = `bootout`, keeps the plist | `pause` = `disable --now <timer>`, keeps the units |
| `_is_installed` = plist present **and** `launchctl print` | unit files present **and** `is-enabled` |

`LOOPS_INSTALL_BACKEND` forces a backend so the hermetic suite exercises the Linux path from
macOS; an explicit argument still wins over it, and an unrecognised value raises rather than
guessing. Four things are load-bearing and were each a real defect avoided, not a precaution:

- **The service passes `--trigger launchd`.** `run-loop.sh` defaults to `--trigger manual`, and
  **both** of its §4.1 step 1 guards exempt manual — a paused loop and a `schedule=manual` loop
  each run anyway under that trigger. A timer unit that omits the flag silently defeats
  `loopctl pause`. `launchd` is the vocabulary token for "the platform scheduler fired this",
  kept platform-independent under the same alias-not-a-rewrite rule as the `com.loops.*` labels.
- **`TimeoutStartSec=infinity`.** A `Type=oneshot` is bounded by `DefaultTimeoutStartSec` (90 s
  upstream). Real engine runs are longer — this repo already documents a 90 s poll that "can
  never outlast a codex verification run" — so without it systemd SIGTERMs every scheduled run at
  90 seconds. The runner owns its own timeouts.
- **`OnBootSec=` accompanies every `OnUnitActiveSec=`.** A monotonic timer never fires until its
  service has run once; `StartInterval` has no such gap. Without it an interval loop goes silent
  after a reboot.
- **`start --no-block`** for step 5's trigger: `start` on a `Type=oneshot` otherwise blocks for
  the entire engine run.

`Persistent=true` (calendar timers) plus `loginctl enable-linger` supersedes the macOS
"bootstrapped installs do not survive reboot" failure: units live in the user unit dir, the
`timers.target.wants` symlink persists, and a firing missed while the host was down is caught up
at boot. **Timezone is a deployment property, not a grammar one** — §5.1 times are local in both
systems, so the host's timezone must match the one the fleet's schedules were authored in.

## 9. Tier-1 contract

### 9.1 Shape
```json
{
  "schema_version": 1,
  "run_id": "…",
  "status": "ok | warn | alert",
  "status_reason": "short_machine_category",
  "headline": "one line, e.g. '3 repos unpushed, 1 has no remote'",
  "report_markdown": "# full human report…",
  "metrics": "{\"repos\": {\"dirty\": 2}}",
  "findings": [
    {
      "finding_id": "cookingapp:no-remote",
      "title": "cookingapp has 23 unpushed commits and no remote",
      "severity": "info | warn | alert",
      "detail": "…"
    }
  ]
}
```
- `findings` is **required but MAY be empty** (a clean run, a watchdog probe). Each item requires
  all four fields shown; no additional properties.
- **`finding_id` must be deterministic and stable across runs** for the same real-world condition
  and must NOT embed volatile data (timestamps, run ids, counts, shifting line numbers). Derive it
  from the durable identity of the thing: `<subject>:<condition>`. Identity is **loop-defined**:
  each loop's `prompt.md` documents its derivation rule under `## Finding identity` (§8).
- The **engine emits identity only** — it never computes recurrence, age, or "3rd time seen"; the
  runner derives all of that from sqlite. Never trust the model to count its own history.
- `report_markdown` is in-schema on purpose: the schema-enforced final message **is** the entire
  emission, so there is no second free-text channel that can be lost.
- `run_id` must equal the runner's `RUN_ID`; a mismatch is a `contract-violation`.
- `metrics` is tier-2 and free-form **in content**, but its wire encoding is a **JSON string
  containing a serialized JSON object** (`"{}"` when empty). This is forced by codex's strict
  structured-output mode, which rejects free-form objects (verified — §7.1 / `ENGINE_PROBES.md`).
  `validate_contract.py` additionally checks the string parses as a JSON **object**; a
  non-parsing or non-object `metrics` string is a `contract-violation`. The string stays
  verbatim in `contract.json`/`latest.json`; `db.py record-metrics` parses it before flattening
  (§3); the dashboard reads metrics from sqlite only.
- The schema file `contract/contract.schema.json` is the single source of truth and is passed to
  **both** engines. If the two CLIs demand incompatible strictness, the schema is written to the
  intersection of what both accept.

### 9.2 `bin/validate_contract.py`
Stdlib-only validator (no `jsonschema` dependency) covering exactly the subset used: `type`,
`required`, `properties`, `enum`, `additionalProperties:false`, `minLength`, `maxLength`, `const`,
integer/number/string/object/array/boolean. Usage:
`validate_contract.py --schema S --file F [--expect-run-id ID]` → exit `0` valid, `1` invalid
(reasons to stderr, one per line), `2` usage.

### 9.3 Metrics → dashboard
Flattening rules are in §3. `dashboard.json` (per loop) declares how to render them:
```json
{"panels":[
  {"title":"Dirty repos","metric":"repos.dirty","type":"number","unit":"repos",
   "direction":"higher_is_worse","thresholds":{"warn":1,"alert":5},"missing":"gap"},
  {"title":"Unpushed","metric":"repos.unpushed","type":"trend","window_days":30,"missing":"hold"}
]}
```
- `type`: `number | table | list | trend`. `table` expects the metric to be an array of objects
  (columns = union of keys, stable order); `list` an array of scalars; `trend` a numeric metric
  read from the `metrics` table over `window_days` (default 30).
- `direction`: `higher_is_worse | lower_is_worse | neutral`. `thresholds`: `{warn, alert}` — panel
  colouring only; it never overrides `loop_status`.
- `missing`: `hold` (carry the previous value forward, marked stale) or `gap` (render a hole).
- **Undeclared metrics are never hidden**: they render in a capped raw fallback panel (values
  truncated at 2 KiB with a link to the full report).
- `dashboard.json` may be absent ⇒ tier-1 row + raw fallback only.

## 10. `dashboard/generate.py`

`python3 dashboard/generate.py [--root R] [--out FILE] [--now ISO8601]` → writes
`dashboard/loops.html` via
**tmp file + `os.rename`** (never a partially-written page, even under concurrent runs). Reads only
sqlite + `reports/*/latest.*` + `loops.d/*/{loop.conf,dashboard.json}`. Self-contained single file:
inline CSS/JS, no network requests, no external assets (it is opened as `file://`).
**(Amendment 2026-08-03, B-18):** `--now` pins the rendering clock (the pre-existing `now=`
parameter of `generate()`, exposed on the CLI). With a fixed root and pinned `--now` the output is
byte-deterministic — kagami's drift detection depends on this; naked `datetime.now` calls must not
be added to the render path.

- **Global view:** one row per loop — precedence-resolved status light (§4.3), headline, last run
  (relative + absolute), schedule + best-effort next run, 7-day token spend, link to latest report.
- **Owner (B-17 — 2026-08-03):** each row shows an owner chip (主 + resolved owner) in the summary
  tier; assumed owners render dimmed/dashed with a title hint. Every `.loop-row` carries
  `data-owner="<resolved>"` (always emitted, like `data-tags`). The owner `<select>` always
  renders (the tag select stays conditional on tags existing); one client-side function applies
  owner ∧ tag. Clicking the chip copies `loopctl set-owner <loop> <owner>` to the clipboard
  (inline JS, clipboard-only) — the page's owner-edit affordance; the page itself still mutates
  nothing and fetches nothing. (B-19 moved the controls into the garden kicker — next bullet.)
- **Garden kicker controls + recency order (B-19 — 2026-08-03):** the garden kicker's right side
  hosts the filter/sort selects (`<span class="filters">`) — the old glossary note (床の間/休
  glosses) is REMOVED from the kicker; the glyphs keep their `title` glosses. New
  `<select id="sort-order">` with `recent` (default) and `name`. **Default garden order is
  recency, server-rendered**: rows sort by `data-latest-event` — the loop's newest `loop_events`
  ts of ANY type (so a just-created loop tops the garden; same missing-table degradation as
  provenance), event-less loops last in name order. The attribute is always emitted (`""` = no
  events). The select's client-side re-sort reorders the same rows with a FLIP transform
  animation (inline JS, no fetches; `prefers-reduced-motion` disables it). The recent-events
  strip is visually compacted (tight paddings, no per-row hairlines) — still the same 15-row
  `load_loop_events` content contract.
- **Top strip:** fleet counts by status, `needs_attention` count, spend today / 7d, last regen time.
- **Stale detection:** a loop overdue by > 1.5 × its `expected_interval_s` (§5.1) is flagged
  `stale` and counts toward `needs_attention`. `manual` loops are exempt.
  **(Amendment 2026-07-30, rewritten B-25):** staleness applies only to *installed* loops.
  Install state is `unit_files_present(root, name)` — file presence only, never a scheduler
  subprocess (the generator stays hermetic): launchd backend → `launchd/com.loops.<name>.plist`
  exists; systemd backend → **both** `loops-<name>.service` and `loops-<name>.timer` exist
  under `LOOPS_SYSTEMD_UNIT_DIR` (default `~/.config/systemd/user`). Backend =
  `LOOPS_INSTALL_BACKEND` if set (unknown value raises), else `launchd` on darwin and
  `systemd` otherwise. Canonical in `bin/loopctl`; `dashboard/generate.py` carries a
  lockstep mirror (must run with no `bin/` on disk) pinned by
  `tests/test_dashboard.py::test_unit_files_present_mirror_never_drifts`. A non-manual
  loop without unit files renders as 休 "no schedule loaded" (supervised-only), is
  staleness-exempt, and does not count toward `needs_attention` on schedule grounds; its
  status still counts as before. The 巡 tooltip reads `schedule loaded ({backend})`.
  Rationale: a supervised-only fleet rendered wall-to-wall `stale`, and fake next-run
  estimates ("in 4m" for a loop that will never fire) made the column meaningless.
  **(Console amendment, §13):** the same file-presence + conf-parse check is now three-way, still
  without a scheduler subprocess: unit files present and `enabled=true` → 巡 "schedule loaded"
  (next-run shown); unit files present and `enabled=false` → 休 "paused" (rounds toggled off via
  console/`loopctl pause`; `next` reads "paused"); no unit files → 休 "no schedule loaded"
  (supervised-only, as above). **Staleness and `needs_attention` are `enabled`-blind**: pausing
  a loop does NOT exempt it from either — `stale` keys on `installed` (`unit_files_present`)
  alone, so a paused loop whose last run is overdue still renders `stale` and still counts toward
  `needs_attention`. Only the no-unit-files state is staleness-exempt (the pre-existing 2026-07-30
  amendment above). **(Resolved 2026-07-30):** paused loops stay staleness-visible — settled,
  do not relitigate. Pause has no expiry (unlike `snooze --until`), so a paused-and-forgotten
  loop is exactly the failure mode `needs_attention` exists to catch; exempting it would
  create a silent way to turn a loop off forever. A deliberate long-term off is
  `set-schedule manual`, which removes the unit files and lands in the staleness-exempt 休
  no-schedule state. Paused → keep nagging; manual → exempt. That split is the design.
- **Running/overdue/died trichotomy (§4.6, Amendment 2 — 2026-07-30):** for a run row with
  `finished_at IS NULL`, age is measured against the loop's `timeout_s` (missing/unparseable
  conf falls back to the `900` default, same as elsewhere): age ≤ `timeout_s` renders `running`
  (a pulsing badge — live and in-flight, **not** a failure, does **not** count toward
  `needs_attention`); age in `(timeout_s, timeout_s+120]` renders `overdue` (amber badge — still
  running past its own timeout budget but not yet past the died grace, counts as amber
  `needs_attention`); age `> timeout_s + 120` renders `died` (red, harness-problem marker, and
  counts toward `needs_attention`) — this outer boundary is unchanged from the original rule.
- **Status light** uses `effective_status` (§4.5) under the §4.3 precedence — never raw
  `loop_status`.
- **Per-loop sections:** declared panels, trends (from the `metrics` table), a **findings list**
  (from sqlite + `latest.json`, never parsed from markdown): open findings with recurrence and
  disposition rendered as text — e.g. `3rd report · dismissed 2026-06-01 ("note")` — suppressed
  findings shown greyed/collapsed, not hidden; a recent-runs table including runner_status,
  report links, and the raw fallback panel. The dashboard is static (Change 4 Option A):
  dispositions are entered via `loopctl`, and the page may display the ready-to-paste command.
  **Per-finding agent handoff (Amendment 2 — 2026-07-30):** each **unsuppressed** open finding
  additionally renders a collapsed `<details class="finding-handoff">` paste-into-an-agent
  block (`_render_findings`/`finding_handoff_text`) — same deterministic-template pattern as
  the run-failure handoff block, merging sqlite's recurrence fields (`finding_id`, `severity`,
  `times_seen`, `first_seen_at`) with `latest.json`'s `title`/`detail` (falling back to
  sqlite's `title`/`severity` and an empty detail when the finding has no live entry — e.g.
  resolved since, or `latest.json` missing — never a crash). Model-derived text (`title`,
  `detail`) is HTML-escaped along with the rest of the composed block; `detail` is clamped at
  2 KiB (`truncate_value`) with a truncation marker. The template MUST NEVER contain the word
  "approve" in any form (ack ≠ approval is settled doctrine): it distinguishes acting on the
  finding in the reader's OWN agent context/permissions from suppressing it via the pasted
  `loopctl dismiss <loop> <finding_id> [--root R] --note "..."` / `loopctl snooze <loop>
  <finding_id> [--root R] --until YYYY-MM-DD` command lines. `--root <root>` is included in
  those pasted commands only when the generating root's realpath differs from the realpath of
  `~/projects/loops` (`root_flag_for`). Suppressed findings are unaffected — still
  greyed/collapsed with the existing `reopen` command, no handoff block.
  **Mock-parity controls (B-11 — 2026-07-31, presentation-only):** the fleet row's three-way
  schedule state renders as the site mock's read-only rounds switch (section 04 of `site/index.html`;
  same kanji and title-string vocabulary as the Console amendment above — 手 manual keeps the
  chip). It is display, not a control: hidden under `html.console-active`, where the console's
  interactive toggle takes over, and the base row grid widened 30px → 64px to seat it (the
  Task-4 rule that the wider *console* track is gated on `console-active` is unchanged). Each
  **unsuppressed** open finding renders a hanko rank (承 認 休 済): 認/休/済 carry their full
  ready-to-paste `loopctl ack|snooze|dismiss` command in `data-copy`/`title` and copy it to the
  clipboard on click (this is where the "may display the ready-to-paste command" affordance now
  lives — the always-visible dismiss one-liner is retired; `--root` inclusion via
  `root_flag_for` is unchanged). Clipboard failure degrades to revealing the command as a
  selectable line; nothing touches the network. 承 is rendered **disabled**: its verb does not
  exist and `ack` is deliberately not it (open thread — the handoff doctrine's page-wide ban on
  the word stays pinned by test). Suppressed findings keep the `reopen` one-liner and show
  their disposition as a stamp-mark (認/休/済) in place of the rank.
- **Failure surfacing (amendment 2026-07-29):** runs whose `runner_status` is one of
  `precheck-failed | engine-failed | engine-timeout | auth-failed | tool-denied |
  contract-violation | harness-error` render their `error_detail` + `exit_code` in the
  recent-runs table and as the fleet-row headline fallback (failed runs have no headline).
  When the **latest** run failed, the loop section shows a collapsed-open **agent handoff
  block**: a deterministic, generator-templated paste-into-an-agent prompt built ONLY from
  sqlite fields (`run_id`, `loop_name`, `runner_status`, `error_detail`, `exit_code`) and
  static path/doc references — never model output. Skips/overlaps are not failures.
- **Inline report drawer (amendment 2026-07-29):** each loop section may render
  `report_markdown` from the suppression-filtered `latest.json` inside a collapsed
  `<details>` — HTML-escaped, displayed as text (markdown is not parsed), clamped at 8 KiB
  with a truncation marker; the `latest.md` link is retained alongside.
- **Tags + provenance + recent-events strip (Amendment 2 — 2026-07-30):** rendered from
  `loop.conf`'s `tags=` and the `loop_events` table (§3) — never re-derived from markdown.
  Every loop row and section carries `data-tags="a b c"` (space-separated; `data-tags=""`,
  present but empty, when the loop has no tags — the attribute is never omitted, so the
  filter's `[data-tags]` selector reaches every loop and an untagged one is correctly hidden
  rather than defaulting to always-visible); tag chips (`<span class="tag">`) render next to
  the loop name in both the fleet row and its per-loop section, but only when the loop has
  tags. A `<select id="tag-filter">` is rendered only when at least one tag exists fleet-wide,
  populated from the union of every loop's tags; its `onchange` runs inline vanilla JS that
  exact-matches the selected tag against each element's split `data-tags` list and toggles
  `display:none` on non-matching `[data-tags]` elements — **client-side only**, no server
  round-trip, no query-string state; selecting a tag shows ONLY loops carrying it (same
  semantics as `loopctl list --tag`, §8). Each per-loop section shows a provenance line for
  the loop's most recent `created`/`imported` event (found by filtering
  `event IN ('created','imported')` in SQL before any `LIMIT`, so the founding event is never
  lost behind later `paused`/`resumed`/etc. rows): `<event> from <source> by <actor>, <date>`
  when that event's `detail` JSON carries a `source_skill`, else `<event> by <actor>, <date>`;
  no such event ⇒ no line rendered. A fleet-wide `<section id="recent-events">` lists the last
  15 `loop_events` rows (newest first, `load_loop_events(conn, limit=15)`); zero events still
  renders the section, with a literal "no lifecycle events yet" line rather than omitting it.
  Both `load_loop_events` and the per-loop provenance lookup degrade to `[]`/`None` (not a
  crash) against a pre-Amendment-2 sqlite whose `loop_events` table doesn't exist yet.
- **Report pages (Amendment 2; amended 2026-08-02):** report links + dated history live in
  each loop's accordion expansion body (report block). Links prefer
  `../reports/<name>/latest.html` (md link kept secondary); a page whose envelope
  `meta.run_id` ≠ the loop's latest promoted run (newest row with
  `runner_status='completed'` AND non-NULL `contract_path`) gets a `stale` badge.
  Dated history is taken from filenames only (newest first, capped 30 with a `+N older`
  note). Envelope meta is read via `bin/page_envelope.py` (display HTML is never scraped;
  only `latest.html` is ever parsed). The standalone `dashboard/reports.html` screen was
  **retired 2026-08-02** — the garden is the sole index; orphaned report dirs (on disk with
  no `loops.d/` entry) remain directly servable by URL but are not listed. The §10 read-set
  gains the `latest.html` envelope.
- **Garden accordion + English glosses (Amendment 2026-08-02):** each garden row is a native
  `<details class="loop-row" name="garden" id="loop-<name>">` accordion — the shared
  `name="garden"` gives one-open-at-a-time natively. The `<summary>` is the glance row
  (stamp, name, schedule/description, tokonoma, run-meta text, rounds switch); the expansion
  body is the former per-loop section (findings, recent runs, panels) plus the report block
  and a permalink glyph (`#loop-<name>`). ~10 lines of inline deep-link JS open the matching
  row on `DOMContentLoaded` and `hashchange` when `location.hash` is `#loop-<name>` — stays
  hermetic (no fetch, no external assets). Meaning-bearing kanji carry a tiny muted English
  gloss (`<span class="en">…</span>`): stamps 済/注/警/未 → ok/warn/alert/no data; switch
  巡/休/手 → on/paused|off/manual; run-meta 巡 → run; findings 巡 ×N → seen; hanko 認/休/済 →
  ack/snooze/dismiss (and disposition marks ack/snoozed/dismissed). 承 is unglossed (the
  natural English word is banned page-wide — ack ≠ approval). Tooltips stay; kicker/note
  kanji already adjacent to English stay unglossed.
- Style **(amended 2026-07-30, B-04/B-07; Amendment 2026-08-02, WP2)**: the roops garden
  design system — washi/sumi palette, vermillion accent, mincho serif + mono numerals,
  status rendered as hanko stamps (済 ok · 注 warn · 警 alert · 未 no data) over the
  unchanged §4.3 precedence, per-loop tokonoma output alcove in the global row. Visual
  only: every §10 semantic above is untouched. Local system fonts only, no webfonts, no
  textures via external or data URLs — the no-network rule binds the stylesheet too.
  Function first, dense over airy: it is a status board read at a glance, not a marketing
  page. **Dark mode (WP2):** the same named role tokens carry a second value set —
  `@media (prefers-color-scheme: dark)` redefines them as the OS-driven default;
  explicit `:root[data-theme="dark"]` / `:root[data-theme="light"]` overrides win in
  both directions (toggle beats OS preference via attribute specificity alone). A
  topstrip `<button id="theme-toggle">` persists the choice under localStorage key
  **`loops-theme`** (values `"dark"` / `"light"`; attribute **`data-theme`** on
  `<html>`; absence means follow OS — never a third stored value). A synchronous inline
  script in `<head>` ahead of `<style>` stamps `data-theme` before first paint (no flash).
  Toggle and persistence JS are pure client-side — `localStorage` and `matchMedia` only,
  zero new network surface; the §10 hermetic rule binds this addition exactly as it binds
  everything else. WP3 reuses the same key/attribute/values on report pages verbatim.
  **Report-page kit (WP3 2026-08-02):** `pagekit/kit.css` shares the garden's exact role
  + font tokens in both modes (light `:root`, dark via `prefers-color-scheme` and
  `:root[data-theme="dark"|"light"]`); parity is enforced by `tests/test_token_drift.py`
  (parser self-tested by `tests/test_token_parser.py`). Report pages carry the same theme
  toggle, inlined from `$PAGEKIT/toggle.js` (same missing-file-fails-loudly contract as
  `kit.css`), persisted under the same localStorage key **`loops-theme`**, so a viewer's
  choice carries between the garden and any report page.

## 11. Testing conventions

- Tests live in `tests/`, run by `tests/run-tests.sh` (plain bash; no framework). Each test is a
  function or a `test_*.sh` file that exits non-zero on failure and prints a one-line reason.
- Python units use stdlib `unittest` (`python3 -m unittest discover -s tests -p 'test_*.py'`).
- Every test must be **hermetic**: it creates its own temp `LOOPS_ROOT` (via `mktemp -d`) and never
  touches the real `state/`, `reports/`, launchd, or any project outside the repo. No test may
  invoke a real engine CLI or the network; engine invocation is tested with a `engines/fake.sh`
  stub fixture that emits a canned contract, honours `EXIT_CODE`/`SLEEP_S` env knobs for the
  timeout/failure paths, and is used by the runner tests.
- `tests/run-tests.sh` must pass before any task is considered complete.

**Amendment 1 verification additions (mandatory in the pilot matrix and the hermetic suite):**
- **Idempotence:** run a loop twice against an unchanged world (fake engine, canned contract) →
  identical `finding_id`s, `times_seen` increments, no duplicate `findings` rows, promoted report
  unchanged in substance.
- **Suppression:** dismiss a finding → the next run's promoted `latest.json` and the dashboard
  omit it even though the engine still emits it (runner-side suppression proven, not prompt-side);
  `effective_status` recomputed per §4.5 (a loop whose only finding is dismissed goes green).
- **Resolution lifecycle:** a finding absent from a run gets `resolved_at` set; reappearing later
  clears `resolved_at` and continues `times_seen`.
- **Engine-error paths:** adapter exit 12 retried exactly `retry_transient` times then
  `engine-failed` with transient classification; exits 10/11 never retried; an injected runner
  exception produces `harness-error` with the lock released; a row with `finished_at IS NULL`
  past `timeout_s + 120s` renders `died` on the dashboard.
- **Pilot:** `examples/hello-loop` emits ≥2 findings with stable ids so recurrence and one
  disposition are exercised as regression fixtures.

## 12. `bin/page_envelope.py` — report-page envelope (Amendment 2)

Single stdlib-only implementation used by the runner gate AND `dashboard/generate.py`.

```
page_envelope.py check --file F [--expect-run-id ID] [--expect-loop L]
page_envelope.py meta  --file F
```

`check`: exit 0 = promotable; exit 1 with one reason per stderr line. Checks: readable,
non-empty, UTF-8, ≤ 8 MiB; exactly one `<script type="application/json" id="report-data">`
block; JSON parses; `meta.loop`, `meta.run_id`, `meta.generated_at` (`%Y-%m-%dT%H:%M:%SZ`),
`meta.title` (non-empty str), `meta.page_class` ∈ {snapshot, findings} all present;
`meta.totals` when present is a flat object with number or ≤64-char string values;
`--expect-*` mismatches; external-fetch heuristics (`<script src=`, `<link href="http`,
`<img src="http`, `<iframe`, `@import`, `url(http`); redaction-clean (`bin/redact.py` over
the full page text must be a no-op). `meta`: prints the parsed `meta` object as JSON to
stdout (exit 1 + reasons if extraction fails). Importable: `check_page(path, expect_run_id=None,
expect_loop=None) -> list[str]` (empty = pass) and `read_meta(path) -> dict | None`.

## 13. Console (`loopctl serve`)

Local control surface for the dashboard. `bin/console.py`, started by `loopctl serve
[--port PORT] [--allow-host HOST ...]` (default 8929), binds 127.0.0.1 ONLY. Trusted
unsandboxed harness code: MAY shell out (`scheduler_loaded` for live load state; `bin/loopctl`
subprocesses for all mutations — one code path for CLI and console). §10's hermeticity binds
dashboard/generate.py, never this module. No daemon mode; persistent operation is
`loopctl console install` (launchd label `com.roops.console` / systemd
`loops-console.service`, machine-local) which just runs `loopctl serve` under KeepAlive /
`Restart=always` — the harness itself still has no daemon code. The listener stays 127.0.0.1
only.

`scheduler_loaded(name)` is the live check: launchd `launchctl print gui/$uid/com.loops.<name>`;
systemd `systemctl --user is-enabled loops-<name>.timer`. `_systemctl` defaults
`XDG_RUNTIME_DIR` to `/run/user/<uid>` in the child when the parent left it unset (a
non-login ssh shell does not set it; without it `systemctl --user` cannot reach the bus).
Honours `LOOPS_LAUNCHCTL` / `LOOPS_SYSTEMCTL`.

| endpoint | effect |
|---|---|
| `GET /` `/loops.html` | serve generated pages (loops.html regenerated if missing). `/reports.html` retired 2026-08-02 → 404 |
| `GET /reports/<loop>/<file>` | serve one file from `<root>/reports/` — the dashboard's own `../reports/<name>/latest.html` links. Path regex allows `[A-Za-z0-9_-]` / `[A-Za-z0-9_.-]` only (no `/`, no `%`), plus an `os.path.realpath` containment check under `<root>/reports`. No directory listing; non-file → 404. Content-Type: `.html`→`text/html`, `.json`→`application/json`, `.md`→`text/plain`, else `application/octet-stream`. **Always answered with `Content-Security-Policy: sandbox allow-scripts`** — see below. |
| `GET /api/state` | `{loops:[{name, schedule, enabled, plist_present, loaded}]}`. Field names kept (B-11 hydration). `plist_present` = unit files present for this host's backend (launchd plist / systemd service+timer). `loaded` = `scheduler_loaded`. |
| `POST /api/loops/<name>/rounds {on}` | resume/pause (sets `enabled=` + bootstrap/bootout). `on` must be a real JSON boolean, else 400. 409 if unit files are not present — install/uninstall stay CLI-only (supervised verification gate, §8.1). |
| `POST /api/loops/<name>/schedule {spec}` | `set-schedule`: §5.1-validate, rewrite conf, re-render plist, bootout+bootstrap iff loaded. NEVER kickstart. `spec` must be a JSON string, else 400; 400 on bad grammar. **`spec: "manual"` is refused 400 by the console** even though it is valid §5.1 grammar: `_apply_schedule` implements manual as an UNINSTALL (bootout + remove the plist), and install/uninstall stay CLI-only (§8.1). The refusal is console-layer only — `loopctl set-schedule <name> manual` is unchanged. |
| `POST /api/loops/<name>/run {}` | B-13 (§13.3): start ONE supervised run (`loopctl run <name>`) in a background worker. 202 `{ok:true, state:<run-status dict>}` snapshot; 404 unknown loop; 409 `{error, loop, started_at}` while the console-wide job slot is busy; body must be a JSON object (uniform 400). **POST only — any other method is a route MISS (generic 404), never matched-then-refused**: §13.1's Content-Type gate binds only POST, so a GET-matched run route would be a plain cross-origin CSRF that fires real runs. **No installed/enabled/schedule gate** — firing a paused or never-installed loop from the page is a deliberate human act (the whole point of B-13; contrast `rounds`' 409-without-plist). |
| `GET /api/run/status` | bare run-status dict `{running, loop, started_at, finished_at, exit_code, ok, error}` (timestamps ISO-8601 UTC). Idle = `running:false` + nulls; the terminal snapshot persists across polls until the next job RESETS it (`loop` = new name, other fields back to null mid-flight). GET only — POST is a route miss. |

**Report pages are sandboxed off this origin.** Report HTML is loop/model-derived content and
the promotion gate (§12) blocks only EXTERNAL-fetch markup — an inline `<script>` is allowed.
Served from the same origin as the mutation API, such a script could POST to
`/api/loops/<any>/rounds` with a valid `Host` and no CORS preflight, i.e. pause loops or
rewrite schedules; under `file://` that was impossible (opaque origin), so the route is what
created the adjacency and the route is what closes it. **Loop-authored content must never share
the API's origin.** `sandbox allow-scripts` (never `allow-same-origin` — the two together let a
page drop its own sandbox) keeps the page's own inline script working while making every
request it issues cross-origin, which then fails closed on the missing `OPTIONS` handler.

Every mutation regenerates the dashboard before responding, and the regen is best-effort on
both endpoints — the mutation already succeeded, so a regen failure must never change the
response. `/rounds`: the console owns the regen and warns
`warning: dashboard regen failed: …` on stderr. `/schedule`: `loopctl set-schedule` owns it,
guarded the same way as the disposition verbs (warn on stderr, exit 0). **(Amended
2026-07-30):** before this, the set-schedule regen ran unguarded, so a regen exception exited
non-zero and the console reported a false `400 invalid schedule` for a schedule change that
DID take effect. The conf is the source of truth either way: `/api/state` reflects the new
schedule regardless of regen outcome.

A malformed or non-object JSON body is 400 uniformly, as is a `Content-Length` that is negative
or non-numeric, and a `spec` that is not a JSON string or contains a NUL byte (never an
unhandled exception — an exception out of the handler drops the connection with no HTTP
response, `read(-1)` would park the serving thread, and a NUL makes `subprocess.run` raise
`ValueError: embedded null byte`). A NUL in `<name>` cannot arise: the route regexes admit only
`[A-Za-z0-9_-]`, so such a request is a route miss (404). With `--port 0` the listener binds
first and the ACTUAL bound port is what §13.1's `Host` gate and the startup banner use.
`<name>`/`spec` positionals reach
`bin/loopctl` after a `--` separator, so a `spec` of `--help` is passed through as the literal
positional value instead of being consumed by argparse as `-h/--help` — the latter would exit 0
with no mutation and no error, a false "success" for a schedule that never took effect.

### 13.1 Request-origin gate (fail-closed, applies to every request)

Every request — GET or POST, page or API — is rejected `403` unless the `Host` header is
exactly `127.0.0.1:<port>` or `localhost:<port>` (or an entry in the operator's explicit
`--allow-host` list — amendment below); every POST must additionally carry
`Content-Type: application/json`. Rationale: binding to `127.0.0.1` stops packets arriving from
off-box, but not a browser on this same machine tricked into firing a request here — a plain
cross-origin `<form method="POST" action="http://127.0.0.1:PORT/...">` still reaches the
socket, and DNS rebinding defeats an Origin-only check. The exact-match `Host` check closes
both; the `Content-Type` requirement additionally closes the forms vector (a bare `<form>` can
only send `application/x-www-form-urlencoded`/`multipart/form-data`/`text/plain`, never
`application/json`) and forces a CORS preflight (`OPTIONS`) for any cross-origin JSON `fetch()`
— this server implements no `OPTIONS` handler, so the preflight fails closed.

**Standing rule:** the console MUST NEVER emit `Access-Control-Allow-Origin` or any other
`Access-Control-*` header. `/api/state`'s confidentiality (fleet names, schedules, enabled
state) rests entirely on the browser's same-origin default; adding such a header would leak
fleet state to any page the user's browser happens to visit.

**Exposure note:** with no `--allow-host`, the `Host` check admits only the loopback literals
above, so fronting the console with Caddy, `tailscale serve`, or rebinding the listener to
`0.0.0.0` will 403 every request. That is deliberate for a launchd-mutating API — document it
here so it isn't debugged the hard way. (Explicit exception to the machine-global "bind dev
servers to `0.0.0.0`" habit: this server binds loopback-only on purpose.)

**Amendment (2026-08-04, B-22) — `--allow-host`, the trusted-proxy deployment.** `loopctl
serve --allow-host HOST` (repeatable) adds HOST to the gate's exact-match set — nothing else
changes: the listener still binds 127.0.0.1 only, POSTs still require the JSON Content-Type,
`OPTIONS` still fails closed, and `Access-Control-*` stays forbidden. The intended (and only
sanctioned) use is a trusted local reverse proxy that terminates TLS and forwards the
browser's REAL `Host` untouched — on this machine, the dev-tailnet Caddy's `@loops` route
proxying `loops.example.ts.net` → `127.0.0.1:8929`, with a `handle_errors` fallback to
the static `dashboard/` files when the console is down. DNS rebinding stays dead under this
deployment: an attacker-controlled hostname arrives as ITS OWN `Host` and fails exact-match.
The proxy MUST NOT rewrite `Host` to a loopback literal (`header_up Host 127.0.0.1:8929`
forges the exact credential this gate checks and would blind it to rebound hostnames);
allowlisting the real public name is the honest form of the same trust decision. Exposure
consequence, stated plainly: every mutation route (§13 table) becomes reachable by any
device on the tailnet behind that hostname — the tailnet is the trust boundary. The console
runs persistently for this via `loopctl console install`: launchd label `com.roops.console`
(NOT `com.loops.<name>` — that namespace is reserved for loops) or systemd
`loops-console.service`, KeepAlive/`Restart=always`, logs under `state/`; like loop unit
files it is machine state, not repo content. Args (`--port`, `--allow-host`) are baked from
`.env` (`LOOPS_CONSOLE_PORT`, `LOOPS_CONSOLE_ALLOW_HOSTS`) at install time.

### 13.2 Page hydration

The generated dashboard's console controls (`data-console-controls`) render `hidden` and
unhide only once a **relative** `fetch('api/state')` succeeds; that same success branch stamps
a `console-active` class on `<html>`, which widens the schedule/rounds column via CSS. Opened
as a plain `file://` page (no console running), the fetch fails, the controls stay hidden, and
the page is byte-identical in behavior to the pre-console dashboard. The `hidden` attribute is
only a USER-AGENT stylesheet rule, which any author-origin `display` declaration outranks, so
the generated stylesheet MUST carry an author-origin `[hidden] { display: none !important; }`
rule — without it `.con-cell`/`.sp-form`'s own `display` values un-hide the controls and the
file-opened page shows toggles it cannot actuate. §10's hermeticity binds
`dashboard/generate.py` only; the console itself is trusted harness code and is not subject to
that rule.

The run-now control (`con-run`, B-13) rides the same gate: inert hidden markup inside
`.con-cell`, rendered for EVERY loop (never `disabled` on account of install state). On the
`api/state` success branch the page fetches `api/run/status` once and reflects any in-flight
job (all `con-run` buttons disabled, the running loop's marked). The click branch calls
`ev.preventDefault()` first (the controls sit inside `<summary>` — an ungated click toggles
the garden accordion), POSTs through the shared `post()` normalizer, and gates
`location.reload()` on the success path only: an error re-enables the button and surfaces via
the existing alert path, and polling (`api/run/status`, ~3s) reloads only once `running` flips
false.

### 13.3 Run trigger (`/api/loops/<name>/run` + `/api/run/status`) — B-13, added 2026-08-03

The ONLY console path that fires a run — `rounds` and `schedule` are unchanged and
set-schedule's "NEVER kickstart" rule stands. Contract:

- **Worker.** The POST handler never blocks on the run: it starts a
  `threading.Thread(daemon=True)` whose whole body is `try/finally`; the `finally`
  publishes the terminal status and releases the slot under the same status lock, so no
  exception path can strand `running:true` (the dmp_regen :180 lesson) and a reader can
  never observe a torn terminal snapshot. The worker calls the existing
  `_loopctl(root, ["run", name])` — one code path for CLI and console, `--` separator
  preserved — records the exit code VERBATIM (`ok` iff 0), and never reinterprets run
  results: `state/` and the regenerated dashboard stay authoritative.
- **Error field.** On non-zero exit, `error` carries the stderr TAIL bounded at 4096
  characters (the end survives, the head drops); `null` on success.
- **Regen.** After the subprocess exits — regardless of exit code — the worker runs
  `_regen_dashboard(root)` best-effort: a regen failure warns on stderr and never alters
  the recorded outcome or the already-sent response.
- **One job console-wide.** A single non-blocking `threading.Lock`; a busy POST for ANY
  loop is 409. Per-loop overlap with scheduled/CLI runs stays the runner's fcntl lock's
  concern (`skipped-overlap`). Two consoles on different ports have independent slots —
  documented, not defended.
- **Console killed mid-run.** The daemon thread dies with the process but the `loopctl`
  child keeps running and records normally; only the status dict is lost, and the next
  console boot reports idle. Accepted for v1.
- **Security.** §13.1's Host/Content-Type gate applies unchanged; the console still emits
  no `Access-Control-*` headers; `<name>` stays `[A-Za-z0-9_-]+`; the status GET leaks
  nothing beyond `/api/state`'s existing confidentiality class.

## 14. Probe channel

The data host runs sshd with ONE forced command (`bin/probe-server`). Loops call
`bin/probe <name>` and never know whether the probe ran locally or over ssh. The
server never interprets a shell. Shared header parsing, clean env, timed exec,
and list/hash live in `bin/probe_core.py` so the two scripts cannot drift.
`PROBE_SERVER_VERSION = 1`.

### 14.1 authorized_keys line

```
restrict,command="<abs path of bin/probe-server>" ssh-ed25519 <body> loops-probe
```

`bin/probe-server --authorize <pubkey-file> [--write] [--replace]` reads one
`ssh-ed25519 …` line and prints that restrict line. `--write` appends it to
`~/.ssh/authorized_keys` (created 0600 if missing; refused if the file mode is
not 0600 or the dir is not 0700 — the message says which). Refused if the same
key (by its base64 body) is already present unless `--replace`, which rewrites
that key's line in place. Not reachable as the forced command: when
`sys.argv[1:]` is non-empty AND `SSH_ORIGINAL_COMMAND` is set the server
refuses, exit 64, and does not authorize.

### 14.2 Wire grammar

`SSH_ORIGINAL_COMMAND` is parsed with **no shell**: `parts = cmd.split(" ")`
(single-space split). An empty string, two consecutive spaces, a
leading/trailing space, a tab or newline anywhere → `refused: <reason>` on
stderr, exit **64**, logged, nothing executed. `verb = parts[0]`, `args =
parts[1:]`. Verb must match `^[a-z][a-z0-9-]{1,40}$`; each arg must match
`^[A-Za-z0-9_.:@/=+-]{1,8192}$`; `len(args) <= 8`. Otherwise the same refusal.

ROOT is the repo root containing the server script
(`os.path.dirname(os.path.dirname(os.path.realpath(__file__)))`), never from
the client's env. `.env` is loaded via `loopconf.load_env(ROOT)`; on
`EnvFileError` → `refused: .env: <msg>`, exit 64.

### 14.3 Built-ins

Not files. Names `ping`, `list`, `check` are **reserved**: a file by one of
those names in `probes/` is refused (`refused: reserved name`) and omitted from
`list`. Built-ins take no other args (`ping x` → refused). `check` takes exactly
one name.

- `ping` → stdout `ok probe-server 1 <hostname>`, exit 0.
- `list` → one line per executable regular file in `ROOT/probes/` whose name
  matches the verb regex, sorted: `<name> <first 12 hex of sha256 of the file
  bytes>`. Symlinks skipped.
- `check <name>` → exactly as running the probe `<name>` with argv `["--check"]`
  (same exec path, same timeout, same log).

### 14.4 Header grammar

Parsed from the first 20 lines of the script. The block must start at line 2
(right after the shebang); parsing stops at the first line that is not a
`# probe-*:` line.

```
# probe: <name>                 (must equal the file name)
# probe-timeout-s: <int>        (optional; default 120; values above 600 are clamped to 600)
# probe-writes: <text>          (required; "none" or a statement)
# probe-output: json|tar|text   (required)
# probe-reads: <text>           (required; free text)
```

Missing required line, or `probe:` name ≠ file name → `refused: bad header: …`,
exit 64.

Every probe, invoked with the single argument `--check`, verifies its own
inputs exist, prints one line, exits 0 (ok) or 1 (unmet), and touches nothing.

### 14.5 Exec + clean env + timeout

`path = ROOT/probes/<verb>` must be a regular file (`os.path.islink` → refused),
executable, with a valid header. Exec `[path, *args]` with `cwd=ROOT`,
`stdin=/dev/null`, a **clean env** = `{HOME, PATH=requirements.runtime_path(home),
LOOPS_ROOT=ROOT, LANG=C.UTF-8}` plus every key from `.env` — never
`SSH_ORIGINAL_COMMAND`, never the inherited environment. stdout/stderr inherit.
Started in its own process group (`start_new_session=True`). Header timeout
(default 120 s, cap 600 s): on expiry `killpg(TERM)`, 10 s grace, `killpg(KILL)`,
exit **124** with `probe timed out after N s` on stderr. Otherwise exit with the
probe's exit code.

### 14.6 Log

One line per invocation (including refusals and built-ins) to
`ROOT/state/probe-log/<YYYY-MM-DD>.log` (dir created 0700, file 0600, UTC date):

```
<ISO-8601 UTC> client=<SSH_CLIENT first field or "-"> verb=<verb> args=<args joined by space, or "-"> exit=<n> ms=<duration>
```

When `verb == "ticket-add"` log `args=<redacted>`. On every start, delete log
files older than 30 days (by the date in the file name).

### 14.7 Client (`bin/probe`)

```
bin/probe <name> [arg …] [--out FILE]
bin/probe --check <name>
bin/probe --list
bin/probe --ping
```

ROOT like the server; `.env` via `loopconf.load_env` (malformed → stderr + exit
64). `LOOPS_PROBE_HOST` unset/empty → **local mode**; set → **remote mode**.

Local: built-ins answered locally (`ping` → `ok probe local <hostname>`; `list`
→ the same hash lines); a probe is exec'd exactly as the server would.

Remote: `argv = [ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o",
"IdentitiesOnly=yes", "-i", key, "--", host, name, *args]` where `ssh =
os.environ.get("LOOPS_SSH", "ssh")`, `key = .env LOOPS_PROBE_KEY or
~/.ssh/loops-probe` (expanded), `host = LOOPS_PROBE_HOST`. The `--` sits
**before** the host. Args are separate argv elements; the client validates them
with the server's regexes first and refuses locally (exit 64) so a bad arg
never travels. ssh exit 255 → stderr `probe transport failed: <ssh stderr>`,
exit **75**. Any other exit code passes through.

`--out FILE`: stdout goes to `FILE` created 0600 (temp file in the same
directory, renamed into place on success; on failure the temp is removed and
`FILE` untouched).

`--check <name>`: local → run `probes/<name> --check`. Remote → `ping` must
answer `ok …`, `list` must contain `<name> <hash>` equal to the hash of the
client's own `ROOT/probes/<name>` (else exit 3, stderr `probe drift: <name>
server=<h1> client=<h2>` or `probe not offered by server: <name>`), then
`check <name>` must exit 0. One `ping`+`list` round-trip per process (in-memory
cache). Exit 0 ok / 3 unmet / 75 transport.

`--list`: prints the server's (or local) list.

Client exits: 0 / the probe's / 3 (check unmet) / 64 (refused) / 75 (transport)
/ 124 (timeout passthrough).

### 14.8 `loopctl probe`

`status` (default): mode (local/remote), host, key path + present/missing, ping
result (or `transport failed`), table `name | client-hash | server-hash |
status` (`ok` / `drift` / `not on server` / `not local`). Exit 0 if everything
`ok`, 1 otherwise. Never writes.

`keygen`: `ssh-keygen -t ed25519 -N "" -f <key> -C "loops-probe <hostname>"`
(refuse if the key exists; `LOOPS_SSH_KEYGEN` is the test seam). Prints the
public key, a `~/.ssh/config` stanza (`Host llm-probe`), the
`ssh-keyscan -t ed25519 <HostName> >> ~/.ssh/known_hosts` line, `.env`
`LOOPS_PROBE_HOST=llm-probe`, and `bin/probe-server --authorize <pubkey>
--write`.

### 14.9 Two-checkout deploy rule

After cutover the probes execute from llm's checkout and the loops from
firstparty's. The probe key cannot pull. A change to `probes/`, `bin/probe`, or
`bin/probe-server` is pushed from llm and pulled on firstparty before any loop
that uses it is installed. A name the server does not list, or lists with a
different content hash, is unmet.
