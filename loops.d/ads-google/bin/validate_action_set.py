#!/usr/bin/env python3
"""validate_action_set.py — validate a produced ads-google action set.

Ships with the ads-google loop and is run INSIDE the engine session via the
loop's exec allowlist (docs LOOP_AUTHORING.md — the harness has no post-engine
hook, so validation is an allowlisted local command). It is also importable by
emit_action_set.py (which calls it as its final step) and runnable standalone
so a human can re-check any set:

    python3 validate_action_set.py <action-set-dir> [--continuity <continuity.json>]

Checks (stdlib only, per the harness ground rules):
  1. context.json exists, parses, and carries the required keys.
  2. ACTIONS.md exists and its first non-empty line is a `> generated:` stamp.
  3. Every register heading id matches ^ADG-\\d{2,}$ (two-or-more digits;
     daily loops outlive 99), with no duplicate ids.
  4. Register <-> briefs consistency: every OPEN (non-struck) register id has a
     brief at actions/<ID>.md, and every brief file id appears in the register.
  5. Every brief's first non-empty line is a `> generated:` stamp.
  6. ID CONTINUITY (only with --continuity, written by precheck.sh):
     (a) REUSE — every id is either carried forward from the prior set or
         strictly above the high-water mark. Catches the aba304-style ghost
         reuse (ids burned by an unpersisted run handed to new actions).
     (b) COMPLETENESS — every `prior_open_ids` entry appears in this register
         (open or newly struck). A set is COMPLETE truth: a silent restart or
         silent drop of a live action must fail even when its low ids collide
         with genuinely-prior ids (reuse alone cannot see that case).

Exit 0 = valid, 1 = invalid (reasons to stderr, one per line), 2 = usage.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

LOOP = "ads-google"
PREFIX = "ADG"
# Provenance designators this loop may mint (docs/actionator-warmstart.md in
# the marketing repo): EV evaluator · CMP campaign/delivery · JRN journal/
# guard · BUD budget/caps · INP input gap. ads-program instead mints
# PRG · BUD · INP. New ids are two-part `PREFIX-SRC-NN`; single-part legacy
# ids remain valid ONLY while carried forward — never minted anew.
ALLOWED_SOURCES = ("EV", "CMP", "JRN", "BUD", "INP")
_SRC_ALT = "|".join(ALLOWED_SOURCES)
ID_RE = re.compile(rf"^{PREFIX}-(?:(?:{_SRC_ALT})-)?\d{{2,}}$")
# Heading capture is deliberately looser ([A-Z]+) so an id with an INVALID
# source still parses as an id and fails the ID_RE check with a clear error,
# instead of vanishing from the register.
HEADING_RE = re.compile(
    rf"^##\s+(~~)?\s*({PREFIX}-(?:[A-Z]+-)?\d+)\s+[—-]\s+(.*?)(~~)?\s*$"
)
STAMP_RE = re.compile(r"^>\s*generated:\s*\S")
REQUIRED_CONTEXT_KEYS = {"loop", "run_id", "generated", "engine", "action_ids"}
ID_NUM_RE = re.compile(rf"^{PREFIX}-(?:[A-Z]+-)?(\d+)$")
ID_SRC_RE = re.compile(rf"^{PREFIX}-([A-Z]+)-\d+$")


def _first_nonempty_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.rstrip("\n")
    return ""


def _continuity_errors(register_ids: list[str], continuity: dict) -> list[str]:
    """Ids must be carried forward from the prior set, or above the high-water mark.

    `continuity` is precheck.sh's continuity.json: {high_water, prior_ids, ...}.
    """
    errors: list[str] = []
    try:
        high_water = int(continuity.get("high_water") or 0)
    except (TypeError, ValueError):
        return ["continuity: high_water is not an integer"]
    prior_ids = set(continuity.get("prior_ids") or [])

    for aid in register_ids:
        m = ID_NUM_RE.match(aid)
        if not m:
            continue  # shape already reported by the id-pattern check
        if aid in prior_ids:
            continue  # legitimately carried forward
        if not ID_SRC_RE.match(aid):
            errors.append(
                f"{aid}: NEW ids must carry a source designator — "
                f"{PREFIX}-<SRC>-NN with SRC one of "
                f"{', '.join(ALLOWED_SOURCES)}. Only ids carried forward from "
                "the prior set may keep the legacy single-part shape."
            )
        if int(m.group(1)) <= high_water:
            errors.append(
                f"{aid}: REUSED id — it is not in the prior set yet is at or below "
                f"the high-water mark {high_water}. New actions must start at "
                f"{PREFIX}-<SRC>-{high_water + 1:02d}. Ids are never reused, even "
                "after a strike."
            )

    # Completeness: every prior OPEN action must be carried forward (open or
    # newly struck) — a set is the COMPLETE current truth, so silently dropping
    # a live action (the "restart" failure mode) is an error even when the new
    # set's ids collide with prior ids and the reuse arm cannot see it.
    present = set(register_ids)
    for pid in continuity.get("prior_open_ids") or []:
        if pid not in present:
            errors.append(
                f"{pid}: prior OPEN action is missing from this register — a set "
                "is complete truth: carry it forward under its id or strike it "
                "with a struck_reason, never drop it."
            )
    return errors


def validate(set_dir: Path, continuity: dict | None = None) -> list[str]:
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

    # 3. headings / ids (+ a struck heading must carry a `- **Struck:** <why>`
    #    line before the next heading — an unexplained strike is unauditable)
    current_struck_id = None
    struck_reason_seen = True
    for line in reg_text.splitlines():
        m = HEADING_RE.match(line)
        if not m:
            if (
                current_struck_id
                and line.strip().startswith("- **Struck:**")
                and line.strip()[len("- **Struck:**") :].strip()
            ):
                struck_reason_seen = True
            continue
        if current_struck_id and not struck_reason_seen:
            errors.append(
                f"{current_struck_id}: struck without a `- **Struck:** <reason>` "
                "line — every strike must say why (resolution evidence)."
            )
        struck = bool(m.group(1) or m.group(4))
        aid = m.group(2)
        current_struck_id = aid if struck else None
        struck_reason_seen = not struck
        if not ID_RE.match(aid):
            errors.append(f"ACTIONS.md: id {aid!r} does not match {ID_RE.pattern}")
        if aid in register_ids:
            errors.append(f"ACTIONS.md: duplicate id {aid}")
        register_ids.append(aid)
        if not struck:
            open_ids.add(aid)
    if current_struck_id and not struck_reason_seen:
        errors.append(
            f"{current_struck_id}: struck without a `- **Struck:** <reason>` "
            "line — every strike must say why (resolution evidence)."
        )

    # ONE number sequence per loop, shared across sources: ADG-CMP-08 and
    # ADG-JRN-08 are the same slot, not two actions.
    seen_nums: dict[int, str] = {}
    for aid in register_ids:
        m = ID_NUM_RE.match(aid)
        if not m:
            continue
        n = int(m.group(1))
        if n in seen_nums and seen_nums[n] != aid:
            errors.append(
                f"{aid}: numeric part {n} already used by {seen_nums[n]} — the "
                "number sequence is per-loop, shared across all sources."
            )
        else:
            seen_nums[n] = aid

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
                errors.append(f"actions/{p.name}: id does not match {ID_RE.pattern}")
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

    # 6. id continuity (opt-in: only when precheck's continuity.json is supplied)
    if isinstance(continuity, dict):
        errors.extend(_continuity_errors(register_ids, continuity))

    return errors


# Printed on failure so the in-session engine gets the protocol at the point of
# failure, not just a diagnosis. Product decision (Generalissimo, 2026-07-28).
FAILURE_PROTOCOL = (
    "REMEDY: fix the payload and re-emit. If the set still cannot be written, "
    "report the analysis anyway (full report_markdown + headline), set "
    "status=alert with a precise status_reason, and emit ZERO findings "
    "(findings: []). No ADG- id may enter the findings list without a durable "
    "set behind it. Empty findings is also what lets the declared alert surface "
    "— per INTERFACES.md 4.5 a non-empty findings array overrides the declared "
    "status with the findings' max severity."
)


def main(argv: list[str]) -> int:
    args = argv[1:]
    continuity = None
    if "--continuity" in args:
        i = args.index("--continuity")
        if i + 1 >= len(args):
            sys.stderr.write("--continuity requires a path\n")
            return 2
        cpath = Path(args[i + 1])
        del args[i : i + 2]
        if cpath.is_file():
            try:
                continuity = json.loads(cpath.read_text())
            except (json.JSONDecodeError, ValueError) as exc:
                sys.stderr.write(f"--continuity file not parseable: {exc}\n")
                return 2
        # A missing continuity file is not fatal: the check is defence in depth,
        # and a first-ever run legitimately has none.

    if len(args) != 1:
        sys.stderr.write(
            "usage: validate_action_set.py <action-set-dir> "
            "[--continuity <continuity.json>]\n"
        )
        return 2
    set_dir = Path(args[0])
    if not set_dir.is_dir():
        sys.stderr.write(f"not a directory: {set_dir}\n")
        return 2
    errors = validate(set_dir, continuity)
    if errors:
        for e in errors:
            sys.stderr.write(e + "\n")
        sys.stderr.write(f"INVALID: {len(errors)} problem(s) in {set_dir}\n")
        sys.stderr.write(FAILURE_PROTOCOL + "\n")
        return 1
    sys.stdout.write(f"OK action set valid: {set_dir}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
