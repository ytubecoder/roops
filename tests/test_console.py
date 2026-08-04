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

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import threading
import time
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

    def test_schedule_regen_failure_still_200(self):
        self.write_loop("alpha", schedule="daily:09:00")
        # A FILE at root/dashboard makes loopctl's regen fail; the schedule
        # mutation itself succeeded, so the response must stay 200 (§13) —
        # previously this surfaced as a false `400 invalid schedule`.
        with open(os.path.join(self.root, "dashboard"), "w") as f:
            f.write("in the way")
        captured_stderr = io.StringIO()
        with contextlib.redirect_stderr(captured_stderr):
            status, _payload, _ = call(
                self.fixture,
                "POST",
                "/api/loops/alpha/schedule",
                {"spec": "interval:30m"},
            )
        self.assertEqual(status, 200)
        self.assertIn("schedule=interval:30m", _read(self.conf_path("alpha")))
        # The regen really failed (dashboard/ is a file, not a dir, so no
        # loops.html could have been written) — proves this isn't vacuously
        # passing if the injection stops injecting.
        self.assertFalse(
            os.path.isfile(os.path.join(self.root, "dashboard", "loops.html"))
        )
        # The console layer (Fix 2) passes the child loopctl's warning through
        # to its own stderr — not just captured inside subprocess.run's
        # result, which _loopctl already swallows into r.stderr.
        self.assertIn("warning: dashboard regen failed", captured_stderr.getvalue())

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

    # -- final-review wave: `spec` must be a real JSON string (F6) --

    def test_schedule_non_string_spec_400(self):
        self.write_loop("alpha", schedule="daily:09:00")
        status, payload, _ = call(
            self.fixture, "POST", "/api/loops/alpha/schedule", {"spec": 7}
        )
        self.assertEqual(status, 400)
        self.assertIn("spec", json.loads(payload)["error"])
        self.assertIn("schedule=daily:09:00", _read(self.conf_path("alpha")))

    def test_schedule_missing_spec_400(self):
        self.write_loop("alpha", schedule="daily:09:00")
        status, _, _ = call(self.fixture, "POST", "/api/loops/alpha/schedule", {})
        self.assertEqual(status, 400)
        self.assertIn("schedule=daily:09:00", _read(self.conf_path("alpha")))

    # -- re-review: a NUL in `spec` made subprocess.run raise `ValueError:
    # embedded null byte` out of handle_request — the same dropped-connection
    # mode the negative Content-Length fix eliminated --

    def test_schedule_nul_byte_in_spec_400(self):
        self.write_loop("alpha", schedule="daily:09:00")
        status, payload, _ = call(
            self.fixture,
            "POST",
            "/api/loops/alpha/schedule",
            {"spec": "daily:09\x0000"},
        )
        self.assertEqual(status, 400)
        self.assertIn("NUL", json.loads(payload)["error"])
        self.assertIn("schedule=daily:09:00", _read(self.conf_path("alpha")))

    def test_nul_byte_in_loop_name_never_reaches_a_route(self):
        # the route regexes admit only [A-Za-z0-9_-], so a NUL-bearing name is a
        # route miss (404), never an exception out of handle_request
        self.write_loop("alpha", schedule="daily:09:00")
        for path in ("/api/loops/al\x00pha/schedule", "/api/loops/al\x00pha/rounds"):
            status, _, _ = call(self.fixture, "POST", path, {"spec": "interval:5m"})
            self.assertEqual(status, 404, path)
        self.assertIn("schedule=daily:09:00", _read(self.conf_path("alpha")))

    # -- final-review wave: spec="manual" is an UNINSTALL in _apply_schedule
    # (bootout + remove the plist); install/uninstall stay CLI-only (§13, §8.1),
    # so the console refuses it before loopctl is ever invoked (F5) --

    def test_schedule_manual_refused_and_plist_survives(self):
        self.write_loop("alpha", schedule="daily:09:00")
        self.write_plist("alpha")
        plist = os.path.join(self.root, "launchd", "com.loops.alpha.plist")
        status, payload, _ = call(
            self.fixture, "POST", "/api/loops/alpha/schedule", {"spec": "manual"}
        )
        self.assertEqual(status, 400)
        self.assertIn("loopctl uninstall alpha", json.loads(payload)["error"])
        self.assertTrue(os.path.isfile(plist))  # NOT uninstalled
        self.assertIn("schedule=daily:09:00", _read(self.conf_path("alpha")))
        self.assertNotIn("bootout", " ".join(self.fixture.launchctl_calls()))

    def test_rounds_on_true_resumes(self):
        self.write_loop("alpha", schedule="daily:09:00", enabled="false")
        self.write_plist("alpha")
        status, _, _ = call(
            self.fixture, "POST", "/api/loops/alpha/rounds", {"on": True}
        )
        self.assertEqual(status, 200)
        self.assertIn("enabled=true", _read(self.conf_path("alpha")))
        self.assertIn("bootstrap", " ".join(self.fixture.launchctl_calls()))


class TestReportRoute(ConsoleTestCase):
    """GET /reports/<loop>/<file> — the dashboard's per-loop report links
    (`../reports/<name>/latest.html`) 404'd under the console before this route
    existed. Narrow by construction: a regex allowlist with no '/' and no '%',
    plus a realpath containment check under <root>/reports."""

    def write_report(self, loop, filename, body):
        d = os.path.join(self.root, "reports", loop)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, filename), "w") as f:
            f.write(body)

    def test_report_html_is_served(self):
        self.write_report("kagi-ban", "latest.html", "<h1>report</h1>")
        status, payload, ctype = call(
            self.fixture, "GET", "/reports/kagi-ban/latest.html"
        )
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"<h1>report</h1>", payload)

    def test_report_json_and_md_content_types(self):
        self.write_report("kagi-ban", "latest.json", '{"a": 1}')
        self.write_report("kagi-ban", "latest.md", "# hi")
        _, _, ctype = call(self.fixture, "GET", "/reports/kagi-ban/latest.json")
        self.assertEqual(ctype, "application/json")
        _, _, ctype = call(self.fixture, "GET", "/reports/kagi-ban/latest.md")
        self.assertIn("text/plain", ctype)

    def test_unknown_extension_is_octet_stream(self):
        self.write_report("kagi-ban", "shot.png", "notreallyapng")
        status, _, ctype = call(self.fixture, "GET", "/reports/kagi-ban/shot.png")
        self.assertEqual(status, 200)
        self.assertEqual(ctype, "application/octet-stream")

    def test_unknown_file_404s(self):
        self.write_report("kagi-ban", "latest.html", "<h1>report</h1>")
        status, _, _ = call(self.fixture, "GET", "/reports/kagi-ban/nope.html")
        self.assertEqual(status, 404)
        status, _, _ = call(self.fixture, "GET", "/reports/ghost/latest.html")
        self.assertEqual(status, 404)

    def test_traversal_is_refused(self):
        # a real secret one directory above the reports root
        with open(os.path.join(self.root, "secret.txt"), "w") as f:
            f.write("TOPSECRET")
        for path in (
            "/reports/kagi-ban/../../secret.txt",  # literal ../
            "/reports/../secret.txt",
            "/reports/kagi-ban/..%2f..%2fsecret.txt",  # percent-encoded (never decoded)
            "/reports/kagi-ban/..",  # bare .. -> resolves to the reports dir itself
            "/reports/kagi-ban/",  # no directory listing
            "/reports/kagi-ban",
        ):
            status, payload, _ = call(self.fixture, "GET", path)
            self.assertEqual(status, 404, path)
            self.assertNotIn(b"TOPSECRET", payload, path)

    def test_symlink_out_of_the_tree_is_refused(self):
        with open(os.path.join(self.root, "secret.txt"), "w") as f:
            f.write("TOPSECRET")
        d = os.path.join(self.root, "reports", "kagi-ban")
        os.makedirs(d, exist_ok=True)
        os.symlink(os.path.join(self.root, "secret.txt"), os.path.join(d, "leak.md"))
        status, payload, _ = call(self.fixture, "GET", "/reports/kagi-ban/leak.md")
        self.assertEqual(status, 404)
        self.assertNotIn(b"TOPSECRET", payload)

    def test_post_to_a_report_path_is_not_a_route(self):
        self.write_report("kagi-ban", "latest.html", "<h1>report</h1>")
        status, _, _ = call(self.fixture, "POST", "/reports/kagi-ban/latest.html", {})
        self.assertEqual(status, 404)


class TestReportSandboxHeader(unittest.TestCase):
    """console.response_headers() — the CSP that keeps loop-authored report HTML
    off the mutation API's origin. Report pages may legally carry an INLINE
    <script> (bin/page_envelope.py blocks only external-fetch markup), and the
    F4 route made those pages same-origin with /api/*; `sandbox allow-scripts`
    (no allow-same-origin) puts them in an opaque origin instead."""

    def _csp(self, path):
        return dict(console.response_headers(path)).get("Content-Security-Policy")

    def test_report_pages_are_sandboxed(self):
        for path in (
            "/reports/kagi-ban/latest.html",
            "/reports/kagi-ban/latest.json",
            "/reports/a_b-c/2026-07-30-0032.html",
        ):
            self.assertEqual(self._csp(path), "sandbox allow-scripts", path)

    def test_sandbox_never_grants_same_origin(self):
        # allow-scripts + allow-same-origin together let the page remove its own
        # sandbox — the combination must never appear
        csp = self._csp("/reports/kagi-ban/latest.html")
        self.assertNotIn("allow-same-origin", csp)

    def test_dashboard_and_api_are_not_sandboxed(self):
        for path in (
            "/",
            "/loops.html",
            "/api/state",
            "/api/loops/alpha/rounds",
            "/api/loops/alpha/schedule",
        ):
            self.assertIsNone(self._csp(path), path)

    def test_query_string_does_not_defeat_the_match(self):
        self.assertEqual(
            self._csp("/reports/kagi-ban/latest.html?v=2"), "sandbox allow-scripts"
        )


class TestReportsHtmlRetired(ConsoleTestCase):
    """WP1: /reports.html is retired; per-loop report files stay served."""

    def write_report(self, loop, filename, body):
        d = os.path.join(self.root, "reports", loop)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, filename), "w") as f:
            f.write(body)

    def test_reports_html_is_404(self):
        status, _, _ = call(self.fixture, "GET", "/reports.html")
        self.assertEqual(status, 404)

    def test_per_loop_report_still_served(self):
        self.write_report("kagi-ban", "latest.html", "<h1>report</h1>")
        status, payload, ctype = call(
            self.fixture, "GET", "/reports/kagi-ban/latest.html"
        )
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"<h1>report</h1>", payload)

    def test_dashboard_routes_still_200(self):
        # ensure loops.html exists so serve can return it
        dash = os.path.join(self.root, "dashboard")
        os.makedirs(dash, exist_ok=True)
        with open(os.path.join(dash, "loops.html"), "w") as f:
            f.write("<html><body>garden</body></html>")
        for path in ("/", "/loops.html"):
            status, payload, _ = call(self.fixture, "GET", path)
            self.assertEqual(status, 200, path)
            self.assertIn(b"garden", payload)


class TestParseContentLength(unittest.TestCase):
    """console.parse_content_length() — the guard between a client-supplied
    header and rfile.read(). Pure, like check_origin(): no port is bound."""

    def test_missing_header_is_zero(self):
        n, err = console.parse_content_length(None)
        self.assertEqual((n, err), (0, None))
        n, err = console.parse_content_length("")
        self.assertEqual((n, err), (0, None))

    def test_positive_value_passes_through(self):
        n, err = console.parse_content_length("12")
        self.assertEqual((n, err), (12, None))

    def test_non_numeric_400(self):
        n, err = console.parse_content_length("banana")
        self.assertIsNone(n)
        self.assertEqual(err[0], 400)
        self.assertIn("Content-Length", json.loads(err[1])["error"])

    def test_negative_400_not_an_exception(self):
        # int("-5") succeeds, and rfile.read(-5) would raise ValueError straight out
        # of the handler (connection dropped, no HTTP response at all)
        n, err = console.parse_content_length("-5")
        self.assertIsNone(n)
        self.assertEqual(err[0], 400)

    def test_minus_one_400_would_otherwise_read_to_eof(self):
        # rfile.read(-1) parks the serving thread until the client closes
        n, err = console.parse_content_length("-1")
        self.assertIsNone(n)
        self.assertEqual(err[0], 400)


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

    # --- §13.1 amendment (B-22): --allow-host extends the exact-match set ---

    def test_allow_host_accepts_configured_host_get(self):
        ok, _ = console.check_origin(
            "loops.example.ts.net",
            "",
            "GET",
            8929,
            allow_hosts=("loops.example.ts.net",),
        )
        self.assertTrue(ok)

    def test_allow_host_accepts_configured_host_post(self):
        ok, _ = console.check_origin(
            "loops.example.ts.net",
            "application/json",
            "POST",
            8929,
            allow_hosts=("loops.example.ts.net",),
        )
        self.assertTrue(ok)

    def test_allow_host_is_exact_match_not_suffix(self):
        # A rebound/attacker hostname must still fail even with an allowlist set.
        ok, reason = console.check_origin(
            "evil.example",
            "application/json",
            "POST",
            8929,
            allow_hosts=("loops.example.ts.net",),
        )
        self.assertFalse(ok)
        self.assertIn("Host", reason)

    def test_allow_host_subdomain_of_allowed_rejected(self):
        ok, _ = console.check_origin(
            "x.loops.example.ts.net",
            "application/json",
            "POST",
            8929,
            allow_hosts=("loops.example.ts.net",),
        )
        self.assertFalse(ok)

    def test_allow_host_keeps_loopback_entries(self):
        ok, _ = console.check_origin(
            "127.0.0.1:8929",
            "application/json",
            "POST",
            8929,
            allow_hosts=("loops.example.ts.net",),
        )
        self.assertTrue(ok)

    def test_allow_host_post_still_needs_json_content_type(self):
        # The allowlist widens ONLY the Host set — the forms-CSRF gate is untouched.
        ok, reason = console.check_origin(
            "loops.example.ts.net",
            "application/x-www-form-urlencoded",
            "POST",
            8929,
            allow_hosts=("loops.example.ts.net",),
        )
        self.assertFalse(ok)
        self.assertIn("Content-Type", reason)

    def test_default_allow_hosts_empty_behavior_unchanged(self):
        ok, _ = console.check_origin(
            "loops.example.ts.net", "application/json", "POST", 8929
        )
        self.assertFalse(ok)


# ---------------------------------------------------------------------------
# B-13 — manual run trigger: POST /api/loops/<name>/run + GET /api/run/status
# (openspec/changes/b-13-dashboard-run-now-2026-08-02/specs/manual-run-trigger)
#
# Hermetic like everything above: console._loopctl is replaced by LoopctlStub,
# so no engine, no subprocess and no launchctl is ever reached from a worker
# thread. Every wait here is event-driven or deadline-bounded — no test sleeps
# a guessed interval and hopes the worker got there.
# ---------------------------------------------------------------------------

WORKER_TIMEOUT = 10.0  # hard ceiling on every wait in this section
POLL_INTERVAL = 0.01
# The worker's terminal-status write and its slot release happen in one
# `finally`; only that window may legitimately answer 409 after a job ended.
SLOT_HANDOFF_TIMEOUT = 1.0

# The dict GET /api/run/status returns, enumerated by the spec ("Run status is
# pollable"). Named once so the shape can only be changed deliberately.
RUN_STATUS_FIELDS = frozenset(
    ("running", "loop", "started_at", "finished_at", "exit_code", "ok", "error")
)


def fresh_console():
    """A freshly executed copy of bin/console.py: a new module object with its
    own module-level state — i.e. a console that has just booted, which is
    exactly what the spec's idle scenario ("since the console booted") is
    about. The run job slot is console-wide/module-global, so per-test
    isolation means a per-test module: no test can inherit another test's
    terminal snapshot or a stranded slot."""
    spec = importlib.util.spec_from_file_location("console_mod_b13", CONSOLE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RunCall:
    """One `loopctl run` invocation seen by the stub, with its OWN events.

    Per-call events, not one set shared by every call: a test that drives two
    jobs in sequence (fire, finish, fire again) would otherwise have its second
    call sail straight through a `release` the first one already consumed, and
    "the second job is in flight" would be a hope rather than a fact."""

    def __init__(self, argv):
        self.argv = list(argv)
        self.entered = threading.Event()  # this call has begun
        self.release = threading.Event()  # the test lets it finish
        self.returned = threading.Event()  # ...and it has left the stub
        self.thread = None  # the worker thread that made it


class LoopctlStub:
    """Stands in for console._loopctl in the run-worker tests.

    A `run` call parks until the test releases it, so "a job is in flight" is a
    fact the test controls rather than a race it hopes for. Any other verb (the
    best-effort `dashboard` regen the worker fires afterwards) returns success
    immediately, so stubbing this one seam keeps the whole worker off any real
    subprocess. `returncode`/`stderr`/`raises` are read when a call RETURNS, so
    a test can set the outcome after the call has already parked."""

    def __init__(self, returncode=0, stdout="", stderr="", raises=None):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.raises = raises
        self._lock = threading.Lock()
        self._new_run = threading.Condition(self._lock)
        self._calls = []
        self._runs = []

    def calls(self):
        with self._lock:
            return [list(c) for c in self._calls]

    def run_calls(self):
        return [c for c in self.calls() if c and c[0] == "run"]

    def runs(self):
        with self._lock:
            return list(self._runs)

    def wait_run(self, n=1, timeout=WORKER_TIMEOUT):
        """Blocks until the stub has entered its n-th `run` call; returns it."""
        with self._new_run:
            arrived = self._new_run.wait_for(
                lambda: len(self._runs) >= n, timeout=timeout
            )
            if not arrived:
                raise AssertionError(
                    f"the worker never made run call #{n} "
                    f"(saw {len(self._runs)}: {[c.argv for c in self._runs]})"
                )
            return self._runs[n - 1]

    def release_all(self):
        """Unparks every run call and JOINS the worker thread that made it, so
        no daemon thread outlives its test still holding the console's slot.
        Joining the handle beats guessing from an event: a worker that has left
        the stub may still be inside the module's `finally`."""
        for call in self.runs():
            call.release.set()
        for call in self.runs():
            call.returned.wait(timeout=WORKER_TIMEOUT)
            if (
                call.thread is not None
                and call.thread is not threading.current_thread()
            ):
                call.thread.join(timeout=WORKER_TIMEOUT)

    def __call__(self, root, argv):
        argv = list(argv)
        with self._lock:
            self._calls.append(argv)
        if not argv or argv[0] != "run":
            return subprocess.CompletedProcess(
                args=argv, returncode=0, stdout="", stderr=""
            )
        call = RunCall(argv)
        call.thread = threading.current_thread()
        with self._new_run:
            self._runs.append(call)
            self._new_run.notify_all()
        call.entered.set()
        try:
            if not call.release.wait(timeout=WORKER_TIMEOUT):
                raise AssertionError("LoopctlStub: run call was never released")
            if self.raises is not None:
                raise self.raises
            return subprocess.CompletedProcess(
                args=argv,
                returncode=self.returncode,
                stdout=self.stdout,
                stderr=self.stderr,
            )
        finally:
            call.returned.set()


def wait_for(predicate, timeout=WORKER_TIMEOUT, interval=POLL_INTERVAL):
    """Blocks until predicate() is truthy or the deadline passes; returns the
    last value seen. Bounded by construction — the alternative (a bare sleep
    long enough to "probably" be enough) is how a suite gets flaky."""
    deadline = time.monotonic() + timeout
    while True:
        value = predicate()
        if value or time.monotonic() > deadline:
            return value
        time.sleep(interval)


def posted_state(payload):
    """The `state` dict out of a 202 body.

    design.md decision 1 (amended 2026-08-03) fixes the shape: nested
    `{"ok": true, "state": <the 7-field run-status dict>}`, matching the
    sibling mutations. Flat is no longer legal, so nothing here goes looking
    for it — and the field set is checked, so an all-null husk with the right
    keys still has to survive the CONTENT assertions at the call site."""
    obj = json.loads(payload)
    if not isinstance(obj, dict) or "state" not in obj:
        raise AssertionError(f"202 body carries no `state` key: {obj!r}")
    state = obj["state"]
    if not isinstance(state, dict) or set(state) != set(RUN_STATUS_FIELDS):
        raise AssertionError(
            f"202 `state` is not the 7-field run-status dict: {state!r}"
        )
    return state


def deep_value(obj, key):
    """First value for `key` anywhere in a nested JSON body. The 409 payload's
    exact shape is not fixed by the design — only its content is ("names the
    running loop and its started_at")."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = deep_value(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = deep_value(value, key)
            if found is not None:
                return found
    return None


class RunTriggerTestCase(ConsoleTestCase):
    """Shared rig for the B-13 tests: a per-test console module, a stubbed
    _loopctl, and the launchctl env seam held open for the WHOLE test (not just
    the duration of one handle_request) so a worker thread can never outrun the
    patch and reach the real binary."""

    def setUp(self):
        super().setUp()
        self.console = fresh_console()
        self.stub = LoopctlStub()
        self.console._loopctl = self.stub
        env = mock.patch.dict(os.environ, self.fixture.base_env())
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.stub.release_all)

    # -- request helpers (the HTTP layer, exactly like the tests above) --

    def post_run(self, name, raw=None):
        body = b"{}" if raw is None else raw
        return self.console.handle_request(
            self.root, "POST", f"/api/loops/{name}/run", body
        )

    def get_status(self):
        status, payload, ctype = self.console.handle_request(
            self.root, "GET", "/api/run/status", b""
        )
        self.assertEqual(status, 200, f"GET /api/run/status -> {status} {payload!r}")
        self.assertEqual(ctype, "application/json")
        return json.loads(payload)

    def post_run_when_free(self, name):
        """POSTs a run, retrying only while the answer is 409. The worker
        publishes its terminal status and releases the slot in the same
        `finally`, and nothing in the contract fixes which of the two happens
        first — so a 409 in the instant after the job goes terminal is legal.
        The deadline is short on purpose: that handoff is sub-millisecond, and
        a generous one would let a genuinely slow (or stuck) slot look fine."""
        deadline = time.monotonic() + SLOT_HANDOFF_TIMEOUT
        while True:
            resp = self.post_run(name)
            if resp[0] != 409 or time.monotonic() > deadline:
                return resp
            time.sleep(POLL_INTERVAL)

    # -- worker synchronisation --

    def wait_idle(self):
        """Polls until the job is REALLY terminal: `running` false AND
        `finished_at` published.

        An implementation may flip `running` before it writes the terminal
        fields — the audit's counter-implementation did exactly that, with a
        scheduler hiccup in between — and returning on `running` alone turns
        that into a misleading `None != 0` three assertions later, pointing at
        the exit code instead of at the publish order. The timeout names
        whichever condition was not met."""
        deadline = time.monotonic() + WORKER_TIMEOUT
        while True:
            snap = self.get_status()
            if not snap["running"] and snap["finished_at"] is not None:
                return snap
            if time.monotonic() > deadline:
                unmet = (
                    "`running` is still true"
                    if snap["running"]
                    else "`finished_at` is still null"
                )
                self.fail(
                    f"run job never became terminal within {WORKER_TIMEOUT}s "
                    f"({unmet}): {snap!r}"
                )
            time.sleep(POLL_INTERVAL)

    def finish_run(self, call):
        """Releases a parked run call and returns the terminal snapshot."""
        call.release.set()
        return self.wait_idle()


class TestRunEndpointRequestPath(RunTriggerTestCase):
    """POST /api/loops/<name>/run — the request path itself. Spec requirement:
    "Console can start a supervised run in the background"."""

    def test_fires_a_loop_that_has_no_plist(self):
        # deliberately NOT installed: no write_plist() call anywhere here. The
        # rounds endpoint 409s in this exact state; this route must not.
        self.write_loop("alpha", schedule="daily:09:00")
        status, payload, ctype = self.post_run("alpha")
        self.assertEqual(status, 202, payload)
        self.assertEqual(ctype, "application/json")
        self.assertIs(json.loads(payload)["ok"], True)
        self.stub.wait_run(1)
        # started through the ONE _loopctl code path, with the loop's own name
        self.assertIn(["run", "alpha"], self.stub.run_calls())

    def test_202_snapshot_describes_the_job_it_just_started(self):
        """design.md decision 1 (amended): the 202 body is
        `{"ok": true, "state": <7-field dict>}` and that dict describes THIS
        job — an all-null husk with the right keys is not a snapshot."""
        self.write_loop("alpha", schedule="daily:09:00")
        _status, payload, _ = self.post_run("alpha")
        state = posted_state(payload)
        self.assertIs(state["running"], True)
        self.assertEqual(state["loop"], "alpha")
        self.assertTrue(state["started_at"])
        self.assertIsNone(state["ok"])  # not judged yet — the run just began

    def test_responds_before_the_run_subprocess_returns(self):
        """ "The request path MUST NOT invoke loopctl synchronously": the 202 is
        answered while the run call is still parked inside the stub."""
        self.write_loop("alpha", schedule="daily:09:00")
        status, _payload, _ = self.post_run("alpha")
        self.assertEqual(status, 202)
        call = self.stub.wait_run(1)
        self.assertFalse(call.returned.is_set())
        snap = self.get_status()
        self.assertIs(snap["running"], True)
        self.assertEqual(snap["loop"], "alpha")
        self.assertTrue(snap["started_at"])

    def test_fires_a_paused_loop(self):
        """ "regardless of its installed, ENABLED, or schedule state". Pausing
        takes a loop off the schedule, which is precisely when a human wants to
        fire it by hand; copying the rounds endpoint's guard onto this route
        would defeat the whole ticket."""
        self.write_loop("alpha", schedule="daily:09:00", enabled="false")
        status, payload, _ = self.post_run("alpha")
        self.assertEqual(status, 202, payload)
        self.stub.wait_run(1)
        self.assertIn(["run", "alpha"], self.stub.run_calls())

    def test_fires_a_loop_whose_schedule_is_manual(self):
        # `manual` means "never on a timer", not "never runnable" — it is the
        # off-schedule state the CLI uses, and B-13 exists to reach it
        self.write_loop("alpha", schedule="manual")
        status, payload, _ = self.post_run("alpha")
        self.assertEqual(status, 202, payload)
        self.stub.wait_run(1)
        self.assertIn(["run", "alpha"], self.stub.run_calls())

    def test_unknown_loop_404_and_no_worker_starts(self):
        self.write_loop("alpha", schedule="daily:09:00")
        status, payload, _ = self.post_run("ghost")
        self.assertEqual(status, 404)
        self.assertEqual(json.loads(payload)["error"], "unknown loop: ghost")
        self.assertEqual(self.stub.calls(), [])
        self.assertEqual(self.stub.runs(), [])

    def test_non_object_json_body_400_and_no_worker_starts(self):
        # uniform with the rounds/schedule endpoints: `_parse_json_object`'s
        # "invalid JSON body", never an exception out of handle_request
        self.write_loop("alpha", schedule="daily:09:00")
        for raw in (b'"x"', b"[1]", b"7", b"garbage"):
            status, payload, _ = self.post_run("alpha", raw=raw)
            self.assertEqual(status, 400, raw)
            self.assertIn("invalid JSON", json.loads(payload)["error"], raw)
        self.assertEqual(self.stub.run_calls(), [])


class TestRunRouteMethodPinning(RunTriggerTestCase):
    """design.md decision 1 (amended 2026-08-03): the run route matches POST
    ONLY, the status route GET ONLY, and anything else on either path is a
    route miss that starts no worker.

    This is a security pin, not tidiness: §13.1's Content-Type gate only fires
    on POST, so a run route that also matched GET would be reachable by a plain
    cross-origin browser GET carrying a valid Host — a CSRF that spends engine
    budget. Each test carries its positive control, because "GET 404s" on its
    own is equally true of a console with no run route at all."""

    def _request(self, method, path, body=b"{}"):
        return self.console.handle_request(self.root, method, path, body)

    def test_run_route_answers_post_only(self):
        self.write_loop("alpha", schedule="daily:09:00")
        for method in ("GET", "HEAD", "PUT", "DELETE", "PATCH"):
            status, payload, _ = self._request(method, "/api/loops/alpha/run")
            self.assertEqual(status, 404, method)
            # the generic route-miss body, not "unknown loop" — proof the path
            # was never matched, rather than matched and then refused
            self.assertEqual(json.loads(payload)["error"], "not found", method)
            self.assertEqual(self.stub.run_calls(), [], method)
        self.assertEqual(self.post_run("alpha")[0], 202)  # positive control

    def test_status_route_answers_get_only(self):
        self.write_loop("alpha", schedule="daily:09:00")
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            status, payload, _ = self._request(method, "/api/run/status")
            self.assertEqual(status, 404, method)
            self.assertEqual(json.loads(payload)["error"], "not found", method)
            self.assertEqual(self.stub.run_calls(), [], method)
        self.assertIs(self.get_status()["running"], False)  # positive control


class TestRunJobSlot(RunTriggerTestCase):
    """Spec requirement: "One run job at a time, console-wide"."""

    def test_second_fire_while_busy_is_409_naming_the_running_loop(self):
        self.write_loop("alpha", schedule="daily:09:00")
        self.write_loop("beta", schedule="daily:10:00")
        self.assertEqual(self.post_run("alpha")[0], 202)
        self.stub.wait_run(1)
        # the slot is console-wide, so ANOTHER loop is refused too, not just alpha
        for name in ("beta", "alpha"):
            status, payload, _ = self.post_run(name)
            self.assertEqual(status, 409, name)
            obj = json.loads(payload)
            self.assertIn("error", obj)
            self.assertIn("alpha", json.dumps(obj), name)  # names the running loop
            self.assertTrue(deep_value(obj, "started_at"), name)  # and its start time
        self.assertEqual(len(self.stub.run_calls()), 1)


class TestRunStatusRoute(RunTriggerTestCase):
    """Spec requirement: "Run status is pollable" + "Worker records, never
    interprets" (the exit code is copied, not judged)."""

    def test_idle_console_reports_running_false_with_null_fields(self):
        snap = self.get_status()
        self.assertEqual(set(snap), set(RUN_STATUS_FIELDS))  # the enumerated shape
        self.assertIs(snap["running"], False)
        for field in sorted(RUN_STATUS_FIELDS - {"running"}):
            self.assertIsNone(snap[field], field)

    def test_successful_run_reports_exit_zero_ok_true_and_no_error(self):
        self.write_loop("alpha", schedule="daily:09:00")
        self.stub.returncode = 0
        self.assertEqual(self.post_run("alpha")[0], 202)
        snap = self.finish_run(self.stub.wait_run(1))
        self.assertIs(snap["running"], False)
        self.assertEqual(snap["loop"], "alpha")
        self.assertEqual(snap["exit_code"], 0)
        self.assertIs(snap["ok"], True)
        self.assertIsNone(snap["error"])
        self.assertTrue(snap["finished_at"])

    def test_failed_run_keeps_the_exit_code_verbatim_and_a_bounded_stderr_tail(self):
        """`ok` is exit==0 and nothing else, and `error` keeps the END of a
        noisy stderr. The 4096-char bound and "tail, not head" are design.md
        decision 2 (amended 2026-08-03), so this pins the contract rather than
        a number the test invented."""
        self.write_loop("alpha", schedule="daily:09:00")
        noisy = (
            "HEAD-MARKER\n"
            + "".join(f"filler line {i:04d} {'x' * 40}\n" for i in range(400))
            + "TAIL-MARKER\n"
        )
        self.assertEqual(self.post_run("alpha")[0], 202)
        call = self.stub.wait_run(1)
        self.stub.returncode = 7
        self.stub.stderr = noisy
        snap = self.finish_run(call)
        self.assertEqual(snap["exit_code"], 7)  # verbatim: not 1, not True
        self.assertIs(snap["ok"], False)
        self.assertIn("TAIL-MARKER", snap["error"])
        self.assertNotIn("HEAD-MARKER", snap["error"])
        self.assertLessEqual(len(snap["error"]), 4096)
        # ...and truncation actually happened, so the bound is not vacuous
        self.assertLess(len(snap["error"]), len(noisy))

    def test_terminal_snapshot_stays_readable_across_repeated_polls(self):
        self.write_loop("alpha", schedule="daily:09:00")
        self.assertEqual(self.post_run("alpha")[0], 202)
        first = self.finish_run(self.stub.wait_run(1))
        for _ in range(3):
            self.assertEqual(self.get_status(), first)

    def test_second_job_replaces_the_terminal_snapshot(self):
        """design.md decision 2 (amended): starting a job RESETS the snapshot —
        `loop` becomes the new name and the terminal fields go back to null. A
        snapshot that keeps the finished job's name reports the wrong loop as
        in flight; one that keeps its exit code reports a verdict on a run that
        has not happened yet."""
        self.write_loop("alpha", schedule="daily:09:00")
        self.write_loop("beta", schedule="daily:10:00")
        self.assertEqual(self.post_run("alpha")[0], 202)
        first = self.stub.wait_run(1)
        self.stub.returncode = 7
        self.stub.stderr = "alpha exploded\n"
        terminal = self.finish_run(first)
        self.assertEqual(terminal["exit_code"], 7)  # a real terminal state to clear

        self.assertEqual(self.post_run_when_free("beta")[0], 202)
        self.stub.wait_run(2)
        snap = self.get_status()
        self.assertIs(snap["running"], True)
        self.assertEqual(snap["loop"], "beta")
        for field in ("finished_at", "exit_code", "ok", "error"):
            self.assertIsNone(snap[field], field)


class TestRunWorkerCrashSafety(RunTriggerTestCase):
    """Spec requirement: "Worker cannot strand the job slot"."""

    def test_worker_exception_ends_the_job_and_frees_the_slot(self):
        self.write_loop("alpha", schedule="daily:09:00")
        self.stub.raises = RuntimeError("worker blew up")
        self.assertEqual(self.post_run("alpha")[0], 202)
        snap = self.finish_run(self.stub.wait_run(1))
        self.assertIs(snap["running"], False)
        self.assertIs(snap["ok"], False)
        # nothing stranded: the slot takes the next fire
        status, _payload, _ = self.post_run_when_free("alpha")
        self.assertEqual(status, 202)
        self.finish_run(self.stub.wait_run(2))


class TestRunIsTheOnlyFiringRoute(RunTriggerTestCase):
    """Spec requirement: "Only console path that fires a run" — set-schedule
    never kickstarts, rounds only pauses/resumes. The contrast at the end is
    what keeps this from being a statement about a console with no run route."""

    def test_only_the_run_route_starts_a_run(self):
        self.write_loop("alpha", schedule="daily:09:00", enabled="true")
        self.write_plist("alpha")
        for path, body in (
            ("/api/loops/alpha/rounds", {"on": False}),
            ("/api/loops/alpha/schedule", {"spec": "interval:30m"}),
        ):
            status, payload, _ = self.console.handle_request(
                self.root, "POST", path, json.dumps(body).encode()
            )
            self.assertEqual(status, 200, f"{path} -> {payload!r}")
        # those endpoints really did their own work through the same seam...
        self.assertIn(["pause", "alpha"], self.stub.calls())
        self.assertIn(["set-schedule", "alpha", "interval:30m"], self.stub.calls())
        self.assertEqual(self.stub.run_calls(), [])  # ...and fired no run
        # contrast: the run route does
        self.assertEqual(self.post_run("alpha")[0], 202)
        self.stub.wait_run(1)
        self.assertEqual(self.stub.run_calls(), [["run", "alpha"]])


class TestRunDashboardRegen(RunTriggerTestCase):
    """Spec requirement: "Worker records, never interprets" — the best-effort
    regen half. design.md decision 3 (amended 2026-08-03): the regen runs after
    the subprocess exits REGARDLESS of exit code, warns on failure, and never
    alters the recorded outcome."""

    def exploding_regen(self):
        """Replaces console._regen_dashboard and records that it was reached —
        stderr alone would leave "did it even run?" to inference."""
        seen = []

        def boom(root):
            seen.append(root)
            raise RuntimeError("regen exploded")

        self.console._regen_dashboard = boom
        return seen

    def run_once_with_exploding_regen(self, returncode):
        seen = self.exploding_regen()
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            self.assertEqual(self.post_run("alpha")[0], 202)
            call = self.stub.wait_run(1)
            self.stub.returncode = returncode
            self.stub.stderr = "run stderr\n"
            snap = self.finish_run(call)
            # the warning may be written either side of the terminal-status
            # write, so wait for it on the same bounded deadline
            wait_for(lambda: "regen" in captured.getvalue().lower())
        self.assertEqual(seen, [self.root])  # regen was really reached
        return snap, captured.getvalue().lower()

    def test_regen_failure_after_a_successful_run_keeps_ok_true_and_warns(self):
        self.write_loop("alpha", schedule="daily:09:00")
        snap, err = self.run_once_with_exploding_regen(0)
        self.assertIs(snap["ok"], True)
        self.assertEqual(snap["exit_code"], 0)
        self.assertIn("warning", err)
        self.assertIn("regen", err)

    def test_regen_runs_after_a_failed_run_too(self):
        # a failed run leaves the dashboard just as stale as a successful one,
        # so the regen is not on the success path
        self.write_loop("alpha", schedule="daily:09:00")
        snap, err = self.run_once_with_exploding_regen(5)
        self.assertIs(snap["ok"], False)
        self.assertEqual(snap["exit_code"], 5)
        self.assertIn("warning", err)
        self.assertIn("regen", err)


class TestRunRouteOriginGate(RunTriggerTestCase):
    """Spec requirement: "Only console path that fires a run" — the §13.1
    Host/Content-Type gate applies to the new route unchanged.

    check_origin() is path-agnostic, so a bad-Host assertion ALONE would pass
    against a console that has no run route at all — a tautology. Each test
    here therefore pairs the refusal with its positive control: the same
    request from a loopback origin DOES fire the run, which is the only thing
    that makes "403 before any worker starts" a statement about this route."""

    PORT = 8929

    def gated(
        self,
        path,
        body=b"{}",
        host=f"127.0.0.1:{PORT}",
        ctype="application/json",
        method="POST",
    ):
        """serve()'s Handler ordering without a socket: check_origin() first,
        handle_request() only if it passes."""
        ok, reason = self.console.check_origin(host, ctype, method, self.PORT)
        if not ok:
            return self.console._json(403, {"error": reason})
        return self.console.handle_request(self.root, method, path, body)

    def test_non_loopback_host_is_refused_before_any_worker_starts(self):
        self.write_loop("alpha", schedule="daily:09:00")
        status, payload, _ = self.gated(
            "/api/loops/alpha/run", host="evil.example:8929"
        )
        self.assertEqual(status, 403)
        self.assertIn("Host", json.loads(payload)["error"])
        self.assertEqual(self.stub.calls(), [])
        # positive control: the same request from loopback fires the run
        self.assertEqual(self.gated("/api/loops/alpha/run")[0], 202)

    def test_non_json_content_type_is_refused_before_any_worker_starts(self):
        self.write_loop("alpha", schedule="daily:09:00")
        status, payload, _ = self.gated("/api/loops/alpha/run", ctype="text/plain")
        self.assertEqual(status, 403)
        self.assertIn("Content-Type", json.loads(payload)["error"])
        self.assertEqual(self.stub.calls(), [])
        # positive control, as above
        self.assertEqual(self.gated("/api/loops/alpha/run")[0], 202)


if __name__ == "__main__":
    unittest.main()
