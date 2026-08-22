#!/usr/bin/env bash
# engines/fake.sh — TEST-ONLY engine adapter stub (§6 adapter interface).
#
# THIS IS NOT A REAL ENGINE. It exists solely so tests/test_runner.sh can
# exercise every bin/run-loop.sh code path deterministically, without
# invoking a real CLI, spending tokens, or touching the network (§11).
#
# The runner (bin/run-loop.sh) will only ever select this adapter when the
# loop.conf declares a real `engine=codex|claude` (loopconf's enum forbids
# anything else) AND both of these env vars are set at invocation time:
#   LOOPS_ENGINE_OVERRIDE=fake
#   LOOPS_ALLOW_FAKE_ENGINE=1
# This double-gate keeps a stray env var from ever substituting the fake
# engine into a real (launchd-triggered) run.
#
# Input: the standard §6.1 adapter environment (LOOP_NAME, RUN_ID,
# LOOPS_ROOT, WORKDIR, PROMPT_FILE, OUT_DIR, TIMEOUT_S, SCHEMA_FILE, MODEL,
# PERM_*, EXEC_ALLOWLIST, LOOP_TYPE). No arguments.
#
# Test knobs (env):
#   FAKE_CONTRACT_FILE   path to a canned contract.json to emit verbatim as
#                         contract.json.tmp (default: synthesize a minimal
#                         valid contract using $RUN_ID)
#   FAKE_EXIT             exit code to return: 0 ok, 1 generic failure,
#                         10 auth-failed, 11 tool-denied, 12 transient
#                         (§6.4). Default 0.
#   FAKE_SLEEP_S          seconds to sleep before doing anything else —
#                         used to exercise the runner's process-group
#                         timeout path. Default 0.
#   FAKE_INVALID=1        emit a schema-violating JSON blob as
#                         contract.json.tmp instead of a valid contract
#                         (exercises the contract-violation path).
#   FAKE_OMIT_TMP=1       do not write contract.json.tmp at all (exercises
#                         the missing-artifact contract-violation path).
set -euo pipefail

: "${OUT_DIR:?OUT_DIR must be set}"
: "${RUN_ID:?RUN_ID must be set}"

mkdir -p "$OUT_DIR"

if [ "${FAKE_SLEEP_S:-0}" != "0" ]; then
  sleep "${FAKE_SLEEP_S}"
fi

exit_code="${FAKE_EXIT:-0}"

{
  echo "fake engine invoked for loop=${LOOP_NAME:-} run=${RUN_ID}"
  echo "loop_type=${LOOP_TYPE:-} model=${MODEL:-} timeout_s=${TIMEOUT_S:-} exit=${exit_code}"
  if [ -n "${GC_BASE+x}" ]; then
    echo "GC_BASE=$GC_BASE"
  fi
} > "$OUT_DIR/engine.log" 2>&1 || true

cat > "$OUT_DIR/usage.json" <<EOF
{"input_tokens": 10, "output_tokens": 5}
EOF

status_word="ok"
case "$exit_code" in
  0) status_word="ok" ;;
  10) status_word="auth-failed" ;;
  11) status_word="tool-denied" ;;
  12) status_word="transient" ;;
  *) status_word="engine-failed" ;;
esac
echo "status=${status_word} exit=${exit_code}" > "$OUT_DIR/engine.status"

if [ "$exit_code" != "0" ]; then
  exit "$exit_code"
fi

if [ "${FAKE_OMIT_TMP:-0}" = "1" ]; then
  exit 0
fi

if [ "${FAKE_INVALID:-0}" = "1" ]; then
  echo '{"not": "a valid contract"}' > "$OUT_DIR/contract.json.tmp"
  exit 0
fi

if [ -n "${FAKE_CONTRACT_FILE:-}" ]; then
  # Fixture contracts may contain the literal placeholder __RUN_ID__ (the
  # test can't know the real run_id in advance, since the runner generates
  # it); substitute it for the actual $RUN_ID so schema-valid fixtures
  # still pass the runner's --expect-run-id check. A fixture that wants to
  # exercise the run_id-mismatch path simply omits the placeholder.
  sed "s/__RUN_ID__/${RUN_ID}/g" "$FAKE_CONTRACT_FILE" > "$OUT_DIR/contract.json.tmp"
  exit 0
fi

cat > "$OUT_DIR/contract.json.tmp" <<EOF
{
  "schema_version": 1,
  "run_id": "${RUN_ID}",
  "status": "ok",
  "status_reason": "fake_default",
  "headline": "fake engine default run",
  "report_markdown": "# Fake run\n\nAll clear.",
  "metrics": "{}",
  "findings": []
}
EOF
exit 0
