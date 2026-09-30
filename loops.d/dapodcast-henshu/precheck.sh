#!/usr/bin/env bash
# dapodcast-henshu/precheck.sh — trusted, unsandboxed step (type=agent).
#
# Codex's workspace-write sandbox makes .git read-only, so the engine can
# prepare an editorial pass but can never commit. Publishing therefore lives
# here: a pending manifest left by the previous firing is published by
# tools/feedback-publish.sh (idempotent, resumable), and the engine is then
# invoked only to report the result or to fix a failed check.
#
# Empty stdout + exit 0  => nothing to do, zero tokens (skipped-precheck).
# Non-empty stdout       => mode + context handed to the engine.
# Non-zero exit          => precheck-failed alert.
set -euo pipefail
: "${OUT_DIR:?OUT_DIR required}"

REPO="${DAPODCAST_ROOT:-$HOME/projects/directorsactorspodcast}"
API="${REVIEW_API_BASE:-https://dapodcast.pages.dev}"
STATE_DIR="$HOME/.cache/dapodcast/henshu"
MAX_FIX_ROUNDS=3
# The harness caps a precheck at 300 s. Publish is resumable, so a kill only
# defers the rest to the next firing; stop it cleanly before the cap.
PUBLISH_BUDGET_S=270
export PATH="$HOME/.local/node/bin:$PATH" GIT_TERMINAL_PROMPT=0
mkdir -p "$STATE_DIR"
cd "$REPO"

git fetch -q origin
pending="$(find docs/author-feedback/pending -maxdepth 1 -name '*.json' ! -name '*.state.json' 2>/dev/null | sort || true)"

if [ -n "$pending" ]; then
  if [ "$(printf '%s\n' "$pending" | wc -l)" -gt 1 ]; then
    echo "fatal: more than one pending editorial run"; printf '%s\n' "$pending"; exit 1
  fi
  run_id="$(basename "$pending" .json)"
  # A crash mid-git can leave a stale lock; no other process owns this checkout.
  if [ -f .git/index.lock ] && ! pgrep -u "$(id -u)" -x git >/dev/null; then rm -f .git/index.lock; fi
  set +e
  timeout "$PUBLISH_BUDGET_S" tools/feedback-publish.sh --resume > "$OUT_DIR/publish.log" 2>&1
  code=$?
  set -e
  result="$(grep '^RESULT ' "$OUT_DIR/publish.log" | tail -1 || true)"
  case "$code" in
    0)   mode=published; rm -f "$STATE_DIR/fix-$run_id" ;;
    2)   rounds=$(( $(cat "$STATE_DIR/fix-$run_id" 2>/dev/null || echo 0) + 1 ))
         echo "$rounds" > "$STATE_DIR/fix-$run_id"
         if [ "$rounds" -gt "$MAX_FIX_ROUNDS" ]; then
           echo "fatal: run $run_id failed validation/checks $MAX_FIX_ROUNDS times — needs a human"
           tail -20 "$OUT_DIR/publish.log"; exit 1
         fi
         mode=fix ;;
    124) mode=publish-timeout ;;
    *)   mode=publish-failed ;;
  esac
  echo "mode: $mode"
  echo "repo: $REPO"
  echo "run_id: $run_id"
  echo "manifest: $REPO/$pending"
  echo "publish_exit: $code"
  echo "publish_log: $OUT_DIR/publish.log"
  echo "result: ${result:-none}"
  echo "metrics: {\"new_feedback\": 0, \"new_edits\": 0, \"requeued\": 0, \"resumable\": 0}"
  echo "publish_log_tail:"
  tail -25 "$OUT_DIR/publish.log"
  exit 0
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "fatal: checkout is dirty and no editorial run is pending"
  git status --short | head -20
  exit 1
fi
ahead="$(git rev-list --count origin/main..HEAD)"
behind="$(git rev-list --count HEAD..origin/main)"
if [ "$ahead" != 0 ]; then
  echo "fatal: local main is $ahead commit(s) ahead of origin with no pending run"
  exit 1
fi
[ "$behind" = 0 ] || git merge -q --ff-only origin/main

if ! curl -fsS --max-time 30 "$API/api/review/state" -o "$OUT_DIR/state.json"; then
  echo "fatal: production review state unavailable at $API/api/review/state"
  exit 1
fi
node tools/review-feedback-scan.mjs --state "$OUT_DIR/state.json" --json > "$OUT_DIR/scan.json"

counts="$(node -e '
const s=require(process.argv[1]);
const n=k=>(s[k]||[]).length;
console.log([n("newFeedback"),n("newEdits"),n("requeued"),n("resumable")].join(" "));' "$OUT_DIR/scan.json")"
read -r nf ne nq nr <<<"$counts"

if [ "$nr" -gt 0 ]; then
  echo "fatal: $nr ledger record(s) are replied/applied but no pending run exists — needs a human"
  exit 1
fi
[ $((nf + ne + nq)) -gt 0 ] || exit 0

"$HOME/.local/venvs/dapodcast/bin/python" tools/pdf-pages.py >/dev/null 2>"$OUT_DIR/pdf-pages.log"

echo "mode: editorial"
echo "repo: $REPO"
echo "counts: new_feedback=$nf new_edits=$ne requeued=$nq resumable=$nr"
echo "scan_file: $OUT_DIR/scan.json"
echo "state_file: $OUT_DIR/state.json"
echo "pdf_cache: $HOME/.cache/dapodcast/pdf/<how-to-direct|secret-of-acting|to-be-an-actor>/<printed page>.txt"
echo "metrics: {\"new_feedback\": $nf, \"new_edits\": $ne, \"requeued\": $nq, \"resumable\": $nr}"
if [ "$(wc -c < "$OUT_DIR/scan.json")" -lt 50000 ]; then
  echo "scan:"
  cat "$OUT_DIR/scan.json"
else
  echo "scan: too large to inline — read scan_file"
fi
