#!/usr/bin/env bash
# engines/claude.sh — claude adapter (INTERFACES.md §6, §7.3).
#
# Thin, dumb translator: axes -> claude tool flags, run the CLI, write four
# files into OUT_DIR, exit with the §6.4 classification code. No loop logic
# here. Contains NO adapter-side timeout — the runner owns TIMEOUT_S.
#
# NEVER pass --dangerously-skip-permissions: the user's interactive shell
# aliases `claude` to add that flag, but aliases do not apply in scripts and
# this adapter must invoke the plain binary without it (§7.3 warning).
set -euo pipefail

: "${LOOP_NAME:?LOOP_NAME required}"
: "${RUN_ID:?RUN_ID required}"
: "${LOOPS_ROOT:?LOOPS_ROOT required}"
: "${WORKDIR:?WORKDIR required}"
: "${PROMPT_FILE:?PROMPT_FILE required}"
: "${OUT_DIR:?OUT_DIR required}"
: "${SCHEMA_FILE:?SCHEMA_FILE required}"
: "${PERM_FS_WRITE:?PERM_FS_WRITE required}"
: "${PERM_NETWORK:?PERM_NETWORK required}"
: "${PERM_LOCAL_EXEC:?PERM_LOCAL_EXEC required}"
: "${PERM_REMOTE_MUTATION:?PERM_REMOTE_MUTATION required}"
: "${LOOP_TYPE:?LOOP_TYPE required}"
MODEL="${MODEL:-}"
EXEC_ALLOWLIST="${EXEC_ALLOWLIST:-}"

mkdir -p "$OUT_DIR"

REDACT="$LOOPS_ROOT/bin/redact.py"
STDOUT_RAW="$(mktemp "${TMPDIR:-/tmp}/claude-stdout.XXXXXX")"
STDERR_RAW="$(mktemp "${TMPDIR:-/tmp}/claude-stderr.XXXXXX")"
cleanup() { rm -f "$STDOUT_RAW" "$STDERR_RAW"; }
trap cleanup EXIT

# --- axes -> --tools/--allowedTools (§7.3 table). Floor = --tools "" (no
# tools at all). perm_local_exec=allowlist grants Bash narrowed to one
# --allowedTools "Bash(<pattern>)" per EXEC_ALLOWLIST entry. perm_local_exec
# =full grants Bash with no --allowedTools narrowing (unrestricted, per the
# CLI's tools-grants/allowedTools-narrows model). perm_fs_write and
# perm_network have no dedicated claude flag in §7.3 (fs_write only steers
# codex's sandbox mode; network is governed by which tools are exposed —
# there is no Bash/WebFetch grant tied to perm_network alone in v1). ---
TOOLS=""
ALLOWED_TOOLS_ARGS=()

case "$PERM_LOCAL_EXEC" in
  allowlist)
    TOOLS="Bash"
    if [ -n "$EXEC_ALLOWLIST" ]; then
      IFS=',' read -ra _raw_patterns <<<"$EXEC_ALLOWLIST"
      for _p in "${_raw_patterns[@]}"; do
        # trim leading/trailing whitespace
        _p="${_p#"${_p%%[![:space:]]*}"}"
        _p="${_p%"${_p##*[![:space:]]}"}"
        [ -n "$_p" ] || continue
        ALLOWED_TOOLS_ARGS+=(--allowedTools "Bash($_p)")
      done
    fi
    ;;
  full)
    TOOLS="Bash"
    ;;
  none|*)
    TOOLS=""
    ;;
esac

cmd=(claude -p --output-format json --json-schema "$(cat "$SCHEMA_FILE")")

if [ -n "$MODEL" ]; then
  cmd+=(--model "$MODEL")
fi

cmd+=(--tools "$TOOLS")

if [ "${#ALLOWED_TOOLS_ARGS[@]}" -gt 0 ]; then
  cmd+=("${ALLOWED_TOOLS_ARGS[@]}")
fi

cmd+=(--setting-sources "" --strict-mcp-config --no-session-persistence --disable-slash-commands)

set +e
"${cmd[@]}" <"$PROMPT_FILE" >"$STDOUT_RAW" 2>"$STDERR_RAW"
EXIT_CODE=$?
set -e

# engine.log: stdout+stderr through bin/redact.py (§4.4/§6.3).
{ cat "$STDOUT_RAW"; cat "$STDERR_RAW"; } | python3 "$REDACT" >"$OUT_DIR/engine.log"

ADAPTER_EXIT="$(python3 - "$STDOUT_RAW" "$STDERR_RAW" "$OUT_DIR" "$EXIT_CODE" <<'PY'
import json
import os
import re
import sys

stdout_path, stderr_path, out_dir, proc_exit = sys.argv[1:5]
proc_exit = int(proc_exit)

with open(stdout_path, "r", errors="replace") as f:
    stdout_text = f.read()
with open(stderr_path, "r", errors="replace") as f:
    stderr_text = f.read()

try:
    obj = json.loads(stdout_text.strip())
    if not isinstance(obj, dict):
        obj = None
except (json.JSONDecodeError, ValueError):
    obj = None

# usage.json — whole result object, verbatim, best-effort.
with open(os.path.join(out_dir, "usage.json"), "w") as f:
    if obj is not None:
        json.dump(obj, f)
    else:
        f.write("{}")

AUTH_RE = re.compile(r"401|unauthorized|login", re.IGNORECASE)
TRANSIENT_RE = re.compile(
    r"429|rate.?limit|5xx|5\d\d|overloaded|stream disconnected|connection|network",
    re.IGNORECASE,
)


def classify_status_code(value):
    try:
        code = int(value)
    except (TypeError, ValueError):
        return None
    if code in (401, 403):
        return "auth"
    if code == 429 or 500 <= code < 600:
        return "transient"
    return None


status = None
adapter_exit = None

if obj is not None:
    is_error = bool(obj.get("is_error"))
    subtype = obj.get("subtype")
    structured_output = obj.get("structured_output")
    permission_denials = obj.get("permission_denials") or []
    api_error_status = obj.get("api_error_status")

    success = (
        proc_exit == 0
        and not is_error
        and subtype in (None, "success")
        and structured_output is not None
    )

    if success:
        with open(os.path.join(out_dir, "contract.json.tmp"), "w") as f:
            json.dump(structured_output, f)
        status = "ok"
        adapter_exit = 0
    else:
        bucket = classify_status_code(api_error_status)
        combined = stdout_text + "\n" + stderr_text
        if bucket == "auth":
            status = "auth-failed"
            adapter_exit = 10
        elif bucket == "transient":
            status = "transient"
            adapter_exit = 12
        elif structured_output is None and len(permission_denials) > 0:
            status = "tool-denied"
            adapter_exit = 11
        elif AUTH_RE.search(combined):
            status = "auth-failed"
            adapter_exit = 10
        elif TRANSIENT_RE.search(combined):
            status = "transient"
            adapter_exit = 12
        else:
            status = "engine-failed"
            adapter_exit = 1
else:
    # Could not parse a result object at all — classify from raw text/exit.
    combined = stdout_text + "\n" + stderr_text
    if AUTH_RE.search(combined):
        status = "auth-failed"
        adapter_exit = 10
    elif TRANSIENT_RE.search(combined):
        status = "transient"
        adapter_exit = 12
    else:
        status = "engine-failed"
        adapter_exit = 1

with open(os.path.join(out_dir, "engine.status"), "w") as f:
    f.write(f"status={status} exit={adapter_exit}\n")

print(adapter_exit)
PY
)"

exit "$ADAPTER_EXIT"
