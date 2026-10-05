"""Hermetic checks for GTM collection and the no-change model gate."""

import json
import os
import runpy
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class GTMLearning(unittest.TestCase):
    def test_bounded_public_collection_and_partial_failure(self):
        collect = runpy.run_path(str(ROOT / "loops.d/gtm-research/collect.py"))[
            "collect"
        ]

        def fetch(url):
            if "GitNexus" not in url:
                raise OSError("offline")
            return {
                "items": [
                    {
                        "html_url": "https://github.com/abhigyanpatwari/GitNexus/issues/1",
                        "title": "missing result",
                        "body": "x@example.com " + "word " * 50,
                    }
                ]
            }

        result = collect(datetime(2026, 10, 5, tzinfo=timezone.utc), fetch)
        self.assertEqual(len(result["sources"]), 4)
        self.assertEqual(sum(s["status"] == "error" for s in result["sources"]), 3)
        record = result["sources"][0]["records"][0]
        self.assertNotIn("@", record["text"])
        self.assertLessEqual(len(record["text"].split()), 22)

    def test_precheck_only_runs_model_for_new_accepted_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bin").mkdir()
            (root / "output").mkdir()
            probe = root / "bin/probe"
            probe.write_text(
                '#!/bin/sh\nif [ "$1" = gtm-refresh ]; then\n'
                'echo \'{"status":"ok","synthesis":"unchanged"}\' > "$3"\n'
                'else cp "$LOOPS_ROOT/digest.json" "$3"; fi\n'
            )
            probe.chmod(0o700)
            env = {**os.environ, "LOOPS_ROOT": tmp, "OUT_DIR": str(root / "output")}
            for previous, expected_empty in (("same", True), ("old", False)):
                (root / "digest.json").write_text(
                    json.dumps(
                        {
                            "input_hash": "same",
                            "previous_assessment": {"input_hash": previous},
                        }
                    )
                )
                result = subprocess.run(
                    ["bash", str(ROOT / "loops.d/gtm-synthesis/precheck.sh")],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                self.assertEqual(not result.stdout.strip(), expected_empty)


if __name__ == "__main__":
    unittest.main()
