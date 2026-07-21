"""Tests for bin/redact.py — §4.4 redaction patterns."""
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN = REPO_ROOT / "bin" / "redact.py"

spec = importlib.util.spec_from_file_location("redact_mod", BIN)
redact_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(redact_mod)
redact = redact_mod.redact


class TestRedactPatterns(unittest.TestCase):
    def test_github_token(self):
        text = "token is ghp_abcdefghijklmnopqrstuvwxyz1234"
        out = redact(text)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz1234", out)
        self.assertIn("«redacted:github-token»", out)

    def test_github_token_case_insensitive(self):
        text = "GHP_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234"
        out = redact(text)
        self.assertIn("«redacted:", out)
        self.assertNotIn("ABCDEFGHIJKLMNOPQRSTUVWXYZ1234", out)

    def test_openai_style_secret_key(self):
        text = "key=sk-ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
        out = redact(text)
        self.assertIn("«redacted:", out)
        self.assertNotIn("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef", out)

    def test_slack_token(self):
        text = "xoxb-1234567890-abcdefghij"
        out = redact(text)
        self.assertIn("«redacted:slack-token»", out)
        self.assertNotIn("1234567890-abcdefghij", out)

    def test_aws_key(self):
        text = "AWS key AKIAABCDEFGHIJKLMNOP in use"
        out = redact(text)
        self.assertIn("«redacted:aws-key»", out)
        self.assertNotIn("AKIAABCDEFGHIJKLMNOP", out)

    def test_private_key_block(self):
        text = (
            "before\n"
            "-----BEGIN RSA PRIVATE KEY-----\n"
            "MIIEpAIBAAKCAQEA1234567890abcdefg\n"
            "moredata==\n"
            "-----END RSA PRIVATE KEY-----\n"
            "after\n"
        )
        out = redact(text)
        self.assertIn("«redacted:private-key»", out)
        self.assertNotIn("MIIEpAIBAAKCAQEA1234567890abcdefg", out)
        self.assertIn("before", out)
        self.assertIn("after", out)

    def test_key_value_line_keeps_key_redacts_value(self):
        text = "password: hunter2value"
        out = redact(text)
        self.assertIn("password:", out)
        self.assertIn("«redacted:secret»", out)
        self.assertNotIn("hunter2value", out)

    def test_key_value_case_insensitive_and_variants(self):
        for line in [
            "API_KEY=abc123xyz",
            "Api-Key: abc123xyz",
            "SECRET=abc123xyz",
            "TOKEN=abc123xyz",
            "Authorization: abc123xyz",
        ]:
            out = redact(line)
            self.assertIn("«redacted:secret»", out, msg=line)
            self.assertNotIn("abc123xyz", out, msg=line)

    def test_does_not_touch_unrelated_text(self):
        text = "the quick brown fox jumps over the lazy dog"
        self.assertEqual(redact(text), text)

    def test_already_redacted_value_not_double_mangled(self):
        # A key=value line whose value is itself a token gets a single
        # redaction marker, not stacked/garbled markers.
        text = "TOKEN=ghp_abcdefghijklmnopqrstuvwxyz1234"
        out = redact(text)
        self.assertEqual(out.count("«redacted:"), 1)


class TestRedactCLI(unittest.TestCase):
    def test_stdin_to_stdout_filter(self):
        proc = subprocess.run(
            [sys.executable, str(BIN)],
            input="secret=abc123xyz\n",
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("«redacted:secret»", proc.stdout)
        self.assertNotIn("abc123xyz", proc.stdout)


if __name__ == "__main__":
    unittest.main()
