# Report pages — follow-up warmstart (updated 2026-07-30)

The report-pages tier (INTERFACES Amendment 2) and its pilot `kagi-ban` **shipped and are live**.
**The three parked decisions and the whole follow-up backlog are now CLEARED** (second wave,
2026-07-30). What remains below is the live-state context a cold agent needs, the PATH explanation
that must not be "fixed" back, and two small leftovers. Delete sections here as they resolve.

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
- **`pagekit/kit.css` stays an inlined copy, bound by test** — not read at render time. Reading it
  live would make the shipped page's bytes depend on a mutable external file (breaking
  byte-determinism) and would need a fallback copy anyway, reintroducing the drift. The bodies are
  asserted byte-identical instead. **Edit both together.**
- **The corrupted finding row is deleted.** `av:gh-cli-hosts-token:«redacted:secret»` (the
  pre-redact-fix id, times_seen=4, resolved) is gone from `state/loops.sqlite`; the real finding
  `av:gh-cli-hosts-token:ff079f7f` is untouched. No `«redacted:»` ids remain in `findings`.

## Remaining leftovers (neither blocking)

- **`PAGEKIT` (the env var) still has no reader.** `bin/run-loop.sh` step 6.5 exports it and no
  renderer consumes it — kit.css is now bound by test instead. It remains the documented contract
  for future page loops; decide whether to keep exporting it when the second page-enabled loop
  lands.
- **CLAUDE.md's "`http://` must not appear anywhere in the dashboard page" is stricter than what
  is enforceable.** The real `dashboard/loops.html` contains one occurrence — inside a loop's
  finding text, a probe reporting that `http://127.0.0.1:9/dead` refused a connection. It is data
  in a `<pre>`, not an asset reference, so it triggers no network fetch. `tests/test_dashboard.py`
  correctly asserts against a hermetic fixture (it guards the *generator*), so real finding text
  can never be caught by it. Do not "fix" this by mangling finding text.

## Tooling note (not this repo)

`peon` dispatches to **grok** need a git workaround in linked worktrees (sandbox blocks the real
gitdir under `.git/worktrees/…`). Fix: run grok without the workspace sandbox for linked worktrees,
exactly as `peon` already does for gemini. Full detail lives in agent memory
(`peon-grok-worktree-sandbox`), not here.
