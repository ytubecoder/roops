"""Real ctl_cli receipts reach the precheck through a local fake probe.

All runtime state lives in tmp_path. No loop is installed or run, and no
remote probe is contacted. The maguyva-marketing checkout is read-only.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CONTROLLER_PRECHECK = REPO / "loops.d" / "ads-experiment-controller" / "precheck.sh"

REAL_CLI_PROBE = """\
#!/usr/bin/env python3
import base64, json, os, subprocess, sys
from pathlib import Path

args = sys.argv[1:]
assert args[0] == "ads-experiment-tick"
raw = args[1]
request = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
assert request["dry_run"] is True
out_path = Path(args[args.index("--out") + 1])
selected_job = os.environ.get("REAL_CLI_JOB")
if selected_job and request["job"] != selected_job:
    # Isolate the selected real job so other jobs cannot mask its exit status.
    out_path.write_text(json.dumps({"ok": True, "status": "succeeded"}))
    raise SystemExit(0)
gc_dir = Path(os.environ["MAGUYVA_REPO"]) / "growth-console"
proc = subprocess.run(
    [str(gc_dir / ".venv" / "bin" / "python"), "-m", "console.ads.ctl_cli",
     "tick", "--job", request["job"], "--request-id", request["request_id"],
     "--dry-run"],
    cwd=gc_dir, capture_output=True, text=True, check=False, timeout=30,
)
out_path.write_text(proc.stdout)
sys.stderr.write(proc.stderr)
# Like the real probe, transport succeeds even when the CLI receipt is failed.
"""

SEED_UNCERTAIN_PAUSE = """\
import json
from datetime import datetime, timedelta, timezone
from console.ads import ctl_commands, ctl_runs, ctl_settings

now = datetime.now(timezone.utc)
actor = "human:generalissimo"
run = ctl_runs.create_run(
    "g-msg", actor=actor, settings=ctl_settings.resolve(),
    reason="Test reconciliation of an uncertain simulated pause.",
    body={
        "hypothesis": "The challenger changes repository linking.",
        "treatment": "challenger lander", "changed_variable": "landing_page",
        "arms": [
            {"name": name, "network": "google", "account": "test-account",
             "campaign_ids": [campaign]}
            for name, campaign in (("control", "c1"), ("challenger", "c2"))
        ],
        "allocation": {"control": 50, "challenger": 50},
        "enrollment_start": now.date().isoformat(),
        "enrollment_end": (now + timedelta(days=28)).date().isoformat(),
        "primary_metric": "repository_linked",
        "method": "fisher-two-arm-fixed-checkpoints-v1",
        "assumption_set": None, "envelope_usd": None, "mode": "simulate",
    },
)
ctl_runs.record_review(run["run_id"], 1, actor=actor, decision="approved")
ctl_runs.activate(run["run_id"], 1, actor=actor)
command = ctl_commands.create(
    run_id=run["run_id"], revision=1, decision_id="fd2-uncertain-pause",
    action="pause", scope=["c1"], mode="simulate", actor=actor,
    reason="Seed a simulated pause with a lapsed execution lease.",
    expires_at=(now + timedelta(days=1)).isoformat(),
)
# Zero-second leases are rejected; recover(now=...) supplies the lapsed time.
claimed = ctl_commands.claim(worker="fd2-seed", lease_seconds=120)
assert claimed["idem_key"] == command["idem_key"]
recoveries = []

def lapse_during_execution(executing):
    # A lapsed claimed command is requeued, so recovery must see executing.
    after_lease = datetime.fromisoformat(executing["lease_until"]) + timedelta(seconds=1)
    recoveries.append(ctl_commands.recover(now=after_lease.isoformat()))
    return {"outcome": "succeeded", "receipt": {"simulated": True}}

result = ctl_commands.execute(
    command["idem_key"], worker="fd2-seed", executor=lapse_during_execution,
)
assert recoveries == [{"queued": 0, "uncertain": 1, "expired": 0}]
assert result["status"] == "uncertain"
stored = ctl_commands.list_commands(run["run_id"])
assert len(stored) == 1 and stored[0]["status"] == "uncertain"
print(json.dumps({"run_id": run["run_id"], "idem_key": command["idem_key"]}))
"""


@pytest.fixture
def real_cli_env(tmp_path):
    maguyva_repo = (
        Path(os.environ.get("MAGUYVA_REPO", "~/projects/maguyva-marketing"))
        .expanduser()
        .resolve()
    )
    gc_dir = maguyva_repo / "growth-console"
    for required in (
        gc_dir / ".venv" / "bin" / "python",
        gc_dir / "console" / "ads" / "ctl_cli.py",
    ):
        if not required.is_file():
            pytest.skip(f"real ctl_cli integration unavailable: missing {required}")

    root = tmp_path / "loops"
    bindir = root / "bin"
    bindir.mkdir(parents=True)
    probe = bindir / "probe"
    probe.write_text(REAL_CLI_PROBE)
    probe.chmod(0o755)
    out_dir = root / "out"
    out_dir.mkdir()
    stripe_dir = tmp_path / "stripe"
    stripe_dir.mkdir()
    attribution_dir = tmp_path / "attribution"
    attribution_dir.mkdir()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GOOGLE_ADS_") and key != "REAL_CLI_JOB"
    }
    env.update(
        MAGUYVA_REPO=str(maguyva_repo),
        LOOPS_ROOT=str(root),
        OUT_DIR=str(out_dir),
        RUN_ID="fd2-real-cli",
        GROWTH_CONSOLE_ADS_DB=str(tmp_path / "ads.db"),
        GROWTH_CONSOLE_ADS_CTL_DIR=str(tmp_path / "ads-ctl"),
        GC_SKIP_DOTENV="1",
        GC_STRIPE_DIR=str(stripe_dir),
        ADS_ATTRIBUTION_DIR=str(attribution_dir),
        PYTHONDONTWRITEBYTECODE="1",
    )
    return gc_dir, env


def _run_precheck(env):
    proc = subprocess.run(
        ["bash", str(CONTROLLER_PRECHECK)],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    return proc, Path(env["OUT_DIR"]) / "inputs"


@pytest.fixture
def real_cli_precheck(real_cli_env):
    _gc_dir, env = real_cli_env
    return _run_precheck(env)


def test_real_cli_collect_failure_turns_loop_red(real_cli_precheck):
    proc, inputs = real_cli_precheck
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "PROBLEM: collect: failed" in proc.stdout
    receipt = json.loads((inputs / "collect.json").read_text())
    assert receipt["status"] == "failed"
    assert receipt["ok"] is False
    assert any(item["source"] == "google.account" for item in receipt["failures"])
    assert any(item["source"] == "stripe.aggregate" for item in receipt["degraded"])
    assert "google.account: google config missing" in proc.stdout


def test_real_cli_reconcile_empty_is_ok(real_cli_precheck):
    proc, inputs = real_cli_precheck
    assert "reconcile: ok" in proc.stdout.splitlines(), proc.stdout + proc.stderr
    receipt = json.loads((inputs / "reconcile.json").read_text())
    assert receipt["status"] == "succeeded"
    assert receipt["ok"] is True
    assert receipt["executed"] == []


def test_real_cli_degraded_turns_loop_amber(real_cli_env):
    gc_dir, env = real_cli_env
    commands_path = gc_dir / "console" / "ads" / "ctl_commands.py"
    if not commands_path.is_file():
        pytest.skip(
            f"real ctl_cli degradation test unavailable: missing {commands_path}"
        )
    seeded = subprocess.run(
        [str(gc_dir / ".venv" / "bin" / "python"), "-"],
        input=SEED_UNCERTAIN_PAUSE,
        cwd=gc_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    command = json.loads(seeded.stdout)
    env["REAL_CLI_JOB"] = "reconcile"
    proc, inputs = _run_precheck(env)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "DEGRADED: reconcile:" in proc.stdout
    assert "reconcile: ok" not in proc.stdout
    assert "PROBLEM:" not in proc.stdout
    receipt = json.loads((inputs / "reconcile.json").read_text())
    assert receipt["status"] == "degraded"
    assert receipt["ok"] is True
    assert receipt["failures"] == []
    assert receipt["degraded"] == [
        {
            "key": "g-msg",
            "source": "reconcile",
            "idem_key": command["idem_key"],
            "reason": "command outcome uncertain",
        }
    ]
    assert receipt["run_ids"] == [command["run_id"]]
    assert "g-msg/reconcile: command outcome uncertain" in proc.stdout
