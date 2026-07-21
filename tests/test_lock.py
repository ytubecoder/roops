"""Tests for bin/lock.py — §2 fcntl lock helper."""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN = REPO_ROOT / "bin" / "lock.py"


def run(args, **kwargs):
    return subprocess.run(
        [sys.executable, str(BIN)] + args,
        capture_output=True,
        text=True,
        **kwargs,
    )


class TestLock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="loops-lock-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def lock_path(self):
        return Path(self.tmp) / "state" / "locks" / "myloop.lock"

    def spawn_holder(self):
        """Start a holder process that has acquired the lock and is
        blocked on stdin. Registers cleanup to release + close pipes."""
        proc = subprocess.Popen(
            [sys.executable, str(BIN), "acquire", "--name", "myloop", "--root", self.tmp],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def cleanup():
            if proc.poll() is None:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
                try:
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
                    proc.wait()
            proc.stdout.close()
            proc.stderr.close()

        self.addCleanup(cleanup)
        line = proc.stdout.readline()
        assert line.strip() == "ACQUIRED", f"holder failed to acquire: {line!r}"
        return proc

    def release_holder(self, proc):
        proc.stdin.close()
        proc.wait(timeout=5)

    def test_check_free_exits_0(self):
        proc = run(["check", "--name", "myloop", "--root", self.tmp])
        self.assertEqual(proc.returncode, 0)

    def test_acquire_prints_acquired_and_creates_file(self):
        proc = self.spawn_holder()
        self.assertTrue(self.lock_path().exists())
        mode = self.lock_path().stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
        self.release_holder(proc)
        self.assertEqual(proc.returncode, 0)

    def test_contention_exits_3_and_prints_held_by(self):
        holder = self.spawn_holder()
        proc = run(["acquire", "--name", "myloop", "--root", self.tmp])
        self.assertEqual(proc.returncode, 3)
        self.assertIn("HELD_BY", proc.stderr)
        self.assertIn(str(holder.pid), proc.stderr)

    def test_check_held_exits_3(self):
        self.spawn_holder()
        proc = run(["check", "--name", "myloop", "--root", self.tmp])
        self.assertEqual(proc.returncode, 3)
        self.assertIn("HELD_BY", proc.stderr)

    def test_check_never_modifies_lock_file(self):
        run(["check", "--name", "myloop", "--root", self.tmp])
        self.assertFalse(self.lock_path().exists())

    def test_wait_s_retries_then_succeeds(self):
        holder = self.spawn_holder()

        def release_after_delay():
            time.sleep(0.6)
            holder.stdin.close()

        t = threading.Thread(target=release_after_delay)
        t.start()

        start = time.time()
        proc = run(["acquire", "--name", "myloop", "--root", self.tmp, "--wait-s", "3"],
                    input="")
        elapsed = time.time() - start
        t.join()
        holder.wait(timeout=5)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("ACQUIRED", proc.stdout)
        self.assertGreater(elapsed, 0.4)

    def test_wait_s_exhausted_exits_3(self):
        self.spawn_holder()
        start = time.time()
        proc = run(["acquire", "--name", "myloop", "--root", self.tmp, "--wait-s", "1"],
                    input="")
        elapsed = time.time() - start
        self.assertEqual(proc.returncode, 3)
        self.assertGreaterEqual(elapsed, 0.9)

    def test_stale_pid_takeover(self):
        # Simulate a stale lock: write a pid that does not exist.
        lockdir = Path(self.tmp) / "state" / "locks"
        lockdir.mkdir(parents=True, exist_ok=True)
        lockfile = lockdir / "myloop.lock"
        dead_pid = 999999
        try:
            os.kill(dead_pid, 0)
            self.skipTest("dead_pid unexpectedly alive")
        except OSError:
            pass
        lockfile.write_text(f"{dead_pid} 2020-01-01T00:00:00Z\n")
        lockfile.chmod(0o600)

        proc = run(["check", "--name", "myloop", "--root", self.tmp])
        self.assertEqual(proc.returncode, 0)

        proc2 = run(["acquire", "--name", "myloop", "--root", self.tmp], input="")
        self.assertEqual(proc2.returncode, 0)
        self.assertIn("ACQUIRED", proc2.stdout)

    def test_usage_error_exits_2(self):
        proc = run(["bogus-verb"])
        self.assertEqual(proc.returncode, 2)

    def test_lock_dir_created_with_mkdir_p(self):
        self.assertFalse((Path(self.tmp) / "state").exists())
        run(["check", "--name", "myloop", "--root", self.tmp])
        # check must not create the lock file, but acquire should create dir.
        proc = run(["acquire", "--name", "myloop", "--root", self.tmp], input="")
        self.assertEqual(proc.returncode, 0)
        self.assertTrue((Path(self.tmp) / "state" / "locks").is_dir())


if __name__ == "__main__":
    unittest.main()
