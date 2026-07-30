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
    directory (real bin/), never from --root; --root is appended as the
    data-root argument, matching how tests/test_loopctl.py's run_cli()
    invokes it (subcommand, positionals, then --root). subprocess.run
    inherits the parent's environment by default, which is how the
    LOOPS_LAUNCHCTL test seam reaches the child loopctl process."""
    cmd = [sys.executable, _LOOPCTL_BIN, *argv, "--root", root]
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
