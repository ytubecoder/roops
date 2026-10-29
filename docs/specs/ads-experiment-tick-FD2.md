# FD2 (planner R7, loops side): the controller loop surfaces failed and degraded receipts

This belongs to maguyva-marketing issue #5. GC's `ctl_cli` (fix FD, merged in maguyva-marketing) now gives every receipt a `status` of `succeeded`, `degraded` or `failed`:
- `failed` comes with `ok: false` and a `failures` list.
- `degraded` comes with `ok: true` and a `degraded` list.
- A job is `failed` when any required source failed for any target.

**Planner finding.** `loops.d/ads-experiment-controller/precheck.sh` accepts any receipt whose `ok` is true, so a collect whose sources all failed showed `collect: ok`.

## Change

In `loops.d/ads-experiment-controller/precheck.sh` and `loops.d/ads-experiment-candidates/precheck.sh`, the receipt check becomes:
- `status == "failed"`, `ok` not literally true, or a transport failure → `PROBLEM: <job>: failed — <first 3 failures joined by "; ">`, and the loop exits 1.
- `status == "degraded"` → `DEGRADED: <job>: <first 3 degraded items>`. The loop **also exits 1**, so the engine writes a visible finding; dismiss it through the loops findings workflow if it is expected.
- `succeeded`, or `skipped: locked` → `<job>: ok`.
- A receipt with no `status` key, from an old CLI, is treated as failed, with the reason `"receipt has no status (CLI too old)"`.

Update `prompt.md` for both loops so the finding names failed versus degraded and the affected sources, and never suggests an ad change.

**Do not install, run or schedule** any loop, and do not contact llm.

## Files

Allowlist:
```
loops.d/ads-experiment-controller/precheck.sh
loops.d/ads-experiment-controller/prompt.md
loops.d/ads-experiment-candidates/precheck.sh
loops.d/ads-experiment-candidates/prompt.md
tests/test_ads_experiment_tick_probe.py
tests/test_ads_experiment_precheck_real_cli.py
docs/specs/ads-experiment-tick-FD2.md
PEON_REPORT.md
```

Verify (from the loops repo root):
```
$HOME/projects/maguyva-marketing/growth-console/.venv/bin/python -m pytest tests/test_ads_experiment_tick_probe.py tests/test_ads_experiment_precheck_real_cli.py -q -p no:cacheprovider && LOOPS_ROOT=$PWD bin/loopctl validate ads-experiment-controller && LOOPS_ROOT=$PWD bin/loopctl validate ads-experiment-candidates
```

## Mandated tests

In `tests/test_ads_experiment_tick_probe.py`, using a fake probe as before:
- `test_precheck_failed_status_exits_1`
- `test_precheck_degraded_exits_1_and_says_degraded`
- `test_precheck_missing_status_is_failed`

`tests/test_ads_experiment_precheck_real_cli.py` is the **real CLI-to-precheck** test:
- Skip it, with the reason, if `$MAGUYVA_REPO/growth-console/.venv/bin/python` or `console/ads/ctl_cli.py` is missing. `MAGUYVA_REPO` defaults to `~/projects/maguyva-marketing`.
- Build a tmp `LOOPS_ROOT` containing a fake `bin/probe`. It is a small Python script that decodes the request and runs `<venv python> -m console.ads.ctl_cli tick --job <job> --request-id <id> --dry-run` with `cwd=$MAGUYVA_REPO/growth-console`, and writes the stdout to `--out`.
- Set these in the environment:
  - `GROWTH_CONSOLE_ADS_DB` and `GROWTH_CONSOLE_ADS_CTL_DIR` to tmp paths, and `GC_SKIP_DOTENV=1`;
  - `GC_STRIPE_DIR` to an empty tmp dir, so Stripe fails;
  - and remove every `GOOGLE_ADS_*` variable, so Google configuration is missing.
- `test_real_cli_collect_failure_turns_loop_red`: run `precheck.sh` → exit 1, and the output contains `PROBLEM: collect: failed`.
- `test_real_cli_reconcile_empty_is_ok`: an empty DB reconcile → that line reads `reconcile: ok`.

## Definition of done

- The tests above pass, along with the existing probe tests and both `loopctl validate` runs.
- Only allowlisted files changed.
- `PEON_REPORT.md` holds the verify output.
