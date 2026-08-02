# PEON_REPORT — WP1 Garden reorg (structure) — 2026-08-02

Branch: `peon/wp1-garden-reorg`  
Work package: `docs/workpackages/2026-08-02-wp1-garden-reorg.md`

## What changed

### `dashboard/generate.py`
- Garden rows are now native `<details class="loop-row" name="garden" id="loop-<name>" data-tags="…">` accordions. Shared `name="garden"` → one-open-at-a-time.
- `<summary>` holds the former row glance content (stamp, plain-text name, schedule/description, tokonoma, run-meta text, rounds switch + console controls). The name is no longer an `<a href="#loop-…">`.
- Expansion body is the former `section.loop` content (Findings, Recent runs, panels, handoff, report drawer) plus:
  - a permalink glyph `<a class="permalink" href="#loop-<name>">#</a>`
  - a **report block** (page / `latest.md` links + dated history capped at 30 with `+N older`, stale badge when envelope run_id lags)
- Report links were removed from the summary (links inside `<summary>` fight the toggle).
- Grid CSS moved from `.loop-row` onto `.loop-row > summary`; default disclosure marker hidden; cursor pointer; console-active and mobile grid overrides retargeted to the summary.
- Deep-link JS (~10 lines): `loopsOpenHash` on `DOMContentLoaded` + `hashchange` opens `#loop-<name>` and `scrollIntoView()`.
- English glosses (`<span class="en">…</span>`) per the pinned table for stamps, switch, run-meta 巡, findings pchip, hanko buttons, suppressed disposition marks, topstrip stamp chips. **承 unglossed**.
- Console control click handlers call `event.preventDefault()` so they don't flap the accordion.
- **`reports.html` retired**: `generate()` writes only `loops.html`; removed `--reports-out`, `generate_reports`, `_render_reports_page`, `_reports_document`, `_discover_report_page_names`, and `include_report_only`. Topstrip "reports" chip removed.

### `bin/console.py`
- Dropped `/reports.html` from `_PAGES`. Per-loop `/reports/<name>/<file>` serving unchanged. `/` and `/loops.html` unchanged.

### `docs/INTERFACES.md`
- §10 header: writes `loops.html` only (no `--reports-out`).
- Report pages bullet amended: links + dated history live in accordion body; reports screen retired 2026-08-02.
- New §10 bullet (Amendment 2026-08-02): accordion, English glosses, hermetic deep-link JS, one-open-at-a-time `name="garden"`.
- Layout tree + gitignore list + §13 console page table updated (no `reports.html`).

### Tests
- Reworked/removed retired reports-page tests; re-pinned history/stale/md behavior on the accordion report block.
- Added:
  - `TestGardenAccordion`
  - `TestReportBlock`
  - `TestReportsRetired`
  - `TestEnglishGlosses`
  - `TestReportsHtmlRetired` (console)
- Updated tag-filter, switch-manual, suppressed-stamp, and console-active CSS assertions for the new markup.

## Why

Implements WP1 of the garden three-tier reorg (umbrella: `docs/superpowers/specs/2026-08-02-garden-three-tier-design.md`). Collapses the stacked per-loop sections into inline accordions, retires the separate dark-teal reports index, and adds English glosses beside meaning-bearing kanji — without theming (WP2) or pagekit changes (WP3).

## Deviations / notes

1. **No extra meaning-bearing kanji sites found** beyond the pinned table. Tokonoma "never run" 未, kicker 庭, note 床の間/休, and seal-mini 巡 remain unglossed per the table. 承 unglossed.
2. **Report block labels**: page link text is `page` (with optional stale badge); md link text is always `latest.md` (was `md`/`latest` in the old row). Hrefs unchanged (`../reports/<name>/latest.html|.md`).
3. **Orphaned report dirs** (e.g. `hello-denied` with no `loops.d/` entry) lose their index entry as specified; still directly servable.
4. **Full suite env note**: this machine has `CLICOLOR_FORCE=1`, which injects ANSI color into `ls -d` captures inside `tests/test_runner_pages.sh` (pre-existing, out of WP1 scope). The suite is green when run as `env -u CLICOLOR_FORCE -u CLICOLOR bash tests/run-tests.sh`. Not caused by this change; no WP1 files touch the runner-pages tests.
5. Live `dashboard/loops.html` was regenerated once for verification; it is gitignored and not committed.

## Verification

### New tests

```
$ python3 -m unittest tests.test_dashboard.TestGardenAccordion \
    tests.test_dashboard.TestReportBlock \
    tests.test_dashboard.TestReportsRetired \
    tests.test_dashboard.TestEnglishGlosses \
    tests.test_console.TestReportsHtmlRetired -v

test_deep_link_script_present ... ok
test_each_loop_emits_one_details_accordion ... ok
test_summary_has_stamp_name_toko_switch_no_name_link_no_report_links ... ok
test_body_links_page_md_and_dated_history ... ok
test_stale_badge_when_envelope_lags_promoted_run ... ok
test_generate_writes_no_reports_html ... ok
test_stamp_switch_and_run_meta_glosses ... ok
test_dashboard_routes_still_200 ... ok
test_per_loop_report_still_served ... ok
test_reports_html_is_404 ... ok

----------------------------------------------------------------------
Ran 10 tests in 0.161s

OK
```

### Full suite

```
$ env -u CLICOLOR_FORCE -u CLICOLOR bash tests/run-tests.sh

== python3 -m unittest discover -s tests -p 'test_*.py' ==
Ran 641 tests in 53.156s
OK

== tests/test_adapters.sh ==
passed: 158, failed: 0

== tests/test_examples.sh ==
passed: 35, failed: 0

== tests/test_runner.sh ==
passed: 135, failed: 0

== tests/test_runner_pages.sh ==
test_runner_pages: passed=23 failed=0

== tests/test_skill_import_e2e.sh ==
passed: 16, failed: 0

EXIT:0
```

### WP verification commands

```
$ python3 dashboard/generate.py --root "$PWD"
…/dashboard/loops.html

$ ls dashboard/reports.html 2>&1
ls: dashboard/reports.html: No such file or directory

$ grep -rn 'reports\.html' dashboard/generate.py bin/console.py
(no live refs)
```

## Open questions

None that block WP1. Post-merge foreman work (not peon):
- Delete stale machine-local `dashboard/reports.html` if present.
- Check machine-local Caddy / live URLs.
- WP2 (dark mode / tokens) and WP3 (pagekit token share) remain sequential follow-ups.

## Commit status (sandbox blocked)

**`git commit` failed in this sandbox** with:

```
fatal: Unable to create '/Users/llm/projects/loops/.git/worktrees/loops-wp1-garden-reorg/index.lock': Operation not permitted
```

The worktree content dir is writable; the linked gitdir under
`/Users/llm/projects/loops/.git/worktrees/loops-wp1-garden-reorg/` is not
(sandbox denies creating `index.lock`). Per WP constraints: did **not**
improvise standalone `.git` dirs, remotes, or other escapes.

**Foreman must commit** the following working-tree changes on
`peon/wp1-garden-reorg`:

```
 M bin/console.py
 M dashboard/generate.py
 M docs/INTERFACES.md
 M tests/test_console.py
 M tests/test_dashboard.py
?? PEON_REPORT.md
```

Suggested message:

```
WP1: garden accordion reorg — details rows, report block, glosses; retire reports.html

Each garden row is now a <details name="garden"> accordion with the former
section content as the expansion body, a per-loop report block (page/md +
dated history), deep-link JS, and English glosses beside meaning-bearing
kanji. reports.html is retired from the generator and console; INTERFACES
§10 amended in the same change.
```

## Definition of Done checklist

- [x] Garden rows are `<details name="garden">` accordions; sections no longer stacked below; one open at a time; deep-links wired.
- [x] Report block in body; report links gone from summary.
- [x] English glosses per pinned table; 承 unglossed; banned-word test untouched.
- [x] `reports.html` gone from generator, console routes, topstrip; dead code removed.
- [x] INTERFACES amendments in the same change.
- [x] New tests added and green; reworked tests replaced, not skipped.
- [x] Full suite green (with color-env note above).
- [ ] Code + PEON_REPORT committed — **blocked on sandbox; foreman to commit** (see above).
