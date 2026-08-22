"""Tests for bin/requirements.py — host requirement checks."""

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
REQ_BIN = REPO_ROOT / "bin" / "requirements.py"
LOOPCONF_BIN = REPO_ROOT / "bin" / "loopconf.py"


def _load_module_from_path(path, modname):
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


requirements = _load_module_from_path(str(REQ_BIN), "_test_requirements")
loopconf = _load_module_from_path(str(LOOPCONF_BIN), "_test_req_loopconf")

MINIMAL = """\
name={name}
description="d"
type=agent
engine=codex
schedule=manual
"""


def _this_os():
    if sys.platform.startswith("darwin"):
        return "darwin"
    return "linux"


def _other_os():
    return "linux" if _this_os() == "darwin" else "darwin"


class RequirementsRoot:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="loops-req-")
        os.makedirs(os.path.join(self.root, "loops.d"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "bin"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "probes"), exist_ok=True)

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write_loop(self, name, extra_lines=None):
        d = os.path.join(self.root, "loops.d", name)
        os.makedirs(d, exist_ok=True)
        lines = MINIMAL.format(name=name)
        if extra_lines:
            lines += "\n".join(extra_lines) + "\n"
        with open(os.path.join(d, "loop.conf"), "w") as f:
            f.write(lines)
        return d

    def parse(self, name):
        path = os.path.join(self.root, "loops.d", name, "loop.conf")
        conf, errors = loopconf.parse(path)
        assert not errors, errors
        return conf


class TestRequirementsCheck(unittest.TestCase):
    def setUp(self):
        self.fx = RequirementsRoot()
        self.addCleanup(self.fx.cleanup)
        self._old_home = os.environ.get("HOME")
        self._old_path = os.environ.get("PATH")
        self._old_gc = os.environ.get("GC_BASE")
        self._old_probe_host = os.environ.get("LOOPS_PROBE_HOST")
        self._old_probe_key = os.environ.get("LOOPS_PROBE_KEY")
        self._old_fake_exit = os.environ.get("FAKE_PROBE_EXIT")

    def tearDown(self):
        self._restore("HOME", self._old_home)
        self._restore("PATH", self._old_path)
        self._restore("GC_BASE", self._old_gc)
        self._restore("LOOPS_PROBE_HOST", self._old_probe_host)
        self._restore("LOOPS_PROBE_KEY", self._old_probe_key)
        self._restore("FAKE_PROBE_EXIT", self._old_fake_exit)

    def _restore(self, key, value):
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    def _check(self, conf, **kwargs):
        live = kwargs.pop("live", False)
        return requirements.check(self.fx.root, conf, live=live, **kwargs)

    def test_os_kind(self):
        this = _this_os()
        other = _other_os()
        self.fx.write_loop("osloop", [f"requires=os:{this},os:{other}"])
        conf = self.fx.parse("osloop")
        rows = self._check(conf, live=False)
        by_item = {item: (ok, detail) for item, ok, detail in rows}
        self.assertTrue(by_item[f"os:{this}"][0])
        self.assertFalse(by_item[f"os:{other}"][0])
        self.assertIn("host is", by_item[f"os:{other}"][1])

    def test_bin_kind_uses_unit_path_not_callers_path(self):
        tmp_home = tempfile.mkdtemp(prefix="req-home-")
        self.addCleanup(shutil.rmtree, tmp_home, ignore_errors=True)
        os.environ["HOME"] = tmp_home

        caller_bin = tempfile.mkdtemp(prefix="req-caller-bin-")
        self.addCleanup(shutil.rmtree, caller_bin, ignore_errors=True)
        fake = os.path.join(caller_bin, "fake")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(fake, 0o755)
        os.environ["PATH"] = caller_bin + os.pathsep + (self._old_path or "")

        self.fx.write_loop("binloop", ["requires=bin:fake"])
        conf = self.fx.parse("binloop")
        rows = self._check(conf, live=False)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0][1], msg=rows)
        self.assertIn("not on unit PATH", rows[0][2])

        local_bin = os.path.join(tmp_home, ".local", "bin")
        os.makedirs(local_bin, exist_ok=True)
        local_fake = os.path.join(local_bin, "fake")
        with open(local_fake, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(local_fake, 0o755)
        rows = self._check(conf, live=False)
        self.assertTrue(rows[0][1], msg=rows)

    def test_file_kind_expands_home_and_checks_readable(self):
        tmp_home = tempfile.mkdtemp(prefix="req-file-home-")
        self.addCleanup(shutil.rmtree, tmp_home, ignore_errors=True)
        os.environ["HOME"] = tmp_home
        self.fx.write_loop("fileloop", ["requires=file:~/x"])
        conf = self.fx.parse("fileloop")

        rows = self._check(conf, live=False)
        self.assertFalse(rows[0][1])
        self.assertEqual(rows[0][2], "missing")

        target = os.path.join(tmp_home, "x")
        with open(target, "w") as f:
            f.write("ok\n")
        os.chmod(target, 0o644)
        rows = self._check(conf, live=False)
        self.assertTrue(rows[0][1], msg=rows)

        if os.geteuid() == 0:
            self.skipTest("root bypasses mode 000 readability")
        os.chmod(target, 0o000)
        try:
            rows = self._check(conf, live=False)
            self.assertFalse(rows[0][1])
            self.assertEqual(rows[0][2], "not readable")
        finally:
            os.chmod(target, 0o644)

    def test_env_kind_reads_dotenv_only_when_unset(self):
        self.fx.write_loop("envloop", ["requires=env:GC_BASE"])
        conf = self.fx.parse("envloop")
        os.environ.pop("GC_BASE", None)
        with open(os.path.join(self.fx.root, ".env"), "w") as f:
            f.write("GC_BASE=a\n")

        rows = self._check(conf, live=False, env=None)
        self.assertTrue(rows[0][1], msg=rows)

        os.remove(os.path.join(self.fx.root, ".env"))
        rows = self._check(conf, live=False, env={})
        self.assertFalse(rows[0][1])
        self.assertEqual(rows[0][2], "unset or empty")

        with open(os.path.join(self.fx.root, ".env"), "w") as f:
            f.write("GC_BASE=a\n")
        rows = self._check(conf, live=False, env={"GC_BASE": "from-shell"})
        self.assertTrue(rows[0][1], msg=rows)
        rows = self._check(conf, live=False, env={"GC_BASE": ""})
        self.assertFalse(rows[0][1], msg="empty explicit env must win over .env")

    def test_probe_kind_live_calls_bin_probe_check(self):
        argv_log = os.path.join(self.fx.root, "probe-argv.log")
        probe = os.path.join(self.fx.root, "bin", "probe")
        with open(probe, "w") as f:
            f.write("#!/bin/bash\n")
            f.write(f"printf '%s\\n' \"$@\" > '{argv_log}'\n")
            f.write("exit \"${FAKE_PROBE_EXIT:-0}\"\n")
        os.chmod(probe, 0o755)

        self.fx.write_loop("probeloop", ["requires=probe:av-scan"])
        conf = self.fx.parse("probeloop")

        os.environ["FAKE_PROBE_EXIT"] = "0"
        rows = self._check(conf, live=True)
        self.assertTrue(rows[0][1], msg=rows)
        with open(argv_log) as f:
            argv = [line.rstrip("\n") for line in f]
        self.assertEqual(argv, ["--check", "av-scan"])

        os.environ["FAKE_PROBE_EXIT"] = "3"
        rows = self._check(conf, live=True)
        self.assertFalse(rows[0][1])
        self.assertIn("3", rows[0][2])

        os.remove(probe)
        rows = self._check(conf, live=True)
        self.assertFalse(rows[0][1])
        self.assertEqual(rows[0][2], "bin/probe missing")

    def test_probe_kind_config_only_never_spawns(self):
        canary = os.path.join(self.fx.root, "probe-canary")
        probe = os.path.join(self.fx.root, "bin", "probe")
        with open(probe, "w") as f:
            f.write("#!/bin/bash\n")
            f.write(f"echo invoked > '{canary}'\n")
            f.write("exit 0\n")
        os.chmod(probe, 0o755)

        self.fx.write_loop("probecfg", ["requires=probe:av-scan"])
        conf = self.fx.parse("probecfg")
        os.environ.pop("LOOPS_PROBE_HOST", None)
        os.environ.pop("LOOPS_PROBE_KEY", None)

        rows = self._check(conf, live=False, env={})
        self.assertFalse(rows[0][1])
        self.assertIn("not executable", rows[0][2])
        self.assertFalse(os.path.exists(canary))

        probe_script = os.path.join(self.fx.root, "probes", "av-scan")
        with open(probe_script, "w") as f:
            f.write("#!/bin/bash\nexit 0\n")
        os.chmod(probe_script, 0o755)
        rows = self._check(conf, live=False, env={})
        self.assertTrue(rows[0][1], msg=rows)
        self.assertFalse(os.path.exists(canary))

        os.chmod(probe_script, 0o644)
        rows = self._check(conf, live=False, env={})
        self.assertFalse(rows[0][1])
        self.assertFalse(os.path.exists(canary))

        rows = self._check(
            conf, live=False, env={"LOOPS_PROBE_HOST": "x"}
        )
        self.assertFalse(rows[0][1])
        self.assertIn("probe key missing", rows[0][2])
        self.assertFalse(os.path.exists(canary))

        key_path = os.path.join(self.fx.root, "loops-probe-key")
        with open(key_path, "w") as f:
            f.write("fake-key\n")
        os.chmod(key_path, 0o600)
        rows = self._check(
            conf,
            live=False,
            env={"LOOPS_PROBE_HOST": "x", "LOOPS_PROBE_KEY": key_path},
        )
        self.assertTrue(rows[0][1], msg=rows)
        self.assertFalse(os.path.exists(canary))


class TestRequirementsCLI(unittest.TestCase):
    def setUp(self):
        self.fx = RequirementsRoot()
        self.addCleanup(self.fx.cleanup)

    def run_cli(self, args):
        return subprocess.run(
            [sys.executable, str(REQ_BIN)] + args,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_cli_exit_codes_and_json(self):
        this = _this_os()
        self.fx.write_loop("okloop", [f"requires=os:{this}"])
        r = self.run_cli(
            [
                "check",
                "--root",
                self.fx.root,
                "--loop",
                "okloop",
                "--no-live",
                "--json",
            ]
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["loop"], "okloop")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["items"][0]["item"], f"os:{this}")
        self.assertTrue(payload["items"][0]["ok"])
        self.assertIn("detail", payload["items"][0])

        self.fx.write_loop(
            "badloop", ["requires=bin:definitely-not-a-binary"]
        )
        r = self.run_cli(
            [
                "check",
                "--root",
                self.fx.root,
                "--loop",
                "badloop",
                "--no-live",
                "--json",
            ]
        )
        self.assertEqual(r.returncode, 1, msg=r.stdout + r.stderr)
        payload = json.loads(r.stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["items"][0]["item"], "bin:definitely-not-a-binary")
        self.assertFalse(payload["items"][0]["ok"])

        r = self.run_cli(
            [
                "check",
                "--root",
                self.fx.root,
                "--loop",
                "does-not-exist",
                "--json",
            ]
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("loop not found", r.stderr)


if __name__ == "__main__":
    unittest.main()
