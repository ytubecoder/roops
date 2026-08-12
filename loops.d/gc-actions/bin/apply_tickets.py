#!/usr/bin/env python3
"""gc-actions post-promotion apply hook (invoked by render.sh).

Reads the PROMOTED latest.json (suppressed findings already filtered out by
the runner), extracts `create_ticket` ops from finding details, dedupes
against the action↔ticket map (disposition-aware) and the board file, and
creates tickets in the maguyva-actions project's IDEAS section via
tickets-cli. That is the ONLY write it performs: it never moves, edits,
accepts, or strikes anything, and it never touches any other project.

Idempotent by construction: a created ticket's description carries the
`[loop:gc-actions | <ID>]` prefix, so the id is board-covered on any re-run.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

CLI = Path.home() / ".claude/ticket-takeaway/tickets-cli.py"
GC_DIR = Path(
    os.environ.get("GC_ACTIONS_DIR")
    or Path.home() / "projects/maguyva-marketing/gc-actions"
)
PROJECT = "maguyva-actions"
MAX_OPS = 20  # sanity cap per run — a promoted run proposing more is suspect

ID_RE = re.compile(r"\b((?:CRO|AEO|SEO|COMP|ALL)-\d{2})\b")
FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def map_covered_ids(map_path: Path) -> set[str]:
    """Action ids the map covers — EXCLUDING disposition `uncovered` rows
    (those are exactly the ids the loop is allowed to ticket)."""
    if not map_path.is_file():
        return set()
    rows: list[tuple[str, set[str]]] = []  # (disposition, ids)
    disp, ids = "?", set()
    for line in map_path.read_text().splitlines():
        if line.strip().startswith("#"):
            continue
        if re.match(r"^\s*- row:", line):
            rows.append((disp, ids))
            disp, ids = "?", set()
            continue
        m = re.match(r"^\s*disposition:\s*(\S+)", line)
        if m:
            disp = m.group(1)
            continue
        ids.update(ID_RE.findall(line))
    rows.append((disp, ids))
    covered: set[str] = set()
    for d, i in rows:
        if d != "uncovered":
            covered.update(i)
    return covered


def board_ids(board_path: Path) -> set[str]:
    if not board_path.is_file():
        return set()
    return set(ID_RE.findall(board_path.read_text()))


def main() -> int:
    latest = os.environ.get("LATEST_JSON")
    if not latest or not Path(latest).is_file():
        print(f"apply_tickets: LATEST_JSON missing ({latest})", file=sys.stderr)
        return 1
    contract = json.loads(Path(latest).read_text())
    ops = []
    for f in contract.get("findings", []):
        for block in FENCE_RE.findall(f.get("detail", "")):
            try:
                op = json.loads(block)
            except json.JSONDecodeError:
                print(
                    f"apply_tickets: unparseable op in {f.get('finding_id')}",
                    file=sys.stderr,
                )
                continue
            if op.get("op") == "create_ticket":
                op["_finding"] = f.get("finding_id", "?")
                ops.append(op)

    if not ops:
        print("apply_tickets: no create_ticket ops in promoted contract")
        return 0
    if len(ops) > MAX_OPS:
        print(
            f"apply_tickets: REFUSING {len(ops)} ops (> {MAX_OPS} sanity cap)",
            file=sys.stderr,
        )
        return 1

    covered = map_covered_ids(GC_DIR / "action-ticket-map.yaml") | board_ids(
        GC_DIR / "PRODUCT_BACKLOG.md"
    )
    created = skipped = failed = 0
    for op in ops:
        ids = [i for i in op.get("action_ids", []) if ID_RE.fullmatch(i)]
        title = (op.get("title") or "").strip()
        desc = (op.get("description") or "").strip()
        prio = (
            op.get("priority")
            if op.get("priority") in ("high", "medium", "low")
            else "medium"
        )
        if not ids or not title or not desc.startswith("[loop:gc-actions"):
            print(
                f"apply_tickets: malformed op from {op['_finding']} — skipped",
                file=sys.stderr,
            )
            failed += 1
            continue
        if any(i in covered for i in ids):
            print(f"apply_tickets: {'/'.join(ids)} already covered — skipped")
            skipped += 1
            continue
        r = subprocess.run(
            [
                "python3",
                str(CLI),
                "add",
                PROJECT,
                title,
                "--section",
                "ideas",
                "--priority",
                prio,
                "--description",
                desc,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if r.returncode == 0:
            print(
                f"apply_tickets: created Ideas ticket for {'/'.join(ids)}: {r.stdout.strip()}"
            )
            covered.update(ids)
            created += 1
        else:
            print(
                f"apply_tickets: CLI failed for {'/'.join(ids)}: {r.stderr.strip()}",
                file=sys.stderr,
            )
            failed += 1

    print(
        f"apply_tickets: done — created {created}, skipped {skipped}, failed {failed}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
