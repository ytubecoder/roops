# WP3 — backend-aware install state, console service, install-time host checks

Peon spec. Self-contained: you have no conversation context. Parent design:
`openspec/changes/b-25-linux-port-2026-08-23/design.md` §6 (authority for WHY; this file is the
authority for WHAT). House rules: `CLAUDE.md`, `docs/INTERFACES.md` §0 (macOS-safe shell, bash
3.2, Python stdlib only). WP1 is merged: `bin/loopconf.py` has `load_env(root)`;
`bin/requirements.py` has `runtime_path(home)`. Use them.

## 0. Context in three sentences

The fleet is moving from macOS/launchd to Debian/systemd while keeping macOS. `loopctl install`
is already platform-dispatched (B-24: `_install_backend()` → `launchd` on darwin, else
`systemd`), but the **dashboard** and the **console** still decide "is this loop installed?" by
looking for a launchd plist — so on Linux every loop renders 休, staleness never fires, and the
pause/resume switch 409s. This package fixes that, adds a console-service installer (today the
console is a hand-written, gitignored plist that nothing regenerates), and adds the three
install-time host checks the migration found the hard way (linger, timezone, XDG).

## 1. Deliverables

### 1.1 Two predicates in `bin/loopctl` (one rule each)

```python
def unit_files_present(root, name) -> bool   # file presence only, subprocess-free
def scheduler_loaded(name) -> bool           # live check, shells out
```

- `unit_files_present`: launchd backend → `os.path.isfile(_plist_path(root, name))`; systemd
  backend → BOTH `_systemd_unit_paths(name)` files exist (`bin/loopctl:259-262`). Backend =
  `_install_backend()` (`:210-225`), which already honours `LOOPS_INSTALL_BACKEND`.
- `scheduler_loaded`: launchd → `_launchctl(["print", f"gui/{uid}/{label}"]).returncode == 0`;
  systemd → `_systemctl(["is-enabled", timer]).returncode == 0`. Make `_systemctl` (`:233-239`)
  default `XDG_RUNTIME_DIR` to `/run/user/<uid>` in the child's env when unset (a non-login ssh
  shell does not set it; without it `systemctl --user` says "Failed to connect to bus").
- Replace the body of `_is_installed` (`:411-427`) with `unit_files_present(...) and
  scheduler_loaded(...)` — behaviour unchanged, one implementation. Keep `_systemd_is_installed`
  only if still referenced; otherwise delete it.
- Export both names (they are module functions; tests import `loopctl` via
  `_load_module_from_path` as `tests/test_loopctl.py:150-178` does).

### 1.2 `dashboard/generate.py` — lockstep mirror

`_schedule_loaded(root, name)` (`dashboard/generate.py:1554-1558`) becomes a **mirror of
`unit_files_present`** that must run with no `bin/` on disk (the generator's lazy-seam doctrine —
see the owner mirror at `:79-90` and its drift test `tests/test_dashboard.py:2803-2830`): the
backend rule is the same three lines as `loopctl._install_backend` (`LOOPS_INSTALL_BACKEND` if
set — raise on an unknown value — else `"launchd" if sys.platform.startswith("darwin") else
"systemd"`); the systemd unit dir is `os.environ.get("LOOPS_SYSTEMD_UNIT_DIR") or
os.path.join(os.path.expanduser("~"), ".config", "systemd", "user")`; unit names
`loops-<name>.service` / `loops-<name>.timer`. Add a drift test (§2) that feeds both
implementations the same roots/envs and asserts equality — the same pattern as
`test_resolve_owner_mirror_never_drifts`.

Cosmetics: the 巡 tooltip `title="schedule loaded (launchd)"` (`:2021`) becomes
`f"schedule loaded ({backend})"`; the glossary/legend text that says "launchd plist" (grep
`plist` in the file, outside comments) says "unit files for this host's backend (launchd plist /
systemd timer)". Nothing else in the page changes; `tests/html_selfcontained.py` must still pass.

### 1.3 `bin/console.py`

- `_loaded` (`:112-120`) → delegates to the same rule as `loopctl.scheduler_loaded` (the console
  already imports `loopconf`; load `loopctl` the same way, or duplicate the 6-line rule with a
  drift test — prefer the import). Keep the `LOOPS_LAUNCHCTL` seam working (`:85-87`).
- `_state` (`:136-148`): `plist = unit_files_present(root, name)`; field names
  `plist_present`/`loaded` are **kept** (the page's hydration script reads them).
- `/rounds` 409 guard (`:382-383`): use `unit_files_present`; message unchanged.
- INTERFACES §13 table: redefine `plist_present` as "unit files present for this host's backend
  (launchd plist / systemd service+timer)".

### 1.4 `loopctl console install | uninstall | status`

New verb `console` with a positional action (`status` default). Register `"console"` in the
`dispatch` dict (`:1890-1910`, known-verb set). Build from `.env` (`loopconf.load_env(root)`):
`port = LOOPS_CONSOLE_PORT or 8929`; `allow_hosts = LOOPS_CONSOLE_ALLOW_HOSTS` split on commas,
stripped, empties dropped.

- **launchd**: render `launchd/com.roops.console.plist` — same label `com.roops.console`, same
  shape as the existing hand-written file (`KeepAlive`, `RunAtLoad`, `EnvironmentVariables`
  `HOME/LOOPS_ROOT/PATH=_runtime_path`, `ProgramArguments = [sys.executable, <root>/bin/loopctl,
  "serve", "--port", port, "--allow-host", h1, "--allow-host", h2 …]`, `StandardOutPath/
  StandardErrorPath` = `<root>/state/launchd-logs/console.{out,err}.log`), then
  `_launchctl(["bootout", f"gui/{uid}/com.roops.console"])` (ignore failure) and `bootstrap`.
  Reuse `_render_plist_xml`'s plistlib approach (`:747-753`), not string templating.
- **systemd**: write `<unit_dir>/loops-console.service`:
  ```
  [Unit]
  Description=roops console
  After=network-online.target
  [Service]
  Type=simple
  Restart=always
  RestartSec=5
  WorkingDirectory=<root>
  Environment=HOME=<home>
  Environment=PATH=<runtime_path>
  Environment=LOOPS_ROOT=<root>
  ExecStart=<sys.executable> <root>/bin/loopctl serve --port <port> --allow-host … 
  StandardOutput=append:<root>/state/launchd-logs/console.out.log
  StandardError=append:<root>/state/launchd-logs/console.err.log
  [Install]
  WantedBy=default.target
  ```
  then `daemon-reload`, `enable --now loops-console.service`. **Singleton**: if the unit file
  already exists and its `Environment=LOOPS_ROOT=` line differs from this root → refuse
  (`refusing: loops-console.service belongs to <other root>; uninstall it there first`), exit 1.
  Apply the §1.5 host checks before writing anything.
- **Self-verify (both backends)**: poll `GET http://127.0.0.1:<port>/api/state` with
  `urllib.request` (`Host` header `127.0.0.1:<port>` — the console's origin gate accepts that
  form) every 1 s for up to `LOOPCTL_CONSOLE_VERIFY_TIMEOUT_S` (default 30) until HTTP 200. On
  timeout: tear down (uninstall) and print `console install failed: no 200 from /api/state within
  Ns`, exit 1. Test seam: `LOOPS_CONSOLE_PROBE_URL` overrides the URL (tests point it at a tiny
  local `http.server` or at a fake that answers 200/refuses).
- `uninstall`: bootout + remove plist / `disable --now` + remove unit + `daemon-reload`. Exit 0
  even if nothing was installed (idempotent), printing what it did.
- `status`: prints `backend`, `unit: present|absent (<path>)`, `loaded: yes|no`, `http: 200|<err>`;
  `--json` gives the same as a dict. Exit 0 iff present+loaded+200.
- Record a `loop_events` row? **No** — the console is not a loop; print only.

### 1.5 Install-time host checks (systemd backend only) — `_host_checks(root) -> list[str]`

Returns human messages for every failed check; empty = ok. Called by `cmd_install` (after the
requirements refusal from WP1, before `_systemd_install`) and by `console install`; any message
→ print each as `refusing to install: <msg>`, exit 1, nothing written.

| check | rule | message |
|---|---|---|
| linger | `loginctl show-user <user> -p Linger` stdout contains `Linger=yes`; binary from `LOOPS_LOGINCTL` (default `loginctl`); a non-zero exit counts as failed | `linger is off for <user> — run: loginctl enable-linger <user> (timers die at logout without it)` |
| timezone | only when `.env` has `LOOPS_EXPECT_TZ`: host zone = zoneinfo name from `os.readlink("/etc/localtime")` (the part after `zoneinfo/`), else `timedatectl show -p Timezone --value` (`LOOPS_TIMEDATECTL` seam); compare as strings | `host timezone is <X>, fleet schedules are authored in <Y> (LOOPS_EXPECT_TZ) — fix the host or the .env, then reinstall` |
| XDG | `XDG_RUNTIME_DIR` set in the environment OR `/run/user/<uid>` is a directory (then the check passes and `_systemctl` uses it) | `XDG_RUNTIME_DIR is unset and /run/user/<uid> does not exist — systemctl --user cannot reach the bus; export XDG_RUNTIME_DIR=/run/user/<uid>` |

Test seam for `/etc/localtime`: `LOOPS_LOCALTIME_PATH`.

### 1.6 Wording

`cmd_install`'s two failure strings (`:1237-1247`) say `… after kickstart — check engine
process under launchd` / `… under launchd`; change to `… after the install trigger — check the
engine process under the scheduler ({backend})` / `… check engine auth/env under the scheduler
({backend})`. Keep everything else byte-identical (the launchd tests assert the prefix
`install failed:`; check what they assert before editing).

### 1.7 Docs (same commit)

- `docs/INTERFACES.md`: §10 install-state paragraph (`:1118-1127`) rewritten to the
  `unit_files_present` rule + mirror + drift test; §13 intro (console persistence: `loopctl
  console install` replaces the hand-written LaunchAgent; systemd unit named; still 127.0.0.1
  only), the `plist_present` redefinition, `scheduler_loaded` + XDG note; §8 verb table gains
  `console`; §8.1 gains the host checks as a refusal step (systemd) and the backend-neutral
  wording. Do not renumber sections.
- `README.md:7` badge → `platform-macOS%20launchd%20·%20Linux%20systemd`; `README.md:232`
  paragraph: "macOS and Linux" (keep the flock/timeout rationale).
- `CLAUDE.md` 🚨 block (line 5): prefix with "launchd only —", and add one sentence: on systemd
  units persist (`loginctl enable-linger`), and the console is installed with `loopctl console
  install` on either host. `CLAUDE.md:9`: replace "runs persistently as machine-local LaunchAgent
  `com.roops.console` (`launchd/com.roops.console.plist`, gitignored like loop plists; re-bootstrap
  after reboot alongside the fleet)" with "installed by `loopctl console install` (launchd label
  `com.roops.console` / systemd `loops-console.service`, machine-local)".
- `skills/loops/SKILL.md` lines 18, 78, 86: "launchd" → "the host scheduler (launchd on macOS,
  systemd on Linux)".
- `docs/LOOP_AUTHORING.md` §5 (lines ~333-346): add one paragraph — on systemd `Persistent=true`
  catches up a missed calendar firing at boot; the same "document it, don't fight it" rule.

## 2. Mandated tests

`tests/test_loopctl.py` (extend; launchd-pinned fixture):
- `test_unit_files_present_and_scheduler_loaded_launchd` — plist absent → False/False; plist
  present + fake launchctl `print` ok → True/True; `print` fails → True/False.
- `test_console_install_launchd_writes_plist_and_verifies` — `.env` with
  `LOOPS_CONSOLE_ALLOW_HOSTS=a.example,a.example:443` and `LOOPS_CONSOLE_PORT=18929`; fake
  launchctl; `LOOPS_CONSOLE_PROBE_URL` → a local `http.server` thread answering 200: plist exists
  with label `com.roops.console`, `ProgramArguments` contains `serve --port 18929 --allow-host
  a.example --allow-host a.example:443` (parse with `plistlib`), launchctl log shows `bootout`
  then `bootstrap`; exit 0. With the probe URL answering 500: exit 1, stderr `console install
  failed`, plist removed, log shows a final `bootout`.
- `test_console_uninstall_and_status` — after install: `status` exit 0 and JSON has
  `unit.present true`; after `uninstall`: plist gone, `status` exit 1, a second `uninstall` exits 0.
- `test_install_failure_strings_are_backend_neutral` — the existing install-poll-timeout test
  (find it: it asserts `install failed:`) additionally asserts the string contains
  `under the scheduler (launchd)` and NOT `under launchd`.
- `test_console_is_a_known_verb` — `loopctl --actor console` → "ambiguous invocation".

`tests/test_loopctl_systemd.py` (extend; `LOOPS_INSTALL_BACKEND=systemd`, fake systemctl,
`LOOPS_SYSTEMD_UNIT_DIR` under the temp root):
- `test_unit_files_present_requires_both_units` — service only → False; both → True.
- `test_scheduler_loaded_uses_is_enabled_with_xdg_default` — fake systemctl records its env;
  with `XDG_RUNTIME_DIR` unset in the parent, the child saw `XDG_RUNTIME_DIR=/run/user/<uid>`;
  `is-enabled` exit 0 → True, exit 1 → False.
- `test_host_checks_linger_tz_xdg` — fake `LOOPS_LOGINCTL` printing `Linger=no` → message names
  `enable-linger`; `Linger=yes` → none. `LOOPS_EXPECT_TZ=Asia/Manila` with `LOOPS_LOCALTIME_PATH`
  → a symlink to `…/zoneinfo/Etc/UTC` → message contains both names; symlink to
  `…/zoneinfo/Asia/Manila` → none; no `LOOPS_EXPECT_TZ` → none. `XDG_RUNTIME_DIR` unset and a
  fake `/run/user/<uid>` path (seam: monkeypatch the function's `run_user_dir` argument or an
  env `LOOPS_RUN_USER_DIR`) missing → message; present → none.
- `test_install_refuses_on_failed_host_check_before_writing_units` — `Linger=no`: exit 1,
  stderr `refusing to install`, no unit files written, no systemctl call.
- `test_console_install_systemd_unit_singleton_and_verify` — unit written with the §1.4 lines
  (assert `Restart=always`, `WantedBy=default.target`, the `ExecStart` args, both `append:` logs);
  systemctl log shows `daemon-reload`, `enable --now loops-console.service`; a second install
  from a DIFFERENT root refuses with `belongs to`; uninstall removes the unit and calls
  `disable --now` + `daemon-reload`.
- `test_install_failure_strings_say_systemd` — poll-timeout path under systemd contains
  `under the scheduler (systemd)`.

`tests/test_dashboard.py` (extend):
- Every existing fixture that writes a plist (`install()` helper at `:222-231` and any direct
  writer) now runs with `LOOPS_INSTALL_BACKEND=launchd` in the environment (use
  `mock.patch.dict(os.environ, …)` in `setUp` of the classes that use it) — the file must pass on
  a Linux host unchanged otherwise.
- `test_schedule_loaded_systemd_both_units` — `LOOPS_INSTALL_BACKEND=systemd`,
  `LOOPS_SYSTEMD_UNIT_DIR` temp: service only → not installed (休); both → installed; tooltip
  contains `schedule loaded (systemd)`.
- `test_unit_files_present_mirror_never_drifts` — load `bin/loopctl` and `dashboard/generate.py`;
  for each backend × {no files, plist only, service only, both units, plist+both} assert
  `generate._schedule_loaded(root, name) == loopctl.unit_files_present(root, name)`; and
  `LOOPS_INSTALL_BACKEND=bogus` raises in both.

`tests/test_console.py` (extend):
- New class `TestConsoleApiSystemd(ConsoleTestCase)` whose env sets `LOOPS_INSTALL_BACKEND=
  systemd`, `LOOPS_SYSTEMCTL=<fake>`, `LOOPS_SYSTEMD_UNIT_DIR=<temp>`, with a `write_units(name)`
  helper: `/api/state` → `plist_present true` with both units, `loaded` follows the fake's
  `is-enabled` exit; `/rounds` → 200 path reached with units present (the fake loopctl/pause
  is whatever the launchd class uses), 409 without.
- `test_state_plist_present_false_on_systemd_when_only_plist_exists` — a stray plist under
  a systemd backend does NOT count.

## 3. Hard constraints

- **Allowlist**: `bin/loopctl`, `bin/console.py`, `dashboard/generate.py`, `docs/INTERFACES.md`,
  `README.md`, `CLAUDE.md`, `skills/loops/SKILL.md`, `docs/LOOP_AUTHORING.md`,
  `tests/test_loopctl.py`, `tests/test_loopctl_systemd.py`, `tests/test_dashboard.py`,
  `tests/test_console.py`, `PEON_REPORT.md`. Nothing else (not `run-loop.sh`, not `loops.d/`,
  not `pagekit/`, not `bin/probe*`).
- `dashboard/generate.py` must keep running with NO `bin/` on disk (lazy-seam doctrine) — never
  import `loopctl` there. Token blocks: do not touch (`tests/test_token_drift.py`).
- The console must keep binding `127.0.0.1` only. `/api/state` field names unchanged.
- No real `launchctl`/`systemctl`/`loginctl` in tests; every call goes through the seams.
- Verify: `bash tests/run-tests.sh` → 0, no network.

## 4. Definition of Done

- Every §2 test exists by name, asserts what is written, passes; full suite green.
- INTERFACES §8/§8.1/§10/§13 + README/CLAUDE/SKILL/LOOP_AUTHORING edits in the same commit.
- `PEON_REPORT.md`: files touched, verify output tail, deviations with reasons.
