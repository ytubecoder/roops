# PEON_REPORT — B-25 WP5 state snapshot

## What changed

- `bin/loopctl` gained `snapshot` and `restore` verbs (dispatch + `common_sub` parents).
- `loopctl snapshot <out.tar.gz> [--force]`:
  - refuses live `state/locks/*.lock` pids via `lock.py`'s existing `read_holder` + `pid_alive` unless `--force` (WARNING line);
  - copies sqlite with `Connection.backup()` (no `-wal`/`-shm` members);
  - tars `state/loops.sqlite`, `state/runs/`, `state/loop-data/`, `reports/`, plus `state/.snapshot-counts.json` (archive-only, never written into the source root);
  - skips symlinks and excluded trees (`locks`, `launchd-logs`, `tmp`, `probe-log`, `kagami-fixture`, `launchd/`);
  - writes via a temp file in the archive's directory, `os.replace`, mode `0600`;
  - prints `runs=… findings=… dispositions=… loop_events=… run_dirs=… reports=…` and `archive_bytes`; `--json` emits the same as a dict.
- `loopctl restore <in.tar.gz> [--force]`:
  - refuses a target db with `runs` rows unless `--force`;
  - extracts into `state/.restore-<pid>/` with `tarfile` `filter="data"`;
  - replace-not-merge of managed paths (`rename` aside to `.pre-restore-<pid>`, swap in, delete backups);
  - re-applies `0700`/`0600`; `db.py init`; count-file check (`count mismatch` → exit 1); best-effort dashboard regen.
- `workflows/firstparty-cutover.txt` — operator runbook (design §8.2), steps 1–6 + rollback.
- Docs: `docs/INTERFACES.md` §8 verbs + §1 archive-only counts file; `CLAUDE.md` fleet-state / Start here; `README.md` Hosts section.

## Why

Implements `openspec/changes/b-25-linux-port-2026-08-23/specs/state-snapshot/spec.md` so fleet state can be cold-copied llm → firstparty as one checked unit. The runbook is the cutover; this package does not execute it.

## Files touched (allowlist)

- `bin/loopctl`
- `tests/test_loopctl.py` (`TestSnapshotRestore`, all eight mandated names)
- `workflows/firstparty-cutover.txt`
- `docs/INTERFACES.md`
- `CLAUDE.md`
- `README.md`
- `PEON_REPORT.md`

Not touched: `bin/lock.py` (`pid_alive` / `read_holder` already public; imported as `lock_mod`), `bin/db.py` (counts against the backed-up sqlite in loopctl). `~/.claude/workflows/loops-reboot-recovery.txt` is outside the repo; the runbook tells the operator what to edit.

## How verified

Hermetic only: every snapshot/restore in tests uses a `LoopsRoot` temp tree, never `~/projects/loops` state.

```
python3 -m unittest tests.test_loopctl.TestSnapshotRestore -v
```

8 tests, OK.

Full suite (green run). This session's environment has `CLICOLOR_FORCE=1`, which makes macOS `ls -d` inject ANSI into paths and fails `tests/test_runner_pages.sh` (not on the allowlist; not caused by this change). Re-ran as `env -u CLICOLOR_FORCE CLICOLOR=0 bash tests/run-tests.sh` → exit 0.

Python: `Ran 849 tests in 93.874s` / `OK`.

Last 15 lines of that `bash tests/run-tests.sh` output (actual):

```
== bin/run-loop.sh: retention pruning ==
== bin/run-loop.sh: enabled=false ==
== bin/run-loop.sh: schedule=manual (IMPORTANT #2b) ==
== bin/run-loop.sh: --dry-run ==
== bin/run-loop.sh: prompt composition ==
== bin/run-loop.sh: start-of-run non-blocking dashboard regen ==
== bin/run-loop.sh: .env seam + host requirements ==

passed: 154, failed: 0
== /Users/llm/.peon/worktrees/loops-b25-wp5-snapshot/tests/test_runner_pages.sh ==
test_runner_pages: passed=23 failed=0
== /Users/llm/.peon/worktrees/loops-b25-wp5-snapshot/tests/test_skill_import_e2e.sh ==
== tests/test_skill_import_e2e.sh: import -> two runs -> finding_id stability ==

passed: 16, failed: 0
```

Shell fixtures in the same run: adapters 158, examples 35, runner 154, runner_pages 23, skill_import_e2e 16 — all failed: 0.

## Deviations

- `bin/lock.py` / `bin/db.py` unchanged: existing helpers were enough.
- Excluded-lock fixture in `test_snapshot_contents_and_counts` uses pid `999999`, not `1`. Pid 1 is alive on macOS, so a `1 …` lock file would (correctly) refuse snapshot; the spec wants those excluded files present in the source tree, not to trip the live-lock gate.
- First unforced `bash tests/run-tests.sh` exited 1 on `test_runner_pages.sh` (`ls` color). Documented above; not patched (file not on the allowlist). Green run used `CLICOLOR=0`.

## Commit

`git commit` was blocked by the sandbox:

```
fatal: Unable to create '/Users/llm/projects/loops/.git/worktrees/loops-b25-wp5-snapshot/index.lock': Operation not permitted
```

No workaround (no new `.git`, no remotes, no push). Implementation files and this report are left on disk uncommitted for the foreman.

## Open questions

- WP4 is not on this branch (spec said WP1–WP3 verbs exist). The runbook still names the design §8.2 install set, including loops whose probe retrofits land in WP4.
- The cutover itself, guest staging, and rehearsal against a copy of live state stay with the foreman (design §8.3 / §10).
- Flip-blocking answers (phoneapp, kagami auth, tailnet name) remain design §12.
