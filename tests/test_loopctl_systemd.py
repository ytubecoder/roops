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
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_loopctl import LoopsRoot, _start_probe, run_cli

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
env_log = os.environ.get("FAKE_SYSTEMCTL_ENV_LOG")
if env_log:
    with open(env_log, "a") as f:
        f.write(os.environ.get("XDG_RUNTIME_DIR", "") + "\\n")

sys.exit(int(os.environ.get("FAKE_SYSTEMCTL_RC", "0")))
"""

FAKE_LOGINCTL_SRC = """#!/usr/bin/env python3
import os
import sys
print("Linger=" + os.environ.get("FAKE_LOGINCTL_LINGER", "yes"))
sys.exit(int(os.environ.get("FAKE_LOGINCTL_RC", "0")))
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


def _write_fake_bin(path, src):
    with open(path, "w") as f:
        f.write(src)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC | stat.S_IXGRP)
    return path


class TestSystemdPredicatesAndHostChecks(unittest.TestCase):
    def setUp(self):
        self.fx = SystemdFixture()
        self.fx.activate()
        os.environ["LOOPS_INSTALL_BACKEND"] = "systemd"
        self.addCleanup(os.environ.pop, "LOOPS_INSTALL_BACKEND", None)
        self.addCleanup(self.fx.cleanup)
        self.loginctl = _write_fake_bin(
            os.path.join(self.fx.dir, "fake_loginctl.py"), FAKE_LOGINCTL_SRC
        )
        os.environ["LOOPS_LOGINCTL"] = self.loginctl
        self.addCleanup(os.environ.pop, "LOOPS_LOGINCTL", None)
        self.env_log = os.path.join(self.fx.dir, "systemctl.env.log")
        os.environ["FAKE_SYSTEMCTL_ENV_LOG"] = self.env_log
        self.addCleanup(os.environ.pop, "FAKE_SYSTEMCTL_ENV_LOG", None)

    def test_unit_files_present_requires_both_units(self):
        name = "ads-x"
        self.assertFalse(loopctl.unit_files_present("/unused", name))
        svc, timer = loopctl._systemd_unit_paths(name)
        Path(svc).write_text("[Service]\n")
        self.assertFalse(loopctl.unit_files_present("/unused", name))
        Path(timer).write_text("[Timer]\n")
        self.assertTrue(loopctl.unit_files_present("/unused", name))

    def test_scheduler_loaded_uses_is_enabled_with_xdg_default(self):
        uid = os.getuid()
        with mock.patch.dict(os.environ, {"FAKE_SYSTEMCTL_RC": "0"}):
            os.environ.pop("XDG_RUNTIME_DIR", None)
            self.assertTrue(loopctl.scheduler_loaded("ads-x"))
        with open(self.env_log) as f:
            seen = [ln.strip() for ln in f if ln.strip()]
        self.assertIn(f"/run/user/{uid}", seen)
        calls = " | ".join(self.fx.calls())
        self.assertIn("is-enabled loops-ads-x.timer", calls)
        open(self.env_log, "w").close()
        open(self.fx.log, "w").close()
        os.environ["FAKE_SYSTEMCTL_RC"] = "1"
        os.environ.pop("XDG_RUNTIME_DIR", None)
        try:
            self.assertFalse(loopctl.scheduler_loaded("ads-x"))
        finally:
            os.environ.pop("FAKE_SYSTEMCTL_RC", None)

    def test_host_checks_linger_tz_xdg(self):
        root = tempfile.mkdtemp(prefix="loops-hostcheck-")
        self.addCleanup(shutil.rmtree, root, True)
        run_user = os.path.join(self.fx.dir, "run-user")
        os.makedirs(run_user, exist_ok=True)
        user = os.environ.get("USER", "unknown")

        os.environ["FAKE_LOGINCTL_LINGER"] = "no"
        msgs = loopctl._host_checks(root, run_user_dir=run_user)
        self.assertTrue(any("enable-linger" in m for m in msgs), msgs)
        self.assertTrue(any(user in m for m in msgs), msgs)

        os.environ["FAKE_LOGINCTL_LINGER"] = "yes"
        msgs = loopctl._host_checks(root, run_user_dir=run_user)
        self.assertFalse(any("enable-linger" in m for m in msgs), msgs)

        zone_root = os.path.join(self.fx.dir, "zones")
        utc_dir = os.path.join(zone_root, "zoneinfo", "Etc")
        manila_dir = os.path.join(zone_root, "zoneinfo", "Asia")
        os.makedirs(utc_dir, exist_ok=True)
        os.makedirs(manila_dir, exist_ok=True)
        Path(os.path.join(utc_dir, "UTC")).write_text("")
        Path(os.path.join(manila_dir, "Manila")).write_text("")
        utc_link = os.path.join(self.fx.dir, "localtime-utc")
        manila_link = os.path.join(self.fx.dir, "localtime-manila")
        os.symlink(os.path.join(utc_dir, "UTC"), utc_link)
        os.symlink(os.path.join(manila_dir, "Manila"), manila_link)

        Path(os.path.join(root, ".env")).write_text("LOOPS_EXPECT_TZ=Asia/Manila\n")
        with mock.patch.dict(os.environ, {"LOOPS_LOCALTIME_PATH": utc_link}):
            msgs = loopctl._host_checks(root, run_user_dir=run_user)
        joined = " ".join(msgs)
        self.assertIn("Etc/UTC", joined)
        self.assertIn("Asia/Manila", joined)

        with mock.patch.dict(os.environ, {"LOOPS_LOCALTIME_PATH": manila_link}):
            msgs = loopctl._host_checks(root, run_user_dir=run_user)
        self.assertFalse(any("timezone" in m for m in msgs), msgs)

        Path(os.path.join(root, ".env")).write_text("")
        with mock.patch.dict(os.environ, {"LOOPS_LOCALTIME_PATH": utc_link}):
            msgs = loopctl._host_checks(root, run_user_dir=run_user)
        self.assertFalse(any("timezone" in m for m in msgs), msgs)

        missing = os.path.join(self.fx.dir, "missing-run-user")
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XDG_RUNTIME_DIR", None)
            msgs = loopctl._host_checks(root, run_user_dir=missing)
        self.assertTrue(any("XDG_RUNTIME_DIR" in m for m in msgs), msgs)

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XDG_RUNTIME_DIR", None)
            msgs = loopctl._host_checks(root, run_user_dir=run_user)
        self.assertFalse(any("XDG_RUNTIME_DIR" in m for m in msgs), msgs)

    def _cli_root(self):
        fx = LoopsRoot()
        self.addCleanup(fx.cleanup)
        name = "ready"
        fx.minimal_valid_loop(name)
        fx.write_spec(name, "filled\n" * 11)
        fx.add_run(f"20260101T000000Z-{name}-ok1", name, "2026-01-01T00:00:00Z")
        return fx, name

    def _cli_env(self, fx, **extra):
        xdg = os.path.join(self.fx.dir, "xdg")
        os.makedirs(xdg, exist_ok=True)
        env = fx.base_env(
            LOOPS_INSTALL_BACKEND="systemd",
            LOOPS_SYSTEMCTL=self.fx.bin,
            LOOPS_SYSTEMD_UNIT_DIR=self.fx.unit_dir,
            FAKE_SYSTEMCTL_LOG=self.fx.log,
            LOOPS_LOGINCTL=self.loginctl,
            XDG_RUNTIME_DIR=xdg,
            FAKE_LOGINCTL_LINGER="yes",
            LOOPCTL_INSTALL_POLL_TIMEOUT_S="0.5",
            LOOPCTL_INSTALL_POLL_INTERVAL_S="0.1",
        )
        env.update(extra)
        return env

    def test_install_refuses_on_failed_host_check_before_writing_units(self):
        fx, name = self._cli_root()
        open(self.fx.log, "w").close()
        env = self._cli_env(fx, FAKE_LOGINCTL_LINGER="no")
        r = run_cli(["install", name, "--root", fx.root], env_overrides=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("refusing to install", r.stderr)
        svc, timer = loopctl._systemd_unit_paths(name)
        self.assertFalse(os.path.isfile(svc))
        self.assertFalse(os.path.isfile(timer))
        self.assertEqual(self.fx.calls(), [])

    def test_install_failure_strings_say_systemd(self):
        fx, name = self._cli_root()
        env = self._cli_env(fx)
        r = run_cli(["install", name, "--root", fx.root], env_overrides=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("under the scheduler (systemd)", r.stderr)

    def test_console_install_systemd_unit_singleton_and_verify(self):
        fx, _name = self._cli_root()
        with open(os.path.join(fx.root, ".env"), "w") as f:
            f.write("LOOPS_CONSOLE_ALLOW_HOSTS=a.example,a.example:443\n")
            f.write("LOOPS_CONSOLE_PORT=18929\n")
        httpd = _start_probe(200)
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.server_close)
        probe = f"http://127.0.0.1:{httpd.server_address[1]}/api/state"
        env = self._cli_env(
            fx,
            LOOPS_CONSOLE_PROBE_URL=probe,
            LOOPCTL_CONSOLE_VERIFY_TIMEOUT_S="5",
        )
        open(self.fx.log, "w").close()
        r = run_cli(["console", "install", "--root", fx.root], env_overrides=env)
        self.assertEqual(r.returncode, 0, msg=r.stdout + r.stderr)
        unit = os.path.join(self.fx.unit_dir, "loops-console.service")
        self.assertTrue(os.path.isfile(unit))
        text = Path(unit).read_text()
        self.assertIn("Restart=always", text)
        self.assertIn("WantedBy=default.target", text)
        self.assertIn("append:", text)
        self.assertIn("console.out.log", text)
        self.assertIn("console.err.log", text)
        self.assertIn("serve --port 18929", text)
        self.assertIn("--allow-host a.example", text)
        self.assertIn("--allow-host a.example:443", text)
        calls = " | ".join(self.fx.calls())
        self.assertIn("daemon-reload", calls)
        self.assertIn("enable --now loops-console.service", calls)

        other = tempfile.mkdtemp(prefix="loops-other-root-")
        self.addCleanup(shutil.rmtree, other, True)
        Path(os.path.join(other, ".env")).write_text("LOOPS_CONSOLE_PORT=18929\n")
        r2 = run_cli(["console", "install", "--root", other], env_overrides=env)
        self.assertEqual(r2.returncode, 1)
        self.assertIn("belongs to", r2.stderr)

        un = run_cli(["console", "uninstall", "--root", fx.root], env_overrides=env)
        self.assertEqual(un.returncode, 0, msg=un.stdout + un.stderr)
        self.assertFalse(os.path.isfile(unit))
        later = " | ".join(self.fx.calls())
        self.assertIn("disable --now loops-console.service", later)
        self.assertGreaterEqual(later.count("daemon-reload"), 2)


if __name__ == "__main__":
    unittest.main()
