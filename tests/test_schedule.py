"""Tests for bin/schedule.py — §5.1 schedule grammar parser."""
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN = REPO_ROOT / "bin" / "schedule.py"

spec = importlib.util.spec_from_file_location("schedule_mod", BIN)
schedule = importlib.util.module_from_spec(spec)
spec.loader.exec_module(schedule)


class TestScheduleParse(unittest.TestCase):
    def test_manual(self):
        result = schedule.parse("manual")
        self.assertEqual(result["kind"], "manual")
        self.assertEqual(result["expected_interval_s"], 0)
        self.assertEqual(result["launchd"], {})

    def test_interval_minutes(self):
        result = schedule.parse("interval:15m")
        self.assertEqual(result["kind"], "interval")
        self.assertEqual(result["launchd"], {"StartInterval": 900})
        self.assertEqual(result["expected_interval_s"], 900)

    def test_interval_hours(self):
        result = schedule.parse("interval:2h")
        self.assertEqual(result["launchd"], {"StartInterval": 7200})
        self.assertEqual(result["expected_interval_s"], 7200)

    def test_daily(self):
        result = schedule.parse("daily:07:30")
        self.assertEqual(result["kind"], "daily")
        self.assertEqual(
            result["launchd"], {"StartCalendarInterval": {"Hour": 7, "Minute": 30}}
        )
        self.assertEqual(result["expected_interval_s"], 86400)

    def test_times_multiple(self):
        result = schedule.parse("times:07:30,19:30")
        self.assertEqual(result["kind"], "times")
        self.assertEqual(
            result["launchd"],
            {
                "StartCalendarInterval": [
                    {"Hour": 7, "Minute": 30},
                    {"Hour": 19, "Minute": 30},
                ]
            },
        )
        self.assertEqual(result["expected_interval_s"], 86400 // 2)

    def test_weekly(self):
        result = schedule.parse("weekly:mon:08:00")
        self.assertEqual(result["kind"], "weekly")
        self.assertEqual(
            result["launchd"],
            {"StartCalendarInterval": {"Hour": 8, "Minute": 0, "Weekday": 1}},
        )
        self.assertEqual(result["expected_interval_s"], 7 * 86400)

    def test_weekly_sunday_is_0(self):
        result = schedule.parse("weekly:sun:08:00")
        self.assertEqual(result["launchd"]["StartCalendarInterval"]["Weekday"], 0)

    def test_weekly_saturday_is_6(self):
        result = schedule.parse("weekly:sat:08:00")
        self.assertEqual(result["launchd"]["StartCalendarInterval"]["Weekday"], 6)

    def test_monthly(self):
        result = schedule.parse("monthly:01:09:00")
        self.assertEqual(result["kind"], "monthly")
        self.assertEqual(
            result["launchd"],
            {"StartCalendarInterval": {"Hour": 9, "Minute": 0, "Day": 1}},
        )
        self.assertEqual(result["expected_interval_s"], 30 * 86400)

    def test_invalid_spec_raises(self):
        with self.assertRaises(ValueError):
            schedule.parse("bogus")

    def test_invalid_interval_unit_raises(self):
        with self.assertRaises(ValueError):
            schedule.parse("interval:15x")

    def test_invalid_daily_time_raises(self):
        with self.assertRaises(ValueError):
            schedule.parse("daily:25:99")

    def test_invalid_weekly_day_raises(self):
        with self.assertRaises(ValueError):
            schedule.parse("weekly:funday:08:00")

    def test_empty_spec_raises(self):
        with self.assertRaises(ValueError):
            schedule.parse("")


class TestScheduleCLI(unittest.TestCase):
    def run_cli(self, spec_str):
        return subprocess.run(
            [sys.executable, str(BIN), "parse", spec_str, "--json"],
            capture_output=True,
            text=True,
        )

    def test_cli_parses_daily(self):
        proc = self.run_cli("daily:07:30")
        self.assertEqual(proc.returncode, 0)
        data = json.loads(proc.stdout)
        self.assertEqual(data["kind"], "daily")

    def test_cli_invalid_spec_clear_error(self):
        proc = self.run_cli("nonsense")
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(proc.stderr.strip())


if __name__ == "__main__":
    unittest.main()
