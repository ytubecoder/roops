#!/usr/bin/env python3
"""kagami fixture — builds the pinned mock loops-root the public garden renders from.

Usage: build_root.py DEST   (wipes DEST, rebuilds, prints PINNED_NOW on stdout)

Everything here is deterministic: fixed PINNED_NOW, fixed rows, fixed names. The
regenerated page therefore changes ONLY when dashboard/generate.py (or pagekit
tokens) change — that byte-diff is kagami's drift signal. Mock data rules
(site/workflows/publish.txt): genericized loop names, organic never-round numbers,
no real loop names / paths / business data. The real-name leak gate in precheck.sh
is the enforcement backstop; this file is the front line.
"""

import json
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
PINNED_NOW = datetime(2026, 8, 9, 21, 47, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def ago(**kw):
    return PINNED_NOW - timedelta(**kw)


# name: (description, owner, type, engine, schedule, installed, enabled)
LOOPS = {
    "tls-certs": (
        "certificate inventory — expiry horizon across served domains",
        "infra",
        "agent",
        "codex",
        "daily:06:10",
        True,
        True,
    ),
    "dead-links": (
        "docs link rot — crawl published docs, report 404s",
        "docs",
        "agent",
        "claude",
        "daily:07:20",
        True,
        True,
    ),
    "deps-drift": (
        "dependency freshness — outdated + security-flagged packages",
        "infra",
        "agent",
        "codex",
        "weekly:mon:08:30",
        True,
        True,
    ),
    "backup-verify": (
        "nightly snapshot verification probe",
        "ops",
        "watchdog",
        "codex",
        "interval:4h",
        True,
        True,
    ),
    "smoke-probe": (
        "staging endpoint reachability probe",
        "ops",
        "watchdog",
        "codex",
        "interval:30m",
        True,
        True,
    ),
    "log-rotate": (
        "archive rotation audit (paused for the season)",
        "ops",
        "agent",
        "codex",
        "daily:23:50",
        False,
        False,
    ),
}

DASHBOARDS = {
    "tls-certs": {
        "panels": [
            {
                "title": "Expiring ≤30d",
                "metric": "certs.expiring_30d",
                "type": "number",
                "unit": "certs",
                "direction": "higher_is_worse",
                "thresholds": {"warn": 1, "alert": 3},
                "missing": "gap",
            },
            {
                "title": "Certs tracked",
                "metric": "certs.total",
                "type": "trend",
                "window_days": 30,
                "missing": "hold",
            },
        ]
    },
    "dead-links": {
        "panels": [
            {
                "title": "Broken links",
                "metric": "links.broken",
                "type": "number",
                "unit": "links",
                "direction": "higher_is_worse",
                "thresholds": {"warn": 1, "alert": 5},
                "missing": "gap",
            }
        ]
    },
    "deps-drift": {
        "panels": [
            {
                "title": "Outdated",
                "metric": "deps.outdated",
                "type": "trend",
                "window_days": 60,
                "missing": "hold",
            }
        ]
    },
}

CONTRACTS = {
    "tls-certs": {
        "status": "ok",
        "headline": "34 certs checked — nearest expiry in 41 days",
        "findings": [],
        "report_markdown": "# tls-certs\n34 certificates inventoried across 6 served "
        "domains. Nearest expiry: `edge-cache` in 41 days. No renewals due inside "
        "the 30-day window.\n",
    },
    "dead-links": {
        "status": "warn",
        "headline": "2 links rotting in docs",
        "findings": [
            {
                "finding_id": "docs-setup:404",
                "severity": "warn",
                "title": "setup guide links a moved install page (404)",
                "detail": "docs/setup.md → /install/quickstart returns 404; target "
                "moved during the docs reshuffle. 412 links crawled.",
            },
            {
                "finding_id": "api-ref:404",
                "severity": "info",
                "title": "api reference footnote target gone",
                "detail": "single footnote link in api-ref.md returns 404.",
            },
        ],
        "report_markdown": "# dead-links\n412 links crawled, 2 broken. The setup-guide "
        "break is user-facing; the api-ref one is a footnote.\n",
    },
    "deps-drift": {
        "status": "ok",
        "headline": "7 packages behind — none security-flagged",
        "findings": [],
        "report_markdown": "# deps-drift\n7 of 143 packages have newer releases; none "
        "carry security advisories. Largest lag: `imgtool` at 3 minors.\n",
    },
    "smoke-probe": {
        "status": "alert",
        "headline": "staging endpoint unreachable — 2 consecutive probes",
        "findings": [
            {
                "finding_id": "target:unreachable",
                "severity": "alert",
                "title": "staging endpoint refused connection twice running",
                "detail": "probe at :17 and :47 both refused; last success 61 minutes "
                "ago. Diagnosis engine suspects the reverse proxy restart loop.",
            }
        ],
        "report_markdown": "# smoke-probe\nTwo consecutive refused probes. The window "
        "matches the proxy's crash-loop signature.\n",
    },
}


def write_conf(root, name):
    desc, owner, type_, engine, schedule, _inst, enabled = LOOPS[name]
    d = root / "loops.d" / name
    d.mkdir(parents=True)
    lines = [
        f"name={name}",
        f'description="{desc}"',
        f"owner={owner}",
        f"type={type_}",
        f"engine={engine}",
        f"schedule={schedule}",
        "timeout_s=900",
    ]
    if not enabled:
        lines.append("enabled=false")
    (d / "loop.conf").write_text("\n".join(lines) + "\n")
    if name in DASHBOARDS:
        (d / "dashboard.json").write_text(json.dumps(DASHBOARDS[name]))


def add_run(
    cur,
    run_id,
    loop,
    started,
    status,
    headline,
    engine,
    tokens=None,
    cost=None,
    eff=None,
):
    cur.execute(
        "INSERT INTO runs (run_id, loop_name, started_at, finished_at, engine, "
        "trigger, runner_status, loop_status, effective_status, headline, "
        "tokens_total, cost_usd) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            loop,
            iso(started),
            iso(started + timedelta(seconds=64)),
            engine,
            "launchd",
            "completed",
            status,
            eff or status,
            headline,
            tokens,
            cost,
        ),
    )


def metric(cur, run_id, loop, ts, key, num):
    cur.execute(
        "INSERT INTO metrics (run_id, loop_name, ts, key, num) VALUES (?,?,?,?,?)",
        (run_id, loop, iso(ts), key, num),
    )


def main():
    dest = Path(sys.argv[1]).resolve()
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copytree(
        REPO / "bin",
        dest / "bin",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for sub in ("reports", "launchd", "state"):
        (dest / sub).mkdir()

    for name in LOOPS:
        write_conf(dest, name)
        installed = LOOPS[name][5]
        if installed:
            (dest / "launchd" / f"com.loops.{name}.plist").write_text("<plist/>\n")

    for name, contract in CONTRACTS.items():
        d = dest / "reports" / name
        d.mkdir()
        (d / "latest.json").write_text(json.dumps(contract))
        (d / "latest.md").write_text(contract["report_markdown"])

    subprocess.run(
        [sys.executable, str(dest / "bin" / "db.py"), "init", "--root", str(dest)],
        check=True,
        capture_output=True,
    )
    conn = sqlite3.connect(dest / "state" / "loops.sqlite")
    cur = conn.cursor()

    # tls-certs — six daily runs, ok, gentle cert-count trend
    for i, total in enumerate([33, 33, 34, 34, 34, 34]):
        started = ago(days=5 - i, hours=15, minutes=36 - i)
        rid = f"2026080{4 + i}-0611-tls"
        add_run(
            cur,
            rid,
            "tls-certs",
            started,
            "ok",
            "34 certs checked — nearest expiry in 41 days",
            "codex",
            1873,
        )
        metric(cur, rid, "tls-certs", started, "certs.total", total)
        metric(cur, rid, "tls-certs", started, "certs.expiring_30d", 0)

    # dead-links — four daily runs, warn, one pancaking finding
    for i in range(4):
        started = ago(days=3 - i, hours=14, minutes=27)
        rid = f"2026080{6 + i}-0720-links"
        add_run(
            cur,
            rid,
            "dead-links",
            started,
            "warn",
            "2 links rotting in docs",
            "claude",
            6421,
            0.0788,
        )
        metric(cur, rid, "dead-links", started, "links.checked", 412)
        metric(cur, rid, "dead-links", started, "links.broken", 2)
    cur.execute(
        "INSERT INTO findings (loop_name, finding_id, title, severity, "
        "first_seen_run, first_seen_at, last_seen_run, last_seen_at, times_seen) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "dead-links",
            "docs-setup:404",
            "setup guide links a moved install page (404)",
            "warn",
            "20260806-0720-links",
            iso(ago(days=3, hours=14, minutes=27)),
            "20260809-0720-links",
            iso(ago(hours=14, minutes=27)),
            4,
        ),
    )
    cur.execute(
        "INSERT INTO findings (loop_name, finding_id, title, severity, "
        "first_seen_run, first_seen_at, last_seen_run, last_seen_at, times_seen) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "dead-links",
            "api-ref:404",
            "api reference footnote target gone",
            "info",
            "20260809-0720-links",
            iso(ago(hours=14, minutes=27)),
            "20260809-0720-links",
            iso(ago(hours=14, minutes=27)),
            1,
        ),
    )

    # deps-drift — five weekly runs, ok, downward trend
    for i, out in enumerate([9, 8, 8, 7, 7]):
        started = ago(days=34 - 7 * i, hours=13, minutes=16)
        rid = f"202607{6 + i:02d}-0830-deps"
        add_run(
            cur,
            rid,
            "deps-drift",
            started,
            "ok",
            "7 packages behind — none security-flagged",
            "codex",
            11284,
        )
        metric(cur, rid, "deps-drift", started, "deps.outdated", out)
        metric(cur, rid, "deps-drift", started, "deps.security", 0)

    # backup-verify — healthy watchdog: heartbeats, one quiet run row
    add_run(
        cur,
        "20260809-1947-backup",
        "backup-verify",
        ago(hours=2),
        "ok",
        "rsync target verified — 213 GiB, snapshot 03:58",
        "codex",
    )
    for i in range(6):
        cur.execute(
            "INSERT INTO heartbeats (loop_name, run_id, ts, ok, detail) "
            "VALUES (?,?,?,?,?)",
            (
                "backup-verify",
                f"hb-backup-{i}",
                iso(ago(hours=2 + 4 * i)),
                1,
                "probe ok",
            ),
        )

    # smoke-probe — alerting watchdog: last two probes failed
    add_run(
        cur,
        "20260809-2117-smoke",
        "smoke-probe",
        ago(minutes=30),
        "alert",
        "staging endpoint unreachable — 2 consecutive probes",
        "codex",
        2941,
    )
    for i, ok in enumerate([0, 0, 1, 1, 1]):
        cur.execute(
            "INSERT INTO heartbeats (loop_name, run_id, ts, ok, detail) "
            "VALUES (?,?,?,?,?)",
            (
                "smoke-probe",
                f"hb-smoke-{i}",
                iso(ago(minutes=30 * (i + 1) - 13)),
                ok,
                "connection refused" if not ok else "probe ok",
            ),
        )
    cur.execute(
        "INSERT INTO findings (loop_name, finding_id, title, severity, "
        "first_seen_run, first_seen_at, last_seen_run, last_seen_at, times_seen) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "smoke-probe",
            "target:unreachable",
            "staging endpoint refused connection twice running",
            "alert",
            "20260809-2047-smoke",
            iso(ago(hours=1)),
            "20260809-2117-smoke",
            iso(ago(minutes=30)),
            2,
        ),
    )

    # log-rotate — paused: old ok run, no plist, paused event
    add_run(
        cur,
        "20260728-2351-logs",
        "log-rotate",
        ago(days=12, hours=2),
        "ok",
        "archives rotated — 18 pruned",
        "codex",
        1204,
    )

    events = [
        ("tls-certs", "created", ago(days=42)),
        ("tls-certs", "installed", ago(days=41)),
        ("dead-links", "created", ago(days=38)),
        ("dead-links", "installed", ago(days=38)),
        ("deps-drift", "created", ago(days=36)),
        ("deps-drift", "installed", ago(days=35)),
        ("backup-verify", "created", ago(days=29)),
        ("backup-verify", "installed", ago(days=29)),
        ("smoke-probe", "created", ago(days=21)),
        ("smoke-probe", "installed", ago(days=21)),
        ("log-rotate", "created", ago(days=33)),
        ("log-rotate", "installed", ago(days=33)),
        ("log-rotate", "paused", ago(days=12)),
    ]
    for loop, event, ts in events:
        cur.execute(
            "INSERT INTO loop_events (loop_name, event, actor, ts) VALUES (?,?,?,?)",
            (loop, event, "gardener", iso(ts)),
        )

    conn.commit()
    conn.close()
    print(iso(PINNED_NOW))


if __name__ == "__main__":
    main()
