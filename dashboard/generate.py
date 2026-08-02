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


# B-17: lockstep MIRROR of bin/loopconf.py's DEFAULT_OWNER/resolve_owner.
# generate.py cannot import loopconf (the lazy-seam doctrine above: this module
# must work against roots where bin/loopconf.py does not exist, and the
# hermetic dashboard tests assert exactly that), so the rule is copied here and
# pinned by a drift test in tests/test_dashboard.py — same canonical-copy
# pattern as the token blocks vs pagekit/kit.css (tests/test_token_drift.py).
DEFAULT_OWNER = "loops"


def resolve_owner(conf):
    """-> (owner, assumed). Mirror of bin/loopconf.py resolve_owner."""
    owner = conf.get("owner")
    if owner:
        return owner, False
    return DEFAULT_OWNER, True


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


def is_running(finished_at, started_at, timeout_s, now):
    """(Amendment 2 -- 2026-07-30): finished_at IS NULL and age <= timeout_s -- still
    inside its own budget, a live in-flight run rather than a failure."""
    if finished_at:
        return False
    started = _parse_iso(started_at)
    if started is None:
        return False
    age_s = (now - started).total_seconds()
    return age_s <= (timeout_s or 900)


def is_overdue(finished_at, started_at, timeout_s, now):
    """(Amendment 2 -- 2026-07-30): finished_at IS NULL and age in (timeout_s,
    timeout_s+120] -- past its own budget but not yet past the §4.6 died grace."""
    if finished_at:
        return False
    started = _parse_iso(started_at)
    if started is None:
        return False
    age_s = (now - started).total_seconds()
    to = timeout_s or 900
    return to < age_s <= to + 120


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


def root_flag_for(root):
    """`--root <root>` only when the generating root's realpath differs from the default
    `~/projects/loops` checkout -- omitted for the common case so pasted commands stay
    short. Comparison is realpath-to-realpath so symlinked checkouts still match."""
    default_root = os.path.realpath(os.path.expanduser("~/projects/loops"))
    if os.path.realpath(root) != default_root:
        return f" --root {root}"
    return ""


def finding_handoff_text(loop, f, root, root_flag):
    """Deterministic paste-into-an-agent template for ONE open, unsuppressed finding --
    same pattern as HANDOFF_TEMPLATE for a failed run: plain text built only from sqlite
    recurrence fields (`finding_id`, `severity`, `times_seen`, `first_seen_at`) merged with
    `latest.json`'s `title`/`detail` (the caller falls back to sqlite's title/severity when
    the finding has no live entry -- e.g. resolved since, or latest.json missing). The whole
    composed string is HTML-escaped by the caller before embedding, so title/detail need no
    escaping here. MUST NOT ever say "approve" -- ack/suppress is not approval (settled
    doctrine): the two actions offered are acting on the finding in the reader's OWN agent
    context/permissions, or suppressing it via loopctl dismiss/snooze."""
    detail = truncate_value(f.get("detail") or "", 2048)
    detail_block = f"\n\n  {detail}" if detail else ""
    return (
        f"A scheduled report-only loop ('{loop}') flagged this finding "
        f"(id {f['finding_id']}, severity {f['severity']}, seen {f['times_seen']}x "
        f"since {(f.get('first_seen_at') or '')[:10]}):\n\n"
        f"  {f['title']}{detail_block}\n\n"
        f"Context files: reports/{loop}/latest.md and state/runs/ under {root}.\n"
        f"The loop only reports; decide and act in YOUR context and permissions.\n"
        f"If instead this should stop being reported, suppress it:\n"
        f'  {root}/bin/loopctl dismiss {loop} {f["finding_id"]}{root_flag} --note "..."\n'
        f"  {root}/bin/loopctl snooze {loop} {f['finding_id']}{root_flag} --until YYYY-MM-DD\n"
    )


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
    """The newest run row for `loop_name`, tie-broken by `rowid DESC` on a
    `started_at` tie so this agrees with `bin/db.py`'s `query_loops_summary`
    (the source `loopctl`'s fleet aggregate uses) — both must pick the same
    physical row for the same tie, or the dashboard and `loopctl status`
    disagree about a loop's health from the exact same data."""
    if conn is None:
        return None
    row = conn.execute(
        "SELECT * FROM runs WHERE loop_name=? ORDER BY started_at DESC, rowid DESC LIMIT 1",
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


def load_loop_events(conn, limit=15):
    """Fleet-wide most-recent lifecycle events (§3 `loop_events`), newest first.
    Powers the `<section id="recent-events">` strip. A `state/loops.sqlite` created before
    Amendment 2 has no `loop_events` table until something re-runs `db.py init` -- degrade to
    an empty list rather than crashing the whole page (fix round 1, 2026-07-30)."""
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT * FROM loop_events ORDER BY ts DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]


def _loop_provenance(conn, loop_name):
    """Most recent created/imported event for a loop (Amendment 2). The `event IN (...)`
    filter runs in SQL before the LIMIT so the founding event is never lost behind a run of
    later paused/resumed/etc. rows — same fix as loopctl's `status --json` provenance lookup.
    Same missing-table degradation as load_loop_events (fix round 1, 2026-07-30)."""
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT * FROM loop_events WHERE loop_name=? AND event IN ('created','imported') "
            "ORDER BY ts DESC, id DESC LIMIT 1",
            (loop_name,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(row) if row else None


def _latest_event_ts(conn, loop_name):
    """B-19: the loop's newest lifecycle event ts (ANY event type — a
    just-created loop's `created` row IS its newest event, which is what
    floats it to the top of the garden's default recency order). Same
    missing-table degradation as _loop_provenance."""
    if conn is None:
        return None
    try:
        row = conn.execute(
            "SELECT ts FROM loop_events WHERE loop_name=? ORDER BY ts DESC, id DESC LIMIT 1",
            (loop_name,),
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    return row["ts"] if row else None


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
   assets (Section 10): local mincho only, no webfonts, no textures, no urls of any scheme.
   Dark palette + toggle (WP2 2026-08-02): OS prefers-color-scheme default; explicit
   data-theme on <html> overrides both ways; localStorage key loops-theme. */
:root {
  color-scheme: light;
  --sumi: #1C1A17;
  --sumi-deep: #16130F;
  --washi: #F2EDE3;
  --washi-shade: #E9E2D3;
  --shu: #C73E2B;
  --shu-deep: #A93321;
  --ai: #2E4A5B;
  --nibi: #8C8578;
  --nibi-faint: #ABA495;
  --koke: #6B7A5C;
  --ochre: #A87A2A;
  --hair: rgba(28,26,23,.14);
  --hair2: rgba(28,26,23,.22);
  --sumi-rgb: 28,26,23;
  --shu-rgb: 199,62,43;
  --ai-rgb: 46,74,91;
  --nibi-rgb: 140,133,120;
  --serif: "Hiragino Mincho ProN", "Yu Mincho", "Noto Serif JP", Georgia, serif;
  --mono: ui-monospace, "SF Mono", "Cascadia Code", Menlo, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --sumi: #E7E9EC;
    --sumi-deep: #F4F5F7;
    --washi: #0E0F12;
    --washi-shade: #14161A;
    --shu: #D84F63;
    --shu-deep: #B84354;
    --ai: #279A83;
    --nibi: #9AA1AB;
    --nibi-faint: #5D6570;
    --koke: #8FA97A;
    --ochre: #B48C1A;
    --hair: #22252B;
    --hair2: #2C3037;
    --sumi-rgb: 231,233,236;
    --shu-rgb: 216,79,99;
    --ai-rgb: 39,154,131;
    --nibi-rgb: 154,161,171;
  }
  /* --sumi-deep is near-white (ink tier); body backdrop keeps a near-black frame so the
     paper-on-desk relationship does not invert in dark mode. */
  body { background: #08090B; }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --sumi: #E7E9EC;
  --sumi-deep: #F4F5F7;
  --washi: #0E0F12;
  --washi-shade: #14161A;
  --shu: #D84F63;
  --shu-deep: #B84354;
  --ai: #279A83;
  --nibi: #9AA1AB;
  --nibi-faint: #5D6570;
  --koke: #8FA97A;
  --ochre: #B48C1A;
  --hair: #22252B;
  --hair2: #2C3037;
  --sumi-rgb: 231,233,236;
  --shu-rgb: 216,79,99;
  --ai-rgb: 39,154,131;
  --nibi-rgb: 154,161,171;
}
:root[data-theme="dark"] body { background: #08090B; }
:root[data-theme="light"] {
  color-scheme: light;
  --sumi: #1C1A17;
  --sumi-deep: #16130F;
  --washi: #F2EDE3;
  --washi-shade: #E9E2D3;
  --shu: #C73E2B;
  --shu-deep: #A93321;
  --ai: #2E4A5B;
  --nibi: #8C8578;
  --nibi-faint: #ABA495;
  --koke: #6B7A5C;
  --ochre: #A87A2A;
  --hair: rgba(28,26,23,.14);
  --hair2: rgba(28,26,23,.22);
  --sumi-rgb: 28,26,23;
  --shu-rgb: 199,62,43;
  --ai-rgb: 46,74,91;
  --nibi-rgb: 140,133,120;
}
:root[data-theme="light"] body { background: var(--sumi-deep); }
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
#theme-toggle {
  font-family: var(--mono); font-size: 13px; line-height: 1; letter-spacing: 0;
  color: var(--nibi); background: transparent; text-transform: none;
  border: 1px solid var(--hair2); border-radius: 3px;
  padding: 4px 8px; cursor: pointer; align-self: center;
}
#theme-toggle:hover { color: var(--sumi); border-color: var(--nibi); }
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
/* B-19: the kicker's right side hosts the filter/sort controls (the old
   glossary note is gone — the glosses live on the glyphs' own titles). */
.kicker .filters {
  margin-left: auto; display: flex; gap: 14px; align-items: baseline;
  text-transform: none; letter-spacing: .04em; font-size: 10.5px;
}
.kicker .filters select {
  font-family: var(--mono); font-size: 10.5px; background: var(--washi); color: var(--sumi);
  border: 1px solid var(--hair2); border-radius: 3px; padding: 1px 6px; margin-left: 5px;
}
@media (prefers-reduced-motion: reduce) {
  .loop-row { transition: none !important; }
}

/* ---------- the garden (global view) ---------- */
.garden { border: 1px solid var(--hair2); border-radius: 3px; background: rgba(255,255,255,.25); overflow-x: auto; }
/* Each garden row is a <details>; the summary is the grid (grid children must be direct
   children of the grid container). Shared name="garden" → one-open-at-a-time natively. */
.loop-row {
  border-bottom: 1px solid var(--hair);
}
.loop-row:last-child { border-bottom: none; }
.loop-row > summary {
  display: grid; grid-template-columns: 44px 1.1fr 1.5fr 190px 64px; gap: 16px;
  align-items: center; padding: 12px 18px; min-width: 960px;
  list-style: none; cursor: pointer;
}
.loop-row > summary::-webkit-details-marker { display: none; }
.loop-row > summary:hover { background: rgba(var(--sumi-rgb),.035); }
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
/* English gloss beside meaning-bearing kanji (WP1 2026-08-02) — tiny muted mono */
.en {
  font-family: var(--mono); font-size: 8.5px; font-weight: 400; color: var(--nibi);
  letter-spacing: .04em; margin-left: 3px; text-transform: none; white-space: nowrap;
}
.loop-name { font-family: var(--mono); font-size: 12.5px; font-weight: 400; color: var(--sumi); }
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
  scrollbar-width: thin; scrollbar-color: rgba(var(--nibi-rgb),.3) transparent;
}
.toko-scroll::-webkit-scrollbar { width: 4px; }
.toko-scroll::-webkit-scrollbar-thumb { background: rgba(var(--nibi-rgb),.28); border-radius: 2px; }
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

/* schedule state — 巡 loaded / 休 not loaded or paused / 手 manual (B-11: rendered as the
   section-04 mock's rounds switch, read-only — same three states, same kanji + title
   vocabulary as §10; the console's interactive toggle (.con-sw) replaces this span when
   console-active, so the two never show together). 手 manual keeps the square chip: there
   is no schedule to toggle, and a switch would misread as "off". */
.sw-cell { display: flex; justify-content: flex-end; align-items: center; gap: 8px; }
.sw { display: inline-flex; align-items: center; gap: 7px; flex: none; }
.sw .sw-track {
  display: block; position: relative; width: 36px; height: 18px; border-radius: 9px;
  border: 1px solid var(--hair2); background: var(--washi);
}
.sw .sw-knob { position: absolute; top: 2px; width: 12px; height: 12px; border-radius: 50%; }
.sw.on .sw-knob { left: 20px; background: var(--koke); }
.sw.off .sw-track { background: var(--washi-shade); }
.sw.off .sw-knob { left: 2px; background: var(--nibi); }
.sw.off.paused .sw-track { border-style: dashed; }
.sw .sw-lab { font-family: var(--serif); font-size: 12px; line-height: 1; color: var(--nibi); width: 13px; flex: none; }
.sw.manual {
  width: 24px; height: 24px; border-radius: 3px; font-family: var(--serif); font-size: 13px;
  justify-content: center; border: 1.5px dashed var(--hair2); color: var(--nibi);
}
html.console-active .sw { display: none; }

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
.badge.overdue { color: var(--ochre); border: 1px solid var(--ochre); }
.badge.running {
  color: var(--ai); border: 1px solid var(--ai);
  animation: pulse-badge 1.6s ease-in-out infinite;
}
@keyframes pulse-badge { 0%, 100% { opacity: 1; } 50% { opacity: .5; } }

/* ---------- report block (accordion body; replaces retired reports screen) ---------- */
.report-block {
  margin: 0 0 14px; padding: 10px 0 12px; border-bottom: 1px solid var(--hair);
  font-family: var(--mono); font-size: 11px;
}
.report-block .report-links { display: flex; flex-wrap: wrap; gap: 6px 14px; align-items: baseline; }
.report-block .report-links a { font-size: 12px; letter-spacing: .04em; }
.report-block .history {
  margin-top: 8px; display: flex; flex-wrap: wrap; gap: 4px 12px;
  font-size: 10.5px; line-height: 1.8; color: var(--nibi);
}
.report-block .history:empty { display: none; }
.report-block .history a { overflow-wrap: anywhere; }
.permalink {
  float: right; font-family: var(--mono); font-size: 12px; color: var(--nibi);
  text-decoration: none; letter-spacing: .04em; margin: 2px 0 0 10px;
}
.permalink:hover { color: var(--ai); text-decoration: underline; }

/* ---------- per-loop sections (inside accordion body) ---------- */
section.loop { padding: 14px 18px 18px; border-top: 1px solid var(--hair); }
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
  display: flex; flex-wrap: wrap; align-items: flex-start;
  justify-content: space-between; gap: 10px 18px;
}
.finding .f-main { flex: 1 1 32ch; min-width: 0; }
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
  color: var(--ai); background: rgba(var(--sumi-rgb),.05); border: 1px solid var(--hair);
  padding: 3px 8px; border-radius: 2px; width: fit-content; max-width: 100%; overflow-wrap: anywhere;
}
/* hanko button rank (B-11) — the section-04 mock's per-finding stamps, wired to the only
   write path a static page has: each enabled button copies its ready-to-paste loopctl
   command (§10: dispositions are entered via loopctl). 承 is rendered but disabled — its
   verb does not exist yet, and ack is deliberately not it (open thread §1). */
.arr-btns { display: flex; gap: 8px; flex: none; align-items: center; margin-top: 2px; }
.hanko-btn {
  width: 34px; height: 34px; border-radius: 4px; border: 1.5px solid var(--shu);
  background: transparent; color: var(--shu); font-family: var(--serif); font-size: 16px;
  line-height: 1; cursor: pointer; transform: rotate(-2deg);
  transition: background .3s cubic-bezier(0.16, 1, 0.3, 1),
              color .3s cubic-bezier(0.16, 1, 0.3, 1),
              transform .15s cubic-bezier(0.16, 1, 0.3, 1);
}
.hanko-btn:hover { background: rgba(var(--shu-rgb),.08); }
.hanko-btn:active { transform: rotate(-2deg) scale(.94); background: var(--shu); color: var(--washi); }
.hanko-btn:disabled { opacity: .25; cursor: default; }
.stamp-mark {
  font-family: var(--serif); font-size: 17px; color: var(--shu);
  transform: rotate(-4deg); display: inline-block; margin-left: 4px;
}
.copy-note {
  font-family: var(--mono); font-size: 9px; letter-spacing: .14em;
  text-transform: uppercase; color: var(--ai);
}
details.finding-handoff { margin-top: 8px; }
details.finding-handoff summary {
  cursor: pointer; color: var(--nibi); font-family: var(--mono); font-size: 10px;
  letter-spacing: .14em; text-transform: uppercase;
}
.finding-handoff pre {
  background: var(--washi-shade); border: 1px solid var(--hair); margin-top: 6px;
  padding: 10px 12px; border-radius: 3px; overflow-x: auto;
  font-family: var(--mono); font-size: 10px; line-height: 1.6;
  white-space: pre-wrap; word-break: break-word;
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
details.handoff { margin: 14px 0; border: 1px solid var(--shu); border-radius: 3px; background: rgba(var(--shu-rgb),.05); }
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

/* ---------- console controls (Task 4) — rounds switch + schedule picker ----------
   Hidden by default (see data-console-controls in dashboard/generate.py); unhidden only
   by the page's own hydration script once fetch('api/state') succeeds. Tokens reused
   verbatim from :root above -- no new hex/rgba literals introduced by this block.
   All rules in this section are unconditional (apply at every width); the mobile
   @media (max-width: 767px) block at the very end of this stylesheet -- the file's one
   existing mobile breakpoint, kept as the single last-word block by convention so its
   overrides reliably win on source order at equal specificity -- is where the two
   width-specific overrides for this section live (.con-sched's tighter max-width and
   html.console-active .loop-row's tighter track cap). Declaring either override here
   instead, ahead of that block, would make it dead code: identical specificity, later
   unconditional rule wins the cascade regardless of a match media query elsewhere. */

/* The `hidden` ATTRIBUTE only hides via a USER-AGENT stylesheet rule (`[hidden] {
   display: none }`), and every author-origin declaration outranks the UA origin. So
   `.con-cell { display: inline-flex }` and `.sp-form { display: flex }` below silently
   defeat the `hidden` attribute on their own elements: a dashboard opened as a plain
   file would show live-looking toggles and schedule chips it can never actuate. This
   rule restores the attribute's meaning at author origin, and `!important` makes it
   order- and specificity-proof for any future `display` rule added to this block.
   Removing `hidden` (what the hydration script does on fetch success) still unhides,
   because the rule keys on the attribute, not on a class. */
[hidden] { display: none !important; }
.con-cell { display: inline-flex; align-items: center; gap: 8px; min-width: 0; max-width: 100%; }
.con-sw { background: none; border: 0; padding: 0; cursor: pointer; display: inline-flex; border-radius: 9px; }
.con-sw:focus-visible { outline: 1px solid var(--shu); outline-offset: 3px; }
.con-sw[disabled] { opacity: .35; cursor: default; }
.con-track {
  display: block; position: relative; width: 34px; height: 17px; border-radius: 9px;
  border: 1px solid var(--hair2); background: var(--washi);
  transition: background .8s cubic-bezier(0.16,1,0.3,1), border-color .8s cubic-bezier(0.16,1,0.3,1);
}
.con-knob {
  position: absolute; top: 2px; left: 18px; width: 11px; height: 11px; border-radius: 50%;
  background: var(--koke);
  transition: left .8s cubic-bezier(0.16,1,0.3,1), background .8s cubic-bezier(0.16,1,0.3,1);
}
.con-sw[aria-checked="false"] .con-knob { left: 2px; background: var(--nibi); }
.con-sched {
  font-family: var(--mono); font-size: 11px; background: none; cursor: pointer;
  border: 1px solid var(--hair2); border-radius: 3px; padding: 3px 8px; color: inherit;
  max-width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.con-sched:hover { border-color: var(--shu); }
.sched-panel {
  position: absolute; z-index: 9; background: var(--washi);
  border: 1px solid var(--hair2); border-radius: 4px; padding: 12px;
  box-shadow: 0 10px 30px -12px rgba(0,0,0,.4);
}
.sched-panel button {
  font-family: var(--mono); font-size: 11px; background: none;
  border: 1px solid var(--hair2); border-radius: 3px; padding: 4px 9px; cursor: pointer; margin: 2px;
}
.sched-panel button:hover { border-color: var(--shu); color: var(--shu); }
.sp-form { margin-top: 8px; display: flex; gap: 6px; align-items: center; }
.sp-err { font-family: var(--mono); font-size: 11px; color: var(--shu); margin-top: 6px; }

/* generate.py's first motion-sensitive CSS (Task 4) -- no prior prefers-reduced-motion
   block existed to extend, so this is that block, going forward. */
@media (prefers-reduced-motion: reduce) {
  .con-track, .con-knob, .hanko-btn { transition: none; }
}

/* Each .loop-row is its own independent grid (no shared parent grid across rows), so every
   row must keep IDENTICAL grid-template-columns to stay column-aligned -- a content-sized
   track (auto/fit-content) would size differently per row depending on that row's own
   schedule-string length, breaking alignment. A definite (non-fr) max like minmax(64px,
   214px) also does NOT behave like a plain 64px column when idle: CSS Grid grows
   non-flexible tracks up to their fixed max using free space BEFORE flexible (fr) tracks
   get a share -- widening the base rule to the console width was verified live (Task 4)
   to expand every row's sw-cell even with controls hidden. So the wider column applies
   ONLY once the hydration script (in _CONSOLE_CONTROLS_HTML below) confirms api/state is
   live and stamps `console-active` on <html> in the same success branch that unhides the
   controls -- the widen and the reveal always happen together. (Task 4 pinned the base
   track at the pre-console 30px "byte-identical" width; B-11 deliberately supersedes that
   pin -- the static page now renders the section-04 mock's read-only rounds switch, which
   needs 36px track + 7px gap + 13px label ≈ 56px, so the base track is 64px. The
   mechanism above -- console widen gated on console-active -- is unchanged.) Console
   widths are measured, not guessed (see Task 4 fix report): a worst-realistic-case
   schedule chip ("weekly:mon:08:00" / "monthly:01:09:00", the longest §5.1 grammar
   strings) renders con-cell at ~166px, and console-active hides the static .sw switch,
   leaving headroom under 214px desktop / 160px mobile (the mobile block's tighter
   .con-sched max-width keeps the chip itself from ever exceeding the mobile budget). */
html.console-active .loop-row > summary { grid-template-columns: 44px 1.1fr 1.5fr 190px minmax(64px, 214px); }

@media (max-width: 767px) {
  .head-stats { margin-left: 0; width: 100%; }
  .loop-row > summary { grid-template-columns: 44px minmax(0, 1fr) 64px; gap: 10px 12px; min-width: 0; padding: 14px; }
  .loop-row > summary > .stamp-cell { grid-column: 1; grid-row: 1; }
  .loop-row > summary > .loop-name { grid-column: 2; grid-row: 1; }
  .loop-row > summary > .sw-cell { grid-column: 3; grid-row: 1; }
  .loop-row > summary > .toko { grid-column: 1 / -1; grid-row: 2; }
  .loop-row > summary > .run-meta { grid-column: 1 / -1; grid-row: 3; align-items: flex-start; text-align: left; }
  .loop-name { overflow-wrap: anywhere; }
  .garden { overflow-x: visible; }
  /* tighter cap than desktop's 130px, so a long schedule spec ellipsizes instead of
     pushing the row past the 390px viewport. This block is the last word in the
     stylesheet (by convention -- see the note above .con-cell) so it reliably wins
     over the unconditional .con-sched rule at equal specificity. */
  .con-sched { max-width: 84px; }
  html.console-active .loop-row > summary { grid-template-columns: 44px minmax(0, 1fr) minmax(64px, 160px); }
}
/* tags + provenance + fleet recent-events strip (Amendment 2 — 2026-07-30) */
.tags { margin: 4px 0; display: flex; flex-wrap: wrap; gap: 4px 6px; }
.tag {
  display: inline-flex; align-items: center; font-family: var(--mono); font-size: 9px;
  letter-spacing: .1em; text-transform: uppercase; padding: 2px 7px; border-radius: 2px;
  border: 1px solid var(--hair2); color: var(--ai); background: rgba(var(--ai-rgb),.06);
}
.provenance { font-family: var(--mono); font-size: 10px; margin: 2px 0 4px; letter-spacing: .04em; }
/* B-19: compact — the strip was pushing the garden below the fold. Tighter
   paddings, no per-row hairlines; the header rule alone separates rows enough. */
#recent-events {
  padding: 7px clamp(20px, 4vw, 44px) 6px; border-bottom: 1px solid var(--hair2);
  background: rgba(255,255,255,.15);
}
#recent-events h3 {
  font-family: var(--mono); font-size: 10.5px; letter-spacing: .28em;
  text-transform: uppercase; color: var(--nibi); font-weight: 400; margin-bottom: 3px;
}
.events-table { width: 100%; border-collapse: collapse; font-family: var(--mono); font-size: 10.5px; line-height: 1.35; }
.events-table th {
  text-align: left; color: var(--nibi); font-weight: 400; text-transform: uppercase;
  font-size: 9px; letter-spacing: .16em; padding: 1px 10px 2px 0; border-bottom: 1px solid var(--hair2);
}
.events-table td { padding: 1px 10px 1px 0; }
/* owner chip (B-17) — 主 = the loop's owning project/process. A button (it
   copies the set-owner command) styled apart from .tag; assumed owners dim. */
.owner-chip {
  display: inline-flex; align-items: center; gap: 3px; font-family: var(--mono);
  font-size: 9px; letter-spacing: .1em; padding: 2px 7px; margin-left: 8px;
  border-radius: 2px; border: 1px solid var(--hair2); cursor: copy;
  color: var(--sumi); background: rgba(var(--sumi-rgb), .05); vertical-align: 1px;
}
.owner-chip b { font-weight: 400; color: var(--nibi); }
.owner-chip.owner-assumed { color: var(--nibi); border-style: dashed; background: transparent; }
.owner-chip.copied { color: var(--ai); border-color: var(--ai); background: rgba(var(--ai-rgb), .1); }
"""

DASHBOARD_JS = """
function loopsApplyFilters() {
  var ownerSel = document.getElementById('owner-filter');
  var tagSel = document.getElementById('tag-filter');
  var owner = ownerSel ? ownerSel.value : '';
  var tag = tagSel ? tagSel.value : '';
  document.querySelectorAll('[data-tags]').forEach(function (el) {
    var tags = (el.getAttribute('data-tags') || '').split(' ');
    var okTag = !tag || tags.indexOf(tag) > -1;
    var okOwner = !owner || el.getAttribute('data-owner') === owner;
    el.style.display = (okTag && okOwner) ? '' : 'none';
  });
}
function loopsApplySort() {
  /* B-19 recency sort. Recency = the loop's newest lifecycle EVENT
     (data-latest-event, ISO ts) — a just-created loop tops the garden.
     The server already renders rows in recent order (the default), so this
     only runs on user changes; movement is FLIP-animated. */
  var sel = document.getElementById('sort-order');
  var mode = sel ? sel.value : 'recent';
  var garden = document.querySelector('.garden');
  if (!garden) return;
  var rows = Array.prototype.slice.call(garden.querySelectorAll('details.loop-row'));
  var before = {};
  rows.forEach(function (el) { before[el.id] = el.getBoundingClientRect().top; });
  var byName = function (a, b) { return a.id < b.id ? -1 : (a.id > b.id ? 1 : 0); };
  var sorted = rows.slice().sort(function (a, b) {
    if (mode === 'name') return byName(a, b);
    var ta = a.getAttribute('data-latest-event') || '';
    var tb = b.getAttribute('data-latest-event') || '';
    if (ta !== tb) {
      if (!ta) return 1;
      if (!tb) return -1;
      return ta < tb ? 1 : -1;
    }
    return byName(a, b);
  });
  sorted.forEach(function (el) { garden.appendChild(el); });
  rows.forEach(function (el) {
    var dy = before[el.id] - el.getBoundingClientRect().top;
    if (!dy) return;
    el.style.transition = 'none';
    el.style.transform = 'translateY(' + dy + 'px)';
    requestAnimationFrame(function () {
      el.style.transition = 'transform .45s ease';
      el.style.transform = '';
    });
  });
}
function loopsCopyOwnerCmd(ev, el) {
  /* Chip lives inside <summary>: without preventDefault the click also
     toggles the accordion. Copy is clipboard-only -- no request, no mutation. */
  ev.preventDefault();
  ev.stopPropagation();
  var cmd = el.getAttribute('data-copy') || '';
  var done = function () {
    el.classList.add('copied');
    setTimeout(function () { el.classList.remove('copied'); }, 900);
  };
  var fallback = function () {
    var ta = document.createElement('textarea');
    ta.value = cmd;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.left = '-9999px';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    document.body.removeChild(ta);
    done();
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(cmd).then(done, fallback);
  } else {
    fallback();
  }
}
function loopsOpenHash() {
  var h = location.hash;
  if (!h || h.indexOf('#loop-') !== 0) return;
  var el = document.getElementById(h.slice(1));
  if (el && el.tagName === 'DETAILS') {
    el.open = true;
    el.scrollIntoView();
  }
}
document.addEventListener('DOMContentLoaded', loopsOpenHash);
window.addEventListener('hashchange', loopsOpenHash);
function loopsToggleTheme() {
  var root = document.documentElement;
  var current = root.getAttribute('data-theme');
  if (!current) {
    current = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  }
  var next = current === 'dark' ? 'light' : 'dark';
  root.setAttribute('data-theme', next);
  try { localStorage.setItem('loops-theme', next); } catch (e) {}
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


def _render_owner_chip(loop):
    """B-17 owner chip: 主 <owner>. A <button> because clicking copies the
    ready-made `loopctl set-owner` command (the static page's edit
    affordance — the page itself never mutates anything). Assumed owners
    render dashed/dim so the missing owner= line stays visible."""
    owner = loop["owner"]
    cmd = f"loopctl set-owner {loop['name']} {owner}"
    cls = "owner-chip"
    title = f"owner — click to copy: {cmd}"
    if loop["owner_assumed"]:
        cls += " owner-assumed"
        title = (
            f"owner assumed {owner!r} (no owner= in loop.conf) — click to copy: {cmd}"
        )
    return (
        f'<button type="button" class="{cls}" data-copy="{e(cmd)}" title="{e(title)}" '
        f'onclick="loopsCopyOwnerCmd(event, this)"><b>主</b>{e(owner)}</button>'
    )


def _data_owner_attr(loop):
    """Always emitted, like _data_tags_attr below: the combined filter reads
    data-owner off every [data-tags] row, and every loop resolves an owner."""
    return f' data-owner="{e(loop["owner"])}"'


def _data_tags_attr(tags):
    """Always emits `data-tags="..."` (empty string when the loop has no tags) -- fix round
    1, 2026-07-30. The client-side filter only ever touches `[data-tags]` elements; a loop
    row/section that omitted the attribute entirely would stay visible under every tag
    selection instead of correctly being hidden alongside every other non-matching loop."""
    return f' data-tags="{e(" ".join(tags or []))}"'


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


# Status stamps (hanko) — the garden's rendering of the same §4.3 precedence result that
# _light_html renders as a dot. Purely presentational: color in, kanji out.
_STAMP_KANJI = {"green": "済", "amber": "注", "red": "警", "grey": "未"}
# English glosses beside meaning-bearing kanji (WP1 2026-08-02, pinned wording).
_STAMP_GLOSS = {"green": "ok", "amber": "warn", "red": "alert", "grey": "no data"}
_DISP_GLOSS = {"ack": "ack", "snooze": "snoozed", "dismiss": "dismissed"}


def _en(word):
    """Tiny muted English gloss immediately after a meaning-bearing kanji."""
    return f'<span class="en">{e(word)}</span>'


def _stamp_html(color, marker=None, extra_badges=()):
    kanji = _STAMP_KANJI.get(color, "未")
    gloss = _STAMP_GLOSS.get(color, "no data")
    out = (
        f'<span class="stamp-cell"><span class="stamp {color}" title="{e(color)}">'
        f"{kanji}</span>{_en(gloss)}"
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


def _render_finding_handoff(
    loop_name, fid, title, severity, detail, f, root, root_flag
):
    """Collapsed paste-into-an-agent block for one unsuppressed open finding. `f` is the raw
    sqlite row (supplies `times_seen`/`first_seen_at`); `title`/`severity`/`detail` are the
    already-merged (latest.json-preferred) display values computed by the caller."""
    merged = {
        "finding_id": fid,
        "title": title,
        "severity": severity,
        "times_seen": f["times_seen"],
        "first_seen_at": f["first_seen_at"],
        "detail": detail,
    }
    text = finding_handoff_text(loop_name, merged, root, root_flag)
    return (
        '<details class="finding-handoff"><summary>hand to an agent</summary>'
        f"<pre>{e(text)}</pre></details>"
    )


# Disposition → hanko kanji, matching the section-04 mock's stamp vocabulary:
# 認 acknowledge · 休 snooze · 済 settle (dismiss). 承 approve exists in the mock but has
# no CLI verb — ack ≠ approval is settled doctrine (docs/OPEN_THREADS_WARMSTART.md §1).
_DISP_KANJI = {"ack": "認", "snooze": "休", "dismiss": "済"}


def _render_hanko_btns(loop_name, fid, root_flag, action):
    """The hanko rank for one unsuppressed open finding. Enabled stamps carry their full
    loopctl command in data-copy (the page script copies it to the clipboard); the command
    text also sits in the title so it is readable without JS. 承 is always disabled and
    unglossed (the natural English word is banned page-wide — ack ≠ approval)."""

    def btn(kanji, gloss, verb, cmd_text):
        return (
            f'<button class="hanko-btn" type="button" data-copy="{e(cmd_text)}" '
            f'title="{e(verb + " — copies: " + cmd_text)}" '
            f'aria-label="{e("copy " + verb + " command for " + fid)}">'
            f"{kanji}{_en(gloss)}</button>"
        )

    def cmd_for(verb):
        return f"loopctl {verb} {loop_name} {fid}{root_flag}"

    # NB: the word "appro*" is banned page-wide (test_finding_paste_block pins it, per
    # the §10 handoff doctrine) — 承's title says what it will be without saying it.
    # 承 gets no English gloss (WP1 settled decision 9).
    parts = [
        (
            '<button class="hanko-btn" type="button" disabled '
            'title="承 — becomes an order · not wired: that verb does not exist yet '
            '(ack is not it); to act now, use the hand-to-an-agent block">承</button>'
        ),
        btn("認", "ack", "acknowledge", cmd_for("ack")),
        btn("休", "snooze", "snooze", cmd_for("snooze") + " --until YYYY-MM-DD"),
        btn("済", "dismiss", "settle (dismiss)", cmd_for("dismiss") + ' --note "…"'),
    ]
    if action == "ack":
        parts.append(
            f'<span class="stamp-mark" title="acknowledged">認{_en("ack")}</span>'
        )
    return f'<span class="arr-btns">{"".join(parts)}</span>'


def _render_findings(conn, loop_name, latest_json, now, root):
    findings = _open_findings(conn, loop_name)
    if not findings:
        return ""
    latest_by_id = {}
    if latest_json and isinstance(latest_json.get("findings"), list):
        latest_by_id = {f.get("finding_id"): f for f in latest_json["findings"]}
    root_flag = root_flag_for(root)
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
        handoff_html = ""
        btns_html = ""
        if not suppressed:
            # B-11: the section-04 mock's hanko rank, wired to the static page's only
            # write path — each enabled stamp copies its ready-to-paste loopctl command
            # (§10: "the page may display the ready-to-paste command"; delivery moved
            # from a visible one-liner into data-copy/title, MINOR #3's root_flag
            # guarantee carries over — all three verbs share root_flag_for()). 承 is
            # rendered disabled: ack ≠ approval is settled doctrine and the approve
            # verb does not exist (open thread — approve→action bridge).
            btns_html = _render_hanko_btns(loop_name, fid, root_flag, action)
            handoff_html = _render_finding_handoff(
                loop_name, fid, title, severity, detail, f, root, root_flag
            )
        else:
            cmd = f'<code class="cmd">loopctl reopen {e(loop_name)} {e(fid)}{e(root_flag)}</code>'
            kanji = _DISP_KANJI.get(action)
            if kanji:
                gloss = _DISP_GLOSS.get(action, action)
                btns_html = (
                    f'<span class="arr-btns"><span class="stamp-mark" '
                    f'title="{e(dtext or action)}">{kanji}{_en(gloss)}</span></span>'
                )
        detail_html = f"<div>{e(detail)}</div>" if detail else ""
        # pancaked — the same finding across N rounds is one stack, one decision
        pchip = ""
        if (f.get("times_seen") or 0) >= 2:
            pchip = (
                f'<span class="pchip"><i>巡</i>{_en("seen")} '
                f"×{int(f['times_seen'])}</span>"
            )
        out.append(
            f'<div class="{cls}" data-sev="{e(severity)}">'
            f'<div class="f-main">'
            f'<span class="sev {e(severity)}">{e(severity)}</span>'
            f'<span class="fid">{e(fid)}</span> — {e(title)} {pchip}'
            f'<span class="recurrence">{e(recurrence)}</span>{detail_html}{cmd}'
            f"{handoff_html}</div>"
            f"{btns_html}</div>"
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


def _render_console_controls(loop):
    """Hidden-by-default control cell (rounds toggle + schedule-edit button) for one loop
    row. Pure inert markup: the page's own hydration script (see _wrap_html) is what
    removes `hidden` — and only once `fetch('api/state')` succeeds, i.e. only when the
    page is served by `loopctl serve` (Task 3's bin/console.py). Opened as a plain file,
    the fetch fails and this stays hidden forever — same as before Task 4."""
    name = loop["name"]
    disabled_attr = (
        ""
        if loop["installed"]
        else ' disabled title="install from CLI: loopctl install"'
    )
    checked = "true" if (loop["installed"] and loop["enabled"]) else "false"
    return (
        f'<span class="con-cell" data-console-controls hidden data-loop="{e(name)}" '
        f'data-installed="{"1" if loop["installed"] else ""}" '
        f'data-enabled="{"1" if loop["enabled"] else ""}" '
        f'data-schedule="{e(loop["schedule"] or "")}">'
        f'<button class="con-sw" type="button" role="switch" aria-checked="{checked}" '
        f'aria-label="toggle rounds for {e(name)}"{disabled_attr}>'
        '<span class="con-track"><span class="con-knob"></span></span></button>'
        f'<button class="con-sched" type="button" data-sched-edit '
        f'aria-label="edit schedule for {e(name)}">'
        f"{e(loop['schedule'] or 'manual')}</button>"
        "</span>"
    )


def _render_report_block(loop):
    """Report links + dated history for the accordion body (replaces retired reports screen).
    Reuses loop['page'] state from _page_state — does not re-scan the filesystem."""
    page = loop.get("page") or {}
    href = page.get("href")
    report_href = loop.get("report_href")
    if not href and not report_href:
        return ""
    name = loop["name"]
    links = []
    if href:
        badge = (
            ' <span class="badge page-stale">stale</span>' if page.get("stale") else ""
        )
        links.append(f'<a href="{e(href)}">page</a>{badge}')
    if report_href:
        links.append(f'<a href="{e(report_href)}">latest.md</a>')
    dated = page.get("dated") or []
    shown = dated[:30]
    more = (
        f' <span class="muted">+{len(dated) - 30} older</span>'
        if len(dated) > 30
        else ""
    )
    history = ""
    if shown or more:
        history = (
            '<div class="history">'
            + " ".join(
                f'<a href="../reports/{e(name)}/{e(d)}">{e(d)}</a>' for d in shown
            )
            + more
            + "</div>"
        )
    return (
        f'<div class="report-block"><div class="report-links">'
        f"{' · '.join(links)}</div>{history}</div>"
    )


def _render_loop_summary(loop, now):
    """The <summary> content for one garden accordion row (glance tier)."""
    badges = []
    if loop["stale"]:
        badges.append("stale")
    if loop.get("running"):
        badges.append("running")
    if loop.get("overdue"):
        badges.append("overdue")
    if loop["died"]:
        badges.append("died")
    stamp = _stamp_html(loop["light_color"], loop["light_marker"], badges)

    latest = loop["latest_run"]
    # 巡 gloss is "run" → "last 巡 run …" / "next 巡 run …"
    if latest:
        started = latest["started_at"] or ""
        abs_short = started[5:16].replace("T", " ") if len(started) >= 16 else started
        last_run = (
            f"last 巡{_en('run')} {e(format_relative(started, now))} · {e(abs_short)}"
        )
    else:
        last_run = f"last 巡{_en('run')} never"

    spend_tok, spend_cost = loop["spend_7d"]
    spend_text = f"7d {fmt_num(spend_tok)} tok"
    if spend_cost:
        spend_text += f" (${spend_cost:.2f})"

    # B-11: scheduled states render as the section-04 mock's rounds switch — read-only
    # (the write path stays loopctl/console; the console's own toggle replaces this when
    # console-active). Same three states, kanji, and title strings as before (§10).
    _switch = (
        '<span class="sw {cls}" role="img" aria-label="{aria}" title="{title}">'
        '<span class="sw-track"><span class="sw-knob"></span></span>'
        '<span class="sw-lab">{kanji}</span>{gloss}</span>'
    )
    if loop["schedule"] == "manual":
        sw = (
            f'<span class="sw manual" title="manual — run via loopctl">'
            f"手{_en('manual')}</span>"
        )
        next_html = '<span class="rm-next off">manual</span>'
    elif loop["installed"] and loop["enabled"]:
        sw = _switch.format(
            cls="on",
            aria="rounds on",
            title="schedule loaded (launchd)",
            kanji="巡",
            gloss=_en("on"),
        )
        next_html = (
            f'<span class="rm-next">next 巡{_en("run")} '
            f"{e(loop['next_run_text'])}</span>"
        )
    elif loop["installed"]:
        sw = _switch.format(
            cls="off paused",
            aria="rounds paused",
            title="rounds paused — resume from console or loopctl resume",
            kanji="休",
            gloss=_en("paused"),
        )
        next_html = '<span class="rm-next off">paused</span>'
    else:
        sw = _switch.format(
            cls="off",
            aria="rounds off",
            title="no schedule loaded — supervised runs only",
            kanji="休",
            gloss=_en("off"),
        )
        next_html = '<span class="rm-next off">no schedule loaded</span>'

    # Report links live in the expansion body (links inside <summary> fight the toggle).
    toko = "".join(loop["toko_lines"]) or _toko_line(
        "未", "", '<span class="muted">never run</span>'
    )
    controls = _render_console_controls(loop)
    tags_html = _render_tag_chips(loop["tags"])
    owner_html = _render_owner_chip(loop)
    return (
        f"{stamp}"
        f'<div class="loop-name">{e(loop["name"])}{owner_html}'
        f"{tags_html}"
        f"<small>{e(loop['schedule'])} · {e(loop['description'])}</small></div>"
        f'<div class="toko"><div class="toko-scroll">{toko}</div>'
        '<span class="toko-tag">latest</span></div>'
        f'<div class="run-meta"><span class="rm-when">{last_run}</span>'
        f'<span class="rm-cost">{e(spend_text)}</span>{next_html}</div>'
        f'<div class="sw-cell">{sw}{controls}</div>'
    )


def _render_loop_row(loop, conn, now):
    """One garden accordion: <details name="garden"> with summary glance + section body."""
    data_tags = _data_tags_attr(loop["tags"])
    data_owner = _data_owner_attr(loop)
    # B-19: always emitted like data-tags/data-owner ("" = no events yet) —
    # the client-side recency sort reads it off every row.
    data_latest = f' data-latest-event="{e(loop["latest_event_ts"] or "")}"'
    name = loop["name"]
    summary = _render_loop_summary(loop, now)
    body = _render_loop_section(loop, conn, now)
    return (
        f'<details class="loop-row" name="garden" id="loop-{e(name)}"{data_tags}{data_owner}{data_latest}>'
        f"<summary>{summary}</summary>"
        f"{body}"
        f"</details>"
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
    if loop.get("running"):
        badges.append("running")
    if loop.get("overdue"):
        badges.append("overdue")
    if loop["died"]:
        badges.append("died")
    stamp = _stamp_html(loop["light_color"], loop["light_marker"], badges)

    latest = loop["latest_run"]
    run_metrics = _run_metrics(conn, latest["run_id"]) if latest else []
    run_metrics_by_key = {m["key"]: m for m in run_metrics}

    panels_html, declared_keys = _render_panels(
        loop["dashboard_json"], conn, loop["name"], run_metrics_by_key, now
    )
    findings_html = _render_findings(
        conn, loop["name"], loop["latest_json"], now, loop["root"]
    )
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
    provenance_html = _render_provenance(loop.get("provenance"))
    report_block_html = _render_report_block(loop)
    permalink = (
        f'<a class="permalink" href="#loop-{e(loop["name"])}" '
        f'title="link to this loop">#</a>'
    )

    # id="loop-<name>" lives on the outer <details>; section is the expansion body only.
    return (
        f'<section class="loop">'
        f"{permalink}"
        f"{report_block_html}"
        f'<h2>{stamp}<span class="lname">{e(loop["name"])}</span> '
        f'<span class="muted">{e(loop["description"])}</span></h2>'
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
    tags = conf.get("tags") or []
    owner, owner_assumed = resolve_owner(conf)
    provenance = _loop_provenance(conn, name)
    latest_event_ts = _latest_event_ts(conn, name)

    timeout_s = conf.get("timeout_s", 900)
    schedule_spec = conf.get("schedule", "manual")
    try:
        sched = schedule_parse(schedule_spec)
        expected_interval_s = sched.get("expected_interval_s", 0)
    except Exception:  # noqa: BLE001 — §10: a bad schedule must degrade, never crash the page
        expected_interval_s = 0

    died = False
    overdue = False
    running = False
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
        # (Amendment 2) in-flight, not yet past the died threshold -- split by age
        # into running (still inside timeout_s) vs overdue (past it, amber attention).
        fin, st = latest_run.get("finished_at"), latest_run["started_at"]
        if is_overdue(fin, st, timeout_s, now):
            overdue = True
            light_color, light_marker = "amber", None
        elif is_running(fin, st, timeout_s, now):
            running = True
            light_color, light_marker = "grey", None
        else:
            # started_at unparseable -- degrade to the pre-Amendment-2 default (§10)
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
        "tags": tags,
        "owner": owner,
        "owner_assumed": owner_assumed,
        "provenance": provenance,
        "latest_event_ts": latest_event_ts,
        "died": died,
        "overdue": overdue,
        "running": running,
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
):
    names = _discover_loops(root)
    loops = [
        _resolve_loop(
            root, name, conn, loopconf_parse, schedule_parse, now, envelope_mod
        )
        for name in names
    ]
    # B-19: default garden order is RECENCY — newest lifecycle event first
    # (a just-created loop tops the garden), event-less loops last in name
    # order. Server-side so the static page is correct before any JS runs;
    # the sort-order select re-sorts client-side (FLIP) from the same
    # data-latest-event attribute.
    loops.sort(key=lambda loop: loop["latest_event_ts"] or "", reverse=True)
    return loops


def generate(
    root=None,
    out_file=None,
    loopconf_parse=None,
    schedule_parse=None,
    now=None,
    return_html=False,
):
    """Generates dashboard/loops.html via atomic rename."""
    root = root or os.environ.get("LOOPS_ROOT") or os.getcwd()
    root = os.path.abspath(root)
    out_file = out_file or os.path.join(root, "dashboard", "loops.html")
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
        )

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
    return html if return_html else out_file


def _render_page(
    loops, counts, needs_attention_count, spend_today, spend_7d_fleet, now, conn, events
):
    top_chips = "".join(
        f'<span class="chip"><span class="jp">{_STAMP_KANJI[c]}</span>'
        f"{_en(_STAMP_GLOSS[c])} <b>{n}</b></span>"
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
        '<button type="button" id="theme-toggle" onclick="loopsToggleTheme()" '
        'title="toggle light/dark" aria-label="toggle color theme">◐</button>'
        "</div></div>"
    )

    events_html = _render_events_strip(events, now)

    if not loops:
        body = (
            f"{events_html}"
            '<main><div class="empty">No loops configured yet.</div></main>'
        )
        return _wrap_html(top, body)

    # B-17/B-19: the filter/sort controls live in the kicker (the old glossary
    # note is gone). Owner select always renders (every loop resolves an
    # owner); tag select keeps its only-when-tags-exist rule; sort defaults to
    # recent — matching the server-rendered row order.
    owner_options = sorted({loop["owner"] for loop in loops})
    owner_opts = '<option value="">all owners</option>' + "".join(
        f'<option value="{e(o)}">{e(o)}</option>' for o in owner_options
    )
    filter_parts = [
        (
            "<label>owner"
            f'<select id="owner-filter" onchange="loopsApplyFilters()">{owner_opts}</select>'
            "</label>"
        )
    ]
    tag_options = sorted({t for loop in loops for t in loop["tags"]})
    if tag_options:
        opts = '<option value="">all tags</option>' + "".join(
            f'<option value="{e(t)}">{e(t)}</option>' for t in tag_options
        )
        filter_parts.append(
            "<label>tag"
            f'<select id="tag-filter" onchange="loopsApplyFilters()">{opts}</select>'
            "</label>"
        )
    filter_parts.append(
        "<label>sort"
        '<select id="sort-order" onchange="loopsApplySort()">'
        '<option value="recent" selected>recent</option>'
        '<option value="name">name</option>'
        "</select></label>"
    )
    filters_html = f'<span class="filters">{"".join(filter_parts)}</span>'

    # Accordion rows include each loop's section body; no separate sections stack below.
    global_rows = "".join(_render_loop_row(loop, conn, now) for loop in loops)
    garden = (
        '<div class="zone"><div class="kicker"><b>庭</b> the garden — all loops'
        f"{filters_html}</div>"
        f'<div class="garden">{global_rows}</div></div>'
    )

    body = f"{events_html}<main>{garden}</main>"
    return _wrap_html(top, body)


# Shared schedule-picker panel + hydration script (Task 4). Static (no interpolation) --
# one instance per page, placed as a sibling of .sheet (not nested inside it) so the
# panel's position:absolute math (rect + window.scrollX/scrollY, both document-relative)
# isn't thrown off by .sheet's own `position: relative`. Everything here is inert until
# fetch('api/state') succeeds; opened as a plain file it never runs past the .catch().
_CONSOLE_CONTROLS_HTML = r"""<div class="sched-panel" data-sched-panel hidden>
  <div class="sp-presets">
    <button data-spec="interval:5m">5m</button><button data-spec="interval:15m">15m</button>
    <button data-spec="interval:30m">30m</button><button data-spec="interval:1h">hourly</button>
    <button data-kind="daily">daily</button><button data-kind="weekly">weekly</button>
    <button data-kind="monthly">monthly</button>
  </div>
  <div class="sp-form" hidden>
    <select class="sp-dow" hidden><option>mon</option><option>tue</option><option>wed</option>
      <option>thu</option><option>fri</option><option>sat</option><option>sun</option></select>
    <input class="sp-dom" type="number" min="1" max="28" value="1" hidden>
    <input class="sp-time" type="time" value="09:00">
    <button class="sp-apply" type="button">apply</button>
  </div>
  <div class="sp-err" role="alert"></div>
</div>
<script>
(function(){
  'use strict';
  // B-11: hanko copy stamps -- run on the static page too (no console involved). The
  // stamp copies its ready-to-paste loopctl command; if the clipboard is unavailable
  // (file:// open, denied permission), the command is revealed as a selectable line.
  document.addEventListener('click', function(ev){
    var hb = ev.target.closest('.hanko-btn[data-copy]');
    if (!hb || hb.disabled) return;
    var cmd = hb.getAttribute('data-copy');
    var rank = hb.closest('.arr-btns');
    function note(msg){
      var n = rank.querySelector('.copy-note');
      if (!n){ n = document.createElement('span'); n.className = 'copy-note'; rank.appendChild(n); }
      n.textContent = msg;
      clearTimeout(n._t); n._t = setTimeout(function(){ n.textContent = ''; }, 1800);
    }
    function fallback(){
      var row = hb.closest('.finding');
      var line = row.querySelector('.cmd[data-copy-fallback]');
      if (!line){
        line = document.createElement('code');
        line.className = 'cmd'; line.setAttribute('data-copy-fallback', '');
        row.querySelector('.f-main').appendChild(line);
      }
      line.textContent = cmd;
      note('clipboard blocked -- select the line');
    }
    if (navigator.clipboard && navigator.clipboard.writeText){
      navigator.clipboard.writeText(cmd).then(function(){ note('copied'); }, fallback);
    } else { fallback(); }
  });
  fetch('api/state').then(function(r){ if(!r.ok) throw 0; return r.json(); }).then(function(){
    document.querySelectorAll('[data-console-controls]').forEach(function(c){ c.hidden=false; });
    document.documentElement.classList.add('console-active');
  }).catch(function(){ /* static file mode -- controls stay hidden */ });
  // The .catch normalizes ANY transport-level failure (console stopped mid-session, a
  // response that isn't JSON) into the same {ok:false, j:{error}} shape a 4xx/5xx takes,
  // so every caller's existing else-branch runs: the rounds switch gets re-enabled and
  // the message surfaces. Without it a rejected promise skipped those branches and left
  // the switch disabled forever, with nothing shown.
  function post(path, body){
    return fetch(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})
      .then(function(r){ return r.json().then(function(j){ return {ok:r.ok, j:j}; }); })
      .catch(function(err){ return {ok:false, j:{error:'console unreachable: ' + err}}; });
  }
  document.addEventListener('click', function(ev){
    var sw = ev.target.closest('.con-sw');
    if (sw && !sw.disabled){
      // preventDefault: these controls sit inside <summary>; without it the click
      // would also toggle the garden accordion open/closed.
      ev.preventDefault();
      var cell = sw.closest('[data-console-controls]');
      var on = sw.getAttribute('aria-checked') !== 'true';
      sw.disabled = true;
      post('api/loops/' + cell.getAttribute('data-loop') + '/rounds', {on:on}).then(function(res){
        if (res.ok) location.reload(); else { sw.disabled=false; alert(res.j.error); }
      });
      return;
    }
    var ed = ev.target.closest('[data-sched-edit]');
    var panel = document.querySelector('[data-sched-panel]');
    if (ed){
      ev.preventDefault();
      var cell2 = ed.closest('[data-console-controls]');
      panel.dataset.loop = cell2.getAttribute('data-loop');
      panel.dataset.cur = cell2.getAttribute('data-schedule');
      var rect = ed.getBoundingClientRect();
      panel.style.top = (rect.bottom + window.scrollY + 6) + 'px';
      panel.style.left = Math.max(8, rect.left + window.scrollX - 120) + 'px';
      panel.querySelector('.sp-form').hidden = true;
      panel.querySelector('.sp-err').textContent = '';
      panel.hidden = false;
      return;
    }
    if (panel && !panel.hidden && !ev.target.closest('[data-sched-panel]')) panel.hidden = true;
  });
  var panel = document.querySelector('[data-sched-panel]');
  function apply(spec){
    post('api/loops/' + panel.dataset.loop + '/schedule', {spec:spec}).then(function(res){
      if (res.ok) location.reload(); else panel.querySelector('.sp-err').textContent = res.j.error;
    });
  }
  panel.addEventListener('click', function(ev){
    var b = ev.target.closest('button'); if (!b) return;
    if (b.dataset.spec) { apply(b.dataset.spec); return; }
    if (b.dataset.kind) {
      panel.dataset.kind = b.dataset.kind;
      panel.querySelector('.sp-form').hidden = false;
      panel.querySelector('.sp-dow').hidden = b.dataset.kind !== 'weekly';
      panel.querySelector('.sp-dom').hidden = b.dataset.kind !== 'monthly';
      var cur = panel.dataset.cur || '';
      var mTime = cur.match(/(\d{2}:\d{2})$/); if (mTime) panel.querySelector('.sp-time').value = mTime[1];
      return;
    }
    if (b.classList.contains('sp-apply')) {
      var t = panel.querySelector('.sp-time').value || '09:00';
      var k = panel.dataset.kind;
      // Each kind is applied only when it was explicitly chosen. The bare `else` this
      // replaces would have sent a MONTHLY spec for any unset/unknown kind; that path is
      // now unreachable anyway (the [hidden] CSS rule keeps .sp-form, and so this apply
      // button, undisplayed until a kind button sets panel.dataset.kind), but panel.dataset
      // .kind is never cleared between loops, so the guard stays explicit.
      if (k === 'daily') apply('daily:' + t);
      else if (k === 'weekly') apply('weekly:' + panel.querySelector('.sp-dow').value + ':' + t);
      else if (k === 'monthly') apply('monthly:' + String(panel.querySelector('.sp-dom').value).padStart(2, '0') + ':' + t);
    }
  });
})();
</script>
"""


def _wrap_html(top, body):
    # No-flash theme stamp: runs before <style> so data-theme is set before first paint.
    # localStorage key / attribute / values are pinned for WP3 report-page reuse.
    theme_stamp = (
        "<script>(function(){try{var t=localStorage.getItem('loops-theme');"
        "if(t==='dark'||t==='light'){document.documentElement.setAttribute('data-theme',t);}"
        "}catch(e){}})();</script>"
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>roops — 庭 the garden</title>"
        f"{theme_stamp}"
        f"<style>{CSS}</style></head><body>"
        f'<div class="sheet">{top}{body}'
        "<footer>loops harness — static sheet · report/propose-only · "
        "findings are actions in waiting</footer></div>"
        f"{_CONSOLE_CONTROLS_HTML}"
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
    parser.add_argument(
        "--now",
        default=None,
        help="pin the rendering clock (ISO8601, e.g. 2026-08-09T21:47:00Z) "
        "for byte-deterministic output over a fixed root",
    )
    args = parser.parse_args(argv)
    now = None
    if args.now:
        now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
    out = generate(root=args.root, out_file=args.out, now=now)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
