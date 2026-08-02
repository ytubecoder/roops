# PEON_REPORT

## What Changed

- Added the B-13 run-now button to every loop row's existing hidden console-controls cell in `dashboard/generate.py`.
- Added console hydration logic that fetches `api/run/status` after `api/state` succeeds and reflects an in-flight run by disabling all `.con-run` buttons and marking the active loop's button.
- Added delegated `.con-run` click handling that prevents the surrounding `<summary>` accordion toggle, posts through the existing normalized `post()` helper, surfaces failures via `alert(res.j.error)`, and polls `api/run/status` every 3 seconds before reloading once the run finishes.
- Added `.con-run` and `.con-run.is-running` CSS inside the existing console-controls stylesheet block.

## Why

B-13 requires a per-loop dashboard control to manually trigger one supervised run for any loop, including loops that are not installed. The control must remain inert in static-file mode, use only the established relative console API fetch pattern, and avoid changing the existing rounds/schedule behavior.

## How I Verified It

Command: `python3 -m unittest tests.test_dashboard`

Actual output:

```text
.............................................................................................../Users/llm/.peon/worktrees/loops-b13-dashboard/dashboard/generate.py:2201: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10a248e50>
  elif latest_run is not None and latest_run.get("finished_at") is None:
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/Users/llm/.peon/worktrees/loops-b13-dashboard/dashboard/generate.py:2201: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10a2488b0>
  elif latest_run is not None and latest_run.get("finished_at") is None:
ResourceWarning: Enable tracemalloc to get the object allocation traceback
/Users/llm/.peon/worktrees/loops-b13-dashboard/dashboard/generate.py:2201: ResourceWarning: unclosed database in <sqlite3.Connection object at 0x10a2493f0>
  elif latest_run is not None and latest_run.get("finished_at") is None:
ResourceWarning: Enable tracemalloc to get the object allocation traceback
...............................................
----------------------------------------------------------------------
Ran 142 tests in 0.471s

OK
```

## Open Questions

None.
