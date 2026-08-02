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
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import page_envelope
import redact
from html_selfcontained import assert_self_contained

_SPEC = importlib.util.spec_from_file_location(
    "kagi_ban_render_page", os.path.join(LOOP, "render_page.py")
)
render_page = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(render_page)


def run_renderer(scan_path, out, pagekit=None):
    """Invoke render_page.py as the runner does. `pagekit` overrides $PAGEKIT so
    a test can point at a throwaway kit without touching the real one."""
    env = dict(os.environ)
    if pagekit is None:
        env.pop("PAGEKIT", None)
    else:
        env["PAGEKIT"] = pagekit
    return subprocess.run(
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
        env=env,
    )


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
        proc = run_renderer(scan_path, out)
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


class PagekitSourcingTests(unittest.TestCase):
    """pagekit/kit.css is THE kit: read at render time and inlined, so there is
    exactly one copy of the report-page CSS. These tests fail if someone
    re-inlines a private copy (drift returns) or breaks the $PAGEKIT plumbing."""

    def kit_body(self):
        with open(KIT_CSS) as f:
            kit = f.read()
        # The header comment is aimed at maintainers and is stripped before inlining.
        return re.sub(r"\A/\*.*?\*/\s*", "", kit, flags=re.DOTALL).rstrip("\n")

    def test_template_defers_to_pagekit_rather_than_inlining_css(self):
        match = re.search(
            r"<style>\n(.*?)\n</style>", render_page.PAGE.template, re.DOTALL
        )
        self.assertIsNotNone(match, "renderer template has no <style> block")
        self.assertEqual(
            match.group(1).strip(),
            "$kit_css",
            "the <style> block must defer to $PAGEKIT/kit.css, not inline a copy",
        )

    def test_load_kit_css_strips_only_the_header_comment(self):
        loaded = render_page.load_kit_css()
        self.assertEqual(loaded, self.kit_body())
        self.assertFalse(
            loaded.lstrip().startswith("/*"),
            "kit.css header comment leaked into the inlined body",
        )
        self.assertIn(":root{", loaded)

    def test_rendered_page_inlines_the_real_kit_body(self):
        with tempfile.TemporaryDirectory() as root:
            out = os.path.join(root, "page.html")
            proc = run_renderer(FIXTURE, out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out) as f:
                html_text = f.read()
        self.assertIn(self.kit_body(), html_text)
        # Self-containment: inlined, never <link>ed to a stylesheet.
        self.assertNotIn('rel="stylesheet"', html_text)

    def test_edit_to_kit_css_reaches_the_page(self):
        """The whole point of reading it: a kit edit restyles pages with no
        renderer change. Uses a throwaway $PAGEKIT so the real kit is untouched."""
        marker = ".roops-kit-sourcing-probe{outline:1px solid #abcdef}"
        with tempfile.TemporaryDirectory() as root:
            kit_dir = os.path.join(root, "pagekit")
            os.makedirs(kit_dir)
            with open(os.path.join(kit_dir, "kit.css"), "w") as f:
                f.write("/* throwaway header */\n" + marker + "\n")
            # toggle.js is also required under $PAGEKIT (WP3); seed a stub so
            # this test isolates the kit.css read path.
            with open(os.path.join(kit_dir, "toggle.js"), "w") as f:
                f.write("/* throwaway toggle */\n")
            out = os.path.join(root, "page.html")
            proc = run_renderer(FIXTURE, out, pagekit=kit_dir)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out) as f:
                html_text = f.read()
        self.assertIn(marker, html_text)
        self.assertNotIn("throwaway header", html_text)

    def test_missing_kit_css_fails_the_render_loudly(self):
        """A missing committed file is a broken checkout. Fail with the path
        named in page-render.log rather than promote an unstyled page."""
        with tempfile.TemporaryDirectory() as root:
            empty_kit = os.path.join(root, "pagekit")
            os.makedirs(empty_kit)
            out = os.path.join(root, "page.html")
            proc = run_renderer(FIXTURE, out, pagekit=empty_kit)
        self.assertNotEqual(proc.returncode, 0, "missing kit.css must fail the render")
        self.assertIn("kit.css", proc.stderr)
        self.assertFalse(os.path.exists(out), "no page may be written on failure")


class PageSelfContainmentTests(unittest.TestCase):
    """The report page is served over the tailnet and opened offline, so it must
    fetch nothing on load. av findings carry real https:// docs URLs and free
    text, so this is asserted on references, not on the presence of a scheme."""

    def test_rendered_page_fetches_nothing_on_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "page.html")
            proc = run_renderer(FIXTURE, out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out) as f:
                html_text = f.read()
        assert_self_contained(self, html_text, "kagi-ban report page")
        # The docs links are real and must survive — they are navigation, not
        # subresources. Their presence is exactly why the substring rule failed.
        self.assertIn("https://", html_text)

    def test_hostile_finding_text_cannot_inject_a_subresource(self):
        """Finding text is av's, not ours. Even if a scan carried markup, the
        renderer escapes it, so it can never become a fetching element."""
        with tempfile.TemporaryDirectory() as tmp:
            with open(FIXTURE) as f:
                scan = json.load(f)
            scan["findings"][0]["explanation"] = (
                '<img src="http://tracker.example/pixel.gif"> and '
                '<script src="//cdn.example/x.js"></script> plus a bare '
                "http://127.0.0.1:9/dead probe result"
            )
            scan["findings"][1]["solution"] = (
                "<style>@import url('https://fonts.example/f.css');</style>"
            )
            scan_path = os.path.join(tmp, "scan.json")
            with open(scan_path, "w") as f:
                json.dump(scan, f)
            out = os.path.join(tmp, "page.html")
            proc = run_renderer(scan_path, out)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            with open(out) as f:
                html_text = f.read()
        assert_self_contained(self, html_text, "page with hostile finding text")
        # Escaped, not stripped: the operator still sees what av reported.
        self.assertIn("tracker.example/pixel.gif", html_text)
        self.assertNotIn('<img src="http://tracker.example', html_text)


if __name__ == "__main__":
    unittest.main()
