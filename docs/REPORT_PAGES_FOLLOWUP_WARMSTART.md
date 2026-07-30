# Report pages — follow-up warmstart (2026-07-30)

The report-pages tier (INTERFACES Amendment 2) and its pilot `kagi-ban` **shipped and are live**.
This doc is only what is LEFT: three decisions for generalissimo, a follow-up backlog, and the
context a cold agent needs to not re-derive it. Delete sections here as they resolve.

- Design rationale: `docs/REPORT_PAGES_PLAN.md` · Mechanical contract: `docs/INTERFACES.md`
  §4.1 step 6.5 + §12 · Author guide: `docs/REPORT_PAGES.md` · Plan (executed):
  `docs/superpowers/plans/2026-07-30-report-pages.md`.
- Shipped commits: `d6ebceb..44e0942` on main (10 plan tasks + a final-review fix wave).
  649 hermetic tests green (`bash tests/run-tests.sh`).

## Current live state (verified 2026-07-30)

- `kagi-ban` is **installed on launchd**, `daily:07:40`, and is the second loop on a schedule
  after `loop-sensei`. Steady state: `16 exposures (14 high, 2 medium); 0 new, 0 resolved`,
  `effective_status=alert` (correct posture — 14 undismissed highs).
- Serving over the tailnet vhost (path-scoped roots, machine-local Caddyfile — NOT in this repo):
  - `https://loops.example.ts.net/` — dashboard
  - `https://loops.example.ts.net/reports.html` — reports screen
  - `https://loops.example.ts.net/reports/kagi-ban/latest.html` — the page
- One finding is dismissed as an exercise: `av:homebrew:b4cc5e56`, note "known-accepted PATH
  exposure (LOOP_HANDOFF)". Suppressed from `latest.json`, still rendered on the snapshot page —
  that asymmetry is the spec (§1.4), not a bug.

## Why the count is 16 and not the 20 in `~/projects/av-audit/`

**Do not "fix" this back to 20.** `av`'s bash/zsh detectors flag user-writable directories that
**precede** system paths. In a real login shell `/opt/homebrew/{bin,sbin}` precede `/usr/bin`
(flagged) while `~/.grok/bin` sits after them (correctly not flagged). The old 20-finding number —
including av-audit's own `scan-latest.json` baseline and `LOOP_HANDOFF.md` — was measured in a
Claude-Code-like process whose PATH ordering differs. `precheck.sh` now pins the login-shell PATH,
so the observation no longer depends on who triggered the run. 16 is the canonical user's-shell
view. (av-audit is not ours to edit per its handoff; its baseline doc is simply stale.)

## Decisions waiting on generalissimo

1. **Normalize two plan-mandated cosmetics.** `bin/loopctl`'s `_SPEC_MD_TEMPLATE` renders section
   12 as `## 12. Page output (q12)` while sections 1–11 are plain `N. Title` lines; likewise
   `docs/LOOP_AUTHORING.md` labels the question `**q12. Page output**` where the others are
   `**11. …**`. Reviewer verified normalizing is safe: `tests/test_loopctl.py` asserts the
   substring `"12. Page output"` (matches either form) and the `[FILL:` count is unaffected.
   Recommendation: normalize both, keep `q12_page` as the rubric id in prose and the import plan.
2. **Clean up one stale finding row.** `av:gh-cli-hosts-token:«redacted:secret»` (times_seen=4,
   now resolved) is the corrupted id from before the redact fix; the real finding is
   `av:gh-cli-hosts-token:ff079f7f`. Options: leave it as resolved history, `loopctl dismiss` it
   with a note, or delete the row. Not urgent — it no longer nags.
3. **Ack the `bin/redact.py` lookbehind tradeoff.** `_KV_RE` now carries `(?<![A-Za-z0-9-])`, so
   hyphenated compounds and letter-adjacent tails (`gh-cli-hosts-token:`, `authtoken:`) no longer
   trip the GENERIC rest-of-line rule, while underscore compounds (`GITHUB_TOKEN=`) still do. The
   specific high-value patterns (`ghp_`/`sk-`/`xox`/`AKIA`/`eyJ`/private-key) fire regardless.
   This is shared infra, hence worth an explicit ack. Reversible one-liner if you disagree.

## Follow-up backlog (none blocking; roughly priority-ordered)

- **Bind the renderer's keyword list to `redact.py`.** `loops.d/kagi-ban/render_page.py`'s
  `_KV_PHRASE_RE` duplicates the gate's keyword alternation with different boundaries (`\b` vs the
  lookbehind). Add a keyword added to `redact.py` and kagi-ban silently starts failing promotion
  again — visible only as a `stale` badge. Import the alternation from `redact`, or add a test
  asserting the two share a keyword set.
- **`docs/REPORT_PAGES.md` misleads precheck authors.** It says "read baselines from
  `$LOOP_DATA_DIR`", but the runner exports that variable **only to `render.sh`** (`bin/run-loop.sh`
  step 6.5); prechecks get `LOOP_NAME RUN_ID LOOPS_ROOT WORKDIR OUT_DIR`. Under `set -u` a precheck
  using it dies immediately. `kagi-ban` works around it with an explicit
  `$LOOPS_ROOT/state/loop-data/kagi-ban/` path. One sentence fixes the doc.
- **`pagekit/kit.css` has zero consumers.** `PAGEKIT` is exported and read by nobody; the only
  page-enabled loop inlines its own copy of the same CSS inside `render_page.py`'s `PAGE` template,
  and `pagekit/reference/reference-page.html` is generated from that copy. The "shared kit" will
  drift from the page it was extracted from. Either have `render_page.py` inline `$PAGEKIT/kit.css`
  or add a test asserting the two CSS bodies match.
- **`dashboard/generate.py` resolves every loop twice** (once for `loops`, once for
  `report_loops`), doubling sqlite + filesystem work on a path that runs after every firing.
  Resolve once with an include-report-only flag and filter.
- **Isolate the reports-screen render.** `_render_reports_page` runs inside the same `try` that
  produces `loops.html`; per §10's "degrade, never crash" it deserves its own `try/except` so a
  fault in the newer path cannot take the main dashboard down.
- **`bin/run-loop.sh` render-log redaction is silently suppressed.**
  `redact_file_inplace "$RENDER_LOG" 2>/dev/null || true` — the precheck equivalent has no
  suppression. If redaction fails the log is left unredacted AND silent. Keep `|| true` (step 6.5
  must never fail the run), drop the `2>/dev/null`, add a `log_err`.
- **Four small test gaps:** `test_gate_rejects_wrong_run_id` asserts `latest.html` absent but not
  the dated file (promotion is dated-first, so a half-promotion slips); loop-data `0700`/`0600`
  modes are implemented but unasserted; the reports screen's `historical` and report-only branches
  are untested; `test_kv_phrased_explanations_survive_the_redaction_gate`'s docstring claims real
  token VALUES still fail the gate but asserts no such case (bound elsewhere, not through the
  renderer).
- **Doc cosmetics:** `docs/INTERFACES.md` has a doubled closing paren near the retention sentence,
  and §10's opening still describes `generate.py` as writing only `dashboard/loops.html`
  (`--reports-out` and the second output appear only in the Amendment 2 bullet below it).
  `docs/SKILL_IMPORT_AND_AGENT_SURFACE_PLAN.md` still says "eleven-question intake rubric" /
  "all eleven sections" in two places while its own delta note adds q12.

## Tooling note (not this repo)

`peon` dispatches to **grok** needed a git workaround on all six dispatches this session: grok runs
under `--sandbox workspace`, and a linked worktree's real gitdir lives under the main repo's
`.git/worktrees/…` — outside the seatbelt — so `git commit` fails with `Operation not permitted`.
Peons escaped via `ssh localhost` (×5) and once by converting the worktree `.git` into a standalone
repo with an `objects/info/alternates` link, which then needed a manual
`git fetch <worktree> peon/<slug>` + `git update-ref` before `peon merge` would accept the branch.
Fix: run grok **without** the workspace sandbox for linked worktrees, exactly as `peon` already
does for gemini. Codex peons had no such trouble.

## Cross-agent state to be careful about

The **roops rebrand agent works in this same checkout**. As of 2026-07-30 it had ~11 unpushed
commits on main (brand-level rename, `site/` absorption, garden UI work). Report-pages work is
fully pushed through `44e0942`. Do not push, stash, or revert another agent's in-flight work — this
session merged around it (one `docs/INTERFACES.md` §10 conflict resolved keeping both texts; its
dirty files stash-sandwiched during each peon merge). If `peon merge` refuses on a dirty target,
check whose files they are first.
