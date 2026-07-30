# PEON_REPORT — peon/loops-skill

## What changed

Exactly two product deliverables, plus this report:

1. **Created** `skills/loops/SKILL.md` — distributable Claude Agent Skill for the loops harness.
2. **Modified** `README.md` — one new **Agent surface** paragraph (after Findings, before Engines) pointing at the skill and `docs/SKILL_IMPORT.md`.

No code under `bin/`, `dashboard/`, or `tests/` was touched. No other files were modified.

## Why

Task 17 of the skill-import / agent-surface work: agents in other projects need a discoverable front door for loops (when to offer import, how to drive `loopctl`, safety rules they must not soften). The skill is the Claude-facing layer; `docs/SKILL_IMPORT.md` remains the full recipe.

## How verified

- Grepped `bin/loopctl` for every CLI flag and verb named in the skill:
  - flags: `--json`, `--tag`, `--actor`, `--root`, `--analyze`, `--apply`, `--answers`, `--note`, `--until` — all present
  - verbs: `status`, `list`, `findings`, `ack`, `dismiss`, `snooze`, `reopen`, `run`, `validate`, `install`, `import`, `new`, `pause`, `resume`, `uninstall` — all present
- Confirmed frontmatter has `name: loops` and the prescribed `description:`.
- Confirmed all seven body sections (Install note near top; §1–§6 as headings).
- Confirmed safety §5 carries the load-bearing substance: precheck is trusted UNSANDBOXED bash; `[read-only?]` is advisory not a boundary; never uncomment `[MUTATING — do not enable]`; axes start at report-only floor.
- Confirmed no hardcoded `/Users/...` paths (uses `$HOME` / `~`).
- Confirmed README diff is +2 lines only (one paragraph).
- Read sources of truth before writing: `docs/SKILL_IMPORT.md` (esp. §6 trust), `docs/LOOP_AUTHORING.md` §2/§7, `bin/loopctl` argparse, root `CLAUDE.md`.

## Open questions

1. **Content-first bare `loopctl` and status fleet aggregate** are documented in the skill per the agent-surface design / this task's prescribed body, but as of this worktree `bin/loopctl` still prints usage and exits 2 with no verb, and `cmd_status` has no leading fleet aggregate line. Those are Tasks 15–16 in the implementation plan — skill teaches the intended surface; code may still be catching up on this branch.
2. **`import --apply`** is still a stub (`--apply: not implemented yet (Task 12)` exit 2). The skill documents the full apply → validate → run → install gauntlet as the contract agents should follow once apply lands.
3. **Install run-first precondition** (refuse install with no prior non-failed supervised run) is design intent (Task 13); current `install` verifies a fresh non-failed run *after* kickstart rather than requiring a prior supervised `loopctl run`. Skill text follows the task/spec wording.
4. Skill length landed ~96 lines (under the ~120–180 aim) deliberately — density over padding; depth stays in the linked docs.
