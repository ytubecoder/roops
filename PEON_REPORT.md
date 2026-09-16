# PEON_REPORT — vecomap-unmapped

## What changed

Scaffolded `loops.d/vecomap-unmapped/` with `loopctl new --type watchdog --engine codex --owner vecomap` (via `--root` on this worktree) and filled every `[FILL:` marker.

| file | role |
|---|---|
| `loop.conf` | `type=watchdog`, `engine=codex`, `schedule=times:07:40,19:40`, `timeout_s=2400`, `owner=vecomap`, `tags=vecomap,tracer`, the 11-item `requires=` list, all `perm_*` at defaults (`report_only/none/none/none`). `notes=` records that the only remote mutation is the precheck-side `git push`. |
| `SPEC.md` | All twelve intake sections, truthful: watchdog + human-in-the-loop via PR merge; kagami permission precedent; finding ids; stickiness; page class `snapshot`. |
| `precheck.sh` | Thin bash wrapper. Exit 0 = silent-green; non-zero = escalate. `exec`s `run.py`. |
| `run.py` | Stdlib orchestrator: refresh `~/projects/vecomap` (`git fetch` + `checkout -B main origin/main`, never changes remotes), list unmapped keys, choose work (cap 25), refresh OSM if `cebu_named_ways.json` is older than 60 days (temp dir, keep old files on failure), resolve/trace via the tracer venv, commit high/medium candidates as `vecomap-tracer <vecomap-tracer@users.noreply.github.com>`, push unless `VECOMAP_DRY_RUN=1`, detect KML-superseded candidates (no Node, no delete), age provisionals, write `$OUT_DIR/summary.json` + engine-facing stdout. State: `$LOOPS_ROOT/state/vecomap-unmapped/attempted.json`. |
| `prompt.md` | Engine is floor-only: copy metrics, emit the five finding id families, 5-line `report_markdown`. `status_reason` is the branch or `no_branch`. |
| `render.sh` / `render_page.py` | Snapshot page: stat strip, per-key table, downscaled overlay as `data:` JPEG (tracer venv + cv2, ≤ 600 px, quality 60), tinyurl + vecomap hash links. Drops images after 40 keys. |
| `dashboard.json` | Panels for `traced`, `confident`, `pushed`, `low`, `failed`, `aging`. |

Watchdog gating matches INTERFACES §4.1 exactly: exit 0 when nothing was traced and nothing is superseded/aging; exit 1 otherwise. No invented runner exit codes.

## Why

VECO stores area codes it cannot draw. This loop, twice a day on firstparty as `svc`, traces those images off-platform (vecomap SPEC §28 / R-669..R-677) and pushes a `tracer/<YYYYMMDD-HHMM>` branch. vecomap's `tracer-pr` workflow regenerates the layer and opens the PR. Merge is the human gate. The engine never holds the pen (kagami shape).

## How it was verified

No network to the real unmapped API or GitHub (sandbox). Git of `~/projects/vecomap` was not used as the live checkout; hermetic tests used throwaway repos.

### Official foreman command

`LOOPS_ROOT` was this worktree (`loopctl`'s default root is `~/projects/loops`).

```
$ bin/loopctl validate vecomap-unmapped && bash -n loops.d/vecomap-unmapped/precheck.sh && bash -n loops.d/vecomap-unmapped/render.sh && grep -c "\[FILL:" loops.d/vecomap-unmapped/SPEC.md | grep -qx 0
OK vecomap-unmapped
  note: requirement unmet on this host: os:linux — host is darwin
  note: requirement unmet on this host: env:VECOMAP_ADMIN_SECRET — unset or empty
  note: requirement unmet on this host: env:VECOMAP_API_BASE — unset or empty
  note: requirement unmet on this host: file:~/.ssh/vecomap-deploy — missing
exit=0
```

Those four notices are expected on this macOS laptop, not on the guest `firstparty` where the loop is meant to run (`os:linux`, `.env` secrets, `~/.ssh/vecomap-deploy`). `bin:git`/`curl`/`ssh` and the tracer files under `~/projects/vecomap` / `~/.cache/vecomap-tracer` were met here.

### loops.d validation test

```
$ python3 -m unittest tests.test_loopctl.TestValidateAllRealFleet -v
test_validate_all_over_real_loops_d_exits_0 (tests.test_loopctl.TestValidateAllRealFleet.test_validate_all_over_real_loops_d_exits_0) ... ok

----------------------------------------------------------------------
Ran 1 test in 6.083s

OK
```

### Hermetic precheck / render (no real API, no real vecomap mutation)

Selection helpers (`choose_work`, KML placemark names, provisional aging) asserted in-process: skip provisional and existing candidates; retry stale / version-mismatch / unpushed high-medium; cap at `VECOMAP_MAX_KEYS`.

Fake git remotes + fake tracer + local HTTP unmapped API, `VECOMAP_DRY_RUN=1`:

```
EXIT 1
STDOUT
vecomap-unmapped: traced=2 confident=1 low=1 failed=0 superseded=1 aging=0 branch=tracer/20260916-0910
...
metrics: {"traced": 2, "confident": 1, "pushed": 0, "low": 1, "failed": 0, "aging": 0, "superseded": 1}
per_key newhigh1: ok confidence=high branch=tracer/20260916-0910 ok
per_key lowzzzz1: ok confidence=low branch=- ok
superseded_key: handdrawn
git:
osm cache age 0.0d (fresh)
commit ca55197ab0e4801f530664c66518b63b65dcb42a
Author: vecomap-tracer <vecomap-tracer@users.noreply.github.com>
...
    data: tracer candidates for 1 key(s)

    newhigh1 (high)
...
dry_run: skipped git push
```

- Missing env fatals **before** `git fetch` (sentinel file in a dummy `VECOMAP_ROOT` unchanged).
- Silent-green (candidate already on main, no superseded overlap, new provisional not yet 30 days): exit 0, first line `vecomap-unmapped: nothing to report`.
- After dry-run commit, the fake checkout was back on `main`; local `tracer/*` branch remained (expected).
- Render of a 800×400 overlay through the tracer venv: `page_envelope.py check` exit 0, no external subresources, JPEG data URI present, tinyurl + `vecomap.pages.dev/#area=` links present, 16379 bytes.

### Full `tests/run-tests.sh`

Python: 899 tests, **1 failure caused by this loop**:

```
FAIL: test_cutover_runbook_exists_and_names_every_loop (test_loopctl.TestSnapshotRestore.test_cutover_runbook_exists_and_names_every_loop)
AssertionError: 'vecomap-unmapped' not found in 'Step 3 — Arm the guest
...
for n in hello-watchdog hello-loop loop-sensei ads-google ads-intl ads-reddit ads-x ads-program ads-delivery-watch ads-hard-cut gc-health-watch gc-actions kagi-ban tailnet-zones kagami; do
...
vecomap-unmapped missing from step 3
```

That runbook is `workflows/firstparty-cutover.txt`, **outside the allowed paths**. Not edited.

Shell:

```
tests/test_adapters.sh          passed: 158, failed: 0
tests/test_examples.sh          passed: 35, failed: 0
tests/test_runner.sh            passed: 154, failed: 0
tests/test_runner_pages.sh      passed: 21, failed: 2
tests/test_skill_import_e2e.sh  passed: 16, failed: 0
```

`test_runner_pages.sh` failures used ANSI-colored temp paths (`\033[34m/var/folders/.../pageloop-...\033[39;49m/page-render.log`) for a throwaway `pageloop`, not this loop. Not investigated as a product bug in vecomap-unmapped.

## Open questions

1. **Cutover runbook (blocks `tests/run-tests.sh`).** Foreman should add `vecomap-unmapped` to the Step 3 install `for n in ...` list in `workflows/firstparty-cutover.txt`, and to the Prerequisites `loopctl requirements` name list. Allowlist forbade that edit.

2. **Precheck wall clock is 300s, not 2400s.** `timeout_s=2400` is the engine budget. `bin/run-loop.sh` still caps precheck at `PRECHECK_MAX_TIMEOUT_S=300`. A 25-key OCR run may not finish in one firing; leftover keys retry next run via `attempted.json`. Raising the cap is a harness change (not in allowlist).

3. **`AREA_TRACER_OCR=rapidocr`.** Set on every `trace` as specified. The vecomap checkout on this laptop (`area_tracer` 0.2.2) only honors `AREA_TRACER_OCR_BIN` / the Vision helper. firstparty's tracer is assumed to understand `rapidocr` (venv has `rapidocr_onnxruntime`). If the guest tracer is still 0.2.2, traces will fail until the tracer grows that env or `AREA_TRACER_OCR_BIN` points at a RapidOCR wrapper.

4. **Branch timestamp is UTC** (`now_utc().strftime("%Y%m%d-%H%M")`), not firstparty local time. A 07:40 Asia/Manila firing is `tracer/YYYYMMDD-2340` the day before in UTC. Say if it should be local.

5. **Dry-run vs `attempted.json`.** High/medium keys are recorded without `branch` unless `git push` succeeded, so the next non-dry-run retraces and pushes. Low/failed keys are skipped for 30 days even after a dry-run.

6. **Watchdog stickiness.** Any escalation stores `loop_status=alert` regardless of info/warn findings (INTERFACES §4.3). New estimates will light the dashboard red until the next silent-green firing after the PR is merged (or the attempt is recorded and there is nothing else). That is the watchdog contract, not a warn-level dashboard.

7. **Supervised run** (foreman, on firstparty): `.env` must export `VECOMAP_ADMIN_SECRET` and `VECOMAP_API_BASE`. First run with `VECOMAP_DRY_RUN=1`. Do not change the vecomap remote (`github-vecomap`).

## Git commit blocked by the sandbox

`git add` / `git commit` were not performed. The sandbox refused the worktree index lock:

```
fatal: Unable to create '/Users/llm/projects/loops/.git/worktrees/loops-loops-vecomap-unmapped/index.lock': Operation not permitted
```

Tried twice. Did not work around it (no new `.git`, no remotes, no push). Untracked files left in the worktree for the foreman to commit:

- `loops.d/vecomap-unmapped/` (loop.conf, SPEC.md, prompt.md, precheck.sh, run.py, render.sh, render_page.py, dashboard.json)
- `PEON_REPORT.md`

Suggested commits (foreman):

```
feat(vecomap-unmapped): watchdog that traces VECO unmapped areas and pushes tracer branches
docs: PEON_REPORT for vecomap-unmapped
```

Do not `git push` from this peon (task rule).
