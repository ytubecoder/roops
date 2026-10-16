import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
from ads_program_events import MAX_BYTES, event_digest


class ProgramEventTests(unittest.TestCase):
    def test_old_preview_has_exact_omissions_and_source_while_recent_is_complete(self):
        events = {
            "events": [
                {"date": "2026-07-01", "text": "x" * 500},
                {
                    "date": "2026-10-16",
                    "text": "y" * 500,
                    "reference": "native-receipt",
                },
            ]
        }
        lines = event_digest(events, "2026-10-16T02:00:00Z", "Asia/Singapore")
        rows = [
            json.loads(line.split("PROGRAM_EVENT ", 1)[1])
            for line in lines
            if line.startswith("- PROGRAM_EVENT ")
        ]
        self.assertEqual(rows[0]["event"], events["events"][1])
        self.assertTrue(rows[0]["text_complete"])
        self.assertEqual(rows[1]["omitted_text_chars"], 280)
        self.assertFalse(rows[1]["text_complete"])
        self.assertIn("events[0]", rows[1]["source"])
        self.assertIn("Never infer a resolution", "\n".join(lines))

    def test_large_recent_events_are_omitted_whole_with_explicit_gap(self):
        latest = {
            "date": "2026-10-16",
            "text": "Latest correction: keep the original scope open.",
        }
        lines = event_digest(
            {"events": [{"date": "2026-10-15", "text": "z" * MAX_BYTES}, latest]},
            "2026-10-16T02:00:00Z",
        )
        text = "\n".join(lines)
        self.assertIn(latest["text"], text)
        self.assertNotIn("z" * 220, text)
        self.assertIn("INPUT GAP", text)
        self.assertIn('"recent_full":1', text)
        self.assertLess(len(text.encode()), MAX_BYTES)

    def test_account_day_boundary_and_undated_fields_do_not_get_old_prefixes(self):
        correction = "x" * 250 + " Full qualification."
        lines = event_digest(
            {
                "events": [
                    {"date": "2026-09-19", "text": correction},
                    {"text": correction},
                ]
            },
            "2026-10-15T18:00:00Z",
            "Asia/Singapore",
        )
        text = "\n".join(lines)
        self.assertIn("2026-09-19 through 2026-10-16", text)
        self.assertEqual(text.count("Full qualification."), 2)
        self.assertIn("date_unverified_full", text)


if __name__ == "__main__":
    unittest.main()
