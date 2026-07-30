#!/usr/bin/env bash
# tests/test_runner_pages.sh — hermetic tests for the Amendment 2 render step
# (report pages): loop-data commit, render gate, promotion, retention.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./runner_test_helpers.sh
source "$HERE/runner_test_helpers.sh"

export LOOPS_ENGINE_OVERRIDE=fake
export LOOPS_ALLOW_FAKE_ENGINE=1

reset_fake_env() {
  unset FAKE_EXIT FAKE_SLEEP_S FAKE_INVALID FAKE_OMIT_TMP FAKE_CONTRACT_FILE LOOPS_RETRY_BACKOFF_S 2>/dev/null || true
}
reset_fake_env

# Every hermetic root needs page_envelope.py + redact.py (already copied by
# new_hermetic_root's bin/*.py glob) and a pagekit dir for $PAGEKIT.
seed_pagekit() { mkdir -p "$1/pagekit"; touch "$1/pagekit/kit.css"; }

# make_render <loop_dir> — writes render.sh from stdin, executable.
make_render() {
  local dir="$1"
  cat > "$dir/render.sh"
  chmod +x "$dir/render.sh"
}

# A renderer that emits a minimal VALID page for whatever RUN_ID/LOOP_NAME
# the runner hands it.
good_renderer_body() {
  cat <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
python3 - "$PAGE_OUT" "$LOOP_NAME" "$RUN_ID" <<'PY'
import json, sys, datetime
out, loop, run_id = sys.argv[1:4]
meta = {"loop": loop, "run_id": run_id,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "title": "Test page", "page_class": "snapshot", "totals": {"findings": 1}}
envelope = json.dumps({"meta": meta, "data": {}}).replace("</", "<\\/")
open(out, "w").write("<!DOCTYPE html><html><body>"
    f'<script type="application/json" id="report-data">{envelope}</script>'
    "</body></html>")
PY
EOF
}

test_successful_render_promotes_dated_and_latest() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"; seed_pagekit "$root"
  local dir; dir="$(make_loop "$root" pageloop agent)"
  local fixture="$root/fixture-contract.json"
  write_contract_fixture "$fixture" ok '[]'
  good_renderer_body | make_render "$dir"
  export FAKE_CONTRACT_FILE="$fixture"
  run_runner "$root" pageloop
  unset FAKE_CONTRACT_FILE
  assert_eq "runner exit 0" 0 "$RUNNER_EXIT"
  assert_file_exists "latest.html promoted" "$root/reports/pageloop/latest.html"
  local dated
  dated="$(ls "$root/reports/pageloop/" 2>/dev/null | grep -c '^[0-9-]*[0-9]\.html$')"
  assert_eq "one dated page" 1 "$dated"
  assert_contains "stdout announces page" "$RUNNER_STDOUT" "page promoted: reports/pageloop/"
  cmp -s "$root/reports/pageloop/latest.html" \
    "$root/reports/pageloop/$(ls "$root/reports/pageloop/" | grep '^[0-9-]*[0-9]\.html$')" \
    && tr_ok || tr_fail "dated and latest byte-identical"
  rm -rf "$root"
}

test_failing_renderer_leaves_latest_untouched_and_run_completed() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"; seed_pagekit "$root"
  local dir; dir="$(make_loop "$root" pageloop agent "retention_days=1")"
  local fixture="$root/fixture-contract.json"
  write_contract_fixture "$fixture" ok '[]'
  mkdir -p "$root/reports/pageloop"
  printf 'PREVIOUS' > "$root/reports/pageloop/latest.html"
  printf 'old' > "$root/reports/pageloop/2020-01-01-0000.html"
  touch -t 202001010000 "$root/reports/pageloop/latest.html" \
    "$root/reports/pageloop/2020-01-01-0000.html"
  printf '#!/usr/bin/env bash\nexit 3\n' | make_render "$dir"
  export FAKE_CONTRACT_FILE="$fixture"
  run_runner "$root" pageloop
  unset FAKE_CONTRACT_FILE
  assert_eq "runner exit 0" 0 "$RUNNER_EXIT"
  assert_eq "latest.html untouched" "PREVIOUS" "$(cat "$root/reports/pageloop/latest.html")"
  assert_file_missing "old dated page pruned" "$root/reports/pageloop/2020-01-01-0000.html"
  local status
  status="$(sqlite3 "$root/state/loops.sqlite" \
    "SELECT runner_status FROM runs ORDER BY started_at DESC LIMIT 1")"
  assert_eq "run still completed" completed "$status"
  local run_dir; run_dir="$(ls -d "$root/state/runs/"*pageloop* | head -n1)"
  assert_file_exists "render log written" "$run_dir/page-render.log"
  rm -rf "$root"
}

test_gate_rejects_wrong_run_id() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"; seed_pagekit "$root"
  local dir; dir="$(make_loop "$root" pageloop agent)"
  local fixture="$root/fixture-contract.json"
  write_contract_fixture "$fixture" ok '[]'
  cat <<'EOF' | make_render "$dir"
#!/usr/bin/env bash
python3 - "$PAGE_OUT" "$LOOP_NAME" <<'PY'
import json, sys
out, loop = sys.argv[1:3]
meta = {"loop": loop, "run_id": "WRONG", "generated_at": "2026-07-30T00:00:00Z",
        "title": "t", "page_class": "snapshot"}
env = json.dumps({"meta": meta, "data": {}}).replace("</", "<\\/")
open(out, "w").write(f'<script type="application/json" id="report-data">{env}</script>')
PY
EOF
  export FAKE_CONTRACT_FILE="$fixture"
  run_runner "$root" pageloop
  unset FAKE_CONTRACT_FILE
  assert_file_missing "no promotion on run_id mismatch" "$root/reports/pageloop/latest.html"
  local dated
  dated="$(ls "$root/reports/pageloop/" 2>/dev/null | grep -c '^[0-9-]*[0-9]\.html$')"
  assert_eq "no dated page on run_id mismatch" 0 "$dated"
  rm -rf "$root"
}

test_gate_rejects_secret_shaped_content() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"; seed_pagekit "$root"
  local dir; dir="$(make_loop "$root" pageloop agent)"
  local fixture="$root/fixture-contract.json"
  write_contract_fixture "$fixture" ok '[]'
  cat <<'EOF' | make_render "$dir"
#!/usr/bin/env bash
python3 - "$PAGE_OUT" "$LOOP_NAME" "$RUN_ID" <<'PY'
import json, sys, datetime
out, loop, run_id = sys.argv[1:4]
meta = {"loop": loop, "run_id": run_id,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "title": "t", "page_class": "snapshot"}
env = json.dumps({"meta": meta, "data": {}}).replace("</", "<\\/")
open(out, "w").write("<html><body><pre>ghp_" + "a"*24 + "</pre>"
    f'<script type="application/json" id="report-data">{env}</script></body></html>')
PY
EOF
  export FAKE_CONTRACT_FILE="$fixture"
  run_runner "$root" pageloop
  unset FAKE_CONTRACT_FILE
  assert_file_missing "no promotion of secret-shaped page" "$root/reports/pageloop/latest.html"
  local run_dir; run_dir="$(ls -d "$root/state/runs/"*pageloop* | head -n1)"
  assert_contains "reason logged" "$(cat "$run_dir/page-render.log")" "redaction"
  rm -rf "$root"
}

test_loop_data_commit_only_on_promotion() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"; seed_pagekit "$root"
  local dir; dir="$(make_loop "$root" pageloop agent)"
  make_precheck "$dir" <<'EOF'
#!/usr/bin/env bash
mkdir -p "$OUT_DIR/loop-data.commit"
printf 'baseline-v2' > "$OUT_DIR/loop-data.commit/baseline.txt"
echo "digest line"
EOF
  # Case A: contract invalid -> no commit.
  local fixture="$root/fixture-contract.json"
  write_contract_fixture "$fixture" ok '[]'
  export FAKE_INVALID=1
  export FAKE_CONTRACT_FILE="$fixture"
  run_runner "$root" pageloop
  unset FAKE_INVALID FAKE_CONTRACT_FILE
  assert_file_missing "no commit on contract violation" \
    "$root/state/loop-data/pageloop/baseline.txt"
  # Case B: valid run -> committed.
  export FAKE_CONTRACT_FILE="$fixture"
  run_runner "$root" pageloop
  unset FAKE_CONTRACT_FILE
  assert_eq "baseline committed" "baseline-v2" \
    "$(cat "$root/state/loop-data/pageloop/baseline.txt")"
  rm -rf "$root"
}

test_loop_data_commit_sets_restrictive_modes() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"; seed_pagekit "$root"
  local dir; dir="$(make_loop "$root" pageloop agent)"
  make_precheck "$dir" <<'EOF'
#!/usr/bin/env bash
mkdir -p "$OUT_DIR/loop-data.commit"
printf 'baseline-v2' > "$OUT_DIR/loop-data.commit/baseline.txt"
echo "digest line"
EOF
  local fixture="$root/fixture-contract.json"
  write_contract_fixture "$fixture" ok '[]'
  export FAKE_CONTRACT_FILE="$fixture"
  run_runner "$root" pageloop
  unset FAKE_CONTRACT_FILE
  local mode_parent mode_loop_dir mode_file
  mode_parent="$(python3 -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777)[2:])' "$root/state/loop-data")"
  mode_loop_dir="$(python3 -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777)[2:])' "$root/state/loop-data/pageloop")"
  mode_file="$(python3 -c 'import os,sys; print(oct(os.stat(sys.argv[1]).st_mode & 0o777)[2:])' "$root/state/loop-data/pageloop/baseline.txt")"
  assert_eq "loop-data parent dir is 0700" 700 "$mode_parent"
  assert_eq "loop-data loop dir is 0700" 700 "$mode_loop_dir"
  assert_eq "committed loop-data file is 0600" 600 "$mode_file"
  rm -rf "$root"
}

test_retention_keeps_latest_html() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"; seed_pagekit "$root"
  local dir; dir="$(make_loop "$root" pageloop agent "retention_days=1")"
  local fixture="$root/fixture-contract.json"
  write_contract_fixture "$fixture" ok '[]'
  good_renderer_body | make_render "$dir"
  mkdir -p "$root/reports/pageloop"
  printf 'old' > "$root/reports/pageloop/2020-01-01-0000.html"
  printf 'keep' > "$root/reports/pageloop/latest.html"
  touch -t 202001010000 "$root/reports/pageloop/2020-01-01-0000.html" \
    "$root/reports/pageloop/latest.html"
  export FAKE_CONTRACT_FILE="$fixture"
  run_runner "$root" pageloop
  unset FAKE_CONTRACT_FILE
  assert_file_missing "old dated page pruned" "$root/reports/pageloop/2020-01-01-0000.html"
  assert_file_exists "latest.html survives retention" "$root/reports/pageloop/latest.html"
  rm -rf "$root"
}

test_no_render_sh_means_no_pages() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"; seed_pagekit "$root"
  make_loop "$root" plainloop agent >/dev/null
  local fixture="$root/fixture-contract.json"
  write_contract_fixture "$fixture" ok '[]'
  export FAKE_CONTRACT_FILE="$fixture"
  run_runner "$root" plainloop
  unset FAKE_CONTRACT_FILE
  assert_eq "runner exit 0" 0 "$RUNNER_EXIT"
  assert_file_missing "no page for plain loop" "$root/reports/plainloop/latest.html"
  rm -rf "$root"
}

test_successful_render_promotes_dated_and_latest
test_failing_renderer_leaves_latest_untouched_and_run_completed
test_gate_rejects_wrong_run_id
test_gate_rejects_secret_shaped_content
test_loop_data_commit_only_on_promotion
test_loop_data_commit_sets_restrictive_modes
test_retention_keeps_latest_html
test_no_render_sh_means_no_pages

echo "test_runner_pages: passed=$TR_PASSED failed=$TR_FAILED"
[ "$TR_FAILED" -eq 0 ]
