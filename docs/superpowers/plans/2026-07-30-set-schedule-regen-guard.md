# set-schedule Regen Guard + §10 Staleness Close-out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** shipped 2026-07-30 (commits `5365039..a95688a` on main; see `## Shipped (as built)` at the end)

**Goal:** Make `loopctl set-schedule`'s dashboard regen best-effort (killing the console's false `400` for a schedule change that took effect), close INTERFACES §10's paused-staleness open question, and add a change-lifecycle routing note to CLAUDE.md.

**Architecture:** Copy the existing best-effort regen idiom from `cmd_disposition` (`bin/loopctl:1064-1068`) into `cmd_set_schedule`. Everything else is documentation amendments to the frozen contract (`docs/INTERFACES.md`), shipped in the same commit as the code they describe. Ticket Takeaway ticket **B-09** tracks this work (already in WIP).

**Tech Stack:** Python 3 stdlib only + bash. Tests are hermetic `unittest` (no network, fake launchctl seam). macOS — no GNU-only flags.

## Global Constraints

- `docs/INTERFACES.md` is the FROZEN contract: any behavior change ships its INTERFACES amendment **in the same commit** as the code.
- Tests must stay hermetic: no network, no real `launchctl`, no touching the real `~/projects/loops` state. Use the existing `LoopsRoot` fixture only.
- Repo root: `/Users/llm/projects/loops`. All commands below run from there unless stated.
- Run a single test class with: `cd tests && python3 -m unittest test_loopctl.TestSetSchedule -v` (discovery puts `tests/` on `sys.path`; `test_console.py` does `from test_loopctl import ...`, so always run from inside `tests/`).
- After every commit: `git push` (user works across machines).
- Commit messages follow repo style: `fix(loopctl): …`, `docs(interfaces): …` etc. (see `git log --oneline`).
- A global PostToolUse hook auto-runs `ruff format` on edited `.py` files and a turn-end `ruff check` may report findings. The repo does not conform to ruff's broad ruleset: **only fix findings on lines you yourself added/edited; ignore pre-existing debt in untouched lines/files.**
- `bin/loopctl` has no `.py` extension — hooks skip it; format your additions to match the surrounding code by hand.

---

### Task 1: Best-effort dashboard regen in `cmd_set_schedule`

**Files:**
- Modify: `bin/loopctl:974-985` (`cmd_set_schedule`)
- Test: `tests/test_loopctl.py` (class `TestSetSchedule`, ends ~line 1608)
- Test: `tests/test_console.py` (class `TestConsoleApi`, after `test_schedule_applies_and_regenerates_dashboard`, ~line 126)
- Modify: `docs/INTERFACES.md` (§13 paragraph at lines 905-911; §8 CLI table line 652)

**Interfaces:**
- Consumes: `_apply_schedule(root, from_dir, name, spec) -> (old_spec, new_spec)` (unchanged); `_dashboard_module()`; the guarded-regen idiom already present in `cmd_disposition` at `bin/loopctl:1064-1068`.
- Produces: `loopctl set-schedule` exits 0 with `warning: dashboard regen failed: <err>` on stderr when the mutation succeeded but regen raised. Console `POST /api/loops/<name>/schedule` consequently returns 200 in that case (console code unchanged — it keys off the exit code at `bin/console.py:299-302`).

- [ ] **Step 1: Write the failing CLI test**

Add to class `TestSetSchedule` in `tests/test_loopctl.py`, after `test_manual_removes_plist_after_bootout`:

```python
    def test_regen_failure_warns_but_exits_zero(self):
        self._write_loop("alpha", "daily:09:00")
        # The regen writes root/dashboard/loops.html; a FILE named `dashboard`
        # makes its makedirs raise — a hermetic dashboard-generation failure.
        with open(os.path.join(self.root, "dashboard"), "w") as f:
            f.write("in the way")
        r = self._set_schedule("alpha", "interval:15m")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("warning: dashboard regen failed", r.stderr)
        self.assertIn("schedule alpha: daily:09:00 -> interval:15m", r.stdout)
        self.assertIn("schedule=interval:15m", _read(self._conf_path("alpha")))
```

(The `LoopsRoot` fixture creates only `loops.d`, `examples`, `engines`, `bin`, `state` — no `dashboard/` dir, so the file collides cleanly.)

- [ ] **Step 2: Run it to verify it fails**

Run: `cd tests && python3 -m unittest test_loopctl.TestSetSchedule.test_regen_failure_warns_but_exits_zero -v`
Expected: FAIL — returncode is 1 (unguarded `dash.generate` raises `FileExistsError`, traceback on stderr), and stderr lacks the `warning:` line.

- [ ] **Step 3: Guard the regen in `cmd_set_schedule`**

In `bin/loopctl`, replace the two unguarded lines in `cmd_set_schedule`:

```python
    dash = _dashboard_module()
    dash.generate(root=args.root, loopconf_parse=loopconf.parse, schedule_parse=schedule.parse)
```

with the `cmd_disposition` idiom (match its comment style exactly):

```python
    dash = _dashboard_module()
    try:
        dash.generate(root=args.root, loopconf_parse=loopconf.parse, schedule_parse=schedule.parse)
    except Exception as e:  # noqa: BLE001 — dashboard regen is best-effort; the schedule change itself succeeded
        print(f"warning: dashboard regen failed: {e}", file=sys.stderr)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd tests && python3 -m unittest test_loopctl.TestSetSchedule -v`
Expected: all TestSetSchedule tests PASS (the new one plus the six existing).

- [ ] **Step 5: Add the console-level regression pin**

This test pins the client-visible consequence. It is written AFTER the fix, so it should pass immediately — it exists to catch anyone re-unguarding the regen. Add to class `TestConsoleApi` in `tests/test_console.py`, directly after `test_schedule_applies_and_regenerates_dashboard`:

```python
    def test_schedule_regen_failure_still_200(self):
        self.write_loop("alpha", schedule="daily:09:00")
        # A FILE at root/dashboard makes loopctl's regen fail; the schedule
        # mutation itself succeeded, so the response must stay 200 (§13) —
        # previously this surfaced as a false `400 invalid schedule`.
        with open(os.path.join(self.root, "dashboard"), "w") as f:
            f.write("in the way")
        status, _payload, _ = call(
            self.fixture, "POST", "/api/loops/alpha/schedule", {"spec": "interval:30m"}
        )
        self.assertEqual(status, 200)
        self.assertIn("schedule=interval:30m", _read(self.conf_path("alpha")))
```

- [ ] **Step 6: Run the console tests**

Run: `cd tests && python3 -m unittest test_console -v`
Expected: all PASS, including `test_schedule_regen_failure_still_200`.

- [ ] **Step 7: Amend INTERFACES §13 (same commit as the code)**

In `docs/INTERFACES.md`, replace this paragraph (currently lines 905-911):

```
Every mutation regenerates the dashboard before responding, but the two endpoints differ and
the difference is visible to a client. `/rounds`: the console owns the regen, best-effort — a
failure warns `warning: dashboard regen failed: …` on stderr and never changes the response.
`/schedule`: `loopctl set-schedule` regenerates unguarded AFTER writing the conf and plist, so
a regen exception exits non-zero and the console reports `400 invalid schedule` for a mutation
that DID take effect. Pre-existing behavior, documented rather than changed; the conf is the
source of truth, so re-reading `/api/state` after such a 400 shows the new schedule.
```

with:

```
Every mutation regenerates the dashboard before responding, and the regen is best-effort on
both endpoints — the mutation already succeeded, so a regen failure must never change the
response. `/rounds`: the console owns the regen and warns
`warning: dashboard regen failed: …` on stderr. `/schedule`: `loopctl set-schedule` owns it,
guarded the same way as the disposition verbs (warn on stderr, exit 0). **(Amended
2026-07-30):** before this, the set-schedule regen ran unguarded, so a regen exception exited
non-zero and the console reported a false `400 invalid schedule` for a schedule change that
DID take effect. The conf is the source of truth either way: `/api/state` reflects the new
schedule regardless of regen outcome.
```

- [ ] **Step 8: Amend the §8 CLI table line**

In `docs/INTERFACES.md` line 652, replace:

```
loopctl set-schedule <name> <spec>                                   # §5.1-validate; rewrite conf; re-render+reload plist iff installed; NEVER kickstart
```

with:

```
loopctl set-schedule <name> <spec>                                   # §5.1-validate; rewrite conf; re-render+reload plist iff installed; NEVER kickstart; best-effort dashboard regen
```

- [ ] **Step 9: Commit and push**

```bash
git add bin/loopctl tests/test_loopctl.py tests/test_console.py docs/INTERFACES.md
git commit -m "fix(loopctl): set-schedule dashboard regen is best-effort — no false 400 for an applied mutation"
git push
```

---

### Task 2: Close INTERFACES §10's paused-staleness open question (docs-only)

**Files:**
- Modify: `docs/INTERFACES.md` §10, the console-amendment bullet (currently lines 780-789)

**Interfaces:**
- Consumes: nothing from Task 1 (independent docs change; runs after it only to avoid same-file churn).
- Produces: §10 no longer carries an open question; the enabled-blind staleness behavior is settled with rationale. No code or test changes — `dashboard/generate.py` behavior is already exactly this.

- [ ] **Step 1: Verify no test or doc pins the open-question text**

Run: `grep -rn "Open question" tests/ docs/ | grep -iv warmstart`
Expected: the only hit is `docs/INTERFACES.md` (the text being replaced). If anything else pins it, stop and report back instead of editing.

- [ ] **Step 2: Replace the open question with the resolution**

In `docs/INTERFACES.md`, replace (end of the §10 console-amendment bullet, currently lines 787-789):

```
  `needs_attention`. Only the no-plist state is staleness-exempt (the pre-existing 2026-07-30
  amendment above). Open question, deliberately unresolved: whether a paused loop *should* be
  staleness-exempt — today it is not.
```

with:

```
  `needs_attention`. Only the no-plist state is staleness-exempt (the pre-existing 2026-07-30
  amendment above). **(Resolved 2026-07-30):** paused loops stay staleness-visible — settled,
  do not relitigate. Pause has no expiry (unlike `snooze --until`), so a paused-and-forgotten
  loop is exactly the failure mode `needs_attention` exists to catch; exempting it would
  create a silent way to turn a loop off forever. A deliberate long-term off is
  `set-schedule manual`, which removes the plist and lands in the staleness-exempt 休
  no-schedule state. Paused → keep nagging; manual → exempt. That split is the design.
```

- [ ] **Step 3: Commit and push**

```bash
git add docs/INTERFACES.md
git commit -m "docs(interfaces): §10 — resolve paused-staleness open question: paused loops stay staleness-visible"
git push
```

---

### Task 3: CLAUDE.md change-lifecycle routing note + full suite + ticket to review

**Files:**
- Modify: `CLAUDE.md` (repo root — `/Users/llm/projects/loops/CLAUDE.md`)

**Interfaces:**
- Consumes: Tasks 1-2 committed (this task runs the full suite over their result).
- Produces: a routing rule so future sessions know when OpenSpec applies; B-09 moved to review.

- [ ] **Step 1: Add the routing section to CLAUDE.md**

In `/Users/llm/projects/loops/CLAUDE.md`, insert a new section immediately BEFORE the line `## Non-negotiables (from the plan — full rationale in docs/HARNESS_PLAN.md)`:

```markdown
## Change lifecycle routing (which flow for which change)
- **Feature-scale change** (new capability, new loop, new output tier — anything that IS or
  should be a B-ticket): OpenSpec is the enrolled spec lifecycle. `openspec-propose` creates
  `openspec/changes/<date>-<ticket-id>-<slug>/`; implement via `openspec-apply-change`;
  `openspec-archive-change` when done. Name the change dir after the Ticket Takeaway ticket
  (precedent: B-01, the only change through it so far — B-04..B-08 bypassed it via
  superpowers plans; from now on, feature-scale work goes through OpenSpec).
  `docs/INTERFACES.md` stays the frozen authority — OpenSpec artifacts point at it, never
  duplicate it (`openspec/config.yaml` says the same).
- **Small fix / hardening / docs amendment** (like B-09): superpowers plan in
  `docs/superpowers/plans/` or a direct edit; the INTERFACES amendment ships in the same
  commit as the code. No OpenSpec change below feature scale.
- **Either way:** open a Ticket Takeaway ticket at start of work
  (`python3 ~/.claude/ticket-takeaway/tickets-cli.py add loops "<title>" --section wip`),
  move it to review when coding completes.
```

- [ ] **Step 2: Run the full hermetic suite**

Run: `bash tests/run-tests.sh`
Expected: all tests pass (742 as of 2026-07-30, plus the 2 added in Task 1). If anything fails, fix only what Tasks 1-3 broke; report pre-existing failures back instead of chasing them.

- [ ] **Step 3: Commit and push**

```bash
git add CLAUDE.md
git commit -m "docs(claude): change-lifecycle routing — OpenSpec for feature-scale, plans for fixes, ticket either way"
git push
```

- [ ] **Step 4: Move ticket B-09 to review**

```bash
python3 ~/.claude/ticket-takeaway/tickets-cli.py move loops B-09 review
```

---

## Shipped (as built)

Four commits on main, all pushed 2026-07-30; full hermetic suite 756/756 green after.

- `5365039` — Task 1 as planned (guard + CLI test + console pin + §13/§8 amendments, one commit).
- `e778aa4` — Task 2 as planned (§10 resolution, docs-only).
- `4033878` — Task 3 with a controller amendment: the commit also landed this plan doc
  (newly tracked; `docs/superpowers/plans/` is tracked by convention) and
  `PRODUCT_BACKLOG.md` (B-09 opened + moved to review), and the ticket move ran BEFORE the
  commit. The brief's "expect 744 tests" was a stale baseline; actual was 756 (suite had
  grown to 754 before this plan; +2 from Task 1).
- `a95688a` — fix wave from the final whole-branch review, beyond the plan's scope:
  1. `_dashboard_module()` moved INSIDE the regen guard at BOTH call sites
     (`cmd_set_schedule`, `cmd_disposition`) — a missing/broken `dashboard/generate.py`
     was still exiting non-zero after a successful mutation, reproducing the false 400
     the plan exists to kill.
  2. `bin/console.py` `/schedule` now passes the loopctl child's stderr through to its own
     stderr on exit 0, so a regen failure is never symptomless (the warning previously
     died inside `capture_output=True`).
  3. The console regen-failure test gained non-vacuousness assertions (regen really
     failed; warning really surfaced).
  4. This plan doc's `cmd_dispose` naming corrected to the real `cmd_disposition`.

Parked with rulings (final review + re-review): a non-`ValueError` after the conf rewrite
still exits non-zero — CORRECT, the mutation is genuinely half-applied there; task-block
prose in this plan describes state as of task time (e.g. Task 1's "console code unchanged"
predates the fix wave) — historical, not errata.
