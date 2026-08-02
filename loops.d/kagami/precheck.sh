#!/usr/bin/env bash
# kagami precheck — ALL deterministic work lives here (script→agent pattern):
# regenerate the public mock garden from the pinned fixture, run the publish
# gates, compare against the live page, and (drift + gates green) open/update
# the refresh PR. Deliberate deviation from the read-only default, justified in
# SPEC.md §7: this trusted deterministic script is the ONLY thing that mutates
# remote state (one branch + one PR on the public Pages repo); the engine that
# follows is at the report-only floor and just interprets the matrix below.
# Public-facing strings say "roops mirror" — internal loop names never leave.
set -euo pipefail

# launchd's minimal PATH lacks gh/git — pin to the login-shell PATH (kagi-ban pattern).
AUDIT_PATH="$(/bin/zsh -l -c 'printf %s "$PATH"' 2>/dev/null | tail -n 1)"
case "$AUDIT_PATH" in
  */bin*) export PATH="$AUDIT_PATH" ;;
  *) echo "WARN: could not resolve login-shell PATH; using inherited PATH" >&2 ;;
esac

LIVE_URL="https://ytubecoder.github.io/roops/mock-garden.html"
PAGES_REPO="ytubecoder/ytubecoder.github.io"
BRANCH="roops/mock-garden-refresh"
PAT_FILE="$HOME/.config/roops-kagami/pat"
MOCK_PATH="/Users/niwa/roops"
FIX="$LOOPS_ROOT/state/kagami-fixture"
ART="$OUT_DIR/mock-garden.html"

CHECKS_OUT=""
TESTS_FAILED=0
add_check() { # name pass|fail [note]
  local note=""
  if [ -n "${3:-}" ]; then note=" — $3"; fi
  CHECKS_OUT="${CHECKS_OUT}check $1: $2${note}
"
  if [ "$2" = "fail" ]; then TESTS_FAILED=$((TESTS_FAILED + 1)); fi
  python3 - "$OUT_DIR/matrix-checks.jsonl" "$1" "$2" "${3:-}" <<'PY'
import json, sys
path, name, verdict, note = sys.argv[1:5]
with open(path, "a") as f:
    f.write(json.dumps({"name": name, "ok": verdict == "pass", "note": note}) + "\n")
PY
  return 0
}
: > "$OUT_DIR/matrix-checks.jsonl"

# --- check: regenerate (fixture -> real generator, pinned clock, path rewrite) ---
REGEN_OK=1
if PINNED_NOW=$(python3 "$LOOPS_ROOT/loops.d/kagami/fixture/build_root.py" "$FIX") \
   && python3 "$LOOPS_ROOT/dashboard/generate.py" --root "$FIX" \
        --now "$PINNED_NOW" --out "$ART" >/dev/null; then
  python3 - "$ART" "$FIX" "$MOCK_PATH" <<'PY'
import os, sys
art, fix, mock = sys.argv[1:4]
html = open(art, encoding="utf-8").read()
for real in {fix, os.path.realpath(fix)}:
    html = html.replace(real, mock)
open(art, "w", encoding="utf-8").write(html)
PY
  add_check regenerate pass
else
  REGEN_OK=0
  add_check regenerate fail "build_root.py or generate.py exited non-zero"
fi

DRIFT=no
LIVE=skipped
PR_STATE=none
PR_URL=""
PR_NOTE=""
ART_BYTES=0
ART_SHA=""

if [ "$REGEN_OK" = 1 ]; then
  ART_BYTES=$(wc -c < "$ART" | tr -d ' ')
  ART_SHA=$(shasum -a 256 "$ART" | cut -d' ' -f1)

  # --- check: self-contained (page fetches nothing on load) ---
  if EXT=$(python3 - "$LOOPS_ROOT" "$ART" <<'PY'
import sys
root, art = sys.argv[1:3]
sys.path.insert(0, f"{root}/tests")
from html_selfcontained import external_subresources
ext = external_subresources(open(art, encoding="utf-8").read())
if ext:
    print("; ".join(str(e) for e in ext[:3]))
    sys.exit(1)
PY
  ); then add_check self-contained pass
  else add_check self-contained fail "external subresource: ${EXT:-see scan}"; fi

  # --- check: name-leak (no real loop names, paths, hosts on the public artifact) ---
  if LEAK=$(python3 - "$LOOPS_ROOT" "$ART" "$HOME" <<'PY'
import os, sys
root, art, home = sys.argv[1:4]
html = open(art, encoding="utf-8").read()
terms = {home, "/Users/llm", "maguyva", "example.org", "example"}
terms.update(os.listdir(os.path.join(root, "loops.d")))  # every REAL loop name
hits = sorted(t for t in terms if t and t in html)
if hits:
    print(", ".join(hits[:5]))
    sys.exit(1)
PY
  ); then add_check name-leak pass
  else add_check name-leak fail "real-world term on public artifact: ${LEAK:-?}"; fi

  # --- check: token-drift (generate.py palette <-> pagekit/kit.css lockstep) ---
  if (cd "$LOOPS_ROOT" && python3 tests/test_token_drift.py >/dev/null 2>&1); then
    add_check token-drift pass
  else add_check token-drift fail "tests/test_token_drift.py failing"; fi

  # --- live fetch + byte compare ---
  CODE=$(curl -s --max-time 60 -o "$OUT_DIR/live.html" -w '%{http_code}' \
    "$LIVE_URL?v=$(date +%s)" || echo 000)
  if [ "$CODE" = 200 ]; then
    LIVE=200
    if cmp -s "$ART" "$OUT_DIR/live.html"; then DRIFT=no; else DRIFT=yes; fi
  elif [ "$CODE" = 404 ]; then
    LIVE=404 DRIFT=yes   # first publication: nothing live yet counts as drift
  else
    LIVE=unreachable
  fi
fi

# --- PR open/update: only on drift with EVERY gate green ---
if [ "$DRIFT" = yes ] && [ "$TESTS_FAILED" = 0 ]; then
  WORK="$OUT_DIR/pages-repo"
  CLONE_URL="https://github.com/${PAGES_REPO}.git"
  if [ -f "$PAT_FILE" ]; then
    TOKEN=$(cat "$PAT_FILE")
    export GH_TOKEN="$TOKEN"
    CLONE_URL="https://x-access-token:${TOKEN}@github.com/${PAGES_REPO}.git"
  fi
  if PR_ERR=$( { git clone --quiet --depth 1 "$CLONE_URL" "$WORK" \
      && git -C "$WORK" checkout --quiet -B "$BRANCH" \
      && mkdir -p "$WORK/roops" \
      && cp "$ART" "$WORK/roops/mock-garden.html" \
      && git -C "$WORK" add roops/mock-garden.html; } 2>&1 ) \
      && git -C "$WORK" diff --cached --quiet; then
    # regenerated artifact == repo default branch: the refresh is already merged
    # and only the Pages deploy lags. Nothing to propose.
    PR_STATE=none PR_NOTE="merged content matches; Pages deploy pending"
  elif [ -d "$WORK/.git" ] \
      && PR_ERR=$( { git -C "$WORK" -c user.name="roops mirror" \
           -c user.email="roops-mirror@users.noreply.github.com" \
           commit --quiet -m "roops: refresh mock garden (mirror drift)" \
      && git -C "$WORK" push --quiet --force origin "$BRANCH"; } 2>&1 ); then
    EXISTING=$(gh pr list -R "$PAGES_REPO" --head "$BRANCH" --state open \
      --json url -q '.[0].url' 2>/dev/null || true)
    if [ -n "$EXISTING" ]; then
      PR_STATE=updated PR_URL="$EXISTING"
    else
      BODY="$OUT_DIR/pr-body.md"
      {
        echo "Automated refresh of the public mock garden: the interface changed"
        echo "and the published mirror no longer matches the generator's output"
        echo "over the pinned mock fixture. Merging publishes the refresh."
        echo
        echo "| check | result |"
        echo "|---|---|"
        printf '%s' "$CHECKS_OUT" | sed 's/^check /| /; s/: pass/ | pass |/; s/: fail/ | FAIL |/'
        echo
        echo "artifact: ${ART_BYTES} bytes · sha256 ${ART_SHA}"
        echo
        echo "Opened by the roops mirror loop. Review the rendered page, then"
        echo "merge to approve publication — the loop never merges."
      } > "$BODY"
      if PR_URL=$(gh pr create -R "$PAGES_REPO" --head "$BRANCH" \
          --title "roops: refresh mock garden" --body-file "$BODY" 2>&1); then
        PR_STATE=opened
      else
        PR_STATE=failed PR_NOTE="gh pr create: $(printf '%s' "$PR_URL" | tail -1)"
        PR_URL=""
      fi
    fi
  else
    PR_STATE=failed PR_NOTE="clone/push: $(printf '%s' "$PR_ERR" | tail -1)"
  fi
fi

# --- persist matrix.json for render.sh, then print the engine-facing matrix ---
python3 - "$OUT_DIR" "$LIVE" "$DRIFT" "$PR_STATE" "$PR_URL" "$PR_NOTE" \
  "$ART_BYTES" "$ART_SHA" <<'PY'
import json, sys
out, live, drift, pr_state, pr_url, pr_note, nbytes, sha = sys.argv[1:9]
checks = [json.loads(x) for x in open(f"{out}/matrix-checks.jsonl")]
matrix = {
    "checks": checks,
    "live": live,
    "drift": drift == "yes",
    "pr": {"state": pr_state, "url": pr_url, "note": pr_note},
    "artifact_bytes": int(nbytes),
    "artifact_sha256": sha,
}
json.dump(matrix, open(f"{out}/matrix.json", "w"), indent=1)
PY

printf '%s' "$CHECKS_OUT"
echo "live: $LIVE"
echo "drift: $DRIFT"
if [ "$PR_STATE" = failed ]; then
  echo "pr: failed — $PR_NOTE"
elif [ "$PR_STATE" = none ]; then
  echo "pr: none"
else
  echo "pr: $PR_STATE $PR_URL"
fi
PR_OPEN=0
case "$PR_STATE" in opened|updated|open) PR_OPEN=1 ;; esac
DRIFT_N=0; [ "$DRIFT" = yes ] && DRIFT_N=1
echo "metrics: {\"mirror.tests_failed\": $TESTS_FAILED, \"mirror.drift\": $DRIFT_N, \"mirror.pr_open\": $PR_OPEN, \"mirror.artifact_bytes\": $ART_BYTES}"
