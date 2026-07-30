# Peon Report: Task 6 Dashboard Report Pages

Status: DONE

## What Changed

- Added report-page state resolution to `dashboard/generate.py`:
  - Loads `bin/page_envelope.py` through the existing module loader.
  - Computes page enablement from executable `loops.d/<name>/render.sh`.
  - Reads only `reports/<name>/latest.html` envelope metadata, never display HTML.
  - Lists dated history from `YYYY-MM-DD-HHMM.html` filenames only.
  - Computes stale page badges against the latest promoted run.
  - Marks no-page, no-meta, and historical report-page states.
- Updated loop row report links to prefer `../reports/<name>/latest.html`, with markdown retained as `md` or `latest`.
- Added `dashboard/reports.html` generation, `generate_reports(...)`, `generate(..., reports_out_file=None, return_html=False)`, and CLI `--reports-out`.
- Added garden-style CSS for report entries and page-state badges.
- Added `dashboard/reports.html` to `.gitignore`.
- Added report-page dashboard tests to `tests/test_dashboard.py`.

## Why

Task 6 requires dashboard surfaces for promoted report pages under docs/INTERFACES.md §10 Amendment 2: row page links with stale status and a new reports index generated in the same dashboard invocation.

## Deviations / Adaptations

- The brief's line anchors were stale, so I located the live anchors by function name after the garden restyle.
- Markup/CSS follows the current roops garden shell rather than the old dark-slate snippets.
- The stale row test enables `render.sh`; this matches the §10 rule that page staleness is computed only for page-enabled loops with parsed metadata.
- The report-page tests install the real `page_envelope.py` and `redact.py` into the fixture root so metadata is parsed through the same `root/bin` loader path used in production.
- The existing atomic-write test now treats `reports.html` as a legitimate generated output, not a leftover temp file.

## Verification

- TDD failure pass: `python3 -m unittest tests.test_dashboard -v` failed as expected after adding tests: missing `generate_reports`, missing `return_html`, and missing `reports.html`.
- Final targeted pass: `python3 -m unittest tests.test_dashboard -v` passed 66 tests.
- Final full pass: `bash tests/run-tests.sh` passed:
  - Python unittest discovery: 330 tests.
  - Adapter shell checks: 158 passed, 0 failed.
  - Example shell checks: 35 passed, 0 failed.
  - Runner shell checks: 115 passed, 0 failed.
- `python3 -m py_compile dashboard/generate.py tests/test_dashboard.py` passed.
- `git diff --check` passed.

## Self-Review

- New report-page read paths degrade to empty state, no-meta, or no stale badge on missing/unreadable helper, envelope, sqlite promoted-run query, and report directory listing failures.
- `reports.html` includes configured page-enabled loops and report directories with HTML pages on disk, including historical dated-only entries.
- Dated history is capped at 30 and derived from filenames only.
- Existing dashboard semantics for status, stale loop detection, findings, and report markdown drawers were left intact.

## Concerns / Open Questions

- Full-suite unittest discovery still emits ResourceWarning messages about unclosed sqlite connections from existing tests; the suite passes and this task did not introduce or address those warnings.
- No open functional questions.
