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

REPO_ROOT = Path(__file__).resolve().parent.parent
LOOPCTL = REPO_ROOT / "bin" / "loopctl"
DB_PY = REPO_ROOT / "bin" / "db.py"

FAKE_LAUNCHCTL_SRC = '''#!/usr/bin/env python3
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
# run row, so loopctl's post-kickstart poll (§8.1 step 4) has something real
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
'''

FAKE_RUN_LOOP_SRC = '''#!/usr/bin/env bash
echo "FAKE_RUN_LOOP_SH called with: $@"
exit "${FAKE_RUN_LOOP_EXIT:-0}"
'''


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
    )


def run_db(args):
    return subprocess.run(
        [sys.executable, str(DB_PY)] + args, capture_output=True, text=True
    )


def _query_last_runs(root, loop_name, limit=1):
    r = run_db(["query", "last-runs", "--root", root, "--loop", loop_name, "--limit", str(limit)])
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

    def minimal_valid_loop(self, name, extra_lines=None, from_dir="loops.d", type_="agent"):
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

    def add_run(self, run_id, loop_name, started_at, runner_status="completed",
                finished_at=None, effective_status="ok", headline="ok"):
        r = run_db([
            "start-run", "--root", self.root, "--run-id", run_id, "--loop", loop_name,
            "--engine", "codex", "--trigger", "manual", "--started-at", started_at,
        ])
        assert r.returncode == 0, r.stderr
        finish_args = [
            "finish-run", "--root", self.root, "--run-id", run_id,
            "--runner-status", runner_status,
            "--headline", headline, "--finished-at", finished_at or started_at,
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
        for fname in ("loop.conf", "prompt.md", "precheck.sh", "dashboard.json", "SPEC.md"):
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
        run_cli(["new", "watch-me", "--root", self.root, "--type", "watchdog", "--engine", "claude"])
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

    def test_scaffold_spec_has_11_sections_in_order(self):
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
        ]
        positions = [text.index(h) for h in headers]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(text.count("[FILL:"), 11)


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
        self.fixture.minimal_valid_loop("bad2", extra_lines=["perm_remote_mutation=allowlist",
                                                               'exec_allowlist="git status"'])
        self.fixture.write_spec("bad2", "filled\n" * 11)
        r = self._validate("bad2")
        errors = json.loads(r.stdout)["bad2"]["errors"]
        self.assertTrue(any("rule 2" in e or "remote_mutation_justification" in e for e in errors), errors)

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
            extra_lines=["perm_fs_write=workdir", 'notes="needs to write into workdir because X"'],
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
            any("rule 8" in e and "credential_env" in e and "reserved" in e for e in errors),
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
        self.assertTrue(any("rule 6" in e and "engine adapter" in e for e in errors), errors)

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
        self.assertTrue(any("rule 6" in e and "directory name" in e for e in errors), errors)

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
        r = run_cli(["run", "hello-loop", "--root", self.root],
                     env_overrides={"FAKE_RUN_LOOP_EXIT": "0"})
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertIn("FAKE_RUN_LOOP_SH called", r.stdout)

    def test_propagates_nonzero_exit(self):
        r = run_cli(["run", "hello-loop", "--root", self.root],
                     env_overrides={"FAKE_RUN_LOOP_EXIT": "7"})
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
            f.write(
                "#!/usr/bin/env bash\n"
                f'echo "$LOOPS_ROOT" > "{recorder}"\n'
                "exit 0\n"
            )
        os.chmod(self.fixture.run_loop_sh, 0o755)

        env = os.environ.copy()
        env.pop("LOOPS_ROOT", None)  # simulate a shell that never exported it
        env["FAKE_RUN_LOOP_EXIT"] = "0"
        r = subprocess.run(
            [sys.executable, str(LOOPCTL), "run", "hello-loop", "--root", self.root],
            capture_output=True,
            text=True,
            env=env,
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
        text = _read(conf_path).replace("schedule=interval:15m", "schedule=interval:30m")
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
        text = _read(conf_path).replace("schedule=interval:15m", "schedule=times:07:30,19:30")
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
        text = _read(conf_path).replace("schedule=interval:15m", "schedule=weekly:mon:08:00")
        with open(conf_path, "w") as f:
            f.write(text)
        self.fixture.write_spec("wk", "filled\n" * 11)
        self._run_install("wk")
        plist = _read_plist(os.path.join(self.root, "launchd", "com.loops.wk.plist"))
        self.assertEqual(plist["StartCalendarInterval"], {"Hour": 8, "Minute": 0, "Weekday": 1})

    def test_monthly_schedule(self):
        self.fixture.minimal_valid_loop("mo", extra_lines=[])
        conf_path = os.path.join(self.fixture.loop_dir("mo"), "loop.conf")
        text = _read(conf_path).replace("schedule=interval:15m", "schedule=monthly:01:09:00")
        with open(conf_path, "w") as f:
            f.write(text)
        self.fixture.write_spec("mo", "filled\n" * 11)
        self._run_install("mo")
        plist = _read_plist(os.path.join(self.root, "launchd", "com.loops.mo.plist"))
        self.assertEqual(plist["StartCalendarInterval"], {"Hour": 9, "Minute": 0, "Day": 1})

    def test_plist_has_absolute_paths_and_env(self):
        self.fixture.minimal_valid_loop("iv2", extra_lines=[])
        self.fixture.write_spec("iv2", "filled\n" * 11)
        self._run_install("iv2")
        plist = _read_plist(os.path.join(self.root, "launchd", "com.loops.iv2.plist"))
        self.assertTrue(os.path.isabs(plist["ProgramArguments"][1]))
        self.assertTrue(os.path.isabs(plist["WorkingDirectory"]))
        self.assertIn("HOME", plist["EnvironmentVariables"])
        self.assertIn("PATH", plist["EnvironmentVariables"])
        self.assertEqual(plist["EnvironmentVariables"]["LOOPS_ROOT"], os.path.abspath(self.root))
        self.assertTrue(plist["StandardOutPath"].startswith(os.path.join(self.root, "state")))
        self.assertTrue(plist["StandardErrorPath"].startswith(os.path.join(self.root, "state")))


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
        r = run_cli(["install", name, "--root", self.root], env_overrides=self.fixture.base_env())
        self.assertEqual(r.returncode, 1)
        self.assertIn("manual", r.stderr)
        self.assertEqual(self.fixture.launchctl_calls(), [])

    def test_refuses_invalid_loop(self):
        name = self._valid_loop("invalid-one")
        self.fixture.write_spec(name, "1. Purpose\n[FILL: still here]\n")
        r = run_cli(["install", name, "--root", self.root], env_overrides=self.fixture.base_env())
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.fixture.launchctl_calls(), [])

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
        self.assertTrue(os.path.isfile(os.path.join(self.root, "launchd", f"com.loops.{name}.plist")))
        # the freshly-inserted row, not the stale one, is what verified install
        rows = _query_last_runs(self.root, name)
        self.assertNotEqual(rows[0]["run_id"], stale_run_id)

    def test_bootstrap_failure_aborts(self):
        name = self._valid_loop("bootstrap-fails")
        env = self.fixture.base_env(FAKE_LAUNCHCTL_BOOTSTRAP_EXIT="1")
        r = run_cli(["install", name, "--root", self.root], env_overrides=env)
        self.assertEqual(r.returncode, 1)
        calls = self.fixture.launchctl_calls()
        verbs = [c.split()[0] for c in calls]
        self.assertEqual(verbs, ["bootout", "bootstrap"])  # never reaches kickstart

    def test_kickstart_failure_aborts_and_boots_out(self):
        name = self._valid_loop("kickstart-fails")
        env = self.fixture.base_env(FAKE_LAUNCHCTL_KICKSTART_EXIT="1")
        r = run_cli(["install", name, "--root", self.root], env_overrides=env)
        self.assertEqual(r.returncode, 1)
        calls = self.fixture.launchctl_calls()
        verbs = [c.split()[0] for c in calls]
        self.assertEqual(verbs, ["bootout", "bootstrap", "kickstart", "bootout"])

    def test_no_fresh_run_row_fails_and_boots_out(self):
        name = self._valid_loop("no-fresh-run")
        # No run rows at all inserted -> poll must time out.
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
        r = run_cli(["uninstall", name, "--root", self.root], env_overrides=self.fixture.base_env())
        self.assertEqual(r.returncode, 0)
        self.assertFalse(os.path.isfile(plist_path))
        calls = self.fixture.launchctl_calls()
        self.assertEqual([c.split()[0] for c in calls], ["bootout"])

    def test_uninstall_with_no_plist_still_succeeds(self):
        r = run_cli(["uninstall", "never-installed", "--root", self.root],
                     env_overrides=self.fixture.base_env())
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

        r = run_cli(["pause", "fresh-toggle", "--root", self.root], env_overrides=self.fixture.base_env())
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

        r = run_cli(["pause", name, "--root", self.root], env_overrides=self.fixture.base_env())
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
        r = run_cli(["resume", name, "--root", self.root], env_overrides=self.fixture.base_env())
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
        with open(os.path.join(self.root, "launchd", f"com.loops.{name}.plist"), "wb") as f:
            f.write(b"<plist/>")
        r = run_cli(["pause", name, "--root", self.root], env_overrides=self.fixture.base_env())
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
        with open(os.path.join(self.root, "launchd", f"com.loops.{name}.plist"), "wb") as f:
            f.write(b"<plist/>")
        r = run_cli(["resume", name, "--root", self.root], env_overrides=self.fixture.base_env())
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
        r = run_db(["start-run", "--root", self.root, "--run-id", run_id, "--loop", loop_name,
                     "--engine", "codex", "--trigger", "manual", "--started-at", started])
        self.assertEqual(r.returncode, 0, r.stderr)

        contract = {
            "schema_version": 1, "run_id": run_id, "status": "alert",
            "status_reason": "x", "headline": "x", "report_markdown": "x",
            "metrics": "{}",
            "findings": [{"finding_id": finding_id, "title": "svc down", "severity": "alert", "detail": "d"}],
        }
        contract_path = os.path.join(self.root, "state", f"{run_id}.contract.json")
        with open(contract_path, "w") as f:
            json.dump(contract, f)
        r = run_db(["upsert-findings", "--root", self.root, "--run-id", run_id, "--loop", loop_name,
                     "--contract-file", contract_path, "--ts", started])
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
        r = run_cli(["dismiss", name, "svc:down", "--note", "known issue", "--root", self.root])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)

        rq = run_db(["query", "open-findings", "--root", self.root, "--loop", name])
        self.assertEqual(rq.returncode, 0)
        # finding still open in findings table (dismiss suppresses, doesn't resolve)
        rows = json.loads(rq.stdout)
        self.assertEqual(len(rows), 1)

        rsup = run_db(["suppressed", "--root", self.root, "--loop", name, "--ts", iso(datetime.now(timezone.utc))])
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
        rsup = run_db(["suppressed", "--root", self.root, "--loop", name, "--ts", iso(datetime.now(timezone.utc))])
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
        rsup = run_db(["suppressed", "--root", self.root, "--loop", name, "--ts", iso(datetime.now(timezone.utc))])
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
        run_db(["start-run", "--root", self.root, "--run-id", run_id, "--loop", name,
                "--engine", "codex", "--trigger", "manual", "--started-at", started])
        contract = {
            "schema_version": 1, "run_id": run_id, "status": "warn",
            "status_reason": "x", "headline": "x", "report_markdown": "x", "metrics": "{}",
            "findings": [{"finding_id": "repo:dirty", "title": "dirty repo", "severity": "warn", "detail": "d"}],
        }
        contract_path = os.path.join(self.root, "state", f"{run_id}.contract.json")
        with open(contract_path, "w") as f:
            json.dump(contract, f)
        run_db(["upsert-findings", "--root", self.root, "--run-id", run_id, "--loop", name,
                "--contract-file", contract_path, "--ts", started])

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


# ---------------------------------------------------------------------------
# loopctl list / status
# ---------------------------------------------------------------------------

class TestListStatus(LoopsRootTestCase):
    def test_list_json_shape(self):
        self.fixture.minimal_valid_loop("l1")
        self.fixture.minimal_valid_loop("l2", type_="watchdog")
        r = run_cli(["list", "--root", self.root, "--json"],
                     env_overrides=self.fixture.base_env())
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rows = {row["name"]: row for row in json.loads(r.stdout)}
        self.assertEqual(set(rows), {"l1", "l2"})
        self.assertEqual(rows["l1"]["engine"], "codex")
        self.assertEqual(rows["l2"]["type"], "watchdog")
        self.assertIn("installed", rows["l1"])
        self.assertFalse(rows["l1"]["installed"])

    def test_list_human_table(self):
        self.fixture.minimal_valid_loop("l3")
        r = run_cli(["list", "--root", self.root], env_overrides=self.fixture.base_env())
        self.assertEqual(r.returncode, 0)
        self.assertIn("l3", r.stdout)
        self.assertIn("name", r.stdout)

    def test_status_single_loop_no_runs(self):
        self.fixture.minimal_valid_loop("s1")
        r = run_cli(["status", "s1", "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rows = json.loads(r.stdout)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["runner_status"])

    def test_status_single_loop_with_run(self):
        name = "s2"
        self.fixture.minimal_valid_loop(name)
        self.fixture.add_run(f"20260722T000000Z-{name}-c1", name, iso(datetime.now(timezone.utc)),
                              headline="all good")
        r = run_cli(["status", name, "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        rows = json.loads(r.stdout)
        self.assertEqual(rows[0]["headline"], "all good")
        self.assertEqual(rows[0]["runner_status"], "completed")

    def test_status_all_loops(self):
        self.fixture.minimal_valid_loop("s3")
        self.fixture.minimal_valid_loop("s4")
        r = run_cli(["status", "--root", self.root, "--json"])
        self.assertEqual(r.returncode, 0)
        rows = json.loads(r.stdout)
        self.assertEqual({row["name"] for row in rows}, {"s3", "s4"})


if __name__ == "__main__":
    unittest.main()
