#!/usr/bin/env python3
"""dashboard/generate.py — static status dashboard for the loops harness.

python3 dashboard/generate.py [--root R] [--out FILE]

Reads only:
  - state/loops.sqlite   (§3 schema, frozen — queried with raw SQL, no bin/db.py dependency)
  - reports/<name>/latest.json   (suppression-filtered contract; markdown is NEVER parsed)
  - loops.d/<name>/{loop.conf,dashboard.json}   (§5, §9.3)

Writes dashboard/loops.html via tmp-file + os.rename (atomic; never a partially-written page).

Stdlib only. bin/loopconf.py and bin/schedule.py are built concurrently by another agent
against the frozen signatures `loopconf.parse(path) -> (conf, errors)` and
`schedule.parse(spec) -> {kind, launchd, expected_interval_s}`. They are imported lazily
(only when a real loop.conf needs parsing) so this module — and its tests — work even before
those files exist. Callers (and tests) may inject fake parsers via generate()'s parameters.
"""

import argparse
import html as html_lib
import importlib.util
import json
import os
import re
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone

# --------------------------------------------------------------------------------------------
# Lazy-import seam for the concurrently-built modules (bin/loopconf.py, bin/schedule.py)
# --------------------------------------------------------------------------------------------


def _load_module_from_path(path, modname):
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load module {modname} from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _default_loopconf_parse(root):
    """Returns a callable that lazily loads bin/loopconf.py's parse() from the given root on
    its FIRST actual invocation — not when this factory is called. This means generate() can
    be called against a root with zero loops.d entries (e.g. a fresh checkout, or the hermetic
    empty-state case) without ever touching bin/loopconf.py, even if that file doesn't exist
    yet (it's built concurrently by another agent)."""
    _cache = {}

    def _parse(path):
        if "fn" not in _cache:
            mod = _load_module_from_path(
                os.path.join(root, "bin", "loopconf.py"), "_loops_dashboard_loopconf"
            )
            _cache["fn"] = mod.parse
        return _cache["fn"](path)

    return _parse


def _default_schedule_parse(root):
    """Same lazy-on-first-call seam as _default_loopconf_parse, for bin/schedule.py."""
    _cache = {}

    def _parse(spec):
        if "fn" not in _cache:
            mod = _load_module_from_path(
                os.path.join(root, "bin", "schedule.py"), "_loops_dashboard_schedule"
            )
            _cache["fn"] = mod.parse
        return _cache["fn"](spec)

    return _parse


# --------------------------------------------------------------------------------------------
# Pure logic — precedence, staleness, disposition text. Unit-testable without any I/O.
# --------------------------------------------------------------------------------------------

HARNESS_PROBLEM_STATUSES = {
    "auth-failed",
    "tool-denied",
    "contract-violation",
    "harness-error",
}

_STATUS_COLOR = {"ok": "green", "warn": "amber", "alert": "red"}


# Runner statuses that mean "this run did not produce a valid report" (§4.3) —
# these get their why surfaced on the page and, for the latest run, a handoff block.
FAILURE_STATUSES = {
    "precheck-failed",
    "engine-failed",
    "engine-timeout",
    "auth-failed",
    "tool-denied",
    "contract-violation",
    "harness-error",
}


def status_color(effective_status):
    return _STATUS_COLOR.get(effective_status, "grey")


def compute_light(runner_status, effective_status):
    """§4.3 precedence for the displayed status light.

    Returns (color, marker) where color in {green,amber,red,grey} and marker is
    None or "harness-problem".
    """
    if runner_status == "skipped-overlap":
        return "amber", None
    if runner_status == "skipped-precheck":
        return "amber", None
    if runner_status == "completed":
        return status_color(effective_status), None
    if runner_status in HARNESS_PROBLEM_STATUSES:
        return "red", "harness-problem"
    # any other non-completed runner_status (precheck-failed, engine-failed, engine-timeout, ...)
    return "red", None


def _parse_iso(ts):
    if not ts:
        return None
    ts = ts.strip()
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def is_stale(last_started_at, expected_interval_s, now):
    """§10: overdue by > 1.5 x expected_interval_s. expected_interval_s == 0 means manual
    (staleness-exempt)."""
    if not expected_interval_s:
        return False
    last = _parse_iso(last_started_at)
    if last is None:
        return False
    overdue_s = (now - last).total_seconds()
    return overdue_s > 1.5 * expected_interval_s


def is_died(finished_at, started_at, timeout_s, now):
    """§4.6: finished_at IS NULL and started_at older than timeout_s + 120s grace."""
    if finished_at:
        return False
    started = _parse_iso(started_at)
    if started is None:
        return False
    age_s = (now - started).total_seconds()
    return age_s > (timeout_s or 900) + 120


def is_suppressed(action, snooze_until, now):
    """§4.5: current disposition is dismiss, or snooze with snooze_until > now."""
    if action == "dismiss":
        return True
    if action == "snooze":
        until = _parse_iso(snooze_until)
        if until is not None and until > now:
            return True
    return False


def disposition_text(action, note, snooze_until, created_at):
    if not action:
        return ""
    date = (created_at or "")[:10]
    if action == "dismiss":
        note_part = f' ("{note}")' if note else ""
        return f"dismissed {date}{note_part}"
    if action == "snooze":
        until = (snooze_until or "")[:10]
        return f"snoozed until {until}"
    if action == "ack":
        return f"acked {date}"
    if action == "reopen":
        return f"reopened {date}"
    return f"{action} {date}"


def ordinal(n):
    if 10 <= (n % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def truncate_value(s, limit=2048):
    if s is None:
        return ""
    if len(s) <= limit:
        return s
    return s[:limit] + " …[truncated]"


def render_sparkline(points, width=140, height=32):
    """Inline SVG sparkline. No external assets, no JS charting library."""
    points = [p for p in points if p is not None]
    if not points:
        return ""
    lo = min(points)
    hi = max(points)
    span = hi - lo
    n = len(points)
    pad = 2
    usable_w = width - 2 * pad
    usable_h = height - 2 * pad

    def x_at(i):
        if n == 1:
            return pad
        return pad + (usable_w * i / (n - 1))

    def y_at(v):
        if span == 0:
            return pad + usable_h / 2
        return pad + usable_h - ((v - lo) / span) * usable_h

    coords = " ".join(f"{x_at(i):.1f},{y_at(v):.1f}" for i, v in enumerate(points))
    last_x, last_y = x_at(n - 1), y_at(points[-1])
    return (
        f'<svg class="spark" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="trend sparkline">'
        f'<polyline points="{coords}" fill="none" stroke="currentColor" stroke-width="1.6" '
        f'stroke-linejoin="round" stroke-linecap="round" />'
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="2" fill="currentColor" />'
        f"</svg>"
    )


def format_relative(ts, now):
    dt = _parse_iso(ts)
    if dt is None:
        return "never"
    delta = now - dt
    secs = int(delta.total_seconds())
    future = secs < 0
    secs = abs(secs)
    if secs < 60:
        out = f"{secs}s"
    elif secs < 3600:
        out = f"{secs // 60}m"
    elif secs < 86400:
        out = f"{secs // 3600}h"
    else:
        out = f"{secs // 86400}d"
    return f"in {out}" if future else f"{out} ago"


def fmt_num(n):
    if n is None:
        return "0"
    return f"{int(n):,}"


def e(s):
    """HTML-escape."""
    return html_lib.escape("" if s is None else str(s), quote=True)


# --------------------------------------------------------------------------------------------
# Schedule spec parsing for best-effort "next run" (owned here — decoupled from the exact
# internal shape of schedule.py's `launchd` dict, which this module does not need).
# --------------------------------------------------------------------------------------------

_HHMM_RE = re.compile(r"^(\d{2}):(\d{2})$")
_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def next_run_estimate(spec, last_started_at, now):
    """Best-effort next-run estimate from the raw §5.1 schedule spec string. Returns a
    datetime or None if it cannot be estimated (manual, unparseable, or interval with no
    run history yet)."""
    if not spec:
        return None
    spec = spec.strip()
    if spec == "manual":
        return None
    if spec.startswith("interval:"):
        val = spec.split(":", 1)[1]
        m = re.match(r"^(\d+)([smh])$", val)
        if not m:
            return None
        n, unit = int(m.group(1)), m.group(2)
        secs = n * {"s": 1, "m": 60, "h": 3600}[unit]
        last = _parse_iso(last_started_at)
        if last is None:
            return None
        nxt = last
        while nxt <= now:
            nxt = nxt + timedelta(seconds=secs)
        return nxt
    if spec.startswith("daily:"):
        hhmm = spec.split(":", 1)[1]
        return _next_time_of_day(hhmm, now)
    if spec.startswith("times:"):
        times = spec.split(":", 1)[1].split(",")
        candidates = [
            t for t in (_next_time_of_day(t.strip(), now) for t in times) if t
        ]
        return min(candidates) if candidates else None
    if spec.startswith("weekly:"):
        _, day, hhmm = spec.split(":", 2)
        return _next_weekday_time(day.strip().lower(), hhmm, now)
    if spec.startswith("monthly:"):
        _, dom, hhmm = spec.split(":", 2)
        return _next_monthly(int(dom), hhmm, now)
    return None


def _next_time_of_day(hhmm, now):
    m = _HHMM_RE.match(hhmm)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _next_weekday_time(day, hhmm, now):
    if day not in _WEEKDAYS:
        return None
    target_idx = _WEEKDAYS.index(day)
    candidate = _next_time_of_day(hhmm, now)
    if candidate is None:
        return None
    while candidate.weekday() != target_idx or candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _next_monthly(dom, hhmm, now):
    m = _HHMM_RE.match(hhmm)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    year, month = now.year, now.month
    while True:
        try:
            candidate = datetime(year, month, dom, hour, minute, tzinfo=now.tzinfo)
        except ValueError:
            candidate = None
        if candidate is not None and candidate > now:
            return candidate
        month += 1
        if month > 12:
            month = 1
            year += 1


# --------------------------------------------------------------------------------------------
# Data access — raw SQL against the frozen §3 schema. No dependency on bin/db.py.
# --------------------------------------------------------------------------------------------


def _open_db(root):
    db_path = os.path.join(root, "state", "loops.sqlite")
    if not os.path.exists(db_path):
        return None
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _discover_loops(root):
    loops_d = os.path.join(root, "loops.d")
    if not os.path.isdir(loops_d):
        return []
    names = []
    for entry in sorted(os.listdir(loops_d)):
        conf_path = os.path.join(loops_d, entry, "loop.conf")
        if os.path.isfile(conf_path):
            names.append(entry)
    return names


def _latest_run(conn, loop_name):
    if conn is None:
        return None
    row = conn.execute(
        "SELECT * FROM runs WHERE loop_name=? ORDER BY started_at DESC LIMIT 1",
        (loop_name,),
    ).fetchone()
    return dict(row) if row else None


def _recent_runs(conn, loop_name, limit=15):
    if conn is None:
        return []
    rows = conn.execute(
        "SELECT * FROM runs WHERE loop_name=? ORDER BY started_at DESC LIMIT ?",
        (loop_name, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def _spend(conn, loop_name, since_iso):
    if conn is None:
        return 0, 0.0
    row = conn.execute(
        "SELECT COALESCE(SUM(tokens_total),0) AS tok, COALESCE(SUM(cost_usd),0) AS cost "
        "FROM runs WHERE loop_name=? AND started_at>=?",
        (loop_name, since_iso),
    ).fetchone()
    return row["tok"] or 0, row["cost"] or 0.0


def _fleet_spend(conn, since_iso):
    if conn is None:
        return 0, 0.0
    row = conn.execute(
        "SELECT COALESCE(SUM(tokens_total),0) AS tok, COALESCE(SUM(cost_usd),0) AS cost "
        "FROM runs WHERE started_at>=?",
        (since_iso,),
    ).fetchone()
    return row["tok"] or 0, row["cost"] or 0.0


def _open_findings(conn, loop_name):
    if conn is None:
        return []
    rows = conn.execute(
        "SELECT * FROM findings WHERE loop_name=? AND resolved_at IS NULL "
        "ORDER BY last_seen_at DESC",
        (loop_name,),
    ).fetchall()
    return [dict(r) for r in rows]


def _current_disposition(conn, loop_name, finding_id):
    if conn is None:
        return None
    row = conn.execute(
        "SELECT * FROM dispositions WHERE loop_name=? AND finding_id=? "
        "ORDER BY created_at DESC LIMIT 1",
        (loop_name, finding_id),
    ).fetchone()
    return dict(row) if row else None


def _metric_history(conn, loop_name, key, since_iso):
    if conn is None:
        return []
    rows = conn.execute(
        "SELECT ts, num FROM metrics WHERE loop_name=? AND key=? AND ts>=? ORDER BY ts ASC",
        (loop_name, key, since_iso),
    ).fetchall()
    return [(r["ts"], r["num"]) for r in rows]


def _run_metrics(conn, run_id):
    if conn is None or run_id is None:
        return []
    rows = conn.execute(
        "SELECT key, num, text FROM metrics WHERE run_id=? ORDER BY key ASC", (run_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def _latest_heartbeat(conn, loop_name):
    if conn is None:
        return None
    row = conn.execute(
        "SELECT * FROM heartbeats WHERE loop_name=? ORDER BY ts DESC LIMIT 1",
        (loop_name,),
    ).fetchone()
    return dict(row) if row else None


def load_loop_events(conn, limit=15):
    """Fleet-wide most-recent lifecycle events (§3 `loop_events`), newest first.
    Powers the `<section id="recent-events">` strip."""
    if conn is None:
        return []
    rows = conn.execute(
        "SELECT * FROM loop_events ORDER BY ts DESC, id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def _loop_provenance(conn, loop_name):
    """Most recent created/imported event for a loop (Amendment 2). The `event IN (...)`
    filter runs in SQL before the LIMIT so the founding event is never lost behind a run of
    later paused/resumed/etc. rows — same fix as loopctl's `status --json` provenance lookup."""
    if conn is None:
        return None
    row = conn.execute(
        "SELECT * FROM loop_events WHERE loop_name=? AND event IN ('created','imported') "
        "ORDER BY ts DESC, id DESC LIMIT 1",
        (loop_name,),
    ).fetchone()
    return dict(row) if row else None


def _read_json(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------------

CSS = """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 0 0 3rem;
  background: #0b0e14; color: #d7dce3;
  font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
}
a { color: #6fb3ff; }
a:hover { color: #9ccbff; }
h1, h2, h3 { font-weight: 700; letter-spacing: -0.01em; margin: 0; }
.topstrip {
  display: flex; flex-wrap: wrap; gap: 1.25rem; align-items: center;
  background: #11151d; border-bottom: 1px solid #232a36; padding: 0.9rem 1.4rem;
  position: sticky; top: 0; z-index: 5;
}
.topstrip h1 { font-size: 1.05rem; color: #f2f4f8; margin-right: 0.5rem; }
.chip {
  display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.25rem 0.65rem;
  border-radius: 999px; background: #1a2029; font-size: 0.8rem; font-weight: 600;
  border: 1px solid #262e3b;
}
.chip .dot { width: 0.55rem; height: 0.55rem; border-radius: 50%; }
.needs-attention { border-color: #7a3a3a; background: #241416; color: #ff9d9d; }
.spacer { flex: 1; }
.muted { color: #808a99; font-weight: 400; }
main { padding: 1.4rem; max-width: 1400px; margin: 0 auto; }
table.loops { width: 100%; border-collapse: collapse; margin-bottom: 2rem; }
table.loops th {
  text-align: left; font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.04em;
  color: #808a99; padding: 0.4rem 0.7rem; border-bottom: 1px solid #232a36;
}
table.loops td { padding: 0.55rem 0.7rem; border-bottom: 1px solid #171c25; vertical-align: middle; }
table.loops tr:hover td { background: #12161f; }
.light { display: inline-block; width: 0.85rem; height: 0.85rem; border-radius: 50%; margin-right: 0.5rem; vertical-align: -1px; }
.light.green { background: #37d67a; box-shadow: 0 0 8px #37d67a55; }
.light.amber { background: #f2b23c; box-shadow: 0 0 8px #f2b23c55; }
.light.red { background: #ff5c5c; box-shadow: 0 0 8px #ff5c5c55; }
.light.grey { background: #4a5364; }
.badge { display: inline-block; font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.03em; padding: 0.1rem 0.4rem; border-radius: 4px; margin-left: 0.35rem; }
.badge.harness { background: #4a1010; color: #ff9d9d; border: 1px solid #7a3a3a; }
.badge.stale { background: #4a3a10; color: #ffd68a; border: 1px solid #7a5a2a; }
.badge.died { background: #4a1010; color: #ff9d9d; border: 1px solid #7a3a3a; }
.loop-name { font-weight: 700; color: #f2f4f8; }
.loop-name a { color: inherit; text-decoration: none; }
.loop-name a:hover { text-decoration: underline; }
section.loop {
  background: #11151d; border: 1px solid #232a36; border-radius: 10px;
  padding: 1.1rem 1.3rem; margin-bottom: 1.2rem;
}
section.loop h2 { font-size: 1.05rem; color: #f2f4f8; }
section.loop h2 .light { margin-right: 0.6rem; }
.panels { display: flex; flex-wrap: wrap; gap: 0.8rem; margin: 0.9rem 0; }
.panel {
  background: #171c25; border: 1px solid #232a36; border-radius: 8px; padding: 0.7rem 0.9rem;
  min-width: 150px;
}
.panel .title { font-size: 0.72rem; text-transform: uppercase; color: #808a99; letter-spacing: 0.03em; }
.panel .value { font-size: 1.35rem; font-weight: 700; color: #f2f4f8; margin-top: 0.15rem; }
.panel .value.warn { color: #f2b23c; }
.panel .value.alert { color: #ff5c5c; }
.panel .spark { color: #6fb3ff; margin-top: 0.3rem; }
table.list-panel { border-collapse: collapse; font-size: 0.82rem; }
table.list-panel td, table.list-panel th { padding: 0.15rem 0.5rem; }
.findings { margin: 0.9rem 0; }
.finding { padding: 0.5rem 0.7rem; border-radius: 6px; margin-bottom: 0.4rem; background: #171c25; border: 1px solid #232a36; }
.finding.suppressed { opacity: 0.5; background: #12151b; }
.finding .fid { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.78rem; color: #9fb0c3; }
.finding .sev { font-weight: 700; margin-right: 0.4rem; text-transform: uppercase; font-size: 0.7rem; }
.finding .sev.warn { color: #f2b23c; }
.finding .sev.alert { color: #ff5c5c; }
.finding .sev.info { color: #6fb3ff; }
.finding .recurrence { color: #808a99; font-size: 0.8rem; margin-left: 0.4rem; }
.finding .cmd { display: block; margin-top: 0.3rem; font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.76rem; color: #6fb3ff; background: #0b0e14; padding: 0.2rem 0.5rem; border-radius: 4px; }
.runs-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; margin-top: 0.5rem; }
.runs-table th { text-align: left; color: #808a99; font-weight: 600; padding: 0.25rem 0.5rem; border-bottom: 1px solid #232a36; }
.runs-table td { padding: 0.25rem 0.5rem; border-bottom: 1px solid #171c25; }
.fail-detail { color: #ff9d9d; font-size: 0.8rem; }
details.handoff { margin: 0.7rem 0; border: 1px solid #7a3a3a; border-radius: 6px; background: #241416; }
details.handoff summary { cursor: pointer; color: #ff9d9d; font-weight: 600; font-size: 0.85rem; padding: 0.5rem 0.7rem; }
details.handoff .hint { color: #808a99; font-size: 0.75rem; padding: 0 0.7rem 0.4rem; }
details.handoff textarea {
  display: block; width: calc(100% - 1.4rem); margin: 0 0.7rem 0.7rem; height: 9.5rem;
  background: #0b0e14; color: #d7dce3; border: 1px solid #232a36; border-radius: 4px;
  font: 0.76rem/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; padding: 0.5rem; resize: vertical;
}
details.report-drawer summary { cursor: pointer; color: #808a99; font-size: 0.8rem; margin-top: 0.7rem; }
.report-drawer pre { background: #0b0e14; padding: 0.6rem; border-radius: 6px; overflow-x: auto;
  font-size: 0.78rem; white-space: pre-wrap; word-break: break-word; }
details.raw-fallback summary { cursor: pointer; color: #808a99; font-size: 0.8rem; margin-top: 0.7rem; }
.raw-fallback pre { background: #0b0e14; padding: 0.6rem; border-radius: 6px; overflow-x: auto; font-size: 0.76rem; }
.hb { font-size: 0.85rem; margin: 0.5rem 0; }
.hb .light { margin-right: 0.35rem; }
.empty { padding: 3rem; text-align: center; color: #808a99; }
footer { text-align: center; color: #4a5364; font-size: 0.75rem; padding: 1.5rem; }
.tags { margin: 0.2rem 0; }
.tag {
  display: inline-block; font-size: 0.68rem; font-weight: 600; padding: 0.05rem 0.5rem;
  border-radius: 999px; background: #1a2029; border: 1px solid #2c3444; color: #9fb0c3;
  margin-right: 0.3rem;
}
.provenance { font-size: 0.78rem; margin: 0.3rem 0 0.6rem; }
#recent-events {
  padding: 0.7rem 1.4rem; background: #0e1218; border-bottom: 1px solid #1a2029;
}
#recent-events h3 {
  font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; color: #808a99;
  margin-bottom: 0.5rem; font-weight: 700;
}
.events-table { width: 100%; border-collapse: collapse; font-size: 0.8rem; }
.events-table th { text-align: left; color: #808a99; font-weight: 600; padding: 0.2rem 0.6rem; }
.events-table td { padding: 0.2rem 0.6rem; border-bottom: 1px solid #171c25; }
.filter-bar { margin-bottom: 0.8rem; font-size: 0.82rem; color: #808a99; }
.filter-bar select {
  background: #171c25; color: #d7dce3; border: 1px solid #232a36; border-radius: 4px;
  padding: 0.25rem 0.5rem; font: inherit; margin-left: 0.4rem;
}
"""

DASHBOARD_JS = """
function loopsFilterByTag(tag) {
  document.querySelectorAll('[data-tags]').forEach(function (el) {
    if (!tag) { el.style.display = ''; return; }
    var tags = (el.getAttribute('data-tags') || '').split(' ');
    el.style.display = tags.indexOf(tag) > -1 ? '' : 'none';
  });
}
"""


def _render_tag_chips(tags):
    if not tags:
        return ""
    return (
        '<div class="tags">'
        + "".join(f'<span class="tag">{e(t)}</span>' for t in tags)
        + "</div>"
    )


def _data_tags_attr(tags):
    if not tags:
        return ""
    return f' data-tags="{e(" ".join(tags))}"'


def _event_source_skill(detail):
    """Pulls `source_skill` out of an event's opaque detail JSON, if present. Detail is
    imported-file-derived text -- callers must still HTML-escape whatever this returns."""
    if not detail:
        return None
    try:
        data = json.loads(detail)
    except (TypeError, ValueError):
        return None
    if isinstance(data, dict):
        val = data.get("source_skill")
        if val:
            return str(val)
    return None


def _render_provenance(prov):
    """Per-loop provenance line for the most recent created/imported event:
    `<event> from <source> by <actor>, <date>` when detail carries a source_skill,
    else `<event> by <actor>, <date>`. No qualifying event -> no line."""
    if not prov:
        return ""
    event = prov.get("event") or ""
    actor = prov.get("actor") or ""
    date = (prov.get("ts") or "")[:10]
    source = _event_source_skill(prov.get("detail"))
    if source:
        text = f"{event} from {source} by {actor}, {date}"
    else:
        text = f"{event} by {actor}, {date}"
    return f'<div class="provenance muted">{e(text)}</div>'


def _render_events_strip(events, now):
    """Fleet-wide `<section id="recent-events">` — last N loop_events rows, newest first.
    Zero events still renders the section with an explicit empty-state line."""
    if not events:
        body = '<p class="muted">no lifecycle events yet</p>'
    else:
        rows = []
        for ev in events:
            source = _event_source_skill(ev.get("detail"))
            detail_text = e(source) if source else ""
            rows.append(
                "<tr>"
                f"<td>{e(format_relative(ev['ts'], now))} "
                f'<span class="muted">({e(ev["ts"])})</span></td>'
                f"<td>{e(ev['loop_name'])}</td>"
                f"<td>{e(ev['event'])}</td>"
                f"<td>{e(ev['actor'])}</td>"
                f"<td>{detail_text}</td>"
                "</tr>"
            )
        body = (
            '<table class="events-table"><thead><tr><th>When</th><th>Loop</th>'
            "<th>Event</th><th>Actor</th><th>Detail</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )
    return (
        f'<section id="recent-events"><h3>Recent lifecycle events</h3>{body}</section>'
    )


def _light_html(color, marker=None, extra_badges=()):
    out = f'<span class="light {color}" title="{e(color)}"></span>'
    if marker == "harness-problem":
        out += '<span class="badge harness">harness</span>'
    for b in extra_badges:
        out += f'<span class="badge {e(b.lower())}">{e(b)}</span>'
    return out


def _render_panel_number(panel, metric_row, now, conn=None, loop_name=None):
    val = None
    is_held = False
    if metric_row is not None:
        val = metric_row.get("num")
        if val is None and metric_row.get("text") is not None:
            try:
                val = json.loads(metric_row["text"])
            except (TypeError, json.JSONDecodeError):
                val = metric_row.get("text")
    missing = panel.get("missing", "gap")
    key = panel.get("metric", "")
    if (
        val is None
        and missing == "hold"
        and conn is not None
        and loop_name is not None
        and key
    ):
        # §9.3: "hold" carries the previous value forward, marked stale.
        prior = conn.execute(
            "SELECT num, text FROM metrics WHERE loop_name=? AND key=? AND num IS NOT NULL "
            "ORDER BY ts DESC LIMIT 1",
            (loop_name, key),
        ).fetchone()
        if prior is not None:
            val = prior["num"]
            is_held = True

    cls = ""
    thresholds = panel.get("thresholds") or {}
    if isinstance(val, (int, float)):
        if "alert" in thresholds and _breaches(
            val, thresholds["alert"], panel.get("direction")
        ):
            cls = "alert"
        elif "warn" in thresholds and _breaches(
            val, thresholds["warn"], panel.get("direction")
        ):
            cls = "warn"
    if val is None:
        display = "—"
    elif isinstance(val, float) and val.is_integer():
        display = fmt_num(val)
    elif isinstance(val, (int, float)):
        display = str(val)
    else:
        display = e(str(val))
    unit = panel.get("unit", "")
    held_badge = (
        '<span class="badge stale" title="carried forward — no fresh value this run">hold</span>'
        if is_held
        else ""
    )
    return (
        f'<div class="panel"><div class="title">{e(panel.get("title", panel.get("metric", "")))}</div>'
        f'<div class="value {cls}">{display}{" " + e(unit) if unit and val is not None else ""}'
        f"{held_badge}</div></div>"
    )


def _breaches(val, threshold, direction):
    if direction == "lower_is_worse":
        return val <= threshold
    # default / higher_is_worse / neutral treated as >=
    return val >= threshold


def _render_panel_trend(panel, conn, loop_name, now):
    key = panel.get("metric", "")
    window_days = panel.get("window_days", 30)
    since = (now - timedelta(days=window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    history = _metric_history(conn, loop_name, key, since)
    values = [v for _, v in history if v is not None]
    spark = render_sparkline(values)
    last = values[-1] if values else None
    display = fmt_num(last) if isinstance(last, (int, float)) else "—"
    return (
        f'<div class="panel"><div class="title">{e(panel.get("title", key))}</div>'
        f'<div class="value">{display}</div>{spark}</div>'
    )


def _render_panel_table_or_list(panel, metric_row):
    title = panel.get("title", panel.get("metric", ""))
    if metric_row is None:
        return f'<div class="panel"><div class="title">{e(title)}</div><div class="value">—</div></div>'
    try:
        data = json.loads(metric_row["text"]) if metric_row.get("text") else None
    except (TypeError, json.JSONDecodeError):
        data = None
    if not isinstance(data, list):
        return f'<div class="panel"><div class="title">{e(title)}</div><div class="value">—</div></div>'
    if panel.get("type") == "table" and data and isinstance(data[0], dict):
        cols = []
        for row in data:
            for k in row:
                if k not in cols:
                    cols.append(k)
        thead = "".join(f"<th>{e(c)}</th>" for c in cols)
        trs = "".join(
            "<tr>" + "".join(f"<td>{e(row.get(c, ''))}</td>" for c in cols) + "</tr>"
            for row in data
        )
        table_html = f'<table class="list-panel"><thead><tr>{thead}</tr></thead><tbody>{trs}</tbody></table>'
    else:
        table_html = "<ul>" + "".join(f"<li>{e(item)}</li>" for item in data) + "</ul>"
    return f'<div class="panel"><div class="title">{e(title)}</div>{table_html}</div>'


def _render_panels(dashboard_json, conn, loop_name, run_metrics_by_key, now):
    if not dashboard_json or not dashboard_json.get("panels"):
        return "", set()
    out = []
    declared_keys = set()
    for panel in dashboard_json["panels"]:
        metric_key = panel.get("metric", "")
        declared_keys.add(metric_key)
        ptype = panel.get("type", "number")
        if ptype == "trend":
            out.append(_render_panel_trend(panel, conn, loop_name, now))
        elif ptype in ("table", "list"):
            out.append(
                _render_panel_table_or_list(panel, run_metrics_by_key.get(metric_key))
            )
        else:
            out.append(
                _render_panel_number(
                    panel,
                    run_metrics_by_key.get(metric_key),
                    now,
                    conn=conn,
                    loop_name=loop_name,
                )
            )
    return f'<div class="panels">{"".join(out)}</div>', declared_keys


def _render_raw_fallback(run_metrics, declared_keys, report_href):
    undeclared = [m for m in run_metrics if m["key"] not in declared_keys]
    if not undeclared:
        return ""
    lines = []
    for m in undeclared:
        if m.get("num") is not None:
            val = fmt_num(m["num"]) if float(m["num"]).is_integer() else str(m["num"])
        else:
            val = truncate_value(m.get("text") or "", 2048)
        lines.append(f"{e(m['key'])}: {e(val)}")
    body = "\n".join(lines)
    link = f' — <a href="{e(report_href)}">full report</a>' if report_href else ""
    return (
        '<details class="raw-fallback" open><summary>Other metrics'
        f"{link}</summary><pre>{body}</pre></details>"
    )


def _render_findings(conn, loop_name, latest_json, now):
    findings = _open_findings(conn, loop_name)
    if not findings:
        return ""
    latest_by_id = {}
    if latest_json and isinstance(latest_json.get("findings"), list):
        latest_by_id = {f.get("finding_id"): f for f in latest_json["findings"]}
    out = []
    for f in findings:
        fid = f["finding_id"]
        disp = _current_disposition(conn, loop_name, fid)
        action = disp["action"] if disp else None
        note = disp.get("note") if disp else None
        snooze_until = disp.get("snooze_until") if disp else None
        created_at = disp.get("created_at") if disp else None
        suppressed = is_suppressed(action, snooze_until, now)
        dtext = disposition_text(action, note, snooze_until, created_at)
        recurrence = f"{ordinal(f['times_seen'])} report"
        if dtext:
            recurrence += f" · {dtext}"
        title = f["title"]
        severity = f["severity"]
        detail = ""
        live = latest_by_id.get(fid)
        if live:
            detail = live.get("detail", "")
            title = live.get("title", title)
            severity = live.get("severity", severity)
        cls = "finding suppressed" if suppressed else "finding"
        cmd = ""
        if not suppressed:
            cmd = (
                f'<code class="cmd">loopctl dismiss {e(loop_name)} {e(fid)} '
                f'--note "…"</code>'
            )
        else:
            cmd = f'<code class="cmd">loopctl reopen {e(loop_name)} {e(fid)}</code>'
        detail_html = f"<div>{e(detail)}</div>" if detail else ""
        out.append(
            f'<div class="{cls}"><span class="sev {e(severity)}">{e(severity)}</span>'
            f'<span class="fid">{e(fid)}</span> — {e(title)}'
            f'<span class="recurrence">{e(recurrence)}</span>{detail_html}{cmd}</div>'
        )
    return f'<div class="findings"><h3>Findings</h3>{"".join(out)}</div>'


def _render_recent_runs(runs, now):
    if not runs:
        return "<p class='muted'>No runs yet.</p>"
    rows = []
    for r in runs:
        color, marker = compute_light(r["runner_status"], r["effective_status"])
        light = _light_html(color, marker)
        detail = ""
        if r["runner_status"] in FAILURE_STATUSES:
            parts = [p for p in (r.get("error_detail"),) if p]
            if r.get("exit_code") is not None:
                parts.append(f"exit {r['exit_code']}")
            if parts:
                detail = f'<span class="fail-detail">{e("; ".join(parts))}</span>'
        rows.append(
            "<tr>"
            f"<td>{light}</td>"
            f"<td>{e(format_relative(r['started_at'], now))}</td>"
            f"<td>{e(r['runner_status'])}</td>"
            f"<td>{e(r.get('effective_status') or '—')}</td>"
            f"<td>{e(r.get('headline') or '')}{detail}</td>"
            "</tr>"
        )
    return (
        '<table class="runs-table"><thead><tr><th></th><th>Started</th><th>Runner status</th>'
        f"<th>Status</th><th>Headline / detail</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _render_loop_row(loop, now):
    color = loop["light_color"]
    marker = loop["light_marker"]
    badges = []
    if loop["stale"]:
        badges.append("stale")
    if loop["died"]:
        badges.append("died")
    light = _light_html(color, marker, badges)
    latest = loop["latest_run"]
    if latest:
        last_run = f'{e(format_relative(latest["started_at"], now))} <span class="muted">({e(latest["started_at"])})</span>'
        headline = e(latest.get("headline") or "")
        if not headline and latest["runner_status"] in FAILURE_STATUSES:
            # failed runs have no headline — surface the why instead of a blank cell
            why = latest.get("error_detail") or latest["runner_status"]
            headline = f'<span class="fail-detail">{e(why)}</span>'
    else:
        last_run = '<span class="muted">never run</span>'
        headline = '<span class="muted">no data</span>'
    next_run = loop["next_run_text"]
    spend_tok, spend_cost = loop["spend_7d"]
    spend_html = fmt_num(spend_tok) + " tok"
    if spend_cost:
        spend_html += f" (${spend_cost:.2f})"
    report_link = ""
    if loop["report_href"]:
        report_link = f'<a href="{e(loop["report_href"])}">latest</a>'
    tags_html = _render_tag_chips(loop["tags"])
    data_tags = _data_tags_attr(loop["tags"])
    return (
        f"<tr{data_tags}>"
        f"<td>{light}</td>"
        f'<td class="loop-name"><a href="#loop-{e(loop["name"])}">{e(loop["name"])}</a>'
        f'<div class="muted">{e(loop["description"])}</div>{tags_html}</td>'
        f"<td>{headline}</td>"
        f"<td>{last_run}</td>"
        f'<td>{e(loop["schedule"])}<div class="muted">{e(next_run)}</div></td>'
        f"<td>{spend_html}</td>"
        f"<td>{report_link}</td>"
        "</tr>"
    )


HANDOFF_TEMPLATE = """In the loops harness checkout at {root}: run {run_id} of loop {name} failed.
runner_status={runner_status}; exit_code={exit_code}; error_detail: {error_detail}
Artifacts: state/runs/{run_id}/ (engine.log, engine.status, precheck.out, prompt.composed.md, usage.json) and loops.d/{name}/ (loop.conf, precheck.sh, prompt.md).
Reference: docs/INTERFACES.md (section 4: runner algorithm + status enum; sections 6-7: engine adapters), docs/ENGINE_PROBES.md for verified engine CLI behavior.
Diagnose the root cause and propose a fix. Harness internals are a frozen contract (docs/INTERFACES.md) - if the fix needs a harness change, say so explicitly rather than patching silently."""


def _render_handoff(loop):
    """Paste-into-an-agent block for the latest run, rendered only when that run failed.
    Deterministic template over sqlite fields — same pattern as the findings dismiss cmd."""
    latest = loop["latest_run"]
    if not latest or latest["runner_status"] not in FAILURE_STATUSES:
        return ""
    text = HANDOFF_TEMPLATE.format(
        root=loop["root"],
        run_id=latest["run_id"],
        name=loop["name"],
        runner_status=latest["runner_status"],
        exit_code=latest.get("exit_code")
        if latest.get("exit_code") is not None
        else "n/a",
        error_detail=latest.get("error_detail") or "(none recorded)",
    )
    return (
        '<details class="handoff" open><summary>Run failed — agent handoff</summary>'
        '<div class="hint">Click the text to select it, then copy and paste into an agent.</div>'
        f'<textarea readonly onclick="this.select()">{e(text)}</textarea></details>'
    )


def _render_report_drawer(latest_json, report_href, clamp_bytes=8192):
    """Inline latest report_markdown, escaped and clamped, collapsed by default.
    The markdown is displayed as text, never parsed; the full-report link stays."""
    if not latest_json:
        return ""
    md = latest_json.get("report_markdown") or ""
    if not md.strip():
        return ""
    clamped = md
    if len(clamped.encode("utf-8", errors="replace")) > clamp_bytes:
        clamped = clamped.encode("utf-8", errors="replace")[:clamp_bytes].decode(
            "utf-8", errors="replace"
        )
        clamped += "\n…[truncated — see full report]"
    link = f' — <a href="{e(report_href)}">full report</a>' if report_href else ""
    return (
        f'<details class="report-drawer"><summary>Report{link}</summary>'
        f"<pre>{e(clamped)}</pre></details>"
    )


def _render_loop_section(loop, conn, now):
    color = loop["light_color"]
    marker = loop["light_marker"]
    badges = []
    if loop["stale"]:
        badges.append("stale")
    if loop["died"]:
        badges.append("died")
    light = _light_html(color, marker, badges)

    latest = loop["latest_run"]
    run_metrics = _run_metrics(conn, latest["run_id"]) if latest else []
    run_metrics_by_key = {m["key"]: m for m in run_metrics}

    panels_html, declared_keys = _render_panels(
        loop["dashboard_json"], conn, loop["name"], run_metrics_by_key, now
    )
    findings_html = _render_findings(conn, loop["name"], loop["latest_json"], now)
    handoff_html = _render_handoff(loop)
    report_drawer_html = _render_report_drawer(loop["latest_json"], loop["report_href"])
    recent_runs_html = _render_recent_runs(loop["recent_runs"], now)
    raw_fallback_html = _render_raw_fallback(
        run_metrics, declared_keys, loop["report_href"]
    )

    hb_html = ""
    if loop["conf"].get("type") == "watchdog":
        hb = loop["heartbeat"]
        if hb:
            hb_color = "green" if hb["ok"] else "red"
            hb_text = "probe ok" if hb["ok"] else "probe failed"
            hb_html = (
                f'<div class="hb">{_light_html(hb_color)}Heartbeat: {e(hb_text)} '
                f"&mdash; {e(format_relative(hb['ts'], now))}</div>"
            )
        else:
            hb_html = '<div class="hb muted">Heartbeat: no probes recorded</div>'

    tags_html = _render_tag_chips(loop["tags"])
    data_tags = _data_tags_attr(loop["tags"])
    provenance_html = _render_provenance(loop.get("provenance"))

    return (
        f'<section class="loop" id="loop-{e(loop["name"])}"{data_tags}>'
        f'<h2>{light}{e(loop["name"])} <span class="muted">{e(loop["description"])}</span></h2>'
        f"{tags_html}"
        f"{provenance_html}"
        f"{hb_html}"
        f"{handoff_html}"
        f"{panels_html}"
        f"{findings_html}"
        f"{report_drawer_html}"
        f"<h3>Recent runs</h3>{recent_runs_html}"
        f"{raw_fallback_html}"
        "</section>"
    )


# --------------------------------------------------------------------------------------------
# Main pipeline
# --------------------------------------------------------------------------------------------


def _resolve_loop(root, name, conn, loopconf_parse, schedule_parse, now):
    conf_path = os.path.join(root, "loops.d", name, "loop.conf")
    conf, errors = loopconf_parse(conf_path)

    dashboard_json_path = os.path.join(root, "loops.d", name, "dashboard.json")
    dashboard_json = _read_json(dashboard_json_path)

    latest_json_path = os.path.join(root, "reports", name, "latest.json")
    latest_json = _read_json(latest_json_path)

    report_href = None
    if os.path.isfile(os.path.join(root, "reports", name, "latest.md")):
        report_href = f"../reports/{name}/latest.md"

    latest_run = _latest_run(conn, name)
    recent_runs = _recent_runs(conn, name)
    heartbeat = _latest_heartbeat(conn, name)
    tags = conf.get("tags") or []
    provenance = _loop_provenance(conn, name)

    timeout_s = conf.get("timeout_s", 900)
    schedule_spec = conf.get("schedule", "manual")
    try:
        sched = schedule_parse(schedule_spec)
        expected_interval_s = sched.get("expected_interval_s", 0)
    except Exception:  # noqa: BLE001 — §10: a bad schedule must degrade, never crash the page
        expected_interval_s = 0

    died = False
    if latest_run is not None:
        died = is_died(
            latest_run.get("finished_at"), latest_run["started_at"], timeout_s, now
        )

    stale = False
    if latest_run is not None and not died:
        stale = is_stale(latest_run["started_at"], expected_interval_s, now)
    elif latest_run is None:
        stale = False  # no run history yet — nothing to compare against

    if died:
        light_color, light_marker = "red", "harness-problem"
    elif latest_run is not None and latest_run.get("finished_at") is None:
        # in-flight, not yet past the died threshold
        light_color, light_marker = "grey", None
    elif latest_run is not None:
        light_color, light_marker = compute_light(
            latest_run["runner_status"], latest_run.get("effective_status")
        )
    else:
        light_color, light_marker = "grey", None

    since_7d = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    spend_7d = _spend(conn, name, since_7d)

    next_run_dt = next_run_estimate(
        schedule_spec, latest_run["started_at"] if latest_run else None, now
    )
    next_run_text = (
        format_relative(next_run_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), now)
        if next_run_dt
        else "—"
    )

    needs_attention = (light_color in ("amber", "red")) or stale or died

    return {
        "name": name,
        "root": root,
        "conf": conf,
        "conf_errors": errors,
        "description": conf.get("description", ""),
        "schedule": schedule_spec,
        "dashboard_json": dashboard_json,
        "latest_json": latest_json,
        "report_href": report_href,
        "latest_run": latest_run,
        "recent_runs": recent_runs,
        "heartbeat": heartbeat,
        "tags": tags,
        "provenance": provenance,
        "died": died,
        "stale": stale,
        "light_color": light_color,
        "light_marker": light_marker,
        "spend_7d": spend_7d,
        "next_run_text": next_run_text,
        "needs_attention": needs_attention,
    }


def generate(
    root=None, out_file=None, loopconf_parse=None, schedule_parse=None, now=None
):
    """Generates dashboard/loops.html. Writes via tmp-file + os.rename (atomic)."""
    root = root or os.environ.get("LOOPS_ROOT") or os.getcwd()
    root = os.path.abspath(root)
    out_file = out_file or os.path.join(root, "dashboard", "loops.html")
    now = now or datetime.now(timezone.utc)

    _loopconf_parse = loopconf_parse or _default_loopconf_parse(root)
    _schedule_parse = schedule_parse or _default_schedule_parse(root)

    conn = _open_db(root)
    try:
        names = _discover_loops(root)
        loops = [
            _resolve_loop(root, name, conn, _loopconf_parse, _schedule_parse, now)
            for name in names
        ]

        counts = {"green": 0, "amber": 0, "red": 0, "grey": 0}
        for loop in loops:
            counts[loop["light_color"]] = counts.get(loop["light_color"], 0) + 1
        needs_attention_count = sum(1 for loop in loops if loop["needs_attention"])

        since_today = now.strftime("%Y-%m-%dT00:00:00Z")
        since_7d = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        spend_today = _fleet_spend(conn, since_today)
        spend_7d_fleet = _fleet_spend(conn, since_7d)
        events = load_loop_events(conn, limit=15)

        html = _render_page(
            loops,
            counts,
            needs_attention_count,
            spend_today,
            spend_7d_fleet,
            now,
            conn,
            events,
        )
    finally:
        if conn is not None:
            conn.close()

    _atomic_write(out_file, html)
    return out_file


def _render_page(
    loops, counts, needs_attention_count, spend_today, spend_7d_fleet, now, conn, events
):
    top_chips = "".join(
        f'<span class="chip"><span class="dot" style="background:'
        f'{_color_hex(c)}"></span>{c} {n}</span>'
        for c, n in counts.items()
        if n
    )
    na_chip = (
        f'<span class="chip needs-attention">needs attention {needs_attention_count}</span>'
        if needs_attention_count
        else '<span class="chip">needs attention 0</span>'
    )
    spend_today_html = fmt_num(spend_today[0]) + " tok"
    if spend_today[1]:
        spend_today_html += f" (${spend_today[1]:.2f})"
    spend_7d_html = fmt_num(spend_7d_fleet[0]) + " tok"
    if spend_7d_fleet[1]:
        spend_7d_html += f" (${spend_7d_fleet[1]:.2f})"

    top = (
        '<div class="topstrip"><h1>loops</h1>'
        f"{top_chips}{na_chip}"
        f'<span class="chip">spend today {e(spend_today_html)}</span>'
        f'<span class="chip">spend 7d {e(spend_7d_html)}</span>'
        '<span class="spacer"></span>'
        f'<span class="muted">regenerated {e(now.strftime("%Y-%m-%dT%H:%M:%SZ"))}</span>'
        "</div>"
    )

    events_html = _render_events_strip(events, now)

    if not loops:
        body = (
            f"{events_html}"
            '<main><div class="empty">No loops configured yet.</div></main>'
        )
        return _wrap_html(top, body)

    tag_options = sorted({t for loop in loops for t in loop["tags"]})
    filter_html = ""
    if tag_options:
        opts = '<option value="">all tags</option>' + "".join(
            f'<option value="{e(t)}">{e(t)}</option>' for t in tag_options
        )
        filter_html = (
            '<div class="filter-bar"><label>Filter by tag'
            f'<select id="tag-filter" onchange="loopsFilterByTag(this.value)">{opts}</select>'
            "</label></div>"
        )

    global_rows = "".join(_render_loop_row(loop, now) for loop in loops)
    global_table = (
        '<table class="loops"><thead><tr><th></th><th>Loop</th><th>Headline</th>'
        "<th>Last run</th><th>Schedule / next</th><th>Spend (7d)</th><th>Report</th>"
        f"</tr></thead><tbody>{global_rows}</tbody></table>"
    )

    sections = "".join(_render_loop_section(loop, conn, now) for loop in loops)

    body = f"{events_html}<main>{filter_html}{global_table}{sections}</main>"
    return _wrap_html(top, body)


def _color_hex(name):
    return {
        "green": "#37d67a",
        "amber": "#f2b23c",
        "red": "#ff5c5c",
        "grey": "#4a5364",
    }.get(name, "#4a5364")


def _wrap_html(top, body):
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>loops dashboard</title>"
        f"<style>{CSS}</style></head><body>"
        f"{top}{body}"
        "<footer>loops harness — static dashboard, report/propose-only</footer>"
        f"<script>{DASHBOARD_JS}</script>"
        "</body></html>"
    )


def _atomic_write(out_file, content):
    out_dir = os.path.dirname(out_file)
    os.makedirs(out_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".loops-dashboard-", suffix=".tmp", dir=out_dir
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_path, out_file)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate the static loops status dashboard."
    )
    parser.add_argument(
        "--root", default=None, help="LOOPS_ROOT (default: $LOOPS_ROOT or cwd)"
    )
    parser.add_argument(
        "--out",
        default=None,
        help="output HTML path (default: <root>/dashboard/loops.html)",
    )
    args = parser.parse_args(argv)
    out = generate(root=args.root, out_file=args.out)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
