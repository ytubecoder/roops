#!/usr/bin/env python3
"""bin/page_envelope.py — report-page envelope check/extract (Amendment 2, §12).

The SINGLE implementation used by both the runner's promotion gate and
dashboard/generate.py, so the two can never diverge. Stdlib only.

CLI:
  page_envelope.py check --file F [--expect-run-id ID] [--expect-loop L]
  page_envelope.py meta  --file F
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from redact import redact  # noqa: E402

MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_TOTALS_STR = 64

_ENVELOPE_RE = re.compile(
    r'<script\s+type="application/json"\s+id="report-data"\s*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
_REQUIRED_META = ("loop", "run_id", "generated_at", "title", "page_class")
_PAGE_CLASSES = ("snapshot", "findings")
# Heuristic external-fetch markers (spec §2.1). Plain <a href> anchors are
# deliberately NOT matched — navigation links are allowed, fetches are not.
_FETCH_PATTERNS = (
    ("script src", re.compile(r"<script[^>]*\bsrc\s*=", re.IGNORECASE)),
    ("link href to remote", re.compile(r"<link[^>]*\bhref\s*=\s*[\"']?https?:", re.IGNORECASE)),
    ("img src to remote", re.compile(r"<img[^>]*\bsrc\s*=\s*[\"']?https?:", re.IGNORECASE)),
    ("iframe", re.compile(r"<iframe", re.IGNORECASE)),
    ("css @import", re.compile(r"@import\b", re.IGNORECASE)),
    ("css url() to remote", re.compile(r"url\(\s*[\"']?https?:", re.IGNORECASE)),
)


def _load(path):
    """Returns (text, errors). Reads at most MAX_PAGE_BYTES + 1 bytes."""
    errors = []
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, [f"unreadable: {exc}"]
    if size == 0:
        return None, ["empty file"]
    if size > MAX_PAGE_BYTES:
        return None, [f"size {size} exceeds 8 MiB cap"]
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_PAGE_BYTES + 1)
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, ["not valid UTF-8"]
    except OSError as exc:
        return None, [f"unreadable: {exc}"]
    return text, errors


def _extract(text):
    """Returns (envelope_dict | None, errors)."""
    blocks = _ENVELOPE_RE.findall(text)
    if len(blocks) != 1:
        return None, [f"exactly one report-data envelope required, found {len(blocks)}"]
    try:
        envelope = json.loads(blocks[0].replace("<\\/", "</"))
    except (ValueError, TypeError) as exc:
        return None, [f"envelope JSON does not parse: {exc}"]
    if not isinstance(envelope, dict) or not isinstance(envelope.get("meta"), dict):
        return None, ["envelope must be an object with a meta object"]
    return envelope, []


def _validate_meta(meta):
    errors = []
    for field in _REQUIRED_META:
        value = meta.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"meta.{field} missing or not a non-empty string")
    gen = meta.get("generated_at")
    if isinstance(gen, str):
        try:
            datetime.strptime(gen, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            errors.append("meta.generated_at is not ISO8601Z (%Y-%m-%dT%H:%M:%SZ)")
    if isinstance(meta.get("page_class"), str) and meta["page_class"] not in _PAGE_CLASSES:
        errors.append(f"meta.page_class must be one of {_PAGE_CLASSES}")
    totals = meta.get("totals")
    if totals is not None:
        if not isinstance(totals, dict):
            errors.append("meta.totals must be a flat object")
        else:
            for key, value in totals.items():
                if isinstance(value, bool) or isinstance(value, (int, float)):
                    continue
                if isinstance(value, str) and len(value) <= MAX_TOTALS_STR:
                    continue
                errors.append(
                    f"meta.totals.{key} must be a number or a string of <= {MAX_TOTALS_STR} chars"
                )
    return errors


def check_page(path, expect_run_id=None, expect_loop=None):
    """Full promotion-gate check. Returns [] when promotable, else reasons."""
    text, errors = _load(path)
    if text is None:
        return errors
    envelope, extract_errors = _extract(text)
    errors.extend(extract_errors)
    if envelope is not None:
        meta = envelope["meta"]
        errors.extend(_validate_meta(meta))
        if expect_run_id is not None and meta.get("run_id") != expect_run_id:
            errors.append(f"meta.run_id {meta.get('run_id')!r} != expected {expect_run_id!r}")
        if expect_loop is not None and meta.get("loop") != expect_loop:
            errors.append(f"meta.loop {meta.get('loop')!r} != expected {expect_loop!r}")
    for label, pattern in _FETCH_PATTERNS:
        if pattern.search(text):
            errors.append(f"external fetch markup: {label}")
    if redact(text) != text:
        errors.append("redaction-clean check failed: page contains secret-shaped content")
    return errors


def read_meta(path):
    """Best-effort meta for display surfaces. None when unreadable/invalid."""
    text, _ = _load(path)
    if text is None:
        return None
    envelope, errors = _extract(text)
    if envelope is None or errors:
        return None
    return envelope["meta"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_check = sub.add_parser("check")
    p_check.add_argument("--file", required=True)
    p_check.add_argument("--expect-run-id", default=None)
    p_check.add_argument("--expect-loop", default=None)
    p_meta = sub.add_parser("meta")
    p_meta.add_argument("--file", required=True)
    args = parser.parse_args(argv)

    if args.cmd == "check":
        errors = check_page(args.file, args.expect_run_id, args.expect_loop)
        for err in errors:
            print(err, file=sys.stderr)
        return 1 if errors else 0
    meta = read_meta(args.file)
    if meta is None:
        print("no valid report-data envelope", file=sys.stderr)
        return 1
    print(json.dumps(meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
