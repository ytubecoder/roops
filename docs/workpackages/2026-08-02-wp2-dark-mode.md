# WP2 — Dark mode (garden dashboard) — 2026-08-02

Work package 2 of 3 in the garden three-tier reorg. Umbrella design (read it first,
it is the shared context): `docs/superpowers/specs/2026-08-02-garden-three-tier-design.md`.
Its "Settled decisions" (§3) and "Shared token contract" (§4) are **pinned — do not
relitigate**. WP1's spec, `docs/workpackages/2026-08-02-wp1-garden-reorg.md`, is this
file's sibling — match its skeleton and precision; this doc stands alone the same way.

## Context (cold start)

You are working in the loops harness (`docs/INTERFACES.md` is the frozen mechanical
contract; §10 governs `dashboard/generate.py`). WP1 restructured the garden dashboard
into an accordion (`<details class="loop-row" name="garden">`) and retired
`dashboard/reports.html`. WP2 does not touch that structure — it adds a second value
set for the existing CSS custom-property tokens (light stays exactly as today; dark is
new) plus a small toggle so a viewer can pick light/dark explicitly instead of only
following the OS preference. Scope is `dashboard/loops.html` output only — report pages
(`pagekit/kit.css`, `reports/<name>/*.html`) are WP3's job, not this one.

**Line-number caveat:** the numbers below are from `main` as of 2026-08-02, **before**
WP1 has landed on this checkout (WP1 had not merged at the time this spec was written).
By the time you start WP2, WP1's restructuring will have shifted every line number in
`dashboard/generate.py`. Re-locate everything via grep, not by trusting a line number:

- `CSS = """` / `:root {` — the token block (currently ~line 689–701 in pre-WP1
  `generate.py`; contains `color-scheme: light;` plus the 12 role tokens).
- `DASHBOARD_JS = """` — the inline JS blob (currently ~line 1174–1182, holds
  `loopsFilterByTag`; WP1 adds the deep-link script here too).
- `_render_page` — topstrip assembly (currently ~line 2177; emits the `.topstrip` div
  with `seal-mini`, `h1`, `.head-stats` chips). By the time WP2 runs, WP1 will have
  already removed the `<a href="reports.html">reports</a>` chip from here.
- `_wrap_html` — currently ~line 2481; emits `<!doctype html><html><head>...<style>{CSS}
  </style></head><body>...<script>{DASHBOARD_JS}</script></body></html>`. This is
  where the new no-flash head script goes.
- `docs/INTERFACES.md` §10 (~line 1001), specifically the "Style" bullet (~line 1126)
  amended 2026-07-30 for B-04/B-07 — WP2 amends it again.

## Scope

`dashboard/generate.py` (the `CSS` string's token block + a handful of component
selectors identified below, the topstrip toggle control, `DASHBOARD_JS`, `_wrap_html`'s
head script), `tests/test_dashboard.py` (new tests), `docs/INTERFACES.md` (§10
amendment, same change). Nothing else.

## Non-goals

- **No `pagekit/kit.css` changes, no report-page re-rendering, no `tests/
  test_token_drift.py`** — all WP3. This WP's own new test (token-name-set parity
  between light and dark) is the *forerunner* WP3's drift test builds on, not the same
  test.
- **No structural/markup changes beyond the toggle control** — WP1 owns the accordion,
  the report block, the English glosses, the deep-link JS. Don't touch them beyond
  what's needed to add the toggle button.
- No `site/` changes (published mock pages are out of scope for real dark-mode work).
- No new role tokens beyond the 13 pinned in the umbrella's §4 table (12 pre-existing
  + `--nibi-faint`, resolved 2026-08-02 during spec review). New *companion* tokens
  are allowed — exactly four, listed in this spec (§ "Literal-value sweep" below) —
  and no others.
- No recency sort, no print-mode expand-all — still parked per the umbrella, untouched
  here.
- Screenshots / visual QA are **not** this WP's job — see Definition of Done.

## Preconditions

WP2 requires WP1 merged. Verify before starting:

```sh
grep -n '<details class="loop-row" name="garden"' dashboard/generate.py   # matches
grep -n 'reports\.html' dashboard/generate.py bin/console.py              # no live refs
bash tests/run-tests.sh                                                    # green on base
```

If the first grep finds nothing, WP1 hasn't landed yet — stop and say so; do not start
WP2 against pre-WP1 structure.

## Settled decisions (from the umbrella — repeated here so this file stands alone)

1. **Mechanism** (umbrella §3.5, pinned): `color-scheme` tracks the active mode;
   `@media (prefers-color-scheme: dark)` redefines the `:root` tokens as the
   OS-driven default; explicit `:root[data-theme="dark"]` / `:root[data-theme="light"]`
   blocks override in **both** directions (toggle beats OS preference — this falls out
   of CSS specificity alone: `:root[data-theme=...]` has higher specificity than plain
   `:root`, in or out of a media query, so it always wins regardless of source order —
   no `:not()` tricks needed anywhere in this WP). A small toggle control sits in the
   topstrip. A few lines of inline JS persist the choice to `localStorage` and stamp
   `data-theme` on `<html>` **before first paint** (a synchronous inline script placed
   ahead of `<style>{CSS}</style>` in `<head>` — this is what prevents the flash; do not
   defer it, do not put it in `DASHBOARD_JS` at the end of `<body>`). Zero network
   anything, per §10.
2. **Pinned identifiers — WP3 reuses these verbatim on report pages:**
   - localStorage key: **`loops-theme`**
   - attribute: **`data-theme`** on `<html>` (i.e. `document.documentElement`)
   - values: **`"dark"`** / **`"light"`** (lowercase strings; absence of the attribute
     means "follow OS preference," never a third stored value)
   Known limitation, not a WP2 blocker: cross-page persistence via a shared
   `localStorage` key assumes pages share an origin. That holds for the console's HTTP
   serving (`bin/console.py`) and generally for `file://` access in the browsers this
   fleet is viewed in, but `file://` origin/localStorage scoping is browser-dependent.
   WP3 verifies the actual cross-page carryover when it wires report pages to the same
   key; WP2 just needs the key/attribute pinned and documented so WP3 has something to
   match.
3. **Light values stay byte-identical** — this binds the **12 existing named role
   tokens'** values, not the literal formatting of the `:root` block. The parseability
   rule (below) requires reflowing the existing crammed-multiple-per-line `:root` block
   to one declaration per line; that is a formatting-only change (source bytes move,
   computed values do not) and is required, not optional. The new
   `TestGenerateOutput`-style test asserts the *values*, not the block's raw text, so
   this reflow cannot regress it.
4. **Parseability (settled, umbrella §5):** every token block — light `:root`, the
   `@media (prefers-color-scheme: dark) { :root {...} }` block, and both
   `:root[data-theme="dark"|"light"]` blocks — keeps exactly one `--token: value;`
   declaration per line. This is what lets WP3's `tests/test_token_drift.py` parse both
   `loops.html` and `pagekit/kit.css` with a dumb regex instead of a CSS engine. Treat
   it as a hard requirement, gated by this WP's own new test (see Tests).
5. **Dark value table** (below) is the *starting point* — the implementer may tune
   individual values for contrast/legibility but must stay within the seed family given
   (e.g. `--ai` stays in the `#279a83` teal family; don't invent an unrelated hue).
   Record whatever final values you ship in `PEON_REPORT.md`, even if unchanged from
   this table.
6. **No hex literals exist outside the token block** (verified 2026-08-02 by scanning
   the `CSS` string for `#[0-9a-fA-F]{3,8}` outside `:root {...}` — zero matches). **RGBA
   literals do exist outside the token block** — 13 occurrences across 11 selectors,
   verified by the same scan for `rgba?\([^)]*\)`. All 13 are enumerated below with a
   disposition for each; this list is exhaustive for `dashboard/generate.py`'s `CSS`
   string as of 2026-08-02 pre-WP1. If WP1 introduced new component rules with literal
   colors, re-run the scan and extend the table; say so in `PEON_REPORT.md`.
7. **Spot-check list** (not an exhaustive rule audit): hanko stamps (済/注/警/未),
   stale/running/died badges, findings severity left-border colors
   (`.finding[data-sev]`), and the tokonoma (`.toko`) inset shadow. These are named
   because they're the highest-density color usage on the page; give them a visual
   glance in both modes before calling the work done (full screenshot QA is the
   foreman's job — see Definition of Done — but a quick self-check catches the obvious
   breaks before that review).

## Concrete dark value table

Thirteen role tokens (12 pre-existing + `--nibi-faint`), seeded from the umbrella's §4
table (the kagi-ban report page palette). Where the umbrella gave an exact hex (washi,
washi-shade, sumi, shu, hair, hair2, nibi-faint — direct kagi-ban reuse), it's copied
verbatim below. Where the umbrella said "WP2 decides" (`--ai`, `--koke`, `--sumi-deep`)
or left ambiguity (`--nibi`, `--ochre` family, `--shu-deep` family), this table makes
the call.

| Token | Light (unchanged) | Dark | Rationale |
|---|---|---|---|
| `--washi` | `#F2EDE3` | `#0E0F12` | Exact reuse of kagi-ban's `--bg` — same role (paper/page surface). |
| `--washi-shade` | `#E9E2D3` | `#14161A` | Exact reuse of kagi-ban's `--panel` — same role (secondary surface). |
| `--sumi` | `#1C1A17` | `#E7E9EC` | Exact reuse of kagi-ban's `--ink` — same role (primary text). |
| `--sumi-deep` | `#16130F` | `#F4F5F7` | Umbrella-pinned "near-white step above sumi's dark value" for the max-emphasis ink tier. **See the flagged tension below before shipping this one blind.** |
| `--shu` | `#C73E2B` | `#D84F63` | Exact reuse of kagi-ban's `--high` — same role (alert/danger). |
| `--shu-deep` | `#A93321` | `#B84354` | ~15% darken of dark `--shu`, mirroring light mode's own `--shu`→`--shu-deep` ratio. Currently unused in `loops.html` (`grep -n -- '--shu-deep' dashboard/generate.py` shows only the declaration) but kept for token-set parity with WP3's `kit.css`. |
| `--ai` | `#2E4A5B` | `#279A83` | Umbrella-pinned teal family; exact reuse of kagi-ban's `--accent` — same role (link/accent). |
| `--nibi` | `#8C8578` | `#9AA1AB` | Kagi-ban's *lighter* muted tier (`--sub`). `--nibi` labels small 9–11px mono text throughout the page; the lighter tier keeps it legible at that size. |
| `--nibi-faint` | `#ABA495` | `#5D6570` | **New 13th role token (resolved 2026-08-02, spec review — settled).** Kagi-ban's deeper muted tier (`--mut`). No garden rule consumes it yet — declare it in all four token blocks anyway: WP3 maps kit.css's `--mut` → `--nibi-faint` and the drift test requires set parity. Do not wire it into any garden selector in this WP. |
| `--koke` | `#6B7A5C` | `#8FA97A` | Lightened + desaturated moss/sage, same hue family, for contrast against the dark `--washi`/`--washi-shade` surfaces. Kagi-ban has no direct "ok/moss" equivalent to copy. |
| `--ochre` | `#A87A2A` | `#B48C1A` | Exact reuse of kagi-ban's `--med` — same role (watch/amber). |
| `--hair` | `rgba(28,26,23,.14)` | `#22252B` | Exact reuse of kagi-ban's `--line`. Note the *type* change (alpha-over-paper → flat hex): a translucent dark rule reads far too faint on a dark surface, so the dark seed is deliberately solid, matching kagi-ban's own approach. |
| `--hair2` | `rgba(28,26,23,.22)` | `#2C3037` | Exact reuse of kagi-ban's `--line2`, same reasoning as `--hair`. |

**Flagged tension — `--sumi-deep` vs. `body`'s backdrop:** `--sumi-deep`'s *only*
current consumer in `loops.html` is `body { background: var(--sumi-deep); ... }` — the
page backdrop visible around the `.sheet` paper, not text/ink. The umbrella's pinned
direction ("near-white step above sumi's dark value") is written for an ink/emphasis
role; applying it literally to `body`'s backdrop means the margin around the
near-black paper goes bright in dark mode — the inverse of light mode's "near-black
backdrop, pale paper" relationship. Ship `--sumi-deep` itself at the pinned near-white
value (don't retarget the token — a future WP may give it a real text/ink use where the
umbrella's direction is exactly right). If the resulting backdrop looks broken rather
than intentional when you actually look at it, the scoped fix is to override **only**
`body`'s dark-mode `background` declaration to a literal near-black value inside the
dark blocks (do not change what `--sumi-deep` resolves to) — record whichever way you
went and why in `PEON_REPORT.md`. This is exactly the kind of call the foreman's
post-merge screenshot pass (Definition of Done) exists to catch either way.

Font tokens (`--serif`, `--mono`) are mode-independent — do not touch them.

## Literal-value sweep (rgba() outside the token block)

Verified 2026-08-02 against pre-WP1 `dashboard/generate.py`'s `CSS` string. Two groups:

**Group A — tinted with a role token's own RGB, needs a dark-mode-correct value.**
Route these through four new *companion* tokens (not new role tokens — plain
`R,G,B` triplets, so each site keeps its own existing alpha untouched and only the base
RGB swaps by mode via the normal cascade). This is the one place this WP adds new
custom properties; these four and no others:

| New token | Light | Dark | Mirrors |
|---|---|---|---|
| `--sumi-rgb` | `28,26,23` | `231,233,236` | `--sumi` |
| `--shu-rgb` | `199,62,43` | `216,79,99` | `--shu` |
| `--ai-rgb` | `46,74,91` | `39,154,131` | `--ai` |
| `--nibi-rgb` | `140,133,120` | `154,161,171` | `--nibi` |

Consuming selectors (rewrite each to `rgba(var(--x-rgb), <same alpha as today>)` —
**one-time, unconditional edit; these selectors do NOT get duplicated into the dark
blocks** — only the four `-rgb` tokens above do, alongside the 12 role tokens, in all
four token blocks):

| Selector | Today (literal) | Rewrite to |
|---|---|---|
| `.loop-row:hover` | `background: rgba(28,26,23,.035);` | `background: rgba(var(--sumi-rgb),.035);` |
| `.finding .cmd` | `background: rgba(28,26,23,.05);` | `background: rgba(var(--sumi-rgb),.05);` |
| `.hanko-btn:hover` | `background: rgba(199,62,43,.08);` | `background: rgba(var(--shu-rgb),.08);` |
| `details.handoff` | `background: rgba(199,62,43,.05);` | `background: rgba(var(--shu-rgb),.05);` |
| `.tags .tag` | `background: rgba(46,74,91,.06);` | `background: rgba(var(--ai-rgb),.06);` |
| `.toko-scroll` | `scrollbar-color: rgba(140,133,120,.3) transparent;` | `scrollbar-color: rgba(var(--nibi-rgb),.3) transparent;` |
| `.toko-scroll::-webkit-scrollbar-thumb` | `background: rgba(140,133,120,.28);` | `background: rgba(var(--nibi-rgb),.28);` |

Computed value in light mode is byte-for-byte identical before and after this rewrite
(same numbers, just sourced from a token) — this does not violate "light stays
byte-identical" for the 12 role tokens, and it's the mechanism, not a component-rule
duplication, that makes these seven sites correct in dark mode automatically.

**Group B — mode-neutral ambient effects (pure black/white with alpha). Leave as
literal, unchanged.** These are shadow/sheen effects whose direction (darken vs.
lighten) is inherent to the effect, not tied to a themed surface color, so they don't
need a dark-mode counterpart:

| Selector | Literal | Why left alone |
|---|---|---|
| `.sheet` | `box-shadow: 0 1px 2px rgba(0,0,0,.5), 0 24px 80px -24px rgba(0,0,0,.8);` | Ambient drop shadow under the paper; pure black works against any backdrop. |
| `.garden` | `background: rgba(255,255,255,.25);` | Sheen wash; part of the garden-zone spot-check. |
| `.toko` | `box-shadow: inset 0 1px 3px -2px rgba(28,26,23,.55), inset 0 -1px 0 rgba(255,255,255,.4);` | Sunken inset look on the tokonoma alcove — **on the named spot-check list**, glance at it in dark before calling this done. |
| `.badge.no-page` | `background: rgba(255,255,255,.2);` | Sheen wash behind a dashed badge — on the named spot-check list (badges). |
| `.sched-panel` | `box-shadow: 0 10px 30px -12px rgba(0,0,0,.4);` | Console-only dropdown shadow (gated `html.console-active`), pure black. |
| `#recent-events` | `background: rgba(255,255,255,.15);` | Sheen wash separating the fleet events strip; low visual priority. |

## Tasks

1. Add the `@media (prefers-color-scheme: dark) { :root { ... } }` block immediately
   after the existing `:root` block (source order matters for the OS-default case;
   see settled decision 1 for why the explicit-override blocks don't care about order).
   Set `color-scheme: dark;` inside it. Populate it with the 13 role tokens' dark values
   plus the 4 new `-rgb` companion tokens, one `--token: value;` per line.
2. Add `:root[data-theme="dark"] { ... }` (same values as the media block, same
   one-per-line rule, `color-scheme: dark;`) and `:root[data-theme="light"] { ... }`
   (the 13 light values plus the 4 companion tokens' light values, `color-scheme:
   light;`) — placed after the media block. Order between the two `[data-theme]` blocks
   doesn't matter (mutually exclusive attribute values).
3. Reflow the existing light `:root` block to one `--token: value;` declaration per
   line (existing values byte-identical; only line breaks move) per settled decision
   3/4, and add the `--nibi-faint: #ABA495;` and 4 companion declarations to it. The
   font tokens (`--serif`, `--mono`) stay in this base `:root` block ONLY — they are
   mode-independent and are NOT repeated in the dark/`[data-theme]` blocks.
4. Apply the Group A rewrites (7 selectors → `var(--x-rgb)`, alphas untouched) per the
   sweep table. Leave Group B untouched.
5. Add the toggle control to the topstrip: a real `<button>` (not a div — keep it
   keyboard-reachable), e.g. `<button type="button" id="theme-toggle"
   onclick="loopsToggleTheme()" title="toggle light/dark" aria-label="toggle color
   theme">◐</button>`, placed as the last item in `.head-stats` (after WP1's topstrip
   content — by the time you're working, the retired-in-WP1 "reports" chip is already
   gone, so there's no chip to displace). Style it minimally with existing tokens
   (`--hair2` border, `--nibi` color/hover-color, transparent background) — no new
   colors needed for the control itself.
6. Add `loopsToggleTheme()` to `DASHBOARD_JS` (end of `<body>`, same as
   `loopsFilterByTag` today — the toggle's click handler doesn't need to run before
   paint, only the initial stamp does):
   ```js
   function loopsToggleTheme() {
     var root = document.documentElement;
     var current = root.getAttribute('data-theme');
     if (!current) {
       current = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
     }
     var next = current === 'dark' ? 'light' : 'dark';
     root.setAttribute('data-theme', next);
     try { localStorage.setItem('loops-theme', next); } catch (e) {}
   }
   ```
7. Add the no-flash head-stamp script in `_wrap_html`, as an inline `<script>` placed
   **before** `<style>{CSS}</style>` (immediately after the charset meta tag is fine):
   ```html
   <script>(function(){try{var t=localStorage.getItem('loops-theme');if(t==='dark'||t==='light'){document.documentElement.setAttribute('data-theme',t);}}catch(e){}})();</script>
   ```
   Both `localStorage` calls (here and in `loopsToggleTheme`) are wrapped in
   `try/catch` — `localStorage` can throw under some `file://`/private-browsing
   conditions, and the page must never crash over a theme preference.
8. Amend `docs/INTERFACES.md` §10 **in the same change**: extend the "Style" bullet
   (~line 1126, amended 2026-07-30 for B-04/B-07) with an **(Amendment 2026-08-02,
   WP2)** clause covering: the second token-value set (media default + explicit
   `[data-theme]` override, toggle beats OS preference), the pinned `localStorage`
   key `loops-theme` / attribute `data-theme` / values `dark`/`light` (note WP3 reuses
   them verbatim), the no-flash head-stamp mechanism, and an explicit restatement that
   the toggle/persistence JS is pure client-side with zero new network surface — the
   §10 hermetic rule binds this addition exactly as it binds everything else in the
   file.

## Tests

Existing suites that must stay green: `bash tests/run-tests.sh` (the pass bar),
including `tests/html_selfcontained.py`-based coverage of `loops.html` — the new head
script and toggle JS must not introduce anything fetched on load (no `fetch`, no
`XMLHttpRequest`, no remote `url()`/`src`; `localStorage` and `matchMedia` are local
APIs, not network).

New tests in `tests/test_dashboard.py` (indicative names — follow the file's actual
class-naming convention, e.g. `PureFunctionTests`, `ReportPagesDashboardTests`, i.e.
`FooTests`, not `TestFoo`):

- `DarkModeTokensTests`:
  - `@media (prefers-color-scheme: dark)` block present, contains a `:root {...}` with
    `color-scheme: dark` and all 13 role tokens plus the 4 `-rgb` companions;
  - `:root[data-theme="dark"]` block present, same token set;
  - `:root[data-theme="light"]` block present, same token set;
  - light `:root`'s 12 pre-existing role token values are byte-exact matches for the
    umbrella's light column (assert the literal hex/rgba strings, e.g.
    `--washi: #F2EDE3;`), and `--nibi-faint: #ABA495;` is present (new token);
  - token-block parseability: a regex extracting `--([\w-]+):\s*([^;]+);` pairs from
    each of the four blocks yields the **same set of token names** across all four
    (light `:root`, dark media, `[data-theme="dark"]`, `[data-theme="light"]`) —
    **after excluding the font tokens** `--serif`/`--mono`, which by design appear
    only in the base `:root` (mode-independent). This is the forerunner of WP3's
    `tests/test_token_drift.py`, not that test itself.
- `ThemeToggleTests`:
  - `<button id="theme-toggle"` present inside the topstrip markup;
  - `loopsToggleTheme` function present in the rendered `<script>` output;
  - the head-stamp script is present and appears **before** `<style>` in the rendered
    HTML (string-index comparison: `html.index('data-theme') < html.index('<style>')`
    or equivalent);
  - the pinned key string `'loops-theme'` appears in both the head-stamp script and
    `loopsToggleTheme` (asserts the exact string, not just "a" localStorage call).

Run the new tests plus the full suite; paste both outputs into `PEON_REPORT.md`.

## Definition of Done

- [ ] Both palettes present in `loops.html`'s output: light `:root` unchanged, dark
      media block, both `[data-theme]` override blocks, all one-token-per-line.
- [ ] Toggle control in the topstrip; OS-preference default respected when no explicit
      choice is stored; explicit choice overrides OS preference in both directions and
      persists across reloads (via `localStorage['loops-theme']`).
- [ ] No flash of the wrong theme on load — the head-stamp script runs before
      `<style>{CSS}</style>`, stamping `data-theme` before first paint.
- [ ] Group A literal-color sweep applied (7 selectors routed through the 4 new `-rgb`
      companion tokens); Group B left as documented mode-neutral literals.
- [ ] `--sumi-deep`/`body`-backdrop tension resolved one way or the other, documented
      in `PEON_REPORT.md`.
- [ ] `docs/INTERFACES.md` §10 "Style" bullet amended in the same change, per Task 8.
- [ ] New tests added and green; `bash tests/run-tests.sh` fully green — both outputs
      pasted into `PEON_REPORT.md`.
- [ ] `PEON_REPORT.md` written: what changed, final dark values actually shipped (even
      if unchanged from this spec's table), test evidence, any deviations.
- [ ] **Not this WP's job:** actual screenshots of both modes. That's the **foreman's**
      post-merge verification step (Playwright MCP, both `prefers-color-scheme` states
      and both explicit toggle states) — the implementer does not need a browser to
      finish this work, only the tests above.

## Verification commands

```sh
bash tests/run-tests.sh                                              # full suite — the pass bar
python3 dashboard/generate.py --root "$PWD"                          # regenerates dashboard/loops.html
grep -c '@media (prefers-color-scheme: dark)' dashboard/loops.html   # == 1
grep -c 'data-theme="dark"' dashboard/loops.html                     # >= 1 (CSS block + JS)
grep -c 'data-theme="light"' dashboard/loops.html                    # >= 1 (CSS block)
grep -n "localStorage.*loops-theme" dashboard/loops.html             # head script + toggle fn, 2 hits
grep -c -- '--sumi:' dashboard/loops.html                            # == 4 (one per token block)
grep -c -- '--sumi-rgb:' dashboard/loops.html                        # == 4
grep -c -- '--nibi-faint:' dashboard/loops.html                      # == 4
grep -c -- '--serif:' dashboard/loops.html                           # == 1 (base :root only)
python3 -c "
h = open('dashboard/loops.html').read()
assert h.index('data-theme') < h.index('<style>'), 'head-stamp script must precede <style>'
print('ok: head-stamp precedes style')
"
```

## Constraints & gotchas

- **§10 hermetic rules bind**: the generated page fetches NOTHING on load — no
  webfonts, no CDN, no remote `url()`/`src`. `localStorage` and `matchMedia` are local
  browser APIs, not network calls, and are fine. Navigation `<a href>` is fine (not
  used here, but noted for consistency with WP1's constraints). `tests/
  html_selfcontained.py` enforces this; keep `loops.html` covered by it.
- **INTERFACES amendment ships in the same change as the code** — never drift.
- **Shared checkout / worktree**: you are in an isolated worktree; commit there. If
  `git commit` fails in your sandbox, leave the working tree clean and state it in
  `PEON_REPORT.md` — do **not** improvise ssh remotes, standalone `.git` dirs, or
  other escapes.
- **Ruff hook**: the repo carries pre-existing lint debt and the hook uses a broad
  ruleset — fix findings only in files you edited, leave the rest.
- **macOS**: no `flock`, no GNU `timeout` — don't add either to tests or scripts.
- **Scope guards**: no `pagekit/kit.css` changes, no report-page re-rendering, no
  `site/` changes, no structural/markup changes beyond the toggle button (WP1 owns
  structure — accordion, report block, English glosses, deep-link JS are all off
  limits here). Don't touch `bin/redact.py`, `bin/page_envelope.py`.
- Don't invent new role tokens beyond the 13 pinned in the umbrella's §4 table, and
  don't invent companion tokens beyond the 4 `-rgb` triplets listed in this spec — if
  you find a literal color this spec's sweep missed, add a new row to the sweep table
  in `PEON_REPORT.md` and route it through an *existing* token/companion if at all
  possible before reaching for a new one.
- The dashboard is opened both via HTTP and as `file://` — nothing in this WP changes
  that; `localStorage` cross-page scoping under `file://` is a known, documented
  limitation (see settled decision 2), not something to solve here.
