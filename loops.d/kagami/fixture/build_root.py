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
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PINNED_NOW = datetime(2026, 8, 9, 21, 47, 0, tzinfo=timezone.utc)

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


def bucket_age(seconds):
    if seconds < 0:
        return 0
    if seconds < 172800:
        return round(seconds / 3600.0) * 3600
    return round(seconds / 86400.0) * 86400


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
        try:
            dj = json.loads((loops_d / name / "dashboard.json").read_text())
            panels = [str(p.get("type", "number")) for p in dj.get("panels", [])]
        except (OSError, ValueError):
            pass
        latest = q(
            "SELECT runner_status, loop_status, effective_status, started_at "
            "FROM runs WHERE loop_name=? ORDER BY started_at DESC LIMIT 1",
            (name,),
        )
        age = None
        if latest and latest[0][3]:
            try:
                started = datetime.fromisoformat(
                    str(latest[0][3]).replace("Z", "+00:00")
                )
                age = max(0, int((now_real - started).total_seconds()))
            except ValueError:
                age = None
        findings = q(
            "SELECT severity, times_seen FROM findings "
            "WHERE loop_name=? AND resolved_at IS NULL ORDER BY finding_id",
            (name,),
        )
        hb = q(
            "SELECT ok FROM heartbeats WHERE loop_name=? ORDER BY ts DESC LIMIT 1",
            (name,),
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
                "runner_status": latest[0][0] if latest else None,
                "loop_status": latest[0][1] if latest else None,
                "effective_status": latest[0][2] if latest else None,
                "age_s": age,
                "run_count": min(
                    6,
                    len(
                        q("SELECT run_id FROM runs WHERE loop_name=? LIMIT 6", (name,))
                    ),
                ),
                "findings": [(str(sev), int(seen or 1)) for sev, seen in findings],
                "hb_ok": (hb[0][0] if hb else None),
                "has_report": (source / "reports" / name / "latest.json").is_file(),
            }
        )
    if conn is not None:
        conn.close()
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
        if real["runner_status"] is not None:
            age = bucket_age(real["age_s"] if real["age_s"] is not None else 3600)
            started = PINNED_NOW - timedelta(seconds=age)
            cadence = cadence_seconds(real["schedule"])
            failed = real["runner_status"] not in ("completed", "skipped-precheck")
            for r in range(real["run_count"] or 1):
                rid = f"mock-{name}-{r}"
                run_started = started - timedelta(seconds=cadence * r)
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
                        if (failed and r == 0)
                        else iso(
                            run_started
                            + timedelta(seconds=seeded(name, f"d{r}", 40, 200))
                        ),
                        real["engine"],
                        "launchd",
                        real["runner_status"] if r == 0 else "completed",
                        real["loop_status"] if r == 0 else "ok",
                        eff if r == 0 else "ok",
                        headline
                        if r == 0
                        else HEADLINES["ok"].format(n=seeded(name, f"h{r}", 2, 38)),
                        seeded(name, f"t{r}", 800, 14000),
                        round(seeded(name, f"c{r}", 0, 900) / 10000.0, 4)
                        if real["engine"] == "claude"
                        else None,
                        "engine exited 1 (mock diagnostics in run log)"
                        if (failed and r == 0)
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

        mock_findings = []
        for j, (sev, seen) in enumerate(real["findings"]):
            fid = f"{name}:item-{j + 1}"
            title = FINDING_TITLES[(i + j) % len(FINDING_TITLES)]
            last = PINNED_NOW - timedelta(seconds=bucket_age(real["age_s"] or 3600))
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
            mock_findings.append(
                {
                    "finding_id": fid,
                    "severity": sev,
                    "title": title,
                    "detail": "Synthetic mirror of a live condition of this severity; "
                    "values on this public page are generated mock data.",
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

        if real["has_report"]:
            rd = dest / "reports" / name
            rd.mkdir()
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
