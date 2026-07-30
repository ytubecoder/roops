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
import importlib.util
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_schedule_module():
    """Dynamically load bin/schedule.py — the single schedule-grammar parser
    (bin/loopconf.py's own `_load_schedule_module()` does the exact same
    thing for the exact same reason: `schedule.py` is a frozen sibling
    module, not a package, so a plain `import schedule` would depend on
    ambient `sys.path` state this module shouldn't have to assume)."""
    spec = importlib.util.spec_from_file_location(
        "_skill_import_schedule", os.path.join(_HERE, "schedule.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_schedule = _load_schedule_module()

ANALYZER_VERSION = "1"

MAX_FILES = 50
MAX_FILE_BYTES = 256 * 1024  # 256 KiB
BINARY_SNIFF_BYTES = 8 * 1024  # 8 KiB

# --- Task 9: static analyzer -------------------------------------------------
#
# Detection heuristics (regex, case-insensitive, over body + all bundled files'
# text). Kept as module-level compiled constants on purpose: they double as
# documentation of exactly what trips each flag (docs/SKILL_IMPORT.md, Task 11,
# transcribes this table) and are unit-testable in isolation.
RE_INTERACTIVITY = re.compile(
    r"ask the user|askuserquestion|wait for (approval|confirmation)|prompt the user",
    re.IGNORECASE,
)
RE_MUTATION = re.compile(
    r"\bgit push\b|\bdeploy\b|\bnpm publish\b|\bsend (an? )?(email|sms|message)\b"
    r"|\bpost to\b|gh pr create|\brm -rf\b",
    re.IGNORECASE,
)
RE_MCP = re.compile(r"\bmcp__[a-z0-9_]+__[a-z0-9_]+\b|\bMCP\b", re.IGNORECASE)
RE_CREDENTIALS = re.compile(
    r"api[_ -]?key|oauth|bearer token|[A-Z][A-Z0-9_]{4,}_(KEY|TOKEN|SECRET)|credentials?\b",
    re.IGNORECASE,
)
RE_ITERATION = re.compile(
    r"until (it|the test|tests) pass|retry until|keep trying|iterate until",
    re.IGNORECASE,
)
RE_NETWORK = re.compile(r"\bcurl\b|\bhttps?://|api call|fetch\b|webhook", re.IGNORECASE)

# Claude-idiom markers -> engine recommendation of "claude" instead of "codex".
RE_CLAUDE_IDIOM = re.compile(
    r"mcp__|AskUserQuestion|\.claude/|allowed-tools", re.IGNORECASE
)

# CLIs that count as "a CLI equivalent exists" for the mcp-without-cli-equivalent
# blocked rule. `curl` is deliberately a member (the brief calls it out by name)
# rather than a special case.
KNOWN_CLIS = (
    "curl",
    "git",
    "gh",
    "aws",
    "gcloud",
    "kubectl",
    "docker",
    "npm",
    "npx",
    "yarn",
    "pnpm",
    "stripe",
    "vercel",
    "wrangler",
    "psql",
    "mysql",
    "ssh",
    "rsync",
    "terraform",
    "ansible",
    "supabase",
)
_CLI_TOKEN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(c) for c in KNOWN_CLIS) + r")\b", re.IGNORECASE
)

# First-token(s) that make a candidate precheck command line "read-only?" per
# the brief's scoped-read-forms table. Single-word commands are read-only by
# name alone; `git` needs its subcommand inspected (status|log|diff only).
READ_ONLY_SIMPLE_COMMANDS = frozenset(
    {
        "ls",
        "find",
        "grep",
        "rg",
        "wc",
        "cat",
        "curl",
        "head",
        "tail",
        "stat",
        "du",
        "df",
    }
)
READ_ONLY_GIT_SUBCOMMANDS = frozenset({"status", "log", "diff"})
# git flags that consume the following token as their value (so it isn't
# mistaken for the subcommand) — `-C <repo>` is the common one in practice.
_GIT_VALUE_FLAGS = frozenset({"-C", "-c"})

# Binaries a precheck candidate's leading token must plausibly be (fix-round-1
# #b): keeps bare words like `metrics` or lone paths pulled out of inline
# backticks from ever being emitted as a precheck line at all.
KNOWN_COMMAND_TOKENS = (
    READ_ONLY_SIMPLE_COMMANDS
    | frozenset(KNOWN_CLIS)
    | frozenset(
        {
            "rm",
            "mv",
            "cp",
            "kill",
            "xargs",
            "echo",
            "touch",
            "mkdir",
            "chmod",
            "chown",
            "sed",
            "awk",
            "tar",
            "zip",
            "unzip",
            "brew",
            "pip",
            "pip3",
            "python",
            "python3",
            "node",
            "make",
            "cargo",
            "go",
        }
    )
)

# fenced-code-block languages that count as shell for precheck extraction
# (fix-round-1 minor a): ```bash, ```sh, ```shell, tolerating info-string
# suffixes like ```bash icon.
_SHELL_FENCE_LANGS = frozenset({"bash", "sh", "shell"})

# Redirect forms tolerated as "not dangerous" for the read-only check: stderr
# to /dev/null or to stdout, both common in read-only diagnostic one-liners
# (e.g. the clean-check fixture's `2>/dev/null`). Anything else involving
# `>`/`>>` is treated as a write. Trailing `(?![^\s])` boundary (fix-round-2
# minor) so `>/dev/nullx` (a real file, not /dev/null) isn't matched as a
# prefix of the tolerated form.
_SAFE_REDIRECTS_RE = re.compile(r"(?:[12]?>&[12]|[12]?>\s*/dev/null)(?![^\s])")

# `<repo>`/`<p>`/`<target>`-style doc placeholders — these fixtures' own
# convention (e.g. `git -C <repo> status`) — contain a literal `>` that must
# not be mistaken for shell redirection by the dangerous-redirect check.
# fix-round-2 #1: whitespace-delimited on BOTH sides (not just non-nested) —
# the naive version matched `<payload>` inside `cat <payload>/etc/hosts`
# (real stdin+stdout redirection: `<payload` then `>/etc/hosts`), swallowing
# the dangerous `>` along with it. Requiring the token to be flanked by
# whitespace/string-edges keeps `git -C <repo> status` safe while refusing to
# strip `<payload>` when it's glued to what follows (`<a>b`, `<url>/tmp/x`).
_PLACEHOLDER_RE = re.compile(r"(?<![^\s])<[^<>\s]+>(?![^\s])")

RUBRIC_IDS = (
    "q1_purpose",
    "q2_pattern",
    "q3_type",
    "q4_cadence",
    "q5_scope",
    "q6_guardrails",
    "q7_axes",
    "q8_finding_identity",
    "q9_semantics",
    "q10_metrics",
    "q11_budget",
)
RUBRIC_BUCKETS = ("answered", "derived", "missing", "incompatible")
# answers_needed question_ids for the per-axis raise questions — outside the
# q1..q11 rubric namespace on purpose (they aren't intake-rubric answers,
# they're axis-raise proposals gated on the mutation/network flags).
AXIS_RAISE_IDS = ("raise_perm_network", "raise_perm_remote_mutation")

# Headings that count as an actual scope/exclusions statement for q5_scope
# (fix-round-1 #6) — a bare document title (e.g. "# Repo hygiene check") does
# NOT count; the heading itself must look like it's declaring scope.
_SCOPE_HEADING_RE = re.compile(
    r"\bscope\b|\bexclusions?\b|out of scope|what this does not do", re.IGNORECASE
)

# Numeric-content hint shared by q10's options and context text (fix-round-1
# minor c — was duplicated inline in both).
_Q10_NUMERIC_HINT_RE = re.compile(r"\bcount\b|\bnumber of\b|wc -l|\d+", re.IGNORECASE)

# Rubric ids whose bucket flips to "incompatible" when the paired flag fires,
# with a drafted reshaping note (docs/SKILL_IMPORT.md Task 11 transcribes the
# "reshaping rules" this table encodes: interactivity->findings,
# iterate-until-success->single-shot, mcp->CLI/curl-or-blocked,
# mutation->propose-only, credentials->blocked).
INCOMPATIBLE_RUBRIC_MAP = {
    "q2_pattern": (
        (
            "interactivity",
            (
                "Assumes synchronous user interaction during a run ('ask the user' / "
                "wait-for-approval), but v1 loops run unattended each firing "
                "(docs/LOOP_AUTHORING.md §2) — reshape: surface as a finding for "
                "async human review instead of an inline prompt."
            ),
        ),
        (
            "iteration",
            (
                "Assumes iterate-until-success across invocations ('until tests pass' / "
                "'retry until'), but v1 is single-shot per firing with no cross-firing "
                "retry loop — reshape: check once per firing and report; a human is "
                "the repeat mechanism."
            ),
        ),
    ),
    "q3_type": (
        (
            "mcp",
            (
                "Depends on an MCP server tool the harness engine may not have "
                "configured — reshape to an equivalent CLI/curl call, or leave "
                "blocked if none exists."
            ),
        ),
    ),
    "q7_axes": (
        (
            "mutation",
            (
                "Performs a mutating action (git push/deploy/npm publish/send email or "
                "sms/post to/gh pr create/rm -rf) but the harness floor is "
                "report_only/none/none/none — reshape to propose-only: the mutating "
                "command is emitted as a commented precheck line a human must "
                "uncomment deliberately."
            ),
        ),
        (
            "credentials",
            (
                "Requires credentials/API keys/OAuth/bearer tokens the harness has no "
                "injection story for yet — blocked until a credential-handling "
                "design exists."
            ),
        ),
    ),
}


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


def _combined_text(skill: dict) -> str:
    """Body + all bundled files' text, joined for a single case-insensitive
    heuristic scan (per the task-9 ambiguity resolution: heuristics scan body
    + all bundled files' text, not just body)."""
    parts = [skill.get("body", "") or ""]
    for f in skill.get("files", []) or []:
        parts.append(f.get("text", "") or "")
    return "\n".join(parts)


def _detect_flags(text: str) -> dict:
    """Run every detection heuristic over `text` (case-insensitive; the
    regexes already carry re.IGNORECASE) and return the six boolean flags."""
    return {
        "interactivity": bool(RE_INTERACTIVITY.search(text)),
        "mutation": bool(RE_MUTATION.search(text)),
        "mcp": bool(RE_MCP.search(text)),
        "credentials": bool(RE_CREDENTIALS.search(text)),
        "iteration": bool(RE_ITERATION.search(text)),
        "network": bool(RE_NETWORK.search(text)),
    }


def _first_match_location(regex, skill: dict):
    """Find `regex`'s first match across the skill's sources, body first then
    bundled files in their existing order. Returns (matched_text, location)
    where location is "SKILL.md" or a bundled file's relpath, or (None, None)
    if nothing matches anywhere."""
    body = skill.get("body", "") or ""
    m = regex.search(body)
    if m:
        return m.group(0), "SKILL.md"
    for f in skill.get("files", []) or []:
        m = regex.search(f.get("text", "") or "")
        if m:
            return m.group(0), f.get("relpath", "<file>")
    return None, None


def _find_cli_equivalent_same_file(skill: dict, location):
    """mcp-without-cli-equivalent heuristic, fix-round-1 #3: the CLI token
    must appear in the SAME source as the mcp mention (`location`, from
    `_first_match_location`), not merely anywhere in the bundle — a stray
    `git`/`curl` mention in an unrelated bundled file doesn't excuse an MCP
    dependency actually used in SKILL.md. Returns (matched_text, location) or
    (None, None)."""
    if location is None:
        return None, None
    if location == "SKILL.md":
        text = skill.get("body", "") or ""
    else:
        text = None
        for f in skill.get("files", []) or []:
            if f.get("relpath") == location:
                text = f.get("text", "") or ""
                break
        if text is None:
            return None, None
    m = _CLI_TOKEN_RE.search(text)
    if m:
        return m.group(0), location
    return None, None


def _build_blocked_info(skill: dict, flags: dict) -> tuple:
    """Decide `blocked` and build `blocked_reasons` (fix-round-1 #2/#3):
    credentials always blocks; mcp blocks unless a CLI equivalent is found in
    the SAME source as the mcp mention. Every applicable check appends a
    reason — including an informational, non-blocking one when mcp has a CLI
    equivalent — so the mcp decision is always visible, not just when it
    blocks. Every entry is prefixed `[blocking] ` or `[info] ` (fix-round-2
    #3 minor) so a downstream printer can discriminate without parsing the
    trailing "— not blocked" text."""
    blocked = False
    reasons = []

    if flags.get("credentials"):
        blocked = True
        matched, location = _first_match_location(RE_CREDENTIALS, skill)
        if matched:
            reasons.append(f"[blocking] credentials: matched {matched!r} in {location}")
        else:
            reasons.append(
                "[blocking] credentials: matched credential-like text in the skill"
            )

    if flags.get("mcp"):
        mcp_matched, mcp_location = _first_match_location(RE_MCP, skill)
        cli_matched, cli_location = _find_cli_equivalent_same_file(skill, mcp_location)
        if cli_matched:
            reasons.append(
                f"[info] mcp: CLI equivalent {cli_matched!r} found in {cli_location} — not blocked"
            )
        else:
            blocked = True
            label = mcp_matched or "mcp__*"
            loc = mcp_location or "the skill"
            reasons.append(f"[blocking] mcp: {label!r} with no CLI equivalent in {loc}")

    return blocked, reasons


def _body_headings(body: str) -> list:
    """Markdown headings (`#`..`######`) found in `body`, in document order."""
    return [
        m.group(1).strip() for m in re.finditer(r"^#{1,6}\s+(.+)$", body, re.MULTILINE)
    ]


def _looks_like_command(candidate: str) -> bool:
    """fix-round-1 minor #b: filter for inline-backtick candidates only —
    is `candidate` plausibly a shell command rather than a bare word/path
    someone happened to backtick-quote (e.g. `metrics`, `~/projects/foo`)?
    True when the leading token is a known binary, or a later token looks
    like a flag (`-x`/`--long`)."""
    tokens = candidate.split()
    if not tokens:
        return False
    if tokens[0] in KNOWN_COMMAND_TOKENS:
        return True
    return any(t.startswith("-") for t in tokens[1:])


def _extract_candidate_commands(body: str) -> list:
    """Candidate shell snippets for the precheck proposal: backtick-quoted
    inline spans (filtered to command-looking spans, see _looks_like_command)
    and every line inside a fenced ```bash/```sh/```shell block (info-string
    suffixes tolerated), in document order. Only `body` is scanned (not
    bundled files) — precheck candidates come from what the skill's own
    instructions literally tell the engine to run."""
    candidates = []
    in_fence = False
    in_shell_fence = False
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                in_fence = True
                info = stripped[3:].strip().lower()
                lang = info.split()[0] if info.split() else ""
                in_shell_fence = lang in _SHELL_FENCE_LANGS
            else:
                in_fence = False
                in_shell_fence = False
            continue
        if in_fence:
            if in_shell_fence and stripped:
                candidates.append(stripped)
            continue
        for m in re.finditer(r"`([^`\n]+)`", line):
            content = m.group(1).strip()
            if content and _looks_like_command(content):
                candidates.append(content)
    # Preserve order, drop exact duplicates.
    return list(dict.fromkeys(candidates))


def _git_subcommand(tokens: list):
    """Given a token list starting with `git`, walk past `-C <value>`-style
    flags to find the actual subcommand (or None if there isn't one)."""
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok in _GIT_VALUE_FLAGS:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return tok
    return None


def _xargs_subcommand(tokens_after_xargs: list):
    """Given the tokens that follow `xargs`, skip xargs's own flags
    (`-I{}`, `-n1`, `-0`, ...) to find the command it actually invokes (or
    None if there isn't one)."""
    for tok in tokens_after_xargs:
        if not tok.startswith("-"):
            return tok
    return None


def _segment_is_read_only(segment: str) -> bool:
    """Classify a single `|`/`;`/`&&`/`||`/`&`-delimited segment by its
    leading command per the brief's scoped-read-forms table. `xargs` is
    resolved to the command it actually invokes rather than being treated
    as opaque. fix-round-2 #2: a segment containing `$(` or a backtick is a
    command substitution — conservatively demoted to not-read-only rather
    than trying to parse the inner command."""
    if "$(" in segment or "`" in segment:
        return False
    tokens = segment.split()
    if not tokens:
        return True  # an empty segment (trailing separator) contributes nothing
    head = tokens[0]
    if head == "git":
        return _git_subcommand(tokens) in READ_ONLY_GIT_SUBCOMMANDS
    if head == "xargs":
        sub = _xargs_subcommand(tokens[1:])
        return sub in READ_ONLY_SIMPLE_COMMANDS
    return head in READ_ONLY_SIMPLE_COMMANDS


def _has_full_line_danger(cmd_line: str) -> bool:
    """fix-round-1 #1 (+ fix-round-2 #3 minors): signals that override a
    read-only-looking head token because precheck.sh runs UNSANDBOXED and
    this annotation is the only safety signal a human has. Checked over the
    FULL line, not per segment: a dangerous redirect/flag anywhere in a
    compound command taints all of it. Covers `>`/`>>` writes (except the
    tolerated `2>/dev/null`/`>&1` forms), `find -delete`, `find -exec`,
    `curl -X`/`--request <non-GET>`, `curl -o`/`-O` (writes to local disk
    without needing an explicit `>`), `tail -f`/`-F`/`--follow` (never
    terminates), and `xargs <a command that isn't itself read-only>`."""
    # `<repo>`/`<p>`-style doc placeholders (both fixtures use this convention,
    # e.g. `git -C <repo> status`) contain a literal `>` that isn't a shell
    # redirection — strip those spans before scanning for real ones.
    without_placeholders = _PLACEHOLDER_RE.sub("", cmd_line)
    stripped_redirects = _SAFE_REDIRECTS_RE.sub("", without_placeholders)
    if ">" in stripped_redirects:
        return True
    if re.search(r"-delete\b", cmd_line):
        return True
    if re.search(r"-exec\b", cmd_line):
        return True
    m = re.search(r"(?:-X|--request)\s*(\S+)", cmd_line)
    if m and m.group(1).strip("'\"").upper() != "GET":
        return True
    if re.search(r"\bcurl\b", cmd_line) and re.search(
        r"(?:^|\s)(-o|-O)(?:\s|$)", cmd_line
    ):
        return True
    if re.search(r"\btail\b", cmd_line) and re.search(
        r"(?:^|\s)(-f\b|-F\b|--follow\b)", cmd_line
    ):
        return True
    m = re.search(r"\bxargs\b\s*(.*)$", cmd_line)
    if m:
        sub = _xargs_subcommand(m.group(1).split())
        if sub not in READ_ONLY_SIMPLE_COMMANDS:
            return True
    return False


# fix-round-2 #2: split on bare `&` (backgrounding) too, not just `&&` — a
# command chained after a lone `&` was previously invisible to segment
# classification. The lookbehind excludes `&` preceded by `>`/`&`/a digit so
# `2>&1` (fd duplication) and `&&` itself are never mis-split; the lookahead
# excludes the first `&` of a literal `&&` for the same reason (though `&&`
# is already matched earlier in the alternation at that position anyway).
_SEGMENT_SPLIT_RE = re.compile(r"\|\||&&|\||;|(?<![>&0-9])&(?!&)")


def _is_read_only_command(cmd_line: str) -> bool:
    """Is `cmd_line` a scoped-read form per the brief's read-only table?
    False (MUTATING) if any full-line danger signal fires (_has_full_line_
    danger), OR if any `|`/`;`/`&&`/`||`-delimited segment fails its own
    read-only check — every segment must be read-only, not just the head."""
    if _has_full_line_danger(cmd_line):
        return False
    segments = _SEGMENT_SPLIT_RE.split(cmd_line)
    return all(_segment_is_read_only(seg.strip()) for seg in segments)


def _propose_precheck(body: str) -> list:
    """Candidate precheck.sh lines, every one commented (`#`), annotated
    `[read-only?]` or `[MUTATING — do not enable]`. Nothing here is ever
    live precheck code — uncommenting is a deliberate human act
    (docs/SKILL_IMPORT.md's trust rule)."""
    lines = []
    for cmd in _extract_candidate_commands(body):
        if _is_read_only_command(cmd):
            lines.append(f"# [read-only?] {cmd}")
        else:
            lines.append(f"# [MUTATING — do not enable] {cmd}")
    return lines


_NAME_SEP_RE = re.compile(r"[_ ]+")
_NAME_INVALID_RE = re.compile(r"[^a-z0-9-]")
_NAME_DASH_RUN_RE = re.compile(r"-{2,}")


def _sanitize_name(raw: str) -> str:
    """Sanitize a free-form skill/frontmatter name into
    ^[a-z][a-z0-9-]{1,40}$: lowercase, `[_ ]+`->`-`, strip anything not
    [a-z0-9-], collapse `-{2,}`, trim to fit, prefix `x-` if it doesn't
    start with a letter."""
    s = (raw or "").lower()
    s = _NAME_SEP_RE.sub("-", s)
    s = _NAME_INVALID_RE.sub("", s)
    s = _NAME_DASH_RUN_RE.sub("-", s)
    s = s.strip("-")
    if not s:
        s = "skill"
    if not s[0].isalpha():
        s = _NAME_DASH_RUN_RE.sub("-", "x-" + s)
    s = s[:41].rstrip("-")
    if len(s) < 2:
        s = (s + "-skill")[:41]
    return s


def _kv_summary(d: dict) -> str:
    """ "k=v, k=v" summary — used for both `flags` and `axes`."""
    return ", ".join(f"{k}={v}" for k, v in d.items())


def _q10_options(text: str) -> list:
    """Panel-type options for q10_metrics: if the skill's checks mention
    counts/numbers, offer the full dashboard.json panel-type set
    (docs/LOOP_AUTHORING.md §3.3); otherwise just the two generic presets."""
    generic = [
        {"id": "number", "label": "number (single current value)"},
        {"id": "trend", "label": "trend (history over window_days)"},
    ]
    if _Q10_NUMERIC_HINT_RE.search(text):
        return generic + [
            {"id": "table", "label": "table (array of objects, stable columns)"},
            {"id": "list", "label": "list (array of scalars)"},
        ]
    return generic


def _q10_context(text: str) -> str:
    if _Q10_NUMERIC_HINT_RE.search(text):
        return (
            "the skill's checks mention counts/numbers, so table/list panels "
            "may fit in addition to a single number."
        )
    return "no specific counts were mentioned, so only the generic number/trend presets are offered."


def _build_rubric(frontmatter: dict, body: str, flags: dict, axes: dict) -> dict:
    """Classify all eleven intake-rubric questions (docs/LOOP_AUTHORING.md §2,
    ids q1_purpose..q11_budget) into a bucket, per the task-9 ambiguity
    resolution (fix-round-1 #6 tightened q5_scope; #4 made q7_axes's value
    a plain string):
      - q1_purpose: answered, value = frontmatter description (else missing)
      - q5_scope: answered only when a body heading actually looks like a
        scope/exclusions statement (_SCOPE_HEADING_RE); else missing
      - q2_pattern / q3_type: derived (agentic pattern + script/agent
        precheck split)
      - q6_guardrails / q7_axes: derived (report-only floor + detected flags)
      - q4_cadence / q8_finding_identity / q9_semantics / q10_metrics /
        q11_budget: missing (never statically answerable)
    Then any INCOMPATIBLE_RUBRIC_MAP hit overrides that item's bucket to
    "incompatible" with a drafted reshaping note.
    """
    rubric = {}

    description = (frontmatter.get("description") or "").strip()
    if description:
        rubric["q1_purpose"] = {"bucket": "answered", "value": description}
    else:
        rubric["q1_purpose"] = {"bucket": "missing"}

    rubric["q2_pattern"] = {
        "bucket": "derived",
        "value": (
            "v1 loops are Human-in-the-loop by design (docs/LOOP_AUTHORING.md §2); "
            "this skill's steps run once per firing, single-shot."
        ),
    }
    rubric["q3_type"] = {
        "bucket": "derived",
        "value": (
            "type=agent; script→agent split proposed in precheck_proposal "
            "(read-only? candidates for precheck.sh, everything else stays "
            "for the engine to interpret)."
        ),
    }

    rubric["q4_cadence"] = {"bucket": "missing"}

    headings = _body_headings(body)
    scope_headings = [h for h in headings if _SCOPE_HEADING_RE.search(h)]
    if scope_headings:
        rubric["q5_scope"] = {"bucket": "answered", "value": "; ".join(scope_headings)}
    else:
        rubric["q5_scope"] = {"bucket": "missing"}

    rubric["q6_guardrails"] = {
        "bucket": "derived",
        "value": (
            "report-only floor (perm_fs_write=report_only, perm_network=none, "
            "perm_local_exec=none, perm_remote_mutation=none); detected flags: "
            + _kv_summary(flags)
        ),
    }
    # value is a plain string here on purpose (fix-round-1 #4) — the axes
    # dict itself is already the sibling top-level "axes" key, and
    # INCOMPATIBLE_RUBRIC_MAP overwrites this same field with a string when
    # mutation/credentials fire, so the type must be stable either way.
    rubric["q7_axes"] = {
        "bucket": "derived",
        "value": "floor (see top-level 'axes'): " + _kv_summary(axes),
    }

    rubric["q8_finding_identity"] = {"bucket": "missing"}
    rubric["q9_semantics"] = {"bucket": "missing"}
    rubric["q10_metrics"] = {"bucket": "missing"}
    rubric["q11_budget"] = {"bucket": "missing"}

    for rubric_id, reasons in INCOMPATIBLE_RUBRIC_MAP.items():
        hits = [reason for flag_name, reason in reasons if flags.get(flag_name)]
        if hits:
            rubric[rubric_id] = {"bucket": "incompatible", "value": " ".join(hits)}

    return rubric


def _detected_phrases(regex, text: str, limit: int = 5) -> list:
    """Distinct matched substrings for `regex` over `text`, in first-seen
    order, capped at `limit` — used to draft a from-the-text justification
    sentence for axis-raise questions (fix-round-1 #5) instead of a generic
    one."""
    seen = []
    seen_lower = set()
    for m in regex.finditer(text):
        s = m.group(0).strip()
        if s and s.lower() not in seen_lower:
            seen.append(s)
            seen_lower.add(s.lower())
        if len(seen) >= limit:
            break
    return seen


def _draft_axis_justification(axis: str, regex, text: str, default_verb: str) -> str:
    """fix-round-1 #5: one sentence built from the detected verbs, e.g. for
    mutation: 'Draft justification: "This loop needs perm_remote_mutation to
    <verb list> as the skill describes; scoped to <target>. EDIT BEFORE
    ACCEPTING."' `<target>` is left as a literal placeholder for the human/
    agent to fill in — this is a draft, not a finished justification."""
    verbs = _detected_phrases(regex, text)
    verb_list = ", ".join(verbs) if verbs else default_verb
    return (
        f'Draft justification: "This loop needs {axis} to {verb_list} as the '
        f'skill describes; scoped to <target>. EDIT BEFORE ACCEPTING."'
    )


def _build_answers_needed(flags: dict, text: str, q5_missing: bool) -> list:
    """The always-missing questions (never statically answerable), plus
    q5_scope when it wasn't answered by a real scope heading (fix-round-1
    #6, suggested_answerer "agent" — the supervising agent usually has
    enough project context to propose scope without asking the user), plus
    a per-axis raise question for each of network/mutation that fired."""
    items = [
        {
            "question_id": "q4_cadence",
            "prompt": (
                "How often should this run, and why (what's the staleness "
                "expectation if a firing is missed)?"
            ),
            "context": (
                "No cadence is stated in the skill. Schedule grammar: manual | "
                "interval:<N><unit> | daily:HH:MM | times:HH:MM,... | "
                "weekly:<day>:HH:MM | monthly:<DD>:HH:MM (docs/LOOP_AUTHORING.md §5)."
            ),
            "options": [
                {"id": "manual", "label": "manual"},
                {"id": "every_15m", "label": "interval:15m"},
                {"id": "hourly", "label": "interval:1h"},
                {"id": "daily_morning", "label": "daily:07:30"},
                {"id": "weekly_monday", "label": "weekly:mon:08:00"},
            ],
            "suggested_answerer": "user",
        },
        {
            "question_id": "q8_finding_identity",
            "prompt": (
                "What IS a finding for this loop, and what is the exact "
                "finding_id derivation rule (<subject>:<condition>, never "
                "volatile data)?"
            ),
            "context": (
                "Not statically inferable from the skill text — this is the "
                "single most load-bearing answer (docs/LOOP_AUTHORING.md §2, "
                "question 8)."
            ),
            "options": [],
            "suggested_answerer": "user",
        },
        {
            "question_id": "q9_semantics",
            "prompt": (
                "For this loop specifically, what do ok/warn/alert mean? "
                "('this loop never uses warn' is a valid answer.)"
            ),
            "context": "Not statically inferable from the skill text.",
            "options": [],
            "suggested_answerer": "user",
        },
        {
            "question_id": "q10_metrics",
            "prompt": (
                "What numeric/table/list values should this loop emit in "
                "metrics, and how should dashboard.json render each as a panel?"
            ),
            "context": "Not statically inferable; " + _q10_context(text),
            "options": _q10_options(text),
            "suggested_answerer": "user",
        },
        {
            "question_id": "q11_budget",
            "prompt": "engine/model + rough tokens-per-run + retry_transient + timeout_s?",
            "context": (
                "Not statically inferable beyond the engine recommendation "
                "already proposed."
            ),
            "options": [],
            "suggested_answerer": "user",
        },
    ]

    if q5_missing:
        items.append(
            {
                "question_id": "q5_scope",
                "prompt": (
                    "What's explicitly in scope for this loop, and what's "
                    "explicitly excluded?"
                ),
                "context": (
                    "No scope/exclusions-looking heading (Scope / Exclusions / "
                    "Out of scope / What this does not do) was found in the "
                    "skill body. The supervising agent usually has enough "
                    "project context to propose scope + exclusions without "
                    "asking the user."
                ),
                "options": [],
                "suggested_answerer": "agent",
            }
        )

    if flags.get("network"):
        items.append(
            {
                "question_id": "raise_perm_network",
                "prompt": (
                    "This skill appears to make network calls (curl/http(s)/"
                    "api call/fetch/webhook detected). Raise perm_network "
                    "above the report-only floor?"
                ),
                "context": (
                    "Floor is perm_network=none. Raising to 'full' permits "
                    "outbound network for the engine (docs/LOOP_AUTHORING.md §4); "
                    "this does not by itself grant remote-mutation rights. "
                    + _draft_axis_justification(
                        "perm_network", RE_NETWORK, text, "make network calls"
                    )
                ),
                "options": [
                    {
                        "id": "keep_none",
                        "label": "keep perm_network=none (report/propose-only)",
                    },
                    {"id": "raise_full", "label": "raise perm_network=full"},
                ],
                "suggested_answerer": "user",
            }
        )
    if flags.get("mutation"):
        items.append(
            {
                "question_id": "raise_perm_remote_mutation",
                "prompt": (
                    "This skill appears to perform a mutating action (git push/"
                    "deploy/npm publish/send email or sms/post to/gh pr create/"
                    "rm -rf detected). Raise perm_remote_mutation above the "
                    "report-only floor?"
                ),
                "context": (
                    "Floor is perm_remote_mutation=none. Raising requires a "
                    "non-empty remote_mutation_justification "
                    "(docs/LOOP_AUTHORING.md §4, dangerous combo 2); until "
                    "answered, the mutating command stays a commented precheck "
                    "line that must be uncommented deliberately. "
                    + _draft_axis_justification(
                        "perm_remote_mutation",
                        RE_MUTATION,
                        text,
                        "perform a mutating action",
                    )
                ),
                "options": [
                    {
                        "id": "keep_none",
                        "label": "keep perm_remote_mutation=none (propose-only)",
                    },
                    {
                        "id": "raise_allowlist",
                        "label": "raise perm_remote_mutation=allowlist",
                    },
                ],
                "suggested_answerer": "user",
            }
        )
    return items


def analyze(skill: dict) -> dict:
    """Static, zero-token gap analysis of a parsed skill (`parse_skill`'s
    output). See docs/plans/2026-07-30-skill-import-and-agent-surface.md
    Task 9 for the full output shape. Never raises axes above the
    report-only floor — any raise is only ever proposed via
    `answers_needed`."""
    frontmatter = skill.get("frontmatter", {}) or {}
    body = skill.get("body", "") or ""
    text = _combined_text(skill)

    flags = _detect_flags(text)
    engine = "claude" if RE_CLAUDE_IDIOM.search(text) else "codex"

    # ALWAYS the report-only floor — a tool-usage scan never raises an axis
    # itself; any raise is proposed via answers_needed (see _build_answers_needed).
    axes = {
        "perm_fs_write": "report_only",
        "perm_network": "none",
        "perm_local_exec": "none",
        "perm_remote_mutation": "none",
    }

    # fix-round-1 #2/#3: blocked is decided (and explained) by
    # _build_blocked_info, which also records the mcp CLI-equivalent
    # decision even when it doesn't end up blocking.
    blocked, blocked_reasons = _build_blocked_info(skill, flags)

    raw_name = (
        frontmatter.get("name")
        or os.path.basename((skill.get("skill_dir") or "").rstrip(os.sep))
        or "imported-skill"
    )
    proposed_name = _sanitize_name(raw_name)

    precheck_proposal = _propose_precheck(body)
    rubric = _build_rubric(frontmatter, body, flags, axes)
    answers_needed = _build_answers_needed(
        flags, text, q5_missing=rubric["q5_scope"]["bucket"] == "missing"
    )

    notes = list(skill.get("notes", []) or [])
    notes.append(
        "type recommendation is a v1 constant ('agent'); watchdog "
        "classification is not attempted by the static analyzer — reassess "
        "manually if this looks like a single-probe health check."
    )

    return {
        "analyzer_version": ANALYZER_VERSION,
        "skill_sha256": skill["sha256"],
        "proposed_name": proposed_name,
        "type": "agent",
        "engine": engine,
        "axes": axes,
        "flags": flags,
        "blocked": blocked,
        "blocked_reasons": blocked_reasons,
        "rubric": rubric,
        "precheck_proposal": precheck_proposal,
        "answers_needed": answers_needed,
        "notes": notes,
    }


# --- Task 12: apply() --------------------------------------------------------
#
# Scaffold templates. These are the single canonical copies — `bin/loopctl`'s
# `cmd_new` aliases them (`_LOOP_CONF_TEMPLATE = skill_import._LOOP_CONF_TEMPLATE`
# etc., see the module docstring there) rather than keeping its own copies, so
# `loopctl new` and `loopctl import --apply` always render the SAME SPEC.md/
# prompt.md/precheck.sh frame — the controller ruling for this task requires
# reusing `loopctl new`'s templates rather than duplicating the strings.

_LOOP_CONF_TEMPLATE = """\
# Scaffolded by `loopctl new __NAME__` on __DATE__.
# TODO before installing: pick a real `schedule` (see docs/INTERFACES.md §5.1)
# and fill in `description`. Optional keys below are commented with their
# permissive defaults — uncomment and edit only what this loop needs.
name=__NAME__
description="TODO: one-line description of what this loop does"
type=__TYPE__
engine=__ENGINE__
# model=
schedule=manual
# workdir=$LOOPS_ROOT
# timeout_s=900
# enabled=true
# retention_days=30
# retry_transient=1
# perm_fs_write=report_only
# perm_network=none
# perm_local_exec=none
# perm_remote_mutation=none
# exec_allowlist=
# credential_env=
# remote_mutation_justification=
# notes=
"""

_PROMPT_MD_TEMPLATE = """\
# __NAME__ — prompt

TODO: describe what this loop should investigate/monitor and report on.

## Output contract

Your final message MUST be a single JSON object conforming exactly to
`contract/contract.schema.json` — schema_version, run_id, status,
status_reason, headline, report_markdown, metrics, findings. No prose
outside that JSON object.

- `run_id` MUST equal the value from the `## RUN CONTEXT` block the runner
  appends to this prompt — copy it exactly; never invent your own.
- `metrics` MUST be a JSON **string** containing a serialized JSON object
  (e.g. the string `"{}"` when there is nothing to report) — not a nested
  JSON object.
- `findings` is required but MAY be an empty array.

## Findings prompt contract

1. Re-emit a still-true finding with its **same `finding_id`** — never
   invent a new id for a recurring condition.
2. Do not re-argue a `DISMISSED` finding unless the underlying situation has
   **materially changed**; if it has, say what changed.
3. Still emit `SNOOZED` findings if true — suppression is the runner's job,
   not the model's.

## Finding identity

[FILL: derivation rule — how finding_id is derived from the durable identity
of the condition being reported, e.g. `<subject>:<condition>`. Must be
deterministic and stable across runs for the same real-world condition, and
must NOT embed volatile data (timestamps, run ids, counts, shifting line
numbers).]
"""

_SPEC_MD_TEMPLATE = """\
# __NAME__ — intake spec

1. Purpose & stop condition
[FILL: what this loop exists to catch/report, and what "done" looks like]

2. Agentic pattern
[FILL: note — every v1 loop is outer-shape Human-in-the-loop; iterate-across-invocations
is v2 — record the aspiration here, but ship single-shot for v1]

3. Type & data flow (precheck gathers vs engine interprets)
[FILL: what precheck.sh deterministically gathers vs what the engine is asked to interpret]

4. Cadence
[FILL: schedule and why]

5. Scope & exclusions
[FILL: what's in scope, what's explicitly excluded]

6. Guardrails
[FILL: verbatim guardrails this loop must respect]

7. Permission axes + justification
[FILL: perm_fs_write / perm_network / perm_local_exec / perm_remote_mutation values and why]

8. Finding identity (what a finding IS + finding_id derivation rule)
[FILL: same derivation rule as prompt.md's ## Finding identity section]

9. Tier-1 semantics (ok/warn/alert meaning)
[FILL: what ok / warn / alert mean for this loop]

10. Tier-2 metrics + panels
[FILL: metrics this loop emits and how dashboard.json renders them]

11. Engine/model + budget
[FILL: engine, model, expected tokens/run, retry_transient, timeout_s]
"""

_PRECHECK_SH_TEMPLATE = """\
#!/usr/bin/env bash
# __NAME__/precheck.sh — deterministic gathering step (script->agent pattern,
# docs/INTERFACES.md §4.1/§6.2): this script does cheap, deterministic data
# gathering; its stdout is injected into the engine's prompt as ground truth.
# It must be idempotent and side-effect-free beyond read-only inspection. For
# type=watchdog loops, THIS SCRIPT IS THE JOB — a non-zero exit or a
# failure-shaped result escalates to the engine for diagnosis (§4.1).
set -euo pipefail

# TODO: gather deterministic signal here and print it to stdout.
echo "TODO: implement precheck for __NAME__"
"""


def _render_template(template, name, **extra):
    out = template.replace("__NAME__", name)
    for key, value in extra.items():
        out = out.replace(f"__{key.upper()}__", str(value))
    return out


class SkillApplyError(Exception):
    """Raised by `apply()` when refusing to scaffold. The message is
    user-facing — `loopctl import --apply` prints it verbatim to stderr and
    exits 1."""


def _resolved(rubric: dict, answers: dict, rubric_id: str):
    """Controller ruling (Task 12, 2026-07-30): an explicit
    `answers["answers"][id]` entry WINS over the rubric's own value for that
    id — the rubric `value` only fills ids ABSENT (or blank) from `answers`.
    This applies uniformly across every bucket (`answered`/`derived`/
    `incompatible`) that carries a `value`, not just the buckets that were
    already unanswered — an extra `answers` entry for an id the rubric had
    already resolved is honored, not ignored.

    Returns `None` — never a derived-default fallback; none exists — when
    neither source has anything for `rubric_id` (i.e. the rubric bucket is
    `missing` and `answers` has nothing either). The caller must leave that
    section as an unresolved `[FILL: ...]` placeholder in that case, which is
    the intended safety net: `loopctl validate` hard-fails on any remaining
    `[FILL:` in SPEC.md.

    Round-1 review fix: the emptiness test is `str(value).strip()`, not
    `value not in (None, "")` — a whitespace-only answer (`"   "`) is
    neither `None` nor `""`, so the old check treated it as "provided" and
    blanked out the SPEC section (no `[FILL:` marker left), defeating the
    safety net above."""
    answers_map = (answers or {}).get("answers") or {}
    if rubric_id in answers_map:
        value = answers_map[rubric_id]
        if value is not None and str(value).strip():
            return str(value)
    item = (rubric or {}).get(rubric_id) or {}
    if "value" in item:
        return item["value"]
    return None


def _spec_template_sections(rendered_spec_template: str) -> list:
    """Split a __NAME__-substituted `_SPEC_MD_TEMPLATE` into its eleven
    `(header_line, placeholder_text)` pairs, in `RUBRIC_IDS` order — parsed
    from the template text itself (rather than a hardcoded copy of the
    section headings) so the section wording is never duplicated here."""
    _, _, body = rendered_spec_template.partition("\n\n")
    blocks = re.split(r"\n\n(?=\d+\.\s)", body.strip("\n"))
    return [block.partition("\n")[0::2] for block in blocks]


def _render_spec_md(name: str, rubric: dict, answers: dict) -> str:
    """SPEC.md's eleven sections (docs/LOOP_AUTHORING.md §2), each filled
    from `_resolved()` when available, else left as the template's own
    `[FILL: ...]` placeholder text verbatim — so `loopctl validate`'s
    `[FILL:` scan keeps working as the safety net for anything genuinely
    unanswered."""
    template = _SPEC_MD_TEMPLATE.replace("__NAME__", name)
    header_line, _, _ = template.partition("\n\n")
    sections = _spec_template_sections(template)
    if len(sections) != len(RUBRIC_IDS):
        # `assert` vanishes under `python -O` — this invariant (the template
        # has exactly RUBRIC_IDS-many sections) must not silently stop being
        # checked in an optimized run.
        raise RuntimeError(
            f"SPEC.md template section count drifted: got {len(sections)} "
            f"sections, expected {len(RUBRIC_IDS)} (RUBRIC_IDS)"
        )

    rendered = []
    for rubric_id, (header, placeholder) in zip(RUBRIC_IDS, sections):
        value = _resolved(rubric, answers, rubric_id)
        body_text = value.strip() if value else placeholder
        rendered.append(header + "\n" + body_text)

    return header_line + "\n\n" + "\n\n".join(rendered) + "\n"


def _blocked_spec_section(analysis: dict) -> str:
    """The `## BLOCKED — read before scheduling` section appended to SPEC.md
    when a blocked skill is scaffolded with `acknowledge_blocked: true`
    (controller ruling 3) — names the blocking reasons verbatim so a human
    reading SPEC.md later sees exactly why `schedule` was forced to
    `manual`."""
    reasons = [
        r for r in (analysis.get("blocked_reasons") or []) if r.startswith("[blocking]")
    ] or list(analysis.get("blocked_reasons") or [])
    intro = (
        "This loop was scaffolded from a skill the analyzer marked `blocked` "
        "(answers.json set `acknowledge_blocked: true`). `schedule` was forced "
        "to `manual` regardless of any `q4_cadence` answer — do not change that "
        "until every reason below has been resolved:"
    )
    lines = [
        "## BLOCKED — read before scheduling",
        "",
        intro,
        "",
    ]
    lines += [f"- {r}" for r in reasons]
    return "\n".join(lines) + "\n"


def _render_prompt_md(name: str, skill: dict, rubric: dict, answers: dict) -> str:
    """prompt.md: the `loopctl new` frame (Output contract, Findings prompt
    contract, `## Finding identity` heading — all reused verbatim, byte-for-
    byte, from `_PROMPT_MD_TEMPLATE`) with the skill's own body spliced into
    the task section, and the `## Finding identity` placeholder filled from
    `q8_finding_identity` when resolved.

    Round-1 review fix: the `## Finding identity` split MUST be computed on
    the TEMPLATE, before the skill body is ever spliced in. A skill body
    that itself contains a `## Finding identity` heading (plausible — it's
    an ordinary-looking markdown heading) would otherwise make
    `text.index()` find the BODY's copy once the two were concatenated,
    truncating everything after it — the Output contract and Findings
    prompt contract sections included. `loopctl validate` does not catch
    this: it only greps for the heading's presence, never what follows it.
    Splitting the template FIRST into `head`/`tail` and only ever splicing
    the body into `head` (which by construction of the template always
    precedes the real heading) makes this positionally impossible regardless
    of what the body contains."""
    template = _PROMPT_MD_TEMPLATE.replace("__NAME__", name)

    heading = "## Finding identity\n\n"
    idx = template.index(heading)
    head, tail = template[: idx + len(heading)], template[idx + len(heading) :]

    todo_line = (
        "TODO: describe what this loop should investigate/monitor and report on."
    )
    body = (skill.get("body") or "").strip()
    task_text = (
        body if body else "[FILL: no task description found in the source skill]"
    )
    head = head.replace(todo_line, task_text, 1)

    q8 = _resolved(rubric, answers, "q8_finding_identity")
    if q8:
        tail = q8.strip() + "\n"

    return head + tail


_APPLY_TAG_RE = re.compile(r"^[a-z][a-z0-9:_-]{1,40}$")

# loopconf.py's own FIELDS ranges for timeout_s/retry_transient (bin/loopconf.py
# §5) — duplicated here as plain constants (not imported; loopconf.py is a
# frozen single-parser module loaded independently by bin/loopctl, and these
# two bounds are simple enough that re-stating them is safer than reaching
# into loopconf.FIELDS's internal shape from here) so a bad structured answer
# is refused at apply()-time with a clear message, before ever being written.
_TIMEOUT_S_MIN, _TIMEOUT_S_MAX = 30, 7200
_RETRY_TRANSIENT_MIN, _RETRY_TRANSIENT_MAX = 0, 3

# loop.conf's KEY=value grammar (bin/loopconf.py's _parse_value, see
# _quote_conf_value below) treats a BARE (unquoted) value as everything up to
# the first whitespace — model= is written bare (never quoted), so a `model`
# containing whitespace silently truncates or, worse, spills the remainder
# onto what loopconf.parse() then reads as trailing garbage
# ("bare value must not contain spaces"). A literal newline is even worse: it
# injects a bogus extra KEY=value-shaped line. Round-2 review: refuse loudly
# rather than truncate/quote — a model id with whitespace in it is a mistake,
# not a value worth preserving.
_MODEL_SHAPE_RE = re.compile(r"\S+")


def _quote_conf_value(text: str, field_name: str = "value") -> str:
    """loop.conf's KEY=value grammar (§5.0) is strictly line-based — flatten
    any embedded newlines/repeated whitespace to single spaces and escape
    embedded double quotes before wrapping in a quoted value.

    Round-1 review: a trailing literal backslash is refused outright
    (`SkillApplyError`) rather than silently emitting a value
    `loopconf.parse()` will itself reject as "unterminated quoted value".
    `bin/loopconf.py`'s `_parse_value` recognizes ONLY the two-character
    sequence backslash+quote as an escape — there is no separate
    backslash-escaping rule. Verified empirically (against the real parser,
    for N=1..5 trailing backslashes, both with and without doubling the
    backslash first) that a run of one-or-more literal backslashes
    immediately before the closing quote is ALWAYS misread as escaping that
    closing quote itself, regardless of how many backslashes precede it or
    how they're encoded — there is no valid encoding of this grammar for a
    trailing backslash. An INTERIOR backslash immediately before a literal
    quote (not at the very end of the value) IS representable and already
    round-trips correctly with the escaping below — only the trailing case
    is refused."""
    flat = " ".join((text or "").split())
    if flat.endswith("\\"):
        raise SkillApplyError(
            f"cannot scaffold: the {field_name} value ends with a literal "
            "backslash, which loop.conf's KEY=value grammar cannot represent "
            "— edit the answer so it does not end in '\\'"
        )
    return '"' + flat.replace('"', '\\"') + '"'


def _sanitize_tags(raw_tags) -> list:
    """Best-effort filter of an optional top-level `answers["tags"]` list
    down to loop.conf's tag grammar (`^[a-z][a-z0-9:_-]{1,40}$`, deduped,
    max 8, §5) — silently drops anything that doesn't fit rather than
    failing the whole apply() over a cosmetic field."""
    if not raw_tags:
        return []
    out = []
    for t in raw_tags:
        t = str(t).strip()
        if t and _APPLY_TAG_RE.match(t) and t not in out:
            out.append(t)
        if len(out) >= 8:
            break
    return out


def _validate_structured_answers(answers: dict) -> None:
    """Fail fast, before any rendering or writing, on a malformed
    `answers.json` shape or an out-of-range structured budget key — "better
    a loud refusal than an invented number" (round-1 review). Two classes of
    check:

    1. Shape: `answers["answers"]`/`answers["provenance"]`, if present, must
       be objects (dicts) — `{"answers": ["q1_purpose"]}` (a list) used to
       traceback deep inside `_resolved()` (list indexing by string), which
       both crashed the CLI AND (before the render-before-write restructure)
       left a half-written `loops.d/<name>/` behind that made the next,
       correct attempt fail with a spurious "already exists".
    2. Structured budget keys — the ONLY source of `model`/`timeout_s`/
       `retry_transient` in loop.conf now (round-1 review defect #2: q11_budget
       free text is SPEC.md §11 prose ONLY, never config; see
       `_render_loop_conf`). Validated against loopconf.py's own ranges."""
    answers_field = answers.get("answers")
    if answers_field is not None and not isinstance(answers_field, dict):
        raise SkillApplyError(
            "invalid answers.json: 'answers' must be an object mapping "
            f"question_id -> value, got {type(answers_field).__name__}"
        )

    provenance_field = answers.get("provenance")
    if provenance_field is not None and not isinstance(provenance_field, dict):
        raise SkillApplyError(
            "invalid answers.json: 'provenance' must be an object, got "
            f"{type(provenance_field).__name__}"
        )

    model = answers.get("model")
    if model is not None and (
        not isinstance(model, str) or not _MODEL_SHAPE_RE.fullmatch(model)
    ):
        raise SkillApplyError(
            "invalid answers.json: 'model' must be a single whitespace-free "
            f"token (no spaces, tabs, or newlines) — loop.conf writes it as a "
            f"bare, unquoted value — got {model!r}"
        )

    timeout_s = answers.get("timeout_s")
    if timeout_s is not None and (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, int)
        or not (_TIMEOUT_S_MIN <= timeout_s <= _TIMEOUT_S_MAX)
    ):
        raise SkillApplyError(
            "invalid answers.json: 'timeout_s' must be an integer in "
            f"{_TIMEOUT_S_MIN}-{_TIMEOUT_S_MAX}, got {timeout_s!r}"
        )

    retry_transient = answers.get("retry_transient")
    if retry_transient is not None and (
        isinstance(retry_transient, bool)
        or not isinstance(retry_transient, int)
        or not (_RETRY_TRANSIENT_MIN <= retry_transient <= _RETRY_TRANSIENT_MAX)
    ):
        raise SkillApplyError(
            "invalid answers.json: 'retry_transient' must be an integer "
            f"in {_RETRY_TRANSIENT_MIN}-{_RETRY_TRANSIENT_MAX}, got "
            f"{retry_transient!r}"
        )


def _render_loop_conf(
    name: str,
    analysis: dict,
    rubric: dict,
    answers: dict,
    blocked: bool,
    acknowledge_blocked: bool,
) -> str:
    """loop.conf: name/description/type/engine from analysis+rubric,
    schedule from `q4_cadence`, tags from the optional top-level
    `answers["tags"]`, and the permission axes ALWAYS at the report-only
    floor `analysis["axes"]` — import never raises an axis itself
    (docs/SKILL_IMPORT.md §1), same as `analyze()`. Ruling 3: a blocked-but-
    acknowledged skill forces `schedule=manual` regardless of what
    `q4_cadence` says.

    Round-1 review defect #2: `model`/`timeout_s`/`retry_transient` come
    ONLY from the optional top-level structured `answers["model"]`/
    `["timeout_s"]`/`["retry_transient"]` keys (validated in
    `_validate_structured_answers`, called before this function ever runs) —
    NEVER scraped from `q11_budget`'s free text. That prose is SPEC.md §11
    documentation only; regex-scraping it previously invented config values
    from unrelated words ("...no model override needed..." -> `model=override`)."""
    description = _resolved(rubric, answers, "q1_purpose") or (
        "TODO: one-line description of what this loop does"
    )
    loop_type = analysis.get("type") or "agent"
    engine = analysis.get("engine") or "codex"

    if blocked and acknowledge_blocked:
        schedule_value = "manual"
    else:
        schedule_value = _resolved(rubric, answers, "q4_cadence") or "manual"
        # Round-2 review: the same "silently unparseable loop.conf" shape as
        # the model bug above — free-text cadence like "daily at 07:30"
        # would otherwise pass straight through to loop.conf's `schedule=`
        # and only fail later, opaquely, inside `loopconf.parse()`. Validate
        # against the REAL schedule grammar (bin/schedule.py — the single
        # parser also used by loopconf.py/loopctl/dashboard) rather than
        # reinventing a regex, and refuse loudly, naming the accepted forms.
        try:
            _schedule.parse(schedule_value)
        except ValueError as e:
            raise SkillApplyError(
                "invalid q4_cadence answer: not a valid schedule spec — "
                f"got {schedule_value!r} ({e}). Accepted forms: manual | "
                "interval:<N>m | interval:<N>h | daily:HH:MM | "
                "times:HH:MM[,HH:MM...] | weekly:<day>:HH:MM | "
                "monthly:<DD>:HH:MM (docs/LOOP_AUTHORING.md §5)"
            ) from e

    header_comment = (
        "# Scaffolded by `loopctl import --apply` "
        f"(analyzer_version={analysis.get('analyzer_version')}, "
        f"skill_sha256={analysis.get('skill_sha256')})."
    )
    lines = [
        header_comment,
        "# TODO before installing: read SPEC.md end to end — every remaining",
        "# [FILL: ...] there blocks `loopctl validate`. Permission axes below are",
        "# the report-only floor and were never raised by import — raise them",
        "# deliberately if this loop genuinely needs more (docs/LOOP_AUTHORING.md §4).",
        f"name={name}",
        f"description={_quote_conf_value(description, 'description')}",
        f"type={loop_type}",
        f"engine={engine}",
    ]

    model = (answers or {}).get("model")
    if model:
        lines.append(f"model={model}")

    lines.append(f"schedule={schedule_value}")

    timeout_s = (answers or {}).get("timeout_s")
    if timeout_s is not None:
        lines.append(f"timeout_s={timeout_s}")

    retry_transient = (answers or {}).get("retry_transient")
    if retry_transient is not None:
        lines.append(f"retry_transient={retry_transient}")

    axes = analysis.get("axes") or {}
    for axis_key in (
        "perm_fs_write",
        "perm_network",
        "perm_local_exec",
        "perm_remote_mutation",
    ):
        if axis_key in axes:
            lines.append(f"{axis_key}={axes[axis_key]}")

    tags = _sanitize_tags((answers or {}).get("tags"))
    if tags:
        lines.append("tags=" + ",".join(tags))

    return "\n".join(lines) + "\n"


def _render_precheck_sh(name: str, analysis: dict) -> str:
    """precheck.sh: the safe `_PRECHECK_SH_TEMPLATE` boilerplate (still the
    fallback TODO/echo — apply() never assumes the extracted candidates are
    the whole story), plus `analysis["precheck_proposal"]`'s COMMENTED
    candidate lines spliced in right after `set -euo pipefail`. Every
    candidate line is already prefixed `#` by `_propose_precheck` — apply()
    never uncomments anything (docs/SKILL_IMPORT.md §6)."""
    base = _render_template(_PRECHECK_SH_TEMPLATE, name)
    proposal = analysis.get("precheck_proposal") or []
    if not proposal:
        return base

    block_lines = (
        [
            "",
            "# --- Imported from skill: proposed precheck candidates (COMMENTED — read",
            "# docs/SKILL_IMPORT.md §6 before ever uncommenting a line here; precheck.sh",
            '# runs UNSANDBOXED bash, and "[read-only?]" is a heuristic hint, not a',
            "# guarantee) ---",
        ]
        + list(proposal)
        + [""]
    )
    block = "\n".join(block_lines)
    marker = "set -euo pipefail\n"
    idx = base.index(marker) + len(marker)
    return base[:idx] + block + "\n" + base[idx:]


def _render_dashboard_json(rubric: dict, answers: dict) -> str:
    """dashboard.json from the resolved `q10_metrics` answer when it parses
    as `{"panels": [...]}`, else the same empty-panels default `loopctl new`
    writes."""
    raw = _resolved(rubric, answers, "q10_metrics")
    if raw:
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            parsed = None
        if isinstance(parsed, dict) and isinstance(parsed.get("panels"), list):
            return json.dumps(parsed) + "\n"
    return '{"panels": []}\n'


def apply(skill: dict, analysis: dict, answers: dict, dest_dir: str) -> list:
    """Scaffold a loop at `dest_dir` from a parsed `skill`, its `analysis`
    (`analyze(skill)`'s output), and a filled-in `answers.json` dict
    (docs/SKILL_IMPORT.md §7). Returns the list of paths written. Never
    installs (docs/SKILL_IMPORT.md §7/§8 — `loopctl validate` -> `loopctl
    run` -> `loopctl install` are unchanged downstream gates).

    Raises `SkillApplyError` (message is user-facing) for every refusal case:
    stale `answers` (analyzer_version or skill_sha256 mismatch against the
    freshly re-parsed `skill`/`analysis` the caller passes in — re-run
    --analyze rather than hand-patching the hash), a malformed `answers.json`
    shape or an out-of-range structured budget key (`_validate_structured_
    answers`), or a `blocked` analysis without `acknowledge_blocked: true`
    (checked `is True` — round-1 review: a truthy-but-wrong JSON value like
    the string `"false"` must never acknowledge).

    Round-1 review (defense in depth): every file's content is fully
    RENDERED into local variables before `dest_dir` is ever created or
    anything is written — a genuinely unexpected rendering failure (bad
    input this function doesn't specifically validate for) must never leave
    a half-written `dest_dir` behind that would make a corrected retry fail
    with a spurious "already exists".

    The pre-existing-`dest_dir` collision check (refuse unless `--overwrite`)
    is deliberately NOT here — it's a CLI/`--overwrite` concern the caller
    (`bin/loopctl`'s `cmd_import`) owns, since `apply()` has no `--overwrite`
    concept of its own and writing into an already-scaffolded `dest_dir`
    (same five filenames every time) is otherwise harmless idempotent
    overwrite."""
    # Round-2 review: a non-dict, non-None top-level answers.json value
    # (a bare JSON list/string/number — `["x"]`, `"hello"`, `42`) must refuse
    # cleanly here rather than traceback out of the first `.get()` call
    # below. `None` still falls through to `{}` — that's the documented "no
    # answers given" convenience default, not a malformed-shape error.
    if answers is not None and not isinstance(answers, dict):
        raise SkillApplyError(
            "invalid answers.json: the top-level value must be a JSON "
            f"object, got {type(answers).__name__}"
        )
    answers = answers or {}

    if answers.get("analyzer_version") != analysis.get("analyzer_version"):
        raise SkillApplyError(
            "stale answers — analyzer_version mismatch, re-run --analyze"
        )
    if answers.get("skill_sha256") != skill.get("sha256"):
        raise SkillApplyError("stale answers — re-run --analyze")

    _validate_structured_answers(answers)

    blocked = bool(analysis.get("blocked"))
    acknowledge_blocked = answers.get("acknowledge_blocked") is True
    if blocked and not acknowledge_blocked:
        reasons = (
            "; ".join(analysis.get("blocked_reasons") or []) or "(no reasons recorded)"
        )
        raise SkillApplyError(
            "refusing to scaffold a blocked skill: "
            + reasons
            + " — set acknowledge_blocked=true in answers.json to scaffold "
            "anyway (forces schedule=manual + a BLOCKED section in SPEC.md)"
        )

    rubric = analysis.get("rubric") or {}
    name = os.path.basename(os.path.normpath(dest_dir))

    # Render everything FIRST (see docstring) — nothing touches the
    # filesystem until every piece of content has been computed successfully.
    spec_text = _render_spec_md(name, rubric, answers)
    if blocked and acknowledge_blocked:
        spec_text += "\n" + _blocked_spec_section(analysis)

    contents = [
        (
            "loop.conf",
            _render_loop_conf(
                name, analysis, rubric, answers, blocked, acknowledge_blocked
            ),
            False,
        ),
        ("SPEC.md", spec_text, False),
        ("prompt.md", _render_prompt_md(name, skill, rubric, answers), False),
        ("precheck.sh", _render_precheck_sh(name, analysis), True),
        ("dashboard.json", _render_dashboard_json(rubric, answers), False),
    ]

    os.makedirs(dest_dir, exist_ok=True)

    written = []
    for fname, content, executable in contents:
        path = os.path.join(dest_dir, fname)
        with open(path, "w") as f:
            f.write(content)
        if executable:
            os.chmod(path, 0o755)
        written.append(path)

    return written
