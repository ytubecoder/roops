# PEON_REPORT — rp-task1 (Amendment 2 contract freeze)

**Status:** DONE

**Branch:** `peon/rp-task1`  
**Worktree:** `/Users/llm/.peon/worktrees/loops-rp-task1`  
**Commit:** `7e41e71` — `docs(interfaces): Amendment 2 — report pages (render step, envelope helper, surfaces)`  
(+ this report commit)

## What I did

Docs-only freeze of Amendment 2 deltas in `docs/INTERFACES.md` (Task 1 of 10). No harness code changed.

1. **§1 Repository layout** — added Amendment 2 paths with group-adjacent placement (no reordering of existing lines):
   - `bin/page_envelope.py` with other `bin/` entries
   - `pagekit/{kit.css,README.md,reference/}` as a new top-level group after `contract/`
   - `loops.d/<name>/render.sh` next to `loops.d/<name>/`
   - `state/loop-data/<name>/` with other `state/` entries
   - `reports/<name>/YYYY-MM-DD-HHMM.html + latest.html` with other `reports/` entries
   - `dashboard/reports.html` with `dashboard/generate.py`
   - gitignore note now lists `dashboard/reports.html` alongside `dashboard/loops.html`

2. **§4.1 step 6.5** — inserted verbatim after step 6 / before step 7: loop-data commit, optional `render.sh`, envelope promotion gate, failure isolation (does not change runner_status / exit code).

3. **§4.1 step 8 retention** — `latest.*` never pruned → `latest.md`, `latest.json`, `latest.html` never pruned (keep-list names all three — Amendment 2).

4. **§8 `loopctl validate`** — non-executable `render.sh` present = FAIL; absent = fine (not page-enabled).

5. **§10 dashboard** — reports screen bullet: `dashboard/reports.html`, preferred HTML report links, stale badge on envelope `meta.run_id` mismatch, envelope-only parse of `latest.html`.

6. **§12** — new `bin/page_envelope.py` contract: CLI (`check` / `meta`), checks list, importable API. Inner fenced CLI block nesting preserved.

## Deviations from the brief

| Brief | What I did | Why |
| --- | --- | --- |
| `cd ~/projects/loops` then commit | Committed from this worktree on `peon/rp-task1` | House rule: work only in the peon worktree; do not switch branches |
| `git push` in step 6 | **Did not push** | House rule + peon rules: foreman pushes after review/merge |
| Only `docs/INTERFACES.md` in the brief commit | Exact message + file for that commit; `PEON_REPORT.md` is a second commit | Peon contract requires the report committed too |

No content deviations from the fenced verbatim inserts (beyond removing the outer ```markdown fence wrappers that are delivery packaging, not document content). One stray trailing fence that would have been an artifact of that packaging was not left in the file.

## Verification

```bash
bash tests/run-tests.sh
# exit 0
```

Tail / summary:

```
== python3 -m unittest discover -s tests -p 'test_*.py' ==
Ran 307 tests in 25.839s
OK

== tests/test_adapters.sh ==
passed: 158, failed: 0

== tests/test_examples.sh ==
passed: 35, failed: 0

== tests/test_runner.sh ==
passed: 115, failed: 0
```

(307 Python + 308 shell = 615 hermetic tests, all green. Docs-only change; expected no breakage.)

## Self-review

- All five content steps applied; only `docs/INTERFACES.md` (+ this report) touched.
- Placement is group-adjacent, not a full re-sort of the layout block.
- §12 has a single inner code fence for the CLI synopsis; prose continues after it (matches existing §2/§8 style).
- Step 6.5 failure isolation wording matches the step-7 dashboard-failure precedent called out in the brief.
- Worktree left clean after commits; branch remains `peon/rp-task1`; no push.

## Concerns / open questions

- None blocking. Later tasks that implement runner/dashboard/pagekit should cite "(Amendment 2)" against this frozen text.
- `pagekit/` was placed after `contract/` and before `loops.d/` (no pre-existing pagekit lines to adjoin). If the foreman prefers a different top-level slot, that is a pure layout-comment tweak, not a contract change.
- Nested parens in step 8 (`… never pruned (the runner's keep-list … Amendment 2)).`) follow replacing the old `` `latest.*` never pruned `` clause with the full replacement phrase from the brief; reads slightly dense but faithful.
