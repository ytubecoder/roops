# Garden three-tier reorg — umbrella design (2026-08-02)

Status: **shipped 2026-08-02, live** (all three WPs merged same day — see §8). Every
decision in this document was made interactively with the generalissimo on 2026-08-02 —
**do not relitigate any of them**.
Implementation is split into three work packages under `docs/workpackages/`, executed
strictly in sequence by farmed-out peon agents. Each WP MD is a self-contained warmstart;
this document is the shared context they all point back to.

`docs/INTERFACES.md` remains the frozen mechanical authority. Each WP ships its
INTERFACES amendment **in the same change** as its code — this doc never substitutes
for that.

## 1. Problem

The garden dashboard (`dashboard/loops.html`, served at
`https://loops.example.ts.net/`) shows the garden rows **and** nine full per-loop
sections (Findings + Recent runs) stacked below them — a wall of detail on what should
be a glance surface. A separate `/reports.html` screen lists long-form report pages in a
completely different design system (dark teal), splitting the index in two. Kanji carry
meaning with no adjacent English. There is no dark mode anywhere, and report pages
(`pagekit/kit.css`) share no tokens with the garden.

## 2. The three-tier model (settled)

| Tier | Surface | Purpose |
|---|---|---|
| **Short** | Garden row (`<summary>` of the row's `<details>`) | Glance: stamp, name, schedule, tokonoma headline, run-meta, rounds switch |
| **Medium** | Inline accordion expansion of that row | Uniform harness view: findings, recent runs, panels, report block |
| **Long** | The loop's own report page `/reports/<name>/latest.html` | Park-on-a-monitor view; exists only for page-enabled loops |

The medium tier **points** at the long tier (links), it never reproduces it. The garden
is the sole index: `/reports.html` is retired outright.

## 3. Settled decisions

1. **Three-tier model** as above.
2. **`/reports.html` retired.** `generate.py` stops emitting it; the console drops the
   route; the topstrip "reports" chip goes. Report pages themselves
   (`/reports/<name>/*.html|.md|.json`) remain served. Dated history links move into
   each loop's accordion report block.
3. **Accordion = native `<details name="garden">`** — one open at a time via the shared
   `name` attribute. Only ~10 lines of inline JS for deep-links (`#loop-<name>` opens
   that row on load + `hashchange`). No framework, nothing fetched — §10 hermetic rules
   bind exactly as before.
4. **English labels beside every meaning-bearing kanji** — a tiny muted English word
   (`<span class="en">…</span>`) beside stamps (済/注/警/未), topstrip chips, 巡 in
   run-meta, the 巡/休/手 switch, and finding hanko/disposition marks. Kanji already
   adjacent to English (庭 kicker, 床の間 note) stay as-is. Tooltips stay. Wording is
   pinned in WP1's table (sourced from existing `title` attributes / §10 vocabulary).
   Exception: **承 gets no English gloss** — the natural word is banned page-wide by
   test (ack ≠ approval doctrine, INTERFACES §10).
5. **Light mode stays byte-identical in intent** — today's palette values are canonical.
   Dark mode is added as a standard: `prefers-color-scheme` default +
   `:root[data-theme="dark"|"light"]` override toggle persisted in `localStorage`
   (the standard artifact/theme pattern). Dark palette **seeded from the kagi-ban
   report page** (§4 below).
6. **One design system, two modes**: garden role tokens are canonical;
   `pagekit/kit.css` is rebuilt on the same tokens so report pages match the garden in
   both modes. Anti-drift is enforced by a test (§5).
7. **Parked, not spec'd** (recorded for later, out of scope for all three WPs):
   - Recency-sort toggle on the garden (rows reorder by last run, subtle animation) —
     for when the fleet grows.
   - Orphaned report directories (pages on disk with no `loops.d/<name>` entry — today
     only the `hello-denied` demo) lose their index entry when `/reports.html` retires.
     They remain directly servable by URL. Accepted; revisit only if a real loop is
     ever retired with history worth indexing.
   - Print-mode expand-all: CSS cannot open `<details>`; printing shows collapsed rows.
     Accepted limitation.

## 4. Shared token contract (pinned)

Role tokens keep their existing names. Light values are **today's garden `:root`,
unchanged**; dark seeds come from the kagi-ban page palette (`reports/kagi-ban/latest.html`
`:root`). Both source palettes below were extracted and verified against the live files
on 2026-08-02. Fine mapping/contrast tuning within the seed families is **WP2's job**;
WP2 records its final dark values in its own MD when done, and WP3 copies them into
`pagekit/kit.css` exactly.

| Token (role) | Light (today, unchanged) | Dark seed (kagi-ban) |
|---|---|---|
| `--washi` (paper/bg) | `#F2EDE3` | `#0e0f12` |
| `--washi-shade` (panel) | `#E9E2D3` | `#14161a` |
| `--sumi` (ink) | `#1C1A17` | `#e7e9ec` |
| `--sumi-deep` | `#16130F` | a near-white step above `--sumi`'s dark value |
| `--shu` (alert red) | `#C73E2B` | `#d84f63` family |
| `--shu-deep` | `#A93321` | `#d84f63` family, deeper step |
| `--ai` (indigo accent) | `#2E4A5B` | teal `#279a83` family (WP2 decides mapping) |
| `--nibi` (muted) | `#8C8578` | `#9aa1ab` |
| `--nibi-faint` (muted, deeper step — **new token**, resolved 2026-08-02) | `#ABA495` (faint step in the nibi family; no garden rule uses it yet, so garden light pixels are unchanged) | `#5d6570` |
| `--koke` (moss/ok) | `#6B7A5C` | dark-adjusted moss (WP2 tunes) |
| `--ochre` (amber/watch) | `#A87A2A` | `#b48c1a` family |
| `--hair` (rule) | `rgba(28,26,23,.14)` | `#22252b` |
| `--hair2` (rule, stronger) | `rgba(28,26,23,.22)` | `#2c3037` |

Font tokens (`--serif`, `--mono`) are mode-independent and unchanged.

**Why `--nibi-faint` exists (resolved 2026-08-02, during spec review):** the kagi-ban
page carries two muted steps (`--sub` #9aa1ab and `--mut` #5d6570); the garden had one.
Collapsing both onto `--nibi` would flatten report-page text hierarchy, so the shared
contract gains exactly one token instead. WP2 declares it in both modes (it changes no
garden pixel — no garden rule consumes it yet); WP3 maps kit.css `--sub` → `--nibi` and
`--mut` → `--nibi-faint`. The drift test covers it like every other token.

## 5. Anti-drift rule

Token values will exist in two places once WP3 lands: inlined into `loops.html` by
`dashboard/generate.py`, and in `pagekit/kit.css`. WP3 adds
`tests/test_token_drift.py`, which parses both sources and asserts the token **sets and
values** are identical (light and dark). WP2 must keep its token blocks trivially
parseable (one `--token: value;` per declaration inside the `:root` /
`[data-theme]` blocks) so that test stays a dumb text parse, not a CSS engine.

Scope refinements (resolved 2026-08-02, spec review):

- **Font tokens** (`--serif`, `--mono`) are mode-independent — declared in the base
  `:root` only, in both files; compared cross-file as part of the light set, excluded
  from the dark set.
- **Companion `-rgb` tokens** (WP2's literal-value sweep adds exactly four:
  `--sumi-rgb`, `--shu-rgb`, `--ai-rgb`, `--nibi-rgb`) are part of the contract and
  the drift test's full-set parity — kit.css declares them too, WP2's exact values.

## 6. Package sequencing

Strictly serial — each WP's preconditions section says how to verify the prior one
merged.

| Seq | WP | MD | Scope in one line |
|---|---|---|---|
| 1 | WP1 garden reorg | `docs/workpackages/2026-08-02-wp1-garden-reorg.md` | Accordion structure, per-loop sections move inside rows, report block, English labels, retire `reports.html` |
| 2 | WP2 dark mode | `docs/workpackages/2026-08-02-wp2-dark-mode.md` | Dark token values + theme toggle on `loops.html` only |
| 3 | WP3 pagekit unification | `docs/workpackages/2026-08-02-wp3-pagekit-unification.md` | Rebuild `kit.css` on shared tokens, both modes; `test_token_drift.py`; re-render kagi-ban |

## 7. Execution mode (how these ship)

Implementation is farmed to grok peons (isolated worktrees, `peon dispatch grok`,
foreman review, merge). Each WP MD is a **self-tested work unit**: it requires the peon
to add and run the specified tests and paste outputs into `PEON_REPORT.md`, so foreman
review is report + diff spot-check backed by test evidence. Known grok gotcha: grok
peons can't commit normally in worktrees (sandbox blocks the gitdir) and improvise
escapes — the foreman verifies the branch tip is base + expected commits before
merging.

Ticket Takeaway tracks each WP (loops project, one ticket per WP, referencing its MD
path): **B-14** (WP1), **B-15** (WP2), **B-16** (WP3) — backlog → wip at dispatch →
review at merge.

## 8. Shipped (as built) — 2026-08-02

All three WPs implemented by grok peons the same day the specs landed, foreman-reviewed
and merged serially: WP1 `2ef7434` (merge `fd7f256`), WP2 `4035c8d` (merge `3233e9d`),
WP3 `95614cd` (merge `b31b53d`). Full suite green on final main: 661 python + 367 shell
tests, EXIT=0. Live-verified: accordion + deep links + glosses, dark toggle with reload
persistence, report page on shared tokens, theme carryover garden↔report page.

Deltas vs. spec (all within sanctioned latitude):

- `--sumi-deep` ships near-white in dark per the contract, but `body`'s backdrop gets a
  scoped `#08090B` override in the dark blocks (WP2's documented escape hatch) so the
  paper-on-desk frame doesn't invert; `:root[data-theme="light"] body` restores it.
- Report-block md link is always labeled `latest.md` (was context-dependent `md`/`latest`
  in the old row).
- `render_page.py` severity color refs renamed `--high`/`--med` → `--shu`/`--ochre`
  (forced by the kit rename; findings logic untouched).
- The garden's dark body-frame override is deliberately NOT in kit.css — report pages
  use `var(--washi)` as the page surface itself (no sheet-on-desk chrome).
- `/reports.html` on the live host resolves to the dashboard via machine-local Caddy's
  existing `try_files` fallback (spec allowed 404 or fallback); the console itself 404s.

Token contract additions resolved during spec review (§4/§5 above already reflect them):
`--nibi-faint` (13th role token), the four `-rgb` companions, and the font-tokens-in-
base-`:root`-only rule.
