# PEON_REPORT — B-25 WP1 host requirements

Worktree: `peon/b25-wp1-requires`
Change: `openspec/changes/b-25-linux-port-2026-08-23` (host-requirements spec)
OpenSpec `apply` was blocked (`tasks.md` missing). Implemented the peon spec directly.

## What changed

Fleet `.env` loader + `requires=` host-requirement seam, so a loop that cannot run on this host refuses to install instead of failing at 2am, and scheduled runs finally see host config (`GC_BASE`, etc.).

| File | Role |
|---|---|
| `bin/loopconf.py` | `ENV_KEY_RE`, `load_env()`, `EnvFileError`, `requires=` field/`REQ_RE`, `env` CLI |
| `bin/requirements.py` | **NEW.** `runtime_path()`, `check()`, CLI `check --json/--no-live` |
| `bin/loopctl` | `requirements` verb; validate notices; install/resume/set-schedule refusal; `_runtime_path` alias; `# requires=` scaffold comment |
| `bin/run-loop.sh` | default `TMPDIR=$ROOT/state/tmp`; `.env` load after start-run; config-only requirement check; `env -u` strip on engine spawn |
| `engines/fake.sh` | dump `GC_BASE` when set (test-only env-strip canary) |
| `.gitignore` | `.env` |
| `docs/INTERFACES.md` | §1, §4.1 3a, §5 table + §5.0 + §5.3, §6.1, §8, §8.1 1a |
| `tests/test_loopconf.py` | mandated `load_env` / `requires` / env CLI tests |
| `tests/test_requirements.py` | **NEW.** mandated checker tests |
| `tests/test_loopctl.py` | mandated verb / validate / install / resume / known-verb / scaffold tests |
| `tests/test_runner.sh` | mandated `.env` / unmet / TMPDIR tests |

## Why

Design §3 (F1: nothing read `.env`) and §4 (D2: loops declare host needs; harness answers). WP1 is the app/loop separation seam. Probe binaries themselves are WP2; this package only calls `bin/probe --check` (tests use a fake).

## How verified

Command: `bash tests/run-tests.sh` (after unsetting `CLICOLOR_FORCE`/`CLICOLOR`/`FORCE_COLOR` — see deviations). Exit 0.

Python: `Ran 809 tests in 61.532s OK`
Shell: adapters 158, examples 35, runner 154, runner_pages 23, skill_import_e2e 16 — all `failed: 0`.

Last 15 lines of the real `bash tests/run-tests.sh` output:

```
== bin/run-loop.sh: retention pruning ==
== bin/run-loop.sh: enabled=false ==
== bin/run-loop.sh: schedule=manual (IMPORTANT #2b) ==
== bin/run-loop.sh: --dry-run ==
== bin/run-loop.sh: prompt composition ==
== bin/run-loop.sh: start-of-run non-blocking dashboard regen ==
== bin/run-loop.sh: .env seam + host requirements ==

passed: 154, failed: 0
== /Users/llm/.peon/worktrees/loops-b25-wp1-requires/tests/test_runner_pages.sh ==
test_runner_pages: passed=23 failed=0
== /Users/llm/.peon/worktrees/loops-b25-wp1-requires/tests/test_skill_import_e2e.sh ==
== tests/test_skill_import_e2e.sh: import -> two runs -> finding_id stability ==

passed: 16, failed: 0
```

## Deviations (from the spec as written)

1. **`TMPDIR` chmod.** Spec two-liner is `mkdir -p "$TMPDIR" && chmod 700 "$TMPDIR"` under `set -e`. Implemented as `mkdir -p` then `chmod 700 … 2>/dev/null || true`. A pre-set host `TMPDIR` (`/tmp`, `/var/folders/…/T`) is not ours to chmod; a failing chmod would abort every run. When `TMPDIR` is unset, the default `$ROOT/state/tmp` is still created and mode 700 (pinned by `test_tmpdir_under_state`).

2. **`env -u` only when there are keys.** Spec's `exec env ${UNSET_ARGS} …` with an empty bash-3.2 array under `set -u` unbound-variable'd and marked every engine run `engine-failed`. Empty `.env` (the common case) keeps the previous `LOOP_NAME=… exec "$ENGINE_ADAPTER"` path. Non-empty strip lists still use `exec env -u K1 -u K2 … LOOP_NAME=… "$ENGINE_ADAPTER"`.

3. **`engines/fake.sh` no longer cats the composed prompt into `engine.log`.** Precheck stdout is injected into the prompt, so dumping the prompt would put `GC_BASE=http://x` in `engine.log` even after `env -u`. The mandated assertion is that the engine log does not contain that value. Other tests read `prompt.composed.md` for prompt composition, not `engine.log`.

4. **`# requires=` scaffold comment is post-processed.** `bin/skill_import.py` (the template home) is not on the allowlist. `cmd_new` and `_cmd_import_apply` insert the comment after the `tags` line if present, else after `# notes=` (today's template has no `tags` line).

5. **`_runtime_path` is loaded the same way as `loopconf`** (`_load_module_from_path` → `requirements.runtime_path`), not a literal `from requirements import runtime_path`. Call sites (`:285`, plist PATH) still use the `_runtime_path` alias.

6. **Malformed `.env` at run time uses `finalize_and_finish harness-error …` and therefore exits 0** (recorded outcome), matching the spec's "use finalize_and_finish like precheck-failed". INTERFACES §4.2 still says `harness-error` exits 1 after best-effort recording; the trap path still does that. Spec was the authority here.

7. **Verify env.** First `bash tests/run-tests.sh` in this session failed two `test_runner_pages.sh` assertions because `CLICOLOR_FORCE=1` made `ls -d` emit ANSI-wrapped paths. That file is not on the allowlist and the failure is host-env, not this change. Re-ran after `unset CLICOLOR_FORCE CLICOLOR FORCE_COLOR`; suite exit 0.

## Commit

Blocked by the sandbox. `git add` failed with:

```
fatal: Unable to create '/Users/llm/projects/loops/.git/worktrees/loops-b25-wp1-requires/index.lock': Operation not permitted
```

No workaround attempted (no new `.git`, no remotes, no push). Implementation files and this report are left uncommitted in the worktree for the foreman.

## Open questions

- Should `finalize_and_finish harness-error` be taught to `finalize_exit 1` so it matches INTERFACES §4.2, or is a recorded harness-error-at-exit-0 the intended garden-red path (B-05)?
- `loopctl requirements` with a missing named loop currently exits 2 (`loop not found`); spec only defined 0/1 for the matrix. Fine unless WP3 wants it quieter.
- Live `probe:` still needs WP2's `bin/probe`. Until then, `install` of a `probe:` loop is unmet with `bin/probe missing` unless a fake is present.
- `skill_import.py` template should gain the `# requires=` line in a later package so new/import stop post-processing.
