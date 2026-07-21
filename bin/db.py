#!/usr/bin/env python3
"""bin/db.py — §3 sqlite schema + CLI. `state/loops.sqlite`, WAL +
busy_timeout=5000 on every connection. Schema created idempotently by
`init` (safe/cheap to re-run on every invocation).

Importable helpers mirror the CLI verbs 1:1 (see the CLI dispatch at the
bottom); `run-loop.sh` shells out to the CLI, `loopctl`/dashboard code may
import this module directly.
"""
import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

SCHEMA_VERSION = "1"

DDL = """
CREATE TABLE IF NOT EXISTS runs (
  run_id        TEXT PRIMARY KEY,
  loop_name     TEXT NOT NULL,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  duration_ms   INTEGER,
  engine        TEXT,
  model         TEXT,
  trigger       TEXT,
  runner_status TEXT NOT NULL,
  loop_status   TEXT,
  effective_status TEXT,
  status_reason TEXT,
  headline      TEXT,
  report_path   TEXT,
  contract_path TEXT,
  tokens_input  INTEGER,
  tokens_output INTEGER,
  tokens_total  INTEGER,
  cost_usd      REAL,
  usage_raw     TEXT,
  attempts      INTEGER,
  exit_code     INTEGER,
  error_detail  TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_loop_started ON runs(loop_name, started_at DESC);

CREATE TABLE IF NOT EXISTS heartbeats (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  loop_name TEXT NOT NULL,
  run_id    TEXT,
  ts        TEXT NOT NULL,
  ok        INTEGER NOT NULL,
  detail    TEXT
);
CREATE INDEX IF NOT EXISTS idx_hb_loop_ts ON heartbeats(loop_name, ts DESC);

CREATE TABLE IF NOT EXISTS metrics (
  run_id    TEXT NOT NULL,
  loop_name TEXT NOT NULL,
  ts        TEXT NOT NULL,
  key       TEXT NOT NULL,
  num       REAL,
  text      TEXT,
  PRIMARY KEY (run_id, key)
);
CREATE INDEX IF NOT EXISTS idx_metrics_loop_key_ts ON metrics(loop_name, key, ts);

CREATE TABLE IF NOT EXISTS findings (
  finding_id     TEXT NOT NULL,
  loop_name      TEXT NOT NULL,
  title          TEXT NOT NULL,
  severity       TEXT NOT NULL,
  first_seen_run TEXT NOT NULL,
  first_seen_at  TEXT NOT NULL,
  last_seen_run  TEXT NOT NULL,
  last_seen_at   TEXT NOT NULL,
  times_seen     INTEGER NOT NULL DEFAULT 1,
  resolved_at    TEXT,
  PRIMARY KEY (loop_name, finding_id)
);

CREATE TABLE IF NOT EXISTS dispositions (
  loop_name    TEXT NOT NULL,
  finding_id   TEXT NOT NULL,
  action       TEXT NOT NULL,
  note         TEXT,
  snooze_until TEXT,
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_disp_loop_finding ON dispositions(loop_name, finding_id, created_at DESC);

CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT);
"""

METRIC_DEPTH_CAP = 3
METRIC_KEY_MAXLEN = 128
METRIC_COUNT_CAP = 200


# ---------------------------------------------------------------------------
# Connection / init
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def db_path(root: str) -> str:
    return os.path.join(root, "state", "loops.sqlite")


def connect(root: str) -> sqlite3.Connection:
    path = db_path(root)
    state_dir = os.path.dirname(path)
    os.makedirs(state_dir, exist_ok=True)
    # §0 file-modes rule: state/ is 0700.
    try:
        os.chmod(state_dir, 0o700)
    except OSError:
        pass
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    # §0 file-modes rule: files written into state/ are 0600.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    with conn:
        conn.executescript(DDL)
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (SCHEMA_VERSION,),
        )


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def _parse_iso(ts: str) -> datetime:
    # Accept both "...Z" and "...+00:00" forms; stdlib only.
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def _duration_ms(started_at: str, finished_at: str):
    try:
        start = _parse_iso(started_at)
        end = _parse_iso(finished_at)
    except (ValueError, TypeError):
        return None
    delta = end - start
    return int(delta.total_seconds() * 1000)


def _normalize_date_for_compare(ts: str) -> str:
    """Normalize a date-only (YYYY-MM-DD) or full ISO8601 timestamp into a
    fully comparable ISO8601 string. Date-only values are treated as
    inclusive through end-of-day, so a snooze_until of a bare date still
    suppresses for the whole of that day."""
    if ts is None:
        return None
    if len(ts) == 10:
        return ts + "T23:59:59Z"
    return ts


# ---------------------------------------------------------------------------
# Usage extraction (§3 finish-run --usage-file)
# ---------------------------------------------------------------------------

def extract_usage(raw_text: str):
    """Best-effort usage/cost extraction. Never raises. Returns a dict with
    keys tokens_input, tokens_output, tokens_total, cost_usd — all
    nullable. Recognizes:
      - codex shape: JSONL, one JSON object per line, looking for a line
        with type == "turn.completed" and a .usage sub-object.
      - claude shape: a single JSON object with a .usage sub-object and
        optionally .total_cost_usd.
    Anything else (unparseable, unrecognized shape) yields all-null
    without crashing.
    """
    result = {"tokens_input": None, "tokens_output": None, "tokens_total": None, "cost_usd": None}
    if raw_text is None:
        return result
    text = raw_text.strip()
    if not text:
        return result

    # Try whole-text JSON object first (claude shape).
    try:
        obj = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        obj = None

    if isinstance(obj, dict) and "usage" in obj and isinstance(obj["usage"], dict):
        usage = obj["usage"]
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        if isinstance(input_tokens, (int, float)):
            result["tokens_input"] = int(input_tokens)
        if isinstance(output_tokens, (int, float)):
            result["tokens_output"] = int(output_tokens)
        if result["tokens_input"] is not None and result["tokens_output"] is not None:
            result["tokens_total"] = result["tokens_input"] + result["tokens_output"]
        cost = obj.get("total_cost_usd")
        if isinstance(cost, (int, float)):
            result["cost_usd"] = float(cost)
        return result

    # Try JSONL (codex shape): scan lines for a turn.completed event.
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (ValueError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "turn.completed" and isinstance(event.get("usage"), dict):
            usage = event["usage"]
            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            if isinstance(input_tokens, (int, float)):
                result["tokens_input"] = int(input_tokens)
            if isinstance(output_tokens, (int, float)):
                result["tokens_output"] = int(output_tokens)
            if result["tokens_input"] is not None and result["tokens_output"] is not None:
                result["tokens_total"] = result["tokens_input"] + result["tokens_output"]
            # codex: no cost field — cost_usd stays NULL.
            return result

    # Unknown shape / garbage — all null, never crash.
    return result


# ---------------------------------------------------------------------------
# Metric flattening (§3 / §9.3)
# ---------------------------------------------------------------------------

def _flatten_recursive(obj: dict, prefix: str, depth: int, out: dict) -> None:
    for k, v in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict) and depth < METRIC_DEPTH_CAP:
            _flatten_recursive(v, key, depth + 1, out)
        else:
            out[key] = v


def flatten_metrics(metrics_obj: dict) -> dict:
    """Flatten a parsed metrics JSON object per §3/§9.3 rules:
      - top-level keys become metric keys; nested objects flatten with '.'
        up to a dot-nesting depth cap of 3 (deeper structure is frozen as
        an opaque JSON blob at the depth-3 key).
      - arrays do NOT flatten — stored whole (JSON) in `text`.
      - numbers and booleans go to `num` (bool -> 0/1); everything else
        goes to `text` (JSON-encoded).
      - keys longer than 128 chars are truncated with a trailing '…'.
      - a run contributing more than 200 metrics is truncated to 200,
        plus an extra `metrics_truncated=1` marker metric.

    Returns {key: {"num": float|None, "text": str|None}}.
    """
    if not isinstance(metrics_obj, dict):
        return {}

    raw_flat = {}
    _flatten_recursive(metrics_obj, "", 1, raw_flat)

    # Key length cap.
    truncated_flat = {}
    for key, value in raw_flat.items():
        if len(key) > METRIC_KEY_MAXLEN:
            key = key[:METRIC_KEY_MAXLEN] + "…"
        truncated_flat[key] = value

    items = list(truncated_flat.items())
    was_truncated = len(items) > METRIC_COUNT_CAP
    if was_truncated:
        items = items[:METRIC_COUNT_CAP]

    out = {}
    for key, value in items:
        if isinstance(value, bool):
            out[key] = {"num": 1.0 if value else 0.0, "text": None}
        elif isinstance(value, (int, float)):
            out[key] = {"num": float(value), "text": None}
        else:
            out[key] = {"num": None, "text": json.dumps(value)}

    if was_truncated:
        out["metrics_truncated"] = {"num": 1.0, "text": None}

    return out


def _load_metrics_from_contract(contract_file: str) -> dict:
    try:
        with open(contract_file, "r") as f:
            contract = json.load(f)
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(contract, dict):
        return {}
    metrics_str = contract.get("metrics")
    if not isinstance(metrics_str, str):
        return {}
    try:
        parsed = json.loads(metrics_str)
    except (ValueError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return parsed


# ---------------------------------------------------------------------------
# CLI verb implementations
# ---------------------------------------------------------------------------

def cmd_init(args) -> int:
    conn = connect(args.root)
    try:
        init_db(conn)
    finally:
        conn.close()
    return 0


def cmd_start_run(args) -> int:
    conn = connect(args.root)
    try:
        init_db(conn)
        with conn:
            conn.execute(
                """
                INSERT INTO runs (run_id, loop_name, started_at, engine, model, trigger, runner_status)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    loop_name=excluded.loop_name, started_at=excluded.started_at,
                    engine=excluded.engine, model=excluded.model, trigger=excluded.trigger
                """,
                (args.run_id, args.loop, args.started_at, args.engine, args.model, args.trigger, "started"),
            )
    finally:
        conn.close()
    return 0


def cmd_finish_run(args) -> int:
    conn = connect(args.root)
    try:
        init_db(conn)
        finished_at = args.finished_at or now_iso()

        row = conn.execute("SELECT started_at FROM runs WHERE run_id=?", (args.run_id,)).fetchone()
        duration_ms = None
        if row is not None:
            duration_ms = _duration_ms(row["started_at"], finished_at)

        usage_raw = None
        usage = {"tokens_input": None, "tokens_output": None, "tokens_total": None, "cost_usd": None}
        if args.usage_file:
            try:
                with open(args.usage_file, "r") as f:
                    usage_raw = f.read()
            except OSError:
                usage_raw = None
            if usage_raw is not None:
                usage = extract_usage(usage_raw)

        with conn:
            conn.execute(
                """
                UPDATE runs SET
                    finished_at=?, duration_ms=?, runner_status=?, loop_status=?,
                    effective_status=?, status_reason=?, headline=?, report_path=?,
                    contract_path=?, exit_code=?, error_detail=?, attempts=?,
                    tokens_input=?, tokens_output=?, tokens_total=?, cost_usd=?, usage_raw=?
                WHERE run_id=?
                """,
                (
                    finished_at, duration_ms, args.runner_status, args.loop_status,
                    args.effective_status, args.status_reason, args.headline, args.report_path,
                    args.contract_path, args.exit_code, args.error_detail, args.attempts,
                    usage["tokens_input"], usage["tokens_output"], usage["tokens_total"],
                    usage["cost_usd"], usage_raw,
                    args.run_id,
                ),
            )
    finally:
        conn.close()
    return 0


def cmd_heartbeat(args) -> int:
    conn = connect(args.root)
    try:
        init_db(conn)
        with conn:
            conn.execute(
                "INSERT INTO heartbeats (loop_name, run_id, ts, ok, detail) VALUES (?, ?, ?, ?, ?)",
                (args.loop, args.run_id, now_iso(), args.ok, args.detail),
            )
    finally:
        conn.close()
    return 0


def cmd_record_metrics(args) -> int:
    conn = connect(args.root)
    try:
        init_db(conn)
        metrics_obj = _load_metrics_from_contract(args.contract_file)
        flat = flatten_metrics(metrics_obj)
        ts = now_iso()
        with conn:
            for key, val in flat.items():
                conn.execute(
                    """
                    INSERT INTO metrics (run_id, loop_name, ts, key, num, text)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(run_id, key) DO UPDATE SET
                        ts=excluded.ts, num=excluded.num, text=excluded.text, loop_name=excluded.loop_name
                    """,
                    (args.run_id, args.loop, ts, key, val["num"], val["text"]),
                )
    finally:
        conn.close()
    return 0


def cmd_upsert_findings(args) -> int:
    conn = connect(args.root)
    try:
        init_db(conn)
        try:
            with open(args.contract_file, "r") as f:
                contract = json.load(f)
        except (OSError, ValueError, json.JSONDecodeError):
            contract = {}
        findings = contract.get("findings") if isinstance(contract, dict) else None
        if not isinstance(findings, list):
            findings = []

        seen_ids = set()
        upserted = 0
        with conn:
            for finding in findings:
                if not isinstance(finding, dict):
                    continue
                fid = finding.get("finding_id")
                if not fid or fid in seen_ids:
                    continue
                seen_ids.add(fid)
                title = finding.get("title", "")
                severity = finding.get("severity", "")

                existing = conn.execute(
                    "SELECT times_seen FROM findings WHERE loop_name=? AND finding_id=?",
                    (args.loop, fid),
                ).fetchone()
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO findings
                          (finding_id, loop_name, title, severity, first_seen_run, first_seen_at,
                           last_seen_run, last_seen_at, times_seen, resolved_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, NULL)
                        """,
                        (fid, args.loop, title, severity, args.run_id, args.ts, args.run_id, args.ts),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE findings SET
                            title=?, severity=?, last_seen_run=?, last_seen_at=?,
                            times_seen=times_seen+1, resolved_at=NULL
                        WHERE loop_name=? AND finding_id=?
                        """,
                        (title, severity, args.run_id, args.ts, args.loop, fid),
                    )
                upserted += 1

            # Resolve previously-open findings absent from this run.
            open_rows = conn.execute(
                "SELECT finding_id FROM findings WHERE loop_name=? AND resolved_at IS NULL",
                (args.loop,),
            ).fetchall()
            resolved = 0
            for row in open_rows:
                if row["finding_id"] not in seen_ids:
                    conn.execute(
                        "UPDATE findings SET resolved_at=? WHERE loop_name=? AND finding_id=?",
                        (args.ts, args.loop, row["finding_id"]),
                    )
                    resolved += 1
    finally:
        conn.close()

    print(json.dumps({"upserted": upserted, "resolved": resolved}))
    return 0


def _current_disposition(conn, loop_name, finding_id):
    return conn.execute(
        """
        SELECT * FROM dispositions
        WHERE loop_name=? AND finding_id=?
        ORDER BY created_at DESC, rowid DESC
        LIMIT 1
        """,
        (loop_name, finding_id),
    ).fetchone()


def _format_disposition(disp) -> str:
    if disp is None or disp["action"] == "reopen":
        return "open"
    action = disp["action"]
    if action == "ack":
        date = disp["created_at"][:10]
        return f"ACKED {date}"
    if action == "dismiss":
        date = disp["created_at"][:10]
        note = disp["note"] or ""
        return f'DISMISSED {date} ("{note}")'
    if action == "snooze":
        until = disp["snooze_until"] or ""
        return f"SNOOZED until {until}"
    return "open"


def cmd_prior_findings(args) -> int:
    conn = connect(args.root)
    try:
        init_db(conn)
        rows = conn.execute(
            """
            SELECT * FROM findings
            WHERE loop_name=? AND resolved_at IS NULL
            ORDER BY first_seen_at ASC, finding_id ASC
            """,
            (args.loop,),
        ).fetchall()
        lines = []
        for row in rows:
            disp = _current_disposition(conn, args.loop, row["finding_id"])
            since_date = row["first_seen_at"][:10]
            disp_str = _format_disposition(disp)
            lines.append(
                f"{row['finding_id']}  seen {row['times_seen']}x since {since_date}  {disp_str}"
            )
    finally:
        conn.close()

    for line in lines:
        print(line)
    return 0


def cmd_suppressed(args) -> int:
    """JSON array of objects {finding_id, action, created_at, note,
    snooze_until} for findings whose current disposition is dismiss, or
    snooze with snooze_until > ts (§4.5). ack never suppresses (and is
    excluded, like reopen/no-disposition). The runner uses finding_id for
    filtering and the rest for the human-readable suppression footer
    (§4.5 exact format)."""
    conn = connect(args.root)
    try:
        init_db(conn)
        rows = conn.execute(
            "SELECT finding_id FROM findings WHERE loop_name=? AND resolved_at IS NULL",
            (args.loop,),
        ).fetchall()
        result = []
        ts_norm = _normalize_date_for_compare(args.ts)
        for row in rows:
            fid = row["finding_id"]
            disp = _current_disposition(conn, args.loop, fid)
            if disp is None or disp["action"] == "reopen":
                continue
            if disp["action"] == "dismiss":
                result.append({
                    "finding_id": fid,
                    "action": "dismiss",
                    "created_at": disp["created_at"],
                    "note": disp["note"],
                    "snooze_until": None,
                })
            elif disp["action"] == "snooze":
                until_norm = _normalize_date_for_compare(disp["snooze_until"])
                if until_norm is not None and until_norm > ts_norm:
                    result.append({
                        "finding_id": fid,
                        "action": "snooze",
                        "created_at": disp["created_at"],
                        "note": disp["note"],
                        "snooze_until": disp["snooze_until"],
                    })
    finally:
        conn.close()

    print(json.dumps(result))
    return 0


def cmd_dispose(args) -> int:
    if args.action == "dismiss" and not args.note:
        print("dismiss requires --note", file=sys.stderr)
        return 1
    if args.action == "snooze" and not args.until:
        print("snooze requires --until", file=sys.stderr)
        return 1

    conn = connect(args.root)
    try:
        init_db(conn)
        existing = conn.execute(
            "SELECT 1 FROM findings WHERE loop_name=? AND finding_id=?",
            (args.loop, args.finding_id),
        ).fetchone()
        if existing is None:
            print(f"unknown finding: {args.loop} {args.finding_id}", file=sys.stderr)
            return 1
        with conn:
            conn.execute(
                """
                INSERT INTO dispositions (loop_name, finding_id, action, note, snooze_until, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (args.loop, args.finding_id, args.action, args.note, args.until, now_iso()),
            )
    finally:
        conn.close()
    return 0


# ---------------------------------------------------------------------------
# Named queries (db.py query <name> ...)
# ---------------------------------------------------------------------------

def _rows_to_dicts(rows):
    return [dict(r) for r in rows]


def query_loops_summary(conn, args):
    rows = conn.execute(
        """
        SELECT r.* FROM runs r
        INNER JOIN (
            SELECT loop_name, MAX(started_at) AS max_started
            FROM runs GROUP BY loop_name
        ) latest ON r.loop_name = latest.loop_name AND r.started_at = latest.max_started
        """
    ).fetchall()
    return _rows_to_dicts(rows)


def query_last_runs(conn, args):
    rows = conn.execute(
        "SELECT * FROM runs WHERE loop_name=? ORDER BY started_at DESC LIMIT ?",
        (args.loop, args.limit),
    ).fetchall()
    return _rows_to_dicts(rows)


def query_metric_history(conn, args):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        "SELECT ts, num, text FROM metrics WHERE loop_name=? AND key=? AND ts >= ? ORDER BY ts ASC",
        (args.loop, args.key, cutoff),
    ).fetchall()
    return _rows_to_dicts(rows)


def query_open_findings(conn, args):
    rows = conn.execute(
        "SELECT * FROM findings WHERE loop_name=? AND resolved_at IS NULL ORDER BY first_seen_at ASC",
        (args.loop,),
    ).fetchall()
    return _rows_to_dicts(rows)


def query_heartbeats(conn, args):
    rows = conn.execute(
        "SELECT * FROM heartbeats WHERE loop_name=? ORDER BY ts DESC LIMIT ?",
        (args.loop, args.limit),
    ).fetchall()
    return _rows_to_dicts(rows)


def query_spend(conn, args):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = conn.execute(
        """
        SELECT loop_name,
               SUM(tokens_input) AS tokens_input,
               SUM(tokens_output) AS tokens_output,
               SUM(tokens_total) AS tokens_total,
               SUM(cost_usd) AS cost_usd
        FROM runs
        WHERE started_at >= ?
        GROUP BY loop_name
        """,
        (cutoff,),
    ).fetchall()
    return _rows_to_dicts(rows)


QUERY_DISPATCH = {
    "loops-summary": query_loops_summary,
    "last-runs": query_last_runs,
    "metric-history": query_metric_history,
    "open-findings": query_open_findings,
    "heartbeats": query_heartbeats,
    "spend": query_spend,
}


def cmd_query(args) -> int:
    fn = QUERY_DISPATCH.get(args.query_name)
    if fn is None:
        print(f"unknown query: {args.query_name}", file=sys.stderr)
        return 2
    conn = connect(args.root)
    try:
        init_db(conn)
        result = fn(conn, args)
    finally:
        conn.close()
    print(json.dumps(result))
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

def _default_root():
    return os.environ.get("LOOPS_ROOT", os.path.expanduser("~/projects/loops"))


def build_parser():
    p = argparse.ArgumentParser(prog="db.py")
    sub = p.add_subparsers(dest="verb")

    init_p = sub.add_parser("init")
    init_p.add_argument("--root", default=_default_root())

    start_p = sub.add_parser("start-run")
    start_p.add_argument("--root", default=_default_root())
    start_p.add_argument("--run-id", required=True, dest="run_id")
    start_p.add_argument("--loop", required=True)
    start_p.add_argument("--engine", required=True)
    start_p.add_argument("--model", default=None)
    start_p.add_argument("--trigger", required=True)
    start_p.add_argument("--started-at", required=True, dest="started_at")

    finish_p = sub.add_parser("finish-run")
    finish_p.add_argument("--root", default=_default_root())
    finish_p.add_argument("--run-id", required=True, dest="run_id")
    finish_p.add_argument("--runner-status", required=True, dest="runner_status")
    finish_p.add_argument("--loop-status", default=None, dest="loop_status")
    finish_p.add_argument("--effective-status", default=None, dest="effective_status")
    finish_p.add_argument("--attempts", default=None, type=int)
    finish_p.add_argument("--status-reason", default=None, dest="status_reason")
    finish_p.add_argument("--headline", default=None)
    finish_p.add_argument("--report-path", default=None, dest="report_path")
    finish_p.add_argument("--contract-path", default=None, dest="contract_path")
    finish_p.add_argument("--exit-code", default=None, type=int, dest="exit_code")
    finish_p.add_argument("--error-detail", default=None, dest="error_detail")
    finish_p.add_argument("--usage-file", default=None, dest="usage_file")
    finish_p.add_argument("--finished-at", default=None, dest="finished_at")

    hb_p = sub.add_parser("heartbeat")
    hb_p.add_argument("--root", default=_default_root())
    hb_p.add_argument("--loop", required=True)
    hb_p.add_argument("--run-id", default=None, dest="run_id")
    hb_p.add_argument("--ok", required=True, type=int, choices=[0, 1])
    hb_p.add_argument("--detail", default=None)

    rm_p = sub.add_parser("record-metrics")
    rm_p.add_argument("--root", default=_default_root())
    rm_p.add_argument("--run-id", required=True, dest="run_id")
    rm_p.add_argument("--loop", required=True)
    rm_p.add_argument("--contract-file", required=True, dest="contract_file")

    uf_p = sub.add_parser("upsert-findings")
    uf_p.add_argument("--root", default=_default_root())
    uf_p.add_argument("--run-id", required=True, dest="run_id")
    uf_p.add_argument("--loop", required=True)
    uf_p.add_argument("--contract-file", required=True, dest="contract_file")
    uf_p.add_argument("--ts", required=True)

    pf_p = sub.add_parser("prior-findings")
    pf_p.add_argument("--root", default=_default_root())
    pf_p.add_argument("--loop", required=True)

    sup_p = sub.add_parser("suppressed")
    sup_p.add_argument("--root", default=_default_root())
    sup_p.add_argument("--loop", required=True)
    sup_p.add_argument("--ts", required=True)

    disp_p = sub.add_parser("dispose")
    disp_p.add_argument("--root", default=_default_root())
    disp_p.add_argument("--loop", required=True)
    disp_p.add_argument("--finding-id", required=True, dest="finding_id")
    disp_p.add_argument("--action", required=True, choices=["ack", "dismiss", "snooze", "reopen"])
    disp_p.add_argument("--note", default=None)
    disp_p.add_argument("--until", default=None)

    query_p = sub.add_parser("query")
    query_p.add_argument("query_name")
    query_p.add_argument("--root", default=_default_root())
    query_p.add_argument("--loop", default=None)
    query_p.add_argument("--limit", default=50, type=int)
    query_p.add_argument("--key", default=None)
    query_p.add_argument("--days", default=30, type=int)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "init": cmd_init,
        "start-run": cmd_start_run,
        "finish-run": cmd_finish_run,
        "heartbeat": cmd_heartbeat,
        "record-metrics": cmd_record_metrics,
        "upsert-findings": cmd_upsert_findings,
        "prior-findings": cmd_prior_findings,
        "suppressed": cmd_suppressed,
        "dispose": cmd_dispose,
        "query": cmd_query,
    }
    fn = dispatch.get(args.verb)
    if fn is None:
        parser.print_usage(sys.stderr)
        return 2
    return fn(args)


if __name__ == "__main__":
    sys.exit(main())
