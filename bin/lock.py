#!/usr/bin/env python3
"""bin/lock.py — §2 fcntl lock helper. Replaces `flock` (absent on macOS).

Advisory, per-loop, non-blocking by default.

    bin/lock.py acquire --name <loop> [--root $LOOPS_ROOT] [--wait-s N]
    bin/lock.py check   --name <loop> [--root $LOOPS_ROOT]

`acquire`: on success writes "<pid> <iso-ts>" into the lock file, prints
ACQUIRED to stdout, then blocks reading stdin until EOF, then releases and
exits 0. On contention (after any --wait-s retries) exits 3 and prints
"HELD_BY <pid> <since>" to stderr.

`check`: exit 0 if free, 3 if held. Never modifies the lock file.

Exit codes: 0 ok, 3 contention, 2 usage error.
"""
import argparse
import fcntl
import os
import sys
import time
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def lock_path(root: str, name: str) -> str:
    return os.path.join(root, "state", "locks", f"{name}.lock")


def read_holder(path: str):
    """Return (pid, since) parsed from the lock file, or (None, None)."""
    try:
        with open(path, "r") as f:
            content = f.read().strip()
    except OSError:
        return None, None
    if not content:
        return None, None
    parts = content.split(None, 1)
    if not parts:
        return None, None
    try:
        pid = int(parts[0])
    except ValueError:
        return None, None
    since = parts[1] if len(parts) > 1 else "unknown"
    return pid, since


def pid_alive(pid: int) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we can't signal it — still alive.
        return True
    except OSError:
        return False
    return True


def try_acquire(path: str):
    """Attempt a single non-blocking acquire.

    Returns an open file descriptor (int) on success, or None on
    contention (file still held by a live process). Never raises for the
    stale-pid case — takes over silently.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def write_holder(fd: int) -> None:
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    payload = f"{os.getpid()} {now_iso()}\n".encode("utf-8")
    os.write(fd, payload)
    os.fsync(fd)


def cmd_check(args) -> int:
    path = lock_path(args.root, args.name)
    if not os.path.exists(path):
        # Nothing to check and nothing to create — check never modifies.
        return 0
    fd = os.open(path, os.O_RDONLY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pid, since = read_holder(path)
            if pid is not None and pid_alive(pid):
                print(f"HELD_BY {pid} {since}", file=sys.stderr)
                return 3
            # Stale annotation on a currently-unlockable file shouldn't
            # happen (flock releases on process death) but never crash.
            return 0
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return 0
    finally:
        os.close(fd)


def cmd_acquire(args) -> int:
    path = lock_path(args.root, args.name)
    deadline = time.monotonic() + args.wait_s if args.wait_s else None
    poll_s = 0.25

    while True:
        fd = try_acquire(path)
        if fd is not None:
            break
        pid, since = read_holder(path)
        if pid is not None and not pid_alive(pid):
            # Stale pid annotation; flock itself already let us through if
            # the holder truly died, so a contention here with a dead pid
            # means we should just retry the acquire immediately.
            continue
        if deadline is None or time.monotonic() >= deadline:
            print(f"HELD_BY {pid} {since}", file=sys.stderr)
            return 3
        time.sleep(poll_s)

    try:
        write_holder(fd)
        print("ACQUIRED", flush=True)
        # Hold the lock until stdin closes (EOF).
        try:
            sys.stdin.read()
        except Exception:
            pass
        return 0
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lock.py")
    sub = p.add_subparsers(dest="verb")

    acquire = sub.add_parser("acquire")
    acquire.add_argument("--name", required=True)
    acquire.add_argument("--root", default=os.environ.get("LOOPS_ROOT", os.path.expanduser("~/projects/loops")))
    acquire.add_argument("--wait-s", dest="wait_s", type=float, default=0.0)

    check = sub.add_parser("check")
    check.add_argument("--name", required=True)
    check.add_argument("--root", default=os.environ.get("LOOPS_ROOT", os.path.expanduser("~/projects/loops")))

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.verb == "acquire":
        return cmd_acquire(args)
    if args.verb == "check":
        return cmd_check(args)
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
