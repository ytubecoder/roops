#!/usr/bin/env python3
"""bin/requirements.py — host-requirement checker (INTERFACES §5.3).

Importable module + CLI, same shape as bin/lock.py / bin/db.py.

    check(root, conf, *, live, env=None) -> list[(item, ok, detail)]

CLI:

    requirements.py check --root R --loop NAME [--from loops.d] [--no-live] [--json]
"""
import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_loopconf():
    spec = importlib.util.spec_from_file_location(
        "_requirements_loopconf", os.path.join(_HERE, "loopconf.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


loopconf = _load_loopconf()

PROBE_LIVE_TIMEOUT_S = 30


def runtime_path(home):
    """The PATH a scheduled run gets. Single source for both emitters: neither
    launchd nor a systemd user unit inherits a login shell's PATH, and the
    engine CLIs live in $HOME/.local/bin. /opt/homebrew is a no-op on Linux;
    it stays so the two backends cannot drift apart."""
    return ":".join(
        [
            "/opt/homebrew/bin",
            "/usr/local/bin",
            os.path.join(home, ".local/bin"),
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        ]
    )


def _default_root():
    return os.environ.get("LOOPS_ROOT", os.path.expanduser("~/projects/loops"))


def _merged_env(root, env):
    """Caller-supplied env wins as-is. None → os.environ then .env where unset."""
    if env is not None:
        return env
    merged = dict(os.environ)
    for k, v in loopconf.load_env(root).items():
        merged.setdefault(k, v)
    return merged


def _check_os(value):
    ok = sys.platform.startswith(value)
    return ok, f"host is {sys.platform}"


def _check_bin(name):
    home = os.path.expanduser("~")
    unit_path = runtime_path(home)
    found = shutil.which(name, path=unit_path)
    if found:
        return True, found
    return False, f"not on unit PATH ({unit_path})"


def _check_file(path_spec):
    path = loopconf._expand_home(path_spec)
    if not os.path.isfile(path):
        return False, "missing"
    if not os.access(path, os.R_OK):
        return False, "not readable"
    return True, path


def _check_env(key, env):
    val = env.get(key)
    if val:
        return True, ""
    return False, "unset or empty"


def _check_probe(root, name, *, live, env):
    if live:
        probe = os.path.join(root, "bin", "probe")
        if not os.path.isfile(probe):
            return False, "bin/probe missing"
        try:
            proc = subprocess.run(
                [probe, "--check", name],
                cwd=root,
                timeout=PROBE_LIVE_TIMEOUT_S,
                capture_output=True,
                text=True,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, f"probe check timed out after {PROBE_LIVE_TIMEOUT_S}s"
        except OSError:
            return False, "bin/probe missing"
        if proc.returncode == 0:
            return True, ""
        snippet = (proc.stderr or proc.stdout or "").strip().splitlines()
        extra = snippet[0] if snippet else ""
        detail = f"probe check exited {proc.returncode}"
        if extra:
            detail = f"{detail}: {extra}"
        return False, detail

    host = env.get("LOOPS_PROBE_HOST") or ""
    if host:
        key_path = env.get("LOOPS_PROBE_KEY") or os.path.expanduser(
            "~/.ssh/loops-probe"
        )
        display = env.get("LOOPS_PROBE_KEY") or "~/.ssh/loops-probe"
        if os.path.isfile(key_path) and os.access(key_path, os.R_OK):
            return True, ""
        return False, f"probe key missing: {display}"

    probe_script = os.path.join(root, "probes", name)
    if os.path.isfile(probe_script) and os.access(probe_script, os.X_OK):
        return True, ""
    return False, f"probes/{name} not executable"


def check(root, conf, *, live, env=None):
    """Return one (item, ok, detail) per requires= item, declaration order.

    [] when none declared. `env` is the already-merged process environment
    (os.environ with .env applied where unset); None builds that here.
    """
    items = conf.get("requires") or []
    if not items:
        return []
    resolved_env = _merged_env(root, env)
    out = []
    for item in items:
        kind, _, value = item.partition(":")
        if kind == "os":
            ok, detail = _check_os(value)
        elif kind == "bin":
            ok, detail = _check_bin(value)
        elif kind == "file":
            ok, detail = _check_file(value)
        elif kind == "env":
            ok, detail = _check_env(value, resolved_env)
        elif kind == "probe":
            ok, detail = _check_probe(
                root, value, live=live, env=resolved_env
            )
        else:
            ok, detail = False, f"unknown kind {kind!r}"
        out.append((item, bool(ok), detail))
    return out


def _items_payload(results):
    return [
        {"item": item, "ok": ok, "detail": detail} for item, ok, detail in results
    ]


def cmd_check(args) -> int:
    from_dir = args.from_dir
    conf_path = os.path.join(args.root, from_dir, args.loop, "loop.conf")
    if not os.path.isfile(conf_path):
        print("loop not found", file=sys.stderr)
        return 2
    try:
        env = _merged_env(args.root, None)
    except loopconf.EnvFileError as e:
        print(str(e), file=sys.stderr)
        return 2
    conf, errors = loopconf.parse(conf_path)
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 2
    live = not args.no_live
    results = check(args.root, conf, live=live, env=env)
    items = _items_payload(results)
    all_ok = all(row["ok"] for row in items)
    payload = {"loop": args.loop, "items": items, "ok": all_ok}
    if args.json:
        print(json.dumps(payload))
    else:
        if not items:
            print(f"OK {args.loop} (no requirements declared)")
        else:
            for row in items:
                mark = "OK" if row["ok"] else "UNMET"
                if row["detail"]:
                    print(f"{mark} {row['item']}  {row['detail']}")
                else:
                    print(f"{mark} {row['item']}")
    return 0 if all_ok else 1


def build_parser():
    p = argparse.ArgumentParser(prog="requirements.py")
    sub = p.add_subparsers(dest="verb")
    check_p = sub.add_parser("check")
    check_p.add_argument("--root", default=_default_root())
    check_p.add_argument("--loop", required=True)
    check_p.add_argument(
        "--from", dest="from_dir", default="loops.d", choices=["loops.d", "examples"]
    )
    check_p.add_argument("--no-live", action="store_true")
    check_p.add_argument("--json", action="store_true")
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.verb == "check":
        return cmd_check(args)
    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
