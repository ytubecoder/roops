# Session Log

## 2026-07-22 — Project founded (planning only)

### Summary
- Project created as the home for the custom loop harness after a full portfolio analysis + harness evaluation session (detailed log: `~/projects/.claude/SESSION_LOG.md`, 2026-07-22 entry).
- Deliverables: `docs/HARNESS_PLAN.md` (harness design, plan-checked 3 rounds with codex, gate passed) and `docs/LOOPS_WARMSTART.md` (loop-selection warmstart). No implementation yet — deliberately split into two follow-up work streams (harness build / loop selection).

### Decisions
- Directory `~/projects/loops` chosen by generalissimo (over `loops-infra`).
- Harness: custom thin (Paperclip + hermes evaluated and rejected as harness — rationale and constraints recorded in HARNESS_PLAN.md and the global session log).
- Engine: codex default, claude switchable, local models later; report-only enforced via permission axes.

## 2026-07-22 — Harness build (full): Amendment 1 → probes → waves A–H → live verification
- Folded HARNESS_PLAN_AMENDMENT_1 (findings memory) into INTERFACES.md BEFORE any code: findings/dispositions tables, PRIOR FINDINGS injection, runner-side suppression + effective_status, transient retry (adapter exit 12), harness-error, died-run. schema_version stayed 1 (initial schema, not migration).
- Live-probed both CLIs inline (generalissimo's decision): codex --output-schema is OpenAI-STRICT → free-form objects rejected → `metrics` is a JSON-string field; ONE schema file works on both engines (verified incl. minLength). Evidence: docs/ENGINE_PROBES.md. Prompt via stdin `-` on codex (arg+stdin = double read). claude: structured_output + total_cost_usd + permission_denials.
- Built via parallel sonnet implementer waves w/ opus task reviews + fix loops: A core python, B dashboard, C runner, D adapters, E loopctl, F pilots+LOOP_AUTHORING. Notable adjudications: codex network=full requires fs_write=workdir (adapter hard-fail + validate rule 7); recorded harness-problem statuses exit 0 (§4.2 clarified); install self-verify must wait for a TERMINAL run row (started-row race).
- Wave G (real machine) caught 2 integration bugs hermetic tests couldn't: run_id never injected into prompt (→ RUN CONTEXT block in §6.2) and extract_usage not parsing the adapter's bare codex usage object. LESSON: fake-engine fixtures that substitute values themselves (fake.sh sed __RUN_ID__) can mask "who tells the engine X" gaps — pilot on real engines early.
- Live-proven: launchd install/kickstart-verify/NATURAL interval firing/uninstall; codex+claude same loop same finding ids; idempotence times_seen; dismiss → runner suppression w/ §4.5 footer; sandbox write-denial (PWNED.txt probe); skipped-overlap/precheck; watchdog silent-green + sticky probe-red; dashboard all states.
- Final fable whole-branch review: 2 blockers fixed (loopctl run --root env passthrough; credential_env = v1 validate hard-fail RESERVED) + claude denial-vs-transient order, cross-loop retention scoping, redaction rest-of-line+JWT. v1.1 follow-up list in .superpowers/sdd/progress.md.
- End state: 21 commits on main (local-only, no remote — intentional), suite green (278 py + 158 adapter + 115 runner + 35 examples). docs/LOOPS_WARMSTART.md has generalissimo's uncommitted parallel edits — left untouched.

## 2026-07-23 — OpenSpec pilot (brownfield, both lanes) + TT enrollment

### Summary
- `loops` was the pilot for adopting OpenSpec as Ticket Takeaway's spec lifecycle. Chosen because it has ~530 hermetic tests, no remote, no client exposure, and a frozen contract (`docs/INTERFACES.md`), so quality deltas are measurable rather than confounded. It was also unregistered in TT, so this exercised enrollment too.
- Installed `@fission-ai/openspec@1.6.0` globally (pinned — **not** the bare `openspec` npm name, which is a dead 2019 squat with no `bin`). `OPENSPEC_TELEMETRY=0` in `~/.zshrc`; the TT adapter also sets it on every invocation.
- `openspec init --tools claude` verified non-destructive: `git status` gained only `openspec/`, `.claude/skills/openspec-*`, `.claude/commands/opsx/`. `CLAUDE.md` byte-identical. No `AGENTS.md` in this repo to disturb.
- Registered in TT as `loops`, and declared `WORKFLOW.toml` `[verify] command = "tests/run-tests.sh"` — the close gate runs this and records the real exit code.

### Both lanes closed through one gate
- **Lane A — B-01.** Backfilled the existing `finding-suppression` capability (`bin/db.py: cmd_suppressed` / `cmd_dispose`, §4.5) as 4 canonical requirements, derived by **reading the code, not `docs/`**. `openspec archive` merged the delta into `openspec/specs/finding-suppression/spec.md`; change filed under `changes/archive/2026-07-22-…`.
- **Lane C — B-02.** `WORKFLOW.toml` config only, closed with `--no-change --reason "…"`. No spec delta written, because nothing observable changed — but the claim is recorded and the CLI refuses `--no-change` without a reason.
- Both ran the **same** verify command and both recorded real output. The lane changed how the work was described; it did not change how it closed.

### Lessons
- **The reversibility test is destructive to untracked files.** `rm -rf openspec .claude/skills/openspec-* .claude/commands/opsx` proved removal is clean (tests exit 0, ticket flow unaffected, `git status` back to baseline) — but it also deleted the archived change and the canonical spec, which are untracked until committed. Do it on a copy, or commit first.
- **Verify output must merge stderr into stdout.** Capturing them separately and concatenating puts *all* of stderr last, which buried the real `passed: 115, failed: 0` summary under `ResourceWarning` noise. The recorded tail has to show how the run actually ended.
- **Port 8787 is owned by `growth-console` (maguyva-marketing)**, not TT. TT's `serve.py` needs `--port 8790` here.
- Backfilling from code found a detail the docs compress away: `ack` never suppresses, and snooze uses a *strict* `>` against a date-normalised deadline, so a finding snoozed until today is still suppressed today. Specified as-is rather than "fixed" in a backfill.

### Not done
- **Token spend per change was not isolated.** Both changes ran inside one long session that was also building the Ticket Takeaway gates, so there is no clean per-change number to settle the "75k tokens to change a variable" concern. That measurement still needs a dedicated session: one trivial Lane C and one real Lane A feature, each in its own session, with spend recorded.

## 2026-07-27 — Dashboard on the tailnet + name scrub
- Dashboard now served at https://loops.example.ts.net via the existing dev-tailnet pattern: new userspace tailscaled node `loops` (com.generalissimo.dev-tailnet.tailscaled-loops launchd agent, statedir ~/.config/dev-tailnet/state/loops) forwarding HTTPS to the shared Caddy on 127.0.0.1:8443; Caddy @loops block file-serves dashboard/loops.html (ALL paths rewritten to it — generate.py never exposed). `loops` added to dev-tailnet register/install scripts.
- GOTCHA discovered: BOTH caddy instances on this machine (dev-tailnet + ~/caddy-tailscale) bind admin localhost:2019, so `caddy reload --address localhost:2019` is a coin flip and can clobber the wrong instance's running config (it briefly restarted the caddy-tailscale one). Reliable path: `launchctl kickstart -k gui/$UID/com.generalissimo.dev-tailnet.caddy`.
- Personal name replaced with "generalissimo" across all repo prose/templates (infra labels like com.generalissimo.* untouched — renaming would break running services).
- 2026-07-28: investigated the dual-caddy situation for the cleanup warmstart: instance B is com.vane.caddy (~/caddy-tailscale custom tsnet build, IS the `vane` tailnet node, own watchdog documenting a 2026-07-04 duplicate-caddy/netmap incident). Wrote ~/.config/dev-tailnet/WARMSTART_CADDY_CLEANUP.md (consolidation plan + com.generalissimo→com.generalissimo label rename runbook). Nothing executed.
