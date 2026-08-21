#!/usr/bin/env bash
# gc-actions/precheck.sh — deterministic, zero-judgment gathering (script→agent
# pattern, INTERFACES §4.1/§6.2). Plain unsandboxed runner-invoked script.
#
# Emits a compact digest of:
#   1. every DMP/CRO action register under the DMP output root — register.yaml
#      preferred, ACTIONS.md fallback (both hand-written and generated formats),
#      condensed to one line per action: id | status | title;
#   2. the action↔ticket map (gc-actions/action-ticket-map.yaml) — covered ids;
#   3. the maguyva-actions board state (PRODUCT_BACKLOG.md) — one line per
#      ticket: id | section | action-ids found in its description;
#   4. the MECHANICAL set difference: open action ids not covered by map or
#      board (set arithmetic is deterministic — the engine judges what to DO
#      about the gaps, not what the gaps are).
# Never mutates anything. Read-only file access + one tickets DB read via the
# generated board markdown (no CLI invocation, no sqlite).
set -euo pipefail

DMP_ROOT="${DMP_OUTPUT_DIR:-$HOME/projects/digital-marketing-pro/output/maguyva}"
GC_ACTIONS_DIR="${GC_ACTIONS_DIR:-$HOME/projects/maguyva-marketing/gc-actions}"

DMP_ROOT="$DMP_ROOT" GC_ACTIONS_DIR="$GC_ACTIONS_DIR" python3 <<'PY'
import os, re, sys
from pathlib import Path

dmp_root = Path(os.environ["DMP_ROOT"])
gc_dir = Path(os.environ["GC_ACTIONS_DIR"])
map_path = gc_dir / "action-ticket-map.yaml"
board_path = gc_dir / "PRODUCT_BACKLOG.md"


def load_onboarded_prefixes(gc_dir):
    """Registry-derived onboarded id-prefixes from action-sources.yaml (spec
    docs/superpowers/specs/2026-08-21-action-source-onboarding-design.md §4.5).
    Line-oriented stdlib parse, no PyYAML — never fatal, always falls back to
    the literal list on any registry problem. Keep in sync with
    bin/apply_tickets.py's copy of this function.
    """
    fallback = ["CRO", "AEO", "SEO", "COMP", "ALL"]
    reg_path = gc_dir / "action-sources.yaml"
    try:
        text = reg_path.read_text()
    except OSError as e:
        print(
            f"precheck: cannot read {reg_path} ({e}) — falling back to literal prefix list",
            file=sys.stderr,
        )
        return fallback

    blocks = []
    block_lines = []
    in_sources = False

    def flush():
        if block_lines:
            blocks.append("\n".join(block_lines))
            block_lines.clear()

    for line in text.splitlines():
        if re.match(r"^[A-Za-z_][\w-]*:", line):
            # top-level key — leaves (or re-enters) the sources: section
            flush()
            in_sources = line.startswith("sources:")
            continue
        if not in_sources:
            continue
        if re.match(r"^  - id:", line):
            flush()
        block_lines.append(line)
    flush()

    prefixes = set()
    for block in blocks:
        if not re.search(r"^\s*status:\s*onboarded\b", block, re.M):
            continue
        for m in re.finditer(r"prefix:\s*([A-Za-z0-9_-]+)", block):
            p = m.group(1)
            if re.match(r"^[A-Z]{2,6}$", p):
                prefixes.add(p)
            else:
                print(
                    f"precheck: skipping malformed prefix {p!r} in {reg_path}",
                    file=sys.stderr,
                )

    if not prefixes:
        print(
            f"precheck: no onboarded prefixes found in {reg_path} — falling back to literal prefix list",
            file=sys.stderr,
        )
        return fallback

    return sorted(prefixes) + ["ALL"]


ID_RE = re.compile(r"\b((?:" + "|".join(load_onboarded_prefixes(gc_dir)) + r")-\d{2})\b")
problems = []

# --- 1. registers -----------------------------------------------------------
# {action_id: (status, title, source)} — register.yaml wins over ACTIONS.md
# for the same run dir; across run dirs the NEWEST dir (name sort) wins for a
# given id prefix family, but we list every dir so the engine sees history.
actions = {}

def note(aid, status, title, src):
    actions[aid] = (status, title, src)

def parse_register_yaml(text, src):
    cur = None
    for line in text.splitlines():
        m = re.match(r"^- id:\s*([A-Z]+-\d{2})", line)
        if m:
            cur = m.group(1)
            note(cur, "open", "", src)
            continue
        if cur:
            t = re.match(r"^\s+title:\s*(.+)$", line)
            if t and not actions[cur][1]:
                note(cur, actions[cur][0], t.group(1).strip(), src)
            s = re.match(r"^\s+status:\s*(\w+)", line)
            if s:
                note(cur, s.group(1), actions[cur][1], src)

def parse_actions_md(text, src):
    # generated struck form: ## ~~ID — title~~ ; open/hand-written: ## ID — title
    for m in re.finditer(r"^## (~~)?([A-Z]+-\d{2}) — (.+?)(~~)?\s*$", text, re.M):
        struck = bool(m.group(1))
        aid, title = m.group(2), m.group(3)
        # hand-written struck convention: a "- **Struck" bullet in the body
        body_start = m.end()
        nxt = text.find("\n## ", body_start)
        body = text[body_start : nxt if nxt != -1 else len(text)]
        if re.search(r"^- \*\*Struck", body, re.M):
            struck = True
        note(aid, "struck" if struck else "open", title, src)

# Iterate oldest → newest so the NEWEST run dir wins per action id. A date-only
# dir name sorts as time 0000, so 2026-07-26-2217-cro-audit beats the legacy
# 2026-07-26-cro-audit sibling (mirrors console/runnames.py ordering).
def dir_key(p):
    m = re.match(r"^(\d{4}-\d{2}-\d{2})(?:-(\d{4}))?", p.name)
    return (m.group(1), m.group(2) or "0000", p.name) if m else ("0000-00-00", "0000", p.name)

run_dirs = sorted((p for p in dmp_root.iterdir() if p.is_dir()), key=dir_key) if dmp_root.is_dir() else []
if not run_dirs:
    problems.append(f"no run dirs under {dmp_root}")
registers_scanned = 0
for d in run_dirs:
    reg = d / "actions" / "register.yaml"
    amd = d / "ACTIONS.md"
    try:
        if reg.is_file():
            parse_register_yaml(reg.read_text(), d.name)
            registers_scanned += 1
        elif amd.is_file():
            parse_actions_md(amd.read_text(), d.name)
            registers_scanned += 1
    except Exception as e:
        problems.append(f"unparseable register in {d.name}: {e}")

# --- 2. covered ids from the map -------------------------------------------
covered = {}  # aid -> (row, disposition)
if map_path.is_file():
    row, disp = None, "?"
    for line in map_path.read_text().splitlines():
        if line.strip().startswith("#"):
            continue
        rm = re.match(r"^\s*- row:\s*(\S+)", line)
        if rm:
            row, disp = rm.group(1), "?"
            continue
        dm = re.match(r"^\s*disposition:\s*(\S+)", line)
        if dm:
            disp = dm.group(1)
            if row:
                for a in list(covered):
                    if covered[a] == (row, "?"):
                        covered[a] = (row, disp)
            continue
        if row:
            for aid in ID_RE.findall(line):
                covered.setdefault(aid, (row, disp))
else:
    problems.append(f"missing map file {map_path}")

# --- 3. board state ---------------------------------------------------------
board = []  # (ticket_id, section, action_ids)
if board_path.is_file():
    section = "?"
    for line in board_path.read_text().splitlines():
        sm = re.match(r"^## (.+)$", line)
        if sm:
            section = sm.group(1).strip()
            continue
        tm = re.match(r"^### ([A-Z]+-\d+):\s*(.+)$", line)
        if tm:
            board.append([tm.group(1), section, set(), tm.group(2)])
            continue
        if board and not line.startswith("#"):
            board[-1][2].update(ID_RE.findall(line))
else:
    problems.append(f"missing board file {board_path}")

board_covered = set()
for _, _, ids, _ in board:
    board_covered.update(ids)

# --- 4. mechanical set difference ------------------------------------------
open_ids = sorted(a for a, (st, _, _) in actions.items() if st == "open")
struck_ids = sorted(a for a, (st, _, _) in actions.items() if st == "struck")
# A map row with disposition `uncovered` means "no ticket and no ruling yet —
# KEEP surfacing": it never counts as coverage.
really_covered = {a for a, (_, d) in covered.items() if d != "uncovered"}
uncovered = [a for a in open_ids if a not in really_covered and a not in board_covered]
# register says struck but the map still calls it ticketed — surfaced
# mechanically; the ENGINE judges whether that is a real conflict.
struck_vs_map = sorted(
    a for a in struck_ids if covered.get(a, ("", ""))[1] == "ticketed")

print("# gc-actions precheck digest")
print(f"dmp_root: {dmp_root}")
print(f"registers_scanned: {registers_scanned}")
print(f"actions_total: {len(actions)}  open: {len(open_ids)}  struck: {len(struck_ids)}")
print(f"covered_by_map: {len(covered)}  covered_by_board: {len(board_covered)}")
print(f"UNCOVERED_OPEN_ACTIONS: {len(uncovered)}")
print(f"STRUCK_BUT_MAP_TICKETED: {len(struck_vs_map)}")
print()
print("## Registers (id | status | title | source)")
for aid in sorted(actions):
    st, title, src = actions[aid]
    print(f"{aid} | {st} | {title} | {src}")
print()
print("## Map coverage (action id -> map row, disposition)")
for aid in sorted(covered):
    row, disp = covered[aid]
    print(f"{aid} -> {row} ({disp})")
print()
print("## Struck in register but map disposition=ticketed")
for aid in struck_vs_map:
    print(f"{aid} | {actions[aid][1]} | {covered[aid][0]}")
if not struck_vs_map:
    print("(none)")
print()
print("## Board (ticket | section | action ids | title)")
for tid, section, ids, title in board:
    print(f"{tid} | {section} | {','.join(sorted(ids)) or '-'} | {title}")
print()
print("## UNCOVERED open actions (no map row, no board ticket)")
if uncovered:
    for aid in uncovered:
        st, title, src = actions[aid]
        print(f"{aid} | {title} | {src}")
else:
    print("(none)")
print()
print("## Problems")
if problems:
    for p in problems:
        print(f"- {p}")
else:
    print("(none)")
PY
