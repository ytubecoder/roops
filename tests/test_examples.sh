#!/usr/bin/env bash
# tests/test_examples.sh — hermetic end-to-end regression fixture tests for
# examples/hello-loop and examples/hello-watchdog (docs/INTERFACES.md §11
# "Pilot" clause; Task F verification). Exercises the FAKE engine path only
# (never a real codex/claude CLI, never real network) against a throwaway
# LOOPS_ROOT, using the canned contracts checked into each example's
# fixtures/contract.json.
#
# Flow per example (matches the Amendment 1 verification matrix, §11):
#   run 1 -> findings recorded
#   run 2 (same canned contract) -> same finding_id(s), times_seen=2, no
#     duplicate findings rows
#   dismiss one finding via `bin/loopctl dismiss` (the real repo's loopctl,
#     pointed at the hermetic --root — dispose only touches sqlite, so this
#     never needs run-loop.sh under the hermetic root)
#   run 3 -> promoted latest.json omits the dismissed finding, the audit
#     copy (state/runs/<id>/contract.json) still has it verbatim
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./runner_test_helpers.sh
source "$HERE/runner_test_helpers.sh"

LOOPCTL="$REPO_ROOT/bin/loopctl"

export LOOPS_ENGINE_OVERRIDE=fake
export LOOPS_ALLOW_FAKE_ENGINE=1

reset_fake_env() {
  unset FAKE_EXIT FAKE_SLEEP_S FAKE_INVALID FAKE_OMIT_TMP FAKE_CONTRACT_FILE LOOPS_RETRY_BACKOFF_S 2>/dev/null || true
}
reset_fake_env

# copy_example <root> <name> — copies examples/<name> from the real repo
# into the hermetic root's examples/ dir, verbatim, executable bits
# preserved on precheck.sh. Never touches the real repo's examples/ (read
# only) or the real repo's state/reports/launchd (never referenced).
copy_example() {
  local root="$1" name="$2"
  mkdir -p "$root/examples"
  cp -R "$REPO_ROOT/examples/$name" "$root/examples/$name"
  chmod +x "$root/examples/$name/precheck.sh"
}

# ===========================================================================
# hello-loop (type=agent)
# ===========================================================================

test_hello_loop_e2e() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  copy_example "$root" hello-loop

  local fixture="$root/examples/hello-loop/fixtures/contract.json"
  assert_file_exists "hello-loop: fixture contract present" "$fixture"

  # --- run 1 ---
  export FAKE_CONTRACT_FILE="$fixture"
  run_runner "$root" hello-loop --from examples
  assert_eq "hello-loop run1: exit code" "0" "$RUNNER_EXIT"
  assert_eq "hello-loop run1: runner_status" "completed" "$(last_run_field "$root" hello-loop runner_status)"
  assert_eq "hello-loop run1: loop_status" "warn" "$(last_run_field "$root" hello-loop loop_status)"
  local latest1; latest1="$(cat "$root/reports/hello-loop/latest.json" 2>/dev/null || true)"
  assert_contains "hello-loop run1: latest.json has alpha:has-todo" "$latest1" "alpha:has-todo"
  assert_contains "hello-loop run1: latest.json has beta:has-todo" "$latest1" "beta:has-todo"

  # --- run 2: identical canned contract -> idempotence ---
  run_runner "$root" hello-loop --from examples
  unset FAKE_CONTRACT_FILE
  assert_eq "hello-loop run2: exit code" "0" "$RUNNER_EXIT"
  assert_eq "hello-loop: alpha times_seen == 2" "2" \
    "$(db_exec "$root" "SELECT times_seen FROM findings WHERE loop_name='hello-loop' AND finding_id='alpha:has-todo'")"
  assert_eq "hello-loop: beta times_seen == 2" "2" \
    "$(db_exec "$root" "SELECT times_seen FROM findings WHERE loop_name='hello-loop' AND finding_id='beta:has-todo'")"
  assert_eq "hello-loop: findings row count == 2 (no dups)" "2" \
    "$(db_exec "$root" "SELECT COUNT(*) FROM findings WHERE loop_name='hello-loop'")"

  # --- dismiss alpha:has-todo via loopctl (the real repo's loopctl, pointed
  # at the hermetic root; dispose only touches --root's sqlite) ---
  local dismiss_out dismiss_ec
  dismiss_out="$("$LOOPCTL" dismiss hello-loop alpha:has-todo --note "test dismiss" --root "$root" 2>&1)"
  dismiss_ec=$?
  assert_eq "hello-loop: loopctl dismiss exit code" "0" "$dismiss_ec"

  # --- run 3: engine still emits both (prompt-contract rule 3 says it
  # should keep emitting a dismissed finding until the situation changes;
  # the fixture, being static, does exactly that) -> suppression must be
  # runner-side, not prompt-side ---
  export FAKE_CONTRACT_FILE="$fixture"
  run_runner "$root" hello-loop --from examples
  unset FAKE_CONTRACT_FILE
  assert_eq "hello-loop run3: exit code" "0" "$RUNNER_EXIT"
  assert_eq "hello-loop run3: loop_status (verbatim engine emission)" "warn" "$(last_run_field "$root" hello-loop loop_status)"
  assert_eq "hello-loop run3: effective_status recomputed (only info-severity beta left unsuppressed)" \
    "ok" "$(last_run_field "$root" hello-loop effective_status)"

  local latest3; latest3="$(cat "$root/reports/hello-loop/latest.json" 2>/dev/null || true)"
  assert_not_contains "hello-loop run3: latest.json omits dismissed alpha:has-todo" "$latest3" "alpha:has-todo"
  assert_contains "hello-loop run3: latest.json still has beta:has-todo" "$latest3" "beta:has-todo"

  local run_id3; run_id3="$(last_run_field "$root" hello-loop run_id)"
  local audit3; audit3="$(cat "$root/state/runs/$run_id3/contract.json" 2>/dev/null || true)"
  assert_contains "hello-loop run3: audit contract.json keeps alpha:has-todo verbatim" "$audit3" "alpha:has-todo"

  local latest_md; latest_md="$(cat "$root/reports/hello-loop/latest.md" 2>/dev/null || true)"
  assert_contains "hello-loop run3: latest.md has suppression footer" "$latest_md" "Suppressed by disposition"
  assert_contains "hello-loop run3: latest.md footer names alpha:has-todo" "$latest_md" "alpha:has-todo"

  rm -rf "$root"
}

# ===========================================================================
# hello-watchdog (type=watchdog)
# ===========================================================================

test_hello_watchdog_e2e() {
  reset_fake_env
  local root; root="$(new_hermetic_root)"
  copy_example "$root" hello-watchdog

  # Force the probe to fail deterministically and hermetically: a
  # nonexistent file:// path never touches the network and never depends
  # on external state, unlike an unreachable http(s) host would.
  printf 'file:///nonexistent-test-examples-hello-watchdog-target\n' \
    > "$root/examples/hello-watchdog/target.txt"

  local fixture="$root/examples/hello-watchdog/fixtures/contract.json"
  assert_file_exists "hello-watchdog: fixture contract present" "$fixture"

  # --- run 1: precheck fails -> escalation -> diagnosis engine (fake) ---
  export FAKE_CONTRACT_FILE="$fixture"
  run_runner "$root" hello-watchdog --from examples
  assert_eq "hello-watchdog run1: exit code" "0" "$RUNNER_EXIT"
  assert_eq "hello-watchdog run1: loop_status sticky alert" "alert" "$(last_run_field "$root" hello-watchdog loop_status)"
  assert_eq "hello-watchdog run1: effective_status sticky alert" "alert" "$(last_run_field "$root" hello-watchdog effective_status)"
  assert_eq "hello-watchdog run1: heartbeat ok=0 (probe failed)" "0" \
    "$(db_exec "$root" "SELECT ok FROM heartbeats WHERE loop_name='hello-watchdog' ORDER BY id DESC LIMIT 1")"
  local run_id1; run_id1="$(last_run_field "$root" hello-watchdog run_id)"
  assert_file_exists "hello-watchdog run1: engine WAS invoked (escalation)" "$root/state/runs/$run_id1/engine.log"
  local latest1; latest1="$(cat "$root/reports/hello-watchdog/latest.json" 2>/dev/null || true)"
  assert_contains "hello-watchdog run1: latest.json has target:unreachable" "$latest1" "target:unreachable"

  # --- run 2: identical canned contract, probe still failing -> idempotence ---
  run_runner "$root" hello-watchdog --from examples
  unset FAKE_CONTRACT_FILE
  assert_eq "hello-watchdog run2: exit code" "0" "$RUNNER_EXIT"
  assert_eq "hello-watchdog: target:unreachable times_seen == 2" "2" \
    "$(db_exec "$root" "SELECT times_seen FROM findings WHERE loop_name='hello-watchdog' AND finding_id='target:unreachable'")"
  assert_eq "hello-watchdog: findings row count == 1 (no dups)" "1" \
    "$(db_exec "$root" "SELECT COUNT(*) FROM findings WHERE loop_name='hello-watchdog'")"

  # --- dismiss target:unreachable via loopctl ---
  local dismiss_ec
  "$LOOPCTL" dismiss hello-watchdog target:unreachable --note "test dismiss" --root "$root" >/dev/null 2>&1
  dismiss_ec=$?
  assert_eq "hello-watchdog: loopctl dismiss exit code" "0" "$dismiss_ec"

  # --- run 3: probe (still) fails -> escalation again; the finding is
  # suppressed from the promoted view, but watchdog stickiness (§4.3) keeps
  # loop_status/effective_status alert regardless of suppression ---
  export FAKE_CONTRACT_FILE="$fixture"
  run_runner "$root" hello-watchdog --from examples
  unset FAKE_CONTRACT_FILE
  assert_eq "hello-watchdog run3: exit code" "0" "$RUNNER_EXIT"
  assert_eq "hello-watchdog run3: loop_status still sticky alert" "alert" "$(last_run_field "$root" hello-watchdog loop_status)"
  assert_eq "hello-watchdog run3: effective_status still sticky alert (suppression != probe recovery)" \
    "alert" "$(last_run_field "$root" hello-watchdog effective_status)"

  local latest3; latest3="$(cat "$root/reports/hello-watchdog/latest.json" 2>/dev/null || true)"
  assert_not_contains "hello-watchdog run3: latest.json omits dismissed target:unreachable" "$latest3" "target:unreachable"

  local run_id3; run_id3="$(last_run_field "$root" hello-watchdog run_id)"
  local audit3; audit3="$(cat "$root/state/runs/$run_id3/contract.json" 2>/dev/null || true)"
  assert_contains "hello-watchdog run3: audit contract.json keeps target:unreachable verbatim" "$audit3" "target:unreachable"

  rm -rf "$root"
}

# ===========================================================================
# main
# ===========================================================================

echo "== tests/test_examples.sh: examples/hello-loop e2e (fake engine) =="
test_hello_loop_e2e

echo "== tests/test_examples.sh: examples/hello-watchdog e2e (fake engine) =="
test_hello_watchdog_e2e

echo
echo "passed: $TR_PASSED, failed: $TR_FAILED"
if [ "$TR_FAILED" -ne 0 ]; then
  exit 1
fi
exit 0
