# PEON_REPORT — gc-health-watch (revision: repo-root .env)

Branch: `peon/gc-health-watch`. Follow-up to the live-run correction:
`POSTIZ_API_KEY` lives in `$MAGUYVA_REPO/.env`, not `growth-console/.env`
(that file does not exist on the data host). Spec on main, §2.1 / header /
§2.2.

## Files touched

- `probes/gc-health-read` — `ENV_FILES = [$MAGUYVA_REPO/.env, $GC_DIR/.env]`;
  first file that defines the key wins; missing file skipped; env still wins
  over both. `--check` names both files. `# probe-reads:` header matches the
  updated spec exactly.
- `probes/README.md` — the one shipped-probes table row, reads column
  aligned with the new header.
- `tests/test_gc_health_read.py` — added `test_postiz_key_from_repo_root_env`
  (key only in `<repo>/.env`, no `growth-console/.env`; Postiz fixture now
  requires `Authorization: fixture-secret-KEY-123` so a missed lookup is
  `HTTP 401` instead of a silent green). Existing growth-console/.env
  fixtures still pass (fallback).
- `PEON_REPORT.md` (this file)

Nothing else. Spec file on this branch is still the pre-correction text;
that path is outside the allowlist.

## Why

Foreman live run on llm: `growth-console/.env` is absent; the key is in the
same repo-root `.env` `probes/ads-delivery-watch` already reads.

## How verified

Hermetic temp roots, copied `bin/{probe,probe_core.py,loopconf.py,requirements.py,schedule.py}`,
`LOOPS_PROBE_HOST` popped, Postiz on `127.0.0.1`. Never touched real
`state/`, `~/.opentwins`, or `~/projects`.

27 gc-health tests (18 original + 8 precheck + new key test):

```
test_postiz_key_from_repo_root_env ... ok
...
Ran 27 tests in 22.512s

OK
```

Command: `python3 -m unittest tests.test_gc_health_read tests.test_gc_health_watch_precheck -v`

Ruff (via `uvx ruff check` with a temp cache): `All checks passed!`

## Actual tail of `bash tests/run-tests.sh`

```
----------------------------------------------------------------------
Ran 891 tests in 122.044s

OK
OK wrote + validated action set: /var/folders/t7/tl9jvgxs28392s28thsqr14r0000gn/T/tmpzooz6e7l/action-set (2 actions; continuity NOT verified). Include action_set.written: 1 in your contract metrics.
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
FAIL: render log written (missing: /var/folders/t7/tl9jvgxs28392s28thsqr14r0000gn/T//loops-runner-test.13JwXG/state/runs/20260904T233131Z-pageloop-29197d/page-render.log)
cat: \033[34m/var/folders/t7/tl9jvgxs28392s28thsqr14r0000gn/T//loops-runner-test.kyLZar/state/runs/20260904T233135Z-pageloop-c94f5d\033[39;49m\033[0m/page-render.log: No such file or directory
FAIL: reason logged (expected to contain [redaction])
test_runner_pages: passed=21 failed=2
FAIL: /Users/llm/.peon/worktrees/loops-gc-health-watch/tests/test_runner_pages.sh
== /Users/llm/.peon/worktrees/loops-gc-health-watch/tests/test_skill_import_e2e.sh ==
== tests/test_skill_import_e2e.sh: import -> two runs -> finding_id stability ==

passed: 16, failed: 0
```

(The first `test_runner_pages` FAIL line also wrapped the path in ANSI color
in the live terminal; the `cat:` line keeps the escape sequences.)

Python unittest: 891 OK. Shell adapters/examples/runner/skill-import: all
pass. The only failure is `tests/test_runner_pages.sh` (2 fails, ANSI-colored
paths looking for `page-render.log`). That script is outside the allowlist
and failed the same way before this revision.

## Spec choices

- `ENV_FILES` order is repo-root then `growth-console/.env`. Per-key: skip
  a file that does not exist; do not overwrite a key already in the
  environment (so process env wins, then repo-root, then the GC file).
- `--check` unmet line names both paths:
  `POSTIZ_API_KEY not set (env or <repo>/.env or <gc>/.env)`.
- Header `probe-reads` copied verbatim from main's spec block.
- The in-test Postiz `http.server` now rejects a missing/wrong
  `Authorization` with 401 so `test_postiz_key_from_repo_root_env` cannot
  pass vacuously.

## Git commit blocked

`git add` failed with:

```
fatal: Unable to create '/Users/llm/projects/loops/.git/worktrees/loops-gc-health-watch/index.lock': Operation not permitted
```

Did not work around it. Foreman should commit:

```
modified:   probes/gc-health-read
modified:   probes/README.md
modified:   tests/test_gc_health_read.py
modified:   PEON_REPORT.md
```

## Open questions

- This branch's copy of
  `docs/superpowers/specs/2026-09-05-gc-health-watch-design.md` is still
  the pre-correction text (`HEAD` is behind `main` by `5b32220`). Not
  merged here — spec path is outside the allowlist.
