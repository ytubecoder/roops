# PEON_REPORT — ads-experiment-tick U9b

Branch `peon/ctl-u9b-probe`. Nothing was installed (`loopctl install`, `loopctl run`, `systemctl` were not run). Nothing contacted llm or any other host.

## What changed

New files only. No existing loop, probe, or harness file was edited.

- `probes/ads-experiment-tick` — one base64url JSON argument, job pinned by the allow file, `dry_run` must be literal `true`. Runs `growth-console/.venv/bin/python -m console.ads.ctl_cli tick --job … --request-id … --dry-run --host "$(hostname)"` with `cwd=growth-console` and `timeout=560`. Passes stdout JSON through and adds `tick_exit`. Exit 0 when the tick ran, 1 when the venv interpreter or the `console.ads.ctl_cli` module is missing, 2 on a rejected request.
- `probes/ads-experiment-tick.allow` — pins `collect`, `evaluate`, `reconcile`, `candidates`, `health`.
- `loops.d/ads-experiment-controller/` — watchdog, `interval:30m`, jobs `collect` then `evaluate` then `reconcile`. Quiet when every receipt is `ok: true` or `skipped: locked`. The engine runs only on exit 1 and must not call the probe or suggest an ad change.
- `loops.d/ads-experiment-candidates/` — same shape, `weekly:mon:10:00`, one job `candidates`. Notes say AI drafts are proposals only, shown on the GC experiment page.
- `tests/test_ads_experiment_tick_probe.py` — the mandated tests, loaded with `importlib.machinery.SourceFileLoader`.

Both loops are `perm_remote_mutation=allowlist` with `exec_allowlist="bin/probe ads-experiment-tick"`. The justification is ledger rows in GC's `ads.db` on llm (observations, decisions, simulated commands, job runs, candidate drafts). The probe forces dry-run. The GC CLI is specified to refuse anything else. No order, amount, budget, campaign, resource, or params key can be passed.

Decisions made while implementing:

- `owner=maguyva-marketing`, matching `drip-scheduler`.
- `retention_days=90`, matching `drip-scheduler`. The spec did not name this key.
- Finding id is `ads-experiment:<job>` (one finding per failing job). A transport failure and a receipt error share that id.
- The precheck builds `expires_at` at now + 9 minutes, inside the 10-minute cap.
- If `RUN_ID` is unset, the request id prefix is a UTC timestamp `YYYYMMDDTHHMMSSZ`.
- A receipt is quiet only when `ok` is `true` or `skipped` is `locked`. The probe does not invent `ok`.

## Why

U9b is the loops side of maguyva-marketing OpenSpec change `add-ads-experiment-controller` (design D3). The controller only observes and simulates. The operator rule of 2026-10-29 is that no new ads run until the controller is ready and proposing, and the operator has set the test budget. These loops are the schedule and the failure write-up. They are not installed.

## How verified

`python3` on this machine is Homebrew 3.15.1 and has no pytest (`externally-managed-environment`; pytest was not installed). `bin/loopctl` with `LOOPS_ROOT` unset reads `~/projects/loops`, not this worktree. The suite was run with `/usr/bin/python3` (3.9, pytest 8.4.2) and again with the growth-console venv Python 3.14.7. Validate was run with `LOOPS_ROOT` set to this worktree. `-p no:cacheprovider` only stops pytest from writing `.pytest_cache`; it does not change collection or assertions.

A later ruff pass caught `DTZ007` on the unset-`RUN_ID` assertion (`strptime` without a timezone). That timestamp is parsed and then marked UTC. Re-check after the fix:

```
$ ruff check tests/test_ads_experiment_tick_probe.py probes/ads-experiment-tick loops.d/ads-experiment-controller loops.d/ads-experiment-candidates
All checks passed!
RUFF_EXIT:0
$ /usr/bin/python3 -m pytest tests/test_ads_experiment_tick_probe.py -q -p no:cacheprovider
.................                                                        [100%]
17 passed in 2.40s
PYTEST_EXIT:0
```

Validate, from the earlier run with `LOOPS_ROOT` pointed at this worktree:

```
$ LOOPS_ROOT=$PWD bin/loopctl validate ads-experiment-controller
OK ads-experiment-controller
VALIDATE_CONTROLLER_EXIT:0
$ LOOPS_ROOT=$PWD bin/loopctl validate ads-experiment-candidates
OK ads-experiment-candidates
VALIDATE_CANDIDATES_EXIT:0
```

Same tests on the growth-console venv:

```
$ /Users/llm/projects/maguyva-marketing/growth-console/.venv/bin/python -m pytest tests/test_ads_experiment_tick_probe.py -q -p no:cacheprovider
.................                                                        [100%]
17 passed in 2.12s
PY314_EXIT:0
```

Literal spec command from this worktree, unmodified environment:

```
$ python3 -m pytest tests/test_ads_experiment_tick_probe.py -q && bin/loopctl validate ads-experiment-controller && bin/loopctl validate ads-experiment-candidates
/opt/homebrew/opt/python@3.15/bin/python3.15: No module named pytest
LITERAL_EXIT:1
```

`&&` stopped there. The validate half, run on its own with `LOOPS_ROOT` unset, looks at the main checkout:

```
$ bin/loopctl validate ads-experiment-controller
FAIL ads-experiment-controller
  - loop.conf not found at /Users/llm/projects/loops/loops.d/ads-experiment-controller/loop.conf
EXIT:1
$ bin/loopctl validate ads-experiment-candidates
FAIL ads-experiment-candidates
  - loop.conf not found at /Users/llm/projects/loops/loops.d/ads-experiment-candidates/loop.conf
EXIT:1
```

The probe header parses on the worktree's `probe_core`: name `ads-experiment-tick`, timeout 600, output `json`.

## Git

`git add` / `git commit` did not run. The sandbox refused the index lock, including a second attempt after the `DTZ007` fix:

```
fatal: Unable to create '/Users/llm/projects/loops/.git/worktrees/loops-ctl-u9b-probe/index.lock': Operation not permitted
```

No workaround. No new `.git` directory, no remote, no push. The implementation and this report are plain files in the worktree. `git status --short` shows them untracked. The foreman can commit them.

## Open questions

- U9a says a failed tick prints `ok: false` and exits 1. The success receipt examples do not include `ok: true`. This precheck treats a receipt as quiet only when `ok` is `true` or `skipped` is `locked`. If a successful `ctl_cli` receipt omits `ok`, the loop will escalate every firing until the CLI sets `ok: true`. The probe does not add `ok` itself; the spec says to pass stdout through and add `tick_exit`.
- `probes/README.md` still does not list `ads-experiment-tick`. That file is outside the allowlist, so it was left unchanged.
- `timeout_s=900` on the controller, while each probe call is allowed 600s and the inner CLI timeout is 560s. Three slow jobs in one firing can exceed the loop timeout. The spec sets both numbers; they were not changed.
- A foreman re-run of the literal verify line needs pytest on the `python3` that is first on `PATH`, and `LOOPS_ROOT` (or `bin/loopctl --root`) pointed at this worktree. Otherwise it fails the way the literal transcript above shows.
