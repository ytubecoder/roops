# PEON_REPORT — gc-health-watch

Branch: `peon/gc-health-watch`. Spec:
`docs/superpowers/specs/2026-09-05-gc-health-watch-design.md`.

## What changed

New read-only probe and watchdog loop that surface GC-stack failures already
sitting on the data host (OpenTwins X session / launch cycle, Postiz, GC
schedules). Precheck is the job; the engine only writes the alarm up.

Files touched (allowlist only):

- `probes/gc-health-read` (new; python3 stdlib; executable; header per spec §2)
- `probes/README.md` (one shipped-probes table row)
- `loops.d/gc-health-watch/loop.conf`
- `loops.d/gc-health-watch/precheck.sh` (uses `OUT_DIR`, never `LOOP_RUN_DIR`)
- `loops.d/gc-health-watch/prompt.md` (includes `## Finding identity`)
- `loops.d/gc-health-watch/dashboard.json` (`{"panels": []}`)
- `loops.d/gc-health-watch/SPEC.md`
- `tests/test_gc_health_read.py` (18 mandated names)
- `tests/test_gc_health_watch_precheck.py` (8 mandated names)
- `PEON_REPORT.md` (this file)

No `tests/fixtures/gc-health/**` — fixtures are generated in-test.

## Why

The X agent logged itself out for days and wrote that into memory files every
hour; nobody read them. This loop only reads what the data host already has.

## How verified

Hermetic: temp roots, copied `bin/{probe,probe_core.py,loopconf.py,requirements.py,schedule.py}`,
`LOOPS_PROBE_HOST` popped for local mode, Postiz served on `127.0.0.1` from
an in-test `http.server`. Never touched real `state/`, `~/.opentwins`, or
`~/projects`.

### Mandated 26 tests

```
test_all_green_has_no_findings_and_exit_0 ... ok
test_cdp_errors_warn ... ok
test_check_ok_with_fixture_inputs ... ok
test_check_unmet_when_venv_missing ... ok
test_error_and_overdue_rows_become_findings ... ok
test_excluded_and_manual_rows_never_alarm ... ok
test_findings_are_deterministically_ordered ... ok
test_launch_cycle_stalled_alert ... ok
test_locked_outranks_everything ... ok
test_logged_out_session_from_memory ... ok
test_no_secret_in_output ... ok
test_postiz_disabled_error_and_missed ... ok
test_postiz_unreachable_is_input_gap ... ok
test_recovery_entry_later_evidence_wins ... ok
test_schedules_subprocess_failure_is_input_gap ... ok
test_stale_task_ledger_is_ignored ... ok
test_still_healthy_is_logged_in ... ok
test_writes_failing_from_task_ledger ... ok
test_findings_exit_1_and_are_rendered ... ok
test_inputs_land_under_out_dir ... ok
test_loopconf_parses_with_expected_values ... ok
test_output_deterministic_modulo_timestamp ... ok
test_prompt_has_finding_identity_heading ... ok
test_section_error_is_rendered_not_hidden ... ok
test_silent_green_exits_0 ... ok
test_transport_failure_is_input_gap_exit_1 ... ok

----------------------------------------------------------------------
Ran 26 tests in 22.097s

OK
```

Command: `python3 -m unittest tests.test_gc_health_read tests.test_gc_health_watch_precheck -v`

### Full suite — actual tail of `bash tests/run-tests.sh`

```
======================================================================
FAIL: test_cutover_runbook_exists_and_names_every_loop (test_loopctl.TestSnapshotRestore.test_cutover_runbook_exists_and_names_every_loop)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/llm/.peon/worktrees/loops-gc-health-watch/tests/test_loopctl.py", line 4437, in test_cutover_runbook_exists_and_names_every_loop
    self.assertIn(name, step3, msg=f"{name} missing from step 3")
    ~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 'ads-delivery-watch' not found in 'Step 3 — Arm the guest\n\nInstall in this order. Each `install` runs a real self-verify engine run (~10 sequential runs). Kagami last: its verify run may open or update the mirror PR, which is its job. Use a 600s poll so a codex verify cannot time out the install and orphan the engine.\n\n- Run: ssh firstparty-svc \'cd ~/projects/loops && for n in hello-watchdog hello-loop loop-sensei ads-google ads-intl ads-reddit ads-x ads-program gc-actions kagi-ban tailnet-zones kagami; do LOOPCTL_INSTALL_POLL_TIMEOUT_S=600 ./bin/loopctl install "$n" || exit 1; done\'\n- Expected outcome: each name prints a successful install; guest `./bin/loopctl list` shows them installed.\n- If fails: the named loop\'s install stderr. Do not skip ahead; a failed install boots the unit back out. Fix requirements / probe / credential, then re-run from the failed name.\n\n- Run: ssh firstparty-svc \'cd ~/projects/loops && ./bin/loopctl console install\'\n- Expected outcome: `loops-console.service` enabled and `/api/state` 200 on the guest loopback.\n- If fails: linger / tz / XDG host-check messages; `loginctl enable-linger`, set `XDG_RUNTIME_DIR=/run/user/$(id -u)`, then retry.\n\n### ' : ads-delivery-watch missing from step 3

----------------------------------------------------------------------
Ran 887 tests in 125.457s

FAILED (failures=1)
OK wrote + validated action set: /var/folders/t7/tl9jvgxs28392s28thsqr14r0000gn/T/tmpou63h3e8/action-set (2 actions; continuity NOT verified). Include action_set.written: 1 in your contract metrics.
== /Users/llm/.peon/worktrees/loops-gc-health-watch/tests/test_adapters.sh ==
== codex adapter ==
== claude adapter ==
== cross-cutting: forbidden flags / no adapter-side timeout ==

passed: 158, failed: 0
== /Users/llm/.peon/worktrees/loops-gc-health-watch/tests/test_examples.sh ==
== tests/test_examples.sh: examples/hello-loop e2e (fake engine) ==
== tests/test_examples.sh: examples/hello-watchdog e2e (fake engine) ==

passed: 35, failed: 0
== /Users/llm/.peon/worktrees/loops-gc-health-watch/tests/test_runner.sh ==
== bin/run-loop.sh: completed ok/warn/alert ==
== bin/run-loop.sh: skipped-overlap ==
== bin/run-loop.sh: precheck (agent) ==
== bin/run-loop.sh: watchdog ==
== bin/run-loop.sh: engine-timeout / stale-green ==
== bin/run-loop.sh: contract-violation ==
== bin/run-loop.sh: transient retry / non-retried failures ==
== bin/run-loop.sh: harness-error ==
== bin/run-loop.sh: suppression / idempotence ==
== bin/run-loop.sh: retention pruning ==
== bin/run-loop.sh: enabled=false ==
== bin/run-loop.sh: schedule=manual (IMPORTANT #2b) ==
== bin/run-loop.sh: --dry-run ==
== bin/run-loop.sh: prompt composition ==
== bin/run-loop.sh: start-of-run non-blocking dashboard regen ==
== bin/run-loop.sh: .env seam + host requirements ==

passed: 154, failed: 0
== /Users/llm/.peon/worktrees/loops-gc-health-watch/tests/test_runner_pages.sh ==
FAIL: render log written (missing: /var/folders/t7/tl9jvgxs28392s28thsqr14r0000gn/T//loops-runner-test.gNFpUw/state/runs/20260904T230754Z-pageloop-8d9e8f/page-render.log)
cat: \033[34m/var/folders/t7/tl9jvgxs28392s28thsqr14r0000gn/T//loops-runner-test.cFUjeR/state/runs/20260904T230758Z-pageloop-9abc98\033[39;49m\033[0m/page-render.log: No such file or directory
FAIL: reason logged (expected to contain [redaction])
test_runner_pages: passed=21 failed=2
FAIL: /Users/llm/.peon/worktrees/loops-gc-health-watch/tests/test_runner_pages.sh
== /Users/llm/.peon/worktrees/loops-gc-health-watch/tests/test_skill_import_e2e.sh ==
== tests/test_skill_import_e2e.sh: import -> two runs -> finding_id stability ==

passed: 16, failed: 0
```

(The first `test_runner_pages` FAIL line in the live terminal also wrapped the path in ANSI color codes; reproduced here with the escape sequences visible in the `cat:` line.)

These two full-suite failures are **outside the allowlist** and pre-exist this change:

1. `test_cutover_runbook_exists_and_names_every_loop` fails first on
   `ads-delivery-watch` (already on HEAD, `schedule=daily:09:00`, not in
   `workflows/firstparty-cutover.txt` step 3). `ads-hard-cut` is the same
   shape. Editing that runbook is not allowed here. Once those two are
   added, this loop will also need a step-3 name (`gc-health-watch`).
2. `tests/test_runner_pages.sh` (2 fails) looks for `page-render.log` and
   hits ANSI-colored paths (`\033[34m...\033[0m`). No file in this change
   touches the runner or page render.

`test_validate_all_over_real_loops_d_exits_0` passed, so `loopctl validate`
accepts the new loop.

## Spec ambiguities resolved

1. **`--check` exit code vs `bin/probe --check`.** Spec §2.2 says the probe
   exits 1 on unmet; INTERFACES §14 maps a non-zero probe `--check` through
   the client to exit 3. The mandated tests assert exit 1, so `--check`
   tests invoke `probes/gc-health-read --check` directly (with the same
   `MAGUYVA_REPO` / `OT_HOME` / `POSTIZ_API_BASE` / `GC_HEALTH_NOW` the
   `.env` seam feeds `bin/probe`). JSON tests run through copied
   `bin/probe … --out` in local mode, as §4.1 requires.
2. **`sections.schedules.excluded` shape.** Spec says listed "with a
   reason". Implemented as `[{"name", "reason"}, …]`. Tests only require
   the automated names to be present.
3. **`generated_at`.** When `GC_HEALTH_NOW` is set, both `now` and
   `generated_at` use that instant so day windows are deterministic.
4. **Postiz integrations payload.** Spec's happy path is a JSON list; the
   collector also accepts a dict with an `integrations` list. Tests serve a
   list.
5. **Opentwins sub-part error vs precheck rendering.** Probe still reports
   successful sub-parts when another fails (spec §2.6). Precheck, per §3.2,
   prints `ERROR: <message>` under that section heading *instead of* its
   rows when `sections.<name>.error` is set.
6. **Cutover runbook.** Spec forbids editing it. Foreman needs to add
   `gc-health-watch` to step 3 (and should add the already-missing
   `ads-delivery-watch` and `ads-hard-cut` while there).

## Git commit blocked

`git add` failed with:

```
fatal: Unable to create '/Users/llm/projects/loops/.git/worktrees/loops-gc-health-watch/index.lock': Operation not permitted
```

Per dispatch rules: stop, do not work around a blocked commit (no new `.git`,
no remotes, no pushes). All implementation files and this report are left as
plain files in the worktree for the foreman to commit.

Uncommitted paths:

```
modified:   probes/README.md
untracked:  loops.d/gc-health-watch/
untracked:  probes/gc-health-read
untracked:  tests/test_gc_health_read.py
untracked:  tests/test_gc_health_watch_precheck.py
untracked:  PEON_REPORT.md
```

## Open questions

- Foreman deploy path (not done here, per spec §6): `loopctl validate
  gc-health-watch`, live probe on llm, two-checkout pull on firstparty,
  supervised first run, install. Cadence `daily:09:15` local.
- `workflows/firstparty-cutover.txt` step 3 does not name this loop. Same
  pre-existing gap for `ads-delivery-watch` and `ads-hard-cut`.
- Foreman needs to `git add` + commit the paths above on `peon/gc-health-watch`.
