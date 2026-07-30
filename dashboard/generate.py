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


def _default_page_envelope(root):
    path = os.path.join(root, "bin", "page_envelope.py")
    if not os.path.isfile(path):
        return None
    try:
        return _load_module_from_path(path, "loops_page_envelope")
    except Exception:  # noqa: BLE001 — §10: degrade, never crash the page
        return None


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


def _latest_promoted_run(conn, loop_name):
    if conn is None:
        return None
    try:
        cur = conn.execute(
            "SELECT run_id FROM runs WHERE loop_name = ? AND runner_status = 'completed' "
            "AND contract_path IS NOT NULL ORDER BY started_at DESC LIMIT 1",
            (loop_name,),
        )
        row = cur.fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


_DATED_PAGE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}\.html$")


def _discover_report_page_names(root):
    reports_dir = os.path.join(root, "reports")
    try:
        entries = sorted(os.listdir(reports_dir))
    except OSError:
        return []
    names = []
    for entry in entries:
        report_dir = os.path.join(reports_dir, entry)
        if not os.path.isdir(report_dir):
            continue
        try:
            files = os.listdir(report_dir)
        except OSError:
            continue
        if "latest.html" in files or any(_DATED_PAGE_RE.match(name) for name in files):
            names.append(entry)
    return names


def _page_state(root, name, conn, envelope_mod):
    """Returns {enabled, href, meta, stale, dated:[names], historical}."""
    report_dir = os.path.join(root, "reports", name)
    latest = os.path.join(report_dir, "latest.html")
    render_sh = os.path.join(root, "loops.d", name, "render.sh")
    enabled = os.path.isfile(render_sh) and os.access(render_sh, os.X_OK)
    dated = []
    if os.path.isdir(report_dir):
        try:
            dated = sorted(
                (
                    entry
                    for entry in os.listdir(report_dir)
                    if _DATED_PAGE_RE.match(entry)
                ),
                reverse=True,
            )
        except OSError:
            dated = []
    has_latest = os.path.isfile(latest)
    state = {
        "enabled": enabled,
        "href": None,
        "meta": None,
        "stale": False,
        "dated": dated,
        "historical": bool(dated or has_latest) and not enabled,
    }
    if not has_latest:
        return state
    state["href"] = f"../reports/{name}/latest.html"
    read_meta = getattr(envelope_mod, "read_meta", None) if envelope_mod else None
    if callable(read_meta):
        try:
            meta = read_meta(latest)
        except Exception:  # noqa: BLE001 — §10: bad page content never stops the dashboard
            meta = None
        if isinstance(meta, dict):
            state["meta"] = meta
    if enabled and state["meta"] is not None:
        promoted = _latest_promoted_run(conn, name)
        if promoted is not None and state["meta"].get("run_id") != promoted:
            state["stale"] = True
    return state


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
/* garden dashboard — roops design system (B-04/B-07). Tokens are the roops set; no network
   assets (Section 10): local mincho only, no webfonts, no textures, no urls of any scheme. */
:root {
  color-scheme: light;
  --sumi: #1C1A17; --sumi-deep: #16130F;
  --washi: #F2EDE3; --washi-shade: #E9E2D3;
  --shu: #C73E2B; --shu-deep: #A93321;
  --ai: #2E4A5B; --nibi: #8C8578; --koke: #6B7A5C; --ochre: #A87A2A;
  --hair: rgba(28,26,23,.14); --hair2: rgba(28,26,23,.22);
  --serif: "Hiragino Mincho ProN", "Yu Mincho", "Noto Serif JP", Georgia, serif;
  --mono: ui-monospace, "SF Mono", "Cascadia Code", Menlo, monospace;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  background: var(--sumi-deep); color: var(--sumi);
  font-family: var(--serif); font-size: 14px; line-height: 1.6;
  padding: clamp(10px, 2.5vw, 36px);
}
a { color: var(--ai); }
a:hover { color: var(--shu); }
h1, h2, h3 { font-weight: 500; letter-spacing: .01em; }
.sheet {
  position: relative; max-width: 1320px; margin: 0 auto;
  background: var(--washi); border-radius: 4px; overflow: hidden;
  box-shadow: 0 1px 2px rgba(0,0,0,.5), 0 24px 80px -24px rgba(0,0,0,.8);
}
.sheet::before, .sheet::after { content: ""; position: absolute; width: 24px; height: 24px; pointer-events: none; z-index: 2; }
.sheet::before { top: 14px; left: 14px; border-top: 1px solid var(--nibi); border-left: 1px solid var(--nibi); }
.sheet::after { bottom: 14px; right: 14px; border-bottom: 1px solid var(--nibi); border-right: 1px solid var(--nibi); }

/* ---------- header ---------- */
.topstrip {
  display: flex; flex-wrap: wrap; align-items: center; gap: 14px 18px;
  padding: 20px clamp(20px, 4vw, 44px); border-bottom: 1px solid var(--hair2);
}
.seal-mini {
  width: 34px; height: 34px; background: var(--shu); color: var(--washi);
  font-family: var(--serif); font-size: 19px; border-radius: 4px;
  display: flex; align-items: center; justify-content: center;
  transform: rotate(-2deg); flex: none;
}
.topstrip h1 { font-size: 17px; white-space: nowrap; }
.topstrip h1 small {
  display: block; font-family: var(--mono); font-size: 9.5px; font-weight: 400;
  letter-spacing: .3em; color: var(--nibi); text-transform: uppercase; margin-top: 1px;
}
.head-stats {
  margin-left: auto; display: flex; flex-wrap: wrap; gap: 8px clamp(14px, 2.5vw, 34px);
  align-items: baseline; font-family: var(--mono); font-size: 11px;
  letter-spacing: .14em; color: var(--nibi); text-transform: uppercase;
}
.chip { white-space: nowrap; }
.chip b { font-weight: 400; color: var(--sumi); }
.chip .jp { font-family: var(--serif); letter-spacing: 0; }
.chip.needs-attention { color: var(--shu); }
.chip.needs-attention b { color: var(--shu); }
.spacer { display: none; }
.muted { color: var(--nibi); font-weight: 400; }

/* ---------- section chrome ---------- */
main { padding: 0 0 8px; }
.zone { padding: clamp(22px, 3vw, 38px) clamp(20px, 4vw, 44px); }
.zone + .zone { border-top: 1px solid var(--hair); }
.kicker {
  font-family: var(--mono); font-size: 10.5px; letter-spacing: .28em;
  text-transform: uppercase; color: var(--nibi);
  display: flex; align-items: baseline; gap: 12px; margin-bottom: 16px;
}
.kicker b { color: var(--shu); font-weight: 400; font-size: 13px; letter-spacing: 0; font-family: var(--serif); }
.kicker .note { margin-left: auto; letter-spacing: .14em; font-size: 10px; }

/* ---------- the garden (global view) ---------- */
.garden { border: 1px solid var(--hair2); border-radius: 3px; background: rgba(255,255,255,.25); overflow-x: auto; }
.loop-row {
  display: grid; grid-template-columns: 44px 1.1fr 1.5fr 190px 30px; gap: 16px;
  align-items: center; padding: 12px 18px; min-width: 960px;
  border-bottom: 1px solid var(--hair);
}
.loop-row:last-child { border-bottom: none; }
.loop-row:hover { background: rgba(28,26,23,.035); }
.stamp-cell { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.stamp {
  width: 28px; height: 28px; border-radius: 3px; font-size: 14px; line-height: 1;
  display: inline-flex; align-items: center; justify-content: center;
  font-family: var(--serif); transform: rotate(-2deg); flex: none;
}
.stamp.green { border: 1.5px solid var(--koke); color: var(--koke); }
.stamp.amber { border: 1.5px solid var(--ochre); color: var(--ochre); }
.stamp.red { background: var(--shu); color: var(--washi); }
.stamp.grey { border: 1.5px solid var(--nibi); color: var(--nibi); }
.loop-name { font-family: var(--mono); font-size: 12.5px; font-weight: 400; color: var(--sumi); }
.loop-name a { color: inherit; text-decoration: none; }
.loop-name a:hover { text-decoration: underline; }
.loop-name small {
  display: block; font-size: 10px; color: var(--nibi); letter-spacing: .06em; margin-top: 2px;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}

/* the tokonoma — each loop hangs its own output */
.toko {
  position: relative; height: 64px; border-radius: 3px;
  background: var(--washi-shade); border: 1px solid var(--hair2);
  box-shadow: inset 0 1px 3px -2px rgba(28,26,23,.55), inset 0 -1px 0 rgba(255,255,255,.4);
}
.toko-scroll {
  height: 100%; overflow-y: auto; overflow-x: hidden; padding: 8px 10px;
  scrollbar-width: thin; scrollbar-color: rgba(140,133,120,.3) transparent;
}
.toko-scroll::-webkit-scrollbar { width: 4px; }
.toko-scroll::-webkit-scrollbar-thumb { background: rgba(140,133,120,.28); border-radius: 2px; }
.toko-tag {
  position: absolute; top: 3px; right: 5px; z-index: 1; pointer-events: none;
  font-family: var(--mono); font-size: 8.5px; letter-spacing: .2em; text-transform: uppercase;
  color: var(--sumi); opacity: .35; background: var(--washi-shade); padding: 0 1px 0 6px;
}
.toko-scroll > .obj:first-child { padding-right: 52px; }
.obj { display: grid; grid-template-columns: 13px 1fr; gap: 5px; line-height: 14.5px; }
.obj .mk { font-family: var(--serif); font-style: normal; font-size: 11.5px; line-height: 14.5px; text-align: center; }
.obj .mk-ok { color: var(--koke); }
.obj .mk-part { color: var(--ochre); }
.obj .mk-fail { color: var(--shu); }
.obj .oc { font-family: var(--mono); font-size: 10.5px; color: var(--sumi); overflow-wrap: anywhere; }

.run-meta {
  display: flex; flex-direction: column; align-items: flex-end; gap: 2px;
  font-family: var(--mono); text-align: right; white-space: nowrap;
}
.run-meta .rm-when { font-size: 11px; color: var(--nibi); }
.run-meta .rm-cost { font-size: 10px; color: var(--nibi); opacity: .82; }
.run-meta .rm-next { font-size: 10px; color: var(--koke); letter-spacing: .04em; }
.run-meta .rm-next.off { color: var(--nibi); }
.run-meta a { font-size: 10px; letter-spacing: .06em; }

/* schedule state — 巡 loaded / 休 not loaded or paused / 手 manual */
.sw-cell { display: flex; justify-content: flex-end; }
.sw {
  width: 24px; height: 24px; border-radius: 3px; font-family: var(--serif); font-size: 13px;
  display: inline-flex; align-items: center; justify-content: center; flex: none;
}
.sw.on { border: 1.5px solid var(--koke); color: var(--koke); }
.sw.off { border: 1.5px solid var(--hair2); color: var(--nibi); }
.sw.off.paused { border: 1.5px dashed var(--hair2); color: var(--nibi); }
.sw.manual { border: 1.5px dashed var(--hair2); color: var(--nibi); }

/* small ink dots — run history, heartbeats */
.light {
  display: inline-block; width: 9px; height: 9px; border-radius: 2px;
  margin-right: 8px; vertical-align: -1px; transform: rotate(-2deg);
}
.light.green { background: var(--koke); }
.light.amber { background: var(--ochre); }
.light.red { background: var(--shu); }
.light.grey { background: var(--nibi); opacity: .55; }
.badge {
  display: inline-block; font-family: var(--mono); font-size: 8.5px; font-weight: 400;
  text-transform: uppercase; letter-spacing: .14em; padding: 2px 6px; border-radius: 2px;
  margin-left: 6px; vertical-align: 1px;
}
.badge.harness { color: var(--shu); border: 1px solid var(--shu); }
.badge.stale { color: var(--ochre); border: 1px solid var(--ochre); }
.badge.page\\2d stale { color: var(--ochre); border: 1px solid var(--ochre); }
.badge.died { color: var(--washi); background: var(--shu); border: 1px solid var(--shu); }
.badge.hold { color: var(--ochre); border: 1px solid var(--ochre); }
.badge.historical { color: var(--ai); border: 1px solid var(--ai); }
.badge.no-meta { color: var(--nibi); border: 1px dashed var(--nibi); }
.badge.no-page { color: var(--nibi); border: 1px dashed var(--hair2); background: rgba(255,255,255,.2); }

/* ---------- report pages screen ---------- */
.reports-list { display: grid; gap: 0; border-top: 1px solid var(--hair); }
.report-entry {
  padding: clamp(18px, 2.6vw, 30px) clamp(20px, 4vw, 44px);
  border-bottom: 1px solid var(--hair);
}
.report-entry h2 {
  font-size: 15px; line-height: 1.6; display: flex; flex-wrap: wrap;
  gap: 4px 8px; align-items: baseline;
}
.report-entry h2 a { font-family: var(--mono); font-size: 13px; text-decoration-thickness: 1px; }
.report-entry .chips {
  margin-top: 10px; display: flex; flex-wrap: wrap; gap: 8px 18px;
  font-family: var(--mono); font-size: 10px; letter-spacing: .1em;
  color: var(--nibi); text-transform: uppercase;
}
.report-entry .chips:empty { display: none; }
.report-entry .history {
  margin-top: 9px; display: flex; flex-wrap: wrap; gap: 4px 12px;
  font-family: var(--mono); font-size: 10.5px; line-height: 1.8;
}
.report-entry .history:empty { display: none; }
.report-entry .history a { overflow-wrap: anywhere; }

/* ---------- per-loop sections ---------- */
section.loop { padding: clamp(22px, 3vw, 38px) clamp(20px, 4vw, 44px); border-top: 1px solid var(--hair); }
section.loop h2 { font-size: 15px; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
section.loop h2 .lname { font-family: var(--mono); font-size: 13.5px; }
section.loop h2 .muted { font-size: 12px; font-weight: 400; flex-basis: 100%; max-width: 88ch; line-height: 1.55; }

/* measures (帳) — declared panels */
.panels { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 1px;
  background: var(--hair2); border: 1px solid var(--hair2); border-radius: 3px; margin: 18px 0; }
.panel { background: var(--washi); padding: 14px 16px 12px; }
.panel .title {
  font-family: var(--mono); font-size: 9.5px; text-transform: uppercase;
  color: var(--nibi); letter-spacing: .16em; line-height: 1.7;
}
.panel .value {
  font-family: var(--mono); font-size: 24px; line-height: 1.1; letter-spacing: -.02em;
  color: var(--sumi); margin-top: 6px;
}
.panel .value.warn { color: var(--ochre); }
.panel .value.alert { color: var(--shu); }
.panel .spark { color: var(--sumi); margin-top: 6px; display: block; }
table.list-panel { border-collapse: collapse; font-family: var(--mono); font-size: 10.5px; margin-top: 6px; }
table.list-panel td, table.list-panel th { padding: 2px 8px 2px 0; text-align: left; }
table.list-panel th { color: var(--nibi); font-weight: 400; text-transform: uppercase; font-size: 9px; letter-spacing: .12em; }
.panel ul { list-style: none; font-family: var(--mono); font-size: 10.5px; margin-top: 6px; }

/* the arrangement — findings */
.findings { margin: 18px 0; }
.findings h3 {
  font-family: var(--mono); font-size: 10.5px; letter-spacing: .28em;
  text-transform: uppercase; color: var(--nibi); font-weight: 400; margin-bottom: 10px;
}
.finding {
  padding: 12px 4px 12px 14px; border-bottom: 1px solid var(--hair);
  border-left: 3px solid var(--nibi);
}
.finding:last-child { border-bottom: none; }
.finding[data-sev="alert"] { border-left-color: var(--shu); }
.finding[data-sev="warn"] { border-left-color: var(--ochre); }
.finding[data-sev="info"] { border-left-color: var(--koke); }
.finding.suppressed { opacity: .45; }
.finding .fid { font-family: var(--mono); font-size: 10px; color: var(--ai); letter-spacing: .06em; }
.finding .sev {
  font-family: var(--mono); font-weight: 400; margin-right: 8px; text-transform: uppercase;
  font-size: 9px; letter-spacing: .2em;
}
.finding .sev.warn { color: var(--ochre); }
.finding .sev.alert { color: var(--shu); }
.finding .sev.info { color: var(--koke); }
.finding .recurrence { color: var(--nibi); font-family: var(--mono); font-size: 10px; margin-left: 8px; }
.finding .pchip {
  display: inline-flex; align-items: center; gap: 5px; margin-right: 8px;
  font-family: var(--mono); font-size: 8.5px; letter-spacing: .16em; text-transform: uppercase;
  color: var(--nibi); border: 1px solid var(--hair2); border-radius: 3px; padding: 2px 7px 2px 5px;
}
.finding .pchip i { font-family: var(--serif); font-style: normal; font-size: 12px; line-height: 1; letter-spacing: 0; color: var(--sumi); }
.finding > div { font-size: 13px; }
.finding .cmd {
  display: block; margin-top: 6px; font-family: var(--mono); font-size: 10px;
  color: var(--ai); background: rgba(28,26,23,.05); border: 1px solid var(--hair);
  padding: 3px 8px; border-radius: 2px; width: fit-content; max-width: 100%; overflow-wrap: anywhere;
}

/* run history */
.runs-table { width: 100%; border-collapse: collapse; font-family: var(--mono); font-size: 10.5px; margin-top: 8px; }
.runs-table th {
  text-align: left; color: var(--nibi); font-weight: 400; text-transform: uppercase;
  font-size: 9px; letter-spacing: .16em; padding: 4px 10px 4px 0; border-bottom: 1px solid var(--hair2);
}
.runs-table td { padding: 4px 10px 4px 0; border-bottom: 1px solid var(--hair); vertical-align: top; }
section.loop > h3 {
  font-family: var(--mono); font-size: 10.5px; letter-spacing: .28em;
  text-transform: uppercase; color: var(--nibi); font-weight: 400; margin-top: 20px;
}
.fail-detail { color: var(--shu); font-size: 10.5px; }

/* failure handoff — the one washi-red block */
details.handoff { margin: 14px 0; border: 1px solid var(--shu); border-radius: 3px; background: rgba(199,62,43,.05); }
details.handoff summary {
  cursor: pointer; color: var(--shu); font-family: var(--mono); font-size: 11px;
  letter-spacing: .1em; padding: 9px 13px;
}
details.handoff .hint { color: var(--nibi); font-family: var(--mono); font-size: 9.5px; letter-spacing: .06em; padding: 0 13px 6px; }
details.handoff textarea {
  display: block; width: calc(100% - 26px); margin: 0 13px 12px; height: 9.5rem;
  background: var(--washi); color: var(--sumi); border: 1px solid var(--hair2); border-radius: 2px;
  font: 10.5px/1.5 var(--mono); padding: 8px; resize: vertical;
}
details.report-drawer summary, details.raw-fallback summary {
  cursor: pointer; color: var(--nibi); font-family: var(--mono); font-size: 10px;
  letter-spacing: .14em; text-transform: uppercase; margin-top: 14px;
}
.report-drawer pre, .raw-fallback pre {
  background: var(--washi-shade); border: 1px solid var(--hair); margin-top: 8px;
  padding: 12px 14px; border-radius: 3px; overflow-x: auto;
  font-family: var(--mono); font-size: 10.5px; line-height: 1.7;
  white-space: pre-wrap; word-break: break-word;
}
.hb { font-family: var(--mono); font-size: 11px; margin: 10px 0; color: var(--sumi); }
.empty { padding: 4rem 2rem; text-align: center; color: var(--nibi); font-family: var(--mono); font-size: 12px; letter-spacing: .1em; }
footer {
  padding: 18px clamp(20px, 4vw, 44px) 26px; border-top: 1px solid var(--hair);
  font-family: var(--mono); font-size: 9.5px; letter-spacing: .14em; color: var(--nibi);
  text-transform: uppercase; line-height: 2;
}

@media (max-width: 767px) {
  .head-stats { margin-left: 0; width: 100%; }
  .loop-row { grid-template-columns: 44px minmax(0, 1fr) 30px; gap: 10px 12px; min-width: 0; padding: 14px; }
  .loop-row > .stamp-cell { grid-column: 1; grid-row: 1; }
  .loop-row > .loop-name { grid-column: 2; grid-row: 1; }
  .loop-row > .sw-cell { grid-column: 3; grid-row: 1; }
  .loop-row > .toko { grid-column: 1 / -1; grid-row: 2; }
  .loop-row > .run-meta { grid-column: 1 / -1; grid-row: 3; align-items: flex-start; text-align: left; }
  .loop-name { overflow-wrap: anywhere; }
  .garden { overflow-x: visible; }
}
"""


def _light_html(color, marker=None, extra_badges=()):
    out = f'<span class="light {color}" title="{e(color)}"></span>'
    if marker == "harness-problem":
        out += '<span class="badge harness">harness</span>'
    for b in extra_badges:
        out += f'<span class="badge {e(b.lower())}">{e(b)}</span>'
    return out


# Status stamps (hanko) — the garden's rendering of the same §4.3 precedence result that
# _light_html renders as a dot. Purely presentational: color in, kanji out.
_STAMP_KANJI = {"green": "済", "amber": "注", "red": "警", "grey": "未"}


def _stamp_html(color, marker=None, extra_badges=()):
    kanji = _STAMP_KANJI.get(color, "未")
    out = (
        f'<span class="stamp-cell"><span class="stamp {color}" title="{e(color)}">'
        f"{kanji}</span>"
    )
    if marker == "harness-problem":
        out += '<span class="badge harness">harness</span>'
    for b in extra_badges:
        out += f'<span class="badge {e(b.lower())}">{e(b)}</span>'
    return out + "</span>"


def _schedule_loaded(root, name):
    """Display-only install-state check (§10 amendment 2026-07-30): the launchd plist file
    written by `loopctl install` exists. File presence only — never shells out to launchctl,
    so the generator stays hermetic and subprocess-free."""
    return os.path.isfile(os.path.join(root, "launchd", f"com.loops.{name}.plist"))


# Marubatsu marks for the tokonoma — severity/status rendered as 〇 △ ×, never emoji.
_MARK_BY_STATUS = {
    "ok": ("〇", "mk-ok"),
    "warn": ("△", "mk-part"),
    "alert": ("×", "mk-fail"),
}
_MARK_BY_SEVERITY = {
    "info": ("〇", "mk-ok"),
    "warn": ("△", "mk-part"),
    "alert": ("×", "mk-fail"),
}


def _toko_line(mark, mark_cls, text_html):
    return (
        f'<div class="obj"><i class="mk {mark_cls}">{mark}</i>'
        f'<span class="oc">{text_html}</span></div>'
    )


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
        '<details class="raw-fallback"><summary>Other metrics'
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
        # pancaked — the same finding across N rounds is one stack, one decision
        pchip = ""
        if (f.get("times_seen") or 0) >= 2:
            pchip = f'<span class="pchip"><i>巡</i> ×{int(f["times_seen"])}</span>'
        out.append(
            f'<div class="{cls}" data-sev="{e(severity)}">'
            f'<span class="sev {e(severity)}">{e(severity)}</span>'
            f'<span class="fid">{e(fid)}</span> — {e(title)} {pchip}'
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
    badges = []
    if loop["stale"]:
        badges.append("stale")
    if loop["died"]:
        badges.append("died")
    stamp = _stamp_html(loop["light_color"], loop["light_marker"], badges)

    latest = loop["latest_run"]
    if latest:
        started = latest["started_at"] or ""
        abs_short = started[5:16].replace("T", " ") if len(started) >= 16 else started
        last_run = f"last 巡 {e(format_relative(started, now))} · {e(abs_short)}"
    else:
        last_run = "last 巡 never"

    spend_tok, spend_cost = loop["spend_7d"]
    spend_text = f"7d {fmt_num(spend_tok)} tok"
    if spend_cost:
        spend_text += f" (${spend_cost:.2f})"

    if loop["schedule"] == "manual":
        sw = '<span class="sw manual" title="manual — run via loopctl">手</span>'
        next_html = '<span class="rm-next off">manual</span>'
    elif loop["installed"] and loop["enabled"]:
        sw = '<span class="sw on" title="schedule loaded (launchd)">巡</span>'
        next_html = f'<span class="rm-next">next 巡 {e(loop["next_run_text"])}</span>'
    elif loop["installed"]:
        sw = (
            '<span class="sw off paused" '
            'title="rounds paused — resume from console or loopctl resume">休</span>'
        )
        next_html = '<span class="rm-next off">paused</span>'
    else:
        sw = '<span class="sw off" title="no schedule loaded — supervised runs only">休</span>'
        next_html = '<span class="rm-next off">no schedule loaded</span>'

    page = loop.get("page") or {}
    links = []
    if page.get("href"):
        badge = (
            ' <span class="badge page-stale">stale</span>' if page.get("stale") else ""
        )
        links.append(f'<a href="{e(page["href"])}">page</a>{badge}')
    if loop["report_href"]:
        label = "md" if page.get("href") else "latest"
        links.append(f'<a href="{e(loop["report_href"])}">{label}</a>')
    report_link = " · ".join(links)

    toko = "".join(loop["toko_lines"]) or _toko_line(
        "未", "", '<span class="muted">never run</span>'
    )
    return (
        '<div class="loop-row">'
        f"{stamp}"
        f'<div class="loop-name"><a href="#loop-{e(loop["name"])}">{e(loop["name"])}</a>'
        f"<small>{e(loop['schedule'])} · {e(loop['description'])}</small></div>"
        f'<div class="toko"><div class="toko-scroll">{toko}</div>'
        '<span class="toko-tag">latest</span></div>'
        f'<div class="run-meta"><span class="rm-when">{last_run}</span>'
        f'<span class="rm-cost">{e(spend_text)}</span>{next_html}{report_link}</div>'
        f'<div class="sw-cell">{sw}</div>'
        "</div>"
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
    badges = []
    if loop["stale"]:
        badges.append("stale")
    if loop["died"]:
        badges.append("died")
    stamp = _stamp_html(loop["light_color"], loop["light_marker"], badges)

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

    return (
        f'<section class="loop" id="loop-{e(loop["name"])}">'
        f'<h2>{stamp}<span class="lname">{e(loop["name"])}</span> '
        f'<span class="muted">{e(loop["description"])}</span></h2>'
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


def _resolve_loop(
    root, name, conn, loopconf_parse, schedule_parse, now, envelope_mod=None
):
    conf_path = os.path.join(root, "loops.d", name, "loop.conf")
    try:
        conf, errors = loopconf_parse(conf_path)
    except Exception as exc:  # noqa: BLE001 — §10: bad config must degrade, never crash
        conf, errors = {}, [f"loop.conf parse failed: {exc}"]

    dashboard_json_path = os.path.join(root, "loops.d", name, "dashboard.json")
    dashboard_json = _read_json(dashboard_json_path)

    latest_json_path = os.path.join(root, "reports", name, "latest.json")
    latest_json = _read_json(latest_json_path)

    report_href = None
    if os.path.isfile(os.path.join(root, "reports", name, "latest.md")):
        report_href = f"../reports/{name}/latest.md"
    page = _page_state(root, name, conn, envelope_mod)

    latest_run = _latest_run(conn, name)
    recent_runs = _recent_runs(conn, name)
    heartbeat = _latest_heartbeat(conn, name)

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

    # §10 amendment 2026-07-30: staleness only applies when the schedule is actually
    # loaded (launchd plist present). A supervised-only loop is 休 — "no schedule
    # loaded" — not overdue; flagging the whole fleet stale made the badge meaningless.
    installed = _schedule_loaded(root, name)
    stale = False
    if installed and latest_run is not None and not died:
        stale = is_stale(latest_run["started_at"], expected_interval_s, now)

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

    # Tokonoma (床の間) — the row's output alcove: latest headline (or failure detail)
    # plus standing findings, each with a marubatsu mark. Derived only from sqlite +
    # latest.json fields the page already renders elsewhere; nothing model-authored is
    # computed here.
    toko_lines = []
    open_findings_count = 0
    if latest_run is not None:
        if latest_run["runner_status"] in FAILURE_STATUSES:
            why = latest_run.get("error_detail") or latest_run["runner_status"]
            toko_lines.append(
                _toko_line("×", "mk-fail", f'<span class="fail-detail">{e(why)}</span>')
            )
        elif latest_run.get("headline"):
            mark, mark_cls = _MARK_BY_STATUS.get(
                latest_run.get("effective_status") or "", ("△", "mk-part")
            )
            toko_lines.append(_toko_line(mark, mark_cls, e(latest_run["headline"])))
    shown = 0
    for f in _open_findings(conn, name):
        disp = _current_disposition(conn, name, f["finding_id"])
        action = disp["action"] if disp else None
        snooze_until = disp.get("snooze_until") if disp else None
        if is_suppressed(action, snooze_until, now):
            continue
        open_findings_count += 1
        if shown < 4:
            mark, mark_cls = _MARK_BY_SEVERITY.get(f["severity"], ("△", "mk-part"))
            toko_lines.append(_toko_line(mark, mark_cls, e(f["title"])))
            shown += 1
    if open_findings_count > shown:
        toko_lines.append(
            _toko_line(
                "△",
                "mk-part",
                f'<span class="muted">+{open_findings_count - shown} more standing</span>',
            )
        )

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
        "page": page,
        "latest_run": latest_run,
        "recent_runs": recent_runs,
        "heartbeat": heartbeat,
        "died": died,
        "stale": stale,
        "installed": installed,
        "enabled": str(conf.get("enabled", "true")).lower() != "false",
        "light_color": light_color,
        "light_marker": light_marker,
        "spend_7d": spend_7d,
        "next_run_text": next_run_text,
        "needs_attention": needs_attention,
        "toko_lines": toko_lines,
        "open_findings_count": open_findings_count,
    }


def _resolve_dashboard_loops(
    root,
    conn,
    loopconf_parse,
    schedule_parse,
    now,
    envelope_mod,
    include_report_only=False,
):
    names = _discover_loops(root)
    loops = [
        _resolve_loop(
            root, name, conn, loopconf_parse, schedule_parse, now, envelope_mod
        )
        for name in names
    ]
    if not include_report_only:
        return loops
    loop_by_name = {loop["name"]: loop for loop in loops}
    for name in _discover_report_page_names(root):
        if name not in loop_by_name:
            loop_by_name[name] = _resolve_loop(
                root, name, conn, loopconf_parse, schedule_parse, now, envelope_mod
            )
    return [loop_by_name[name] for name in sorted(loop_by_name)]


def generate(
    root=None,
    out_file=None,
    loopconf_parse=None,
    schedule_parse=None,
    now=None,
    reports_out_file=None,
    return_html=False,
):
    """Generates dashboard/loops.html and dashboard/reports.html via atomic renames."""
    root = root or os.environ.get("LOOPS_ROOT") or os.getcwd()
    root = os.path.abspath(root)
    out_file = out_file or os.path.join(root, "dashboard", "loops.html")
    reports_out_file = reports_out_file or os.path.join(
        root, "dashboard", "reports.html"
    )
    now = now or datetime.now(timezone.utc)

    _loopconf_parse = loopconf_parse or _default_loopconf_parse(root)
    _schedule_parse = schedule_parse or _default_schedule_parse(root)
    envelope_mod = _default_page_envelope(root)

    conn = _open_db(root)
    try:
        # Resolve once (§10 perf: this path runs after every loop firing). include_report_only
        # returns the superset -- loop.d entries plus report-only names -- so filtering it down
        # to the loop.d names gives the exact same `loops` list _resolve_dashboard_loops(root,
        # ..., include_report_only=False) would have produced, without a second sqlite/fs pass
        # per loop. _discover_loops() returns names already sorted alphabetically, matching the
        # sort order report_loops is built in, so the filter preserves ordering byte-for-byte.
        report_loops = _resolve_dashboard_loops(
            root,
            conn,
            _loopconf_parse,
            _schedule_parse,
            now,
            envelope_mod,
            include_report_only=True,
        )
        discovered_names = set(_discover_loops(root))
        loops = [loop for loop in report_loops if loop["name"] in discovered_names]

        counts = {"green": 0, "amber": 0, "red": 0, "grey": 0}
        for loop in loops:
            counts[loop["light_color"]] = counts.get(loop["light_color"], 0) + 1
        needs_attention_count = sum(1 for loop in loops if loop["needs_attention"])

        since_today = now.strftime("%Y-%m-%dT00:00:00Z")
        since_7d = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        spend_today = _fleet_spend(conn, since_today)
        spend_7d_fleet = _fleet_spend(conn, since_7d)

        html = _render_page(
            loops, counts, needs_attention_count, spend_today, spend_7d_fleet, now, conn
        )
        try:
            reports_html = _render_reports_page(report_loops, now)
        except Exception:  # noqa: BLE001 — §10: a broken reports view must not take down loops.html
            reports_html = _reports_document(
                '<div class="empty">Reports view failed to render.</div>', now
            )
    finally:
        if conn is not None:
            conn.close()

    _atomic_write(out_file, html)
    _atomic_write(reports_out_file, reports_html)
    return html if return_html else out_file


def generate_reports(
    root=None,
    reports_out_file=None,
    loopconf_parse=None,
    schedule_parse=None,
    now=None,
    return_html=False,
):
    """Generates dashboard/reports.html. Thin wrapper used by tests."""
    root = root or os.environ.get("LOOPS_ROOT") or os.getcwd()
    root = os.path.abspath(root)
    reports_out_file = reports_out_file or os.path.join(
        root, "dashboard", "reports.html"
    )
    now = now or datetime.now(timezone.utc)

    _loopconf_parse = loopconf_parse or _default_loopconf_parse(root)
    _schedule_parse = schedule_parse or _default_schedule_parse(root)
    envelope_mod = _default_page_envelope(root)

    conn = _open_db(root)
    try:
        loops = _resolve_dashboard_loops(
            root,
            conn,
            _loopconf_parse,
            _schedule_parse,
            now,
            envelope_mod,
            include_report_only=True,
        )
        html = _render_reports_page(loops, now)
    finally:
        if conn is not None:
            conn.close()

    _atomic_write(reports_out_file, html)
    return html if return_html else reports_out_file


def _render_page(
    loops, counts, needs_attention_count, spend_today, spend_7d_fleet, now, conn
):
    top_chips = "".join(
        f'<span class="chip"><span class="jp">{_STAMP_KANJI[c]}</span> <b>{n}</b></span>'
        for c, n in counts.items()
        if n and c in _STAMP_KANJI
    )
    na_chip = (
        f'<span class="chip needs-attention">needs attention {needs_attention_count}</span>'
        if needs_attention_count
        else '<span class="chip">needs attention 0</span>'
    )
    standing_total = sum(loop["open_findings_count"] for loop in loops)
    standing_chip = f'<span class="chip">standing <b>{standing_total}</b></span>'
    spend_today_html = fmt_num(spend_today[0]) + " tok"
    if spend_today[1]:
        spend_today_html += f" (${spend_today[1]:.2f})"
    spend_7d_html = fmt_num(spend_7d_fleet[0]) + " tok"
    if spend_7d_fleet[1]:
        spend_7d_html += f" (${spend_7d_fleet[1]:.2f})"

    top = (
        '<div class="topstrip"><span class="seal-mini">巡</span>'
        "<h1>roops<small>the garden · 庭</small></h1>"
        '<div class="head-stats">'
        f"{top_chips}{na_chip}{standing_chip}"
        f'<span class="chip">spend today <b>{e(spend_today_html)}</b></span>'
        f'<span class="chip">spend 7d <b>{e(spend_7d_html)}</b></span>'
        f'<span class="chip muted">regenerated {e(now.strftime("%Y-%m-%dT%H:%M:%SZ"))}</span>'
        '<span class="chip"><a href="reports.html">reports</a></span>'
        "</div></div>"
    )

    if not loops:
        body = '<main><div class="empty">No loops configured yet.</div></main>'
        return _wrap_html(top, body)

    global_rows = "".join(_render_loop_row(loop, now) for loop in loops)
    garden = (
        '<div class="zone"><div class="kicker"><b>庭</b> the garden — all loops'
        '<span class="note">床の間 = each loop hangs its own output · '
        "休 = paused or no schedule loaded</span></div>"
        f'<div class="garden">{global_rows}</div></div>'
    )

    sections = "".join(_render_loop_section(loop, conn, now) for loop in loops)

    body = f"<main>{garden}{sections}</main>"
    return _wrap_html(top, body)


def _safe_format_relative(ts, now):
    try:
        return format_relative(ts, now)
    except (AttributeError, TypeError, ValueError):
        return "unknown"


def _render_reports_page(loops, now):
    entries = []
    for loop in loops:
        page = loop.get("page") or {}
        if not (page.get("enabled") or page.get("href") or page.get("dated")):
            continue
        name = loop["name"]
        meta = page.get("meta")
        historical = (
            ' <span class="badge historical">historical</span>'
            if page.get("historical")
            else ""
        )
        if page.get("href") and meta:
            totals = meta.get("totals")
            if not isinstance(totals, dict):
                totals = {}
            chips = "".join(
                f'<span class="chip">{e(str(k))} <b>{e(str(v))}</b></span>'
                for k, v in totals.items()
            )
            stale = (
                ' <span class="badge page-stale">stale</span>'
                if page.get("stale")
                else ""
            )
            generated_at = meta.get("generated_at") or ""
            page_class = meta.get("page_class") or ""
            head = (
                f'<a href="{e(page["href"])}">{e(meta.get("title") or name)}</a>'
                f"{stale}{historical} "
                f'<span class="muted">{e(page_class)} · '
                f"{e(_safe_format_relative(generated_at, now))}"
                f" ({e(generated_at)})</span>"
            )
        elif page.get("href"):
            head = (
                f'<a href="{e(page["href"])}">{e(name)}</a> '
                f'<span class="badge no-meta">no meta</span>{historical}'
            )
            chips = ""
        elif page.get("dated") and page.get("historical"):
            head = f'{e(name)} <span class="badge historical">historical</span>'
            chips = ""
        else:
            head = (
                f'{e(name)} <span class="badge no-page">'
                "no page yet — last render failed or has not run</span>"
            )
            chips = ""
        dated = page.get("dated") or []
        shown = dated[:30]
        more = (
            f' <span class="muted">+{len(dated) - 30} older</span>'
            if len(dated) > 30
            else ""
        )
        history = (
            " ".join(f'<a href="../reports/{e(name)}/{e(d)}">{e(d)}</a>' for d in shown)
            + more
        )
        entries.append(
            f'<section class="report-entry"><h2>{head}</h2>'
            f'<div class="chips">{chips}</div>'
            f'<div class="history">{history}</div></section>'
        )
    body = "".join(entries) or '<div class="empty">No page-enabled loops yet.</div>'
    return _reports_document(body, now)


def _reports_document(body, now):
    regenerated = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    top = (
        '<div class="topstrip"><span class="seal-mini">頁</span>'
        "<h1>reports<small>the garden · 庭</small></h1>"
        '<div class="head-stats">'
        f'<span class="chip muted">regenerated {e(regenerated)}</span>'
        '<span class="chip"><a href="loops.html">loops</a></span>'
        "</div></div>"
    )
    intro = (
        '<div class="zone"><div class="kicker"><b>頁</b> report pages'
        '<span class="note">latest envelopes · dated history from filenames</span>'
        "</div></div>"
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>reports — 庭 the garden</title>"
        f"<style>{CSS}</style></head><body>"
        f'<div class="sheet">{top}<main>{intro}'
        f'<div class="reports-list">{body}</div></main>'
        "<footer>loops harness — static sheet · report/propose-only · "
        "page metadata is read only from envelopes</footer></div>"
        "</body></html>"
    )


def _wrap_html(top, body):
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>roops — 庭 the garden</title>"
        f"<style>{CSS}</style></head><body>"
        f'<div class="sheet">{top}{body}'
        "<footer>loops harness — static sheet · report/propose-only · "
        "findings are actions in waiting</footer></div>"
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
    parser.add_argument(
        "--reports-out",
        default=None,
        help="reports HTML path (default: <root>/dashboard/reports.html)",
    )
    args = parser.parse_args(argv)
    out = generate(root=args.root, out_file=args.out, reports_out_file=args.reports_out)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
