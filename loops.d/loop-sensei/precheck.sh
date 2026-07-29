#!/usr/bin/env bash
# loop-sensei/precheck.sh — deterministic fleet-health inventory (script->agent
# pattern, docs/LOOP_AUTHORING.md §2 q3). Examines the OTHER loops' latest runs
# in sqlite and, for each one that failed, gathers the evidence the engine will
# diagnose from: run row fields, recent history, and capped tails of the run
# dir artifacts. Zero network, zero judgment. Empty stdout when the fleet is
# healthy -> skipped-precheck, engine never invoked, zero tokens (§6 discipline).
#
# Identity is computed HERE, not by the model: each failing loop's block carries
# a precomputed `finding_id: <loop>:<class>` the engine must copy verbatim —
# same doctrine as "metrics a precheck can compute are computed there".
#
# The runner cd's into this loop dir and passes LOOPS_ROOT (bin/run-loop.sh:542).
set -euo pipefail

ROOT="${LOOPS_ROOT:?LOOPS_ROOT required}"
DB="$ROOT/state/loops.sqlite"
[ -f "$DB" ] || exit 0  # no db yet: nothing to examine -> silent skip

python3 - "$ROOT" <<'PY'
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

ROOT = sys.argv[1]
SELF = "loop-sensei"

# The seven §4.3 failure statuses + the died pseudo-class (INTERFACES §4.6 rule:
# finished_at IS NULL past timeout_s + 120s). skips/overlaps are NOT failures.
FAILURE_STATUSES = {
    "precheck-failed", "engine-failed", "engine-timeout", "auth-failed",
    "tool-denied", "contract-violation", "harness-error",
}
DIED_GRACE_S = 120
MAX_DETAILED = 8          # cap detailed blocks; anything beyond is counted, never hidden
TAIL_LINES = 40
TAIL_BYTES = 4096

now = datetime.now(timezone.utc)


def parse_ts(ts):
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def conf_timeout_s(loop_dir):
    try:
        with open(os.path.join(loop_dir, "loop.conf")) as f:
            for line in f:
                m = re.match(r"\s*timeout_s\s*=\s*(\d+)", line)
                if m:
                    return int(m.group(1))
    except OSError:
        pass
    return 900


def tail(path, max_lines=TAIL_LINES, max_bytes=TAIL_BYTES):
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if b"\x00" in data:
        return "(binary — not shown)"
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()[-max_lines:]
    out = "\n".join(lines)
    if len(out.encode()) > max_bytes:
        out = out.encode()[-max_bytes:].decode("utf-8", errors="replace")
        out = "…" + out
    return out


loops = sorted(
    d for d in os.listdir(os.path.join(ROOT, "loops.d"))
    if os.path.isdir(os.path.join(ROOT, "loops.d", d)) and d != SELF
)

conn = sqlite3.connect(f"file:{os.path.join(ROOT, 'state', 'loops.sqlite')}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA busy_timeout=5000")

failing = []   # (loop, class, row)
checked = 0
for name in loops:
    row = conn.execute(
        "SELECT * FROM runs WHERE loop_name=? ORDER BY started_at DESC LIMIT 1", (name,)
    ).fetchone()
    if row is None:
        continue  # never run: not a failure, just young
    checked += 1
    status = row["runner_status"]
    if status in FAILURE_STATUSES:
        failing.append((name, status, row))
    elif row["finished_at"] is None:
        started = parse_ts(row["started_at"])
        limit = conf_timeout_s(os.path.join(ROOT, "loops.d", name)) + DIED_GRACE_S
        if started and (now - started) > timedelta(seconds=limit):
            failing.append((name, "died", row))

died_count = sum(1 for _, cls, _ in failing if cls == "died")

if not failing:
    sys.exit(0)  # healthy fleet: EMPTY stdout -> skipped-precheck, no engine

print(f"fleet check {now.strftime('%Y-%m-%dT%H:%M:%SZ')} — "
      f"{len(failing)} of {checked} loops failing (latest run)")
print()

for name, cls, row in failing[:MAX_DETAILED]:
    run_id = row["run_id"]
    run_dir = os.path.join(ROOT, "state", "runs", run_id)
    print(f"== loop: {name}")
    print(f"finding_id: {name}:{cls}   <- copy this VERBATIM; never derive your own")
    print(f"class: {cls}")
    print(f"run_id: {run_id}")
    print(f"engine: {row['engine']}  model: {row['model'] or '(default)'}  "
          f"trigger: {row['trigger']}  attempts: {row['attempts']}")
    print(f"started: {row['started_at']}  finished: {row['finished_at'] or 'NEVER'}")
    print(f"exit_code: {row['exit_code']}  error_detail: {row['error_detail'] or '(none)'}")
    hist = conn.execute(
        "SELECT runner_status FROM runs WHERE loop_name=? ORDER BY started_at DESC LIMIT 6",
        (name,),
    ).fetchall()
    print("recent runner_status (newest first): " + ", ".join(h[0] for h in hist))
    for artifact in ("engine.status", "engine.log", "precheck.out"):
        content = tail(os.path.join(run_dir, artifact))
        if content is None:
            print(f"-- {artifact}: (absent)")
        else:
            print(f"-- {artifact} (tail):")
            print(content)
    print()

if len(failing) > MAX_DETAILED:
    rest = ", ".join(f"{n}:{c}" for n, c, _ in failing[MAX_DETAILED:])
    print(f"NOT DETAILED ({len(failing) - MAX_DETAILED} more, evidence capped): {rest}")
    print()

metrics = {
    "fleet.loops_checked": checked,
    "fleet.failing": len(failing),
    "fleet.died": died_count,
}
print("metrics to copy verbatim into the contract metrics string:")
print(json.dumps(metrics, sort_keys=True))
PY
