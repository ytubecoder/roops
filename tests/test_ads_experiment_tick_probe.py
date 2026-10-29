"""ads-experiment-tick probe and the two controller prechecks.

The probe has no .py suffix, so tests load it with SourceFileLoader.
MAGUYVA_REPO points at a tmp tree whose growth-console venv python is a
shell script. Nothing here installs a loop or contacts a host.
"""

from __future__ import annotations

import base64
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PROBE_PATH = REPO / "probes" / "ads-experiment-tick"
ALLOW_PATH = REPO / "probes" / "ads-experiment-tick.allow"
CONTROLLER_PRECHECK = REPO / "loops.d" / "ads-experiment-controller" / "precheck.sh"
CANDIDATES_PRECHECK = REPO / "loops.d" / "ads-experiment-candidates" / "precheck.sh"
PRECHECKS = [(CONTROLLER_PRECHECK, "collect"), (CANDIDATES_PRECHECK, "candidates")]

ECHO_ARGV = """\
#!/bin/sh
exec python3 -c 'import json,os,sys; print(json.dumps({"ok": True, "argv": sys.argv[1:], "cwd": os.getcwd()}))' "$@"
"""

UNPARSEABLE = """\
#!/bin/sh
echo 'NOT-JSON-TICK-OUTPUT'
exit 3
"""

FAKE_BIN_PROBE = """\
#!/usr/bin/env python3
import base64, json, os, sys
args = sys.argv[1:]
if len(args) < 2 or args[0] != "ads-experiment-tick":
    sys.stderr.write("bad invocation: %s\\n" % (args,))
    raise SystemExit(2)
raw = args[1]
out_path = None
if "--out" in args:
    out_path = args[args.index("--out") + 1]
pad = "=" * ((-len(raw)) % 4)
obj = json.loads(base64.urlsafe_b64decode(raw + pad))
job = obj["job"]
root = os.environ["LOOPS_ROOT"]
with open(os.path.join(root, "calls.jsonl"), "a") as fh:
    fh.write(json.dumps(obj) + "\\n")
transport = os.environ.get("TRANSPORT_JOB", "")
if transport and job == transport:
    sys.stderr.write("probe transport failed\\n")
    raise SystemExit(75)
if os.environ.get("LOCK_JOB", "") == job:
    body = {"ok": True, "status": "succeeded", "skipped": "locked", "job": job}
elif os.environ.get("FAIL_JOB", "") == job:
    body = {"ok": False, "status": "failed", "failures": ["simulated failure"], "job": job}
else:
    body = {"ok": True, "status": "succeeded", "job": job}
if os.environ.get("RECEIPT_JOB", "") == job:
    body = json.loads(os.environ["RECEIPT_JSON"])
text = json.dumps(body)
if out_path:
    with open(out_path, "w") as fh:
        fh.write(text)
else:
    sys.stdout.write(text + "\\n")
"""


def load_probe(path: str, name: str):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def load_probe_core():
    path = str(REPO / "bin" / "probe_core.py")
    loader = importlib.machinery.SourceFileLoader("_ads_tick_probe_core", path)
    spec = importlib.util.spec_from_loader("_ads_tick_probe_core", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


PROBE = load_probe(str(PROBE_PATH), "ads_experiment_tick_under_test")
PROBE_CORE = load_probe_core()


def encode(obj) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def valid_body(**overrides) -> dict:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    body = {
        "v": 1,
        "request_id": "run1:collect",
        "job": "collect",
        "issued_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(minutes=9)).isoformat().replace("+00:00", "Z"),
        "dry_run": True,
    }
    body.update(overrides)
    return body


def invoke(module, arg: str):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = module.main([arg] if isinstance(arg, str) else list(arg))
    text = buf.getvalue()
    data = json.loads(text) if text.strip() else None
    return rc, data, text


def write_fake_python(root: Path, body: str) -> None:
    path = root / "growth-console" / ".venv" / "bin" / "python"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)


def test_valid_request_runs_cli_with_dry_run(tmp_path, monkeypatch):
    write_fake_python(tmp_path, ECHO_ARGV)
    monkeypatch.setenv("MAGUYVA_REPO", str(tmp_path))
    body = valid_body()
    rc, data, _ = invoke(PROBE, encode(body))
    assert rc == 0
    assert data["ok"] is True
    assert data["tick_exit"] == 0
    argv = data["argv"]
    assert "tick" in argv
    assert argv[argv.index("--job") + 1] == "collect"
    assert "--request-id" in argv
    assert argv[argv.index("--request-id") + 1] == body["request_id"]
    assert "--dry-run" in argv
    assert "--host" in argv
    assert "console.ads.ctl_cli" in argv
    assert data["cwd"] == str(tmp_path / "growth-console")


def test_rejects_dry_run_false():
    for flag in (False, "true", 1):
        rc, data, _ = invoke(PROBE, encode(valid_body(dry_run=flag)))
        assert rc == 2
        assert data["ok"] is False
        assert "dry_run" in data["error"]


@pytest.mark.parametrize(
    "field", ["order", "amount", "budget", "campaign", "resource", "params"]
)
def test_rejects_extra_keys(field):
    body = valid_body()
    body[field] = "nope"
    rc, data, _ = invoke(PROBE, encode(body))
    assert rc == 2
    assert data["ok"] is False
    assert field in data["error"]


def test_rejects_unknown_job(tmp_path, monkeypatch):
    dest_probe = tmp_path / "ads-experiment-tick"
    dest_allow = tmp_path / "ads-experiment-tick.allow"
    shutil.copy(PROBE_PATH, dest_probe)
    kept = []
    removed = False
    for line in ALLOW_PATH.read_text().splitlines():
        if line.strip() == "health":
            removed = True
            continue
        kept.append(line)
    assert removed
    dest_allow.write_text("\n".join(kept) + "\n")
    module = load_probe(str(dest_probe), "ads_experiment_tick_no_health")
    rc, data, _ = invoke(
        module, encode(valid_body(job="health", request_id="run1:health"))
    )
    assert rc == 2
    assert data["ok"] is False
    assert "job" in data["error"]
    monkeypatch.setenv("MAGUYVA_REPO", str(tmp_path))
    ok_rc, ok_data, _ = invoke(module, encode(valid_body()))
    # collect is still listed. Refusal is the missing venv, not the allow file.
    assert ok_rc == 1
    assert "venv" in ok_data["error"]
    assert ok_data["error"] != data["error"]


def test_rejects_expired_and_long_lived():
    now = datetime.now(timezone.utc).replace(microsecond=0)

    def iso(moment):
        return moment.isoformat().replace("+00:00", "Z")

    expired = valid_body(
        issued_at=iso(now - timedelta(minutes=5)),
        expires_at=iso(now - timedelta(minutes=1)),
    )
    rc, data, _ = invoke(PROBE, encode(expired))
    assert rc == 2 and data["ok"] is False
    assert "expired" in data["error"]

    long_lived = valid_body(
        issued_at=iso(now),
        expires_at=iso(now + timedelta(minutes=10, seconds=30)),
    )
    rc, data, _ = invoke(PROBE, encode(long_lived))
    assert rc == 2 and data["ok"] is False
    assert "10 minutes" in data["error"]

    future = valid_body(
        issued_at=iso(now + timedelta(minutes=3)),
        expires_at=iso(now + timedelta(minutes=8)),
    )
    rc, data, _ = invoke(PROBE, encode(future))
    assert rc == 2 and data["ok"] is False
    assert "future" in data["error"]


def test_rejects_naive_timestamps():
    naive = "2026-10-29T12:00:00"
    aware = valid_body()["expires_at"]
    rc, data, _ = invoke(PROBE, encode(valid_body(issued_at=naive, expires_at=aware)))
    assert rc == 2 and data["ok"] is False
    assert "timezone-aware" in data["error"]
    rc, data, _ = invoke(PROBE, encode(valid_body(expires_at=naive)))
    assert rc == 2 and data["ok"] is False
    assert "timezone-aware" in data["error"]


def test_rejects_non_base64_and_non_object():
    rc, data, _ = invoke(PROBE, "not base64!!!")
    assert rc == 2 and data["ok"] is False
    assert "base64url" in data["error"]

    rc, data, _ = invoke(PROBE, encode(["collect"]))
    assert rc == 2 and data["ok"] is False
    assert "JSON object" in data["error"]

    rc, data, _ = invoke(PROBE, encode("collect"))
    assert rc == 2 and data["ok"] is False

    rc, data, _ = invoke(PROBE, encode(None))
    assert rc == 2 and data["ok"] is False

    standard = base64.b64encode(b'{"v":1}').decode()
    assert "+" in standard or "/" in standard or standard.endswith("=")
    # Alphabet rejection: '+' and '/' are not base64url. Force one if needed.
    if "+" not in standard and "/" not in standard:
        standard = standard[:-1] + "+"
    rc, data, _ = invoke(PROBE, standard)
    assert rc == 2 and data["ok"] is False
    assert "base64url" in data["error"]


def test_arg_fits_probe_core_regex():
    token = encode(
        valid_body(
            request_id="20261029T120000Z-ads-experiment-controller-ab12cd:collect"
        )
    )
    assert PROBE_CORE.ARG_RE.fullmatch(token)


def test_missing_venv_exit_1(tmp_path, monkeypatch):
    monkeypatch.setenv("MAGUYVA_REPO", str(tmp_path))
    rc, data, _ = invoke(PROBE, encode(valid_body()))
    assert rc == 1
    assert data["ok"] is False
    assert "venv" in data["error"]


def test_unparseable_cli_output_wrapped(tmp_path, monkeypatch):
    write_fake_python(tmp_path, UNPARSEABLE)
    monkeypatch.setenv("MAGUYVA_REPO", str(tmp_path))
    rc, data, _ = invoke(PROBE, encode(valid_body()))
    assert rc == 0
    assert data["ok"] is False
    assert data["error"] == "unparseable tick output"
    assert "NOT-JSON-TICK-OUTPUT" in data["stdout_tail"]
    assert data["tick_exit"] == 3


def _run_precheck(root: Path, script: Path, **env_extra):
    bindir = root / "bin"
    bindir.mkdir(parents=True)
    probe = bindir / "probe"
    probe.write_text(FAKE_BIN_PROBE)
    probe.chmod(0o755)
    out_dir = root / "out"
    out_dir.mkdir()
    env = os.environ.copy()
    env["LOOPS_ROOT"] = str(root)
    env["OUT_DIR"] = str(out_dir)
    run_id = env_extra.pop("RUN_ID", "testrun")
    if run_id is None:
        env.pop("RUN_ID", None)
    else:
        env["RUN_ID"] = run_id
    for key, value in env_extra.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    proc = subprocess.run(
        ["bash", str(script)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    calls = []
    log = root / "calls.jsonl"
    if log.is_file():
        for line in log.read_text().splitlines():
            if line.strip():
                calls.append(json.loads(line))
    return proc, calls


def test_precheck_quiet_when_all_ok(tmp_path):
    proc, calls = _run_precheck(
        tmp_path / "controller", CONTROLLER_PRECHECK, RUN_ID="testrun"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PROBLEM:" not in proc.stdout
    assert "DEGRADED:" not in proc.stdout
    assert all(
        f"{job}: ok" in proc.stdout for job in ("collect", "evaluate", "reconcile")
    )
    assert [item["job"] for item in calls] == ["collect", "evaluate", "reconcile"]
    for item in calls:
        assert item["dry_run"] is True
        assert item["v"] == 1
        assert item["request_id"] == f"testrun:{item['job']}"
        issued = datetime.fromisoformat(item["issued_at"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(item["expires_at"].replace("Z", "+00:00"))
        assert issued.tzinfo is not None and expires.tzinfo is not None
        assert expires - issued == timedelta(minutes=9)

    proc, calls = _run_precheck(
        tmp_path / "candidates", CANDIDATES_PRECHECK, RUN_ID="testrun"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "candidates: ok" in proc.stdout
    assert [item["job"] for item in calls] == ["candidates"]
    assert calls[0]["request_id"] == "testrun:candidates"
    assert calls[0]["dry_run"] is True

    proc, calls = _run_precheck(
        tmp_path / "locked",
        CONTROLLER_PRECHECK,
        RUN_ID="testrun",
        LOCK_JOB="evaluate",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "evaluate: ok" in proc.stdout

    proc, calls = _run_precheck(tmp_path / "norun", CONTROLLER_PRECHECK, RUN_ID=None)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(calls) == 3
    for item in calls:
        assert item["request_id"].endswith(":" + item["job"])
        stamp = item["request_id"].split(":", 1)[0]
        parsed = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        assert parsed.tzinfo is timezone.utc


def test_precheck_exit_1_on_failure(tmp_path):
    proc, calls = _run_precheck(
        tmp_path / "controller",
        CONTROLLER_PRECHECK,
        FAIL_JOB="evaluate",
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "evaluate" in proc.stdout
    assert "PROBLEM: evaluate: failed — simulated failure" in proc.stdout
    assert [item["job"] for item in calls] == ["collect", "evaluate", "reconcile"]

    proc, _calls = _run_precheck(
        tmp_path / "candidates",
        CANDIDATES_PRECHECK,
        FAIL_JOB="candidates",
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "candidates" in proc.stdout
    assert "PROBLEM: candidates: failed — simulated failure" in proc.stdout

    proc, _calls = _run_precheck(
        tmp_path / "transport",
        CONTROLLER_PRECHECK,
        TRANSPORT_JOB="reconcile",
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "reconcile" in proc.stdout
    assert "PROBLEM: reconcile: failed — transport failed" in proc.stdout


@pytest.mark.parametrize("script,job", PRECHECKS)
@pytest.mark.parametrize("ok", [False, True])
def test_precheck_failed_status_exits_1(tmp_path, script, job, ok):
    failures = [
        {"key": "example", "source": source, "error": "config missing"}
        for source in (
            "google.account",
            "google.status",
            "google.cost",
            "google.budget_history",
        )
    ]
    proc, _ = _run_precheck(
        tmp_path,
        script,
        RECEIPT_JOB=job,
        RECEIPT_JSON=json.dumps({"ok": ok, "status": "failed", "failures": failures}),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    expected = "; ".join(
        f"example/{item['source']}: config missing" for item in failures[:3]
    )
    assert f"PROBLEM: {job}: failed — {expected}" in proc.stdout.splitlines()
    assert "google.budget_history" not in proc.stdout
    assert "DEGRADED:" not in proc.stdout


@pytest.mark.parametrize("script,job", PRECHECKS)
def test_precheck_degraded_exits_1_and_says_degraded(tmp_path, script, job):
    degraded = [
        {"key": "example", "source": "stripe.aggregate", "reason": "snapshot missing"},
        {"key": "example", "source": "umami.funnel", "reason": "partial source data"},
        {"key": None, "source": "reconcile", "reason": "command outcome uncertain"},
        {"key": "example", "source": "google.experiment", "reason": "fourth item"},
    ]
    proc, _ = _run_precheck(
        tmp_path,
        script,
        RECEIPT_JOB=job,
        RECEIPT_JSON=json.dumps(
            {"ok": True, "status": "degraded", "degraded": degraded}
        ),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert (
        f"DEGRADED: {job}: example/stripe.aggregate: snapshot missing; "
        "example/umami.funnel: partial source data; job/reconcile: command outcome uncertain"
    ) in proc.stdout.splitlines()
    assert "fourth item" not in proc.stdout
    assert "PROBLEM:" not in proc.stdout


@pytest.mark.parametrize("script,job", PRECHECKS)
@pytest.mark.parametrize("skipped", [None, "locked"])
def test_precheck_missing_status_is_failed(tmp_path, script, job, skipped):
    proc, _ = _run_precheck(
        tmp_path,
        script,
        RECEIPT_JOB=job,
        RECEIPT_JSON=json.dumps({"ok": True, "skipped": skipped}),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert (
        f"PROBLEM: {job}: failed — receipt has no status (CLI too old)"
    ) in proc.stdout.splitlines()


@pytest.mark.parametrize("script,job", PRECHECKS)
@pytest.mark.parametrize("ok", [False, None, 1, "true"])
def test_precheck_requires_literal_true_ok(tmp_path, script, job, ok):
    proc, _ = _run_precheck(
        tmp_path,
        script,
        RECEIPT_JOB=job,
        RECEIPT_JSON=json.dumps(
            {"ok": ok, "status": "succeeded", "error": "invalid ok"}
        ),
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert f"PROBLEM: {job}: failed — invalid ok" in proc.stdout.splitlines()


@pytest.mark.parametrize("script,job", PRECHECKS)
def test_precheck_locked_is_ok(tmp_path, script, job):
    proc, _ = _run_precheck(tmp_path, script, LOCK_JOB=job)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"{job}: ok" in proc.stdout.splitlines()


@pytest.mark.parametrize("script,job", PRECHECKS)
def test_precheck_transport_failure_is_failed(tmp_path, script, job):
    proc, _ = _run_precheck(tmp_path, script, TRANSPORT_JOB=job)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert (
        f"PROBLEM: {job}: failed — transport failed — probe transport failed"
        in proc.stdout.splitlines()
    )
