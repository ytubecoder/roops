# gc-health-watch — design spec (black-box acceptance)

Date: 2026-09-05. Repo: `~/projects/loops` (this repo). Owner: maguyva-marketing.
Status: approved by Generalissimo in chat 2026-09-05; implementation farmed out.

## 0. Summary

One new read-only probe (`probes/gc-health-read`) and one new watchdog loop
(`loops.d/gc-health-watch`) that surface failures across the Growth Console
(GC) stack — an OpenTwins X-agent logout or a stalled Chrome launch cycle,
Postiz publish errors, and any GC-tracked routine in `error`/`overdue` — as
loop findings. It follows `loops.d/ads-delivery-watch` exactly in shape:
`type=watchdog`, precheck decides deterministically, codex only writes an alarm
up when the precheck has already established one. Zero engine tokens on a
healthy day.

Nothing here logs in anywhere, opens a browser, or writes outside the run dir.
The detection already exists on the data host (llm); this loop only reads it.

Origin: the X agent was logged out 13 of 31 August days and for a 53-hour
stretch 2026-08-31 → 09-02; a deleted Chrome ownership marker then killed every
write for 2026-09-02 with the session healthy. The agent itself wrote both
conditions into its memory files every hour. Nobody read them.

## 1. Definitions the implementer must not re-derive

- **Data host** = llm (macOS). Probes execute from llm's checkout of this repo
  under `bin/probe-server`. Loops execute on firstparty. See
  `probes/README.md` "Two-checkout deploy rule" (verbatim there) and
  `docs/INTERFACES.md` §14. The implementer builds and tests hermetically; the
  foreman deploys.
- **GC** = `~/projects/maguyva-marketing/growth-console`, a Python project
  with its own venv at `growth-console/.venv/bin/python`. Its schedules ledger
  is `console.dashboard.schedules.collect_schedules(brand_dir)` returning
  `{"automated": [row…], "manual": [row…], "live": [...]}`; each row has keys
  `name, status, last_ok, error, last_error_at, schedule, method, feeds, note,
  count`; `status ∈ {ok, error, overdue, never, off, ondemand, untracked}`.
  `brand_dir` comes from `console.brands.resolve(None)`.
- **OT_HOME** = `~/.opentwins`. Twitter agent workspace at
  `$OT_HOME/workspaces/agent-twitter/` with `memory/YYYY-MM-DD.md` (brand-local
  EDT dates, one file per day, entries headed by a UTC timestamp) and
  `schedule.json` (today's task ledger, regenerated daily). Daemon logs at
  `$OT_HOME/logs/opentwins-YYYY-MM-DD.log` (UTC-dated, one JSON object per
  line: `{"ts","level","mod","msg","data":{…}}`).
- **Postiz** public API: base `https://api.postiz.com/public/v1`, header
  `Authorization: <POSTIZ_API_KEY>` (raw key, no `Bearer`), `Accept:
  application/json`. `GET /integrations` → list of
  `{id, identifier, name, disabled, picture, profile}`. `GET
  /posts?startDate=<ISO Z>&endDate=<ISO Z>` → either a list or a dict with one
  of the keys `posts|data|items|results` holding the list; each post has
  `state` (`QUEUE|PUBLISHED|ERROR|DRAFT`, upper-case it defensively),
  `publishDate` (ISO Z), `integration: {id, providerIdentifier, name, …}`,
  `id`, `releaseURL`. The key lives in `growth-console/.env` as
  `POSTIZ_API_KEY=…` (optional `POSTIZ_API_BASE=…`).

## 2. Probe: `probes/gc-health-read`

Python 3 (stdlib only — it runs under the data host's system `python3` with a
clean env; `urllib.request` for HTTP, no `httpx`). Regular file, executable.

Header (must be exactly this block at lines 2–6):

```
#!/usr/bin/env python3
# probe: gc-health-read
# probe-timeout-s: 180
# probe-writes: none
# probe-output: json
# probe-reads: growth-console venv + schedules ledger under MAGUYVA_REPO (default ~/projects/maguyva-marketing); ~/.opentwins (env OT_HOME override); Postiz public API using POSTIZ_API_KEY from growth-console/.env
```

### 2.1 Overrides (the testability seam)

- `MAGUYVA_REPO` — repo root; default `~/projects/maguyva-marketing`.
  `GC_DIR = $MAGUYVA_REPO/growth-console`, `GC_PY = $GC_DIR/.venv/bin/python`,
  `ENV_FILE = $GC_DIR/.env`.
- `OT_HOME` — default `~/.opentwins`.
- `POSTIZ_API_KEY`, `POSTIZ_API_BASE` — taken from the environment if set,
  otherwise parsed from `ENV_FILE` (lines `KEY=VALUE`, optional surrounding
  single/double quotes, `#` comment lines ignored; **never overwrite a key
  already in the environment**). Key values are never printed anywhere.
- `GC_HEALTH_NOW` — ISO-8601 UTC instant (e.g. `2026-09-04T21:00:00Z`) used
  as "now" for every date computation when set; default = real UTC now. Tests
  set it so day windows are deterministic.

### 2.2 `--check`

Verifies: `GC_PY` is an executable file; `$OT_HOME/workspaces/agent-twitter`
is a directory; a Postiz key is resolvable (env or `ENV_FILE`). All met →
print `ok gc-health-read`, exit 0. Otherwise print one line naming the first
unmet input, exit 1. Touches nothing.

### 2.3 Exit and error policy (decision — do not vary)

The probe **always exits 0** on a normal run and reports failures **in band**:
each section carries `"error": null | "<message>"`, and a section error also
produces a finding `probe:<section>-read-failed` (severity `warn`). Rationale:
`bin/probe --out` only commits the output file on exit 0, so an out-of-band
non-zero exit would leave the precheck with nothing to show. Section
collection is isolated — one section failing never blanks another. Only a
crash outside all sections (a bug) may exit non-zero.

### 2.4 Output (single JSON object on stdout)

```json
{
  "probe": "gc-health-read",
  "generated_at": "2026-09-04T21:00:00Z",
  "now": "2026-09-04T21:00:00Z",
  "sections": {
    "schedules": {"error": null, "rows": [...], "excluded": [...], "manual": [...]},
    "opentwins": {"error": null, "session": {...}, "launches": [...], "tasks": {...}},
    "postiz":    {"error": null, "integrations": [...], "posts": {...}}
  },
  "findings": [{"id": "...", "severity": "warn|alert", "detail": "..."}]
}
```

`findings` is the complete, deterministic list; the precheck adds nothing.
Order: by section (schedules, opentwins, postiz, then `probe:*`), then by id.
No finding carries `info` severity — informational context lives in the
sections. Details never contain secrets, never contain full memory-file
lines longer than 200 characters, and never contain file contents beyond the
quoted evidence fragment.

### 2.5 Section `schedules`

Run `GC_PY` as a subprocess (cwd = `GC_DIR`, timeout 90 s, argv — never a
shell string):

```
GC_PY -c "import json; from console import brands; from console.dashboard.schedules import collect_schedules; print(json.dumps(collect_schedules(brands.resolve(None)), default=str))"
```

Parse stdout as JSON. Non-zero exit, timeout, or unparseable stdout → section
`error` (include the first 200 chars of stderr).

- `rows` = `automated` rows **minus the exclusions**, each reduced to
  `{name, status, last_ok, error, last_error_at, schedule}`.
- **Exclusions (by name, listed in `excluded` with a reason):**
  1. `gc cache warmer` — its last-run lives in the dashboard process's memory
     and always reads `never` out-of-process.
  2. any name matching `^ads-[a-z]+ loop$` — llm-local loop state is dead by
     design since the fleet moved to firstparty on 2026-08-23; the fleet
     self-reports there. (Keep the regex; do not hard-code the five names.)
- `manual` = the `manual` rows reduced the same way, for context only.
  **Manual rows never produce findings** — a routine nobody has run is not a
  failure.

Findings from `rows`:
- `status == "error"` → id `gc:<slug>:error`, severity `alert`, detail
  `"<name>: <error[:200]> (last error <last_error_at>, last ok <last_ok>)"`.
- `status == "overdue"` → id `gc:<slug>:overdue`, severity `warn`, detail
  `"<name>: last ok <last_ok>, expected <schedule>"`.
- `ok`, `off`, `never` → no finding.

`slug(name)` = lower-case, every run of non-`[a-z0-9]` → `-`, leading/trailing
`-` stripped. `"opentwins twitter heartbeat"` → `opentwins-twitter-heartbeat`;
`"linkedin notifications"` → `linkedin-notifications`.

### 2.6 Section `opentwins`

All three sub-parts read the twitter agent only (reddit is retired).

**`session`** — from the two newest `memory/YYYY-MM-DD.md` files (sorted by
file name; fewer than two is fine; none → sub-part `error`). Split each file
into entries at header lines matching

```
^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC\b.*Heartbeat
```

(the header sometimes carries a parenthesised EDT time between `UTC` and
`- Heartbeat`; group 1 is the UTC stamp). Classify each entry by evidence,
all regexes case-insensitive, searched over the entry body:

- `LOCKED`:     `account has been locked|account/access`
- `LOGGED_OUT`: `mode=login|no twid|hasTwid[^,;)\n]{0,15}false|\[\[twitter_session_logout\]\][^\n]{0,40}(STILL DOWN|RELAPSED|LOGGED OUT|LOGIN WALL)`
- `LOGGED_IN`:  `hasTwid[^,;)\n]{0,15}true|hasCt0/hasTwid all true|\[\[twitter_session_logout\]\][^\n]{0,40}(healthy|RESOLVED|RECOVERED|stable)`

Rules: `LOCKED` anywhere → `locked`. Else if both `LOGGED_OUT` and
`LOGGED_IN` match, the match whose **last occurrence starts later in the
entry** wins (the agent narrates chronologically: a recovery entry may recount
the outage before confirming the live session). Only one → that state. Neither
→ `unknown`. Note the trap: `[[twitter_session_logout]] still healthy` must
classify `logged_in` — the word `STILL` alone means nothing; only the exact
phrase `STILL DOWN` is logged-out evidence.

Real evidence fragments (use them verbatim as fixture material):

```
- § 2/§ 3: … Session health check: sideNav/primaryColumn/hasCt0/hasTwid all true, accountName "maguyva", title "(4) Notifications / X" - [[twitter_session_logout]] healthy, no login-redirect, session now stable across the full Day58
- 🚨 § 2/§3: [[twitter_session_logout]] RELAPSED - Browser open + explicit navigate to /notifications/mentions both {ok:true} (API-layer only, doesn't confirm …
- 🚨 § 2/§3: [[twitter_session_logout]] STILL DOWN - 23rd consecutive check on this relapse (~17h45m in, since 07:18 EDT 08-31 first detection). …
… `https://x.com/i/jf/onboarding/web?redirect_after_login=%2Fnotifications%2Fmentions&mode=login`, title "X - The Everything App / X", cookies only guest_id/guest_id_marketing/guest_id_ads/gt/__cuid (no twid/ct0/auth_token). Genuine full session logout
- 🚨 § 2/3: [[twitter_session_logout]] RECOVERED - opened browser, navigated to /notifications/mentions, combined session-state evaluate confirmed genuine live session: sideNav:true, primaryColumn:true, hasCt0:true, hasTwid:true
- § 2/§ 3: … [[twitter_session_logout]] still healthy.
```

Two real header shapes: `## 2026-08-31 04:43 UTC (00:43 EDT) - Heartbeat (Day55, 1st run)` and `## 2026-09-04 04:41 UTC - Heartbeat (Day59, 1st run)`.

Output `session`:
```json
{"state": "logged_in|logged_out|locked|unknown",
 "as_of": "<UTC stamp of the newest entry with evidence>",
 "since": "<UTC stamp of the first entry in the current run of that state>",
 "consecutive": <int>, "entries_examined": <int>, "files": ["…md","…md"],
 "last_logged_in_at": "<UTC stamp of the newest logged_in entry, or null>",
 "login_did_not_stick": true|false}
```
`state` is the classification of the **newest entry with evidence**
(`unknown` entries are skipped when determining state and do not break a
run). `login_did_not_stick` is true when the current state is `logged_out`
and a `logged_in` entry exists, before the current run started, within 6 hours
of `since`.

Findings:
- `logged_out` → `opentwins:twitter:logged-out`, `alert`, detail
  `"X agent (@maguyvaai) logged out since <since> UTC (as of <as_of>, N consecutive heartbeats); last healthy <last_logged_in_at>. Re-login is human-only: the ot-twitter Chrome via x-ads-tools/ot-chrome-start.sh, never a raw launch."`
  Append `" A login was recorded at <last_logged_in_at> and did not stick."`
  when `login_did_not_stick`.
- `locked` → `opentwins:twitter:locked`, `alert`.
- `logged_in` / `unknown` → nothing.

**`launches`** — for each of the two most recent UTC day files that exist
(`opentwins-<YYYY-MM-DD>.log` for `now` and `now − 1 day`), count lines whose
parsed JSON has the given `msg` and either `data.profile == "ot-twitter"` or
`data.platform == "twitter"`:

| key | `msg` |
|---|---|
| `runs` | `Run started` |
| `completed` | `Run completed` |
| `launched` | `Chrome launched` |
| `quit` | `Chrome quit` |
| `deferred` | `Deferred (browser leased)` |
| `cdp_errors` | `navigate failed` **plus** `evaluate failed` |

Malformed lines are skipped, never fatal. Output `launches` = list of
`{"day": "YYYY-MM-DD", "runs", "completed", "launched", "quit", "deferred", "cdp_errors"}`
newest first (missing files simply absent).

Findings, evaluated on **the most recent day whose `runs ≥ 3`** only (a
partial morning has too few runs to judge):
- `launched == 0` → `opentwins:twitter:launch-cycle-stalled`, `alert`, detail
  `"<day> UTC: <runs> heartbeats ran but Chrome was launched 0 times — the daemon is attaching to a stale window it does not own, so every write fails silently (typedLen:0). Usually a deleted ~/.opentwins/locks/chrome-ot-twitter.pid; repair: OT_OWNER=<owner> x-ads-tools/ot-chrome-start.sh --finish (see CLAUDE.md)."`
- `cdp_errors ≥ 10` → `opentwins:twitter:cdp-errors`, `warn`, detail with the
  count and day.

**`tasks`** — `schedule.json`: `for_date = metadata.for_date`, `counts` by
`status` over `tasks[]`, `failed` = list of `{id, action, time, notes[:160]}`
for status `failed`, `typed_len_zero` = number of failed tasks whose `notes`
contains `typedLen:0`. Missing/unparseable file → sub-part `error`.

Findings (only when `for_date` equals the brand-local date of `now` or the day
before, computed in `America/New_York` via `zoneinfo`; otherwise the ledger is
stale and says nothing about today):
- `counts.done == 0 and counts.failed ≥ 3` → `opentwins:twitter:writes-failing`, `alert`,
  detail `"<for_date>: 0 tasks done, <failed> failed (<typed_len_zero> with typedLen:0), <pending> pending"` plus up to 3 failed `action: notes` fragments.
- `typed_len_zero ≥ 3` (and the above did not fire) → `opentwins:twitter:paste-failures`, `warn`.

Sub-part errors (session/launches/tasks) are recorded as
`sections.opentwins.error` = joined messages and produce ONE
`probe:opentwins-read-failed` warn finding; the sub-parts that did succeed are
still reported and still evaluated.

### 2.7 Section `postiz`

`GET /integrations` and `GET /posts` with `startDate = now − 14 d`,
`endDate = now + 1 d` (ISO, `Z` suffix, millisecond precision not required).
HTTP timeout 20 s each. Any exception or non-2xx → section `error`
(status code or exception class + message, no URL query, no key) and finding
`probe:postiz-read-failed` (`warn`).

Output:
```json
{"error": null,
 "integrations": [{"identifier": "x", "name": "maguyva", "disabled": false}, …],
 "posts": {"window_days": 14, "total": N, "by_state": {"PUBLISHED": n, …},
           "error": [{"id","publishDate","integration"}…],
           "missed": [{"id","publishDate","integration"}…]}}
```
`integration` on a post = `post.integration.providerIdentifier` (fallback
`post.integration.identifier`, then `"unknown"`). A post is **missed** when
`state == "QUEUE"` and `publishDate < now − 30 min`.

Findings:
- integration `disabled == true` → `postiz:<identifier>:disabled`, `alert`.
- ≥1 `ERROR` post for an integration → `postiz:<identifier>:post-error`,
  `warn`, ONE finding per integration (never per post — ids must stay stable),
  detail lists up to 5 `publishDate` + `id`.
- ≥1 missed post for an integration → `postiz:<identifier>:post-missed`, `warn`, same shape.
- An empty queue is **not** a finding (posting has had no timer since April;
  that is a known, separate item).

## 3. Loop: `loops.d/gc-health-watch/`

Files: `loop.conf`, `precheck.sh`, `prompt.md`, `dashboard.json`, `SPEC.md`.
Mirror `loops.d/ads-delivery-watch/` for tone and structure; copy nothing
that this spec contradicts.

### 3.1 `loop.conf` (strict KEY=value grammar, never sourced, unknown keys fail validate)

```
name=gc-health-watch
owner=maguyva-marketing
description="Surfaces failures across the Growth Console stack that already sit unread on the data host: the OpenTwins X agent logged out or its Chrome launch cycle stalled, Postiz publish errors or a disabled integration, and any GC-tracked routine in error or overdue."
type=watchdog
engine=codex
schedule=daily:09:15
timeout_s=300
requires="probe:gc-health-read"
retention_days=365
perm_fs_write=report_only
perm_network=none
perm_local_exec=none
perm_remote_mutation=none
notes="<one paragraph: see §3.5>"
```

Cadence rationale (goes in the file's comments and SPEC.md §4): the X agent
checks its own session every hour; the only human action on the far side of
any finding here (a re-login, a Postiz reconnect, a scraper restart) is a
same-day chore, so a morning read is the useful resolution. 09:15 sits right
after `ads-delivery-watch` at 09:00 so the two morning alarms arrive together.

### 3.2 `precheck.sh` (THIS IS THE JOB)

`set -euo pipefail` is fine here (the probe always exits 0; only transport
can fail). Requirements:

- Inputs dir: `INPUTS="${OUT_DIR:?OUT_DIR required}/inputs"` — **`OUT_DIR`,
  not `LOOP_RUN_DIR`** (the runner never sets `LOOP_RUN_DIR`; two existing
  loops carry that bug and write to `/tmp/inputs` — do not copy them).
- Call `"$LOOPS_ROOT/bin/probe" gc-health-read --out "$INPUTS/gc-health.json"`.
  On non-zero exit print a block beginning `PROBE TRANSPORT FAILED — could not
  reach the probe host (llm).`, state that this is an input gap and not
  evidence of health, show the first 20 stderr lines, and `exit 1`.
- Otherwise render, via an inline `python3 - "$INPUTS/gc-health.json"` heredoc
  (no shell string building), exactly this shape (values illustrative):

```
# gc-health-watch precheck — 2026-09-05T01:15:02Z
probe generated 2026-09-05T01:15:01Z

## schedules (10 rows examined · 6 excluded by policy)
  ok         opentwins twitter heartbeat        last ok 2026-09-05 05:33
  off        opentwins reddit heartbeat         last ok 2026-07-25 05:31
  overdue    linkedin notifications             last ok 2026-09-03 06:19  expected every 20 min
  excluded: gc cache warmer (in-process only); ads-google loop, … (fleet state lives on firstparty)

## opentwins
  session: logged_in since 2026-09-02 16:19 UTC (as of 2026-09-04 20:31 UTC, 52 consecutive)
  launches 2026-09-04 UTC: runs 20 · launched 20 · quit 20 · deferred 0 · cdp-errors 0
  launches 2026-09-03 UTC: runs 22 · launched 6 · quit 6 · deferred 0 · cdp-errors 32
  tasks 2026-09-04: done 7 · failed 2 · pending 4 · typedLen:0 in 1

## postiz
  integrations: facebook ok · x ok · linkedin-page ok
  posts last 14d: 0 total (queue empty)

## findings (1)
[WARN] gc:linkedin-notifications:overdue — linkedin notifications: last ok 2026-09-03 06:19, expected every 20 min
```

  A section with `error` prints `  ERROR: <message>` under its heading instead
  of its rows. Then `exit 1` if `findings` is non-empty, else `exit 0`
  (silent green: the runner records the first stdout line and skips the
  engine).
- Never print secrets; the probe already omits them, the precheck adds none.
- Output must be byte-deterministic for the same JSON input except the first
  line's timestamp.

### 3.3 `prompt.md`

Same job as `ads-delivery-watch/prompt.md`: the precheck has decided; the
engine writes the alarm up in one read for a human. Must contain:

- The statement that `PRECHECK OUTPUT` is the only ground truth; no
  speculation past it, no softening.
- A "What the conditions mean" section covering every id family from §2:
  `gc:<row>:error|overdue`, `opentwins:twitter:logged-out` (say plainly that
  re-login is human-only and that a raw Chrome launch on the ot-twitter
  profile destroys the cookie jar — direct to `x-ads-tools/ot-chrome-start.sh`),
  `opentwins:twitter:locked`, `opentwins:twitter:launch-cycle-stalled` (the
  ownership-marker mechanism, repair = `ot-chrome-start.sh --finish`),
  `opentwins:twitter:cdp-errors`, `opentwins:twitter:writes-failing`,
  `opentwins:twitter:paste-failures` (a known chronic per-surface bug —
  informational unless it spreads), `postiz:<integration>:disabled|post-error|post-missed`,
  `probe:<section>-read-failed` (an input gap: say the channel is dark, never
  report that section as healthy).
- "What NOT to do": never act, never describe an action as taken, never
  report anything healthy on the strength of a failed probe or a missing section.
- The output contract: one JSON object per `contract/contract.schema.json`;
  `run_id` copied exactly from `## RUN CONTEXT`; `metrics` is a JSON **string**
  encoding an object (`"{}"` allowed); `findings` may be `[]` but here never
  is, because the engine only runs when the precheck found something.
- The three findings rules from `docs/INTERFACES.md` §6.2 verbatim (re-emit a
  still-true finding under the same id; do not re-argue DISMISSED unless
  materially changed; still emit SNOOZED — suppression is the runner's job).
- A `## Finding identity` heading (mechanically required by `loopctl`) stating:
  `finding_id` = the probe's `id`, unchanged, one finding per precheck
  finding line; `title` = a short human phrase; `severity` = the precheck's
  bracketed severity, lower-cased; the same real-world condition re-raises the
  same id every run until it clears.
- Metrics to emit (as the JSON string): `findings.alert`, `findings.warn`,
  `schedules.error_rows`, `schedules.overdue_rows`, `opentwins.session`
  (0 = logged_in/unknown, 1 = logged_out/locked), `opentwins.launched_latest_day`,
  `postiz.error_posts`, `postiz.missed_posts`.

### 3.4 `dashboard.json`

`{"panels": []}` is acceptable and preferred (the tier-1 row plus the raw
metrics fallback is enough for a watchdog).

### 3.5 `SPEC.md` (intake shape, like `ads-delivery-watch/SPEC.md`)

Sections: 1 Purpose & stop condition · 2 Agentic pattern (human-in-the-loop,
alarm only) · 3 Type & data flow (precheck gathers, engine interprets; list
the three sections and the exclusions with reasons) · 4 Cadence · 5
Permissions (floor; the probe runs unsandboxed inside the precheck, which is
the job for type=watchdog and is not governed by `perm_network`) · 6 Finding
identity (ids stable; the rule from `ads-delivery-watch` about never encoding
counts or dates into an id) · 7 Out of scope: the postiz push having no
timer; the GC `/schedules` ads-loop rows reading llm-local state (a GC-side
fix, tracked separately); reddit (retired 2026-07-25); any loop that logs in.

`notes=` in `loop.conf` carries the one-paragraph version of §3 + §5.

## 4. Tests (mandated — names and assertions are the contract)

Framework and layout follow `tests/test_ads_x_precheck.py` and
`tests/test_gc_actions_probe.py`: stdlib `unittest`, hermetic temp roots,
copy the real `bin/{probe,probe_core.py,loopconf.py,requirements.py,schedule.py}`
into the temp root, `env.pop("LOOPS_PROBE_HOST")` for local mode. No network
beyond `127.0.0.1`. Fixtures under `tests/fixtures/gc-health/` are allowed but
generated-in-test fixtures are preferred.

### 4.1 `tests/test_gc_health_read.py`

A helper builds a fixture `MAGUYVA_REPO` whose
`growth-console/.venv/bin/python` is an **executable shell script** that
prints a canned `collect_schedules` JSON (the probe execs that path; this is
the seam — no extra env var), a fixture `growth-console/.env` with
`POSTIZ_API_KEY=fixture-secret-KEY-123`, and a fixture `OT_HOME` with
`workspaces/agent-twitter/memory/*.md`, `workspaces/agent-twitter/schedule.json`
and `logs/opentwins-*.log`. A local `http.server` on an ephemeral 127.0.0.1
port serves `/integrations` and `/posts` from dicts the test sets;
`POSTIZ_API_BASE` points at it. `GC_HEALTH_NOW` is pinned (e.g.
`2026-09-04T21:00:00Z`). The probe is run through the copied `bin/probe`
in local mode with `--out`, and the JSON is read from the out file.

1. `test_check_ok_with_fixture_inputs` — `--check` exit 0, stdout `ok gc-health-read`.
2. `test_check_unmet_when_venv_missing` — remove the fake venv python → exit 1, stdout names it.
3. `test_all_green_has_no_findings_and_exit_0` — every section `error` is `null`, `findings == []`, out file written, exit 0.
4. `test_error_and_overdue_rows_become_findings` — `linkedin notifications` status `error` → `gc:linkedin-notifications:error` alert; `opentwins twitter heartbeat` `overdue` → `gc:opentwins-twitter-heartbeat:overdue` warn; both details contain the row name.
5. `test_excluded_and_manual_rows_never_alarm` — automated `ads-google loop` overdue, `gc cache warmer` never, manual `website knowledge (maguyva.ai)` overdue → `findings == []`; `sections.schedules.excluded` contains both automated names; `manual` carries the manual row.
6. `test_logged_out_session_from_memory` — two memory files; day 1 ends with a `healthy` entry at 06:00 UTC, day 2 has `RELAPSED` at 07:18 then `STILL DOWN` ×3 → `session.state == "logged_out"`, `since == "2026-08-31 07:18"` (the first RELAPSED entry's stamp, in the entry-header format), `consecutive == 4`, `login_did_not_stick` is true because a healthy entry exists within 6h; finding `opentwins:twitter:logged-out` alert whose detail contains `did not stick`.
7. `test_still_healthy_is_logged_in` — newest entry body `… [[twitter_session_logout]] still healthy.` → `state == "logged_in"`, no session finding.
8. `test_recovery_entry_later_evidence_wins` — one entry contains `(no twid/ct0/auth_token)` early and `hasTwid:true … [[twitter_session_logout]] RECOVERED` later → `logged_in`.
9. `test_locked_outranks_everything` — entry with `account has been locked` and `hasTwid:true` → `locked`, finding `opentwins:twitter:locked` alert.
10. `test_launch_cycle_stalled_alert` — yesterday's log: 23 `Run started` (platform twitter), 0 `Chrome launched` → `opentwins:twitter:launch-cycle-stalled` alert; with 20 launched → no launch finding. Assert the ot-tracker profile's launches are not counted (add 5 `Chrome launched` lines with `profile: "ot-tracker"` to the stalled fixture).
11. `test_cdp_errors_warn` — 21 `navigate failed` + 11 `evaluate failed` on the most recent day with ≥3 runs → `opentwins:twitter:cdp-errors` warn with `32` in the detail.
12. `test_writes_failing_from_task_ledger` — `schedule.json` with `for_date` = brand-local today, 0 done / 12 failed (7 notes containing `typedLen:0`) → `opentwins:twitter:writes-failing` alert and NO `paste-failures` finding; 7 done / 2 failed → neither.
13. `test_stale_task_ledger_is_ignored` — `for_date` three days old with 0 done / 12 failed → no task finding.
14. `test_postiz_disabled_error_and_missed` — integrations `x` disabled; posts: 2 `ERROR` on `linkedin-page`, 1 `QUEUE` on `x` dated 2h before `now`, 1 `QUEUE` on `x` dated 1h after `now`, 3 `PUBLISHED` → findings exactly `postiz:x:disabled` alert, `postiz:linkedin-page:post-error` warn (one finding, detail lists both ids), `postiz:x:post-missed` warn (one id); `posts.by_state.PUBLISHED == 3`.
15. `test_postiz_unreachable_is_input_gap` — `POSTIZ_API_BASE` at a closed 127.0.0.1 port → `sections.postiz.error` non-null, finding `probe:postiz-read-failed` warn, other sections intact, exit 0.
16. `test_schedules_subprocess_failure_is_input_gap` — fake venv python exits 3 with stderr `boom` → `sections.schedules.error` contains `boom`, finding `probe:schedules-read-failed` warn, opentwins/postiz sections intact.
17. `test_no_secret_in_output` — the literal `fixture-secret-KEY-123` appears nowhere in the out file or stdout/stderr, in the healthy AND the postiz-unreachable case.
18. `test_findings_are_deterministically_ordered` — run the probe twice on the same fixtures; `findings` lists are identical and ordered by (section, id).

### 4.2 `tests/test_gc_health_watch_precheck.py`

Runs `loops.d/gc-health-watch/precheck.sh` with `OUT_DIR`, `LOOPS_ROOT`,
`LOOP_NAME=gc-health-watch`, `RUN_ID=test-run`, `WORKDIR`, `HOME` set and a
fake `probes/gc-health-read` in the temp root that prints a canned payload.

1. `test_silent_green_exits_0` — payload with `findings: []` → exit 0; stdout begins `# gc-health-watch precheck — ` and contains `## findings (0)`.
2. `test_findings_exit_1_and_are_rendered` — payload with an alert and a warn → exit 1; stdout contains `## findings (2)`, `[ALERT] opentwins:twitter:logged-out — `, `[WARN] gc:linkedin-notifications:overdue — `, and the `## schedules`, `## opentwins`, `## postiz` headings.
3. `test_section_error_is_rendered_not_hidden` — payload with `sections.postiz.error = "HTTP 401"` and its `probe:postiz-read-failed` finding → exit 1, stdout contains `ERROR: HTTP 401` under `## postiz`.
4. `test_transport_failure_is_input_gap_exit_1` — `LOOPS_PROBE_HOST=x`, `LOOPS_SSH=<fake ssh exiting 255>` → exit 1, stdout contains `PROBE TRANSPORT FAILED`.
5. `test_inputs_land_under_out_dir` — after a green run, `$OUT_DIR/inputs/gc-health.json` exists; the script text does not contain `LOOP_RUN_DIR`.
6. `test_output_deterministic_modulo_timestamp` — two runs on the same payload: `stdout.splitlines()[1:]` equal.
7. `test_prompt_has_finding_identity_heading` — `prompt.md` contains a line `## Finding identity`.
8. `test_loopconf_parses_with_expected_values` — parse `loop.conf` with `bin/loopconf.py`'s loader (see how `tests/test_loopconf.py` does it): `type == "watchdog"`, `schedule == "daily:09:15"`, `requires` contains `probe:gc-health-read`, all four `perm_*` at the floor.

## 5. Allowlist and verify (recorded at dispatch)

Allowed paths (anything else is a scope violation):
```
probes/gc-health-read
probes/README.md                  (one table row for the new probe, nothing else)
loops.d/gc-health-watch/**
tests/test_gc_health_read.py
tests/test_gc_health_watch_precheck.py
tests/fixtures/gc-health/**
```
Verify command: `bash tests/run-tests.sh` (full suite; must pass).

## 6. Definition of done

- All 26 mandated tests exist under the mandated names, assert what §4 says,
  and pass; the full suite passes.
- `probes/gc-health-read` is executable, header exactly as §2, `--check`
  behaves as §2.2, and never prints a key value.
- `loops.d/gc-health-watch/` has all five files; `loop.conf` has only known
  keys; `prompt.md` has `## Finding identity`.
- `PEON_REPORT.md` pastes the actual `bash tests/run-tests.sh` tail and lists
  every file touched.

The foreman (not the peon) will: run `bin/loopctl validate gc-health-watch`,
run the real probe on llm against live data, push llm → pull firstparty
(two-checkout rule), do the supervised first run, and install.
