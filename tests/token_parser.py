"""Dumb line-oriented CSS custom-property parser for token-drift tests (WP3).

Expects one `--token: value;` declaration per line (umbrella §5 / WP2 contract).
Does not handle multi-declaration lines, nested blocks, or comments mid-line.
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Set, Tuple

# Font tokens live in base :root only — never in dark blocks (WP2 placement rule).
FONT_TOKENS = frozenset({"--serif", "--mono"})

# One custom-property declaration on its own line.
_DECL_RE = re.compile(r"^\s*(--[a-zA-Z0-9-]+)\s*:\s*(.+?)\s*;\s*$")

# Opening of a token block we care about. Captures the selector form.
_BLOCK_OPEN_RE = re.compile(
    r"^(:root(?:\[data-theme=\"(?:dark|light)\"\])?)\s*\{?\s*$"
)


def parse_root_block(css_text: str) -> Dict[str, str]:
    """Parse `--token: value;` lines from a CSS fragment that is one block body.

    Accepts either a bare body (lines of declarations) or a full `:root{...}` /
    `:root[data-theme=...]{...}` block. Returns a name→value map. Ignores
    non-custom-property lines (`color-scheme:`, comments, braces, blank).
    """
    out: Dict[str, str] = {}
    for line in css_text.splitlines():
        m = _DECL_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def _extract_block_body(css_text: str, open_pattern: re.Pattern[str]) -> Optional[str]:
    """Find the first block whose opening line matches open_pattern; return its body."""
    lines = css_text.splitlines()
    i = 0
    while i < len(lines):
        if open_pattern.match(lines[i].strip()):
            # Opening may be `:root {` or `:root{` on its own line, or with `{` next.
            depth = lines[i].count("{") - lines[i].count("}")
            body_lines = []
            if depth == 0 and "{" not in lines[i]:
                # Next non-empty line should open the brace, or same line missed it.
                i += 1
                if i < len(lines) and "{" in lines[i]:
                    depth = lines[i].count("{") - lines[i].count("}")
                    # content after `{` on the brace line
                    after = lines[i].split("{", 1)[1]
                    if after.strip() and not after.strip().startswith("}"):
                        body_lines.append(after)
                    if depth == 0:
                        return "\n".join(body_lines)
                    i += 1
                else:
                    return None
            elif depth > 0:
                after = lines[i].split("{", 1)[1]
                if after.strip() and depth > 0:
                    # strip trailing } if single-line block
                    if depth == 0:
                        pass
                if "}" in lines[i] and depth == 0:
                    inner = lines[i]
                    start = inner.index("{") + 1
                    end = inner.rindex("}")
                    return inner[start:end]
                i += 1
            else:
                i += 1
                continue

            while i < len(lines) and depth > 0:
                line = lines[i]
                depth += line.count("{") - line.count("}")
                if depth > 0:
                    body_lines.append(line)
                else:
                    # last line may have trailing `}` with optional content before it
                    if "}" in line:
                        before = line.rsplit("}", 1)[0]
                        if before.strip():
                            body_lines.append(before)
                i += 1
            return "\n".join(body_lines)
        i += 1
    return None


def extract_light_tokens(css_text: str) -> Dict[str, str]:
    """Token map from the base `:root` block (not a `[data-theme]` selector).

    Font tokens are included. `color-scheme` and non-custom properties are ignored.
    """
    # Prefer a line that is exactly `:root` or `:root{` / `:root {` — not data-theme.
    body = _extract_block_body(
        css_text,
        re.compile(r"^:root\s*\{?\s*$"),
    )
    if body is None:
        return {}
    return parse_root_block(body)


def extract_dark_tokens(css_text: str) -> Dict[str, str]:
    """Token map from `:root[data-theme="dark"]` (attribute override block).

    Font tokens are stripped (they live in base `:root` only per WP2 rule).
    Prefers the attribute block over the media-query nested `:root` so both
    surfaces are compared on the same structural axis.
    """
    body = _extract_block_body(
        css_text,
        re.compile(r'^:root\[data-theme="dark"\]\s*\{?\s*$'),
    )
    if body is None:
        return {}
    tokens = parse_root_block(body)
    for ft in FONT_TOKENS:
        tokens.pop(ft, None)
    return tokens


def compare_token_maps(
    a: Dict[str, str], b: Dict[str, str]
) -> Tuple[Set[str], Set[str], Dict[str, Tuple[str, str]]]:
    """Compare two name→value maps.

    Returns (only_in_a, only_in_b, value_mismatches) where value_mismatches maps
    token → (value_in_a, value_in_b). Empty sets/dict means identical.
    """
    keys_a = set(a)
    keys_b = set(b)
    only_a = keys_a - keys_b
    only_b = keys_b - keys_a
    mismatches: Dict[str, Tuple[str, str]] = {}
    for k in keys_a & keys_b:
        if a[k] != b[k]:
            mismatches[k] = (a[k], b[k])
    return only_a, only_b, mismatches


def extract_css_string_from_generate_py(source: str) -> str:
    """Pull the CSS = \"\"\"...\"\"\" triple-quoted string from dashboard/generate.py."""
    m = re.search(r'^CSS = """(.*?)"""', source, re.MULTILINE | re.DOTALL)
    if not m:
        raise ValueError("could not find CSS = \"\"\"...\"\"\" in generate.py source")
    return m.group(1)
