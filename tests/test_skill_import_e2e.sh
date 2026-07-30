#!/usr/bin/env bash
# tests/test_skill_import_e2e.sh — end-to-end fixture proving Task 14's
# invariant: an IMPORTED loop, run TWICE against an unchanged world, produces
# STABLE finding_ids. This is the thing `loopctl validate` structurally
# cannot check (validate is static — it never runs an engine or touches
# sqlite) and is the reason the plan insists a supervised `loopctl run` is
# the real gate on an imported loop, not `validate` alone.
#
# Flow:
#   loopctl import fixtures/skills/clean-check --apply (canned answers,
#     schedule=manual per controller ruling — this loop is never installed)
#   -> point the scaffolded loop's engine at engines/fake.sh (same
#      LOOPS_ENGINE_OVERRIDE=fake / LOOPS_ALLOW_FAKE_ENGINE=1 double-gate
#      tests/test_examples.sh uses) with a canned contract emitting two
#      findings, alpha:dirty and beta:unpushed
#   -> loopctl run TWICE, same canned contract both times
#   -> via `db.py query open-findings`: same two finding_ids, times_seen==2
#      each, no duplicate rows
#   -> promoted reports/<name>/latest.json exists; the run's effective_status
#      (sqlite, authoritative — never hand-computed from the contract) is
#      "warn"
#
# Hermetic: own mktemp -d LOOPS_ROOT (new_hermetic_root(), extended below
# with a real bin/run-loop.sh — `loopctl run` shells out to
# <root>/bin/run-loop.sh, per bin/loopctl's cmd_run, unlike
# tests/test_examples.sh's run_runner helper, which invokes the real repo's
# bin/run-loop.sh directly). Never touches the real repo's
# state/reports/loops.d, never invokes a real engine CLI or the network.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=./runner_test_helpers.sh
source "$HERE/runner_test_helpers.sh"

LOOPCTL="$REPO_ROOT/bin/loopctl"
SKILL_FIXTURE="$REPO_ROOT/tests/fixtures/skills/clean-check"
LOOP_NAME="repo-hygiene-check"

export LOOPS_ENGINE_OVERRIDE=fake
export LOOPS_ALLOW_FAKE_ENGINE=1

reset_fake_env() {
  unset FAKE_EXIT FAKE_SLEEP_S FAKE_INVALID FAKE_OMIT_TMP FAKE_CONTRACT_FILE LOOPS_RETRY_BACKOFF_S 2>/dev/null || true
}
reset_fake_env

# import_root — new_hermetic_root() plus a real, executable bin/run-loop.sh
# (new_hermetic_root() only copies bin/*.py — run-loop.sh is a bash script,
# and `loopctl run` needs a real one under <root>/bin to shell out to).
import_root() {
  local root; root="$(new_hermetic_root)"
  cp "$REPO_ROOT/bin/run-loop.sh" "$root/bin/run-loop.sh"
  chmod +x "$root/bin/run-loop.sh"
  printf '%s' "$root"
}

# db_open_findings <root> <loop> — the exact CLI surface the brief calls out:
# `db.py query open-findings`, JSON array of {finding_id, times_seen, ...}.
db_open_findings() {
  local root="$1" loop="$2"
  python3 "$root/bin/db.py" query open-findings --root "$root" --loop "$loop"
}

# write_answers <path> <sha256> — the brief's own filled example (also the
# canonical CLEAN_ANSWERS in tests/test_loopctl.py's TestImportApply),
# EXCEPT q4_cadence: this loop is never installed, so `manual` (controller
# ruling) is the honest cadence and sidesteps launchd schedule semantics
# entirely.
write_answers() {
  local path="$1" sha="$2"
  python3 - "$path" "$sha" <<'PY'
import json, sys

path, sha = sys.argv[1:3]
answers = {
    "analyzer_version": "1",
    "skill_sha256": sha,
    "answers": {
        "q1_purpose": (
            "Report dirty/unpushed repos; done per-firing = report written; "
            "cross-run done = repo becomes clean"
        ),
        "q4_cadence": "manual",
        "q5_scope": "~/projects only; exclude maguyva",
        "q8_finding_identity": (
            "<repo-dir-name>:<condition> where condition is dirty|unpushed"
        ),
        "q9_semantics": "ok=all clean; warn=any dirty/unpushed; alert=never",
        "q10_metrics": (
            '{"panels":[{"title":"Dirty","metric":"repos.dirty","type":"number"}]}'
        ),
        "q11_budget": "engine default model; ~1k tokens; retry 1; timeout 300",
    },
    "provenance": {"q4_cadence": "controller"},
}
with open(path, "w") as f:
    json.dump(answers, f)
PY
}

# json_field <json> <python-expr-over-"data"> — small helper to pick a value
# out of a JSON blob without a temp file; used throughout for db.py query /
# --json CLI output.
json_field() {
  local json="$1" expr="$2"
  printf '%s' "$json" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print($expr)
"
}

test_skill_import_e2e() {
  reset_fake_env
  local root; root="$(import_root)"

  # ===========================================================================
  # Step 1: import the fixture skill with canned, validate-clean answers.
  # ===========================================================================

  local analyze_json sha
  analyze_json="$("$LOOPCTL" import "$SKILL_FIXTURE" --analyze --json --root "$root")"
  sha="$(json_field "$analyze_json" 'data["skill_sha256"]')"

  local answers_path="$root/answers.json"
  write_answers "$answers_path" "$sha"

  local import_out import_ec
  import_out="$("$LOOPCTL" import "$SKILL_FIXTURE" --apply --answers "$answers_path" \
    --actor "test-e2e" --root "$root" 2>&1)"
  import_ec=$?
  assert_eq "import --apply: exit code" "0" "$import_ec"
  assert_file_exists "import --apply: loop.conf scaffolded" \
    "$root/loops.d/$LOOP_NAME/loop.conf"
  assert_file_exists "import --apply: prompt.md scaffolded" \
    "$root/loops.d/$LOOP_NAME/prompt.md"

  # ===========================================================================
  # Step 2: point the scaffolded loop at engines/fake.sh with a canned
  # contract emitting two findings, alpha:dirty and beta:unpushed (both
  # severity=warn, so the recomputed effective_status is unambiguously
  # "warn" — the promotion logic's max-severity-of-unsuppressed-findings
  # rule, see bin/run-loop.sh's PROMOTE_OUT step).
  # ===========================================================================

  local fixture="$root/canned-contract.json"
  write_contract_fixture "$fixture" "warn" '[
    {"finding_id": "alpha:dirty", "title": "alpha has uncommitted changes", "severity": "warn", "detail": "git status --porcelain is non-empty for alpha"},
    {"finding_id": "beta:unpushed", "title": "beta has unpushed commits", "severity": "warn", "detail": "beta has 2 commits not on @{u}"}
  ]'
  assert_file_exists "canned contract fixture written" "$fixture"

  export FAKE_CONTRACT_FILE="$fixture"

  # ===========================================================================
  # Step 3: `loopctl run` TWICE against the SAME unchanged canned contract.
  # ===========================================================================

  local run1_out run1_ec
  run1_out="$("$LOOPCTL" run "$LOOP_NAME" --root "$root" 2>&1)"
  run1_ec=$?
  assert_eq "run1: loopctl run exit code" "0" "$run1_ec"
  assert_eq "run1: runner_status" "completed" "$(last_run_field "$root" "$LOOP_NAME" runner_status)"

  local run2_out run2_ec
  run2_out="$("$LOOPCTL" run "$LOOP_NAME" --root "$root" 2>&1)"
  run2_ec=$?
  assert_eq "run2: loopctl run exit code" "0" "$run2_ec"
  assert_eq "run2: runner_status" "completed" "$(last_run_field "$root" "$LOOP_NAME" runner_status)"

  unset FAKE_CONTRACT_FILE

  # ===========================================================================
  # Step 4: via `db.py query open-findings` — the SAME two finding_ids,
  # times_seen==2 for each, no duplicate rows. Assert from sqlite (via the
  # CLI), never from the contract — the engine only emits identity, the
  # runner derives recurrence.
  # ===========================================================================

  local open_findings
  open_findings="$(db_open_findings "$root" "$LOOP_NAME")"

  assert_eq "open-findings: row count == 2 (no duplicate rows)" "2" \
    "$(json_field "$open_findings" 'len(data)')"
  assert_eq "open-findings: finding_id set == {alpha:dirty, beta:unpushed}" \
    "alpha:dirty,beta:unpushed" \
    "$(json_field "$open_findings" '",".join(sorted(r["finding_id"] for r in data))')"
  assert_eq "open-findings: alpha:dirty times_seen == 2" "2" \
    "$(json_field "$open_findings" 'next(r for r in data if r["finding_id"] == "alpha:dirty")["times_seen"]')"
  assert_eq "open-findings: beta:unpushed times_seen == 2" "2" \
    "$(json_field "$open_findings" 'next(r for r in data if r["finding_id"] == "beta:unpushed")["times_seen"]')"

  # ===========================================================================
  # Step 5: promoted latest.json exists; the run's effective_status (sqlite,
  # authoritative, via db.py — same source test_examples.sh uses for this
  # field) is "warn".
  # ===========================================================================

  local latest_path="$root/reports/$LOOP_NAME/latest.json"
  assert_file_exists "reports/$LOOP_NAME/latest.json promoted" "$latest_path"
  local latest_json; latest_json="$(cat "$latest_path" 2>/dev/null || true)"
  assert_contains "latest.json contains alpha:dirty" "$latest_json" "alpha:dirty"
  assert_contains "latest.json contains beta:unpushed" "$latest_json" "beta:unpushed"
  assert_eq "run2: effective_status == warn" "warn" \
    "$(last_run_field "$root" "$LOOP_NAME" effective_status)"

  rm -rf "$root"
}

# ===========================================================================
# main
# ===========================================================================

echo "== tests/test_skill_import_e2e.sh: import -> two runs -> finding_id stability =="
test_skill_import_e2e

echo
echo "passed: $TR_PASSED, failed: $TR_FAILED"
if [ "$TR_FAILED" -ne 0 ]; then
  exit 1
fi
exit 0
