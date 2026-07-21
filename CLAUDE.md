# Loops — recurring automation harness

Custom thin harness for scheduled agent "loops" (recurring maintenance/monitoring jobs across ~/projects). **Status: planned, not yet built.**

## Start here (cold start)
- **Building the harness:** execute `docs/HARNESS_PLAN.md` (finalized; plan-checked 3 rounds with codex — do not relitigate settled decisions recorded there).
- **Choosing/specifying loops:** work from `docs/LOOPS_WARMSTART.md` (13-candidate table awaiting Generalissimo's yes/no; evidence + hard guardrails included). Loops can be specified before the harness exists, but not built (they need `docs/LOOP_AUTHORING.md`, which the harness build produces).

## Non-negotiables (from the plan — full rationale in docs/HARNESS_PLAN.md)
- Engine: **codex default** (`codex exec --output-last-message --output-schema`), claude switchable (`claude -p --json-schema`), local models later. Prompts engine-neutral; prefer CLI/curl over MCP.
- All loops **report/propose-only**, enforced by per-loop permission axes at engine level — never by prompt text alone.
- macOS has **no `flock`, no GNU `timeout`** — use the fcntl lock helper + runner-owned process-group timeouts.
- Paperclip and hermes are OUT as harness (evaluated + rejected 2026-07; hermes cron is the documented fallback; `hermes` ≠ `hermes-local`). Never install `NousResearch/hermes-paperclip-adapter` v0.3.0 (frozen, buggy).
- All paths `$HOME`-relative (runs on macOS now, WSL later).
