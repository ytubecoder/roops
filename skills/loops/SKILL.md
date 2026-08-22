---
name: loops
description: Use when the user wants recurring checks/reports scheduled, mentions the loops dashboard, or repeatedly runs the same check-shaped skill or workflow by hand — push it to the loops harness (a report-only scheduled runner) via loopctl.
---

# loops

**Install (once per machine):**
```bash
ln -s "$HOME/projects/loops/skills/loops" "$HOME/.claude/skills/loops"
```
(or copy the directory). Use `$HOME`, never a hardcoded absolute path.

Default root is `$HOME/projects/loops`. Override with `LOOPS_ROOT` or `--root <path>`. Drive everything through `$LOOPS_ROOT/bin/loopctl` (or `./bin/loopctl` from the repo).

## 1. What loops is

A thin harness that runs scheduled agent "loops" via the host scheduler (launchd on macOS, systemd on Linux). Every loop is report/propose-only — enforced by per-loop permission axes at the engine level, never by prompt text. Findings persist across runs with dispositions (ack/dismiss/snooze/reopen). A static dashboard at `dashboard/loops.html` shows fleet state.

The invariant: deterministic code you wrote (`precheck.sh`) gets full power; the model gets a sandbox. Do not reverse those roles.

## 2. The CLI surface

- Bare `loopctl` with no verb prints the live fleet summary and exits 0 (content-first).
- `loopctl status [name] [--json]` — includes a leading fleet aggregate line (counts by status, needs_attention, 7-day spend); `--json` rows carry `tags` and `provenance`.
- `loopctl list [--tag project:x] [--json]` — `--tag` is an EXACT match, not substring (`project` does not match `project:x`).
- `loopctl findings <loop>` then disposition verbs (each takes `<loop> <finding_id>`):
  - `loopctl ack <loop> <finding_id>`
  - `loopctl dismiss <loop> <finding_id> --note "…"` — note **required** (audit trail)
  - `loopctl snooze <loop> <finding_id> --until YYYY-MM-DD` — until **required**
  - `loopctl reopen <loop> <finding_id>`
- `loopctl run <name>` (supervised, foreground), `loopctl validate <name>`, `loopctl install <name>`.
- Always pass `--actor "claude/<project-name>"` on lifecycle verbs (`new` / `import` / `install` / `uninstall` / `pause` / `resume`). It is advisory provenance for observability, **not** authentication.
- Non-default root: set `LOOPS_ROOT` or pass `--root <path>`. Default is `$HOME/projects/loops`.

`ack` means stop nagging — it is **not** approval to execute a proposed action. That bridge is a separate open thread; today the arrow stops at the human.

## 3. When to OFFER an import — and when NOT to

**OFFER** when the user repeatedly runs the same check-shaped workflow by hand (audit, digest, monitor, "check X and tell me if Y"), or asks for something recurring. Prefer offering over waiting to be asked once the pattern is obvious.

**Do NOT offer, or reshape first,** when the skill:

| Shape | Why / what to do |
|---|---|
| Needs credentials / API keys / OAuth | Imports refuse unless explicitly acknowledged, and then only as `schedule=manual` |
| Depends on MCP tools with no CLI equivalent | Reshape to CLI/curl, or leave blocked |
| Must take actions (deploy / send / push) | Loops **proposes**, it never acts — emit the action it WOULD take as a finding |
| Needs mid-run questions from the user | Decisions become findings; the answer channel is dispositions |
| Needs to retry until success | v1 is one engine invocation per firing — reshape to check-once-and-report |

A skill that *almost* fits is still importable after reshape. A skill that needs secrets mid-run is not a schedule candidate until that is fixed.

## 4. Running an import end-to-end

Gates are unchanged from hand-authored loops: **analyze → apply → validate → supervised run → install**. Import scaffolds; it never skips a gate and never installs.

1. `loopctl import <skill-path> --analyze --json` — static, zero-token, never invokes a model.
2. Read `answers_needed`. Each item has `question_id`, `prompt`, `context`, `options`, and `suggested_answerer` (`"agent"` or `"user"`). Answer the `"agent"` ones yourself from project context; relay the `"user"` ones with their options **verbatim** and let the user pick. Presentation choices (which metrics become dashboard panels, thresholds) are usually user-facing.
3. Write `answers.json`:
   ```json
   {
     "analyzer_version": "…",
     "skill_sha256": "…",
     "answers": {"<question_id>": "<value>"},
     "provenance": {"<question_id>": "user|agent"},
     "acknowledge_blocked": false,
     "tags": ["imported", "skill:<name>"],
     "model": "claude-sonnet-5",
     "timeout_s": 600,
     "retry_transient": 2
   }
   ```
   Copy `analyzer_version` and `skill_sha256` from the analyze output. If the skill file changes you must re-analyze — apply refuses stale answers. Set `acknowledge_blocked: true` only when the analysis came back blocked **and** the user accepted a manual-schedule scaffold. `tags`/`model`/`timeout_s`/`retry_transient` are all OPTIONAL top-level keys (siblings of `answers`, not nested inside it) that map straight onto the matching `loop.conf` fields — omit any you don't need. They are validated and refused outright (never coerced) if out of shape; `q11_budget`'s free text does NOT set `model`/`timeout_s`/`retry_transient` — use these keys instead (`docs/SKILL_IMPORT.md` §7).
4. `loopctl import <skill-path> --apply --answers answers.json --actor "claude/<project>"` — scaffolds the loop, records provenance. **Never installs.**
5. `loopctl validate <name>` must exit 0. Any remaining `[FILL:` marker in `SPEC.md` is a hard fail.
6. `loopctl run <name>` — then **read the report against ground truth**. Passing validate is necessary, never sufficient: validate cannot see a volatile `finding_id` rule or a precheck that skips forever. This step is the real gate.
7. `loopctl install <name>` — goes live on the host scheduler (launchd on macOS, systemd on Linux). It refuses unless a prior non-failed supervised run exists, so step 6 is mechanically enforced — with one caveat: a `runner_status=skipped-precheck` run also satisfies this precondition, and that status means the engine never actually ran (an empty-output `type=agent` precheck short-circuits before invocation) — so "mechanically enforced" guarantees a run row exists, not that step 6's report was ever produced to read.

After install, manage noise with dispositions — do not re-prompt the model to "stop mentioning X."

## 5. Safety rules you must not soften

These are load-bearing. Do not paraphrase them into something softer.

- The importer emits proposed precheck commands **commented out**. `precheck.sh` is trusted **UNSANDBOXED** bash — the four permission axes govern the **model**, not the precheck. A live precheck has the full run of the host scheduler (launchd on macOS, systemd on Linux) user's account.
- `[read-only?]` is an **advisory heuristic hint**, not a guarantee and not a security boundary. Known escapes exist (and more will). A human must read every line before uncommenting it. **NEVER** uncomment a line labelled `[MUTATING — do not enable]`, and never uncomment anything on the user's behalf without them reading it.
- Permission axes are proposed at the report-only floor (`report_only` / `none` / `none` / `none`). Any raise is a user decision with a written justification — never silent, never inherited from the source skill.

## 6. Conventions

- **Tags:** `project:<name>` and `campaign:<name>` (lowercase, `^[a-z][a-z0-9:_-]{1,40}$`, max 8, exact-match filtering via `loopctl list --tag`).
- **Loop names** follow the fleet's Japanese theme (see the loops repo `CLAUDE.md`) — e.g. `loop-sensei`, not `loop-doctor`. Prefer short, pronounceable names that read as roles, not ticket titles.
- Full manual recipe (importing without the tool): `docs/SKILL_IMPORT.md`. Loop authoring from scratch (eleven intake questions + build process): `docs/LOOP_AUTHORING.md`. Frozen mechanical contract: `docs/INTERFACES.md`.

## See also

- `docs/SKILL_IMPORT.md` §6 (trust) — mandatory before uncommenting any proposed precheck line
- `docs/LOOP_AUTHORING.md` §2 (intake interview) and §7 (build process)
- `dashboard/loops.html` — fleet view after a successful supervised run
