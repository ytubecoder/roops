"""Hermetic kagi-ban tests: precheck digest against a stub av binary, and the
renderer against the sanitized fixture. Never touches the real av app."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOOP = os.path.join(REPO, "loops.d", "kagi-ban")
FIXTURE = os.path.join(REPO, "pagekit", "reference", "fixture-scan.json")

sys.path.insert(0, os.path.join(REPO, "bin"))
import page_envelope  # noqa: E402


def make_stub_av(dirpath, scan_json_path):
    stub = os.path.join(dirpath, "av")
    with open(stub, "w") as f:
        f.write("#!/usr/bin/env bash\n"
                'if [ "$1" = "--version" ]; then echo "av 0.0-stub"; exit 0; fi\n'
                f'cat "{scan_json_path}"\n')
    os.chmod(stub, 0o755)
    return stub


class KagiBanPrecheckTests(unittest.TestCase):
    def run_precheck(self, root, scan_json_path):
        out_dir = os.path.join(root, "state", "runs", "test-run")
        os.makedirs(out_dir, exist_ok=True)
        stub = make_stub_av(root, scan_json_path)
        env = dict(os.environ, AV_BIN=stub, OUT_DIR=out_dir, LOOPS_ROOT=root,
                   LOOP_NAME="kagi-ban", RUN_ID="test-run", WORKDIR=root)
        proc = subprocess.run(["bash", os.path.join(LOOP, "precheck.sh")],
                              capture_output=True, text=True, env=env, cwd=LOOP)
        return proc, out_dir

    def test_first_run_labels_everything_new(self):
        with tempfile.TemporaryDirectory() as root:
            proc, out_dir = self.run_precheck(root, FIXTURE)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("first_run=yes", proc.stdout)
            self.assertIn("NEW av:github-cli:", proc.stdout)
            self.assertNotIn("ONGOING", proc.stdout)
            self.assertTrue(os.path.isfile(
                os.path.join(out_dir, "loop-data.commit", "scan-prev.json")))

    def test_unchanged_world_is_all_ongoing_and_ids_stable(self):
        with tempfile.TemporaryDirectory() as root:
            proc1, out_dir = self.run_precheck(root, FIXTURE)
            committed = os.path.join(root, "state", "loop-data", "kagi-ban")
            os.makedirs(committed, exist_ok=True)
            os.replace(os.path.join(out_dir, "loop-data.commit", "scan-prev.json"),
                       os.path.join(committed, "scan-prev.json"))
            proc2, _ = self.run_precheck(root, FIXTURE)
            self.assertIn("new=0", proc2.stdout)
            self.assertIn("resolved=0", proc2.stdout)
            ids1 = sorted(line.split()[1] for line in proc1.stdout.splitlines()
                          if line.startswith(("NEW ", "ONGOING ")))
            ids2 = sorted(line.split()[1] for line in proc2.stdout.splitlines()
                          if line.startswith(("NEW ", "ONGOING ")))
            self.assertEqual(ids1, ids2)


class KagiBanRendererTests(unittest.TestCase):
    def test_renderer_passes_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "page.html")
            proc = subprocess.run(
                [sys.executable, os.path.join(LOOP, "render_page.py"), FIXTURE,
                 "--loop", "kagi-ban", "--run-id", "test-run", "-o", out,
                 "--host", "fixture", "--av-version", "0.0-stub"],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            errors = page_envelope.check_page(out, expect_run_id="test-run",
                                              expect_loop="kagi-ban")
            self.assertEqual(errors, [])
            meta = page_envelope.read_meta(out)
            self.assertEqual(meta["page_class"], "snapshot")
            self.assertEqual(meta["totals"]["findings"], 5)


if __name__ == "__main__":
    unittest.main()
