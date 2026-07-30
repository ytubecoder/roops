#!/usr/bin/env bash
# kagi-ban precheck — trusted deterministic gathering (script→agent pattern).
# Runs the Automic Vault scanner READ-ONLY, diffs against the committed
# baseline, emits a digest for the engine. All counts are computed HERE
# (model-emitted metrics get believed — house gotcha).
set -euo pipefail

AV_BIN="${AV_BIN:-/Applications/Automic Vault.app/Contents/MacOS/av}"

if [ ! -x "$AV_BIN" ]; then
  echo "ERROR: av binary not found at $AV_BIN" >&2
  exit 1
fi

# The audit's world-view must not depend on the trigger context (launchd's
# minimal PATH silently dropped 4 path-hygiene exposures on 2026-07-30).
# Pin to the login-shell PATH — the surface the user actually lives in.
AUDIT_PATH="$(/bin/zsh -l -c 'printf %s "$PATH"' 2>/dev/null | tail -n 1)"
case "$AUDIT_PATH" in
  */bin*) export PATH="$AUDIT_PATH" ;;
  *) echo "WARN: could not resolve login-shell PATH; using inherited PATH" >&2 ;;
esac

AV_VERSION="$("$AV_BIN" --version 2>/dev/null | head -n1 || echo unknown)"
"$AV_BIN" scan --json > "$OUT_DIR/scan.json"

mkdir -p "$OUT_DIR/loop-data.commit"

python3 - "$OUT_DIR/scan.json" "$LOOPS_ROOT/state/loop-data/kagi-ban/scan-prev.json" \
  "$OUT_DIR/loop-data.commit/scan-prev.json" "$AV_VERSION" <<'PY'
import hashlib
import json
import shutil
import sys

scan_path, prev_path, commit_path, av_version = sys.argv[1:5]

def current_findings(path):
    try:
        with open(path) as f:
            return json.load(f).get("findings") or []
    except (OSError, ValueError) as exc:
        print(f"ERROR: current scan JSON unparseable: {exc}", file=sys.stderr)
        sys.exit(1)

def previous_findings(path):
    try:
        with open(path) as f:
            return json.load(f).get("findings") or []
    except (OSError, ValueError):
        return []

def keys(findings):
    """finding key: av:<source>:<sha8 of sorted affected paths> (no line
    numbers — volatile identity is forbidden, LOOP_AUTHORING §2)."""
    out = {}
    for item in findings:
        paths = sorted(a.get("path") or "" for a in item.get("affected") or [])
        digest = hashlib.sha256("|".join(paths).encode()).hexdigest()[:8]
        key = f"av:{item.get('source')}:{digest}"
        out[key] = {"source": item.get("source"), "severity": item.get("severity"),
                    "paths": paths}
    return out

current = keys(current_findings(scan_path))
previous = keys(previous_findings(prev_path))
new = sorted(set(current) - set(previous))
resolved = sorted(set(previous) - set(current))
ongoing = sorted(set(current) & set(previous))
sev_high = sum(1 for v in current.values() if v["severity"] in ("high", "critical"))
sev_med = sum(1 for v in current.values() if v["severity"] == "medium")
first_run = not previous

print(f"av_version: {av_version}")
print(f"counts: total={len(current)} high={sev_high} medium={sev_med} "
      f"new={len(new)} resolved={len(resolved)} ongoing={len(ongoing)} "
      f"first_run={'yes' if first_run else 'no'}")
print()
print("CURRENT EXPOSURES (finding_id | severity | source | paths) — the engine")
print("re-emits EVERY line below as a finding with exactly this finding_id:")
for key in sorted(current):
    item = current[key]
    label = "NEW" if key in new else "ONGOING"
    print(f"{label} {key} | {item['severity']} | {item['source']} | {';'.join(item['paths'])}")
for key in resolved:
    item = previous[key]
    print(f"RESOLVED {key} | was {item['severity']} | {item['source']}")

shutil.copyfile(scan_path, commit_path)
PY
