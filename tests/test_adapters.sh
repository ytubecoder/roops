#!/usr/bin/env bash
# tests/test_adapters.sh — hermetic tests for engines/codex.sh and
# engines/claude.sh (INTERFACES.md §6, §7; ENGINE_PROBES.md §7).
#
# Never invokes a real engine CLI (§11): PATH is shimmed with fake `codex`
# and `claude` executables that record their argv (NUL-delimited, since the
# --output-schema/--json-schema argument is the whole multi-line schema file
# content as ONE shell word) and emit canned output controlled by env knobs.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/.." && pwd)"

FAILED=0
PASSED=0
fail() {
  echo "FAIL: $1"
  FAILED=$((FAILED + 1))
}
ok() {
  PASSED=$((PASSED + 1))
}

TMP_ROOT="$(mktemp -d)"
cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

SHIM_DIR="$TMP_ROOT/shims"
mkdir -p "$SHIM_DIR"

# Hermetic fake LOOPS_ROOT — only bin/redact.py is needed by the adapters,
# copied in so nothing under the real repo's state/ is ever touched.
FAKE_LOOPS_ROOT="$TMP_ROOT/loops_root"
mkdir -p "$FAKE_LOOPS_ROOT/bin"
cp "$REPO_ROOT/bin/redact.py" "$FAKE_LOOPS_ROOT/bin/redact.py"

SCHEMA_FILE="$REPO_ROOT/contract/contract.schema.json"
WORKDIR="$TMP_ROOT/workdir"
mkdir -p "$WORKDIR"
PROMPT_FILE="$TMP_ROOT/prompt.md"
printf 'TEST PROMPT CONTENT\nsecond line\n' >"$PROMPT_FILE"

CODEX_ADAPTER="$REPO_ROOT/engines/codex.sh"
CLAUDE_ADAPTER="$REPO_ROOT/engines/claude.sh"

# ---------------------------------------------------------------------------
# Fixture: a schema-conformant contract object (matches
# contract/contract.schema.json exactly).
# ---------------------------------------------------------------------------
CONTRACT_JSON='{"schema_version":1,"run_id":"testrun","status":"ok","status_reason":"clean","headline":"all good","report_markdown":"# report","metrics":"{}","findings":[]}'

CLAUDE_SUCCESS_JSON="{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false,\"result\":\"ok\",\"structured_output\":$CONTRACT_JSON,\"total_cost_usd\":0.0186,\"usage\":{\"input_tokens\":50,\"output_tokens\":10},\"permission_denials\":[],\"session_id\":\"sess-1\",\"num_turns\":1}"
CLAUDE_AUTH_JSON='{"type":"result","subtype":"error","is_error":true,"api_error_status":401,"permission_denials":[]}'
CLAUDE_TRANSIENT_JSON='{"type":"result","subtype":"error","is_error":true,"api_error_status":429,"permission_denials":[]}'
CLAUDE_TOOLDENIED_JSON='{"type":"result","subtype":"error","is_error":true,"permission_denials":[{"tool":"Bash","pattern":"Bash(rm -rf /)"}]}'
CLAUDE_GENERIC_JSON='{"type":"result","subtype":"error","is_error":true,"permission_denials":[]}'

# ---------------------------------------------------------------------------
# Shims
# ---------------------------------------------------------------------------
cat >"$SHIM_DIR/codex" <<'SHIM'
#!/usr/bin/env bash
# Fake codex CLI for adapter tests: records argv NUL-delimited, captures
# stdin, then emits canned JSONL/exit behavior controlled by env knobs.
: "${CODEX_ARGV_OUT:?}"
: >"$CODEX_ARGV_OUT"
for a in "$@"; do
  printf '%s\0' "$a" >>"$CODEX_ARGV_OUT"
done

if [ -n "${CODEX_STDIN_OUT:-}" ]; then
  cat >"$CODEX_STDIN_OUT"
else
  cat >/dev/null
fi

o_file=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then
    o_file="$a"
  fi
  prev="$a"
done

if [ -n "${CODEX_STDERR_TEXT:-}" ]; then
  printf '%s\n' "$CODEX_STDERR_TEXT" >&2
fi

exit_code="${CODEX_EXIT_CODE:-0}"

if [ "$exit_code" = "0" ]; then
  if [ -n "$o_file" ] && [ -n "${CODEX_CONTRACT_JSON:-}" ]; then
    printf '%s' "$CODEX_CONTRACT_JSON" >"$o_file"
  fi
  echo '{"type":"thread.started"}'
  echo '{"type":"turn.started"}'
  echo '{"type":"item.completed"}'
  echo '{"type":"turn.completed","usage":{"input_tokens":100,"cached_input_tokens":10,"output_tokens":20,"reasoning_output_tokens":5}}'
else
  echo '{"type":"thread.started"}'
  echo '{"type":"turn.started"}'
  if [ -n "${CODEX_ERROR_MSG:-}" ]; then
    printf '{"type":"error","message":"%s"}\n' "$CODEX_ERROR_MSG"
  fi
  echo '{"type":"turn.failed"}'
fi
exit "$exit_code"
SHIM
chmod +x "$SHIM_DIR/codex"

cat >"$SHIM_DIR/claude" <<'SHIM'
#!/usr/bin/env bash
# Fake claude CLI for adapter tests: records argv NUL-delimited, captures
# stdin, then prints one canned JSON result object controlled by env knobs.
: "${CLAUDE_ARGV_OUT:?}"
: >"$CLAUDE_ARGV_OUT"
for a in "$@"; do
  printf '%s\0' "$a" >>"$CLAUDE_ARGV_OUT"
done

if [ -n "${CLAUDE_STDIN_OUT:-}" ]; then
  cat >"$CLAUDE_STDIN_OUT"
else
  cat >/dev/null
fi

if [ -n "${CLAUDE_STDERR_TEXT:-}" ]; then
  printf '%s\n' "$CLAUDE_STDERR_TEXT" >&2
fi

printf '%s' "${CLAUDE_RESULT_JSON:?}"
exit "${CLAUDE_EXIT_CODE:-0}"
SHIM
chmod +x "$SHIM_DIR/claude"

# ---------------------------------------------------------------------------
# argv assertion helpers (NUL-delimited parsing; each is a plain function
# definition, not nested inside a $(...) heredoc — that combination breaks
# on macOS's stock bash 3.2 when the heredoc body contains an apostrophe).
# ---------------------------------------------------------------------------
argv_has_pair() {
  local file="$1" flag="$2" value="$3"
  python3 - "$file" "$flag" "$value" <<'PY'
import sys
path, flag, value = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, "rb") as f:
    data = f.read()
tokens = data.split(b"\x00")
if tokens and tokens[-1] == b"":
    tokens = tokens[:-1]
tokens = [t.decode("utf-8", "replace") for t in tokens]
for i in range(len(tokens) - 1):
    if tokens[i] == flag and tokens[i + 1] == value:
        sys.exit(0)
sys.exit(1)
PY
}

argv_has_token() {
  local file="$1" token="$2"
  python3 - "$file" "$token" <<'PY'
import sys
path, token = sys.argv[1], sys.argv[2]
with open(path, "rb") as f:
    data = f.read()
tokens = data.split(b"\x00")
if tokens and tokens[-1] == b"":
    tokens = tokens[:-1]
tokens = [t.decode("utf-8", "replace") for t in tokens]
sys.exit(0 if token in tokens else 1)
PY
}

argv_value_after_equals_file() {
  local file="$1" flag="$2" ref="$3"
  python3 - "$file" "$flag" "$ref" <<'PY'
import sys
path, flag, ref = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, "rb") as f:
    data = f.read()
tokens = data.split(b"\x00")
if tokens and tokens[-1] == b"":
    tokens = tokens[:-1]
tokens = [t.decode("utf-8", "replace") for t in tokens]
with open(ref, "r") as f:
    # $(cat "$ref") strips ALL trailing newlines (command substitution
    # behavior) -- mirror that so this compares what the adapter actually
    # passed, not raw file bytes.
    ref_content = f.read().rstrip("\n")
for i in range(len(tokens) - 1):
    if tokens[i] == flag:
        sys.exit(0 if tokens[i + 1] == ref_content else 1)
sys.exit(1)
PY
}

argv_count_token() {
  local file="$1" token="$2"
  python3 - "$file" "$token" <<'PY'
import sys
path, token = sys.argv[1], sys.argv[2]
with open(path, "rb") as f:
    data = f.read()
tokens = data.split(b"\x00")
if tokens and tokens[-1] == b"":
    tokens = tokens[:-1]
tokens = [t.decode("utf-8", "replace") for t in tokens]
print(sum(1 for t in tokens if t == token))
PY
}

json_eq_file_and_literal() {
  local file="$1" literal="$2"
  python3 - "$file" "$literal" <<'PY'
import json, sys
path, literal = sys.argv[1], sys.argv[2]
with open(path) as f:
    a = json.load(f)
b = json.loads(literal)
sys.exit(0 if a == b else 1)
PY
}

assert() {
  local desc="$1"
  shift
  if "$@"; then ok; else fail "$desc"; fi
}

assert_not() {
  local desc="$1"
  shift
  if "$@"; then fail "$desc"; else ok; fi
}

assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    ok
  else
    fail "$desc (expected [$expected] got [$actual])"
  fi
}

assert_file_exists() {
  local desc="$1" file="$2"
  if [ -f "$file" ]; then ok; else fail "$desc (missing: $file)"; fi
}

assert_file_absent() {
  local desc="$1" file="$2"
  if [ ! -e "$file" ]; then ok; else fail "$desc (unexpectedly present: $file)"; fi
}

# ---------------------------------------------------------------------------
# Adapter invocation helpers. Each call gets a brand-new, fully clean
# environment (env -i) so no test can leak state into the next.
# ---------------------------------------------------------------------------
new_out_dir() {
  mktemp -d "$TMP_ROOT/out.XXXXXX"
}

# run_codex OUT_DIR FS_WRITE NETWORK LOCAL_EXEC REMOTE_MUT ALLOWLIST MODEL \
#           EXIT_CODE ERROR_MSG STDERR_TEXT CONTRACT_JSON
run_codex() {
  local out_dir="$1" fs_write="$2" network="$3" local_exec="$4" remote_mut="$5"
  local allowlist="$6" model="$7" exit_code="${8:-0}" error_msg="${9:-}"
  local stderr_text="${10:-}" contract_json="${11:-}"
  env -i \
    HOME="$HOME" \
    PATH="$SHIM_DIR:$PATH" \
    LOOP_NAME="testloop" RUN_ID="testrun" LOOPS_ROOT="$FAKE_LOOPS_ROOT" \
    WORKDIR="$WORKDIR" PROMPT_FILE="$PROMPT_FILE" OUT_DIR="$out_dir" \
    TIMEOUT_S="900" SCHEMA_FILE="$SCHEMA_FILE" LOOP_TYPE="agent" \
    PERM_FS_WRITE="$fs_write" PERM_NETWORK="$network" \
    PERM_LOCAL_EXEC="$local_exec" PERM_REMOTE_MUTATION="$remote_mut" \
    EXEC_ALLOWLIST="$allowlist" MODEL="$model" \
    CODEX_ARGV_OUT="$out_dir/.argv" CODEX_STDIN_OUT="$out_dir/.stdin" \
    CODEX_EXIT_CODE="$exit_code" CODEX_ERROR_MSG="$error_msg" \
    CODEX_STDERR_TEXT="$stderr_text" CODEX_CONTRACT_JSON="$contract_json" \
    "$CODEX_ADAPTER"
  return $?
}

# run_claude OUT_DIR FS_WRITE NETWORK LOCAL_EXEC REMOTE_MUT ALLOWLIST MODEL \
#            EXIT_CODE STDERR_TEXT RESULT_JSON
run_claude() {
  local out_dir="$1" fs_write="$2" network="$3" local_exec="$4" remote_mut="$5"
  local allowlist="$6" model="$7" exit_code="${8:-0}" stderr_text="${9:-}"
  local result_json="${10:-$CLAUDE_SUCCESS_JSON}"
  env -i \
    HOME="$HOME" \
    PATH="$SHIM_DIR:$PATH" \
    LOOP_NAME="testloop" RUN_ID="testrun" LOOPS_ROOT="$FAKE_LOOPS_ROOT" \
    WORKDIR="$WORKDIR" PROMPT_FILE="$PROMPT_FILE" OUT_DIR="$out_dir" \
    TIMEOUT_S="900" SCHEMA_FILE="$SCHEMA_FILE" LOOP_TYPE="agent" \
    PERM_FS_WRITE="$fs_write" PERM_NETWORK="$network" \
    PERM_LOCAL_EXEC="$local_exec" PERM_REMOTE_MUTATION="$remote_mut" \
    EXEC_ALLOWLIST="$allowlist" MODEL="$model" \
    CLAUDE_ARGV_OUT="$out_dir/.argv" CLAUDE_STDIN_OUT="$out_dir/.stdin" \
    CLAUDE_EXIT_CODE="$exit_code" CLAUDE_STDERR_TEXT="$stderr_text" \
    CLAUDE_RESULT_JSON="$result_json" \
    "$CLAUDE_ADAPTER"
  return $?
}

echo "== codex adapter =="

# --- floor axes: report_only/none/none/none ---
d="$(new_out_dir)"
run_codex "$d" report_only none none none "" "" 0 "" "" "$CONTRACT_JSON"
rc=$?
assert_eq "codex floor: adapter exit" "0" "$rc"
assert "codex floor: -s read-only" argv_has_pair "$d/.argv" "-s" "read-only"
assert_not "codex floor: no network key" argv_has_token "$d/.argv" "sandbox_workspace_write.network_access=true"
assert_not "codex floor: no -m (empty MODEL)" argv_has_token "$d/.argv" "-m"
assert "codex floor: prompt via stdin" cmp -s "$PROMPT_FILE" "$d/.stdin"
assert_file_exists "codex floor: contract.json.tmp written" "$d/contract.json.tmp"
assert "codex floor: contract.json.tmp content correct" json_eq_file_and_literal "$d/contract.json.tmp" "$CONTRACT_JSON"
assert_file_exists "codex floor: usage.json written" "$d/usage.json"
assert "codex floor: usage.json extraction correct" json_eq_file_and_literal "$d/usage.json" '{"input_tokens":100,"cached_input_tokens":10,"output_tokens":20,"reasoning_output_tokens":5}'
assert_file_exists "codex floor: engine.log written" "$d/engine.log"
assert_eq "codex floor: engine.status" "status=ok exit=0" "$(cat "$d/engine.status" 2>/dev/null)"

# --- workdir-write axes ---
d="$(new_out_dir)"
run_codex "$d" workdir none none none "" "" 0 "" "" "$CONTRACT_JSON"
assert "codex workdir-write: -s workspace-write" argv_has_pair "$d/.argv" "-s" "workspace-write"

# --- network=full axes (report_only fs_write; independent per brief) ---
d="$(new_out_dir)"
run_codex "$d" report_only full none none "" "" 0 "" "" "$CONTRACT_JSON"
assert "codex network=full: -s still read-only" argv_has_pair "$d/.argv" "-s" "read-only"
assert "codex network=full: network_access key present" argv_has_pair "$d/.argv" "-c" "sandbox_workspace_write.network_access=true"

# --- MODEL passthrough ---
d="$(new_out_dir)"
run_codex "$d" report_only none none none "" "gpt-7" 0 "" "" "$CONTRACT_JSON"
assert "codex MODEL: -m gpt-7 present" argv_has_pair "$d/.argv" "-m" "gpt-7"

# --- perm_local_exec has no dedicated codex flag ---
d="$(new_out_dir)"
run_codex "$d" report_only none allowlist none "gh run list,git status" "" 0 "" "" "$CONTRACT_JSON"
n="$(argv_count_token "$d/.argv" "--add-dir")"
assert_eq "codex local_exec=allowlist: no --add-dir flag added" "0" "$n"
assert "codex local_exec=allowlist: still -s read-only (no bash-only flag added)" argv_has_pair "$d/.argv" "-s" "read-only"

# --- failure classification: auth ---
d="$(new_out_dir)"
run_codex "$d" report_only none none none "" "" 1 "401 unauthorized: invalid token" "" ""
rc=$?
assert_eq "codex auth: adapter exit 10" "10" "$rc"
assert_eq "codex auth: engine.status" "status=auth-failed exit=10" "$(cat "$d/engine.status" 2>/dev/null)"
assert_file_absent "codex auth: no contract.json.tmp" "$d/contract.json.tmp"

# --- failure classification: transient ---
d="$(new_out_dir)"
run_codex "$d" report_only none none none "" "" 1 "429 rate limit exceeded, please retry" "" ""
rc=$?
assert_eq "codex transient: adapter exit 12" "12" "$rc"
assert_eq "codex transient: engine.status" "status=transient exit=12" "$(cat "$d/engine.status" 2>/dev/null)"

# --- failure classification: other ---
d="$(new_out_dir)"
run_codex "$d" report_only none none none "" "" 1 "something exploded unexpectedly" "" ""
rc=$?
assert_eq "codex other: adapter exit 1" "1" "$rc"
assert_eq "codex other: engine.status" "status=engine-failed exit=1" "$(cat "$d/engine.status" 2>/dev/null)"

# --- redaction ---
d="$(new_out_dir)"
run_codex "$d" report_only none none none "" "" 0 "" "token: sk-ABCDEFGHIJ0123456789" "$CONTRACT_JSON"
assert_not "codex redaction: raw secret absent from engine.log" grep -q "sk-ABCDEFGHIJ0123456789" "$d/engine.log"
assert "codex redaction: redaction marker present in engine.log" grep -q "«redacted:" "$d/engine.log"

echo "== claude adapter =="

# --- floor axes ---
d="$(new_out_dir)"
run_claude "$d" report_only none none none "" "" 0 "" "$CLAUDE_SUCCESS_JSON"
rc=$?
assert_eq "claude floor: adapter exit" "0" "$rc"
assert "claude floor: --tools empty" argv_has_pair "$d/.argv" "--tools" ""
assert_not "claude floor: no --allowedTools" argv_has_token "$d/.argv" "--allowedTools"
assert_not "claude floor: no --model (empty MODEL)" argv_has_token "$d/.argv" "--model"
assert "claude floor: prompt via stdin" cmp -s "$PROMPT_FILE" "$d/.stdin"
assert "claude floor: --json-schema carries schema file content" argv_value_after_equals_file "$d/.argv" "--json-schema" "$SCHEMA_FILE"
assert "claude floor: --setting-sources empty" argv_has_pair "$d/.argv" "--setting-sources" ""
assert "claude floor: --strict-mcp-config present" argv_has_token "$d/.argv" "--strict-mcp-config"
assert "claude floor: --no-session-persistence present" argv_has_token "$d/.argv" "--no-session-persistence"
assert "claude floor: --disable-slash-commands present" argv_has_token "$d/.argv" "--disable-slash-commands"
assert_file_exists "claude floor: contract.json.tmp written" "$d/contract.json.tmp"
assert "claude floor: contract.json.tmp content correct" json_eq_file_and_literal "$d/contract.json.tmp" "$CONTRACT_JSON"
assert_file_exists "claude floor: usage.json written" "$d/usage.json"
assert "claude floor: usage.json is whole result object" json_eq_file_and_literal "$d/usage.json" "$CLAUDE_SUCCESS_JSON"
assert_file_exists "claude floor: engine.log written" "$d/engine.log"
assert_eq "claude floor: engine.status" "status=ok exit=0" "$(cat "$d/engine.status" 2>/dev/null)"

# --- workdir-write / network axes: claude has no dedicated flag for either
# (per §7.3 table — only perm_local_exec drives --tools) ---
d="$(new_out_dir)"
run_claude "$d" workdir full none none "" "" 0 "" "$CLAUDE_SUCCESS_JSON"
assert "claude workdir+network: --tools still empty (no flag mapped)" argv_has_pair "$d/.argv" "--tools" ""

# --- allowlist with 2 entries ---
d="$(new_out_dir)"
run_claude "$d" report_only none allowlist none "gh run list, git status" "" 0 "" "$CLAUDE_SUCCESS_JSON"
assert "claude allowlist: --tools Bash" argv_has_pair "$d/.argv" "--tools" "Bash"
assert "claude allowlist: allowedTools entry 1 (trimmed)" argv_has_pair "$d/.argv" "--allowedTools" "Bash(gh run list)"
assert "claude allowlist: allowedTools entry 2 (trimmed)" argv_has_pair "$d/.argv" "--allowedTools" "Bash(git status)"
n="$(argv_count_token "$d/.argv" "--allowedTools")"
assert_eq "claude allowlist: exactly 2 allowedTools flags" "2" "$n"

# --- perm_local_exec=full: Bash granted, unrestricted (no allowedTools) ---
d="$(new_out_dir)"
run_claude "$d" report_only none full none "" "" 0 "" "$CLAUDE_SUCCESS_JSON"
assert "claude full: --tools Bash" argv_has_pair "$d/.argv" "--tools" "Bash"
assert_not "claude full: no --allowedTools narrowing" argv_has_token "$d/.argv" "--allowedTools"

# --- MODEL passthrough ---
d="$(new_out_dir)"
run_claude "$d" report_only none none none "" "haiku" 0 "" "$CLAUDE_SUCCESS_JSON"
assert "claude MODEL: --model haiku present" argv_has_pair "$d/.argv" "--model" "haiku"

# --- failure classification: auth (401) ---
d="$(new_out_dir)"
run_claude "$d" report_only none none none "" "" 1 "" "$CLAUDE_AUTH_JSON"
rc=$?
assert_eq "claude auth: adapter exit 10" "10" "$rc"
assert_eq "claude auth: engine.status" "status=auth-failed exit=10" "$(cat "$d/engine.status" 2>/dev/null)"
assert_file_absent "claude auth: no contract.json.tmp" "$d/contract.json.tmp"

# --- failure classification: transient (429) ---
d="$(new_out_dir)"
run_claude "$d" report_only none none none "" "" 1 "" "$CLAUDE_TRANSIENT_JSON"
rc=$?
assert_eq "claude transient: adapter exit 12" "12" "$rc"
assert_eq "claude transient: engine.status" "status=transient exit=12" "$(cat "$d/engine.status" 2>/dev/null)"

# --- failure classification: tool-denied (missing structured_output + denials) ---
d="$(new_out_dir)"
run_claude "$d" report_only none none none "" "" 1 "" "$CLAUDE_TOOLDENIED_JSON"
rc=$?
assert_eq "claude tool-denied: adapter exit 11" "11" "$rc"
assert_eq "claude tool-denied: engine.status" "status=tool-denied exit=11" "$(cat "$d/engine.status" 2>/dev/null)"

# --- failure classification: other/generic ---
d="$(new_out_dir)"
run_claude "$d" report_only none none none "" "" 1 "" "$CLAUDE_GENERIC_JSON"
rc=$?
assert_eq "claude other: adapter exit 1" "1" "$rc"
assert_eq "claude other: engine.status" "status=engine-failed exit=1" "$(cat "$d/engine.status" 2>/dev/null)"

# --- redaction ---
d="$(new_out_dir)"
run_claude "$d" report_only none none none "" "" 0 "token: sk-ZYXWVUTSRQ0123456789" "$CLAUDE_SUCCESS_JSON"
assert_not "claude redaction: raw secret absent from engine.log" grep -q "sk-ZYXWVUTSRQ0123456789" "$d/engine.log"
assert "claude redaction: redaction marker present in engine.log" grep -q "«redacted:" "$d/engine.log"

echo "== cross-cutting: forbidden flags / no adapter-side timeout =="

# Forbidden flags must never appear in any recorded argv from the runs above.
for argv_file in "$TMP_ROOT"/out.*/.argv; do
  [ -f "$argv_file" ] || continue
  for forbidden in "--resume" "resume" "--continue" "--dangerously-skip-permissions"; do
    assert_not "forbidden flag [$forbidden] absent from $argv_file" argv_has_token "$argv_file" "$forbidden"
  done
done

# Static source check: no invocation of `timeout`/`gtimeout` as a command
# (word-boundary match on non-comment lines only — comments legitimately
# discuss "timeout" as a concept per the env contract).
for src in "$CODEX_ADAPTER" "$CLAUDE_ADAPTER"; do
  if grep -v '^[[:space:]]*#' "$src" | grep -qE '(^|[^A-Za-z0-9_])(timeout|gtimeout)([^A-Za-z0-9_]|$)'; then
    fail "no adapter-side timeout: $src invokes timeout/gtimeout"
  else
    ok
  fi
done

echo
echo "passed: $PASSED, failed: $FAILED"
if [ "$FAILED" -gt 0 ]; then
  exit 1
fi
exit 0
