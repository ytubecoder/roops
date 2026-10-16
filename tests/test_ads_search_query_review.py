"""Native Search fifth-input integration, using real prechecks and fake curl."""

import copy
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class SearchInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="search-input-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        bindir = self.root / "bin"
        bindir.mkdir()
        curl = bindir / "curl"
        curl.write_text("""#!/usr/bin/env python3
import os, sys
from pathlib import Path
url = next(x for x in sys.argv if x.startswith("http"))
name = url.split("/api/ads/")[-1].split("?")[0]
Path(sys.argv[sys.argv.index("-o") + 1]).write_text((Path(os.environ["FIXTURES"]) / (name + ".json")).read_text())
with open(os.environ["FETCH_LOG"], "a") as log:
    log.write(name + "\\n")
""")
        curl.chmod(0o755)
        self.path = str(bindir) + os.pathsep + os.environ["PATH"]
        self.counter = 0
        self.snapshot = {
            "schema": 1,
            "available": True,
            "stale": False,
            "age_seconds": 30,
            "generated_at": "2026-09-13T13:00:00+00:00",
            "last_attempt": {},
            "complete_window": ["2026-09-06", "2026-09-12"],
            "partial_day": "2026-09-13",
            "time_zone": "Asia/Singapore",
            "campaigns": [
                {"campaign": {"id": cid, "name": cid}}
                for cid in ("google", "intl", "orphan")
            ],
            "coverage": {
                cid: {"complete": {"visible_click_fraction": 0.5}, "partial": {}}
                for cid in ("google", "intl", "orphan")
            },
            "queries": [
                {"campaign_id": cid, "term": cid + " coding mcp"}
                for cid in ("google", "intl", "orphan")
            ],
            "limits": {"queries": {"total": 2000, "shown": 3, "truncated": True}},
            "truncated": True,
            "sources": {
                "queries_daily": {"rows_fetched": 3000, "possible_truncation": False}
            },
        }

    def run_precheck(self, name, snapshot):
        self.counter += 1
        fixture = self.root / str(self.counter)
        fixture.mkdir()
        payloads = {
            "scoreboard": {"networks": {}, "budget": {}},
            "journal": {"rows": []},
            "program-events": {"events": []},
            "search-query-review": snapshot,
            "campaigns": {
                "cards": [
                    {
                        "key": key,
                        "legs": [
                            {
                                "network": "google",
                                "variant_ids": [],
                                "campaigns": [{"campaign_id": cid, "name": cid}],
                            }
                        ],
                    }
                    for key, cid in (("g-msg", "google"), ("g-intl", "intl"))
                ]
            },
        }
        for key, value in payloads.items():
            (fixture / (key + ".json")).write_text(json.dumps(value))
        out = fixture / "out"
        result = subprocess.run(
            ["bash", str(REPO / "loops.d" / name / "precheck.sh")],
            env={
                **os.environ,
                "PATH": self.path,
                "FIXTURES": str(fixture),
                "FETCH_LOG": str(fixture / "fetch.log"),
                "OUT_DIR": str(out),
                "LOOPS_ROOT": str(self.root),
                "RUN_ID": "fixture",
                "GC_BASE": "http://fixture",
            },
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (fixture / "fetch.log").read_text().splitlines(),
            [
                "scoreboard",
                "campaigns",
                "journal",
                "program-events",
                "search-query-review",
            ],
        )
        self.assertIn("of 5 GC endpoints", result.stdout)
        self.assertEqual(
            json.loads((out / "inputs/search-query-review.json").read_text()), snapshot
        )
        return result.stdout

    def test_fresh_truncated_input_is_present_and_ownership_is_preserved(self):
        for name in ("ads-google", "ads-intl"):
            with self.subTest(loop=name):
                text = self.run_precheck(name, self.snapshot)
                self.assertIn("inputs.missing: 0", text)
                self.assertIn("any_truncation= True", text)
                self.assertIn(
                    "Native campaigns missing an active registry owner: ['orphan']",
                    text,
                )
                detail = json.loads(
                    next(
                        line
                        for line in text.splitlines()
                        if line.startswith('{"queries":[')
                    )
                )
                expected = {"google" if name == "ads-google" else "intl", "orphan"}
                self.assertEqual(
                    {row["campaign_id"] for row in detail["queries"]}, expected
                )
                self.assertIn("Complete account-local days:", text)
                self.assertIn("visible_click_fraction", text)

    def test_stale_missing_failed_and_missing_freshness_each_count_one_gap(self):
        cases = [
            {"available": False, "stale": True},
            {**self.snapshot, "stale": True, "stale_reasons": ["day rollover"]},
            {**self.snapshot, "last_attempt": {"error": "read failed"}},
        ]
        for field in ("stale", "generated_at", "age_seconds"):
            data = copy.deepcopy(self.snapshot)
            del data[field]
            cases.append(data)
        for name in ("ads-google", "ads-intl"):
            for index, data in enumerate(cases):
                with self.subTest(loop=name, case=index):
                    text = self.run_precheck(name, data)
                    self.assertIn("inputs.missing: 1", text)
                    self.assertIn("search_query_review=MISSING_OR_STALE", text)
                    if data.get("available"):
                        self.assertIn("INPUT GAP:", text)
                    else:
                        self.assertIn("review is blocked", text)

    def test_digest_trim_keeps_raw_input_and_discloses_omissions(self):
        data = copy.deepcopy(self.snapshot)
        data["queries"] = [
            {"campaign_id": "orphan", "term": "x" * 500 + str(i)} for i in range(200)
        ]
        for name in ("ads-google", "ads-intl"):
            with self.subTest(loop=name):
                text = self.run_precheck(name, data)
                line = next(
                    line
                    for line in text.splitlines()
                    if line.startswith('{"queries":[')
                )
                self.assertLessEqual(len(line.encode()), 30000)
                self.assertLess(len(json.loads(line)["queries"]), 200)
                self.assertIn('"eligible_cached_rows": 200', text)
                self.assertIn(
                    "omitted cached rows remain in inputs/search-query-review.json",
                    text,
                )

    def test_intent_recommendations_keep_targets_before_bulky_rows_and_ownership(self):
        data = copy.deepcopy(self.snapshot)
        data.update(
            schema=2,
            review_window=["2026-08-16", "2026-09-12"],
            conversion_evidence={
                "available": True,
                "actions": {
                    "signup_credits": {"resource_name": "signup"},
                    "repository_linked_credits": {"resource_name": "linked"},
                },
            },
            intent_recommendations=[
                {
                    "campaign_id": cid,
                    "kind": "keyword_pause_review",
                    "targets": [{"keyword_resource": "stable-" + cid}],
                    "metrics": {
                        "clicks": 23,
                        "signup_credits": 0,
                        "repository_linked_credits": 0,
                    },
                }
                for cid in ("google", "intl", "orphan")
            ],
        )
        for name, cid in (("ads-google", "google"), ("ads-intl", "intl")):
            with self.subTest(loop=name):
                text = self.run_precheck(name, data)
                rows = [
                    json.loads(line.split("INTENT_REVIEW ", 1)[1])
                    for line in text.splitlines()
                    if "- INTENT_REVIEW " in line
                ]
                self.assertEqual([row["campaign_id"] for row in rows], [cid])
                self.assertEqual(
                    rows[0]["targets"][0]["keyword_resource"], "stable-" + cid
                )
                self.assertLess(
                    text.index("- INTENT_REVIEW "), text.index("## Native Search query")
                )
                self.assertIn("independent of Band E", text)
                self.assertIn("Omit order.*", text)
                self.assertNotIn("order.verb: pause_keywords", text)

    def test_legacy_snapshot_is_explicit_28_day_input_gap(self):
        text = self.run_precheck("ads-google", self.snapshot)
        self.assertIn("28-day Search intent/action evidence is unavailable", text)


if __name__ == "__main__":
    unittest.main()
