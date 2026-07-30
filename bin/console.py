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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOOPCTL_BIN = os.path.join(_HERE, "loopctl")

_ROUNDS_RE = re.compile(r"^/api/loops/([A-Za-z0-9_-]+)/rounds$")
_SCHED_RE = re.compile(r"^/api/loops/([A-Za-z0-9_-]+)/schedule$")
_PAGES = {
    "/": "loops.html",
    "/loops.html": "loops.html",
    "/reports.html": "reports.html",
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

    if method == "GET" and path == "/api/state":
        return _json(200, _state(root))

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
        _loopctl(root, ["dashboard"])
        return _json(200, {"ok": True, "state": _state(root)})

    m = _SCHED_RE.match(path) if method == "POST" else None
    if m:
        name = m.group(1)
        if name not in _loop_names(root):
            return _json(404, {"error": f"unknown loop: {name}"})
        body_obj, err = _parse_json_object(body_bytes)
        if err:
            return err
        spec = str(body_obj.get("spec", ""))
        r = _loopctl(root, ["set-schedule", name, spec])
        if r.returncode != 0:
            return _json(400, {"error": r.stderr.strip() or "invalid schedule"})
        return _json(200, {"ok": True, "state": _state(root)})

    return _json(404, {"error": "not found"})


def check_origin(host_header, content_type, method, port):
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

    Pure and socket-free (no header object, just the plain strings a real
    request would carry) so it's testable in-process without ever binding a
    port. Called by serve()'s Handler before handle_request() runs;
    handle_request()'s own signature is untouched."""
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if host_header not in allowed_hosts:
        return False, f"bad Host header: {host_header!r}"
    if method == "POST":
        ctype = (content_type or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return False, "POST requires Content-Type: application/json"
    return True, ""


def serve(root, port):
    class Handler(BaseHTTPRequestHandler):
        def _respond(self, status, payload, ctype):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _do(self):
            ok, reason = check_origin(
                self.headers.get("Host", ""),
                self.headers.get("Content-Type", ""),
                self.command,
                port,
            )
            if not ok:
                self._respond(*_json(403, {"error": reason}))
                return
            raw_len = self.headers.get("Content-Length")
            try:
                n = int(raw_len) if raw_len else 0
            except ValueError:
                self._respond(*_json(400, {"error": "invalid Content-Length header"}))
                return
            body = self.rfile.read(n) if n else b""
            self._respond(*handle_request(root, self.command, self.path, body))

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
