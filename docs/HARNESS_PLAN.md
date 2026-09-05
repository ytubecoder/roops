# Custom Loop Harness — Plan (harness only)

> **REVISION 9 (final)** — Scope: custom harness + dashboard + loop-authoring contract ONLY. Individual loops = separate context, seeded by **`~/projects/loops/docs/LOOP_SELECTION.md`** (companion file — loop selection/specification lives there, not here). Paperclip/hermes fully out of scope (generalissimo explores Paperclip separately). Engine: codex default, claude switchable. Working directory: **`~/projects/loops`** (generalissimo's choice). **Plan-checked: 3 rounds with Codex — final gate passed; all agreed findings incorporated** (marked ⊕): rounds 1–2 hardened runner/contract/permissions; round 3 gate added per-run contract atomicity, `report_markdown` in-schema emission, and real (credential/allowlist) enforcement for remote-capable CLIs.

## Context

generalissimo is standing up recurring automated "loops" across ~40 projects. Portfolio analysis found zero standing automation against real recurring pain (evidence in `docs/LOOP_SELECTION.md`). After evaluating Paperclip (token-heavy orchestration, CalVer churn) and hermes (redundant hop to the same codex backend), generalissimo chose a **custom thin harness we fully understand**, with: a **dashboard (global + per-loop views)** fed by a **standard reporting contract** (tier 1, required) plus **loop-specific custom reporting** (tier 2, optional but fully displayed), and a defined **authoring process** so developer-agent teams integrate the contract correctly every time.

Verified environment facts (Codex round 1 double-checked): macOS has **no `flock`, no GNU `timeout`**; `codex exec` supports `--json` (JSONL events), `--output-last-message`, `--output-schema`; `claude -p` supports `--output-format json`, `--json-schema`.

## Architecture

```
~/projects/loops/                  # NEW git repo — the whole system; $HOME-relative; bash+python only
  bin/run-loop.sh                        # runner (below)
  bin/loopctl                            # CLI: new|validate|run|list|status|install|uninstall|pause|resume
  bin/lock.py                            # ⊕ fcntl-based lock helper (no flock on macOS; portable to WSL)
  engines/codex.sh                       # DEFAULT — codex exec --output-last-message + --output-schema
  engines/claude.sh                      # claude -p --output-format json --json-schema
  engines/README.md                      # adapter interface spec
  contract/contract.schema.json          # tier-1 JSON schema (single source of truth; engines enforce it ⊕)
  loops.d/<name>/                        # one dir per loop (created via authoring process)
    loop.conf                            # name, schedule, engine, workdir, timeout, type=agent|watchdog,
                                         #   ⊕ permissions (separate axes: fs_write, network, local_exec,
                                         #     remote_mutation — default: read-only, no-network, report-dir-write-only)
    precheck.sh                          # optional deterministic gate
    prompt.md                            # engine-neutral prompt (guardrails embedded)
    dashboard.json                       # tier-2 panel + metric metadata declarations
  state/loops.sqlite                     # ⊕ WAL mode, busy_timeout; runs + heartbeats tables
  state/runs/<run_id>/                   # ⊕ per-run artifacts: contract.json, output.md, usage.json (raw), engine.log
  reports/<name>/YYYY-MM-DD[-HHMM].md    # human report; latest.md/latest.json promoted ATOMICALLY by runner ⊕
  dashboard/generate.py → loops.html     # static; atomic tmp→rename write ⊕
  launchd/                               # generated com.loops.<name>.plist (bootstrap/bootout, abs paths,
                                         #   WorkingDirectory, EnvironmentVariables, Std{Out,Err}Path ⊕)
  examples/                              # ⊕ pilot loops kept permanently as regression fixtures (never installed)
  docs/LOOP_AUTHORING.md                 # contract + build process for loop developers
  docs/LOOP_SELECTION.md                # companion file (already written) — seeds the separate loop-selection context
```

### Runner (`bin/run-loop.sh <name>`)
1. Acquire per-loop lock via `lock.py` (skip if running → runner-status `skipped-overlap`).
2. Generate `run_id`; create `state/runs/<run_id>/`.
3. `precheck.sh` (if present): capped stdout (⊕ size cap, redaction pass, binary rejected); exit 0 + empty → record **amber/skipped** run + heartbeat, done. Non-empty → injected into prompt as `PRECHECK OUTPUT`.
4. `type=watchdog`: precheck IS the job. **⊕ Always writes a heartbeat row even when silent-green** (healthy silence must be distinguishable from scheduler death — the flickki lesson applied to ourselves). Failure output/exit≠0 → escalate to the loop's diagnosis prompt via engine.
5. `type=agent`: invoke engine adapter. **⊕ Runner-owned timeout**: engine spawned in its own process group; TERM → grace → KILL; partial logs preserved; lock always released.
6. **⊕ Contract artifact is per-run and runner-mediated**: engine's schema-enforced final message (via `--output-schema`/`--json-schema`) is captured by the adapter to `contract.json.tmp`; runner validates against `contract.schema.json` and renames to `contract.json` (per-run atomicity too ⊕). The schema includes a `report_markdown` field ⊕ — the runner writes it out as `output.md`/the dated report (the schema-enforced final message IS the whole emission; no separate free-text channel to lose). Only a valid run **atomically promotes** `latest.json`/`latest.md` — a hung/failed engine can never leave stale-green state.
7. Insert run row (nullable token/cost fields, raw usage stored ⊕); regenerate dashboard (atomic write, short global lock ⊕).

### Status model ⊕ (runner-status ≠ loop-status, with precedence)
- **Loop-status** (from contract): `ok | warn | alert` → green/amber/red.
- **Runner-status**: `completed | skipped-precheck | skipped-overlap | engine-failed | engine-timeout | auth-failed | tool-denied | contract-violation | missed-schedule | stale`. ⊕ Auth failures / permission-denials / schema refusals are first-class statuses, not generic red — they mean "fix the harness," not "the loop found a problem."
- Precedence: runner failure states override loop status for the dashboard light; a watchdog's probe failure stays red even if its diagnosis run then fails (both recorded).
- `stale`: dashboard computes expected-next-run from the schedule; a loop overdue by >1.5× its interval flags `needs_attention` (single-user system — no ownership/routing metadata; a top strip `needs_attention` count is the escalation path, optional local notification later).

### Engine adapter interface ⊕ (enriched)
`engines/<engine>.sh` receives env: `LOOP_NAME, RUN_ID, WORKDIR, PROMPT_FILE, OUT_DIR, TIMEOUT_S, SCHEMA_FILE, PERMISSIONS_*` (the separate axes from loop.conf) `, MODEL?` — writes `OUT_DIR/{contract.json, output.md, usage.json, engine.log}`; exit code = engine success.
- codex: `codex exec --output-last-message` + `--output-schema $SCHEMA_FILE`, sandbox mapped from PERMISSIONS axes; JSONL event stream parsed for usage → `usage.json` (raw preserved).
- claude: `claude -p --output-format json --json-schema $SCHEMA_FILE --allowedTools` mapped from PERMISSIONS axes.
- ⊕ "Report-only" is enforced by these engine-level permissions, not by prompt guardrails. Remote-mutation risk (gh, ad CLIs) is its own axis: network access does NOT imply mutation tools. ⊕ For remote-capable CLIs the axis must be backed by real enforcement, not annotation: read-only credentials where the platform offers them (e.g. fine-grained read-only GitHub PAT for sweep loops) and/or command allowlists in the engine invocation (claude `--allowedTools "Bash(gh run list*)"...`-style; codex sandbox command policy). `loopctl validate` fails a loop granting network+local_exec to a remote-capable CLI without one of these mechanisms configured.
- Adding an engine later = one script honoring this env contract.

## Reporting Contract

**Tier 1 — REQUIRED (global dashboard):** engine-emitted `contract.json`, schema-enforced:
```json
{
  "schema_version": 1,
  "run_id": "…",
  "status": "ok | warn | alert",
  "status_reason": "short machine-usable category",
  "headline": "one line, e.g. '3 repos unpushed, 1 has no remote'",
  "metrics": { }
}
```
Runner adds: timestamps, duration, engine, runner-status, tokens/cost (nullable), report path.

**Tier 2 — OPTIONAL, loop-specific (per-loop dashboard sections):** `metrics` free-form; `dashboard.json` declares panels **with metric metadata** ⊕: `{"panels":[{"title","metric","type":"number|table|list|trend","unit","direction":"higher_is_worse|lower_is_worse|neutral","thresholds":{...},"missing":"hold|gap"}]}`. Trends read metric history from sqlite. Undeclared metrics render in a capped raw fallback panel (large values truncated, linked to full report ⊕) — everything available is displayed, nothing silently hidden.

### Dashboard (`loops.html`, static)
- **Global view:** row per loop — status light (precedence-resolved), headline, last run, schedule + best-effort next run, 7-day token spend, latest-report link. Top strip: fleet counts, `needs_attention` count, spend today/7d, last regen time, ⊕ stale-loop detection.
- **Per-loop sections:** declared panels + trends + recent-runs table (incl. runner-statuses) + report links + raw fallback.
- ⊕ Schedule grammar defined once (subset mapping cleanly to launchd `StartCalendarInterval`/`StartInterval`); launchd sleep semantics documented (calendar events coalesce on wake; interval firings during sleep are missed) so "next run" is explicitly best-effort.
- Reports/state dirs 0700, files 0600 ⊕; retention policy: reports + run artifacts pruned after N days (config), sqlite rows kept (small) ⊕.
- Styling per generalissimo's taste (bold/distinctive, not generic corporate); function first.

## Loop Authoring Process (`docs/LOOP_AUTHORING.md`)

1. **Spec** (template in doc): purpose, cadence, scope, type, guardrails verbatim, permission axes justification, tier-1 semantics, tier-2 metrics + panel metadata.
2. **Scaffold:** `loopctl new <name>` from templates (prompt pre-seeded with contract instructions).
3. **Build:** developer agent fills precheck/prompt/conf/dashboard.json.
4. **Validate:** `loopctl validate <name>` — conf sanity, schedule parse, schema dry-check, ⊕ **dangerous-permission-combination checks** (e.g. network+gh without an explicit remote-read-only guardrail annotation → hard fail).
5. **Supervised run:** `loopctl run <name>` foreground; human reviews report + dashboard rendering vs ground truth. ⊕ Contract compliance is verified by tooling (validate + engine schema enforcement), not trusted to scaffolding.
6. **Install:** `loopctl install <name>` — generates plist, `launchctl bootstrap`, then ⊕ **one `launchctl kickstart` of the actual installed job** must produce a valid run before install is declared done (env/auth issues only surface in the real launchd context).

## Implementation Steps

1. ~~Write `docs/LOOP_SELECTION.md`~~ — DONE (2026-07-22, alongside this plan doc).
2. Build core: repo init, sqlite schema (WAL), `lock.py`, `contract.schema.json`, `run-loop.sh`, both engine adapters.
3. Build `loopctl` (new/validate/run/list/install first; pause/resume/uninstall/status after pilot) + launchd template.
4. Build `dashboard/generate.py` + `loops.html`.
5. Write `docs/LOOP_AUTHORING.md` (contract, process, spec template, permission axes, schedule grammar, launchd sleep semantics).
6. **Pilot (kept as `examples/`):** `hello-loop` (agent; emits valid contract + one custom metric incl. a trend panel) + `hello-watchdog` (curl, toggleable target). Verify the full matrix in Verification. Examples stay in the repo as regression fixtures; their schedules are never installed permanently.
7. Document: `~/projects/CLAUDE.md` short section (harness exists, `loopctl` usage, dashboard location); decision record + plan-check gotchas → `~/projects/.claude/SESSION_LOG.md`; memory file (harness location/contract, engine-swap procedure, Paperclip/hermes deferred, no-flock/no-timeout-on-macOS).

## Verification

- **Scheduling:** installed `hello-*` jobs fire unattended via launchd (real firing, not only kickstart); `skipped-overlap` proven by a deliberately-long run; amber recorded on precheck-skip.
- **Contract integrity:** contract-violation recorded red on a deliberately-bad emission; **stale-green impossible**: kill the engine mid-run → `latest.json` still points at the previous valid run, run row shows `engine-timeout`.
- **Watchdog:** heartbeat rows written when silent-green; failure escalates to diagnosis; probe-red survives a failed diagnosis run.
- **Engines:** same loop on codex AND claude produces schema-valid contracts; usage parsed on both; nullable-usage path exercised.
- **Enforcement:** engine denied a project write under default permissions (tool-denied status recorded); `loopctl validate` rejects a dangerous permission combo and a broken conf.
- **Dashboard:** all states visible and precedence-correct after the pilot matrix; trend panel renders from ≥3 runs; raw fallback caps oversized metrics; atomic regen (no torn HTML under concurrent runs).
- **Docs:** fresh-eyes test — `LOOP_AUTHORING.md` + `loopctl new` alone suffice to build a valid loop.

## Out of Scope

- The 13 candidate loops (separate context via warmstart; `examples/` don't count).
- Paperclip (generalissimo explores separately), hermes (future engine lever), WSL rollout (portability by construction — fcntl lock, bash/python, cron-install script later; ⊕ noted: cron minimal env, no catch-up, state must live on WSL ext4 not /mnt/c).
- Notifications/delivery channels (dashboard `needs_attention` is v1's escalation; local notifications later).
- Any auto-mutation of projects (enforced by default permission axes, not just prompts).

