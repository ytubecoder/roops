"""Hermetic unit + integration tests for dashboard/generate.py.

Every test builds its own temp LOOPS_ROOT (per docs/INTERFACES.md §11) and never touches the
real repo state/reports/loops.d. The sqlite fixture copies the §3 DDL verbatim. loopconf.py and
schedule.py are not depended on directly (they're being built concurrently) -- fake parser
callables are injected via generate()'s function-level seam.
"""

import glob
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from dashboard import generate

# --- §3 schema, copied verbatim from docs/INTERFACES.md -------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
  run_id        TEXT PRIMARY KEY,
  loop_name     TEXT NOT NULL,
  started_at    TEXT NOT NULL,          -- ISO8601 Z
  finished_at   TEXT,
  duration_ms   INTEGER,
  engine        TEXT,
  model         TEXT,
  trigger       TEXT,                   -- launchd | manual | kickstart
  runner_status TEXT NOT NULL,          -- §4.3 enum
  loop_status   TEXT,                   -- ok | warn | alert | NULL — engine-emitted, verbatim
  effective_status TEXT,                -- ok | warn | alert | NULL — post-suppression (§4.5); the dashboard displays THIS
  status_reason TEXT,
  headline      TEXT,
  report_path   TEXT,                   -- repo-relative
  contract_path TEXT,                   -- repo-relative
  tokens_input  INTEGER,                -- nullable
  tokens_output INTEGER,                -- nullable
  tokens_total  INTEGER,                -- nullable
  cost_usd      REAL,                   -- nullable
  usage_raw     TEXT,                   -- raw engine usage JSON, verbatim
  attempts      INTEGER,                -- engine attempts incl. transient retries (§4.6); NULL if engine not invoked
  exit_code     INTEGER,
  error_detail  TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_loop_started ON runs(loop_name, started_at DESC);

CREATE TABLE IF NOT EXISTS heartbeats (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  loop_name TEXT NOT NULL,
  run_id    TEXT,
  ts        TEXT NOT NULL,
  ok        INTEGER NOT NULL,           -- 1 = probe healthy (incl. silent-green), 0 = probe failed
  detail    TEXT
);
CREATE INDEX IF NOT EXISTS idx_hb_loop_ts ON heartbeats(loop_name, ts DESC);

CREATE TABLE IF NOT EXISTS metrics (
  run_id    TEXT NOT NULL,
  loop_name TEXT NOT NULL,
  ts        TEXT NOT NULL,
  key       TEXT NOT NULL,
  num       REAL,
  text      TEXT,
  PRIMARY KEY (run_id, key)
);
CREATE INDEX IF NOT EXISTS idx_metrics_loop_key_ts ON metrics(loop_name, key, ts);

CREATE TABLE IF NOT EXISTS findings (
  finding_id     TEXT NOT NULL,
  loop_name      TEXT NOT NULL,
  title          TEXT NOT NULL,
  severity       TEXT NOT NULL,
  first_seen_run TEXT NOT NULL,
  first_seen_at  TEXT NOT NULL,
  last_seen_run  TEXT NOT NULL,
  last_seen_at   TEXT NOT NULL,
  times_seen     INTEGER NOT NULL DEFAULT 1,
  resolved_at    TEXT,
  PRIMARY KEY (loop_name, finding_id)
);

CREATE TABLE IF NOT EXISTS dispositions (
  loop_name    TEXT NOT NULL,
  finding_id   TEXT NOT NULL,
  action       TEXT NOT NULL,
  note         TEXT,
  snooze_until TEXT,
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_disp_loop_finding ON dispositions(loop_name, finding_id, created_at DESC);

CREATE TABLE IF NOT EXISTS loop_events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  loop_name TEXT NOT NULL,
  event     TEXT NOT NULL,
  actor     TEXT NOT NULL,
  ts        TEXT NOT NULL,
  detail    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_loop_ts ON loop_events(loop_name, ts DESC);

CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT);
"""

NOW = datetime(2026, 7, 22, 14, 0, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class FixtureRoot:
    """Builds a temp LOOPS_ROOT with the standard layout."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="loops-dashboard-test-")
        for d in ("state", "reports", "loops.d"):
            os.makedirs(os.path.join(self.root, d), exist_ok=True)
        self.db_path = os.path.join(self.root, "state", "loops.sqlite")

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        return conn

    def add_loop(
        self,
        name,
        description="a loop",
        type_="agent",
        engine="codex",
        schedule="interval:15m",
        dashboard_json=None,
        timeout_s=900,
    ):
        d = os.path.join(self.root, "loops.d", name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "loop.conf"), "w") as f:
            f.write(
                f"name={name}\ndescription={description}\ntype={type_}\n"
                f"engine={engine}\nschedule={schedule}\ntimeout_s={timeout_s}\n"
            )
        if dashboard_json is not None:
            with open(os.path.join(d, "dashboard.json"), "w") as f:
                json.dump(dashboard_json, f)
        return d

    def add_run(
        self,
        conn,
        run_id,
        loop_name,
        started_at,
        finished_at=None,
        runner_status="completed",
        loop_status=None,
        effective_status=None,
        headline="",
        tokens_total=None,
        cost_usd=None,
        engine="codex",
        error_detail=None,
        exit_code=None,
    ):
        conn.execute(
            "INSERT INTO runs (run_id, loop_name, started_at, finished_at, engine, "
            "trigger, runner_status, loop_status, effective_status, headline, "
            "tokens_total, cost_usd, error_detail, exit_code) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id,
                loop_name,
                started_at,
                finished_at,
                engine,
                "manual",
                runner_status,
                loop_status,
                effective_status,
                headline,
                tokens_total,
                cost_usd,
                error_detail,
                exit_code,
            ),
        )
        conn.commit()

    def add_event(self, conn, loop_name, event, actor, ts=None, detail=None):
        conn.execute(
            "INSERT INTO loop_events(loop_name, event, actor, ts, detail) VALUES (?,?,?,?,?)",
            (loop_name, event, actor, ts or iso(NOW), detail),
        )
        conn.commit()

    def write_latest_json(self, name, contract):
        d = os.path.join(self.root, "reports", name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "latest.json"), "w") as f:
            json.dump(contract, f)
        with open(os.path.join(d, "latest.md"), "w") as f:
            f.write("# report\n")


def fake_loopconf_parse(conf_overrides=None):
    """Returns a fake loopconf.parse(path) that reads our simple KEY=value fixture files."""
    conf_overrides = conf_overrides or {}

    def _parse(path):
        conf = {
            "timeout_s": 900,
            "retention_days": 30,
            "type": "agent",
        }
        errors = []
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    k, _, v = line.partition("=")
                    conf[k.strip()] = v.strip()
        except FileNotFoundError:
            errors.append("missing loop.conf")
        if "timeout_s" in conf:
            conf["timeout_s"] = int(conf["timeout_s"])
        conf.update(conf_overrides.get(conf.get("name", ""), {}))
        return conf, errors

    return _parse


def fake_schedule_parse(interval_map=None):
    """Returns a fake schedule.parse(spec) -> {kind, launchd, expected_interval_s}."""
    interval_map = interval_map or {}

    def _parse(spec):
        if spec in interval_map:
            return interval_map[spec]
        if spec == "manual":
            return {"kind": "manual", "launchd": {}, "expected_interval_s": 0}
        if spec.startswith("interval:"):
            # interval:15m / interval:2h
            val = spec.split(":", 1)[1]
            n = int(val[:-1])
            unit = val[-1]
            secs = n * (60 if unit == "m" else 3600 if unit == "h" else 1)
            return {
                "kind": "interval",
                "launchd": {"StartInterval": secs},
                "expected_interval_s": secs,
            }
        if spec.startswith("daily:"):
            return {"kind": "daily", "launchd": {}, "expected_interval_s": 86400}
        return {"kind": "unknown", "launchd": {}, "expected_interval_s": 86400}

    return _parse


class PureFunctionTests(unittest.TestCase):
    """Precedence matrix, stale/died, disposition text — pure logic, no I/O."""

    def test_completed_ok_is_green(self):
        color, marker = generate.compute_light("completed", "ok")
        self.assertEqual(color, "green")
        self.assertIsNone(marker)

    def test_completed_warn_is_amber(self):
        color, marker = generate.compute_light("completed", "warn")
        self.assertEqual(color, "amber")
        self.assertIsNone(marker)

    def test_completed_alert_is_red(self):
        color, marker = generate.compute_light("completed", "alert")
        self.assertEqual(color, "red")
        self.assertIsNone(marker)

    def test_skipped_precheck_is_amber(self):
        color, _marker = generate.compute_light("skipped-precheck", "ok")
        self.assertEqual(color, "amber")

    def test_skipped_overlap_is_amber_even_if_effective_status_would_be_alert(self):
        # skipped-overlap rows have no effective_status (started=finished=now, no engine run)
        color, marker = generate.compute_light("skipped-overlap", None)
        self.assertEqual(color, "amber")
        self.assertIsNone(marker)

    def test_precheck_failed_is_red_no_harness_marker(self):
        color, marker = generate.compute_light("precheck-failed", "alert")
        self.assertEqual(color, "red")
        self.assertIsNone(marker)

    def test_engine_failed_is_red(self):
        color, _marker = generate.compute_light("engine-failed", None)
        self.assertEqual(color, "red")

    def test_engine_timeout_is_red(self):
        color, _marker = generate.compute_light("engine-timeout", None)
        self.assertEqual(color, "red")

    def test_auth_failed_is_red_with_harness_marker(self):
        color, marker = generate.compute_light("auth-failed", None)
        self.assertEqual(color, "red")
        self.assertEqual(marker, "harness-problem")

    def test_tool_denied_is_red_with_harness_marker(self):
        color, marker = generate.compute_light("tool-denied", None)
        self.assertEqual(color, "red")
        self.assertEqual(marker, "harness-problem")

    def test_contract_violation_is_red_with_harness_marker(self):
        color, marker = generate.compute_light("contract-violation", None)
        self.assertEqual(color, "red")
        self.assertEqual(marker, "harness-problem")

    def test_harness_error_is_red_with_harness_marker(self):
        color, marker = generate.compute_light("harness-error", None)
        self.assertEqual(color, "red")
        self.assertEqual(marker, "harness-problem")

    def test_effective_status_wins_over_loop_status(self):
        # effective_status is what colours the light; a completed run with loop_status=alert
        # but effective_status=ok (all findings suppressed) must render green.
        color, _marker = generate.compute_light("completed", "ok")
        self.assertEqual(color, "green")

    def test_stale_true_when_overdue_past_1_5x(self):
        last = NOW - timedelta(seconds=1000)
        # expected_interval_s=600 -> stale threshold 900s; 1000 > 900
        self.assertTrue(generate.is_stale(iso(last), 600, NOW))

    def test_stale_false_when_within_1_5x(self):
        last = NOW - timedelta(seconds=800)
        self.assertFalse(generate.is_stale(iso(last), 600, NOW))

    def test_manual_exempt_from_stale(self):
        last = NOW - timedelta(days=400)
        self.assertFalse(generate.is_stale(iso(last), 0, NOW))

    def test_died_true_past_timeout_plus_grace(self):
        started = NOW - timedelta(seconds=1000)
        # timeout_s=800 -> died threshold 920s; 1000 > 920
        self.assertTrue(generate.is_died(None, iso(started), 800, NOW))

    def test_died_false_within_grace(self):
        started = NOW - timedelta(seconds=500)
        self.assertFalse(generate.is_died(None, iso(started), 800, NOW))

    def test_died_false_when_finished(self):
        started = NOW - timedelta(seconds=5000)
        self.assertFalse(generate.is_died(iso(NOW), iso(started), 800, NOW))

    # -- running/overdue trichotomy (Amendment 2 -- 2026-07-30) --------------------------

    def test_overdue_true_between_timeout_and_grace(self):
        started = NOW - timedelta(seconds=850)
        # timeout_s=800 -> overdue window (800, 920]; 850 is inside it
        self.assertTrue(generate.is_overdue(None, iso(started), 800, NOW))

    def test_overdue_false_within_timeout(self):
        started = NOW - timedelta(seconds=500)
        self.assertFalse(generate.is_overdue(None, iso(started), 800, NOW))

    def test_overdue_false_past_grace(self):
        started = NOW - timedelta(seconds=1000)
        self.assertFalse(generate.is_overdue(None, iso(started), 800, NOW))

    def test_overdue_false_when_finished(self):
        started = NOW - timedelta(seconds=850)
        self.assertFalse(generate.is_overdue(iso(NOW), iso(started), 800, NOW))

    def test_running_true_within_timeout(self):
        started = NOW - timedelta(seconds=500)
        self.assertTrue(generate.is_running(None, iso(started), 800, NOW))

    def test_running_false_past_timeout(self):
        started = NOW - timedelta(seconds=850)
        self.assertFalse(generate.is_running(None, iso(started), 800, NOW))

    def test_running_false_when_finished(self):
        started = NOW - timedelta(seconds=500)
        self.assertFalse(generate.is_running(iso(NOW), iso(started), 800, NOW))

    def test_disposition_text_dismissed(self):
        text = generate.disposition_text(
            "dismiss", "intentional", None, "2026-06-01T00:00:00Z"
        )
        self.assertIn("dismissed", text)
        self.assertIn("2026-06-01", text)
        self.assertIn("intentional", text)

    def test_disposition_text_snoozed(self):
        text = generate.disposition_text(
            "snooze", None, "2026-09-01T00:00:00Z", "2026-07-01T00:00:00Z"
        )
        self.assertIn("snoozed", text)
        self.assertIn("2026-09-01", text)

    def test_disposition_text_none(self):
        self.assertEqual(generate.disposition_text(None, None, None, None), "")

    def test_is_suppressed_dismiss(self):
        self.assertTrue(generate.is_suppressed("dismiss", None, NOW))

    def test_is_suppressed_active_snooze(self):
        self.assertTrue(
            generate.is_suppressed("snooze", iso(NOW + timedelta(days=1)), NOW)
        )

    def test_is_suppressed_expired_snooze(self):
        self.assertFalse(
            generate.is_suppressed("snooze", iso(NOW - timedelta(days=1)), NOW)
        )

    def test_is_suppressed_ack_is_not_suppressed(self):
        self.assertFalse(generate.is_suppressed("ack", None, NOW))

    def test_is_suppressed_none_is_not_suppressed(self):
        self.assertFalse(generate.is_suppressed(None, None, NOW))

    def test_ordinal(self):
        self.assertEqual(generate.ordinal(1), "1st")
        self.assertEqual(generate.ordinal(2), "2nd")
        self.assertEqual(generate.ordinal(3), "3rd")
        self.assertEqual(generate.ordinal(4), "4th")
        self.assertEqual(generate.ordinal(11), "11th")
        self.assertEqual(generate.ordinal(12), "12th")
        self.assertEqual(generate.ordinal(13), "13th")
        self.assertEqual(generate.ordinal(21), "21st")

    def test_truncate_value_short_unchanged(self):
        s = "hello"
        self.assertEqual(generate.truncate_value(s, 2048), "hello")

    def test_truncate_value_long_truncated(self):
        s = "x" * 3000
        out = generate.truncate_value(s, 2048)
        self.assertLessEqual(len(out), 2048 + 40)  # allow room for marker
        self.assertIn("truncated", out)

    def test_render_sparkline_has_svg_and_points(self):
        svg = generate.render_sparkline([1, 2, 3, 4, 5])
        self.assertIn("<svg", svg)
        self.assertIn("polyline", svg)

    def test_render_sparkline_handles_flat_series(self):
        svg = generate.render_sparkline([5, 5, 5])
        self.assertIn("<svg", svg)

    def test_render_sparkline_empty(self):
        svg = generate.render_sparkline([])
        self.assertEqual(svg, "")


class GenerateIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fx = FixtureRoot()
        self.addCleanup(self.fx.cleanup)

    def test_empty_state_no_loops_no_db_does_not_crash(self):
        out = os.path.join(self.fx.root, "dashboard", "loops.html")
        generate.generate(
            root=self.fx.root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(),
            now=NOW,
        )
        self.assertTrue(os.path.exists(out))
        with open(out) as f:
            html = f.read()
        self.assertIn("<html", html.lower())
        self.assertNotIn("Traceback", html)

    def test_empty_state_with_default_lazy_parsers_never_imports_missing_bin_modules(
        self,
    ):
        # No injected parsers, no bin/loopconf.py or bin/schedule.py on disk (as in a real
        # fresh checkout / concurrent-build window) -- with zero loops.d entries this must
        # not attempt to import either module and must not crash.
        self.assertFalse(
            os.path.exists(os.path.join(self.fx.root, "bin", "loopconf.py"))
        )
        self.assertFalse(
            os.path.exists(os.path.join(self.fx.root, "bin", "schedule.py"))
        )
        out = os.path.join(self.fx.root, "dashboard", "loops.html")
        generate.generate(root=self.fx.root, out_file=out, now=NOW)
        self.assertTrue(os.path.exists(out))
        with open(out) as f:
            html = f.read()
        self.assertIn("<html", html.lower())
        self.assertNotIn("Traceback", html)

    def test_atomic_write_no_tmp_file_left_and_output_complete(self):
        conn = self.fx.init_db()
        self.fx.add_loop("hello-loop")
        self.fx.add_run(
            conn,
            "r1",
            "hello-loop",
            iso(NOW - timedelta(minutes=5)),
            iso(NOW - timedelta(minutes=4)),
            "completed",
            "ok",
            "ok",
            "all clear",
            tokens_total=100,
        )
        conn.close()
        out_dir = os.path.join(self.fx.root, "dashboard")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, "loops.html")
        generate.generate(
            root=self.fx.root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(),
            now=NOW,
        )
        self.assertTrue(os.path.exists(out))
        with open(out) as f:
            html = f.read()
        self.assertIn("</html>", html.lower())
        leftover_tmp = [p for p in glob.glob(os.path.join(out_dir, "*")) if p != out]
        self.assertEqual(leftover_tmp, [], f"leftover tmp files: {leftover_tmp}")

    def test_precedence_in_full_pipeline_completed_alert_renders_red(self):
        conn = self.fx.init_db()
        self.fx.add_loop("sweep")
        self.fx.add_run(
            conn,
            "r1",
            "sweep",
            iso(NOW - timedelta(minutes=5)),
            iso(NOW - timedelta(minutes=4)),
            "completed",
            "alert",
            "alert",
            "3 repos broken",
        )
        conn.close()
        out = os.path.join(self.fx.root, "dashboard", "loops.html")
        generate.generate(
            root=self.fx.root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(),
            now=NOW,
        )
        with open(out) as f:
            html = f.read()
        self.assertIn("sweep", html)
        self.assertIn("3 repos broken", html)

    def test_stale_loop_flagged_and_counts_toward_needs_attention(self):
        conn = self.fx.init_db()
        self.fx.add_loop("rare", schedule="interval:1m")
        self.fx.add_run(
            conn,
            "r1",
            "rare",
            iso(NOW - timedelta(hours=5)),
            iso(NOW - timedelta(hours=5)),
            "completed",
            "ok",
            "ok",
            "fine",
        )
        conn.close()
        out = os.path.join(self.fx.root, "dashboard", "loops.html")
        generate.generate(
            root=self.fx.root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(
                {
                    "interval:1m": {
                        "kind": "interval",
                        "launchd": {"StartInterval": 60},
                        "expected_interval_s": 60,
                    }
                }
            ),
            now=NOW,
        )
        with open(out) as f:
            html = f.read()
        # exact rendered badge markup (not just the substring "stale", which also
        # appears in the page's static .badge.stale CSS rule regardless of wiring)
        self.assertIn('<span class="badge stale">stale</span>', html)
        # and it must actually count toward the needs-attention chip
        self.assertIn("needs attention 1", html)

    def test_manual_loop_never_flagged_stale(self):
        conn = self.fx.init_db()
        self.fx.add_loop("occasional", schedule="manual")
        self.fx.add_run(
            conn,
            "r1",
            "occasional",
            iso(NOW - timedelta(days=400)),
            iso(NOW - timedelta(days=400)),
            "completed",
            "ok",
            "ok",
            "fine",
        )
        conn.close()
        out = os.path.join(self.fx.root, "dashboard", "loops.html")
        generate.generate(
            root=self.fx.root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(),
            now=NOW,
        )
        with open(out) as f:
            html = f.read()
        # loop row itself present, but not marked stale
        self.assertIn("occasional", html)
        idx = html.find("occasional")
        window = html[idx : idx + 2000]
        self.assertNotIn("stale", window.lower())

    def test_died_run_flagged(self):
        conn = self.fx.init_db()
        self.fx.add_loop("hanger", timeout_s=800)
        self.fx.add_run(
            conn,
            "r1",
            "hanger",
            iso(NOW - timedelta(seconds=1200)),
            finished_at=None,
            runner_status="running",
        )
        conn.close()
        out = os.path.join(self.fx.root, "dashboard", "loops.html")
        generate.generate(
            root=self.fx.root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(),
            now=NOW,
        )
        with open(out) as f:
            html = f.read()
        # exact rendered badge markup (not just the substring "died", which also
        # appears in the page's static .badge.died CSS rule regardless of wiring)
        self.assertIn('<span class="badge died">died</span>', html)
        # and it must actually count toward the needs-attention chip
        self.assertIn("needs attention 1", html)

    def test_findings_suppressed_greyed_open_shows_recurrence(self):
        conn = self.fx.init_db()
        self.fx.add_loop("cookingapp")
        self.fx.add_run(
            conn,
            "r1",
            "cookingapp",
            iso(NOW - timedelta(minutes=5)),
            iso(NOW - timedelta(minutes=4)),
            "completed",
            "warn",
            "warn",
            "issues found",
        )
        conn.execute(
            "INSERT INTO findings (finding_id, loop_name, title, severity, first_seen_run, "
            "first_seen_at, last_seen_run, last_seen_at, times_seen, resolved_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,NULL)",
            (
                "cookingapp:no-remote",
                "cookingapp",
                "cookingapp has no remote",
                "warn",
                "r0",
                iso(NOW - timedelta(days=50)),
                "r1",
                iso(NOW - timedelta(minutes=4)),
                3,
            ),
        )
        conn.execute(
            "INSERT INTO dispositions (loop_name, finding_id, action, note, snooze_until, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                "cookingapp",
                "cookingapp:no-remote",
                "dismiss",
                "intentional, local scratch repo",
                None,
                iso(NOW - timedelta(days=10)),
            ),
        )
        conn.execute(
            "INSERT INTO findings (finding_id, loop_name, title, severity, first_seen_run, "
            "first_seen_at, last_seen_run, last_seen_at, times_seen, resolved_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,NULL)",
            (
                "cookingapp:unpushed",
                "cookingapp",
                "cookingapp has unpushed commits",
                "warn",
                "r1",
                iso(NOW - timedelta(minutes=4)),
                "r1",
                iso(NOW - timedelta(minutes=4)),
                1,
            ),
        )
        conn.commit()
        self.fx.write_latest_json(
            "cookingapp",
            {
                "schema_version": 1,
                "run_id": "r1",
                "status": "warn",
                "status_reason": "x",
                "headline": "issues found",
                "report_markdown": "# x",
                "metrics": "{}",
                "findings": [
                    {
                        "finding_id": "cookingapp:unpushed",
                        "title": "cookingapp has unpushed commits",
                        "severity": "warn",
                        "detail": "23 commits behind",
                    },
                ],
            },
        )
        conn.close()
        out = os.path.join(self.fx.root, "dashboard", "loops.html")
        generate.generate(
            root=self.fx.root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(),
            now=NOW,
        )
        with open(out) as f:
            html = f.read()
        # open finding shows recurrence text (1st report)
        self.assertIn("cookingapp:unpushed", html)
        self.assertIn("1st report", html)
        # suppressed finding still present (not hidden) with disposition text
        self.assertIn("cookingapp:no-remote", html)
        self.assertIn("dismissed", html)
        self.assertIn("intentional, local scratch repo", html)
        # a ready-to-paste loopctl dismiss command for the open finding
        self.assertIn("loopctl dismiss cookingapp cookingapp:unpushed", html)
        # suppressed finding visually marked (grey/collapsed class), not simply absent --
        # exact rendered markup, not just the substring "suppressed" which also appears
        # in the page's static .finding.suppressed CSS rule regardless of wiring
        self.assertIn('class="finding suppressed"', html)

    def test_spend_aggregation_7day(self):
        conn = self.fx.init_db()
        self.fx.add_loop("spender")
        self.fx.add_run(
            conn,
            "r1",
            "spender",
            iso(NOW - timedelta(days=1)),
            iso(NOW - timedelta(days=1)),
            "completed",
            "ok",
            "ok",
            "fine",
            tokens_total=1000,
            cost_usd=0.5,
        )
        self.fx.add_run(
            conn,
            "r2",
            "spender",
            iso(NOW - timedelta(days=3)),
            iso(NOW - timedelta(days=3)),
            "completed",
            "ok",
            "ok",
            "fine",
            tokens_total=2000,
            cost_usd=1.0,
        )
        self.fx.add_run(
            conn,
            "r3",
            "spender",
            iso(NOW - timedelta(days=10)),
            iso(NOW - timedelta(days=10)),
            "completed",
            "ok",
            "ok",
            "fine",
            tokens_total=99999,
            cost_usd=99.0,
        )
        conn.close()
        out = os.path.join(self.fx.root, "dashboard", "loops.html")
        generate.generate(
            root=self.fx.root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(),
            now=NOW,
        )
        with open(out) as f:
            html = f.read()
        # 3000 tokens within 7d window; the 10-day-old run must be excluded
        self.assertIn("3,000", html)
        self.assertNotIn("99,999", html)

    def test_trend_svg_present_with_at_least_3_points(self):
        conn = self.fx.init_db()
        self.fx.add_loop(
            "trendy",
            dashboard_json={
                "panels": [
                    {
                        "title": "Dirty repos",
                        "metric": "repos.dirty",
                        "type": "trend",
                        "window_days": 30,
                    },
                ]
            },
        )
        self.fx.add_run(
            conn,
            "r1",
            "trendy",
            iso(NOW - timedelta(days=2)),
            iso(NOW - timedelta(days=2)),
            "completed",
            "ok",
            "ok",
            "fine",
        )
        for i, (rid, days, val) in enumerate(
            [("r1", 2, 1.0), ("r2", 1, 2.0), ("r3", 0, 3.0)]
        ):
            conn.execute(
                "INSERT INTO metrics (run_id, loop_name, ts, key, num, text) VALUES (?,?,?,?,?,NULL)",
                (rid, "trendy", iso(NOW - timedelta(days=days)), "repos.dirty", val),
            )
        conn.commit()
        conn.close()
        out = os.path.join(self.fx.root, "dashboard", "loops.html")
        generate.generate(
            root=self.fx.root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(),
            now=NOW,
        )
        with open(out) as f:
            html = f.read()
        self.assertIn("polyline", html)
        self.assertIn("Dirty repos", html)

    def test_raw_fallback_panel_truncates_undeclared_metric(self):
        conn = self.fx.init_db()
        self.fx.add_loop("rawloop", dashboard_json={"panels": []})
        self.fx.add_run(
            conn,
            "r1",
            "rawloop",
            iso(NOW - timedelta(minutes=5)),
            iso(NOW - timedelta(minutes=4)),
            "completed",
            "ok",
            "ok",
            "fine",
        )
        big_value = "y" * 5000
        conn.execute(
            "INSERT INTO metrics (run_id, loop_name, ts, key, num, text) VALUES (?,?,?,?,NULL,?)",
            (
                "r1",
                "rawloop",
                iso(NOW - timedelta(minutes=4)),
                "undeclared.blob",
                json.dumps(big_value),
            ),
        )
        conn.commit()
        conn.close()
        out = os.path.join(self.fx.root, "dashboard", "loops.html")
        generate.generate(
            root=self.fx.root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(),
            now=NOW,
        )
        with open(out) as f:
            html = f.read()
        self.assertIn("undeclared.blob", html)
        self.assertIn("truncated", html.lower())
        # the full 5000-char blob must not appear verbatim
        self.assertNotIn("y" * 5000, html)

    def test_number_panel_missing_hold_carries_forward_previous_value(self):
        conn = self.fx.init_db()
        self.fx.add_loop(
            "holdy",
            dashboard_json={
                "panels": [
                    {
                        "title": "Dirty repos",
                        "metric": "repos.dirty",
                        "type": "number",
                        "missing": "hold",
                    },
                ]
            },
        )
        # older run had the metric; latest run does not report it at all
        self.fx.add_run(
            conn,
            "r1",
            "holdy",
            iso(NOW - timedelta(days=2)),
            iso(NOW - timedelta(days=2)),
            "completed",
            "ok",
            "ok",
            "fine",
        )
        conn.execute(
            "INSERT INTO metrics (run_id, loop_name, ts, key, num, text) VALUES (?,?,?,?,?,NULL)",
            ("r1", "holdy", iso(NOW - timedelta(days=2)), "repos.dirty", 7.0),
        )
        self.fx.add_run(
            conn,
            "r2",
            "holdy",
            iso(NOW - timedelta(minutes=5)),
            iso(NOW - timedelta(minutes=4)),
            "completed",
            "ok",
            "ok",
            "fine",
        )
        conn.commit()
        conn.close()
        out = os.path.join(self.fx.root, "dashboard", "loops.html")
        generate.generate(
            root=self.fx.root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(),
            now=NOW,
        )
        with open(out) as f:
            html = f.read()
        # the held-forward value (7) shows up, marked as a hold
        self.assertIn(">7<", html)
        self.assertIn("hold", html.lower())

    def test_number_panel_missing_gap_renders_hole_not_stale_value(self):
        conn = self.fx.init_db()
        self.fx.add_loop(
            "gappy",
            dashboard_json={
                "panels": [
                    {
                        "title": "Dirty repos",
                        "metric": "repos.dirty",
                        "type": "number",
                        "missing": "gap",
                    },
                ]
            },
        )
        self.fx.add_run(
            conn,
            "r1",
            "gappy",
            iso(NOW - timedelta(days=2)),
            iso(NOW - timedelta(days=2)),
            "completed",
            "ok",
            "ok",
            "fine",
        )
        conn.execute(
            "INSERT INTO metrics (run_id, loop_name, ts, key, num, text) VALUES (?,?,?,?,?,NULL)",
            ("r1", "gappy", iso(NOW - timedelta(days=2)), "repos.dirty", 7.0),
        )
        self.fx.add_run(
            conn,
            "r2",
            "gappy",
            iso(NOW - timedelta(minutes=5)),
            iso(NOW - timedelta(minutes=4)),
            "completed",
            "ok",
            "ok",
            "fine",
        )
        conn.commit()
        conn.close()
        out = os.path.join(self.fx.root, "dashboard", "loops.html")
        generate.generate(
            root=self.fx.root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(),
            now=NOW,
        )
        with open(out) as f:
            html = f.read()
        # a hole, not the stale 7 carried forward
        self.assertNotIn(">7<", html)

    def test_watchdog_heartbeat_rendered_ok(self):
        conn = self.fx.init_db()
        self.fx.add_loop("watcher", type_="watchdog")
        self.fx.add_run(
            conn,
            "r1",
            "watcher",
            iso(NOW - timedelta(minutes=5)),
            iso(NOW - timedelta(minutes=4)),
            "completed",
            "ok",
            "ok",
            "probe ok",
        )
        conn.execute(
            "INSERT INTO heartbeats (loop_name, run_id, ts, ok, detail) VALUES (?,?,?,?,?)",
            ("watcher", "r1", iso(NOW - timedelta(minutes=4)), 1, None),
        )
        conn.commit()
        conn.close()
        out = os.path.join(self.fx.root, "dashboard", "loops.html")
        generate.generate(
            root=self.fx.root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(),
            now=NOW,
        )
        with open(out) as f:
            html = f.read()
        self.assertIn("Heartbeat", html)
        self.assertIn("probe ok", html)

    def test_watchdog_heartbeat_rendered_failed(self):
        conn = self.fx.init_db()
        self.fx.add_loop("watcher2", type_="watchdog")
        self.fx.add_run(
            conn,
            "r1",
            "watcher2",
            iso(NOW - timedelta(minutes=5)),
            iso(NOW - timedelta(minutes=4)),
            "completed",
            "alert",
            "alert",
            "probe failed",
        )
        conn.execute(
            "INSERT INTO heartbeats (loop_name, run_id, ts, ok, detail) VALUES (?,?,?,?,?)",
            (
                "watcher2",
                "r1",
                iso(NOW - timedelta(minutes=4)),
                0,
                "connection refused",
            ),
        )
        conn.commit()
        conn.close()
        out = os.path.join(self.fx.root, "dashboard", "loops.html")
        generate.generate(
            root=self.fx.root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(),
            now=NOW,
        )
        with open(out) as f:
            html = f.read()
        self.assertIn("Heartbeat", html)
        self.assertIn("probe failed", html)

    def test_no_network_no_external_assets(self):
        conn = self.fx.init_db()
        self.fx.add_loop("simple")
        self.fx.add_run(
            conn,
            "r1",
            "simple",
            iso(NOW - timedelta(minutes=5)),
            iso(NOW - timedelta(minutes=4)),
            "completed",
            "ok",
            "ok",
            "fine",
        )
        conn.close()
        out = os.path.join(self.fx.root, "dashboard", "loops.html")
        generate.generate(
            root=self.fx.root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(),
            now=NOW,
        )
        with open(out) as f:
            html = f.read()
        for token in ("http://", "https://", "//cdn", 'src="http'):
            self.assertNotIn(token, html)


class FailureSurfacingTests(unittest.TestCase):
    """error_detail on the page, the paste-into-an-agent handoff block, and the
    inline report drawer. All deterministic generator output — no model anywhere."""

    def setUp(self):
        self.fx = FixtureRoot()
        self.addCleanup(self.fx.cleanup)

    def _generate(self):
        out = os.path.join(self.fx.root, "dashboard", "loops.html")
        generate.generate(
            root=self.fx.root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(),
            now=NOW,
        )
        with open(out) as f:
            return f.read()

    def test_failed_run_error_detail_in_runs_table(self):
        conn = self.fx.init_db()
        self.fx.add_loop("l1")
        self.fx.add_run(
            conn,
            "r1",
            "l1",
            iso(NOW - timedelta(minutes=10)),
            iso(NOW - timedelta(minutes=9)),
            "engine-failed",
            error_detail="adapter exit 1 (attempt 1)",
            exit_code=1,
        )
        conn.close()
        html = self._generate()
        self.assertIn("adapter exit 1 (attempt 1)", html)

    def test_completed_run_no_detail_and_no_handoff(self):
        conn = self.fx.init_db()
        self.fx.add_loop("l1")
        self.fx.add_run(
            conn,
            "r1",
            "l1",
            iso(NOW - timedelta(minutes=10)),
            iso(NOW - timedelta(minutes=9)),
            "completed",
            "ok",
            "ok",
            "all clear",
        )
        conn.close()
        html = self._generate()
        self.assertNotIn('class="handoff"', html)

    def test_skipped_overlap_is_not_a_failure_no_handoff(self):
        conn = self.fx.init_db()
        self.fx.add_loop("l1")
        self.fx.add_run(
            conn,
            "r1",
            "l1",
            iso(NOW - timedelta(minutes=10)),
            iso(NOW - timedelta(minutes=10)),
            "skipped-overlap",
        )
        conn.close()
        html = self._generate()
        self.assertNotIn('class="handoff"', html)

    def test_failed_latest_run_renders_handoff_with_run_id_paths_and_docs(self):
        conn = self.fx.init_db()
        self.fx.add_loop("l1")
        self.fx.add_run(
            conn,
            "20260728T000000Z-l1-abc123",
            "l1",
            iso(NOW - timedelta(minutes=10)),
            iso(NOW - timedelta(minutes=9)),
            "engine-timeout",
            error_detail="killed after 900s",
            exit_code=124,
        )
        conn.close()
        html = self._generate()
        self.assertIn('class="handoff"', html)
        self.assertIn("20260728T000000Z-l1-abc123", html)
        self.assertIn("engine-timeout", html)
        self.assertIn("killed after 900s", html)
        self.assertIn("state/runs/20260728T000000Z-l1-abc123/", html)
        self.assertIn("engine.log", html)
        self.assertIn("docs/INTERFACES.md", html)

    def test_handoff_only_for_latest_run_not_older_failures(self):
        conn = self.fx.init_db()
        self.fx.add_loop("l1")
        self.fx.add_run(
            conn,
            "r-old",
            "l1",
            iso(NOW - timedelta(hours=2)),
            iso(NOW - timedelta(hours=2)),
            "engine-failed",
            error_detail="old failure",
            exit_code=1,
        )
        self.fx.add_run(
            conn,
            "r-new",
            "l1",
            iso(NOW - timedelta(minutes=10)),
            iso(NOW - timedelta(minutes=9)),
            "completed",
            "ok",
            "ok",
            "recovered",
        )
        conn.close()
        html = self._generate()
        # older failure still shows its why in the runs table…
        self.assertIn("old failure", html)
        # …but no handoff block, because the loop has recovered
        self.assertNotIn('class="handoff"', html)

    def test_fleet_row_headline_falls_back_to_error_detail(self):
        conn = self.fx.init_db()
        self.fx.add_loop("l1")
        self.fx.add_run(
            conn,
            "r1",
            "l1",
            iso(NOW - timedelta(minutes=10)),
            iso(NOW - timedelta(minutes=9)),
            "harness-error",
            error_detail="unhandled runner error (exit 0) at line 452",
        )
        conn.close()
        html = self._generate()
        # headline is NULL for failed runs; the fleet row must not be blank
        self.assertIn("unhandled runner error (exit 0) at line 452", html)

    def test_handoff_escapes_html_in_error_detail(self):
        conn = self.fx.init_db()
        self.fx.add_loop("l1")
        self.fx.add_run(
            conn,
            "r1",
            "l1",
            iso(NOW - timedelta(minutes=10)),
            iso(NOW - timedelta(minutes=9)),
            "engine-failed",
            error_detail='<script>alert("x")</script>',
            exit_code=1,
        )
        conn.close()
        html = self._generate()
        self.assertNotIn('<script>alert("x")</script>', html)
        self.assertIn("&lt;script&gt;", html)

    def test_report_drawer_renders_escaped_and_linked(self):
        conn = self.fx.init_db()
        self.fx.add_loop("l1")
        self.fx.add_run(
            conn,
            "r1",
            "l1",
            iso(NOW - timedelta(minutes=10)),
            iso(NOW - timedelta(minutes=9)),
            "completed",
            "ok",
            "ok",
            "fine",
        )
        conn.close()
        self.fx.write_latest_json(
            "l1",
            {
                "schema_version": 1,
                "run_id": "r1",
                "status": "ok",
                "status_reason": "",
                "headline": "fine",
                "metrics": "{}",
                "findings": [],
                "report_markdown": "## Section\nnumbers look fine\n<script>evil()</script>",
            },
        )
        html = self._generate()
        self.assertIn('class="report-drawer"', html)
        self.assertIn("numbers look fine", html)
        self.assertNotIn("<script>evil()</script>", html)
        self.assertIn("&lt;script&gt;evil()&lt;/script&gt;", html)
        # link to the full report survives alongside the drawer
        self.assertIn("../reports/l1/latest.md", html)

    def test_report_drawer_clamps_long_reports(self):
        conn = self.fx.init_db()
        self.fx.add_loop("l1")
        self.fx.add_run(
            conn,
            "r1",
            "l1",
            iso(NOW - timedelta(minutes=10)),
            iso(NOW - timedelta(minutes=9)),
            "completed",
            "ok",
            "ok",
            "fine",
        )
        conn.close()
        self.fx.write_latest_json(
            "l1",
            {
                "schema_version": 1,
                "run_id": "r1",
                "status": "ok",
                "status_reason": "",
                "headline": "fine",
                "metrics": "{}",
                "findings": [],
                "report_markdown": "x" * 20000,
            },
        )
        html = self._generate()
        self.assertIn('class="report-drawer"', html)
        self.assertNotIn("x" * 20000, html)
        self.assertIn("truncated", html.lower())

    def test_no_report_drawer_without_latest_json(self):
        conn = self.fx.init_db()
        self.fx.add_loop("l1")
        self.fx.add_run(
            conn,
            "r1",
            "l1",
            iso(NOW - timedelta(minutes=10)),
            iso(NOW - timedelta(minutes=9)),
            "completed",
            "ok",
            "ok",
            "fine",
        )
        conn.close()
        html = self._generate()
        self.assertNotIn('class="report-drawer"', html)


class FindingHandoffTests(unittest.TestCase):
    """Per-finding paste-into-an-agent blocks (Amendment 2) — mirrors the run-failure
    handoff block's deterministic-template style, but scoped to one open finding."""

    def setUp(self):
        self.fx = FixtureRoot()
        self.addCleanup(self.fx.cleanup)

    def _root_with_finding(
        self,
        loop_name,
        finding_id,
        severity="warn",
        detail=None,
        title=None,
        times_seen=1,
        dismiss=False,
        in_latest_json=True,
    ):
        conn = self.fx.init_db()
        self.fx.add_loop(loop_name)
        self.fx.add_run(
            conn,
            "r1",
            loop_name,
            iso(NOW - timedelta(minutes=5)),
            iso(NOW - timedelta(minutes=4)),
            "completed",
            severity,
            severity,
            "issues found",
        )
        title = title or f"{loop_name} finding {finding_id}"
        conn.execute(
            "INSERT INTO findings (finding_id, loop_name, title, severity, first_seen_run, "
            "first_seen_at, last_seen_run, last_seen_at, times_seen, resolved_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,NULL)",
            (
                finding_id,
                loop_name,
                title,
                severity,
                "r0",
                iso(NOW - timedelta(days=1)),
                "r1",
                iso(NOW - timedelta(minutes=4)),
                times_seen,
            ),
        )
        if dismiss:
            conn.execute(
                "INSERT INTO dispositions (loop_name, finding_id, action, note, "
                "snooze_until, created_at) VALUES (?,?,?,?,?,?)",
                (
                    loop_name,
                    finding_id,
                    "dismiss",
                    "handled elsewhere",
                    None,
                    iso(NOW - timedelta(hours=1)),
                ),
            )
        conn.commit()
        conn.close()
        if in_latest_json:
            finding_entry = {
                "finding_id": finding_id,
                "title": title,
                "severity": severity,
            }
            if detail is not None:
                finding_entry["detail"] = detail
            self.fx.write_latest_json(
                loop_name,
                {
                    "schema_version": 1,
                    "run_id": "r1",
                    "status": severity,
                    "status_reason": "x",
                    "headline": "issues found",
                    "report_markdown": "# x",
                    "metrics": "{}",
                    "findings": [finding_entry],
                },
            )
        return self.fx.root

    def _generate(self, root):
        out = os.path.join(root, "dashboard", "loops.html")
        generate.generate(
            root=root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(),
            now=NOW,
        )
        with open(out) as f:
            return f.read()

    def test_finding_paste_block(self):
        root = self._root_with_finding(
            "l1", "repo:no-remote", severity="warn", detail="23 unpushed commits"
        )
        html = self._generate(root)
        self.assertIn("finding-handoff", html)
        self.assertIn("repo:no-remote", html)
        self.assertIn("23 unpushed commits", html)  # detail from latest.json
        # MINOR #3 (fix wave, 2026-07-30): the visible one-liner now includes
        # --root (root here is a non-default temp dir), same as the handoff
        # block below it -- see test_visible_dismiss_command_includes_root_
        # when_nondefault for the dedicated non-default-root pin.
        self.assertIn(f"loopctl dismiss l1 repo:no-remote --root {root} --note", html)
        self.assertNotIn("approve", html.lower())

    def test_paste_block_includes_root_when_nondefault(self):
        root = self._root_with_finding("l1", "a:b")  # temp root ≠ ~/projects/loops
        self.assertIn(f"--root {root}", self._generate(root))

    def test_visible_dismiss_command_includes_root_when_nondefault(self):
        # MINOR #3 (fix wave, 2026-07-30): the pre-existing VISIBLE one-liner
        # (<code class="cmd">loopctl dismiss ...) used to omit --root while
        # the handoff block below it already included it -- on a non-default
        # root the easiest-to-copy visible command targeted the wrong root.
        # test_paste_block_includes_root_when_nondefault above only proves
        # "--root <root>" appears SOMEWHERE on the page, which the handoff
        # block alone already satisfied before this fix -- this pins the
        # visible command specifically.
        root = self._root_with_finding("l1", "repo:no-remote")
        html = self._generate(root)
        self.assertIn(f"loopctl dismiss l1 repo:no-remote --root {root} --note", html)

    def test_visible_reopen_command_includes_root_when_nondefault(self):
        # Same bug, same fix, in the suppressed-finding branch's "reopen"
        # one-liner.
        root = self._root_with_finding("l1", "repo:no-remote", dismiss=True)
        html = self._generate(root)
        self.assertIn(f"loopctl reopen l1 repo:no-remote --root {root}", html)

    def test_suppressed_finding_gets_no_paste_block(self):
        root = self._root_with_finding("l1", "repo:no-remote", dismiss=True)
        html = self._generate(root)
        # suppressed finding is still shown (greyed), just without a handoff block --
        # exact rendered markup, not just the substring "finding-handoff" which also
        # appears in the page's static CSS rule regardless of wiring
        self.assertIn("repo:no-remote", html)
        self.assertNotIn('<details class="finding-handoff"', html)

    def test_finding_missing_from_latest_json_still_renders_without_crash(self):
        # sqlite has the open finding but latest.json doesn't carry it (e.g. resolved
        # since, or the file is simply missing) — must degrade, never crash.
        root = self._root_with_finding("l1", "repo:no-remote", in_latest_json=False)
        html = self._generate(root)
        self.assertIn('<details class="finding-handoff"', html)
        self.assertIn("repo:no-remote", html)


class TagsProvenanceEventsTests(unittest.TestCase):
    """Tag chips, per-loop provenance line, and the fleet-wide recent-events strip
    (Amendment 2 -- 2026-07-30). loop_events rows are inserted directly against the
    §3 schema fixture; tags are injected via fake_loopconf_parse's conf_overrides seam
    (the fake KEY=value fixture parser doesn't replicate loopconf.py's real `tags=`
    comma-split/quoting grammar -- that's covered by bin/loopconf.py's own tests)."""

    def setUp(self):
        self.fx = FixtureRoot()
        self.addCleanup(self.fx.cleanup)
        self.fx.init_db().close()
        self._tag_overrides = {}

    def _root_with_loop(self, name, tags=None, description="a loop"):
        self.fx.add_loop(name, description=description)
        if tags is not None:
            self._tag_overrides[name] = {"tags": tags}
        return self.fx.root

    def _insert_event(self, root, loop_name, event, actor, detail=None, ts=None):
        conn = sqlite3.connect(self.fx.db_path)
        try:
            self.fx.add_event(conn, loop_name, event, actor, ts=ts, detail=detail)
        finally:
            conn.close()

    def _generate(self, root):
        out = os.path.join(root, "dashboard", "loops.html")
        generate.generate(
            root=root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(self._tag_overrides),
            schedule_parse=fake_schedule_parse(),
            now=NOW,
        )
        with open(out) as f:
            return f.read()

    def test_tags_and_provenance_render(self):
        root = self._root_with_loop("tagged", tags=["project:x"])
        self._insert_event(
            root,
            "tagged",
            "imported",
            "claude/maguyva",
            detail='{"source_skill": "~/.claude/skills/seo-audit"}',
        )
        html = self._generate(root)
        self.assertIn('class="tag"', html)
        self.assertIn("project:x", html)
        self.assertIn("claude/maguyva", html)
        self.assertIn("seo-audit", html)

    def test_provenance_without_source_skill_falls_back_to_actor_and_date(self):
        root = self._root_with_loop("plain")
        self._insert_event(
            root, "plain", "created", "tester", ts=iso(NOW - timedelta(days=2))
        )
        html = self._generate(root)
        self.assertIn("created by tester", html)
        self.assertIn((NOW - timedelta(days=2)).strftime("%Y-%m-%d"), html)

    def test_no_events_no_provenance_line_for_that_loop(self):
        root = self._root_with_loop("lonely")
        html = self._generate(root)
        self.assertNotIn('class="provenance', html)

    def test_no_tags_no_chip_but_data_tags_attribute_present_empty(self):
        # Fix round 1: an untagged loop must still carry `data-tags=""` (not omit the
        # attribute) -- the client-side filter only touches `[data-tags]` elements, so an
        # element missing the attribute entirely would stay visible under every tag
        # selection instead of being correctly hidden. No visible chips either way.
        root = self._root_with_loop("untagged")
        html = self._generate(root)
        self.assertNotIn('class="tag"', html)
        self.assertIn('<tr data-tags="">', html)
        self.assertIn('id="loop-untagged" data-tags=""', html)

    def test_tag_filter_hides_untagged_loops_structural_precondition(self):
        # Selecting a tag must show ONLY loops carrying that tag (matches loopctl's
        # `list --tag` exact-match contract) -- an untagged loop must never leak through.
        # We can't run the browser-side JS in this test, so we assert the structural
        # precondition the JS logic depends on: every loop row/section carries a
        # `data-tags` attribute (queryable via `[data-tags]`), empty for the untagged
        # loop, so `''.split(' ')` -> `['']` never matches a real tag and the JS hides it.
        root = self._root_with_loop("tagged", tags=["project:x"])
        self.fx.add_loop("untagged")
        html = self._generate(root)
        self.assertIn('<tr data-tags="project:x">', html)
        self.assertIn('<tr data-tags="">', html)
        self.assertIn('id="loop-tagged" data-tags="project:x"', html)
        self.assertIn('id="loop-untagged" data-tags=""', html)
        # the JS must match tags exactly against the split list, not treat a missing
        # attribute as "always visible"
        self.assertIn("querySelectorAll('[data-tags]')", html)

    def test_tag_filter_select_only_rendered_when_tags_exist(self):
        root = self._root_with_loop("untagged")
        html = self._generate(root)
        self.assertNotIn('id="tag-filter"', html)

    def test_tag_filter_select_rendered_and_populated_when_tags_exist(self):
        root = self._root_with_loop("tagged", tags=["project:x", "seo"])
        html = self._generate(root)
        self.assertIn('id="tag-filter"', html)
        self.assertIn('<option value="project:x">', html)
        self.assertIn('<option value="seo">', html)

    def test_data_tags_attribute_on_row_and_section_space_separated(self):
        root = self._root_with_loop("tagged", tags=["project:x", "seo"])
        html = self._generate(root)
        self.assertIn('data-tags="project:x seo"', html)

    def test_chips_render_in_both_fleet_row_and_loop_section(self):
        root = self._root_with_loop("tagged", tags=["project:x"])
        html = self._generate(root)
        self.assertEqual(html.count('<span class="tag">project:x</span>'), 2)

    def test_recent_events_strip(self):
        root = self._root_with_loop("l1")
        self._insert_event(root, "l1", "created", "tester")
        html = self._generate(root)
        self.assertIn('id="recent-events"', html)
        self.assertIn("created", html)

    def test_recent_events_strip_empty_state(self):
        root = self._root_with_loop("l1")
        html = self._generate(root)
        self.assertIn('id="recent-events"', html)
        self.assertIn("no lifecycle events yet", html)

    def test_survives_pre_amendment_2_sqlite_missing_loop_events_table(self):
        # Fix round 1: a state/loops.sqlite created before this branch has no loop_events
        # table until something re-runs db.py init. generate() must degrade gracefully
        # (empty events strip, no provenance line) rather than crashing with
        # sqlite3.OperationalError: no such table: loop_events.
        root = self._root_with_loop("l1")
        conn = sqlite3.connect(self.fx.db_path)
        conn.execute("DROP TABLE loop_events")
        conn.commit()
        conn.close()
        html = self._generate(root)
        self.assertIn('id="recent-events"', html)
        self.assertIn("no lifecycle events yet", html)
        self.assertNotIn('class="provenance', html)

    def test_recent_events_strip_limited_to_15_fleet_wide(self):
        root = self._root_with_loop("l1")
        for i in range(20):
            self._insert_event(
                root,
                "l1",
                "paused" if i % 2 else "resumed",
                "tester",
                ts=iso(NOW - timedelta(minutes=20 - i)),
            )
        html = self._generate(root)
        table_start = html.index('id="recent-events"')
        table_end = html.index("</section>", table_start)
        strip = html[table_start:table_end]
        body_start = strip.index("<tbody>")
        body_end = strip.index("</tbody>")
        self.assertEqual(strip[body_start:body_end].count("<tr>"), 15)

    def test_event_detail_and_actor_html_escaped(self):
        root = self._root_with_loop("l1")
        self._insert_event(
            root,
            "l1",
            "imported",
            '<script>alert("x")</script>',
            detail='{"source_skill": "<img src=x onerror=alert(1)>"}',
        )
        html = self._generate(root)
        self.assertNotIn('<script>alert("x")</script>', html)
        self.assertNotIn("<img src=x onerror=alert(1)>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_tag_filter_js_toggles_display_none_on_non_matching(self):
        root = self._root_with_loop("tagged", tags=["project:x"])
        html = self._generate(root)
        self.assertIn("onchange=", html)
        self.assertIn("display", html)
        self.assertIn("none", html)


class RunTrichotomyTests(unittest.TestCase):
    """running / overdue / died trichotomy for an in-flight run (finished_at IS NULL),
    per docs/INTERFACES.md §4.6 + §10 (Amendment 2 -- 2026-07-30): age <= timeout_s ->
    running (live, not a failure, does not count toward needs_attention); age in
    (timeout_s, timeout_s+120] -> overdue (amber, still running past timeout, amber
    attention); age > timeout_s+120 -> died (existing rule, unchanged, red harness-
    problem)."""

    def setUp(self):
        self.fx = FixtureRoot()
        self.addCleanup(self.fx.cleanup)
        self.fx.init_db().close()
        self._run_counter = 0

    def _root_with_loop(self, name, conf_extra=""):
        self.fx.add_loop(name)
        if conf_extra:
            conf_path = os.path.join(self.fx.root, "loops.d", name, "loop.conf")
            with open(conf_path, "a") as f:
                f.write(conf_extra + "\n")
        return self.fx.root

    def _insert_unfinished_run(self, root, loop_name, started_secs_ago):
        self._run_counter += 1
        conn = sqlite3.connect(self.fx.db_path)
        try:
            self.fx.add_run(
                conn,
                f"r{self._run_counter}",
                loop_name,
                iso(NOW - timedelta(seconds=started_secs_ago)),
                finished_at=None,
                runner_status="started",
            )
        finally:
            conn.close()

    def _reset_runs(self, root):
        conn = sqlite3.connect(self.fx.db_path)
        try:
            conn.execute("DELETE FROM runs")
            conn.commit()
        finally:
            conn.close()

    def _generate(self, root):
        out = os.path.join(root, "dashboard", "loops.html")
        generate.generate(
            root=root,
            out_file=out,
            loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(),
            now=NOW,
        )
        with open(out) as f:
            return f.read()

    def test_run_states_trichotomy(self):
        # Fix round 1: the original assertIn("running"/"overdue"/"died", html) forms were
        # vacuous -- the page's static .badge.running/.badge.overdue/.badge.died CSS rules
        # (emitted unconditionally, regardless of wiring) already contain those literal
        # words, so all three passed even against a stub that never emits the badges.
        # Assert the exact rendered badge markup instead (matches the .badge.stale/.died
        # precedent elsewhere in this file), and pin both boundaries the spec defines with
        # <=: age == timeout_s is still "running" (not yet overdue), and age ==
        # timeout_s+120 is still "overdue" (not yet died) -- the ages most likely to flip
        # silently under a future off-by-one refactor.
        root = self._root_with_loop("l1", conf_extra="timeout_s=60")

        self._insert_unfinished_run(root, "l1", started_secs_ago=60)  # == timeout_s
        html = self._generate(root)
        self.assertIn('<span class="badge running">running</span>', html)
        self.assertNotIn('<span class="badge overdue">overdue</span>', html)
        self.assertNotIn('<span class="badge died">died</span>', html)

        self._reset_runs(root)
        self._insert_unfinished_run(
            root, "l1", started_secs_ago=180
        )  # == timeout_s+120
        html = self._generate(root)
        self.assertIn('<span class="badge overdue">overdue</span>', html)
        self.assertNotIn('<span class="badge running">running</span>', html)
        self.assertNotIn('<span class="badge died">died</span>', html)

        self._reset_runs(root)
        self._insert_unfinished_run(
            root, "l1", started_secs_ago=181
        )  # == timeout_s+120+1
        html = self._generate(root)
        self.assertIn('<span class="badge died">died</span>', html)
        self.assertNotIn('<span class="badge running">running</span>', html)
        self.assertNotIn('<span class="badge overdue">overdue</span>', html)

    def test_running_badge_exact_markup_and_not_needs_attention(self):
        root = self._root_with_loop("l2", conf_extra="timeout_s=60")
        self._insert_unfinished_run(root, "l2", started_secs_ago=10)
        html = self._generate(root)
        self.assertIn('<span class="badge running">running</span>', html)
        self.assertNotIn("needs attention 1", html)

    def test_overdue_badge_exact_markup_and_counts_toward_needs_attention(self):
        root = self._root_with_loop("l3", conf_extra="timeout_s=60")
        self._insert_unfinished_run(root, "l3", started_secs_ago=90)
        html = self._generate(root)
        self.assertIn('<span class="badge overdue">overdue</span>', html)
        self.assertIn("needs attention 1", html)

    def test_died_still_counts_toward_needs_attention_and_red(self):
        root = self._root_with_loop("l4", conf_extra="timeout_s=60")
        self._insert_unfinished_run(root, "l4", started_secs_ago=300)
        html = self._generate(root)
        self.assertIn('<span class="badge died">died</span>', html)
        self.assertIn("needs attention 1", html)


if __name__ == "__main__":
    unittest.main()
