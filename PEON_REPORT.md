# PEON Report

## What Changed

- Added `POST /api/loops/<name>/run` in `bin/console.py` for `[A-Za-z0-9_-]+` loop names.
- Added `GET /api/run/status` returning the bare 7-field run status dict.
- Added a console-wide non-blocking `threading.Lock` job slot and thread-safe status snapshot.
- Started `loopctl run <name>` through the existing `_loopctl(root, ["run", name])` helper in a daemon worker thread.
- Recorded terminal worker state with UTC timestamps, verbatim exit code, `ok` as `exit_code == 0`, and a 4096-character stderr tail on failure.
- Ran dashboard regeneration after the run subprocess exits, best-effort, with stderr warnings that do not change the recorded run outcome.

## Why

B-13 requires a manual console run trigger that never blocks the HTTP request path, allows any loop present in `loops.d` regardless of install/enabled/schedule state, exposes a pollable status snapshot, and refuses overlapping console-triggered runs with a 409 payload naming the in-flight job.

## Verification

Command:

```sh
PYTHONPATH=tests python3 -m unittest tests.test_console
```

Actual output:

```text
......................................................................
----------------------------------------------------------------------
Ran 70 tests in 3.678s

OK
```

## Open Questions

None.
