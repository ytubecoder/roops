#!/usr/bin/env python3
"""bin/console.py — Roops console: serves dashboard/ + a JSON API on 127.0.0.1.

Trusted unsandboxed harness code (INTERFACES §13): MAY shell out. Mutations go
through bin/loopctl subprocesses — one code path for CLI and console. The
hermeticity contract (§10) binds dashboard/generate.py, not this module.
Toggle = pause/resume ONLY; install/uninstall stay CLI (supervised verification).

Module-loading and launchctl-resolution rules mirror bin/loopctl exactly (see
its own module docstring):
- `bin/loopconf.py` is the frozen single implementation, loaded here from
  THIS script's own directory (the repo's `bin/`) — never from `root` (the
  data root, which under hermetic tests is a bare tmp tree with no copy of
  it).
- `bin/loopctl` itself is likewise resolved from THIS script's own
  directory and invoked as a subprocess with `--root <data root>` appended
  — the only place a mutation happens from here, so CLI and console share
  one code path.
- `launchctl` is never invoked directly except via the read-only `print`
  probe in `_loaded()`, gated by `LOOPS_LAUNCHCTL` exactly like loopctl's
  own `_launchctl_bin()` — tests point this at a recording stub and must
  never reach the real binary.
"""

import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOOPCTL_BIN = os.path.join(_HERE, "loopctl")

_ROUNDS_RE = re.compile(r"^/api/loops/([A-Za-z0-9_-]+)/rounds$")
_SCHED_RE = re.compile(r"^/api/loops/([A-Za-z0-9_-]+)/schedule$")
_RUN_RE = re.compile(r"^/api/loops/([A-Za-z0-9_-]+)/run$")
# Report pages: the dashboard links each page-enabled loop as ../reports/<name>/latest.html
# (see dashboard/generate.py), which resolves to /reports/<name>/<file> when the page is
# served from this root. The character classes are the whole allowlist — no '/', no '%',
# so neither a literal `../` nor a percent-encoded `..%2f` can even match (the server never
# percent-decodes self.path, so `%` arrives literal). A realpath containment check in the
# handler is the second, independent gate; it also covers a symlink pointing out of the tree.
_REPORT_RE = re.compile(r"^/reports/([A-Za-z0-9_-]+)/([A-Za-z0-9_.-]+)$")
_REPORT_CTYPES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json",
    ".md": "text/plain; charset=utf-8",
}
_PAGES = {
    "/": "loops.html",
    "/loops.html": "loops.html",
}
_RUN_ERROR_TAIL_CHARS = 4096
_RUN_SLOT = threading.Lock()
_RUN_STATUS_LOCK = threading.Lock()
_RUN_STATUS = {
    "running": False,
    "loop": None,
    "started_at": None,
    "finished_at": None,
    "exit_code": None,
    "ok": None,
    "error": None,
}


def _load_module_from_path(path, modname):
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load module {modname} from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


loopconf = _load_module_from_path(
    os.path.join(_HERE, "loopconf.py"), "_console_loopconf"
)


def _launchctl_bin():
    return os.environ.get(
        "LOOPS_LAUNCHCTL", "launchctl"
    )  # matches loopctl's own resolution exactly


def _loopctl(root, argv):
    """The only place bin/loopctl is invoked from — one code path for CLI
    and console mutations. loopctl is resolved from THIS script's own
    directory (real bin/), never from --root; --root is passed before a
    `--` separator, with argv's positionals (a loop name, a schedule spec —
    both client-influenced: the name via the URL path, the spec via the
    JSON body) placed after it. This is load-bearing, not stylistic:
    without `--`, a spec of "--help" would be consumed by argparse as the
    `-h/--help` flag instead of the schedule positional, exiting 0 with no
    mutation and no error — a false "success" for a value that never took
    effect. `--` forces argparse to treat everything after it as positional
    regardless of a leading `-`. subprocess.run inherits the parent's
    environment by default, which is how the LOOPS_LAUNCHCTL test seam
    reaches the child loopctl process."""
    verb, *positionals = argv
    cmd = [sys.executable, _LOOPCTL_BIN, verb, "--root", root]
    if positionals:
        cmd += ["--", *positionals]
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _loaded(root, name):
    uid = os.getuid()
    r = subprocess.run(
        [_launchctl_bin(), "print", f"gui/{uid}/com.loops.{name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return r.returncode == 0


def _loop_names(root):
    d = os.path.join(root, "loops.d")
    return (
        sorted(
            n for n in os.listdir(d) if os.path.isfile(os.path.join(d, n, "loop.conf"))
        )
        if os.path.isdir(d)
        else []
    )


def _state(root):
    loops = []
    for name in _loop_names(root):
        conf, _errors = loopconf.parse(os.path.join(root, "loops.d", name, "loop.conf"))
        plist = os.path.isfile(os.path.join(root, "launchd", f"com.loops.{name}.plist"))
        loops.append(
            {
                "name": name,
                "schedule": conf.get("schedule", ""),
                "enabled": str(conf.get("enabled", "true")).lower() != "false",
                "plist_present": plist,
                "loaded": _loaded(root, name) if plist else False,
            }
        )
    return {"loops": loops}


def _json(status, obj):
    return status, json.dumps(obj).encode(), "application/json"


def _now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_status_snapshot():
    with _RUN_STATUS_LOCK:
        return dict(_RUN_STATUS)


def _parse_json_object(body_bytes):
    """Parses body_bytes as a JSON object. Returns (obj, None) on success or
    (None, error_response) on failure. Malformed JSON (json.loads raising)
    or a non-object top level (a JSON array/string/number is valid JSON but
    has no .get()) is a 400 client error here, never an unhandled exception
    — over a real socket an uncaught exception just drops the connection
    instead of returning a response."""
    try:
        obj = json.loads(body_bytes) if body_bytes else {}
    except (ValueError, TypeError):
        return None, _json(400, {"error": "invalid JSON body"})
    if not isinstance(obj, dict):
        return None, _json(400, {"error": "invalid JSON body"})
    return obj, None


def parse_content_length(raw_len):
    """Parses a raw `Content-Length` header value. Returns (n, None) or
    (None, error_response). A MISSING header is 0 (no body), not an error.

    A negative value must be rejected here and never reach `rfile.read()`:
    `int("-5")` succeeds, and `read(-5)` raises ValueError out of the request
    handler — over a real socket that just drops the connection with no HTTP
    response at all — while `read(-1)` means "read to EOF" and parks the
    serving thread until the client closes. Pure (a string in, a decision out)
    so it is testable without binding a port, like check_origin()."""
    try:
        n = int(raw_len) if raw_len else 0
    except (TypeError, ValueError):
        return None, _json(400, {"error": "invalid Content-Length header"})
    if n < 0:
        return None, _json(400, {"error": "invalid Content-Length header"})
    return n, None


def response_headers(path):
    """Extra response headers for a path, as a list of (name, value) pairs.

    Report pages ONLY get `Content-Security-Policy: sandbox allow-scripts`.
    Report HTML is loop/model-derived content, and the promotion gate
    (bin/page_envelope.py) blocks only EXTERNAL-fetch markup — an INLINE
    `<script>` is allowed and the shipped kagi-ban page uses one. Served from
    this origin, such a script would be same-origin with the mutation API: a
    plain `fetch('/api/loops/<any>/rounds', {method:'POST', headers:{'Content-Type':
    'application/json'}})` from inside a report would carry a valid Host, need no
    CORS preflight, and pause loops or rewrite schedules. Opened as a `file://`
    page that was impossible (opaque origin); the console route is what created
    the adjacency, so the console route is what closes it.

    `sandbox allow-scripts` WITHOUT `allow-same-origin` puts the page in an
    opaque origin: its own inline script still runs (tooltips, drawers), but every
    request it makes is cross-origin and fails closed on this server's missing
    OPTIONS handler (§13.1). Never add `allow-same-origin` — the two flags together
    let the page remove its own sandbox.

    Keyed off the same `_REPORT_RE` the route uses, so the header and the route
    cannot drift apart. Pure and socket-free, like check_origin()."""
    if _REPORT_RE.match(path.split("?", 1)[0]):
        return [("Content-Security-Policy", "sandbox allow-scripts")]
    return []


def _regen_dashboard(root):
    """Post-mutation dashboard regeneration. Best-effort — the mutation itself
    already succeeded, so a regen failure must not turn a 200 into a 500; it
    warns on stderr in bin/loopctl cmd_disposition's exact wording instead, so
    a silently stale dashboard is never the only symptom."""
    r = _loopctl(root, ["dashboard"])
    if r.returncode != 0:
        sys.stderr.write(
            f"warning: dashboard regen failed: {r.stderr.strip() or 'loopctl dashboard failed'}\n"
        )


def _stderr_tail(stderr):
    return (stderr or "")[-_RUN_ERROR_TAIL_CHARS:]


def _run_worker(root, name, started_at):
    """Runs one manual loopctl invocation off the request path.

    The slot release lives in `finally` with the terminal status publish so an
    unexpected worker exception cannot leave the console claiming a phantom run
    is still active."""
    exit_code = None
    ok = False
    error = None
    try:
        try:
            r = _loopctl(root, ["run", name])
            exit_code = r.returncode
            ok = exit_code == 0
            error = None if ok else _stderr_tail(r.stderr)
            try:
                _regen_dashboard(root)
            except Exception as exc:  # noqa: BLE001 — §13: regen is best-effort; the run already happened
                sys.stderr.write(f"warning: dashboard regen failed: {exc}\n")
        except Exception as exc:  # noqa: BLE001 — any worker failure must land in run status, never escape the thread
            ok = False
            error = _stderr_tail(str(exc) or exc.__class__.__name__)
    finally:
        finished_at = _now_utc()
        with _RUN_STATUS_LOCK:
            _RUN_STATUS.update(
                {
                    "running": False,
                    "loop": name,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "exit_code": exit_code,
                    "ok": ok,
                    "error": error,
                }
            )
            _RUN_SLOT.release()


def _start_run(root, name):
    with _RUN_STATUS_LOCK:
        if not _RUN_SLOT.acquire(blocking=False):
            return None, dict(_RUN_STATUS)
        started_at = _now_utc()
        _RUN_STATUS.update(
            {
                "running": True,
                "loop": name,
                "started_at": started_at,
                "finished_at": None,
                "exit_code": None,
                "ok": None,
                "error": None,
            }
        )
        snapshot = dict(_RUN_STATUS)
    try:
        t = threading.Thread(
            target=_run_worker, args=(root, name, started_at), daemon=True
        )
        t.start()
    except Exception as exc:  # noqa: BLE001 — §13.3: a spawn failure must release the slot and report, never 500 raw
        with _RUN_STATUS_LOCK:
            _RUN_STATUS.update(
                {
                    "running": False,
                    "loop": name,
                    "started_at": started_at,
                    "finished_at": _now_utc(),
                    "exit_code": None,
                    "ok": False,
                    "error": str(exc) or "failed to start worker thread",
                }
            )
            _RUN_SLOT.release()
        return None, None
    return snapshot, None


def handle_request(root, method, path, body_bytes):
    path = path.split("?", 1)[0]

    if method == "GET" and path in _PAGES:
        fp = os.path.join(root, "dashboard", _PAGES[path])
        if not os.path.isfile(fp) and _PAGES[path] == "loops.html":
            _loopctl(root, ["dashboard"])
        if os.path.isfile(fp):
            with open(fp, "rb") as f:
                return 200, f.read(), "text/html; charset=utf-8"
        return _json(404, {"error": "not generated yet"})

    m = _REPORT_RE.match(path) if method == "GET" else None
    if m:
        base = os.path.realpath(os.path.join(root, "reports"))
        fp = os.path.realpath(os.path.join(base, m.group(1), m.group(2)))
        # Containment, second gate: realpath resolves `..` segments AND symlinks, so this
        # compares the FINAL target against the reports root. os.sep guards the prefix so
        # a sibling like <root>/reports-old can never pass as <root>/reports. No directory
        # listing is ever produced: a non-file (a directory, a dangling link) is a 404.
        if not (fp == base or fp.startswith(base + os.sep)) or not os.path.isfile(fp):
            return _json(404, {"error": "not found"})
        ctype = _REPORT_CTYPES.get(
            os.path.splitext(fp)[1].lower(), "application/octet-stream"
        )
        with open(fp, "rb") as f:
            return 200, f.read(), ctype

    if method == "GET" and path == "/api/state":
        return _json(200, _state(root))

    if method == "GET" and path == "/api/run/status":
        return _json(200, _run_status_snapshot())

    m = _RUN_RE.match(path) if method == "POST" else None
    if m:
        name = m.group(1)
        if name not in _loop_names(root):
            return _json(404, {"error": f"unknown loop: {name}"})
        _body_obj, err = _parse_json_object(body_bytes)
        if err:
            return err
        snapshot, busy = _start_run(root, name)
        if snapshot is None and busy is None:
            return _json(500, {"error": "failed to start worker thread"})
        if busy is not None:
            return _json(
                409,
                {
                    "error": f"run already in progress: {busy['loop']}",
                    "loop": busy["loop"],
                    "started_at": busy["started_at"],
                },
            )
        return _json(202, {"ok": True, "state": snapshot})

    m = _ROUNDS_RE.match(path) if method == "POST" else None
    if m:
        name = m.group(1)
        if name not in _loop_names(root):
            return _json(404, {"error": f"unknown loop: {name}"})
        if not os.path.isfile(os.path.join(root, "launchd", f"com.loops.{name}.plist")):
            return _json(409, {"error": f"not installed — run: loopctl install {name}"})
        body_obj, err = _parse_json_object(body_bytes)
        if err:
            return err
        on = body_obj.get("on")
        if not isinstance(on, bool):
            return _json(400, {"error": 'body must be {"on": true|false}'})
        r = _loopctl(root, ["resume" if on else "pause", name])
        if r.returncode != 0:
            return _json(500, {"error": r.stderr.strip() or "loopctl failed"})
        _regen_dashboard(root)
        return _json(200, {"ok": True, "state": _state(root)})

    m = _SCHED_RE.match(path) if method == "POST" else None
    if m:
        name = m.group(1)
        if name not in _loop_names(root):
            return _json(404, {"error": f"unknown loop: {name}"})
        body_obj, err = _parse_json_object(body_bytes)
        if err:
            return err
        spec = body_obj.get("spec")
        if not isinstance(spec, str):  # same shape as `on`'s bool check above
            return _json(400, {"error": 'body must be {"spec": "<schedule spec>"}'})
        # Same failure family as the negative Content-Length: a NUL makes
        # subprocess.run raise `ValueError: embedded null byte` out of
        # handle_request, which over a real socket drops the connection with no
        # HTTP response at all. It must be a 400, not an exception. (`name` cannot
        # carry a NUL — the route regexes above admit only [A-Za-z0-9_-], so a
        # NUL-bearing name never matches a route and 404s; test_console.py pins
        # that, so this guard stays where the value is actually reachable.)
        if "\x00" in spec:
            return _json(400, {"error": "invalid schedule spec: embedded NUL byte"})
        # `manual` is a valid §5.1 spec, but _apply_schedule implements it as an UNINSTALL
        # (bootout + remove the plist). Install/uninstall stay CLI-only (§13, §8.1: the
        # supervised-verification gate), so the console refuses it here — at the console
        # layer only. `loopctl set-schedule <name> manual` keeps working unchanged.
        if spec == "manual":
            return _json(
                400,
                {
                    "error": (
                        "manual takes the loop off schedule — "
                        f"use loopctl uninstall {name}"
                    )
                },
            )
        r = _loopctl(root, ["set-schedule", name, spec])
        if r.returncode != 0:
            return _json(400, {"error": r.stderr.strip() or "invalid schedule"})
        if r.stderr.strip():
            sys.stderr.write(r.stderr if r.stderr.endswith("\n") else r.stderr + "\n")
        return _json(200, {"ok": True, "state": _state(root)})

    return _json(404, {"error": "not found"})


def check_origin(host_header, content_type, method, port, allow_hosts=()):
    """Origin/DNS-rebinding gate for the mutation API. Binding to 127.0.0.1
    stops packets arriving from off-box, but it does NOT stop a browser on
    THIS machine from being tricked into firing a request at this server —
    a plain cross-origin `<form method="POST" action="http://127.0.0.1:PORT/...">`
    reaches 127.0.0.1 just fine (the browser, not a remote attacker, opens
    that socket), and DNS rebinding (an attacker-controlled hostname that
    briefly resolves to 127.0.0.1) defeats a same-origin check based on the
    request's own claimed origin alone. Two independent checks close this:

    1. `Host` header must be exactly `127.0.0.1:<port>` or `localhost:<port>`
       — a rebound hostname sends its own (attacker) name as Host, which
       fails this exact-match unless the attacker already owns one of these
       two literal strings, which they cannot (they're not real DNS names
       pointed anywhere but this loopback listener).
    2. Every POST must declare `Content-Type: application/json`. A bare
       HTML `<form>` can only ever send
       `application/x-www-form-urlencoded`, `multipart/form-data`, or
       `text/plain` — never `application/json` — so this alone defeats the
       forms-as-CSRF vector. It also forces a browser to run a CORS
       preflight (OPTIONS) for any cross-origin fetch() with a JSON body;
       this server never answers OPTIONS, so the preflight fails closed.

    `allow_hosts` (§13.1 amendment, 2026-08-04 / B-22) extends the exact-match
    set with explicitly configured names — the trusted-proxy deployment:
    Caddy forwards the browser's real Host (never `header_up`-spoofed) for a
    hostname the operator listed via `loopctl serve --allow-host`. Rebinding
    is still dead: an attacker-controlled hostname sends ITSELF as Host and
    fails the exact match, same as before. Default `()` = behavior unchanged.

    Pure and socket-free (no header object, just the plain strings a real
    request would carry) so it's testable in-process without ever binding a
    port. Called by serve()'s Handler before handle_request() runs;
    handle_request()'s own signature is untouched."""
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", *allow_hosts}
    if host_header not in allowed_hosts:
        return False, f"bad Host header: {host_header!r}"
    if method == "POST":
        ctype = (content_type or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return False, "POST requires Content-Type: application/json"
    return True, ""


def serve(root, port, allow_hosts=()):
    class Handler(BaseHTTPRequestHandler):
        def _respond(self, status, payload, ctype):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            for name, value in response_headers(self.path):
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(payload)

        def _do(self):
            ok, reason = check_origin(
                self.headers.get("Host", ""),
                self.headers.get("Content-Type", ""),
                self.command,
                port,
                allow_hosts,
            )
            if not ok:
                # The rejected request's body is never drained. Under HTTP/1.0 (this
                # server's default) the connection closes after the response anyway;
                # say so explicitly so a future keep-alive switch cannot make the
                # undrained body get parsed as the NEXT request on this socket.
                self.close_connection = True
                self._respond(*_json(403, {"error": reason}))
                return
            n, err = parse_content_length(self.headers.get("Content-Length"))
            if err:
                # Same reason as the 403 above: the body is never drained (here it
                # cannot be — the declared length is exactly what was rejected), so
                # a future keep-alive switch must not parse it as the next request.
                self.close_connection = True
                self._respond(*err)
                return
            body = self.rfile.read(n) if n else b""
            self._respond(*handle_request(root, self.command, self.path, body))

        do_GET = _do
        do_POST = _do

        def log_message(self, fmt, *args):
            sys.stderr.write("console: " + fmt % args + "\n")

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    # Bind FIRST, then adopt the port the socket actually got. With `--port 0` the OS
    # picks an ephemeral port, and that is the port the browser puts in `Host` — gating
    # check_origin() on the REQUESTED port would 403 every single request with no hint
    # as to why. Rebinding this name (a local of serve(), closed over by Handler._do,
    # read at request time — never at class-definition time) is what makes the handler
    # and the banner below agree with the socket.
    port = httpd.server_address[1]
    extra = f" (+Host allowlist: {', '.join(allow_hosts)})" if allow_hosts else ""
    print(f"roops console: 127.0.0.1:{port}{extra} (Ctrl-C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
