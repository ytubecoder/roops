# WP2 — probe channel: `bin/probe-server`, `bin/probe`, `probes/`, `loopctl probe`

Peon spec. Self-contained: you have no conversation context. Parent design:
`openspec/changes/b-25-linux-port-2026-08-23/design.md` §5 (read it in full — it is the
authority for WHY; this file is the authority for WHAT). House rules: `CLAUDE.md` (repo root),
`docs/INTERFACES.md` §0 (macOS-safe shell, bash 3.2, Python stdlib only, no `flock`/`timeout`).
WP1 (`specs/host-requirements/spec.md`) is already merged: `bin/requirements.py` exposes
`runtime_path(home)` and `bin/loopconf.py` exposes `load_env(root)` — use both, do not duplicate.

## 0. Context in three sentences

The loop fleet is moving to a Linux guest; some loops need data that stays on the Mac (`llm`).
Instead of a daemon, the Mac runs `sshd` with ONE forced command (`bin/probe-server`) that can
execute only named, reviewed scripts in `probes/`; loops call `bin/probe <name>` and never know
whether the probe ran locally or over ssh. Security is the point of this package: the server
must never interpret a shell, and the tests in §2 are the proof.

## 1. Deliverables

### 1.1 `probes/` directory, header grammar, `probes/README.md`, `probes/ping`? — NO: built-ins

- Create `probes/README.md` stating, in these words: a probe is trusted unsandboxed code on the
  host it runs on, same class as `precheck.sh`; the narrowing is WHICH scripts exist, reviewed
  in git; `ticket-add` (a later package) is the only probe that writes. Document the header
  grammar and the `--check` contract below, and the two-checkout deploy rule (design §5.4 item 7).
- **Header grammar** (parsed by the server from the first 20 lines of the script):
  ```
  # probe: <name>                 (must equal the file name)
  # probe-timeout-s: <int>        (optional; default 120; values above 600 are clamped to 600)
  # probe-writes: <text>          (required; "none" or a statement)
  # probe-output: json|tar|text   (required)
  # probe-reads: <text>           (required; free text)
  ```
  A probe whose header is missing a required line, or whose `probe:` name mismatches the file
  name, is **refused** by the server (`refused: bad header: …`, exit 64) — a header is part of
  the review contract, not decoration.
- **`--check` contract**: every probe, when invoked with the single argument `--check`, verifies
  its own inputs exist (binary, db, directory…), prints one line, exits 0 (ok) or 1 (unmet), and
  touches nothing. This package ships only `probes/echo-test` (a test-only probe: header
  `probe-writes: none`, `probe-output: text`; prints its argv joined by `|`; `--check` prints
  `ok echo-test` and exits 0). Real probes come in WP4.
- Built-ins are NOT files: `ping`, `list`, `check` (§1.2). Those three names are **reserved**: a
  file by one of those names in `probes/` is refused by the server (`refused: reserved name`) and
  omitted from `list`. Header parsing reads only the LEADING `# probe-*:` lines: the block must
  start at line 2 (right after the shebang) and parsing stops at the first line that is not a
  `# probe-*:` line — so a `probe-timeout-s` written further down in an ordinary comment is
  ignored and cannot change the timeout.

### 1.2 `bin/probe-server` (Python, `#!/usr/bin/env python3`, executable)

The forced command on the data host. Behaviour, in order:

1. Resolve `ROOT` = the repo root containing this script (`os.path.dirname(os.path.dirname(
   os.path.realpath(__file__)))`), never from the client's env. Load `.env` via
   `loopconf.load_env(ROOT)`; on `EnvFileError` → `refused: .env: <msg>`, exit 64 (logged).
2. Read `SSH_ORIGINAL_COMMAND` from the environment. **Parsing, with no shell ever involved:**
   `parts = cmd.split(" ")` (single-space split; an empty string, two consecutive spaces, a
   leading/trailing space, a tab or newline anywhere → refused). `verb = parts[0]`, `args =
   parts[1:]`. `verb` must match `^[a-z][a-z0-9-]{1,40}$`; each arg must match
   `^[A-Za-z0-9_.:@/=+-]{1,8192}$`; `len(args) <= 8`. Otherwise `refused: <reason>` on stderr,
   exit 64, logged, **nothing executed**.
3. Built-ins: `ping` → stdout `ok probe-server 1 <hostname>` (the `1` is the protocol version),
   exit 0. `list` → one line per executable file in `ROOT/probes/` whose name matches the verb
   regex, sorted: `<name> <first 12 hex of sha256 of the file bytes>`. `check <name>` → exactly as
   running the probe `<name>` with argv `["--check"]` (same exec path, same timeout, same log).
   Built-ins take no other args (`ping x` → refused).
4. A probe: `path = ROOT/probes/<verb>`; must be a regular file (no symlink — `os.path.islink`
   → refused), executable, with a valid header (§1.1). Exec `[path, *args]` with `cwd=ROOT`,
   `stdin=/dev/null`, a **clean env** = `{HOME, PATH=requirements.runtime_path(home), LOOPS_ROOT=
   ROOT, LANG=C.UTF-8}` plus every key from `.env` — never `SSH_ORIGINAL_COMMAND`, never the
   inherited environment. stdout/stderr inherit (they are the ssh channel). Start it in its own
   process group (`start_new_session=True`); enforce the header timeout: on expiry `killpg(TERM)`,
   10 s grace, `killpg(KILL)`, exit 124 with `probe timed out after N s` on stderr. Otherwise exit
   with the probe's exit code.
5. **Log** one line per invocation (including refusals and built-ins) to
   `ROOT/state/probe-log/<YYYY-MM-DD>.log` (dir created 0700, file 0600, UTC date): `<ISO-8601 UTC>
   client=<SSH_CLIENT first field or "-"> verb=<verb> args=<args joined by space, or "-"> exit=<n>
   ms=<duration>`. **Exception:** when `verb == "ticket-add"` log `args=<redacted>` (the payload
   carries a ticket body). On every start, delete log files older than 30 days (by the date in
   the file name).
6. `bin/probe-server --authorize <pubkey-file> [--write] [--replace]` (a CLI mode, not reachable
   through ssh because a forced command gets no argv — the server must refuse to act as the
   forced command when `sys.argv[1:]` is non-empty AND `SSH_ORIGINAL_COMMAND` is set): reads one
   `ssh-ed25519 …` line, prints
   `restrict,command="<abs path of this script>" <key> loops-probe`. `--write` appends it to
   `~/.ssh/authorized_keys` (created 0600 if missing; refused if the file mode is not 0600 or the
   dir is not 0700 — say which), refusing if the same key (by its base64 body) is already present
   unless `--replace`, which rewrites that key's line in place. Prints what it did.
7. Version constant `PROBE_SERVER_VERSION = 1`.

### 1.3 `bin/probe` (Python, executable)

```
bin/probe <name> [arg …] [--out FILE]
bin/probe --check <name>
bin/probe --list
bin/probe --ping
```

- Resolve `ROOT` like the server; load `.env` via `loopconf.load_env` (malformed → stderr +
  exit 64). `LOOPS_PROBE_HOST` unset/empty → **local mode**; set → **remote mode**.
- **Local mode**: built-ins are answered locally with the same output format as the server
  (`ping` → `ok probe local <hostname>`; `list` → same hash lines from `ROOT/probes/`); a probe
  is exec'd exactly as the server would (same header check, clean env, timeout, stdin
  `/dev/null`) — implement this once in a shared module `bin/probe_core.py` used by both scripts
  (header parsing, clean env, timed exec, list/hash), so the two cannot drift.
- **Remote mode**: `argv = [ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o",
  "IdentitiesOnly=yes", "-i", key, "--", host, name, *args]` where `ssh = os.environ.get(
  "LOOPS_SSH", "ssh")` (test seam), `key = .env LOOPS_PROBE_KEY or ~/.ssh/loops-probe` (expanded),
  `host = LOOPS_PROBE_HOST`. The `--` sits **before** the host. Args are separate argv elements;
  the client validates them with the server's regexes first and refuses locally (exit 64) so a
  bad arg never travels. ssh exit 255 → stderr `probe transport failed: <ssh stderr>`, exit
  **75**. Any other exit code passes through.
- `--out FILE`: stdout goes to `FILE` created 0600 (via a temp file in the same directory
  renamed into place on success; on failure the temp file is removed and `FILE` untouched).
- `--check <name>`: local → run `probes/<name> --check` (built-in `check`); remote → `ping` must
  answer `ok …`, `list` must contain `<name> <hash>` where `<hash>` equals the hash of the
  client's own `ROOT/probes/<name>` (else exit 3, stderr `probe drift: <name> server=<h1>
  client=<h2>` or `probe not offered by server: <name>`), then `check <name>` must exit 0. One
  `ping`+`list` round-trip per process (cache in memory; `LOOPS_PROBE_CACHE_TTL_S` not needed).
  Exit 0 ok / 3 unmet / 75 transport.
- `--list`: prints the server's (or local) list.

### 1.4 `bin/loopctl probe status | keygen`

Add `probe` as a verb with a positional sub-action (`status` default, `keygen`). Register
`"probe"` in the `dispatch` dict in `main()` (known-verb set) and build its subparser with
`common_sub` like the others (`bin/loopctl:1774-1846`).

- `status`: prints mode (local/remote), host, key path + present/missing, `ping` result (or
  `transport failed`), and a table `name | client-hash | server-hash | status` (`ok` /
  `drift` / `not on server` / `not local`). Exit 0 if everything `ok`, 1 otherwise. Never writes.
- `keygen`: `ssh-keygen -t ed25519 -N "" -f <key> -C "loops-probe <hostname>"` (refuse if the
  key exists), then prints: the public key; a `~/.ssh/config` stanza
  ```
  Host llm-probe
      HostName <fill in: IP or name of the data host>
      User <fill in: data-host user>
      IdentityFile <key>
      IdentitiesOnly yes
      BatchMode yes
  ```
  the `ssh-keyscan -t ed25519 <HostName> >> ~/.ssh/known_hosts` line; the `.env` line
  `LOOPS_PROBE_HOST=llm-probe`; and the command to run on the data host:
  `bin/probe-server --authorize <pubkey> --write`. `LOOPS_SSH_KEYGEN` is the test seam.

### 1.5 `docs/INTERFACES.md` — new §14 "Probe channel" (same commit)

Mechanical contract only: the authorized_keys line; the wire grammar (verb/arg regexes, max 8,
space split, refusals → exit 64); built-ins and their exact output; the header grammar; the
clean env; the timeout (default 120, cap 600, exit 124); the log line format + `ticket-add`
redaction + 30-day self-prune; client flags and exits (0 / probe's / 3 / 64 / 75 / 124
passthrough); local vs remote selection by `LOOPS_PROBE_HOST`; `--check` semantics and the
hash-drift rule; the two-checkout deploy rule. Add `probes/`, `bin/probe`, `bin/probe-server`,
`bin/probe_core.py`, `state/probe-log/` to §1. Add `probe` to the §8 verb table.

## 2. Mandated tests — `tests/test_probe.py` (new) + `tests/test_loopctl.py` (extend)

Build a temp root with `bin/` copied from the repo (`probe`, `probe-server`, `probe_core.py`,
`loopconf.py`, `requirements.py`), a `probes/` dir, and a canary: `probes/canary-touch` that
creates `<root>/CANARY` when executed — used to prove refusals execute nothing. Drive the server
by running it as a subprocess with `SSH_ORIGINAL_COMMAND` in the env.

Server:
- `test_server_refuses_shell_metacharacters_and_executes_nothing` — for each of
  `"echo-test; rm -rf /"`, `"echo-test $(id)"`, "echo-test `id`", `"echo-test 'a'"`, `'echo-test "a"'`,
  `"echo-test a\nb"`, `"../probes/echo-test"`, `"canary-touch/../canary-touch"`, `""`,
  `"nope-unknown"`, `"echo-test " + " ".join(["a"]*9)`, `"echo-test " + "a"*8193`,
  `"echo-test  a"` (double space), `" echo-test"`: exit 64, stderr starts with `refused:`,
  `CANARY` absent, and one log line with `exit=64`.
- `test_server_runs_probe_with_clean_env_and_args` — `SSH_ORIGINAL_COMMAND="echo-test a b=c"`
  with a polluted parent env (`SSH_ORIGINAL_COMMAND`, `SECRET=1`, `PATH=/nope`); the probe
  (replace echo-test's body in the fixture with an env dump) sees `LOOPS_ROOT=<root>`, `HOME`,
  `PATH` equal to `requirements.runtime_path(home)`, a `.env` key, NOT `SECRET`, NOT
  `SSH_ORIGINAL_COMMAND`; argv is `["a","b=c"]`; stdin is at EOF (probe reads stdin and asserts
  empty).
- `test_server_builtins` — `ping` → `ok probe-server 1 <hostname>`; `list` → `echo-test <12hex>`
  and the hex equals `sha256(file)[:12]`; `check echo-test` exits 0 with `ok echo-test`;
  `ping x` refused.
- `test_server_refuses_symlink_and_bad_header` — a symlinked probe → refused; a probe with no
  `probe-output` line → refused; a probe whose `probe:` name differs from the file name → refused;
  a file named `probes/list` (valid header) → `refused: reserved name` and absent from `list`;
  a `# probe-timeout-s: 1` line placed AFTER a non-header comment line is ignored (default 120).
- `test_server_timeout_kills_process_group` — a probe with `probe-timeout-s: 1` that spawns a
  child, both trapping TERM (`trap '' TERM`) and sleeping 30: server exits 124 within ~12 s, and
  neither pid is alive afterwards. Also: `probe-timeout-s: 9999` is clamped (assert via a probe
  that prints nothing and a header of 9999 — check the server's parsed value through a
  `--dump-header <path>` CLI flag you add for tests, or by unit-testing `probe_core.parse_header`
  directly — the latter is fine).
- `test_server_log_line_and_ticket_add_redaction` — after a run the day file has one line
  matching the documented format; with `verb=ticket-add` (a fixture probe named `ticket-add`
  that just exits 0) the line has `args=<redacted>`; log dir 0700, file 0600.
- `test_server_prunes_old_logs` — a 31-day-old file is removed on start; a 29-day-old one stays.
- `test_server_malformed_env_refuses` — `.env` with a bad line → exit 64, `refused: .env:`.
- `test_authorize_line_write_duplicate_replace` — `--authorize` prints the exact
  `restrict,command="<abs>" ssh-ed25519 … loops-probe` line; `--write` appends (HOME monkeypatched
  to a temp dir with a 0700 `.ssh`); a second `--write` of the same key is refused; `--replace`
  rewrites the line in place (file has one line for that key); wrong `.ssh` mode → refused.
- `test_authorize_not_reachable_as_forced_command` — server invoked with argv `--authorize x`
  AND `SSH_ORIGINAL_COMMAND=ping` → refused, exit 64.

Client:
- `test_client_local_mode_runs_probe_and_builtins` — no `LOOPS_PROBE_HOST`: `bin/probe echo-test
  a b` prints `a|b`; `--ping` → `ok probe local …`; `--list` matches the server's format;
  `--check echo-test` exit 0.
- `test_client_remote_mode_argv_and_dashdash_before_host` — `LOOPS_PROBE_HOST=llm-probe`,
  `LOOPS_SSH=<fake ssh script that dumps its argv as JSON and exits per FAKE_SSH_EXIT>`: argv is
  exactly `[fake, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "IdentitiesOnly=yes",
  "-i", <key>, "--", "llm-probe", "echo-test", "a", "b=c"]`; `FAKE_SSH_EXIT=255` → exit 75 and
  stderr contains `probe transport failed`; `FAKE_SSH_EXIT=7` → exit 7.
- `test_client_refuses_bad_args_locally` — an arg with a space or quote → exit 64, fake ssh never
  invoked.
- `test_client_out_file_atomic_0600` — `--out f` writes 0600; on probe failure `f` is absent and
  no temp file remains.
- `test_client_check_remote_hash_drift` — fake ssh answers `ping`/`list`/`check` from canned
  responses: matching hash → 0; differing hash → exit 3 with `probe drift:`; name absent → exit 3
  `not offered`; `check` non-zero → 3; ssh 255 → 75. Assert only ONE `list` round-trip when
  `--check` is called for two names in one process (unit-test the cache in `probe_core`).

loopctl:
- `test_probe_status_and_keygen` — `loopctl probe status` in local mode lists `echo-test ok`,
  exit 0; with a drifted fake server → exit 1 and `drift` in the table. `loopctl probe keygen`
  with `LOOPS_SSH_KEYGEN=<fake that writes two files>` prints the stanza, the keyscan line, and
  the authorize command; a second keygen refuses. `loopctl --actor probe` → "ambiguous
  invocation" (known-verb set).

## 3. Hard constraints

- **Allowlist**: `bin/probe`, `bin/probe-server`, `bin/probe_core.py`, `bin/loopctl`,
  `probes/README.md`, `probes/echo-test`, `tests/test_probe.py`, `tests/test_loopctl.py`,
  `docs/INTERFACES.md`, `PEON_REPORT.md`. Nothing else — in particular not `bin/run-loop.sh`,
  `bin/requirements.py`, `loops.d/`, `dashboard/`, `bin/console.py`.
- Python stdlib only; no `shell=True` anywhere; no `os.system`; no string-built commands.
- Verify: `bash tests/run-tests.sh` exits 0, no network (every ssh in tests is the fake).
- Never add a key to the real `~/.ssh/authorized_keys` — tests monkeypatch `HOME`.
- Never run `ssh` for real in tests.

## 4. Definition of Done

- Every §2 test exists by name, asserts what is written, passes; `bash tests/run-tests.sh` → 0.
- INTERFACES §14 + §1 + §8 amendments in the same commit.
- `PEON_REPORT.md`: files touched, verify output tail, deviations (with why).
