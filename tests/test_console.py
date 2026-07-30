"""Tests for bin/console.py — the roops console server (dashboard + JSON API
on 127.0.0.1). Reuses tests/test_loopctl.py's hermetic LoopsRoot fixture and
its LOOPS_LAUNCHCTL recording-stub seam, so no test here ever touches real
launchd either. `console.handle_request()` is a pure function (root, method,
path, body_bytes) -> (status, payload, content_type) — no socket is opened in
these tests; `serve()` itself is exercised only by wiring, not by a live
listener.

Mutations (pause/resume, set-schedule) run through the real bin/loopctl as a
subprocess, exactly like production — the LOOPS_LAUNCHCTL env seam flows
through via plain subprocess env inheritance (mock.patch.dict(os.environ,
...) around the call, no explicit env= passed anywhere).
"""

import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from test_loopctl import LoopsRoot

REPO_ROOT = Path(__file__).resolve().parent.parent
CONSOLE_PY = REPO_ROOT / "bin" / "console.py"

# Loaded the same way tests/test_schedule.py loads bin/schedule.py: by file
# path, not via sys.path + a plain `import console` (bin/ is not a package
# and isn't put on sys.path by the test runner). Loaded at module level so a
# missing bin/console.py fails collection with an ImportError-shaped error —
# the expected RED before Step 3 implements the module.
_spec = importlib.util.spec_from_file_location("console_mod", CONSOLE_PY)
console = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(console)


def call(fixture, method, path, body=None):
    raw = json.dumps(body).encode() if body is not None else b""
    with mock.patch.dict(os.environ, fixture.base_env()):
        return console.handle_request(fixture.root, method, path, raw)


def call_raw(fixture, method, path, raw_body):
    """Like call(), but for tests that need to send bytes that are NOT the
    JSON-encoding of a Python object — e.g. deliberately malformed JSON."""
    with mock.patch.dict(os.environ, fixture.base_env()):
        return console.handle_request(fixture.root, method, path, raw_body)


def _read(path):
    with open(path) as f:
        return f.read()


class ConsoleTestCase(unittest.TestCase):
    def setUp(self):
        self.fixture = LoopsRoot()
        self.addCleanup(self.fixture.cleanup)

    @property
    def root(self):
        return self.fixture.root

    def write_loop(self, name, schedule, enabled=None):
        lines = [
            f"name={name}",
            'description="d"',
            "type=agent",
            "engine=codex",
            f"schedule={schedule}",
        ]
        if enabled is not None:
            lines.append(f"enabled={enabled}")
        self.fixture.write_conf(name, lines)

    def conf_path(self, name):
        return os.path.join(self.fixture.loop_dir(name), "loop.conf")

    def write_plist(self, name):
        launchd_dir = os.path.join(self.root, "launchd")
        os.makedirs(launchd_dir, exist_ok=True)
        with open(os.path.join(launchd_dir, f"com.loops.{name}.plist"), "wb") as f:
            f.write(b"<plist/>")


class TestConsoleApi(ConsoleTestCase):
    def test_state_shape(self):
        self.write_loop("alpha", schedule="daily:09:00")
        self.write_plist("alpha")
        status, payload, ctype = call(self.fixture, "GET", "/api/state")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "application/json")
        loops = {loop["name"]: loop for loop in json.loads(payload)["loops"]}
        a = loops["alpha"]
        self.assertEqual(a["schedule"], "daily:09:00")
        self.assertTrue(a["plist_present"])
        self.assertIn("enabled", a)
        self.assertIn("loaded", a)

    def test_rounds_409_when_no_plist(self):
        self.write_loop("alpha", schedule="daily:09:00")
        status, payload, _ = call(
            self.fixture, "POST", "/api/loops/alpha/rounds", {"on": False}
        )
        self.assertEqual(status, 409)
        self.assertIn("loopctl install", json.loads(payload)["error"])

    def test_rounds_off_pauses(self):
        self.write_loop("alpha", schedule="daily:09:00", enabled="true")
        self.write_plist("alpha")
        status, _, _ = call(
            self.fixture, "POST", "/api/loops/alpha/rounds", {"on": False}
        )
        self.assertEqual(status, 200)
        self.assertIn("enabled=false", _read(self.conf_path("alpha")))
        self.assertIn("bootout", " ".join(self.fixture.launchctl_calls()))

    def test_schedule_400_on_bad_grammar(self):
        self.write_loop("alpha", schedule="daily:09:00")
        status, _payload, _ = call(
            self.fixture, "POST", "/api/loops/alpha/schedule", {"spec": "sometimes"}
        )
        self.assertEqual(status, 400)
        self.assertIn("schedule=daily:09:00", _read(self.conf_path("alpha")))

    def test_schedule_applies_and_regenerates_dashboard(self):
        self.write_loop("alpha", schedule="daily:09:00")
        status, _, _ = call(
            self.fixture, "POST", "/api/loops/alpha/schedule", {"spec": "interval:30m"}
        )
        self.assertEqual(status, 200)
        self.assertIn("schedule=interval:30m", _read(self.conf_path("alpha")))
        self.assertTrue(
            os.path.isfile(os.path.join(self.fixture.root, "dashboard", "loops.html"))
        )

    def test_unknown_loop_404_and_unknown_path_404(self):
        status, _, _ = call(
            self.fixture, "POST", "/api/loops/ghost/rounds", {"on": True}
        )
        self.assertEqual(status, 404)
        status, _, _ = call(self.fixture, "GET", "/api/nope")
        self.assertEqual(status, 404)

    def test_get_root_serves_dashboard_html(self):
        status, _payload, ctype = call(self.fixture, "GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)

    # -- fix-wave: malformed JSON must 400, never raise (review finding 2) --

    def test_rounds_malformed_json_400(self):
        self.write_loop("alpha", schedule="daily:09:00")
        self.write_plist("alpha")
        status, payload, _ = call_raw(
            self.fixture, "POST", "/api/loops/alpha/rounds", b"garbage"
        )
        self.assertEqual(status, 400)
        self.assertIn("invalid JSON", json.loads(payload)["error"])

    def test_schedule_malformed_json_400(self):
        self.write_loop("alpha", schedule="daily:09:00")
        status, payload, _ = call_raw(
            self.fixture, "POST", "/api/loops/alpha/schedule", b"garbage"
        )
        self.assertEqual(status, 400)
        self.assertIn("invalid JSON", json.loads(payload)["error"])

    # -- fix-wave: a spec that looks like a CLI flag must not sneak past
    # loopctl's own argparse as --help/-h (review finding 3) --

    def test_schedule_flag_injection_rejected(self):
        self.write_loop("alpha", schedule="daily:09:00")
        status, _payload, _ = call(
            self.fixture, "POST", "/api/loops/alpha/schedule", {"spec": "--help"}
        )
        self.assertNotEqual(status, 200)
        self.assertEqual(status, 400)
        self.assertIn(
            "schedule=daily:09:00", _read(self.conf_path("alpha"))
        )  # untouched

    # -- fix-wave: `on` must be an explicit JSON boolean (review finding 4) --

    def test_rounds_missing_on_field_400(self):
        self.write_loop("alpha", schedule="daily:09:00", enabled="true")
        self.write_plist("alpha")
        status, payload, _ = call(self.fixture, "POST", "/api/loops/alpha/rounds", {})
        self.assertEqual(status, 400)
        self.assertIn("on", json.loads(payload)["error"])
        self.assertIn("enabled=true", _read(self.conf_path("alpha")))  # untouched

    def test_rounds_null_on_field_400(self):
        self.write_loop("alpha", schedule="daily:09:00", enabled="true")
        self.write_plist("alpha")
        status, _, _ = call(
            self.fixture, "POST", "/api/loops/alpha/rounds", {"on": None}
        )
        self.assertEqual(status, 400)

    def test_rounds_non_bool_on_field_400(self):
        self.write_loop("alpha", schedule="daily:09:00", enabled="true")
        self.write_plist("alpha")
        status, _, _ = call(
            self.fixture, "POST", "/api/loops/alpha/rounds", {"on": "yes"}
        )
        self.assertEqual(status, 400)

    def test_rounds_on_true_resumes(self):
        self.write_loop("alpha", schedule="daily:09:00", enabled="false")
        self.write_plist("alpha")
        status, _, _ = call(
            self.fixture, "POST", "/api/loops/alpha/rounds", {"on": True}
        )
        self.assertEqual(status, 200)
        self.assertIn("enabled=true", _read(self.conf_path("alpha")))
        self.assertIn("bootstrap", " ".join(self.fixture.launchctl_calls()))


class TestCheckOrigin(unittest.TestCase):
    """console.check_origin() — the Host/Content-Type gate applied in
    serve()'s Handler before handle_request() ever runs (review finding 1).
    Pure and socket-free: no port is ever bound to exercise these."""

    def test_valid_127_host_accepted(self):
        ok, _ = console.check_origin("127.0.0.1:8929", "application/json", "POST", 8929)
        self.assertTrue(ok)

    def test_valid_localhost_host_accepted(self):
        ok, _ = console.check_origin("localhost:8929", "application/json", "POST", 8929)
        self.assertTrue(ok)

    def test_evil_host_rejected(self):
        ok, reason = console.check_origin(
            "evil.example:8929", "application/json", "POST", 8929
        )
        self.assertFalse(ok)
        self.assertIn("Host", reason)

    def test_missing_host_rejected(self):
        ok, _ = console.check_origin("", "application/json", "GET", 8929)
        self.assertFalse(ok)

    def test_post_without_json_content_type_rejected(self):
        ok, reason = console.check_origin("127.0.0.1:8929", "text/plain", "POST", 8929)
        self.assertFalse(ok)
        self.assertIn("Content-Type", reason)

    def test_post_form_urlencoded_rejected(self):
        ok, _ = console.check_origin(
            "127.0.0.1:8929", "application/x-www-form-urlencoded", "POST", 8929
        )
        self.assertFalse(ok)

    def test_post_missing_content_type_rejected(self):
        ok, _ = console.check_origin("127.0.0.1:8929", "", "POST", 8929)
        self.assertFalse(ok)

    def test_get_needs_no_content_type(self):
        ok, _ = console.check_origin("127.0.0.1:8929", "", "GET", 8929)
        self.assertTrue(ok)

    def test_post_json_with_charset_param_accepted(self):
        ok, _ = console.check_origin(
            "127.0.0.1:8929", "application/json; charset=utf-8", "POST", 8929
        )
        self.assertTrue(ok)

    def test_wrong_port_in_host_rejected(self):
        ok, _ = console.check_origin("127.0.0.1:9999", "application/json", "POST", 8929)
        self.assertFalse(ok)


if __name__ == "__main__":
    unittest.main()
