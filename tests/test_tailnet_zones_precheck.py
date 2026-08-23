"""tailnet-zones precheck against a fake sysadmin-tailnet tar probe."""
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PRECHECK = os.path.join(REPO, "loops.d", "tailnet-zones", "precheck.sh")
BIN_FILES = (
    "probe",
    "probe_core.py",
    "loopconf.py",
    "requirements.py",
    "schedule.py",
)


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


def _header(name):
    return (
        "#!/usr/bin/env bash\n"
        f"# probe: {name}\n"
        "# probe-writes: none\n"
        "# probe-output: tar\n"
        "# probe-reads: fixture\n"
        'if [ "${1:-}" = "--check" ]; then echo "ok '
        + name
        + '"; exit 0; fi\n'
    )


def _build_tar(path):
    tree = path + ".tree"
    os.makedirs(os.path.join(tree, "docs"))
    os.makedirs(os.path.join(tree, "site"))
    with open(os.path.join(tree, "docs", "policy-live.hujson"), "w") as f:
        f.write("{}\n")
    with open(os.path.join(tree, "site", "zones-meta.json"), "w") as f:
        f.write("{}\n")
    with tarfile.open(path, "w") as tar:
        tar.add(
            os.path.join(tree, "docs", "policy-live.hujson"),
            arcname="docs/policy-live.hujson",
        )
        tar.add(
            os.path.join(tree, "site", "zones-meta.json"),
            arcname="site/zones-meta.json",
        )


class TailnetZonesPrecheckTests(unittest.TestCase):
    def test_precheck_reaches_build_model_with_extracted_paths(self):
        with tempfile.TemporaryDirectory() as root:
            _copy_bin(root)
            tar_path = os.path.join(root, "sysadmin-tailnet.tar")
            _build_tar(tar_path)
            _write_exec(
                os.path.join(root, "probes", "sysadmin-tailnet"),
                _header("sysadmin-tailnet") + f'cat "{tar_path}"\n',
            )
            stub_dir = os.path.join(root, "loops.d", "tailnet-zones")
            os.makedirs(stub_dir)
            stub = os.path.join(stub_dir, "build_model.py")
            with open(stub, "w") as f:
                f.write(
                    "#!/usr/bin/env python3\n"
                    "import sys\n"
                    "print('STUB_ARGV', ' '.join(sys.argv[1:]))\n"
                )
            out_dir = os.path.join(root, "state", "runs", "test-run")
            os.makedirs(out_dir, exist_ok=True)
            env = dict(
                os.environ,
                OUT_DIR=out_dir,
                LOOPS_ROOT=root,
                LOOP_NAME="tailnet-zones",
                RUN_ID="test-run",
                HOME=root,
                TS_POLICY_READ_TOKEN_FILE=os.path.join(root, "no-such-token"),
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
            self.assertIn("STUB_ARGV", proc.stdout)
            self.assertIn(os.path.join(out_dir, "remote", "docs", "policy-live.hujson"), proc.stdout)
            self.assertIn(os.path.join(out_dir, "remote", "site", "zones-meta.json"), proc.stdout)

    def test_missing_file_probe_exit_1_fails_precheck(self):
        with tempfile.TemporaryDirectory() as root:
            _copy_bin(root)
            _write_exec(
                os.path.join(root, "probes", "sysadmin-tailnet"),
                _header("sysadmin-tailnet")
                + 'echo "docs/policy-live.hujson" >&2\nexit 1\n',
            )
            out_dir = os.path.join(root, "state", "runs", "test-run")
            os.makedirs(out_dir, exist_ok=True)
            env = dict(
                os.environ,
                OUT_DIR=out_dir,
                LOOPS_ROOT=root,
                LOOP_NAME="tailnet-zones",
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
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
