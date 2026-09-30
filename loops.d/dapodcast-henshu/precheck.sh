#!/usr/bin/env bash
# dapodcast-henshu/precheck.sh — read-only gate (type=agent).
# Empty stdout + exit 0  => nothing to do, zero tokens (skipped-precheck).
# Non-empty stdout       => mode + scan handed to the engine.
# Non-zero exit          => precheck-failed alert (dirty checkout, API down...).
set -euo pipefail
: "${OUT_DIR:?OUT_DIR required}"

REPO="${DAPODCAST_ROOT:-$HOME/projects/directorsactorspodcast}"
API="${REVIEW_API_BASE:-https://dapodcast.pages.dev}"
export PATH="$HOME/.local/node/bin:$PATH" GIT_TERMINAL_PROMPT=0
cd "$REPO"

git fetch -q origin
pending="$(find docs/author-feedback/pending -maxdepth 1 -name '*.json' ! -name '*.state.json' 2>/dev/null | sort || true)"
if [ -n "$(git status --porcelain)" ] && [ -z "$pending" ]; then
  echo "fatal: checkout is dirty and no editorial run is pending"
  git status --short | head -20
  exit 1
fi
ahead="$(git rev-list --count origin/main..HEAD)"
behind="$(git rev-list --count HEAD..origin/main)"
if [ -z "$pending" ]; then
  if [ "$ahead" != 0 ]; then
    echo "fatal: local main is $ahead commit(s) ahead of origin with no pending run"
    exit 1
  fi
  [ "$behind" = 0 ] || git merge -q --ff-only origin/main
fi

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

if [ -n "$pending" ]; then mode=resume
elif [ $((nf + ne + nq)) -gt 0 ]; then mode=editorial
elif [ "$nr" -gt 0 ]; then
  echo "fatal: $nr ledger record(s) are replied/applied but no pending run exists — needs a human"
  exit 1
else
  exit 0
fi

if [ "$mode" = editorial ]; then
  "$HOME/.local/venvs/dapodcast/bin/python" tools/pdf-pages.py >/dev/null
fi

echo "mode: $mode"
echo "repo: $REPO"
echo "pending: ${pending:-none}"
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
