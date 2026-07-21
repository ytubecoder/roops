#!/usr/bin/env bash
# tests/test_runner_lib.sh — shared hermetic-root + assertion helpers for
# tests/test_runner*.sh (§11). Sourced, not executed directly.
#
# Every test gets its own mktemp -d "LOOPS_ROOT" via new_hermetic_root,
# seeded with copies of bin/*.py, contract/contract.schema.json, and
# engines/fake.sh from the real repo (never the real state/, reports/,
# launchd, or network). bin/run-loop.sh itself is invoked from the real
# repo path (it only ever touches paths under $LOOPS_ROOT, resolved at
# runtime from the LOOPS_ROOT env var), pointed at the hermetic root.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNNER="$REPO_ROOT/bin/run-loop.sh"

TR_FAILED=0
TR_PASSED=0

tr_fail() {
  echo "FAIL: $1"
  TR_FAILED=$((TR_FAILED + 1))
}
tr_ok() {
  TR_PASSED=$((TR_PASSED + 1))
}

assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then tr_ok; else tr_fail "$desc (expected [$expected] got [$actual])"; fi
}
assert_ne() {
  local desc="$1" not_expected="$2" actual="$3"
  if [ "$not_expected" != "$actual" ]; then tr_ok; else tr_fail "$desc (expected NOT [$not_expected], got it)"; fi
}
assert_file_exists() {
  local desc="$1" path="$2"
  if [ -e "$path" ]; then tr_ok; else tr_fail "$desc (missing: $path)"; fi
}
assert_file_missing() {
  local desc="$1" path="$2"
  if [ ! -e "$path" ]; then tr_ok; else tr_fail "$desc (should not exist: $path)"; fi
}
assert_contains() {
  local desc="$1" haystack="$2" needle="$3"
  case "$haystack" in
    *"$needle"*) tr_ok ;;
    *) tr_fail "$desc (expected to contain [$needle])" ;;
  esac
}
assert_not_contains() {
  local desc="$1" haystack="$2" needle="$3"
  case "$haystack" in
    *"$needle"*) tr_fail "$desc (did NOT expect to contain [$needle])" ;;
    *) tr_ok ;;
  esac
}

# new_hermetic_root — creates a fresh $LOOPS_ROOT-shaped temp tree with
# copies of bin/, contract/, engines/fake.sh (§11). Prints the path.
new_hermetic_root() {
  local root
  root="$(mktemp -d "${TMPDIR:-/tmp}/loops-runner-test.XXXXXX")"
  mkdir -p "$root/bin" "$root/contract" "$root/engines" "$root/loops.d" "$root/examples" \
    "$root/state" "$root/reports" "$root/dashboard"
  cp "$REPO_ROOT"/bin/*.py "$root/bin/"
  cp "$REPO_ROOT/contract/contract.schema.json" "$root/contract/contract.schema.json"
  cp "$REPO_ROOT/engines/fake.sh" "$root/engines/fake.sh"
  chmod +x "$root/engines/fake.sh"
  if [ -f "$REPO_ROOT/dashboard/generate.py" ]; then
    cp "$REPO_ROOT/dashboard/generate.py" "$root/dashboard/generate.py"
  fi
  printf '%s' "$root"
}

# make_loop <root> <name> <type> [extra KEY=VALUE conf lines...]
# Writes a minimal valid loop.conf + prompt.md under loops.d/<name>/.
make_loop() {
  local root="$1" name="$2" type="$3"; shift 3
  local dir="$root/loops.d/$name"
  mkdir -p "$dir"
  {
    echo "name=$name"
    echo "description=\"test loop $name\""
    echo "type=$type"
    echo "engine=codex"
    echo "schedule=manual"
    echo "timeout_s=30"
    for line in "$@"; do
      echo "$line"
    done
  } > "$dir/loop.conf"
  cat > "$dir/prompt.md" <<'EOF'
# Test loop prompt

## Finding identity
`<subject>:<condition>` derives from the fixture's fixed test subject —
stable across runs on purpose (recurrence fixture).
EOF
  printf '%s' "$dir"
}

# make_precheck <loop_dir> — writes precheck.sh from stdin, executable.
make_precheck() {
  local dir="$1"
  cat > "$dir/precheck.sh"
  chmod +x "$dir/precheck.sh"
}

# write_contract_fixture <path> <status> <findings_json_array>
# findings_json_array e.g. '[]' or a JSON array literal. run_id is left as
# the literal placeholder __RUN_ID__, substituted by engines/fake.sh.
write_contract_fixture() {
  local path="$1" status="$2" findings="$3"
  python3 - "$path" "$status" "$findings" <<'PY'
import json, sys
path, status, findings_raw = sys.argv[1:4]
findings = json.loads(findings_raw)
obj = {
    "schema_version": 1,
    "run_id": "__RUN_ID__",
    "status": status,
    "status_reason": "fixture",
    "headline": f"fixture status={status}",
    "report_markdown": f"# Fixture report\n\nstatus={status}\n",
    "metrics": "{}",
    "findings": findings,
}
with open(path, "w") as f:
    json.dump(obj, f)
PY
}

# run_runner <root> <name> [extra run-loop.sh args...]
# Sets RUNNER_STDOUT, RUNNER_STDERR, RUNNER_EXIT.
run_runner() {
  local root="$1" name="$2"; shift 2
  local out err ec
  out="$(mktemp "${TMPDIR:-/tmp}/loops-runner-stdout.XXXXXX")"
  err="$(mktemp "${TMPDIR:-/tmp}/loops-runner-stderr.XXXXXX")"
  ec=0
  LOOPS_ROOT="$root" "$RUNNER" "$name" "$@" > "$out" 2> "$err" || ec=$?
  RUNNER_STDOUT="$(cat "$out")"
  RUNNER_STDERR="$(cat "$err")"
  RUNNER_EXIT="$ec"
  rm -f "$out" "$err"
}

# last_run_field <root> <name> <field>
last_run_field() {
  local root="$1" name="$2" field="$3"
  python3 "$root/bin/db.py" query last-runs --root "$root" --loop "$name" --limit 1 | \
    python3 -c "
import json,sys
rows=json.load(sys.stdin)
print('' if not rows or rows[0].get(sys.argv[1]) is None else rows[0].get(sys.argv[1]))
" "$field"
}

run_count() {
  local root="$1" name="$2"
  python3 "$root/bin/db.py" query last-runs --root "$root" --loop "$name" --limit 1000 | \
    python3 -c "import json,sys; print(len(json.load(sys.stdin)))"
}

# db_exec <root> <sql> — raw sqlite3 query against the hermetic db, one row
# per line, pipe-separated columns.
db_exec() {
  local root="$1" sql="$2"
  sqlite3 -separator '|' "$root/state/loops.sqlite" "$sql"
}
