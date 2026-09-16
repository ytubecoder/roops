#!/usr/bin/env python3
"""vecomap-unmapped precheck orchestrator.

Stdlib only. Tracer invocations use the tracer venv python declared in
requires=file:~/.cache/vecomap-tracer/venv/bin/python. Exit 0 = silent-green
(nothing traced, nothing superseded/aging); non-zero = escalate.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

ATTEMPT_MAX_DAYS = 30
PROVISIONAL_AGE_DAYS = 30
OSM_MAX_DAYS = 60
MAX_KEYS_CAP = 25
# The runner caps a precheck at 300 s (INTERFACES); a trace takes 6-15 s on firstparty, so stop
# starting new keys once this much wall-clock has gone. Untouched keys are picked up next firing.
TIME_BUDGET_S = 200
OSM_BBOX = "10.05,123.65,10.60,124.10"
OSM_FILES = (
    "cebu_named_ways.json",
    "cebu_highways.json",
    "cebu_places.json",
    "cebu_pois.json",
    "cebu_water.json",
)
GIT_NAME = "vecomap-tracer"
GIT_EMAIL = "vecomap-tracer@users.noreply.github.com"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso(ts: datetime | None = None) -> str:
    return (ts or now_utc()).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def age_days(then: datetime, now: datetime) -> float:
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - then).total_seconds() / 86400.0


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def is_true(value: Any) -> bool:
    return value in (True, 1, "1", "true", "True")


def read_tracer_version(init_py: str) -> str:
    try:
        text = open(init_py, encoding="utf-8").read()
    except OSError:
        return "unknown"
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    return match.group(1) if match else "unknown"


def load_state(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        data = json.loads(open(path, encoding="utf-8").read())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path: str, state: dict[str, Any]) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def kml_placemark_names(path: str) -> set[str]:
    names: set[str] = set()
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return names
    for el in root.iter():
        if _local(el.tag) != "Placemark":
            continue
        for child in el:
            if _local(child.tag) == "name" and child.text:
                name = child.text.strip()
                if name:
                    names.add(name)
    return names


def candidate_keys(directory: str) -> set[str]:
    keys: set[str] = set()
    if not os.path.isdir(directory):
        return keys
    try:
        names = os.listdir(directory)
    except OSError:
        return keys
    for name in names:
        if name.endswith(".geojson"):
            keys.add(name[: -len(".geojson")])
    return keys


def superseded_keys(candidates: set[str], kml_names: set[str]) -> list[str]:
    return sorted(candidates & kml_names)


def has_fresh_attempt(entry: dict[str, Any] | None, version: str, now: datetime) -> bool:
    if not entry:
        return False
    if not entry.get("tracedAt"):
        return False
    if entry.get("tracerVersion") != version:
        return False
    ts = parse_iso(str(entry.get("tracedAt") or ""))
    if ts is None:
        return False
    if age_days(ts, now) > ATTEMPT_MAX_DAYS:
        return False
    conf = entry.get("confidence")
    # High/medium without a pushed branch must be retried (dry-run or push fail).
    if conf in ("high", "medium") and not entry.get("branch"):
        return False
    return True


def choose_work(
    areas: list[dict[str, Any]],
    candidates: set[str],
    state: dict[str, Any],
    version: str,
    now: datetime,
    max_keys: int,
) -> list[str]:
    chosen: list[str] = []
    for item in areas:
        if len(chosen) >= max_keys:
            break
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key or is_true(item.get("provisional")):
            continue
        if key in candidates:
            continue
        entry = state.get(key) if isinstance(state.get(key), dict) else None
        if has_fresh_attempt(entry, version, now):
            continue
        chosen.append(key)
    return chosen


def update_provisional(
    state: dict[str, Any],
    areas: list[dict[str, Any]],
    now: datetime,
    now_s: str,
) -> list[str]:
    current: set[str] = set()
    for item in areas:
        if not isinstance(item, dict):
            continue
        if not is_true(item.get("provisional")):
            continue
        key = str(item.get("key") or "").strip()
        if key:
            current.add(key)

    aging: list[str] = []
    for key in sorted(current):
        entry = state.get(key)
        if not isinstance(entry, dict):
            entry = {}
            state[key] = entry
        if not entry.get("provisionalSince"):
            entry["provisionalSince"] = now_s
        ts = parse_iso(str(entry.get("provisionalSince") or ""))
        if ts is not None and age_days(ts, now) > PROVISIONAL_AGE_DAYS:
            aging.append(key)

    for key, entry in list(state.items()):
        if not isinstance(entry, dict):
            continue
        if key in current:
            continue
        if "provisionalSince" in entry:
            entry.pop("provisionalSince", None)
            if not entry:
                del state[key]
    return aging


def run_cmd(
    argv: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except OSError as exc:
        return 127, "", str(exc)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def git(repo: str, *args: str, timeout: int = 60) -> tuple[int, str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    return run_cmd(["git", "-C", repo, *args], env=env, timeout=timeout)


def refresh_checkout(repo: str) -> str | None:
    rc, _out, err = git(repo, "fetch", "-q", "origin", timeout=90)
    if rc != 0:
        return f"git fetch failed: {(err or '').strip() or rc}"
    rc, _out, err = git(repo, "checkout", "-q", "-B", "main", "origin/main")
    if rc != 0:
        return f"git checkout -B main origin/main failed: {(err or '').strip() or rc}"
    return None


def refresh_osm(tracer_py: str, tracer_dir: str, cache: str, now: datetime) -> str:
    named = os.path.join(cache, "cebu_named_ways.json")
    try:
        mtime = os.path.getmtime(named)
        days = (now.timestamp() - mtime) / 86400.0
    except OSError:
        days = OSM_MAX_DAYS + 1
    if days <= OSM_MAX_DAYS:
        return f"osm cache age {days:.1f}d (fresh)"
    tmp = os.path.join(cache, "osm-refresh-tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        os.makedirs(tmp, exist_ok=True)
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.join(tracer_dir, "src")
        rc, _out, err = run_cmd(
            [
                tracer_py,
                "-m",
                "area_tracer",
                "fetch-osm",
                "--bbox",
                OSM_BBOX,
                "--out",
                tmp,
            ],
            cwd=tracer_dir,
            env=env,
            timeout=90,
        )
        src_named = os.path.join(tmp, "cebu_named_ways.json")
        if rc != 0 or not os.path.isfile(src_named):
            return f"osm refresh failed (kept old files): {(err or '').strip()[:200] or rc}"
        for name in OSM_FILES:
            src = os.path.join(tmp, name)
            if os.path.isfile(src):
                os.replace(src, os.path.join(cache, name))
        return "osm cache refreshed"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def fetch_unmapped(api_base: str, secret: str, dest: str) -> tuple[str | None, dict[str, Any]]:
    url = api_base.rstrip("/") + "/api/admin/unmapped?include=provisional"
    proc_timeout = 65
    rc, out, err = run_cmd(
        [
            "curl",
            "-sS",
            "--max-time",
            "60",
            "-H",
            "X-Admin-Secret: " + secret,
            "-o",
            dest,
            "-w",
            "%{http_code}",
            url,
        ],
        timeout=proc_timeout,
    )
    code = (out or "").strip()
    if rc != 0:
        return f"curl failed ({rc}): {(err or '').strip()[:200]}", {}
    if code != "200":
        return f"unmapped API HTTP {code or '000'}", {}
    try:
        payload = json.loads(open(dest, encoding="utf-8").read())
    except (OSError, ValueError) as exc:
        return f"unmapped API JSON: {exc}", {}
    if not isinstance(payload, dict):
        return "unmapped API: expected object", {}
    return None, payload


def resolve_key(
    tracer_py: str, tracer_dir: str, cache: str, key: str
) -> tuple[bool, str, str]:
    png = os.path.join(cache, "images", f"{key}.png")
    if os.path.isfile(png):
        return True, png, "cached"
    os.makedirs(os.path.dirname(png), exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(tracer_dir, "src")
    rc, out, err = run_cmd(
        [tracer_py, "-m", "area_tracer", "resolve", "--key", key, "--out", png],
        cwd=tracer_dir,
        env=env,
        timeout=45,
    )
    if rc == 0 and os.path.isfile(png):
        return True, png, "resolved"
    msg = (err or out or f"resolve exit {rc}").strip().replace("\n", " ")[:400]
    return False, png, msg or "resolve_failed"


def trace_key(
    tracer_py: str,
    tracer_dir: str,
    cache: str,
    key: str,
    png: str,
    trace_dir: str,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["AREA_TRACER_OCR"] = "rapidocr"
    env["PYTHONPATH"] = os.path.join(tracer_dir, "src")
    argv = [
        tracer_py,
        "-m",
        "area_tracer",
        "trace",
        "--key",
        key,
        "--image",
        png,
        "--osm",
        os.path.join(cache, "cebu_named_ways.json"),
        "--roads",
        os.path.join(cache, "cebu_highways.json"),
        "--places",
        os.path.join(cache, "cebu_places.json"),
        "--pois",
        os.path.join(cache, "cebu_pois.json"),
        "--water",
        os.path.join(cache, "cebu_water.json"),
        "--out",
        trace_dir,
    ]
    rc, out, err = run_cmd(argv, cwd=tracer_dir, env=env, timeout=120)
    report_path = os.path.join(trace_dir, f"{key}.report.json")
    report: dict[str, Any] = {}
    if os.path.isfile(report_path):
        try:
            loaded = json.loads(open(report_path, encoding="utf-8").read())
            if isinstance(loaded, dict):
                report = loaded
        except (OSError, ValueError):
            report = {}
    geo = os.path.join(trace_dir, f"{key}.geojson")
    overlay = os.path.join(trace_dir, f"{key}.overlay.png")
    status = str(report.get("status") or "")
    conf = report.get("confidence")
    message = str(report.get("message") or "").strip()
    if rc == 0 and os.path.isfile(geo):
        row_status = "ok"
        if not message:
            message = "ok"
    elif rc == 0:
        row_status = "could_not_trace"
        message = message or "trace exit 0 but geojson missing"
    elif rc == 3:
        row_status = status or "could_not_trace"
        if not message:
            message = (err or out or "could not trace").strip().replace("\n", " ")[:400]
    else:
        row_status = status or "could_not_trace"
        extra = (err or out or f"trace exit {rc}").strip().replace("\n", " ")[:400]
        message = message or extra
    return {
        "key": key,
        "status": row_status,
        "confidence": conf if conf in ("high", "medium", "low") else None,
        "message": message,
        "style": report.get("style"),
        "anchors": report.get("anchors"),
        "inliers": report.get("inliers"),
        "branch": None,
        "geojson": geo if os.path.isfile(geo) else None,
        "overlay": overlay if os.path.isfile(overlay) else None,
    }


def record_attempt(
    state: dict[str, Any],
    row: dict[str, Any],
    version: str,
    traced_at: str,
    branch: str | None,
) -> None:
    key = row["key"]
    entry = state.get(key)
    if not isinstance(entry, dict):
        entry = {}
    entry["tracedAt"] = traced_at
    entry["tracerVersion"] = version
    entry["status"] = row.get("status")
    entry["confidence"] = row.get("confidence")
    entry["branch"] = branch
    state[key] = entry


def commit_and_push(
    repo: str,
    trace_dir: str,
    confident: list[dict[str, Any]],
    now: datetime,
    dry_run: bool,
) -> tuple[str | None, bool, str]:
    """Return (branch, pushed, log_text). branch is set if commit succeeded."""
    if not confident:
        return None, False, "no confident keys"
    branch = "tracer/" + now.strftime("%Y%m%d-%H%M")
    log_chunks: list[str] = []
    rc, _out, err = git(repo, "checkout", "-q", "-B", branch, "origin/main")
    if rc != 0:
        git(repo, "checkout", "-q", "-B", "main", "origin/main")
        return None, False, f"branch checkout failed: {(err or '').strip()[:200]}"
    try:
        cand_dir = os.path.join(repo, "data", "candidates")
        os.makedirs(cand_dir, exist_ok=True)
        copied: list[str] = []
        included: list[dict[str, Any]] = []
        for row in confident:
            key = row["key"]
            geo_src = os.path.join(trace_dir, f"{key}.geojson")
            ov_src = os.path.join(trace_dir, f"{key}.overlay.png")
            if not (os.path.isfile(geo_src) and os.path.isfile(ov_src)):
                continue
            shutil.copy2(geo_src, os.path.join(cand_dir, f"{key}.geojson"))
            shutil.copy2(ov_src, os.path.join(cand_dir, f"{key}.overlay.png"))
            copied.extend(
                [f"data/candidates/{key}.geojson", f"data/candidates/{key}.overlay.png"]
            )
            included.append(row)
        if not copied:
            return None, False, "confident keys missing geojson/overlay"
        rc, _out, err = git(repo, "add", "--", *copied)
        if rc != 0:
            return None, False, f"git add failed: {(err or '').strip()[:200]}"
        rc, _out, _err = git(repo, "diff", "--cached", "--quiet")
        if rc == 0:
            return None, False, "no candidate diff vs origin/main"
        n = len(included)
        body = "\n".join(f"{row['key']} ({row.get('confidence')})" for row in included)
        msg = f"data: tracer candidates for {n} key(s)\n\n{body}\n"
        rc, out, err = git(
            repo,
            "-c",
            f"user.name={GIT_NAME}",
            "-c",
            f"user.email={GIT_EMAIL}",
            "commit",
            "-m",
            msg,
        )
        if rc != 0:
            return None, False, f"git commit failed: {(err or out or '').strip()[:200]}"
        rc, log_out, log_err = git(repo, "log", "-1", "--stat")
        log_chunks.append((log_out or log_err or "").rstrip())
        log_chunks.append(f"branch: {branch}")
        pushed = False
        if dry_run:
            log_chunks.append("dry_run: skipped git push")
        else:
            rc, out, err = git(repo, "push", "origin", branch, timeout=120)
            if rc != 0:
                log_chunks.append(f"git push failed: {(err or out or '').strip()[:300]}")
            else:
                pushed = True
                log_chunks.append(f"pushed: origin {branch}")
        for row in included:
            row["branch"] = branch
            row["pushed"] = pushed
        return branch, pushed, "\n".join(log_chunks)
    finally:
        git(repo, "checkout", "-q", "-B", "main", "origin/main")


def emit(
    summary: dict[str, Any],
    git_log: str,
    fatal: str | None,
) -> None:
    traced = int(summary.get("traced") or 0)
    confident = summary.get("confident") or []
    low = summary.get("low") or []
    failed = summary.get("failed") or []
    superseded = summary.get("superseded") or []
    aging = summary.get("aging") or []
    branch = summary.get("pushed_branch") or "none"
    pushed_n = 1 if summary.get("pushed") else 0
    first = (
        f"vecomap-unmapped: traced={traced} confident={len(confident)} "
        f"low={len(low)} failed={len(failed)} superseded={len(superseded)} "
        f"aging={len(aging)} branch={branch}"
    )
    if traced == 0 and not superseded and not aging and not fatal:
        first = "vecomap-unmapped: nothing to report"
    print(first)
    print(f"traced: {traced}")
    print(f"confident: {len(confident)}")
    print(f"low: {len(low)}")
    print(f"failed: {len(failed)}")
    print(f"superseded: {len(superseded)}")
    print(f"aging: {len(aging)}")
    print(f"pushed: {pushed_n}")
    print(f"pushed_branch: {branch}")
    print(f"dry_run: {1 if summary.get('dry_run') else 0}")
    if fatal:
        print(f"fatal: {fatal}")
    metrics = {
        "traced": traced,
        "confident": len(confident),
        "pushed": pushed_n,
        "low": len(low),
        "failed": len(failed),
        "aging": len(aging),
        "superseded": len(superseded),
    }
    print("metrics: " + json.dumps(metrics, separators=(", ", ": ")))
    for row in summary.get("per_key") or []:
        key = row.get("key")
        status = row.get("status")
        conf = row.get("confidence") or "-"
        br = row.get("branch") or "-"
        msg = str(row.get("message") or "").replace("\n", " ")[:200]
        print(f"per_key {key}: {status} confidence={conf} branch={br} {msg}")
    for key in superseded:
        print(f"superseded_key: {key}")
    for key in aging:
        print(f"aging_key: {key}")
    if git_log:
        print("git:")
        print(git_log)


def run() -> int:
    loops_root = os.environ.get("LOOPS_ROOT") or ""
    out_dir = os.environ.get("OUT_DIR") or ""
    if not loops_root or not out_dir:
        print("vecomap-unmapped: LOOPS_ROOT and OUT_DIR required")
        print("fatal: missing LOOPS_ROOT or OUT_DIR")
        return 1

    os.makedirs(out_dir, exist_ok=True)
    trace_dir = os.path.join(out_dir, "trace")
    os.makedirs(trace_dir, exist_ok=True)

    home = os.path.expanduser("~")
    repo = os.environ.get("VECOMAP_ROOT") or os.path.join(home, "projects", "vecomap")
    tracer_py = os.environ.get("TRACER_PY") or os.path.join(
        home, ".cache", "vecomap-tracer", "venv", "bin", "python"
    )
    cache = os.environ.get("TRACER_CACHE") or os.path.join(
        home, ".cache", "vecomap-tracer"
    )
    tracer_dir = os.environ.get("TRACER_DIR") or os.path.join(
        repo, "tools", "area-tracer"
    )
    state_path = os.path.join(loops_root, "state", "vecomap-unmapped", "attempted.json")
    dry_run = env_flag("VECOMAP_DRY_RUN")
    try:
        max_keys = int(os.environ.get("VECOMAP_MAX_KEYS") or "10")
    except ValueError:
        max_keys = 25
    max_keys = max(0, min(MAX_KEYS_CAP, max_keys))

    now = now_utc()
    now_s = now_iso(now)
    version = read_tracer_version(
        os.path.join(tracer_dir, "src", "area_tracer", "__init__.py")
    )
    state = load_state(state_path)

    fatal: str | None = None
    secret = os.environ.get("VECOMAP_ADMIN_SECRET") or ""
    api_base = os.environ.get("VECOMAP_API_BASE") or ""
    if not secret or not api_base:
        fatal = "VECOMAP_ADMIN_SECRET or VECOMAP_API_BASE unset"

    if not fatal:
        err = refresh_checkout(repo)
        if err:
            fatal = err

    osm_note = ""
    if not fatal:
        try:
            osm_note = refresh_osm(tracer_py, tracer_dir, cache, now)
        except OSError as exc:
            osm_note = f"osm refresh skipped: {exc}"

    payload: dict[str, Any] = {}
    areas: list[dict[str, Any]] = []
    if not fatal:
        dest = os.path.join(out_dir, "unmapped.json")
        err, payload = fetch_unmapped(api_base, secret, dest)
        if err:
            fatal = err
        else:
            raw_areas = payload.get("areas") or []
            areas = [a for a in raw_areas if isinstance(a, dict)]

    cand = candidate_keys(os.path.join(repo, "data", "candidates"))
    kml_names = kml_placemark_names(os.path.join(repo, "data", "raw", "vecomap.kml"))
    superseded = superseded_keys(cand, kml_names)
    aging = update_provisional(state, areas, now, now_s) if not fatal else []

    work = (
        choose_work(areas, cand, state, version, now, max_keys) if not fatal else []
    )

    per_key: list[dict[str, Any]] = []
    started = time.monotonic()
    for key in work:
        if time.monotonic() - started > TIME_BUDGET_S:
            print(f"time budget reached after {len(per_key)} key(s); the rest wait for the next firing")
            break
        ok, png, msg = resolve_key(tracer_py, tracer_dir, cache, key)
        if not ok:
            row = {
                "key": key,
                "status": "resolve_failed",
                "confidence": None,
                "message": msg,
                "style": None,
                "anchors": None,
                "inliers": None,
                "branch": None,
            }
            per_key.append(row)
            record_attempt(state, row, version, now_s, None)
            continue
        row = trace_key(tracer_py, tracer_dir, cache, key, png, trace_dir)
        per_key.append(row)
        record_attempt(state, row, version, now_s, None)

    confident_rows = [
        r
        for r in per_key
        if r.get("status") == "ok" and r.get("confidence") in ("high", "medium")
    ]
    git_log = osm_note
    branch: str | None = None
    pushed = False
    if confident_rows:
        branch, pushed, git_detail = commit_and_push(
            repo, trace_dir, confident_rows, now, dry_run
        )
        git_log = (git_log + "\n" + git_detail).strip() if git_log else git_detail
        stored_branch = branch if pushed else None
        for row in per_key:
            if row.get("key") in {r["key"] for r in confident_rows if r.get("branch")}:
                record_attempt(state, row, version, now_s, stored_branch)

    confident = [
        r["key"]
        for r in per_key
        if r.get("status") == "ok" and r.get("confidence") in ("high", "medium")
    ]
    low = [r["key"] for r in per_key if r.get("confidence") == "low"]
    failed = []
    for row in per_key:
        if row.get("status") != "ok":
            failed.append(row["key"])
        elif row.get("confidence") not in ("high", "medium", "low"):
            failed.append(row["key"])

    public_rows = []
    for row in per_key:
        public_rows.append(
            {
                "key": row.get("key"),
                "status": row.get("status"),
                "confidence": row.get("confidence"),
                "message": row.get("message"),
                "style": row.get("style"),
                "anchors": row.get("anchors"),
                "inliers": row.get("inliers"),
                "branch": row.get("branch"),
            }
        )

    summary = {
        "traced": len(per_key),
        "pushed_branch": branch,
        "confident": confident,
        "low": low,
        "failed": failed,
        "superseded": superseded,
        "aging": aging,
        "per_key": public_rows,
        "dry_run": dry_run,
        "pushed": pushed,
        "fatal": fatal,
        "generated_at": now_s,
        "tracer_version": version,
    }
    summary_path = os.path.join(out_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
        fh.write("\n")
    try:
        os.chmod(summary_path, 0o600)
    except OSError:
        pass

    try:
        save_state(state_path, state)
    except OSError as exc:
        if not fatal:
            fatal = f"could not write state: {exc}"
            summary["fatal"] = fatal
            with open(summary_path, "w", encoding="utf-8") as fh:
                json.dump(summary, fh, indent=2)
                fh.write("\n")

    emit(summary, git_log, fatal)
    escalate = bool(fatal) or bool(per_key) or bool(superseded) or bool(aging)
    return 1 if escalate else 0


def main() -> int:
    try:
        return run()
    except Exception as exc:  # noqa: BLE001 — precheck must emit, not crash silent
        print("vecomap-unmapped: fatal precheck error")
        print(f"fatal: {type(exc).__name__}: {exc}")
        print('metrics: {"traced": 0, "confident": 0, "pushed": 0, "low": 0, "failed": 0, "aging": 0, "superseded": 0}')
        out_dir = os.environ.get("OUT_DIR") or ""
        if out_dir:
            try:
                os.makedirs(out_dir, exist_ok=True)
                payload = {
                    "traced": 0,
                    "pushed_branch": None,
                    "confident": [],
                    "low": [],
                    "failed": [],
                    "superseded": [],
                    "aging": [],
                    "per_key": [],
                    "fatal": f"{type(exc).__name__}: {exc}",
                    "generated_at": now_iso(),
                }
                with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=2)
                    fh.write("\n")
            except OSError:
                pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
