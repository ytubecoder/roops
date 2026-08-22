"""Tests for the probe channel — bin/probe-server, bin/probe, bin/probe_core.py.

Every test builds a temp root with bin/ copied from the repo and a canary
probe. The server is driven as a subprocess with SSH_ORIGINAL_COMMAND.
ssh is NEVER the real binary (LOOPS_SSH points at a fake). HOME is always
a temp dir for --authorize. Never touches the real ~/.ssh.
"""
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BIN_FILES = (
    "probe",
    "probe-server",
    "probe_core.py",
    "loopconf.py",
    "requirements.py",
    "schedule.py",  # loopconf.py loads it as a sibling
)

LOG_LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z "
    r"client=\S+ "
    r"verb=\S+ "
    r"args=\S.* "
    r"exit=-?\d+ "
    r"ms=\d+$"
)

REFUSE_CMDS = [
    "echo-test; rm -rf /",
    "echo-test $(id)",
    "echo-test `id`",
    "echo-test 'a'",
    'echo-test "a"',
    "echo-test a\nb",
    "../probes/echo-test",
    "canary-touch/../canary-touch",
    "",
    "nope-unknown",
    "echo-test " + " ".join(["a"] * 9),
    "echo-test " + ("a" * 8193),
    "echo-test  a",
    " echo-test",
]


def _load(path, modname):
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


probe_core = _load(str(REPO_ROOT / "bin" / "probe_core.py"), "_test_probe_core")
requirements = _load(str(REPO_ROOT / "bin" / "requirements.py"), "_test_probe_req")


PROBE_HEADER = """\
#!/usr/bin/env {interp}
# probe: {name}
# probe-timeout-s: {timeout}
# probe-writes: {writes}
# probe-output: {output}
# probe-reads: {reads}
"""


def _hash12(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()[:12]


def _write_exec(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(body)
        if not body.endswith("\n"):
            f.write("\n")
    os.chmod(path, 0o755)


def _header(name, timeout=120, writes="none", output="text", reads="none", interp="bash"):
    return PROBE_HEADER.format(
        interp=interp,
        name=name,
        timeout=timeout,
        writes=writes,
        output=output,
        reads=reads,
    )


class ProbeRoot:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="loops-probe-")
        self.home = tempfile.mkdtemp(prefix="loops-probe-home-")
        os.makedirs(os.path.join(self.root, "bin"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "probes"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "state"), exist_ok=True)
        for name in BIN_FILES:
            src = REPO_ROOT / "bin" / name
            dst = os.path.join(self.root, "bin", name)
            shutil.copy(src, dst)
            if name in ("probe", "probe-server"):
                os.chmod(dst, 0o755)
        echo_src = REPO_ROOT / "probes" / "echo-test"
        echo_dst = os.path.join(self.root, "probes", "echo-test")
        shutil.copy(echo_src, echo_dst)
        os.chmod(echo_dst, 0o755)
        self.write_probe(
            "canary-touch",
            _header("canary-touch")
            + 'touch "$LOOPS_ROOT/CANARY"\n',
        )

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    @property
    def server(self):
        return os.path.join(self.root, "bin", "probe-server")

    @property
    def client(self):
        return os.path.join(self.root, "bin", "probe")

    def write_probe(self, name, body):
        path = os.path.join(self.root, "probes", name)
        _write_exec(path, body)
        return path

    def write_env(self, text):
        path = os.path.join(self.root, ".env")
        with open(path, "w") as f:
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")
        return path

    def canary_path(self):
        return os.path.join(self.root, "CANARY")

    def log_dir(self):
        return os.path.join(self.root, "state", "probe-log")

    def latest_log(self):
        d = self.log_dir()
        if not os.path.isdir(d):
            return None
        files = sorted(os.listdir(d))
        if not files:
            return None
        return os.path.join(d, files[-1])

    def read_log(self):
        path = self.latest_log()
        if path is None:
            return ""
        with open(path) as f:
            return f.read()

    def base_env(self, **extra):
        env = os.environ.copy()
        env.pop("LOOPS_PROBE_HOST", None)
        env.pop("LOOPS_PROBE_KEY", None)
        env.pop("LOOPS_SSH", None)
        env.pop("LOOPS_SSH_KEYGEN", None)
        env.pop("SSH_ORIGINAL_COMMAND", None)
        env["HOME"] = self.home
        env["LOOPS_ROOT"] = self.root
        env.update(extra)
        return env

    def run_server(self, cmd, env_extra=None, timeout=30):
        env = self.base_env()
        if cmd is None:
            env.pop("SSH_ORIGINAL_COMMAND", None)
        else:
            env["SSH_ORIGINAL_COMMAND"] = cmd
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, self.server],
            cwd=self.root,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            check=False,
        )

    def run_server_argv(self, argv, env_extra=None, timeout=30):
        env = self.base_env()
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, self.server] + argv,
            cwd=self.root,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            check=False,
        )

    def run_client(self, argv, env_extra=None, timeout=30):
        env = self.base_env()
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [sys.executable, self.client] + argv,
            cwd=self.root,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
            check=False,
        )


class ProbeTestCase(unittest.TestCase):
    def setUp(self):
        self.fx = ProbeRoot()
        self.addCleanup(self.fx.cleanup)


class TestServerRefusals(ProbeTestCase):
    def test_server_refuses_shell_metacharacters_and_executes_nothing(self):
        for cmd in REFUSE_CMDS:
            with self.subTest(cmd=cmd):
                if os.path.isfile(self.fx.canary_path()):
                    os.remove(self.fx.canary_path())
                before = self.fx.read_log()
                r = self.fx.run_server(cmd)
                self.assertEqual(r.returncode, 64, msg=repr(cmd) + r.stderr)
                self.assertTrue(
                    r.stderr.startswith("refused:"),
                    msg=repr(cmd) + " stderr=" + r.stderr,
                )
                self.assertFalse(
                    os.path.isfile(self.fx.canary_path()),
                    msg="CANARY present after " + repr(cmd),
                )
                after = self.fx.read_log()
                new_lines = after[len(before) :].strip().splitlines()
                self.assertTrue(new_lines, msg="no log line for " + repr(cmd))
                last = new_lines[-1]
                self.assertRegex(last, LOG_LINE_RE)
                self.assertIn("exit=64", last)


class TestServerExec(ProbeTestCase):
    def test_server_runs_probe_with_clean_env_and_args(self):
        body = (
            _header("echo-test", interp="python3")
            + "import json, os, sys\n"
            + "data = {'argv': sys.argv[1:], 'env': dict(os.environ), "
            + "'stdin': sys.stdin.read()}\n"
            + "print(json.dumps(data))\n"
        )
        self.fx.write_probe("echo-test", body)
        self.fx.write_env("FIXTURE_KEY=from-env\n")
        r = self.fx.run_server(
            "echo-test a b=c",
            env_extra={
                "SSH_ORIGINAL_COMMAND": "echo-test a b=c",
                "SECRET": "1",
                "PATH": "/nope",
            },
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        payload = json.loads(r.stdout)
        env = payload["env"]
        self.assertEqual(payload["argv"], ["a", "b=c"])
        self.assertEqual(payload["stdin"], "")
        self.assertEqual(env.get("LOOPS_ROOT"), os.path.realpath(self.fx.root))
        self.assertTrue(env.get("HOME"))
        home = env["HOME"]
        self.assertEqual(env.get("PATH"), requirements.runtime_path(home))
        self.assertEqual(env.get("FIXTURE_KEY"), "from-env")
        self.assertNotIn("SECRET", env)
        self.assertNotIn("SSH_ORIGINAL_COMMAND", env)

    def test_server_builtins(self):
        r = self.fx.run_server("ping")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        host = probe_core.hostname()
        self.assertEqual(r.stdout.strip(), f"ok probe-server 1 {host}")

        r = self.fx.run_server("list")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        names = {ln.split()[0]: ln.split()[1] for ln in lines}
        self.assertIn("echo-test", names)
        echo = os.path.join(self.fx.root, "probes", "echo-test")
        self.assertEqual(names["echo-test"], _hash12(echo))
        self.assertEqual(len(names["echo-test"]), 12)

        r = self.fx.run_server("check echo-test")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("ok echo-test", r.stdout)

        r = self.fx.run_server("ping x")
        self.assertEqual(r.returncode, 64)
        self.assertTrue(r.stderr.startswith("refused:"))

    def test_server_refuses_symlink_and_bad_header(self):
        echo = os.path.join(self.fx.root, "probes", "echo-test")
        link = os.path.join(self.fx.root, "probes", "sym-probe")
        os.symlink(echo, link)
        os.chmod(echo, 0o755)
        r = self.fx.run_server("sym-probe")
        self.assertEqual(r.returncode, 64, msg=r.stderr)
        self.assertTrue(r.stderr.startswith("refused:"))

        self.fx.write_probe(
            "no-output",
            "#!/usr/bin/env bash\n"
            "# probe: no-output\n"
            "# probe-writes: none\n"
            "# probe-reads: none\n"
            "echo hi\n",
        )
        r = self.fx.run_server("no-output")
        self.assertEqual(r.returncode, 64, msg=r.stderr)
        self.assertIn("refused: bad header:", r.stderr)

        self.fx.write_probe(
            "wrong-name",
            _header("echo-test") + "echo hi\n",
        )
        r = self.fx.run_server("wrong-name")
        self.assertEqual(r.returncode, 64, msg=r.stderr)
        self.assertIn("refused: bad header:", r.stderr)

        self.fx.write_probe(
            "list",
            _header("list") + "echo should-not-run\n",
        )
        r = self.fx.run_server("check list")
        self.assertEqual(r.returncode, 64, msg=r.stderr)
        self.assertIn("refused: reserved name", r.stderr)
        r = self.fx.run_server("list")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        listed = [ln.split()[0] for ln in r.stdout.splitlines() if ln.strip()]
        self.assertNotIn("list", listed)

        delayed = (
            "#!/usr/bin/env bash\n"
            "# probe: delayed-timeout\n"
            "# probe-writes: none\n"
            "# probe-output: text\n"
            "# probe-reads: none\n"
            "# ordinary comment, not a header line\n"
            "# probe-timeout-s: 1\n"
            "echo hi\n"
        )
        path = self.fx.write_probe("delayed-timeout", delayed)
        header = probe_core.parse_header(path)
        self.assertEqual(header.timeout_s, 120)

    def test_server_timeout_kills_process_group(self):
        body = (
            _header("hang-group", timeout=1, interp="python3")
            + "import os, signal, sys, time\n"
            + "if sys.argv[1:] == ['--check']:\n"
            + "    print('ok hang-group')\n"
            + "    sys.exit(0)\n"
            + "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            + "child = os.fork()\n"
            + "if child == 0:\n"
            + "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            + "    time.sleep(30)\n"
            + "    os._exit(0)\n"
            + "root = os.environ['LOOPS_ROOT']\n"
            + "with open(os.path.join(root, 'hang.pids'), 'w') as f:\n"
            + "    f.write(str(os.getpid()) + '\\n' + str(child) + '\\n')\n"
            + "time.sleep(30)\n"
        )
        self.fx.write_probe("hang-group", body)
        t0 = time.monotonic()
        r = self.fx.run_server("hang-group", timeout=20)
        elapsed = time.monotonic() - t0
        self.assertEqual(r.returncode, 124, msg=r.stderr)
        self.assertIn("probe timed out after 1 s", r.stderr)
        self.assertLess(elapsed, 13)
        pids_path = os.path.join(self.fx.root, "hang.pids")
        self.assertTrue(os.path.isfile(pids_path), msg="probe never wrote pids")
        with open(pids_path) as f:
            pids = [int(x) for x in f.read().split() if x.strip()]
        time.sleep(0.2)
        for pid in pids:
            alive = True
            try:
                os.kill(pid, 0)
            except OSError:
                alive = False
            self.assertFalse(alive, msg=f"pid {pid} still alive")

        clamped = self.fx.write_probe(
            "clamp-timeout",
            _header("clamp-timeout", timeout=9999) + "echo hi\n",
        )
        header = probe_core.parse_header(clamped)
        self.assertEqual(header.timeout_s, 600)

    def test_server_log_line_and_ticket_add_redaction(self):
        self.fx.write_probe(
            "ticket-add",
            _header("ticket-add") + "exit 0\n",
        )
        r = self.fx.run_server("echo-test a b=c")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        log_path = self.fx.latest_log()
        self.assertIsNotNone(log_path)
        st_dir = os.stat(self.fx.log_dir())
        self.assertEqual(stat.S_IMODE(st_dir.st_mode), 0o700)
        st_file = os.stat(log_path)
        self.assertEqual(stat.S_IMODE(st_file.st_mode), 0o600)
        with open(log_path) as f:
            lines = [ln.rstrip("\n") for ln in f if ln.strip()]
        self.assertTrue(lines)
        self.assertRegex(lines[-1], LOG_LINE_RE)
        self.assertIn("verb=echo-test", lines[-1])
        self.assertIn("args=a b=c", lines[-1])

        r = self.fx.run_server("ticket-add payload-should-hide")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        with open(self.fx.latest_log()) as f:
            lines = [ln.rstrip("\n") for ln in f if ln.strip()]
        last = lines[-1]
        self.assertRegex(last, LOG_LINE_RE)
        self.assertIn("verb=ticket-add", last)
        self.assertIn("args=<redacted>", last)
        self.assertNotIn("payload-should-hide", last)

    def test_server_prunes_old_logs(self):
        d = os.path.join(self.fx.root, "state", "probe-log")
        os.makedirs(d, mode=0o700, exist_ok=True)
        today = datetime.now(timezone.utc).date()
        old = (today - timedelta(days=31)).strftime("%Y-%m-%d") + ".log"
        keep = (today - timedelta(days=29)).strftime("%Y-%m-%d") + ".log"
        old_path = os.path.join(d, old)
        keep_path = os.path.join(d, keep)
        for p in (old_path, keep_path):
            fd = os.open(p, os.O_WRONLY | os.O_CREAT, 0o600)
            os.write(fd, b"old\n")
            os.close(fd)
        r = self.fx.run_server("ping")
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertFalse(os.path.isfile(old_path), msg="31-day-old log not pruned")
        self.assertTrue(os.path.isfile(keep_path), msg="29-day-old log was pruned")

    def test_server_malformed_env_refuses(self):
        self.fx.write_env("not a valid env line\n")
        r = self.fx.run_server("ping")
        self.assertEqual(r.returncode, 64, msg=r.stderr)
        self.assertIn("refused: .env:", r.stderr)
        self.assertFalse(os.path.isfile(self.fx.canary_path()))


class TestAuthorize(ProbeTestCase):
    def _pubkey(self, body="AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyBodyForTests0001"):
        path = os.path.join(self.fx.root, "id_ed25519.pub")
        with open(path, "w") as f:
            f.write(f"ssh-ed25519 {body} original-comment\n")
        return path, body

    def test_authorize_line_write_duplicate_replace(self):
        pub, body = self._pubkey()
        ssh_dir = os.path.join(self.fx.home, ".ssh")
        os.mkdir(ssh_dir, 0o700)
        abs_server = os.path.realpath(self.fx.server)
        expected = f'restrict,command="{abs_server}" ssh-ed25519 {body} loops-probe'

        r = self.fx.run_server_argv(["--authorize", pub])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout.strip().splitlines()[0], expected)

        r = self.fx.run_server_argv(["--authorize", pub, "--write"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn(expected, r.stdout)
        ak = os.path.join(ssh_dir, "authorized_keys")
        self.assertTrue(os.path.isfile(ak))
        self.assertEqual(stat.S_IMODE(os.stat(ak).st_mode), 0o600)
        with open(ak) as f:
            contents = f.read()
        self.assertIn(expected, contents)
        self.assertEqual(contents.count(body), 1)

        r = self.fx.run_server_argv(["--authorize", pub, "--write"])
        self.assertEqual(r.returncode, 64, msg=r.stdout + r.stderr)
        self.assertTrue(r.stderr.startswith("refused:"))
        with open(ak) as f:
            self.assertEqual(f.read().count(body), 1)

        pub2, body2 = self._pubkey("AAAAC3NzaC1lZDI1NTE5AAAAIFakeKeyBodyForTests0001")
        # same body — --replace rewrites in place
        r = self.fx.run_server_argv(["--authorize", pub2, "--write", "--replace"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        with open(ak) as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        matching = [ln for ln in lines if body in ln]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0], expected)

        os.chmod(ssh_dir, 0o755)
        r = self.fx.run_server_argv(["--authorize", pub, "--write", "--replace"])
        self.assertEqual(r.returncode, 64, msg=r.stdout + r.stderr)
        self.assertTrue(r.stderr.startswith("refused:"))
        self.assertIn("0700", r.stderr)

        os.chmod(ssh_dir, 0o700)
        os.chmod(ak, 0o644)
        r = self.fx.run_server_argv(["--authorize", pub, "--write", "--replace"])
        self.assertEqual(r.returncode, 64, msg=r.stdout + r.stderr)
        self.assertTrue(r.stderr.startswith("refused:"))
        self.assertIn("0600", r.stderr)

    def test_authorize_not_reachable_as_forced_command(self):
        r = self.fx.run_server_argv(
            ["--authorize", "x"],
            env_extra={"SSH_ORIGINAL_COMMAND": "ping"},
        )
        self.assertEqual(r.returncode, 64, msg=r.stdout + r.stderr)
        self.assertTrue(r.stderr.startswith("refused:"))
        ak = os.path.join(self.fx.home, ".ssh", "authorized_keys")
        self.assertFalse(os.path.isfile(ak))


class TestClient(ProbeTestCase):
    def test_client_local_mode_runs_probe_and_builtins(self):
        r = self.fx.run_client(["echo-test", "a", "b"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(r.stdout.strip(), "a|b")

        r = self.fx.run_client(["--ping"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertEqual(
            r.stdout.strip(), f"ok probe local {probe_core.hostname()}"
        )

        r = self.fx.run_client(["--list"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
        names = {ln.split()[0]: ln.split()[1] for ln in lines}
        echo = os.path.join(self.fx.root, "probes", "echo-test")
        self.assertEqual(names["echo-test"], _hash12(echo))

        r = self.fx.run_client(["--check", "echo-test"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)

    def _fake_ssh(self, body, name="fake-ssh"):
        path = os.path.join(self.fx.root, name)
        _write_exec(path, body)
        return path

    def test_client_remote_mode_argv_and_dashdash_before_host(self):
        key = os.path.join(self.fx.root, "id_ed25519")
        with open(key, "w") as f:
            f.write("fake-key\n")
        stamp = os.path.join(self.fx.root, "ssh-invoked")
        fake = self._fake_ssh(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            f"open({stamp!r}, 'w').write('1')\n"
            "print(json.dumps(sys.argv))\n"
            "sys.exit(int(os.environ.get('FAKE_SSH_EXIT', '0')))\n"
        )
        env = {
            "LOOPS_PROBE_HOST": "llm-probe",
            "LOOPS_PROBE_KEY": key,
            "LOOPS_SSH": fake,
            "FAKE_SSH_EXIT": "0",
        }
        r = self.fx.run_client(["echo-test", "a", "b=c"], env_extra=env)
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(
            got,
            [
                fake,
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-o",
                "IdentitiesOnly=yes",
                "-i",
                key,
                "--",
                "llm-probe",
                "echo-test",
                "a",
                "b=c",
            ],
        )
        self.assertEqual(got[got.index("--") + 1], "llm-probe")

        if os.path.isfile(stamp):
            os.remove(stamp)
        env["FAKE_SSH_EXIT"] = "255"
        r = self.fx.run_client(["echo-test", "a"], env_extra=env)
        self.assertEqual(r.returncode, 75, msg=r.stdout + r.stderr)
        self.assertIn("probe transport failed", r.stderr)

        env["FAKE_SSH_EXIT"] = "7"
        r = self.fx.run_client(["echo-test", "a"], env_extra=env)
        self.assertEqual(r.returncode, 7, msg=r.stdout + r.stderr)

    def test_client_refuses_bad_args_locally(self):
        stamp = os.path.join(self.fx.root, "ssh-invoked")
        fake = self._fake_ssh(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            f"open({stamp!r}, 'w').write('1')\n"
            "sys.exit(0)\n"
        )
        key = os.path.join(self.fx.root, "id_ed25519")
        with open(key, "w") as f:
            f.write("k\n")
        env = {
            "LOOPS_PROBE_HOST": "llm-probe",
            "LOOPS_PROBE_KEY": key,
            "LOOPS_SSH": fake,
        }
        r = self.fx.run_client(["echo-test", "hello world"], env_extra=env)
        self.assertEqual(r.returncode, 64, msg=r.stdout + r.stderr)
        self.assertTrue(r.stderr.startswith("refused:"))
        self.assertFalse(os.path.isfile(stamp), msg="fake ssh was invoked")

        r = self.fx.run_client(["echo-test", "a'b"], env_extra=env)
        self.assertEqual(r.returncode, 64, msg=r.stdout + r.stderr)
        self.assertFalse(os.path.isfile(stamp), msg="fake ssh was invoked")

    def test_client_out_file_atomic_0600(self):
        out = os.path.join(self.fx.root, "out.txt")
        r = self.fx.run_client(["echo-test", "a", "b", "--out", out])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertTrue(os.path.isfile(out))
        self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
        with open(out) as f:
            self.assertEqual(f.read().strip(), "a|b")

        self.fx.write_probe(
            "fail-test",
            _header("fail-test") + "echo nope; exit 1\n",
        )
        fail_out = os.path.join(self.fx.root, "fail-out.txt")
        r = self.fx.run_client(["fail-test", "--out", fail_out])
        self.assertNotEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        self.assertFalse(os.path.isfile(fail_out))
        leftovers = [
            n
            for n in os.listdir(self.fx.root)
            if n.startswith(".probe-out-")
        ]
        self.assertEqual(leftovers, [])

    def test_client_check_remote_hash_drift(self):
        echo = os.path.join(self.fx.root, "probes", "echo-test")
        good_hash = _hash12(echo)
        other = self.fx.write_probe(
            "other-probe",
            _header("other-probe") + 'if [ "$1" = "--check" ]; then echo ok other-probe; exit 0; fi\n',
        )
        other_hash = _hash12(other)
        key = os.path.join(self.fx.root, "id_ed25519")
        with open(key, "w") as f:
            f.write("k\n")
        list_log = os.path.join(self.fx.root, "ssh-list-log")
        canned = os.path.join(self.fx.root, "canned")
        os.makedirs(canned, exist_ok=True)

        fake = self._fake_ssh(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "args = sys.argv[sys.argv.index('--') + 2:]\n"
            "verb = args[0] if args else ''\n"
            "rest = args[1:]\n"
            f"logp = {list_log!r}\n"
            "if verb == 'list':\n"
            "    with open(logp, 'a') as f: f.write('list\\n')\n"
            "mode = os.environ.get('FAKE_MODE', 'match')\n"
            "if os.environ.get('FAKE_SSH_EXIT') == '255':\n"
            "    sys.stderr.write('ssh boom\\n')\n"
            "    sys.exit(255)\n"
            "if verb == 'ping':\n"
            "    print('ok probe-server 1 testhost')\n"
            "    sys.exit(0)\n"
            "if verb == 'list':\n"
            "    if mode == 'missing':\n"
            "        print('other-probe deadbeefdead')\n"
            "    elif mode == 'drift':\n"
            f"        print('echo-test deadbeefdead')\n"
            "    else:\n"
            f"        print('echo-test {good_hash}')\n"
            f"        print('other-probe {other_hash}')\n"
            "    sys.exit(0)\n"
            "if verb == 'check':\n"
            "    code = int(os.environ.get('FAKE_CHECK_EXIT', '0'))\n"
            "    print('ok', rest[0] if rest else '')\n"
            "    sys.exit(code)\n"
            "sys.exit(0)\n"
        )
        base = {
            "LOOPS_PROBE_HOST": "llm-probe",
            "LOOPS_PROBE_KEY": key,
            "LOOPS_SSH": fake,
        }

        r = self.fx.run_client(
            ["--check", "echo-test"], env_extra={**base, "FAKE_MODE": "match"}
        )
        self.assertEqual(r.returncode, 0, msg=r.stderr)

        r = self.fx.run_client(
            ["--check", "echo-test"], env_extra={**base, "FAKE_MODE": "drift"}
        )
        self.assertEqual(r.returncode, 3, msg=r.stdout + r.stderr)
        self.assertIn("probe drift:", r.stderr)

        r = self.fx.run_client(
            ["--check", "echo-test"], env_extra={**base, "FAKE_MODE": "missing"}
        )
        self.assertEqual(r.returncode, 3, msg=r.stdout + r.stderr)
        self.assertIn("not offered", r.stderr)

        r = self.fx.run_client(
            ["--check", "echo-test"],
            env_extra={**base, "FAKE_MODE": "match", "FAKE_CHECK_EXIT": "1"},
        )
        self.assertEqual(r.returncode, 3, msg=r.stdout + r.stderr)

        r = self.fx.run_client(
            ["--check", "echo-test"],
            env_extra={**base, "FAKE_SSH_EXIT": "255"},
        )
        self.assertEqual(r.returncode, 75, msg=r.stdout + r.stderr)
        self.assertIn("probe transport failed", r.stderr)

        # Unit-test the ping+list cache: two --check names, one list round-trip.
        calls = []

        def transport(verb, args):
            calls.append((verb, tuple(args)))
            if verb == "ping":
                return probe_core.InvokeResult(0, "ok probe-server 1 testhost\n", "")
            if verb == "list":
                text = f"echo-test {good_hash}\nother-probe {other_hash}\n"
                return probe_core.InvokeResult(0, text, "")
            if verb == "check":
                return probe_core.InvokeResult(0, f"ok {args[0]}\n", "")
            return probe_core.InvokeResult(64, "", "refused\n")

        ch = probe_core.Channel(
            self.fx.root,
            file_env={"LOOPS_PROBE_HOST": "llm-probe"},
            environ={"LOOPS_PROBE_HOST": "llm-probe"},
            transport=transport,
        )
        from io import StringIO
        buf = StringIO()
        old_out = sys.stdout
        sys.stdout = buf
        try:
            rc1 = ch.check_name("echo-test")
            rc2 = ch.check_name("other-probe")
        finally:
            sys.stdout = old_out
        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        self.assertEqual([c[0] for c in calls if c[0] == "list"], ["list"])
        self.assertEqual([c[0] for c in calls if c[0] == "ping"], ["ping"])
        self.assertEqual(len([c for c in calls if c[0] == "check"]), 2)


if __name__ == "__main__":
    unittest.main()
