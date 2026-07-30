#!/usr/bin/env python3
"""bin/skill_import.py — Phase 4 skill parser. Reads an existing Agent
Skill directory (a `SKILL.md` plus optional bundled files) into a plain
dict for downstream analysis (`loopctl import`, Task 9's `analyze()`).

    parse_skill(path) -> dict

`path` may be a directory containing `SKILL.md` or a direct path to a
`SKILL.md` file. Frontmatter parsing is intentionally dumb (stdlib only,
no YAML): only flat `key: value` lines between a leading `---` fence pair
are accepted; anything else degrades the whole frontmatter to `{}` plus a
note, leaving the raw text in `body`.

Bundled files (anything under the skill dir other than SKILL.md itself)
are read subject to caps — see MAX_FILES / MAX_FILE_BYTES below — so a
malicious or oversized skill directory can't blow up the importer.
"""

import hashlib
import os

ANALYZER_VERSION = "1"

MAX_FILES = 50
MAX_FILE_BYTES = 256 * 1024  # 256 KiB
BINARY_SNIFF_BYTES = 8 * 1024  # 8 KiB


class SkillParseError(Exception):
    """Raised only when no SKILL.md can be found at/under `path`."""


def _resolve_skill_md(path: str) -> str:
    """Return the absolute path to SKILL.md given either a skill directory
    or a direct path to SKILL.md. Raises SkillParseError if not found."""
    if os.path.isfile(path) and os.path.basename(path) == "SKILL.md":
        return os.path.abspath(path)
    if os.path.isdir(path):
        candidate = os.path.join(path, "SKILL.md")
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    raise SkillParseError(f"no SKILL.md found at {path!r}")


def _parse_frontmatter(text: str):
    """Split leading `---` frontmatter (if any) from the body. Returns
    (frontmatter: dict, body: str, notes: list[str]). Only flat
    `key: value` lines are accepted between the fences; any other line
    degrades the whole frontmatter to {} plus a note, and `body` becomes
    the original text unchanged."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text, []

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        return {}, text, []

    fm_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1 :])
    # A leading blank line right after the closing fence is expected
    # (the fixtures all have one); strip at most one so body starts at
    # the heading rather than accumulating blank noise.
    body = body.removeprefix("\n")

    frontmatter = {}
    for fm_line in fm_lines:
        if fm_line.strip() == "":
            continue
        if ":" not in fm_line:
            return {}, text, ["frontmatter not flat key:value; kept as body text"]
        key, _, value = fm_line.partition(":")
        key = key.strip()
        value = value.strip()
        # A flat line must not itself start with a nesting indicator: no
        # leading whitespace on the key, and the key must be a bare token
        # (no further structure).
        if fm_line != fm_line.lstrip() or key == "":
            return {}, text, ["frontmatter not flat key:value; kept as body text"]
        frontmatter[key] = value

    return frontmatter, body, []


def _is_binary(raw: bytes) -> bool:
    return b"\x00" in raw[:BINARY_SNIFF_BYTES]


def _collect_bundled_files(skill_dir: str, skill_md_path: str):
    """Walk skill_dir (symlinks not followed), collect candidate relpaths
    (excluding SKILL.md) sorted, apply caps, and read the survivors.
    Returns (files: list[{"relpath","text"}], notes: list[str])."""
    candidates = []
    for root, dirnames, filenames in os.walk(skill_dir, followlinks=False):
        # Don't descend into symlinked directories.
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(root, d))]
        for fname in filenames:
            fpath = os.path.join(root, fname)
            relpath = os.path.relpath(fpath, skill_dir).replace(os.sep, "/")
            if os.path.abspath(fpath) == os.path.abspath(skill_md_path):
                continue
            candidates.append((relpath, fpath))

    candidates.sort(key=lambda pair: pair[0])

    notes = []
    considered = candidates
    if len(considered) > MAX_FILES:
        skipped_count = len(considered) - MAX_FILES
        considered = considered[:MAX_FILES]
        notes.append(
            f"{skipped_count} file(s) beyond the {MAX_FILES}-file cap were skipped"
        )

    files = []
    for relpath, fpath in considered:
        if os.path.islink(fpath):
            notes.append(f"{relpath}: symlink not followed, skipped")
            continue
        try:
            with open(fpath, "rb") as f:
                raw = f.read(MAX_FILE_BYTES + 1)
        except OSError as e:
            notes.append(f"{relpath}: unreadable ({e}), skipped")
            continue

        if len(raw) > MAX_FILE_BYTES:
            notes.append(f"{relpath}: exceeds {MAX_FILE_BYTES}-byte cap, skipped")
            continue

        if _is_binary(raw):
            notes.append(f"{relpath}: binary content, skipped")
            continue

        text = raw.decode("utf-8", errors="replace")
        files.append({"relpath": relpath, "text": text})

    return files, notes


def parse_skill(path: str) -> dict:
    """Parse a SKILL.md (plus any bundled files) into a plain dict:
    {"skill_dir", "frontmatter", "body", "files", "notes", "sha256"}.

    `path` may be the skill directory or a direct path to SKILL.md.
    Raises SkillParseError only when no SKILL.md is found."""
    skill_md_path = _resolve_skill_md(path)
    skill_dir = os.path.dirname(skill_md_path)

    with open(skill_md_path, "r", encoding="utf-8", errors="replace") as f:
        skill_md_text = f.read()

    frontmatter, body, fm_notes = _parse_frontmatter(skill_md_text)
    files, file_notes = _collect_bundled_files(skill_dir, skill_md_path)

    notes = list(fm_notes) + list(file_notes)

    hasher = hashlib.sha256()
    hasher.update(skill_md_text.encode("utf-8", errors="replace"))
    for f in sorted(files, key=lambda x: x["relpath"]):
        hasher.update(f["relpath"].encode("utf-8", errors="replace"))
        hasher.update(f["text"].encode("utf-8", errors="replace"))

    return {
        "skill_dir": skill_dir,
        "frontmatter": frontmatter,
        "body": body,
        "files": files,
        "notes": notes,
        "sha256": hasher.hexdigest(),
    }
