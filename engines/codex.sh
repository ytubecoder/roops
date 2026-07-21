#!/usr/bin/env bash
# engines/codex.sh — codex adapter (INTERFACES.md §6, §7.2).
#
# Thin, dumb translator: axes -> codex flags, run the CLI, write four files
# into OUT_DIR, exit with the §6.4 classification code. No loop logic here.
# Contains NO adapter-side timeout — the runner (bin/run-loop.sh) owns
# enforcement of TIMEOUT_S via a process-group timeout.
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
O_FILE="$OUT_DIR/last-message.json"
STDOUT_RAW="$(mktemp "${TMPDIR:-/tmp}/codex-stdout.XXXXXX")"
STDERR_RAW="$(mktemp "${TMPDIR:-/tmp}/codex-stderr.XXXXXX")"
cleanup() { rm -f "$STDOUT_RAW" "$STDERR_RAW"; }
trap cleanup EXIT

# --- axes -> flags (§7.2 table; read-only floor; workspace-write only when
# PERM_FS_WRITE=workdir; network key only when PERM_NETWORK=full — the two
# conditions are independent per the table's two flag rows). ---
if [ "$PERM_FS_WRITE" = "workdir" ]; then
  SANDBOX="workspace-write"
else
  SANDBOX="read-only"
fi

cmd=(codex exec --skip-git-repo-check --ephemeral -C "$WORKDIR" -s "$SANDBOX")

if [ "$PERM_NETWORK" = "full" ]; then
  cmd+=(-c "sandbox_workspace_write.network_access=true")
fi

cmd+=(--output-schema "$SCHEMA_FILE" -o "$O_FILE" --json)

if [ -n "$MODEL" ]; then
  cmd+=(-m "$MODEL")
fi

cmd+=(-)

# perm_local_exec: codex has no dedicated flag (§7.2 — sandbox + credential
# scoping enforce it, not a CLI flag). Nothing to add here.

set +e
"${cmd[@]}" <"$PROMPT_FILE" >"$STDOUT_RAW" 2>"$STDERR_RAW"
EXIT_CODE=$?
set -e

# engine.log: stdout+stderr through bin/redact.py (§4.4/§6.3).
{ cat "$STDOUT_RAW"; cat "$STDERR_RAW"; } | python3 "$REDACT" >"$OUT_DIR/engine.log"

# Classify, write contract.json.tmp / usage.json / engine.status, and print
# the adapter's own exit code on stdout for the shell to pick up.
ADAPTER_EXIT="$(python3 - "$STDOUT_RAW" "$STDERR_RAW" "$O_FILE" "$OUT_DIR" "$EXIT_CODE" <<'PY'
import json
import os
import re
import sys

stdout_path, stderr_path, o_file, out_dir, proc_exit = sys.argv[1:6]
proc_exit = int(proc_exit)

with open(stdout_path, "r", errors="replace") as f:
    stdout_text = f.read()
with open(stderr_path, "r", errors="replace") as f:
    stderr_text = f.read()

# usage.json -- best-effort, from the last turn.completed event, .usage.
usage = {}
for line in stdout_text.splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        continue
    if isinstance(obj, dict) and obj.get("type") == "turn.completed":
        usage = obj.get("usage") or {}
with open(os.path.join(out_dir, "usage.json"), "w") as f:
    json.dump(usage, f)

success = proc_exit == 0 and os.path.exists(o_file) and os.path.getsize(o_file) > 0

AUTH_RE = re.compile(r"401|unauthorized|login", re.IGNORECASE)
TRANSIENT_RE = re.compile(
    r"429|rate.?limit|5xx|5\d\d|overloaded|stream disconnected|connection|network",
    re.IGNORECASE,
)

if success:
    with open(o_file, "r") as src, open(os.path.join(out_dir, "contract.json.tmp"), "w") as dst:
        dst.write(src.read())
    status = "ok"
    adapter_exit = 0
else:
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
