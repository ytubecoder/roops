"""Every precheck writes its probe inputs under OUT_DIR, the run dir the
runner exports (INTERFACES.md §4.1).

Regression: ads-delivery-watch and ads-hard-cut used `${LOOP_RUN_DIR:-/tmp}`.
The runner never set LOOP_RUN_DIR, so both silently wrote every probe input —
including ads-hard-cut's pause payload — to /tmp/inputs. Found 2026-09-05.
"""
import glob
import json
import os
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN_FILES = ("probe", "probe_core.py", "loopconf.py", "requirements.py", "schedule.py")


def _copy_bin(root):
    dest = os.path.join(root, "bin")
    os.makedirs(dest, exist_ok=True)
    for name in BIN_FILES:
        shutil.copy(os.path.join(REPO, "bin", name), os.path.join(dest, name))
    os.chmod(os.path.join(dest, "probe"), 0o755)


def _fake_probe(root, name, payload):
    os.makedirs(os.path.join(root, "probes"), exist_ok=True)
    path = os.path.join(root, "probes", name)
    with open(path, "w") as fh:
        fh.write(
            "#!/usr/bin/env bash\n"
            f"# probe: {name}\n"
            "# probe-writes: none\n"
            "# probe-output: json\n"
            "# probe-reads: fixture\n"
            f'if [ "${{1:-}}" = "--check" ]; then echo "ok {name}"; exit 0; fi\n'
            "cat <<'JSON'\n" + json.dumps(payload) + "\nJSON\n"
        )
    os.chmod(path, 0o755)


class PrecheckInputsUnderOutDir(unittest.TestCase):
    def test_no_precheck_references_loop_run_dir(self):
        scripts = sorted(glob.glob(os.path.join(REPO, "loops.d", "*", "precheck.sh")))
        self.assertTrue(scripts)
        for path in scripts:
            with open(path) as fh:
                code = "\n".join(l for l in fh.read().splitlines() if not l.lstrip().startswith("#"))
            self.assertNotIn("LOOP_RUN_DIR", code, path)

    def test_ads_delivery_watch_writes_inputs_under_out_dir(self):
        with tempfile.TemporaryDirectory() as root:
            _copy_bin(root)
            _fake_probe(root, "ads-delivery-watch", {
                "customer_id": "1", "mtd_usd": 1.0, "google_network_cap_usd": 500.0,
                "enabled_campaigns": ["c1"], "consecutive_dark_days": 0,
                "daily": {"2026-09-04": {"spend_usd": 1.0, "impressions": 10, "clicks": 1}},
                "findings": [],
            })
            _fake_probe(root, "ads-billing-read", {
                "ok": True, "scraped_at": "2026-09-05T00:00:00Z", "age_hours": 1,
                "balance_usd": 10.0, "threshold_usd": 500.0, "headroom_usd": 490.0,
                "payment_method": {"primary": "card", "primary_declined": False, "has_backup": False},
                "activity_pages_read": 4, "findings": [],
            })
            out_dir = os.path.join(root, "run")
            os.makedirs(out_dir)
            env = dict(os.environ)
            env.update({"OUT_DIR": out_dir, "LOOPS_ROOT": root, "LOOP_NAME": "ads-delivery-watch",
                        "RUN_ID": "test-run", "WORKDIR": root, "HOME": root})
            env.pop("LOOPS_PROBE_HOST", None)
            env.pop("LOOPS_PROBE_KEY", None)
            proc = subprocess.run(
                ["bash", os.path.join(REPO, "loops.d", "ads-delivery-watch", "precheck.sh")],
                capture_output=True, text=True, env=env,
                cwd=os.path.join(REPO, "loops.d", "ads-delivery-watch"), check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(os.path.exists(os.path.join(out_dir, "inputs", "delivery.json")))
            self.assertTrue(os.path.exists(os.path.join(out_dir, "inputs", "billing.json")))
            self.assertFalse(os.path.exists("/tmp/inputs/delivery.json")
                             and os.path.getmtime("/tmp/inputs/delivery.json") > os.path.getmtime(out_dir))

    def test_precheck_refuses_to_run_without_out_dir(self):
        env = dict(os.environ)
        env.pop("OUT_DIR", None)
        env.update({"LOOPS_ROOT": REPO, "LOOP_NAME": "ads-delivery-watch", "RUN_ID": "x"})
        proc = subprocess.run(
            ["bash", os.path.join(REPO, "loops.d", "ads-delivery-watch", "precheck.sh")],
            capture_output=True, text=True, env=env,
            cwd=os.path.join(REPO, "loops.d", "ads-delivery-watch"), check=False,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("OUT_DIR required", proc.stderr)


if __name__ == "__main__":
    unittest.main()
