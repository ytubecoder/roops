# Session Log

## 2026-07-30 — Roops rebrand: brand system, public site, full UI concept (separate repo)

### Summary
- Explored and shipped the "Roops" rebrand candidate (ループス — "loops" as the Japanese loanword, re-borrowed): design system (washi/sumi, one vermillion accent, ensō mark, hanko stamps), a public landing page, and a full UI concept for a re-skinned dashboard — all in a NEW repo `~/projects/roops` (github.com/ytubecoder/roops, live on Pages). This repo untouched; detailed log in that repo's `.claude/SESSION_LOG.md`.
- UI concepts established there that imply future harness work (none started, each an explicit INTERFACES amendment): timestamped `metrics` table in the existing sqlite for graphable loop-reported numbers; pancake/retroactive-apply + stale-out semantics on finding identity; read-only SSE tail of a running round ("engawa" view); per-loop enable/disable surfaced in UI (maps to install/uninstall).

### Lessons Learned
- **User correction (copy-level but load-bearing):** never present the harness as flatly "report-only / never acts" — rounds are read-only, but findings are actions-in-waiting and approval (distinct from ack, per OPEN_THREADS) turns them into orders on a separate audited path. Presenting the absolute version reads as wrong to the owner.

### Decisions
- Rebrand stays a candidate: site lives in its own public repo precisely so nothing here renames; if adopted, the rename starts with a `loopctl` alias.
- Public site uses genericized loop names — internal `ads-*` names kept off it deliberately.

## 2026-07-28 — Ads loops: emit-path, contract-conformance and continuity fixes; console surface verified live

### Summary
- Fixed three defects in `ads-google` that would have cloned into every sibling loop: the action-set **emit path** (a `{}` instruction in `prompt.md` produced a command the shell layer hard-denies), **contract conformance** (docs promised a `loop_status=error` that does not exist in the schema — the real key is `status`, enum `ok|warn|alert`), and **id continuity** (a run that emitted findings but failed to persist its set left those ids unburned, so the next run reused `ADG-01..04`).
- Verified the console surface end-to-end: `/ads/actions` renders real actions (200, ~34KB), `/ads/campaigns` 200, `/schedules` lists all five ads loops. The schedules path is `/schedules` — `/settings/schedules` 404s.
- Established what's actually open and wrote `docs/ADS_LOOPS_FOLLOWUP_WARMSTART.md`: ~half of all runs die with no contract, nothing is installed under launchd (engine auth fails in that environment), and `/schedules` still lists the legacy manual check-in rows beside the new loop rows.

### Lessons Learned
- **Gotcha (§4.5 inverts status):** a non-empty `findings` array overrides the declared contract `status` with the findings' max severity. A run that wrote no action set declared `alert` and displayed **amber** because its findings topped out at `warn`. Emitting *zero* findings is what lets an alert through — the fix for "make failure visible" was to report less, not more. Promoted to CLAUDE.md.
- **Gotcha (`--allowedTools` is not a boundary):** with a single allowlist entry, a non-listed `echo` still executed. Real containment is the working-directory write sandbox plus `--tools` and `network=none`. The `exec_allowlist` axis expresses intent, not enforcement. Promoted to CLAUDE.md.
- **Gotcha (model-emitted metrics get believed):** one run reported `inputs.missing: 4` while its own digest showed all four inputs healthy, tripping the dashboard's alert threshold on good data. Metrics a precheck can compute must be computed there and copied verbatim.
- **Rejected (`--in <path>` for the emit payload):** at the `report_only` floor the model has no Write tool, so producing a payload file would itself need a heredoc plus redirection — strictly worse than the flat stdin format.
- **Gotcha (truncated listings produce confident wrong claims):** a `find … | head -20` hid two run dirs and produced a "two supervised runs" claim; later, inspecting only each loop's *latest* run produced "only google checks anything" when in fact all five networks emit real sets. Both were reported to the user before being caught. Census the whole set before characterising it.
- **Gotcha (UTC vs local):** the box runs `Asia/Manila` (+08:00, which `date` abbreviates `PST`). UTC-stamped run ids read as "yesterday" and a run in flight reads as "aborted" — one was called a dangling failure while it was still executing.

### Decisions
- **Action ids come strictly from campaign report recommendations** — the DMP/CRO pattern (`report > action generation > future execution`). Infrastructure problems (a failed fetch, missing inputs) are run status only and mint no id. This was raised because a transient fetch blip minted `ADG-06` ("INPUT GAP"), which then had to be struck, permanently burning an id on plumbing. All five prompts still instruct the old behaviour at `prompt.md:44`.
- **Acceptance bar is two ordered phases:** phase 1 is a manual "run everything" trigger plus a clean schedules screen plus runs that reliably produce output; phase 2 is real scheduled runs. Explicitly not before phase 1 holds.
- **Manual trigger should copy the DMP/CRO regenerate pattern** — `POST` starts a daemon thread behind a shared job lock, a `GET …/status` endpoint polls it. The no-shell-out rule governs the request path only, so a background worker does not violate it.
- **Subagent isolation by file ownership did not hold.** Three agents were each scoped to disjoint files and told to run no git commands; they made 4 commits here and 22 in `maguyva-marketing`, one attributed its commit to the parent, and work landed well outside ads. Nothing was pushed at the time and no installs succeeded. File-ownership briefs are not a sandbox — if isolation matters, use worktrees.

## 2026-07-28 — Machine infra: caddy admin-port split + launchd label rename; repo gets a remote; README rewrite

### Summary
- **Machine infra (outside this repo, runbook at `~/.config/dev-tailnet/WARMSTART_CADDY_CLEANUP.md`):** resolved the two-caddy `localhost:2019` admin collision by moving the vane caddy's admin API to `:2029`, and renamed all 16 `com.generalissimo.dev-tailnet.*` launchd labels to `com.generalissimo.dev-tailnet.*`. Both verified: one listener on 2019, full FQDN sweep answering on 15 hosts, 16/16 services loaded with live pids, zero `com.generalissimo` references left in plists or `dev-tailnet/bin`.
- **This repo gained a remote.** Was local-only for 33 commits; now private at `https://github.com/ytubecoder/loops`, `main` tracking `origin/main`. Secret-scanned tree and full history before creating it — every hit was a fake fixture in the harness's own redaction tests.
- **README rewritten** in the style of `~/projects/ticket-takeaway/README.md`: badge row, ASCII banner, blockquote tagline, install one-liner + agent-facing variant, run-flow diagram with a gate table, report-only rationale, command reference, docs table.

### Lessons Learned
- **Gotcha (test counting):** `tests/run-tests.sh` runs BOTH the shell suites and `python3 -m unittest discover`. Summing only `passed: N` lines undercounts badly — unittest reports `Ran N tests`, not `passed:`. True total is **593** (285 python + 308 shell). A first pass at this session's README published "308" and had to be corrected; `CLAUDE.md`'s older "~530" was closer to right than the "measured" number that replaced it. Measure with both patterns or trust the existing figure.
- **Gotcha (zsh word splitting):** an ad-hoc verification loop `for l in $labels` silently tested one 16-line string as a single filename, reporting a false "MISSING plist". zsh does not word-split unquoted parameters. Verification harnesses need the same scrutiny as the thing they verify — a false alarm from a checking script wastes exactly as much time as a real bug.
- **Accepted (verify-then-act on an inherited runbook):** the caddy runbook was written by a prior session and turned out accurate, but re-verifying it live surfaced four defects — `com.vane.*` is 4 plists not 2 (an unrelated `searxng` that must survive), the stale-LABELS list was missing 4 entries not 5, an 8s curl timeout yields false `000`s on cold tailnet nodes (25s is honest), and the doc's own grep-verify could never pass because the doc lives in the directory it greps.
- **Accepted (prove the failure mode, not just the config):** after splitting the admin ports, confirming the on-disk config equalled the running config made a real `caddy reload --address localhost:2019` a safe no-op — which then proved determinism on the exact operation that misfired on 2026-07-26, rather than merely asserting it from a port listing.

### Decisions
- **Job A1 (migrate vane into the dev-tailnet pattern, retire `~/caddy-tailscale`) is BLOCKED** — "dont touch vane at all". There are **two vanes**: `vane` = 100.69.211.49 on llm (tsnet node fronting `~/projects/Vane` on :8347) and `vane-mm` = 100.71.78.96 on mm. They are different services and must never be consolidated. A1's "delete the old vane machine" step sits beside a `vane-mm` row in the Tailscale console — if ever unblocked, match on IP, never on name. Saved to auto-memory as `two-vanes-never-consolidate`.
- **The two-caddy situation persists by decision**, now collision-free. A0 was always designed as a complete stopping point; A1 was the only step that would have retired the second instance.
- **README omits license and release badges** (ticket-takeaway has both) because this repo has no `LICENSE` file and no releases — fabricating them was rejected. Also omits the four `ads-*` sibling loops, which are uninstalled and carry known drift from `ads-google`; the README says "seven loops defined, none installed" and uses `hello-loop`/`hello-watchdog` as the worked examples.
- **No dashboard screenshots in the README** — `dashboard/loops.html` is gitignored as generated output, so there's nothing committed to link. Deferred as not worth it yet.

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
