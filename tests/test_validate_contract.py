"""Tests for bin/validate_contract.py — §9.2 stdlib-only contract validator."""
import json
import subprocess
import sys
import tempfile
import shutil
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN = REPO_ROOT / "bin" / "validate_contract.py"
SCHEMA = REPO_ROOT / "contract" / "contract.schema.json"

VALID_CONTRACT = {
    "schema_version": 1,
    "run_id": "20260722T140311Z-hello-loop-a1b2c3",
    "status": "ok",
    "status_reason": "clean",
    "headline": "all good",
    "report_markdown": "# report\nall good",
    "metrics": json.dumps({"repos": {"dirty": 2}}),
    "findings": [
        {
            "finding_id": "cookingapp:no-remote",
            "title": "cookingapp has no remote",
            "severity": "warn",
            "detail": "details here",
        }
    ],
}


class TestValidateContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loops-validate-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write_contract(self, obj, name="contract.json"):
        path = Path(self.tmp) / name
        path.write_text(json.dumps(obj))
        return str(path)

    def run_cli(self, file_path, expect_run_id=None):
        args = [sys.executable, str(BIN), "--schema", str(SCHEMA), "--file", file_path]
        if expect_run_id is not None:
            args += ["--expect-run-id", expect_run_id]
        return subprocess.run(args, capture_output=True, text=True)

    def test_valid_contract_exit_0(self):
        path = self.write_contract(VALID_CONTRACT)
        proc = self.run_cli(path)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)

    def test_valid_contract_empty_findings_ok(self):
        obj = dict(VALID_CONTRACT)
        obj["findings"] = []
        path = self.write_contract(obj)
        proc = self.run_cli(path)
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)

    def test_missing_required_field_exit_1(self):
        obj = dict(VALID_CONTRACT)
        del obj["headline"]
        path = self.write_contract(obj)
        proc = self.run_cli(path)
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(proc.stderr.strip())

    def test_additional_property_rejected(self):
        obj = dict(VALID_CONTRACT)
        obj["extra_field"] = "nope"
        path = self.write_contract(obj)
        proc = self.run_cli(path)
        self.assertEqual(proc.returncode, 1)

    def test_bad_status_enum_rejected(self):
        obj = dict(VALID_CONTRACT)
        obj["status"] = "danger"
        path = self.write_contract(obj)
        proc = self.run_cli(path)
        self.assertEqual(proc.returncode, 1)

    def test_bad_schema_version_rejected(self):
        obj = dict(VALID_CONTRACT)
        obj["schema_version"] = 2
        path = self.write_contract(obj)
        proc = self.run_cli(path)
        self.assertEqual(proc.returncode, 1)

    def test_wrong_type_rejected(self):
        obj = dict(VALID_CONTRACT)
        obj["headline"] = 12345
        path = self.write_contract(obj)
        proc = self.run_cli(path)
        self.assertEqual(proc.returncode, 1)

    def test_metrics_not_a_string_rejected(self):
        obj = dict(VALID_CONTRACT)
        obj["metrics"] = {"repos": {"dirty": 2}}
        path = self.write_contract(obj)
        proc = self.run_cli(path)
        self.assertEqual(proc.returncode, 1)

    def test_metrics_string_not_valid_json_rejected(self):
        obj = dict(VALID_CONTRACT)
        obj["metrics"] = "{not valid json"
        path = self.write_contract(obj)
        proc = self.run_cli(path)
        self.assertEqual(proc.returncode, 1)

    def test_metrics_string_valid_json_but_not_object_rejected(self):
        obj = dict(VALID_CONTRACT)
        obj["metrics"] = json.dumps([1, 2, 3])
        path = self.write_contract(obj)
        proc = self.run_cli(path)
        self.assertEqual(proc.returncode, 1)

    def test_finding_missing_field_rejected(self):
        obj = dict(VALID_CONTRACT)
        obj["findings"] = [{"finding_id": "x", "title": "y", "severity": "info"}]
        path = self.write_contract(obj)
        proc = self.run_cli(path)
        self.assertEqual(proc.returncode, 1)

    def test_finding_bad_severity_rejected(self):
        obj = dict(VALID_CONTRACT)
        obj["findings"] = [
            {"finding_id": "x", "title": "y", "severity": "critical", "detail": "z"}
        ]
        path = self.write_contract(obj)
        proc = self.run_cli(path)
        self.assertEqual(proc.returncode, 1)

    def test_finding_additional_property_rejected(self):
        obj = dict(VALID_CONTRACT)
        obj["findings"] = [
            {
                "finding_id": "x",
                "title": "y",
                "severity": "info",
                "detail": "z",
                "extra": "nope",
            }
        ]
        path = self.write_contract(obj)
        proc = self.run_cli(path)
        self.assertEqual(proc.returncode, 1)

    def test_run_id_mismatch_rejected(self):
        path = self.write_contract(VALID_CONTRACT)
        proc = self.run_cli(path, expect_run_id="some-other-run-id")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("run_id", proc.stderr)

    def test_run_id_match_ok(self):
        path = self.write_contract(VALID_CONTRACT)
        proc = self.run_cli(path, expect_run_id=VALID_CONTRACT["run_id"])
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)

    def test_missing_file_usage_error(self):
        proc = self.run_cli(str(Path(self.tmp) / "nope.json"))
        self.assertEqual(proc.returncode, 2)

    def test_not_json_file_invalid(self):
        path = Path(self.tmp) / "notjson.json"
        path.write_text("not json at all")
        proc = self.run_cli(str(path))
        self.assertEqual(proc.returncode, 1)

    def test_reasons_one_per_line_to_stderr(self):
        obj = dict(VALID_CONTRACT)
        del obj["headline"]
        del obj["status_reason"]
        path = self.write_contract(obj)
        proc = self.run_cli(path)
        self.assertEqual(proc.returncode, 1)
        lines = [l for l in proc.stderr.strip().splitlines() if l]
        self.assertGreaterEqual(len(lines), 2)


if __name__ == "__main__":
    unittest.main()
