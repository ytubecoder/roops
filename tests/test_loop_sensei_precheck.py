"""Hermetic tests for loops.d/loop-sensei/precheck.sh — the deterministic
fleet-health inventory. No engine, no network: the precheck is bash+python
over a fixture LOOPS_ROOT built per test (docs/INTERFACES.md §11)."""

import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PRECHECK = REPO_ROOT / "loops.d" / "loop-sensei" / "precheck.sh"

RUNS_DDL = """
CREATE TABLE IF NOT EXISTS runs (
  run_id        TEXT PRIMARY KEY,
  loop_name     TEXT NOT NULL,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  duration_ms   INTEGER,
  engine        TEXT,
  model         TEXT,
  trigger       TEXT,
  runner_status TEXT NOT NULL,
  loop_status   TEXT,
  effective_status TEXT,
  status_reason TEXT,
  headline      TEXT,
  report_path   TEXT,
  contract_path TEXT,
  tokens_input  INTEGER,
  tokens_output INTEGER,
  tokens_total  INTEGER,
  cost_usd      REAL,
  usage_raw     TEXT,
  attempts      INTEGER,
  exit_code     INTEGER,
  error_detail  TEXT
);
"""


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = datetime.now(timezone.utc)


class SenseiFixture:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="loop-sensei-test-")
        os.makedirs(os.path.join(self.root, "state"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "loops.d", "loop-sensei"), exist_ok=True)
        conn = sqlite3.connect(os.path.join(self.root, "state", "loops.sqlite"))
        conn.executescript(RUNS_DDL)
        conn.commit()
        conn.close()

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def add_loop(self, name, timeout_s=600):
        d = os.path.join(self.root, "loops.d", name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "loop.conf"), "w") as f:
            f.write(
                f"name={name}\ntype=agent\nengine=codex\n"
                f"schedule=daily:06:00\ntimeout_s={timeout_s}\n"
            )

    def add_run(
        self, run_id, loop, started, finished, status, error_detail=None, exit_code=None
    ):
        conn = sqlite3.connect(os.path.join(self.root, "state", "loops.sqlite"))
        conn.execute(
            "INSERT INTO runs (run_id, loop_name, started_at, finished_at, "
            "runner_status, error_detail, exit_code, engine, trigger) "
            "VALUES (?,?,?,?,?,?,?,'codex','launchd')",
            (run_id, loop, started, finished, status, error_detail, exit_code),
        )
        conn.commit()
        conn.close()

    def add_artifact(self, run_id, name, content):
        d = os.path.join(self.root, "state", "runs", run_id)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "w") as f:
            f.write(content)

    def run_precheck(self):
        return subprocess.run(
            ["bash", str(PRECHECK)],
            env={**os.environ, "LOOPS_ROOT": self.root},
            capture_output=True,
            text=True,
            check=False,
        )


class SenseiPrecheckTests(unittest.TestCase):
    def setUp(self):
        self.fx = SenseiFixture()
        self.addCleanup(self.fx.cleanup)

    def test_healthy_fleet_empty_output_exit_zero(self):
        self.fx.add_loop("l1")
        self.fx.add_run(
            "r1",
            "l1",
            iso(NOW - timedelta(hours=1)),
            iso(NOW - timedelta(hours=1)),
            "completed",
        )
        r = self.fx.run_precheck()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_no_db_empty_output_exit_zero(self):
        os.remove(os.path.join(self.fx.root, "state", "loops.sqlite"))
        r = self.fx.run_precheck()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "")

    def test_engine_failed_detected_with_precomputed_id_and_evidence(self):
        self.fx.add_loop("l1")
        self.fx.add_run(
            "r1",
            "l1",
            iso(NOW - timedelta(hours=2)),
            iso(NOW - timedelta(hours=2)),
            "engine-failed",
            error_detail="adapter exit 1 (attempt 1)",
            exit_code=1,
        )
        self.fx.add_artifact("r1", "engine.log", "boom: something exploded\n")
        r = self.fx.run_precheck()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("finding_id: l1:engine-failed", r.stdout)
        self.assertIn("adapter exit 1 (attempt 1)", r.stdout)
        self.assertIn("boom: something exploded", r.stdout)
        self.assertIn(
            '{"fleet.died": 0, "fleet.failing": 1, "fleet.loops_checked": 1}', r.stdout
        )

    def test_died_detected_past_timeout_plus_grace(self):
        self.fx.add_loop("l1", timeout_s=600)
        self.fx.add_run("r1", "l1", iso(NOW - timedelta(hours=2)), None, "started")
        r = self.fx.run_precheck()
        self.assertIn("finding_id: l1:died", r.stdout)
        self.assertIn('"fleet.died": 1', r.stdout)

    def test_inflight_run_within_timeout_not_died(self):
        self.fx.add_loop("l1", timeout_s=600)
        self.fx.add_run("r1", "l1", iso(NOW - timedelta(minutes=5)), None, "started")
        r = self.fx.run_precheck()
        self.assertEqual(r.stdout, "")

    def test_skips_and_overlaps_are_not_failures(self):
        self.fx.add_loop("l1")
        self.fx.add_loop("l2")
        self.fx.add_run(
            "r1",
            "l1",
            iso(NOW - timedelta(hours=1)),
            iso(NOW - timedelta(hours=1)),
            "skipped-precheck",
        )
        self.fx.add_run(
            "r2",
            "l2",
            iso(NOW - timedelta(hours=1)),
            iso(NOW - timedelta(hours=1)),
            "skipped-overlap",
        )
        r = self.fx.run_precheck()
        self.assertEqual(r.stdout, "")

    def test_recovered_loop_drops_out(self):
        self.fx.add_loop("l1")
        self.fx.add_run(
            "r1",
            "l1",
            iso(NOW - timedelta(hours=3)),
            iso(NOW - timedelta(hours=3)),
            "engine-failed",
            error_detail="x",
            exit_code=1,
        )
        self.fx.add_run(
            "r2",
            "l1",
            iso(NOW - timedelta(hours=1)),
            iso(NOW - timedelta(hours=1)),
            "completed",
        )
        r = self.fx.run_precheck()
        self.assertEqual(r.stdout, "")

    def test_self_is_excluded(self):
        self.fx.add_run(
            "r1",
            "loop-sensei",
            iso(NOW - timedelta(hours=2)),
            iso(NOW - timedelta(hours=2)),
            "engine-failed",
            error_detail="x",
            exit_code=1,
        )
        r = self.fx.run_precheck()
        self.assertEqual(r.stdout, "")

    def test_never_run_loop_is_not_a_failure(self):
        self.fx.add_loop("l1")  # no run rows at all
        r = self.fx.run_precheck()
        self.assertEqual(r.stdout, "")

    def test_deterministic_output_modulo_timestamp(self):
        self.fx.add_loop("l1")
        self.fx.add_run(
            "r1",
            "l1",
            iso(NOW - timedelta(hours=2)),
            iso(NOW - timedelta(hours=2)),
            "engine-failed",
            error_detail="x",
            exit_code=1,
        )
        a = self.fx.run_precheck().stdout.splitlines()[1:]
        b = self.fx.run_precheck().stdout.splitlines()[1:]
        self.assertEqual(a, b)

    def test_overflow_beyond_cap_is_named_not_hidden(self):
        for i in range(10):
            name = f"l{i:02d}"
            self.fx.add_loop(name)
            self.fx.add_run(
                f"r{i}",
                name,
                iso(NOW - timedelta(hours=2)),
                iso(NOW - timedelta(hours=2)),
                "engine-failed",
                error_detail="x",
                exit_code=1,
            )
        r = self.fx.run_precheck()
        self.assertIn("NOT DETAILED (2 more", r.stdout)
        self.assertIn('"fleet.failing": 10', r.stdout)
        # the two overflow loops are still named
        self.assertIn("l08:engine-failed", r.stdout)
        self.assertIn("l09:engine-failed", r.stdout)

    def test_binary_artifact_not_dumped(self):
        self.fx.add_loop("l1")
        self.fx.add_run(
            "r1",
            "l1",
            iso(NOW - timedelta(hours=2)),
            iso(NOW - timedelta(hours=2)),
            "engine-failed",
            error_detail="x",
            exit_code=1,
        )
        d = os.path.join(self.fx.root, "state", "runs", "r1")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "engine.log"), "wb") as f:
            f.write(b"abc\x00def")
        r = self.fx.run_precheck()
        self.assertIn("(binary — not shown)", r.stdout)
        self.assertNotIn("abc\x00def", r.stdout)


if __name__ == "__main__":
    unittest.main()
