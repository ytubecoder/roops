import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
from ads_evidence import decision_digest


class AdsEvidenceTests(unittest.TestCase):
    def test_legacy_high_impressions_cannot_become_eligible(self):
        board = {
            "networks": {
                "google": {
                    "rows": [
                        {
                            "variant_id": "g4",
                            "impressions": 999999,
                            "evaluator": {"action": "kill"},
                        }
                    ]
                }
            }
        }
        text = "\n".join(decision_digest(board))
        self.assertIn("legacy/missing decision", text)
        self.assertNotIn("EVAL-ELIGIBLE", text)

    def test_paused_and_silent_targets_survive_digest(self):
        decision = {
            "version": 1,
            "decision_id": "abc",
            "action": "unproven",
            "actionable": False,
            "targets": [
                {
                    "ad_group_id": "silent-search",
                    "state": "active",
                    "evidence_state": "unsampled",
                },
                {"ad_group_id": "old-youtube", "state": "paused"},
            ],
            "blockers": ["reference cohort unavailable"],
            "window": {"start_ms": 1, "end_ms": 2},
        }
        board = {
            "networks": {
                "google": {
                    "rows": [
                        {"variant_id": "g4", "evaluator": decision},
                        {"variant_id": "g9", "evaluator": decision},
                    ]
                }
            }
        }
        text = "\n".join(decision_digest(board, {"g4"}))
        for part in (
            '"decision_id": "abc"',
            '"actionable": false',
            "silent-search",
            "old-youtube",
            "reference cohort unavailable",
        ):
            self.assertIn(part, text)
        self.assertNotIn("- g9:", text)

    def test_no_data_is_not_zero_performance(self):
        self.assertIn("INPUT GAP", "\n".join(decision_digest(None)))

    def test_sync_source_gap_survives_digest(self):
        decision = {
            "version": 2,
            "decision_id": "sync-proof",
            "action": "unproven",
            "objective": "first_index_completed",
            "business_objective": "first_collected_payment",
            "objective_status": "source_unavailable",
            "diagnostic_objective": "account_created",
            "diagnostic_band": "E",
            "actionable": False,
            "blockers": ["successful-sync acquisition evidence is unavailable"],
        }
        board = {
            "networks": {
                "google": {"rows": [{"variant_id": "g17", "evaluator": decision}]}
            }
        }
        text = "\n".join(decision_digest(board))
        for part in (
            "sync-proof",
            "first_index_completed",
            "first_collected_payment",
            "source_unavailable",
            "successful-sync acquisition evidence is unavailable",
        ):
            self.assertIn(part, text)
        self.assertNotIn("legacy/missing", text)


class PrecheckIntegrationTests(unittest.TestCase):
    def test_all_readers_request_matching_windows_and_keep_evidence(self):
        import http.server
        import json
        import os
        import subprocess
        import tempfile
        import threading
        import urllib.parse

        requests = []
        decision = {
            "version": 2,
            "decision_id": "stable-proof",
            "action": "unproven",
            "actionable": False,
            "targets": [
                {
                    "ad_group_id": "silent",
                    "state": "active",
                    "evidence_state": "unsampled",
                }
            ],
            "blockers": ["cohort unavailable"],
        }
        payloads = {
            "/api/ads/scoreboard": {
                "days": 7,
                "budget": {},
                "networks": {
                    "google": {
                        "rows": [
                            {"variant_id": "g4", "evaluator": decision},
                            {"variant_id": "g9", "evaluator": decision},
                        ]
                    }
                },
            },
            "/api/ads/campaigns": {
                "cards": [
                    {
                        "key": "g-msg",
                        "legs": [{"network": "google", "variant_ids": ["g4"]}],
                    },
                    {
                        "key": "g-intl",
                        "legs": [{"network": "google", "variant_ids": ["g9"]}],
                    },
                ]
            },
            "/api/ads/journal": {"rows": []},
            "/api/ads/program-events": {"events": []},
        }

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                data = json.dumps(
                    payloads[urllib.parse.urlparse(self.path).path]
                ).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        repo = Path(__file__).resolve().parents[1]
        try:
            with tempfile.TemporaryDirectory() as directory:
                for name in ("ads-google", "ads-intl", "ads-program"):
                    out = Path(directory) / name
                    env = dict(
                        os.environ,
                        OUT_DIR=str(out),
                        LOOPS_ROOT=directory,
                        RUN_ID="fixture",
                        GC_BASE=f"http://127.0.0.1:{server.server_port}",
                    )
                    result = subprocess.run(
                        ["bash", str(repo / "loops.d" / name / "precheck.sh")],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('"decision_id": "stable-proof"', result.stdout)
                    self.assertIn('"ad_group_id": "silent"', result.stdout)
                    self.assertNotIn("EVAL-ELIGIBLE", result.stdout)
            evidence_requests = [
                p for p in requests if "scoreboard" in p or "campaigns" in p
            ]
            self.assertEqual(len(evidence_requests), 6)
            self.assertTrue(all(p.endswith("?days=7") for p in evidence_requests))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
