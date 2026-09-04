#!/usr/bin/env bash
# gc-health-watch/precheck.sh — THIS SCRIPT IS THE JOB (type=watchdog,
# docs/INTERFACES.md §4.1). It reads one probe, decides pass/fail
# deterministically, and exits non-zero only when a real condition is present.
# The engine is never asked to judge whether the stack is healthy — that is
# already in the probe's findings list. It is only asked to write the alarm
# up when the script has already decided there is one.
#
# Why this loop exists: the X agent was logged out 13 of 31 August days and
# for a 53-hour stretch 2026-08-31 → 09-02; a deleted Chrome ownership marker
# then killed every write for 2026-09-02 with the session healthy. The agent
# itself wrote both conditions into its memory files every hour. Nobody read
# them.
set -euo pipefail

INPUTS="${OUT_DIR:?OUT_DIR required}/inputs"
mkdir -p "$INPUTS"

echo "# gc-health-watch precheck — $(date -u +%Y-%m-%dT%H:%M:%SZ)"

if ! "$LOOPS_ROOT/bin/probe" gc-health-read --out "$INPUTS/gc-health.json" 2>"$INPUTS/gc-health.err"; then
  echo "PROBE TRANSPORT FAILED — could not reach the probe host (llm)."
  echo "This is an input gap and not evidence of health. Do not report the"
  echo "stack as healthy on the strength of a failed probe."
  echo
  sed -n '1,20p' "$INPUTS/gc-health.err" 2>/dev/null || true
  exit 1
fi

python3 - "$INPUTS/gc-health.json" <<'PY'
import json, sys

with open(sys.argv[1]) as fh:
    d = json.load(fh)

print(f"probe generated {d.get('generated_at') or '?'}")
print()

sections = d.get("sections") or {}


def section_error(block):
    if isinstance(block, dict):
        return block.get("error")
    return None


sch = sections.get("schedules") if isinstance(sections.get("schedules"), dict) else {}
rows = sch.get("rows") or []
excluded = sch.get("excluded") or []
print(f"## schedules ({len(rows)} rows examined · {len(excluded)} excluded by policy)")
err = section_error(sch)
if err:
    print(f"  ERROR: {err}")
else:
    for row in rows:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "")
        name = str(row.get("name") or "")
        last_ok = row.get("last_ok") or "-"
        line = f"  {status:<10} {name:<36} last ok {last_ok}"
        if status == "overdue":
            line += f"  expected {row.get('schedule') or '-'}"
        print(line)
    parts = []
    for item in excluded:
        if isinstance(item, dict):
            n = item.get("name") or ""
            r = item.get("reason") or ""
            parts.append(f"{n} ({r})" if r else n)
        else:
            parts.append(str(item))
    if parts:
        print("  excluded: " + "; ".join(parts))
print()

ot = sections.get("opentwins") if isinstance(sections.get("opentwins"), dict) else {}
print("## opentwins")
err = section_error(ot)
if err:
    print(f"  ERROR: {err}")
else:
    ses = ot.get("session") if isinstance(ot.get("session"), dict) else {}
    if ses:
        print(
            f"  session: {ses.get('state')} since {ses.get('since')} UTC "
            f"(as of {ses.get('as_of')} UTC, {ses.get('consecutive')} consecutive)"
        )
    for day in ot.get("launches") or []:
        if not isinstance(day, dict):
            continue
        print(
            f"  launches {day.get('day')} UTC: runs {day.get('runs')} · "
            f"launched {day.get('launched')} · quit {day.get('quit')} · "
            f"deferred {day.get('deferred')} · cdp-errors {day.get('cdp_errors')}"
        )
    tasks = ot.get("tasks") if isinstance(ot.get("tasks"), dict) else {}
    if tasks:
        counts = tasks.get("counts") if isinstance(tasks.get("counts"), dict) else {}
        print(
            f"  tasks {tasks.get('for_date')}: done {counts.get('done', 0)} · "
            f"failed {counts.get('failed', 0)} · pending {counts.get('pending', 0)} · "
            f"typedLen:0 in {tasks.get('typed_len_zero', 0)}"
        )
print()

pz = sections.get("postiz") if isinstance(sections.get("postiz"), dict) else {}
print("## postiz")
err = section_error(pz)
if err:
    print(f"  ERROR: {err}")
else:
    bits = []
    for integ in pz.get("integrations") or []:
        if not isinstance(integ, dict):
            continue
        ident = integ.get("identifier") or "?"
        bits.append(f"{ident} {'disabled' if integ.get('disabled') else 'ok'}")
    print("  integrations: " + (" · ".join(bits) if bits else "(none)"))
    posts = pz.get("posts") if isinstance(pz.get("posts"), dict) else {}
    total = posts.get("total", 0)
    extra = " (queue empty)" if not total else ""
    print(f"  posts last {posts.get('window_days', 14)}d: {total} total{extra}")
print()

findings = d.get("findings") or []
print(f"## findings ({len(findings)})")
for f in findings:
    if not isinstance(f, dict):
        continue
    sev = str(f.get("severity") or "").upper()
    fid = f.get("id") or ""
    detail = f.get("detail") or ""
    print(f"[{sev}] {fid} — {detail}")

raise SystemExit(1 if findings else 0)
PY
