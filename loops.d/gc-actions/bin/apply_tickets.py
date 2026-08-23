#!/usr/bin/env python3
"""gc-actions post-promotion apply hook (invoked by render.sh).

Reads the PROMOTED latest.json (suppressed findings already filtered out by
the runner), extracts `create_ticket` ops from finding details, dedupes
against the action↔ticket map (disposition-aware) and the board file, and
creates tickets in the maguyva-actions project's IDEAS section via the
ticket-add probe. That is the ONLY write it performs: it never moves, edits,
accepts, or strikes anything, and it never touches any other project.

Idempotent by construction: a created ticket's description carries the
`[loop:gc-actions | <ID>]` prefix, so the id is board-covered on any re-run.
"""

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT = "maguyva-actions"
MAX_OPS = 20  # sanity cap per run — a promoted run proposing more is suspect
FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _loops_root() -> Path:
    raw = os.environ.get("LOOPS_ROOT")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[3]


def _probe_core():
    root = _loops_root()
    bin_dir = str(root / "bin")
    if bin_dir not in sys.path:
        sys.path.insert(0, bin_dir)
    from probe_core import extract_tar  # noqa: WPS433

    return extract_tar


def fetch_gc_dir() -> Path:
    """Fresh board snapshot via gc-actions-files (board may have changed)."""
    extract_tar = _probe_core()
    root = _loops_root()
    tmp = tempfile.mkdtemp(prefix="gc-actions-files-")
    tar_path = os.path.join(tmp, "gc.tar")
    dest = os.path.join(tmp, "gc")
    r = subprocess.run(
        [str(root / "bin" / "probe"), "gc-actions-files", "--out", tar_path],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if r.returncode == 75:
        print(
            "apply_tickets: probe transport failed (llm unreachable)",
            file=sys.stderr,
        )
        sys.exit(1)
    if r.returncode != 0:
        print(
            f"apply_tickets: gc-actions-files probe failed: {(r.stderr or '').strip()}",
            file=sys.stderr,
        )
        sys.exit(1)
    extract_tar(tar_path, dest)
    return Path(dest)


def create_ticket(title: str, prio: str, desc: str) -> subprocess.CompletedProcess:
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "project": PROJECT,
                "title": title,
                "section": "ideas",
                "priority": prio,
                "description": desc,
            }
        ).encode()
    ).decode().rstrip("=")
    return subprocess.run(
        [str(_loops_root() / "bin" / "probe"), "ticket-add", payload],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def load_onboarded_prefixes(gc_dir: Path) -> list[str]:
    """Registry-derived onboarded id-prefixes from action-sources.yaml (spec
    docs/superpowers/specs/2026-08-21-action-source-onboarding-design.md §4.5).
    Line-oriented stdlib parse, no PyYAML — never fatal, always falls back to
    the literal list on any registry problem. Keep in sync with
    precheck.sh's copy of this function.
    """
    fallback = ["CRO", "AEO", "SEO", "COMP", "ALL"]
    reg_path = gc_dir / "action-sources.yaml"
    try:
        text = reg_path.read_text()
    except OSError as e:
        print(
            f"apply_tickets: cannot read {reg_path} ({e}) — falling back to literal prefix list",
            file=sys.stderr,
        )
        return fallback

    blocks = []
    block_lines: list[str] = []
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
                    f"apply_tickets: skipping malformed prefix {p!r} in {reg_path}",
                    file=sys.stderr,
                )

    if not prefixes:
        print(
            f"apply_tickets: no onboarded prefixes found in {reg_path} — falling back to literal prefix list",
            file=sys.stderr,
        )
        return fallback

    return sorted(prefixes) + ["ALL"]


def map_covered_ids(map_path: Path, id_re: re.Pattern) -> set[str]:
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
        ids.update(id_re.findall(line))
    rows.append((disp, ids))
    covered: set[str] = set()
    for d, i in rows:
        if d != "uncovered":
            covered.update(i)
    return covered


def board_ids(board_path: Path, id_re: re.Pattern) -> set[str]:
    if not board_path.is_file():
        return set()
    return set(id_re.findall(board_path.read_text()))


def main() -> int:
    latest = os.environ.get("LATEST_JSON")
    if not latest or not Path(latest).is_file():
        print(f"apply_tickets: LATEST_JSON missing ({latest})", file=sys.stderr)
        return 1
    gc_dir = fetch_gc_dir()
    id_re = re.compile(
        r"\b((?:" + "|".join(load_onboarded_prefixes(gc_dir)) + r")-\d{2})\b"
    )
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

    covered = map_covered_ids(gc_dir / "action-ticket-map.yaml", id_re) | board_ids(
        gc_dir / "PRODUCT_BACKLOG.md", id_re
    )
    created = skipped = failed = 0
    for op in ops:
        ids = [i for i in op.get("action_ids", []) if id_re.fullmatch(i)]
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
        r = create_ticket(title, prio, desc)
        if r.returncode == 75:
            print(
                f"apply_tickets: probe transport failed (llm unreachable) for {'/'.join(ids)}",
                file=sys.stderr,
            )
            failed += 1
            continue
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
