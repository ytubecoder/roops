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

sys.path.insert(0, os.path.join(REPO, "loops.d", "kagami", "fixture"))
from parity import missing_from_mirror

SOURCE_LOOPS = ("zz-secret-alpha", "zz-secret-beta")


def _ts(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_source_root(base):
    """A tiny synthetic 'real fleet': two loops, one warn finding, one failure."""
    src = os.path.join(base, "source-root")
    for sub in ("loops.d", "state", "reports", "launchd"):
        os.makedirs(os.path.join(src, sub))
    # A real loops root carries bin/ — generate.py lazily loads bin/loopconf.py
    # and bin/schedule.py from the RENDERED root to parse loop.conf; without it,
    # owner/schedule silently fall back to assumed/manual and the render lies.
    os.symlink(os.path.join(REPO, "bin"), os.path.join(src, "bin"))
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
        "INSERT INTO runs (run_id, loop_name, started_at, finished_at, "
        "runner_status, loop_status, effective_status, headline) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "r1",
            SOURCE_LOOPS[0],
            _ts(hours=6),  # daily cadence: comfortably fresh (boundary at 36h)
            _ts(hours=5, minutes=58),
            "completed",
            "warn",
            "warn",
            "secret business headline",
        ),
    )
    # Older history with a harness failure — the mirror must reproduce the
    # harness badge and fail-detail rows the runs drawer renders for these.
    conn.execute(
        "INSERT INTO runs (run_id, loop_name, started_at, finished_at, "
        "runner_status, error_detail) VALUES (?,?,?,?,?,?)",
        (
            "r1b",
            SOURCE_LOOPS[0],
            _ts(days=5),
            _ts(days=5, minutes=-2),
            "harness-error",
            "secret traceback: /Users/secret/broken.py line 7",
        ),
    )
    conn.execute(
        "INSERT INTO runs (run_id, loop_name, started_at, finished_at, "
        "runner_status, loop_status, effective_status, headline) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            "r1c",
            SOURCE_LOOPS[0],
            _ts(days=6),
            _ts(days=6, minutes=-3),
            "completed",
            "ok",
            "ok",
            "secret all clear",
        ),
    )
    conn.execute(
        "INSERT INTO runs (run_id, loop_name, started_at, runner_status) "
        "VALUES (?,?,?,?)",
        ("r2", SOURCE_LOOPS[1], _ts(minutes=10), "engine-failed"),
    )
    # A panel-covered metric plus an uncovered one — the extra key renders the
    # raw-fallback "Other metrics" drawer.
    conn.execute(
        "INSERT INTO metrics (run_id, loop_name, ts, key, num) VALUES (?,?,?,?,?)",
        ("r1", SOURCE_LOOPS[0], _ts(hours=6), "secret.n", 4),
    )
    conn.execute(
        "INSERT INTO metrics (run_id, loop_name, ts, key, num) VALUES (?,?,?,?,?)",
        ("r1", SOURCE_LOOPS[0], _ts(hours=6), "secret.extra", 7),
    )
    for fid, title, seen in (
        ("secret-client:overspend", "secret client overspending", 3),
        ("secret-client:dismissed", "secret dismissed condition", 6),
        ("secret-client:acked", "secret acknowledged condition", 2),
        ("secret-client:snoozed", "secret snoozed condition", 5),
    ):
        conn.execute(
            "INSERT INTO findings (loop_name, finding_id, title, severity, "
            "first_seen_run, first_seen_at, last_seen_run, last_seen_at, "
            "times_seen) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                SOURCE_LOOPS[0],
                fid,
                title,
                "warn",
                "r1",
                "2026-07-30T06:11:00Z",
                "r1",
                "2026-08-01T06:11:00Z",
                seen,
            ),
        )
    # Disposition classifications: dismissed (suppressed + reopen cmd), acked
    # (stamp on an open finding), actively snoozed (suppressed until future).
    for fid, action, until in (
        ("secret-client:dismissed", "dismiss", None),
        ("secret-client:acked", "ack", None),
        ("secret-client:snoozed", "snooze", _ts(days=-3)),
    ):
        conn.execute(
            "INSERT INTO dispositions (loop_name, finding_id, action, note, "
            "snooze_until, created_at) VALUES (?,?,?,?,?,?)",
            (
                SOURCE_LOOPS[0],
                fid,
                action,
                "secret internal reasoning",
                until,
                _ts(days=1),
            ),
        )
    # A real fleet always carries lifecycle events (B-19 recency sort feeds on
    # them); an event-less source would render the empty-state <p> the mirror
    # (which always writes events) never shows, failing parity spuriously.
    for event, ts in (("created", _ts(days=9)), ("installed", _ts(days=8))):
        conn.execute(
            "INSERT INTO loop_events (loop_name, event, actor, ts) VALUES (?,?,?,?)",
            (SOURCE_LOOPS[0], event, "secret-admin", ts),
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
    # Page-enabled loop with a rendered latest + dated history — drives the
    # "page" link and the dated-history block.
    with open(os.path.join(d, "latest.html"), "w") as f:
        f.write("<!doctype html><title>secret report</title>\n")
    for dated in ("2026-07-28-0610.html", "2026-07-27-0610.html"):
        with open(os.path.join(d, dated), "w") as f:
            f.write("<!doctype html><title>secret dated report</title>\n")
    render_sh = os.path.join(src, "loops.d", SOURCE_LOOPS[0], "render.sh")
    with open(render_sh, "w") as f:
        f.write("#!/bin/sh\nexit 0\n")
    os.chmod(render_sh, 0o755)
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
        # The source root IS a loops root — render it with the real generator to
        # get the "real garden" feature surface the mirror must cover.
        source_out = os.path.join(cls.tmp.name, "source.html")
        subprocess.run(
            [sys.executable, GENERATE, "--root", cls.source, "--out", source_out],
            check=True,
            capture_output=True,
        )
        with open(source_out, encoding="utf-8") as f:
            cls.source_html = f.read()
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

    def test_mirror_covers_source_feature_surface(self):
        """Parity (the B-20 gate's property, hermetically): every data-conditional
        UI feature the source fleet renders — harness badges, fail-detail rows,
        suppressed findings + disposition stamps, reopen commands, raw-fallback
        metrics, report page links and dated history — must render on the mirror
        too. A mirror that under-exhibits the interface passes byte-drift checks
        while the public mockup silently stops showcasing the current garden."""
        self.assertEqual(missing_from_mirror(self.source_html, self.html_a), [])

    def test_source_render_exhibits_the_hard_features(self):
        """Guard the guard: if the synthetic source fleet ever stops rendering
        the data-conditional features, the parity test above passes vacuously."""
        for token in (
            "badge harness",
            "fail-detail",
            "finding suppressed",
            "stamp-mark",
            "raw-fallback",
            "history",
        ):
            self.assertIn(token, self.source_html)

    def test_mirror_stable_across_stale_days(self):
        """A stale loop's mirrored age is binary (fresh vs stale), not a daily
        moving bucket — 3 days stale and 4 days stale must render identically,
        or the nightly refresh PR churns (regression: 2026-08-03)."""
        conn = sqlite3.connect(os.path.join(self.source, "state", "loops.sqlite"))

        def render_with_r1_age(days):
            conn.execute(
                "UPDATE runs SET started_at=?, finished_at=? WHERE run_id='r1'",
                (_ts(days=days), _ts(days=days, minutes=-2)),
            )
            conn.commit()
            out = subprocess.run(
                [sys.executable, BUILDER, self.fix, "--source", self.source],
                check=True,
                capture_output=True,
                text=True,
            )
            pinned = out.stdout.strip()
            dest = os.path.join(self.tmp.name, f"stale-{days}.html")
            subprocess.run(
                [
                    sys.executable,
                    GENERATE,
                    "--root",
                    self.fix,
                    "--now",
                    pinned,
                    "--out",
                    dest,
                ],
                check=True,
                capture_output=True,
            )
            with open(dest, encoding="utf-8") as f:
                return f.read()

        try:
            self.assertEqual(render_with_r1_age(3), render_with_r1_age(4))
        finally:
            conn.execute(
                "UPDATE runs SET started_at=?, finished_at=? WHERE run_id='r1'",
                (_ts(hours=6), _ts(hours=5, minutes=58)),
            )
            conn.commit()
            conn.close()


class KagamiPrecheckPortabilityTests(unittest.TestCase):
    def test_precheck_does_not_hardcode_bin_zsh_login(self):
        path = os.path.join(REPO, "loops.d", "kagami", "precheck.sh")
        with open(path) as f:
            text = f.read()
        self.assertNotIn("/bin/zsh -l", text)
        self.assertIn("command -v zsh", text)


if __name__ == "__main__":
    unittest.main()

