#!/usr/bin/env python3
"""validate_action_set.py — validate a produced ads-google action set.

Ships with the ads-google loop and is run INSIDE the engine session via the
loop's exec allowlist (docs LOOP_AUTHORING.md — the harness has no post-engine
hook, so validation is an allowlisted local command). It is also importable by
emit_action_set.py (which calls it as its final step) and runnable standalone
so a human can re-check any set:

    python3 validate_action_set.py <action-set-dir>

Checks (stdlib only, per the harness ground rules):
  1. context.json exists, parses, and carries the required keys.
  2. ACTIONS.md exists and its first non-empty line is a `> generated:` stamp.
  3. Every register heading id matches ^ADG-\\d{2,}$ (two-or-more digits;
     daily loops outlive 99), with no duplicate ids.
  4. Register <-> briefs consistency: every OPEN (non-struck) register id has a
     brief at actions/<ID>.md, and every brief file id appears in the register.
  5. Every brief's first non-empty line is a `> generated:` stamp.

Exit 0 = valid, 1 = invalid (reasons to stderr, one per line), 2 = usage.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ID_RE = re.compile(r"^ADG-\d{2,}$")
# Register heading: "## ADG-07 — title"  (open)  or  "## ~~ADG-07 — title~~" (struck)
HEADING_RE = re.compile(r"^##\s+(~~)?\s*(ADG-\d+)\s+[—-]\s+(.*?)(~~)?\s*$")
STAMP_RE = re.compile(r"^>\s*generated:\s*\S")
REQUIRED_CONTEXT_KEYS = {"loop", "run_id", "generated", "engine", "action_ids"}


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.rstrip("\n")
    return ""


def validate(set_dir: Path) -> list[str]:
    errors: list[str] = []
    set_dir = Path(set_dir)

    # 1. context.json
    ctx_path = set_dir / "context.json"
    ctx = None
    if not ctx_path.is_file():
        errors.append("context.json: missing")
    else:
        try:
            ctx = json.loads(ctx_path.read_text())
        except (json.JSONDecodeError, ValueError) as exc:
            errors.append(f"context.json: not parseable ({exc})")
        else:
            if not isinstance(ctx, dict):
                errors.append("context.json: not a JSON object")
                ctx = None
            else:
                missing = REQUIRED_CONTEXT_KEYS - set(ctx)
                if missing:
                    errors.append(
                        "context.json: missing keys " + ", ".join(sorted(missing))
                    )

    # 2. ACTIONS.md + stamp
    reg_path = set_dir / "ACTIONS.md"
    register_ids: list[str] = []
    open_ids: set[str] = set()
    if not reg_path.is_file():
        errors.append("ACTIONS.md: missing")
        return errors  # nothing more to check without the register
    reg_text = reg_path.read_text()
    if not STAMP_RE.match(_first_nonempty_line(reg_text)):
        errors.append("ACTIONS.md: first non-empty line is not a `> generated:` stamp")

    # 3. headings / ids
    for line in reg_text.splitlines():
        m = HEADING_RE.match(line)
        if not m:
            continue
        struck = bool(m.group(1) or m.group(4))
        aid = m.group(2)
        if not ID_RE.match(aid):
            errors.append(f"ACTIONS.md: id {aid!r} does not match ^ADG-\\d{{2,}}$")
        if aid in register_ids:
            errors.append(f"ACTIONS.md: duplicate id {aid}")
        register_ids.append(aid)
        if not struck:
            open_ids.add(aid)

    if not register_ids:
        # An EMPTY register is valid only when context.json explicitly declares
        # zero actions (a quiet day) — otherwise it's a malformed register.
        declared_empty = (
            isinstance(ctx, dict)
            and isinstance(ctx.get("action_ids"), list)
            and len(ctx["action_ids"]) == 0
        )
        if not declared_empty:
            errors.append(
                "ACTIONS.md: no `## ADG-NN — title` register headings found "
                "(and context.json does not declare an empty set)"
            )

    # 4. register <-> briefs
    briefs_dir = set_dir / "actions"
    brief_ids: set[str] = set()
    if briefs_dir.is_dir():
        for p in sorted(briefs_dir.glob("*.md")):
            bid = p.stem
            brief_ids.add(bid)
            if not ID_RE.match(bid):
                errors.append(f"actions/{p.name}: id does not match ^ADG-\\d{{2,}}$")
            if not STAMP_RE.match(_first_nonempty_line(p.read_text())):
                errors.append(
                    f"actions/{p.name}: first non-empty line is not a `> generated:` stamp"
                )
    for aid in sorted(open_ids):
        if aid not in brief_ids:
            errors.append(f"{aid}: open in register but has no actions/{aid}.md brief")
    for bid in sorted(brief_ids):
        if bid not in register_ids:
            errors.append(f"actions/{bid}.md: brief has no matching register heading")

    # 5. context action_ids agree with register (best-effort, when ctx present)
    if isinstance(ctx, dict) and isinstance(ctx.get("action_ids"), list):
        ctx_ids = set(ctx["action_ids"])
        reg_set = set(register_ids)
        if ctx_ids != reg_set:
            errors.append(
                "context.json action_ids disagree with ACTIONS.md headings "
                f"(context-only: {sorted(ctx_ids - reg_set)}, "
                f"register-only: {sorted(reg_set - ctx_ids)})"
            )

    return errors


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: validate_action_set.py <action-set-dir>\n")
        return 2
    set_dir = Path(argv[1])
    if not set_dir.is_dir():
        sys.stderr.write(f"not a directory: {set_dir}\n")
        return 2
    errors = validate(set_dir)
    if errors:
        for e in errors:
            sys.stderr.write(e + "\n")
        sys.stderr.write(f"INVALID: {len(errors)} problem(s) in {set_dir}\n")
        return 1
    sys.stdout.write(f"OK action set valid: {set_dir}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
