# PEON_REPORT

WP6 forums-lane on branch `peon/forums-wp6-20261015`. The loop is not installed. No `loopctl install` and no live `loopctl run`.

## What changed

New agent loop `loops.d/forums-lane`. Once a day at 07:10 local (`LOOPS_EXPECT_TZ=Asia/Manila`) the precheck collects forum candidates. The Codex engine assesses every candidate, drafts at most five top-tier replies, and runs emit, which writes `$OUT_DIR/loop-data.commit/batch.json`. The harness later promotes that file to `state/loop-data/forums-lane/batch.json`. Nothing posts, votes, messages, submits, or drives a browser.

Operator recipe: `docs/forums-lane-deploy.md`. The coordinator copies it into maguyva-marketing `workflows/`.

## Why

Design §3 and §14 WP6: a script-then-agent loop. The unsandboxed precheck fetches. The engine has `perm_network=none` and `perm_remote_mutation=none`. Empty collector stdout plus exit 0 is the harness skip (`skipped-precheck`). `FORUMS_LANE_ENABLED=0` uses that same skip, before `MAGUYVA_REPO` or `OUT_DIR` are required. The one finding id is `forums-lane:collect-failed`, and only when `source_stats.json` field `finding` is that string.

## Files touched

- `loops.d/forums-lane/loop.conf` (new)
- `loops.d/forums-lane/precheck.sh` (new, mode 755)
- `loops.d/forums-lane/prompt.md` (new)
- `loops.d/forums-lane/SPEC.md` (new, 12 intake headings, no `[FILL:]` markers)
- `loops.d/forums-lane/dashboard.json` (new)
- `tests/test_forums_lane_precheck.py` (new)
- `docs/forums-lane-deploy.md` (new)
- `PEON_REPORT.md` (this file)

## Deviations from the brief (with reasons)

1. `credential_env` is commented out, names only. `bin/loopctl` validate rule 8 hard-fails any non-empty `credential_env` (`docs/INTERFACES.md` §5; `tests/test_loopctl.py` `test_rule8_credential_env_nonempty_hard_fails`). The parser also splits lists on commas, so the brief's space-separated string would have been one list item. The five names sit in comments: `REDDITGROW_API_KEY`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PASSWORD`. The precheck inherits them from the loops `.env`. The runner strips that file from the engine. `bin/loopctl` is outside the allowlist, so rule 8 was not changed. `loopctl validate forums-lane` exits 0 with the key absent (`credential_env` parses falsy).

2. When `LOOP_DATA_DIR` is unset, the precheck exports it as `${LOOPS_ROOT:-$HOME/projects/loops}/state/loop-data` (the parent). `console.forums.collect` appends `forums-lane/rules-cache`. The runner does not export `LOOP_DATA_DIR` into the precheck; it sets the per-loop directory only at promotion. The parent path puts the 7-day cache beside the promoted `batch.json`. An already-set `LOOP_DATA_DIR` is left alone.

3. The precheck writes `$OUT_DIR/inputs/checkout.txt` with `MAGUYVA_REPO` before collect. A file write is not stdout, so an empty collect still skips. The runner strips `.env` keys from the engine, so the prompt reads the checkout path from that file.

4. The collect invocation is two commands, `cd` and then python. Under `set -e`, a failed `cd` inside `cd && python` is not the final command, so the shell would exit 0 and the harness would record a missing checkout as a skip. A failed `cd` is now precheck-failed.

5. Validate ran against this worktree: `LOOPS_ROOT="$PWD" bin/loopctl validate forums-lane`. `_default_root()` is `LOOPS_ROOT` or `~/projects/loops`. The brief's `cd ~/projects/loops` would validate the other checkout. The test calls `bin/loopctl validate forums-lane --root <worktree>`.

6. Two tests beyond the three named ones: `test_precheck_forwards_collector_stdout` and `test_precheck_fails_when_collector_fails`. The second locks the `set -e` behaviour above.

7. Deploy recipe path is `docs/forums-lane-deploy.md` in this repo. The coordinator addendum overrides `workflows/forums-lane-deploy.txt` in maguyva-marketing.

8. `owner=maguyva-marketing`. The brief does not name an owner. The ads loops set one, and a missing owner is only a validate notice. `notes` is set because `perm_fs_write=workdir` requires it (rule 5).

9. Lint used `/Users/llm/.grok/bin/ruff` 0.16.10. The brief's `uvx ruff` invocation is the growth-console one, and `uvx` cannot create its cache in this sandbox. The touched Python file was the ruff target. Shell, markdown, and `loop.conf` are not ruff targets.

10. The full suite is `bash tests/run-tests.sh` in this loops worktree. The brief's growth-console `pytest tests -q` applies to packets that edit `growth-console/`. This packet does not.

11. `exec_allowlist` keeps `$HOME` literal. The parser expands `$HOME` only in `workdir`.

## How verified

Commands were run in this worktree. The loop was not installed and was not run for real.

- `LOOPS_ROOT="$PWD" bin/loopctl validate forums-lane && LOOPS_ROOT="$PWD" python3 -m pytest tests/test_forums_lane_precheck.py tests/test_loopconf.py -q` — exit 0. Validate prints `OK forums-lane` plus two unmet-requirement notes (`env:GC_BASE`, `env:MAGUYVA_REPO`). Those notes do not fail validate. This host is not firstparty.
- `bash tests/run-tests.sh` — exit 1. See the pasted output. The Python failure is `test_cutover_runbook_exists_and_names_every_loop` on `competitor-watch`, which is already absent from `workflows/firstparty-cutover.txt` at HEAD. `forums-lane` is also absent; the assertion stops at the first missing name. `tests/test_runner_pages.sh` failed 2 of 23. That script and the runner were not edited. Shell suites `test_adapters.sh`, `test_examples.sh`, `test_runner.sh`, and `test_skill_import_e2e.sh` passed.
- `/Users/llm/.grok/bin/ruff format tests/test_forums_lane_precheck.py && /Users/llm/.grok/bin/ruff check tests/test_forums_lane_precheck.py` — exit 0, file unchanged.

## Mandated tests: pasted output

```
$ LOOPS_ROOT="$PWD" bin/loopctl validate forums-lane && LOOPS_ROOT="$PWD" python3 -m pytest tests/test_forums_lane_precheck.py tests/test_loopconf.py -q
OK forums-lane
  note: requirement unmet on this host: env:GC_BASE — unset or empty
  note: requirement unmet on this host: env:MAGUYVA_REPO — unset or empty
....................................................................     [100%]
68 passed in 0.85s
```

Exit code 0.

## Full suite: pasted output

```
$ bash tests/run-tests.sh
```

Exit code 1. Body of the run:

```
== python3 -m unittest discover -s tests -p 'test_*.py' ==
warning: no continuity.json at /var/folders/t7/tl9jvgxs28392s28thsqr14r0000gn/T/tmp44vlpbjk/continuity.json — ID continuity not verified this run
OK wrote + validated action set: /var/folders/t7/tl9jvgxs28392s28thsqr14r0000gn/T/tmp44vlpbjk/action-set (2 actions; continuity NOT verified). Include action_set.written: 1 in your contract metrics.
..................................................................................................................................................................................................................................................<frozen importlib._bootstrap_external>:602: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10aad5990>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen importlib._bootstrap_external>:602: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10aad58a0>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen importlib._bootstrap_external>:602: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10aad57b0>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen importlib._bootstrap_external>:602: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10aad53f0>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen importlib._bootstrap_external>:602: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10aad5c60>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
..................................................................................<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bc371f0>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bc35c60>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10aad5990>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfca90>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfc8b0>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10aad6200>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10aad5f30>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfcf40>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfd030>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfd120>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfd5d0>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfd6c0>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfd7b0>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfd8a0>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfd990>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfda80>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfdb70>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfdc60>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10aad6110>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfdd50>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfdf30>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfe020>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfe110>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfe200>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfe2f0>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfe3e0>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
<frozen codecs>:312: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10bcfe4d0>
ResourceWarning: Enable tracemalloc to get the object allocation traceback
..................................................................................................................................................................................................................................................................................................................................F..............................................................................................................................................................................................................................................................................................
======================================================================
FAIL: test_cutover_runbook_exists_and_names_every_loop (test_loopctl.TestSnapshotRestore.test_cutover_runbook_exists_and_names_every_loop)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/Users/llm/.peon/worktrees/loops-forums-wp6-20261015/tests/test_loopctl.py", line 4437, in test_cutover_runbook_exists_and_names_every_loop
    self.assertIn(name, step3, msg=f"{name} missing from step 3")
    ~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 'competitor-watch' not found in 'Step 3 — Arm the guest\n\nInstall in this order. Each `install` runs a real self-verify engine run (~10 sequential runs). Kagami last: its verify run may open or update the mirror PR, which is its job. Use a 600s poll so a codex verify cannot time out the install and orphan the engine.\n\n- Run: ssh firstparty-svc \'cd ~/projects/loops && for n in hello-watchdog hello-loop loop-sensei ads-google ads-intl ads-reddit ads-x ads-program ads-delivery-watch ads-hard-cut gc-health-watch gc-actions kagi-ban tailnet-zones kagami; do LOOPCTL_INSTALL_POLL_TIMEOUT_S=600 ./bin/loopctl install "$n" || exit 1; done\'\n- Expected outcome: each name prints a successful install; guest `./bin/loopctl list` shows them installed.\n- If fails: the named loop\'s install stderr. Do not skip ahead; a failed install boots the unit back out. Fix requirements / probe / credential, then re-run from the failed name.\n\n- Run: ssh firstparty-svc \'cd ~/projects/loops && ./bin/loopctl console install\'\n- Expected outcome: `loops-console.service` enabled and `/api/state` 200 on the guest loopback.\n- If fails: linger / tz / XDG host-check messages; `loginctl enable-linger`, set `XDG_RUNTIME_DIR=/run/user/$(id -u)`, then retry.\n\n### ' : competitor-watch missing from step 3

----------------------------------------------------------------------
Ran 933 tests in 155.102s

FAILED (failures=1)
== /Users/llm/.peon/worktrees/loops-forums-wp6-20261015/tests/test_adapters.sh ==
== codex adapter ==
== claude adapter ==
== cross-cutting: forbidden flags / no adapter-side timeout ==

passed: 158, failed: 0
== /Users/llm/.peon/worktrees/loops-forums-wp6-20261015/tests/test_examples.sh ==
== tests/test_examples.sh: examples/hello-loop e2e (fake engine) ==
== tests/test_examples.sh: examples/hello-watchdog e2e (fake engine) ==

passed: 35, failed: 0
== /Users/llm/.peon/worktrees/loops-forums-wp6-20261015/tests/test_runner.sh ==
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

passed: 161, failed: 0
== /Users/llm/.peon/worktrees/loops-forums-wp6-20261015/tests/test_runner_pages.sh ==
FAIL: render log written (missing: [34m/var/folders/t7/tl9jvgxs28392s28thsqr14r0000gn/T//loops-runner-test.mwjE1o/state/runs/20261014T234611Z-pageloop-739a60[39;49m[0m/page-render.log)
cat: \033[34m/var/folders/t7/tl9jvgxs28392s28thsqr14r0000gn/T//loops-runner-test.V5Clrr/state/runs/20261014T234615Z-pageloop-733692\033[39;49m\033[0m/page-render.log: No such file or directory
FAIL: reason logged (expected to contain [redaction])
test_runner_pages: passed=21 failed=2
FAIL: /Users/llm/.peon/worktrees/loops-forums-wp6-20261015/tests/test_runner_pages.sh
== /Users/llm/.peon/worktrees/loops-forums-wp6-20261015/tests/test_skill_import_e2e.sh ==
== tests/test_skill_import_e2e.sh: import -> two runs -> finding_id stability ==

passed: 16, failed: 0
```

A second run of only the pages script, with color variables cleared, still failed the same two asserts:

```
$ CLICOLOR=0 CLICOLOR_FORCE=0 bash tests/test_runner_pages.sh
FAIL: render log written (missing: /var/folders/t7/tl9jvgxs28392s28thsqr14r0000gn/T//loops-runner-test.Fe9yFc/state/runs/20261014T234721Z-pageloop-6fa0d0/page-render.log)
cat: \033[34m/var/folders/t7/tl9jvgxs28392s28thsqr14r0000gn/T//loops-runner-test.eXhDls/state/runs/20261014T234724Z-pageloop-cff223\033[39;49m\033[0m/page-render.log: No such file or directory
FAIL: reason logged (expected to contain [redaction])
test_runner_pages: passed=21 failed=2
```

Exit code 1. `CLICOLOR_FORCE` was 1 in the full-suite environment, which is why that run's "missing" path carried ANSI. Clearing it removed the color from the first failure message. The second `cat` path was still colored. This packet does not touch `tests/test_runner_pages.sh` or `bin/run-loop.sh`.

## Ruff: pasted output

```
$ /Users/llm/.grok/bin/ruff format tests/test_forums_lane_precheck.py && /Users/llm/.grok/bin/ruff check tests/test_forums_lane_precheck.py
1 file left unchanged
All checks passed!
```

Exit code 0. `ruff` 0.16.10 at `/Users/llm/.grok/bin/ruff`.

## Open questions

- The supervised `loopctl run forums-lane` on firstparty has to show that Codex, sandboxed to the loops root, can execute `$HOME/.venvs/forums/bin/python` and read `MAGUYVA_REPO` (both outside that root). If it cannot, widen the workdir or copy the module before any install. This packet left `perm_fs_write=workdir`, matching the spec and the ads loops.
- Emit will re-check assessments against `inputs/config.json`. That file is collector config, not the theme bundle in `assess.md`. See the coordinator notes. A batch can be written with every `top` downgraded and every draft ignored.
- `workflows/firstparty-cutover.txt` Step 3 does not name `forums-lane`. The file is outside the allowlist.

## Notes for the coordinator

Copy `docs/forums-lane-deploy.md` to maguyva-marketing `workflows/forums-lane-deploy.txt`. Do not install until a supervised `loopctl run forums-lane` and design §13 smoke. The recipe says the same.

Do not uncomment `credential_env` until validate rule 8 allows it. Names belong in the loops `.env` on firstparty: `MAGUYVA_REPO`, `FORUMS_LANE_ENABLED=1`, `REDDITGROW_API_KEY`, `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USERNAME`, `REDDIT_PASSWORD`. `GC_BASE` is already set there. `reddit-api` ships disabled until the four Reddit keys exist.

`growth-console/console/forums/emit.py` `coerce_records` (about lines 190–205) unwraps a dict only when the looked-up value is a list, or a single object that has `key` or `source`. Collector `source_stats.json` stores `sources` as a dict of name to `{listed, title_pass, fetched, calls, ok, reason}` (`collect.py` `_empty_stats`). `_source_stats` calls `coerce_records(payload, "source_stats", "sources", "rows", "items")`, so the batch `source_stats` list can come out empty. The engine report still reads `source_stats.json` itself. This packet did not change maguyva-marketing.

`__main__.py` `_emit_main` passes that same `inputs/config.json` as both `themes` and `ruleset` when the file exists (`emit.py` `emit_batch`: `theme_source = DEFAULT_RULESET if themes is None else themes`). `config.py` `to_public_dict` (about lines 151–177) is schema, limits, gate, and forums. It has no `themes` / `claims` / `verified` keys, so `assess.py` `index_themes` treats the other top-level keys as theme names. `validate_assessment` downgrades `relevance=top` when the theme is missing from the verified set (about lines 89–93). Drafts for a non-top row are ignored (`emit.py` `_take_draft`). The prompt tells the engine that themes in `assess.md` win when config does not list them. Emit still re-validates. Likely result: tops downgraded, drafts dropped, batch still written.

`tests/test_loopctl.py` `test_cutover_runbook_exists_and_names_every_loop` requires every non-manual loop directory name to appear in Step 3 of `workflows/firstparty-cutover.txt`. On this branch the assertion fails first on `competitor-watch`. Also absent, schedule not `manual`: `dapodcast-henshu`, `dapodcast-mamori`, `dev-asks-watch`, `drip-scheduler`, `forums-lane`, `gtm-research`, `gtm-synthesis`, `research-replies`, `vecomap-unmapped`. Adding the literal `forums-lane` in Step 3 is required for that test once the earlier names are present. Keep the real install behind the supervised run and §13 smoke in `docs/forums-lane-deploy.md`. This packet did not edit the runbook.

Codex `workspace-write` applies to the loops root. Confirm on the supervised run that emit's python, which lives in `~/.venvs/forums`, can import `console.forums` from the sparse checkout.

## Git

Commit did not run. The sandbox refused the worktree index lock. Both `git add` and `git commit` returned 128. No commit was created. No push. Branch is still `peon/forums-wp6-20261015` at `bee1da7`. The files above are untracked in the worktree. No new `.git` directory, no remote, no retry through another git dir.

```
$ git add loops.d/forums-lane docs/forums-lane-deploy.md tests/test_forums_lane_precheck.py && git commit -F /tmp/forums-lane-commit.txt
fatal: Unable to create '/Users/llm/projects/loops/.git/worktrees/loops-forums-wp6-20261015/index.lock': Operation not permitted
fatal: Unable to create '/Users/llm/projects/loops/.git/worktrees/loops-forums-wp6-20261015/index.lock': Operation not permitted
COMMIT_EXIT:128
```

`git status --short` after that:

```
?? docs/forums-lane-deploy.md
?? loops.d/forums-lane/
?? tests/test_forums_lane_precheck.py
```

`PEON_REPORT.md` is a further untracked file. The foreman can commit these.
