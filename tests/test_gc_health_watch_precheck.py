"""Hermetic tests for loops.d/gc-health-watch/precheck.sh.

Temp roots only. Fake gc-health-read probe prints a canned payload.
ssh is a fake exiting 255. Never touches real state/ or ~/.opentwins.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PRECHECK = os.path.join(REPO, "loops.d", "gc-health-watch", "precheck.sh")
PROMPT = os.path.join(REPO, "loops.d", "gc-health-watch", "prompt.md")
LOOPCONF = os.path.join(REPO, "loops.d", "gc-health-watch", "loop.conf")
BIN_FILES = (
    "probe",
    "probe_core.py",
    "loopconf.py",
    "requirements.py",
    "schedule.py",
)

GREEN_PAYLOAD = {
    "probe": "gc-health-read",
    "generated_at": "2026-09-05T01:15:01Z",
    "now": "2026-09-05T01:15:01Z",
    "sections": {
        "schedules": {
            "error": None,
            "rows": [
                {
                    "name": "opentwins twitter heartbeat",
                    "status": "ok",
                    "last_ok": "2026-09-05 05:33",
                    "error": None,
                    "last_error_at": None,
                    "schedule": "every 1 hour",
                }
            ],
            "excluded": [],
            "manual": [],
        },
        "opentwins": {
            "error": None,
            "session": {
                "state": "logged_in",
                "as_of": "2026-09-04 20:31",
                "since": "2026-09-02 16:19",
                "consecutive": 52,
                "entries_examined": 52,
                "files": ["2026-09-03.md", "2026-09-04.md"],
                "last_logged_in_at": "2026-09-04 20:31",
                "login_did_not_stick": False,
            },
            "launches": [
                {
                    "day": "2026-09-04",
                    "runs": 20,
                    "completed": 20,
                    "launched": 20,
                    "quit": 20,
                    "deferred": 0,
                    "cdp_errors": 0,
                }
            ],
            "tasks": {
                "for_date": "2026-09-04",
                "counts": {"done": 7, "failed": 2, "pending": 4},
                "failed": [],
                "typed_len_zero": 1,
            },
        },
        "postiz": {
            "error": None,
            "integrations": [
                {"identifier": "facebook", "name": "maguyva", "disabled": False},
                {"identifier": "x", "name": "maguyva", "disabled": False},
            ],
            "posts": {
                "window_days": 14,
                "total": 0,
                "by_state": {},
                "error": [],
                "missed": [],
            },
        },
    },
    "findings": [],
}


def _copy_bin(root):
    dest = os.path.join(root, "bin")
    os.makedirs(dest, exist_ok=True)
    for name in BIN_FILES:
        shutil.copy(os.path.join(REPO, "bin", name), os.path.join(dest, name))
    os.chmod(os.path.join(dest, "probe"), 0o755)


def _write_probe(root, payload):
    probes = os.path.join(root, "probes")
    os.makedirs(probes, exist_ok=True)
    path = os.path.join(probes, "gc-health-read")
    body = (
        "#!/usr/bin/env bash\n"
        "# probe: gc-health-read\n"
        "# probe-writes: none\n"
        "# probe-output: json\n"
        "# probe-reads: fixture\n"
        'if [ "${1:-}" = "--check" ]; then echo "ok gc-health-read"; exit 0; fi\n'
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


def _run_precheck(root, env_extra=None):
    out_dir = os.path.join(root, "state", "runs", "test-run")
    os.makedirs(out_dir, exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "OUT_DIR": out_dir,
            "LOOPS_ROOT": root,
            "LOOP_NAME": "gc-health-watch",
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
        cwd=os.path.join(REPO, "loops.d", "gc-health-watch"),
        check=False,
    )
    return proc, out_dir


def _load_loopconf():
    spec = importlib.util.spec_from_file_location(
        "loopconf_mod", os.path.join(REPO, "bin", "loopconf.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class GcHealthWatchPrecheckTests(unittest.TestCase):
    def test_silent_green_exits_0(self):
        with tempfile.TemporaryDirectory() as root:
            _copy_bin(root)
            _write_probe(root, GREEN_PAYLOAD)
            proc, _out = _run_precheck(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(
                proc.stdout.startswith("# gc-health-watch precheck — "),
                proc.stdout,
            )
            self.assertIn("## findings (0)", proc.stdout)

    def test_findings_exit_1_and_are_rendered(self):
        payload = json.loads(json.dumps(GREEN_PAYLOAD))
        payload["findings"] = [
            {
                "id": "opentwins:twitter:logged-out",
                "severity": "alert",
                "detail": "X agent logged out since 2026-08-31 07:18 UTC",
            },
            {
                "id": "gc:linkedin-notifications:overdue",
                "severity": "warn",
                "detail": "linkedin notifications: last ok 2026-09-03 06:19, expected every 20 min",
            },
        ]
        payload["sections"]["schedules"]["rows"].append(
            {
                "name": "linkedin notifications",
                "status": "overdue",
                "last_ok": "2026-09-03 06:19",
                "error": None,
                "last_error_at": None,
                "schedule": "every 20 min",
            }
        )
        with tempfile.TemporaryDirectory() as root:
            _copy_bin(root)
            _write_probe(root, payload)
            proc, _out = _run_precheck(root)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("## findings (2)", proc.stdout)
            self.assertIn("[ALERT] opentwins:twitter:logged-out — ", proc.stdout)
            self.assertIn("[WARN] gc:linkedin-notifications:overdue — ", proc.stdout)
            self.assertIn("## schedules", proc.stdout)
            self.assertIn("## opentwins", proc.stdout)
            self.assertIn("## postiz", proc.stdout)

    def test_section_error_is_rendered_not_hidden(self):
        payload = json.loads(json.dumps(GREEN_PAYLOAD))
        payload["sections"]["postiz"]["error"] = "HTTP 401"
        payload["findings"] = [
            {
                "id": "probe:postiz-read-failed",
                "severity": "warn",
                "detail": "postiz read failed: HTTP 401",
            }
        ]
        with tempfile.TemporaryDirectory() as root:
            _copy_bin(root)
            _write_probe(root, payload)
            proc, _out = _run_precheck(root)
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            postiz_at = proc.stdout.index("## postiz")
            rest = proc.stdout[postiz_at:]
            findings_at = rest.find("## findings")
            block = rest if findings_at < 0 else rest[:findings_at]
            self.assertIn("ERROR: HTTP 401", block)

    def test_transport_failure_is_input_gap_exit_1(self):
        with tempfile.TemporaryDirectory() as root:
            _copy_bin(root)
            _write_probe(root, GREEN_PAYLOAD)
            fake = _fake_ssh(root)
            proc, _out = _run_precheck(
                root,
                {"LOOPS_PROBE_HOST": "x", "LOOPS_SSH": fake},
            )
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("PROBE TRANSPORT FAILED", proc.stdout)

    def test_inputs_land_under_out_dir(self):
        with tempfile.TemporaryDirectory() as root:
            _copy_bin(root)
            _write_probe(root, GREEN_PAYLOAD)
            proc, out_dir = _run_precheck(root)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(
                os.path.isfile(os.path.join(out_dir, "inputs", "gc-health.json"))
            )
            text = Path(PRECHECK).read_text()
            self.assertNotIn("LOOP_RUN_DIR", text)

    def test_output_deterministic_modulo_timestamp(self):
        with tempfile.TemporaryDirectory() as root:
            _copy_bin(root)
            _write_probe(root, GREEN_PAYLOAD)
            proc1, _o1 = _run_precheck(root)
            proc2, _o2 = _run_precheck(root)
            self.assertEqual(proc1.returncode, 0, proc1.stdout + proc1.stderr)
            self.assertEqual(proc2.returncode, 0, proc2.stdout + proc2.stderr)
            self.assertEqual(
                proc1.stdout.splitlines()[1:],
                proc2.stdout.splitlines()[1:],
            )

    def test_prompt_has_finding_identity_heading(self):
        lines = Path(PROMPT).read_text().splitlines()
        self.assertIn("## Finding identity", lines)

    def test_loopconf_parses_with_expected_values(self):
        loopconf = _load_loopconf()
        conf, errors = loopconf.parse(LOOPCONF)
        self.assertEqual(errors, [])
        self.assertEqual(conf["type"], "watchdog")
        self.assertEqual(conf["schedule"], "daily:09:15")
        self.assertIn("probe:gc-health-read", conf["requires"] or [])
        self.assertEqual(conf["perm_fs_write"], "report_only")
        self.assertEqual(conf["perm_network"], "none")
        self.assertEqual(conf["perm_local_exec"], "none")
        self.assertEqual(conf["perm_remote_mutation"], "none")


if __name__ == "__main__":
    unittest.main()
