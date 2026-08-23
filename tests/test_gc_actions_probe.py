"""gc-actions precheck + apply_tickets against fake tar/ticket-add probes."""
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PRECHECK = os.path.join(REPO, "loops.d", "gc-actions", "precheck.sh")
APPLY = os.path.join(REPO, "loops.d", "gc-actions", "bin", "apply_tickets.py")
BIN_FILES = (
    "probe",
    "probe_core.py",
    "loopconf.py",
    "requirements.py",
    "schedule.py",
)

ACTION_SOURCES = """\
sources:
  - id: cro
    status: onboarded
    prefix: CRO
"""

REGISTER = """\
- id: CRO-01
  title: Example CRO action
  status: open
"""

MAP_YAML = """\
- row: 1
  disposition: uncovered
  ids: CRO-01
"""

BOARD_EMPTY = """\
# PRODUCT_BACKLOG

## Ideas
"""

BOARD_COVERED = """\
# PRODUCT_BACKLOG

## Ideas

### TKT-1: Cover CRO-01 with spaces

CRO-01 was ticketed.
"""


def _copy_bin(root):
    dest = os.path.join(root, "bin")
    os.makedirs(dest, exist_ok=True)
    for name in BIN_FILES:
        shutil.copy(os.path.join(REPO, "bin", name), os.path.join(dest, name))
    os.chmod(os.path.join(dest, "probe"), 0o755)


def _write_exec(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(body)
        if not body.endswith("\n"):
            f.write("\n")
    os.chmod(path, 0o755)


def _header(name, output, writes="none"):
    return (
        "#!/usr/bin/env bash\n"
        f"# probe: {name}\n"
        f"# probe-writes: {writes}\n"
        f"# probe-output: {output}\n"
        "# probe-reads: fixture\n"
        'if [ "${1:-}" = "--check" ]; then echo "ok '
        + name
        + '"; exit 0; fi\n'
    )


def _build_dmp_tar(path):
    tree = path + ".tree"
    run = os.path.join(tree, "2026-08-01-1200-cro")
    os.makedirs(os.path.join(run, "actions"))
    with open(os.path.join(run, "actions", "register.yaml"), "w") as f:
        f.write(REGISTER)
    with tarfile.open(path, "w") as tar:
        tar.add(
            os.path.join(run, "actions", "register.yaml"),
            arcname="2026-08-01-1200-cro/actions/register.yaml",
        )


def _build_gc_tar(path, board):
    tree = path + ".tree"
    os.makedirs(tree, exist_ok=True)
    with open(os.path.join(tree, "action-sources.yaml"), "w") as f:
        f.write(ACTION_SOURCES)
    with open(os.path.join(tree, "action-ticket-map.yaml"), "w") as f:
        f.write(MAP_YAML)
    with open(os.path.join(tree, "PRODUCT_BACKLOG.md"), "w") as f:
        f.write(board)
    with tarfile.open(path, "w") as tar:
        for name in (
            "action-sources.yaml",
            "action-ticket-map.yaml",
            "PRODUCT_BACKLOG.md",
        ):
            tar.add(os.path.join(tree, name), arcname=name)


def _install_tar_probe(root, name, tar_path):
    body = _header(name, "tar") + f'cat "{tar_path}"\n'
    _write_exec(os.path.join(root, "probes", name), body)


def _install_ticket_add(root, mode="ok"):
    log = os.path.join(root, "ticket-add.log")
    if mode == "ok":
        script = f"""#!/usr/bin/env python3
# probe: ticket-add
# probe-writes: one ticket via tickets-cli add
# probe-output: text
# probe-reads: fixture
import base64, json, sys
if sys.argv[1:] == ["--check"]:
    print("ok ticket-add")
    raise SystemExit(0)
raw = sys.argv[1]
pad = "=" * ((4 - len(raw) % 4) % 4)
obj = json.loads(base64.urlsafe_b64decode(raw + pad))
with open({log!r}, "a") as f:
    f.write(json.dumps(obj) + "\\n")
print("ticket created")
"""
    else:
        script = """#!/usr/bin/env python3
# probe: ticket-add
# probe-writes: one ticket via tickets-cli add
# probe-output: text
# probe-reads: fixture
import sys
if sys.argv[1:] == ["--check"]:
    print("ok ticket-add")
    raise SystemExit(0)
print("probe transport failed (llm unreachable)", file=sys.stderr)
raise SystemExit(75)
"""
    _write_exec(os.path.join(root, "probes", "ticket-add"), script)
    return log


def _latest_json(path, title="Cover CRO-01 with spaces"):
    op = {
        "op": "create_ticket",
        "action_ids": ["CRO-01"],
        "title": title,
        "priority": "high",
        "description": "[loop:gc-actions | CRO-01] ticket this action",
    }
    contract = {
        "findings": [
            {
                "finding_id": "gap:CRO-01",
                "detail": "please create:\n```json\n"
                + json.dumps(op)
                + "\n```\n",
            }
        ]
    }
    with open(path, "w") as f:
        json.dump(contract, f)


class GcActionsProbeTests(unittest.TestCase):
    def test_precheck_digest_mentions_register_count(self):
        with tempfile.TemporaryDirectory() as root:
            _copy_bin(root)
            dmp_tar = os.path.join(root, "dmp.tar")
            gc_tar = os.path.join(root, "gc.tar")
            _build_dmp_tar(dmp_tar)
            _build_gc_tar(gc_tar, BOARD_EMPTY)
            _install_tar_probe(root, "dmp-actions", dmp_tar)
            _install_tar_probe(root, "gc-actions-files", gc_tar)
            out_dir = os.path.join(root, "state", "runs", "test-run")
            os.makedirs(out_dir, exist_ok=True)
            env = dict(
                os.environ,
                OUT_DIR=out_dir,
                LOOPS_ROOT=root,
                LOOP_NAME="gc-actions",
                RUN_ID="test-run",
                HOME=root,
            )
            env.pop("LOOPS_PROBE_HOST", None)
            proc = subprocess.run(
                ["bash", PRECHECK],
                capture_output=True,
                text=True,
                env=env,
                cwd=os.path.dirname(PRECHECK),
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("registers_scanned:", proc.stdout)
            self.assertIn("CRO-01", proc.stdout)

    def test_apply_tickets_payload_keeps_spaces_and_prefix(self):
        with tempfile.TemporaryDirectory() as root:
            _copy_bin(root)
            gc_tar = os.path.join(root, "gc.tar")
            _build_gc_tar(gc_tar, BOARD_EMPTY)
            _install_tar_probe(root, "gc-actions-files", gc_tar)
            log = _install_ticket_add(root, "ok")
            latest = os.path.join(root, "latest.json")
            _latest_json(latest)
            env = dict(os.environ, LOOPS_ROOT=root, LATEST_JSON=latest, HOME=root)
            env.pop("LOOPS_PROBE_HOST", None)
            proc = subprocess.run(
                [sys.executable, APPLY],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue(os.path.isfile(log))
            payload = json.loads(Path(log).read_text().splitlines()[0])
            self.assertEqual(payload["title"], "Cover CRO-01 with spaces")
            self.assertEqual(payload["section"], "ideas")
            self.assertTrue(payload["description"].startswith("[loop:gc-actions | "))

            _build_gc_tar(gc_tar, BOARD_COVERED)
            proc2 = subprocess.run(
                [sys.executable, APPLY],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertEqual(proc2.returncode, 0, proc2.stdout + proc2.stderr)
            self.assertIn("already covered — skipped", proc2.stdout)

    def test_ticket_add_exit_75_counts_failed(self):
        with tempfile.TemporaryDirectory() as root:
            _copy_bin(root)
            gc_tar = os.path.join(root, "gc.tar")
            _build_gc_tar(gc_tar, BOARD_EMPTY)
            _install_tar_probe(root, "gc-actions-files", gc_tar)
            _install_ticket_add(root, "fail75")
            latest = os.path.join(root, "latest.json")
            _latest_json(latest)
            env = dict(os.environ, LOOPS_ROOT=root, LATEST_JSON=latest, HOME=root)
            env.pop("LOOPS_PROBE_HOST", None)
            proc = subprocess.run(
                [sys.executable, APPLY],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
            self.assertNotEqual(proc.returncode, 0)
            combined = proc.stdout + proc.stderr
            self.assertIn("failed 1", combined)
            self.assertIn("transport", combined)


if __name__ == "__main__":
    unittest.main()
