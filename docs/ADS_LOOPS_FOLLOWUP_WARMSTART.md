# Ads loops — follow-up warmstart (three open issues + the next build)

> **Ephemeral.** Written 2026-07-28 ~07:00 local (Asia/Manila, UTC+8).
> **RE-VERIFY BEFORE ACTING.** Other agents were working concurrently when this was
> written; any of the three issues below may already be fixed. Every item ships with the
> exact command to re-check it — run those first, delete what has resolved, delete the
> file when empty.

## The acceptance bar (generalissimo, 2026-07-28 — this is the product call)

Two phases, in order. **Do not skip to phase 2.**

1. **Phase 1 (what "good enough" means right now):** *"id be happy with either i press a
   button to run everything but at least the schedules screen is nice and the runs work
   and produce the output."* A manual trigger is acceptable. The runs must be reliable and
   the output must land on the tabs.
2. **Phase 2 (only after phase 1 holds):** *"then id move onto setting up the actual
   scheduled runs."* That's the launchd install (issue 2 below).

His mental model of the system, in his words, for anyone re-deriving intent: schedules are
listed / run / **administered** from the schedules page; the pretty output lands on the
**/ads campaigns tab**; actions land on the **/ads actions tab** — mirroring the two-element
pattern already used by DMP and CRO, with campaign reports as just a different source.

## What is genuinely working (verified 2026-07-28 — don't re-investigate these)

- **`/ads/actions` is live** and renders real actions. `curl -s -o /dev/null -w '%{http_code}'
  http://127.0.0.1:8787/ads/actions` → `200` (~34KB). It is the 5th tab, after campaigns
  (`console/dashboard/templates/_ads_subnav.html:28`), route at `ads_routes.py:462`, reader
  at `console/dashboard/ads_actions_feed.py`.
- **`/ads/campaigns` is live** → `200`.
- **The schedules page path is `/schedules`, NOT `/settings/schedules`** (that 404s).
  Route: `growth-console/console/dashboard/server.py:697`. It lists all five ads loops.
- **All five network checks produce real, network-specific output.** One good run each:

  | loop | status | briefs |
  |---|---|---|
  | ads-google | warn | 6 |
  | ads-intl | warn | 3 |
  | ads-reddit | alert | 3 |
  | ads-x | warn | 4 |
  | ads-program | warn | 6 |

  Sample (ads-reddit), to show these are real checks and not filler: *"Desktop-only test is
  delivering near zero and its verdict is overdue — revert reddit targeting to ALL"*;
  *"r-boost message test has no callable verdict"*; *"Reddit CBO daily budget is stated two
  ways in this run's inputs — $8/day versus the journal's $12."*

  ⚠️ A previous session's report claimed "only google checks anything." **That was wrong** —
  it inspected only each loop's *latest* run, which happened to be a failed second batch.
  Census command (run this, don't trust either claim):
  ```bash
  cd ~/projects/loops
  for l in ads-google ads-intl ads-reddit ads-x ads-program; do
    tot=0; ok=0
    for d in state/runs/*-$l-*; do [ -d "$d" ] || continue; tot=$((tot+1))
      [ -f "$d/action-set/ACTIONS.md" ] && ok=$((ok+1)); done
    printf '%-12s runs=%s with_action_set=%s\n' "$l" "$tot" "$ok"
  done
  ```

## Issue 1 — every second run dies (reliability; blocks phase 1)

**Symptom:** each of the four non-google loops ran twice; the **second run of every one
produced no `contract.json` at all** — no output, no status. ads-google: 7 runs, 3 with
action sets. So roughly half of all runs die silently.

**Re-check:** the census command above. Fixed = `with_action_set` equals `runs` (minus any
run you deliberately killed).

**Leads, not conclusions:**
- The failed second batch ran 40s apart (`20260727T2145xx`–`2147xx`). Not mutual overlap.
- A separate ads-google run *was* correctly refused with `skipped-overlap` while another
  held the lock — that mechanism works, so lock contention is unlikely to be the whole story.
- Look at each dead run's `engine.log` / `.adapter.stdio.log` / `engine.status`. A dead run
  dir has only `inputs/`, `precheck.out`, `prompt.composed.md`.

## Issue 2 — nothing is scheduled (this is phase 2; don't start it before phase 1 holds)

**Symptom:** all five loops are `installed=False`; no plists in `~/Library/LaunchAgents`;
no loop jobs in `launchctl`.

**Re-check:**
```bash
cd ~/projects/loops && ./bin/loopctl list          # installed column
launchctl list | grep -i 'loops\.\|ads-'           # expect nothing until installed
ls ~/Library/LaunchAgents | grep -iE 'loop|ads'
```

**The blocker, verbatim:**
```
install failed: no fresh non-failed run recorded for ads-google within 90s after
kickstart — check engine auth/env under launchd
```
The engine authenticates fine from an interactive shell but not when launchd starts it —
launchd jobs get a minimal environment and no login keychain context. `engine=claude` on all
five (`loop.conf`). This is what earlier commit messages meant by *"installs blocked on
launchd credential access."* Nothing was ever actually installed — verified.

## Issue 3 — `/schedules` lists the same work twice (cosmetic; part of "the screen is nice")

**Symptom:** the four legacy manual "ads check-in" rows still render alongside the five new
automated loop rows.

**Re-check:** `curl -s http://127.0.0.1:8787/schedules | grep -c 'ads check-in'` → `4` means
still duplicated, `0` means resolved.

**Fix location:** `growth-console/console/dashboard/schedules.py:84` — the `_ADS_CHECKINS`
tuple (x / google / reddit / intl), rendered via `_ads_checkin_rows()` (:382) and appended to
`manual` at :577. The plan always said these retire once the loops take over; the loops now
produce the same checks, so the rows are redundant.

## The next build, if phase 1 still isn't met: a "run everything" button

No manual trigger exists in the console today (verified — no loop-run endpoint, no button).
`/ads/actions` currently just *tells* the reader to run `loopctl run <loop>` by hand
(`templates/ads_actions.html:88`).

**Copy the DMP/CRO pattern — it is exactly the shape he asked to mirror:**
- `POST /api/dmp/regenerate` → `dmp_regen.start(root)`, returns immediately
  (`server.py:1002`); `GET /api/dmp/regenerate/status` polls (`server.py:1007`).
- `console/dashboard/dmp_regen.py`: a `threading.Thread(daemon=True)` worker (:93) behind a
  **shared job lock** so at most one job runs at a time, plus a `status()` dict (:66).
  Note :180's lesson — an uncaught exception strands the status as running and holds the lock.
- Siblings to match: `/api/cro/generate`, `/api/opentwins/regenerate`.

The no-shell-out rule that governs this area applies to the **request path** (the /schedules
13.8s lesson — never `loopctl` synchronously while rendering). A background worker started by
a POST and polled via a status endpoint does not violate it.

## Settled — do not relitigate

- **Where actions may come from:** *strictly* from the campaign report recommendations, same
  pattern as DMP and CRO (`report > action generation > future execution (tba)`). Infrastructure
  problems — a fetch failing, inputs missing — are **run status only** and must NOT mint an
  `ADG-`/`ADR-`/`ADI-`/`ADX-`/`ADP-` id. Currently **all five prompts still violate this** at
  `prompt.md:44` ("If a critical input is MISSING in the digest, raise ONE action about the
  input gap") and again at :161. This produced `ADG-06` — "INPUT GAP" — which then had to be
  struck, permanently burning an id on plumbing. Removing it is approved work, not an open
  question.
- **Set-write/validation failure protocol (already implemented in ads-google):** emit the
  analysis, `status=alert`, and **zero findings**. Per `INTERFACES.md` §4.5 a non-empty
  findings array overrides the declared status with the findings' max severity, so a failed
  run whose findings top out at `warn` would surface amber; empty findings let the alert
  through. ads-google also has authoritative metrics (copied from precheck, not model-guessed)
  and all-history high-water-mark ids. **The four sibling loops do NOT yet have these three
  fixes** — deliberate, unpropagated drift.

## Repo hygiene note

During the 2026-07-28 session, subagents made **4 commits in `~/projects/loops`**
(`7f9e2fa`, `ede268f`, `dc716e9`, `a2c0128`) and **22 in `~/projects/maguyva-marketing`**,
several well outside ads (opentwins reddit blocklist, inbound Chrome lifecycle, a GitHub
funnel tile), despite briefs saying to run no git commands. **Nothing is pushed** — the loops
repo has no remote configured. Review before assuming any of it was reviewed.

## Pointers

- Build order + the original design: `~/projects/maguyva-marketing/docs/ads-actions-loops-warmstart.md`
- Other open design threads: `docs/OPEN_THREADS_WARMSTART.md`
- Harness contract (frozen): `docs/INTERFACES.md` · loop authoring: `docs/LOOP_AUTHORING.md`
