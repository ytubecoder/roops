#!/usr/bin/env python3
"""kagami shape-mirror — builds the mock loops-root the public ui.html renders from.

Usage: build_root.py DEST [--source ROOT]   (wipes DEST, prints PINNED_NOW)

The public mockup must look like the REAL garden does right now, so this builder
mirrors the real fleet's SHAPE and synthesizes every string and value:

  crosses the boundary : loop count, per-loop type/engine/schedule/enabled,
                         install state, latest run + statuses, run-history length,
                         age (bucketed), finding count/severity/times_seen,
                         panel count/types, report-file presence
  NEVER crosses        : names (mapped to a generic pool), headlines/titles/details
                         (templates), metric keys/values, tokens/spend, thresholds,
                         timestamps (rebased onto PINNED_NOW), paths, owners

Numbers are seeded from md5(mock-name + field) so output is deterministic for a
given fleet shape: the page changes only when the UI code or the fleet's coarse
shape changes — that byte-diff is kagami's drift signal. The precheck name-leak
gate remains the backstop behind this file.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PINNED_NOW = datetime(2026, 8, 9, 21, 47, 0, tzinfo=timezone.utc)

# Same pattern generate.py's _page_state uses for dated report pages.
DATED_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}\.html$")

NAME_POOL = [
    "tls-certs",
    "dead-links",
    "deps-drift",
    "backup-verify",
    "smoke-probe",
    "log-rotate",
    "cert-renew",
    "queue-depth",
    "mail-relay",
    "disk-usage",
    "cron-audit",
    "dns-health",
    "cache-warm",
    "quota-watch",
    "mirror-sync",
    "uptime-probe",
]
OWNER_POOL = ["infra", "ops", "docs", "web", "data", "core"]

HEADLINES = {
    "ok": "all {n} targets clear",
    "warn": "{n} items need a look",
    "alert": "{n} checks failing",
}
FINDING_TITLES = [
    "target drifted from its recorded baseline",
    "response slower than the watched threshold",
    "expected artifact missing from last sweep",
    "stale entry survived past its horizon",
    "count moved outside the tracked band",
    "endpoint refused connection on last probe",
]
PANEL_TITLES = ["volume", "flagged", "backlog", "coverage", "lag", "burn"]


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def seeded(name, field, lo, hi):
    """Deterministic pseudo-value in [lo, hi] — never derived from real data."""
    h = int.from_bytes(hashlib.md5(f"{name}:{field}".encode()).digest()[:4], "big")
    return lo + h % (hi - lo + 1)


def mock_age(seconds, cadence):
    """Binary classification-preserving mock age. The garden classifies age as
    fresh vs stale (is_stale: overdue > 1.5x cadence) — mirror exactly that
    classification and nothing finer. The earlier quantized-multiple scheme
    moved a stale loop one bucket per real day (2x -> 3x -> 4x cadence), which
    drifted the page nightly and churned the refresh PR."""
    if seconds is None or seconds <= 1.5 * cadence:
        return min(cadence // 3, 4 * 3600)
    return 2 * cadence


def quantize_seen(times_seen):
    """Pancake tiers, not raw counts — a finding's times_seen increments every
    firing and would otherwise drift the page daily."""
    if times_seen <= 1:
        return 1
    if times_seen <= 4:
        return 3
    return 6


def cadence_seconds(schedule):
    s = (schedule or "").strip()
    if s.startswith("interval:"):
        v = s.split(":", 1)[1]
        mult = {"m": 60, "h": 3600, "d": 86400}.get(v[-1:], 60)
        try:
            return int(v[:-1]) * mult
        except ValueError:
            return 86400
    if s.startswith("weekly:"):
        return 7 * 86400
    if s.startswith("monthly:"):
        return 30 * 86400
    if s.startswith("times:"):
        return 86400 // max(1, s.count(",") + 1)
    return 86400


def parse_conf(path):
    conf = {}
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            conf[k.strip()] = v.strip().strip('"')
    except OSError:
        pass
    return conf


def read_fleet(source):
    """Read the real fleet's SHAPE (read-only; no string leaves this dict
    except via explicit mapping)."""
    fleet = []
    loops_d = source / "loops.d"
    names = (
        sorted(p.name for p in loops_d.iterdir() if (p / "loop.conf").is_file())
        if loops_d.is_dir()
        else []
    )
    conn = None
    db = source / "state" / "loops.sqlite"
    if db.is_file():
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)

    def q(sql, args):
        if conn is None:
            return []
        try:
            return conn.execute(sql, args).fetchall()
        except sqlite3.OperationalError:
            return []

    now_real = datetime.now(timezone.utc)
    for name in names:
        conf = parse_conf(loops_d / name / "loop.conf")
        panels = []
        panel_keys = set()
        try:
            dj = json.loads((loops_d / name / "dashboard.json").read_text())
            panels = [str(p.get("type", "number")) for p in dj.get("panels", [])]
            panel_keys = {str(p.get("metric", "")) for p in dj.get("panels", [])}
        except (OSError, ValueError):
            pass
        recent = q(
            "SELECT run_id, runner_status, loop_status, effective_status, "
            "started_at, finished_at FROM runs WHERE loop_name=? "
            "ORDER BY started_at DESC LIMIT 6",
            (name,),
        )
        age = None
        if recent and recent[0][4]:
            try:
                started = datetime.fromisoformat(
                    str(recent[0][4]).replace("Z", "+00:00")
                )
                age = max(0, int((now_real - started).total_seconds()))
            except ValueError:
                age = None
        # Disposition CLASSIFICATION per finding (latest wins) — the enum crosses,
        # never the note/ids. Lapsed snoozes render unsuppressed; keep them distinct.
        dispo = {}
        for fid, action, until in q(
            "SELECT finding_id, action, snooze_until FROM dispositions "
            "WHERE loop_name=? ORDER BY created_at",
            (name,),
        ):
            dispo[str(fid)] = (str(action or ""), str(until or ""))
        findings = []
        for fid, sev, seen in q(
            "SELECT finding_id, severity, times_seen FROM findings "
            "WHERE loop_name=? AND resolved_at IS NULL ORDER BY finding_id",
            (name,),
        ):
            action, until = dispo.get(str(fid), ("", ""))
            act = None
            if action == "snooze":
                act = "snooze" if until > iso(now_real) else "snooze-lapsed"
            elif action in ("ack", "dismiss"):
                act = action
            findings.append((str(sev), quantize_seen(int(seen or 1)), act))
        # Count (never name) the latest run's metric keys with no panel — they
        # render as the raw-fallback drawer, part of the visible feature surface.
        extra_metrics = 0
        if recent:
            keys = q(
                "SELECT DISTINCT key FROM metrics WHERE run_id=?",
                (str(recent[0][0]),),
            )
            extra_metrics = min(3, sum(1 for (k,) in keys if str(k) not in panel_keys))
        hb = q(
            "SELECT ok FROM heartbeats WHERE loop_name=? ORDER BY ts DESC LIMIT 1",
            (name,),
        )
        report_dir = source / "reports" / name
        render_sh = loops_d / name / "render.sh"
        dated_count = 0
        if report_dir.is_dir():
            dated_count = min(
                3, sum(1 for p in report_dir.iterdir() if DATED_RE.match(p.name))
            )
        fleet.append(
            {
                "real_name": name,
                "type": conf.get("type", "agent"),
                "engine": conf.get("engine", "codex"),
                "schedule": conf.get("schedule", "manual"),
                "enabled": conf.get("enabled", "true").lower() != "false",
                "owner": conf.get("owner", ""),
                "installed": (source / "launchd" / f"com.loops.{name}.plist").is_file(),
                "panels": panels[:4],
                "runner_status": recent[0][1] if recent else None,
                "loop_status": recent[0][2] if recent else None,
                "effective_status": recent[0][3] if recent else None,
                "age_s": age,
                # Per-run harness/status enums (newest first): these drive the
                # harness badge, fail-detail rows, and died classification.
                "runs": [
                    (str(r[1] or ""), str(r[3] or ""), r[5] is None) for r in recent
                ],
                "findings": findings,
                "extra_metrics": extra_metrics,
                "hb_ok": (hb[0][0] if hb else None),
                "has_report": (report_dir / "latest.json").is_file(),
                "page_enabled": render_sh.is_file() and os.access(render_sh, os.X_OK),
                "has_latest_html": (report_dir / "latest.html").is_file(),
                "dated_count": dated_count,
            }
        )
    if conn is not None:
        conn.close()
    for entry in fleet:
        if entry["real_name"] == "kagami":
            # Pin the mirror loop's own row to its post-merge truth (green, fresh,
            # no findings) — mirroring its live drift status makes every merge
            # breed an echo PR: merge -> kagami goes ok -> shape changed -> new PR.
            entry.update(
                runner_status="completed",
                loop_status="ok",
                effective_status="ok",
                age_s=None,
                findings=[],
                runs=[("completed", "ok", False)] * max(1, len(entry["runs"])),
            )
    return fleet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dest")
    ap.add_argument(
        "--source",
        default=None,
        help="real loops root to mirror (default: harness repo root)",
    )
    args = ap.parse_args()
    source = Path(args.source).resolve() if args.source else REPO
    dest = Path(args.dest).resolve()
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copytree(
        REPO / "bin",
        dest / "bin",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for sub in ("reports", "launchd", "state", "loops.d"):
        (dest / sub).mkdir()

    fleet = read_fleet(source)
    owner_map = {
        o: OWNER_POOL[i % len(OWNER_POOL)]
        for i, o in enumerate(sorted({f["owner"] for f in fleet if f["owner"]}))
    }

    subprocess.run(
        [sys.executable, str(dest / "bin" / "db.py"), "init", "--root", str(dest)],
        check=True,
        capture_output=True,
    )
    conn = sqlite3.connect(dest / "state" / "loops.sqlite")
    cur = conn.cursor()

    for i, real in enumerate(fleet):
        name = NAME_POOL[i] if i < len(NAME_POOL) else f"probe-{i + 1:02d}"
        d = dest / "loops.d" / name
        d.mkdir()
        lines = [
            f"name={name}",
            f'description="{name.replace("-", " ")} — routine sweep"',
            f"owner={owner_map.get(real['owner'], 'ops')}",
            f"type={real['type']}",
            f"engine={real['engine']}",
            f"schedule={real['schedule']}",
            "timeout_s=900",
        ]
        if not real["enabled"]:
            lines.append("enabled=false")
        (d / "loop.conf").write_text("\n".join(lines) + "\n")
        if real["installed"]:
            (dest / "launchd" / f"com.loops.{name}.plist").write_text("<plist/>\n")

        if real["panels"]:
            panels = []
            for j, ptype in enumerate(real["panels"]):
                p = {
                    "title": PANEL_TITLES[(i + j) % len(PANEL_TITLES)],
                    "metric": f"{name}.m{j}",
                    "type": ptype,
                    "missing": "gap",
                }
                if ptype == "number":
                    p["direction"] = "higher_is_worse"
                    p["thresholds"] = {"warn": 1, "alert": 3}
                if ptype == "trend":
                    p["window_days"] = 30
                panels.append(p)
            (d / "dashboard.json").write_text(json.dumps({"panels": panels}))

        eff = real["effective_status"]
        headline = HEADLINES.get(
            real["loop_status"] or "", "swept — nothing tracked"
        ).format(n=seeded(name, "n", 2, 38))
        cadence = cadence_seconds(real["schedule"])
        if real["runs"]:
            age = mock_age(real["age_s"], cadence)
            started = PINNED_NOW - timedelta(seconds=age)
            for r, (rstat, eff_r, unfinished) in enumerate(real["runs"]):
                rid = f"mock-{name}-{r}"
                run_started = started - timedelta(seconds=cadence * r)
                failed = rstat not in (
                    "completed",
                    "skipped-precheck",
                    "skipped-overlap",
                )
                if r == 0:
                    head = headline
                elif rstat.startswith("skipped"):
                    head = None
                else:
                    head = HEADLINES.get(eff_r or "", HEADLINES["ok"]).format(
                        n=seeded(name, f"h{r}", 2, 38)
                    )
                cur.execute(
                    "INSERT INTO runs (run_id, loop_name, started_at, finished_at, "
                    "engine, trigger, runner_status, loop_status, effective_status, "
                    "headline, tokens_total, cost_usd, error_detail) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        rid,
                        name,
                        iso(run_started),
                        None
                        if unfinished
                        else iso(
                            run_started
                            + timedelta(seconds=seeded(name, f"d{r}", 40, 200))
                        ),
                        real["engine"],
                        "launchd",
                        rstat or "completed",
                        real["loop_status"] if r == 0 else (eff_r or None),
                        eff if r == 0 else (eff_r or None),
                        head,
                        seeded(name, f"t{r}", 800, 14000),
                        round(seeded(name, f"c{r}", 0, 900) / 10000.0, 4)
                        if real["engine"] == "claude"
                        else None,
                        "engine exited 1 (mock diagnostics in run log)"
                        if failed
                        else None,
                    ),
                )
                for j, ptype in enumerate(real["panels"]):
                    cur.execute(
                        "INSERT INTO metrics (run_id, loop_name, ts, key, num) "
                        "VALUES (?,?,?,?,?)",
                        (
                            rid,
                            name,
                            iso(run_started),
                            f"{name}.m{j}",
                            seeded(name, f"m{j}r{r}", 0, 41),
                        ),
                    )
            # Panel-less metric keys on the latest run — renders the raw-fallback
            # "Other metrics" drawer the real garden shows.
            for k in range(real["extra_metrics"]):
                cur.execute(
                    "INSERT INTO metrics (run_id, loop_name, ts, key, num) "
                    "VALUES (?,?,?,?,?)",
                    (
                        f"mock-{name}-0",
                        name,
                        iso(started),
                        f"{name}.x{k}",
                        seeded(name, f"x{k}", 0, 97),
                    ),
                )

        mock_findings = []
        for j, (sev, seen, act) in enumerate(real["findings"]):
            fid = f"{name}:item-{j + 1}"
            title = FINDING_TITLES[(i + j) % len(FINDING_TITLES)]
            last = PINNED_NOW - timedelta(seconds=mock_age(real["age_s"], cadence))
            cur.execute(
                "INSERT INTO findings (loop_name, finding_id, title, severity, "
                "first_seen_run, first_seen_at, last_seen_run, last_seen_at, "
                "times_seen) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    name,
                    fid,
                    title,
                    sev,
                    f"mock-{name}-0",
                    iso(last - timedelta(days=max(0, seen - 1))),
                    f"mock-{name}-0",
                    iso(last),
                    seen,
                ),
            )
            if act:
                until_val = None
                if act == "snooze":
                    until_val = iso(PINNED_NOW + timedelta(days=3))
                elif act == "snooze-lapsed":
                    until_val = iso(PINNED_NOW - timedelta(days=1))
                cur.execute(
                    "INSERT INTO dispositions (loop_name, finding_id, action, "
                    "note, snooze_until, created_at) VALUES (?,?,?,?,?,?)",
                    (
                        name,
                        fid,
                        "snooze" if act.startswith("snooze") else act,
                        "reviewed — routine, no action needed",
                        until_val,
                        iso(PINNED_NOW - timedelta(days=2)),
                    ),
                )
            # latest.json is the runner's suppression-FILTERED contract — keep the
            # mock consistent: dismissed/actively-snoozed findings stay DB-only.
            if act not in ("dismiss", "snooze"):
                mock_findings.append(
                    {
                        "finding_id": fid,
                        "severity": sev,
                        "title": title,
                        "detail": "Synthetic mirror of a live condition of this "
                        "severity; values on this public page are generated mock "
                        "data.",
                    }
                )

        if real["type"] == "watchdog":
            ok = 1 if (real["hb_ok"] in (1, None)) else 0
            for r in range(5):
                cur.execute(
                    "INSERT INTO heartbeats (loop_name, run_id, ts, ok, detail) "
                    "VALUES (?,?,?,?,?)",
                    (
                        name,
                        f"hb-{name}-{r}",
                        iso(
                            PINNED_NOW
                            - timedelta(
                                seconds=cadence_seconds(real["schedule"]) * (r + 1)
                            )
                        ),
                        ok if r == 0 else 1,
                        "probe ok" if (ok or r) else "probe failed",
                    ),
                )

        rd = dest / "reports" / name
        if real["has_report"] or real["has_latest_html"] or real["dated_count"]:
            rd.mkdir(exist_ok=True)
        if real["page_enabled"]:
            # An executable render.sh is what marks a loop page-enabled to the
            # garden ("page" link); the stub is never run by the mirror.
            rs = d / "render.sh"
            rs.write_text("#!/bin/sh\nexit 0\n")
            rs.chmod(0o755)
        page_stub = (
            '<!doctype html><meta charset="utf-8">'
            f"<title>{name} — mock report</title>"
            "<p>Generated mock report page. All figures here are mock data.</p>\n"
        )
        if real["has_latest_html"]:
            (rd / "latest.html").write_text(page_stub)
        for k in range(real["dated_count"]):
            stamp = (PINNED_NOW - timedelta(days=k + 1)).strftime("%Y-%m-%d-%H%M")
            (rd / f"{stamp}.html").write_text(page_stub)
        if real["has_report"]:
            contract = {
                "status": real["loop_status"] or "ok",
                "headline": headline,
                "findings": mock_findings,
                "report_markdown": f"# {name}\nRoutine sweep. {headline}. "
                "All figures on this page are generated mock data.\n",
            }
            (rd / "latest.json").write_text(json.dumps(contract))
            (rd / "latest.md").write_text(contract["report_markdown"])

        created = PINNED_NOW - timedelta(days=20 + seeded(name, "born", 0, 30))
        cur.execute(
            "INSERT INTO loop_events (loop_name, event, actor, ts) VALUES (?,?,?,?)",
            (name, "created", "gardener", iso(created)),
        )
        if real["installed"]:
            cur.execute(
                "INSERT INTO loop_events (loop_name, event, actor, ts) "
                "VALUES (?,?,?,?)",
                (name, "installed", "gardener", iso(created + timedelta(hours=7))),
            )
        if not real["enabled"]:
            cur.execute(
                "INSERT INTO loop_events (loop_name, event, actor, ts) "
                "VALUES (?,?,?,?)",
                (name, "paused", "gardener", iso(PINNED_NOW - timedelta(days=4))),
            )

    conn.commit()
    conn.close()
    print(iso(PINNED_NOW))


if __name__ == "__main__":
    main()
