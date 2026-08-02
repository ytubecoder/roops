# WP1 — Garden reorg (structure) — 2026-08-02

Work package 1 of 3 in the garden three-tier reorg. Umbrella design (read it first,
it is the shared context): `docs/superpowers/specs/2026-08-02-garden-three-tier-design.md`.
All decisions herein are **settled — do not relitigate**.

## Context (cold start)

You are working in the loops harness (`docs/INTERFACES.md` is the frozen mechanical
contract; §10 governs `dashboard/generate.py`). The garden dashboard
(`dashboard/loops.html`) currently renders one `.loop-row` `<div>` per loop, then
**all** per-loop `<section class="loop">` blocks (Findings + Recent runs + panels)
stacked below, plus a separate `dashboard/reports.html` screen indexing report pages.

This WP converts each garden row into a native `<details>` accordion whose body is that
loop's section content, adds English glosses beside meaning-bearing kanji, and retires
`reports.html` (the garden becomes the sole index). The generated page must stay a
self-contained static file — nothing fetched on load (see Constraints).

Key code locations (line numbers are approximate, from 2026-08-02 main):

- `dashboard/generate.py` — `_render_loop_row` (~1663), `_render_loop_section`
  (~1808), `_render_page` (~2177, topstrip + garden + sections),
  `_render_reports_page`/`_reports_document` (~2253/2323), `generate()` (~2059,
  writes both files), `generate_reports` wrapper (~2145), `_page_state` (~511,
  computes `page.href/dated/stale` per loop), `_discover_report_page_names` (~491) +
  `include_report_only` in `_resolve_dashboard_loops` (~2032, feeds only the reports
  page), `_STAMP_KANJI` (~1277), `_DISP_KANJI` (~1501), CSS grid rules for
  `.loop-row` (~763, ~1106–1143, console overrides ~1126/1143).
- `bin/console.py` — `_PAGES` dict (~50) maps `/reports.html`.
- Tests — `tests/test_dashboard.py` (several reports-page tests around lines 551,
  577, 1338–1349, 2234–2306), `tests/test_console.py` (~411 asserts `/reports.html`
  serves).

## Scope

`dashboard/generate.py`, `bin/console.py`, `tests/test_dashboard.py`,
`tests/test_console.py`, `docs/INTERFACES.md` (§10 amendment + any `reports.html`
cross-refs, e.g. §13). Nothing else.

## Non-goals

- **No theming changes** — no dark mode, no token changes, no palette work (WP2).
- **No `pagekit/kit.css` changes**, no report-page re-rendering (WP3).
- No recency sort (parked), no print-mode expand-all (CSS cannot open `<details>`;
  accepted limitation — record nothing, do nothing).
- No changes to `site/`, `bin/loopctl`, runner, engines, sqlite schema.
- Orphaned report dirs (e.g. `reports/hello-denied/` with no `loops.d/` entry) simply
  lose their index entry; they stay directly servable. Do not build anything for them.

## Preconditions

WP1 is first in the sequence — the precondition is current `main` containing this MD.
Verify before starting: `dashboard/generate.py` still emits `reports.html`
(`grep -n "reports.html" dashboard/generate.py` matches) and
`bash tests/run-tests.sh` is green on your base commit.

## Settled decisions (from the umbrella — repeated here so this file stands alone)

1. Each garden row becomes
   `<details class="loop-row" name="garden" id="loop-<name>" data-tags="…">`.
   The shared `name="garden"` gives one-open-at-a-time natively.
2. `<summary>` = the current row content (stamp cell, loop name + schedule/description,
   tokonoma, run-meta, rounds switch cell). The row's grid styling moves onto the
   summary element (grid children must be direct children of the grid container).
3. The `<a href="#loop-<name>">` around the loop name in the summary is **removed**
   (plain text now) — the summary itself is the toggle. A small permalink glyph inside
   the expansion body (`<a class="permalink" href="#loop-<name>" title="link to this
   loop">#</a>`) sets the hash instead.
4. The row's report links (`page` / `md` / `latest` in run-meta) **move out of the
   summary** into the expansion body's report block. Links inside a `<summary>` fight
   the toggle; the run-meta keeps only text (last run, spend, next run).
5. Expansion body = the loop's current `section.loop` content, moved inside the
   `<details>`. The `<section class="loop">` element itself may be kept as the body
   wrapper, but its `id="loop-<name>"` moves to the `<details>` (one id per loop).
6. **Report block** at the top of the expansion body, for loops where
   `loop["page"]["href"]` or `loop["report_href"]` exists: prominent link to
   `../reports/<name>/latest.html` (when page-enabled), the `latest.md` link, and the
   dated history links from `loop["page"]["dated"]` (newest first, capped at 30 with a
   `+N older` note — same cap and link shape as today's `_render_reports_page`, which
   this block replaces). Keep the existing `stale` badge behavior on the page link.
7. Deep-link JS, ~10 lines inline, no more: on `DOMContentLoaded` **and** on
   `hashchange`, if `location.hash` matches `#loop-<name>`, set `.open = true` on that
   `<details>` and `scrollIntoView()`. No other new JS.
8. `reports.html` is retired: `generate()` writes only `loops.html`; remove
   `--reports-out`, `generate_reports`, `_render_reports_page`, `_reports_document`,
   `_discover_report_page_names`, and the `include_report_only` branch (all dead once
   the reports page is gone — delete, don't strand). `bin/console.py` drops the
   `/reports.html` route from `_PAGES` (keeps `/`, `/loops.html`, and all
   `/reports/<name>/<file>` serving untouched). The topstrip
   `<a href="reports.html">reports</a>` chip goes.
9. English glosses: pattern is a `<span class="en">word</span>` immediately after the
   kanji, styled tiny and muted (smaller than the kanji, `--nibi`, no new colors).
   Tooltips (`title=`) stay untouched. Pinned wording:

   | Kanji site | Gloss |
   |---|---|
   | Stamp 済 (rows, sections, topstrip chips) | `ok` |
   | Stamp 注 | `warn` |
   | Stamp 警 | `alert` |
   | Stamp 未 | `no data` |
   | Switch 巡 (rounds on) | `on` |
   | Switch 休 (installed, paused) | `paused` |
   | Switch 休 (no schedule loaded) | `off` |
   | Switch 手 (manual) | `manual` |
   | Run-meta `last 巡` / `next 巡` | `run` (renders as `last 巡 run …` / `next 巡 run …`) |
   | Findings pchip `巡 ×N` (times seen) | `seen` |
   | Hanko button 認 | `ack` |
   | Hanko button 休 | `snooze` |
   | Hanko button 済 | `dismiss` |
   | Hanko button 承 | **no gloss** — the natural English word is banned page-wide by an existing test (ack ≠ approval doctrine, INTERFACES §10). Leave the kanji alone; its `title` already explains. |
   | Suppressed-finding stamp-mark 認/休/済 | the disposition verb (`ack` / `snoozed` / `dismissed`) |
   | Topstrip `seal-mini` 巡 | none — decorative brand seal beside the "roops" wordmark |
   | Kicker 庭, note 床の間/休 lines | none — already adjacent to English |
   | Tokonoma 未 "never run" line | none — "never run" is already adjacent |

10. Console controls rendered inside the summary (`_render_console_controls`, active
    only under `html.console-active`) must not flap the accordion: their click handlers
    in the hydration script call `event.preventDefault()` (a prevented default on a
    click inside `<summary>` suppresses the toggle). Verify the existing handlers;
    add the call where missing.
11. The tag filter (`loopsFilterByTag`) keeps working: the `<details>` carries
    `data-tags` (the row's current attribute). The inner section may keep its own
    `data-tags` or lose it — either is fine as long as filtering hides whole rows.

## Tasks

1. Restructure `_render_loop_row` + `_render_loop_section` + `_render_page` per
   settled decisions 1–6 (row and section render as one `<details>` unit; adjust the
   assembly in `_render_page` so sections are no longer appended separately).
2. Move/retarget the `.loop-row` grid CSS onto the summary; hide the default
   disclosure marker (`summary { list-style: none }` +
   `summary::-webkit-details-marker { display: none }`); `cursor: pointer`; keep the
   console-active and mobile grid overrides working (they currently target
   `.loop-row`, including `html.console-active .loop-row` and the
   `@media (max-width: 767px)` block — keep that block the file's last-word block,
   per the comment convention inside the stylesheet).
3. Add the report block renderer and wire it into the expansion body (settled
   decision 6). Reuse `loop["page"]` state — do not re-scan the filesystem.
4. Add the deep-link JS (settled decision 7) and the permalink glyph (decision 3).
5. Add English glosses per the pinned table (decision 9) — enumerate every emit site
   in `generate.py`; the table is exhaustive for today's code, but if you find another
   meaning-bearing kanji emit site, gloss it from its `title` attribute and say so in
   `PEON_REPORT.md`.
6. Retire `reports.html` per decision 8 (generator + console + topstrip chip + dead
   code removal).
7. Amend `docs/INTERFACES.md` **in the same change**: §10 header (writes `loops.html`
   only), the "Report pages (Amendment 2)" bullet (report links + dated history now
   live in the per-loop accordion body; reports screen retired 2026-08-02), a new §10
   bullet for the accordion + English glosses + deep-link JS (mark it Amendment
   2026-08-02, note the one-open-at-a-time `name="garden"` behavior and that the JS
   stays hermetic), and every other `reports.html` mention (`grep -rn "reports.html"
   docs/INTERFACES.md` — includes §13's page list).
8. Update/replace the existing tests that assert `reports.html` behavior (dashboard
   and console — see Tests). Remove, don't skip.

## Tests

Existing suites that must stay green: `bash tests/run-tests.sh` (full hermetic suite —
the pass bar), including `tests/html_selfcontained.py`-based assertions which continue
to cover `loops.html`.

Existing tests to rework in this WP (they pin the retired behavior):
`tests/test_dashboard.py` reports-page tests (~551, 577, 1338–1349, 2234–2306 —
`generate_reports`, reports self-containment, "no page yet"/"no meta"/historical
markers) and `tests/test_console.py` ~411 (`/reports.html` in the served-pages list).
Findings-worthy content among them (history cap 30, stale badge, md link) is re-pinned
on the accordion report block instead.

New tests to add (names indicative, follow the file's existing class style):

- `test_dashboard.py::TestGardenAccordion` —
  - each fixture loop emits `<details class="loop-row" name="garden"
    id="loop-<name>"` exactly once; no other element carries that id;
  - the summary contains stamp, loop name (as plain text, **no** `<a href="#loop-`),
    tokonoma, and switch; the run-meta contains no `<a` anymore;
  - the body contains the loop's Findings block and `<h3>Recent runs</h3>`;
  - the permalink glyph `href="#loop-<name>"` is present inside the body;
  - the deep-link script is present (assert on `hashchange` and `location.hash`).
- `test_dashboard.py::TestReportBlock` — for a page-enabled fixture loop: body links
  `../reports/<name>/latest.html` and `latest.md`; dated history links present,
  capped at 30 with `+N older`; stale badge renders when the envelope run_id lags the
  latest promoted run (reuse the existing stale fixture).
- `test_dashboard.py::TestReportsRetired` — `generate()` writes no
  `dashboard/reports.html` (and the module has no `generate_reports` attribute); the
  topstrip has no `reports.html` link.
- `test_dashboard.py::TestEnglishGlosses` — glosses from the pinned table present as
  `<span class="en">…</span>` (spot-check at least: stamp `ok`/`warn`/`alert`,
  switch `on`/`paused`/`manual`, run-meta `run`); the page still never contains the
  banned word (existing test keeps pinning this — do not weaken it).
- `test_console.py::TestReportsHtmlRetired` — `GET /reports.html` → 404;
  `GET /reports/<name>/latest.html` → 200; `GET /` and `GET /loops.html` → 200.

Run the new tests plus the full suite; paste both outputs into `PEON_REPORT.md`.

## Definition of Done

- [ ] Garden rows are `<details name="garden">` accordions; sections no longer render
      below the garden; one open at a time; deep-links open the right row.
- [ ] Report block in the body per settled decision 6; report links gone from summary.
- [ ] English glosses per the pinned table; 承 unglossed; banned-word test untouched.
- [ ] `reports.html` gone from generator, console routes, topstrip; dead code removed.
- [ ] INTERFACES amendments in the same change (§10 + every cross-ref).
- [ ] New tests added and green; reworked tests replaced, not skipped.
- [ ] `bash tests/run-tests.sh` fully green — output pasted in `PEON_REPORT.md`.
- [ ] `PEON_REPORT.md` written: what changed, test evidence, any deviations or extra
      kanji sites found.

## Verification commands

```sh
bash tests/run-tests.sh                          # full suite — the pass bar
python3 dashboard/generate.py --root "$PWD"      # regenerates dashboard/loops.html only
grep -c '<details class="loop-row" name="garden"' dashboard/loops.html   # == loop count
ls dashboard/reports.html 2>&1                   # newly absent in a fresh worktree
grep -rn 'reports\.html' dashboard/generate.py bin/console.py            # no live refs
```

(Post-merge, the foreman — not you — verifies the live URLs and deletes the stale
machine-local `dashboard/reports.html` artifact, and checks the machine-local Caddy
config.)

## Constraints & gotchas

- **§10 hermetic rules bind**: the generated page fetches NOTHING on load — no
  webfonts, no CDN, no remote `url()`/`src`. Inline everything. Navigation
  `<a href>` is fine. `tests/html_selfcontained.py` enforces this; keep `loops.html`
  covered by it.
- **INTERFACES amendment ships in the same change as the code** — never drift.
- **Shared checkout / worktree**: you are in an isolated worktree; commit there. If
  `git commit` fails in your sandbox, leave the working tree clean and state it in
  `PEON_REPORT.md` — do **not** improvise ssh remotes, standalone `.git` dirs, or
  other escapes.
- **Ruff hook**: the repo carries pre-existing lint debt and the hook uses a broad
  ruleset — fix findings only in files you edited, leave the rest.
- **macOS**: no `flock`, no GNU `timeout` — don't add either to tests or scripts.
- The dashboard is opened both via HTTP and as `file://` — relative links
  (`../reports/...`) must keep working for both, exactly as today.
- Don't touch `bin/redact.py`, `pagekit/`, `bin/page_envelope.py` — report-page
  rendering is out of scope; only the *links to* pages move.
