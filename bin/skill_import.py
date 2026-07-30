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
import re

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


def _has_cli_equivalent(text: str) -> bool:
    """mcp-without-cli-equivalent heuristic: does `text` also name curl or a
    known CLI (KNOWN_CLIS) anywhere?"""
    return bool(_CLI_TOKEN_RE.search(text))


def _body_headings(body: str) -> list:
    """Markdown headings (`#`..`######`) found in `body`, in document order."""
    return [
        m.group(1).strip() for m in re.finditer(r"^#{1,6}\s+(.+)$", body, re.MULTILINE)
    ]


def _extract_candidate_commands(body: str) -> list:
    """Candidate shell snippets for the precheck proposal: backtick-quoted
    inline spans and lines inside fenced ```bash blocks, in document order.
    Only `body` is scanned (not bundled files) — precheck candidates come
    from what the skill's own instructions literally tell the engine to run.
    """
    candidates = []
    in_fence = False
    in_bash_fence = False
    for line in body.split("\n"):
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                in_fence = True
                lang = stripped[3:].strip().lower()
                in_bash_fence = lang == "bash"
            else:
                in_fence = False
                in_bash_fence = False
            continue
        if in_fence:
            if in_bash_fence and stripped:
                candidates.append(stripped)
            continue
        for m in re.finditer(r"`([^`\n]+)`", line):
            content = m.group(1).strip()
            if content:
                candidates.append(content)
    # Preserve order, drop exact duplicates.
    return list(dict.fromkeys(candidates))


def _is_read_only_command(cmd_line: str) -> bool:
    """Is `cmd_line` a scoped-read form per the brief's read-only table? Only
    the leading command (before any pipe/`;`/`&&`) decides the class — a
    read-only lead into e.g. `| wc -l` is still read-only overall."""
    head = re.split(r"\||;|&&", cmd_line, maxsplit=1)[0].strip()
    tokens = head.split()
    if not tokens:
        return False
    if tokens[0] == "git":
        subcmd = None
        i = 1
        while i < len(tokens):
            tok = tokens[i]
            if tok in _GIT_VALUE_FLAGS:
                i += 2
                continue
            if tok.startswith("-"):
                i += 1
                continue
            subcmd = tok
            break
        return subcmd in READ_ONLY_GIT_SUBCOMMANDS
    return tokens[0] in READ_ONLY_SIMPLE_COMMANDS


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


def _flags_summary(flags: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in flags.items())


def _q10_options(text: str) -> list:
    """Panel-type options for q10_metrics: if the skill's checks mention
    counts/numbers, offer the full dashboard.json panel-type set
    (docs/LOOP_AUTHORING.md §3.3); otherwise just the two generic presets."""
    generic = [
        {"id": "number", "label": "number (single current value)"},
        {"id": "trend", "label": "trend (history over window_days)"},
    ]
    if re.search(r"\bcount\b|\bnumber of\b|wc -l|\d+", text, re.IGNORECASE):
        return generic + [
            {"id": "table", "label": "table (array of objects, stable columns)"},
            {"id": "list", "label": "list (array of scalars)"},
        ]
    return generic


def _q10_context(text: str) -> str:
    if re.search(r"\bcount\b|\bnumber of\b|wc -l|\d+", text, re.IGNORECASE):
        return (
            "the skill's checks mention counts/numbers, so table/list panels "
            "may fit in addition to a single number."
        )
    return "no specific counts were mentioned, so only the generic number/trend presets are offered."


def _build_rubric(frontmatter: dict, body: str, flags: dict, axes: dict) -> dict:
    """Classify all eleven intake-rubric questions (docs/LOOP_AUTHORING.md §2,
    ids q1_purpose..q11_budget) into a bucket, per the task-9 ambiguity
    resolution:
      - q1_purpose: answered, value = frontmatter description (else missing)
      - q5_scope: answered (partial, heuristic) from body headings if any
        present, else missing
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
    if headings:
        rubric["q5_scope"] = {"bucket": "answered", "value": "; ".join(headings)}
    else:
        rubric["q5_scope"] = {"bucket": "missing"}

    rubric["q6_guardrails"] = {
        "bucket": "derived",
        "value": (
            "report-only floor (perm_fs_write=report_only, perm_network=none, "
            "perm_local_exec=none, perm_remote_mutation=none); detected flags: "
            + _flags_summary(flags)
        ),
    }
    rubric["q7_axes"] = {"bucket": "derived", "value": dict(axes)}

    rubric["q8_finding_identity"] = {"bucket": "missing"}
    rubric["q9_semantics"] = {"bucket": "missing"}
    rubric["q10_metrics"] = {"bucket": "missing"}
    rubric["q11_budget"] = {"bucket": "missing"}

    for rubric_id, reasons in INCOMPATIBLE_RUBRIC_MAP.items():
        hits = [reason for flag_name, reason in reasons if flags.get(flag_name)]
        if hits:
            rubric[rubric_id] = {"bucket": "incompatible", "value": " ".join(hits)}

    return rubric


def _build_answers_needed(flags: dict, text: str) -> list:
    """The always-missing questions (never statically answerable), plus a
    per-axis raise question for each of network/mutation that fired."""
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
                    "this does not by itself grant remote-mutation rights."
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
                    "line that must be uncommented deliberately."
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

    blocked = bool(flags["credentials"]) or (
        bool(flags["mcp"]) and not _has_cli_equivalent(text)
    )

    raw_name = (
        frontmatter.get("name")
        or os.path.basename((skill.get("skill_dir") or "").rstrip(os.sep))
        or "imported-skill"
    )
    proposed_name = _sanitize_name(raw_name)

    precheck_proposal = _propose_precheck(body)
    rubric = _build_rubric(frontmatter, body, flags, axes)
    answers_needed = _build_answers_needed(flags, text)

    notes = list(skill.get("notes", []) or [])
    notes.append(
        "type recommendation is a v1 constant ('agent'); watchdog "
        "classification is not attempted by the static analyzer — reassess "
        "manually if this looks like a single-probe health check."
    )
    if rubric["q5_scope"]["bucket"] == "answered":
        notes.append(
            "q5_scope's 'answered' value is a coarse heuristic (body "
            "headings), not a real scope/exclusions statement — confirm "
            "with the user before relying on it."
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
        "rubric": rubric,
        "precheck_proposal": precheck_proposal,
        "answers_needed": answers_needed,
        "notes": notes,
    }
