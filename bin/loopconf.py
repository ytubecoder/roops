#!/usr/bin/env python3
"""bin/loopconf.py — §5.0 + §5 loop.conf parser. Single implementation used
by everything (run-loop.sh via CLI, loopctl and dashboard/generate.py via
import).

    parse(path) -> (conf: dict, errors: list[str])

The file is NEVER `source`d — it is parsed by both bash and Python, and
sourcing arbitrary files is a code-execution footgun. Strict KEY=value
grammar (see module docstring sections below for the exact rules).

Dangerous-combo checks (§5.2) are NOT here — they live in `loopctl
validate`.
"""

import argparse
import importlib.util
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_schedule_module():
    spec = importlib.util.spec_from_file_location(
        "schedule", os.path.join(_HERE, "schedule.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_schedule = _load_schedule_module()

KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
TAG_RE = re.compile(r"^[a-z][a-z0-9:_-]{1,40}$")
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
REQ_RE = re.compile(r"^(os|bin|file|env|probe):[^,\s]+$")
MAX_ENV_KEYS = 64

# Field table (§5). Each entry declares: required, type, default (or
# _REQUIRED / _CONDITIONAL sentinels), plus type-specific extras.
_REQUIRED = object()
_NO_DEFAULT = object()

FIELDS = {
    "name": {"required": True, "type": "name"},
    "description": {"required": True, "type": "str"},
    "owner": {"required": False, "type": "name", "default": None},
    "type": {"required": True, "type": "enum", "values": ["agent", "watchdog"]},
    "engine": {"required": True, "type": "enum", "values": ["codex", "claude"]},
    "model": {"required": False, "type": "str", "default": None},
    "schedule": {"required": True, "type": "schedule"},
    "workdir": {"required": False, "type": "path", "default": None},
    "timeout_s": {
        "required": False,
        "type": "int",
        "min": 30,
        "max": 7200,
        "default": 900,
    },
    "enabled": {"required": False, "type": "bool", "default": True},
    "retention_days": {
        "required": False,
        "type": "int",
        "min": 1,
        "max": 3650,
        "default": 30,
    },
    "retry_transient": {
        "required": False,
        "type": "int",
        "min": 0,
        "max": 3,
        "default": 1,
    },
    "perm_fs_write": {
        "required": False,
        "type": "enum",
        "values": ["none", "report_only", "workdir"],
        "default": "report_only",
    },
    "perm_network": {
        "required": False,
        "type": "enum",
        "values": ["none", "full"],
        "default": "none",
    },
    "perm_local_exec": {
        "required": False,
        "type": "enum",
        "values": ["none", "allowlist", "full"],
        "default": "none",
    },
    "perm_remote_mutation": {
        "required": False,
        "type": "enum",
        "values": ["none", "allowlist"],
        "default": "none",
    },
    "exec_allowlist": {"required": False, "type": "list", "default": None},
    "credential_env": {"required": False, "type": "list", "default": None},
    "remote_mutation_justification": {
        "required": False,
        "type": "str",
        "default": None,
    },
    "notes": {"required": False, "type": "str", "default": None},
    "tags": {"required": False, "type": "tags", "default": None},
    "requires": {"required": False, "type": "requires", "default": None},
    "i_accept_unrestricted": {"required": False, "type": "bool", "default": False},
}


def _loops_root() -> str:
    return os.environ.get("LOOPS_ROOT", os.path.expanduser("~/projects/loops"))


class _LineError(Exception):
    def __init__(self, msg):
        super().__init__(msg)


class EnvFileError(ValueError):
    """Malformed `$LOOPS_ROOT/.env` line. Message is `{path}:{lineno}: {msg}`."""


def _parse_value(rest: str):
    """Parse the value portion of a KEY=value line (rest starts right after
    '='). Returns (value, trailing_ok) or raises _LineError."""
    if rest.startswith('"'):
        # Quoted value with \" escape.
        out = []
        i = 1
        n = len(rest)
        closed = False
        while i < n:
            c = rest[i]
            if c == "\\" and i + 1 < n and rest[i + 1] == '"':
                out.append('"')
                i += 2
                continue
            if c == '"':
                closed = True
                i += 1
                break
            out.append(c)
            i += 1
        if not closed:
            raise _LineError("unterminated quoted value")
        trailer = rest[i:]
        _validate_trailer(trailer)
        return "".join(out)
    else:
        # Bare value: no spaces allowed. Everything up to the first
        # whitespace is the value; whitespace must then be followed
        # (after more whitespace) by a comment '#' or end of line.
        m = re.match(r"^(\S*)(\s*)(.*)$", rest)
        value, _ws, remainder = m.group(1), m.group(2), m.group(3)
        if remainder and not remainder.lstrip().startswith("#"):
            raise _LineError("bare value must not contain spaces")
        return value


def _validate_trailer(trailer: str) -> None:
    stripped = trailer.strip()
    if stripped and not stripped.startswith("#"):
        raise _LineError("unexpected trailing content after quoted value")


def _split_line(line: str):
    """Split a raw config line into (key, rest_after_equals) or None for
    blank/comment lines. Raises _LineError for malformed lines."""
    stripped = line.strip()
    if stripped == "" or stripped.startswith("#"):
        return None
    if "=" not in line:
        raise _LineError("expected KEY=value")
    key, rest = line.split("=", 1)
    key = key.strip()
    # Reject leading/trailing whitespace around key that would indicate a
    # malformed line (KEY_RE below is strict anyway).
    return key, rest


def _expand_home(value: str) -> str:
    if value.startswith("$HOME"):
        return os.path.expanduser("~") + value[len("$HOME") :]
    if value == "~":
        return os.path.expanduser("~")
    if value.startswith("~/"):
        return os.path.expanduser(value)
    return value


def load_env(root: str) -> dict:
    """Parse `<root>/.env` into a dict. Missing file → `{}`.

    Reuses the loop.conf value grammar (`_split_line` / `_parse_value` /
    `_validate_trailer` / `_expand_home`) but NOT `parse()`: env keys are
    upper-case (`ENV_KEY_RE`) and `$HOME`/`~` expand in every value.
    """
    path = os.path.join(root, ".env")
    if not os.path.isfile(path):
        return {}
    out = {}
    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except OSError as e:
        raise EnvFileError(f"{path}: cannot read: {e}") from e

    for lineno, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip("\n")
        try:
            split = _split_line(line)
        except _LineError as e:
            raise EnvFileError(f"{path}:{lineno}: {e}") from e
        if split is None:
            continue
        key, rest = split
        if not ENV_KEY_RE.match(key):
            raise EnvFileError(f"{path}:{lineno}: invalid key {key!r}")
        if key in out:
            raise EnvFileError(f"{path}:{lineno}: duplicate key {key}")
        try:
            value = _parse_value(rest)
        except _LineError as e:
            raise EnvFileError(f"{path}:{lineno}: {e}") from e
        if len(out) >= MAX_ENV_KEYS:
            raise EnvFileError(f"{path}:{lineno}: max {MAX_ENV_KEYS} keys")
        out[key] = _expand_home(value)
    return out


def parse(path: str):
    conf = {}
    errors = []
    seen_raw = {}

    try:
        with open(path, "r") as f:
            lines = f.readlines()
    except OSError as e:
        return {}, [f"cannot read {path}: {e}"]

    for lineno, raw_line in enumerate(lines, start=1):
        line = raw_line.rstrip("\n")
        try:
            split = _split_line(line)
        except _LineError as e:
            errors.append(f"line {lineno}: {e}")
            continue
        if split is None:
            continue
        key, rest = split

        if not KEY_RE.match(key):
            errors.append(
                f"line {lineno}: invalid key {key!r} (must match ^[a-z][a-z0-9_]*$)"
            )
            continue

        if key not in FIELDS:
            errors.append(f"line {lineno}: unknown key {key!r}")
            continue

        try:
            value = _parse_value(rest)
        except _LineError as e:
            errors.append(f"line {lineno}: {key}: {e}")
            continue

        seen_raw[key] = value

    # Type/range checks + defaults.
    for key, field in FIELDS.items():
        if key in seen_raw:
            raw_value = seen_raw[key]
            ok, parsed_value, err = _typecheck(key, field, raw_value)
            if not ok:
                errors.append(err)
            else:
                conf[key] = parsed_value
        else:
            if field["required"]:
                errors.append(f"missing required key: {key}")
            else:
                conf[key] = field.get("default")

    # $HOME/~ expansion ONLY in workdir. workdir's schema default is None
    # (not a literal placeholder) so that an omitted workdir falls through
    # to _loops_root(), which resolves LOOPS_ROOT (env, else
    # $HOME/projects/loops) at parse() time — honoring the §0 rule and
    # letting LOOPS_ROOT overrides in the environment take effect.
    if conf.get("workdir"):
        conf["workdir"] = _expand_home(conf["workdir"])
    if not conf.get("workdir"):
        conf["workdir"] = _loops_root()

    # name must equal directory name (best-effort: only checked by loopctl
    # validate, which has the directory context; parser stays generic).

    # Conditional requirements.
    if (
        conf.get("perm_local_exec") == "allowlist"
        or conf.get("perm_remote_mutation") == "allowlist"
    ) and not conf.get("exec_allowlist"):
        errors.append(
            "exec_allowlist is required when perm_local_exec=allowlist or perm_remote_mutation=allowlist"
        )

    if (
        conf.get("perm_remote_mutation")
        and conf.get("perm_remote_mutation") != "none"
        and not conf.get("remote_mutation_justification")
    ):
        errors.append(
            "remote_mutation_justification is required when perm_remote_mutation != none"
        )

    return conf, errors


# §5 owner resolution (B-17): owner is required-but-assumed. Absence is never
# an error anywhere; every surface resolves it here — the ONLY implementation
# of the rule — so nothing is ever unowned while an assumption stays visible.
DEFAULT_OWNER = "loops"


def resolve_owner(conf):
    """-> (owner: str, assumed: bool). Explicit owner passes through; a
    missing one is assumed DEFAULT_OWNER with assumed=True."""
    owner = conf.get("owner")
    if owner:
        return owner, False
    return DEFAULT_OWNER, True


def _typecheck(key, field, raw_value):
    t = field["type"]
    if t == "name":
        if not NAME_RE.match(raw_value):
            return (
                False,
                None,
                f"{key}: {raw_value!r} does not match ^[a-z][a-z0-9-]{{1,40}}$",
            )
        return True, raw_value, None
    if t == "str":
        return True, raw_value, None
    if t == "path":
        return True, raw_value, None
    if t == "enum":
        if raw_value not in field["values"]:
            return False, None, f"{key}: {raw_value!r} not in {field['values']}"
        return True, raw_value, None
    if t == "int":
        try:
            n = int(raw_value)
        except ValueError:
            return False, None, f"{key}: {raw_value!r} is not an integer"
        lo, hi = field["min"], field["max"]
        if not (lo <= n <= hi):
            return False, None, f"{key}: {n} out of range {lo}-{hi}"
        return True, n, None
    if t == "bool":
        if raw_value == "true":
            return True, True, None
        if raw_value == "false":
            return True, False, None
        return False, None, f"{key}: {raw_value!r} must be true or false"
    if t == "list":
        items = [x.strip() for x in raw_value.split(",") if x.strip() != ""]
        return True, items, None
    if t == "tags":
        entries = [e.strip() for e in raw_value.split(",")]
        if any(e == "" for e in entries):
            return False, None, f"{key}: empty tag entry"
        bad = [e for e in entries if not TAG_RE.match(e)]
        if bad:
            return (
                False,
                None,
                f"{key}: invalid tag(s) {bad} (need ^[a-z][a-z0-9:_-]{{1,40}}$)",
            )
        deduped = list(dict.fromkeys(entries))
        if len(deduped) > 8:
            return False, None, f"{key}: max 8 tags, got {len(deduped)}"
        return True, deduped, None
    if t == "requires":
        entries = [e.strip() for e in raw_value.split(",")]
        if any(e == "" for e in entries):
            return False, None, f"{key}: empty entry"
        allowed = "os, bin, file, env, probe"
        for e in entries:
            if not REQ_RE.match(e):
                return (
                    False,
                    None,
                    f"{key}: invalid item {e!r} (allowed kinds: {allowed})",
                )
            kind, val = e.split(":", 1)
            if kind == "os" and val not in ("darwin", "linux"):
                return (
                    False,
                    None,
                    f"{key}: invalid item {e!r} (os value must be darwin or linux)",
                )
        deduped = list(dict.fromkeys(entries))
        if len(deduped) > 16:
            return False, None, f"{key}: max 16 items, got {len(deduped)}"
        return True, deduped, None
    if t == "schedule":
        try:
            _schedule.parse(raw_value)
        except ValueError as e:
            return False, None, f"{key}: {e}"
        return True, raw_value, None
    return False, None, f"{key}: unknown field type {t}"


def build_parser():
    p = argparse.ArgumentParser(prog="loopconf.py")
    sub = p.add_subparsers(dest="verb")

    parse_cmd = sub.add_parser("parse")
    parse_cmd.add_argument("--file", required=True)
    parse_cmd.add_argument("--json", action="store_true")

    get_cmd = sub.add_parser("get")
    get_cmd.add_argument("--file", required=True)
    get_cmd.add_argument("--key", required=True)

    env_cmd = sub.add_parser("env")
    env_cmd.add_argument("--root", required=True)
    env_cmd.add_argument("--json", action="store_true")

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.verb == "parse":
        conf, errors = parse(args.file)
        if args.json:
            print(json.dumps({"conf": conf, "errors": errors}))
        else:
            print(conf)
            for e in errors:
                print(e, file=sys.stderr)
        return 1 if errors else 0

    if args.verb == "get":
        if args.key not in FIELDS:
            print(end="")
            return 1
        conf, errors = parse(args.file)
        if args.key not in conf:
            print(end="")
            return 1
        value = conf[args.key]
        if isinstance(value, list):
            print(",".join(value))
        elif isinstance(value, bool):
            print("true" if value else "false")
        elif value is None:
            print()
        else:
            print(value)
        return 0

    if args.verb == "env":
        try:
            data = load_env(args.root)
        except EnvFileError as e:
            print(str(e), file=sys.stderr)
            return 1
        if args.json:
            print(json.dumps(data))
        else:
            for k, v in data.items():
                print(f"{k}={v}")
        return 0

    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
