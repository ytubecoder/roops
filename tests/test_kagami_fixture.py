"""kagami fixture + drift-signal invariants (hermetic, no network).

The loop's drift detection is `hash(regenerate(fixture, pinned_now)) != hash(live)`,
which is only sound if regeneration is byte-deterministic and the published artifact
carries no real-world names or paths after the precheck rewrite. Pin both here.
"""

import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILDER = os.path.join(REPO, "loops.d", "kagami", "fixture", "build_root.py")
GENERATE = os.path.join(REPO, "dashboard", "generate.py")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from html_selfcontained import external_subresources


class KagamiFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.fix = os.path.join(cls.tmp.name, "fixture-root")
        out = subprocess.run(
            [sys.executable, BUILDER, cls.fix],
            check=True,
            capture_output=True,
            text=True,
        )
        cls.pinned_now = out.stdout.strip()

        def gen(dest):
            subprocess.run(
                [
                    sys.executable,
                    GENERATE,
                    "--root",
                    cls.fix,
                    "--now",
                    cls.pinned_now,
                    "--out",
                    dest,
                ],
                check=True,
                capture_output=True,
            )
            with open(dest, encoding="utf-8") as f:
                return f.read()

        cls.html_a = gen(os.path.join(cls.tmp.name, "a.html"))
        cls.html_b = gen(os.path.join(cls.tmp.name, "b.html"))
        # the precheck rewrite, replicated
        cls.rewritten = cls.html_a
        for real in {cls.fix, os.path.realpath(cls.fix)}:
            cls.rewritten = cls.rewritten.replace(real, "/Users/niwa/roops")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_builder_prints_pinned_now(self):
        self.assertRegex(self.pinned_now, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_regeneration_is_byte_deterministic(self):
        self.assertEqual(self.html_a, self.html_b)

    def test_rewrite_removes_generating_root_path(self):
        self.assertIn(self.fix, self.html_a)  # sanity: rewrite has work to do
        self.assertNotIn(self.fix, self.rewritten)
        self.assertNotIn(os.path.realpath(self.fix), self.rewritten)

    def test_no_real_loop_names_after_rewrite(self):
        real_names = set(os.listdir(os.path.join(REPO, "loops.d")))
        mock_names = set(os.listdir(os.path.join(self.fix, "loops.d")))
        self.assertFalse(
            real_names & mock_names, "fixture must never reuse a real loop name"
        )
        for name in real_names:
            self.assertNotIn(name, self.rewritten)

    def test_artifact_is_self_contained(self):
        self.assertEqual(external_subresources(self.rewritten), [])

    def test_mock_fleet_renders(self):
        for name in ("tls-certs", "dead-links", "smoke-probe", "log-rotate"):
            self.assertIn(name, self.rewritten)


if __name__ == "__main__":
    unittest.main()
