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
REGISTRY = """schema_version: 1
updated: 2026-10-15
competitors:
  - name: Repo Tool
    slug: repo-tool
    status: tracked
    repos: [example/repo-tool]
    voice_hints: ["repo tool mcp"]
  - name: Hosted Tool
    slug: hosted-tool
    status: tracked
    repos: []
    voice_hints: ["hosted tool indexing", "hostedtool"]
  - name: Watched Only
    slug: watched
    status: watch
    repos: [example/watched]
    voice_hints: []
rejected:
  - { name: other, repos: [x/y], reason: "not a competitor" }
"""
VOICE_DOMAINS = ["reddit.com", "news.ycombinator.com", "forum.cursor.com", "github.com"]


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
        # Hermetic registry path: missing unless a test writes the fixture.
        self.registry = self.home / "registry.yaml"

    def write_registry(self, text=REGISTRY):
        self.registry.write_text(text)

    def collect(self, fetch):
        return self.p["collect"](NOW, fetch, self.config, self.registry)

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
        repo = self.home / "projects/maguyva-marketing"
        registry = repo / "gc-actions/specs/inputs/competitor-registry.yaml"
        registry.parent.mkdir(parents=True)
        registry.write_text(REGISTRY)
        with (
            patch.dict(os.environ, {"HOME": str(self.home), "MAGUYVA_REPO": ""}),
            patch(
                "urllib.request.OpenerDirector.open",
                side_effect=AssertionError("network"),
            ),
        ):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(self.p["main"](["--check"]), 0)
            self.assertNotIn(SECRET, out.getvalue())
            registry.unlink()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(self.p["main"](["--check"]), 1)
            self.assertIn("competitor registry", out.getvalue())
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

    def test_output_cap_trims_explicitly_then_fails_closed(self):
        result = self.merged()
        result["sources"][4]["records"][0]["text"] = "x" * 70000
        encoded, code = self.c["finalize"](result)
        self.assertLessEqual(len(encoded.encode()), 65536)
        self.assertEqual(code, 0)
        trimmed = json.loads(encoded)["sources"][4]
        self.assertEqual(trimmed["status"], "ok")
        self.assertTrue(trimmed["truncated"])
        self.assertEqual(trimmed["truncated_count"], 1)
        self.assertEqual(trimmed["unique_count"], 0)
        self.assertEqual(trimmed["result_count"], 5)
        self.assertEqual(
            json.loads(encoded)["families"]["public_reddit"]["truncated_count"], 1
        )
        result = self.merged()
        result["registry"]["competitors"] = [{"name": "x" * 70000, "slug": "x", "repos": [], "voice_hints": []}]
        encoded, code = self.c["finalize"](result)
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
        self.assertEqual([c.args[0] for c in alarm.call_args_list], [165, 0])
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
        self.assertIn("# probe-timeout-s: 180", lines[:20])
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

    def voice_samples(self, source, key):
        if source["source_type"] != "public_voice":
            return {"results": []}
        tag = source["competitor"]
        return {
            "results": [
                {"url": f"https://news.ycombinator.com/item?id={tag}", "content": "Fast but costly " + "w " * 40},
                {"url": f"https://www.reddit.com/r/x/{tag}/", "title": "Indexing stale"},
                {"url": "https://developer.mozilla.org/off-domain", "content": "rejected"},
                {"url": f"https://news.ycombinator.com/item?id={tag}", "content": "dup"},
            ]
        }

    def test_registry_voice_requests_are_bounded_and_tagged(self):
        self.write_registry()
        calls = []

        def opener(request, timeout):
            calls.append(json.loads(request.data))
            return io.BytesIO(b'{"results": []}')

        result = self.collect(lambda s, k: self.p["fetch_search"](s, k, opener))
        self.assertEqual(result["registry"]["status"], "ok")
        self.assertEqual(result["registry"]["updated"], "2026-10-15")
        self.assertEqual(
            [c["slug"] for c in result["registry"]["competitors"]],
            ["repo-tool", "hosted-tool"],
        )
        self.assertEqual(len(calls), 6)
        self.assertEqual(
            [c["query"] for c in calls[4:]], ["hosted tool indexing", "hostedtool"]
        )
        for body in calls[4:]:
            self.assertEqual(body["include_domains"], VOICE_DOMAINS)
            self.assertEqual(body["max_results"], 5)
            self.assertEqual(body["start_date"], "2026-07-08")
        self.assertEqual(result["voice_plan"], {"planned": 2, "run": 2, "truncated": False, "domains": VOICE_DOMAINS})
        self.assertEqual(
            [s["competitor"] for s in result["voice_sources"]], ["hosted-tool"] * 2
        )
        self.assertTrue(all(s["competitor"] is None for s in result["sources"]))
        self.assertNotIn(SECRET, json.dumps(result))

    def test_voice_domain_filter_and_per_competitor_dedupe(self):
        self.write_registry()
        result = self.collect(self.voice_samples)
        first, second = result["voice_sources"]
        self.assertEqual(first["unique_count"], 2)
        self.assertEqual(first["rejected_count"], 1)
        self.assertEqual(first["duplicate_count"], 1)
        self.assertEqual(first["result_count"], 4)
        # The second hint for the same competitor repeats the same URLs: duplicates.
        self.assertEqual(second["unique_count"], 0)
        self.assertEqual(second["duplicate_count"], 3)
        for record in first["records"]:
            self.assertLessEqual(len(record["text"].split()), 22)

    def test_registry_schema_2_is_accepted(self):
        self.write_registry(
            REGISTRY.replace("schema_version: 1", "schema_version: 2").replace(
                "    voice_hints:", "    why: profile text\n    brand_terms: [x]\n    voice_hints:", 1
            )
        )
        rows = self.p["load_registry"](self.registry)["competitors"]
        self.assertTrue(rows)
        self.assertNotIn("why", rows[0])

    def test_malformed_registry_is_a_config_error_without_voice_searches(self):
        self.write_registry("competitors: [\n")
        calls = []
        result = self.collect(lambda s, k: calls.append(s) or {"results": []})
        self.assertEqual(result["registry"], {"status": "error", "error": "malformed", "updated": None, "competitors": []})
        self.assertEqual(result["voice_sources"], [])
        self.assertEqual(len(calls), 4)
        self.write_registry(REGISTRY.replace("slug: hosted-tool", "slug: Bad Slug"))
        self.assertEqual(self.collect(lambda *_: {"results": []})["registry"]["error"], "malformed")
        self.write_registry("schema_version: 1\ncompetitors: []\n")
        result = self.collect(lambda *_: {"results": []})
        self.assertEqual(result["registry"]["status"], "ok")
        self.assertEqual(result["voice_plan"]["planned"], 0)

    def test_deadline_keeps_earlier_sources(self):
        self.write_registry()
        count = {"n": 0}

        def fetch(source, key):
            count["n"] += 1
            if count["n"] == 3:
                raise self.p["DeadlineExpired"]()
            return {"results": []}

        result = self.collect(fetch)
        statuses = [s["status"] for s in result["sources"] + result["voice_sources"]]
        self.assertEqual(statuses, ["ok", "ok", "error", "error", "error", "error"])
        self.assertTrue(all(s["error"] == "timeout" for s in (result["sources"] + result["voice_sources"])[2:]))
        self.assertEqual(count["n"], 3)

    def merged_registry(self, mutate=None, github=None):
        self.write_registry()
        public = self.collect(self.voice_samples)
        if mutate:
            mutate(public)
        path = self.home / "public.json"
        path.write_text(json.dumps(public))
        fetched = []

        def fetch(url):
            fetched.append(url)
            if github:
                return github(url)
            return {"items": []}

        result = self.c["merge_public"](self.c["collect"](NOW, fetch), path, None, fetch)
        return result, fetched

    def test_merge_registry_adds_extra_repos_tags_and_competitor_index(self):
        def github(url):
            if "example%2Frepo-tool" in url or "example/repo-tool" in url:
                return {
                    "items": [
                        {
                            "html_url": "https://github.com/example/repo-tool/issues/7",
                            "title": "Index misses files",
                            "body": "somebody@example.com reports " + "w " * 40,
                            "created_at": "2026-10-01T00:00:00Z",
                        }
                    ]
                }
            return {"items": []}

        result, fetched = self.merged_registry(github=github)
        self.assertEqual(len(fetched), 5)
        self.assertEqual(len(result["sources"]), 9)
        extra = result["sources"][8]
        self.assertEqual(extra["competitor"], "repo-tool")
        self.assertEqual(extra["query"], "repo:example/repo-tool is:issue updated:>=2026-09-06")
        self.assertEqual(extra["unique_count"], 1)
        self.assertNotIn("@", extra["records"][0]["text"])
        self.assertEqual(result["github_plan"], {"fixed": 4, "planned": 5, "run": 5, "truncated": False})
        self.assertEqual([s["competitor"] for s in result["voice_sources"]], ["hosted-tool"] * 2)
        self.assertEqual(result["voice_sources"][0]["unique_count"], 2)
        encoded, code = self.c["finalize"](result)
        self.assertEqual(code, 0)
        out = json.loads(encoded)
        self.assertEqual(out["families"]["public_voice"]["unique_count"], 2)
        self.assertEqual(out["families"]["public_voice"]["queries"], 2)
        index = {c["slug"]: c for c in out["competitors"]}
        self.assertEqual(index["repo-tool"]["status"], "ok")
        self.assertEqual(index["repo-tool"]["record_ids"], [extra["records"][0]["id"]])
        self.assertEqual(index["hosted-tool"]["status"], "ok")
        self.assertEqual(index["hosted-tool"]["url_count"], 2)
        self.assertEqual(index["hosted-tool"]["queries"], 2)
        self.assertLessEqual(len(encoded.encode()), 65536)
        self.assertNotIn(SECRET, encoded)

    def test_merge_tampered_voice_block_keeps_positioning_sources(self):
        def forge(public):
            public["voice_sources"][0]["records"][0]["url"] = "https://developer.mozilla.org/forged"

        result, _ = self.merged_registry(mutate=forge)
        self.assertEqual(result["registry"]["status"], "error")
        self.assertEqual(result["voice_sources"], [])
        self.assertEqual(len(result["sources"]), 8)
        self.assertTrue(all(s["status"] == "ok" for s in result["sources"][4:]))
        self.assertEqual(self.c["finalize"](result)[1], 0)

        def wrong_domain_hint(public):
            public["voice_sources"][0]["query"] = "something else"

        result, _ = self.merged_registry(mutate=wrong_domain_hint)
        self.assertEqual(result["voice_sources"], [])

        def too_many(public):
            public["registry"]["competitors"].append({"name": "Dup", "slug": "hosted-tool", "status": "tracked", "repos": [], "voice_hints": []})

        result, _ = self.merged_registry(mutate=too_many)
        self.assertEqual(result["registry"]["competitors"], [])

    def test_voice_trimming_is_explicit_and_recounts_index(self):
        result, _ = self.merged_registry()
        for record in result["voice_sources"][0]["records"]:
            record["text"] = "y" * 40000
        encoded, code = self.c["finalize"](result)
        self.assertEqual(code, 0)
        out = json.loads(encoded)
        voice = out["voice_sources"][0]
        self.assertTrue(voice["truncated"])
        self.assertEqual(voice["truncated_count"] + voice["unique_count"], 2)
        self.assertEqual(out["families"]["public_voice"]["truncated_count"], voice["truncated_count"])
        index = {c["slug"]: c for c in out["competitors"]}
        self.assertEqual(index["hosted-tool"]["status"], "partial")
        self.assertEqual(len(index["hosted-tool"]["record_ids"]), voice["unique_count"])
        self.assertLessEqual(len(encoded.encode()), 65536)


if __name__ == "__main__":
    unittest.main()
