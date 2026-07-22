# Session Log

## 2026-07-22 — Project founded (planning only)

### Summary
- Project created as the home for the custom loop harness after a full portfolio analysis + harness evaluation session (detailed log: `~/projects/.claude/SESSION_LOG.md`, 2026-07-22 entry).
- Deliverables: `docs/HARNESS_PLAN.md` (harness design, plan-checked 3 rounds with codex, gate passed) and `docs/LOOPS_WARMSTART.md` (loop-selection warmstart). No implementation yet — deliberately split into two follow-up work streams (harness build / loop selection).

### Decisions
- Directory `~/projects/loops` chosen by Generalissimo (over `loops-infra`).
- Harness: custom thin (Paperclip + hermes evaluated and rejected as harness — rationale and constraints recorded in HARNESS_PLAN.md and the global session log).
- Engine: codex default, claude switchable, local models later; report-only enforced via permission axes.

## 2026-07-22 — Harness build (full): Amendment 1 → probes → waves A–H → live verification
- Folded HARNESS_PLAN_AMENDMENT_1 (findings memory) into INTERFACES.md BEFORE any code: findings/dispositions tables, PRIOR FINDINGS injection, runner-side suppression + effective_status, transient retry (adapter exit 12), harness-error, died-run. schema_version stayed 1 (initial schema, not migration).
- Live-probed both CLIs inline (Generalissimo's decision): codex --output-schema is OpenAI-STRICT → free-form objects rejected → `metrics` is a JSON-string field; ONE schema file works on both engines (verified incl. minLength). Evidence: docs/ENGINE_PROBES.md. Prompt via stdin `-` on codex (arg+stdin = double read). claude: structured_output + total_cost_usd + permission_denials.
- Built via parallel sonnet implementer waves w/ opus task reviews + fix loops: A core python, B dashboard, C runner, D adapters, E loopctl, F pilots+LOOP_AUTHORING. Notable adjudications: codex network=full requires fs_write=workdir (adapter hard-fail + validate rule 7); recorded harness-problem statuses exit 0 (§4.2 clarified); install self-verify must wait for a TERMINAL run row (started-row race).
- Wave G (real machine) caught 2 integration bugs hermetic tests couldn't: run_id never injected into prompt (→ RUN CONTEXT block in §6.2) and extract_usage not parsing the adapter's bare codex usage object. LESSON: fake-engine fixtures that substitute values themselves (fake.sh sed __RUN_ID__) can mask "who tells the engine X" gaps — pilot on real engines early.
- Live-proven: launchd install/kickstart-verify/NATURAL interval firing/uninstall; codex+claude same loop same finding ids; idempotence times_seen; dismiss → runner suppression w/ §4.5 footer; sandbox write-denial (PWNED.txt probe); skipped-overlap/precheck; watchdog silent-green + sticky probe-red; dashboard all states.
- Final fable whole-branch review: 2 blockers fixed (loopctl run --root env passthrough; credential_env = v1 validate hard-fail RESERVED) + claude denial-vs-transient order, cross-loop retention scoping, redaction rest-of-line+JWT. v1.1 follow-up list in .superpowers/sdd/progress.md.
- End state: 21 commits on main (local-only, no remote — intentional), suite green (278 py + 158 adapter + 115 runner + 35 examples). docs/LOOPS_WARMSTART.md has Generalissimo's uncommitted parallel edits — left untouched.
