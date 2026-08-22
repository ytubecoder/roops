# PEON_REPORT — B-25 WP3 (install-backend)

Branch: `peon/b25-wp3-backend`
Spec: `openspec/changes/b-25-linux-port-2026-08-23/specs/install-backend/spec.md`
Design authority (WHY): same change, `design.md` §6

## What changed

Backend-aware install state, a `loopctl console` verb, systemd install-time host checks, and the matching docs.

### `bin/loopctl`
- `unit_files_present(root, name)` — file presence only. launchd = plist; systemd = both service+timer.
- `scheduler_loaded(name)` — live check. launchd `print`; systemd `is-enabled`.
- `_is_installed` is now `unit_files_present and scheduler_loaded`.
- `_systemctl` defaults `XDG_RUNTIME_DIR=/run/user/<uid>` in the child when unset.
- `_host_checks(root)` (systemd only): linger, `LOOPS_EXPECT_TZ` vs `/etc/localtime` (seam `LOOPS_LOCALTIME_PATH`) / `timedatectl` (seam `LOOPS_TIMEDATECTL`), XDG. Called from `cmd_install` (after WP1 requirements + run-first, before writing units) and from `console install`.
- `loopctl console install|uninstall|status` (status default). launchd writes `launchd/com.roops.console.plist` via plistlib; systemd writes `loops-console.service` with singleton `LOOPS_ROOT` refusal. Post-install poll of `/api/state` (seam `LOOPS_CONSOLE_PROBE_URL`); timeout tears down. No `loop_events` row.
- Install poll failure strings: `under the scheduler ({backend})`, not `under launchd`.

### `dashboard/generate.py`
- Lazy-seam mirror of `unit_files_present` (`_install_backend` + `_schedule_loaded`). Does not import `bin/loopctl`.
- 巡 tooltip: `schedule loaded ({backend})`.

### `bin/console.py`
- `_loaded` delegates to `loopctl.scheduler_loaded`.
- `_state.plist_present` / `/rounds` 409 use `unit_files_present`. Field names unchanged.
- Loads `bin/loopctl` with `SourceFileLoader` (the file has no `.py` suffix).

### Docs
- `docs/INTERFACES.md` §8 / §8.1 / §10 / §13
- `README.md` badge + “macOS and Linux”
- `CLAUDE.md` 🚨 block + console install sentence
- `skills/loops/SKILL.md` “host scheduler (launchd on macOS, systemd on Linux)”
- `docs/LOOP_AUTHORING.md` §5 systemd `Persistent=true` paragraph

### Tests (every §2 name)
- `tests/test_loopctl.py`: `test_unit_files_present_and_scheduler_loaded_launchd`, `test_console_install_launchd_writes_plist_and_verifies`, `test_console_uninstall_and_status`, `test_install_failure_strings_are_backend_neutral`, `test_console_is_a_known_verb`; existing poll-timeout test also asserts the new wording.
- `tests/test_loopctl_systemd.py`: `test_unit_files_present_requires_both_units`, `test_scheduler_loaded_uses_is_enabled_with_xdg_default`, `test_host_checks_linger_tz_xdg`, `test_install_refuses_on_failed_host_check_before_writing_units`, `test_console_install_systemd_unit_singleton_and_verify`, `test_install_failure_strings_say_systemd`.
- `tests/test_dashboard.py`: launchd pin on classes that write plists; `test_schedule_loaded_systemd_both_units`; `test_unit_files_present_mirror_never_drifts`.
- `tests/test_console.py`: `TestConsoleApiSystemd`; `test_state_plist_present_false_on_systemd_when_only_plist_exists`.

## Why

On Linux the garden and console still decided “installed?” by launchd plist presence, so every loop rendered 休, staleness never fired, and `/rounds` 409’d. The console had no installer. Linger / timezone / XDG were unguarded at install.

## How verified

`env -u CLICOLOR_FORCE -u CLICOLOR -u GREP_COLOR -u GREP_COLORS bash tests/run-tests.sh`

(The sandbox exports `CLICOLOR_FORCE=1`. That makes `ls -d` inject ANSI codes into paths in `tests/test_runner_pages.sh`, which is not in the WP3 allowlist. Unsetting it is a test-host concern, not a product change. Python tests were already green either way.)

Last 15 lines of that run:

```
== bin/run-loop.sh: retention pruning ==
== bin/run-loop.sh: enabled=false ==
== bin/run-loop.sh: schedule=manual (IMPORTANT #2b) ==
== bin/run-loop.sh: --dry-run ==
== bin/run-loop.sh: prompt composition ==
== bin/run-loop.sh: start-of-run non-blocking dashboard regen ==
== bin/run-loop.sh: .env seam + host requirements ==

passed: 154, failed: 0
== /Users/llm/.peon/worktrees/loops-b25-wp3-backend/tests/test_runner_pages.sh ==
test_runner_pages: passed=23 failed=0
== /Users/llm/.peon/worktrees/loops-b25-wp3-backend/tests/test_skill_import_e2e.sh ==
== tests/test_skill_import_e2e.sh: import -> two runs -> finding_id stability ==

passed: 16, failed: 0
```

Python layer from the same run:

```
Ran 825 tests in 76.316s

OK
```

Shell: adapters 158, examples 35, runner 154, runner_pages 23, skill_import_e2e 16 — all failed: 0.

No real `launchctl` / `systemctl` / `loginctl` — every call goes through `LOOPS_LAUNCHCTL` / `LOOPS_SYSTEMCTL` / `LOOPS_LOGINCTL` / `LOOPS_TIMEDATECTL`.

## Deviations

1. **Glossary “launchd plist” copy.** Spec 1.2: grep `plist` in `dashboard/generate.py` outside comments and rewrite glossary/legend text. After B-19 the garden kicker glossary is gone; the only remaining `plist` mentions in that file are comments (updated) and the 巡 tooltip, which now reads `schedule loaded ({backend})`. No new legend string was invented.
2. **`_systemd_is_installed` kept.** Still referenced by `TestSystemdInstallActions` (no `LOOPS_INSTALL_BACKEND` pin). Left as the original systemd-only file+`is-enabled` check. `_is_installed` uses the new predicates.
3. **`tasks.md` not ticked.** Not on the file allowlist.
4. **`console.py` loads `loopctl` via `SourceFileLoader`.** `importlib.util.spec_from_file_location` yields no loader for a suffix-less `bin/loopctl`. Same rule as `tests/test_loopctl_systemd.py`.
5. **Console plist includes `WorkingDirectory`.** Spec listed KeepAlive/RunAtLoad/env/args/logs; WorkingDirectory matches the loop plist shape and the systemd unit.

## Commit

**Blocked.** `git add` / `git commit` failed:

```
fatal: Unable to create '/Users/llm/projects/loops/.git/worktrees/loops-b25-wp3-backend/index.lock': Operation not permitted
```

No workaround (no new `.git`, no remotes, no push). Working tree still has the edits unstaged plus untracked `PEON_REPORT.md`. Foreman should commit.

## Open questions

- Should `_systemd_is_installed` be deleted in a follow-up once its callers go through `unit_files_present`/`scheduler_loaded` (or pin `LOOPS_INSTALL_BACKEND=systemd`)?
- Console `status --json` shape is `{backend, unit: {present, path}, loaded, http}` — spec only required `unit.present`.
- `test_runner_pages.sh` is sensitive to `CLICOLOR_FORCE=1` (`ls -d` into a path). Worth fixing off this ticket.
