#!/usr/bin/env python3
"""bin/redact.py — §4.4 redaction pass.

Usable two ways:
  - importable: `from redact import redact; redact(text) -> str`
  - CLI filter: stdin -> stdout, redacting matches in place.

Regex-replace secrets with `«redacted:<kind>»`, case-insensitive. Best-effort
defence in depth — never the primary control (permission axes are).
"""
import re
import sys

# Whole-text / multi-line token patterns, applied first.
_TOKEN_PATTERNS = [
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}", re.IGNORECASE)),
    ("secret-key", re.compile(r"sk-[A-Za-z0-9_-]{20,}", re.IGNORECASE)),
    ("slack-token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}", re.IGNORECASE)),
    ("aws-key", re.compile(r"AKIA[0-9A-Z]{16}", re.IGNORECASE)),
]

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)

# Generic "key: value" / "key=value" lines — keep the key name, redact the
# value only. Applied last so it doesn't fight with the token patterns above.
_KV_RE = re.compile(
    r"(api[_-]?key|secret|password|token|authorization)(\s*[:=]\s*)(\S+)",
    re.IGNORECASE,
)


def _kv_repl(m: "re.Match[str]") -> str:
    value = m.group(3)
    if value.startswith("«redacted:"):
        # Already redacted by a more specific pattern above — leave it.
        return m.group(0)
    return f"{m.group(1)}{m.group(2)}«redacted:secret»"


def redact(text: str) -> str:
    if text is None:
        return text
    out = _PRIVATE_KEY_RE.sub("«redacted:private-key»", text)
    for kind, pattern in _TOKEN_PATTERNS:
        out = pattern.sub(f"«redacted:{kind}»", out)
    out = _KV_RE.sub(_kv_repl, out)
    return out


def main() -> int:
    data = sys.stdin.read()
    sys.stdout.write(redact(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
