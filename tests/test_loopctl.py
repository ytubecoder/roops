"""Tests for bin/loopctl — §8 CLI surface + §8.1 install self-verification.

Every test builds its own hermetic temp LOOPS_ROOT (§11) and never touches the
real repo's state/, reports/, launchd/, or loops.d/. `bin/loopconf.py`,
`bin/schedule.py`, and `bin/db.py` are the real, already-committed Task A
files (loaded by loopctl from its own script directory — see the module
docstring in bin/loopctl); `dashboard/generate.py` is the real, already
committed Task B module. `bin/run-loop.sh` (Task C) and `engines/<engine>.sh`
(Task D) are being built concurrently and are NEVER assumed to exist — every
fixture supplies its own stubs under the hermetic root.

launchctl is NEVER invoked for real: LOOPS_LAUNCHCTL is always pointed at a
recording Python stub (`fake_launchctl.py`, written per-fixture) whose exit
codes and call log are controllable via environment variables.
"""

import importlib.util
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import ClassVar

REPO_ROOT = Path(__file__).resolve().parent.parent
LOOPCTL = REPO_ROOT / "bin" / "loopctl"
DB_PY = REPO_ROOT / "bin" / "db.py"
FIX = os.path.join(os.path.dirname(__file__), "fixtures", "skills")

FAKE_LAUNCHCTL_SRC = """#!/usr/bin/env python3
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone

log_path = os.environ.get("FAKE_LAUNCHCTL_LOG")
if log_path:
    with open(log_path, "a") as f:
        f.write(" ".join(sys.argv[1:]) + "\\n")

verb = sys.argv[1] if len(sys.argv) > 1 else ""

# Simulates launchd actually firing the job on kickstart: inserts a fresh
# run row, so loopctl's post-kickstart poll (§8.1 step 5) has something real
# to find. Opt-in via env so failure-path tests can leave it unset and let
# the poll time out for real.
#
# FAKE_LAUNCHCTL_INSERT_RUN=<status> inserts a fresh row and immediately
# finishes it with that runner_status (start-run then finish-run) — used to
# simulate a completed engine run, whether it succeeded or failed.
#
# FAKE_LAUNCHCTL_INSERT_STARTED_ONLY=1 inserts a fresh row via start-run and
# never calls finish-run, simulating db.py's transient runner_status="started"
# row that never reaches a terminal state (e.g. the engine hangs or the
# process is killed before it can report back) — the case the install poll
# must NOT treat as success.
if verb == "kickstart" and (
    os.environ.get("FAKE_LAUNCHCTL_INSERT_RUN") or os.environ.get("FAKE_LAUNCHCTL_INSERT_STARTED_ONLY")
):
    label = sys.argv[-1].rsplit("/", 1)[-1]
    loop_name = label[len("com.loops."):] if label.startswith("com.loops.") else label
    root = os.environ["FAKE_LAUNCHCTL_ROOT"]
    db_py = os.environ["FAKE_LAUNCHCTL_DB_PY"]
    run_id = "20260722T000000Z-" + loop_name + "-" + uuid.uuid4().hex[:6]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    subprocess.run([sys.executable, db_py, "start-run", "--root", root, "--run-id", run_id,
                     "--loop", loop_name, "--engine", "codex", "--trigger", "launchd",
                     "--started-at", now], check=True)
    if os.environ.get("FAKE_LAUNCHCTL_INSERT_RUN"):
        status = os.environ["FAKE_LAUNCHCTL_INSERT_RUN"]
        subprocess.run([sys.executable, db_py, "finish-run", "--root", root, "--run-id", run_id,
                         "--runner-status", status, "--effective-status", "ok",
                         "--headline", "kickstart-simulated run", "--finished-at", now], check=True)

exit_var = "FAKE_LAUNCHCTL_" + verb.upper() + "_EXIT"
code = int(os.environ.get(exit_var, "0"))
sys.exit(code)
"""

FAKE_RUN_LOOP_SRC = """#!/usr/bin/env bash
echo "FAKE_RUN_LOOP_SH called with: $@"
exit "${FAKE_RUN_LOOP_EXIT:-0}"
"""


def run_cli(args, env_overrides=None, cwd=None):
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(LOOPCTL)] + args,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        check=False,
    )


def run_db(args):
    return subprocess.run(
        [sys.executable, str(DB_PY)] + args, capture_output=True, text=True, check=False
    )


def _query_last_runs(root, loop_name, limit=1):
    r = run_db(
        [
            "query",
            "last-runs",
            "--root",
            root,
            "--loop",
            loop_name,
            "--limit",
            str(limit),
        ]
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _query_loop_events(root, loop_name=None, limit=50):
    args = ["query", "loop-events", "--root", root, "--limit", str(limit)]
    if loop_name is not None:
        args += ["--loop", loop_name]
    r = run_db(args)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _read(path):
    with open(path) as f:
        return f.read()


def _read_plist(path):
    with open(path, "rb") as f:
        return plistlib.load(f)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_module_from_path(path, modname):
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_dashboard_module():
    """The real, already-committed dashboard/generate.py — loaded directly
    (not via bin/loopctl) so tests can independently ask it what a fixture's
    fleet health SHOULD be, to pin agreement with `loopctl status`'s own
    aggregate (Amendment 2 fix round 1 — "the dashboard is canonical")."""
    return _load_module_from_path(
        str(REPO_ROOT / "dashboard" / "generate.py"), "_test_dashboard"
    )


def _real_loopconf_parse():
    return _load_module_from_path(
        str(REPO_ROOT / "bin" / "loopconf.py"), "_test_loopconf"
    ).parse


def _real_schedule_parse():
    return _load_module_from_path(
        str(REPO_ROOT / "bin" / "schedule.py"), "_test_schedule"
    ).parse


class LoopsRoot:
    """Hermetic LOOPS_ROOT fixture with stub engines/ and a fake launchctl."""

    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="loopctl-test-")
        for d in ("loops.d", "examples", "engines", "bin", "state"):
            os.makedirs(os.path.join(self.root, d), exist_ok=True)

        for eng in ("codex", "claude"):
            p = os.path.join(self.root, "engines", f"{eng}.sh")
            with open(p, "w") as f:
                f.write("#!/usr/bin/env bash\nexit 0\n")
            os.chmod(p, 0o755)

        self.fake_launchctl = os.path.join(self.root, "fake_launchctl.py")
        with open(self.fake_launchctl, "w") as f:
            f.write(FAKE_LAUNCHCTL_SRC)
        os.chmod(self.fake_launchctl, 0o755)
        self.launchctl_log = os.path.join(self.root, "launchctl.log")

        self.run_loop_sh = os.path.join(self.root, "bin", "run-loop.sh")
        with open(self.run_loop_sh, "w") as f:
            f.write(FAKE_RUN_LOOP_SRC)
        os.chmod(self.run_loop_sh, 0o755)

        run_cli_init = subprocess.run(
            [sys.executable, str(DB_PY), "init", "--root", self.root],
            capture_output=True,
            text=True,
            check=False,
        )
        assert run_cli_init.returncode == 0, run_cli_init.stderr

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def base_env(self, **extra):
        env = {
            "LOOPS_LAUNCHCTL": self.fake_launchctl,
            "FAKE_LAUNCHCTL_LOG": self.launchctl_log,
            "FAKE_LAUNCHCTL_ROOT": self.root,
            "FAKE_LAUNCHCTL_DB_PY": str(DB_PY),
        }
        env.update(extra)
        return env

    def launchctl_calls(self):
        if not os.path.isfile(self.launchctl_log):
            return []
        with open(self.launchctl_log) as f:
            return [line.rstrip("\n") for line in f if line.strip()]

    def loop_dir(self, name, from_dir="loops.d"):
        return os.path.join(self.root, from_dir, name)

    def write_conf(self, name, lines, from_dir="loops.d"):
        d = self.loop_dir(name, from_dir)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "loop.conf"), "w") as f:
            f.write("\n".join(lines) + "\n")
        return d

    def write_prompt(self, name, text, from_dir="loops.d"):
        d = self.loop_dir(name, from_dir)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "prompt.md"), "w") as f:
            f.write(text)

    def write_spec(self, name, text, from_dir="loops.d"):
        d = self.loop_dir(name, from_dir)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "SPEC.md"), "w") as f:
            f.write(text)

    def write_precheck(self, name, executable=True, from_dir="loops.d"):
        d = self.loop_dir(name, from_dir)
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "precheck.sh")
        with open(p, "w") as f:
            f.write("#!/usr/bin/env bash\necho ok\n")
        if executable:
            os.chmod(p, 0o755)
        return p

    def minimal_valid_loop(
        self, name, extra_lines=None, from_dir="loops.d", type_="agent"
    ):
        """A loop.conf + prompt.md + SPEC.md-free (deliberately, tests add SPEC.md
        separately) minimal config that passes loopconf parsing and the §5.2
        combo checks at their safe defaults."""
        lines = [
            f"name={name}",
            'description="a test loop"',
            f"type={type_}",
            "engine=codex",
            "schedule=interval:15m",
            "perm_fs_write=report_only",
            "perm_network=none",
            "perm_local_exec=none",
            "perm_remote_mutation=none",
        ]
        if extra_lines:
            lines += extra_lines
        self.write_conf(name, lines, from_dir=from_dir)
        self.write_prompt(
            name,
            "# prompt\n\n## Finding identity\n\nsome-subject:some-condition\n",
            from_dir=from_dir,
        )
        if type_ == "watchdog":
            self.write_precheck(name, executable=True, from_dir=from_dir)
        return self.loop_dir(name, from_dir)

    def add_run(
        self,
        run_id,
        loop_name,
        started_at,
        runner_status="completed",
        finished_at=None,
        effective_status="ok",
        headline="ok",
    ):
        r = run_db(
            [
                "start-run",
                "--root",
                self.root,
                "--run-id",
                run_id,
                "--loop",
                loop_name,
                "--engine",
                "codex",
                "--trigger",
                "manual",
                "--started-at",
                started_at,
            ]
        )
        assert r.returncode == 0, r.stderr
        finish_args = [
            "finish-run",
            "--root",
            self.root,
            "--run-id",
            run_id,
            "--runner-status",
            runner_status,
            "--headline",
            headline,
            "--finished-at",
            finished_at or started_at,
        ]
        if effective_status is not None:
            finish_args += ["--effective-status", effective_status]
        r = run_db(finish_args)
        assert r.returncode == 0, r.stderr


class LoopsRootTestCase(unittest.TestCase):
    def setUp(self):
        self.fixture = LoopsRoot()
        self.addCleanup(self.fixture.cleanup)

    @property
    def root(self):
        return self.fixture.root


# ---------------------------------------------------------------------------
# loopctl new
# ---------------------------------------------------------------------------


class TestNew(LoopsRootTestCase):
    def test_scaffolds_all_five_files(self):
        r = run_cli(["new", "hello-loop", "--root", self.root])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        d = self.fixture.loop_dir("hello-loop")
        for fname in (
            "loop.conf",
            "prompt.md",
            "precheck.sh",
            "dashboard.json",
            "SPEC.md",
        ):
            self.assertTrue(os.path.isfile(os.path.join(d, fname)), fname)

    def test_precheck_is_executable(self):
        run_cli(["new", "hello-loop", "--root", self.root])
        p = os.path.join(self.fixture.loop_dir("hello-loop"), "precheck.sh")
        self.assertTrue(os.access(p, os.X_OK))

    def test_refuses_existing_dir(self):
        run_cli(["new", "hello-loop", "--root", self.root])
        r2 = run_cli(["new", "hello-loop", "--root", self.root])
        self.assertEqual(r2.returncode, 1)

    def test_invalid_name_rejected(self):
        r = run_cli(["new", "Not_Valid!", "--root", self.root])
        self.assertEqual(r.returncode, 2)

    def test_type_and_engine_flow_into_conf(self):
        run_cli(
            [
                "new",
                "watch-me",
                "--root",
                self.root,
                "--type",
                "watchdog",
                "--engine",
                "claude",
            ]
        )
        conf_path = os.path.join(self.fixture.loop_dir("watch-me"), "loop.conf")
        text = _read(conf_path)
        self.assertIn("type=watchdog", text)
        self.assertIn("engine=claude", text)

    def test_name_equals_dirname_in_conf(self):
        run_cli(["new", "hello-loop", "--root", self.root])
        conf_path = os.path.join(self.fixture.loop_dir("hello-loop"), "loop.conf")
        text = _read(conf_path)
        self.assertIn("name=hello-loop", text)

    def test_scaffold_prompt_has_finding_identity_and_rules(self):
        run_cli(["new", "hello-loop", "--root", self.root])
        text = _read(os.path.join(self.fixture.loop_dir("hello-loop"), "prompt.md"))
        self.assertIn("## Finding identity", text)
        self.assertIn("[FILL:", text)
        self.assertIn("same `finding_id`", text)
        self.assertIn("DISMISSED", text)
        self.assertIn("SNOOZED", text)

    def test_scaffold_spec_has_12_sections_in_order(self):
        run_cli(["new", "hello-loop", "--root", self.root])
        text = _read(os.path.join(self.fixture.loop_dir("hello-loop"), "SPEC.md"))
        headers = [
            "1. Purpose & stop condition",
            "2. Agentic pattern",
            "3. Type & data flow",
            "4. Cadence",
            "5. Scope & exclusions",
            "6. Guardrails",
            "7. Permission axes",
            "8. Finding identity",
            "9. Tier-1 semantics",
            "10. Tier-2 metrics",
            "11. Engine/model",
            "12. Page output",
        ]
        positions = [text.index(h) for h in headers]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(text.count("[FILL:"), 12)


# ---------------------------------------------------------------------------
# loopctl validate — scaffold flow (fails ONLY on [FILL:], passes after fill)
# ---------------------------------------------------------------------------


class TestValidateScaffoldFlow(LoopsRootTestCase):
    def test_fresh_scaffold_fails_only_on_spec_fill(self):
        run_cli(["new", "hello-loop", "--root", self.root])
        r = run_cli(["validate", "hello-loop", "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 1, msg=r.stdout + r.stderr)
        result = json.loads(r.stdout)["hello-loop"]
        self.assertFalse(result["ok"])
        self.assertTrue(
            all("[FILL:" in e for e in result["errors"]),
            msg=f"expected only SPEC.md [FILL:] failures, got: {result['errors']}",
        )
        self.assertTrue(len(result["errors"]) >= 1)

    def test_passes_after_filling_spec_and_schedule(self):
        run_cli(["new", "hello-loop", "--root", self.root])
        d = self.fixture.loop_dir("hello-loop")
        spec_path = os.path.join(d, "SPEC.md")
        text = _read(spec_path)
        import re

        filled = re.sub(r"\[FILL:[^\]]*\]", "filled in.", text)
        with open(spec_path, "w") as f:
            f.write(filled)
        # scaffold's schedule=manual parses fine on its own, so validate should
        # now pass outright.
        r = run_cli(["validate", "hello-loop", "--root", self.root])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)


# ---------------------------------------------------------------------------
# loopctl validate — §5.2 dangerous combos
# ---------------------------------------------------------------------------


class TestValidateDangerousCombos(LoopsRootTestCase):
    def _validate(self, name):
        return run_cli(["validate", name, "--root", self.root, "--json"])

    def test_rule1_network_full_local_exec_no_allowlist(self):
        self.fixture.minimal_valid_loop(
            "bad1",
            extra_lines=["perm_network=full", "perm_local_exec=allowlist"],
        )
        self.fixture.write_spec("bad1", "filled\n" * 11)
        r = self._validate("bad1")
        errors = json.loads(r.stdout)["bad1"]["errors"]
        self.assertTrue(any("rule 1" in e for e in errors), errors)

    def test_rule2_remote_mutation_without_justification(self):
        self.fixture.minimal_valid_loop(
            "bad2",
            extra_lines=[
                "perm_remote_mutation=allowlist",
                'exec_allowlist="git status"',
            ],
        )
        self.fixture.write_spec("bad2", "filled\n" * 11)
        r = self._validate("bad2")
        errors = json.loads(r.stdout)["bad2"]["errors"]
        self.assertTrue(
            any("rule 2" in e or "remote_mutation_justification" in e for e in errors),
            errors,
        )

    def test_rule3_full_exec_full_network_without_override(self):
        self.fixture.minimal_valid_loop(
            "bad3", extra_lines=["perm_local_exec=full", "perm_network=full"]
        )
        self.fixture.write_spec("bad3", "filled\n" * 11)
        r = self._validate("bad3")
        errors = json.loads(r.stdout)["bad3"]["errors"]
        self.assertTrue(any("rule 3" in e for e in errors), errors)

    def test_rule3_passes_with_override(self):
        self.fixture.minimal_valid_loop(
            "ok3",
            extra_lines=[
                "perm_local_exec=full",
                "perm_network=full",
                "i_accept_unrestricted=true",
            ],
        )
        self.fixture.write_spec("ok3", "filled\n" * 11)
        r = self._validate("ok3")
        errors = json.loads(r.stdout)["ok3"]["errors"]
        self.assertFalse(any("rule 3" in e for e in errors), errors)

    def test_rule4_bare_tool_name_rejected(self):
        self.fixture.minimal_valid_loop(
            "bad4",
            extra_lines=["perm_local_exec=allowlist", 'exec_allowlist="gh"'],
        )
        self.fixture.write_spec("bad4", "filled\n" * 11)
        r = self._validate("bad4")
        errors = json.loads(r.stdout)["bad4"]["errors"]
        self.assertTrue(any("rule 4" in e for e in errors), errors)

    def test_rule4_mutating_form_rejected(self):
        self.fixture.minimal_valid_loop(
            "bad4b",
            extra_lines=["perm_local_exec=allowlist", 'exec_allowlist="git push"'],
        )
        self.fixture.write_spec("bad4b", "filled\n" * 11)
        r = self._validate("bad4b")
        errors = json.loads(r.stdout)["bad4b"]["errors"]
        self.assertTrue(any("rule 4" in e for e in errors), errors)

    def test_rule4_scoped_read_forms_accepted(self):
        self.fixture.minimal_valid_loop(
            "ok4",
            extra_lines=[
                "perm_local_exec=allowlist",
                'exec_allowlist="gh run list,gh api -X GET,git status,npm outdated"',
            ],
        )
        self.fixture.write_spec("ok4", "filled\n" * 11)
        r = self._validate("ok4")
        errors = json.loads(r.stdout)["ok4"]["errors"]
        self.assertFalse(any("rule 4" in e for e in errors), errors)

    def test_rule5_workdir_fs_write_without_notes(self):
        self.fixture.minimal_valid_loop("bad5", extra_lines=["perm_fs_write=workdir"])
        self.fixture.write_spec("bad5", "filled\n" * 11)
        r = self._validate("bad5")
        errors = json.loads(r.stdout)["bad5"]["errors"]
        self.assertTrue(any("rule 5" in e for e in errors), errors)

    def test_rule5_passes_with_notes(self):
        self.fixture.minimal_valid_loop(
            "ok5",
            extra_lines=[
                "perm_fs_write=workdir",
                'notes="needs to write into workdir because X"',
            ],
        )
        self.fixture.write_spec("ok5", "filled\n" * 11)
        r = self._validate("ok5")
        errors = json.loads(r.stdout)["ok5"]["errors"]
        self.assertFalse(any("rule 5" in e for e in errors), errors)

    def test_rule7_codex_network_full_without_workdir(self):
        self.fixture.minimal_valid_loop("bad7", extra_lines=["perm_network=full"])
        self.fixture.write_spec("bad7", "filled\n" * 11)
        r = self._validate("bad7")
        errors = json.loads(r.stdout)["bad7"]["errors"]
        self.assertTrue(
            any("rule 7" in e and "perm_fs_write=workdir" in e for e in errors), errors
        )

    def test_rule7_passes_with_workdir(self):
        self.fixture.minimal_valid_loop(
            "ok7",
            extra_lines=[
                "perm_network=full",
                "perm_fs_write=workdir",
                'notes="needs workdir writes for network mode"',
            ],
        )
        self.fixture.write_spec("ok7", "filled\n" * 11)
        r = self._validate("ok7")
        errors = json.loads(r.stdout)["ok7"]["errors"]
        self.assertFalse(any("rule 7" in e for e in errors), errors)

    def test_rule8_credential_env_nonempty_hard_fails(self):
        # §5 credential_env row: RESERVED, not implemented in v1 —
        # loopctl validate must hard-fail any non-empty value.
        self.fixture.minimal_valid_loop(
            "bad8", extra_lines=['credential_env="SOME_TOKEN"']
        )
        self.fixture.write_spec("bad8", "filled\n" * 11)
        r = self._validate("bad8")
        errors = json.loads(r.stdout)["bad8"]["errors"]
        self.assertTrue(
            any(
                "rule 8" in e and "credential_env" in e and "reserved" in e
                for e in errors
            ),
            errors,
        )

    def test_rule8_passes_when_credential_env_absent(self):
        self.fixture.minimal_valid_loop("ok8")
        self.fixture.write_spec("ok8", "filled\n" * 11)
        r = self._validate("ok8")
        errors = json.loads(r.stdout)["ok8"]["errors"]
        self.assertFalse(any("rule 8" in e for e in errors), errors)

    def test_rule7_does_not_apply_to_claude_engine(self):
        self.fixture.minimal_valid_loop(
            "ok7b", extra_lines=["engine=claude", "perm_network=full"]
        )
        self.fixture.write_spec("ok7b", "filled\n" * 11)
        r = self._validate("ok7b")
        errors = json.loads(r.stdout)["ok7b"]["errors"]
        self.assertFalse(any("rule 7" in e for e in errors), errors)

    def test_rule6_watchdog_without_executable_precheck(self):
        self.fixture.minimal_valid_loop("bad6", type_="watchdog")
        # remove executability
        precheck = os.path.join(self.fixture.loop_dir("bad6"), "precheck.sh")
        os.chmod(precheck, 0o644)
        self.fixture.write_spec("bad6", "filled\n" * 11)
        r = self._validate("bad6")
        errors = json.loads(r.stdout)["bad6"]["errors"]
        self.assertTrue(any("rule 6" in e and "precheck" in e for e in errors), errors)

    def test_rule6_engine_adapter_missing(self):
        self.fixture.minimal_valid_loop("bad6b", extra_lines=["engine=claude"])
        os.remove(os.path.join(self.root, "engines", "claude.sh"))
        self.fixture.write_spec("bad6b", "filled\n" * 11)
        r = self._validate("bad6b")
        errors = json.loads(r.stdout)["bad6b"]["errors"]
        self.assertTrue(
            any("rule 6" in e and "engine adapter" in e for e in errors), errors
        )

    def test_rule6_name_mismatch(self):
        self.fixture.write_conf(
            "dirname-a",
            [
                "name=dirname-b",
                'description="x"',
                "type=agent",
                "engine=codex",
                "schedule=interval:15m",
            ],
        )
        self.fixture.write_prompt("dirname-a", "## Finding identity\nx\n")
        self.fixture.write_spec("dirname-a", "filled\n" * 11)
        r = self._validate("dirname-a")
        errors = json.loads(r.stdout)["dirname-a"]["errors"]
        self.assertTrue(
            any("rule 6" in e and "directory name" in e for e in errors), errors
        )

    def test_rule6_schedule_unparseable(self):
        self.fixture.minimal_valid_loop("bad6c", extra_lines=[])
        conf_path = os.path.join(self.fixture.loop_dir("bad6c"), "loop.conf")
        text = _read(conf_path).replace("schedule=interval:15m", "schedule=whenever")
        with open(conf_path, "w") as f:
            f.write(text)
        self.fixture.write_spec("bad6c", "filled\n" * 11)
        r = self._validate("bad6c")
        self.assertEqual(r.returncode, 1)
        errors = json.loads(r.stdout)["bad6c"]["errors"]
        self.assertTrue(any("schedule" in e for e in errors), errors)

    def test_validate_fails_on_non_executable_render_sh(self):
        # Amendment 2: present-but-not-executable render.sh is always a mistake.
        loop_dir = self.fixture.minimal_valid_loop("pageloop")
        self.fixture.write_spec("pageloop", "filled\n" * 11)
        render = os.path.join(loop_dir, "render.sh")
        with open(render, "w") as f:
            f.write("#!/usr/bin/env bash\nexit 0\n")
        os.chmod(render, 0o644)  # present, NOT executable
        r = self._validate("pageloop")
        self.assertEqual(r.returncode, 1)
        errors = json.loads(r.stdout)["pageloop"]["errors"]
        self.assertIn("render.sh present but not executable", errors)

    def test_validate_passes_with_executable_render_sh(self):
        loop_dir = self.fixture.minimal_valid_loop("pageloop2")
        self.fixture.write_spec("pageloop2", "filled\n" * 11)
        render = os.path.join(loop_dir, "render.sh")
        with open(render, "w") as f:
            f.write("#!/usr/bin/env bash\nexit 0\n")
        os.chmod(render, 0o755)
        r = self._validate("pageloop2")
        self.assertEqual(r.returncode, 0)
        errors = json.loads(r.stdout)["pageloop2"]["errors"]
        self.assertEqual(errors, [])


# ---------------------------------------------------------------------------
# loopctl validate — Finding identity / SPEC checks in isolation
# ---------------------------------------------------------------------------


class TestValidateFindingIdentityAndSpec(LoopsRootTestCase):
    def test_missing_finding_identity_heading_fails(self):
        self.fixture.minimal_valid_loop("no-fid")
        self.fixture.write_prompt("no-fid", "# prompt\n\nno heading here.\n")
        self.fixture.write_spec("no-fid", "filled\n" * 11)
        r = run_cli(["validate", "no-fid", "--root", self.root, "--json"])
        errors = json.loads(r.stdout)["no-fid"]["errors"]
        self.assertTrue(any("Finding identity" in e for e in errors), errors)

    def test_spec_with_fill_marker_fails(self):
        self.fixture.minimal_valid_loop("has-fill")
        self.fixture.write_spec("has-fill", "1. Purpose\n[FILL: still here]\n")
        r = run_cli(["validate", "has-fill", "--root", self.root, "--json"])
        errors = json.loads(r.stdout)["has-fill"]["errors"]
        self.assertTrue(any("[FILL:" in e for e in errors), errors)

    def test_all_flag_validates_every_loop(self):
        self.fixture.minimal_valid_loop("good-one")
        self.fixture.write_spec("good-one", "filled\n" * 11)
        self.fixture.minimal_valid_loop("bad-one")
        self.fixture.write_spec("bad-one", "1. Purpose\n[FILL: nope]\n")
        r = run_cli(["validate", "--all", "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 1)
        data = json.loads(r.stdout)
        self.assertTrue(data["good-one"]["ok"])
        self.assertFalse(data["bad-one"]["ok"])

    def test_usage_error_without_name_or_all(self):
        r = run_cli(["validate", "--root", self.root])
        self.assertEqual(r.returncode, 2)


# ---------------------------------------------------------------------------
# loopctl run
# ---------------------------------------------------------------------------


class TestRun(LoopsRootTestCase):
    def test_execs_run_loop_sh_and_propagates_exit(self):
        r = run_cli(
            ["run", "hello-loop", "--root", self.root],
            env_overrides={"FAKE_RUN_LOOP_EXIT": "0"},
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("FAKE_RUN_LOOP_SH called", r.stdout)

    def test_propagates_nonzero_exit(self):
        r = run_cli(
            ["run", "hello-loop", "--root", self.root],
            env_overrides={"FAKE_RUN_LOOP_EXIT": "7"},
        )
        self.assertEqual(r.returncode, 7)

    def test_missing_run_loop_sh_fails_cleanly(self):
        os.remove(self.fixture.run_loop_sh)
        r = run_cli(["run", "hello-loop", "--root", self.root])
        self.assertEqual(r.returncode, 1)

    def test_run_passes_root_as_loops_root_env_not_inherited(self):
        # Regression: run-loop.sh resolves its own root purely from the
        # LOOPS_ROOT env var, defaulting to $HOME/projects/loops (the REAL
        # tree) when unset. `loopctl run --root <fixture>` used to spawn
        # run-loop.sh with the bare inherited environment, so a caller whose
        # shell never exported LOOPS_ROOT would silently run against the
        # real tree instead of the hermetic --root fixture. Stub
        # run-loop.sh records the LOOPS_ROOT it actually sees.
        recorder = os.path.join(self.root, "loops_root_seen.txt")
        with open(self.fixture.run_loop_sh, "w") as f:
            f.write(f'#!/usr/bin/env bash\necho "$LOOPS_ROOT" > "{recorder}"\nexit 0\n')
        os.chmod(self.fixture.run_loop_sh, 0o755)

        env = os.environ.copy()
        env.pop("LOOPS_ROOT", None)  # simulate a shell that never exported it
        env["FAKE_RUN_LOOP_EXIT"] = "0"
        r = subprocess.run(
            [sys.executable, str(LOOPCTL), "run", "hello-loop", "--root", self.root],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        with open(recorder) as f:
            seen_root = f.read().strip()
        self.assertEqual(seen_root, self.root)


# ---------------------------------------------------------------------------
# plist generation — one schedule form per test, parsed with plistlib
# ---------------------------------------------------------------------------


class TestPlistGeneration(LoopsRootTestCase):
    def _install(self, name, extra_lines):
        self.fixture.minimal_valid_loop(name, extra_lines=extra_lines)
        self.fixture.write_spec(name, "filled\n" * 11)
        conf_path = os.path.join(self.fixture.loop_dir(name), "loop.conf")
        text = _read(conf_path)
        return conf_path, text

    def _run_install(self, name):
        # Run-first precondition (§8.1 Amendment 2): install needs a prior
        # non-failed supervised run recorded before it will even attempt the
        # launchd flow these tests are actually about.
        self.fixture.add_run(
            f"20260101T000000Z-{name}-ok1", name, "2026-01-01T00:00:00Z"
        )
        env = self.fixture.base_env(
            LOOPCTL_INSTALL_POLL_TIMEOUT_S="5",
            LOOPCTL_INSTALL_POLL_INTERVAL_S="0.1",
            FAKE_LAUNCHCTL_INSERT_RUN="completed",
        )
        r = run_cli(["install", name, "--root", self.root], env_overrides=env)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        return r

    def test_interval_schedule(self):
        self.fixture.minimal_valid_loop("iv", extra_lines=[])
        conf_path = os.path.join(self.fixture.loop_dir("iv"), "loop.conf")
        text = _read(conf_path).replace(
            "schedule=interval:15m", "schedule=interval:30m"
        )
        with open(conf_path, "w") as f:
            f.write(text)
        self.fixture.write_spec("iv", "filled\n" * 11)
        self._run_install("iv")
        plist = _read_plist(os.path.join(self.root, "launchd", "com.loops.iv.plist"))
        self.assertEqual(plist["StartInterval"], 1800)
        self.assertEqual(plist["Label"], "com.loops.iv")

    def test_daily_schedule(self):
        self.fixture.minimal_valid_loop("dl", extra_lines=[])
        conf_path = os.path.join(self.fixture.loop_dir("dl"), "loop.conf")
        text = _read(conf_path).replace("schedule=interval:15m", "schedule=daily:07:30")
        with open(conf_path, "w") as f:
            f.write(text)
        self.fixture.write_spec("dl", "filled\n" * 11)
        self._run_install("dl")
        plist = _read_plist(os.path.join(self.root, "launchd", "com.loops.dl.plist"))
        self.assertEqual(plist["StartCalendarInterval"], {"Hour": 7, "Minute": 30})

    def test_times_schedule_is_array(self):
        self.fixture.minimal_valid_loop("tm", extra_lines=[])
        conf_path = os.path.join(self.fixture.loop_dir("tm"), "loop.conf")
        text = _read(conf_path).replace(
            "schedule=interval:15m", "schedule=times:07:30,19:30"
        )
        with open(conf_path, "w") as f:
            f.write(text)
        self.fixture.write_spec("tm", "filled\n" * 11)
        self._run_install("tm")
        plist = _read_plist(os.path.join(self.root, "launchd", "com.loops.tm.plist"))
        self.assertEqual(
            plist["StartCalendarInterval"],
            [{"Hour": 7, "Minute": 30}, {"Hour": 19, "Minute": 30}],
        )

    def test_weekly_schedule(self):
        self.fixture.minimal_valid_loop("wk", extra_lines=[])
        conf_path = os.path.join(self.fixture.loop_dir("wk"), "loop.conf")
        text = _read(conf_path).replace(
            "schedule=interval:15m", "schedule=weekly:mon:08:00"
        )
        with open(conf_path, "w") as f:
            f.write(text)
        self.fixture.write_spec("wk", "filled\n" * 11)
        self._run_install("wk")
        plist = _read_plist(os.path.join(self.root, "launchd", "com.loops.wk.plist"))
        self.assertEqual(
            plist["StartCalendarInterval"], {"Hour": 8, "Minute": 0, "Weekday": 1}
        )

    def test_monthly_schedule(self):
        self.fixture.minimal_valid_loop("mo", extra_lines=[])
        conf_path = os.path.join(self.fixture.loop_dir("mo"), "loop.conf")
        text = _read(conf_path).replace(
            "schedule=interval:15m", "schedule=monthly:01:09:00"
        )
        with open(conf_path, "w") as f:
            f.write(text)
        self.fixture.write_spec("mo", "filled\n" * 11)
        self._run_install("mo")
        plist = _read_plist(os.path.join(self.root, "launchd", "com.loops.mo.plist"))
        self.assertEqual(
            plist["StartCalendarInterval"], {"Hour": 9, "Minute": 0, "Day": 1}
        )

    def test_plist_has_absolute_paths_and_env(self):
        self.fixture.minimal_valid_loop("iv2", extra_lines=[])
        self.fixture.write_spec("iv2", "filled\n" * 11)
        self._run_install("iv2")
        plist = _read_plist(os.path.join(self.root, "launchd", "com.loops.iv2.plist"))
        self.assertTrue(os.path.isabs(plist["ProgramArguments"][1]))
        self.assertTrue(os.path.isabs(plist["WorkingDirectory"]))
        self.assertIn("HOME", plist["EnvironmentVariables"])
        self.assertIn("PATH", plist["EnvironmentVariables"])
        self.assertEqual(
            plist["EnvironmentVariables"]["LOOPS_ROOT"], os.path.abspath(self.root)
        )
        self.assertTrue(
            plist["StandardOutPath"].startswith(os.path.join(self.root, "state"))
        )
        self.assertTrue(
            plist["StandardErrorPath"].startswith(os.path.join(self.root, "state"))
        )


# ---------------------------------------------------------------------------
# loopctl install — refuses manual/invalid; bootout->bootstrap->kickstart;
# run-row poll success and failure paths
# ---------------------------------------------------------------------------


class TestInstall(LoopsRootTestCase):
    def _valid_loop(self, name="ready", from_dir="loops.d"):
        self.fixture.minimal_valid_loop(name, extra_lines=[], from_dir=from_dir)
        self.fixture.write_spec(name, "filled\n" * 11, from_dir=from_dir)
        return name

    def test_refuses_examples_loop(self):
        name = self._valid_loop("example-loop", from_dir="examples")
        r = run_cli(
            ["install", name, "--root", self.root, "--from", "examples"],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("never installed", r.stderr)
        self.assertEqual(self.fixture.launchctl_calls(), [])

    def test_refuses_manual_schedule(self):
        name = self._valid_loop("manual-loop")
        conf_path = os.path.join(self.fixture.loop_dir(name), "loop.conf")
        text = _read(conf_path).replace("schedule=interval:15m", "schedule=manual")
        with open(conf_path, "w") as f:
            f.write(text)
        r = run_cli(
            ["install", name, "--root", self.root],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("manual", r.stderr)
        self.assertEqual(self.fixture.launchctl_calls(), [])

    def test_refuses_invalid_loop(self):
        name = self._valid_loop("invalid-one")
        self.fixture.write_spec(name, "1. Purpose\n[FILL: still here]\n")
        r = run_cli(
            ["install", name, "--root", self.root],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.fixture.launchctl_calls(), [])

    def test_install_refuses_without_prior_run(self):
        # Amendment 2 (2026-07-30): install refuses when the loop has zero
        # runs with runner_status in (completed, skipped-precheck) already
        # recorded — makes the validate -> supervised run -> install gauntlet
        # mechanical. This check happens before any launchctl call.
        name = self._valid_loop("fresh1")
        r = run_cli(
            ["install", name, "--root", self.root],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("loopctl run", r.stderr)
        self.assertEqual(self.fixture.launchctl_calls(), [])

    def test_install_refuses_when_only_run_is_failed(self):
        # A loop whose only run row is a FAILED runner_status is still
        # refused — the precondition requires a non-failed run, not merely
        # any run at all.
        name = self._valid_loop("failed-only")
        self.fixture.add_run(
            f"20260101T000000Z-{name}-fail1",
            name,
            "2026-01-01T00:00:00Z",
            runner_status="engine-failed",
        )
        r = run_cli(
            ["install", name, "--root", self.root],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("loopctl run", r.stderr)
        self.assertEqual(self.fixture.launchctl_calls(), [])

    def test_install_succeeds_after_completed_run_recorded(self):
        # Positive case: a prior completed run row satisfies the precondition
        # and install proceeds through the normal bootout/bootstrap/kickstart
        # flow.
        name = self._valid_loop("prior-run-ok")
        self.fixture.add_run(
            f"20260101T000000Z-{name}-ok1",
            name,
            "2026-01-01T00:00:00Z",
            runner_status="completed",
        )
        env = self.fixture.base_env(
            LOOPCTL_INSTALL_POLL_TIMEOUT_S="5",
            LOOPCTL_INSTALL_POLL_INTERVAL_S="0.1",
            FAKE_LAUNCHCTL_INSERT_RUN="completed",
        )
        r = run_cli(["install", name, "--root", self.root], env_overrides=env)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        calls = self.fixture.launchctl_calls()
        verbs = [c.split()[0] for c in calls]
        self.assertEqual(verbs, ["bootout", "bootstrap", "kickstart"])

    def test_install_succeeds_after_skipped_precheck_run_recorded(self):
        # skipped-precheck is the other status that satisfies the
        # precondition, per §8.1's runner_status pair.
        name = self._valid_loop("prior-run-skip")
        self.fixture.add_run(
            f"20260101T000000Z-{name}-skip1",
            name,
            "2026-01-01T00:00:00Z",
            runner_status="skipped-precheck",
        )
        env = self.fixture.base_env(
            LOOPCTL_INSTALL_POLL_TIMEOUT_S="5",
            LOOPCTL_INSTALL_POLL_INTERVAL_S="0.1",
            FAKE_LAUNCHCTL_INSERT_RUN="completed",
        )
        r = run_cli(["install", name, "--root", self.root], env_overrides=env)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)

    def test_success_path_runs_bootout_bootstrap_kickstart_and_verifies(self):
        name = self._valid_loop("succeeds")
        # A pre-existing run row proves the poll requires a genuinely NEW
        # run_id, not just any non-failed row already in the table.
        stale_run_id = f"20260101T000000Z-{name}-stale1"
        self.fixture.add_run(stale_run_id, name, "2026-01-01T00:00:00Z")
        env = self.fixture.base_env(
            LOOPCTL_INSTALL_POLL_TIMEOUT_S="5",
            LOOPCTL_INSTALL_POLL_INTERVAL_S="0.1",
            FAKE_LAUNCHCTL_INSERT_RUN="completed",
        )
        r = run_cli(["install", name, "--root", self.root], env_overrides=env)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        calls = self.fixture.launchctl_calls()
        verbs = [c.split()[0] for c in calls]
        self.assertEqual(verbs, ["bootout", "bootstrap", "kickstart"])
        self.assertTrue(
            os.path.isfile(
                os.path.join(self.root, "launchd", f"com.loops.{name}.plist")
            )
        )
        # the freshly-inserted row, not the stale one, is what verified install
        rows = _query_last_runs(self.root, name)
        self.assertNotEqual(rows[0]["run_id"], stale_run_id)

    def test_bootstrap_failure_aborts(self):
        name = self._valid_loop("bootstrap-fails")
        # Prior completed run satisfies the run-first precondition (§8.1
        # Amendment 2) so this test still exercises the bootstrap-failure
        # path it's named for, rather than the earlier precondition refusal.
        self.fixture.add_run(
            f"20260101T000000Z-{name}-ok1", name, "2026-01-01T00:00:00Z"
        )
        env = self.fixture.base_env(FAKE_LAUNCHCTL_BOOTSTRAP_EXIT="1")
        r = run_cli(["install", name, "--root", self.root], env_overrides=env)
        self.assertEqual(r.returncode, 1)
        calls = self.fixture.launchctl_calls()
        verbs = [c.split()[0] for c in calls]
        self.assertEqual(verbs, ["bootout", "bootstrap"])  # never reaches kickstart

    def test_kickstart_failure_aborts_and_boots_out(self):
        name = self._valid_loop("kickstart-fails")
        self.fixture.add_run(
            f"20260101T000000Z-{name}-ok1", name, "2026-01-01T00:00:00Z"
        )
        env = self.fixture.base_env(FAKE_LAUNCHCTL_KICKSTART_EXIT="1")
        r = run_cli(["install", name, "--root", self.root], env_overrides=env)
        self.assertEqual(r.returncode, 1)
        calls = self.fixture.launchctl_calls()
        verbs = [c.split()[0] for c in calls]
        self.assertEqual(verbs, ["bootout", "bootstrap", "kickstart", "bootout"])

    def test_no_fresh_run_row_fails_and_boots_out(self):
        name = self._valid_loop("no-fresh-run")
        # A prior (stale) completed run satisfies the run-first precondition
        # so install proceeds to the launchd flow; no FRESH run row appears
        # after kickstart -> the post-kickstart poll must time out.
        self.fixture.add_run(
            f"20260101T000000Z-{name}-stale1", name, "2026-01-01T00:00:00Z"
        )
        env = self.fixture.base_env(
            LOOPCTL_INSTALL_POLL_TIMEOUT_S="0.5", LOOPCTL_INSTALL_POLL_INTERVAL_S="0.1"
        )
        r = run_cli(["install", name, "--root", self.root], env_overrides=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no fresh non-failed run", r.stderr)
        calls = self.fixture.launchctl_calls()
        verbs = [c.split()[0] for c in calls]
        self.assertEqual(verbs, ["bootout", "bootstrap", "kickstart", "bootout"])

    def test_fresh_but_failed_run_status_fails_install(self):
        # The fake launchctl's kickstart inserts a FRESH, TERMINAL row (like
        # the success-path fake does with "completed") but with a failing
        # runner_status — proving the status-exclusion branch itself rejects
        # it, not just the freshness check (a pre-existing row inserted
        # before install would be rejected by freshness alone and wouldn't
        # exercise the status check at all).
        name = self._valid_loop("fresh-but-failed")
        # Prior completed run satisfies the run-first precondition so
        # install reaches the launchd flow this test targets.
        self.fixture.add_run(
            f"20260101T000000Z-{name}-ok1", name, "2026-01-01T00:00:00Z"
        )
        env = self.fixture.base_env(
            LOOPCTL_INSTALL_POLL_TIMEOUT_S="5",
            LOOPCTL_INSTALL_POLL_INTERVAL_S="0.1",
            FAKE_LAUNCHCTL_INSERT_RUN="engine-failed",
        )
        r = run_cli(["install", name, "--root", self.root], env_overrides=env)
        self.assertEqual(r.returncode, 1)
        rows = _query_last_runs(self.root, name)
        self.assertIsNotNone(rows[0]["finished_at"])
        self.assertEqual(rows[0]["runner_status"], "engine-failed")

    def test_fresh_run_stuck_in_started_fails_install(self):
        # db.py start-run writes a transient runner_status="started" row
        # BEFORE the engine actually runs. If that row never reaches a
        # terminal state (finished_at set) within the poll budget, install
        # must fail loudly — a fresh-but-never-finished row is not a pass,
        # even though "started" is not in the runner-failure-status set.
        name = self._valid_loop("fresh-stuck-started")
        # Prior completed run satisfies the run-first precondition so
        # install reaches the launchd flow this test targets.
        self.fixture.add_run(
            f"20260101T000000Z-{name}-ok1", name, "2026-01-01T00:00:00Z"
        )
        env = self.fixture.base_env(
            LOOPCTL_INSTALL_POLL_TIMEOUT_S="0.5",
            LOOPCTL_INSTALL_POLL_INTERVAL_S="0.1",
            FAKE_LAUNCHCTL_INSERT_STARTED_ONLY="1",
        )
        r = run_cli(["install", name, "--root", self.root], env_overrides=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("never reached a terminal state", r.stderr)
        rows = _query_last_runs(self.root, name)
        self.assertIsNone(rows[0]["finished_at"])
        self.assertEqual(rows[0]["runner_status"], "started")
        # verification failure boots the half-installed job out, same as
        # the no-fresh-run-at-all failure path.
        calls = self.fixture.launchctl_calls()
        verbs = [c.split()[0] for c in calls]
        self.assertEqual(verbs, ["bootout", "bootstrap", "kickstart", "bootout"])


# ---------------------------------------------------------------------------
# loopctl uninstall
# ---------------------------------------------------------------------------


class TestUninstall(LoopsRootTestCase):
    def test_removes_plist_and_boots_out(self):
        name = "to-remove"
        self.fixture.minimal_valid_loop(name)
        self.fixture.write_spec(name, "filled\n" * 11)
        os.makedirs(os.path.join(self.root, "launchd"), exist_ok=True)
        plist_path = os.path.join(self.root, "launchd", f"com.loops.{name}.plist")
        with open(plist_path, "wb") as f:
            f.write(b"<plist/>")
        r = run_cli(
            ["uninstall", name, "--root", self.root],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.isfile(plist_path))
        calls = self.fixture.launchctl_calls()
        self.assertEqual([c.split()[0] for c in calls], ["bootout"])

    def test_uninstall_with_no_plist_still_succeeds(self):
        r = run_cli(
            ["uninstall", "never-installed", "--root", self.root],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 0)


# ---------------------------------------------------------------------------
# loopctl pause / resume — conf rewrite preserves comments; bootout/bootstrap
# ---------------------------------------------------------------------------


class TestPauseResume(LoopsRootTestCase):
    def test_pause_on_freshly_scaffolded_loop_appends_enabled_line(self):
        # `loopctl new` comments out `enabled=` (it's a permissive default) —
        # pause must still work by appending an active line, never touching
        # the commented-out one.
        run_cli(["new", "fresh-toggle", "--root", self.root])
        conf_path = os.path.join(self.fixture.loop_dir("fresh-toggle"), "loop.conf")
        before = _read(conf_path)
        self.assertNotIn("\nenabled=", before)  # only the commented form is present

        r = run_cli(
            ["pause", "fresh-toggle", "--root", self.root],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)

        after = _read(conf_path)
        self.assertIn("enabled=false", after)
        self.assertIn("# enabled=true", after)  # original commented default untouched

    def test_pause_sets_enabled_false_and_preserves_comments(self):
        name = "toggle-me"
        self.fixture.write_conf(
            name,
            [
                "# a helpful comment",
                f"name={name}",
                'description="d"',
                "type=agent",
                "engine=codex",
                "schedule=interval:15m",
                "# another comment mentioning enabled= inside text",
                "enabled=true",
                "notes=keepme",
            ],
        )
        conf_path = os.path.join(self.fixture.loop_dir(name), "loop.conf")
        before = _read(conf_path)

        r = run_cli(
            ["pause", name, "--root", self.root], env_overrides=self.fixture.base_env()
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)

        after = _read(conf_path)
        self.assertIn("enabled=false", after)
        self.assertNotIn("enabled=true", after)
        self.assertIn("# a helpful comment", after)
        self.assertIn("# another comment mentioning enabled= inside text", after)
        self.assertIn("notes=keepme", after)
        # every other line is byte-identical
        before_lines = [l for l in before.splitlines() if not l.startswith("enabled=")]
        after_lines = [l for l in after.splitlines() if not l.startswith("enabled=")]
        self.assertEqual(before_lines, after_lines)

    def test_resume_sets_enabled_true(self):
        name = "toggle-me2"
        self.fixture.write_conf(
            name,
            [
                f"name={name}",
                'description="d"',
                "type=agent",
                "engine=codex",
                "schedule=interval:15m",
                "enabled=false",
            ],
        )
        r = run_cli(
            ["resume", name, "--root", self.root], env_overrides=self.fixture.base_env()
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        conf_path = os.path.join(self.fixture.loop_dir(name), "loop.conf")
        after = _read(conf_path)
        self.assertIn("enabled=true", after)

    def test_pause_boots_out_installed_job(self):
        name = "installed-toggle"
        self.fixture.write_conf(
            name,
            [
                f"name={name}",
                'description="d"',
                "type=agent",
                "engine=codex",
                "schedule=interval:15m",
                "enabled=true",
            ],
        )
        os.makedirs(os.path.join(self.root, "launchd"), exist_ok=True)
        with open(
            os.path.join(self.root, "launchd", f"com.loops.{name}.plist"), "wb"
        ) as f:
            f.write(b"<plist/>")
        r = run_cli(
            ["pause", name, "--root", self.root], env_overrides=self.fixture.base_env()
        )
        self.assertEqual(r.returncode, 0)
        calls = self.fixture.launchctl_calls()
        self.assertEqual([c.split()[0] for c in calls], ["bootout"])

    def test_resume_bootstraps_installed_job(self):
        name = "installed-toggle2"
        self.fixture.write_conf(
            name,
            [
                f"name={name}",
                'description="d"',
                "type=agent",
                "engine=codex",
                "schedule=interval:15m",
                "enabled=false",
            ],
        )
        os.makedirs(os.path.join(self.root, "launchd"), exist_ok=True)
        with open(
            os.path.join(self.root, "launchd", f"com.loops.{name}.plist"), "wb"
        ) as f:
            f.write(b"<plist/>")
        r = run_cli(
            ["resume", name, "--root", self.root], env_overrides=self.fixture.base_env()
        )
        self.assertEqual(r.returncode, 0)
        calls = self.fixture.launchctl_calls()
        self.assertEqual([c.split()[0] for c in calls], ["bootstrap"])


# ---------------------------------------------------------------------------
# loopctl dashboard
# ---------------------------------------------------------------------------


class TestDashboard(LoopsRootTestCase):
    def test_regenerates_and_prints_path(self):
        name = "dashy"
        self.fixture.minimal_valid_loop(name)
        r = run_cli(["dashboard", "--root", self.root])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        out_path = r.stdout.strip()
        self.assertTrue(os.path.isfile(out_path))
        self.assertEqual(out_path, os.path.join(self.root, "dashboard", "loops.html"))


# ---------------------------------------------------------------------------
# disposition verbs round-trip through db.py
# ---------------------------------------------------------------------------


class TestDispositions(LoopsRootTestCase):
    def _seed_finding(self, loop_name, finding_id="svc:down"):
        run_id = f"20260722T000000Z-{loop_name}-aaa111"
        started = iso(datetime.now(timezone.utc))
        r = run_db(
            [
                "start-run",
                "--root",
                self.root,
                "--run-id",
                run_id,
                "--loop",
                loop_name,
                "--engine",
                "codex",
                "--trigger",
                "manual",
                "--started-at",
                started,
            ]
        )
        self.assertEqual(r.returncode, 0, r.stderr)

        contract = {
            "schema_version": 1,
            "run_id": run_id,
            "status": "alert",
            "status_reason": "x",
            "headline": "x",
            "report_markdown": "x",
            "metrics": "{}",
            "findings": [
                {
                    "finding_id": finding_id,
                    "title": "svc down",
                    "severity": "alert",
                    "detail": "d",
                }
            ],
        }
        contract_path = os.path.join(self.root, "state", f"{run_id}.contract.json")
        with open(contract_path, "w") as f:
            json.dump(contract, f)
        r = run_db(
            [
                "upsert-findings",
                "--root",
                self.root,
                "--run-id",
                run_id,
                "--loop",
                loop_name,
                "--contract-file",
                contract_path,
                "--ts",
                started,
            ]
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        return run_id

    def test_dismiss_requires_note(self):
        name = "disp-loop"
        self.fixture.minimal_valid_loop(name)
        self._seed_finding(name)
        r = run_cli(["dismiss", name, "svc:down", "--root", self.root])
        self.assertEqual(r.returncode, 2)

    def test_dismiss_with_note_round_trips(self):
        name = "disp-loop2"
        self.fixture.minimal_valid_loop(name)
        self._seed_finding(name)
        r = run_cli(
            ["dismiss", name, "svc:down", "--note", "known issue", "--root", self.root]
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)

        rq = run_db(["query", "open-findings", "--root", self.root, "--loop", name])
        self.assertEqual(rq.returncode, 0)
        # finding still open in findings table (dismiss suppresses, doesn't resolve)
        rows = json.loads(rq.stdout)
        self.assertEqual(len(rows), 1)

        rsup = run_db(
            [
                "suppressed",
                "--root",
                self.root,
                "--loop",
                name,
                "--ts",
                iso(datetime.now(timezone.utc)),
            ]
        )
        self.assertIn("svc:down", [d["finding_id"] for d in json.loads(rsup.stdout)])

    def test_snooze_requires_until(self):
        name = "disp-loop3"
        self.fixture.minimal_valid_loop(name)
        self._seed_finding(name)
        r = run_cli(["snooze", name, "svc:down", "--root", self.root])
        self.assertEqual(r.returncode, 2)

    def test_snooze_with_until_round_trips(self):
        name = "disp-loop4"
        self.fixture.minimal_valid_loop(name)
        self._seed_finding(name)
        until = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
        r = run_cli(["snooze", name, "svc:down", "--until", until, "--root", self.root])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rsup = run_db(
            [
                "suppressed",
                "--root",
                self.root,
                "--loop",
                name,
                "--ts",
                iso(datetime.now(timezone.utc)),
            ]
        )
        self.assertIn("svc:down", [d["finding_id"] for d in json.loads(rsup.stdout)])

    def test_ack_round_trips(self):
        name = "disp-loop5"
        self.fixture.minimal_valid_loop(name)
        self._seed_finding(name)
        r = run_cli(["ack", name, "svc:down", "--root", self.root])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)

    def test_reopen_clears_dismiss(self):
        name = "disp-loop6"
        self.fixture.minimal_valid_loop(name)
        self._seed_finding(name)
        run_cli(["dismiss", name, "svc:down", "--note", "n", "--root", self.root])
        r = run_cli(["reopen", name, "svc:down", "--root", self.root])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rsup = run_db(
            [
                "suppressed",
                "--root",
                self.root,
                "--loop",
                name,
                "--ts",
                iso(datetime.now(timezone.utc)),
            ]
        )
        self.assertNotIn("svc:down", json.loads(rsup.stdout))

    def test_unknown_finding_fails(self):
        name = "disp-loop7"
        self.fixture.minimal_valid_loop(name)
        r = run_cli(["ack", name, "does:not-exist", "--root", self.root])
        self.assertEqual(r.returncode, 1)

    def test_dispose_regenerates_dashboard(self):
        name = "disp-loop8"
        self.fixture.minimal_valid_loop(name)
        self._seed_finding(name)
        dash_path = os.path.join(self.root, "dashboard", "loops.html")
        self.assertFalse(os.path.isfile(dash_path))
        r = run_cli(["ack", name, "svc:down", "--root", self.root])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertTrue(os.path.isfile(dash_path))


# ---------------------------------------------------------------------------
# loopctl findings
# ---------------------------------------------------------------------------


class TestFindings(LoopsRootTestCase):
    def test_lists_open_findings_with_disposition(self):
        name = "findings-loop"
        self.fixture.minimal_valid_loop(name)
        run_id = f"20260722T000000Z-{name}-bbb222"
        started = iso(datetime.now(timezone.utc))
        run_db(
            [
                "start-run",
                "--root",
                self.root,
                "--run-id",
                run_id,
                "--loop",
                name,
                "--engine",
                "codex",
                "--trigger",
                "manual",
                "--started-at",
                started,
            ]
        )
        contract = {
            "schema_version": 1,
            "run_id": run_id,
            "status": "warn",
            "status_reason": "x",
            "headline": "x",
            "report_markdown": "x",
            "metrics": "{}",
            "findings": [
                {
                    "finding_id": "repo:dirty",
                    "title": "dirty repo",
                    "severity": "warn",
                    "detail": "d",
                }
            ],
        }
        contract_path = os.path.join(self.root, "state", f"{run_id}.contract.json")
        with open(contract_path, "w") as f:
            json.dump(contract, f)
        run_db(
            [
                "upsert-findings",
                "--root",
                self.root,
                "--run-id",
                run_id,
                "--loop",
                name,
                "--contract-file",
                contract_path,
                "--ts",
                started,
            ]
        )

        r = run_cli(["findings", name, "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rows = json.loads(r.stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["finding_id"], "repo:dirty")
        self.assertEqual(rows[0]["severity"], "warn")
        self.assertEqual(rows[0]["disposition"], "open")
        self.assertEqual(rows[0]["times_seen"], 1)

    def test_no_findings_returns_empty(self):
        name = "no-findings-loop"
        self.fixture.minimal_valid_loop(name)
        r = run_cli(["findings", name, "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout), [])

    def test_findings_empty_state(self):
        # Definitive empty state (Amendment 2 — 2026-07-30): the human form
        # says explicitly "0 open findings for <loop>" instead of the
        # generic table "(none)".
        name = "no-findings-loop2"
        self.fixture.minimal_valid_loop(name)
        r = run_cli(["findings", name, "--root", self.root])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn(f"0 open findings for {name}", r.stdout)


# ---------------------------------------------------------------------------
# loopctl list / status
# ---------------------------------------------------------------------------


class TestListStatus(LoopsRootTestCase):
    def test_list_json_shape(self):
        self.fixture.minimal_valid_loop("l1")
        self.fixture.minimal_valid_loop("l2", type_="watchdog")
        r = run_cli(
            ["list", "--root", self.root, "--json"],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rows = {row["name"]: row for row in json.loads(r.stdout)}
        self.assertEqual(set(rows), {"l1", "l2"})
        self.assertEqual(rows["l1"]["engine"], "codex")
        self.assertEqual(rows["l2"]["type"], "watchdog")
        self.assertIn("installed", rows["l1"])
        self.assertFalse(rows["l1"]["installed"])

    def test_list_human_table(self):
        self.fixture.minimal_valid_loop("l3")
        r = run_cli(
            ["list", "--root", self.root], env_overrides=self.fixture.base_env()
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("l3", r.stdout)
        self.assertIn("name", r.stdout)

    def test_list_empty_state(self):
        # Definitive empty state (Amendment 2 — 2026-07-30): zero loops
        # under loops.d/ prints "0 loops (loops.d empty)", not the generic
        # table "(none)", and still exits 0.
        r = run_cli(
            ["list", "--root", self.root], env_overrides=self.fixture.base_env()
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("0 loops (loops.d empty)", r.stdout)

    def test_list_tag_no_match_on_nonempty_fleet_is_not_the_empty_message(self):
        # Fix wave (2026-07-30, IMPORTANT #1): the tag filter used to mutate
        # `rows` BEFORE the empty-state check ran, so a fleet with loops but
        # no --tag match printed the same "0 loops (loops.d empty)" message
        # as a genuinely empty loops.d/ -- a definitively false statement
        # about a 2-loop fleet. It must name the filter and the true fleet
        # size instead, and still exit 0.
        self.fixture.minimal_valid_loop("tagged", extra_lines=['tags="project:x"'])
        self.fixture.minimal_valid_loop("untagged")
        r = run_cli(
            ["list", "--tag", "project:nope", "--root", self.root],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertNotIn("empty)", r.stdout)
        self.assertIn(
            "0 loops matching --tag project:nope (2 loops under loops.d)", r.stdout
        )

    def test_status_single_loop_no_runs(self):
        self.fixture.minimal_valid_loop("s1")
        r = run_cli(["status", "s1", "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rows = json.loads(r.stdout)["loops"]
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["runner_status"])
        self.assertFalse(rows[0]["in_flight"])

    def test_status_single_loop_with_run(self):
        name = "s2"
        self.fixture.minimal_valid_loop(name)
        self.fixture.add_run(
            f"20260722T000000Z-{name}-c1",
            name,
            iso(datetime.now(timezone.utc)),
            headline="all good",
        )
        r = run_cli(["status", name, "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rows = json.loads(r.stdout)["loops"]
        self.assertEqual(rows[0]["headline"], "all good")
        self.assertEqual(rows[0]["runner_status"], "completed")

    def test_status_all_loops(self):
        self.fixture.minimal_valid_loop("s3")
        self.fixture.minimal_valid_loop("s4")
        r = run_cli(["status", "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0)
        data = json.loads(r.stdout)
        rows = data["loops"]
        self.assertEqual({row["name"] for row in rows}, {"s3", "s4"})
        self.assertEqual(data["fleet"]["loops"], 2)

    def test_status_json_envelope_shape(self):
        self.fixture.minimal_valid_loop("s3b")
        r = run_cli(["status", "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(set(data), {"fleet", "loops"})
        self.assertEqual(
            set(data["fleet"]),
            {"loops", "ok", "warn", "alert", "needs_attention", "spend7d"},
        )

    def test_status_aggregate_line(self):
        self.fixture.minimal_valid_loop("s5")
        r = run_cli(["status", "--root", self.root])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("fleet:", r.stdout.splitlines()[0])

    def test_status_empty_fleet_prints_aggregate_then_empty_state(self):
        r = run_cli(["status", "--root", self.root])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        lines = r.stdout.splitlines()
        self.assertIn("fleet: 0 loops", lines[0])
        self.assertIn("0 loops (loops.d empty)", r.stdout)

    def test_status_falls_back_past_overlap_row(self):
        name = "s6"
        self.fixture.minimal_valid_loop(name)
        self.fixture.add_run(
            f"20260722T000000Z-{name}-c1",
            name,
            "2026-07-29T00:00:00Z",
            runner_status="completed",
            effective_status="ok",
            headline="all good",
        )
        self.fixture.add_run(
            f"20260722T000000Z-{name}-c2",
            name,
            iso(datetime.now(timezone.utc)),
            runner_status="skipped-overlap",
        )
        r = run_cli(["status", name, "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rows = json.loads(r.stdout)["loops"]
        self.assertEqual(rows[0]["headline"], "all good")
        self.assertEqual(rows[0]["runner_status"], "completed")
        self.assertFalse(rows[0]["in_flight"])

    def test_status_in_flight_true_for_unfinished_run(self):
        name = "s7"
        self.fixture.minimal_valid_loop(name)
        run_id = f"20260722T000000Z-{name}-c1"
        r_start = run_db(
            [
                "start-run",
                "--root",
                self.root,
                "--run-id",
                run_id,
                "--loop",
                name,
                "--engine",
                "codex",
                "--trigger",
                "manual",
                "--started-at",
                iso(datetime.now(timezone.utc)),
            ]
        )
        self.assertEqual(r_start.returncode, 0, msg=r_start.stderr)
        r = run_cli(["status", name, "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rows = json.loads(r.stdout)["loops"]
        self.assertTrue(rows[0]["in_flight"])
        self.assertIsNone(rows[0]["runner_status"])

    def test_status_falls_back_past_unfinished_row(self):
        # An unfinished (still-running) newest row is the OTHER blanking
        # trigger, alongside skipped-overlap — both must fall back to the
        # newest terminal row for status/headline, while in_flight still
        # reports the true (unfinished) newest row's state.
        name = "s8"
        self.fixture.minimal_valid_loop(name)
        self.fixture.add_run(
            f"20260722T000000Z-{name}-c1",
            name,
            "2026-07-29T00:00:00Z",
            runner_status="completed",
            effective_status="ok",
            headline="all good",
        )
        run_id = f"20260722T000000Z-{name}-c2"
        r_start = run_db(
            [
                "start-run",
                "--root",
                self.root,
                "--run-id",
                run_id,
                "--loop",
                name,
                "--engine",
                "codex",
                "--trigger",
                "manual",
                "--started-at",
                iso(datetime.now(timezone.utc)),
            ]
        )
        self.assertEqual(r_start.returncode, 0, msg=r_start.stderr)
        r = run_cli(["status", name, "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rows = json.loads(r.stdout)["loops"]
        self.assertEqual(rows[0]["headline"], "all good")
        self.assertTrue(rows[0]["in_flight"])

    def test_status_fleet_aggregate_counts_ok_warn_and_spend(self):
        self.fixture.minimal_valid_loop("ok-loop9")
        self.fixture.minimal_valid_loop("warn-loop9")
        self.fixture.add_run(
            "20260722T000000Z-ok-loop9-c1",
            "ok-loop9",
            iso(datetime.now(timezone.utc)),
            runner_status="completed",
            effective_status="ok",
            headline="fine",
        )
        self.fixture.add_run(
            "20260722T000000Z-warn-loop9-c1",
            "warn-loop9",
            iso(datetime.now(timezone.utc)),
            runner_status="completed",
            effective_status="warn",
            headline="hmm",
        )
        r = run_cli(["status", "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        fleet = json.loads(r.stdout)["fleet"]
        self.assertEqual(fleet["loops"], 2)
        self.assertEqual(fleet["ok"], 1)
        self.assertEqual(fleet["warn"], 1)
        self.assertEqual(fleet["alert"], 0)
        self.assertEqual(fleet["needs_attention"], 1)

    def test_fleet_aggregate_agrees_with_dashboard_on_overlap_over_ok(self):
        # Fix round 1 ("the dashboard is canonical" ruling): a loop whose
        # newest run is skipped-overlap over a prior ok run must count as
        # warn/needs_attention in the fleet aggregate, NOT ok — dashboard/
        # generate.py's compute_light() maps skipped-overlap to amber
        # unconditionally, regardless of what came before. This pins
        # agreement between `status --json`'s aggregate and the dashboard's
        # own _resolve_loop() on identical fixture data — the exact
        # divergence verified during review (aggregate said needs_attention:
        # 0, dashboard said True). The per-loop DISPLAYED status/headline
        # still uses the blanking-fix fallback ("all good") — asserted
        # separately in test_status_falls_back_past_overlap_row; this test
        # is about the aggregate health counts only.
        name = "overlap-agree"
        self.fixture.minimal_valid_loop(name)
        self.fixture.add_run(
            f"20260722T000000Z-{name}-c1",
            name,
            "2026-07-29T00:00:00Z",
            runner_status="completed",
            effective_status="ok",
            headline="all good",
        )
        self.fixture.add_run(
            f"20260722T000000Z-{name}-c2",
            name,
            iso(datetime.now(timezone.utc)),
            runner_status="skipped-overlap",
        )

        r = run_cli(["status", "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        fleet = json.loads(r.stdout)["fleet"]
        self.assertEqual(fleet["ok"], 0)
        self.assertEqual(fleet["warn"], 1)
        self.assertEqual(fleet["alert"], 0)
        self.assertEqual(fleet["needs_attention"], 1)

        dash_mod = _load_dashboard_module()
        conn = dash_mod._open_db(self.root)
        try:
            resolved = dash_mod._resolve_loop(
                self.root,
                name,
                conn,
                _real_loopconf_parse(),
                _real_schedule_parse(),
                datetime.now(timezone.utc),
            )
        finally:
            conn.close()
        self.assertTrue(resolved["needs_attention"])
        self.assertEqual(resolved["light_color"], "amber")

    def test_fleet_aggregate_agrees_with_dashboard_on_same_second_tie(self):
        # MINOR #1 (fix wave, 2026-07-30): db.py's query_loops_summary used a
        # naive MAX(started_at) self-join that could return MULTIPLE rows per
        # loop on a started_at TIE (e.g. a skipped-overlap row written the
        # same second a run starts) -- loopctl's dict-comprehension kept the
        # LAST of those rows, while the dashboard's own _latest_run() ("ORDER
        # BY started_at DESC LIMIT 1", no tie-break) effectively kept the
        # FIRST. Reproduced: `status --json` reported alert/needs_attention
        # while the dashboard said green for the same fixture. Both must now
        # agree deterministically (rowid DESC tie-break in both places).
        name = "same-second-tie"
        self.fixture.minimal_valid_loop(name)
        same_ts = "2026-07-29T00:00:00Z"
        self.fixture.add_run(
            f"20260729T000000Z-{name}-a",
            name,
            same_ts,
            runner_status="completed",
            effective_status="ok",
            headline="all good",
        )
        self.fixture.add_run(
            f"20260729T000000Z-{name}-b",
            name,
            same_ts,
            runner_status="skipped-overlap",
        )

        r = run_cli(["status", "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        fleet = json.loads(r.stdout)["fleet"]

        dash_mod = _load_dashboard_module()
        conn = dash_mod._open_db(self.root)
        try:
            resolved = dash_mod._resolve_loop(
                self.root,
                name,
                conn,
                _real_loopconf_parse(),
                _real_schedule_parse(),
                datetime.now(timezone.utc),
            )
        finally:
            conn.close()

        # Whichever of the two tied rows wins the deterministic tie-break,
        # loopctl's fleet aggregate and the dashboard's own resolution must
        # agree with EACH OTHER -- not merely each be internally consistent.
        self.assertEqual(
            fleet["needs_attention"], 1 if resolved["needs_attention"] else 0
        )
        self.assertEqual(fleet["alert"], 1 if resolved["light_color"] == "red" else 0)
        self.assertEqual(fleet["warn"], 1 if resolved["light_color"] == "amber" else 0)
        self.assertEqual(fleet["ok"], 1 if resolved["light_color"] == "green" else 0)
        # Pin the actual winner too, not just "they agree with each other" --
        # rowid DESC means the later-inserted row (skipped-overlap, "b") wins,
        # and compute_light() maps skipped-overlap to amber unconditionally.
        self.assertEqual(resolved["light_color"], "amber")
        self.assertEqual(fleet["warn"], 1)

    def test_list_tag_filter_exact(self):
        self.fixture.minimal_valid_loop("tagged", extra_lines=['tags="project:x"'])
        self.fixture.minimal_valid_loop("untagged")
        r = run_cli(
            ["list", "--tag", "project:x", "--root", self.root, "--json"],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rows = json.loads(r.stdout)
        self.assertEqual([row["name"] for row in rows], ["tagged"])

        # exact match, not substring — "project" must not match "project:x"
        r2 = run_cli(
            ["list", "--tag", "project", "--root", self.root, "--json"],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r2.returncode, 0, msg=r2.stdout + r2.stderr)
        self.assertEqual(json.loads(r2.stdout), [])

    def test_list_json_includes_tags(self):
        self.fixture.minimal_valid_loop(
            "tagged", extra_lines=['tags="project:x,team:infra"']
        )
        self.fixture.minimal_valid_loop("untagged")
        r = run_cli(
            ["list", "--root", self.root, "--json"],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rows = {row["name"]: row for row in json.loads(r.stdout)}
        self.assertEqual(rows["tagged"]["tags"], ["project:x", "team:infra"])
        self.assertEqual(rows["untagged"]["tags"], [])

    def test_list_human_table_includes_tags_column(self):
        self.fixture.minimal_valid_loop("tagged", extra_lines=['tags="project:x"'])
        r = run_cli(
            ["list", "--root", self.root], env_overrides=self.fixture.base_env()
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("tags", r.stdout)
        self.assertIn("project:x", r.stdout)

    def test_status_json_includes_tags_and_provenance(self):
        self.fixture.minimal_valid_loop("tagged", extra_lines=['tags="project:x"'])
        r_new = run_cli(
            [
                "new",
                "fresh",
                "--root",
                self.root,
                "--type",
                "agent",
                "--engine",
                "codex",
                "--actor",
                "claude/t",
            ]
        )
        self.assertEqual(r_new.returncode, 0, msg=r_new.stdout + r_new.stderr)
        r = run_cli(["status", "fresh", "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rows = json.loads(r.stdout)["loops"]
        self.assertEqual(rows[0]["tags"], [])
        self.assertEqual(rows[0]["provenance"]["actor"], "claude/t")
        self.assertEqual(rows[0]["provenance"]["event"], "created")

    def test_status_json_provenance_none_when_no_events(self):
        self.fixture.minimal_valid_loop("noprov")
        r = run_cli(["status", "noprov", "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rows = json.loads(r.stdout)["loops"]
        self.assertIsNone(rows[0]["provenance"])

    def test_status_json_provenance_survives_beyond_ten_later_events(self):
        # Regression: a loop paused/resumed 10+ times must not push its
        # founding `created` event out of a naive "scan the newest 10 rows"
        # window — provenance must still resolve it via a SQL-side events
        # filter, not a client-side scan of a limited row set.
        name = "provwin"
        self.fixture.minimal_valid_loop(name)
        r_created = run_db(
            [
                "record-event",
                "--root",
                self.root,
                "--loop",
                name,
                "--event",
                "created",
                "--actor",
                "claude/t",
            ]
        )
        self.assertEqual(r_created.returncode, 0, msg=r_created.stderr)
        for _ in range(12):
            for ev in ("paused", "resumed"):
                r_ev = run_db(
                    [
                        "record-event",
                        "--root",
                        self.root,
                        "--loop",
                        name,
                        "--event",
                        ev,
                        "--actor",
                        "t",
                    ]
                )
                self.assertEqual(r_ev.returncode, 0, msg=r_ev.stderr)

        r = run_cli(["status", name, "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rows = json.loads(r.stdout)["loops"]
        self.assertEqual(rows[0]["provenance"]["event"], "created")
        self.assertEqual(rows[0]["provenance"]["actor"], "claude/t")


# ---------------------------------------------------------------------------
# loopctl bare invocation (Amendment 2 — 2026-07-30): content-first — no verb
# means "show me the fleet", not "show me usage"
# ---------------------------------------------------------------------------


class TestBareInvocation(LoopsRootTestCase):
    def test_bare_invocation_prints_summary_exit_0(self):
        self.fixture.minimal_valid_loop("b1")
        r = run_cli(["--root", self.root])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("fleet:", r.stdout)

    def test_bare_invocation_respects_root_flag(self):
        self.fixture.minimal_valid_loop("b2")
        # Decoy default root (fix round 2 test hygiene): without this, the
        # assertion leans on the real ~/projects/loops not happening to
        # have exactly 1 loop — a silent fallback to the default would
        # pass or fail this test depending on machine state, not on
        # whether --root was actually respected.
        decoy = tempfile.mkdtemp(prefix="loopctl-decoy-")
        self.addCleanup(shutil.rmtree, decoy, ignore_errors=True)
        r = run_cli(["--root", self.root], env_overrides={"LOOPS_ROOT": decoy})
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("fleet: 1 loops", r.stdout)

    def test_bare_invocation_respects_loops_root_env_with_no_args_at_all(self):
        self.fixture.minimal_valid_loop("b3")
        r = run_cli([], env_overrides={"LOOPS_ROOT": self.root})
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("fleet: 1 loops", r.stdout)

    def test_unknown_verb_still_exit_2(self):
        r = run_cli(["frobnicate", "--root", self.root])
        self.assertEqual(r.returncode, 2)

    def test_help_still_exits_0_and_does_not_dispatch(self):
        # --help is a deliberate ask for usage, distinct from a bare
        # invocation — it must keep behaving like before (print help, exit
        # 0) rather than falling into the new content-first summary path.
        r = run_cli(["--help"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("usage: loopctl", r.stdout)
        self.assertNotIn("fleet:", r.stdout)

    def test_help_output_hides_the_hoisted_common_flags(self):
        # Fix round 1 (verified Critical + this Important companion): `p`
        # now also carries --root/--json/--from/--actor directly (so a
        # bare/pre-verb invocation of any of them can be parsed at all —
        # see TestGlobalFlagPlacement), but they must stay invisible in
        # `loopctl --help` — help=SUPPRESS on that copy specifically.
        # Verified byte-identical against the pre-Amendment-2 commit's
        # --help output during review; asserting the meaningful invariant
        # here rather than the exact formatted text.
        r = run_cli(["--help"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("positional arguments:", r.stdout)
        for hidden_flag in ("--root", "--json", "--from", "--actor"):
            self.assertNotIn(hidden_flag, r.stdout)

    def test_verb_help_still_shows_common_flags(self):
        # The subparser-level copies (`common_sub`) must stay visible in
        # per-verb --help — only the top-level copy is hidden.
        r = run_cli(["status", "--help"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        for flag in ("--root", "--json", "--from", "--actor"):
            self.assertIn(flag, r.stdout)

    # -- fix round 2: a verb-less parse isn't always a genuine bare
    # invocation -- three ways a typo silently loses the verb/root and
    # must now exit 2 instead of exit 0 against the default root.

    def test_unrecognized_flag_with_no_verb_exits_2(self):
        # Repro 1: on the pre-Task-16 base this exited 2. The bug: `extra`
        # was checked AFTER the verb-is-None branch, so a typo'd flag name
        # with no verb silently printed the DEFAULT-root fleet at exit 0.
        decoy = tempfile.mkdtemp(prefix="loopctl-decoy-")
        self.addCleanup(shutil.rmtree, decoy, ignore_errors=True)
        r = run_cli(["--root-dir=/sandbox"], env_overrides={"LOOPS_ROOT": decoy})
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("fleet:", r.stdout)
        self.assertIn("unrecognized arguments", r.stderr)

    def test_actor_swallowing_a_verb_token_exits_2(self):
        # Repro 2: --actor takes a value, so `loopctl --actor status`
        # parses cleanly as actor="status", verb=None, extra=[] -- nothing
        # for an "unrecognized arguments" check to catch. Must refuse
        # rather than silently default the root.
        decoy = tempfile.mkdtemp(prefix="loopctl-decoy-")
        self.addCleanup(shutil.rmtree, decoy, ignore_errors=True)
        r = run_cli(["--actor", "status"], env_overrides={"LOOPS_ROOT": decoy})
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("fleet:", r.stdout)
        self.assertIn("ambiguous invocation", r.stderr)

    def test_root_swallowing_a_verb_token_exits_2(self):
        # Repro 3: same shape as above but for --root -- the more
        # dangerous case, since root silently becoming the literal string
        # "status" would (if not refused) drive every subsequent read
        # against a bogus path instead of the intended root.
        decoy = tempfile.mkdtemp(prefix="loopctl-decoy-")
        self.addCleanup(shutil.rmtree, decoy, ignore_errors=True)
        r = run_cli(["--root", "status"], env_overrides={"LOOPS_ROOT": decoy})
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("fleet:", r.stdout)
        self.assertIn("ambiguous invocation", r.stderr)

    def test_flag_equals_syntax_is_the_ambiguity_escape_hatch(self):
        # `--actor=status` is a single argv token, never equal to a bare
        # verb name, so a genuinely-intended literal value survives the
        # ambiguity check the three tests above rely on.
        self.fixture.minimal_valid_loop("b4")
        decoy = tempfile.mkdtemp(prefix="loopctl-decoy-")
        self.addCleanup(shutil.rmtree, decoy, ignore_errors=True)
        r = run_cli(
            ["--root", self.root, "--actor=status"], env_overrides={"LOOPS_ROOT": decoy}
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("fleet: 1 loops", r.stdout)


# ---------------------------------------------------------------------------
# loopctl global flag placement (Amendment 2 — 2026-07-30 fix round 1):
# --root/--json/--from/--actor must resolve identically whether given before
# or after the verb — a verified Critical: naive parents=[common] on both the
# top-level parser and every subparser let the subparser's fresh sub-
# namespace silently clobber a correctly-resolved pre-verb value with its
# own default.
# ---------------------------------------------------------------------------


class TestGlobalFlagPlacement(LoopsRootTestCase):
    def test_root_before_and_after_verb_are_equivalent(self):
        self.fixture.minimal_valid_loop("g1")
        # A decoy default root: if --root ever gets silently dropped back to
        # a default, these calls would see 0 loops instead of 1 (or worse,
        # in a non-hermetic run, the real ~/projects/loops fleet).
        decoy = tempfile.mkdtemp(prefix="loopctl-decoy-")
        self.addCleanup(shutil.rmtree, decoy, ignore_errors=True)
        env = {"LOOPS_ROOT": decoy}

        r_before = run_cli(["--root", self.root, "status", "--json"], env_overrides=env)
        r_after = run_cli(["status", "--root", self.root, "--json"], env_overrides=env)
        self.assertEqual(r_before.returncode, 0, msg=r_before.stdout + r_before.stderr)
        self.assertEqual(r_after.returncode, 0, msg=r_after.stdout + r_after.stderr)

        fleet_before = json.loads(r_before.stdout)["fleet"]
        fleet_after = json.loads(r_after.stdout)["fleet"]
        self.assertEqual(fleet_before["loops"], 1)
        self.assertEqual(fleet_before, fleet_after)

    def test_root_before_verb_on_genuine_bare_invocation(self):
        # Fix round 2 test hygiene: this used to be misnamed and actually
        # duplicate the "before" half of test_root_before_and_after_verb_
        # are_equivalent (its argv included the "status" verb, so it
        # wasn't a bare invocation at all). A genuine bare invocation —
        # --root with no verb whatsoever — must resolve --root correctly
        # too, not just the "flags before a real verb" case.
        self.fixture.minimal_valid_loop("g1b")
        decoy = tempfile.mkdtemp(prefix="loopctl-decoy-")
        self.addCleanup(shutil.rmtree, decoy, ignore_errors=True)
        r = run_cli(
            ["--root", self.root, "--json"], env_overrides={"LOOPS_ROOT": decoy}
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["fleet"]["loops"], 1)

    def test_json_before_and_after_verb_are_equivalent(self):
        self.fixture.minimal_valid_loop("g2")
        r_before = run_cli(["--json", "list", "--root", self.root])
        r_after = run_cli(["list", "--json", "--root", self.root])
        self.assertEqual(r_before.returncode, 0, msg=r_before.stdout + r_before.stderr)
        self.assertEqual(r_after.returncode, 0, msg=r_after.stdout + r_after.stderr)
        # Both must parse as JSON (a table would fail json.loads) — proves
        # --json wasn't silently dropped in the "before the verb" placement.
        rows_before = json.loads(r_before.stdout)
        rows_after = json.loads(r_after.stdout)
        self.assertEqual([row["name"] for row in rows_before], ["g2"])
        self.assertEqual(rows_before, rows_after)

    def test_from_before_and_after_verb_are_equivalent(self):
        self.fixture.minimal_valid_loop("g3", from_dir="examples")
        r_before = run_cli(
            ["--from", "examples", "list", "--root", self.root, "--json"]
        )
        r_after = run_cli(["list", "--from", "examples", "--root", self.root, "--json"])
        self.assertEqual(r_before.returncode, 0, msg=r_before.stdout + r_before.stderr)
        self.assertEqual(r_after.returncode, 0, msg=r_after.stdout + r_after.stderr)
        rows_before = json.loads(r_before.stdout)
        self.assertEqual([row["name"] for row in rows_before], ["g3"])
        self.assertEqual(rows_before, json.loads(r_after.stdout))

    def test_actor_before_and_after_verb_are_equivalent(self):
        r_before = run_cli(
            ["--actor", "claude/before", "new", "actor-before", "--root", self.root]
        )
        self.assertEqual(r_before.returncode, 0, msg=r_before.stdout + r_before.stderr)
        events_before = _query_loop_events(self.root, "actor-before")
        self.assertEqual(events_before[0]["actor"], "claude/before")

        r_after = run_cli(
            ["new", "actor-after", "--root", self.root, "--actor", "claude/after"]
        )
        self.assertEqual(r_after.returncode, 0, msg=r_after.stdout + r_after.stderr)
        events_after = _query_loop_events(self.root, "actor-after")
        self.assertEqual(events_after[0]["actor"], "claude/after")


# ---------------------------------------------------------------------------
# loopctl lifecycle events (Amendment 2) — --actor + _record_event call sites
# ---------------------------------------------------------------------------


class TestLifecycleEvents(LoopsRootTestCase):
    def _valid_loop(self, name, from_dir="loops.d"):
        self.fixture.minimal_valid_loop(name, extra_lines=[], from_dir=from_dir)
        self.fixture.write_spec(name, "filled\n" * 11, from_dir=from_dir)
        return name

    def test_new_records_created_event_with_default_actor(self):
        r = run_cli(
            [
                "new",
                "evt-loop",
                "--root",
                self.root,
                "--type",
                "agent",
                "--engine",
                "codex",
            ]
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        events = _query_loop_events(self.root, "evt-loop")
        self.assertEqual(events[0]["event"], "created")
        self.assertEqual(events[0]["actor"], os.environ.get("USER", "unknown"))

    def test_new_records_created_event_detail(self):
        r = run_cli(
            [
                "new",
                "evt-loop-detail",
                "--root",
                self.root,
                "--type",
                "watchdog",
                "--engine",
                "claude",
            ]
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        events = _query_loop_events(self.root, "evt-loop-detail")
        self.assertEqual(events[0]["event"], "created")
        detail = json.loads(events[0]["detail"])
        self.assertEqual(detail, {"type": "watchdog", "engine": "claude"})

    def test_actor_flag_overrides(self):
        r = run_cli(
            [
                "new",
                "evt-loop2",
                "--root",
                self.root,
                "--type",
                "agent",
                "--engine",
                "codex",
                "--actor",
                "claude/testproj",
            ]
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        events = _query_loop_events(self.root, "evt-loop2")
        self.assertEqual(events[0]["actor"], "claude/testproj")

    def test_pause_resume_record_events(self):
        name = self._valid_loop("evt3")
        r1 = run_cli(
            ["pause", name, "--root", self.root], env_overrides=self.fixture.base_env()
        )
        self.assertEqual(r1.returncode, 0, msg=r1.stdout + r1.stderr)
        r2 = run_cli(
            ["resume", name, "--root", self.root], env_overrides=self.fixture.base_env()
        )
        self.assertEqual(r2.returncode, 0, msg=r2.stdout + r2.stderr)
        names = [e["event"] for e in _query_loop_events(self.root, name)]
        self.assertEqual(names[:2], ["resumed", "paused"])  # newest first

    def test_pause_resume_record_events_even_when_never_installed(self):
        # Neither the loop nor its plist has ever been installed — pause/resume
        # still flip enabled= and still record the intent (ambiguity resolution).
        name = "evt-uninstalled"
        self.fixture.write_conf(
            name,
            [
                f"name={name}",
                'description="d"',
                "type=agent",
                "engine=codex",
                "schedule=interval:15m",
                "enabled=true",
            ],
        )
        r = run_cli(
            ["pause", name, "--root", self.root], env_overrides=self.fixture.base_env()
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        events = _query_loop_events(self.root, name)
        self.assertEqual(events[0]["event"], "paused")
        self.assertEqual(events[0]["actor"], os.environ.get("USER", "unknown"))

    def test_pause_actor_flag_overrides(self):
        name = self._valid_loop("evt-pause-actor")
        r = run_cli(
            ["pause", name, "--root", self.root, "--actor", "claude/testproj"],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        events = _query_loop_events(self.root, name)
        self.assertEqual(events[0]["actor"], "claude/testproj")

    def test_install_records_installed_event_on_success(self):
        name = self._valid_loop("evt-install")
        # Run-first precondition (§8.1 Amendment 2).
        self.fixture.add_run(
            f"20260101T000000Z-{name}-ok1", name, "2026-01-01T00:00:00Z"
        )
        env = self.fixture.base_env(
            LOOPCTL_INSTALL_POLL_TIMEOUT_S="5",
            LOOPCTL_INSTALL_POLL_INTERVAL_S="0.1",
            FAKE_LAUNCHCTL_INSERT_RUN="completed",
        )
        r = run_cli(["install", name, "--root", self.root], env_overrides=env)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        events = _query_loop_events(self.root, name)
        self.assertEqual(events[0]["event"], "installed")
        self.assertEqual(events[0]["actor"], os.environ.get("USER", "unknown"))

    def test_install_does_not_record_event_on_kickstart_failure(self):
        name = self._valid_loop("evt-install-fails")
        # Run-first precondition (§8.1 Amendment 2) so this test still
        # exercises the kickstart-failure path it's named for.
        self.fixture.add_run(
            f"20260101T000000Z-{name}-ok1", name, "2026-01-01T00:00:00Z"
        )
        env = self.fixture.base_env(FAKE_LAUNCHCTL_KICKSTART_EXIT="1")
        r = run_cli(["install", name, "--root", self.root], env_overrides=env)
        self.assertEqual(r.returncode, 1)
        events = _query_loop_events(self.root, name)
        self.assertEqual(events, [])

    def test_uninstall_records_uninstalled_event(self):
        name = self._valid_loop("evt-uninstall")
        r = run_cli(
            ["uninstall", name, "--root", self.root],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        events = _query_loop_events(self.root, name)
        self.assertEqual(events[0]["event"], "uninstalled")
        self.assertEqual(events[0]["actor"], os.environ.get("USER", "unknown"))

    def test_new_succeeds_even_when_event_recording_fails(self):
        # Fix round 1: proves the best-effort/never-fail contract on
        # _record_event, rather than just relying on manual verification.
        #
        # A read-only loops.sqlite makes db.py record-event's own INSERT
        # fail for real (verified below) — db.py's connect() opens the file
        # fine and both PRAGMA calls succeed (journal_mode is already WAL,
        # so it's a no-op that needs no write), so the failure only surfaces
        # at the actual INSERT, which raises uncaught and exits non-zero.
        #
        # Two side effects of that failed attempt need explicit handling:
        # connect() unconditionally chmods the *main* file back to 0600 on
        # its way out (§0 file-modes rule), independent of whether the write
        # itself succeeded — so the read-only mode must be freshly
        # re-applied to the main file before each subprocess invocation that
        # needs to observe the denial. And SQLite creates the WAL mode's
        # -wal/-shm sidecar files inheriting the main file's 0444 at the
        # moment they're created — connect() never resets *those* — so they
        # stay permanently read-only afterward unless explicitly restored,
        # which would break any later write in the same test (verified: this
        # bit us during development — the final read-back query failed with
        # the same "attempt to write a readonly database" until the sidecars
        # were restored too).
        db_path = os.path.join(self.root, "state", "loops.sqlite")
        sidecar_paths = [db_path + "-wal", db_path + "-shm"]
        self.assertTrue(os.path.isfile(db_path))

        def _restore_perms():
            for p in [db_path] + sidecar_paths:
                if os.path.isfile(p):
                    os.chmod(p, 0o600)

        self.addCleanup(_restore_perms)

        # Prove the injection is real: db.py record-event must fail non-zero
        # against a read-only loops.sqlite.
        os.chmod(db_path, 0o444)
        r_probe = run_db(
            [
                "record-event",
                "--root",
                self.root,
                "--loop",
                "probe-loop",
                "--event",
                "created",
                "--actor",
                "tester",
            ]
        )
        self.assertNotEqual(r_probe.returncode, 0)

        # Re-inject for the actual call under test (see the note above: the
        # probe call already silently restored the main file's permission).
        os.chmod(db_path, 0o444)

        r = run_cli(["new", "evt-bestfeffort", "--root", self.root])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        loop_dir = self.fixture.loop_dir("evt-bestfeffort")
        for fname in (
            "loop.conf",
            "prompt.md",
            "SPEC.md",
            "dashboard.json",
            "precheck.sh",
        ):
            self.assertTrue(os.path.isfile(os.path.join(loop_dir, fname)), fname)

        # And the write genuinely never landed (not just "didn't crash") —
        # confirms this exercised the failure path, not a lucky race. Restore
        # full write access (main file + WAL sidecars) before reading back.
        _restore_perms()
        events = _query_loop_events(self.root, "evt-bestfeffort")
        self.assertEqual(events, [])


# ---------------------------------------------------------------------------
# loopctl import --analyze (Task 10)
# ---------------------------------------------------------------------------


class TestImport(LoopsRootTestCase):
    def test_import_analyze_json(self):
        r = run_cli(
            [
                "import",
                os.path.join(FIX, "clean-check"),
                "--analyze",
                "--json",
                "--root",
                self.root,
            ]
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["analyzer_version"], "1")
        self.assertIn("answers_needed", out)

    def test_import_analyze_missing_path_exits_1(self):
        r = run_cli(["import", "/nonexistent", "--analyze", "--root", self.root])
        self.assertEqual(r.returncode, 1)

    def test_import_analyze_blocked_fixture_still_exits_0(self):
        # A blocked skill (needs-creds: STRIPE_API_KEY) still analyzes fine —
        # `blocked` is a field in the output, not a CLI failure.
        r_json = run_cli(
            [
                "import",
                os.path.join(FIX, "needs-creds"),
                "--analyze",
                "--json",
                "--root",
                self.root,
            ]
        )
        self.assertEqual(r_json.returncode, 0, msg=r_json.stdout + r_json.stderr)
        out = json.loads(r_json.stdout)
        self.assertTrue(out["blocked"])
        self.assertTrue(
            any(reason.startswith("[blocking]") for reason in out["blocked_reasons"])
        )

        r_human = run_cli(
            [
                "import",
                os.path.join(FIX, "needs-creds"),
                "--analyze",
                "--root",
                self.root,
            ]
        )
        self.assertEqual(r_human.returncode, 0, msg=r_human.stdout + r_human.stderr)
        self.assertIn("blocked", r_human.stdout.lower())
        self.assertTrue(
            any("[blocking]" in line for line in r_human.stdout.splitlines())
        )

    def test_import_requires_analyze_or_apply(self):
        r = run_cli(["import", os.path.join(FIX, "clean-check"), "--root", self.root])
        self.assertEqual(r.returncode, 2)

    def test_import_analyze_and_apply_mutually_exclusive(self):
        r = run_cli(
            [
                "import",
                os.path.join(FIX, "clean-check"),
                "--analyze",
                "--apply",
                "--root",
                self.root,
            ]
        )
        self.assertEqual(r.returncode, 2)

    def test_import_apply_requires_answers(self):
        # Task 12: --apply is implemented now, but still needs --answers.
        r = run_cli(
            ["import", os.path.join(FIX, "clean-check"), "--apply", "--root", self.root]
        )
        self.assertEqual(r.returncode, 2)
        self.assertIn("--answers", r.stdout + r.stderr)

    def test_import_analyze_human_form_shows_header_rubric_and_safety_framing(self):
        r = run_cli(
            [
                "import",
                os.path.join(FIX, "clean-check"),
                "--analyze",
                "--root",
                self.root,
            ]
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        out = r.stdout
        # header: proposed name / type / engine / blocked
        self.assertIn("repo-hygiene-check", out)
        self.assertIn("agent", out)
        self.assertIn("blocked", out.lower())
        # rubric table
        self.assertIn("q1_purpose", out)
        self.assertIn("q8_finding_identity", out)
        # flags line
        self.assertIn("mutation", out.lower())
        # precheck proposal, with the safety framing the controller required:
        # commented lines + an explicit human-review caveat + the heuristic caveat.
        self.assertIn("# [read-only?]", out)
        self.assertIn("COMMENTED", out.upper())
        self.assertIn("heuristic", out.lower())
        # answers needed, numbered
        self.assertIn("q4_cadence", out)


# ---------------------------------------------------------------------------
# loopctl import --apply (Task 12)
# ---------------------------------------------------------------------------


class TestImportApply(LoopsRootTestCase):
    # The brief's own filled example (docs/SKILL_IMPORT.md §7), against the
    # clean-check fixture — skill_sha256 is filled in per-test by
    # _write_answers() from a live --analyze, never hardcoded here.
    CLEAN_ANSWERS: ClassVar[dict] = {
        "analyzer_version": "1",
        "skill_sha256": None,
        "answers": {
            "q1_purpose": (
                "Report dirty/unpushed repos; done per-firing = report written; "
                "cross-run done = repo becomes clean"
            ),
            "q4_cadence": "daily:07:30",
            "q5_scope": "~/projects only; exclude maguyva",
            "q8_finding_identity": (
                "<repo-dir-name>:<condition> where condition is dirty|unpushed"
            ),
            "q9_semantics": "ok=all clean; warn=any dirty/unpushed; alert=never",
            "q10_metrics": (
                '{"panels":[{"title":"Dirty","metric":"repos.dirty","type":"number"}]}'
            ),
            "q11_budget": "engine default model; ~1k tokens; retry 1; timeout 300",
        },
        "provenance": {"q4_cadence": "user"},
        "acknowledge_blocked": False,
    }

    def _scaffold_root(self):
        return self.root

    def _analyze_json(self, root, fixture):
        r = run_cli(
            [
                "import",
                os.path.join(FIX, fixture),
                "--analyze",
                "--json",
                "--root",
                root,
            ]
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        return json.loads(r.stdout)

    def _write_answers(self, root, filename, answers, fixture=None):
        """Deep-copies `answers` (tests must never mutate the shared class
        constant) and, if `skill_sha256` is falsy, fills it in from a live
        `--analyze` against `fixture` — never hardcoded, so this always
        tracks whatever the analyzer currently produces."""
        answers = json.loads(json.dumps(answers))
        if fixture is not None and not answers.get("skill_sha256"):
            answers["skill_sha256"] = self._analyze_json(root, fixture)["skill_sha256"]
        path = os.path.join(root, filename)
        with open(path, "w") as f:
            json.dump(answers, f)
        return path

    def _loopctl(self, root, *args):
        return run_cli(list(args) + ["--root", root])

    def _loopctl_rc(self, root, *args):
        return self._loopctl(root, *args).returncode

    def _db_query_json(self, root, query_name, **kwargs):
        args = ["query", query_name, "--root", root]
        for k, v in kwargs.items():
            args += [f"--{k.replace('_', '-')}", str(v)]
        r = run_db(args)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    # --- the load-bearing test --------------------------------------------

    def test_apply_scaffold_passes_validate(self):
        root = self._scaffold_root()
        self._write_answers(
            root, "answers.json", self.CLEAN_ANSWERS, fixture="clean-check"
        )
        analysis = self._analyze_json(root, "clean-check")

        r = self._loopctl(
            root,
            "import",
            os.path.join(FIX, "clean-check"),
            "--apply",
            "--answers",
            os.path.join(root, "answers.json"),
            "--actor",
            "claude/t",
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)

        rc = self._loopctl_rc(root, "validate", "repo-hygiene-check")
        self.assertEqual(rc, 0)

        events = self._db_query_json(root, "loop-events", loop="repo-hygiene-check")
        self.assertEqual(events[0]["event"], "imported")

        precheck_path = os.path.join(
            root, "loops.d", "repo-hygiene-check", "precheck.sh"
        )
        self.assertTrue(os.access(precheck_path, os.X_OK))
        pre = _read(precheck_path)
        # Every line of the injected proposal block (analysis["precheck_proposal"])
        # must survive verbatim, still commented — not "any line containing
        # 'git '", which would miss a proposal line apply() silently mangled
        # as long as it didn't happen to say "git ".
        for line in analysis["precheck_proposal"]:
            self.assertTrue(line.startswith("#"))
            self.assertIn(line, pre)

    # --- stale hash ---------------------------------------------------------

    def test_apply_stale_hash_refused(self):
        root = self._scaffold_root()
        answers = json.loads(json.dumps(self.CLEAN_ANSWERS))
        answers["skill_sha256"] = "0" * 64  # definitely wrong
        answers_path = os.path.join(root, "answers.json")
        with open(answers_path, "w") as f:
            json.dump(answers, f)

        r = self._loopctl(
            root,
            "import",
            os.path.join(FIX, "clean-check"),
            "--apply",
            "--answers",
            answers_path,
            "--actor",
            "claude/t",
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("stale", (r.stdout + r.stderr).lower())
        self.assertFalse(
            os.path.isdir(os.path.join(root, "loops.d", "repo-hygiene-check"))
        )
        events = self._db_query_json(root, "loop-events", loop="repo-hygiene-check")
        self.assertEqual(events, [])

    # --- collision / --overwrite --------------------------------------------

    def test_apply_collision_refused_without_overwrite(self):
        root = self._scaffold_root()
        self._write_answers(
            root, "answers.json", self.CLEAN_ANSWERS, fixture="clean-check"
        )
        answers_path = os.path.join(root, "answers.json")

        r1 = self._loopctl(
            root,
            "import",
            os.path.join(FIX, "clean-check"),
            "--apply",
            "--answers",
            answers_path,
            "--actor",
            "claude/t",
        )
        self.assertEqual(r1.returncode, 0, msg=r1.stdout + r1.stderr)

        r2 = self._loopctl(
            root,
            "import",
            os.path.join(FIX, "clean-check"),
            "--apply",
            "--answers",
            answers_path,
            "--actor",
            "claude/t",
        )
        self.assertEqual(r2.returncode, 1)
        self.assertIn("already exists", (r2.stdout + r2.stderr).lower())

        r3 = self._loopctl(
            root,
            "import",
            os.path.join(FIX, "clean-check"),
            "--apply",
            "--answers",
            answers_path,
            "--overwrite",
            "--actor",
            "claude/t",
        )
        self.assertEqual(r3.returncode, 0, msg=r3.stdout + r3.stderr)

        events = self._db_query_json(
            root, "loop-events", loop="repo-hygiene-check", events="imported"
        )
        self.assertGreaterEqual(len(events), 2)
        latest_detail = json.loads(events[0]["detail"])
        self.assertTrue(latest_detail["overwrite"])
        oldest_detail = json.loads(events[-1]["detail"])
        self.assertFalse(oldest_detail["overwrite"])

    def test_overwrite_refused_when_target_is_installed(self):
        # IMPORTANT #2a (fix wave, 2026-07-30): --overwrite must refuse
        # outright (no files touched, no event recorded) when the target
        # loop is currently INSTALLED -- overwriting an installed loop's
        # prompt.md/loop.conf/precheck.sh in place would let the next
        # launchd firing run the new prompt with none of validate ->
        # supervised run -> install re-applied. No force-past flag; the
        # message must point at `loopctl uninstall <name>`.
        root = self._scaffold_root()
        name = "repo-hygiene-check"
        answers_path = self._write_answers(
            root, "answers.json", self.CLEAN_ANSWERS, fixture="clean-check"
        )

        r1 = self._loopctl(
            root,
            "import",
            os.path.join(FIX, "clean-check"),
            "--apply",
            "--answers",
            answers_path,
            "--actor",
            "claude/t",
        )
        self.assertEqual(r1.returncode, 0, msg=r1.stdout + r1.stderr)

        # Satisfy install's own "prior non-failed supervised run" precondition.
        self.fixture.add_run(
            f"20260101T000000Z-{name}-ok1",
            name,
            "2026-01-01T00:00:00Z",
            runner_status="completed",
        )
        install_env = self.fixture.base_env(
            LOOPCTL_INSTALL_POLL_TIMEOUT_S="5",
            LOOPCTL_INSTALL_POLL_INTERVAL_S="0.1",
            FAKE_LAUNCHCTL_INSERT_RUN="completed",
        )
        r_install = run_cli(
            ["install", name, "--root", root], env_overrides=install_env
        )
        self.assertEqual(
            r_install.returncode, 0, msg=r_install.stdout + r_install.stderr
        )

        r_overwrite = run_cli(
            [
                "import",
                os.path.join(FIX, "clean-check"),
                "--apply",
                "--answers",
                answers_path,
                "--overwrite",
                "--actor",
                "claude/t",
                "--root",
                root,
            ],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r_overwrite.returncode, 1)
        msg = (r_overwrite.stdout + r_overwrite.stderr).lower()
        self.assertIn("installed", msg)
        self.assertIn("loopctl uninstall", msg)

        # No new imported event from the refused attempt -- exactly the one
        # from the original successful import.
        events = self._db_query_json(root, "loop-events", loop=name, events="imported")
        self.assertEqual(len(events), 1)

    # --- blocked + acknowledge_blocked --------------------------------------

    def test_apply_blocked_needs_acknowledgement(self):
        root = self._scaffold_root()
        answers = {
            "analyzer_version": "1",
            "skill_sha256": self._analyze_json(root, "needs-creds")["skill_sha256"],
            "answers": {
                "q1_purpose": "Report yesterday's failed Stripe charges",
                "q4_cadence": "daily:08:00",
                "q5_scope": "the connected Stripe account only",
                "q8_finding_identity": "stripe:failed-charges",
                "q9_semantics": "warn=any failed charge yesterday; alert=never",
                "q10_metrics": '{"panels":[]}',
                "q11_budget": "engine default model; retry 1; timeout 300",
            },
            "provenance": {},
            "acknowledge_blocked": False,
        }
        answers_path = os.path.join(root, "answers.json")
        with open(answers_path, "w") as f:
            json.dump(answers, f)

        r1 = self._loopctl(
            root,
            "import",
            os.path.join(FIX, "needs-creds"),
            "--apply",
            "--answers",
            answers_path,
            "--actor",
            "claude/t",
        )
        self.assertEqual(r1.returncode, 1)
        self.assertIn("blocked", (r1.stdout + r1.stderr).lower())
        self.assertFalse(
            os.path.isdir(os.path.join(root, "loops.d", "stripe-failed-charges"))
        )
        events = self._db_query_json(root, "loop-events", loop="stripe-failed-charges")
        self.assertEqual(events, [])

        answers["acknowledge_blocked"] = True
        with open(answers_path, "w") as f:
            json.dump(answers, f)

        r2 = self._loopctl(
            root,
            "import",
            os.path.join(FIX, "needs-creds"),
            "--apply",
            "--answers",
            answers_path,
            "--actor",
            "claude/t",
        )
        self.assertEqual(r2.returncode, 0, msg=r2.stdout + r2.stderr)

        loop_dir = os.path.join(root, "loops.d", "stripe-failed-charges")
        conf_text = _read(os.path.join(loop_dir, "loop.conf"))
        self.assertIn("schedule=manual", conf_text)
        spec_text = _read(os.path.join(loop_dir, "SPEC.md"))
        self.assertIn("## BLOCKED — read before scheduling", spec_text)
        self.assertIn("credentials", spec_text.lower())

    # --- import grants no dangerous-combo immunity --------------------------

    def test_apply_dangerous_combo_still_fails_validate(self):
        root = self._scaffold_root()
        self._write_answers(
            root, "answers.json", self.CLEAN_ANSWERS, fixture="clean-check"
        )
        answers_path = os.path.join(root, "answers.json")

        r = self._loopctl(
            root,
            "import",
            os.path.join(FIX, "clean-check"),
            "--apply",
            "--answers",
            answers_path,
            "--actor",
            "claude/t",
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)

        conf_path = os.path.join(root, "loops.d", "repo-hygiene-check", "loop.conf")
        text = _read(conf_path)
        # Hand-edit to a dangerous combo: perm_remote_mutation=allowlist with
        # no justification and no exec_allowlist — import grants no immunity
        # from §5.2's dangerous-combination checks.
        text = text.replace(
            "perm_remote_mutation=none", "perm_remote_mutation=allowlist"
        )
        with open(conf_path, "w") as f:
            f.write(text)

        rc = self._loopctl_rc(root, "validate", "repo-hygiene-check")
        self.assertEqual(rc, 1)

    # --- round-1 review: malformed answers.json leaves no dir behind --------

    def test_apply_malformed_answers_shape_leaves_no_directory_and_retry_succeeds(
        self,
    ):
        # Reviewer-reported defect: {"answers": ["q1_purpose"]} (a list, not
        # an object) previously tracebacked out of the CLI AND left an empty
        # loops.d/<name>/ behind, making the next, CORRECT attempt fail with
        # a spurious "already exists".
        root = self._scaffold_root()
        analysis = self._analyze_json(root, "clean-check")
        answers = {
            "analyzer_version": "1",
            "skill_sha256": analysis["skill_sha256"],
            "answers": ["q1_purpose"],  # malformed: list instead of object
            "acknowledge_blocked": False,
        }
        answers_path = os.path.join(root, "answers.json")
        with open(answers_path, "w") as f:
            json.dump(answers, f)

        r1 = self._loopctl(
            root,
            "import",
            os.path.join(FIX, "clean-check"),
            "--apply",
            "--answers",
            answers_path,
            "--actor",
            "claude/t",
        )
        self.assertEqual(r1.returncode, 1)
        self.assertFalse(
            os.path.isdir(os.path.join(root, "loops.d", "repo-hygiene-check"))
        )
        events = self._db_query_json(root, "loop-events", loop="repo-hygiene-check")
        self.assertEqual(events, [])

        # A CORRECT retry must succeed — no spurious "already exists" from a
        # half-written directory the first (malformed) attempt might have
        # left behind.
        self._write_answers(
            root, "answers2.json", self.CLEAN_ANSWERS, fixture="clean-check"
        )
        r2 = self._loopctl(
            root,
            "import",
            os.path.join(FIX, "clean-check"),
            "--apply",
            "--answers",
            os.path.join(root, "answers2.json"),
            "--actor",
            "claude/t",
        )
        self.assertEqual(r2.returncode, 0, msg=r2.stdout + r2.stderr)

    # --- round-1 review: free-text budget prose must never become config ----

    def test_apply_free_text_budget_prose_never_becomes_config(self):
        root = self._scaffold_root()
        answers = json.loads(json.dumps(self.CLEAN_ANSWERS))
        # The reviewer's own reproducer: prose that would have tricked the
        # old regex scraper into inventing model=unless / timeout_s=30.
        answers["answers"]["q11_budget"] = (
            "use the default model unless cost spikes; timeout 30 minutes"
        )
        self._write_answers(root, "answers.json", answers, fixture="clean-check")

        r = self._loopctl(
            root,
            "import",
            os.path.join(FIX, "clean-check"),
            "--apply",
            "--answers",
            os.path.join(root, "answers.json"),
            "--actor",
            "claude/t",
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        conf = _read(os.path.join(root, "loops.d", "repo-hygiene-check", "loop.conf"))
        self.assertNotIn("model=unless", conf)
        self.assertNotIn("timeout_s=30", conf)
        self.assertNotIn("model=", conf)
        self.assertNotIn("timeout_s=", conf)

    def test_apply_structured_budget_keys_honored(self):
        root = self._scaffold_root()
        answers = json.loads(json.dumps(self.CLEAN_ANSWERS))
        answers["model"] = "claude-sonnet-5"
        answers["timeout_s"] = 600
        answers["retry_transient"] = 2
        self._write_answers(root, "answers.json", answers, fixture="clean-check")

        r = self._loopctl(
            root,
            "import",
            os.path.join(FIX, "clean-check"),
            "--apply",
            "--answers",
            os.path.join(root, "answers.json"),
            "--actor",
            "claude/t",
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        conf = _read(os.path.join(root, "loops.d", "repo-hygiene-check", "loop.conf"))
        self.assertIn("model=claude-sonnet-5", conf)
        self.assertIn("timeout_s=600", conf)
        self.assertIn("retry_transient=2", conf)

    def test_apply_invalid_structured_timeout_s_refused(self):
        root = self._scaffold_root()
        answers = json.loads(json.dumps(self.CLEAN_ANSWERS))
        answers["timeout_s"] = 99999  # out of loopconf's 30-7200 range
        self._write_answers(root, "answers.json", answers, fixture="clean-check")

        r = self._loopctl(
            root,
            "import",
            os.path.join(FIX, "clean-check"),
            "--apply",
            "--answers",
            os.path.join(root, "answers.json"),
            "--actor",
            "claude/t",
        )
        self.assertEqual(r.returncode, 1)
        self.assertFalse(
            os.path.isdir(os.path.join(root, "loops.d", "repo-hygiene-check"))
        )
        events = self._db_query_json(root, "loop-events", loop="repo-hygiene-check")
        self.assertEqual(events, [])

    # --- round-2 review: model / schedule shape, non-dict answers ----------

    def test_apply_model_with_internal_whitespace_refused(self):
        root = self._scaffold_root()
        answers = json.loads(json.dumps(self.CLEAN_ANSWERS))
        answers["model"] = "gpt 4 turbo"  # loop.conf writes model= bare, unquoted
        self._write_answers(root, "answers.json", answers, fixture="clean-check")

        r = self._loopctl(
            root,
            "import",
            os.path.join(FIX, "clean-check"),
            "--apply",
            "--answers",
            os.path.join(root, "answers.json"),
            "--actor",
            "claude/t",
        )
        self.assertEqual(r.returncode, 1)
        self.assertFalse(
            os.path.isdir(os.path.join(root, "loops.d", "repo-hygiene-check"))
        )
        events = self._db_query_json(root, "loop-events", loop="repo-hygiene-check")
        self.assertEqual(events, [])

    def test_apply_model_with_double_quote_refused(self):
        # Round-3 review: the round-2 regex (^\S+$) excluded whitespace but
        # not the double-quote character — a `model` containing `"` slipped
        # through and was written bare into loop.conf, where loopconf.parse()
        # then broke with "unterminated quoted value".
        root = self._scaffold_root()
        for bad in ('"weird-model', 'weird"model', 'model"', 'a"b"c', '"'):
            answers = json.loads(json.dumps(self.CLEAN_ANSWERS))
            answers["model"] = bad
            self._write_answers(root, "answers.json", answers, fixture="clean-check")

            r = self._loopctl(
                root,
                "import",
                os.path.join(FIX, "clean-check"),
                "--apply",
                "--answers",
                os.path.join(root, "answers.json"),
                "--actor",
                "claude/t",
            )
            self.assertEqual(r.returncode, 1, msg=f"bad={bad!r}: {r.stdout + r.stderr}")
            self.assertFalse(
                os.path.isdir(os.path.join(root, "loops.d", "repo-hygiene-check")),
                msg=f"bad={bad!r}",
            )
            events = self._db_query_json(root, "loop-events", loop="repo-hygiene-check")
            self.assertEqual(events, [], msg=f"bad={bad!r}")

    def test_apply_legitimate_model_ids_still_accepted(self):
        root = self._scaffold_root()
        for good in ("claude-sonnet-5", "gpt-5.5", "foo\\bar"):
            answers = json.loads(json.dumps(self.CLEAN_ANSWERS))
            answers["model"] = good
            self._write_answers(
                root, f"answers-{hash(good)}.json", answers, fixture="clean-check"
            )

            r = self._loopctl(
                root,
                "import",
                os.path.join(FIX, "clean-check"),
                "--apply",
                "--answers",
                os.path.join(root, f"answers-{hash(good)}.json"),
                "--overwrite",
                "--actor",
                "claude/t",
            )
            self.assertEqual(
                r.returncode, 0, msg=f"good={good!r}: {r.stdout + r.stderr}"
            )
            conf = _read(
                os.path.join(root, "loops.d", "repo-hygiene-check", "loop.conf")
            )
            self.assertIn(f"model={good}", conf, msg=f"good={good!r}")

    def test_apply_free_text_cadence_refused(self):
        # Same "silently unparseable loop.conf" shape as the model bug: a
        # free-text q4_cadence answer must be refused, not written through
        # to schedule= and left for loopctl validate to discover indirectly.
        root = self._scaffold_root()
        answers = json.loads(json.dumps(self.CLEAN_ANSWERS))
        answers["answers"]["q4_cadence"] = "daily at 07:30"
        self._write_answers(root, "answers.json", answers, fixture="clean-check")

        r = self._loopctl(
            root,
            "import",
            os.path.join(FIX, "clean-check"),
            "--apply",
            "--answers",
            os.path.join(root, "answers.json"),
            "--actor",
            "claude/t",
        )
        self.assertEqual(r.returncode, 1)
        self.assertFalse(
            os.path.isdir(os.path.join(root, "loops.d", "repo-hygiene-check"))
        )
        events = self._db_query_json(root, "loop-events", loop="repo-hygiene-check")
        self.assertEqual(events, [])

    def test_apply_non_dict_top_level_answers_refused(self):
        # A bare JSON list/string/number as answers.json's whole top-level
        # value must refuse cleanly (exit 1), the same as every other
        # malformed-input path — not traceback, and not leave a directory
        # behind for a corrected retry to spuriously collide with.
        root = self._scaffold_root()
        for bad in (["x"], "hello", 42):
            answers_path = os.path.join(root, "answers.json")
            with open(answers_path, "w") as f:
                json.dump(bad, f)

            r = self._loopctl(
                root,
                "import",
                os.path.join(FIX, "clean-check"),
                "--apply",
                "--answers",
                answers_path,
                "--actor",
                "claude/t",
            )
            self.assertEqual(r.returncode, 1, msg=f"bad={bad!r}: {r.stdout + r.stderr}")
            self.assertFalse(
                os.path.isdir(os.path.join(root, "loops.d", "repo-hygiene-check")),
                msg=f"bad={bad!r}",
            )
            events = self._db_query_json(root, "loop-events", loop="repo-hygiene-check")
            self.assertEqual(events, [], msg=f"bad={bad!r}")


# ---------------------------------------------------------------------------
# loopctl set-schedule — validate-before-write; conf rewrite; plist re-render
# when present; bootout/bootstrap on reschedule of an enabled+installed loop;
# NEVER kickstart (rescheduling must not fire a run); manual removes the
# plist after bootout.
# ---------------------------------------------------------------------------


class TestSetSchedule(LoopsRootTestCase):
    def _write_loop(self, name, schedule, enabled=None):
        lines = [
            f"name={name}",
            'description="d"',
            "type=agent",
            "engine=codex",
            f"schedule={schedule}",
        ]
        if enabled is not None:
            lines.append(f"enabled={enabled}")
        self.fixture.write_conf(name, lines)

    def _conf_path(self, name):
        return os.path.join(self.fixture.loop_dir(name), "loop.conf")

    def _plist_path(self, name):
        return os.path.join(self.root, "launchd", f"com.loops.{name}.plist")

    def _write_plist(self, name):
        os.makedirs(os.path.join(self.root, "launchd"), exist_ok=True)
        with open(self._plist_path(name), "wb") as f:
            f.write(b"<plist/>")

    def _set_schedule(self, name, spec):
        return run_cli(
            ["set-schedule", name, spec, "--root", self.root],
            env_overrides=self.fixture.base_env(),
        )

    def test_rejects_bad_grammar(self):
        self._write_loop("alpha", "daily:09:00")
        r = self._set_schedule("alpha", "interval:nonsense")
        self.assertEqual(r.returncode, 1, msg=r.stdout + r.stderr)
        self.assertIn("invalid", r.stderr.lower())
        conf = _read(self._conf_path("alpha"))
        self.assertIn("schedule=daily:09:00", conf)  # untouched

    def test_unknown_loop_fails(self):
        r = self._set_schedule("ghost", "daily:09:00")
        self.assertEqual(r.returncode, 1)

    def test_rewrites_conf_no_plist_no_launchctl(self):
        self._write_loop("alpha", "daily:09:00")
        r = self._set_schedule("alpha", "interval:15m")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("schedule=interval:15m", _read(self._conf_path("alpha")))
        self.assertEqual(
            self.fixture.launchctl_calls(), []
        )  # no plist -> nothing to reload

    def test_plist_present_disabled_rerenders_without_bootstrap(self):
        self._write_loop("alpha", "daily:09:00", enabled="false")
        self._write_plist("alpha")
        r = self._set_schedule("alpha", "interval:2h")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        with open(self._plist_path("alpha"), "rb") as f:
            self.assertIn(b"StartInterval", f.read())
        joined = " ".join(self.fixture.launchctl_calls())
        self.assertNotIn("bootstrap", joined)
        self.assertNotIn("kickstart", joined)

    def test_plist_present_enabled_bootout_bootstrap_no_kickstart(self):
        self._write_loop("alpha", "daily:09:00", enabled="true")
        self._write_plist("alpha")
        r = self._set_schedule("alpha", "weekly:mon:08:00")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        joined = " ".join(self.fixture.launchctl_calls())
        self.assertIn("bootout", joined)
        self.assertIn("bootstrap", joined)
        self.assertNotIn("kickstart", joined)

    def test_manual_removes_plist_after_bootout(self):
        self._write_loop("alpha", "daily:09:00", enabled="true")
        self._write_plist("alpha")
        r = self._set_schedule("alpha", "manual")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertFalse(os.path.isfile(self._plist_path("alpha")))
        self.assertIn("bootout", " ".join(self.fixture.launchctl_calls()))

    def test_regen_failure_warns_but_exits_zero(self):
        self._write_loop("alpha", "daily:09:00")
        # The regen writes root/dashboard/loops.html; a FILE named `dashboard`
        # makes its makedirs raise — a hermetic dashboard-generation failure.
        with open(os.path.join(self.root, "dashboard"), "w") as f:
            f.write("in the way")
        r = self._set_schedule("alpha", "interval:15m")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("warning: dashboard regen failed", r.stderr)
        self.assertIn("schedule alpha: daily:09:00 -> interval:15m", r.stdout)
        self.assertIn("schedule=interval:15m", _read(self._conf_path("alpha")))


class TestSetOwner(LoopsRootTestCase):
    """B-17: the set-schedule shape minus launchd — validate before write,
    rewrite the single key, best-effort dashboard regen, no events."""

    def _conf_path(self, name):
        return os.path.join(self.fixture.loop_dir(name), "loop.conf")

    def _set_owner(self, name, owner):
        return run_cli(
            ["set-owner", name, owner, "--root", self.root],
            env_overrides=self.fixture.base_env(),
        )

    def test_sets_owner_on_ownerless_conf(self):
        self.fixture.minimal_valid_loop("alpha")
        r = self._set_owner("alpha", "maguyva-marketing")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("owner alpha: loops (assumed) -> maguyva-marketing", r.stdout)
        conf = _read(self._conf_path("alpha"))
        self.assertIn("owner=maguyva-marketing", conf)
        # every other line survives byte-for-byte semantics of _rewrite_conf_key
        self.assertIn("schedule=interval:15m", conf)

    def test_rewrites_existing_owner_in_place(self):
        self.fixture.minimal_valid_loop("alpha", extra_lines=["owner=loops"])
        r = self._set_owner("alpha", "maguyva-marketing")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("owner alpha: loops -> maguyva-marketing", r.stdout)
        conf = _read(self._conf_path("alpha"))
        self.assertEqual(conf.count("owner="), 1)
        self.assertIn("owner=maguyva-marketing", conf)

    def test_malformed_owner_writes_nothing(self):
        self.fixture.minimal_valid_loop("alpha")
        before = _read(self._conf_path("alpha"))
        r = self._set_owner("alpha", "Bad Owner")
        self.assertEqual(r.returncode, 2, msg=r.stdout + r.stderr)
        self.assertIn("invalid owner", r.stderr)
        self.assertEqual(_read(self._conf_path("alpha")), before)

    def test_unknown_loop_fails(self):
        r = self._set_owner("ghost", "loops")
        self.assertEqual(r.returncode, 1)
        self.assertIn("loop not found", r.stderr)

    def test_records_no_lifecycle_event(self):
        # git history of loops.d/ is the audit trail (§5 owner resolution).
        self.fixture.minimal_valid_loop("alpha")
        r = self._set_owner("alpha", "maguyva-marketing")
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertEqual(_query_loop_events(self.root, "alpha"), [])


class TestOwnerListStatus(LoopsRootTestCase):
    """B-17: owner/owner_assumed in list/status rows; --owner exact filter."""

    def test_list_json_owner_fields(self):
        self.fixture.minimal_valid_loop(
            "explicit", extra_lines=["owner=maguyva-marketing"]
        )
        self.fixture.minimal_valid_loop("bare")
        r = run_cli(
            ["list", "--root", self.root, "--json"],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rows = {row["name"]: row for row in json.loads(r.stdout)}
        self.assertEqual(rows["explicit"]["owner"], "maguyva-marketing")
        self.assertFalse(rows["explicit"]["owner_assumed"])
        self.assertEqual(rows["bare"]["owner"], "loops")
        self.assertTrue(rows["bare"]["owner_assumed"])

    def test_list_table_owner_column(self):
        self.fixture.minimal_valid_loop("l1", extra_lines=["owner=maguyva-marketing"])
        r = run_cli(
            ["list", "--root", self.root], env_overrides=self.fixture.base_env()
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("owner", r.stdout)
        self.assertIn("maguyva-marketing", r.stdout)

    def test_list_owner_filter_exact_match(self):
        self.fixture.minimal_valid_loop("mine", extra_lines=["owner=maguyva-marketing"])
        self.fixture.minimal_valid_loop("bare")  # resolves to loops
        r = run_cli(
            ["list", "--owner", "loops", "--root", self.root, "--json"],
            env_overrides=self.fixture.base_env(),
        )
        rows = json.loads(r.stdout)
        self.assertEqual([row["name"] for row in rows], ["bare"])
        # no substring matching
        r = run_cli(
            ["list", "--owner", "maguyva", "--root", self.root, "--json"],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(json.loads(r.stdout), [])

    def test_list_owner_no_match_names_the_filter(self):
        self.fixture.minimal_valid_loop("l1")
        self.fixture.minimal_valid_loop("l2")
        r = run_cli(
            ["list", "--owner", "nobody", "--root", self.root],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertNotIn("empty)", r.stdout)
        self.assertIn(
            "0 loops matching --owner nobody (2 loops under loops.d)", r.stdout
        )

    def test_list_tag_and_owner_compose(self):
        self.fixture.minimal_valid_loop(
            "both", extra_lines=["owner=maguyva-marketing", 'tags="project:x"']
        )
        self.fixture.minimal_valid_loop("tag-only", extra_lines=['tags="project:x"'])
        r = run_cli(
            [
                "list",
                "--tag",
                "project:x",
                "--owner",
                "maguyva-marketing",
                "--root",
                self.root,
                "--json",
            ],
            env_overrides=self.fixture.base_env(),
        )
        rows = json.loads(r.stdout)
        self.assertEqual([row["name"] for row in rows], ["both"])
        # zero-match message names BOTH active filters
        r = run_cli(
            [
                "list",
                "--tag",
                "project:x",
                "--owner",
                "nobody",
                "--root",
                self.root,
            ],
            env_overrides=self.fixture.base_env(),
        )
        self.assertIn(
            "0 loops matching --tag project:x --owner nobody (2 loops under loops.d)",
            r.stdout,
        )

    def test_status_json_owner_fields(self):
        self.fixture.minimal_valid_loop("s1", extra_lines=["owner=maguyva-marketing"])
        self.fixture.minimal_valid_loop("s2")
        r = run_cli(["status", "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rows = {row["name"]: row for row in json.loads(r.stdout)["loops"]}
        self.assertEqual(rows["s1"]["owner"], "maguyva-marketing")
        self.assertFalse(rows["s1"]["owner_assumed"])
        self.assertEqual(rows["s2"]["owner"], "loops")
        self.assertTrue(rows["s2"]["owner_assumed"])


class TestNewOwner(LoopsRootTestCase):
    """B-17: `new` always stamps an explicit owner= (never scaffolds assumed)."""

    def _conf(self, name):
        with open(os.path.join(self.root, "loops.d", name, "loop.conf")) as f:
            return f.read()

    def test_new_stamps_default_owner(self):
        r = run_cli(
            ["new", "demo", "--root", self.root],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("owner=loops", self._conf("demo"))

    def test_new_honors_owner_flag(self):
        r = run_cli(
            ["new", "demo", "--owner", "maguyva-marketing", "--root", self.root],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("owner=maguyva-marketing", self._conf("demo"))

    def test_new_rejects_malformed_owner(self):
        r = run_cli(
            ["new", "demo", "--owner", "Bad Owner", "--root", self.root],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 2, msg=r.stdout + r.stderr)
        self.assertIn("invalid owner", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.root, "loops.d", "demo")))


class TestValidateOwnerNotices(LoopsRootTestCase):
    """B-17: assumed owner is a non-fatal notice — never an error, never
    the exit code."""

    def _add_spec(self, name):
        # minimal SPEC.md so validate passes its scaffold checks
        self.fixture.write_spec(name, "filled\n" * 11)

    def test_assumed_owner_is_ok_plus_notice(self):
        self.fixture.minimal_valid_loop("bare")
        self._add_spec("bare")
        r = run_cli(
            ["validate", "bare", "--root", self.root],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("OK bare", r.stdout)
        self.assertIn("note: owner assumed 'loops'", r.stdout)

    def test_assumed_owner_notice_in_json(self):
        self.fixture.minimal_valid_loop("bare")
        self._add_spec("bare")
        r = run_cli(
            ["validate", "bare", "--root", self.root, "--json"],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        payload = json.loads(r.stdout)["bare"]
        self.assertTrue(payload["ok"])
        self.assertEqual(len(payload["notices"]), 1)
        self.assertIn("owner assumed", payload["notices"][0])

    def test_explicit_owner_no_notice(self):
        self.fixture.minimal_valid_loop("owned", extra_lines=["owner=loops"])
        self._add_spec("owned")
        r = run_cli(
            ["validate", "owned", "--root", self.root, "--json"],
            env_overrides=self.fixture.base_env(),
        )
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        payload = json.loads(r.stdout)["owned"]
        self.assertEqual(payload["notices"], [])


if __name__ == "__main__":
    unittest.main()
