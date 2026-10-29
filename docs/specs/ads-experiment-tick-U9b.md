# U9b: the `ads-experiment-tick` probe and two controller loops (loops repo)

This unit belongs to maguyva-marketing's OpenSpec change `add-ads-experiment-controller` (issue #5). The canonical design is at `~/projects/maguyva-marketing/openspec/changes/add-ads-experiment-controller/design.md`; read D3. The GC-side CLI it calls is specified in `~/projects/maguyva-marketing/docs/superpowers/specs/ads-experiment-controller/units/U9a-ctl-cli.md`. U9a is built in parallel, so test against a fake CLI.

**No ads mutation.** The controller only observes and simulates. The probe forces dry-run. The operator rule (2026-10-29) is that no new ads run until the controller is ready and proposing, and the operator has set the test budget.

**Do not install anything.** Do not run `loopctl install`, `loopctl run` or `systemctl`. Do not edit other loops or harness internals (`bin/`, `docs/INTERFACES.md`).

## Files

Allowlist:
```
probes/ads-experiment-tick
probes/ads-experiment-tick.allow
loops.d/ads-experiment-controller/*
loops.d/ads-experiment-candidates/*
tests/test_ads_experiment_tick_probe.py
docs/specs/ads-experiment-tick-U9b.md
PEON_REPORT.md
```

Verify (from the loops repo root):
```
python3 -m pytest tests/test_ads_experiment_tick_probe.py -q && python3 bin/validate_contract.py loops.d/ads-experiment-controller && python3 bin/validate_contract.py loops.d/ads-experiment-candidates
```
If `bin/validate_contract.py` takes a different argument form, use the form `bin/loopctl validate` uses for one loop, and record the exact command in the report.

## 1. `probes/ads-experiment-tick`

Model it on `probes/drip-tick`: a Python 3 script with the probe header comments, `--check`, and a JSON pass-through. Header:
```
# probe: ads-experiment-tick
# probe-timeout-s: 600
# probe-writes: experiment-controller ledger rows (ads_ctl_* tables, experiment_assessment) in ~/.growth-console/ads.db on llm. NEVER places, changes or pauses ads: dry-run is forced.
# probe-output: json
# probe-reads: growth-console venv under MAGUYVA_REPO (default ~/projects/maguyva-marketing); Google Ads read-only GAQL, Stripe snapshot, Umami cache via the GC CLI
```

**Arguments.** Exactly one argument: a base64url (RFC 4648 §5, padding optional) encoding of a JSON object. It must fit `ARG_RE` in `bin/probe_core.py`, which allows base64url characters and `=`. The decoded object must have **exactly** these keys:
```json
{"v": 1, "request_id": "<loop run id>:<job>", "job": "collect|evaluate|reconcile|candidates|health",
 "issued_at": "<ISO8601 UTC>", "expires_at": "<ISO8601 UTC>", "dry_run": true}
```

**Rejections.** For each of these, print `{"ok": false, "error": "<reason>"}` and exit 2:
- not base64url, or not a JSON object;
- an extra or missing key, `v != 1`, or `dry_run` not literally `true`;
- a `job` not listed in `ads-experiment-tick.allow` (one job per line, `#` comments);
- `request_id` not matching `^[A-Za-z0-9_.:@=+-]{1,200}$`;
- timestamps that are not timezone-aware;
- `expires_at <= now`, `expires_at > issued_at + 10 min`, or `issued_at > now + 2 min`.

**Forbidden fields** (`order`, `amount`, `budget`, `campaign`, `resource`, `params`) are rejected by the exact-keys rule. Add a test for each.

**Run.** Exec `<MAGUYVA_REPO>/growth-console/.venv/bin/python -m console.ads.ctl_cli tick --job <job> --request-id <request_id> --dry-run --host "$(hostname)"` with `cwd=growth-console`, a `timeout=560` and captured output. Pass stdout JSON through, adding `"tick_exit"`. Unparseable output becomes `{"ok": false, "error": "unparseable tick output", "stdout_tail": ...}`. Exit 0 whenever the tick ran (the JSON carries ok/failed), and 1 when the venv or CLI is missing.

**`--check`** prints `ok ads-experiment-tick` or the problem.

`probes/ads-experiment-tick.allow`: a comment header explaining that it pins the job vocabulary, then `collect`, `evaluate`, `reconcile`, `candidates`, `health`.

## 2. `loops.d/ads-experiment-controller/`

Model it on `loops.d/drip-scheduler/`: `loop.conf`, `SPEC.md`, `precheck.sh`, `prompt.md`, `dashboard.json`.
- **loop.conf:** `type=watchdog`, `engine=codex`, `schedule=interval:30m`, `timeout_s=900`, `requires="probe:ads-experiment-tick"`, `perm_fs_write=report_only`, `perm_network=none`, `perm_local_exec=none`, `perm_remote_mutation=allowlist`, `exec_allowlist="bin/probe ads-experiment-tick"`.
  - `remote_mutation_justification`: it writes experiment-controller ledger rows in GC's ads.db on llm (observations, decisions, simulated commands, job runs). It never places, changes or pauses an ad: the probe forces dry-run, and the GC CLI refuses anything else. The design is maguyva-marketing `openspec/changes/add-ads-experiment-controller` D3.
  - `notes`: the kill switches. Run `loopctl set-schedule ads-experiment-controller manual`, or remove the probe from llm.
- **precheck.sh:**
  - For each job in order (`collect`, `evaluate`, `reconcile`), build the request with `python3` (`issued_at` now, `expires_at` now + 9 min, `request_id "$RUN_ID:<job>"` using the runner's run id env, or a UTC timestamp if that is unset) and base64url-encode it.
  - Call `"$LOOPS_ROOT/bin/probe" ads-experiment-tick <arg> --out "$INPUTS/<job>.json"`.
  - Exit 0 (a quiet heartbeat) when all three report `ok` or `skipped: locked`. Exit 1, with a short digest of the failures, when any transport fails or any receipt is not ok.
- **prompt.md:** the engine runs only on exit 1. It writes one finding naming the failing job and its error. It must not call the probe or suggest any ad change.
- **SPEC.md:** follow `docs/LOOP_AUTHORING.md`. State the no-ads-mutation rule and the operator's no-new-ads rule.

## 3. `loops.d/ads-experiment-candidates/`

The same structure. `schedule=weekly:mon:10:00`, with one job: `candidates`. Its notes say that AI drafts are proposals only, shown on the GC experiment page.

## Mandated tests (`tests/test_ads_experiment_tick_probe.py`, unittest or pytest)

Load the probe with `importlib.machinery.SourceFileLoader`, because it has no `.py` suffix. Point `MAGUYVA_REPO` at a tmp dir holding a fake `growth-console/.venv/bin/python`: an executable shell script that echoes its argv as JSON, `{"ok": true, "argv": [...]}`.
- `test_valid_request_runs_cli_with_dry_run`: argv contains `tick`, `--job collect`, `--request-id`, and `--dry-run`.
- `test_rejects_dry_run_false`
- `test_rejects_extra_keys` (parametrized over `order`, `amount`, `budget`, `campaign`, `resource`, `params`)
- `test_rejects_unknown_job`: one job removed from a tmp copy of the `.allow` file is then refused.
- `test_rejects_expired_and_long_lived`: expired; a lifetime over 10 minutes; issued in the future.
- `test_rejects_naive_timestamps`
- `test_rejects_non_base64_and_non_object`
- `test_arg_fits_probe_core_regex`: a valid encoded request matches `probe_core.ARG_RE`.
- `test_missing_venv_exit_1`
- `test_unparseable_cli_output_wrapped`
- `test_precheck_quiet_when_all_ok`: run `precheck.sh` with a fake `bin/probe` on PATH via `LOOPS_ROOT` pointing to a tmp tree, as `tests/test_gc_actions_probe.py` does, with all jobs returning ok → exit 0.
- `test_precheck_exit_1_on_failure`: one job returns `ok: false` → exit 1, and the output names the job.

## Definition of done

- Every mandated test exists under the exact name and passes.
- The verify command passes.
- Only allowlisted files changed.
- `PEON_REPORT.md` holds the verify output. Nothing was installed or run against llm.
