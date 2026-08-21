"""Tests for the systemd install backend — §8.1 install/uninstall.

The backend is selected by platform, not hardcoded: darwin keeps launchd so
this repo's hermetic suite still runs on the macOS dev box, and Linux
(firstparty, WSL later) gets systemd user units.

systemctl is NEVER invoked for real — `LOOPS_SYSTEMCTL` points at a recording
stub, mirroring `LOOPS_LAUNCHCTL`. Unit files are NEVER written to the real
`~/.config/systemd/user` — `LOOPS_SYSTEMD_UNIT_DIR` redirects them into a
tmpdir, so §11's "no test touches anything outside the repo" still holds.
"""

import importlib.machinery
import importlib.util
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOOPCTL = REPO_ROOT / "bin" / "loopctl"

spec = importlib.util.spec_from_loader(
    "loopctl_systemd_mod",
    importlib.machinery.SourceFileLoader("loopctl_systemd_mod", str(LOOPCTL)),
)
loopctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(loopctl)

FAKE_SYSTEMCTL_SRC = """#!/usr/bin/env python3
import os
import sys

log_path = os.environ.get("FAKE_SYSTEMCTL_LOG")
if log_path:
    with open(log_path, "a") as f:
        f.write(" ".join(sys.argv[1:]) + "\\n")

sys.exit(int(os.environ.get("FAKE_SYSTEMCTL_RC", "0")))
"""


class SystemdFixture:
    """A tmp unit dir plus a recording systemctl stub."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="loops-systemd-test-")
        self.unit_dir = os.path.join(self.dir, "units")
        os.makedirs(self.unit_dir, exist_ok=True)
        self.log = os.path.join(self.dir, "systemctl.log")
        self.bin = os.path.join(self.dir, "fake_systemctl.py")
        with open(self.bin, "w") as f:
            f.write(FAKE_SYSTEMCTL_SRC)
        os.chmod(self.bin, os.stat(self.bin).st_mode | stat.S_IEXEC | stat.S_IXGRP)

    def activate(self):
        os.environ["LOOPS_SYSTEMCTL"] = self.bin
        os.environ["LOOPS_SYSTEMD_UNIT_DIR"] = self.unit_dir
        os.environ["FAKE_SYSTEMCTL_LOG"] = self.log

    def calls(self):
        if not os.path.isfile(self.log):
            return []
        with open(self.log) as f:
            return [ln.rstrip("\n") for ln in f if ln.strip()]

    def cleanup(self):
        for k in ("LOOPS_SYSTEMCTL", "LOOPS_SYSTEMD_UNIT_DIR",
                  "FAKE_SYSTEMCTL_LOG", "FAKE_SYSTEMCTL_RC"):
            os.environ.pop(k, None)
        shutil.rmtree(self.dir, ignore_errors=True)


class TestBackendSelection(unittest.TestCase):
    def test_darwin_selects_launchd(self):
        self.assertEqual(loopctl._install_backend("darwin"), "launchd")

    def test_linux_selects_systemd(self):
        self.assertEqual(loopctl._install_backend("linux"), "systemd")

    def test_platform_defaults_to_this_host(self):
        """No argument must resolve, so a caller can never accidentally pick a
        backend for the wrong machine by forgetting the parameter."""
        self.assertIn(loopctl._install_backend(), ("launchd", "systemd"))


class TestSystemdUnitRendering(unittest.TestCase):
    def test_timer_carries_the_parsed_oncalendar(self):
        unit = loopctl._render_systemd_timer("ads-x", {"OnCalendar": "*-*-* 19:15:00"})
        self.assertIn("OnCalendar=*-*-* 19:15:00", unit)
        self.assertIn("Persistent=true", unit)

    def test_timer_renders_every_entry_of_a_times_schedule(self):
        unit = loopctl._render_systemd_timer(
            "ads-x", {"OnCalendar": ["*-*-* 07:30:00", "*-*-* 19:30:00"]})
        self.assertIn("OnCalendar=*-*-* 07:30:00", unit)
        self.assertIn("OnCalendar=*-*-* 19:30:00", unit)

    def test_interval_timer_gets_an_initial_trigger(self):
        """OnUnitActiveSec alone never fires until the service has run once —
        unlike launchd's StartInterval. Without OnBootSec an interval loop
        installed and then rebooted would go silent forever."""
        unit = loopctl._render_systemd_timer("ads-x", {"OnUnitActiveSec": "900s"})
        self.assertIn("OnUnitActiveSec=900s", unit)
        self.assertIn("OnBootSec=", unit)

    def test_timer_is_installable(self):
        unit = loopctl._render_systemd_timer("ads-x", {"OnCalendar": "*-*-* 19:15:00"})
        self.assertIn("WantedBy=timers.target", unit)

    def test_service_runs_the_loop_and_does_not_restart(self):
        """A scheduled one-shot must not be restarted on exit — the timer owns
        cadence. Restart=always here would spin a failing loop continuously."""
        unit = loopctl._render_systemd_service("ads-x", str(REPO_ROOT))
        self.assertIn("Type=oneshot", unit)
        self.assertNotIn("Restart=always", unit)

    def test_service_declares_the_scheduler_trigger(self):
        """run-loop.sh defaults to --trigger manual, and BOTH of its §4.1 step 1
        guards exempt manual: a paused loop and a schedule=manual loop each run
        anyway when the trigger says manual. A timer unit that omits the flag
        therefore silently defeats `loopctl pause` on Linux."""
        unit = loopctl._render_systemd_service("ads-x", str(REPO_ROOT))
        self.assertIn("--trigger launchd", unit)

    def test_service_does_not_time_out_a_long_engine_run(self):
        """Type=oneshot is bounded by DefaultTimeoutStartSec (90s upstream).
        Real codex runs exceed that — the repo already documents a 90s poll
        that 'can never outlast a codex verification run' — so systemd would
        SIGTERM every scheduled run at 90 seconds."""
        unit = loopctl._render_systemd_service("ads-x", str(REPO_ROOT))
        self.assertIn("TimeoutStartSec=infinity", unit)

    def test_service_supplies_the_minimal_env(self):
        unit = loopctl._render_systemd_service("ads-x", str(REPO_ROOT))
        self.assertIn(f"WorkingDirectory={os.path.abspath(REPO_ROOT)}", unit)
        self.assertIn("LOOPS_ROOT=", unit)
        self.assertIn(".local/bin", unit)

    def test_units_are_rendered_from_a_parsed_schedule(self):
        """The Task 17 -> Task 18 seam: whatever schedule.parse emits is what
        the timer carries, with no second grammar in between."""
        parsed = loopctl.schedule.parse("weekly:mon:08:00")
        unit = loopctl._render_systemd_timer("ads-x", parsed["systemd"])
        self.assertIn("OnCalendar=Mon *-*-* 08:00:00", unit)


class TestSystemdInstallActions(unittest.TestCase):
    def setUp(self):
        self.fx = SystemdFixture()
        self.fx.activate()
        self.addCleanup(self.fx.cleanup)

    def test_unit_dir_honours_the_test_seam(self):
        self.assertEqual(loopctl._systemd_unit_dir(), self.fx.unit_dir)

    def test_write_units_lands_both_files(self):
        loopctl._systemd_write_units(str(REPO_ROOT), "ads-x",
                                     {"schedule": "daily:19:15"})
        svc, timer = loopctl._systemd_unit_paths("ads-x")
        self.assertTrue(os.path.isfile(svc))
        self.assertTrue(os.path.isfile(timer))
        self.assertIn("OnCalendar=*-*-* 19:15:00", Path(timer).read_text())

    def test_install_enables_the_timer_and_reloads(self):
        rc = loopctl._systemd_install(str(REPO_ROOT), "ads-x",
                                      {"schedule": "daily:19:15"})
        self.assertEqual(rc, 0)
        calls = " | ".join(self.fx.calls())
        self.assertIn("--user daemon-reload", calls)
        self.assertIn("enable --now loops-ads-x.timer", calls)

    def test_install_fires_one_verification_run_without_blocking(self):
        """The launchd backend kickstarts so §8.1 step 5 has a fresh run to
        verify. --no-block matters: `start` on a Type=oneshot otherwise waits
        for the whole engine run."""
        loopctl._systemd_install(str(REPO_ROOT), "ads-x", {"schedule": "daily:19:15"})
        calls = " | ".join(self.fx.calls())
        self.assertIn("start --no-block loops-ads-x.service", calls)

    def test_uninstall_disables_and_removes_both_units(self):
        loopctl._systemd_install(str(REPO_ROOT), "ads-x", {"schedule": "daily:19:15"})
        removed = loopctl._systemd_uninstall("ads-x")
        self.assertTrue(removed)
        svc, timer = loopctl._systemd_unit_paths("ads-x")
        self.assertFalse(os.path.isfile(svc))
        self.assertFalse(os.path.isfile(timer))
        self.assertIn("disable --now loops-ads-x.timer", " | ".join(self.fx.calls()))

    def test_uninstall_of_an_absent_loop_is_not_an_error(self):
        self.assertFalse(loopctl._systemd_uninstall("never-installed"))

    def test_pause_disables_the_timer_but_keeps_the_units(self):
        loopctl._systemd_install(str(REPO_ROOT), "ads-x", {"schedule": "daily:19:15"})
        loopctl._systemd_set_enabled("ads-x", False)
        svc, timer = loopctl._systemd_unit_paths("ads-x")
        self.assertTrue(os.path.isfile(timer), "pause must not uninstall")
        self.assertIn("disable --now loops-ads-x.timer", " | ".join(self.fx.calls()))

    def test_is_installed_requires_the_unit_file(self):
        self.assertFalse(loopctl._systemd_is_installed("ads-x"))
        loopctl._systemd_install(str(REPO_ROOT), "ads-x", {"schedule": "daily:19:15"})
        self.assertTrue(loopctl._systemd_is_installed("ads-x"))

    def test_systemctl_is_never_the_real_binary_under_test(self):
        self.assertEqual(loopctl._systemctl_bin(), self.fx.bin)


class TestLinuxDispatchFromDarwin(unittest.TestCase):
    """LOOPS_INSTALL_BACKEND lets the macOS dev box exercise the Linux code
    path. Without this the systemd branches would ship untested until the
    first real install on firstparty."""

    def setUp(self):
        self.fx = SystemdFixture()
        self.fx.activate()
        os.environ["LOOPS_INSTALL_BACKEND"] = "systemd"
        self.addCleanup(os.environ.pop, "LOOPS_INSTALL_BACKEND", None)
        self.addCleanup(self.fx.cleanup)
        self.root = tempfile.mkdtemp(prefix="loops-root-test-")
        self.addCleanup(shutil.rmtree, self.root, True)
        loop_dir = os.path.join(self.root, "loops.d", "ads-x")
        os.makedirs(loop_dir, exist_ok=True)
        self.conf_path = os.path.join(loop_dir, "loop.conf")
        Path(self.conf_path).write_text(
            "name=ads-x\nowner=loops\ntype=report\nengine=codex\n"
            "enabled=true\nschedule=daily:19:15\n"
        )

    def test_env_override_beats_the_host_platform(self):
        self.assertEqual(loopctl._install_backend(), "systemd")

    def test_an_explicit_platform_still_wins_over_the_override(self):
        self.assertEqual(loopctl._install_backend("darwin"), "launchd")

    def test_a_bogus_override_is_refused_not_guessed(self):
        os.environ["LOOPS_INSTALL_BACKEND"] = "upstart"
        with self.assertRaises(ValueError):
            loopctl._install_backend()

    def test_is_installed_reads_the_systemd_state(self):
        self.assertFalse(loopctl._is_installed(self.root, "ads-x"))
        loopctl._systemd_install(self.root, "ads-x", {"schedule": "daily:19:15"})
        self.assertTrue(loopctl._is_installed(self.root, "ads-x"))

    def test_reschedule_rewrites_the_timer_and_fires_no_run(self):
        loopctl._systemd_install(self.root, "ads-x", {"schedule": "daily:19:15"})
        loopctl._apply_schedule(self.root, "loops.d", "ads-x", "daily:06:45")
        _svc, timer = loopctl._systemd_unit_paths("ads-x")
        self.assertIn("OnCalendar=*-*-* 06:45:00", Path(timer).read_text())
        calls = " | ".join(self.fx.calls())
        self.assertIn("restart loops-ads-x.timer", calls)
        self.assertNotIn("start --no-block loops-ads-x.service | ",
                         calls.split("restart")[-1] + " | ",
                         "rescheduling must never fire a run")

    def test_reschedule_to_manual_uninstalls(self):
        loopctl._systemd_install(self.root, "ads-x", {"schedule": "daily:19:15"})
        loopctl._apply_schedule(self.root, "loops.d", "ads-x", "manual")
        svc, timer = loopctl._systemd_unit_paths("ads-x")
        self.assertFalse(os.path.isfile(svc))
        self.assertFalse(os.path.isfile(timer))

    def test_reschedule_of_an_uninstalled_loop_touches_no_units(self):
        loopctl._apply_schedule(self.root, "loops.d", "ads-x", "daily:06:45")
        svc, timer = loopctl._systemd_unit_paths("ads-x")
        self.assertFalse(os.path.isfile(timer))
        self.assertIn("schedule=daily:06:45", Path(self.conf_path).read_text())

    def test_failed_install_is_torn_down(self):
        loopctl._systemd_install(self.root, "ads-x", {"schedule": "daily:19:15"})
        loopctl._install_teardown(self.root, "ads-x")
        _svc, timer = loopctl._systemd_unit_paths("ads-x")
        self.assertFalse(os.path.isfile(timer))


if __name__ == "__main__":
    unittest.main()
