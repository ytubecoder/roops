"""Tests for bin/loopconf.py — §5.0 + §5 loop.conf parser."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN = REPO_ROOT / "bin" / "loopconf.py"

spec = importlib.util.spec_from_file_location("loopconf_mod", BIN)
loopconf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loopconf)


MINIMAL_VALID = """\
name=hello-loop
description="says hello"
type=agent
engine=codex
schedule=daily:07:30
"""


class TestLoopConfParsing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loops-conf-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, content, name="loop.conf"):
        path = Path(self.tmp) / name
        path.write_text(content)
        return str(path)

    def test_minimal_valid_parses_with_defaults(self):
        path = self.write(MINIMAL_VALID)
        conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])
        self.assertEqual(conf["name"], "hello-loop")
        self.assertEqual(conf["type"], "agent")
        self.assertEqual(conf["engine"], "codex")
        self.assertEqual(conf["timeout_s"], 900)
        self.assertEqual(conf["enabled"], True)
        self.assertEqual(conf["retention_days"], 30)
        self.assertEqual(conf["retry_transient"], 1)
        self.assertEqual(conf["perm_fs_write"], "report_only")
        self.assertEqual(conf["perm_network"], "none")
        self.assertEqual(conf["perm_local_exec"], "none")
        self.assertEqual(conf["perm_remote_mutation"], "none")

    def test_comments_and_blank_lines_ignored(self):
        content = MINIMAL_VALID + "\n# a comment\n\n   # indented comment\n"
        path = self.write(content)
        _conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])

    def test_inline_comment_after_whitespace(self):
        content = MINIMAL_VALID + 'notes="hi there" # trailing comment\n'
        path = self.write(content)
        conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])
        self.assertEqual(conf["notes"], "hi there")

    def test_bare_value_no_spaces_ok(self):
        content = MINIMAL_VALID + "notes=nospaceshere\n"
        path = self.write(content)
        conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])
        self.assertEqual(conf["notes"], "nospaceshere")

    def test_quoted_value_with_escape(self):
        content = MINIMAL_VALID + 'notes="has \\"quotes\\" inside"\n'
        path = self.write(content)
        conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])
        self.assertEqual(conf["notes"], 'has "quotes" inside')

    def test_quoted_value_with_spaces(self):
        content = MINIMAL_VALID + 'notes="hello world"\n'
        path = self.write(content)
        conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])
        self.assertEqual(conf["notes"], "hello world")

    def test_bare_value_with_spaces_is_error_or_truncated(self):
        # Bare (unquoted) values may not contain spaces per grammar.
        content = MINIMAL_VALID + "notes=hello world unquoted\n"
        path = self.write(content)
        _conf, errors = loopconf.parse(path)
        self.assertTrue(errors)

    def test_unknown_key_is_error(self):
        content = MINIMAL_VALID + "bogus_key=1\n"
        path = self.write(content)
        _conf, errors = loopconf.parse(path)
        self.assertTrue(any("bogus_key" in e for e in errors))

    def test_missing_required_key_is_error(self):
        content = "description=x\ntype=agent\nengine=codex\nschedule=manual\n"
        path = self.write(content)
        _conf, errors = loopconf.parse(path)
        self.assertTrue(any("name" in e for e in errors))

    def test_never_sourced_no_shell_expansion(self):
        content = MINIMAL_VALID + 'notes="$(echo pwned)"\n'
        path = self.write(content)
        conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])
        self.assertEqual(conf["notes"], "$(echo pwned)")

    def test_home_expansion_only_in_workdir(self):
        content = MINIMAL_VALID + "workdir=$HOME/somewhere\n"
        path = self.write(content)
        conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])
        self.assertEqual(conf["workdir"], os.path.expanduser("~/somewhere"))

    def test_tilde_expansion_in_workdir(self):
        content = MINIMAL_VALID + "workdir=~/somewhere\n"
        path = self.write(content)
        conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])
        self.assertEqual(conf["workdir"], os.path.expanduser("~/somewhere"))

    def test_home_not_expanded_outside_workdir(self):
        content = MINIMAL_VALID + 'notes="$HOME/somewhere"\n'
        path = self.write(content)
        conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])
        self.assertEqual(conf["notes"], "$HOME/somewhere")

    def test_name_regex_enforced(self):
        content = MINIMAL_VALID.replace("name=hello-loop", "name=Bad_Name!")
        path = self.write(content)
        _conf, errors = loopconf.parse(path)
        self.assertTrue(any("name" in e for e in errors))

    def test_type_enum_enforced(self):
        content = MINIMAL_VALID.replace("type=agent", "type=bogus")
        path = self.write(content)
        _conf, errors = loopconf.parse(path)
        self.assertTrue(any("type" in e for e in errors))

    def test_engine_enum_enforced(self):
        content = MINIMAL_VALID.replace("engine=codex", "engine=bogus")
        path = self.write(content)
        _conf, errors = loopconf.parse(path)
        self.assertTrue(any("engine" in e for e in errors))

    def test_timeout_s_range_low(self):
        content = MINIMAL_VALID + "timeout_s=10\n"
        path = self.write(content)
        _conf, errors = loopconf.parse(path)
        self.assertTrue(any("timeout_s" in e for e in errors))

    def test_timeout_s_range_high(self):
        content = MINIMAL_VALID + "timeout_s=99999\n"
        path = self.write(content)
        _conf, errors = loopconf.parse(path)
        self.assertTrue(any("timeout_s" in e for e in errors))

    def test_timeout_s_in_range_ok(self):
        content = MINIMAL_VALID + "timeout_s=120\n"
        path = self.write(content)
        conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])
        self.assertEqual(conf["timeout_s"], 120)

    def test_retry_transient_range(self):
        content = MINIMAL_VALID + "retry_transient=5\n"
        path = self.write(content)
        _conf, errors = loopconf.parse(path)
        self.assertTrue(any("retry_transient" in e for e in errors))

    def test_perm_fs_write_enum(self):
        content = MINIMAL_VALID + "perm_fs_write=bogus\n"
        path = self.write(content)
        _conf, errors = loopconf.parse(path)
        self.assertTrue(any("perm_fs_write" in e for e in errors))

    def test_enabled_bool_parses(self):
        content = MINIMAL_VALID + "enabled=false\n"
        path = self.write(content)
        conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])
        self.assertEqual(conf["enabled"], False)

    def test_enabled_bad_value_is_error(self):
        content = MINIMAL_VALID + "enabled=nope\n"
        path = self.write(content)
        _conf, errors = loopconf.parse(path)
        self.assertTrue(any("enabled" in e for e in errors))

    def test_key_regex_rejects_uppercase(self):
        content = MINIMAL_VALID + "BADKEY=1\n"
        path = self.write(content)
        _conf, errors = loopconf.parse(path)
        self.assertTrue(errors)

    def test_credential_env_comma_separated(self):
        content = MINIMAL_VALID + "credential_env=FOO_TOKEN,BAR_KEY\n"
        path = self.write(content)
        conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])
        self.assertEqual(conf["credential_env"], ["FOO_TOKEN", "BAR_KEY"])

    def test_exec_allowlist_required_when_perm_local_exec_allowlist(self):
        content = MINIMAL_VALID + "perm_local_exec=allowlist\n"
        path = self.write(content)
        _conf, errors = loopconf.parse(path)
        self.assertTrue(any("exec_allowlist" in e for e in errors))

    def test_exec_allowlist_present_ok(self):
        content = (
            MINIMAL_VALID
            + 'perm_local_exec=allowlist\nexec_allowlist="git status,gh run list"\n'
        )
        path = self.write(content)
        conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])
        self.assertEqual(conf["exec_allowlist"], ["git status", "gh run list"])

    def test_exec_allowlist_required_when_remote_mutation_allowlist(self):
        content = (
            MINIMAL_VALID
            + "perm_remote_mutation=allowlist\n"
            + 'remote_mutation_justification="needed for X"\n'
        )
        path = self.write(content)
        _conf, errors = loopconf.parse(path)
        self.assertTrue(any("exec_allowlist" in e for e in errors))

    def test_remote_mutation_justification_required(self):
        content = MINIMAL_VALID + "perm_remote_mutation=allowlist\n"
        path = self.write(content)
        _conf, errors = loopconf.parse(path)
        self.assertTrue(any("remote_mutation_justification" in e for e in errors))

    def test_remote_mutation_justification_present_ok(self):
        content = (
            MINIMAL_VALID
            + "perm_remote_mutation=allowlist\n"
            + 'remote_mutation_justification="needed for X"\n'
            + 'exec_allowlist="gh pr list"\n'
        )
        path = self.write(content)
        _conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])

    def test_nonexistent_file_returns_error(self):
        _conf, errors = loopconf.parse(str(Path(self.tmp) / "missing.conf"))
        self.assertTrue(errors)

    def test_omitted_workdir_defaults_to_loops_root_env(self):
        # Omitting workdir must resolve to the real loops root, not the
        # literal placeholder string "$LOOPS_ROOT". LOOPS_ROOT env wins.
        fake_root = os.path.join(self.tmp, "fake-loops-root")
        os.makedirs(fake_root, exist_ok=True)
        old = os.environ.get("LOOPS_ROOT")
        os.environ["LOOPS_ROOT"] = fake_root
        self.addCleanup(
            lambda: (
                os.environ.pop("LOOPS_ROOT", None)
                if old is None
                else os.environ.__setitem__("LOOPS_ROOT", old)
            )
        )
        path = self.write(MINIMAL_VALID)
        conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])
        self.assertNotEqual(conf["workdir"], "$LOOPS_ROOT")
        self.assertEqual(conf["workdir"], fake_root)

    def test_omitted_workdir_defaults_to_loops_root_no_env(self):
        # Without LOOPS_ROOT set, the fallback is $HOME/projects/loops
        # (per _loops_root()), never the literal placeholder string.
        old = os.environ.pop("LOOPS_ROOT", None)
        self.addCleanup(
            lambda: None if old is None else os.environ.__setitem__("LOOPS_ROOT", old)
        )
        path = self.write(MINIMAL_VALID)
        conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])
        self.assertNotEqual(conf["workdir"], "$LOOPS_ROOT")
        self.assertEqual(conf["workdir"], os.path.expanduser("~/projects/loops"))

    def test_explicit_workdir_unchanged_when_loops_root_env_set(self):
        # An explicitly-set workdir must NOT be overridden by LOOPS_ROOT —
        # only the omitted-workdir fallback consults it.
        old = os.environ.get("LOOPS_ROOT")
        os.environ["LOOPS_ROOT"] = os.path.join(self.tmp, "unrelated-root")
        self.addCleanup(
            lambda: (
                os.environ.pop("LOOPS_ROOT", None)
                if old is None
                else os.environ.__setitem__("LOOPS_ROOT", old)
            )
        )
        content = MINIMAL_VALID + "workdir=/explicit/path\n"
        path = self.write(content)
        conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])
        self.assertEqual(conf["workdir"], "/explicit/path")

    def test_explicit_workdir_home_expansion_still_works(self):
        content = MINIMAL_VALID + "workdir=$HOME/explicit-somewhere\n"
        path = self.write(content)
        conf, errors = loopconf.parse(path)
        self.assertEqual(errors, [])
        self.assertEqual(conf["workdir"], os.path.expanduser("~/explicit-somewhere"))


class TestLoopConfCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loops-conf-cli-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = Path(self.tmp) / "loop.conf"
        self.path.write_text(MINIMAL_VALID)

    def run_cli(self, args):
        return subprocess.run(
            [sys.executable, str(BIN)] + args,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_parse_json_exit_0(self):
        proc = self.run_cli(["parse", "--file", str(self.path), "--json"])
        self.assertEqual(proc.returncode, 0)
        import json

        data = json.loads(proc.stdout)
        self.assertEqual(data["conf"]["name"], "hello-loop")
        self.assertEqual(data["errors"], [])

    def test_parse_json_errors_exit_1(self):
        bad = Path(self.tmp) / "bad.conf"
        bad.write_text("description=x\n")
        proc = self.run_cli(["parse", "--file", str(bad), "--json"])
        self.assertEqual(proc.returncode, 1)

    def test_get_resolved_value(self):
        proc = self.run_cli(["get", "--file", str(self.path), "--key", "timeout_s"])
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "900")

    def test_get_unknown_key_exit_1_empty(self):
        proc = self.run_cli(["get", "--file", str(self.path), "--key", "nope"])
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout.strip(), "")

    def test_get_workdir_omitted_resolves_to_loops_root_env(self):
        # Regression: omitted workdir must not surface the literal
        # placeholder "$LOOPS_ROOT" — it must resolve to the real root,
        # honoring LOOPS_ROOT from the environment.
        fake_root = os.path.join(self.tmp, "fake-loops-root")
        os.makedirs(fake_root, exist_ok=True)
        env = dict(os.environ)
        env["LOOPS_ROOT"] = fake_root
        proc = subprocess.run(
            [
                sys.executable,
                str(BIN),
                "get",
                "--file",
                str(self.path),
                "--key",
                "workdir",
            ],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertNotEqual(proc.stdout.strip(), "$LOOPS_ROOT")
        self.assertEqual(proc.stdout.strip(), fake_root)


class TestTags(unittest.TestCase):
    def _parse_with(self, tags_line):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = os.path.join(d, "loop.conf")
        with open(p, "w") as f:
            f.write(MINIMAL_VALID)
            if tags_line is not None:
                f.write(tags_line + "\n")
        return loopconf.parse(p)

    def test_tags_absent_defaults_none(self):
        conf, errors = self._parse_with(None)
        self.assertEqual(errors, [])
        self.assertIsNone(conf["tags"])

    def test_tags_parse_dedupe_order(self):
        conf, errors = self._parse_with('tags="project:x, campaign:y, project:x"')
        self.assertEqual(errors, [])
        self.assertEqual(conf["tags"], ["project:x", "campaign:y"])

    def test_tags_invalid_entry_fails(self):
        _conf, errors = self._parse_with('tags="Project:X"')  # uppercase
        self.assertTrue(any("tags" in e for e in errors))

    def test_tags_empty_entry_fails(self):
        _conf, errors = self._parse_with('tags="a,,b"')
        self.assertTrue(any("tags" in e for e in errors))

    def test_tags_max_eight(self):
        nine = ",".join(f"t{i}" for i in range(9))
        _conf, errors = self._parse_with(f'tags="{nine}"')
        self.assertTrue(any("tags" in e for e in errors))


class TestOwner(unittest.TestCase):
    """B-17: owner is required-but-assumed — absence is never an error;
    a present-but-malformed value is a parse error like any field."""

    def _parse_with(self, owner_line):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = os.path.join(d, "loop.conf")
        with open(p, "w") as f:
            f.write(MINIMAL_VALID)
            if owner_line is not None:
                f.write(owner_line + "\n")
        return loopconf.parse(p)

    def test_owner_absent_is_none_no_error(self):
        conf, errors = self._parse_with(None)
        self.assertEqual(errors, [])
        self.assertIsNone(conf["owner"])

    def test_owner_valid_parses(self):
        conf, errors = self._parse_with("owner=maguyva-marketing")
        self.assertEqual(errors, [])
        self.assertEqual(conf["owner"], "maguyva-marketing")

    def test_owner_malformed_is_parse_error(self):
        _conf, errors = self._parse_with('owner="Maguyva Marketing!"')
        self.assertTrue(any("owner" in e for e in errors))

    def test_owner_uppercase_fails(self):
        _conf, errors = self._parse_with("owner=Loops")
        self.assertTrue(any("owner" in e for e in errors))

    def test_default_owner_constant(self):
        self.assertEqual(loopconf.DEFAULT_OWNER, "loops")

    def test_resolve_owner_explicit(self):
        self.assertEqual(
            loopconf.resolve_owner({"owner": "maguyva-marketing"}),
            ("maguyva-marketing", False),
        )

    def test_resolve_owner_absent_assumed(self):
        self.assertEqual(loopconf.resolve_owner({"owner": None}), ("loops", True))
        self.assertEqual(loopconf.resolve_owner({}), ("loops", True))


class TestLoadEnv(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loops-env-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write_env(self, content):
        path = Path(self.tmp) / ".env"
        path.write_text(content)
        return str(path)

    def test_load_env_missing_file_is_empty(self):
        self.assertEqual(loopconf.load_env(self.tmp), {})

    def test_load_env_grammar(self):
        self.write_env(
            "FOO=bar\n"
            "\n"
            'BAZ="hello world"\n'
            'QUOT="has \\"quotes\\""\n'
            "# comment line\n"
            "HOMEISH=$HOME/x\n"
            "TILDE=~/x\n"
            'SPACED="value with spaces" # trailing comment\n'
            "\n"
        )
        got = loopconf.load_env(self.tmp)
        home = os.path.expanduser("~")
        self.assertEqual(
            got,
            {
                "FOO": "bar",
                "BAZ": "hello world",
                "QUOT": 'has "quotes"',
                "HOMEISH": home + "/x",
                "TILDE": os.path.expanduser("~/x"),
                "SPACED": "value with spaces",
            },
        )

    def test_load_env_rejects_lowercase_key(self):
        self.write_env("gc_base=1\n")
        with self.assertRaises(loopconf.EnvFileError) as cm:
            loopconf.load_env(self.tmp)
        self.assertIn(":1:", str(cm.exception))

    def test_load_env_rejects_duplicate_and_malformed(self):
        self.write_env("FOO=a\nFOO=b\n")
        with self.assertRaises(loopconf.EnvFileError) as cm:
            loopconf.load_env(self.tmp)
        self.assertIn(":2:", str(cm.exception))

        self.write_env("bad line\n")
        with self.assertRaises(loopconf.EnvFileError) as cm:
            loopconf.load_env(self.tmp)
        self.assertIn(":1:", str(cm.exception))

        self.write_env('FOO="unterminated\n')
        with self.assertRaises(loopconf.EnvFileError) as cm:
            loopconf.load_env(self.tmp)
        self.assertIn(":1:", str(cm.exception))

    def test_load_env_max_keys(self):
        lines = [f"KEY{i:02d}=v\n" for i in range(65)]
        self.write_env("".join(lines))
        with self.assertRaises(loopconf.EnvFileError):
            loopconf.load_env(self.tmp)


class TestRequires(unittest.TestCase):
    def _parse_with(self, requires_line):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = os.path.join(d, "loop.conf")
        with open(p, "w") as f:
            f.write(MINIMAL_VALID)
            if requires_line is not None:
                f.write(requires_line + "\n")
        return loopconf.parse(p)

    def test_requires_parse_ok(self):
        conf, errors = self._parse_with(
            'requires="bin:gh, probe:av-scan, bin:gh"'
        )
        self.assertEqual(errors, [])
        self.assertEqual(conf["requires"], ["bin:gh", "probe:av-scan"])

    def test_requires_rejects_unknown_kind_and_os_value(self):
        _conf, errors = self._parse_with("requires=auth:gh")
        self.assertTrue(errors)
        self.assertTrue(any("auth:gh" in e for e in errors))

        _conf, errors = self._parse_with("requires=os:windows")
        self.assertTrue(errors)
        self.assertTrue(any("os:windows" in e for e in errors))

    def test_requires_max_16(self):
        seventeen = ",".join(f"bin:t{i}" for i in range(17))
        _conf, errors = self._parse_with(f'requires="{seventeen}"')
        self.assertTrue(any("16" in e or "max" in e for e in errors))

    def test_requires_absent_is_none(self):
        conf, errors = self._parse_with(None)
        self.assertEqual(errors, [])
        self.assertIsNone(conf["requires"])


class TestEnvCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loops-env-cli-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def run_cli(self, args):
        return subprocess.run(
            [sys.executable, str(BIN)] + args,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_env_cli_json_and_error_exit(self):
        env_path = Path(self.tmp) / ".env"
        env_path.write_text("GC_BASE=http://x\nOTHER=1\n")
        proc = self.run_cli(["env", "--root", self.tmp, "--json"])
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data, {"GC_BASE": "http://x", "OTHER": "1"})

        env_path.write_text("bad line\n")
        proc = self.run_cli(["env", "--root", self.tmp, "--json"])
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(proc.stderr.strip())


if __name__ == "__main__":
    unittest.main()
