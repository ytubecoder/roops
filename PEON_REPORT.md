# PEON_REPORT

Status: DONE

## What changed

- Added `tests/test_runner_pages.sh`, a hermetic shell test suite for Amendment 2 page rendering behavior.
- Updated `bin/run-loop.sh` retention pruning so `reports/<name>/latest.html` is kept.
- Added step 6.5 immediately after step 6 promotion variable extraction:
  - commits files from `OUT_DIR/loop-data.commit` into `state/loop-data/<name>/`;
  - runs executable `loops.d/<name>/render.sh` with the required renderer environment;
  - captures, caps, redacts, and writes `state/runs/<id>/page-render.log`;
  - gates generated pages through `bin/page_envelope.py check`;
  - promotes dated HTML first, then `latest.html`;
  - prints `page promoted: reports/<name>/<stamp>.html` on successful page promotion.

## Why

Task 4 implements the runner-side Amendment 2 plumbing that later tasks consume: loop-data persistence, renderer env contract, page promotion, stdout promotion signal, and retention behavior for `latest.html`.

## Deviations from the brief

- The brief guessed `FAKE_CONTRACT_INVALID`; the actual fake engine uses `FAKE_INVALID=1`, confirmed in `engines/fake.sh` and `tests/test_runner.sh`.
- The new page tests follow the existing runner test idiom: exported fake-engine gates plus `reset_fake_env` and explicit export/unset around fake-engine knobs.
- I added non-fatal guards around log writes, staging, `mv`, stdout printing, and the top-level step 6.5 calls so the new functions preserve the invariant that page-render failures never alter runner status or exit code under `set -e`.

## Verification

- Pre-implementation TDD check: `bash tests/test_runner_pages.sh` failed as expected with `passed=10 failed=8` due to missing `latest.html`, dated page, stdout promotion line, render log, and loop-data commit.
- Syntax/whitespace checks:
  - `bash -n bin/run-loop.sh`: passed.
  - `bash -n tests/test_runner_pages.sh`: passed.
  - `git diff --check`: passed.
- Targeted page suite: `bash tests/test_runner_pages.sh` -> `test_runner_pages: passed=18 failed=0`.
- Existing runner suite: `bash tests/test_runner.sh` -> `passed: 115, failed: 0`.
- Full suite: `bash tests/run-tests.sh` exited 0:
  - Python unittest: `Ran 321 tests ... OK`.
  - Adapter shell tests: `passed: 158, failed: 0`.
  - Example shell tests: `passed: 35, failed: 0`.
  - Runner shell tests: `passed: 115, failed: 0`.
  - Page shell tests: `test_runner_pages: passed=18 failed=0`.

## Self-review notes

- Step 6.5 only runs after step 6 has validated and promoted the JSON/Markdown report.
- The renderer is skipped with zero behavior change when `render.sh` is missing or non-executable.
- The promotion order is dated HTML first, then `latest.html`.
- Promotion gate failures write reasons to `page-render.log` and do not affect `runner_status`, `loop_status`, or runner exit code.

## Concerns

No known concerns.

## Open questions

None.
