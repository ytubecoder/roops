# Roops console + site interface excerpt — design

Date: 2026-07-30 · Status: approved-pending-spec-review · Owner: generalissimo

Two independent workstreams, console first:

1. **Roops console** — the real dashboard (`dashboard/loops.html`) gains working
   controls: a 巡/休 rounds toggle per loop and a schedule picker
   (5m/15m/30m/hourly/daily/weekly/monthly), backed by a small local server.
2. **Site excerpt** — the public product page's section 04 swaps its static mock
   for the interactive garden excerpt lifted from `site/ui.html` (toggle + stamps,
   simulated); `ui.html` keeps the full concept and is untouched.

User forks already decided (AskUserQuestion): bridge = **local console server**
(not clipboard-copy); site merge = **mock swapped for live excerpt** (both pages
remain).

---

## Workstream 1 — Roops console

### Principle

The invariant stands: deterministic code gets full power; no model anywhere in
this path. The server imports and calls the same functions `loopctl` uses. The
generated dashboard file stays hermetic and fully functional when opened as a
plain file; controls exist only when the page is served by the console.

### 1.1 CLI additions (`bin/loopctl`)

- **`loopctl serve [--port PORT]`** — stdlib `http.server` (ThreadingHTTPServer)
  bound to `127.0.0.1`, default port **8929** (verified free on this machine
  2026-07-30; per the port-coordination rule check `lsof -i :8929` before first
  run and pick another if taken). Serves:
  - `GET /` and `GET /loops.html` → `dashboard/loops.html` (regenerating first if
    missing); `GET /reports.html` → the reports page; static assets under
    `dashboard/` if any.
  - the JSON API below.
  Foreground process, Ctrl-C to stop; v1 has no daemon mode and no LaunchAgent
  (follow-up only if the shape proves out).
- **`loopctl set-schedule <name> <spec>`** — validates `<spec>` via
  `bin/schedule.py parse` (rejects on ValueError, exit 1), rewrites the
  `schedule=` key in `loops.d/<name>/loop.conf` via the existing
  `_rewrite_conf_key`, then: if a plist exists in `launchd/`, re-render it; if
  additionally the loop is loaded (`enabled=true`), `bootout` + `bootstrap` to
  apply — **no kickstart** (rescheduling must not fire a run). Prints old → new
  spec. `manual` is a valid spec here (takes the loop off schedule; plist, if
  any, is removed after bootout — mirrors what install refuses to create).
- Both verbs regenerate the dashboard afterwards under the existing
  `state/locks/_dashboard.lock` (same as §4.1 step 7).

### 1.2 API contract (served on 127.0.0.1:PORT)

- `GET /api/state` → `{"loops": [{name, schedule, enabled, plist_present,
  loaded, next_run_text, effective_status, last_run_at}], "generated_at": ...}`.
  `loaded` is live truth: the server MAY shell out to
  `launchctl print gui/$UID/com.loops.<name>` — the hermeticity rule binds the
  *generator*, not the console.
- `POST /api/loops/<name>/rounds` body `{"on": true|false}` →
  `cmd_resume`/`cmd_pause` (which rewrite `enabled=` and
  bootstrap/bootout the existing plist). **Only valid for loops with a plist
  present**; for a never-installed loop the API returns 409 with
  `{"error": "not installed — run: loopctl install <name>"}`.
- `POST /api/loops/<name>/schedule` body `{"spec": "interval:15m"}` → the
  `set-schedule` path above. 400 on grammar rejection, with the parser's message.
- Every mutating call finishes by regenerating the dashboard and returns the
  fresh per-loop state object.
- Errors are JSON `{"error": msg}`; unknown loop → 404. No other endpoints.

**Narrowing vs the approved sketch (flagged for review):** the design message
said toggle-on could also mean "first-time install". Dropped — `cmd_install` is
a supervised act with a verification gate (kickstart + poll for a verified
first run, up to 90s, rollback on failure). Hiding that behind a browser toggle
skips the supervision on purpose built into it, and would bypass the explicit
phase-gate on the five ads loops. v1 toggle = pause/resume only; the row for a
never-installed loop shows the toggle disabled with "install from CLI" hover
text.

### 1.3 Security / exposure

- Bind `127.0.0.1` only (this server mutates launchd — the 0.0.0.0 dev-server
  habit deliberately does not apply). Remote use:
  `tailscale serve --https=8929 http://127.0.0.1:8929` — TLS mandatory on
  `*.ts.net` (HSTS-preloaded); relay links as `https://`.
- No auth in v1: localhost implies the same trust as running `loopctl`; tailnet
  exposure is gated by tailnet identity. Documented in INTERFACES.

### 1.4 Dashboard changes

- **`dashboard/generate.py` (hermetic, unchanged rules):**
  - Install-state display becomes enabled-aware, still file-presence-only:
    plist present + `enabled=true` (loop.conf, already parsed) → 巡 on;
    plist present + `enabled=false` → 休 **paused** ("rounds paused — resume
    from console or loopctl"); no plist → 休 as today ("no schedule loaded —
    supervised runs only"). Fixes the pre-existing misreport where a paused
    loop showed 巡.
  - Emits the control markup (switch button, schedule chip + picker skeleton)
    with `hidden` attributes; a small inline JS block on load does
    `fetch('api/state')` with a **relative URL** (the no-`http://` test keeps
    passing) — response OK → unhide controls, hydrate live state; failure →
    static page identical to today. No polling in v1; state refreshes on
    mutation responses.
  - Schedule picker UI: current spec rendered as a mono chip; activating it
    opens preset chips — `5m · 15m · 30m · hourly · daily · weekly · monthly`
    mapping to `interval:5m|15m|30m|1h`, `daily:HH:MM`, `weekly:DOW:HH:MM`,
    `monthly:DD:HH:MM` — calendar forms get contextual `HH:MM` (+ weekday /
    day-of-month) inputs prefilled from the current spec where compatible.
    Garden aesthetic per the B-07 design system: tokens only, hanko-red only on
    human decisions, mono for all numbers, motion ≥.8s with reduced-motion
    fallback, holds at 390px.
- Confirmation semantics: mutations are immediate (no dialog); the row re-renders
  from the API response — the stamp-like act is the click itself.

### 1.5 INTERFACES amendment

- New **§13 Console** (amendment, not drift): server binding/port, endpoint
  table, the pause/resume-only toggle rule, set-schedule semantics, the
  no-kickstart rule, exposure/auth statement, and the boundary sentence: the
  hermeticity contract of §10 binds `generate.py`; the console is trusted
  unsandboxed harness code like `loopctl` itself.
- §10 touch-up: install-state display check extended to read `enabled` from
  loop.conf (still no subprocess).
- `loopctl list`/`status` already print `enabled` — no change.

### 1.6 Tests (extend `tests/`, stay hermetic)

- `schedule.py`: no changes, existing coverage stands.
- `set-schedule`: grammar rejection; conf rewrite; plist re-render iff plist
  present; no kickstart call (assert via recorded launchctl invocations —
  existing test seam for `_launchctl`).
- pause/resume: enabled flag transitions; generate.py renders 巡 / 休-paused /
  休-uninstalled correctly from fixture trees.
- API handlers: exercised **in-process** (call the handler functions with a
  fake request, no socket bind) — routing, 400/404/409 paths, mutation →
  regeneration ordering.
- Dashboard page: no-`http://` assertion still passes; controls-hidden-by-default
  assertion; relative-URL fetch string present.

### 1.7 Out of scope (v1)

Run-now button (ads-loops phase-1 thread owns the manual trigger), install /
uninstall from the browser, auth, daemon/LaunchAgent mode, polling/live-tail,
editing anything in loop.conf other than `schedule`, findings actions from the
console (approve→action bridge is its own open thread).

---

## Workstream 2 — site: section 04 mock → live excerpt

- `site/index.html` section 04 (`04 · The interface · 実装`): the static
  `.mock` + `.findings-note` are replaced by an interactive excerpt lifted from
  `site/ui.html`: the four generic loop rows (tls-certs, dead-links, deps-drift,
  backups-verify) with the real `.rsw` switch behavior — toggling 休 settles the
  row into the rest state (dim, 休 stamp) and back — plus the arrangement panel
  with working hanko stamp buttons (承/認/休/済 prune stems as on ui.html).
  All simulation, front-end only, no network, generic data with organic numbers.
- CSS/JS: lift only the blocks the excerpt needs from ui.html (rsw switch, rest
  state, arrangement/stamp interactions), adapted to index.html's existing
  token/motion/reduced-motion/390px blocks — extend, don't fork. The section
  keeps its h2/lede and the "enter the garden" pointer; engawa, ledger,
  captures, gate remain ui.html-only.
- `site/ui.html` untouched. `site/brandkit/` untouched.
- The quiet pointer line's copy adjusts ("this is a working excerpt — the full
  interface concept…").
- Verify + publish per `site/workflows/publish.txt` (local Playwright pass:
  console clean, toggle/stamp interactions exercised, 390px zero-overflow on
  the changed page; then Pages publish, live curls, taildrop).

## Order & deliverables

1. Console: loopctl verbs + server + generate.py controls + INTERFACES §13 +
   tests, verified live in a browser against the real fleet (toggle kagi-ban
   pause/resume round-trip; set-schedule on hello-loop, restored after).
2. Site excerpt: index.html section 04 rebuild, browser-verified, published.

Both land as normal commits on main, pushed; the site publish follows the
runbook.
