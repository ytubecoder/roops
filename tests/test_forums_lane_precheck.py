"""Hermetic tests for loops.d/forums-lane.

The collector is a fake python on PATH under a temp HOME. No network.
"""

import importlib.util
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PRECHECK = REPO / "loops.d" / "forums-lane" / "precheck.sh"
LOOPCONF = REPO / "loops.d" / "forums-lane" / "loop.conf"
LOOPCTL = REPO / "bin" / "loopctl"

CREDENTIAL_NAMES = (
    "REDDITGROW_API_KEY",
    "REDDIT_CLIENT_ID",
    "REDDIT_CLIENT_SECRET",
    "REDDIT_USERNAME",
    "REDDIT_PASSWORD",
)


def _load_loopconf():
    spec = importlib.util.spec_from_file_location(
        "loopconf_mod", REPO / "bin" / "loopconf.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_fake_python(home: Path, *, stdout: str = "", code: int = 0) -> None:
    path = home / ".venvs" / "forums" / "bin" / "python"
    path.parent.mkdir(parents=True)
    payload = _shell_single(stdout)
    path.write_text(
        "#!/bin/sh\n"
        'record="${FORUMS_FAKE_RECORD:?}"\n'
        "{\n"
        '  echo "PWD=$(pwd)"\n'
        '  echo "PYTHONPATH=${PYTHONPATH-}"\n'
        '  echo "LOOP_DATA_DIR=${LOOP_DATA_DIR-}"\n'
        '  echo "ARGS:"\n'
        '  printf "%s\\n" "$@"\n'
        '} > "$record"\n'
        f"printf '%s' {payload}\n"
        f"exit {int(code)}\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _shell_single(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def _run(home: Path, env_extra: dict) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_extra)
    env["HOME"] = str(home)
    env.pop("FORUMS_LANE_ENABLED", None)
    env.pop("LOOP_DATA_DIR", None)
    env.update(env_extra)
    return subprocess.run(
        ["bash", str(PRECHECK)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


class ForumsLanePrecheckTests(unittest.TestCase):
    def test_precheck_skips_when_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            record = root / "called.txt"
            proc = _run(
                root,
                {
                    "FORUMS_LANE_ENABLED": "0",
                    "FORUMS_FAKE_RECORD": str(record),
                    "LOOPS_ROOT": str(root / "loops"),
                },
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, "")
            self.assertFalse(record.exists())
            self.assertFalse((root / "loops" / "state" / "loop-data").exists())

    def test_precheck_skips_on_empty_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            repo = root / "maguyva"
            checkout = repo / "growth-console"
            checkout.mkdir(parents=True)
            out = root / "run"
            record = root / "called.txt"
            loops_root = root / "loops"
            _write_fake_python(home, stdout="", code=0)
            proc = _run(
                home,
                {
                    "FORUMS_FAKE_RECORD": str(record),
                    "MAGUYVA_REPO": str(repo),
                    "OUT_DIR": str(out),
                    "LOOPS_ROOT": str(loops_root),
                },
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, "")
            text = record.read_text()
            self.assertIn(f"PWD={checkout}", text)
            self.assertIn("PYTHONPATH=.", text)
            self.assertIn(f"LOOP_DATA_DIR={loops_root / 'state' / 'loop-data'}", text)
            self.assertIn(
                "\n".join(
                    [
                        "ARGS:",
                        "-m",
                        "console.forums",
                        "collect",
                        "--forum",
                        "all",
                        "--out",
                        str(out / "inputs"),
                    ]
                ),
                text,
            )
            self.assertEqual((out / "inputs" / "checkout.txt").read_text(), f"{repo}\n")

    def test_precheck_forwards_collector_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            repo = root / "maguyva"
            (repo / "growth-console").mkdir(parents=True)
            out = root / "run"
            payload = "collected 2 candidates\n"
            _write_fake_python(home, stdout=payload, code=0)
            proc = _run(
                home,
                {
                    "FORUMS_FAKE_RECORD": str(root / "called.txt"),
                    "MAGUYVA_REPO": str(repo),
                    "OUT_DIR": str(out),
                    "LOOPS_ROOT": str(root / "loops"),
                },
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout, payload)

    def test_precheck_fails_when_collector_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "home"
            repo = root / "maguyva"
            (repo / "growth-console").mkdir(parents=True)
            _write_fake_python(home, stdout="", code=1)
            proc = _run(
                home,
                {
                    "FORUMS_FAKE_RECORD": str(root / "called.txt"),
                    "MAGUYVA_REPO": str(repo),
                    "OUT_DIR": str(root / "run"),
                    "LOOPS_ROOT": str(root / "loops"),
                },
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertEqual(proc.stdout, "")

    def test_loopconf_forums_lane_valid(self):
        loopconf = _load_loopconf()
        conf, errors = loopconf.parse(str(LOOPCONF))
        self.assertEqual(errors, [])
        self.assertEqual(conf["name"], "forums-lane")
        self.assertEqual(conf["type"], "agent")
        self.assertEqual(conf["engine"], "codex")
        self.assertEqual(conf["schedule"], "daily:07:10")
        self.assertEqual(conf["timeout_s"], 900)
        self.assertEqual(conf["retention_days"], 180)
        self.assertEqual(
            conf["requires"], ["env:GC_BASE", "bin:curl", "env:MAGUYVA_REPO"]
        )
        self.assertEqual(conf["perm_fs_write"], "workdir")
        self.assertEqual(conf["perm_network"], "none")
        self.assertEqual(conf["perm_local_exec"], "allowlist")
        self.assertEqual(conf["perm_remote_mutation"], "none")
        self.assertEqual(
            conf["exec_allowlist"],
            ["$HOME/.venvs/forums/bin/python -m console.forums emit:*"],
        )
        self.assertTrue(conf["notes"])
        # Rule 8 rejects a non-empty credential_env. Names are declared in
        # comments and must not carry values.
        self.assertFalse(conf["credential_env"])
        text = LOOPCONF.read_text()
        for name in CREDENTIAL_NAMES:
            self.assertIn(name, text)
            self.assertNotRegex(text, rf"{name}=\S")
        proc = subprocess.run(
            [str(LOOPCTL), "validate", "forums-lane", "--root", str(REPO)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK forums-lane", proc.stdout)


if __name__ == "__main__":
    unittest.main()
