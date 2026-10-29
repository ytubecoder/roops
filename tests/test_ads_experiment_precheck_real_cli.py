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
gc_dir = Path(os.environ["MAGUYVA_REPO"]) / "growth-console"
proc = subprocess.run(
    [str(gc_dir / ".venv" / "bin" / "python"), "-m", "console.ads.ctl_cli",
     "tick", "--job", request["job"], "--request-id", request["request_id"],
     "--dry-run"],
    cwd=gc_dir, capture_output=True, text=True, check=False, timeout=30,
)
Path(args[args.index("--out") + 1]).write_text(proc.stdout)
sys.stderr.write(proc.stderr)
# Like the real probe, transport succeeds even when the CLI receipt is failed.
"""


@pytest.fixture
def real_cli_precheck(tmp_path):
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
        if not key.startswith("GOOGLE_ADS_")
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
    proc = subprocess.run(
        ["bash", str(CONTROLLER_PRECHECK)],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    return proc, out_dir / "inputs"


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
