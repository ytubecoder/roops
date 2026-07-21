# Session Log

## 2026-07-22 — Project founded (planning only)

### Summary
- Project created as the home for the custom loop harness after a full portfolio analysis + harness evaluation session (detailed log: `~/projects/.claude/SESSION_LOG.md`, 2026-07-22 entry).
- Deliverables: `docs/HARNESS_PLAN.md` (harness design, plan-checked 3 rounds with codex, gate passed) and `docs/LOOPS_WARMSTART.md` (loop-selection warmstart). No implementation yet — deliberately split into two follow-up work streams (harness build / loop selection).

### Decisions
- Directory `~/projects/loops` chosen by Generalissimo (over `loops-infra`).
- Harness: custom thin (Paperclip + hermes evaluated and rejected as harness — rationale and constraints recorded in HARNESS_PLAN.md and the global session log).
- Engine: codex default, claude switchable, local models later; report-only enforced via permission axes.
