# Loops — recurring automation harness

Custom thin harness for scheduled agent "loops" (recurring maintenance/monitoring jobs across ~/projects). **Status: BUILT + live-verified 2026-07-22** (real launchd firing, codex AND claude, findings memory/dispositions, enforcement denial, full dashboard matrix; 615 hermetic tests via `bash tests/run-tests.sh` — 307 python + 308 shell, measured 2026-07-30).

**Fleet state (2026-07-30):** `loop-sensei` (the fleet examiner — diagnoses failed loops, proposes fixes as findings) is the FIRST and only loop installed to launchd (`daily:20:00` local). Everything else is supervised-only. Install state is machine-local: `launchd/*.plist` is gitignored, so a fresh clone shows installed=False for everything until reinstalled.

**Naming new loops:** follow the roops Japanese theme (rebrand in flight by another agent — https://ytubecoder.github.io/roops/): loop-sensei, not loop-doctor.

## Start here (cold start)
- **Building a LOOP:** `docs/LOOP_AUTHORING.md` + `bin/loopctl new` — run the intake interview first (11 questions, in the doc); fill SPEC.md; `loopctl validate` gates it.
- **Touching HARNESS code:** `docs/INTERFACES.md` is the frozen mechanical contract — conform or amend it explicitly, never drift. Design rationale: `docs/HARNESS_PLAN.md` + `docs/HARNESS_PLAN_AMENDMENT_1.md` (do not relitigate settled decisions). Verified engine CLI facts: `docs/ENGINE_PROBES.md`.
- **Choosing/specifying loops:** work from `docs/LOOPS_WARMSTART.md`.
- **Ads loops (five per-network checks + the `/ads/actions` console surface):** `docs/ADS_LOOPS_FOLLOWUP_WARMSTART.md` — current state, the three open issues with their re-check commands, and the acceptance bar (a manual "run everything" trigger is phase 1; launchd scheduling is phase 2, explicitly not before).
- **Open threads / unfinished design work:** `docs/OPEN_THREADS_WARMSTART.md` — main open thread is the approve→action bridge (ack ≠ approval; DMP id-space gap; Google Ads OAuth blocker). Check it before assuming a thread is settled; delete sections there as they resolve.
- **Roops rebrand (candidate, NOT applied):** brand + UI-concept site at https://ytubecoder.github.io/roops/ — repo `~/projects/roops` (its CLAUDE.md holds the design system). Harness/CLI names unchanged. The UI concepts imply harness follow-ups (timestamped metrics table in sqlite, pancake/stale finding semantics, read-only live-run tail) — each would be an explicit INTERFACES amendment; none started.
- Findings/disposition flow (the human arrow): `loopctl findings <loop>` → `ack|dismiss --note|snooze --until|reopen`. Dismissed/snoozed findings are suppressed by the RUNNER (latest.json + dashboard); the engine still emits them; audit copy stays in `state/runs/<id>/contract.json`.

## Non-negotiables (from the plan — full rationale in docs/HARNESS_PLAN.md)
- Engine: **codex default** (`codex exec --output-last-message --output-schema`), claude switchable (`claude -p --json-schema`), local models later. Prompts engine-neutral; prefer CLI/curl over MCP.
- All loops **report/propose-only**, enforced by per-loop permission axes at engine level — never by prompt text alone. Know which axis actually contains what: the **working-directory write sandbox**, `--tools`, and `perm_network=none` are the real containment. `exec_allowlist` is NOT — a non-allowlisted `echo` still ran under a one-entry allowlist (verified 2026-07-28), so treat the allowlist as intent, not as a boundary.
- macOS has **no `flock`, no GNU `timeout`** — use the fcntl lock helper + runner-owned process-group timeouts.
- Paperclip and hermes are OUT as harness (evaluated + rejected 2026-07; hermes cron is the documented fallback; `hermes` ≠ `hermes-local`). Never install `NousResearch/hermes-paperclip-adapter` v0.3.0 (frozen, buggy).
- All paths `$HOME`-relative (runs on macOS now, WSL later).

## Critical gotchas (cost real debugging time — the WHY matters)
- **A loop cannot declare its own displayed status when it emits findings.** Per `INTERFACES.md` §4.5, a NON-empty `findings` array makes `effective_status` = max severity of the unsuppressed findings and **discards the declared `status`**; only an EMPTY `findings` lets `status` through. A run that failed to write its action set declared `alert` and displayed **amber**, because its findings topped out at `warn`. If a run must surface red, it emits zero findings.
- **The engine's shell layer hard-denies any command containing both a brace and a quote** ("expansion obfuscation") — before the script runs, so the script cannot detect it. Any loop whose engine writes structured data needs a brace-free payload format. Full probe table + the allowed/denied pair: `docs/ENGINE_PROBES.md`. Do NOT let a model invent smuggling workarounds (`tr`, hex escapes, process substitution); the correct response to a denial is to remove the brace.
- **Model-emitted metrics get believed.** A run reported `inputs.missing: 4` while its own digest showed all four inputs healthy, tripping the dashboard's alert threshold on good data. Metrics a precheck can compute should be computed there and copied verbatim, never left to the model.
