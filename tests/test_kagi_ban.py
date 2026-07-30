"""Hermetic kagi-ban tests: precheck digest against a stub av binary, and the
renderer against the sanitized fixture. Never touches the real av app."""

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOOP = os.path.join(REPO, "loops.d", "kagi-ban")
FIXTURE = os.path.join(REPO, "pagekit", "reference", "fixture-scan.json")
KIT_CSS = os.path.join(REPO, "pagekit", "kit.css")

sys.path.insert(0, os.path.join(REPO, "bin"))
import page_envelope
import redact

_SPEC = importlib.util.spec_from_file_location(
    "kagi_ban_render_page", os.path.join(LOOP, "render_page.py")
)
render_page = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(render_page)


def make_stub_av(dirpath, scan_json_path):
    stub = os.path.join(dirpath, "av")
    with open(stub, "w") as f:
        f.write(
            "#!/usr/bin/env bash\n"
            'if [ "$1" = "--version" ]; then echo "av 0.0-stub"; exit 0; fi\n'
            f'cat "{scan_json_path}"\n'
        )
    os.chmod(stub, 0o755)
    return stub


class KagiBanPrecheckTests(unittest.TestCase):
    def run_precheck(self, root, scan_json_path):
        out_dir = os.path.join(root, "state", "runs", "test-run")
        os.makedirs(out_dir, exist_ok=True)
        stub = make_stub_av(root, scan_json_path)
        env = dict(
            os.environ,
            AV_BIN=stub,
            OUT_DIR=out_dir,
            LOOPS_ROOT=root,
            LOOP_NAME="kagi-ban",
            RUN_ID="test-run",
            WORKDIR=root,
        )
        proc = subprocess.run(
            ["bash", os.path.join(LOOP, "precheck.sh")],
            capture_output=True,
            text=True,
            env=env,
            cwd=LOOP,
            check=False,
        )
        return proc, out_dir

    def test_first_run_labels_everything_new(self):
        with tempfile.TemporaryDirectory() as root:
            proc, out_dir = self.run_precheck(root, FIXTURE)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("first_run=yes", proc.stdout)
            self.assertIn("NEW av:github-cli:", proc.stdout)
            self.assertNotIn("ONGOING", proc.stdout)
            self.assertTrue(
                os.path.isfile(
                    os.path.join(out_dir, "loop-data.commit", "scan-prev.json")
                )
            )

    def test_unchanged_world_is_all_ongoing_and_ids_stable(self):
        with tempfile.TemporaryDirectory() as root:
            proc1, out_dir = self.run_precheck(root, FIXTURE)
            committed = os.path.join(root, "state", "loop-data", "kagi-ban")
            os.makedirs(committed, exist_ok=True)
            os.replace(
                os.path.join(out_dir, "loop-data.commit", "scan-prev.json"),
                os.path.join(committed, "scan-prev.json"),
            )
            proc2, _ = self.run_precheck(root, FIXTURE)
            self.assertIn("new=0", proc2.stdout)
            self.assertIn("resolved=0", proc2.stdout)
            ids1 = sorted(
                line.split()[1]
                for line in proc1.stdout.splitlines()
                if line.startswith(("NEW ", "ONGOING "))
            )
            ids2 = sorted(
                line.split()[1]
                for line in proc2.stdout.splitlines()
                if line.startswith(("NEW ", "ONGOING "))
            )
            self.assertEqual(ids1, ids2)

    def test_unparseable_current_scan_fails_without_committing_baseline(self):
        with tempfile.TemporaryDirectory() as root:
            scan = os.path.join(root, "garbage-scan.json")
            with open(scan, "w") as f:
                f.write("{not json")
            proc, out_dir = self.run_precheck(root, scan)
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("unparseable", proc.stderr)
            self.assertFalse(
                os.path.exists(
                    os.path.join(out_dir, "loop-data.commit", "scan-prev.json")
                )
            )


class KagiBanRendererTests(unittest.TestCase):
    def render(self, scan_path, out):
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(LOOP, "render_page.py"),
                scan_path,
                "--loop",
                "kagi-ban",
                "--run-id",
                "test-run",
                "-o",
                out,
                "--host",
                "fixture",
                "--av-version",
                "0.0-stub",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def write_scan(self, tmp, mutate):
        """Fixture copy with `mutate(scan)` applied; returns its path."""
        with open(FIXTURE) as f:
            scan = json.load(f)
        mutate(scan)
        scan_path = os.path.join(tmp, "scan.json")
        with open(scan_path, "w") as f:
            json.dump(scan, f)
        return scan_path

    def test_renderer_passes_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "page.html")
            self.render(FIXTURE, out)
            errors = page_envelope.check_page(
                out, expect_run_id="test-run", expect_loop="kagi-ban"
            )
            self.assertEqual(errors, [])
            meta = page_envelope.read_meta(out)
            self.assertEqual(meta["page_class"], "snapshot")
            self.assertEqual(meta["totals"]["findings"], 5)

    def test_kv_phrased_explanations_survive_the_redaction_gate(self):
        """Real av scans phrase findings like 'plaintext access token: /path' —
        the path after the keyword trips bin/redact.py's generic KV pattern, and
        run 1 of the live gauntlet (2026-07-30) failed promotion on exactly this.
        The renderer must neutralize keyword-separator phrasing so a PATH never
        reads as a secret value. Real token VALUES must still fail the gate —
        asserted by test_real_token_values_still_fail_the_redaction_gate."""

        def mutate(scan):
            scan["findings"][0]["explanation"] = (
                "flyctl config file contains a plaintext access token: "
                "/Users/taro/.fly/config.yml"
            )
            scan["findings"][1]["solution"] = (
                "Move the password: /Users/taro/.pgpass entry into the keychain."
            )
            # Underscore compound: redact.py's lookbehind DOES fire here (`_` is
            # not a boundary char for it), so the renderer must neutralize it too.
            scan["findings"][2]["explanation"] = (
                "GITHUB_TOKEN=/Users/taro/.zshrc exports a credential."
            )
            # Hyphenated compound: redact.py leaves it alone, so the renderer
            # must NOT damage it (av's own source names read like this).
            scan["findings"][3]["explanation"] = (
                "av:gh-cli-hosts-token: /Users/taro/.config/gh/hosts.yml"
            )

        with tempfile.TemporaryDirectory() as tmp:
            scan_path = self.write_scan(tmp, mutate)
            out = os.path.join(tmp, "page.html")
            self.render(scan_path, out)
            errors = page_envelope.check_page(
                out, expect_run_id="test-run", expect_loop="kagi-ban"
            )
            self.assertEqual(errors, [])
            with open(out) as f:
                text = f.read()
            # Neutralized: separator became an em dash, prose otherwise intact.
            self.assertNotIn("access token: /Users", text)
            self.assertIn("access token — /Users/taro/.fly/config.yml", text)
            self.assertNotIn("GITHUB_TOKEN=/Users", text)
            self.assertIn("GITHUB_TOKEN — /Users/taro/.zshrc", text)
            self.assertIn("password — /Users/taro/.pgpass", text)
            # Untouched: redact.py's lookbehind never fires on a hyphenated
            # compound, so neutralizing it would be pure prose damage.
            self.assertIn("gh-cli-hosts-token: /Users/taro/.config/gh/hosts.yml", text)

    def test_real_token_values_still_fail_the_redaction_gate(self):
        """Neutralization rewrites KV SEPARATORS only — it must never launder a
        secret. A real token VALUE in finding prose reaches the page and the
        §4.4 redaction-clean check rejects the page (no promotion)."""

        def mutate(scan):
            scan["findings"][0]["explanation"] = (
                "flyctl config file contains the token: ghp_" + "a" * 30
            )

        with tempfile.TemporaryDirectory() as tmp:
            scan_path = self.write_scan(tmp, mutate)
            out = os.path.join(tmp, "page.html")
            self.render(scan_path, out)
            errors = page_envelope.check_page(
                out, expect_run_id="test-run", expect_loop="kagi-ban"
            )
            self.assertIn(
                "redaction-clean check failed: page contains secret-shaped content",
                errors,
            )


class KvKeywordSingleSourceTests(unittest.TestCase):
    """The renderer neutralizes prose that bin/redact.py's generic KV rule would
    redact. If the two keyword sets or boundaries drift, kagi-ban's page silently
    stops passing the promotion gate — visible only as a `stale` badge. These
    tests make that drift fail loudly instead."""

    # Path-shaped prose only (no real secret values), so a neutralized probe is
    # expected to be fully redaction-clean.
    KV_PROBES = (
        "plaintext access token: /Users/taro/.fly/config.yml",
        "GITHUB_TOKEN=/Users/taro/.zshrc",
        "DB_PASSWORD=/Users/taro/.pgpass",
        "av:gh-cli-hosts-token: /Users/taro/.config/gh/hosts.yml",
        "an authtoken: /Users/taro/.ngrok2/ngrok.yml",
        "Api-Key: /Users/taro/.netrc",
        "Authorization: Bearer /Users/taro/.config/token.txt",
        "no keyword in this sentence at all",
    )

    def test_renderer_pattern_is_built_from_redact_exports(self):
        pattern = render_page._KV_PHRASE_RE.pattern
        for keyword in redact.KV_KEYWORDS:
            self.assertIn(
                keyword, pattern, msg=f"keyword {keyword!r} not single-sourced"
            )
        self.assertIn(redact.KV_KEY_BOUNDARY, pattern)
        self.assertIn(redact.KV_SEPARATOR, pattern)

    def test_neutralizer_matches_redact_exactly_on_boundary_probes(self):
        for probe in self.KV_PROBES:
            neutralized = render_page.neutralize_kv_phrases([{"explanation": probe}])[
                0
            ]["explanation"]
            self.assertEqual(
                neutralized != probe,
                redact.redact(probe) != probe,
                msg=f"neutralizer/redact boundary disagreement on {probe!r}",
            )
            self.assertEqual(
                redact.redact(neutralized),
                neutralized,
                msg=f"neutralized prose still trips redact: {probe!r}",
            )


class PagekitParityTests(unittest.TestCase):
    """pagekit/kit.css is the canonical kit; the renderer inlines a verbatim copy
    (pages are self-contained and must not read $PAGEKIT at render time). Nothing
    else binds the two, so this test is the only thing preventing drift."""

    def test_inlined_style_block_matches_pagekit_kit_css(self):
        with open(KIT_CSS) as f:
            kit = f.read()
        # kit.css carries a leading header comment the inlined copy omits.
        body = re.sub(r"\A/\*.*?\*/\s*", "", kit, flags=re.DOTALL).rstrip("\n")
        match = re.search(
            r"<style>\n(.*?)\n</style>", render_page.PAGE.template, re.DOTALL
        )
        self.assertIsNotNone(match, "renderer template has no <style> block")
        self.assertEqual(
            match.group(1).rstrip("\n"),
            body,
            "pagekit/kit.css and the renderer's inlined <style> have drifted",
        )


if __name__ == "__main__":
    unittest.main()
