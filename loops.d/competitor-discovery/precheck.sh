#!/usr/bin/env bash
# competitor-discovery/precheck.sh — deterministic gathering step (script->agent
# pattern, docs/LOOP_AUTHORING.md section 3/6). Runs the repo's discovery script
# (GitHub search anonymous; Tavily and Firecrawl/Google keys read by the script
# itself from ~/.codex/config.toml and never printed), diffs candidates against
# cache/seen.json and prints ONLY unseen candidates, the top-5 standing list
# and a stats row. Empty stdout (nothing new, no failed query) -> the runner
# skips the engine: zero tokens. The full standing list lives in
# $OUT_DIR/discovery/<date>/candidates.json.
set -uo pipefail

# launchd's PATH lacks homebrew python3 (PyYAML lives there).
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
# GitHub search/enrich under Generalissimo's own ytubecoder gh login when it
# exists on this host (read-only GETs; 30 search calls/min vs 10 anonymous),
# anonymous otherwise. The token is passed by env only and never printed.
GITHUB_TOKEN="$(gh auth token -u ytubecoder 2>/dev/null || true)"
if [ -n "$GITHUB_TOKEN" ]; then export GITHUB_TOKEN; else unset GITHUB_TOKEN; fi

# Reddit evidence comes from RedditGrow's /reddit/search. Only that one key is
# lifted from the repo .env (never the whole file, never printed); without it
# the script marks the Reddit queries failed/config and carries on.
REPO_FOR_KEY="${MAGUYVA_REPO:-$HOME/projects/maguyva-marketing}"
if [ -f "$REPO_FOR_KEY/.env" ]; then
  RG_KEY="$(grep -E '^REDDITGROW_API_KEY=' "$REPO_FOR_KEY/.env" | head -n1 | cut -d= -f2- | tr -d '"'"'"'"' | tr -d '[:space:]')"
  if [ -n "$RG_KEY" ]; then export REDDITGROW_API_KEY="$RG_KEY"; fi
fi

OUT="${OUT_DIR:?OUT_DIR required}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE="$HERE/cache"
mkdir -p "$CACHE" "$OUT/discovery"

REPO="${MAGUYVA_REPO:-$HOME/projects/maguyva-marketing}"
SCRIPT="$REPO/scripts/competitor-discovery.py"
REGISTRY="$REPO/gc-actions/specs/inputs/competitor-registry.yaml"
for f in "$SCRIPT" "$REGISTRY"; do
  if [ ! -f "$f" ]; then
    echo "INPUT GAP: $f not found. Discovery did not run."
    exit 1
  fi
done

if ! python3 "$SCRIPT" --out-root "$OUT/discovery" --max-candidates 40 >"$OUT/inputs-discovery.log" 2>&1; then
  echo "INPUT GAP: competitor-discovery.py exited non-zero (see inputs-discovery.log in the run dir)."
  exit 1
fi

python3 - "$OUT/discovery" "$CACHE/seen.json" <<'PY'
import json, sys, datetime as dt
from pathlib import Path

root, seen_path = Path(sys.argv[1]), Path(sys.argv[2])
runs = sorted(p for p in root.iterdir() if (p / "candidates.json").is_file())
if not runs:
    print("INPUT GAP: discovery wrote no candidates.json.")
    sys.exit(1)
data = json.loads((runs[-1] / "candidates.json").read_text(encoding="utf-8"))
cands = data.get("candidates") or []
queries = data.get("queries") or []
try:
    seen = json.loads(seen_path.read_text(encoding="utf-8"))
    if not isinstance(seen, dict):
        seen = {}
except (OSError, ValueError):
    seen = {}
today = dt.date.today().isoformat()


def key_of(c):
    return (c.get("repo") or c.get("domain") or c.get("name") or "").lower()


def one_line(s, n):
    return " ".join(str(s or "").split())[:n]


lines, new_keys = [], []
for c in cands:
    k = key_of(c)
    if k and k not in seen:
        new_keys.append(k)
        why = "; ".join(c.get("why_surfaced") or [])
        stars = c.get("stars") if c.get("stars") is not None else "n/a"
        lines.append(
            f"NEW CANDIDATE: {c.get('name')} — stars={stars} score={c.get('score')} — "
            f"{one_line(c.get('description'), 160)} — why: {one_line(why, 300)} — "
            f"{c.get('html_url') or c.get('domain') or k}"
        )
failed = [q for q in queries if q.get("status") == "failed"]
ok = [q for q in queries if q.get("status") == "ok"]

out = []
for q in failed:
    out.append(f"SOURCE UNAVAILABLE: {q.get('query')} — {q.get('error') or 'failed'}")
out += lines
if lines or failed:
    top = sorted(cands, key=lambda c: -(c.get("score") or 0))[:5]
    out.append("STANDING TOP 5:")
    for c in top:
        k = key_of(c)
        first = (seen.get(k) or {}).get("first_seen", today)
        out.append(f"- {c.get('name')} stars={c.get('stars')} score={c.get('score')} first_seen={first}")
    out.append(
        f"STATS: candidates={len(cands)} new={len(lines)} queries_ok={len(ok)} queries_failed={len(failed)}"
    )
    print("\n".join(out))

for c in cands:
    k = key_of(c)
    if not k:
        continue
    prev = seen.get(k) or {}
    score = c.get("score") or 0
    seen[k] = {
        "first_seen": prev.get("first_seen", today),
        "best_score": max(score, prev.get("best_score") or 0),
        "name": c.get("name"),
    }
seen_path.write_text(json.dumps(seen, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
exit $?
