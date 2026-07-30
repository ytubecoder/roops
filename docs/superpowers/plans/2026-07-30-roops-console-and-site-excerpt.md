# Roops Console + Site Interface Excerpt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the real dashboard actionable (巡/休 pause-resume toggle + schedule picker via a local console server) and swap the public product page's static interface mock for the interactive garden excerpt.

**Architecture:** A new `bin/console.py` module serves `dashboard/` plus a tiny JSON API on 127.0.0.1; mutations shell out to `bin/loopctl` subcommands (the single trusted code path) and finish by regenerating the dashboard; the page's new controls are hidden by default and hydrate only when a relative `fetch('api/state')` succeeds, so the generated file stays hermetic and file-openable. The site work is a front-end-only lift from `site/ui.html` into `site/index.html` section 04.

**Tech Stack:** Python 3 stdlib only (`http.server`, `json`, `subprocess`, `urllib`), hand-authored HTML/CSS/JS, unittest (existing hermetic suite patterns).

**Spec:** `docs/superpowers/specs/2026-07-30-roops-console-and-site-excerpt-design.md`. Two refinements vs the spec, both simplifications the reviewer should treat as intended: (1) `/api/state` carries control-relevant fields only (`name, schedule, enabled, plist_present, loaded`) — not `effective_status`/`next_run_text`/`last_run_at`, because (2) after any successful mutation the page does `location.reload()`, and the freshly regenerated page already renders all status truth server-side.

## Global Constraints

- The dashboard **generator** (`dashboard/generate.py`) stays hermetic: no subprocess, no network, file-presence checks only. The **console** (`bin/console.py`) is trusted unsandboxed harness code and MAY shell out (`launchctl print`, `bin/loopctl`).
- The generated page must never contain the substrings `http://`, `https://`, `//cdn`, or `src="http` (tests/test_dashboard.py `test_no_network_no_external_assets`). All fetch URLs are relative (`api/state`, `api/loops/...`).
- launchctl is NEVER invoked for real in tests — always via the `LOOPS_LAUNCHCTL` env seam pointing at the per-fixture recording stub (see tests/test_loopctl.py:128 fixture, `launchctl_calls()`).
- Server binds `127.0.0.1` only, default port `8929`. No auth in v1. Remote = `tailscale serve --https` (docs only, no code).
- The browser toggle drives **pause/resume only**; never install/uninstall. Never-installed loops (no plist) get a disabled control. Rescheduling never kickstarts (must not fire a run).
- Dashboard styling: B-07 garden design system — existing CSS tokens only, vermillion only on human decisions, `--mono` for numbers/labels, motion ≥.8s `cubic-bezier(0.16,1,0.3,1)` with the existing reduced-motion block extended (not forked), holds at 390px.
- Site pages (`site/`): tokens only (8 tokens ∪ {#16130F, #3A362F, #55503F}), seal font never on ≤16px kanji, no emoji (marubatsu 〇△×), mock data generic + organic numbers, never the phrase "report-only, never acts" (findings are actions in waiting), all links relative. `site/ui.html` and `site/brandkit/` are NOT modified.
- All paths `$HOME`-relative at runtime (no hardcoded `/Users/...` in code); macOS: no `flock`, no GNU `timeout`.
- Run the suite with `bash tests/run-tests.sh` (hermetic, 681 tests currently). Python files get ruff-formatted by the edit hook; fix only files you touched if `ruff check` flags them at turn end.
- Commit per task with the message given in the task; do not push (controller pushes after verification).

## File Structure

- `bin/loopctl` — modify: new `cmd_set_schedule`, `cmd_serve`, argparse wiring. Core schedule logic in a helper `_apply_schedule(root, from_dir, name, spec)` so tests hit it through the CLI.
- `bin/console.py` — create: request routing + API handlers as plain functions (`handle_request(root, method, path, body) -> (status:int, payload:dict|bytes, ctype:str)`), plus `serve(root, port)` wiring ThreadingHTTPServer. Handlers are testable in-process without binding a socket.
- `dashboard/generate.py` — modify: enabled-aware 巡/休 chip; per-row control markup (hidden); inline `<script>` block; small CSS additions.
- `tests/test_loopctl.py` — modify: set-schedule tests (reuse the existing fixture class).
- `tests/test_console.py` — create: in-process API handler tests.
- `tests/test_dashboard.py` — modify: enabled-aware display tests + controls-hidden/no-network assertions.
- `docs/INTERFACES.md` — modify: new §13 Console; §10 install-state paragraph touch-up.
- `README.md` — modify: one paragraph in "What Can Actually Change Things".
- `site/index.html` — modify: section 04 mock → interactive excerpt.

---

### Task 1: `loopctl set-schedule`

**Files:**
- Modify: `bin/loopctl` (helpers near `_set_enabled` ~line 895; argparse block ~line 1034-1070)
- Test: `tests/test_loopctl.py` (append a new TestCase class reusing the existing fixture)

**Interfaces:**
- Consumes: `_rewrite_conf_key(path, key, new_value)` (loopctl:214), `_render_plist_xml(root, name, conf)` (loopctl:637), `_plist_path(root, name)`, `_service_label(name)`, `_launchctl(argv)`, `loopconf.parse(path)`, `schedule.parse(spec)`, `_dashboard_module()`.
- Produces: `_apply_schedule(root, from_dir, name, spec) -> (old_spec, new_spec)` raising `ValueError` on bad grammar / unknown loop; CLI verb `loopctl set-schedule <name> <spec>`. Task 3's server shells out to this verb.

- [ ] **Step 1: Write the failing tests** (append to tests/test_loopctl.py; follow the file's existing fixture usage — construct the fixture, write a loop via its helper, run loopctl via subprocess with `self.fixture.env`):

```python
class TestSetSchedule(LoopctlFixtureCase):  # inherit/instantiate exactly as neighboring classes do
    def test_rejects_bad_grammar(self):
        self.write_loop("alpha", schedule="daily:09:00")
        r = self.run_loopctl(["set-schedule", "alpha", "interval:nonsense"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("invalid", r.stderr.lower())
        conf = open(self.conf_path("alpha")).read()
        self.assertIn("schedule=daily:09:00", conf)  # untouched

    def test_unknown_loop_fails(self):
        r = self.run_loopctl(["set-schedule", "ghost", "daily:09:00"])
        self.assertEqual(r.returncode, 1)

    def test_rewrites_conf_no_plist_no_launchctl(self):
        self.write_loop("alpha", schedule="daily:09:00")
        r = self.run_loopctl(["set-schedule", "alpha", "interval:15m"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("schedule=interval:15m", open(self.conf_path("alpha")).read())
        self.assertEqual(self.fixture.launchctl_calls(), [])  # no plist -> nothing to reload

    def test_plist_present_disabled_rerenders_without_bootstrap(self):
        self.write_loop("alpha", schedule="daily:09:00", enabled="false")
        self.write_plist("alpha")          # fixture helper if present, else touch launchd/com.loops.alpha.plist
        self.run_loopctl(["set-schedule", "alpha", "interval:2h"])
        self.assertIn(b"StartInterval", open(self.plist_path("alpha"), "rb").read())
        calls = self.fixture.launchctl_calls()
        self.assertNotIn("bootstrap", " ".join(map(str, calls)))
        self.assertNotIn("kickstart", " ".join(map(str, calls)))

    def test_plist_present_enabled_bootout_bootstrap_no_kickstart(self):
        self.write_loop("alpha", schedule="daily:09:00", enabled="true")
        self.write_plist("alpha")
        self.run_loopctl(["set-schedule", "alpha", "weekly:mon:08:00"])
        joined = " ".join(" ".join(map(str, c)) for c in self.fixture.launchctl_calls())
        self.assertIn("bootout", joined)
        self.assertIn("bootstrap", joined)
        self.assertNotIn("kickstart", joined)

    def test_manual_removes_plist_after_bootout(self):
        self.write_loop("alpha", schedule="daily:09:00", enabled="true")
        self.write_plist("alpha")
        self.run_loopctl(["set-schedule", "alpha", "manual"])
        self.assertFalse(os.path.isfile(self.plist_path("alpha")))
        self.assertIn("bootout", " ".join(" ".join(map(str, c)) for c in self.fixture.launchctl_calls()))
```

Adapt helper names (`write_loop`, `run_loopctl`, `conf_path`, `plist_path`, `write_plist`) to what the fixture actually provides — read the top of tests/test_loopctl.py first and reuse its existing idioms verbatim; add tiny local helpers only if missing. The assertions above are the requirements.

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_loopctl.py -k SetSchedule -x -q` (or the file's own runner idiom — check how run-tests.sh invokes it; unittest via `python3 -m unittest` is likely)
Expected: FAIL — `set-schedule` unknown subcommand.

- [ ] **Step 3: Implement** in `bin/loopctl`:

```python
def _apply_schedule(root, from_dir, name, spec):
    conf_path = os.path.join(root, from_dir, name, "loop.conf")
    if not os.path.isfile(conf_path):
        raise ValueError(f"loop not found: {name}")
    schedule.parse(spec)  # raises ValueError on bad grammar — validate BEFORE any write
    conf, _errors = loopconf.parse(conf_path)
    old_spec = conf.get("schedule", "")
    _rewrite_conf_key(conf_path, "schedule", spec)
    label = _service_label(name)
    plist_path = _plist_path(root, name)
    uid = os.getuid()
    if os.path.isfile(plist_path):
        if spec == "manual":
            _launchctl(["bootout", f"gui/{uid}/{label}"])  # best-effort
            os.remove(plist_path)
        else:
            conf, _errors = loopconf.parse(conf_path)  # re-read: schedule now updated
            with open(plist_path, "wb") as f:
                f.write(_render_plist_xml(root, name, conf))
            if str(conf.get("enabled", "true")).lower() != "false":
                _launchctl(["bootout", f"gui/{uid}/{label}"])
                _launchctl(["bootstrap", f"gui/{uid}", plist_path])
                # NO kickstart — rescheduling must never fire a run.
    return old_spec, spec


def cmd_set_schedule(args):
    try:
        old, new = _apply_schedule(args.root, args.from_dir, args.name, args.spec)
    except ValueError as exc:
        print(f"set-schedule failed: {exc}", file=sys.stderr)
        return 1
    dash = _dashboard_module()
    dash.generate(root=args.root, loopconf_parse=loopconf.parse, schedule_parse=schedule.parse)
    print(f"schedule {args.name}: {old or '(unset)'} -> {new}")
    return 0
```

Argparse (next to the pause/resume parsers):

```python
    set_schedule_p = sub.add_parser("set-schedule", parents=[common])
    set_schedule_p.add_argument("name")
    set_schedule_p.add_argument("spec")
```

and route it in the existing dispatch (same pattern as `pause`/`resume`). If dashboard regeneration uses the `state/locks/_dashboard.lock` helper elsewhere in this file (check `cmd_dashboard` and the runner), wrap the same way; if `cmd_dashboard` doesn't lock, don't add locking here either — mirror the existing call exactly.

- [ ] **Step 4: Run tests** — same command, Expected: PASS. Then `bash tests/run-tests.sh` once — no regressions.

- [ ] **Step 5: Commit**

```bash
git add bin/loopctl tests/test_loopctl.py
git commit -m "feat(loopctl): set-schedule verb — validate, rewrite conf, re-render plist, no kickstart"
```

---

### Task 2: enabled-aware 巡/休 display in the dashboard generator

**Files:**
- Modify: `dashboard/generate.py` (`_schedule_loaded` ~line 928; `_resolve_loop` return dict ~line 1470-1494; the `sw` chip render ~lines 1213-1219; legend line ~1674)
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `_resolve_loop`'s `conf` dict (already parsed via `loopconf.parse`).
- Produces: loop dict gains `"enabled": bool` (True unless `conf.get("enabled") == "false"`, string-insensitive); three-way chip rendering that Task 4's control markup wraps. Rule: plist present + enabled → `巡` on; plist present + not enabled → `休` paused (`title="rounds paused — resume from console or loopctl resume"`); no plist → `休` as today ("no schedule loaded — supervised runs only").

- [ ] **Step 1: Write the failing tests** (reuse the file's existing fixture-tree helpers for building `loops.d/<name>/loop.conf` + `launchd/*.plist`; follow neighboring display tests):

```python
    def test_sw_on_when_plist_and_enabled(self):
        # loop.conf enabled=true (or key absent), plist file present
        html = self.render_with(name="alpha", conf_extra="", plist=True)
        self.assertIn('class="sw on"', html)

    def test_sw_paused_when_plist_and_disabled(self):
        html = self.render_with(name="alpha", conf_extra="enabled=false\n", plist=True)
        self.assertIn('class="sw off paused"', html)
        self.assertIn("rounds paused", html)

    def test_sw_off_when_no_plist(self):
        html = self.render_with(name="alpha", conf_extra="", plist=False)
        self.assertIn("no schedule loaded", html)
```

- [ ] **Step 2: Run to verify failure** — `paused` variant class doesn't exist yet. Expected: FAIL on the second test.

- [ ] **Step 3: Implement** in generate.py — in `_resolve_loop`, add to the returned dict:

```python
        "enabled": str(conf.get("enabled", "true")).lower() != "false",
```

At the chip render site (current two-way `if loop["installed"]`), make it three-way:

```python
    if loop["installed"] and loop["enabled"]:
        sw = '<span class="sw on" title="schedule loaded (launchd)">巡</span>'
        next_html = f'<span class="rm-next">next 巡 {e(loop["next_run_text"])}</span>'
    elif loop["installed"]:
        sw = '<span class="sw off paused" title="rounds paused — resume from console or loopctl resume">休</span>'
        next_html = ""
    else:
        sw = '<span class="sw off" title="no schedule loaded — supervised runs only">休</span>'
        next_html = ""
```

Add a `.sw.off.paused` CSS rule in the page's existing schedule-state CSS block (~line 723 comment) — same visual family as `.sw.off`, tokens only (e.g. ochre-tinted border to read as "deliberately at rest", not new colors). Update the footer legend (~line 1674) to mention the paused state ("休 = paused or no schedule loaded").

- [ ] **Step 4: Run** `python3 -m pytest tests/test_dashboard.py -q` (or unittest equivalent) then full `bash tests/run-tests.sh`. Expected: PASS, no regressions (staleness/needs_attention logic untouched — display only).

- [ ] **Step 5: Commit**

```bash
git add dashboard/generate.py tests/test_dashboard.py
git commit -m "feat(dashboard): enabled-aware 巡/休 — paused loops no longer display as loaded"
```

---

### Task 3: console server (`bin/console.py` + `loopctl serve`)

**Files:**
- Create: `bin/console.py`
- Modify: `bin/loopctl` (`cmd_serve` + argparse)
- Test: `tests/test_console.py` (create)

**Interfaces:**
- Consumes: `bin/loopctl` as a subprocess (`pause`, `resume`, `set-schedule` — Task 1's verb); `loopconf.parse`; `launchctl print` via the `LOOPS_LAUNCHCTL` env override if set (same resolution rule as loopctl's `_launchctl` — read how loopctl resolves the binary and copy that one-liner).
- Produces: `handle_request(root, method, path, body_bytes) -> (status:int, payload:bytes, content_type:str)` — pure function, no socket, JSON in/out for `/api/*`, file bytes for pages; `serve(root, port)`; CLI `loopctl serve [--port PORT]` (default 8929).

- [ ] **Step 1: Write the failing tests** in `tests/test_console.py` — build the same hermetic fixture tree as test_loopctl (import/copy its fixture class per that file's conventions; `LOOPS_LAUNCHCTL` always set):

```python
def call(fixture, method, path, body=None):
    import console  # bin/ on sys.path per the suite's existing pattern (see how tests import schedule/loopconf)
    raw = json.dumps(body).encode() if body is not None else b""
    with mock.patch.dict(os.environ, fixture.env):
        return console.handle_request(fixture.root, method, path, raw)

class TestConsoleApi(...):
    def test_state_shape(self):
        self.write_loop("alpha", schedule="daily:09:00"); self.write_plist("alpha")
        status, payload, ctype = call(self.fixture, "GET", "/api/state")
        self.assertEqual(status, 200); self.assertEqual(ctype, "application/json")
        loops = {l["name"]: l for l in json.loads(payload)["loops"]}
        a = loops["alpha"]
        self.assertEqual(a["schedule"], "daily:09:00")
        self.assertTrue(a["plist_present"]); self.assertIn("enabled", a); self.assertIn("loaded", a)

    def test_rounds_409_when_no_plist(self):
        self.write_loop("alpha", schedule="daily:09:00")
        status, payload, _ = call(self.fixture, "POST", "/api/loops/alpha/rounds", {"on": False})
        self.assertEqual(status, 409)
        self.assertIn("loopctl install", json.loads(payload)["error"])

    def test_rounds_off_pauses(self):
        self.write_loop("alpha", schedule="daily:09:00", enabled="true"); self.write_plist("alpha")
        status, _, _ = call(self.fixture, "POST", "/api/loops/alpha/rounds", {"on": False})
        self.assertEqual(status, 200)
        self.assertIn("enabled=false", open(self.conf_path("alpha")).read())
        self.assertIn("bootout", " ".join(" ".join(map(str, c)) for c in self.fixture.launchctl_calls()))

    def test_schedule_400_on_bad_grammar(self):
        self.write_loop("alpha", schedule="daily:09:00")
        status, payload, _ = call(self.fixture, "POST", "/api/loops/alpha/schedule", {"spec": "sometimes"})
        self.assertEqual(status, 400)
        self.assertIn("schedule=daily:09:00", open(self.conf_path("alpha")).read())

    def test_schedule_applies_and_regenerates_dashboard(self):
        self.write_loop("alpha", schedule="daily:09:00")
        status, _, _ = call(self.fixture, "POST", "/api/loops/alpha/schedule", {"spec": "interval:30m"})
        self.assertEqual(status, 200)
        self.assertIn("schedule=interval:30m", open(self.conf_path("alpha")).read())
        self.assertTrue(os.path.isfile(os.path.join(self.fixture.root, "dashboard", "loops.html")))

    def test_unknown_loop_404_and_unknown_path_404(self):
        self.assertEqual(call(self.fixture, "POST", "/api/loops/ghost/rounds", {"on": True})[0], 404)
        self.assertEqual(call(self.fixture, "GET", "/api/nope")[0], 404)

    def test_get_root_serves_dashboard_html(self):
        status, payload, ctype = call(self.fixture, "GET", "/")
        self.assertEqual(status, 200); self.assertIn("text/html", ctype)
```

- [ ] **Step 2: Run to verify failure** — `console` module doesn't exist. Expected: ImportError.

- [ ] **Step 3: Implement `bin/console.py`:**

```python
#!/usr/bin/env python3
"""bin/console.py — Roops console: serves dashboard/ + a JSON API on 127.0.0.1.

Trusted unsandboxed harness code (INTERFACES §13): MAY shell out. Mutations go
through bin/loopctl subprocesses — one code path for CLI and console. The
hermeticity contract (§10) binds dashboard/generate.py, not this module.
Toggle = pause/resume ONLY; install/uninstall stay CLI (supervised verification).
"""
import json, os, re, subprocess, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_ROUNDS_RE = re.compile(r"^/api/loops/([A-Za-z0-9_-]+)/rounds$")
_SCHED_RE = re.compile(r"^/api/loops/([A-Za-z0-9_-]+)/schedule$")
_PAGES = {"/": "loops.html", "/loops.html": "loops.html", "/reports.html": "reports.html"}


def _launchctl_bin():
    return os.environ.get("LOOPS_LAUNCHCTL", "launchctl")  # match loopctl's resolution exactly


def _loopctl(root, argv):
    cmd = [sys.executable, os.path.join(root, "bin", "loopctl"), *argv, "--root", root]
    return subprocess.run(cmd, capture_output=True, text=True)
    # If the common parser puts --root elsewhere, match how tests/test_loopctl.py invokes it.


def _loaded(root, name):
    uid = os.getuid()
    r = subprocess.run([_launchctl_bin(), "print", f"gui/{uid}/com.loops.{name}"],
                       capture_output=True, text=True)
    return r.returncode == 0


def _loop_names(root):
    d = os.path.join(root, "loops.d")
    return sorted(n for n in os.listdir(d)
                  if os.path.isfile(os.path.join(d, n, "loop.conf"))) if os.path.isdir(d) else []


def _state(root):
    sys.path.insert(0, os.path.join(root, "bin"))
    import loopconf  # noqa: E402
    loops = []
    for name in _loop_names(root):
        conf, _errors = loopconf.parse(os.path.join(root, "loops.d", name, "loop.conf"))
        plist = os.path.isfile(os.path.join(root, "launchd", f"com.loops.{name}.plist"))
        loops.append({
            "name": name,
            "schedule": conf.get("schedule", ""),
            "enabled": str(conf.get("enabled", "true")).lower() != "false",
            "plist_present": plist,
            "loaded": _loaded(root, name) if plist else False,
        })
    return {"loops": loops}


def _json(status, obj):
    return status, json.dumps(obj).encode(), "application/json"


def handle_request(root, method, path, body_bytes):
    path = path.split("?", 1)[0]
    if method == "GET" and path in _PAGES:
        fp = os.path.join(root, "dashboard", _PAGES[path])
        if not os.path.isfile(fp) and _PAGES[path] == "loops.html":
            _loopctl(root, ["dashboard"])
        if os.path.isfile(fp):
            return 200, open(fp, "rb").read(), "text/html; charset=utf-8"
        return _json(404, {"error": "not generated yet"})
    if method == "GET" and path == "/api/state":
        return _json(200, _state(root))
    m = _ROUNDS_RE.match(path) if method == "POST" else None
    if m:
        name = m.group(1)
        if name not in _loop_names(root):
            return _json(404, {"error": f"unknown loop: {name}"})
        if not os.path.isfile(os.path.join(root, "launchd", f"com.loops.{name}.plist")):
            return _json(409, {"error": f"not installed — run: loopctl install {name}"})
        on = bool(json.loads(body_bytes or b"{}").get("on"))
        r = _loopctl(root, ["resume" if on else "pause", name])
        if r.returncode != 0:
            return _json(500, {"error": r.stderr.strip() or "loopctl failed"})
        _loopctl(root, ["dashboard"])
        return _json(200, {"ok": True, "state": _state(root)})
    m = _SCHED_RE.match(path) if method == "POST" else None
    if m:
        name = m.group(1)
        if name not in _loop_names(root):
            return _json(404, {"error": f"unknown loop: {name}"})
        spec = str(json.loads(body_bytes or b"{}").get("spec", ""))
        r = _loopctl(root, ["set-schedule", name, spec])
        if r.returncode != 0:
            return _json(400, {"error": r.stderr.strip() or "invalid schedule"})
        return _json(200, {"ok": True, "state": _state(root)})
    return _json(404, {"error": "not found"})


def serve(root, port):
    class Handler(BaseHTTPRequestHandler):
        def _do(self):
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            status, payload, ctype = handle_request(root, self.command, self.path, body)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        do_GET = _do
        do_POST = _do
        def log_message(self, fmt, *args):
            sys.stderr.write("console: " + fmt % args + "\n")
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"roops console: 127.0.0.1:{port} (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
```

Check two integration points against reality before running tests: (a) how loopctl's `_launchctl` resolves the binary (copy exactly into `_launchctl_bin`); (b) how the common argparse takes `--root`/`--from-dir` (fix `_loopctl` accordingly — the recording-stub env must flow through, which it does since `subprocess.run` inherits `os.environ`). In `bin/loopctl` add:

```python
def cmd_serve(args):
    import console
    return console.serve(args.root, args.port)
```

```python
    serve_p = sub.add_parser("serve", parents=[common])
    serve_p.add_argument("--port", type=int, default=8929)
```

(`import console` works because loopctl already arranges `bin/` imports for `loopconf`/`schedule` — mirror that mechanism.)

- [ ] **Step 4: Run** `tests/test_console.py`, then full `bash tests/run-tests.sh`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bin/console.py bin/loopctl tests/test_console.py
git commit -m "feat(console): loopctl serve — dashboard + JSON API (pause/resume, set-schedule) on 127.0.0.1"
```

---

### Task 4: dashboard controls — hidden markup, picker, hydration JS

**Files:**
- Modify: `dashboard/generate.py` (row render near the Task-2 chip; CSS block; end of `<body>`)
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: Task 2's three-way chip + `loop["enabled"]`/`loop["installed"]`; Task 3's API shapes (`POST api/loops/<name>/rounds {"on":bool}` → reload; `POST api/loops/<name>/schedule {"spec":str}` → reload; errors `{"error":msg}`).
- Produces: the served page's control layer. Everything `hidden` until `fetch('api/state')` succeeds.

- [ ] **Step 1: Write the failing tests:**

```python
    def test_console_controls_hidden_by_default(self):
        html = self.render_default()
        self.assertIn('data-console-controls hidden', html)   # wrapper attr on each row's control cell
        self.assertIn("fetch('api/state'", html)              # relative URL, single quotes
        self.assertIn('data-sched-edit', html)

    def test_no_network_still_clean(self):
        # the existing test_no_network_no_external_assets already covers this; just
        # confirm the new JS didn't introduce banned tokens — same assertion, run here
        # against a controls-bearing render:
        html = self.render_with(name="alpha", plist=True)
        for token in ("http://", "https://", "//cdn", 'src="http'):
            self.assertNotIn(token, html)

    def test_rounds_toggle_carries_loop_name(self):
        html = self.render_with(name="alpha", plist=True)
        self.assertIn('data-loop="alpha"', html)
```

- [ ] **Step 2: Run to verify failure.** Expected: FAIL (no controls markup).

- [ ] **Step 3: Implement.** Per row (rendered next to the Task-2 `sw` chip; loop name is already escaped with the module's `e()`):

```python
    controls = (
        f'<span class="con-cell" data-console-controls hidden data-loop="{e(name)}" '
        f'data-installed="{"1" if loop["installed"] else ""}" '
        f'data-enabled="{"1" if loop["enabled"] else ""}" data-schedule="{e(loop["schedule"] or "")}">'
        '<button class="con-sw" type="button" role="switch" '
        f'aria-checked="{"true" if (loop["installed"] and loop["enabled"]) else "false"}" '
        f'aria-label="toggle rounds for {e(name)}"'
        f'{"" if loop["installed"] else " disabled title=\"install from CLI: loopctl install\""}>'
        '<span class="con-track"><span class="con-knob"></span></span></button>'
        f'<button class="con-sched" type="button" data-sched-edit aria-label="edit schedule for {e(name)}">'
        f'{e(loop["schedule"] or "manual")}</button>'
        "</span>"
    )
```

One shared picker panel at the end of `<body>` (not per-row), plus the hydration script:

```html
<div class="sched-panel" data-sched-panel hidden>
  <div class="sp-presets">
    <button data-spec="interval:5m">5m</button><button data-spec="interval:15m">15m</button>
    <button data-spec="interval:30m">30m</button><button data-spec="interval:1h">hourly</button>
    <button data-kind="daily">daily</button><button data-kind="weekly">weekly</button>
    <button data-kind="monthly">monthly</button>
  </div>
  <div class="sp-form" hidden>
    <select class="sp-dow" hidden><option>mon</option><option>tue</option><option>wed</option>
      <option>thu</option><option>fri</option><option>sat</option><option>sun</option></select>
    <input class="sp-dom" type="number" min="1" max="28" value="1" hidden>
    <input class="sp-time" type="time" value="09:00">
    <button class="sp-apply" type="button">apply</button>
  </div>
  <div class="sp-err" role="alert"></div>
</div>
<script>
(function(){
  'use strict';
  fetch('api/state').then(function(r){ if(!r.ok) throw 0; return r.json(); }).then(function(){
    document.querySelectorAll('[data-console-controls]').forEach(function(c){ c.hidden=false; });
  }).catch(function(){ /* static file mode — controls stay hidden */ });
  function post(path, body){
    return fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})
      .then(function(r){ return r.json().then(function(j){ return {ok:r.ok, j:j}; }); });
  }
  document.addEventListener('click', function(ev){
    var sw = ev.target.closest('.con-sw');
    if (sw && !sw.disabled){
      var cell = sw.closest('[data-console-controls]');
      var on = sw.getAttribute('aria-checked') !== 'true';
      sw.disabled = true;
      post('api/loops/' + cell.getAttribute('data-loop') + '/rounds', {on:on}).then(function(res){
        if (res.ok) location.reload(); else { sw.disabled=false; alert(res.j.error); }
      });
      return;
    }
    var ed = ev.target.closest('[data-sched-edit]');
    var panel = document.querySelector('[data-sched-panel]');
    if (ed){
      var cell2 = ed.closest('[data-console-controls]');
      panel.dataset.loop = cell2.getAttribute('data-loop');
      panel.dataset.cur = cell2.getAttribute('data-schedule');
      var rect = ed.getBoundingClientRect();
      panel.style.top = (rect.bottom + window.scrollY + 6) + 'px';
      panel.style.left = Math.max(8, rect.left + window.scrollX - 120) + 'px';
      panel.querySelector('.sp-form').hidden = true;
      panel.querySelector('.sp-err').textContent = '';
      panel.hidden = false;
      return;
    }
    if (panel && !panel.hidden && !ev.target.closest('[data-sched-panel]')) panel.hidden = true;
  });
  var panel = document.querySelector('[data-sched-panel]');
  function apply(spec){
    post('api/loops/' + panel.dataset.loop + '/schedule', {spec:spec}).then(function(res){
      if (res.ok) location.reload(); else panel.querySelector('.sp-err').textContent = res.j.error;
    });
  }
  panel.addEventListener('click', function(ev){
    var b = ev.target.closest('button'); if (!b) return;
    if (b.dataset.spec) { apply(b.dataset.spec); return; }
    if (b.dataset.kind) {
      panel.dataset.kind = b.dataset.kind;
      panel.querySelector('.sp-form').hidden = false;
      panel.querySelector('.sp-dow').hidden = b.dataset.kind !== 'weekly';
      panel.querySelector('.sp-dom').hidden = b.dataset.kind !== 'monthly';
      var cur = panel.dataset.cur || '';
      var mTime = cur.match(/(\d{2}:\d{2})$/); if (mTime) panel.querySelector('.sp-time').value = mTime[1];
      return;
    }
    if (b.classList.contains('sp-apply')) {
      var t = panel.querySelector('.sp-time').value || '09:00';
      var k = panel.dataset.kind;
      if (k === 'daily') apply('daily:' + t);
      else if (k === 'weekly') apply('weekly:' + panel.querySelector('.sp-dow').value + ':' + t);
      else apply('monthly:' + String(panel.querySelector('.sp-dom').value).padStart(2, '0') + ':' + t);
    }
  });
})();
</script>
```

CSS (append inside the existing `<style>`; tokens/vars already defined in the page — reuse them, do NOT introduce hexes beyond what the page already declares; `.8s cubic-bezier(0.16,1,0.3,1)` transitions; add `.con-*`/`.sp-*` to the existing reduced-motion block):

```css
  .con-cell { display:inline-flex; align-items:center; gap:8px; }
  .con-sw { background:none; border:0; padding:0; cursor:pointer; display:inline-flex; border-radius:9px; }
  .con-sw:focus-visible { outline:1px solid var(--shu); outline-offset:3px; }
  .con-sw[disabled] { opacity:.35; cursor:default; }
  .con-track { display:block; position:relative; width:34px; height:17px; border-radius:9px;
    border:1px solid var(--hair, rgba(28,26,23,.25)); background:var(--washi, #F2EDE3);
    transition:background .8s cubic-bezier(0.16,1,0.3,1), border-color .8s cubic-bezier(0.16,1,0.3,1); }
  .con-knob { position:absolute; top:2px; left:18px; width:11px; height:11px; border-radius:50%;
    background:var(--koke, #6B7A5C); transition:left .8s cubic-bezier(0.16,1,0.3,1), background .8s cubic-bezier(0.16,1,0.3,1); }
  .con-sw[aria-checked="false"] .con-knob { left:2px; background:var(--nibi, #8C8578); }
  .con-sched { font-family:var(--mono); font-size:11px; background:none; cursor:pointer;
    border:1px solid var(--hair, rgba(28,26,23,.25)); border-radius:3px; padding:3px 8px; color:inherit; }
  .con-sched:hover { border-color:var(--shu); }
  .sched-panel { position:absolute; z-index:9; background:var(--washi, #F2EDE3);
    border:1px solid rgba(28,26,23,.3); border-radius:4px; padding:12px; box-shadow:0 10px 30px -12px rgba(0,0,0,.4); }
  .sched-panel button { font-family:var(--mono); font-size:11px; background:none;
    border:1px solid rgba(28,26,23,.25); border-radius:3px; padding:4px 9px; cursor:pointer; margin:2px; }
  .sched-panel button:hover { border-color:var(--shu); color:var(--shu); }
  .sp-form { margin-top:8px; display:flex; gap:6px; align-items:center; }
  .sp-err { font-family:var(--mono); font-size:11px; color:var(--shu); margin-top:6px; }
```

**Adapt the CSS var names to what generate.py's stylesheet actually defines** (read its `:root` — the dashboard's own token names may differ from the site's; the fallback literals above must be replaced by the page's real tokens so no new hex enters the page). Keep every string free of `http://`/`https://`.

- [ ] **Step 4: Run** dashboard tests + full suite. Expected: PASS incl. `test_no_network_no_external_assets`.

- [ ] **Step 5: Commit**

```bash
git add dashboard/generate.py tests/test_dashboard.py
git commit -m "feat(dashboard): console controls — rounds switch + schedule picker, hidden unless served"
```

---

### Task 5: INTERFACES §13 + §10 touch-up + README

**Files:**
- Modify: `docs/INTERFACES.md`, `README.md`

**Interfaces:** documentation of Tasks 1-4 exactly as built (verify each claim against the code before writing it).

- [ ] **Step 1: Add §13 to INTERFACES.md** (after the last section, matching the file's voice — terse, normative):

```markdown
## 13. Console (`loopctl serve`)

Local control surface for the dashboard. `bin/console.py`, started by `loopctl serve
[--port PORT]` (default 8929), binds 127.0.0.1 ONLY. Trusted unsandboxed harness code:
MAY shell out (`launchctl print` for live load state; `bin/loopctl` subprocesses for all
mutations — one code path for CLI and console). §10's hermeticity binds dashboard/generate.py,
never this module. No daemon mode, no LaunchAgent in v1; remote access is
`tailscale serve --https=<port>` (TLS mandatory on ts.net), auth = localhost trust + tailnet
identity.

| endpoint | effect |
|---|---|
| `GET /` `/loops.html` `/reports.html` | serve generated pages (loops.html regenerated if missing) |
| `GET /api/state` | `{loops:[{name, schedule, enabled, plist_present, loaded}]}` |
| `POST /api/loops/<name>/rounds {on}` | resume/pause (§ enabled key + bootstrap/bootout). 409 if no plist — install/uninstall are CLI-only (supervised verification gate). |
| `POST /api/loops/<name>/schedule {spec}` | `set-schedule`: §5.1-validate, rewrite conf, re-render plist, bootout+bootstrap iff loaded. NEVER kickstart. 400 on bad grammar. |

Every mutation regenerates the dashboard before responding. The generated page's controls
are `hidden` and hydrate only when a **relative** `fetch('api/state')` succeeds — opened as
a plain file the page is byte-identical in behavior to the pre-console dashboard.
```

Also: in §10's install-state amendment paragraph, extend the display rule to the three-way form (plist+enabled → 巡; plist+`enabled=false` → 休 paused; no plist → 休), still file-presence + conf-parse only. Add `set-schedule` and `serve` to the §"loopctl" command list (~line 647).

- [ ] **Step 2: README** — in "What Can Actually Change Things", add one short paragraph: the console is deterministic harness code with full power (like loopctl, which it shells to); the model is still never in the mutation path; the browser can pause/resume and reschedule, never install.

- [ ] **Step 3: Self-check** — every §13 claim matches the built behavior (port, 409 text, no-kickstart, regeneration order). Fix doc or flag code mismatch — never document intent.

- [ ] **Step 4: Commit**

```bash
git add docs/INTERFACES.md README.md
git commit -m "docs(interfaces): §13 console amendment + §10 three-way install-state display"
```

---

### Task 6: site — section 04 mock becomes the interactive excerpt

**Files:**
- Modify: `site/index.html` (section 04 only + CSS/JS additions)
- Reference (read-only): `site/ui.html` — `.rsw`/`.sw-*`/`.loop-row.is-off` CSS at lines 208-232; switch markup pattern at line 635; stamp JS inside the `<script>` (hanko-btn handler ~script-lines 127-183; `.rsw` toggle handler ~script-line 314)

**Interfaces:** none consumed from Tasks 1-5 (pure front-end simulation; no fetch, no network).

- [ ] **Step 1: Modify section 04's rows.** In each of the four `.loop-row`s, replace the trailing `<div class="sev">…</div>` column with a switch cell, and widen the grid: `.loop-row` grid-template-columns `44px 1.4fr 1fr 130px 90px` → `44px 1.4fr 1fr 130px 64px`; add:

```html
        <div class="sw-cell">
          <button class="rsw" type="button" role="switch" aria-checked="true" aria-label="toggle rounds for tls-certs"><span class="sw-track"><span class="sw-knob"></span></span></button>
          <span class="sw-lab" aria-hidden="true">巡</span>
        </div>
```

(one per row, aria-label per loop name; the four names stay tls-certs / dead-links / deps-drift / backups-verify). Lift the CSS block from ui.html lines 208-232 (`.sw-cell`, `.rsw`, `.sw-track`, `.sw-knob`, `.sw-lab`, `.loop-row.is-off` rules) into index.html's style block — adapt `var(--hair2)` to `rgba(28,26,23,.25)` (index has no `--hair2` token; do NOT add new custom properties, inline the rgba). The SEV/MA labels move: put the previous `.sev` text (`MA 間` / `SOE 副` / `SHIN 真`) as a `<small>` under the loop-name cell so the severity vocabulary isn't lost.

- [ ] **Step 2: Make the findings-note the interactive arrangement.** Replace the static `.findings-note` `<p>` prose + `.lv` line with stems + hanko buttons (adapted from ui.html's arrangement panel, kept to the existing three deps-drift stems):

```html
      <div class="findings-note" id="arr">
        <svg class="stems" viewBox="0 0 90 96" aria-hidden="true"> [keep the existing three-stem SVG exactly, but add data-stem="shin|soe|hikae" to each line+circle pair's wrapping <g>] </svg>
        <div class="fx">
          <b>The arrangement — findings for deps-drift, 巡 of 06:20</b>
          <div class="arr-row" data-f="shin"><span class="arr-t">SHIN 真 — critical CVE in a pinned transitive dependency</span>
            <span class="arr-btns"><button class="hanko-btn" data-k="承" title="approve — becomes an order">承</button><button class="hanko-btn" data-k="認" title="acknowledge">認</button><button class="hanko-btn" data-k="休" title="snooze">休</button><button class="hanko-btn" data-k="済" title="settle">済</button></span></div>
          <div class="arr-row" data-f="soe"><span class="arr-t">SOE 副 — 2 lockfile drifts aging past 48h</span>
            <span class="arr-btns"><button class="hanko-btn" data-k="承" title="approve — becomes an order">承</button><button class="hanko-btn" data-k="認" title="acknowledge">認</button><button class="hanko-btn" data-k="休" title="snooze">休</button><button class="hanko-btn" data-k="済" title="settle">済</button></span></div>
          <div class="arr-row" data-f="hikae"><span class="arr-t">HIKAE 控 — license audit clean, 18 of 43 packages pinned</span>
            <span class="arr-btns"><button class="hanko-btn" data-k="承" title="approve — becomes an order">承</button><button class="hanko-btn" data-k="認" title="acknowledge">認</button><button class="hanko-btn" data-k="休" title="snooze">休</button><button class="hanko-btn" data-k="済" title="settle">済</button></span></div>
          <span class="lv">EFFECTIVE STATUS = THE TALLEST STEM IN THE VASE — STAMP TO PRUNE</span>
        </div>
      </div>
```

Lift/adapt `.hanko-btn` CSS from ui.html (~line 435-445 region: seal-face button, shu border, rotate, `:active` fills shu — read the actual block and carry it plus its hover/active/disabled states) and add minimal `.arr-row`/`.arr-btns`/`.arr-t` layout CSS (flex row, mono for `.arr-t` small caps like the current `.fx p` styling; stamped row: text gets `opacity:.45; text-decoration:line-through currentColor` — check ui.html's `.stamped` treatment and copy its approach instead if it differs).

- [ ] **Step 3: Add the simulation JS** (extend index.html's existing single `<script>` block — keep the IntersectionObserver code, append):

```js
  // rounds switch — settle a row into 休止 rest and back (simulation)
  document.querySelectorAll('.rsw').forEach(function (sw) {
    sw.addEventListener('click', function () {
      var on = sw.getAttribute('aria-checked') === 'true';
      sw.setAttribute('aria-checked', on ? 'false' : 'true');
      var row = sw.closest('.loop-row');
      row.classList.toggle('is-off', on);
      var lab = sw.parentElement.querySelector('.sw-lab');
      if (lab) lab.textContent = on ? '休' : '巡';
    });
  });
  // hanko stamping — prune a stem, recompute the tallest (simulation)
  var sevRank = { shin: 3, soe: 2, hikae: 1 };
  function tallest() {
    var t = 0;
    document.querySelectorAll('.arr-row:not(.stamped)').forEach(function (r) {
      t = Math.max(t, sevRank[r.getAttribute('data-f')] || 0);
    });
    return t;
  }
  document.querySelectorAll('.arr-row .hanko-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var row = btn.closest('.arr-row');
      if (row.classList.contains('stamped')) return;
      row.classList.add('stamped');
      row.querySelectorAll('.hanko-btn').forEach(function (b) { b.disabled = true; });
      var mark = document.createElement('span');
      mark.className = 'stamp-mark';
      mark.textContent = btn.getAttribute('data-k');
      row.querySelector('.arr-btns').appendChild(mark);
      var stemEl = document.querySelector('.stems [data-stem="' + row.getAttribute('data-f') + '"]');
      if (stemEl) stemEl.style.opacity = '.18';
      var dd = document.getElementById('dd-stamp');   // deps-drift row's status stamp: add id="dd-stamp" to it
      var t = tallest();
      if (dd) {
        if (t === 3) { dd.className = 'stamp alert'; dd.textContent = '警'; }
        else if (t === 2) { dd.className = 'stamp warn'; dd.textContent = '注'; }
        else { dd.className = 'stamp ok'; dd.textContent = '済'; }
      }
    });
  });
```

`.stamp-mark` CSS: seal-face, 15px+, shu, slight rotate — copy ui.html's stamped-mark treatment if present, else: `font-family:var(--seal); font-size:18px; color:var(--shu); transform:rotate(-4deg); display:inline-block; margin-left:8px;` (18px — the seal-font floor is >16px). Add all new interactive elements to the existing reduced-motion block (transitions → none). The section's quiet pointer line becomes: `This is a working excerpt — toggles and stamps are simulated. The full interface concept — running rounds, the live engawa view, the ledger — is a page of its own: <a href="./ui.html">enter the garden</a>.`

- [ ] **Step 4: Mechanical checks** (from repo root):

```bash
python3 -c "from html.parser import HTMLParser; HTMLParser().feed(open('site/index.html').read())"
grep -c "report-only" site/index.html   # expect 0
grep -o '#[0-9A-Fa-f]\{6\}' site/index.html | sort -u   # ⊆ 8 tokens ∪ {#16130F,#3A362F,#55503F}
grep -o 'href="[^"]*"' site/index.html | sort -u        # internal: ./ui.html, ./brandkit/ only
```

Plus: no emoji; ui.html and brandkit/ untouched (`git status`).

- [ ] **Step 5: Commit**

```bash
git add site/index.html
git commit -m "feat(site): section 04 — static mock becomes interactive garden excerpt (rsw + hanko stamps)"
```

---

## Controller verification (after all tasks + final review; NOT subagent work)

1. **Console, live fleet (supervised):** `loopctl serve` → browser on 127.0.0.1:8929 → toggle kagi-ban 休 then 巡 (verify `launchctl print gui/$(id -u)/com.loops.kagi-ban` disappears/returns; `enabled=` flips in loop.conf); set-schedule on hello-loop to `interval:15m` and back to `daily:09:00` (plist diff shows StartInterval↔StartCalendarInterval; no run fired — check `loopctl status hello-loop` run count unchanged). Kill server; confirm dashboard file still opens standalone with controls hidden.
2. **Site:** publish.txt flow — throwaway server + Playwright: toggle each row off/on (is-off state applies, 巡↔休 label), stamp shin (row stamps, stem fades, deps-drift stamp drops 警→注), stamp all three (→済), 390px scrollWidth ≤ 390 on index, console clean; then Pages publish + live curls + taildrop per runbook.
3. Push both repos per the runbook/global rule.
