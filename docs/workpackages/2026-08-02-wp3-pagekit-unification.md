# WP3 — Pagekit unification (report-page kit) — 2026-08-02

Work package 3 of 3 in the garden three-tier reorg. Umbrella design (read it first, it
is the shared context): `docs/superpowers/specs/2026-08-02-garden-three-tier-design.md`.
Its "Settled decisions" (§3), "Shared token contract" (§4), and "Anti-drift rule" (§5)
sections are **settled — do not relitigate**. WP2's spec,
`docs/workpackages/2026-08-02-wp2-dark-mode.md`, pins the exact `localStorage` key and
`data-theme` attribute value strings used by the garden's toggle — **reuse exactly the
key and attribute WP2 pinned**; read that file at implementation time rather than
inventing a name here (it may be written after this MD; do not wait for it, but do not
guess its contents beyond what the umbrella already settles — see Settled decision 3
below for what is and isn't already fixed).

## Context (cold start)

You are working in the loops harness (`docs/INTERFACES.md` is the frozen mechanical
contract; §4.1 step 6.5 governs report-page rendering, §12 governs the envelope, §10
governs `dashboard/generate.py`). Report pages are one-off self-contained HTML files
rendered by a loop's `render.sh` (page-enabled loops only); today there is exactly one,
`loops.d/kagi-ban/`. Every report page is styled by reading `pagekit/kit.css` at render
time and inlining its body into a `<style>` block — never `<link>`ed, so pages stay
offline-safe. `pagekit/kit.css` is dark-only today (`html{color-scheme:dark}`, fixed
palette) and shares no tokens with the garden dashboard (`dashboard/generate.py`'s
inlined `:root` block, `--washi`/`--sumi`/`--shu`/etc.). WP1 restructured the garden's
accordion; WP2 (assumed merged — see Preconditions) added dark-mode token values and a
theme toggle to the garden. This WP makes report pages share the garden's exact token
values in both modes, with a toggle that persists the same choice across both surfaces.

Key code locations (line numbers approximate, from 2026-08-02 main, pre-WP2/WP3):

- `pagekit/kit.css` — the whole file is the `:root{...}` token block (lines 12–17) plus
  every rule that consumes those tokens. Full old→new mapping in Settled decision 1.
- `pagekit/README.md` — "Using the kit" section (lines 5–22) is the contract prose for
  how renderers consume `$PAGEKIT`; "The envelope" section (24–37) is the precedent for
  a documented copy-paste snippet (as opposed to a shared file) for small renderer-owned
  markup.
- `loops.d/kagi-ban/render_page.py` — `_PAGEKIT_DIR`/`load_kit_css()` (54–77, reads and
  inlines `kit.css`, fails loudly if missing per delta 7's comment at line 51–56); `PAGE`
  Template (305–380, the entire HTML shell including the existing tooltip `<script>` at
  365–377 — the precedent for renderer-owned inline JS chrome).
- `loops.d/kagi-ban/render.sh` — 5 lines, just execs `render_page.py` with
  `$OUT_DIR/scan.json`, no `$PAGEKIT`-adjacent logic of its own.
- `dashboard/generate.py` — pre-WP2 token block at `CSS = """` (689) → `:root {` (692–700,
  light values only, no dark block, no `data-theme`, no `prefers-color-scheme` — WP2 adds
  all three). Style bullet to amend: `docs/INTERFACES.md` §10 line ~1126 ("Style
  **(amended 2026-07-30, B-04/B-07)**...").
- Tests — `tests/test_kagi_ban.py` `PagekitSourcingTests` (lines 293–362, pins the
  read-and-inline contract, the missing-kit failure, and that a kit edit reaches the
  page — these must all stay green unchanged in behavior even though the token names
  inside `kit.css` change) and `PageSelfContainmentTests` (365–407); `tests/run-tests.sh`
  (`python3 -m unittest discover -s tests -p 'test_*.py'` — auto-discovers any
  `test_*.py` in `tests/`, no per-file registration); `tests/html_selfcontained.py` +
  `tests/test_html_selfcontained.py` (the scanner + its own non-vacuity self-test —
  mirror this convention, see Tests); `tests/test_runner_pages.sh`'s `seed_pagekit()`
  (line 20) only `touch`es an empty `kit.css` for hermetic runner-plumbing tests that use
  a synthetic stub renderer — it never reads real kit content, so it needs no changes for
  this WP.

## Scope

`pagekit/kit.css`, `pagekit/README.md`, `loops.d/kagi-ban/render_page.py` (toggle button
markup + `$PAGEKIT`-sourced JS wiring only — not the findings-rendering logic),
`pagekit/reference/reference-page.html` (regenerated, see Tasks — it is git-tracked,
unlike `reports/`), a new `pagekit/toggle.js`, `tests/test_token_drift.py`, a new
`tests/token_parser.py` (parsing helper) + `tests/test_token_parser.py` (its self-test),
`docs/INTERFACES.md` (§10 style-bullet amendment). Nothing else.

## Non-goals

- **No color/value invention.** `dashboard/generate.py`'s token block (as WP2 ships it)
  is canonical. If a value looks wrong or under-contrasted, flag it in `PEON_REPORT.md`
  — do not change `dashboard/generate.py`.
- **No typography rework.** `kit.css`'s body font stack (`-apple-system,
  BlinkMacSystemFont,"Helvetica Neue",sans-serif`) and its existing `var(--mono)` usages
  stay as they are. The font tokens (`--serif`, `--mono`) are added/realigned as CSS
  custom properties with the garden's exact values (Settled decision 1), but nothing in
  `kit.css` is rewired to use `var(--serif)` — the report page does not grow mincho
  headlines in this WP. If the result reads inconsistent next to the garden, say so in
  `PEON_REPORT.md`; do not redesign it.
- **No `bin/redact.py` changes**, no KV-neutralization changes — untouched, verify with
  `git status` showing it clean at the end.
- **No `dashboard/generate.py`, `bin/console.py`, or garden-structure changes** — that
  was WP1; token *values* there are WP2's; WP3 only *reads* them.
- **No `site/` changes.**
- **No new loop pages, no changes to which loops are page-enabled.**
- **No new `$PAGEKIT`-adjacent environment variable.** `pagekit/toggle.js` is read from
  the same `$PAGEKIT` directory `kit.css` already comes from — no `docs/INTERFACES.md`
  §4.1 step 6.5 env-var amendment is needed (verify this remains true once you've built
  it; if it turns out not to be, that's a real §4.1 amendment and must ship in this
  change, not be silently skipped).

## Preconditions

WP3 is last in the sequence. Verify before starting:

```sh
grep -n 'data-theme' dashboard/generate.py            # hits (WP2 landed)
grep -n 'prefers-color-scheme' dashboard/generate.py  # hits (WP2 landed)
bash tests/run-tests.sh                                # green on your base commit
```

If either grep is empty, WP2 has not merged — stop and say so rather than guessing its
token values or toggle mechanism.

## Settled decisions (from the umbrella — repeated here so this file stands alone)

1. **Old→new token mapping.** `kit.css`'s existing role names are replaced by the
   garden's role token names. Read the exact values from `dashboard/generate.py` as WP2
   ships them (below is the umbrella's §4 *seed* table, verified against pre-WP2 main on
   2026-08-02 — treat it as the shape of the mapping, not the literal dark values, which
   are WP2's to finalize):

   | kit.css today | → garden token | today's kit.css value | garden role |
   |---|---|---|---|
   | `--bg` | `--washi` | `#0e0f12` | paper/background |
   | `--panel` | `--washi-shade` | `#14161a` | panel |
   | `--line` | `--hair` | `#22252b` | rule |
   | `--line2` | `--hair2` | `#2c3037` | rule, stronger |
   | `--ink` | `--sumi` | `#e7e9ec` | ink |
   | `--sub` | `--nibi` | `#9aa1ab` | muted |
   | `--mut` | `--nibi-faint` | `#5d6570` | muted, deeper step |
   | `--accent` | `--ai` | `#279a83` | accent |
   | `--high` | `--shu` | `#d84f63` | alert red |
   | `--med` | `--ochre` | `#b48c1a` | amber/watch |
   | `--mono` | `--mono` | `ui-monospace,"SF Mono",Menlo,Consolas,monospace` | font (value changes — see below) |

   **`--sub`/`--mut` note (RESOLVED 2026-08-02, spec review — settled):** the shared
   contract gains one token, `--nibi-faint` (muted, deeper step; umbrella §4 has the
   values: light `#ABA495`, dark seed `#5d6570`). WP2 declares it in both modes even
   though no garden rule consumes it yet. Map kit.css `--sub` → `--nibi` and `--mut` →
   `--nibi-faint`; do NOT collapse the two-step distinction. If WP2's shipped
   `dashboard/generate.py` is missing `--nibi-faint`, that's a WP2 regression against
   the umbrella — stop and flag it in `PEON_REPORT.md`, don't invent values.

   **New tokens with no current `kit.css` equivalent** (must still be defined in the
   rebuilt blocks, even though no existing rule in `kit.css` consumes them yet —
   future-proofing and drift-test parity): `--koke` (moss/ok — success color; no report
   page renders one today), `--sumi-deep`, `--shu-deep`, `--serif` (new font token —
   garden's exact value; read WP2's actual string), **and the four `-rgb` companion
   tokens WP2 ships** (`--sumi-rgb`, `--shu-rgb`, `--ai-rgb`, `--nibi-rgb` — plain
   `R,G,B` triplets, both modes; declare them with WP2's exact values so the drift
   test's full-set parity holds).

   **Font-token placement rule (matches WP2):** `--serif` and `--mono` are
   mode-independent and live in the base `:root` block ONLY — never repeated in the
   dark media / `[data-theme]` blocks, in either file. The drift test compares them
   cross-file as part of the light set and excludes them from the dark set.

   **`--mono`'s value changes**, not just its name: today's `kit.css` string
   (`...Menlo,Consolas,monospace`) differs from the garden's (`...  "Cascadia Code",
   Menlo,monospace` — no Consolas, per `dashboard/generate.py` line 700 pre-WP2). Copy
   the garden's exact string.

2. **Mechanism**: same as the garden — `color-scheme` set appropriately, a
   `prefers-color-scheme: dark` media query supplying the dark values as the OS-driven
   default, and a `[data-theme="dark"|"light"]` attribute override that wins over the
   media query in both directions. Build this the same way WP2 built it in
   `dashboard/generate.py` (read its actual CSS structure once merged — do not assume a
   specific block layout beyond what the umbrella already fixes: media-query default +
   attribute override).

3. **Toggle control + persistence JS — pinned approach: a new `pagekit/toggle.js`,
   read via `$PAGEKIT` and inlined exactly like `kit.css`.** Two options were on the
   table (a static copy-paste snippet documented in `pagekit/README.md`, vs. wiring into
   the render path); a shared *file* wins because it is the pattern this repo already
   uses for exactly this problem — `pagekit/README.md`'s own words about `kit.css`
   apply verbatim: "Do not paste a copy into your renderer — a copy drifts, and the kit
   exists so a restyle reaches every page-enabled loop at once." A second hand-copied
   artifact (a pasted-in toggle script) would reintroduce the same drift risk `kit.css`
   was built to prevent, and today's `render_page.py` already inlines exactly one other
   piece of shared machinery this way. What IS renderer-owned, small enough to document
   as copy-paste text in `pagekit/README.md` (same tier as the envelope snippet, §"The
   envelope"): the toggle **button's** one-line HTML (e.g.
   `<button id="theme-toggle" type="button" aria-label="toggle theme">◐</button>`,
   placed near `.meta` in kagi-ban's header) — trivial boilerplate, not logic.
   `toggle.js` owns: reading the persisted choice from `localStorage` under WP2's exact
   key, applying `data-theme` on `<html>` before paint (avoid a flash of the wrong
   theme), and a click handler that flips it and persists. The `data-theme` attribute
   name itself is already fixed at the umbrella level (§3.5: `:root[data-theme="dark"|
   "light"]`) — not WP2's to invent. The `localStorage` KEY STRING is WP2's to pin; read
   it from `docs/workpackages/2026-08-02-wp2-dark-mode.md` (or, if that file is not yet
   written when you start, from WP2's actual landed `dashboard/generate.py` toggle JS —
   grep for `localStorage` there) and use the identical string, not a new one.
4. **Pipeline stays unchanged otherwise**: renderers read `$PAGEKIT/kit.css` and inline
   the body; missing `kit.css` still fails the render (`load_kit_css()`'s existing
   `SystemExit`, untested by this WP beyond staying green); `pagekit/toggle.js` is read
   and inlined the same way, added as a second file under the same `$PAGEKIT` directory
   (no new env var).
5. **`pagekit/reference/reference-page.html` is git-tracked** (unlike `reports/`, which
   is gitignored — verified via `git ls-files pagekit/` and `git check-ignore -v
   reports/kagi-ban/latest.html`) and is described by `pagekit/README.md` as "the
   quality benchmark." It embeds a frozen copy of `kit.css`'s old dark-only palette
   inline. It must be regenerated **in this change** (committed), using the exact
   command the original build task used (`docs/superpowers/plans/
   2026-07-30-report-pages.md`, "Run the renderer once against the fixture" step):

   ```sh
   python3 loops.d/kagi-ban/render_page.py pagekit/reference/fixture-scan.json \
     --loop kagi-ban --run-id reference --host fixture --av-version 0.0-stub \
     -o pagekit/reference/reference-page.html
   python3 bin/page_envelope.py check --file pagekit/reference/reference-page.html
   ```
6. **`reports/kagi-ban/latest.html` (and every other file under `reports/`) is
   gitignored — machine-local, never committed.** Re-rendering it is a **foreman**
   post-merge step (Verification commands), not the implementer's. The implementer's
   worktree won't have real `state/`/`reports/` data anyway (both gitignored, so a fresh
   `git worktree add` checkout starts without them) — the implementer's own build/visual
   verification uses the fixture, same command as decision 5 above (or a throwaway `-o`
   path), which is also exactly what `tests/test_kagi_ban.py` already exercises.

## Tasks

1. Confirm WP2's actual dark-mode CSS structure and toggle JS in `dashboard/generate.py`
   (Preconditions greps) — read the real block, don't assume. Note its exact
   `localStorage` key string and confirm the `data-theme` values used.
2. Rebuild `pagekit/kit.css`'s `:root`/`[data-theme]`/media-query blocks per Settled
   decisions 1–2: same token names as `dashboard/generate.py`, same light values, same
   dark values, same mechanism. Rewrite every existing rule in `kit.css` that referenced
   an old token name (`--bg`, `--panel`, `--line`, `--line2`, `--ink`, `--sub`, `--mut`,
   `--accent`, `--high`, `--med`) to the new name per the mapping table. Keep the header
   comment (lines 1–11) accurate — update the "Palette validated" line's hex values if
   they've moved, and the "single source" framing stays true.
3. Write `pagekit/toggle.js` per Settled decision 3: read persisted theme, apply
   `data-theme` pre-paint, wire a click handler on `#theme-toggle`, persist on change.
   No frameworks, plain script, small.
4. Update `pagekit/README.md`: document `toggle.js` alongside `kit.css` in "Using the
   kit" (read-and-inline rule applies to both now), add the copy-paste button markup
   snippet (Settled decision 3), and update the palette line (currently "Palette: high
   `#d84f63`, medium `#b48c1a`, accent `#279a83` on surface `#0e0f12`" — dark-only
   phrasing) to describe both modes using the new token names.
5. Update `loops.d/kagi-ban/render_page.py`'s `PAGE` template: add the toggle button
   markup near `.meta` in the header, add a second `$toggle_js` substitution alongside
   `$kit_css` (read via the same `_PAGEKIT_DIR` helper pattern as `load_kit_css()` — add
   a `load_toggle_js()` sibling function, same missing-file-fails-loudly behavior).
   Existing findings-rendering logic (`build_bars`, `build_groups`, `categorize`, KV
   neutralization) is untouched.
6. Regenerate `pagekit/reference/reference-page.html` (Settled decision 5's exact
   command) and commit it.
7. Add `tests/token_parser.py`: a small, dumb, regex-based line parser —
   `parse_root_block(css_text) -> dict[str, str]` for a `:root{...}` block's
   `--token: value;` lines (one per line, per the umbrella §5 mandate on WP2's format;
   if you find WP2 shipped multiple declarations per line, that's a WP2 regression
   against its own settled contract — flag it in `PEON_REPORT.md`, don't silently make
   the parser handle it), plus a light/dark extraction function that locates the
   `:root{...}` block (light) and the `[data-theme="dark"]`-scoped block (dark) in a
   larger CSS text, however WP2 nested them (media query, attribute selector, or both —
   read WP2's actual structure per Task 1 before writing this).
8. Add `tests/test_token_drift.py`: imports `token_parser`, parses
   `dashboard/generate.py`'s inlined CSS string (the authoritative source) and
   `pagekit/kit.css`, and asserts the light token-name **sets** are identical, the dark
   token-name **sets** are identical, and every token's **value** matches between the
   two files, for both modes. The light set includes the font tokens (`--serif`,
   `--mono` — base `:root` only in both files); the dark set excludes them (see the
   font-token placement rule in Settled decision 1). Companion `-rgb` tokens
   participate in both sets like any other token. One test method per axis (name-set
   light, name-set dark, values light, values dark) so a failure names which axis
   drifted.
9. Add `tests/test_token_parser.py` — the non-vacuity self-test for `token_parser.py`,
   mirroring `tests/test_html_selfcontained.py`'s convention (a truth-table pair: cases
   that must be caught, cases that must not). Feed synthetic `:root{...}`/
   `[data-theme="dark"]{...}` snippets: (a) two identical blocks → the drift-comparison
   function reports no diff; (b) a block missing one token → reports it as missing; (c) a
   block with one token's value changed → reports it as a value mismatch, not a false
   "identical." This proves `test_token_drift.py` isn't checking a parser that always
   returns "no drift."
10. Amend `docs/INTERFACES.md` §10's style bullet (~line 1126, "Style **(amended
    2026-07-30, B-04/B-07)**...") **in the same change**: append a clause that
    `pagekit/kit.css` now shares the garden's exact role + font tokens in both modes
    (name the anti-drift test), and that report pages carry the same theme toggle,
    persisted via the same `localStorage` key, so a viewer's choice carries between the
    garden and any report page.
11. Run the full suite plus the new tests; paste both outputs into `PEON_REPORT.md`
    (see Tests).

## Tests

Existing suites that must stay green, unchanged in behavior:
`tests/test_kagi_ban.py`'s `PagekitSourcingTests` (the read-and-inline contract, the
missing-kit-fails-render behavior, the kit-edit-reaches-the-page proof — none of these
care about token *names*, only that `kit.css` is read/inlined verbatim, so they should
pass with zero changes) and `PageSelfContainmentTests` (the new toggle button/script must
not introduce any fetching markup — `assert_self_contained` covers this automatically
once the template changes). `tests/html_selfcontained.py`-based coverage of report pages
generally. `tests/test_page_envelope.py` and `tests/test_runner_pages.sh` — unaffected by
a CSS/JS content change, confirm green, do not edit unless a real regression appears
(`seed_pagekit`'s empty-file stub means these don't need a `toggle.js` fixture, per
Context above).

New tests (Tasks 8–9 have the detail; summarized here):

- `tests/test_token_drift.py::TestTokenDrift` — light name-set match, dark name-set
  match, light value match, dark value match, comparing `dashboard/generate.py`'s
  inlined CSS against `pagekit/kit.css`.
- `tests/test_token_parser.py` — non-vacuity self-test of the parser/diff logic
  (identical-passes, missing-token-caught, value-mismatch-caught).

`tests/test_token_drift.py` and `tests/test_token_parser.py` need **no explicit wiring**
into `tests/run-tests.sh` — it runs `python3 -m unittest discover -s tests -p
'test_*.py'`, which auto-discovers any correctly-named file in `tests/`, the same way
every other Python test module in the suite is picked up. Confirm this by running
`bash tests/run-tests.sh` and checking the new tests appear in the output; do not add a
manual invocation line.

Self-tested work unit: implementer runs `python3 -m unittest tests.test_token_drift
tests.test_token_parser -v` and the full `bash tests/run-tests.sh`, and pastes both
outputs into `PEON_REPORT.md`.

## Definition of Done

- [ ] `pagekit/kit.css` rebuilt on the shared garden role + font tokens, both modes,
      old→new mapping applied throughout (no leftover `--bg`/`--panel`/`--ink`/etc.
      references).
- [ ] `pagekit/toggle.js` added, read via `$PAGEKIT` and inlined the same way as
      `kit.css`; kagi-ban's report page carries the toggle button and picks up the
      identical `localStorage` key + `data-theme` values WP2 pinned, so a viewer's
      choice carries between the garden and a report page.
- [ ] `pagekit/README.md` updated (kit + toggle.js contract, button snippet, palette
      description).
- [ ] `pagekit/reference/reference-page.html` regenerated and committed (git-tracked
      benchmark).
- [ ] `tests/test_token_drift.py` added, wired implicitly via `run-tests.sh`'s
      auto-discovery, green — and proven non-vacuous by `tests/test_token_parser.py`.
- [ ] `bin/redact.py` untouched. Missing-kit-fails-render behavior untouched
      (`PagekitSourcingTests::test_missing_kit_css_fails_the_render_loudly` still
      passes unmodified).
- [ ] `docs/INTERFACES.md` §10 style-bullet amendment shipped in the same change.
- [ ] Full suite green (`bash tests/run-tests.sh`), outputs pasted in `PEON_REPORT.md`
      alongside the two new-test-only runs.
- [ ] `PEON_REPORT.md` written: what changed, test evidence, confirmation that
      `--sub` → `--nibi` and `--mut` → `--nibi-faint` per the settled mapping, any
      deviations.
- [ ] **Not the implementer's job**: side-by-side screenshots of the garden and a
      report page, light and dark, are the **foreman's** post-merge verification via
      Playwright MCP — do not attempt to produce or paste screenshots into
      `PEON_REPORT.md`.

## Verification commands

```sh
bash tests/run-tests.sh                                            # full suite — the pass bar
python3 -m unittest tests.test_token_drift tests.test_token_parser -v   # new tests, explicit
grep -c -- '--bg\|--panel\b\|--ink\b\|--accent\b' pagekit/kit.css  # 0 — old names gone
grep -n 'toggle.js\|theme-toggle' pagekit/README.md loops.d/kagi-ban/render_page.py

# Implementer's own visual/gate check — fixture-based, works in a fresh worktree with
# no real state/ or reports/ data (both gitignored):
python3 loops.d/kagi-ban/render_page.py pagekit/reference/fixture-scan.json \
  --loop kagi-ban --run-id wp3-check --host fixture --av-version 0.0-stub \
  -o /tmp/kagi-ban-wp3-check.html
python3 bin/page_envelope.py check --file /tmp/kagi-ban-wp3-check.html \
  --expect-run-id wp3-check --expect-loop kagi-ban
```

Post-merge, on the machine with real `state/`/`reports/` data (never in the peon's
worktree — both dirs are gitignored and a fresh worktree won't have them), the
**foreman** re-renders the real `reports/kagi-ban/latest.html` from the most recent
real scan already on disk — no engine call, no cost, just a restyle of already-collected
data:

```sh
LATEST_RUN=$(ls -td state/runs/*-kagi-ban-*/ | head -1)
RUN_ID=$(basename "${LATEST_RUN%/}")
python3 loops.d/kagi-ban/render_page.py "${LATEST_RUN}scan.json" \
  --loop kagi-ban --run-id "$RUN_ID" -o /tmp/kagi-ban-recheck.html
python3 bin/page_envelope.py check --file /tmp/kagi-ban-recheck.html \
  --expect-run-id "$RUN_ID" --expect-loop kagi-ban   # gate before promoting
cp /tmp/kagi-ban-recheck.html reports/kagi-ban/latest.html
```

Then the foreman takes the side-by-side garden/report-page screenshots (light + dark)
via Playwright MCP, per Definition of Done.

## Constraints & gotchas

- **§10 hermetic rules bind pages too**: nothing fetched on load — no webfonts, no CDN,
  no remote `url()`/`src`. `pagekit/toggle.js` is inlined text, exactly like `kit.css`;
  it must never become a `<script src=...>`. `tests/html_selfcontained.py` enforces
  this; keep report-page coverage green.
- **INTERFACES amendment ships in the same change as the code** — never drift.
- **Worktree conduct**: you are in an isolated worktree; commit there. If `git commit`
  fails in your sandbox, leave the working tree clean and state it in `PEON_REPORT.md`
  — do **not** improvise ssh remotes, standalone `.git` dirs, or other escapes.
- **Ruff hook**: the repo carries pre-existing lint debt and the hook uses a broad
  ruleset — fix findings only in files you edited (`token_parser.py`,
  `test_token_drift.py`, `test_token_parser.py`, `render_page.py`), leave the rest.
- **macOS**: no `flock`, no GNU `timeout` — don't add either to tests or scripts.
- **Scope guards**: `bin/redact.py` untouched (`git status` clean on it at the end);
  `dashboard/generate.py` token **values** untouched — it is the canonical source; if a
  value looks wrong, flag it in `PEON_REPORT.md` instead of changing it; no `site/`
  changes.
- **`pagekit/reference/` needs updating, is not out of scope**: `fixture-scan.json`
  stays as-is (it's input data, not styled output); `reference-page.html` is derived
  output that embeds the old palette and must be regenerated + committed (Settled
  decision 5 / Task 6) — it is git-tracked, unlike everything under `reports/`.
- `reports/` and `state/` are both fully gitignored (`.gitignore` lines 2–3) — confirmed
  via `git check-ignore -v reports/kagi-ban/latest.html`. A fresh worktree has neither
  directory populated. Do not try to seed real scan data into the worktree to "test with
  real data" — the fixture is the correct and only offline-verifiable input available to
  the implementer (Settled decision 6).
- `tests/test_runner_pages.sh`'s `seed_pagekit()` touches an empty `kit.css` and never
  exercises real kit/toggle content (its renderer stub is synthetic) — it needs no
  changes for this WP; don't add a `toggle.js` seed there unless a real failure shows up.
