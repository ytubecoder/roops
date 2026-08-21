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


class TestSystemdEmitter(unittest.TestCase):
    """§5.1 — systemd sibling of the launchd emitter. The grammar is unchanged;
    only the representation differs, so every case here mirrors an existing
    launchd case above.

    Note the plan's draft used a `every 900s` spec that the grammar does not
    have — the real interval form is `interval:<N>m|h`, so these mirror that.
    """

    def test_interval_minutes_becomes_on_unit_active(self):
        got = schedule.parse("interval:15m")
        self.assertEqual(got["systemd"], {"OnUnitActiveSec": "900s"})
        self.assertEqual(got["expected_interval_s"], 900)

    def test_interval_hours_becomes_on_unit_active(self):
        got = schedule.parse("interval:2h")
        self.assertEqual(got["systemd"], {"OnUnitActiveSec": "7200s"})

    def test_daily_becomes_on_calendar(self):
        got = schedule.parse("daily:19:15")
        self.assertEqual(got["systemd"], {"OnCalendar": "*-*-* 19:15:00"})

    def test_daily_pads_single_digit_fields(self):
        got = schedule.parse("daily:07:05")
        self.assertEqual(got["systemd"], {"OnCalendar": "*-*-* 07:05:00"})

    def test_times_becomes_a_list_of_on_calendar(self):
        got = schedule.parse("times:07:30,19:30")
        self.assertEqual(
            got["systemd"],
            {"OnCalendar": ["*-*-* 07:30:00", "*-*-* 19:30:00"]},
        )

    def test_weekly_prefixes_the_systemd_weekday_name(self):
        got = schedule.parse("weekly:mon:08:00")
        self.assertEqual(got["systemd"], {"OnCalendar": "Mon *-*-* 08:00:00"})

    def test_weekly_sunday_is_sun_not_zero(self):
        """launchd counts weekdays 0-6; systemd names them. Sunday is the
        index that differs most dangerously between the two."""
        got = schedule.parse("weekly:sun:08:00")
        self.assertEqual(got["systemd"], {"OnCalendar": "Sun *-*-* 08:00:00"})

    def test_weekly_saturday(self):
        got = schedule.parse("weekly:sat:23:59")
        self.assertEqual(got["systemd"], {"OnCalendar": "Sat *-*-* 23:59:00"})

    def test_monthly_pins_the_day_of_month(self):
        got = schedule.parse("monthly:01:09:00")
        self.assertEqual(got["systemd"], {"OnCalendar": "*-*-01 09:00:00"})

    def test_monthly_two_digit_day(self):
        got = schedule.parse("monthly:28:09:00")
        self.assertEqual(got["systemd"], {"OnCalendar": "*-*-28 09:00:00"})

    def test_manual_emits_no_systemd_timer(self):
        got = schedule.parse("manual")
        self.assertEqual(got["systemd"], {})
        self.assertEqual(got["kind"], "manual")

    def test_every_parse_path_carries_a_systemd_key(self):
        """A missing key on one branch is the failure mode Task 18 would hit
        at install time, on one loop, in production."""
        for spec in ("manual", "interval:15m", "interval:2h", "daily:07:30",
                     "times:07:30,19:30", "weekly:mon:08:00",
                     "monthly:01:09:00"):
            with self.subTest(spec=spec):
                self.assertIn("systemd", schedule.parse(spec))

    def test_launchd_output_is_unchanged(self):
        """The systemd emitter must not perturb the existing representation —
        the whole macOS fleet depends on it."""
        got = schedule.parse("daily:19:15")
        self.assertEqual(got["launchd"],
                         {"StartCalendarInterval": {"Hour": 19, "Minute": 15}})

    def test_systemd_form_survives_the_cli_json_round_trip(self):
        proc = subprocess.run(
            [sys.executable, str(BIN), "parse", "weekly:mon:08:00", "--json"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout)["systemd"],
                         {"OnCalendar": "Mon *-*-* 08:00:00"})


if __name__ == "__main__":
    unittest.main()
