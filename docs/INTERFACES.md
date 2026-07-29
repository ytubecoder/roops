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
  examples/<name>/                 # pilot loops, same shape; NEVER installed to launchd
  state/loops.sqlite               # WAL; runs, heartbeats, metrics (§3)
  state/runs/<run_id>/             # contract.json, output.md, usage.json, engine.log, engine.status
  state/locks/<loop>.lock          # lock files (§2)
  state/loop-data/<name>/          # loop-private durable state, 0700/0600 (Amendment 2)
  reports/<name>/YYYY-MM-DD-HHMM.md
  reports/<name>/latest.md         # atomically promoted symlink-free copy
  reports/<name>/latest.json       # atomically promoted copy of contract.json
  reports/<name>/YYYY-MM-DD-HHMM.html + latest.html   # promoted report pages (Amendment 2)
  dashboard/generate.py            # → dashboard/loops.html (§10)
  dashboard/reports.html           # reports screen (Amendment 2, §10)
  launchd/com.loops.<name>.plist   # generated; gitignored
  docs/…
```

`state/`, `reports/`, `launchd/*.plist`, `dashboard/loops.html`, `dashboard/reports.html` are gitignored — they are runtime
artifacts. Every script must create the directories it needs (`mkdir -p`) rather than assuming.

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

CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT);
-- schema_meta('schema_version') = '1'
```

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
db.py query <name> [args...]        # named read queries; JSON to stdout. Names:
                                    #   loops-summary                     (latest run per loop)
                                    #   last-runs   --loop L --limit N
                                    #   metric-history --loop L --key K --days D
                                    #   open-findings  --loop L
                                    #   heartbeats  --loop L --limit N
                                    #   spend       --days D              (per-loop token/cost sums)
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
   `enabled=false` unless `--trigger manual` (exit 0, no run row).
2. **Acquire lock** (§2, non-blocking). On contention: insert a run row with
   `runner_status=skipped-overlap`, `started_at=finished_at=now`, and exit **0** (an overlap is not
   an error — it must not make launchd think the job is broken).
3. `run_id` = `<UTC>-<name>-<6 hex>`, e.g. `20260722T140311Z-hello-loop-a1b2c3`. `mkdir -p 0700
   state/runs/<run_id>`. `db.py start-run`.
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
   `engines/<engine>.sh` with the env of §6, inside its **own process group** (`set -m` +
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
8. Retention: prune `reports/<name>/*` and `state/runs/*` older than `retention_days` (default 30;
   `latest.md`, `latest.json`, `latest.html` never pruned (the runner's keep-list names all three explicitly — Amendment 2)). SQLite rows are kept forever.

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
`run-loop.sh` reads config via `loopconf.py get`/`parse --json`; `loopctl` and
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
| `type` | yes | `agent` \| `watchdog` | — | watchdog ⇒ `precheck.sh` required |
| `engine` | yes | `codex` \| `claude` | — | must have `engines/<engine>.sh` |
| `model` | no | string | engine default | passed through as `MODEL` |
| `schedule` | yes | §5.1 grammar | — | `manual` = never installed |
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

### 5.1 Schedule grammar
| form | meaning | launchd | expected interval (staleness) |
|---|---|---|---|
| `manual` | never scheduled | — (install refuses) | ∞ |
| `interval:15m` / `interval:2h` | every N | `StartInterval` (seconds) | N |
| `daily:07:30` | every day at local 07:30 | `StartCalendarInterval{Hour,Minute}` | 24h |
| `times:07:30,19:30` | those local times daily | array of `StartCalendarInterval` | 86400/count |
| `weekly:mon:08:00` | that weekday, local | `+Weekday` (0=Sun … 6=Sat) | 7d |
| `monthly:01:09:00` | that day-of-month, local | `+Day` | 30d |
All calendar times are **local**, matching launchd semantics. Parsing lives in one place:
`bin/schedule.py` (`parse(spec) -> {kind, launchd: {...}, expected_interval_s: int}`), imported by
`loopctl` and `dashboard/generate.py`. **launchd sleep semantics** (document, don't fight):
calendar events missed while asleep coalesce into a single firing at wake; `StartInterval` firings
during sleep are simply missed. "Next run" on the dashboard is therefore explicitly best-effort.

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
loopctl new <name> [--type agent|watchdog] [--engine codex|claude]   # scaffold from templates
loopctl validate [<name>|--all]                                      # §5 + §5.2 checks; exit 1 on any fail
loopctl run <name> [--trigger manual]                                # foreground; streams progress
loopctl list                                                         # table: name, type, engine, schedule, enabled, installed?
loopctl status [<name>]                                              # last run, status, headline, next-run (best effort)
loopctl install <name>                                               # generate plist → bootstrap → kickstart-verify (§8.1)
loopctl uninstall <name>                                             # bootout + remove plist
loopctl pause <name> / resume <name>                                 # sets enabled= and bootout/bootstrap
loopctl dashboard                                                    # regenerate + print path
loopctl findings <loop>                                              # open findings: id, severity, age, times_seen, disposition
loopctl ack <loop> <finding_id> [--note …]                           # Amendment 1 disposition verbs —
loopctl dismiss <loop> <finding_id> --note …                         #   note REQUIRED (audit trail)
loopctl snooze <loop> <finding_id> --until YYYY-MM-DD                #   --until REQUIRED
loopctl reopen <loop> <finding_id>
```
Disposition verbs are thin wrappers over `db.py dispose` (+ dashboard regen so the change is
visible immediately). The dashboard stays static (Change 4, Option A — settled with generalissimo
2026-07-22): dispositions enter via this CLI only.
Global flags: `--root R` (default `$LOOPS_ROOT`), `--json` (machine-readable output where sensible),
`--from loops.d|examples`. Exit codes: `0` ok · `1` operation failed · `2` usage.

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
2. Write `launchd/com.loops.<name>.plist` with **absolute** paths, `WorkingDirectory`,
   `EnvironmentVariables` (at minimum `HOME`, `PATH`, `LOOPS_ROOT`), `StandardOutPath` /
   `StandardErrorPath` under `state/`, and the schedule from §5.1.
3. `launchctl bootout gui/$UID/com.loops.<name>` (ignore failure) → `launchctl bootstrap
   gui/$UID <plist>`.
4. `launchctl kickstart -p gui/$UID/com.loops.<name>` and then **verify a fresh run row appeared
   with a non-failed runner_status**; if not, report failure loudly and leave the job booted out.
   Env/auth breakage only surfaces in the real launchd context — this step is the point.

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

`python3 dashboard/generate.py [--root R] [--out FILE]` → writes `dashboard/loops.html` via
**tmp file + `os.rename`** (never a partially-written page, even under concurrent runs). Reads only
sqlite + `reports/*/latest.*` + `loops.d/*/{loop.conf,dashboard.json}`. Self-contained single file:
inline CSS/JS, no network requests, no external assets (it is opened as `file://`).

- **Global view:** one row per loop — precedence-resolved status light (§4.3), headline, last run
  (relative + absolute), schedule + best-effort next run, 7-day token spend, link to latest report.
- **Top strip:** fleet counts by status, `needs_attention` count, spend today / 7d, last regen time.
- **Stale detection:** a loop overdue by > 1.5 × its `expected_interval_s` (§5.1) is flagged
  `stale` and counts toward `needs_attention`. `manual` loops are exempt.
  **(Amendment 2026-07-30):** staleness applies only to *installed* loops. Install state is a
  display-only check: `launchd/com.loops.<name>.plist` exists (file presence, never a
  `launchctl` subprocess — the generator stays hermetic). A non-manual loop without a loaded
  schedule renders as 休 "no schedule loaded" (supervised-only), is staleness-exempt, and does
  not count toward `needs_attention` on schedule grounds; its status still counts as before.
  Rationale: a supervised-only fleet rendered wall-to-wall `stale`, and fake next-run
  estimates ("in 4m" for a loop that will never fire) made the column meaningless.
- **Died-run detection (§4.6):** a run row with `finished_at IS NULL` older than
  `timeout_s + 120s` renders as `died` (red, harness-problem marker) and counts toward
  `needs_attention`.
- **Status light** uses `effective_status` (§4.5) under the §4.3 precedence — never raw
  `loop_status`.
- **Per-loop sections:** declared panels, trends (from the `metrics` table), a **findings list**
  (from sqlite + `latest.json`, never parsed from markdown): open findings with recurrence and
  disposition rendered as text — e.g. `3rd report · dismissed 2026-06-01 ("note")` — suppressed
  findings shown greyed/collapsed, not hidden; a recent-runs table including runner_status,
  report links, and the raw fallback panel. The dashboard is static (Change 4 Option A):
  dispositions are entered via `loopctl`, and the page may display the ready-to-paste command.
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
- **Report pages (Amendment 2):** the generator also writes `dashboard/reports.html`
  (same invocation; each output tmp+rename — per-file atomic, the pair is not). Row
  Report cells prefer `../reports/<name>/latest.html` (md link kept secondary); a page
  whose envelope `meta.run_id` ≠ the loop's latest promoted run (newest row with
  `runner_status='completed'` AND non-NULL `contract_path`) gets a `stale` badge. The
  reports screen lists every loop that is page-enabled or has pages on disk: totals chips
  + title + generated_at from the `latest.html` envelope (via `bin/page_envelope.py` —
  display HTML is never scraped; only `latest.html` is ever parsed), dated history from
  filenames only (capped 30), "no page yet" / "no meta" / historical markers per
  `docs/REPORT_PAGES_PLAN.md` §5.2. The §10 read-set gains the `latest.html` envelope.
- Style **(amended 2026-07-30, B-04/B-07)**: the roops garden design system — washi/sumi
  palette, vermillion accent, mincho serif + mono numerals, status rendered as hanko stamps
  (済 ok · 注 warn · 警 alert · 未 no data) over the unchanged §4.3 precedence, per-loop
  tokonoma output alcove in the global row. Visual only: every §10 semantic above is
  untouched. Local system fonts only, no webfonts, no textures via external or data URLs —
  the no-network rule binds the stylesheet too. Function first, dense over airy: it is a
  status board read at a glance, not a marketing page.

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
