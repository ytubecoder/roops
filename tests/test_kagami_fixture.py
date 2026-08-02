"""kagami shape-mirror invariants (hermetic, no network).

The loop's drift detection is `hash(regenerate(mirror(fleet), pinned_now)) !=
hash(live)`, which is only sound if mirroring + regeneration is byte-deterministic
for a fixed fleet shape, and no string from the source fleet survives into the
artifact. Pin both against a SYNTHETIC source root (never the live fleet).
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILDER = os.path.join(REPO, "loops.d", "kagami", "fixture", "build_root.py")
GENERATE = os.path.join(REPO, "dashboard", "generate.py")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from html_selfcontained import external_subresources

SOURCE_LOOPS = ("zz-secret-alpha", "zz-secret-beta")


def _ts(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_source_root(base):
    """A tiny synthetic 'real fleet': two loops, one warn finding, one failure."""
    src = os.path.join(base, "source-root")
    for sub in ("loops.d", "state", "reports", "launchd"):
        os.makedirs(os.path.join(src, sub))
    for name, extra in (
        (SOURCE_LOOPS[0], "schedule=daily:06:10\n"),
        (SOURCE_LOOPS[1], "schedule=interval:30m\ntype=watchdog\n"),
    ):
        d = os.path.join(src, "loops.d", name)
        os.makedirs(d)
        with open(os.path.join(d, "loop.conf"), "w") as f:
            f.write(
                f'name={name}\ndescription="internal secret job"\n'
                f"owner=secret-team\ntype=agent\nengine=codex\n{extra}"
            )
    with open(
        os.path.join(src, "loops.d", SOURCE_LOOPS[0], "dashboard.json"), "w"
    ) as f:
        json.dump(
            {
                "panels": [
                    {"title": "secret metric", "metric": "secret.n", "type": "number"}
                ]
            },
            f,
        )
    with open(
        os.path.join(src, "launchd", f"com.loops.{SOURCE_LOOPS[0]}.plist"), "w"
    ) as f:
        f.write("<plist/>\n")
    subprocess.run(
        [sys.executable, os.path.join(REPO, "bin", "db.py"), "init", "--root", src],
        check=True,
        capture_output=True,
    )
    conn = sqlite3.connect(os.path.join(src, "state", "loops.sqlite"))
    conn.execute(
        "INSERT INTO runs (run_id, loop_name, started_at, runner_status, "
        "loop_status, effective_status, headline) VALUES (?,?,?,?,?,?,?)",
        (
            "r1",
            SOURCE_LOOPS[0],
            _ts(hours=6),  # daily cadence: comfortably fresh (boundary at 36h)
            "completed",
            "warn",
            "warn",
            "secret business headline",
        ),
    )
    conn.execute(
        "INSERT INTO runs (run_id, loop_name, started_at, runner_status) "
        "VALUES (?,?,?,?)",
        ("r2", SOURCE_LOOPS[1], _ts(minutes=10), "engine-failed"),
    )
    conn.execute(
        "INSERT INTO findings (loop_name, finding_id, title, severity, "
        "first_seen_run, first_seen_at, last_seen_run, last_seen_at, times_seen) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            SOURCE_LOOPS[0],
            "secret-client:overspend",
            "secret client overspending",
            "warn",
            "r1",
            "2026-07-30T06:11:00Z",
            "r1",
            "2026-08-01T06:11:00Z",
            3,
        ),
    )
    conn.commit()
    conn.close()
    d = os.path.join(src, "reports", SOURCE_LOOPS[0])
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "latest.json"), "w") as f:
        json.dump(
            {"status": "warn", "headline": "secret business headline", "findings": []},
            f,
        )
    return src


class KagamiMirror(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.source = build_source_root(cls.tmp.name)
        cls.fix = os.path.join(cls.tmp.name, "mirror-root")

        def build_and_render(dest_html):
            out = subprocess.run(
                [sys.executable, BUILDER, cls.fix, "--source", cls.source],
                check=True,
                capture_output=True,
                text=True,
            )
            pinned = out.stdout.strip()
            subprocess.run(
                [
                    sys.executable,
                    GENERATE,
                    "--root",
                    cls.fix,
                    "--now",
                    pinned,
                    "--out",
                    dest_html,
                ],
                check=True,
                capture_output=True,
            )
            with open(dest_html, encoding="utf-8") as f:
                html = f.read()
            for real in {cls.fix, os.path.realpath(cls.fix)}:
                html = html.replace(real, "/Users/niwa/roops")
            return pinned, html

        cls.pinned_now, cls.html_a = build_and_render(
            os.path.join(cls.tmp.name, "a.html")
        )
        _, cls.html_b = build_and_render(os.path.join(cls.tmp.name, "b.html"))
        # shift the source fleet's clock by 40 minutes (same freshness class,
        # same times_seen tier) — the mirror must not move
        conn = sqlite3.connect(os.path.join(cls.source, "state", "loops.sqlite"))
        conn.execute(
            "UPDATE runs SET started_at=? WHERE run_id='r1'",
            (_ts(hours=6, minutes=40),),
        )
        conn.execute(
            "UPDATE findings SET times_seen=4 "
            "WHERE finding_id='secret-client:overspend'"
        )
        conn.commit()
        conn.close()
        _, cls.html_shifted = build_and_render(os.path.join(cls.tmp.name, "c.html"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_builder_prints_pinned_now(self):
        self.assertRegex(self.pinned_now, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_mirror_is_byte_deterministic(self):
        self.assertEqual(self.html_a, self.html_b)

    def test_mirror_stable_under_clock_and_seen_jitter(self):
        """Classification-preserving mirror: raw age/times_seen movement inside
        the same class must not drift the page (prevents nightly PR churn)."""
        self.assertEqual(self.html_a, self.html_shifted)

    def test_row_count_mirrors_source_fleet(self):
        self.assertEqual(self.html_a.count('class="loop-row'), len(SOURCE_LOOPS))

    def test_no_source_strings_survive(self):
        for leak in (*SOURCE_LOOPS, "secret", "zz-"):
            self.assertNotIn(leak, self.html_a)

    def test_no_real_loop_names_in_artifact(self):
        for name in os.listdir(os.path.join(REPO, "loops.d")):
            self.assertNotIn(name, self.html_a.replace("/Users/niwa/roops", ""))

    def test_artifact_is_self_contained(self):
        self.assertEqual(external_subresources(self.html_a), [])


if __name__ == "__main__":
    unittest.main()
