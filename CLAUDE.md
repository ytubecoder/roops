# Loops — recurring automation harness

Custom thin harness for scheduled agent "loops" (recurring maintenance/monitoring jobs across ~/projects). **Status: BUILT + live-verified 2026-07-22** (real launchd firing, codex AND claude, findings memory/dispositions, enforcement denial, full dashboard matrix; ~530 hermetic tests via `bash tests/run-tests.sh`).

## Start here (cold start)
- **Building a LOOP:** `docs/LOOP_AUTHORING.md` + `bin/loopctl new` — run the intake interview first (11 questions, in the doc); fill SPEC.md; `loopctl validate` gates it.
- **Touching HARNESS code:** `docs/INTERFACES.md` is the frozen mechanical contract — conform or amend it explicitly, never drift. Design rationale: `docs/HARNESS_PLAN.md` + `docs/HARNESS_PLAN_AMENDMENT_1.md` (do not relitigate settled decisions). Verified engine CLI facts: `docs/ENGINE_PROBES.md`.
- **Choosing/specifying loops:** work from `docs/LOOPS_WARMSTART.md`.
- **Open threads / unfinished design work:** `docs/OPEN_THREADS_WARMSTART.md` — main open thread is the approve→action bridge (ack ≠ approval; DMP id-space gap; Google Ads OAuth blocker). Check it before assuming a thread is settled; delete sections there as they resolve.
- Findings/disposition flow (the human arrow): `loopctl findings <loop>` → `ack|dismiss --note|snooze --until|reopen`. Dismissed/snoozed findings are suppressed by the RUNNER (latest.json + dashboard); the engine still emits them; audit copy stays in `state/runs/<id>/contract.json`.

## Non-negotiables (from the plan — full rationale in docs/HARNESS_PLAN.md)
- Engine: **codex default** (`codex exec --output-last-message --output-schema`), claude switchable (`claude -p --json-schema`), local models later. Prompts engine-neutral; prefer CLI/curl over MCP.
- All loops **report/propose-only**, enforced by per-loop permission axes at engine level — never by prompt text alone.
- macOS has **no `flock`, no GNU `timeout`** — use the fcntl lock helper + runner-owned process-group timeouts.
- Paperclip and hermes are OUT as harness (evaluated + rejected 2026-07; hermes cron is the documented fallback; `hermes` ≠ `hermes-local`). Never install `NousResearch/hermes-paperclip-adapter` v0.3.0 (frozen, buggy).
- All paths `$HOME`-relative (runs on macOS now, WSL later).
