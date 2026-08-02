# PEON_REPORT — WP3 pagekit unification (peon/wp3-pagekit)

**Work package:** `docs/workpackages/2026-08-02-wp3-pagekit-unification.md`  
**Branch:** `peon/wp3-pagekit`  
**Date:** 2026-08-02

## What changed

1. **`pagekit/kit.css`** — rebuilt on the garden's shared role + font tokens in both modes:
   - Base `:root` = light values (incl. `--serif` / `--mono`, mode-independent).
   - `@media (prefers-color-scheme: dark)` + `:root[data-theme="dark"]` / `:root[data-theme="light"]` mirror WP2's mechanism and exact values from `dashboard/generate.py`.
   - Old→new mapping applied throughout rules:
     | old | new |
     |---|---|
     | `--bg` | `--washi` |
     | `--panel` | `--washi-shade` |
     | `--line` / `--line2` | `--hair` / `--hair2` |
     | `--ink` | `--sumi` |
     | **`--sub`** | **`--nibi`** |
     | **`--mut`** | **`--nibi-faint`** |
     | `--accent` | `--ai` |
     | `--high` | `--shu` |
     | `--med` | `--ochre` |
     | `--mono` | `--mono` (value now garden's Cascadia stack) |
   - Future-proof tokens declared: `--koke`, `--sumi-deep`, `--shu-deep`, `--serif`, and the four `-rgb` companions.
   - Minimal `#theme-toggle` styles added (same idea as the garden button).

2. **`pagekit/toggle.js`** (new) — shared theme toggle + persistence:
   - localStorage key **`loops-theme`** (WP2-pinned).
   - `data-theme` values `"dark"` / `"light"` on `<html>`.
   - Pre-paint stamp + click handler on `#theme-toggle`.
   - Inlined via `$PAGEKIT` exactly like `kit.css` (never `<script src>`).

3. **`pagekit/README.md`** — documents kit + toggle.js read-and-inline contract, button copy-paste snippet, dual-mode palette under garden token names.

4. **`loops.d/kagi-ban/render_page.py`**:
   - `load_toggle_js()` sibling of `load_kit_css()` (missing file fails loudly).
   - PAGE template: toggle script in `<head>` before `<style>` (no-flash), button near `.meta`.
   - Token refs in severity markers / legend / stat dots updated `--high`/`--med` → `--shu`/`--ochre` so they track the renamed kit tokens (required for correct colors; not findings-rendering logic).

5. **`pagekit/reference/reference-page.html`** — regenerated with the pinned fixture command; envelope check passes.

6. **Tests:**
   - `tests/token_parser.py` — line-oriented `--token: value;` parser; light via base `:root`, dark via `:root[data-theme="dark"]`; fonts excluded from dark set; `compare_token_maps`.
   - `tests/test_token_parser.py` — non-vacuity self-test (identical / missing / value-mismatch).
   - `tests/test_token_drift.py` — four axes: light name-set, dark name-set, light values, dark values (garden CSS string vs `kit.css`).
   - `tests/test_kagi_ban.py` — throwaway PAGEKIT in `test_edit_to_kit_css_reaches_the_page` seeds a stub `toggle.js` so the new required file does not break the kit-sourcing probe.

7. **`docs/INTERFACES.md` §10** style bullet — WP3 clause: shared tokens, anti-drift test name, toggle + `loops-theme` persistence across garden and report pages.

## What did **not** change (scope guards)

- `bin/redact.py` — untouched.
- `dashboard/generate.py` — token values untouched (canonical source; WP3 only reads them).
- No new `$PAGEKIT`-adjacent env var (toggle.js lives next to kit.css).
- No `site/` changes; no new page-enabled loops.
- Body font stack in kit.css still `-apple-system…` (not rewired to `var(--serif)`).

## Preconditions verified

```
grep -n 'data-theme' dashboard/generate.py            # hits (WP2 present)
grep -n 'prefers-color-scheme' dashboard/generate.py  # hits
localStorage key: loops-theme
--nibi-faint present in both modes in generate.py
```

## How verified

### New tests only

```text
$ python3 -m unittest tests.test_token_drift tests.test_token_parser -v

test_dark_token_name_sets_match (tests.test_token_drift.TestTokenDrift.test_dark_token_name_sets_match) ... ok
test_dark_token_values_match (tests.test_token_drift.TestTokenDrift.test_dark_token_values_match) ... ok
test_light_token_name_sets_match (tests.test_token_drift.TestTokenDrift.test_light_token_name_sets_match) ... ok
test_light_token_values_match (tests.test_token_drift.TestTokenDrift.test_light_token_values_match) ... ok
test_identical_blocks_report_no_diff (tests.test_token_parser.CompareTokenMapsTests.test_identical_blocks_report_no_diff) ... ok
test_missing_token_is_caught (tests.test_token_parser.CompareTokenMapsTests.test_missing_token_is_caught) ... ok
test_value_mismatch_is_caught (tests.test_token_parser.CompareTokenMapsTests.test_value_mismatch_is_caught) ... ok
test_dark_excludes_fonts (tests.test_token_parser.ExtractTests.test_dark_excludes_fonts) ... ok
test_dark_uses_data_theme_block_not_media_query_only (tests.test_token_parser.ExtractTests.test_dark_uses_data_theme_block_not_media_query_only) ... ok
test_light_includes_fonts_and_role_tokens (tests.test_token_parser.ExtractTests.test_light_includes_fonts_and_role_tokens) ... ok
test_ignores_non_custom_properties (tests.test_token_parser.ParseRootBlockTests.test_ignores_non_custom_properties) ... ok
test_one_declaration_per_line (tests.test_token_parser.ParseRootBlockTests.test_one_declaration_per_line) ... ok

----------------------------------------------------------------------
Ran 12 tests in 0.001s

OK
```

### Full suite (`bash tests/run-tests.sh`)

Python layer (includes auto-discovered `test_token_drift` + `test_token_parser`):

```text
== python3 -m unittest discover -s tests -p 'test_*.py' ==
...
Ran 661 tests in 52.291s

OK
```

Shell layer summary:

```text
test_adapters.sh:          passed: 158, failed: 0
test_examples.sh:          passed: 35, failed: 0
test_runner.sh:            passed: 135, failed: 0
test_runner_pages.sh:      passed=21 failed=2   ← pre-existing env flake (see below)
test_skill_import_e2e.sh:  passed: 16, failed: 0
```

Overall exit code of `run-tests.sh`: **1** solely because of `test_runner_pages.sh` (2 assertions).

### Other gates

```text
grep -c -- '--bg\|--panel\b\|--ink\b\|--accent\b' pagekit/kit.css   → 0
python3 bin/page_envelope.py check --file pagekit/reference/reference-page.html \
  --expect-run-id reference --expect-loop kagi-ban                 → ok
fixture re-render to /tmp/kagi-ban-wp3-check.html + envelope check → ok
tests/test_kagi_ban.py (PagekitSourcingTests + PageSelfContainmentTests) → all ok
```

## Pre-existing failure (not introduced by WP3)

`tests/test_runner_pages.sh` fails 2 assertions that use:

```bash
run_dir="$(ls -d "$root/state/runs/"*pageloop* | head -n1)"
```

With `CLICOLOR_FORCE=1` (set in this peon sandbox environment), macOS `/bin/ls` injects ANSI color codes into the path (`\033[34m…\033[0m`), so `-e "$run_dir/page-render.log"` looks for a non-existent colored path. The page-render.log **does** exist on disk under the real path; the assertion is wrong because of colored `ls` output.

- Same failure on the **base commit** before any WP3 edits (precondition full-suite run).
- WP brief: do not edit `test_runner_pages.sh` unless a real WP3 regression appears; `seed_pagekit` still only needs empty `kit.css` (synthetic renderers never open toggle.js).
- Suggested foreman follow-up (out of WP3 scope): replace `ls -d` with a bash glob / `find`, or force `ls -G`/`CLICOLOR=0` in the helper.

## Deviations / notes

1. **Severity color refs in `render_page.py`** (template + `sev_marker`): updated `--high`/`--med` → `--shu`/`--ochre`. Not listed as a separate task, but required once kit.css drops the old names; leaving them would leave colorless markers. Findings structure (`build_bars` / `build_groups` / KV neutralization) untouched.

2. **`test_edit_to_kit_css_reaches_the_page`**: seeds a stub `toggle.js` in the throwaway PAGEKIT so the new fatal-on-missing contract does not break the kit-sourcing test.

3. **No §4.1 env-var amendment**: `toggle.js` is read from existing `$PAGEKIT` — confirmed; no INTERFACES §4.1 change needed.

4. **Typography / visual consistency:** report pages still use the system UI sans stack for body text; `--serif` is declared for parity but unused in rules (per non-goal). May read slightly inconsistent next to the garden's mincho headlines — intentional for this WP.

5. **Garden dark body frame** (`body { background: #08090B }` in media / data-theme dark) is **not** copied into kit.css: report pages use `background: var(--washi)` as the page surface itself (no `.sheet` wrapper). Token *values* still match; layout chrome differs.

6. **Commits:** **failed in this sandbox** — `git add`/`git commit` cannot create
   `/Users/llm/projects/loops/.git/worktrees/loops-wp3-pagekit/index.lock`
   (`Operation not permitted`). Working tree left with all WP3 changes + this
   report for the **foreman to commit**. Suggested message:

   ```
   feat(pagekit): WP3 unification — shared garden tokens + theme toggle (B-16)

   Rebuild pagekit/kit.css on the garden role/font tokens (both modes), add
   pagekit/toggle.js inlined via $PAGEKIT with localStorage key loops-theme,
   wire kagi-ban report pages, anti-drift tests, INTERFACES §10 amendment.
   ```

   Files to stage:
   - `pagekit/kit.css`, `pagekit/toggle.js`, `pagekit/README.md`
   - `pagekit/reference/reference-page.html`
   - `loops.d/kagi-ban/render_page.py`
   - `tests/token_parser.py`, `tests/test_token_parser.py`, `tests/test_token_drift.py`
   - `tests/test_kagi_ban.py`
   - `docs/INTERFACES.md`
   - `PEON_REPORT.md`

## Open questions

- None that block merge of WP3 as specified.
- Foreman post-merge: re-render real `reports/kagi-ban/latest.html` from latest scan; side-by-side garden/report screenshots (light + dark) via Playwright MCP.
- Optional follow-up: harden `test_runner_pages.sh` against colored `ls` (pre-existing).
