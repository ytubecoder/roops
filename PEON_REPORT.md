# PEON_REPORT — WP2 Dark mode (garden dashboard)

**Branch:** `peon/wp2-dark-mode`  
**Work package:** `docs/workpackages/2026-08-02-wp2-dark-mode.md`  
**Date:** 2026-08-02

## Preconditions (verified before coding)

```
grep -n '<details class="loop-row" name="garden"' dashboard/generate.py
→ 1816: ... matches (WP1 landed)

grep -n 'reports\.html' dashboard/generate.py bin/console.py
→ no live refs
```

Base was WP1-merged (`fd7f256` / `2ef7434`).

## What changed

### `dashboard/generate.py`

1. **Token blocks (one declaration per line):**
   - Light `:root` reflowed; added `--nibi-faint` + 4 companion `-rgb` triplets; fonts stay base-only.
   - `@media (prefers-color-scheme: dark) { :root { … } }` with full dark role + rgb set, `color-scheme: dark`.
   - `:root[data-theme="dark"]` and `:root[data-theme="light"]` override blocks (same token sets).

2. **Group A rgba sweep (7 sites → companion tokens; alphas unchanged):**
   - `.loop-row > summary:hover` → `rgba(var(--sumi-rgb),.035)`  
     *(WP1 renamed this from the pre-WP1 `.loop-row:hover`; same literal, updated selector.)*
   - `.finding .cmd` → `rgba(var(--sumi-rgb),.05)`
   - `.hanko-btn:hover` → `rgba(var(--shu-rgb),.08)`
   - `details.handoff` → `rgba(var(--shu-rgb),.05)`
   - `.tag` → `rgba(var(--ai-rgb),.06)`
   - `.toko-scroll` scrollbar-color → `rgba(var(--nibi-rgb),.3)`
   - `.toko-scroll::-webkit-scrollbar-thumb` → `rgba(var(--nibi-rgb),.28)`

3. **Group B left as mode-neutral literals** (sheet shadow, garden sheen, toko inset, badge.no-page, sched-panel shadow, #recent-events sheen).

4. **`--sumi-deep` / body backdrop tension — resolved with scoped body override:**
   - Token `--sumi-deep` ships at pinned near-white `#F4F5F7` in dark (ink-tier direction preserved).
   - Dark modes set `body { background: #08090B; }` (media block + `:root[data-theme="dark"] body`).
   - Light explicit theme restores `body { background: var(--sumi-deep); }` so OS-dark + user-light does not keep the near-black override.
   - Rationale: without the override, dark mode would put a bright frame around near-black paper — inverse of light mode’s paper-on-desk relationship.

5. **Toggle control** last item in `.head-stats`:
   ```html
   <button type="button" id="theme-toggle" onclick="loopsToggleTheme()"
     title="toggle light/dark" aria-label="toggle color theme">◐</button>
   ```
   Minimal styles via existing tokens (`--hair2` border, `--nibi` / `--sumi` color).

6. **`loopsToggleTheme()`** in `DASHBOARD_JS` (body end); persists `localStorage['loops-theme']` = `dark`|`light`.

7. **No-flash head stamp** in `_wrap_html`: inline `<script>` immediately before `<style>{CSS}</style>`, reads `loops-theme` and stamps `data-theme` on `<html>` inside `try/catch`.

### `docs/INTERFACES.md`

§10 Style bullet amended **(Amendment 2026-08-02, WP2)** covering second token set, media + `[data-theme]` override, pinned `loops-theme` / `data-theme` / `dark`|`light`, head-stamp, hermetic client-only JS, WP3 reuse note.

### `tests/test_dashboard.py`

- `DarkModeTokensTests` — four blocks present, light value byte-exactness, token-name-set parity (fonts excluded), one-decl-per-line parseability.
- `ThemeToggleTests` — button in topstrip, `loopsToggleTheme`, head-stamp before `<style>`, pinned `'loops-theme'` key in stamp + toggle.

## Final dark token values shipped

| Token | Dark value | Notes |
|---|---|---|
| `--washi` | `#0E0F12` | as table |
| `--washi-shade` | `#14161A` | as table |
| `--sumi` | `#E7E9EC` | as table |
| `--sumi-deep` | `#F4F5F7` | as table (token only; body uses `#08090B`) |
| `--shu` | `#D84F63` | as table |
| `--shu-deep` | `#B84354` | as table |
| `--ai` | `#279A83` | as table |
| `--nibi` | `#9AA1AB` | as table |
| `--nibi-faint` | `#5D6570` | as table (declared, unused by garden selectors) |
| `--koke` | `#8FA97A` | as table |
| `--ochre` | `#B48C1A` | as table |
| `--hair` | `#22252B` | as table |
| `--hair2` | `#2C3037` | as table |
| `--sumi-rgb` | `231,233,236` | companion |
| `--shu-rgb` | `216,79,99` | companion |
| `--ai-rgb` | `39,154,131` | companion |
| `--nibi-rgb` | `154,161,171` | companion |

Light role values unchanged (byte-exact vs pre-WP2). Companion light RGB matches prior Group A literals.

## Deviations / notes

1. **Selector name for Group A hover:** WP1 changed `.loop-row:hover` → `.loop-row > summary:hover`. Same rgba; rewrote the live selector. Documented for the sweep table.
2. **Body backdrop override** (see above) — only deviation from “ship tokens and leave component rules alone.”
3. **No new hex outside token blocks** from WP1 (re-scanned). No extra Group A sites beyond the seven.
4. **Literal `#08090B`** for dark body only — intentional, not a new role token; keeps `--sumi-deep` free for future ink use.

## Verification

### New tests

```
$ python3 -m unittest tests.test_dashboard.DarkModeTokensTests tests.test_dashboard.ThemeToggleTests -v
test_light_role_token_values_byte_exact ... ok
test_media_and_data_theme_blocks_present_with_color_scheme ... ok
test_one_declaration_per_line_parseability ... ok
test_token_name_set_parity_across_four_blocks ... ok
test_head_stamp_precedes_style ... ok
test_loops_toggle_theme_function_present ... ok
test_pinned_loops_theme_key_in_stamp_and_toggle ... ok
test_theme_toggle_button_in_topstrip ... ok

----------------------------------------------------------------------
Ran 8 tests in 0.016s

OK
```

### Full suite (`bash tests/run-tests.sh`)

```
== python3 -m unittest discover -s tests -p 'test_*.py' ==
Ran 649 tests in 53.029s
OK

== tests/test_adapters.sh ==
passed: 158, failed: 0

== tests/test_examples.sh ==
passed: 35, failed: 0

== tests/test_runner.sh ==
passed: 135, failed: 0

== tests/test_runner_pages.sh ==
FAIL: render log written (missing: …/page-render.log)   # path polluted with ANSI color codes
FAIL: reason logged (expected to contain [redaction])
test_runner_pages: passed=21 failed=2

== tests/test_skill_import_e2e.sh ==
passed: 16, failed: 0
```

**WP2-related suites are green.** `test_runner_pages.sh` (2 fails) is **unrelated** to this WP: failures show ANSI SGR sequences (`\033[34m` …) embedded in `$run_dir` paths when looking for `page-render.log`. Reproduced on re-run; no edits to page renderer, runner, or `pagekit/`. Foreman should treat as env/pre-existing flake unless it also fails on clean main without colorized tooling.

### Spec verification greps (after `python3 dashboard/generate.py --root "$PWD"`)

```
@media (prefers-color-scheme: dark)  → 1
data-theme="dark"                    → 2
data-theme="light"                   → 2
localStorage.*loops-theme            → head stamp + toggle (getItem + setItem)
--sumi:                              → 4
--sumi-rgb:                          → 4
--nibi-faint:                        → 4
--serif:                             → 1
head-stamp precedes <style>          → ok
```

(`dashboard/loops.html` is gitignored — regen is local only.)

## Open questions

1. Foreman screenshot QA still required (both `prefers-color-scheme` + both explicit toggle states): hanko stamps, badges, finding borders, toko inset — especially Group B ambient sheens on dark.
2. Whether `#08090B` body frame is the right near-black (vs matching `--washi` or dropping override) is a visual call for the foreman.
3. `test_runner_pages.sh` ANSI path pollution — investigate separately; not a WP2 regression surface.
4. WP3 will reuse `loops-theme` / `data-theme` / values on report pages and build on the token-name-set parity test.

## Commit status

**`git commit` failed in this sandbox** (as anticipated by the WP):

```
fatal: Unable to create '.../loops/.git/worktrees/loops-wp2-dark-mode/index.lock': Operation not permitted
```

Working tree intentionally left dirty for the foreman to commit. Branch remains `peon/wp2-dark-mode` (not pushed). Files to commit:

- `dashboard/generate.py`
- `docs/INTERFACES.md`
- `tests/test_dashboard.py`
- `PEON_REPORT.md`

Suggested message:

```
feat(dashboard): WP2 dark mode — dual token sets, theme toggle, INTERFACES §10

Add OS prefers-color-scheme dark palette plus explicit data-theme light/dark
overrides (toggle beats OS). Pin localStorage key loops-theme; no-flash head
stamp before <style>. Route Group A rgba tints through four -rgb companion
tokens; body dark backdrop overridden so paper-on-desk does not invert.
Amend INTERFACES §10 Style; add DarkModeTokensTests + ThemeToggleTests.
```
