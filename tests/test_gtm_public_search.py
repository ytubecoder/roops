"""Hermetic public-search contract tests; never read real credentials or APIs."""

import contextlib
import io
import json
import os
import runpy
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "probes/gtm-public-search"
NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)
SECRET = "fixture-only-secret-never-print"


class PublicSearch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.config = self.home / ".codex/config.toml"
        self.config.parent.mkdir()
        self.config.write_text(
            '[mcp_servers.tavily.env]\nTAVILY_API_KEY = "' + SECRET + '"\n'
        )
        self.p = runpy.run_path(str(PROBE))
        self.c = runpy.run_path(str(ROOT / "loops.d/gtm-research/collect.py"))

    def collect(self, fetch):
        return self.p["collect"](NOW, fetch, self.config)

    def samples(self, source, key):
        host = (
            "www.reddit.com"
            if source["source_type"] == "public_reddit"
            else "developer.mozilla.org"
        )
        return {
            "results": [
                {
                    "url": f"https://{host}/discussion/1",
                    "title": "Context problems",
                    "content": "u/person x@example.com " + "word " * 40,
                }
            ]
            * 9
        }

    def test_exact_config_and_check_without_network(self):
        self.assertEqual(self.p["key_from_config"](self.config), SECRET)
        with (
            patch.dict(os.environ, {"HOME": str(self.home)}),
            patch(
                "urllib.request.OpenerDirector.open",
                side_effect=AssertionError("network"),
            ),
        ):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(self.p["main"](["--check"]), 0)
            self.assertNotIn(SECRET, out.getvalue())
        self.config.write_text('[unrelated]\nTAVILY_API_KEY = "' + SECRET + '"\n')
        result = self.collect(lambda *_: self.fail("network"))
        self.assertTrue(all(s["error"] == "config" for s in result["sources"]))
        self.assertNotIn(SECRET, json.dumps(result))

    def test_invalid_config_never_leaks_parse_error(self):
        self.config.write_text(SECRET + ' = "unterminated')
        with patch.dict(os.environ, {"HOME": str(self.home)}):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(self.p["main"](["--check"]), 1)
            self.assertNotIn(SECRET, out.getvalue())

    def test_fixed_bounded_requests(self):
        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            return io.BytesIO(b'{"results": []}')

        def fetch(source, key):
            return self.p["fetch_search"](source, key, opener)

        result = self.collect(fetch)
        self.assertEqual(len(calls), 4)
        self.assertTrue(
            all(s["status"] == "ok" and s["empty"] for s in result["sources"])
        )
        for i, (request, timeout) in enumerate(calls):
            body = json.loads(request.data)
            self.assertEqual(request.full_url, "https://api.tavily.com/search")
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(timeout, 20)
            self.assertEqual(body["max_results"], 5)
            self.assertEqual(body["search_depth"], "basic")
            self.assertEqual(body["start_date"], "2026-07-08")
            self.assertEqual(body["end_date"], "2026-10-06")
            self.assertFalse(body["auto_parameters"])
            self.assertFalse(body["include_answer"])
            self.assertFalse(body["include_raw_content"])
            self.assertEqual(
                body.get("include_domains") if i < 2 else body.get("exclude_domains"),
                ["reddit.com"] if i < 2 else ["reddit.com", "github.com"],
            )
        self.assertNotIn(SECRET, json.dumps(result))

    def test_response_size_and_redirect_bounds(self):
        source = self.p["source_spec"](*self.p["QUERIES"][0], NOW)
        with self.assertRaises(self.p["CollectionError"]):
            self.p["fetch_search"](
                source, SECRET, lambda *a, **k: io.BytesIO(b"x" * 1_000_001)
            )
        with self.assertRaises(self.p["CollectionError"]):
            self.p["NoRedirect"]().redirect_request(
                None, None, 302, "", {}, "https://other.example/"
            )

    def test_dedupe_counts_and_one_excerpt_budget(self):
        result = self.collect(self.samples)
        self.assertEqual([s["unique_count"] for s in result["sources"]], [1, 0, 1, 0])
        self.assertEqual(
            [s["duplicate_count"] for s in result["sources"]], [4, 5, 4, 5]
        )
        for s in result["sources"]:
            self.assertEqual(s["result_count"], 5)
            for r in s["records"]:
                self.assertLessEqual(len(r["text"].split()), 22)
                self.assertLessEqual(len(r["text"]), 300)
                self.assertNotIn("title", r)
                self.assertNotIn("@", r["text"])
                self.assertNotIn("u/person", r["text"])
                self.assertIsNone(r["published_at"])

    def test_public_url_validation(self):
        for url in [
            "http://reddit.com/x",
            "https://user:pass@reddit.com/x",
            "https://localhost/x",
            "https://127.0.0.1/x",
            "https://10.1.2.3/x",
            "https://[::1]/x",
            "https://machine.local/x",
            "https://a.ts.net/x",
            "https://reddit.com:8443/x",
            "https://reddit.com/x?api_key=secret",
            "https://reddit.com\\@evil.com/x",
            "https://reddit.com/x\n",
        ]:
            with self.subTest(url=url):
                self.assertFalse(self.p["safe_url"](url))
        self.assertTrue(
            self.p["safe_url"]("https://www.reddit.com/r/programming/comments/123")
        )

    def test_partial_empty_error_categories_and_secret_hygiene(self):
        replies = iter(
            [
                {"results": []},
                HTTPError("https://hidden/" + SECRET, 401, SECRET, {}, None),
                HTTPError("https://hidden/", 429, SECRET, {}, None),
                ValueError(SECRET),
            ]
        )

        def fetch(*_):
            reply = next(replies)
            if isinstance(reply, Exception):
                raise reply
            return reply

        result = self.collect(fetch)
        self.assertEqual(
            [s["status"] for s in result["sources"]],
            ["ok", "error", "error", "error"],
        )
        self.assertEqual(
            [s["error"] for s in result["sources"]],
            [None, "auth", "quota", "malformed"],
        )
        self.assertNotIn(SECRET, json.dumps(result))

    def test_provider_echo_redacted(self):
        result = self.collect(
            lambda *_: {
                "results": [
                    {"url": "https://www.reddit.com/r/test/1", "content": SECRET}
                ]
            }
        )
        self.assertNotIn(SECRET, json.dumps(result))

    def test_wrong_family_and_malformed_payload_rejected(self):
        result = self.collect(
            lambda *_: {"results": [{"url": "https://github.com/org/repo"}]}
        )
        self.assertTrue(all(s["status"] == "error" for s in result["sources"]))
        result = self.collect(lambda *_: {"unexpected": []})
        self.assertTrue(all(s["error"] == "malformed" for s in result["sources"]))

    def merged(self, public=None, error=None, github_error=False):
        path = self.home / "public.json"
        path.write_text(json.dumps(public or self.collect(self.samples)))

        def github(_):
            if github_error:
                raise OSError("offline")
            return {"items": []}

        return self.c["merge_public"](self.c["collect"](NOW, github), path, error)

    def test_merge_families_and_unique_ids(self):
        encoded, code = self.c["finalize"](self.merged())
        result = json.loads(encoded)
        self.assertEqual(code, 0)
        self.assertEqual(len(result["sources"]), 8)
        self.assertEqual(result["families"]["public_reddit"]["unique_count"], 1)
        self.assertEqual(result["families"]["public_reddit"]["duplicate_count"], 9)
        self.assertEqual(result["families"]["public_github"]["empty_queries"], 4)
        self.assertLessEqual(len(encoded.encode()), 65536)

    def test_merge_all_failed_vs_empty_success_and_transport(self):
        encoded, code = self.c["finalize"](
            self.merged(error="transport", github_error=True)
        )
        self.assertEqual(code, 1)
        self.assertTrue(
            all(s["status"] == "error" for s in json.loads(encoded)["sources"])
        )
        self.assertEqual(self.c["finalize"](self.merged(error="transport"))[1], 0)
        empty = self.collect(lambda *_: {"results": []})
        self.assertEqual(
            self.c["finalize"](self.merged(public=empty, github_error=True))[1], 0
        )

    def test_merge_tampered_identity_rejected(self):
        public = self.collect(self.samples)
        public["sources"][0]["records"][0]["id"] = "pub-forged"
        result = self.merged(public)
        self.assertTrue(all(s["error"] == "malformed" for s in result["sources"][4:]))

    def test_output_cap_fails_closed(self):
        result = self.merged()
        result["sources"][4]["records"][0]["text"] = "x" * 70000
        encoded, code = self.c["finalize"](result)
        self.assertLessEqual(len(encoded.encode()), 65536)
        self.assertEqual(code, 1)
        self.assertTrue(
            all(s["error"] == "output_limit" for s in json.loads(encoded)["sources"])
        )

    def test_deadline_is_bounded_and_nonsecret(self):
        globals_ = self.p["main"].__globals__

        def expire():
            raise self.p["DeadlineExpired"]()

        out = io.StringIO()
        with (
            patch.dict(globals_, {"collect": expire}),
            patch("signal.signal"),
            patch("signal.alarm") as alarm,
            contextlib.redirect_stdout(out),
        ):
            self.assertEqual(self.p["main"]([]), 0)
        self.assertEqual([c.args[0] for c in alarm.call_args_list], [105, 0])
        self.assertTrue(
            all(s["error"] == "timeout" for s in json.loads(out.getvalue())["sources"])
        )

    def test_merge_duplicate_across_queries_does_not_inflate(self):
        public = self.collect(self.samples)
        first, second = public["sources"][:2]
        second.update(records=first["records"], unique_count=1, duplicate_count=4)
        result = self.merged(public)
        self.assertEqual(result["sources"][5]["unique_count"], 0)
        self.assertEqual(result["sources"][5]["duplicate_count"], 5)

    def test_probe_header_and_permissions(self):
        lines = PROBE.read_text().splitlines()
        self.assertEqual(lines[1], "# probe: gtm-public-search")
        self.assertIn("# probe-timeout-s: 120", lines[:20])
        self.assertIn("# probe-writes: none", lines[:20])
        self.assertTrue(os.access(PROBE, os.X_OK))

    def test_sanitizer_and_public_ip(self):
        self.assertFalse(self.p["safe_url"]("https://8.8.8.8/a"))
        text = self.p["excerpt"]("<b>x&#64;example.com</b>\x00 u/person @person")
        self.assertNotIn("@", text)
        self.assertNotIn("\x00", text)
        self.assertNotIn("person", text)

    def test_precheck_merge_and_transport_failure(self):
        root = self.home / "checkout"
        (root / "bin").mkdir(parents=True)
        (root / "probes").mkdir()
        (root / "loops.d/gtm-research").mkdir(parents=True)
        shutil.copy(PROBE, root / "probes/gtm-public-search")
        shutil.copy(
            ROOT / "loops.d/gtm-research/collect.py",
            root / "loops.d/gtm-research/collect.py",
        )
        payload = self.collect(self.samples)
        (root / "fixture.json").write_text(json.dumps(payload))
        probe = root / "bin/probe"
        probe.write_text(
            '#!/bin/sh\nif [ "$PROBE_FAIL" = 1 ]; then exit 75; fi\ncp "$LOOPS_ROOT/fixture.json" "$3"\n'
        )
        probe.chmod(0o700)
        # Only subprocess GitHub transport is replaced; the actual collector CLI runs.
        (root / "sitecustomize.py").write_text(
            "import io, urllib.request\nurllib.request.urlopen = lambda *a, **k: io.BytesIO(b'{\"items\": []}')\n"
        )
        env = {
            **os.environ,
            "LOOPS_ROOT": str(root),
            "OUT_DIR": str(root / "out"),
            "PYTHONPATH": str(root),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        for fail in ("0", "1"):
            env["PROBE_FAIL"] = fail
            proc = subprocess.run(
                ["bash", str(ROOT / "loops.d/gtm-research/precheck.sh")],
                env=env,
                check=False,
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr.decode())
            self.assertLessEqual(len(proc.stdout), 65536)
            result = json.loads(proc.stdout)
            self.assertEqual(len(result["sources"]), 8)
            self.assertEqual(
                result, json.loads((root / "out/inputs/research.json").read_text())
            )
            if fail == "1":
                self.assertTrue(
                    all(s["error"] == "transport" for s in result["sources"][4:])
                )
            else:
                self.assertEqual(result["families"]["public_web"]["unique_count"], 1)


if __name__ == "__main__":
    unittest.main()
