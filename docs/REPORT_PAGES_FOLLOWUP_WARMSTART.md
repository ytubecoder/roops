# Report pages — follow-up warmstart (updated 2026-07-30)

The report-pages tier (INTERFACES Amendment 2) and its pilot `kagi-ban` **shipped and are live**.
**The three parked decisions, the whole follow-up backlog, and both trailing leftovers are CLEARED**
(2026-07-30). Nothing here is outstanding. What remains is the live-state context a cold agent
needs, the PATH explanation that must not be "fixed" back, and the settled decisions with their
reasoning, so they are not silently reversed. Delete sections here as they stop being load-bearing.

- Design rationale: `docs/REPORT_PAGES_PLAN.md` · Mechanical contract: `docs/INTERFACES.md`
  §4.1 step 6.5 + §12 · Author guide: `docs/REPORT_PAGES.md` · Plan (executed):
  `docs/superpowers/plans/2026-07-30-report-pages.md`.
- Shipped commits: `d6ebceb..44e0942` (ship) then the follow-up wave (this doc's cleanup).
  **681 hermetic tests green** (`bash tests/run-tests.sh`) — 350 python + 331 shell, up from 668.

## Current live state (verified 2026-07-30)

- `kagi-ban` is **installed on launchd**, `daily:07:40`, and is the second loop on a schedule
  after `loop-sensei`. Steady state: `16 exposures (14 high, 2 medium); 0 new, 0 resolved`,
  `status=alert` (correct posture — 14 undismissed highs).
- Serving over the tailnet vhost (path-scoped roots, machine-local Caddyfile — NOT in this repo):
  - `https://loops.example.ts.net/` — dashboard
  - `https://loops.example.ts.net/reports.html` — reports screen
  - `https://loops.example.ts.net/reports/kagi-ban/latest.html` — the page
- One finding is dismissed as an exercise: `av:homebrew:b4cc5e56`, note "known-accepted PATH
  exposure (LOOP_HANDOFF)". Suppressed from `latest.json` (15 findings), still rendered on the
  snapshot page (16) — that asymmetry is the spec (§1.4), not a bug.

## Why the count is 16 and not the 20 in `~/projects/av-audit/`

**Do not "fix" this back to 20.** `av`'s bash/zsh detectors flag user-writable directories that
**precede** system paths. In a real login shell `/opt/homebrew/{bin,sbin}` precede `/usr/bin`
(flagged) while `~/.grok/bin` sits after them (correctly not flagged). The old 20-finding number —
including av-audit's own `scan-latest.json` baseline and `LOOP_HANDOFF.md` — was measured in a
Claude-Code-like process whose PATH ordering differs. `precheck.sh` now pins the login-shell PATH
(`/bin/zsh -l`), so the observation no longer depends on who triggered the run. 16 is the canonical
user's-shell view. (av-audit is not ours to edit per its handoff; its baseline doc is simply stale.)

## The one real bug the cleanup wave found

The renderer's KV-neutralization regex used `\b` where `bin/redact.py`'s `_KV_RE` uses
`(?<![A-Za-z0-9-])`. That divergence was **load-bearing in both directions**, measured:

| probe | `redact.py` redacts | old renderer neutralized |
|---|---|---|
| `GITHUB_TOKEN=/Users/…/.env` | yes | **no** — under-neutralized → promotion gate FAILS |
| `DB_PASSWORD=/Users/…/.pgpass` | yes | **no** — same |
| `av:gh-cli-hosts-token: /path` | no | **yes** — over-neutralized, cosmetic damage |

`_` is a word character (so `\b` misses `GITHUB_TOKEN`) but is **not** in `[A-Za-z0-9-]` (so
redact's lookbehind fires). Any av finding phrasing an underscore-style env var would have silently
staled the page. `bin/redact.py` now exports `KV_KEYWORDS` / `KV_KEY_PATTERN` / `KV_SEPARATOR` and
the renderer composes `_KV_PHRASE_RE` from them, so the keyword set and the boundary are
single-sourced; `tests/test_redact.py` + `tests/test_kagi_ban.py` fail if either drifts. If
`bin/redact.py` cannot be imported the renderer **exits 1 and writes no page** rather than emitting
an ungated one (the promotion gate imports redact too, so nothing could promote anyway).

## Settled — do not re-litigate

- **`_KV_RE` lookbehind: RATIFIED by generalissimo 2026-07-30.** Hyphenated compounds
  (`gh-cli-hosts-token:`) and letter-adjacent tails (`authtoken:`) do not trip the generic
  rest-of-line rule; underscore compounds (`GITHUB_TOKEN=`) still do; the specific high-value
  patterns (`ghp_`/`sk-`/`xox`/`AKIA`/`eyJ`/private-key) fire regardless and are the primary
  control. Recorded in `bin/redact.py`'s comment.
- **`pagekit/kit.css` is READ at render time — it is the single source of report-page CSS.**
  `$PAGEKIT` (exported by `bin/run-loop.sh` step 6.5) is the runner's path to it; `render_page.py`
  falls back to its own repo layout so a bare `python3 render_page.py` works. The body is inlined
  into `<style>` (pages must stay self-contained offline — never `<link>` it) and the header
  comment is stripped. A missing kit.css **fails the render**, same call as the redact import: it
  is a committed file, so its absence is a broken checkout, and failing names the path in
  `page-render.log` instead of quietly promoting an unstyled page. Editing kit.css restyles every
  page-enabled loop. Superseded the earlier inlined-copy-bound-by-test design, which left two
  copies and gave `$PAGEKIT` no reader.
- **The corrupted finding row is deleted.** `av:gh-cli-hosts-token:«redacted:secret»` (the
  pre-redact-fix id, times_seen=4, resolved) is gone from `state/loops.sqlite`; the real finding
  `av:gh-cli-hosts-token:ff079f7f` is untouched. No `«redacted:»` ids remain in `findings`.
- **Page self-containment is asserted on references, not on the substring `http://`.** The old rule
  ("the page must not contain `http://` anywhere") was simultaneously too strict — real finding text
  contains URLs, and `xmlns="http://www.w3.org/2000/svg"` is an identifier — and too loose, since
  `src="//cdn.example/x.js"` fetches from the network without containing `http://` at all.
  `tests/html_selfcontained.py` collects everything the browser dereferences on load (`src`,
  `srcset`, `poster`, `data`, fetching `<link rel>`s, CSS `@import`/`url()`, inline `style=`) and
  requires each to be relative or `data:`. Navigation `<a href>` is deliberately excluded. The
  scanner has its own tests — an always-empty scanner would make every call pass vacuously.

## Tooling note (not this repo)

`peon` dispatches to **grok** need a git workaround in linked worktrees (sandbox blocks the real
gitdir under `.git/worktrees/…`). Fix: run grok without the workspace sandbox for linked worktrees,
exactly as `peon` already does for gemini. Full detail lives in agent memory
(`peon-grok-worktree-sandbox`), not here.
