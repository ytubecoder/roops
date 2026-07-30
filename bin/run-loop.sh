#!/usr/bin/env bash
# bin/run-loop.sh — the runner (§4 of docs/INTERFACES.md). This is where the
# harness's atomicity, stale-green, and suppression guarantees live: one
# firing = one lock, one run row, at most one engine invocation (plus
# transient retries), and a promotion step that only ever happens for a run
# that both completed and validated.
#
# Usage:
#   bin/run-loop.sh <loop-name> [--trigger launchd|manual|kickstart]
#                    [--from examples|loops.d] [--dry-run]
#
# Defaults: --from loops.d, --trigger manual.
#
# macOS has neither `flock` nor GNU `timeout` (§0), and its system /bin/bash
# is 3.2 (no `coproc`, no `exec {fd}>file` dynamic fd allocation). Two
# mechanics below work around that on purpose:
#   - The §2 "hold the lock for a shell block" pattern is implemented with a
#     mkfifo + a fixed fd (9), not `coproc`.
#   - The runner-owned process-group timeout uses `set -m` + background +
#     `kill -TERM/-KILL -- -$pid` (negative pid = signal the whole group),
#     not `setsid`/`timeout`.
#
# Test-only engine override: loop.conf's `engine` field is always a real
# engine (codex|claude — loopconf.py's enum forbids anything else). Tests
# substitute engines/fake.sh at *invocation* time only, via two env vars
# that must BOTH be set: LOOPS_ENGINE_OVERRIDE=fake and
# LOOPS_ALLOW_FAKE_ENGINE=1. The `engine` value recorded in sqlite is always
# the loop.conf-declared engine (what the loop is configured to use), not
# the substituted adapter — the override only changes which script is
# actually exec'd.
#
# LOOPS_RETRY_BACKOFF_S="30 120" (default) — space-separated seconds for the
# §4.6 transient-retry backoff, in attempt order. Tests override this to
# "0 0" so the retry-exhaustion path doesn't take minutes.
set -euo pipefail

# ---------------------------------------------------------------------------
# Constants / environment
# ---------------------------------------------------------------------------

ROOT="${LOOPS_ROOT:-$HOME/projects/loops}"
PY="python3"

PRECHECK_MAX_TIMEOUT_S=300
PRECHECK_CAP_BYTES=65536
LOCK_WAIT_POLL_S=0.05
LOCK_WAIT_MAX_MS=5000
ENGINE_TERM_GRACE_S=10
DASHBOARD_LOCK_WAIT_S=30

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

log_err() { printf '%s\n' "$*" >&2; }

die_usage() {
  log_err "usage: run-loop.sh <loop-name> [--trigger launchd|manual|kickstart] [--from examples|loops.d] [--dry-run]"
  log_err "$*"
  finalize_exit 2
}

# Every deliberate exit from this script goes through here so the EXIT trap
# (cleanup_trap) can tell "we finished on purpose" (any status, incl. usage
# errors and skip paths) from "something blew up unexpectedly" — only the
# latter is a harness-error.
FINALIZED=0
finalize_exit() {
  FINALIZED=1
  exit "$1"
}

# ---------------------------------------------------------------------------
# Lock management (§2) — mkfifo + fixed fd 9, no coproc (bash 3.2 on macOS).
# ---------------------------------------------------------------------------

LOCK_HELD=0
LOCK_PID=""
LOCK_FIFO=""
LOCK_STDOUT_FILE=""
LOCK_STDERR_FILE=""

release_lock() {
  if [ "$LOCK_HELD" = "1" ]; then
    exec 9>&- 2>/dev/null || true
    if [ -n "$LOCK_PID" ]; then
      wait "$LOCK_PID" 2>/dev/null || true
    fi
    [ -n "$LOCK_FIFO" ] && rm -f "$LOCK_FIFO"
    [ -n "$LOCK_STDOUT_FILE" ] && rm -f "$LOCK_STDOUT_FILE"
    [ -n "$LOCK_STDERR_FILE" ] && rm -f "$LOCK_STDERR_FILE"
    LOCK_HELD=0
  fi
}

# Sets LOCK_ACQUIRED=1 (and holds fd 9 open on the fifo, LOCK_HELD=1) or
# LOCK_ACQUIRED=0 on contention (HELD_BY info left in LOCK_STDERR_FILE).
LOCK_ACQUIRED=0
acquire_lock() {
  local locks_dir="$ROOT/state/locks"
  mkdir -p "$locks_dir"
  chmod 700 "$locks_dir" 2>/dev/null || true
  local fifo="$locks_dir/.runner-fifo-$$-$RANDOM"
  rm -f "$fifo"
  mkfifo -m 600 "$fifo"
  local out_file err_file
  out_file="$(mktemp "${TMPDIR:-/tmp}/loops-lock-out.XXXXXX")"
  err_file="$(mktemp "${TMPDIR:-/tmp}/loops-lock-err.XXXXXX")"

  "$PY" "$ROOT/bin/lock.py" acquire --name "$NAME" --root "$ROOT" \
    < "$fifo" > "$out_file" 2> "$err_file" &
  LOCK_PID=$!

  # Open the write side; this is what unblocks lock.py's read-open of the
  # fifo. Held open (fd 9) until release_lock() closes it, sending EOF —
  # that is the "hold the lock for the duration of a shell block" pattern.
  exec 9> "$fifo"

  local waited_ms=0
  while :; do
    if [ -s "$out_file" ]; then break; fi
    if ! kill -0 "$LOCK_PID" 2>/dev/null; then break; fi
    sleep "$LOCK_WAIT_POLL_S"
    waited_ms=$((waited_ms + 50))
    if [ "$waited_ms" -ge "$LOCK_WAIT_MAX_MS" ]; then break; fi
  done

  if grep -q '^ACQUIRED' "$out_file" 2>/dev/null; then
    LOCK_ACQUIRED=1
    LOCK_HELD=1
    LOCK_FIFO="$fifo"
    LOCK_STDOUT_FILE="$out_file"
    LOCK_STDERR_FILE="$err_file"
  else
    LOCK_ACQUIRED=0
    exec 9>&- 2>/dev/null || true
    wait "$LOCK_PID" 2>/dev/null || true
    rm -f "$fifo" "$out_file" "$err_file"
  fi
}

# ---------------------------------------------------------------------------
# Runner-owned process-group timeout (§4.1 step 5 / precheck discipline).
# Runs $2 (a shell function name — invoked with no args, its own
# redirections wired inside the function body) as its own process group
# (set -m before backgrounding), TERM at $1 seconds, 10s grace, then KILL.
# Sets globals RWT_EXIT_CODE and RWT_TIMED_OUT (0/1).
# ---------------------------------------------------------------------------

RWT_EXIT_CODE=0
RWT_TIMED_OUT=0

run_with_pgroup_timeout() {
  local timeout_secs="$1"
  local runner_fn="$2"

  set -m
  "$runner_fn" &
  local pid=$!
  set +m

  RWT_TIMED_OUT=0
  local start_ts now_ts elapsed
  start_ts=$(date +%s)
  while kill -0 "$pid" 2>/dev/null; do
    now_ts=$(date +%s)
    elapsed=$((now_ts - start_ts))
    if [ "$elapsed" -ge "$timeout_secs" ]; then
      RWT_TIMED_OUT=1
      kill -TERM -- -"$pid" 2>/dev/null || true
      local grace_start grace_elapsed
      grace_start=$(date +%s)
      while kill -0 "$pid" 2>/dev/null; do
        grace_elapsed=$(( $(date +%s) - grace_start ))
        if [ "$grace_elapsed" -ge "$ENGINE_TERM_GRACE_S" ]; then
          kill -KILL -- -"$pid" 2>/dev/null || true
          break
        fi
        sleep 0.2
      done
      break
    fi
    sleep 0.2
  done

  RWT_EXIT_CODE=0
  wait "$pid" || RWT_EXIT_CODE=$?
}

# ---------------------------------------------------------------------------
# Redaction (§4.4) — filter through bin/redact.py.
# ---------------------------------------------------------------------------

redact_file_inplace() {
  local f="$1"
  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/loops-redact.XXXXXX")"
  "$PY" "$ROOT/bin/redact.py" < "$f" > "$tmp" 2>/dev/null || cp "$f" "$tmp"
  cat "$tmp" > "$f"
  rm -f "$tmp"
}

# ---------------------------------------------------------------------------
# EXIT trap — harness-error catch-all (§4.6) + always release the lock.
# ---------------------------------------------------------------------------

RUN_ID_FOR_TRAP=""

cleanup_trap() {
  local ec=$?
  set +e

  if [ "$FINALIZED" != "1" ]; then
    if [ -n "$RUN_ID_FOR_TRAP" ]; then
      "$PY" "$ROOT/bin/db.py" finish-run --root "$ROOT" --run-id "$RUN_ID_FOR_TRAP" \
        --runner-status harness-error --exit-code "$ec" \
        --error-detail "unhandled runner error (exit $ec) at line ${BASH_LINENO[0]:-unknown}" \
        >/dev/null 2>&1
    fi
    ec=1
  fi

  release_lock

  exit "$ec"
}
trap cleanup_trap EXIT

# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------

NAME=""
TRIGGER="manual"
FROM="loops.d"
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --trigger)
      TRIGGER="${2:-}"; shift 2 ;;
    --from)
      FROM="${2:-}"; shift 2 ;;
    --dry-run)
      DRY_RUN=1; shift ;;
    -h|--help)
      die_usage "" ;;
    --*)
      die_usage "unknown flag: $1" ;;
    *)
      if [ -z "$NAME" ]; then NAME="$1"; else die_usage "unexpected argument: $1"; fi
      shift ;;
  esac
done

[ -n "$NAME" ] || die_usage "missing <loop-name>"
case "$TRIGGER" in
  launchd|manual|kickstart) ;;
  *) die_usage "invalid --trigger: $TRIGGER" ;;
esac
case "$FROM" in
  loops.d|examples) ;;
  *) die_usage "invalid --from: $FROM" ;;
esac

LOOP_DIR="$ROOT/$FROM/$NAME"
CONF_FILE="$LOOP_DIR/loop.conf"

# ---------------------------------------------------------------------------
# Step 1 (partial, dry-run): resolve + validate conf, no lock/db/engine.
# ---------------------------------------------------------------------------

if [ ! -f "$CONF_FILE" ]; then
  log_err "loop.conf not found: $CONF_FILE"
  finalize_exit 1
fi

CONF_JSON="$("$PY" "$ROOT/bin/loopconf.py" parse --file "$CONF_FILE" --json)" || {
  log_err "loopconf.py parse failed for $CONF_FILE"
  finalize_exit 1
}

CONF_ERRORS_COUNT="$("$PY" -c '
import json,sys
d=json.loads(sys.argv[1])
print(len(d.get("errors") or []))
' "$CONF_JSON")"

if [ "$CONF_ERRORS_COUNT" != "0" ]; then
  "$PY" -c '
import json,sys
d=json.loads(sys.argv[1])
for e in d.get("errors") or []:
    print(e, file=sys.stderr)
' "$CONF_JSON"
  log_err "loop.conf invalid: $CONF_FILE"
  finalize_exit 1
fi

conf_get() {
  "$PY" -c '
import json,sys
d=json.loads(sys.argv[1])["conf"]
v=d.get(sys.argv[2])
if v is None:
    print("")
elif isinstance(v, bool):
    print("true" if v else "false")
elif isinstance(v, list):
    print(",".join(v))
else:
    print(v)
' "$CONF_JSON" "$1"
}

CONF_TYPE="$(conf_get type)"
CONF_ENGINE="$(conf_get engine)"
CONF_MODEL="$(conf_get model)"
CONF_WORKDIR="$(conf_get workdir)"
CONF_TIMEOUT_S="$(conf_get timeout_s)"
CONF_ENABLED="$(conf_get enabled)"
CONF_RETENTION_DAYS="$(conf_get retention_days)"
CONF_RETRY_TRANSIENT="$(conf_get retry_transient)"
CONF_PERM_FS_WRITE="$(conf_get perm_fs_write)"
CONF_PERM_NETWORK="$(conf_get perm_network)"
CONF_PERM_LOCAL_EXEC="$(conf_get perm_local_exec)"
CONF_PERM_REMOTE_MUTATION="$(conf_get perm_remote_mutation)"
CONF_EXEC_ALLOWLIST="$(conf_get exec_allowlist)"

PROMPT_MD="$LOOP_DIR/prompt.md"
PRECHECK_SH="$LOOP_DIR/precheck.sh"

if [ "$DRY_RUN" = "1" ]; then
  # §4.1: resolve conf, compose prompt to stdout — no lock, no db, no
  # engine. PRIOR FINDINGS / PRECHECK OUTPUT blocks require live db /
  # process execution, so a dry run shows only the loop's own prompt.md
  # (the part that's actually static and inspectable without touching
  # state).
  if [ -f "$PROMPT_MD" ]; then
    cat "$PROMPT_MD"
  else
    log_err "prompt.md not found: $PROMPT_MD"
    finalize_exit 1
  fi
  printf '\n---\n## RUN CONTEXT\n(generated by the runner)\n\nrun_id: <not assigned — dry run>\n'
  finalize_exit 0
fi

# ---------------------------------------------------------------------------
# Step 1: db.py init, refuse disabled loops except --trigger manual.
# ---------------------------------------------------------------------------

mkdir -p "$ROOT/state" "$ROOT/reports" "$ROOT/state/runs" "$ROOT/state/locks"
chmod 700 "$ROOT/state" "$ROOT/reports" "$ROOT/state/runs" "$ROOT/state/locks" 2>/dev/null || true

"$PY" "$ROOT/bin/db.py" init --root "$ROOT" >/dev/null

if [ "$CONF_ENABLED" != "true" ] && [ "$TRIGGER" != "manual" ]; then
  finalize_exit 0
fi

if [ "$CONF_TYPE" = "watchdog" ] && { [ ! -f "$PRECHECK_SH" ] || [ ! -x "$PRECHECK_SH" ]; }; then
  log_err "type=watchdog requires an executable precheck.sh: $PRECHECK_SH"
  finalize_exit 1
fi

ENGINE_ADAPTER="$ROOT/engines/${CONF_ENGINE}.sh"
if [ "${LOOPS_ENGINE_OVERRIDE:-}" = "fake" ] && [ "${LOOPS_ALLOW_FAKE_ENGINE:-0}" = "1" ]; then
  ENGINE_ADAPTER="$ROOT/engines/fake.sh"
fi
if [ ! -x "$ENGINE_ADAPTER" ]; then
  log_err "missing or non-executable engine adapter: $ENGINE_ADAPTER"
  finalize_exit 1
fi

# ---------------------------------------------------------------------------
# Step 2: acquire lock (§2, non-blocking). Contention -> skipped-overlap.
# ---------------------------------------------------------------------------

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-${NAME}-$(od -An -N3 -tx1 /dev/urandom | tr -d ' \n')"
RUN_STARTED_AT="$(now_iso)"
OUT_DIR="$ROOT/state/runs/$RUN_ID"

db_start_run() {
  local args=(--root "$ROOT" --run-id "$RUN_ID" --loop "$NAME" --engine "$CONF_ENGINE" \
    --trigger "$TRIGGER" --started-at "$RUN_STARTED_AT")
  [ -n "$CONF_MODEL" ] && args+=(--model "$CONF_MODEL")
  "$PY" "$ROOT/bin/db.py" start-run "${args[@]}" >/dev/null
}

acquire_lock

if [ "$LOCK_ACQUIRED" != "1" ]; then
  RUN_ID_FOR_TRAP="$RUN_ID"
  db_start_run
  "$PY" "$ROOT/bin/db.py" finish-run --root "$ROOT" --run-id "$RUN_ID" \
    --runner-status skipped-overlap --finished-at "$RUN_STARTED_AT" >/dev/null
  finalize_exit 0
fi

RUN_ID_FOR_TRAP="$RUN_ID"

# ---------------------------------------------------------------------------
# Step 3: mkdir state/runs/<id> 0700, db.py start-run.
# ---------------------------------------------------------------------------

mkdir -p "$OUT_DIR"
chmod 700 "$OUT_DIR"

db_start_run

# (Amendment 2) best-effort "running now" regen — NEVER blocks or fails the run.
# check-then-generate is advisory (TOCTOU: the lock could be taken between the check and
# the generate call) — safe only because generate.py always writes via a unique tmp file +
# atomic os.rename, so a racing writer can never corrupt or interleave with another's output.
"$PY" "$ROOT/bin/lock.py" check --name _dashboard --root "$ROOT" >/dev/null 2>&1 && \
  "$PY" "$ROOT/dashboard/generate.py" --root "$ROOT" >/dev/null 2>&1 || true

# finish_run <runner_status> <loop_status> <effective_status> <attempts> \
#            <status_reason> <headline> <report_path> <contract_path> \
#            <exit_code> <error_detail>
# Any arg may be the literal string "-" for "omit this flag" (NULL).
db_finish_run() {
  local runner_status="$1" loop_status="$2" effective_status="$3" attempts="$4"
  local status_reason="$5" headline="$6" report_path="$7" contract_path="$8"
  local exit_code="$9" error_detail="${10}"
  local args=(--root "$ROOT" --run-id "$RUN_ID" --runner-status "$runner_status" --finished-at "$(now_iso)")
  [ "$loop_status" != "-" ] && args+=(--loop-status "$loop_status")
  [ "$effective_status" != "-" ] && args+=(--effective-status "$effective_status")
  [ "$attempts" != "-" ] && args+=(--attempts "$attempts")
  [ "$status_reason" != "-" ] && args+=(--status-reason "$status_reason")
  [ "$headline" != "-" ] && args+=(--headline "$headline")
  [ "$report_path" != "-" ] && args+=(--report-path "$report_path")
  [ "$contract_path" != "-" ] && args+=(--contract-path "$contract_path")
  [ "$exit_code" != "-" ] && args+=(--exit-code "$exit_code")
  [ "$error_detail" != "-" ] && args+=(--error-detail "$error_detail")
  [ -f "$OUT_DIR/usage.json" ] && args+=(--usage-file "$OUT_DIR/usage.json")
  "$PY" "$ROOT/bin/db.py" finish-run "${args[@]}" >/dev/null
}

finalize_and_finish() {
  # $1=runner_status $2=loop_status $3=effective_status $4=attempts
  # $5=status_reason $6=headline $7=report_path $8=contract_path
  # $9=exit_code $10=error_detail $11=heartbeat_ok(or "-")
  local hb_ok="${11:--}"
  if [ "$hb_ok" != "-" ]; then
    "$PY" "$ROOT/bin/db.py" heartbeat --root "$ROOT" --loop "$NAME" --run-id "$RUN_ID" --ok "$hb_ok" >/dev/null
  fi
  db_finish_run "$1" "$2" "$3" "$4" "$5" "$6" "$7" "$8" "$9" "${10}"
  "$PY" "$ROOT/bin/db.py" record-metrics --root "$ROOT" --run-id "$RUN_ID" --loop "$NAME" \
    --contract-file "${CONTRACT_FILE_FOR_METRICS:-/dev/null}" >/dev/null 2>&1 || true
  regen_dashboard
  prune_retention
  finalize_exit 0
}

regen_dashboard() {
  local dash_root="$ROOT/state/locks"
  mkdir -p "$dash_root"
  local fifo="$dash_root/.dashfifo-$$-$RANDOM"
  rm -f "$fifo"
  mkfifo -m 600 "$fifo" 2>/dev/null || { log_err "dashboard: mkfifo failed"; return 0; }
  local out_file
  out_file="$(mktemp "${TMPDIR:-/tmp}/loops-dashlock-out.XXXXXX")"
  "$PY" "$ROOT/bin/lock.py" acquire --name _dashboard --root "$ROOT" --wait-s "$DASHBOARD_LOCK_WAIT_S" \
    < "$fifo" > "$out_file" 2>/dev/null &
  local dpid=$!
  exec 8> "$fifo"
  local waited_ms=0
  while :; do
    if [ -s "$out_file" ]; then break; fi
    if ! kill -0 "$dpid" 2>/dev/null; then break; fi
    sleep 0.05
    waited_ms=$((waited_ms + 50))
    if [ "$waited_ms" -ge $(( (DASHBOARD_LOCK_WAIT_S + 1) * 1000 )) ]; then break; fi
  done
  if grep -q '^ACQUIRED' "$out_file" 2>/dev/null; then
    "$PY" "$ROOT/dashboard/generate.py" --root "$ROOT" >/dev/null 2>&1 || log_err "dashboard regen failed (ignored)"
    exec 8>&- 2>/dev/null || true
    wait "$dpid" 2>/dev/null || true
  else
    log_err "dashboard lock not acquired within ${DASHBOARD_LOCK_WAIT_S}s (ignored)"
    exec 8>&- 2>/dev/null || true
    wait "$dpid" 2>/dev/null || true
  fi
  rm -f "$fifo" "$out_file"
  return 0
}

prune_retention() {
  local retention_days="${CONF_RETENTION_DAYS:-30}"
  case "$retention_days" in ''|*[!0-9]*) retention_days=30 ;; esac
  "$PY" - "$ROOT" "$NAME" "$retention_days" <<'PYEOF' 2>/dev/null || true
import os, re, sys, time, shutil

root, name, days = sys.argv[1], sys.argv[2], int(sys.argv[3])
cutoff = time.time() - days * 86400

def prune_dir(d, keep_names=(), only_match=None):
    if not os.path.isdir(d):
        return
    for entry in os.listdir(d):
        if entry in keep_names:
            continue
        if only_match is not None and not only_match.match(entry):
            continue
        p = os.path.join(d, entry)
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            continue
        if mtime < cutoff:
            try:
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    os.remove(p)
            except OSError:
                pass

prune_dir(os.path.join(root, "reports", name), keep_names=("latest.md", "latest.json"))

# state/runs/* is a GLOBAL directory shared by every loop in the tree: a
# short-retention loop must only prune run dirs it owns, never another
# loop's audit trail. run_id shape (bin/run-loop.sh Step 2) is exactly
# <UTC %Y%m%dT%H%M%SZ>-<name>-<6 hex chars>; anchor on the full shape (not
# just a `-<name>-` substring) so a loop whose name is a prefix/suffix of
# another loop's name can never match its dirs.
run_id_re = re.compile(r"^\d{8}T\d{6}Z-" + re.escape(name) + r"-[0-9a-f]{6}$")
prune_dir(os.path.join(root, "state", "runs"), only_match=run_id_re)
PYEOF
  return 0
}

# ---------------------------------------------------------------------------
# Step 4: precheck (§4.1.4)
# ---------------------------------------------------------------------------

PRECHECK_RAN=0
PRECHECK_EXIT=0
PRECHECK_TIMED_OUT=0
PRECHECK_OUTPUT=""        # captured, capped, redacted stdout
PRECHECK_OUTPUT_EMPTY=1

_precheck_runner_fn() {
  cd "$LOOP_DIR"
  LOOP_NAME="$NAME" RUN_ID="$RUN_ID" LOOPS_ROOT="$ROOT" WORKDIR="$CONF_WORKDIR" OUT_DIR="$OUT_DIR" \
    exec "$PRECHECK_SH" > "$OUT_DIR/.precheck.raw.out" 2> "$OUT_DIR/.precheck.raw.err"
}

if [ -f "$PRECHECK_SH" ] && [ -x "$PRECHECK_SH" ]; then
  PRECHECK_RAN=1
  : > "$OUT_DIR/.precheck.raw.out"
  local_precheck_timeout="$CONF_TIMEOUT_S"
  if [ "$local_precheck_timeout" -gt "$PRECHECK_MAX_TIMEOUT_S" ]; then
    local_precheck_timeout="$PRECHECK_MAX_TIMEOUT_S"
  fi
  run_with_pgroup_timeout "$local_precheck_timeout" _precheck_runner_fn
  PRECHECK_EXIT="$RWT_EXIT_CODE"
  PRECHECK_TIMED_OUT="$RWT_TIMED_OUT"

  raw_file="$OUT_DIR/.precheck.raw.out"
  has_nul=0
  if [ -s "$raw_file" ] && od -An -tx1 "$raw_file" | tr -s ' ' '\n' | grep -qx '00'; then
    has_nul=1
  fi

  size=0
  [ -f "$raw_file" ] && size=$(wc -c < "$raw_file" | tr -d ' ')
  if [ "$size" -gt "$PRECHECK_CAP_BYTES" ]; then
    dd if="$raw_file" of="$OUT_DIR/precheck.out" bs=1 count="$PRECHECK_CAP_BYTES" 2>/dev/null
    printf '\n...[TRUNCATED: precheck output exceeded 64KiB cap]\n' >> "$OUT_DIR/precheck.out"
  else
    cp "$raw_file" "$OUT_DIR/precheck.out" 2>/dev/null || : > "$OUT_DIR/precheck.out"
  fi
  chmod 600 "$OUT_DIR/precheck.out" 2>/dev/null || true
  redact_file_inplace "$OUT_DIR/precheck.out"
  rm -f "$OUT_DIR/.precheck.raw.out" "$OUT_DIR/.precheck.raw.err"

  if [ "$has_nul" = "1" ]; then
    PRECHECK_EXIT=1
  fi
  if [ "$PRECHECK_TIMED_OUT" = "1" ] && [ "$PRECHECK_EXIT" = "0" ]; then
    PRECHECK_EXIT=1
  fi

  PRECHECK_OUTPUT="$(cat "$OUT_DIR/precheck.out" 2>/dev/null || true)"
  if [ -n "$(printf '%s' "$PRECHECK_OUTPUT" | tr -d '[:space:]')" ]; then
    PRECHECK_OUTPUT_EMPTY=0
  else
    PRECHECK_OUTPUT_EMPTY=1
  fi
fi

WATCHDOG_PROBE_FAILED=0
ESCALATE_TO_ENGINE=1

if [ "$CONF_TYPE" = "watchdog" ]; then
  ESCALATE_TO_ENGINE=0
  if [ "$PRECHECK_EXIT" = "0" ]; then
    "$PY" "$ROOT/bin/db.py" heartbeat --root "$ROOT" --loop "$NAME" --run-id "$RUN_ID" --ok 1 >/dev/null
    headline="probe ok"
    if [ "$PRECHECK_OUTPUT_EMPTY" != "1" ]; then
      first_line="$(printf '%s\n' "$PRECHECK_OUTPUT" | head -n1)"
      [ -n "$first_line" ] && headline="$first_line"
    fi
    finalize_and_finish completed ok ok - watchdog_silent_green "$headline" - - 0 -
  else
    "$PY" "$ROOT/bin/db.py" heartbeat --root "$ROOT" --loop "$NAME" --run-id "$RUN_ID" --ok 0 >/dev/null
    WATCHDOG_PROBE_FAILED=1
    ESCALATE_TO_ENGINE=1
  fi
else
  # type=agent
  if [ "$PRECHECK_RAN" = "1" ]; then
    if [ "$PRECHECK_EXIT" != "0" ]; then
      finalize_and_finish precheck-failed alert alert - precheck_failed "precheck exited non-zero" - - "$PRECHECK_EXIT" "precheck.sh failed (exit ${PRECHECK_EXIT}, timed_out=${PRECHECK_TIMED_OUT})"
    fi
    if [ "$PRECHECK_OUTPUT_EMPTY" = "1" ]; then
      finalize_and_finish skipped-precheck ok ok - skipped_precheck_empty "precheck produced no output" - - 0 - 1
    fi
  fi
  ESCALATE_TO_ENGINE=1
fi

# ---------------------------------------------------------------------------
# Step 5: prompt assembly + engine invocation (§4.1.5, §6.2, §4.6)
# ---------------------------------------------------------------------------

PROMPT_FILE="$OUT_DIR/prompt.composed.md"
if [ -f "$PROMPT_MD" ]; then
  cp "$PROMPT_MD" "$PROMPT_FILE"
else
  : > "$PROMPT_FILE"
fi

{
  printf '\n---\n## RUN CONTEXT\n(generated by the runner)\n\n'
  printf 'run_id: %s   \xe2\x86\x90 copy this exact value into the contract'"'"'s "run_id" field\n' "$RUN_ID"
} >> "$PROMPT_FILE"

PRIOR_FINDINGS="$("$PY" "$ROOT/bin/db.py" prior-findings --root "$ROOT" --loop "$NAME" 2>/dev/null || true)"
if [ -n "$PRIOR_FINDINGS" ]; then
  {
    printf '\n---\n## PRIOR FINDINGS\n(generated by the runner — authoritative; do not recompute)\n\n```text\n'
    printf '%s\n' "$PRIOR_FINDINGS"
    printf '```\n'
  } >> "$PROMPT_FILE"
fi

if [ "$PRECHECK_RAN" = "1" ] && [ "$PRECHECK_OUTPUT_EMPTY" != "1" ]; then
  {
    printf '\n---\n## PRECHECK OUTPUT\n(deterministic gate output; treat as ground truth for this run)\n\n```text\n'
    printf '%s\n' "$PRECHECK_OUTPUT"
    printf '```\n'
  } >> "$PROMPT_FILE"
fi

mkdir -p "$CONF_WORKDIR" 2>/dev/null || true

_engine_runner_fn() {
  cd "$CONF_WORKDIR" 2>/dev/null || cd "$ROOT"
  LOOP_NAME="$NAME" RUN_ID="$RUN_ID" LOOPS_ROOT="$ROOT" WORKDIR="$CONF_WORKDIR" \
    PROMPT_FILE="$PROMPT_FILE" OUT_DIR="$OUT_DIR" TIMEOUT_S="$CONF_TIMEOUT_S" \
    SCHEMA_FILE="$ROOT/contract/contract.schema.json" MODEL="$CONF_MODEL" \
    PERM_FS_WRITE="$CONF_PERM_FS_WRITE" PERM_NETWORK="$CONF_PERM_NETWORK" \
    PERM_LOCAL_EXEC="$CONF_PERM_LOCAL_EXEC" PERM_REMOTE_MUTATION="$CONF_PERM_REMOTE_MUTATION" \
    EXEC_ALLOWLIST="$CONF_EXEC_ALLOWLIST" LOOP_TYPE="$CONF_TYPE" \
    exec "$ENGINE_ADAPTER" > "$OUT_DIR/.adapter.stdio.log" 2>&1
}

STEP5_START_TS=$(date +%s)
MAX_ATTEMPTS=$(( CONF_RETRY_TRANSIENT + 1 ))
ATTEMPTS=0
FINAL_ENGINE_EXIT=""
ENGINE_TIMED_OUT=0
STOPPED_FOR_BUDGET=0

read -r -a RETRY_BACKOFFS <<< "${LOOPS_RETRY_BACKOFF_S:-30 120}"

while [ "$ATTEMPTS" -lt "$MAX_ATTEMPTS" ]; do
  now_ts=$(date +%s)
  elapsed=$(( now_ts - STEP5_START_TS ))
  remaining=$(( CONF_TIMEOUT_S - elapsed ))
  if [ "$remaining" -le 0 ]; then
    STOPPED_FOR_BUDGET=1
    break
  fi

  ATTEMPTS=$((ATTEMPTS + 1))
  run_with_pgroup_timeout "$remaining" _engine_runner_fn
  FINAL_ENGINE_EXIT="$RWT_EXIT_CODE"
  ENGINE_TIMED_OUT="$RWT_TIMED_OUT"

  if [ "$ENGINE_TIMED_OUT" = "1" ]; then
    break
  fi
  if [ "$FINAL_ENGINE_EXIT" != "12" ]; then
    break
  fi
  # Transient failure: retry if attempts remain and budget allows.
  if [ "$ATTEMPTS" -ge "$MAX_ATTEMPTS" ]; then
    break
  fi
  backoff_idx=$((ATTEMPTS - 1))
  last_idx=$(( ${#RETRY_BACKOFFS[@]} - 1 ))
  if [ "$backoff_idx" -lt "${#RETRY_BACKOFFS[@]}" ]; then
    backoff_s="${RETRY_BACKOFFS[$backoff_idx]}"
  elif [ "$last_idx" -ge 0 ]; then
    backoff_s="${RETRY_BACKOFFS[$last_idx]}"
  else
    backoff_s=30
  fi
  now_ts=$(date +%s)
  elapsed=$(( now_ts - STEP5_START_TS ))
  remaining=$(( CONF_TIMEOUT_S - elapsed ))
  if [ "$remaining" -le "$backoff_s" ]; then
    STOPPED_FOR_BUDGET=1
    break
  fi
  sleep "$backoff_s"
done

# ---------------------------------------------------------------------------
# Classification of the engine outcome -> runner_status (§4.2/§4.3/§6.4)
# ---------------------------------------------------------------------------

ENGINE_RUNNER_STATUS=""
ENGINE_ERROR_DETAIL="-"

if [ "$ENGINE_TIMED_OUT" = "1" ]; then
  ENGINE_RUNNER_STATUS="engine-timeout"
  ENGINE_ERROR_DETAIL="engine exceeded timeout_s=${CONF_TIMEOUT_S}s (attempt ${ATTEMPTS})"
else
  case "$FINAL_ENGINE_EXIT" in
    0) ENGINE_RUNNER_STATUS="completed" ;;
    10) ENGINE_RUNNER_STATUS="auth-failed"; ENGINE_ERROR_DETAIL="adapter exit 10 (auth/credential failure)" ;;
    11) ENGINE_RUNNER_STATUS="tool-denied"; ENGINE_ERROR_DETAIL="adapter exit 11 (tool denied by permission layer)" ;;
    12) ENGINE_RUNNER_STATUS="engine-failed"; ENGINE_ERROR_DETAIL="transient failures exhausted after ${ATTEMPTS} attempt(s)" ;;
    *) ENGINE_RUNNER_STATUS="engine-failed"; ENGINE_ERROR_DETAIL="adapter exit ${FINAL_ENGINE_EXIT} (attempt ${ATTEMPTS})" ;;
  esac
fi
if [ "$STOPPED_FOR_BUDGET" = "1" ] && [ "$ENGINE_RUNNER_STATUS" != "completed" ]; then
  ENGINE_ERROR_DETAIL="retry budget (timeout_s=${CONF_TIMEOUT_S}) exhausted after ${ATTEMPTS} attempt(s); ${ENGINE_ERROR_DETAIL}"
fi

CONTRACT_TMP="$OUT_DIR/contract.json.tmp"
CONTRACT_FINAL="$OUT_DIR/contract.json"
CONTRACT_FILE_FOR_METRICS="/dev/null"

sticky_loop_status() {
  # If this is a watchdog escalation whose probe failed, loop_status /
  # effective_status are forced to "alert" regardless of everything else
  # (§4.3 watchdog stickiness). Prints "alert" or "-" (no override).
  if [ "$WATCHDOG_PROBE_FAILED" = "1" ]; then
    printf 'alert'
  else
    printf '-'
  fi
}

if [ "$ENGINE_RUNNER_STATUS" != "completed" ]; then
  sticky="$(sticky_loop_status)"
  finalize_and_finish "$ENGINE_RUNNER_STATUS" "$sticky" "$sticky" "$ATTEMPTS" \
    engine_error - - - "${FINAL_ENGINE_EXIT:--}" "$ENGINE_ERROR_DETAIL"
fi

# ---------------------------------------------------------------------------
# Step 6: contract validation + promotion (the stale-green guarantee)
# ---------------------------------------------------------------------------

VALIDATION_ERRORS=""
VALID=0
if [ -f "$CONTRACT_TMP" ]; then
  if VALIDATION_ERRORS="$("$PY" "$ROOT/bin/validate_contract.py" --schema "$ROOT/contract/contract.schema.json" \
      --file "$CONTRACT_TMP" --expect-run-id "$RUN_ID" 2>&1)"; then
    VALID=1
  else
    VALID=0
  fi
else
  VALIDATION_ERRORS="contract.json.tmp missing"
fi

if [ "$VALID" != "1" ]; then
  # §4.1 step 6: invalid/missing contract (incl. run_id mismatch, which
  # validate_contract.py reports as a validation error via --expect-run-id)
  # is alert alert, same as precheck-failed -- not the sticky "-" omission
  # used for engine-failed/auth-failed/tool-denied/engine-timeout. Watchdog
  # stickiness (§4.3) already wants alert here too, so hardcoding it is a
  # superset, not a change in watchdog behavior.
  detail="contract-violation: $(printf '%s' "$VALIDATION_ERRORS" | tr '\n' '; ')"
  finalize_and_finish contract-violation alert alert "$ATTEMPTS" \
    contract_violation - - - "${FINAL_ENGINE_EXIT:--}" "$detail"
fi

mv "$CONTRACT_TMP" "$CONTRACT_FINAL"
chmod 600 "$CONTRACT_FINAL" 2>/dev/null || true
CONTRACT_FILE_FOR_METRICS="$CONTRACT_FINAL"

RUN_TS="$(now_iso)"
"$PY" "$ROOT/bin/db.py" upsert-findings --root "$ROOT" --run-id "$RUN_ID" --loop "$NAME" \
  --contract-file "$CONTRACT_FINAL" --ts "$RUN_TS" >/dev/null

SUPPRESSED_JSON="$("$PY" "$ROOT/bin/db.py" suppressed --root "$ROOT" --loop "$NAME" --ts "$RUN_TS")"

REPORT_DIR="$ROOT/reports/$NAME"
mkdir -p "$REPORT_DIR"
DATED_NAME="$(date -u +%Y-%m-%d-%H%M).md"

PROMOTE_OUT="$("$PY" - "$CONTRACT_FINAL" "$SUPPRESSED_JSON" "$OUT_DIR" "$REPORT_DIR" "$DATED_NAME" "$NAME" <<'PYEOF'
import json, os, sys

contract_path, suppressed_json, out_dir, report_dir, dated_name, loop_name = sys.argv[1:7]

with open(contract_path, "r") as f:
    contract = json.load(f)

# db.py suppressed now emits objects: {finding_id, action, created_at, note,
# snooze_until} (§3, §4.5). Index by finding_id for filtering; keep the
# detail around for the human-readable footer.
suppressed_by_id = {d["finding_id"]: d for d in json.loads(suppressed_json)}
suppressed_ids = set(suppressed_by_id)
findings = contract.get("findings") or []

unsuppressed = [f for f in findings if f.get("finding_id") not in suppressed_ids]
suppressed_findings = [f for f in findings if f.get("finding_id") in suppressed_ids]

loop_status = contract.get("status")
if findings:
    order = {"info": 0, "warn": 1, "alert": 2}
    sev_map = {"info": "ok", "warn": "warn", "alert": "alert"}
    if unsuppressed:
        max_sev = max(unsuppressed, key=lambda f: order.get(f.get("severity"), 0)).get("severity")
        effective_status = sev_map.get(max_sev, "ok")
    else:
        effective_status = "ok"
else:
    effective_status = loop_status

report_md = contract.get("report_markdown", "") or ""
if suppressed_findings:
    lines = []
    for f in suppressed_findings:
        fid = f.get("finding_id", "?")
        disp = suppressed_by_id.get(fid, {})
        action = disp.get("action")
        if action == "dismiss":
            date = (disp.get("created_at") or "")[:10]
            note = disp.get("note") or ""
            if note:
                detail = f'dismissed {date} "{note}"'
            else:
                detail = f"dismissed {date}"
        elif action == "snooze":
            until = disp.get("snooze_until") or ""
            detail = f"snoozed until {until}"
        else:
            detail = action or "?"
        lines.append(f"{fid} ({detail})")
    footer = "\n\n---\nSuppressed by disposition: " + ", ".join(lines) + "\n"
    promoted_md = report_md + footer
else:
    promoted_md = report_md

output_md_path = os.path.join(out_dir, "output.md")
with open(output_md_path, "w") as f:
    f.write(promoted_md)
os.chmod(output_md_path, 0o600)

dated_path = os.path.join(report_dir, dated_name)
with open(dated_path, "w") as f:
    f.write(promoted_md)
os.chmod(dated_path, 0o600)

latest_md_tmp = os.path.join(report_dir, "latest.md.tmp")
with open(latest_md_tmp, "w") as f:
    f.write(promoted_md)
os.chmod(latest_md_tmp, 0o600)
os.rename(latest_md_tmp, os.path.join(report_dir, "latest.md"))

promoted_contract = dict(contract)
promoted_contract["findings"] = unsuppressed
latest_json_tmp = os.path.join(report_dir, "latest.json.tmp")
with open(latest_json_tmp, "w") as f:
    json.dump(promoted_contract, f, indent=2)
os.chmod(latest_json_tmp, 0o600)
os.rename(latest_json_tmp, os.path.join(report_dir, "latest.json"))

print(json.dumps({
    "effective_status": effective_status,
    "loop_status": loop_status,
    "status_reason": contract.get("status_reason", ""),
    "headline": contract.get("headline", ""),
    "dated_report": os.path.join("reports", loop_name, dated_name),
    "suppressed_count": len(suppressed_findings),
}))
PYEOF
)"

ENGINE_LOOP_STATUS="$("$PY" -c 'import json,sys; print(json.loads(sys.argv[1])["loop_status"])' "$PROMOTE_OUT")"
ENGINE_EFFECTIVE_STATUS="$("$PY" -c 'import json,sys; print(json.loads(sys.argv[1])["effective_status"])' "$PROMOTE_OUT")"
ENGINE_STATUS_REASON="$("$PY" -c 'import json,sys; print(json.loads(sys.argv[1])["status_reason"])' "$PROMOTE_OUT")"
ENGINE_HEADLINE="$("$PY" -c 'import json,sys; print(json.loads(sys.argv[1])["headline"])' "$PROMOTE_OUT")"
REPORT_PATH_REL="$("$PY" -c 'import json,sys; print(json.loads(sys.argv[1])["dated_report"])' "$PROMOTE_OUT")"
CONTRACT_PATH_REL="state/runs/$RUN_ID/contract.json"

FINAL_LOOP_STATUS="$ENGINE_LOOP_STATUS"
FINAL_EFFECTIVE_STATUS="$ENGINE_EFFECTIVE_STATUS"
if [ "$WATCHDOG_PROBE_FAILED" = "1" ]; then
  FINAL_LOOP_STATUS="alert"
  FINAL_EFFECTIVE_STATUS="alert"
fi

finalize_and_finish completed "$FINAL_LOOP_STATUS" "$FINAL_EFFECTIVE_STATUS" "$ATTEMPTS" \
  "$ENGINE_STATUS_REASON" "$ENGINE_HEADLINE" "$REPORT_PATH_REL" "$CONTRACT_PATH_REL" 0 -
