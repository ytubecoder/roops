#!/usr/bin/env bash
# competitor-keywords/precheck.sh — deterministic step (script->agent pattern,
# docs/LOOP_AUTHORING.md §3/§6). Runs the read-only Keyword Planner scan,
# diffs it against cache/summary.json and prints ONLY the movement plus a
# 5-line standing summary. The engine never touches Google Ads.
#
# Credentials: the Google Ads OAuth env comes from $MAGUYVA_REPO/.env, sourced
# into this process only. No value is ever echoed; scan output goes to a log
# file under $OUT_DIR and only its last lines are shown on failure.
set -uo pipefail

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
OUT_DIR="${OUT_DIR:?OUT_DIR required}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE="$HERE/cache"
mkdir -p "$CACHE"
MAGUYVA_REPO="${MAGUYVA_REPO:-$HOME/projects/maguyva-marketing}"
PY="$MAGUYVA_REPO/growth-console/.venv/bin/python"
SCAN_OUT="$OUT_DIR/keyword-scan"

if [ ! -f "$MAGUYVA_REPO/.env" ]; then
  echo "INPUT GAP: $MAGUYVA_REPO/.env not found. The Google Ads credentials are unavailable; no scan ran."
  exit 1
fi
if [ ! -x "$PY" ]; then
  echo "INPUT GAP: venv python not found at $PY. No scan ran."
  exit 1
fi

mkdir -p "$OUT_DIR"
set -a
# shellcheck disable=SC1091
source "$MAGUYVA_REPO/.env"
set +a

if ! "$PY" "$MAGUYVA_REPO/google-ads-tools/competitor-keyword-scan.py" \
      --out "$SCAN_OUT" >"$OUT_DIR/scan.log" 2>&1 || [ ! -s "$SCAN_OUT/results.json" ]; then
  echo "INPUT GAP: competitor-keyword-scan.py failed; last log lines follow."
  tail -n 8 "$OUT_DIR/scan.log" | cut -c1-300
  exit 1
fi

"$PY" - "$SCAN_OUT/results.json" "$CACHE/summary.json" "$MAGUYVA_REPO/google-ads-tools" "${RUN_ID:-unknown}" <<'PY'
import json
import sys
from pathlib import Path

results_path, cache_path, lib_dir, run_id = sys.argv[1:5]
sys.path.insert(0, lib_dir)
from competitor_keyword_lib import (  # noqa: E402
    compute_gap,
    merge_keywords,
    multi_competitor_gap,
    normalize,
)

BRAND_MOVE_PCT = 25.0
BRAND_MOVE_MIN_ABS = 20  # ignore bucket noise on tiny volumes
SAMPLE = 10

res = json.loads(Path(results_path).read_text())
comps = res["competitors"]
gap = multi_competitor_gap(compute_gap(comps, res["us"]["seeds"]))
gap_now = {normalize(g["text"]): int(g["avg_monthly_searches"]) for g in gap}
baseline_now = sorted(merge_keywords(res["us"]["seeds"]))
brand_now = {}
brand_terms = {}
for slug, terms in (res.get("brand_metrics") or {}).items():
    brand_terms[slug] = {t: int(m.get("avg_monthly_searches") or 0) for t, m in terms.items()}
    brand_now[slug] = sum(brand_terms[slug].values())
names = {c["slug"]: c["name"] for c in comps}
names.update(res.get("brand_names") or {})
names["us"] = "maguyva (us)"

cache_file = Path(cache_path)
prev = None
if cache_file.exists():
    try:
        prev = json.loads(cache_file.read_text())
    except (OSError, ValueError):
        prev = None

scanned = sum(1 for c in comps if any(not s.get("error") for s in c["seeds"]))
top3 = sorted(brand_now.items(), key=lambda kv: -kv[1])[:3]
summary = [
    f"SUMMARY run: {run_id} (scan {res.get('run_date', '?')})",
    f"SUMMARY competitors scanned: {scanned} of {len(comps)}",
    f"SUMMARY shared gap keywords (3+ competitors, not us): {len(gap_now)}",
    f"SUMMARY our baseline keywords: {len(baseline_now)}",
    "SUMMARY top brand volumes: "
    + (", ".join(f"{names.get(s, s)} {v:,}" for s, v in top3) if top3 else "none (brand metrics unavailable)"),
]

lines = []
if prev is None:
    print("FIRST RUN — baseline established")
    print("\n".join(summary))
    print("TOP SHARED GAP KEYWORDS (first run):")
    for k, v in sorted(gap_now.items(), key=lambda kv: (-kv[1], kv[0]))[:SAMPLE]:
        print(f"  {k} — {v:,}/mo")
else:
    pg = prev.get("gap", {})
    for k, v in sorted(gap_now.items(), key=lambda kv: (-kv[1], kv[0])):
        if k not in pg:
            lines.append(f"NEW GAP KEYWORD: {k} — {v:,}/mo")
    for k, v in sorted(pg.items(), key=lambda kv: (-kv[1], kv[0])):
        if k not in gap_now:
            lines.append(f"DROPPED GAP KEYWORD: {k} — was {v:,}/mo")
    pb = prev.get("brand", {})
    for slug, now in sorted(brand_now.items()):
        old = pb.get(slug)
        if not old or abs(now - old) < BRAND_MOVE_MIN_ABS:
            continue
        pct = (now - old) / old * 100
        if abs(pct) >= BRAND_MOVE_PCT:
            lines.append(
                f"BRAND VOLUME MOVE: {names.get(slug, slug)} ({slug}) {old:,} -> {now:,}/mo ({pct:+.0f}%)"
            )
    ob, nb = set(prev.get("baseline", [])), set(baseline_now)
    if ob != nb:
        added, removed = sorted(nb - ob), sorted(ob - nb)
        lines.append(
            f"BASELINE CHANGE: our pages now attract {len(nb)} keywords (was {len(ob)}); "
            f"{len(added)} added, {len(removed)} removed"
        )
        if added:
            lines.append("  added: " + ", ".join(added[:SAMPLE]))
        if removed:
            lines.append("  removed: " + ", ".join(removed[:SAMPLE]))
    print("\n".join(lines + summary))

cache_file.write_text(
    json.dumps(
        {
            "run_id": run_id,
            "generated_at": res.get("generated_at"),
            "gap": gap_now,
            "brand": brand_now,
            "baseline": baseline_now,
        },
        indent=1,
    )
)
Path(results_path).with_name("diff-metrics.json").write_text(
    json.dumps(
        {
            "gap.shared_count": len(gap_now),
            "baseline.keywords": len(baseline_now),
            "changes.total": sum(1 for ln in lines if not ln.startswith(" ")),
        }
    )
)
PY
exit $?
