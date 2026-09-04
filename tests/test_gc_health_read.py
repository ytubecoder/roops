"""Hermetic tests for probes/gc-health-read.

Temp roots only. No network beyond a 127.0.0.1 http.server this file starts.
Never touches real state/, ~/.opentwins, or ~/projects.
"""
import http.server
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN_FILES = (
    "probe",
    "probe_core.py",
    "loopconf.py",
    "requirements.py",
    "schedule.py",
)
NOW = "2026-09-04T21:00:00Z"
NOW_DT = datetime(2026, 9, 4, 21, 0, 0, tzinfo=timezone.utc)
SECRET = "fixture-secret-KEY-123"

HEALTHY_BODY = (
    '- § 2/§ 3: … Session health check: sideNav/primaryColumn/hasCt0/hasTwid '
    'all true, accountName "maguyva", title "(4) Notifications / X" - '
    "[[twitter_session_logout]] healthy, no login-redirect, session now "
    "stable across the full Day58"
)
RELAPSED_BODY = (
    "🚨 § 2/§3: [[twitter_session_logout]] RELAPSED - Browser open + explicit "
    "navigate to /notifications/mentions both {ok:true} (API-layer only, "
    "doesn't confirm live cookies)"
)
STILL_DOWN_BODY = (
    "🚨 § 2/§3: [[twitter_session_logout]] STILL DOWN - 23rd consecutive check "
    "on this relapse (~17h45m in, since 07:18 EDT 08-31 first detection). "
    "… `https://x.com/i/jf/onboarding/web?redirect_after_login="
    "%2Fnotifications%2Fmentions&mode=login`, title "
    '"X - The Everything App / X", cookies only guest_id/guest_id_marketing/'
    "guest_id_ads/gt/__cuid (no twid/ct0/auth_token). Genuine full session logout"
)
RECOVERED_BODY = (
    "🚨 § 2/3: [[twitter_session_logout]] RECOVERED - opened browser, navigated "
    "to /notifications/mentions, combined session-state evaluate confirmed "
    "genuine live session: sideNav:true, primaryColumn:true, hasCt0:true, "
    "hasTwid:true"
)
STILL_HEALTHY_BODY = (
    "- § 2/§ 3: … [[twitter_session_logout]] still healthy."
)


def _copy_bin(root):
    dest = os.path.join(root, "bin")
    os.makedirs(dest, exist_ok=True)
    for name in BIN_FILES:
        shutil.copy(os.path.join(REPO, "bin", name), os.path.join(dest, name))
    os.chmod(os.path.join(dest, "probe"), 0o755)


def _copy_probe(root):
    probes = os.path.join(root, "probes")
    os.makedirs(probes, exist_ok=True)
    src = os.path.join(REPO, "probes", "gc-health-read")
    dst = os.path.join(probes, "gc-health-read")
    shutil.copy(src, dst)
    os.chmod(dst, 0o755)
    return dst


def _write_exec(path, body):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(body)
        if not body.endswith("\n"):
            f.write("\n")
    os.chmod(path, 0o755)


def _row(name, status, last_ok="2026-09-04 05:33", error=None,
         last_error_at=None, schedule="every 1 hour"):
    return {
        "name": name,
        "status": status,
        "last_ok": last_ok,
        "error": error,
        "last_error_at": last_error_at,
        "schedule": schedule,
        "method": "probe",
        "feeds": [],
        "note": "",
        "count": 1,
    }


def _green_schedules():
    return {
        "automated": [
            _row("opentwins twitter heartbeat", "ok"),
            _row("opentwins reddit heartbeat", "off", last_ok="2026-07-25 05:31"),
            _row("gc cache warmer", "never", last_ok=None),
            _row("ads-google loop", "ok"),
        ],
        "manual": [
            _row("website knowledge (maguyva.ai)", "ok"),
        ],
        "live": [],
    }


def _heartbeat(stamp, body, edt=None):
    if edt:
        hdr = f"## {stamp} UTC ({edt} EDT) - Heartbeat (Day55, 1st run)\n"
    else:
        hdr = f"## {stamp} UTC - Heartbeat (Day59, 1st run)\n"
    return hdr + body + "\n\n"


def _log_line(msg, profile=None, platform=None):
    data = {}
    if profile is not None:
        data["profile"] = profile
    if platform is not None:
        data["platform"] = platform
    return json.dumps({
        "ts": "2026-09-04T00:00:00Z",
        "level": "info",
        "mod": "chrome",
        "msg": msg,
        "data": data,
    })


def _task(tid, action, status, notes="", time="09:00"):
    return {
        "id": tid,
        "action": action,
        "time": time,
        "status": status,
        "notes": notes,
    }


class _PostizStore:
    def __init__(self):
        self.integrations = [
            {"id": "1", "identifier": "x", "name": "maguyva", "disabled": False},
            {"id": "2", "identifier": "facebook", "name": "maguyva", "disabled": False},
            {"id": "3", "identifier": "linkedin-page", "name": "maguyva", "disabled": False},
        ]
        self.posts = []
        self.status = 200


def _make_handler(store):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/integrations":
                body = store.integrations
            elif path == "/posts":
                body = store.posts
            else:
                self.send_response(404)
                self.end_headers()
                return
            raw = json.dumps(body).encode()
            self.send_response(store.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    return Handler


def _start_http(store):
    handler = _make_handler(store)
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def _closed_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class ProbeFixture:
    def __init__(self, schedules=None, postiz_base=None):
        self.root = tempfile.mkdtemp(prefix="gc-health-")
        self.home = os.path.join(self.root, "home")
        os.makedirs(self.home, exist_ok=True)
        _copy_bin(self.root)
        self.probe_path = _copy_probe(self.root)
        self.maguyva = os.path.join(self.root, "maguyva-marketing")
        self.gc_dir = os.path.join(self.maguyva, "growth-console")
        self.ot_home = os.path.join(self.root, "ot-home")
        os.makedirs(os.path.join(self.ot_home, "workspaces", "agent-twitter", "memory"),
                    exist_ok=True)
        os.makedirs(os.path.join(self.ot_home, "logs"), exist_ok=True)
        os.makedirs(self.gc_dir, exist_ok=True)
        env_path = os.path.join(self.gc_dir, ".env")
        with open(env_path, "w") as f:
            f.write(f"POSTIZ_API_KEY={SECRET}\n")
        self.write_gc_python(schedules if schedules is not None else _green_schedules())
        self.write_memory("2026-09-04.md", _heartbeat("2026-09-04 20:31", HEALTHY_BODY))
        self.write_schedule({
            "metadata": {"for_date": "2026-09-04"},
            "tasks": [
                _task("t1", "reply", "done"),
                _task("t2", "reply", "done"),
                _task("t3", "like", "pending"),
            ],
        })
        self.store = _PostizStore()
        self.httpd = None
        self.postiz_base = postiz_base
        if postiz_base is None:
            self.httpd = _start_http(self.store)
            port = self.httpd.server_address[1]
            self.postiz_base = f"http://127.0.0.1:{port}"
        self._write_loops_env()

    def _write_loops_env(self):
        path = os.path.join(self.root, ".env")
        with open(path, "w") as f:
            f.write(f"MAGUYVA_REPO={self.maguyva}\n")
            f.write(f"OT_HOME={self.ot_home}\n")
            f.write(f"POSTIZ_API_BASE={self.postiz_base}\n")
            f.write(f"GC_HEALTH_NOW={NOW}\n")

    def write_gc_python(self, payload=None, fail_stderr=None):
        venv_bin = os.path.join(self.gc_dir, ".venv", "bin")
        os.makedirs(venv_bin, exist_ok=True)
        path = os.path.join(venv_bin, "python")
        if fail_stderr is not None:
            body = f"#!/bin/sh\necho {fail_stderr} >&2\nexit 3\n"
        else:
            raw = json.dumps(payload)
            body = "#!/bin/sh\ncat <<'JSON'\n" + raw + "\nJSON\n"
        _write_exec(path, body)
        return path

    def gc_py(self):
        return os.path.join(self.gc_dir, ".venv", "bin", "python")

    def write_memory(self, name, text):
        path = os.path.join(
            self.ot_home, "workspaces", "agent-twitter", "memory", name
        )
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        return path

    def clear_memory(self):
        mem = os.path.join(self.ot_home, "workspaces", "agent-twitter", "memory")
        for name in os.listdir(mem):
            os.remove(os.path.join(mem, name))

    def write_schedule(self, obj):
        path = os.path.join(
            self.ot_home, "workspaces", "agent-twitter", "schedule.json"
        )
        with open(path, "w") as f:
            json.dump(obj, f)
        return path

    def write_log(self, day, lines):
        path = os.path.join(self.ot_home, "logs", f"opentwins-{day}.log")
        with open(path, "w") as f:
            f.write("\n".join(lines) + "\n")
        return path

    def client_env(self, extra=None):
        env = dict(os.environ)
        env["HOME"] = self.home
        env.pop("LOOPS_PROBE_HOST", None)
        env.pop("LOOPS_PROBE_KEY", None)
        env.pop("POSTIZ_API_KEY", None)
        env.pop("POSTIZ_API_BASE", None)
        env.pop("MAGUYVA_REPO", None)
        env.pop("OT_HOME", None)
        env.pop("GC_HEALTH_NOW", None)
        if extra:
            env.update(extra)
        return env

    def direct_env(self, extra=None):
        env = self.client_env()
        env["MAGUYVA_REPO"] = self.maguyva
        env["OT_HOME"] = self.ot_home
        env["POSTIZ_API_BASE"] = self.postiz_base
        env["GC_HEALTH_NOW"] = NOW
        if extra:
            env.update(extra)
        return env

    def run_check(self):
        return subprocess.run(
            [self.probe_path, "--check"],
            capture_output=True,
            text=True,
            env=self.direct_env(),
            cwd=self.root,
            check=False,
        )

    def run_probe(self):
        out_dir = os.path.join(self.root, "out")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "gc-health.json")
        proc = subprocess.run(
            [os.path.join(self.root, "bin", "probe"),
             "gc-health-read", "--out", out_path],
            capture_output=True,
            text=True,
            env=self.client_env(),
            cwd=self.root,
            check=False,
        )
        data = None
        if os.path.isfile(out_path):
            with open(out_path) as f:
                raw = f.read()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = None
        return proc, out_path, data

    def close(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        shutil.rmtree(self.root, ignore_errors=True)


def _section_rank(fid):
    if fid.startswith("gc:"):
        return 0
    if fid.startswith("opentwins:"):
        return 1
    if fid.startswith("postiz:"):
        return 2
    if fid.startswith("probe:"):
        return 3
    return 4


class GcHealthReadTests(unittest.TestCase):
    def tearDown(self):
        fx = getattr(self, "fx", None)
        if fx is not None:
            fx.close()

    def _fx(self, **kw):
        self.fx = ProbeFixture(**kw)
        return self.fx

    def test_check_ok_with_fixture_inputs(self):
        fx = self._fx()
        proc = fx.run_check()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(proc.stdout.strip(), "ok gc-health-read")

    def test_check_unmet_when_venv_missing(self):
        fx = self._fx()
        gc_py = fx.gc_py()
        os.remove(gc_py)
        proc = fx.run_check()
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertTrue(
            gc_py in proc.stdout
            or "venv" in proc.stdout.lower()
            or "python" in proc.stdout.lower(),
            proc.stdout,
        )

    def test_all_green_has_no_findings_and_exit_0(self):
        fx = self._fx()
        proc, out_path, data = fx.run_probe()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(os.path.isfile(out_path))
        self.assertIsNotNone(data)
        self.assertIsNone(data["sections"]["schedules"]["error"])
        self.assertIsNone(data["sections"]["opentwins"]["error"])
        self.assertIsNone(data["sections"]["postiz"]["error"])
        self.assertEqual(data["findings"], [])

    def test_error_and_overdue_rows_become_findings(self):
        schedules = {
            "automated": [
                _row(
                    "linkedin notifications",
                    "error",
                    last_ok="2026-09-03 06:19",
                    error="selector miss",
                    last_error_at="2026-09-04 06:00",
                    schedule="every 20 min",
                ),
                _row(
                    "opentwins twitter heartbeat",
                    "overdue",
                    last_ok="2026-09-03 05:33",
                    schedule="every 1 hour",
                ),
            ],
            "manual": [],
            "live": [],
        }
        fx = self._fx(schedules=schedules)
        proc, _out, data = fx.run_probe()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        by_id = {f["id"]: f for f in data["findings"]}
        err = by_id["gc:linkedin-notifications:error"]
        self.assertEqual(err["severity"], "alert")
        self.assertIn("linkedin notifications", err["detail"])
        overdue = by_id["gc:opentwins-twitter-heartbeat:overdue"]
        self.assertEqual(overdue["severity"], "warn")
        self.assertIn("opentwins twitter heartbeat", overdue["detail"])

    def test_excluded_and_manual_rows_never_alarm(self):
        schedules = {
            "automated": [
                _row("ads-google loop", "overdue", last_ok="2026-08-01 00:00"),
                _row("gc cache warmer", "never", last_ok=None),
            ],
            "manual": [
                _row(
                    "website knowledge (maguyva.ai)",
                    "overdue",
                    last_ok="2026-08-01 00:00",
                ),
            ],
            "live": [],
        }
        fx = self._fx(schedules=schedules)
        proc, _out, data = fx.run_probe()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(data["findings"], [])
        excluded = data["sections"]["schedules"]["excluded"]
        blob = json.dumps(excluded)
        self.assertIn("ads-google loop", blob)
        self.assertIn("gc cache warmer", blob)
        manual = data["sections"]["schedules"]["manual"]
        names = [r["name"] if isinstance(r, dict) else r for r in manual]
        self.assertIn("website knowledge (maguyva.ai)", names)

    def test_logged_out_session_from_memory(self):
        fx = self._fx()
        fx.clear_memory()
        fx.write_memory(
            "2026-08-31.md",
            _heartbeat("2026-08-31 06:00", HEALTHY_BODY, edt="02:00"),
        )
        day2 = (
            _heartbeat("2026-08-31 07:18", RELAPSED_BODY, edt="03:18")
            + _heartbeat("2026-08-31 08:18", STILL_DOWN_BODY)
            + _heartbeat("2026-08-31 09:18", STILL_DOWN_BODY)
            + _heartbeat("2026-08-31 10:18", STILL_DOWN_BODY)
        )
        fx.write_memory("2026-09-01.md", day2)
        proc, _out, data = fx.run_probe()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        ses = data["sections"]["opentwins"]["session"]
        self.assertEqual(ses["state"], "logged_out")
        self.assertEqual(ses["since"], "2026-08-31 07:18")
        self.assertEqual(ses["consecutive"], 4)
        self.assertTrue(ses["login_did_not_stick"])
        by_id = {f["id"]: f for f in data["findings"]}
        hit = by_id["opentwins:twitter:logged-out"]
        self.assertEqual(hit["severity"], "alert")
        self.assertIn("did not stick", hit["detail"])

    def test_still_healthy_is_logged_in(self):
        fx = self._fx()
        fx.clear_memory()
        fx.write_memory(
            "2026-09-04.md",
            _heartbeat("2026-09-04 20:31", STILL_HEALTHY_BODY),
        )
        proc, _out, data = fx.run_probe()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(
            data["sections"]["opentwins"]["session"]["state"], "logged_in"
        )
        ids = [f["id"] for f in data["findings"]]
        self.assertNotIn("opentwins:twitter:logged-out", ids)
        self.assertNotIn("opentwins:twitter:locked", ids)

    def test_recovery_entry_later_evidence_wins(self):
        fx = self._fx()
        fx.clear_memory()
        body = (
            "early cookies only guest_id (no twid/ct0/auth_token) then later "
            "hasTwid:true … [[twitter_session_logout]] RECOVERED"
        )
        fx.write_memory("2026-09-04.md", _heartbeat("2026-09-04 16:00", body))
        proc, _out, data = fx.run_probe()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(
            data["sections"]["opentwins"]["session"]["state"], "logged_in"
        )

    def test_locked_outranks_everything(self):
        fx = self._fx()
        fx.clear_memory()
        body = "account has been locked but hasTwid:true still listed"
        fx.write_memory("2026-09-04.md", _heartbeat("2026-09-04 16:00", body))
        proc, _out, data = fx.run_probe()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(
            data["sections"]["opentwins"]["session"]["state"], "locked"
        )
        by_id = {f["id"]: f for f in data["findings"]}
        hit = by_id["opentwins:twitter:locked"]
        self.assertEqual(hit["severity"], "alert")

    def test_launch_cycle_stalled_alert(self):
        fx = self._fx()
        stalled = [_log_line("Run started", platform="twitter") for _ in range(23)]
        stalled += [_log_line("Chrome launched", profile="ot-tracker") for _ in range(5)]
        fx.write_log("2026-09-03", stalled)
        proc, _out, data = fx.run_probe()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        by_id = {f["id"]: f for f in data["findings"]}
        hit = by_id["opentwins:twitter:launch-cycle-stalled"]
        self.assertEqual(hit["severity"], "alert")
        days = {d["day"]: d for d in data["sections"]["opentwins"]["launches"]}
        self.assertEqual(days["2026-09-03"]["launched"], 0)
        self.assertEqual(days["2026-09-03"]["runs"], 23)

        healthy = [_log_line("Run started", platform="twitter") for _ in range(23)]
        healthy += [
            _log_line("Chrome launched", profile="ot-twitter") for _ in range(20)
        ]
        fx.write_log("2026-09-03", healthy)
        proc2, _out2, data2 = fx.run_probe()
        self.assertEqual(proc2.returncode, 0, proc2.stdout + proc2.stderr)
        ids = [f["id"] for f in data2["findings"]]
        self.assertNotIn("opentwins:twitter:launch-cycle-stalled", ids)

    def test_cdp_errors_warn(self):
        fx = self._fx()
        lines = [_log_line("Run started", platform="twitter") for _ in range(5)]
        lines += [_log_line("navigate failed", platform="twitter") for _ in range(21)]
        lines += [_log_line("evaluate failed", platform="twitter") for _ in range(11)]
        fx.write_log("2026-09-04", lines)
        proc, _out, data = fx.run_probe()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        by_id = {f["id"]: f for f in data["findings"]}
        hit = by_id["opentwins:twitter:cdp-errors"]
        self.assertEqual(hit["severity"], "warn")
        self.assertIn("32", hit["detail"])

    def test_writes_failing_from_task_ledger(self):
        fx = self._fx()
        failed = []
        for i in range(12):
            notes = "typedLen:0 paste miss" if i < 7 else "other fail"
            failed.append(_task(f"f{i}", f"reply-{i}", "failed", notes=notes))
        fx.write_schedule({
            "metadata": {"for_date": "2026-09-04"},
            "tasks": failed,
        })
        proc, _out, data = fx.run_probe()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        ids = [f["id"] for f in data["findings"]]
        self.assertIn("opentwins:twitter:writes-failing", ids)
        self.assertNotIn("opentwins:twitter:paste-failures", ids)
        by_id = {f["id"]: f for f in data["findings"]}
        self.assertEqual(by_id["opentwins:twitter:writes-failing"]["severity"], "alert")

        mixed = [_task(f"d{i}", "reply", "done") for i in range(7)]
        mixed += [_task("f1", "reply", "failed", notes="typedLen:0")]
        mixed += [_task("f2", "reply", "failed", notes="nope")]
        fx.write_schedule({
            "metadata": {"for_date": "2026-09-04"},
            "tasks": mixed,
        })
        proc2, _out2, data2 = fx.run_probe()
        self.assertEqual(proc2.returncode, 0, proc2.stdout + proc2.stderr)
        ids2 = [f["id"] for f in data2["findings"]]
        self.assertNotIn("opentwins:twitter:writes-failing", ids2)
        self.assertNotIn("opentwins:twitter:paste-failures", ids2)

    def test_stale_task_ledger_is_ignored(self):
        fx = self._fx()
        failed = [
            _task(f"f{i}", "reply", "failed", notes="typedLen:0")
            for i in range(12)
        ]
        fx.write_schedule({
            "metadata": {"for_date": "2026-09-01"},
            "tasks": failed,
        })
        proc, _out, data = fx.run_probe()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        ids = [f["id"] for f in data["findings"]]
        self.assertNotIn("opentwins:twitter:writes-failing", ids)
        self.assertNotIn("opentwins:twitter:paste-failures", ids)

    def test_postiz_disabled_error_and_missed(self):
        fx = self._fx()
        fx.store.integrations = [
            {"id": "1", "identifier": "x", "name": "maguyva", "disabled": True},
            {"id": "2", "identifier": "linkedin-page", "name": "maguyva", "disabled": False},
        ]
        two_h = (NOW_DT - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        one_h_later = (NOW_DT + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pub = (NOW_DT - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        def post(pid, state, when, ident):
            return {
                "id": pid,
                "state": state,
                "publishDate": when,
                "integration": {"id": ident, "providerIdentifier": ident, "name": ident},
                "releaseURL": "",
            }

        fx.store.posts = [
            post("err-a", "ERROR", pub, "linkedin-page"),
            post("err-b", "ERROR", pub, "linkedin-page"),
            post("q-miss", "QUEUE", two_h, "x"),
            post("q-future", "QUEUE", one_h_later, "x"),
            post("p1", "PUBLISHED", pub, "x"),
            post("p2", "PUBLISHED", pub, "facebook"),
            post("p3", "PUBLISHED", pub, "linkedin-page"),
        ]
        proc, _out, data = fx.run_probe()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        findings = data["findings"]
        ids = [f["id"] for f in findings]
        self.assertEqual(
            set(ids),
            {
                "postiz:x:disabled",
                "postiz:linkedin-page:post-error",
                "postiz:x:post-missed",
            },
        )
        by_id = {f["id"]: f for f in findings}
        self.assertEqual(by_id["postiz:x:disabled"]["severity"], "alert")
        err = by_id["postiz:linkedin-page:post-error"]
        self.assertEqual(err["severity"], "warn")
        self.assertIn("err-a", err["detail"])
        self.assertIn("err-b", err["detail"])
        missed = by_id["postiz:x:post-missed"]
        self.assertEqual(missed["severity"], "warn")
        self.assertIn("q-miss", missed["detail"])
        self.assertNotIn("q-future", missed["detail"])
        self.assertEqual(
            data["sections"]["postiz"]["posts"]["by_state"]["PUBLISHED"], 3
        )

    def test_postiz_unreachable_is_input_gap(self):
        port = _closed_port()
        fx = self._fx(postiz_base=f"http://127.0.0.1:{port}")
        proc, _out, data = fx.run_probe()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIsNotNone(data["sections"]["postiz"]["error"])
        by_id = {f["id"]: f for f in data["findings"]}
        hit = by_id["probe:postiz-read-failed"]
        self.assertEqual(hit["severity"], "warn")
        self.assertIsNone(data["sections"]["schedules"]["error"])
        self.assertIsNone(data["sections"]["opentwins"]["error"])

    def test_schedules_subprocess_failure_is_input_gap(self):
        fx = self._fx()
        fx.write_gc_python(fail_stderr="boom")
        proc, _out, data = fx.run_probe()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("boom", data["sections"]["schedules"]["error"])
        by_id = {f["id"]: f for f in data["findings"]}
        hit = by_id["probe:schedules-read-failed"]
        self.assertEqual(hit["severity"], "warn")
        self.assertIsNone(data["sections"]["opentwins"]["error"])
        self.assertIsNone(data["sections"]["postiz"]["error"])

    def test_no_secret_in_output(self):
        fx = self._fx()
        proc, out_path, _data = fx.run_probe()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        blob = Path(out_path).read_text() + proc.stdout + proc.stderr
        self.assertNotIn(SECRET, blob)

        fx.close()
        port = _closed_port()
        fx = self._fx(postiz_base=f"http://127.0.0.1:{port}")
        self.fx = fx
        proc2, out_path2, _data2 = fx.run_probe()
        self.assertEqual(proc2.returncode, 0, proc2.stdout + proc2.stderr)
        blob2 = ""
        if os.path.isfile(out_path2):
            blob2 += Path(out_path2).read_text()
        blob2 += proc2.stdout + proc2.stderr
        self.assertNotIn(SECRET, blob2)

    def test_findings_are_deterministically_ordered(self):
        schedules = {
            "automated": [
                _row(
                    "linkedin notifications",
                    "error",
                    error="x",
                    last_error_at="2026-09-04",
                ),
                _row("opentwins twitter heartbeat", "overdue"),
            ],
            "manual": [],
            "live": [],
        }
        fx = self._fx(schedules=schedules)
        fx.store.integrations = [
            {"id": "1", "identifier": "x", "name": "maguyva", "disabled": True},
        ]
        fx.clear_memory()
        fx.write_memory(
            "2026-09-04.md",
            _heartbeat("2026-09-04 16:00", "account has been locked"),
        )
        proc, _out, data = fx.run_probe()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        proc2, _out2, data2 = fx.run_probe()
        self.assertEqual(proc2.returncode, 0, proc2.stdout + proc2.stderr)
        self.assertEqual(data["findings"], data2["findings"])
        ids = [f["id"] for f in data["findings"]]
        expected = sorted(ids, key=lambda i: (_section_rank(i), i))
        self.assertEqual(ids, expected)


if __name__ == "__main__":
    unittest.main()
