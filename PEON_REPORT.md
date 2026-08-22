# PEON_REPORT — B-25 WP2 probe channel

Branch: `peon/b25-wp2-probe`
Spec: `openspec/changes/b-25-linux-port-2026-08-23/specs/probe-channel/spec.md`
Design authority: same change `design.md` §5

## What changed

Probe channel, three parts, shared core so the server and client cannot drift.

| File | Role |
|---|---|
| `bin/probe_core.py` | Header parse, wire grammar, clean env, process-group timeout, list/hash, log prune helpers, `Channel` (local/remote + in-memory ping+list cache) |
| `bin/probe-server` | Forced command: parse `SSH_ORIGINAL_COMMAND` with no shell, built-ins `ping`/`list`/`check`, probe exec, 0700/0600 log, `--authorize [--write] [--replace]` |
| `bin/probe` | Client: local vs remote via `LOOPS_PROBE_HOST`, `LOOPS_SSH` seam, `--` before host, `--out` atomic 0600, `--check` hash-drift |
| `probes/echo-test` | Test-only probe (`probe-writes: none`, `probe-output: text`) |
| `probes/README.md` | Trusted-unsandboxed wording, header grammar, `--check`, two-checkout deploy rule |
| `bin/loopctl` | `probe` verb (`status` default, `keygen`); registered in `dispatch` + `common_sub` parser |
| `docs/INTERFACES.md` | §1 layout (`probes/`, `bin/probe`, `bin/probe-server`, `bin/probe_core.py`, `state/probe-log/`); §8 verb table; new §14 |
| `tests/test_probe.py` | Every §2 server/client test by name |
| `tests/test_loopctl.py` | `test_probe_status_and_keygen` |

WP1 helpers used, not duplicated: `loopconf.load_env`, `requirements.runtime_path`.

## Why

The fleet is moving to a Linux guest; some loops still need llm-local data. Design D3: no loops-client daemon. The Mac runs sshd with one forced command that can execute only named, reviewed scripts in `probes/`. Loops call `bin/probe <name>` and never know local vs ssh. Security is the point: the server never interprets a shell.

## How verified

Command (this sandbox exports `CLICOLOR_FORCE=1`, which makes macOS `ls` emit ANSI into paths and breaks the pre-existing `tests/test_runner_pages.sh` `ls -d` helper; that file is not on the WP2 allowlist. Verification used the same script with that force-flag unset):

```
env -u CLICOLOR_FORCE bash tests/run-tests.sh
```

Exit 0.

Python layer: `Ran 825 tests in 80.822s` / `OK`

Shell: adapters 158, examples 35, runner 154, runner_pages 23, skill_import_e2e 16 — all failed: 0.

Last 15 lines of that run, copied from the log (not paraphrased):

```
== bin/run-loop.sh: retention pruning ==
== bin/run-loop.sh: enabled=false ==
== bin/run-loop.sh: schedule=manual (IMPORTANT #2b) ==
== bin/run-loop.sh: --dry-run ==
== bin/run-loop.sh: prompt composition ==
== bin/run-loop.sh: start-of-run non-blocking dashboard regen ==
== bin/run-loop.sh: .env seam + host requirements ==

passed: 154, failed: 0
== /Users/llm/.peon/worktrees/loops-b25-wp2-probe/tests/test_runner_pages.sh ==
test_runner_pages: passed=23 failed=0
== /Users/llm/.peon/worktrees/loops-b25-wp2-probe/tests/test_skill_import_e2e.sh ==
== tests/test_skill_import_e2e.sh: import -> two runs -> finding_id stability ==

passed: 16, failed: 0
```

No real `ssh` was invoked. `LOOPS_SSH` / `LOOPS_SSH_KEYGEN` fakes only. `--authorize` tests monkeypatch `HOME` to a temp dir. Never touched the real `~/.ssh`.

## Deviations (with why)

1. **Test fixture also copies `bin/schedule.py`.** Spec §2 says copy `probe`, `probe-server`, `probe_core.py`, `loopconf.py`, `requirements.py`. `loopconf.py` loads sibling `schedule.py` at import; without it the copied server cannot start. Not a product-file add; test-only extra copy. Allowlist product files unchanged.
2. **Timeout clamp / delayed header line via `probe_core.parse_header`, not `--dump-header`.** Spec §2 explicitly allows either. Unit-tested: `probe-timeout-s: 9999` → 600; a `# probe-timeout-s: 1` after a non-header comment is ignored (default 120).
3. **`loopctl probe` talks to `probe_core.Channel(args.root)` in-process**, not via a subprocess of `bin/probe`. Same module the client uses; needed so `--root` hermetic fixtures (echo-test + fake ssh) work. Production `--root` is the checkout.
4. **`env -u CLICOLOR_FORCE` around `tests/run-tests.sh`.** Bare `bash tests/run-tests.sh` in this sandbox failed two assertions in `test_runner_pages.sh` because `ls -d` paths contained ANSI (`CLICOLOR_FORCE=1`). Unrelated to the probe channel; that test is not on the allowlist. With the force-flag unset the same script exits 0.

## Open questions

- Should `loopctl probe` subprocess `$root/bin/probe` once WP4 starts using live `probe:` checks from a guest checkout, or keep the in-process Channel? Behavior matches §14 either way as long as ROOT is the checkout.
- `bin/probe` accepts repeated `--check <name>` in one process (the cache lives on `Channel`). Spec CLI shows one name; the two-name cache assertion is the unit-test of `probe_core` as specified.

## Commit

**Blocked.** `git add` / `git commit` failed with:

```
fatal: Unable to create '/Users/llm/projects/loops/.git/worktrees/loops-b25-wp2-probe/index.lock': Operation not permitted
```

No workaround attempted (no new `.git`, no remotes, no push). All work is left as plain files in the worktree for the foreman to commit. Working tree is intentionally dirty.
