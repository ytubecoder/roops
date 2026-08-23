"""Hermetic ads-x precheck tests against fake probes + a local GC fixture.

Never reads ~/.growth-console or ~/.opentwins; ssh is a fake exiting 255.
"""
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PRECHECK = os.path.join(REPO, "loops.d", "ads-x", "precheck.sh")
BIN_FILES = (
    "probe",
    "probe_core.py",
    "loopconf.py",
    "requirements.py",
    "schedule.py",
)

THREE_BATCH = {
    "batches": [
        {
            "batch_id": "july0000aaaa0001",
            "imported_at": "2026-07-28T12:00:00Z",
            "life": 100.0,
            "window": 80.0,
            "rows": 10,
            "bad": 0,
            "at_cap": 1,
            "headroom": 20.0,
        },
        {
            "batch_id": "aug00000bbbb0002",
            "imported_at": "2026-08-10T12:00:00Z",
            "life": 140.0,
            "window": 90.0,
            "rows": 10,
            "bad": 0,
            "at_cap": 2,
            "headroom": 15.0,
        },
        {
            "batch_id": "aug00000cccc0003",
            "imported_at": "2026-08-20T12:00:00Z",
            "life": 180.0,
            "window": 50.0,
            "rows": 10,
            "bad": 0,
            "at_cap": 3,
            "headroom": 10.0,
        },
    ]
}

LOCK = {
    "files": ["2026-08-21.md", "2026-08-22.md", "2026-08-23.md"],
    "hits": [{"file": "2026-08-23.md", "line": 4}],
}

GC_PAYLOADS = {
    "/api/ads/scoreboard": {"networks": {}, "days": 7, "budget": {}},
    "/api/ads/campaigns": {"cards": [], "totals": {}},
    "/api/ads/journal": {"rows": []},
    "/api/ads/program-events": {"events": []},
    "/api/ads/x-cache": {"snapshot_at": "2026-08-19T00:00:00Z", "age_days": 4.2},
}


def _copy_bin(root):
    dest = os.path.join(root, "bin")
    os.makedirs(dest, exist_ok=True)
    for name in BIN_FILES:
        shutil.copy(os.path.join(REPO, "bin", name), os.path.join(dest, name))
    os.chmod(os.path.join(dest, "probe"), 0o755)


def _write_probe(root, name, output, payload):
    probes = os.path.join(root, "probes")
    os.makedirs(probes, exist_ok=True)
    path = os.path.join(probes, name)
    body = (
        "#!/usr/bin/env bash\n"
        f"# probe: {name}\n"
        "# probe-writes: none\n"
        f"# probe-output: {output}\n"
        "# probe-reads: fixture\n"
        f'if [ "${{1:-}}" = "--check" ]; then echo "ok {name}"; exit 0; fi\n'
        "cat <<'JSON'\n"
        f"{json.dumps(payload)}\n"
        "JSON\n"
    )
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, 0o755)


def _fake_ssh(root):
    path = os.path.join(root, "fake-ssh")
    with open(path, "w") as f:
        f.write("#!/bin/sh\necho ssh-failed >&2\nexit 255\n")
    os.chmod(path, 0o755)
    return path


class _GCHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        body = GC_PAYLOADS.get(path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _start_gc():
    httpd = http.server.HTTPServer(("127.0.0.1", 0), _GCHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def _run_precheck(root, env_extra=None):
    out_dir = os.path.join(root, "state", "runs", "test-run")
    os.makedirs(out_dir, exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "OUT_DIR": out_dir,
            "LOOPS_ROOT": root,
            "LOOP_NAME": "ads-x",
            "RUN_ID": "test-run",
            "WORKDIR": root,
            "HOME": root,
        }
    )
    env.pop("LOOPS_PROBE_HOST", None)
    env.pop("LOOPS_PROBE_KEY", None)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        ["bash", PRECHECK],
        capture_output=True,
        text=True,
        env=env,
        cwd=os.path.join(REPO, "loops.d", "ads-x"),
        check=False,
    )
    return proc


class AdsXPrecheckTests(unittest.TestCase):
    def test_precheck_digest_from_fake_probes_and_x_cache(self):
        with tempfile.TemporaryDirectory() as root:
            _copy_bin(root)
            _write_probe(root, "ads-x-ledger", "json", THREE_BATCH)
            _write_probe(root, "opentwins-lock-signal", "json", LOCK)
            httpd = _start_gc()
            try:
                port = httpd.server_address[1]
                proc = _run_precheck(
                    root, {"GC_BASE": f"http://127.0.0.1:{port}"}
                )
            finally:
                httpd.shutdown()
                httpd.server_close()
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            digest = proc.stdout
            self.assertIn("x_cache_age: 4.2", digest)
            self.assertIn("STALE", digest)
            self.assertIn("TRUE lifetime spend $", digest)
            self.assertIn("serving rate", digest)
            self.assertIn("month attribution", digest)
            self.assertIn("lock/access-wall markers present", digest)

    def test_transport_failure_is_input_gap_and_exits_0(self):
        with tempfile.TemporaryDirectory() as root:
            _copy_bin(root)
            fake = _fake_ssh(root)
            httpd = _start_gc()
            try:
                port = httpd.server_address[1]
                proc = _run_precheck(
                    root,
                    {
                        "GC_BASE": f"http://127.0.0.1:{port}",
                        "LOOPS_PROBE_HOST": "x",
                        "LOOPS_SSH": fake,
                    },
                )
            finally:
                httpd.shutdown()
                httpd.server_close()
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(proc.stdout.count("probe transport failed"), 2)

    def test_precheck_has_no_host_local_paths(self):
        text = Path(PRECHECK).read_text()
        self.assertNotIn("~/.growth-console", text)
        self.assertNotIn(".growth-console", text)
        self.assertNotIn("~/.opentwins", text)
        self.assertNotIn(".opentwins", text)


if __name__ == "__main__":
    unittest.main()
