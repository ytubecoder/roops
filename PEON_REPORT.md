# FD2 implementation report

Implemented `docs/specs/ads-experiment-tick-FD2.md` on `peon/fx-fd2`.

Both ads experiment prechecks now fail on failed receipts, non-literal-true
`ok`, transport errors, and receipts missing `status`. Degraded receipts
emit `DEGRADED:` and also exit 1. Succeeded and valid locked receipts emit
`<job>: ok`. Failure and degradation summaries retain the affected target
and source, and join at most three items with semicolons. Old CLI receipts
report `receipt has no status (CLI too old)`.

Both prompts distinguish failed from degraded findings, name affected
sources, retain stable finding IDs, and prohibit ad changes. Expected
degradation can be dismissed through the existing findings workflow.

This prevents `ok: true` from hiding degraded collection results. Fake-probe
coverage exercises both prechecks, including literal boolean handling,
locked receipts, transport failures, and three-item summaries. The new real
CLI integration tests prove collection failure reaches the precheck and
empty-command reconciliation remains successful.

Verification ran with temporary files inside this worktree, an isolated
ads database and lock directory, empty Stripe and attribution directories,
dotenv disabled, and all `GOOGLE_ADS_*` variables removed from the real CLI
environment. Python bytecode writes were disabled. The fake probe runs the
real CLI locally with `--dry-run`; no loop was installed, run, or scheduled,
and no remote host was contacted. The maguyva-marketing checkout was not
modified. Validation used `LOOPS_PROBE_HOST=` to keep requirement checks local.

The first test run exposed a fake-probe mismatch: it printed receipts even
with `--out`, unlike the production probe. Both test probes now write the
receipt only to `--out`. Final verification passed all 41 tests, with no skips.

Verification environment (from the repository root):

```sh
export PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS="--basetemp=$PWD/state/fd2-verification/pytest" TMPDIR="$PWD/state/fd2-verification/tmp" LOOPS_PROBE_HOST=
```

Required verification command (exit 0):

```sh
$HOME/projects/maguyva-marketing/growth-console/.venv/bin/python -m pytest tests/test_ads_experiment_tick_probe.py tests/test_ads_experiment_precheck_real_cli.py -q -p no:cacheprovider && LOOPS_ROOT=$PWD bin/loopctl validate ads-experiment-controller && LOOPS_ROOT=$PWD bin/loopctl validate ads-experiment-candidates
```

Actual terminal output, verbatim:

```text
.........................................                                [100%]
41 passed in 10.23s
OK ads-experiment-controller
OK ads-experiment-candidates
```

Additional verification command (exit 0; shell syntax and diff checks are silent):

```sh
ruff check --no-cache tests/test_ads_experiment_tick_probe.py tests/test_ads_experiment_precheck_real_cli.py && ruff format --no-cache --check tests/test_ads_experiment_tick_probe.py tests/test_ads_experiment_precheck_real_cli.py && bash -n loops.d/ads-experiment-controller/precheck.sh loops.d/ads-experiment-candidates/precheck.sh && git diff --check
```

Actual terminal output, verbatim:

```text
All checks passed!
2 files already formatted
```

Only the four allowlisted precheck/prompt files, the two allowlisted test
files, and this report changed. The specification itself required no edits.

Open questions: none.
