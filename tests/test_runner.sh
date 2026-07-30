#!/usr/bin/env bash
# tests/test_runner.sh — hermetic tests for bin/run-loop.sh (§4, §11).
#
# Every test gets its own throwaway LOOPS_ROOT (tests/runner_test_helpers.sh's
# new_hermetic_root) seeded with copies of bin/*.py, contract/, and
# engines/fake.sh — never the real repo's state/, reports/, or launchd, and
# never a real engine CLI or the network (§11). engines/fake.sh is only
# ever selected because these tests explicitly set BOTH
# LOOPS_ENGINE_OVERRIDE=fake and LOOPS_ALLOW_FAKE_ENGINE=1 — the double-gate
# documented in bin/run-loop.sh / engines/fake.sh.
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

# assert_before <desc> <haystack> <first> <second> — asserts <first> occurs
# earlier in <haystack> than <second> (both must be present; relies on bash
# %% suffix-removal matching the earliest occurrence to measure position).
assert_before() {
  local desc="$1" haystack="$2" first="$3" second="$4"
  local before_first="${haystack%%"$first"*}"
  local before_second="${haystack%%"$second"*}"
  if [ "$before_first" = "$haystack" ] || [ "$before_second" = "$haystack" ]; then
    tr_fail "$desc (one of the markers is missing)"
  elif [ "${#before_first}" -lt "${#before_second}" ]; then
    tr_ok
  else
    tr_fail "$desc (expected [$first] before [$second])"
  fi
}

# break_db_finish_run <root> — replaces the hermetic root's copy of db.py
# with a shim that forwards every verb to the real implementation EXCEPT
# finish-run, which fails outright. Used to deterministically exercise
# bin/run-loop.sh's harness-error trap without touching the real repo's
# bin/db.py (this only ever mutates a per-test throwaway copy).
break_db_finish_run() {
  local root="$1"
  mv "$root/bin/db.py" "$root/bin/db_real.py"
  cat > "$root/bin/db.py" <<'PY'
#!/usr/bin/env python3
import os, subprocess, sys
real = os.path.join(os.path.dirname(os.path.abspath(__file__)), "db_real.py")
if len(sys.argv) > 1 and sys.argv[1] == "finish-run":
    sys.stderr.write("SIMULATED HARNESS FAILURE: db.py finish-run broken (test fixture)\n")
    sys.exit(77)
sys.exit(subprocess.call([sys.executable, real] + sys.argv[1:]))
PY
  chmod +x "$root/bin/db.py"
}

# ===========================================================================
# completed: ok / warn / alert
# ===========================================================================

test_completed_ok() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopok agent >/dev/null
  run_runner "$root" loopok
  assert_eq "completed-ok: exit code" "0" "$RUNNER_EXIT"
  assert_eq "completed-ok: runner_status" "completed" "$(last_run_field "$root" loopok runner_status)"
  assert_eq "completed-ok: loop_status" "ok" "$(last_run_field "$root" loopok loop_status)"
  assert_eq "completed-ok: effective_status" "ok" "$(last_run_field "$root" loopok effective_status)"
  assert_file_exists "completed-ok: latest.json promoted" "$root/reports/loopok/latest.json"
  assert_file_exists "completed-ok: latest.md promoted" "$root/reports/loopok/latest.md"
  local run_id; run_id="$(last_run_field "$root" loopok run_id)"
  assert_file_exists "completed-ok: contract.json audit copy" "$root/state/runs/$run_id/contract.json"
  local engine_log; engine_log="$(cat "$root/state/runs/$run_id/engine.log" 2>/dev/null || true)"
  assert_contains "completed-ok: adapter saw LOOP_TYPE=agent" "$engine_log" "loop_type=agent"
  assert_contains "completed-ok: adapter saw timeout_s=30" "$engine_log" "timeout_s=30"
  rm -rf "$root"
}

test_completed_warn() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopwarn agent >/dev/null
  local fixture="$root/fixture-warn.json"
  write_contract_fixture "$fixture" warn '[{"finding_id":"subj:warncond","title":"t","severity":"warn","detail":"d"}]'
  export FAKE_CONTRACT_FILE="$fixture"
  run_runner "$root" loopwarn
  unset FAKE_CONTRACT_FILE
  assert_eq "completed-warn: exit code" "0" "$RUNNER_EXIT"
  assert_eq "completed-warn: loop_status" "warn" "$(last_run_field "$root" loopwarn loop_status)"
  assert_eq "completed-warn: effective_status" "warn" "$(last_run_field "$root" loopwarn effective_status)"
  rm -rf "$root"
}

test_completed_alert() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopalert agent >/dev/null
  local fixture="$root/fixture-alert.json"
  write_contract_fixture "$fixture" alert '[{"finding_id":"subj:alertcond","title":"t","severity":"alert","detail":"d"}]'
  export FAKE_CONTRACT_FILE="$fixture"
  run_runner "$root" loopalert
  unset FAKE_CONTRACT_FILE
  assert_eq "completed-alert: exit code" "0" "$RUNNER_EXIT"
  assert_eq "completed-alert: loop_status" "alert" "$(last_run_field "$root" loopalert loop_status)"
  assert_eq "completed-alert: effective_status" "alert" "$(last_run_field "$root" loopalert effective_status)"
  rm -rf "$root"
}

# ===========================================================================
# skipped-overlap
# ===========================================================================

test_skipped_overlap() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" looplk agent >/dev/null

  mkdir -p "$root/state/locks"
  local fifo="$root/state/locks/.testhold-$$"
  rm -f "$fifo"
  mkfifo "$fifo"
  local outfile; outfile="$(mktemp "${TMPDIR:-/tmp}/loops-testlock-out.XXXXXX")"
  python3 "$root/bin/lock.py" acquire --name looplk --root "$root" < "$fifo" > "$outfile" 2>/dev/null &
  local lockpid=$!
  exec 7> "$fifo"
  local waited=0
  while [ ! -s "$outfile" ] && kill -0 "$lockpid" 2>/dev/null && [ "$waited" -lt 50 ]; do
    sleep 0.05
    waited=$((waited + 1))
  done

  run_runner "$root" looplk

  exec 7>&-
  wait "$lockpid" 2>/dev/null || true
  rm -f "$fifo" "$outfile"

  assert_eq "skipped-overlap: exit code" "0" "$RUNNER_EXIT"
  assert_eq "skipped-overlap: runner_status" "skipped-overlap" "$(last_run_field "$root" looplk runner_status)"
  assert_eq "skipped-overlap: run row count" "1" "$(run_count "$root" looplk)"
  rm -rf "$root"
}

# ===========================================================================
# precheck: agent skipped-precheck (amber) / precheck-failed
# ===========================================================================

test_skipped_precheck_amber() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  local dir; dir="$(make_loop "$root" loopsp agent)"
  make_precheck "$dir" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  run_runner "$root" loopsp
  assert_eq "skipped-precheck: exit code" "0" "$RUNNER_EXIT"
  assert_eq "skipped-precheck: runner_status" "skipped-precheck" "$(last_run_field "$root" loopsp runner_status)"
  assert_eq "skipped-precheck: loop_status" "ok" "$(last_run_field "$root" loopsp loop_status)"
  assert_eq "skipped-precheck: heartbeat count" "1" "$(db_exec "$root" "SELECT COUNT(*) FROM heartbeats WHERE loop_name='loopsp'")"
  assert_eq "skipped-precheck: heartbeat ok" "1" "$(db_exec "$root" "SELECT ok FROM heartbeats WHERE loop_name='loopsp'")"
  local run_id; run_id="$(last_run_field "$root" loopsp run_id)"
  assert_file_missing "skipped-precheck: no engine invoked (no engine.log)" "$root/state/runs/$run_id/engine.log"
  rm -rf "$root"
}

test_precheck_failed() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  local dir; dir="$(make_loop "$root" looppf agent)"
  make_precheck "$dir" <<'EOF'
#!/usr/bin/env bash
echo "precheck blew up" >&2
exit 5
EOF
  run_runner "$root" looppf
  assert_eq "precheck-failed: exit code" "0" "$RUNNER_EXIT"
  assert_eq "precheck-failed: runner_status" "precheck-failed" "$(last_run_field "$root" looppf runner_status)"
  assert_eq "precheck-failed: loop_status" "alert" "$(last_run_field "$root" looppf loop_status)"
  assert_eq "precheck-failed: effective_status" "alert" "$(last_run_field "$root" looppf effective_status)"
  local run_id; run_id="$(last_run_field "$root" looppf run_id)"
  assert_file_missing "precheck-failed: no engine invoked" "$root/state/runs/$run_id/engine.log"
  rm -rf "$root"
}

# ===========================================================================
# watchdog: silent-green / escalation with sticky alert
# ===========================================================================

test_watchdog_silent_green() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  local dir; dir="$(make_loop "$root" loopwd watchdog)"
  make_precheck "$dir" <<'EOF'
#!/usr/bin/env bash
echo "probe ok — all systems nominal"
exit 0
EOF
  run_runner "$root" loopwd
  assert_eq "watchdog-silent-green: exit code" "0" "$RUNNER_EXIT"
  assert_eq "watchdog-silent-green: runner_status" "completed" "$(last_run_field "$root" loopwd runner_status)"
  assert_eq "watchdog-silent-green: loop_status" "ok" "$(last_run_field "$root" loopwd loop_status)"
  assert_eq "watchdog-silent-green: effective_status" "ok" "$(last_run_field "$root" loopwd effective_status)"
  assert_eq "watchdog-silent-green: headline" "probe ok — all systems nominal" "$(last_run_field "$root" loopwd headline)"
  assert_eq "watchdog-silent-green: heartbeat ok" "1" "$(db_exec "$root" "SELECT ok FROM heartbeats WHERE loop_name='loopwd'")"
  local run_id; run_id="$(last_run_field "$root" loopwd run_id)"
  assert_file_missing "watchdog-silent-green: no engine invoked" "$root/state/runs/$run_id/engine.log"
  rm -rf "$root"
}

test_watchdog_escalation_sticky() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  local dir; dir="$(make_loop "$root" loopwde watchdog)"
  make_precheck "$dir" <<'EOF'
#!/usr/bin/env bash
echo "probe FAILED: disk at 99%"
exit 1
EOF
  # Diagnosis engine also fails — stickiness must hold even so.
  export FAKE_EXIT=1
  run_runner "$root" loopwde
  unset FAKE_EXIT
  assert_eq "watchdog-escalation: exit code" "0" "$RUNNER_EXIT"
  assert_eq "watchdog-escalation: runner_status reflects diagnosis failure" "engine-failed" "$(last_run_field "$root" loopwde runner_status)"
  assert_eq "watchdog-escalation: loop_status sticky alert" "alert" "$(last_run_field "$root" loopwde loop_status)"
  assert_eq "watchdog-escalation: effective_status sticky alert" "alert" "$(last_run_field "$root" loopwde effective_status)"
  assert_eq "watchdog-escalation: heartbeat ok=0" "0" "$(db_exec "$root" "SELECT ok FROM heartbeats WHERE loop_name='loopwde'")"
  local run_id; run_id="$(last_run_field "$root" loopwde run_id)"
  assert_file_exists "watchdog-escalation: engine WAS invoked" "$root/state/runs/$run_id/engine.log"
  rm -rf "$root"
}

# ===========================================================================
# engine-timeout leaves previous latest.* untouched (stale-green impossibility)
# ===========================================================================

test_engine_timeout_stale_green() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopto agent >/dev/null   # timeout_s=30 (loopconf minimum)

  # Run 1: a valid, fast, completed run — this is the "good" latest.* we
  # must never lose.
  run_runner "$root" loopto
  assert_eq "engine-timeout: setup run 1 completed" "completed" "$(last_run_field "$root" loopto runner_status)"
  local before_json before_md
  before_json="$(cat "$root/reports/loopto/latest.json")"
  before_md="$(cat "$root/reports/loopto/latest.md")"

  # Run 2: sleeps past timeout_s -> killed -> engine-timeout. Must NOT
  # promote anything.
  export FAKE_SLEEP_S=35
  run_runner "$root" loopto
  unset FAKE_SLEEP_S
  assert_eq "engine-timeout: exit code" "0" "$RUNNER_EXIT"
  assert_eq "engine-timeout: runner_status" "engine-timeout" "$(last_run_field "$root" loopto runner_status)"

  local after_json after_md
  after_json="$(cat "$root/reports/loopto/latest.json")"
  after_md="$(cat "$root/reports/loopto/latest.md")"
  assert_eq "engine-timeout: latest.json unchanged (stale-green impossibility)" "$before_json" "$after_json"
  assert_eq "engine-timeout: latest.md unchanged" "$before_md" "$after_md"
  rm -rf "$root"
}

# ===========================================================================
# contract-violation: FAKE_INVALID (no promotion) / run_id mismatch
# ===========================================================================

test_contract_violation_invalid() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopcv agent >/dev/null
  export FAKE_INVALID=1
  run_runner "$root" loopcv
  unset FAKE_INVALID
  assert_eq "contract-violation: exit code" "0" "$RUNNER_EXIT"
  assert_eq "contract-violation: runner_status" "contract-violation" "$(last_run_field "$root" loopcv runner_status)"
  # §4.1 step 6: invalid contract -> loop_status=alert, effective_status=alert
  # (not NULL/omitted -- distinct from engine-failed/auth-failed/etc.).
  assert_eq "contract-violation: loop_status" "alert" "$(last_run_field "$root" loopcv loop_status)"
  assert_eq "contract-violation: effective_status" "alert" "$(last_run_field "$root" loopcv effective_status)"
  assert_file_missing "contract-violation: no latest.json promoted" "$root/reports/loopcv/latest.json"
  rm -rf "$root"
}

test_contract_violation_run_id_mismatch() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopmm agent >/dev/null
  local fixture="$root/fixture-mismatch.json"
  python3 - "$fixture" <<'PY'
import json, sys
obj = {
    "schema_version": 1, "run_id": "totally-wrong-run-id", "status": "ok",
    "status_reason": "x", "headline": "x", "report_markdown": "x",
    "metrics": "{}", "findings": [],
}
with open(sys.argv[1], "w") as f:
    json.dump(obj, f)
PY
  export FAKE_CONTRACT_FILE="$fixture"
  run_runner "$root" loopmm
  unset FAKE_CONTRACT_FILE
  assert_eq "run_id-mismatch: exit code" "0" "$RUNNER_EXIT"
  assert_eq "run_id-mismatch: runner_status" "contract-violation" "$(last_run_field "$root" loopmm runner_status)"
  assert_eq "run_id-mismatch: loop_status" "alert" "$(last_run_field "$root" loopmm loop_status)"
  assert_eq "run_id-mismatch: effective_status" "alert" "$(last_run_field "$root" loopmm effective_status)"
  assert_contains "run_id-mismatch: error_detail mentions run_id" "$(last_run_field "$root" loopmm error_detail)" "run_id"
  assert_file_missing "run_id-mismatch: no promotion" "$root/reports/loopmm/latest.json"
  rm -rf "$root"
}

# ===========================================================================
# transient retry (exit 12) vs never-retried (10 / 11)
# ===========================================================================

test_transient_retry_exhausted() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" looptr agent "retry_transient=2" >/dev/null
  export FAKE_EXIT=12
  export LOOPS_RETRY_BACKOFF_S="0 0"
  run_runner "$root" looptr
  unset FAKE_EXIT LOOPS_RETRY_BACKOFF_S
  assert_eq "transient-retry: exit code" "0" "$RUNNER_EXIT"
  assert_eq "transient-retry: runner_status" "engine-failed" "$(last_run_field "$root" looptr runner_status)"
  assert_eq "transient-retry: attempts == retry_transient+1" "3" "$(last_run_field "$root" looptr attempts)"
  assert_contains "transient-retry: error_detail mentions transient" "$(last_run_field "$root" looptr error_detail)" "transient"
  rm -rf "$root"
}

test_exit10_not_retried() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopa10 agent "retry_transient=2" >/dev/null
  export FAKE_EXIT=10
  run_runner "$root" loopa10
  unset FAKE_EXIT
  assert_eq "exit10: runner_status" "auth-failed" "$(last_run_field "$root" loopa10 runner_status)"
  assert_eq "exit10: attempts == 1 (never retried)" "1" "$(last_run_field "$root" loopa10 attempts)"
  rm -rf "$root"
}

test_exit11_not_retried() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopa11 agent "retry_transient=2" >/dev/null
  export FAKE_EXIT=11
  run_runner "$root" loopa11
  unset FAKE_EXIT
  assert_eq "exit11: runner_status" "tool-denied" "$(last_run_field "$root" loopa11 runner_status)"
  assert_eq "exit11: attempts == 1 (never retried)" "1" "$(last_run_field "$root" loopa11 attempts)"
  rm -rf "$root"
}

# ===========================================================================
# harness-error releases the lock
# ===========================================================================

test_harness_error_releases_lock() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loophe agent >/dev/null
  break_db_finish_run "$root"
  run_runner "$root" loophe
  assert_eq "harness-error: exit code" "1" "$RUNNER_EXIT"
  local check_ec=0
  python3 "$root/bin/lock.py" check --name loophe --root "$root" >/dev/null 2>&1 || check_ec=$?
  assert_eq "harness-error: lock released (check exits 0)" "0" "$check_ec"
  rm -rf "$root"
}

# ===========================================================================
# suppression: dismiss -> re-run -> promoted view omits it, audit trail keeps it
# ===========================================================================

test_suppression() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopsup agent >/dev/null
  local fixture="$root/fixture-sup.json"
  write_contract_fixture "$fixture" warn '[{"finding_id":"subj:supcond","title":"t","severity":"warn","detail":"d"}]'
  export FAKE_CONTRACT_FILE="$fixture"

  run_runner "$root" loopsup
  assert_eq "suppression: run1 effective_status" "warn" "$(last_run_field "$root" loopsup effective_status)"
  local latest1; latest1="$(cat "$root/reports/loopsup/latest.json")"
  assert_contains "suppression: run1 latest.json has finding" "$latest1" "subj:supcond"

  python3 "$root/bin/db.py" dispose --root "$root" --loop loopsup --finding-id "subj:supcond" \
    --action dismiss --note "test dismiss" >/dev/null

  run_runner "$root" loopsup
  unset FAKE_CONTRACT_FILE
  assert_eq "suppression: run2 exit" "0" "$RUNNER_EXIT"
  local latest2; latest2="$(cat "$root/reports/loopsup/latest.json")"
  assert_not_contains "suppression: run2 latest.json omits dismissed finding" "$latest2" "subj:supcond"
  assert_eq "suppression: run2 effective_status recomputed to ok" "ok" "$(last_run_field "$root" loopsup effective_status)"

  local run_id2; run_id2="$(last_run_field "$root" loopsup run_id)"
  local audit; audit="$(cat "$root/state/runs/$run_id2/contract.json")"
  assert_contains "suppression: audit contract.json still has the finding" "$audit" "subj:supcond"

  local latest_md; latest_md="$(cat "$root/reports/loopsup/latest.md")"
  assert_contains "suppression: latest.md has suppression footer" "$latest_md" "Suppressed by disposition"
  # §4.5 exact format: id + kind + date + note, not a bare id (db.py
  # suppressed now emits {finding_id, action, created_at, note,
  # snooze_until} objects, §3).
  local today; today="$(date -u +%Y-%m-%d)"
  assert_contains "suppression: latest.md footer has dismiss detail (id, date, note)" \
    "$latest_md" "subj:supcond (dismissed ${today} \"test dismiss\")"
  rm -rf "$root"
}

# ===========================================================================
# idempotence: two identical runs -> same finding_ids, times_seen=2, no dups
# ===========================================================================

test_idempotence() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopidem agent >/dev/null
  local fixture="$root/fixture-idem.json"
  write_contract_fixture "$fixture" warn '[{"finding_id":"subj:idemcond","title":"t","severity":"warn","detail":"d"},{"finding_id":"subj:idemcond2","title":"t2","severity":"info","detail":"d2"}]'
  export FAKE_CONTRACT_FILE="$fixture"

  run_runner "$root" loopidem
  run_runner "$root" loopidem
  unset FAKE_CONTRACT_FILE

  assert_eq "idempotence: row count for finding 1" "1" "$(db_exec "$root" "SELECT COUNT(*) FROM findings WHERE loop_name='loopidem' AND finding_id='subj:idemcond'")"
  assert_eq "idempotence: times_seen for finding 1 == 2" "2" "$(db_exec "$root" "SELECT times_seen FROM findings WHERE loop_name='loopidem' AND finding_id='subj:idemcond'")"
  assert_eq "idempotence: row count for finding 2" "1" "$(db_exec "$root" "SELECT COUNT(*) FROM findings WHERE loop_name='loopidem' AND finding_id='subj:idemcond2'")"
  assert_eq "idempotence: times_seen for finding 2 == 2" "2" "$(db_exec "$root" "SELECT times_seen FROM findings WHERE loop_name='loopidem' AND finding_id='subj:idemcond2'")"
  assert_eq "idempotence: total findings rows == 2 (no dups)" "2" "$(db_exec "$root" "SELECT COUNT(*) FROM findings WHERE loop_name='loopidem'")"
  rm -rf "$root"
}

# ===========================================================================
# retention pruning
# ===========================================================================

test_retention_pruning() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopret agent "retention_days=1" >/dev/null

  # Synthetic old artifacts, deliberately named so a same-minute real run
  # below can never collide with (and thus resurrect, via overwrite) them —
  # dated reports are named at minute granularity, so a real run's own
  # promoted file is not a safe thing to backdate-and-expect-pruned in a
  # fast test.
  mkdir -p "$root/reports/loopret" "$root/state/runs"
  local old_report_file="$root/reports/loopret/2020-01-01-0000.md"
  local old_run_dir="$root/state/runs/20200101T000000Z-loopret-000000"
  echo "ancient report" > "$old_report_file"
  mkdir -p "$old_run_dir"
  echo "ancient" > "$old_run_dir/contract.json"
  touch -t 202001010000 "$old_report_file" "$old_run_dir"
  assert_file_exists "retention: synthetic old report exists before run" "$old_report_file"
  assert_file_exists "retention: synthetic old run dir exists before run" "$old_run_dir"

  run_runner "$root" loopret

  assert_file_missing "retention: old run dir pruned" "$old_run_dir"
  assert_file_missing "retention: old dated report pruned" "$old_report_file"
  assert_file_exists "retention: latest.json survives pruning" "$root/reports/loopret/latest.json"
  assert_file_exists "retention: latest.md survives pruning" "$root/reports/loopret/latest.md"
  rm -rf "$root"
}

# Cross-loop isolation: state/runs/* is a single GLOBAL directory shared by
# every loop in the tree. Pruning loop A (short retention) must never delete
# loop B's old run dirs — including when B's name is a hyphenated
# superstring of A's name (e.g. "loopxa" and "loopxa-ext"), which would
# false-match a naive `-<name>-` substring/prefix check but must NOT match
# the full `<ts>-<name>-<6hex>` anchored shape.
test_retention_pruning_cross_loop_isolation() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopxa agent "retention_days=1" >/dev/null
  make_loop "$root" "loopxa-ext" agent "retention_days=3650" >/dev/null

  mkdir -p "$root/state/runs"
  local a_old_run_dir="$root/state/runs/20200101T000000Z-loopxa-000000"
  local b_old_run_dir="$root/state/runs/20200101T000000Z-loopxa-ext-000000"
  mkdir -p "$a_old_run_dir" "$b_old_run_dir"
  echo "ancient a" > "$a_old_run_dir/contract.json"
  echo "ancient b" > "$b_old_run_dir/contract.json"
  touch -t 202001010000 "$a_old_run_dir" "$b_old_run_dir"
  assert_file_exists "cross-loop retention: loop A old run dir exists before run" "$a_old_run_dir"
  assert_file_exists "cross-loop retention: loop B old run dir exists before run" "$b_old_run_dir"

  run_runner "$root" loopxa

  assert_file_missing "cross-loop retention: loop A's own old run dir pruned" "$a_old_run_dir"
  assert_file_exists "cross-loop retention: loop B's old run dir NOT pruned by A's run" "$b_old_run_dir"
  rm -rf "$root"
}

# ===========================================================================
# enabled=false refused except --trigger manual
# ===========================================================================

test_enabled_false_refused() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopdis agent "enabled=false" >/dev/null

  run_runner "$root" loopdis --trigger launchd
  assert_eq "disabled: launchd trigger exit 0" "0" "$RUNNER_EXIT"
  assert_eq "disabled: launchd trigger creates no run row" "0" "$(run_count "$root" loopdis)"

  run_runner "$root" loopdis --trigger manual
  assert_eq "disabled: manual trigger exit 0" "0" "$RUNNER_EXIT"
  assert_eq "disabled: manual trigger DOES run" "1" "$(run_count "$root" loopdis)"
  assert_eq "disabled: manual trigger completes" "completed" "$(last_run_field "$root" loopdis runner_status)"
  rm -rf "$root"
}

# ===========================================================================
# schedule=manual refused on a launchd-triggered firing, except --trigger
# manual (IMPORTANT #2b, fix wave 2026-07-30) -- same shape as the
# enabled=false guard above. `loopctl install` already refuses to bootstrap a
# schedule=manual loop, but a loop's live loop.conf can end up schedule=manual
# while an OLDER plist stays bootstrapped (e.g. an --overwrite that forces
# schedule=manual for an acknowledged-blocked skill without touching the
# plist) -- every launchd-triggered firing always arrives as --trigger
# launchd regardless of whether launchd fired it on schedule or via an
# explicit kickstart, so this guard is what actually stops it from running
# unattended.
# ===========================================================================

test_schedule_manual_refused_on_launchd_trigger() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopman agent >/dev/null  # make_loop's own default is schedule=manual

  run_runner "$root" loopman --trigger launchd
  assert_eq "schedule=manual: launchd trigger exit 0" "0" "$RUNNER_EXIT"
  assert_eq "schedule=manual: launchd trigger creates no run row" "0" "$(run_count "$root" loopman)"

  run_runner "$root" loopman --trigger manual
  assert_eq "schedule=manual: manual trigger exit 0" "0" "$RUNNER_EXIT"
  assert_eq "schedule=manual: manual trigger DOES run" "1" "$(run_count "$root" loopman)"
  assert_eq "schedule=manual: manual trigger completes" "completed" "$(last_run_field "$root" loopman runner_status)"
  rm -rf "$root"
}

test_schedule_manual_refused_on_kickstart_trigger() {
  # kickstart is the other non-manual --trigger value run-loop.sh's own arg
  # parsing accepts (usage comment: --trigger launchd|manual|kickstart) --
  # covered separately from launchd so the guard's `!= manual` condition
  # (not a narrower `== launchd` check) is what's actually pinned.
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopman2 agent >/dev/null

  run_runner "$root" loopman2 --trigger kickstart
  assert_eq "schedule=manual: kickstart trigger exit 0" "0" "$RUNNER_EXIT"
  assert_eq "schedule=manual: kickstart trigger creates no run row" "0" "$(run_count "$root" loopman2)"
  rm -rf "$root"
}

test_non_manual_schedule_still_runs_on_launchd_trigger() {
  # The guard must be specific to schedule=manual -- a loop with a real
  # interval schedule must still run normally on --trigger launchd (the
  # enabled=false guard's own sibling test already covers this shape for
  # enabled; this pins it for the new schedule guard so it can't have been
  # written as an unconditional "launchd never runs" refusal).
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopint agent "schedule=interval:15m" >/dev/null

  run_runner "$root" loopint --trigger launchd
  assert_eq "schedule=interval: launchd trigger exit 0" "0" "$RUNNER_EXIT"
  assert_eq "schedule=interval: launchd trigger DOES run" "1" "$(run_count "$root" loopint)"
  assert_eq "schedule=interval: launchd trigger completes" "completed" "$(last_run_field "$root" loopint runner_status)"
  rm -rf "$root"
}

# ===========================================================================
# --dry-run: prompt to stdout, no lock/db/engine touched
# ===========================================================================

test_dry_run() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopdry agent >/dev/null
  run_runner "$root" loopdry --dry-run
  assert_eq "dry-run: exit code" "0" "$RUNNER_EXIT"
  assert_contains "dry-run: stdout has prompt.md content" "$RUNNER_STDOUT" "Test loop prompt"
  assert_contains "dry-run: stdout has RUN CONTEXT block" "$RUNNER_STDOUT" "## RUN CONTEXT"
  assert_contains "dry-run: RUN CONTEXT shows dry-run placeholder run id" "$RUNNER_STDOUT" "run_id: <not assigned — dry run>"
  assert_before "dry-run: RUN CONTEXT after prompt.md body" "$RUNNER_STDOUT" "Test loop prompt" "## RUN CONTEXT"
  assert_file_missing "dry-run: no sqlite db created" "$root/state/loops.sqlite"
  assert_file_missing "dry-run: no run dirs created" "$root/state/runs"
  rm -rf "$root"
}

# ===========================================================================
# prompt composition: PRIOR FINDINGS + PRECHECK OUTPUT blocks (§6.2)
# ===========================================================================

test_prompt_composition() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  local dir; dir="$(make_loop "$root" looppc agent)"
  make_precheck "$dir" <<'EOF'
#!/usr/bin/env bash
echo "precheck says: 3 things found"
exit 0
EOF
  local fixture="$root/fixture-pc.json"
  write_contract_fixture "$fixture" warn '[{"finding_id":"subj:pccond","title":"t","severity":"warn","detail":"d"}]'
  export FAKE_CONTRACT_FILE="$fixture"
  run_runner "$root" looppc
  local run_id1; run_id1="$(last_run_field "$root" looppc run_id)"
  local composed1; composed1="$(cat "$root/state/runs/$run_id1/prompt.composed.md" 2>/dev/null || true)"
  assert_contains "prompt-composition: run1 has PRECHECK OUTPUT block" "$composed1" "## PRECHECK OUTPUT"
  assert_contains "prompt-composition: run1 precheck content present" "$composed1" "3 things found"
  assert_contains "prompt-composition: run1 has RUN CONTEXT block" "$composed1" "## RUN CONTEXT"
  assert_contains "prompt-composition: run1 RUN CONTEXT echoes exact run_id" "$composed1" "run_id: $run_id1"
  assert_before "prompt-composition: run1 RUN CONTEXT after prompt.md body" "$composed1" "Test loop prompt" "## RUN CONTEXT"
  assert_before "prompt-composition: run1 RUN CONTEXT before PRECHECK OUTPUT" "$composed1" "## RUN CONTEXT" "## PRECHECK OUTPUT"

  run_runner "$root" looppc
  unset FAKE_CONTRACT_FILE
  local run_id2; run_id2="$(last_run_field "$root" looppc run_id)"
  local composed2; composed2="$(cat "$root/state/runs/$run_id2/prompt.composed.md" 2>/dev/null || true)"
  assert_contains "prompt-composition: run2 has PRIOR FINDINGS block" "$composed2" "## PRIOR FINDINGS"
  assert_contains "prompt-composition: run2 prior findings mentions finding id" "$composed2" "subj:pccond"
  assert_contains "prompt-composition: run2 has RUN CONTEXT block" "$composed2" "## RUN CONTEXT"
  assert_contains "prompt-composition: run2 RUN CONTEXT echoes exact run_id" "$composed2" "run_id: $run_id2"
  assert_before "prompt-composition: run2 RUN CONTEXT after prompt.md body" "$composed2" "Test loop prompt" "## RUN CONTEXT"
  assert_before "prompt-composition: run2 RUN CONTEXT before PRIOR FINDINGS" "$composed2" "## RUN CONTEXT" "## PRIOR FINDINGS"
  rm -rf "$root"
}

# ===========================================================================
# start-of-run non-blocking dashboard regen (Amendment 2 -- 2026-07-30)
# ===========================================================================

test_start_regen_never_blocks_or_fails_when_dashboard_lock_held() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopdash agent >/dev/null

  mkdir -p "$root/state/locks"
  local fifo="$root/state/locks/.testhold-dash-$$"
  rm -f "$fifo"
  mkfifo "$fifo"
  local outfile; outfile="$(mktemp "${TMPDIR:-/tmp}/loops-testlock-dash-out.XXXXXX")"
  python3 "$root/bin/lock.py" acquire --name _dashboard --root "$root" < "$fifo" > "$outfile" 2>/dev/null &
  local lockpid=$!
  exec 9> "$fifo"
  local waited=0
  while [ ! -s "$outfile" ] && kill -0 "$lockpid" 2>/dev/null && [ "$waited" -lt 50 ]; do
    sleep 0.05
    waited=$((waited + 1))
  done

  assert_file_missing "start-regen: no dashboard yet" "$root/dashboard/loops.html"

  run_runner "$root" loopdash

  exec 9>&-
  wait "$lockpid" 2>/dev/null || true
  rm -f "$fifo" "$outfile"

  assert_eq "start-regen: exit code unaffected by held dashboard lock" "0" "$RUNNER_EXIT"
  assert_eq "start-regen: run row completed despite held dashboard lock" "completed" "$(last_run_field "$root" loopdash runner_status)"
  # both the new start-of-run check (must skip, lock held) and the existing
  # end-of-run --wait-s 30 acquire (also can't get in while held throughout)
  # degrade silently -- neither is allowed to write a partial file or fail the run.
  assert_file_missing "start-regen: dashboard still not regenerated while lock was held throughout" "$root/dashboard/loops.html"

  # once the lock is free, a later run's start-of-run regen fires immediately.
  run_runner "$root" loopdash
  assert_eq "start-regen: second run exit code" "0" "$RUNNER_EXIT"
  assert_file_exists "start-regen: dashboard regenerated once the lock is free" "$root/dashboard/loops.html"
  rm -rf "$root"
}

# test_start_regen_fires_before_engine_finishes — the discriminating case:
# proves the regen actually happens at start-of-run (immediately after
# db.py start-run, step 3), not only at end-of-run (step 7). A slow fake
# engine (FAKE_SLEEP_S) gives a window to observe dashboard.html already
# written WHILE the run is still in flight (kill -0 on the runner's pid
# still succeeds) — end-of-run's regen alone could never produce that.
test_start_regen_fires_before_engine_finishes() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  make_loop "$root" loopmid agent >/dev/null
  export FAKE_SLEEP_S=3

  local out err
  out="$(mktemp "${TMPDIR:-/tmp}/loops-midrun-out.XXXXXX")"
  err="$(mktemp "${TMPDIR:-/tmp}/loops-midrun-err.XXXXXX")"
  LOOPS_ROOT="$root" "$RUNNER" loopmid --trigger manual > "$out" 2> "$err" &
  local pid=$!

  local waited=0 seen=0
  while [ "$waited" -lt 50 ]; do
    if [ -f "$root/dashboard/loops.html" ]; then seen=1; break; fi
    sleep 0.05
    waited=$((waited + 1))
  done
  local still_running=0
  kill -0 "$pid" 2>/dev/null && still_running=1

  wait "$pid" 2>/dev/null
  local ec=$?
  unset FAKE_SLEEP_S
  rm -f "$out" "$err"

  assert_eq "start-regen-mid: dashboard.html appeared before the engine finished" "1" "$seen"
  assert_eq "start-regen-mid: run was still in-flight when it appeared" "1" "$still_running"
  assert_eq "start-regen-mid: run exit code" "0" "$ec"
  assert_eq "start-regen-mid: runner_status" "completed" "$(last_run_field "$root" loopmid runner_status)"
  rm -rf "$root"
}

# ===========================================================================
# main
# ===========================================================================

echo "== bin/run-loop.sh: completed ok/warn/alert =="
test_completed_ok
test_completed_warn
test_completed_alert

echo "== bin/run-loop.sh: skipped-overlap =="
test_skipped_overlap

echo "== bin/run-loop.sh: precheck (agent) =="
test_skipped_precheck_amber
test_precheck_failed

echo "== bin/run-loop.sh: watchdog =="
test_watchdog_silent_green
test_watchdog_escalation_sticky

echo "== bin/run-loop.sh: engine-timeout / stale-green =="
test_engine_timeout_stale_green

echo "== bin/run-loop.sh: contract-violation =="
test_contract_violation_invalid
test_contract_violation_run_id_mismatch

echo "== bin/run-loop.sh: transient retry / non-retried failures =="
test_transient_retry_exhausted
test_exit10_not_retried
test_exit11_not_retried

echo "== bin/run-loop.sh: harness-error =="
test_harness_error_releases_lock

echo "== bin/run-loop.sh: suppression / idempotence =="
test_suppression
test_idempotence

echo "== bin/run-loop.sh: retention pruning =="
test_retention_pruning
test_retention_pruning_cross_loop_isolation

echo "== bin/run-loop.sh: enabled=false =="
test_enabled_false_refused

echo "== bin/run-loop.sh: schedule=manual (IMPORTANT #2b) =="
test_schedule_manual_refused_on_launchd_trigger
test_schedule_manual_refused_on_kickstart_trigger
test_non_manual_schedule_still_runs_on_launchd_trigger

echo "== bin/run-loop.sh: --dry-run =="
test_dry_run

echo "== bin/run-loop.sh: prompt composition =="
test_prompt_composition

echo "== bin/run-loop.sh: start-of-run non-blocking dashboard regen =="
test_start_regen_never_blocks_or_fails_when_dashboard_lock_held
test_start_regen_fires_before_engine_finishes

echo
echo "passed: $TR_PASSED, failed: $TR_FAILED"
if [ "$TR_FAILED" -ne 0 ]; then
  exit 1
fi
exit 0
