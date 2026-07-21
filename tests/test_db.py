"""Tests for bin/db.py — §3 sqlite schema + CLI."""
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN = REPO_ROOT / "bin" / "db.py"

spec = importlib.util.spec_from_file_location("db_mod", BIN)
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)


def run_cli(args, **kwargs):
    return subprocess.run(
        [sys.executable, str(BIN)] + args,
        capture_output=True,
        text=True,
        **kwargs,
    )


class DbTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loops-db-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        r = run_cli(["init", "--root", self.tmp])
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def db_path(self):
        return os.path.join(self.tmp, "state", "loops.sqlite")

    def raw_conn(self):
        conn = sqlite3.connect(self.db_path())
        conn.row_factory = sqlite3.Row
        return conn


class TestInit(DbTestCase):
    def test_creates_db_file(self):
        self.assertTrue(os.path.exists(self.db_path()))

    def test_idempotent(self):
        r = run_cli(["init", "--root", self.tmp])
        self.assertEqual(r.returncode, 0)
        r2 = run_cli(["init", "--root", self.tmp])
        self.assertEqual(r2.returncode, 0)

    def test_all_tables_present(self):
        conn = self.raw_conn()
        names = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for t in ["runs", "heartbeats", "metrics", "findings", "dispositions", "schema_meta"]:
            self.assertIn(t, names)

    def test_schema_version_set(self):
        conn = self.raw_conn()
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()
        self.assertEqual(row["value"], "1")

    def test_wal_mode_enabled(self):
        conn = self.raw_conn()
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

    def test_indexes_present(self):
        conn = self.raw_conn()
        names = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        for idx in [
            "idx_runs_loop_started",
            "idx_hb_loop_ts",
            "idx_metrics_loop_key_ts",
            "idx_disp_loop_finding",
        ]:
            self.assertIn(idx, names)


class TestStartFinishRun(DbTestCase):
    def test_start_run_inserts_row(self):
        r = run_cli([
            "start-run", "--root", self.tmp, "--run-id", "run1", "--loop", "myloop",
            "--engine", "codex", "--trigger", "manual", "--started-at",
            "2026-07-22T14:00:00Z",
        ])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        conn = self.raw_conn()
        row = conn.execute("SELECT * FROM runs WHERE run_id='run1'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["loop_name"], "myloop")
        self.assertEqual(row["engine"], "codex")
        self.assertEqual(row["trigger"], "manual")
        self.assertIsNone(row["finished_at"])

    def test_finish_run_updates_and_computes_duration(self):
        run_cli([
            "start-run", "--root", self.tmp, "--run-id", "run1", "--loop", "myloop",
            "--engine", "codex", "--trigger", "manual", "--started-at",
            "2026-07-22T14:00:00Z",
        ])
        r = run_cli([
            "finish-run", "--root", self.tmp, "--run-id", "run1",
            "--runner-status", "completed", "--loop-status", "ok",
            "--effective-status", "ok", "--finished-at", "2026-07-22T14:00:05Z",
        ])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        conn = self.raw_conn()
        row = conn.execute("SELECT * FROM runs WHERE run_id='run1'").fetchone()
        self.assertEqual(row["runner_status"], "completed")
        self.assertEqual(row["loop_status"], "ok")
        self.assertEqual(row["effective_status"], "ok")
        self.assertEqual(row["duration_ms"], 5000)

    def test_finish_run_is_idempotent_update(self):
        run_cli([
            "start-run", "--root", self.tmp, "--run-id", "run1", "--loop", "myloop",
            "--engine", "codex", "--trigger", "manual", "--started-at",
            "2026-07-22T14:00:00Z",
        ])
        for _ in range(2):
            r = run_cli([
                "finish-run", "--root", self.tmp, "--run-id", "run1",
                "--runner-status", "completed", "--finished-at",
                "2026-07-22T14:00:05Z",
            ])
            self.assertEqual(r.returncode, 0)
        conn = self.raw_conn()
        count = conn.execute("SELECT COUNT(*) c FROM runs WHERE run_id='run1'").fetchone()["c"]
        self.assertEqual(count, 1)

    def test_finish_run_usage_codex_shape(self):
        run_cli([
            "start-run", "--root", self.tmp, "--run-id", "run1", "--loop", "myloop",
            "--engine", "codex", "--trigger", "manual", "--started-at",
            "2026-07-22T14:00:00Z",
        ])
        usage_file = Path(self.tmp) / "usage.json"
        lines = [
            json.dumps({"type": "some.other.event"}),
            json.dumps({
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 10,
                    "output_tokens": 50,
                    "reasoning_output_tokens": 5,
                },
            }),
        ]
        usage_file.write_text("\n".join(lines) + "\n")
        r = run_cli([
            "finish-run", "--root", self.tmp, "--run-id", "run1",
            "--runner-status", "completed", "--usage-file", str(usage_file),
            "--finished-at", "2026-07-22T14:00:05Z",
        ])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        conn = self.raw_conn()
        row = conn.execute("SELECT * FROM runs WHERE run_id='run1'").fetchone()
        self.assertEqual(row["tokens_input"], 100)
        self.assertEqual(row["tokens_output"], 50)
        self.assertEqual(row["tokens_total"], 150)
        self.assertIsNone(row["cost_usd"])
        self.assertIn("turn.completed", row["usage_raw"])

    def test_finish_run_usage_claude_shape(self):
        run_cli([
            "start-run", "--root", self.tmp, "--run-id", "run1", "--loop", "myloop",
            "--engine", "claude", "--trigger", "manual", "--started-at",
            "2026-07-22T14:00:00Z",
        ])
        usage_file = Path(self.tmp) / "usage.json"
        payload = {
            "usage": {"input_tokens": 200, "output_tokens": 80},
            "total_cost_usd": 0.018568,
            "session_id": "abc",
        }
        usage_file.write_text(json.dumps(payload))
        r = run_cli([
            "finish-run", "--root", self.tmp, "--run-id", "run1",
            "--runner-status", "completed", "--usage-file", str(usage_file),
            "--finished-at", "2026-07-22T14:00:05Z",
        ])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        conn = self.raw_conn()
        row = conn.execute("SELECT * FROM runs WHERE run_id='run1'").fetchone()
        self.assertEqual(row["tokens_input"], 200)
        self.assertEqual(row["tokens_output"], 80)
        self.assertEqual(row["tokens_total"], 280)
        self.assertAlmostEqual(row["cost_usd"], 0.018568)
        self.assertIn("session_id", row["usage_raw"])

    def test_finish_run_usage_garbage_never_crashes(self):
        run_cli([
            "start-run", "--root", self.tmp, "--run-id", "run1", "--loop", "myloop",
            "--engine", "codex", "--trigger", "manual", "--started-at",
            "2026-07-22T14:00:00Z",
        ])
        usage_file = Path(self.tmp) / "usage.json"
        usage_file.write_text("not json at all { garbage")
        r = run_cli([
            "finish-run", "--root", self.tmp, "--run-id", "run1",
            "--runner-status", "completed", "--usage-file", str(usage_file),
            "--finished-at", "2026-07-22T14:00:05Z",
        ])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        conn = self.raw_conn()
        row = conn.execute("SELECT * FROM runs WHERE run_id='run1'").fetchone()
        self.assertIsNone(row["tokens_input"])
        self.assertIsNone(row["cost_usd"])
        self.assertEqual(row["usage_raw"], "not json at all { garbage")

    def test_finish_run_missing_usage_file_does_not_crash(self):
        run_cli([
            "start-run", "--root", self.tmp, "--run-id", "run1", "--loop", "myloop",
            "--engine", "codex", "--trigger", "manual", "--started-at",
            "2026-07-22T14:00:00Z",
        ])
        r = run_cli([
            "finish-run", "--root", self.tmp, "--run-id", "run1",
            "--runner-status", "completed",
            "--finished-at", "2026-07-22T14:00:05Z",
        ])
        self.assertEqual(r.returncode, 0, msg=r.stderr)


class TestHeartbeat(DbTestCase):
    def test_heartbeat_inserts_row(self):
        r = run_cli([
            "heartbeat", "--root", self.tmp, "--loop", "myloop", "--ok", "1",
            "--detail", "probe ok",
        ])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        conn = self.raw_conn()
        row = conn.execute("SELECT * FROM heartbeats WHERE loop_name='myloop'").fetchone()
        self.assertEqual(row["ok"], 1)
        self.assertEqual(row["detail"], "probe ok")

    def test_heartbeat_ok_0(self):
        r = run_cli(["heartbeat", "--root", self.tmp, "--loop", "myloop", "--ok", "0"])
        self.assertEqual(r.returncode, 0)
        conn = self.raw_conn()
        row = conn.execute("SELECT * FROM heartbeats WHERE loop_name='myloop'").fetchone()
        self.assertEqual(row["ok"], 0)


class TestFlattenMetrics(unittest.TestCase):
    """Direct unit tests against db.flatten_metrics (internal helper)."""

    def test_top_level_scalar(self):
        flat = db.flatten_metrics({"count": 5})
        self.assertEqual(flat["count"]["num"], 5.0)
        self.assertIsNone(flat["count"]["text"])

    def test_nested_object_dot_flattens(self):
        flat = db.flatten_metrics({"repos": {"dirty": 2}})
        self.assertIn("repos.dirty", flat)
        self.assertEqual(flat["repos.dirty"]["num"], 2.0)

    def test_bool_to_num_0_1(self):
        flat = db.flatten_metrics({"is_clean": True, "is_broken": False})
        self.assertEqual(flat["is_clean"]["num"], 1.0)
        self.assertEqual(flat["is_broken"]["num"], 0.0)

    def test_array_stored_whole_in_text(self):
        flat = db.flatten_metrics({"tags": ["a", "b", "c"]})
        self.assertIsNone(flat["tags"]["num"])
        self.assertEqual(json.loads(flat["tags"]["text"]), ["a", "b", "c"])

    def test_string_value_goes_to_text(self):
        flat = db.flatten_metrics({"label": "hello"})
        self.assertIsNone(flat["label"]["num"])
        self.assertEqual(json.loads(flat["label"]["text"]), "hello")

    def test_depth_cap_3_freezes_deeper_structure(self):
        # a -> b -> c -> d : 4 levels of nesting; cap is 3, so the
        # structure below depth 3 is frozen as opaque JSON text rather
        # than flattened further.
        obj = {"a": {"b": {"c": {"d": 1}}}}
        flat = db.flatten_metrics(obj)
        # a.b.c should exist as an opaque leaf (not a.b.c.d)
        self.assertIn("a.b.c", flat)
        self.assertNotIn("a.b.c.d", flat)
        self.assertEqual(json.loads(flat["a.b.c"]["text"]), {"d": 1})

    def test_key_over_128_chars_truncated_with_ellipsis(self):
        long_key = "x" * 200
        flat = db.flatten_metrics({long_key: 1})
        keys = list(flat.keys())
        self.assertEqual(len(keys), 1)
        k = keys[0]
        self.assertTrue(k.endswith("…"))
        self.assertLessEqual(len(k), 129)

    def test_over_200_metrics_truncated_with_marker(self):
        obj = {f"metric_{i}": i for i in range(250)}
        flat = db.flatten_metrics(obj)
        self.assertIn("metrics_truncated", flat)
        self.assertEqual(flat["metrics_truncated"]["num"], 1.0)
        # 200 real metrics + 1 truncation marker
        self.assertEqual(len(flat), 201)

    def test_under_200_metrics_no_marker(self):
        obj = {f"metric_{i}": i for i in range(5)}
        flat = db.flatten_metrics(obj)
        self.assertNotIn("metrics_truncated", flat)


class TestRecordMetrics(DbTestCase):
    def test_record_metrics_end_to_end(self):
        run_cli([
            "start-run", "--root", self.tmp, "--run-id", "run1", "--loop", "myloop",
            "--engine", "codex", "--trigger", "manual", "--started-at",
            "2026-07-22T14:00:00Z",
        ])
        contract_file = Path(self.tmp) / "contract.json"
        contract = {
            "schema_version": 1,
            "run_id": "run1",
            "status": "ok",
            "status_reason": "ok",
            "headline": "fine",
            "report_markdown": "# fine",
            "metrics": json.dumps({"repos": {"dirty": 2, "unpushed": 3}}),
            "findings": [],
        }
        contract_file.write_text(json.dumps(contract))
        r = run_cli([
            "record-metrics", "--root", self.tmp, "--run-id", "run1", "--loop", "myloop",
            "--contract-file", str(contract_file),
        ])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        conn = self.raw_conn()
        rows = {
            row["key"]: row["num"]
            for row in conn.execute("SELECT * FROM metrics WHERE run_id='run1'")
        }
        self.assertEqual(rows["repos.dirty"], 2.0)
        self.assertEqual(rows["repos.unpushed"], 3.0)

    def test_record_metrics_garbage_metrics_string_never_crashes(self):
        run_cli([
            "start-run", "--root", self.tmp, "--run-id", "run1", "--loop", "myloop",
            "--engine", "codex", "--trigger", "manual", "--started-at",
            "2026-07-22T14:00:00Z",
        ])
        contract_file = Path(self.tmp) / "contract.json"
        contract = {
            "schema_version": 1,
            "run_id": "run1",
            "status": "ok",
            "status_reason": "ok",
            "headline": "fine",
            "report_markdown": "# fine",
            "metrics": "not valid json",
            "findings": [],
        }
        contract_file.write_text(json.dumps(contract))
        r = run_cli([
            "record-metrics", "--root", self.tmp, "--run-id", "run1", "--loop", "myloop",
            "--contract-file", str(contract_file),
        ])
        self.assertEqual(r.returncode, 0, msg=r.stderr)


def make_contract(run_id, findings, status="ok"):
    return {
        "schema_version": 1,
        "run_id": run_id,
        "status": status,
        "status_reason": "x",
        "headline": "x",
        "report_markdown": "# x",
        "metrics": "{}",
        "findings": findings,
    }


FINDING_A = {
    "finding_id": "repo:no-remote",
    "title": "repo has no remote",
    "severity": "warn",
    "detail": "details",
}
FINDING_B = {
    "finding_id": "repo:unpushed",
    "title": "repo has unpushed commits",
    "severity": "info",
    "detail": "details",
}


class TestFindingsLifecycle(DbTestCase):
    def upsert(self, run_id, findings, ts):
        contract_file = Path(self.tmp) / f"{run_id}.json"
        contract_file.write_text(json.dumps(make_contract(run_id, findings)))
        r = run_cli([
            "upsert-findings", "--root", self.tmp, "--run-id", run_id, "--loop", "myloop",
            "--contract-file", str(contract_file), "--ts", ts,
        ])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        return json.loads(r.stdout)

    def get_finding(self, finding_id):
        conn = self.raw_conn()
        return conn.execute(
            "SELECT * FROM findings WHERE loop_name='myloop' AND finding_id=?",
            (finding_id,),
        ).fetchone()

    def test_new_finding_inserted(self):
        summary = self.upsert("run1", [FINDING_A], "2026-07-01T00:00:00Z")
        self.assertEqual(summary["upserted"], 1)
        self.assertEqual(summary["resolved"], 0)
        row = self.get_finding("repo:no-remote")
        self.assertEqual(row["times_seen"], 1)
        self.assertEqual(row["first_seen_run"], "run1")
        self.assertIsNone(row["resolved_at"])

    def test_times_seen_increments_on_reoccurrence(self):
        self.upsert("run1", [FINDING_A], "2026-07-01T00:00:00Z")
        self.upsert("run2", [FINDING_A], "2026-07-02T00:00:00Z")
        row = self.get_finding("repo:no-remote")
        self.assertEqual(row["times_seen"], 2)
        self.assertEqual(row["last_seen_run"], "run2")

    def test_absent_finding_gets_resolved(self):
        self.upsert("run1", [FINDING_A, FINDING_B], "2026-07-01T00:00:00Z")
        summary = self.upsert("run2", [FINDING_B], "2026-07-02T00:00:00Z")
        self.assertEqual(summary["resolved"], 1)
        row = self.get_finding("repo:no-remote")
        self.assertIsNotNone(row["resolved_at"])
        # still-present finding untouched re resolution
        row_b = self.get_finding("repo:unpushed")
        self.assertIsNone(row_b["resolved_at"])

    def test_reappearance_clears_resolved_and_continues_times_seen(self):
        self.upsert("run1", [FINDING_A], "2026-07-01T00:00:00Z")
        self.upsert("run2", [], "2026-07-02T00:00:00Z")
        row = self.get_finding("repo:no-remote")
        self.assertIsNotNone(row["resolved_at"])
        self.assertEqual(row["times_seen"], 1)

        self.upsert("run3", [FINDING_A], "2026-07-03T00:00:00Z")
        row = self.get_finding("repo:no-remote")
        self.assertIsNone(row["resolved_at"])
        self.assertEqual(row["times_seen"], 2)

    def test_idempotent_rerun_same_world_no_duplicate_rows(self):
        self.upsert("run1", [FINDING_A, FINDING_B], "2026-07-01T00:00:00Z")
        self.upsert("run2", [FINDING_A, FINDING_B], "2026-07-02T00:00:00Z")
        conn = self.raw_conn()
        count = conn.execute(
            "SELECT COUNT(*) c FROM findings WHERE loop_name='myloop'"
        ).fetchone()["c"]
        self.assertEqual(count, 2)


class TestPriorFindings(DbTestCase):
    def upsert(self, run_id, findings, ts):
        contract_file = Path(self.tmp) / f"{run_id}.json"
        contract_file.write_text(json.dumps(make_contract(run_id, findings)))
        r = run_cli([
            "upsert-findings", "--root", self.tmp, "--run-id", run_id, "--loop", "myloop",
            "--contract-file", str(contract_file), "--ts", ts,
        ])
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def dispose(self, finding_id, action, note=None, until=None):
        args = [
            "dispose", "--root", self.tmp, "--loop", "myloop",
            "--finding-id", finding_id, "--action", action,
        ]
        if note is not None:
            args += ["--note", note]
        if until is not None:
            args += ["--until", until]
        return run_cli(args)

    def prior_findings(self):
        r = run_cli(["prior-findings", "--root", self.tmp, "--loop", "myloop"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        return r.stdout

    def test_empty_when_no_findings(self):
        self.assertEqual(self.prior_findings().strip(), "")

    def test_open_finding_rendering(self):
        self.upsert("run1", [FINDING_A], "2026-07-01T00:00:00Z")
        out = self.prior_findings()
        self.assertIn("repo:no-remote", out)
        self.assertIn("seen 1x since 2026-07-01", out)
        self.assertIn("open", out)

    def test_dismissed_finding_rendering(self):
        self.upsert("run1", [FINDING_A], "2026-07-01T00:00:00Z")
        r = self.dispose("repo:no-remote", "dismiss", note="intentional")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        out = self.prior_findings()
        self.assertIn("DISMISSED", out)
        self.assertIn("intentional", out)

    def test_snoozed_finding_rendering(self):
        self.upsert("run1", [FINDING_A], "2026-07-01T00:00:00Z")
        r = self.dispose("repo:no-remote", "snooze", until="2026-09-01")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        out = self.prior_findings()
        self.assertIn("SNOOZED until 2026-09-01", out)

    def test_acked_finding_rendering(self):
        self.upsert("run1", [FINDING_A], "2026-07-01T00:00:00Z")
        r = self.dispose("repo:no-remote", "ack")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        out = self.prior_findings()
        self.assertIn("ACKED", out)

    def test_resolved_finding_excluded(self):
        self.upsert("run1", [FINDING_A], "2026-07-01T00:00:00Z")
        self.upsert("run2", [], "2026-07-02T00:00:00Z")
        out = self.prior_findings()
        self.assertEqual(out.strip(), "")


class TestDisposeAndSuppression(DbTestCase):
    def upsert(self, run_id, findings, ts):
        contract_file = Path(self.tmp) / f"{run_id}.json"
        contract_file.write_text(json.dumps(make_contract(run_id, findings)))
        run_cli([
            "upsert-findings", "--root", self.tmp, "--run-id", run_id, "--loop", "myloop",
            "--contract-file", str(contract_file), "--ts", ts,
        ])

    def dispose(self, finding_id, action, note=None, until=None):
        args = [
            "dispose", "--root", self.tmp, "--loop", "myloop",
            "--finding-id", finding_id, "--action", action,
        ]
        if note is not None:
            args += ["--note", note]
        if until is not None:
            args += ["--until", until]
        return run_cli(args)

    def suppressed(self, ts):
        r = run_cli(["suppressed", "--root", self.tmp, "--loop", "myloop", "--ts", ts])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        return json.loads(r.stdout)

    def test_dismiss_requires_note(self):
        self.upsert("run1", [FINDING_A], "2026-07-01T00:00:00Z")
        r = self.dispose("repo:no-remote", "dismiss")
        self.assertNotEqual(r.returncode, 0)

    def test_snooze_requires_until(self):
        self.upsert("run1", [FINDING_A], "2026-07-01T00:00:00Z")
        r = self.dispose("repo:no-remote", "snooze")
        self.assertNotEqual(r.returncode, 0)

    def test_unknown_finding_exit_1(self):
        r = self.dispose("nope:not-real", "ack")
        self.assertEqual(r.returncode, 1)

    def test_dismiss_suppresses(self):
        self.upsert("run1", [FINDING_A], "2026-07-01T00:00:00Z")
        self.dispose("repo:no-remote", "dismiss", note="reason")
        ids = self.suppressed("2026-07-02T00:00:00Z")
        self.assertIn("repo:no-remote", ids)

    def test_snooze_suppresses_until_expiry(self):
        self.upsert("run1", [FINDING_A], "2026-07-01T00:00:00Z")
        self.dispose("repo:no-remote", "snooze", until="2026-09-01")
        ids = self.suppressed("2026-08-01T00:00:00Z")
        self.assertIn("repo:no-remote", ids)

    def test_expired_snooze_does_not_suppress(self):
        self.upsert("run1", [FINDING_A], "2026-07-01T00:00:00Z")
        self.dispose("repo:no-remote", "snooze", until="2026-07-15")
        ids = self.suppressed("2026-08-01T00:00:00Z")
        self.assertNotIn("repo:no-remote", ids)

    def test_reopen_unsuppresses(self):
        self.upsert("run1", [FINDING_A], "2026-07-01T00:00:00Z")
        self.dispose("repo:no-remote", "dismiss", note="reason")
        r = self.dispose("repo:no-remote", "reopen")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        ids = self.suppressed("2026-08-01T00:00:00Z")
        self.assertNotIn("repo:no-remote", ids)

    def test_dispositions_latest_wins(self):
        self.upsert("run1", [FINDING_A], "2026-07-01T00:00:00Z")
        self.dispose("repo:no-remote", "ack")
        self.dispose("repo:no-remote", "dismiss", note="later reason")
        ids = self.suppressed("2026-07-02T00:00:00Z")
        self.assertIn("repo:no-remote", ids)

    def test_reopen_after_dismiss_then_dismiss_again(self):
        self.upsert("run1", [FINDING_A], "2026-07-01T00:00:00Z")
        self.dispose("repo:no-remote", "dismiss", note="first")
        self.dispose("repo:no-remote", "reopen")
        ids = self.suppressed("2026-07-02T00:00:00Z")
        self.assertNotIn("repo:no-remote", ids)
        self.dispose("repo:no-remote", "dismiss", note="second")
        ids = self.suppressed("2026-07-02T00:00:00Z")
        self.assertIn("repo:no-remote", ids)

    def test_dispositions_are_append_only(self):
        self.upsert("run1", [FINDING_A], "2026-07-01T00:00:00Z")
        self.dispose("repo:no-remote", "ack")
        self.dispose("repo:no-remote", "dismiss", note="reason")
        conn = self.raw_conn()
        count = conn.execute(
            "SELECT COUNT(*) c FROM dispositions WHERE loop_name='myloop' AND finding_id='repo:no-remote'"
        ).fetchone()["c"]
        self.assertEqual(count, 2)


class TestQuery(DbTestCase):
    def test_loops_summary(self):
        run_cli([
            "start-run", "--root", self.tmp, "--run-id", "run1", "--loop", "loopA",
            "--engine", "codex", "--trigger", "manual", "--started-at",
            "2026-07-22T14:00:00Z",
        ])
        run_cli([
            "finish-run", "--root", self.tmp, "--run-id", "run1",
            "--runner-status", "completed", "--finished-at", "2026-07-22T14:00:05Z",
        ])
        r = run_cli(["query", "loops-summary", "--root", self.tmp])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["loop_name"], "loopA")

    def test_last_runs(self):
        for i in range(3):
            run_cli([
                "start-run", "--root", self.tmp, "--run-id", f"run{i}", "--loop", "loopA",
                "--engine", "codex", "--trigger", "manual", "--started-at",
                f"2026-07-2{i}T14:00:00Z",
            ])
        r = run_cli(["query", "last-runs", "--root", self.tmp, "--loop", "loopA", "--limit", "2"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(len(data), 2)

    def test_metric_history(self):
        run_cli([
            "start-run", "--root", self.tmp, "--run-id", "run1", "--loop", "loopA",
            "--engine", "codex", "--trigger", "manual", "--started-at",
            "2026-07-22T14:00:00Z",
        ])
        contract_file = Path(self.tmp) / "contract.json"
        contract_file.write_text(json.dumps(make_contract("run1", [])).replace(
            '"metrics": "{}"', '"metrics": "{\\"count\\": 7}"'
        ))
        run_cli([
            "record-metrics", "--root", self.tmp, "--run-id", "run1", "--loop", "loopA",
            "--contract-file", str(contract_file),
        ])
        r = run_cli([
            "query", "metric-history", "--root", self.tmp, "--loop", "loopA",
            "--key", "count", "--days", "30",
        ])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["num"], 7)

    def test_open_findings_query(self):
        contract_file = Path(self.tmp) / "contract.json"
        contract_file.write_text(json.dumps(make_contract("run1", [FINDING_A])))
        run_cli([
            "upsert-findings", "--root", self.tmp, "--run-id", "run1", "--loop", "loopA",
            "--contract-file", str(contract_file), "--ts", "2026-07-01T00:00:00Z",
        ])
        r = run_cli(["query", "open-findings", "--root", self.tmp, "--loop", "loopA"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["finding_id"], "repo:no-remote")

    def test_heartbeats_query(self):
        run_cli(["heartbeat", "--root", self.tmp, "--loop", "loopA", "--ok", "1"])
        run_cli(["heartbeat", "--root", self.tmp, "--loop", "loopA", "--ok", "0"])
        r = run_cli(["query", "heartbeats", "--root", self.tmp, "--loop", "loopA", "--limit", "10"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(len(data), 2)

    def test_spend_query(self):
        run_cli([
            "start-run", "--root", self.tmp, "--run-id", "run1", "--loop", "loopA",
            "--engine", "codex", "--trigger", "manual", "--started-at",
            "2026-07-22T14:00:00Z",
        ])
        usage_file = Path(self.tmp) / "usage.json"
        usage_file.write_text(json.dumps({
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "total_cost_usd": 0.01,
        }))
        run_cli([
            "finish-run", "--root", self.tmp, "--run-id", "run1",
            "--runner-status", "completed", "--usage-file", str(usage_file),
            "--finished-at", "2026-07-22T14:00:05Z",
        ])
        r = run_cli(["query", "spend", "--root", self.tmp, "--days", "7"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        data = json.loads(r.stdout)
        loopA = next(row for row in data if row["loop_name"] == "loopA")
        self.assertEqual(loopA["tokens_total"], 150)
        self.assertAlmostEqual(loopA["cost_usd"], 0.01)


if __name__ == "__main__":
    unittest.main()
