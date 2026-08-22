"""Shared probe-channel helpers (INTERFACES §14).

Used by bin/probe-server and bin/probe so header parsing, the clean env,
timed process-group exec, and list/hash cannot drift apart.
"""
import hashlib
import importlib.util
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name):
    path = os.path.join(_HERE, name + ".py")
    spec = importlib.util.spec_from_file_location("_probe_core_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


requirements = _load("requirements")
loopconf = _load("loopconf")

PROBE_SERVER_VERSION = 1
DEFAULT_TIMEOUT_S = 120
MAX_TIMEOUT_S = 600
TERM_GRACE_S = 10
MAX_ARGS = 8
MAX_ARG_LEN = 8192
MAX_HEADER_LINES = 20
LOG_KEEP_DAYS = 30

EXIT_OK = 0
EXIT_UNMET = 3
EXIT_REFUSED = 64
EXIT_TRANSPORT = 75
EXIT_TIMEOUT = 124

VERB_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
ARG_RE = re.compile(r"^[A-Za-z0-9_.:@/=+-]{1,8192}$")
HEADER_LINE_RE = re.compile(r"^# (probe(?:-[a-z0-9]+)*): (.*)$")
LOG_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.log$")
RESERVED_NAMES = frozenset({"ping", "list", "check"})
OUTPUT_KINDS = frozenset({"json", "tar", "text"})
REQUIRED_HEADER_KEYS = ("probe", "probe-writes", "probe-output", "probe-reads")


class Refuse(Exception):
    """Command refused; callers print `refused: <reason>` and exit 64."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class Header:
    __slots__ = ("name", "timeout_s", "writes", "output", "reads")

    def __init__(self, name, timeout_s, writes, output, reads):
        self.name = name
        self.timeout_s = timeout_s
        self.writes = writes
        self.output = output
        self.reads = reads


class InvokeResult:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def hostname():
    return socket.gethostname()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_today():
    return datetime.now(timezone.utc).date()


def script_root(script_file):
    """Repo root containing this script. Never from the client's env."""
    return os.path.dirname(os.path.dirname(os.path.realpath(script_file)))


def probes_dir(root):
    return os.path.join(root, "probes")


def probe_path(root, name):
    return os.path.join(probes_dir(root), name)


def log_dir(root):
    return os.path.join(root, "state", "probe-log")


def file_hash12(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


def parse_header(path):
    """Parse the leading `# probe-*:` block. Raises Refuse on a bad header."""
    name = os.path.basename(path)
    try:
        with open(path, "r") as f:
            raw_lines = []
            for _ in range(MAX_HEADER_LINES):
                line = f.readline()
                if line == "":
                    break
                raw_lines.append(line.rstrip("\n").rstrip("\r"))
    except OSError as e:
        raise Refuse(f"bad header: cannot read: {e}") from e

    if not raw_lines or not raw_lines[0].startswith("#!"):
        raise Refuse("bad header: missing shebang")

    fields = {}
    for line in raw_lines[1:]:
        m = HEADER_LINE_RE.match(line)
        if not m:
            break
        key, value = m.group(1), m.group(2)
        fields[key] = value

    missing = [k for k in REQUIRED_HEADER_KEYS if k not in fields]
    if missing:
        raise Refuse(f"bad header: missing {missing[0]}")

    hdr_name = fields["probe"].strip()
    if hdr_name != name:
        raise Refuse(f"bad header: name mismatch ({hdr_name!r} != {name!r})")

    writes = fields["probe-writes"]
    reads = fields["probe-reads"]
    if writes == "" or reads == "":
        raise Refuse("bad header: empty required field")

    output = fields["probe-output"].strip()
    if output not in OUTPUT_KINDS:
        raise Refuse(f"bad header: probe-output must be json|tar|text")

    timeout_s = DEFAULT_TIMEOUT_S
    if "probe-timeout-s" in fields:
        raw_t = fields["probe-timeout-s"].strip()
        try:
            timeout_s = int(raw_t)
        except ValueError:
            raise Refuse("bad header: probe-timeout-s is not an int") from None
        if timeout_s < 0:
            raise Refuse("bad header: probe-timeout-s is negative")
        if timeout_s > MAX_TIMEOUT_S:
            timeout_s = MAX_TIMEOUT_S

    return Header(hdr_name, timeout_s, writes, output, reads)


def parse_ssh_command(cmd):
    """Split SSH_ORIGINAL_COMMAND on single spaces. No shell. Raises Refuse."""
    if cmd is None:
        cmd = ""
    if "\t" in cmd or "\n" in cmd or "\r" in cmd:
        raise Refuse("tab or newline")
    if cmd == "":
        raise Refuse("empty command")
    if cmd != cmd.strip(" ") or "  " in cmd:
        raise Refuse("bad spacing")
    parts = cmd.split(" ")
    if any(p == "" for p in parts):
        raise Refuse("empty token")
    verb = parts[0]
    args = parts[1:]
    validate_verb_args(verb, args)
    return verb, args


def validate_verb_args(verb, args):
    if not VERB_RE.match(verb):
        raise Refuse("bad verb")
    if len(args) > MAX_ARGS:
        raise Refuse("too many args")
    for a in args:
        if not ARG_RE.match(a):
            raise Refuse("bad arg")


def clean_env(root, file_env, home=None):
    """Env a probe actually sees. Never the inherited environment."""
    if home is None:
        home = os.environ.get("HOME") or os.path.expanduser("~")
    env = dict(file_env or {})
    env["HOME"] = home
    env["PATH"] = requirements.runtime_path(home)
    env["LOOPS_ROOT"] = root
    env["LANG"] = "C.UTF-8"
    return env


def iter_probe_files(root):
    """Executable regular files in probes/ whose names match VERB_RE.

    Reserved names and symlinks are omitted.
    """
    d = probes_dir(root)
    if not os.path.isdir(d):
        return []
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return []
    for name in sorted(names):
        if not VERB_RE.match(name):
            continue
        if name in RESERVED_NAMES:
            continue
        path = os.path.join(d, name)
        if os.path.islink(path):
            continue
        if not os.path.isfile(path):
            continue
        if not os.access(path, os.X_OK):
            continue
        out.append((name, path))
    return out


def list_lines(root):
    return [f"{name} {file_hash12(path)}" for name, path in iter_probe_files(root)]


def resolve_probe(root, name):
    """Return (path, Header) or raise Refuse. Never follows a symlink."""
    if name in RESERVED_NAMES:
        raise Refuse("reserved name")
    if not VERB_RE.match(name):
        raise Refuse("bad verb")
    path = probe_path(root, name)
    if os.path.islink(path):
        raise Refuse("symlink")
    if not os.path.isfile(path):
        raise Refuse("unknown probe")
    if not os.access(path, os.X_OK):
        raise Refuse("not executable")
    header = parse_header(path)
    return path, header


def timed_exec(argv, *, env, cwd, timeout_s, stdout=None, stderr=None):
    """Run argv in its own session. On timeout: TERM, 10 s grace, KILL, 124."""
    proc = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )
    try:
        return proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        pgid = proc.pid
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=TERM_GRACE_S)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        print(f"probe timed out after {timeout_s} s", file=sys.stderr)
        return EXIT_TIMEOUT


def run_probe(root, name, args, file_env, stdout=None, stderr=None):
    """Header-check + clean-env + timed exec. Returns the probe's exit code."""
    path, header = resolve_probe(root, name)
    env = clean_env(root, file_env)
    return timed_exec(
        [path, *args],
        env=env,
        cwd=root,
        timeout_s=header.timeout_s,
        stdout=stdout,
        stderr=stderr,
    )


def ping_local_line():
    return f"ok probe local {hostname()}"


def ping_server_line():
    return f"ok probe-server {PROBE_SERVER_VERSION} {hostname()}"


def prune_logs(directory, today=None):
    if not os.path.isdir(directory):
        return
    if today is None:
        today = utc_today()
    cutoff = today - timedelta(days=LOG_KEEP_DAYS)
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        m = LOG_NAME_RE.match(name)
        if not m:
            continue
        try:
            file_date = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            try:
                os.remove(os.path.join(directory, name))
            except OSError:
                pass


def ensure_log_dir(root):
    d = log_dir(root)
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def append_log(root, *, client, verb, args, exit_code, ms):
    d = ensure_log_dir(root)
    day = utc_today().strftime("%Y-%m-%d")
    path = os.path.join(d, f"{day}.log")
    if verb == "ticket-add":
        args_field = "<redacted>"
    elif args:
        args_field = " ".join(args)
    else:
        args_field = "-"
    line = (
        f"{now_iso()} client={client} verb={verb or '-'} "
        f"args={args_field} exit={exit_code} ms={ms}\n"
    )
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def ssh_client_addr():
    raw = os.environ.get("SSH_CLIENT") or ""
    first = raw.split(" ")[0] if raw.strip() else ""
    return first or "-"


def probe_host(file_env, environ=None):
    environ = os.environ if environ is None else environ
    host = environ.get("LOOPS_PROBE_HOST")
    if host is None:
        host = (file_env or {}).get("LOOPS_PROBE_HOST")
    return (host or "").strip()


def probe_key_path(file_env, environ=None):
    environ = os.environ if environ is None else environ
    raw = environ.get("LOOPS_PROBE_KEY")
    if not raw:
        raw = (file_env or {}).get("LOOPS_PROBE_KEY")
    if not raw:
        raw = os.path.join(os.path.expanduser("~"), ".ssh", "loops-probe")
    return os.path.expanduser(raw)


def ssh_bin(environ=None):
    environ = os.environ if environ is None else environ
    return environ.get("LOOPS_SSH") or "ssh"


def ssh_argv(host, key, verb, args, environ=None):
    ssh = ssh_bin(environ)
    return [
        ssh,
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "IdentitiesOnly=yes",
        "-i",
        key,
        "--",
        host,
        verb,
        *args,
    ]


def parse_list_map(text):
    """Map probe name -> 12-hex hash from `list` output."""
    out = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2:
            out[parts[0]] = parts[1]
    return out


class Channel:
    """Local or remote probe transport. ping+list cached per instance."""

    def __init__(self, root, file_env=None, environ=None, transport=None):
        self.root = root
        self.file_env = {} if file_env is None else file_env
        self.environ = os.environ if environ is None else environ
        self._transport = transport
        self._ping_cache = None
        self._list_cache = None

    def is_remote(self):
        return bool(probe_host(self.file_env, self.environ))

    def host(self):
        return probe_host(self.file_env, self.environ)

    def key_path(self):
        return probe_key_path(self.file_env, self.environ)

    def ping_result(self):
        if self._ping_cache is None:
            self._ping_cache = self.invoke_capture("ping", [])
        return self._ping_cache

    def list_result(self):
        if self._list_cache is None:
            self._list_cache = self.invoke_capture("list", [])
        return self._list_cache

    def invoke_capture(self, verb, args):
        if self._transport is not None:
            return self._transport(verb, args)
        if self.is_remote():
            return self._ssh(verb, args, capture=True)
        return self._local_capture(verb, args)

    def invoke_passthrough(self, verb, args, stdout=None):
        if self._transport is not None:
            result = self._transport(verb, args)
            if stdout is not None and result.stdout:
                data = result.stdout
                if isinstance(data, str):
                    data = data.encode("utf-8")
                if hasattr(stdout, "write"):
                    stdout.write(data)
                else:
                    os.write(stdout, data)
            if result.stderr:
                sys.stderr.write(result.stderr)
            return result.returncode
        if self.is_remote():
            result = self._ssh(verb, args, capture=False, stdout=stdout)
            return result.returncode
        return self._local_passthrough(verb, args, stdout=stdout)

    def _local_capture(self, verb, args):
        if verb == "ping":
            if args:
                return InvokeResult(EXIT_REFUSED, "", "refused: ping takes no args\n")
            return InvokeResult(0, ping_local_line() + "\n", "")
        if verb == "list":
            if args:
                return InvokeResult(EXIT_REFUSED, "", "refused: list takes no args\n")
            text = "".join(line + "\n" for line in list_lines(self.root))
            return InvokeResult(0, text, "")
        if verb == "check":
            if len(args) != 1:
                return InvokeResult(EXIT_REFUSED, "", "refused: check takes one arg\n")
            return self._local_run_captured(args[0], ["--check"])
        return self._local_run_captured(verb, args)

    def _local_passthrough(self, verb, args, stdout=None):
        if verb == "ping":
            if args:
                sys.stderr.write("refused: ping takes no args\n")
                return EXIT_REFUSED
            print(ping_local_line())
            return 0
        if verb == "list":
            if args:
                sys.stderr.write("refused: list takes no args\n")
                return EXIT_REFUSED
            for line in list_lines(self.root):
                print(line)
            return 0
        if verb == "check":
            if len(args) != 1:
                sys.stderr.write("refused: check takes one arg\n")
                return EXIT_REFUSED
            try:
                return run_probe(
                    self.root, args[0], ["--check"], self.file_env, stdout=stdout
                )
            except Refuse as e:
                sys.stderr.write(f"refused: {e.reason}\n")
                return EXIT_REFUSED
        try:
            return run_probe(self.root, verb, args, self.file_env, stdout=stdout)
        except Refuse as e:
            sys.stderr.write(f"refused: {e.reason}\n")
            return EXIT_REFUSED

    def _local_run_captured(self, name, args):
        try:
            proc = self._popen_probe(name, args)
        except Refuse as e:
            return InvokeResult(EXIT_REFUSED, "", f"refused: {e.reason}\n")
        stdout, stderr = proc.communicate()
        return InvokeResult(proc.returncode, stdout, stderr)

    def _popen_probe(self, name, args):
        path, header = resolve_probe(self.root, name)
        env = clean_env(self.root, self.file_env)
        return _PopenWait(
            [path, *args],
            cwd=self.root,
            env=env,
            timeout_s=header.timeout_s,
        )

    def _ssh(self, verb, args, *, capture, stdout=None):
        host = self.host()
        key = self.key_path()
        argv = ssh_argv(host, key, verb, args, self.environ)
        try:
            if capture:
                proc = subprocess.run(
                    argv, capture_output=True, text=True, check=False
                )
                if proc.returncode == 255:
                    return InvokeResult(
                        EXIT_TRANSPORT,
                        proc.stdout,
                        f"probe transport failed: {proc.stderr}",
                    )
                return InvokeResult(proc.returncode, proc.stdout, proc.stderr)
            proc = subprocess.run(
                argv,
                stdout=stdout,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if proc.returncode == 255:
                sys.stderr.write(f"probe transport failed: {proc.stderr}")
                if proc.stderr and not proc.stderr.endswith("\n"):
                    sys.stderr.write("\n")
                return InvokeResult(EXIT_TRANSPORT, "", proc.stderr)
            if proc.stderr:
                sys.stderr.write(proc.stderr)
            return InvokeResult(proc.returncode, "", proc.stderr)
        except OSError as e:
            msg = f"probe transport failed: {e}"
            sys.stderr.write(msg + "\n")
            return InvokeResult(EXIT_TRANSPORT, "", msg)

    def check_name(self, name):
        """`--check <name>`: 0 ok / 3 unmet / 75 transport. Prints to stderr."""
        if self.is_remote() or self._transport is not None:
            return self._remote_check(name)
        return self._local_check(name)

    def _local_check(self, name):
        result = self._local_run_captured(name, ["--check"])
        if result.stdout:
            sys.stdout.write(result.stdout)
        if result.returncode == 0:
            return 0
        if result.stderr:
            sys.stderr.write(result.stderr)
        return EXIT_UNMET

    def _remote_check(self, name):
        ping = self.ping_result()
        if ping.returncode == EXIT_TRANSPORT:
            if ping.stderr:
                sys.stderr.write(ping.stderr if ping.stderr.endswith("\n") else ping.stderr + "\n")
            return EXIT_TRANSPORT
        ping_text = (ping.stdout or "").strip()
        if ping.returncode != 0 or not ping_text.startswith("ok"):
            sys.stderr.write("probe not offered by server: ping failed\n")
            return EXIT_UNMET

        listed = self.list_result()
        if listed.returncode == EXIT_TRANSPORT:
            if listed.stderr:
                sys.stderr.write(
                    listed.stderr if listed.stderr.endswith("\n") else listed.stderr + "\n"
                )
            return EXIT_TRANSPORT
        if listed.returncode != 0:
            sys.stderr.write("probe not offered by server: list failed\n")
            return EXIT_UNMET

        mapping = parse_list_map(listed.stdout)
        if name not in mapping:
            sys.stderr.write(f"probe not offered by server: {name}\n")
            return EXIT_UNMET

        local_path = probe_path(self.root, name)
        if not os.path.isfile(local_path):
            sys.stderr.write(f"probe drift: {name} server={mapping[name]} client=-\n")
            return EXIT_UNMET
        client_hash = file_hash12(local_path)
        server_hash = mapping[name]
        if server_hash != client_hash:
            sys.stderr.write(
                f"probe drift: {name} server={server_hash} client={client_hash}\n"
            )
            return EXIT_UNMET

        checked = self.invoke_capture("check", [name])
        if checked.returncode == EXIT_TRANSPORT:
            if checked.stderr:
                sys.stderr.write(
                    checked.stderr if checked.stderr.endswith("\n") else checked.stderr + "\n"
                )
            return EXIT_TRANSPORT
        if checked.stdout:
            sys.stdout.write(checked.stdout)
        if checked.returncode != 0:
            if checked.stderr:
                sys.stderr.write(checked.stderr)
            return EXIT_UNMET
        return 0


class _PopenWait:
    """subprocess.Popen + communicate that still enforces the header timeout."""

    def __init__(self, argv, cwd, env, timeout_s):
        self._timeout_s = timeout_s
        self._proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            text=True,
        )

    def communicate(self):
        try:
            stdout, stderr = self._proc.communicate(timeout=self._timeout_s)
            self.returncode = self._proc.returncode
            return stdout, stderr
        except subprocess.TimeoutExpired:
            pgid = self._proc.pid
            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = self._proc.communicate(timeout=TERM_GRACE_S)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                stdout, stderr = self._proc.communicate()
            self.returncode = EXIT_TIMEOUT
            stderr = (stderr or "") + f"probe timed out after {self._timeout_s} s\n"
            return stdout, stderr


def atomic_out_file(out_path):
    """Context helper: yield a writable fd; replace into out_path on success.

    Caller must call `commit()` after a zero exit; otherwise the temp is
    removed and `out_path` is left untouched.
    """
    dest = os.path.abspath(out_path)
    directory = os.path.dirname(dest) or os.getcwd()
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".probe-out-", dir=directory)
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        pass
    return _OutFile(fd, tmp, dest)


class _OutFile:
    def __init__(self, fd, tmp, dest):
        self.fd = fd
        self.tmp = tmp
        self.dest = dest
        self._committed = False
        self._closed = False

    def commit(self):
        if not self._closed:
            os.close(self.fd)
            self._closed = True
        os.replace(self.tmp, self.dest)
        try:
            os.chmod(self.dest, 0o600)
        except OSError:
            pass
        self._committed = True

    def abort(self):
        if not self._closed:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self._closed = True
        if self.tmp and os.path.exists(self.tmp):
            try:
                os.unlink(self.tmp)
            except OSError:
                pass
