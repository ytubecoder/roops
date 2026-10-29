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
CLI integration tests prove collection failure reaches the precheck,
empty-command reconciliation remains successful, and an uncertain simulated
pause produces a degraded reconciliation receipt.

Planner amendment A6 adds `test_real_cli_degraded_turns_loop_amber`. A GC
venv subprocess creates a `g-msg` run using resolved settings, records an
approved review, activates it, and creates one simulated pause. Recovery
leaves that command uncertain. The test checks the actual CLI receipt
(`ok: true`, `status: degraded`, and the matching command/source details),
precheck exit 1, `DEGRADED: reconcile:`, and absence of `reconcile: ok` or
any `PROBLEM:` line. Other jobs receive successful fixture receipts only
in this test, so collection failure cannot mask the degraded exit path.
Missing GC venv, CLI, or command module produces an explicit skip reason.

API adjustment from the requested seed recipe: this GC version rejects
`claim(lease_seconds=0)` with `lease_seconds must be positive`, and
recovery requeues lapsed claimed commands. It makes only executing
commands uncertain. The seed therefore claims a 120-second lease, calls
`execute` with a local test executor, and calls `recover(now=...)` one
second past the recorded lease deadline from that executor. Assertions
confirm exactly one recovery to uncertain and exactly one stored command.
This uses public APIs without sleeping, monkeypatching, direct SQL updates,
ad driver calls, or changes to the read-only GC checkout.

Verification ran with temporary files inside this worktree, an isolated
ads database and lock directory, empty Stripe and attribution directories,
dotenv disabled, and all `GOOGLE_ADS_*` variables removed from the real CLI
environment. Python bytecode writes were disabled. The fake probe runs the
real CLI locally with `--dry-run`; no loop was installed, run, or scheduled,
and no remote host was contacted. The maguyva-marketing checkout was not
modified. Validation used `LOOPS_PROBE_HOST=` to keep requirement checks local.

The first test run exposed a fake-probe mismatch: it printed receipts even
with `--out`, unlike the production probe. Both test probes now write the
receipt only to `--out`. A6 verification passed all 42 tests, with no skips.

Verification environment (from the repository root):

```sh
export PYTHONDONTWRITEBYTECODE=1 PYTEST_ADDOPTS="--basetemp=$PWD/state/fd2-a6-verification/pytest" TMPDIR="$PWD/state/fd2-a6-verification/tmp" LOOPS_PROBE_HOST=
```

Required verification command (exit 0):

```sh
$HOME/projects/maguyva-marketing/growth-console/.venv/bin/python -m pytest tests/test_ads_experiment_tick_probe.py tests/test_ads_experiment_precheck_real_cli.py -q -p no:cacheprovider && LOOPS_ROOT=$PWD bin/loopctl validate ads-experiment-controller && LOOPS_ROOT=$PWD bin/loopctl validate ads-experiment-candidates
```

Actual terminal output, verbatim:

```text
..........................................                               [100%]
42 passed in 10.86s
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
files, and this report changed across FD2. This A6 revision changes only
`tests/test_ads_experiment_precheck_real_cli.py` and `PEON_REPORT.md`;
production prechecks and prompts remain unchanged. The specification
itself required no edits.

Open questions: none.
