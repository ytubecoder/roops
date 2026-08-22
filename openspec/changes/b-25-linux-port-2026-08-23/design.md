# B-25 — Linux port: app/loop separation, host requirements, probes, firstparty cutover

Design spec. Written 2026-08-23 (fable); revised the same day after a council review
(codex, grok, antigravity — round 1 consolidated in `review.md`). Authority for mechanics stays
`docs/INTERFACES.md`; every amendment this change needs is listed in §11 and ships in the same
commit as its code.

## 0. Where we are, and the decisions this spec makes

The pve1 migration already proved the harness runs on Debian 13 (`WARMSTART_SYSTEMD.md`):
B-24 gave `loopctl` a systemd install backend, the suite is green on both hosts, and a timer
survived `qm reboot 101`. The cutover stalled on two things: the fleet is not host-portable
loop by loop, and `state/loops.sqlite` has no host dimension. This session's audit (three
parallel sweeps + live probes of the guest, all 2026-08-23) added facts the brief did not have:

| # | Finding | Consequence |
|---|---|---|
| F1 | **Nothing reads `.env`.** Units carry only `HOME/PATH/LOOPS_ROOT`; `run-loop.sh` sources nothing. | Every scheduled ads run on the guest would silently use `GC_BASE=127.0.0.1:8787` and report "inputs missing". |
| F2 | `dashboard/generate.py:_schedule_loaded` and `bin/console.py` (`/api/state`, `/rounds`, `launchctl print`) decide "installed" by **plist presence** only. | On Linux the garden shows every loop 休, never flags stale, and the rounds switch 409s. |
| F3 | No installer for the console service; no linger or timezone check at install. | The two reboot/clock failures the migration found are unguarded. |
| F4 | `gc-actions` **writes** tickets (`render.sh` → `apply_tickets.py` → `tickets-cli.py add … --description "[loop:gc-actions \| <id>] …"`, the description being the idempotency key); `ads-x` has a **third** llm-local read (`~/.opentwins/…/memory`) and still peeks `ads.db` for the snapshot age; `kagami`'s PAT file does not exist (ambient `gh` auth; SPEC still documents the optional file) and its precheck crashes on Debian (`/bin/zsh -l` under `set -euo pipefail`, line 13); the flickki loops were **decommissioned on purpose** 2026-08-08; `ads-program` must share `state/runs/` with the four network ads loops. | The per-loop matrix in the brief under-counts. |
| F5 | `firstparty → llm` command-restricted ssh is already an accepted part of the trust model (`sysadmin DESIGN.md` §6); the probe script it would restrict to was never written. | The "agent on llm" the generalissimo asked about can be **sshd + a forced command**: zero resident RAM. |
| F6 | `sysadmin DESIGN.md` §7 already settled "the fleet moves as one unit: one console, one `loops.sqlite`"; a split fleet was considered and rejected. | We do not relitigate it. |
| F7 | The guest's own Caddy (runs as root, `caddy-ts.service`) mints tailnet nodes (`bind tailscale/<name>` + `tls { get_certificate tailscale }`); llm is `tag:dmz` and cannot reach `*.ts.net` services. `loops.<tailnet>.ts.net` is held by llm's userspace tailscaled for loops. | The console stays loopback-only; ingress is a tailnet vhost on the guest's Caddy. Freeing the name is an operator action and must precede the bind. |
| F8 | Verified from the guest 2026-08-23 (`curl -s -o /dev/null -w %{http_code} http://192.168.1.52:8787/api/ads/x-cache` → `200`; sshd :22 and :8787 on llm reachable; :8929 not). `curl --fail file:///dev/null` exits 0 on Debian's curl 8.14. `shasum`, `sha256sum`, `perl` present; `gh`, `npm`, `jq`, `rsync`, `zsh` absent. `Linger=yes` for `svc`; `/etc/localtime` → `Asia/Manila`. | Snapshot age is served; the ledger + opentwins reads remain for `ads-x`. The x-cache check becomes a runbook gate. |
| F9 | `ytubecoder/roops` is **not indexed in maguyva** (nine other repos are). | The harness audit ran locally; maguyva was used for `maguyva-marketing` only. |
| F10 | `ytubecoder/roops` is **PUBLIC** (`gh repo view` 2026-08-23; `git ls-remote https://github.com/ytubecoder/roops` works unauthenticated from the guest). The guest's current copy is a tar with no `.git`. | The guest can be a plain `git clone`; deploys become `git pull`. No deploy key needed. |

**Decisions (settled here, do not relitigate):**

- **D1 — One fleet, on firstparty.** One db, one console, one garden. Loops that need llm data reach it through a read-mostly probe channel; nothing stays behind as a second fleet. The flip has a hard precondition: **zero loops remain installed on llm** — so every loop installed on llm today, including other agents' untracked ones, is either moved or uninstalled before the flip (§8.2, §12).
- **D2 — The app is host-neutral; loops declare host needs.** The harness knows no specific host. A loop says what it needs (`requires=`); the harness says whether this host meets it. That is the app/loop separation the generalissimo asked for.
- **D3 — No loops-client daemon.** The only thing llm runs for the fleet is sshd with a forced command. A server/client model is explicitly rejected for now; the probe channel covers every current need at zero RAM.
- **D4 — macOS stays a first-class host.** launchd backend untouched; the suite passes both ways; `probe` runs probes locally when no remote host is configured, so a loop written against the probe API runs unchanged on either host.
- **D5 — Credentials do not move unattended.** The port makes a credentialed loop *install-refuse cleanly* on a host that lacks its credential; copying or minting the credential is the operator's call (end-of-run questions).
- **D6 — The flip is one runbook, executed last, after the operator answers.** Everything up to it — code, guest staging, a full rehearsal on a copy of the real state — is done in this change.
- **D7 — Tailnet only for the console.** No LAN HTTP vhost: it would extend the B-22 "tailnet is the trust boundary" surface (every mutation route) to any LAN browser over plaintext. llm (tag:dmz) loses browser access to the garden; the generalissimo works from tailnet devices, so nothing is lost in practice.

## 1. Goals / non-goals

**Goals.** (a) The harness runs identically on macOS/launchd and Debian/systemd, including the garden, the console, and install-state detection. (b) A per-loop `requires=` declaration, enforced at `validate`/`install`/`run`, plus `loopctl requirements` to answer "can this fleet run here?". (c) A probe channel so loops can read (and in one bounded case write) llm-local data from the guest. (d) The loops that are not host-neutral today are retrofitted onto (b)+(c). (e) A mechanical cold state copy (`loopctl snapshot`/`restore`) and a cutover runbook. (f) The guest staged and rehearsed end-to-end against a copy of real state.

**Non-goals.** A fleet dead-man alarm (separate ticket; systemd `Persistent=true` + linger already removes the reboot failure). The dashboard's UTC-vs-local "next run" arithmetic (pre-existing; follow-up). Any change to `perm_*` semantics or engine adapters. Resurrecting the flickki loops. Moving `gc`, OpenTwins, or ticket-takeaway off llm. A generic "run any command on llm" channel — every probe is a named, reviewed script. Auth proofs beyond binary presence (`bin:gh` does not prove `gh auth`; the install self-verify run is the gate for that, as today).

## 2. Architecture — three layers, two hosts

```
 firstparty (fleet host, svc)                         llm (data host)
 ┌───────────────────────────────────────┐            ┌──────────────────────────┐
 │ APP  bin/ engines/ dashboard/ console │            │ sshd                     │
 │      state/loops.sqlite (THE db)      │  ssh -i    │  └ forced command:       │
 │      systemd user timers + console    │ ────────▶  │    bin/probe-server      │
 │ LOOPS loops.d/<name>/  requires=…     │  key       │      └ probes/<name>     │
 │       precheck.sh ─▶ bin/probe <name> │            │        (av, ads.db,      │
 │ HOST  .env  (GC_BASE, LOOPS_PROBE_*)  │            │         trees, tickets)  │
 └───────────────────────────────────────┘            │ HOST .env (AV_BIN, dirs) │
        Caddy (guest, root): loops.<tailnet>.ts.net ─▶ 127.0.0.1:8929            │
                                                      └──────────────────────────┘
```

- **App** — `bin/`, `engines/`, `dashboard/`, `pagekit/`. Host-neutral; the one platform branch is `_install_backend()` and everything else dispatches through it.
- **Loops** — `loops.d/<name>/`. Each declares `requires=`; each reads host-local data only through `$LOOPS_ROOT/bin/probe` or through env it declares. A loop never contains a hostname.
- **Host** — `$LOOPS_ROOT/.env` (machine-local, gitignored) plus the scheduler backend. The only place a hostname, an IP, or a probe alias lives. **Both hosts carry a checkout and a `.env`**, with different contents (§3).

## 3. Fleet env seam — `$LOOPS_ROOT/.env` (fixes F1)

`bin/loopconf.py` gains `load_env(root) -> dict` and a CLI `loopconf.py env --root R --json`. Grammar: the `loop.conf` **value** grammar (reuse `_parse_value`/`_split_line`/`_expand_home` — never `source`d; `#` comments; bare or double-quoted values; no expansion except a literal leading `$HOME`/`~`) with its **own key regex** `^[A-Z][A-Z0-9_]*$` (env names are upper-case; `loop.conf`'s `KEY_RE` is lower-case — `parse()` is not reused). Max 64 keys. A malformed line is an error naming file and line.

**Who loads it, and what sees it.**

- `bin/run-loop.sh`: loaded **after §4.1 step 3** (`start-run` — a run row exists) and before the §4 requirement check. Each key is exported **only if not already set** in the process environment (an explicit shell export or a unit `Environment=` wins, so `loopctl run` from a shell and the tests keep working). A malformed file → `runner_status=harness-error` with the loader's message in `error_detail`, no engine spawn. When spawning the engine adapter (§6.1) the runner **unsets every key named in `.env`** (`env -u …` for each key the file declares, whether or not the runner was the one that exported it — a key already set by the unit or the shell is stripped too): `.env` reaches the precheck, render, and `bin/probe`, and never the model. The strip is an `env -u …` **prefix on the adapter child only** — the runner process keeps the exported set, because the post-engine render step (`gc-actions`' `render.sh` → `ticket-add`) still needs `LOOPS_PROBE_*`. (A full `env -i` allowlist for the adapter is the stronger form; it is deferred because the engines' auth/env needs under launchd and systemd are not yet enumerated — follow-up, not this change.) Containment is the sandbox, but a host config file must not become a side channel into it.
- `bin/probe` (for `LOOPS_PROBE_*`), `loopctl requirements/install` (for `env:` items and `LOOPS_EXPECT_TZ`), `loopctl console install` (for `LOOPS_CONSOLE_*`), `bin/probe-server` (the data host's `.env`, §5.2). A malformed `.env` on the server refuses every probe with `refused: .env: <msg>`, exit 64.
- `.gitignore` gains `.env` in the same commit as the loader.

Reserved keys (all optional):

| key | meaning | fleet host (firstparty) | data host (llm) |
|---|---|---|---|
| `GC_BASE` | read by the ads prechecks; now reaches scheduled runs | `http://192.168.1.52:8787` (no `llm.home.arpa` record exists) | unset (default loopback) |
| `LOOPS_PROBE_HOST` | ssh alias for the probe server. Unset/empty = **local mode** | `llm-probe` | **must be unset** |
| `LOOPS_PROBE_KEY` | identity file (default `~/.ssh/loops-probe`) | default | — |
| `LOOPS_EXPECT_TZ` | `install` refuses when the host zone differs (§6.3) | `Asia/Manila` | optional |
| `LOOPS_CONSOLE_ALLOW_HOSTS` / `LOOPS_CONSOLE_PORT` | baked into the console unit **at `console install` time** — a change needs a reinstall (§6.2) | the tailnet FQDN (+`:443`) | same, until retired |
| `AV_BIN`, `DMP_OUTPUT_DIR`, `GC_ACTIONS_DIR`, `TAILNET_SETUP_DIR`, `TICKETS_CLI` | inputs the **probes** read (§5.1) | — | set as needed (defaults match today's precheck defaults) |

**`.env` is for non-secret host configuration.** It is not `credential_env` (still RESERVED, still hard-failed by `validate`). Secrets stay in per-loop credential files (`phoneapp`'s `cost-sync.env` pattern), mode 0600, named by a `file:` requirement.

## 4. Host requirements — `requires=` (implements D2)

New optional `loop.conf` key, same comma-list style as `tags`:

```
requires="bin:gh, bin:git, bin:curl, probe:av-scan, file:~/.config/phoneapp/cost-sync.env, env:GC_BASE, os:linux"
```

| kind | met when | notes |
|---|---|---|
| `os:<darwin\|linux>` | `sys.platform` matches | for loops whose *subject* is a platform |
| `bin:<name>` | found on the PATH the **unit** would get (`_runtime_path(home)`), not the caller's shell | `shutil.which(name, path=…)`; proves presence, not auth |
| `file:<path>` | exists and is readable; `$HOME`/`~` expanded | existence only — never opened, never logged beyond the path |
| `env:<KEY>` | non-empty after §3 load | |
| `probe:<name>` | **install/requirements (live):** `bin/probe --check <name>` exits 0 — local mode: `probes/<name> --check` passes; remote: `ping` answers, `list` contains the name, and `check <name>` (the server running `probes/<name> --check`) passes. One `ping`+`list` round-trip is cached per `loopctl` process. **run (config-only):** the channel is configured — local: `probes/<name>` is executable; remote: `LOOPS_PROBE_HOST` set and the key file exists. No network at run time: a live transport failure is the **precheck's** to report (exit 75, §5.3), so its "llm unreachable" wording stays reachable. | the only kind that may touch the network, and only at install/requirements |

Grammar: item `^(os|bin|file|env|probe):[^,\s]+$`, max 16 items, deduped. Unknown kind → parse error (typo safety, same rule as unknown keys). **Required-but-assumed doctrine applies:** absence means "portable", never an error.

Enforcement, one implementation — `bin/requirements.py` (importable, same pattern as `db.py`/`lock.py`/`loopconf.py`): `check(root, conf, *, live: bool) -> list[(item, ok, detail)]`, plus a CLI `requirements.py check --root R --loop NAME [--no-live] [--json]` so the bash runner never has to invoke `loopctl`. `loopctl requirements` wraps it:

- `loopctl validate` — live check; prints the table; unmet items are a **notice**, not a failure (a loop can be valid and not runnable here — that is the point).
- `loopctl install` — **refuses** (live check) before §8.1 step 2, message `refusing to install <name>: requirement unmet — bin:gh (not on unit PATH …)`. Same refusal for `resume` and `set-schedule` when they would arm a timer.
- `run-loop.sh` — after `start-run` and the `.env` load, before precheck: unmet (config-only check) ⇒ `runner_status=precheck-failed`, `loop_status=alert`, `error_detail="requirement unmet: <item> — <detail>"`, **no precheck, no engine spawn**. Reuses the existing status so the garden (B-05 error surfacing) shows it red with no dashboard change. The shell side calls `bin/requirements.py check --root "$ROOT" --loop "$NAME" --no-live --json`. **`--no-live` changes only the `probe:` kind** (host+key / local executable instead of ssh); `os:`/`bin:`/`file:`/`env:` are checked identically live or not, so a missing credential file or `GC_BASE` is still a recorded `requirement unmet` with no precheck.
- `loopctl requirements [<name>…] [--json] [--no-live]` — new verb, positionals like `validate`: fleet × requirement matrix for **this host**, exit 1 if any loop in scope is unmet. This is the cutover's "can it run here" gate. `loopctl probe status` drift (§5.5) is folded in: a `probe:` item is unmet when the server does not list it.

`loopctl new`/`import --apply` emit a commented `# requires=` line so authors see the key. `docs/LOOP_AUTHORING.md` intake gains q13: *what does this loop need from the host?*

## 5. Probe channel — 使い (tsukai) (implements D3, D5; fixes F5)

One mechanism, three parts, all in this repo. The theme name is documentation only; mechanical names are `probe`, `probe-server`, `probes/`.

### 5.1 Probes — `probes/<name>`

A probe is an executable script in `$LOOPS_ROOT/probes/` (git-tracked, reviewed like `precheck.sh`, **trusted unsandboxed code** on the host it runs on). Contract: argv in, stdout out, stderr for diagnostics, exit 0/non-0; **no probe reads stdin** (the server closes it). Every probe implements `--check`: verify its own inputs exist (binary, db, tree), print one line, exit 0/1, touch nothing. Each probe begins with a header the server parses:

```
#!/usr/bin/env bash
# probe: av-scan
# probe-timeout-s: 280          (default 120, cap 600)
# probe-writes: none            (or: a one-line statement of what it writes)
# probe-output: json            (json | tar)
# probe-reads: $AV_BIN, /bin/zsh -l, /Applications/Automic Vault.app/Contents/Info.plist
```

`probes/README.md` is the index. A probe name matches `^[a-z][a-z0-9-]{1,40}$`. `tar` probes emit **relative paths only** (`tar -C <root> -cf - <names>`), never symlinks or hardlinks, and state a byte cap; clients must write them to a file (§5.3 `--out`) because §4.1 caps precheck stdout at 64 KiB, and extract with Python `tarfile` using `filter="data"` (refuses absolute paths, `..`, links, and device nodes) into `$OUT_DIR/remote/<probe>/` — a temp dir renamed into place on success.

Probes this change ships — seven scripts; `ping`/`list`/`check` are built-ins, not files (read-only unless stated). JSON probes have no byte cap but are always written via `--out` under the run dir, never through precheck stdout:

| probe | runs on the data host, does | output |
|---|---|---|
| `av-scan` | resolves the login-shell PATH (`/bin/zsh -l`, which exists **there**), **exports it**, then `"$AV_BIN" scan --json`; reads the bundle version from `Info.plist` | the raw `av scan --json` document with three keys ADDED at its top level — `probe_host`, `probe_av_version`, `probe_login_path` — so `findings` stays exactly where today's digest and renderer read it; timeout 280 s |
| `opentwins-lock-signal` | scans the last 3 `~/.opentwins/workspaces/agent-twitter/memory/*.md` for the lock markers `ads-x` looks for today | JSON `{files:[…], hits:[{file,line}]}` — marker lines only, never the files |
| `ads-x-ledger` | the precheck's decode logic moved verbatim: opens `~/.growth-console/ads.db` `mode=ro`, decodes **every** batch (the precheck needs latest, previous, and the pre-month batch for month-to-date and $/day) | JSON `{batches:[{batch_id, imported_at, life, window, rows, bad, at_cap, headroom}]}` oldest-first — exactly `_decode_batch`'s per-batch aggregates, so the precheck's digest math (latest / previous serving rate / pre-month attribution) moves over unchanged |
| `dmp-actions` | tars `<run>/actions/register.yaml` + `<run>/ACTIONS.md` for the **newest 30** run dirs under `$DMP_OUTPUT_DIR` (by mtime); cap 32 MiB | tar |
| `gc-actions-files` | tars `action-sources.yaml`, `action-ticket-map.yaml`, `PRODUCT_BACKLOG.md` from `$GC_ACTIONS_DIR`; cap 8 MiB | tar |
| `sysadmin-tailnet` | tars `docs/policy-live.hujson` + `site/zones-meta.json` from `$TAILNET_SETUP_DIR`; cap 4 MiB | tar |
| `ticket-add` **(write)** | takes ONE arg: base64url of a JSON object `{project, title, section, priority, description}` (decoded size ≤ 6 KiB — base64url of 6 KiB is ~8.2 K chars, inside the server's 8192-char arg cap). Validates: `project` ∈ `probes/ticket-add.allow` (ships with `maguyva-actions`), `section == "ideas"`, `priority` ∈ `high\|medium\|low`, `title` 1–200 chars, `description` ≤ 4 KiB and starts with `[loop:` (the idempotency prefix `apply_tickets.py` relies on). Then `python3 "$TICKETS_CLI" add <project> <title> --section ideas --priority <p> --description <d>` as argv, no shell. | the CLI's stdout. The server log records project + title only, never the description |

`ticket-add` is the **only** writing probe. Justification: `gc-actions` already performs this exact write today from trusted `render.sh` code at the engine floor; the probe narrows it (project allowlist, ideas-only, caps, prefix check) rather than widening it.

The names `ping`, `list`, `check` are **reserved**: a file by one of those names in `probes/` is refused by the server and flagged by `loopctl probe status`. The header is parsed from the leading `# probe-*:` lines only (parsing stops at the first line that is not one), so a later comment cannot change the timeout. Built-ins (server-side, not files in `probes/`): `ping` → `ok probe-server <version> <hostname>`; `list` → one line per probe, `<name> <sha256-of-the-script-file, first 12 hex>`; `check <name>` → runs `probes/<name> --check`. They are logged like probes. The hash is what makes same-name drift visible: the client compares it with the hash of its own `probes/<name>` and a mismatch is an unmet requirement (`probe drift: av-scan server=… client=…`), so a changed output schema cannot hide behind an unchanged name.

### 5.2 Server — `bin/probe-server` (the forced command)

Installed on the data host as the `command=` of one `authorized_keys` line:

```
restrict,command="/abs/path/to/loops/bin/probe-server" ssh-ed25519 AAAA… loops-probe firstparty->llm
```

`restrict` = no pty, no port/agent/X11 forwarding, no user-rc. The server ignores everything about the client except `$SSH_ORIGINAL_COMMAND`, parsed as `<verb> [arg …]` with **no shell**: split on single spaces; verb = built-in or a probe name by the §5.1 regex; each arg `^[A-Za-z0-9_.:@/=+-]{1,8192}$` (base64url is inside this set; ssh joins the client's argv with spaces, which is why no arg may contain whitespace or quotes), max 8 args. Empty command, unknown verb, a name with `/`, anything else → `refused: <reason>` on stderr, exit **64**, logged, nothing executed.

The probe is exec'd as `probes/<name> arg…` with a **clean env**: `HOME`, `PATH=_runtime_path(home)`, `LOOPS_ROOT`, plus the server root's `.env` (§3) — never `SSH_ORIGINAL_COMMAND`, never the client's env. stdin is `/dev/null`. stdout streams to the client; stderr too. Process-group timeout from the probe header (default 120 s, cap 600 s): TERM, 10 s grace, KILL — the runner's own pattern. Every invocation appends one line to `$LOOPS_ROOT/state/probe-log/<YYYY-MM-DD>.log`: timestamp, `$SSH_CLIENT` addr, verb, args (for `ticket-add`: project + title only), exit, duration. Dir 0700, files 0600; **the server prunes its own log** (files older than 30 days) on every start — the fleet's retention step runs on the other host.

`bin/probe-server --authorize <pubkey-file> [--write] [--replace]` prints the exact line (absolute path resolved from the server's own location); `--write` appends to `~/.ssh/authorized_keys` (creates 0600; refuses a duplicate key unless `--replace`, which rewrites that key's line in place — the path moves when a checkout moves). Run **on the data host**.

### 5.3 Client — `bin/probe`

```
bin/probe <name> [arg …] [--out FILE]   # run; stdout passthrough (or to FILE, 0600); exit = probe's exit
bin/probe --check <name>                # §4 live check; runs ONLY the probe's --check self-test (via the server's `check` built-in when remote), never its body
bin/probe --list
```

Mode from `.env` (§3): `LOOPS_PROBE_HOST` unset → **local mode**, exec `$LOOPS_ROOT/probes/<name>` with the same clean env the server builds (built-ins answered locally). Set → `ssh -o BatchMode=yes -o ConnectTimeout=10 -o IdentitiesOnly=yes -i "$LOOPS_PROBE_KEY" -- "$LOOPS_PROBE_HOST" <name> <args>` — the `--` **before** the host (after it, ssh would send `-- name` as the remote command). The client never builds a shell string; args are separate argv elements. `LOOPS_SSH` is the test seam for the ssh binary. Transport failure (ssh exit 255) is reported distinctly: the client exits **75** (EX_TEMPFAIL) with `probe transport failed: …` on stderr, so a precheck can distinguish "input gap" from "llm is down". `--out` exists because tar probes must not reach precheck stdout (§4.1 64 KiB cap).

**Local mode is the macOS compatibility story (D4):** on llm with no `LOOPS_PROBE_HOST`, `bin/probe av-scan` runs `probes/av-scan` in-process, so `kagi-ban` keeps working on the mac through the same code path the guest uses remotely.

### 5.4 Security properties (stated, tested)

1. The key is usable for nothing but the forced command: `restrict` + `command=` — a test pins the generated line.
2. The server never interprets a shell: a test feeds `"av-scan; rm -rf /"`, `"$(id)"`, backticks, quotes, newlines, `../probes/x`, an empty command, an unknown name, 9 args, an 8193-char arg — every case is `refused`, exit 64, logged, nothing executed (fake `probes/` dir with a canary; a stdin canary proves nothing is read).
3. A probe runs with the data host user's full power — same class as `precheck.sh`. The narrowing is **which** scripts exist, reviewed in git, plus the allowlists inside `ticket-add`. The README says so in those words.
4. Timeouts are server-owned (process group; a test uses a child that ignores TERM), so a hung probe cannot hold the fleet's precheck past its own `timeout_s`.
5. The probe log is the audit trail; the loop's `precheck.out` keeps the client side.
6. Threat accepted (recorded, per `sysadmin DESIGN.md` §6): a compromised `svc@firstparty` can invoke every probe on demand — read av exposure findings, the DMP/gc-actions files, the tailnet policy, the ads ledger, pull those tar streams at will, and add ideas-section tickets to one board up to whatever `gc-actions` could send.
7. **Two checkouts, one contract.** After cutover the probes execute from **llm's** checkout and the loops from **firstparty's**. The probe key cannot pull. Deploy rule (runbook + README): a change to `probes/`, `bin/probe`, or `bin/probe-server` is pushed from llm (the dev checkout, always newest) and pulled on firstparty before any loop that uses it is installed; `loopctl requirements` treats a name the server does not list, or lists with a different content hash, as unmet, which makes drift a hard gate rather than a warning.

### 5.5 `loopctl probe` verbs

`loopctl probe status` — mode, host, key presence, `ping` result, server `list` vs local `probes/` (drift named). `loopctl probe keygen` — `ssh-keygen -t ed25519 -N "" -f $LOOPS_PROBE_KEY -C "loops-probe $(hostname)"`; prints the pubkey, the `~/.ssh/config` stanza to add (`Host llm-probe` / `HostName <ip or name>` / `User <data-host user>` / `IdentityFile` / `IdentitiesOnly yes` / `BatchMode yes`), the `ssh-keyscan` line for `known_hosts` (BatchMode fails closed on an unknown host key), and the `probe-server --authorize` command to run on the data host. Nothing here writes to a remote host.

## 6. Backend-aware app surfaces (fixes F2, F3)

### 6.1 Install-state detection — two predicates, one rule each

Today INTERFACES §10 says "plist present → installed" and §13 shells out to `launchctl print`. Amended:

- **`unit_files_present(root, name)`** — file presence, subprocess-free, hermetic: launchd = `launchd/com.loops.<name>.plist`; systemd = **both** `loops-<name>.service` and `loops-<name>.timer` under `LOOPS_SYSTEMD_UNIT_DIR` (default `~/.config/systemd/user`). Backend = `LOOPS_INSTALL_BACKEND` if set, else the `sys.platform` rule. Used by: the garden's 巡/休 and staleness gate, `/api/state.plist_present`, `/rounds`' 409, and install's overwrite guard. Canonical in `bin/loopctl`; `dashboard/generate.py` carries the lazy-seam mirror (must run with no `bin/` on disk), pinned by a drift test — the `resolve_owner` pattern. **Because systemd units live outside `--root`**, the mirror reads `LOOPS_SYSTEMD_UNIT_DIR`/`HOME`; a rehearsal root sets `LOOPS_SYSTEMD_UNIT_DIR` under itself; every `test_dashboard.py`/`test_console.py` fixture pins `LOOPS_INSTALL_BACKEND=launchd` (they write plists), and a systemd fixture class is added to each.
- **`scheduler_loaded(name)`** — the live check, console only: launchd `launchctl print gui/$uid/<label>`; systemd `systemctl --user is-enabled loops-<name>.timer` with `XDG_RUNTIME_DIR` defaulted to `/run/user/$uid` when unset (a non-login ssh shell does not set it). Honours `LOOPS_LAUNCHCTL`/`LOOPS_SYSTEMCTL`. Feeds `/api/state.loaded`.

`/api/state` keeps the field names `plist_present`/`loaded` (B-11 hydration reads them); §13's table redefines `plist_present` as "unit files present for this host's backend" so nobody "fixes" the name. The 巡/休 tooltip says "schedule loaded (launchd)" / "(systemd)".

### 6.2 Console service — `loopctl console install | uninstall | status`

Platform-dispatched like loop install. launchd: generates `launchd/com.roops.console.plist` (same label as today's hand-written file, so the live install is replaced in place) and bootstraps it. systemd: writes `~/.config/systemd/user/loops-console.service` (`Restart=always`, `RestartSec=5`, `WantedBy=default.target`, `Environment=` for `HOME/PATH/LOOPS_ROOT`, stdout/err appended under `state/launchd-logs/`) and `enable --now`. The unit is a **singleton per user**: install refuses if a unit exists whose `LOOPS_ROOT` differs from this root (a rehearsal never installs the console — it runs `loopctl serve` in the foreground on a rehearsal port). Args are baked from `.env` at install: `--port $LOOPS_CONSOLE_PORT` and one `--allow-host` per `LOOPS_CONSOLE_ALLOW_HOSTS` entry — so the real tailnet FQDN stays machine-local (B-23 scrub invariant) and a change needs `console install` again. `status` = unit present + loaded + `GET 127.0.0.1:<port>/api/state` answers. On systemd the §6.3 host checks (linger, tz, XDG) gate `console install` too — the console is the reboot-recovery surface and dies at logout exactly as loops used to if linger is off. Post-install self-verify: `/api/state` (cheap — parses confs, never regenerates) must answer 200 within **30 s** or the install is torn down and reported. §13 amended; the reboot-recovery step 2 gets a CLI. **The console still binds 127.0.0.1 only.**

### 6.3 Install-time host checks (systemd backend)

Before §8.1 step 3, `install` refuses when: `loginctl show-user $USER -p Linger` ≠ `yes` (message names `loginctl enable-linger`); `LOOPS_EXPECT_TZ` is set and differs from the host zone (`/etc/localtime` → zoneinfo name, fallback `timedatectl show -p Timezone`); `XDG_RUNTIME_DIR` is unset (message gives `XDG_RUNTIME_DIR=/run/user/$(id -u)`). Each is a real defect the migration hit. Both backends: the post-kickstart failure strings become backend-neutral ("under the scheduler (launchd|systemd)").

### 6.4 Smaller portability items

- `run-loop.sh`: `export TMPDIR="${TMPDIR:-$ROOT/state/tmp}"` + `mkdir -m 700` at the **top** of the script, before the lock helper's first `mktemp` (lines 109–110), so raw engine stdout never lands in world-readable `/tmp` on Linux.
- `loopctl`'s known-verb set (the swallowed-verb guard, §8) gains `console`, `probe`, `requirements`, `snapshot`, `restore`.
- `_runtime_path`: unchanged list (firstparty's engines are on it); documented as the one place to extend.
- Vocabulary: `state/launchd-logs/` keeps its name (alias-not-a-rewrite); `--trigger launchd` keeps its token (documented reason in §8.1).
- Docs: README badge → "macOS launchd · Linux systemd"; CLAUDE.md 🚨 reboot block rewritten to say launchd-only and point at `loopctl console install`; `skills/loops/SKILL.md` loses its launchd-only story; `LOOP_AUTHORING.md` §5 gains the systemd sleep analogue (`Persistent=true`).

## 7. Per-loop retrofits

Each loop keeps its SPEC.md intent; only the host coupling moves behind §4/§5. No loop gains a wider permission axis. **Every loop goes through `bin/probe` regardless of host** — local vs remote is decided only by `.env` (`LOOPS_PROBE_HOST`), never by a per-loop branch.

| loop | change | `requires=` |
|---|---|---|
| **kagi-ban** | precheck: `bin/probe av-scan --out "$OUT_DIR/scan.json"` replaces the `AV_BIN` check, the `/bin/zsh -l` line, and the direct scan. `findings` stays at the top level of `scan.json` (the probe ADDS keys, it does not wrap — a wrapped document would make the digest count zero exposures and turn the loop green); `render_page.py` reads `probe_host`/`probe_av_version` from the document instead of `platform.node()` and `Info.plist`; the report's subject line reads "subject: <host>". SPEC.md states the subject is the probe host, not the runner host. The darwin-only test skip goes (fake probe). | `probe:av-scan` |
| **ads-x** | snapshot age from `GET $GC_BASE/api/ads/x-cache` — **the sqlite peek at lines 88–108 is deleted**, it is still there today; ledger from `bin/probe ads-x-ledger` (all batches; the latest/previous/pre-month digest math stays in the precheck); account-lock signal from `bin/probe opentwins-lock-signal`. Exit 75 → the precheck prints the existing "input gap" wording plus `probe transport failed`, so the report says *llm unreachable* rather than *no lock*. Direct `ads.db`/`~/.opentwins` reads deleted. | `env:GC_BASE, bin:curl, probe:ads-x-ledger, probe:opentwins-lock-signal` |
| **gc-actions** | precheck: `bin/probe dmp-actions --out` + `bin/probe gc-actions-files --out`, extracted into `$OUT_DIR/remote/…`, and `DMP_OUTPUT_DIR`/`GC_ACTIONS_DIR` pointed there for the rest of the run (the env overrides are now **server-side** inputs of the probes). `apply_tickets.py`: `create_ticket` builds the JSON payload and calls `bin/probe ticket-add <base64url>`; `TICKETS_CLI` moves to the data host's `.env`. Cap of 20 ops/run unchanged. | `bin:curl, probe:dmp-actions, probe:gc-actions-files, probe:ticket-add` |
| **tailnet-zones** (owner `network-system`) | precheck: `bin/probe sysadmin-tailnet --out` → `$OUT_DIR/remote/`; the probe's default `TAILNET_SETUP_DIR` is `$HOME/projects/sysadmin/tailnet` (the rename that broke it; the rehearsal verifies both files resolve there). The optional live-ACL fetch (`TS_POLICY_READ_TOKEN_FILE`) stays in the precheck; without the token on the fleet host it is snapshot-only — **documented degradation**, same as llm today (the token file does not exist there either). Mechanical fix only; flagged to the owner. | `bin:curl, probe:sysadmin-tailnet` |
| **kagami** | precheck line 13: the login-PATH probe is guarded (`command -v zsh` else inherit the unit PATH — which on systemd already carries `~/.local/bin` and `/usr/local/bin`). Declares its tools. `loops.d/kagami/leak-terms.local.txt` is gitignored and must be copied to the guest (runbook). Install on the guest refuses until `gh` is present; **`bin:gh` does not prove auth** — the §8.1 step-5 self-verify run does, exactly as on llm. Its install-time run may open/update the mirror PR if the mock drifted: that is the loop's daily job, accepted and stated in the runbook. | `bin:gh, bin:git, bin:shasum, bin:curl` |
| **ads-google/intl/reddit/program** | no code change; `.env` now reaches them (§3). `ads-program`'s co-location with the four network loops is documented in its SPEC. | `env:GC_BASE, bin:curl` |
| **hello-watchdog** | no code change (F8 verified `curl --fail file:///dev/null` exits 0 on the guest). | `bin:curl` |
| **loop-sensei, hello-loop** | none. | — |
| **phoneapp-cost-sync, flickki-\*** | **untouched** — other agents' untracked loops. flickki are decommissioned (no plist, `enabled=false`). phoneapp is installed on llm today: under D1 it must be moved or uninstalled before the flip. "Moved" is more than the credential: the loop tree is **untracked**, so a guest `git clone` does not contain it — its owner commits the tree (or copies it to the guest) **and** copies `~/.config/phoneapp/cost-sync.env` **and** declares `file:`; otherwise step 1 of the runbook uninstalls it on llm. | — |

## 8. State migration and cutover

### 8.1 `loopctl snapshot <out.tar.gz> [--force]` / `loopctl restore <in.tar.gz> [--force]`

`snapshot`: refuses while any `state/locks/*.lock` holds a live pid, unless `--force` (documented: the db copy is still consistent — Python `sqlite3.Connection.backup()` folds the WAL — but an in-flight run's `state/runs/<id>/` may be partial and will later render as `died`; `--force` is for rehearsal snapshots of a live fleet, never for the flip, which freezes first). Tars the backed-up db + `state/runs/` + `state/loop-data/` + `reports/` (never `launchd/`, `state/locks`, logs, `state/tmp`, `state/probe-log`; `state/kagami-fixture` is rebuilt by kagami each run). Prints counts (runs, findings, dispositions, loop_events). `restore`: refuses when the target root's db already has run rows unless `--force`; `--force` **replaces** the managed paths (`state/loops.sqlite*`, `state/runs/`, `state/loop-data/`, `reports/`) — never merges over them: extract into a temp dir under `state/`, then swap each managed path in (rename old aside, rename new in, delete old) so a failed extraction leaves the previous state intact; extraction uses `tarfile` `filter="data"`; re-applies 0700/0600; prints the same counts; re-runs `loopctl dashboard`. Both host-neutral.

### 8.2 Cutover runbook — `workflows/firstparty-cutover.txt`

Held for the operator answers (D6). **Preconditions, all verified by commands in the runbook:** the operator has answered §12's flip-blocking questions (phoneapp, kagami auth, tailnet name); `loopctl requirements` on the guest is clean for the install set; `curl $GC_BASE/api/ads/x-cache` is 200 from the guest; `loopctl probe status` shows no drift; `leak-terms.local.txt` and any per-loop credential files are in place on the guest. Then, in order:

1. **Freeze llm** — `loopctl console uninstall`; `loopctl pause` every loop; wait until `state/locks/` holds no live pid and `launchctl list | grep com.loops` shows nothing running.
2. **Move state** — `loopctl snapshot` on llm (no `--force`); `tar | ssh` to the guest (no rsync there); on the guest **delete the scratch state first** (`loopctl uninstall hello-loop`, `rm -rf state/loops.sqlite* state/runs state/loop-data reports`), then `loopctl restore` as `svc`; compare the printed counts.
3. **Arm the guest** — `loopctl install` each loop in the documented order; each install runs a real self-verify run (§8.1 step 5) — ~10 sequential engine runs, the probes all hitting llm, kagami's PR path included: accepted, it is what the llm reboot recovery does today. `loopctl console install`.
4. **Ingress** — on llm: remove `loops` from the dev-tailnet `PROJECTS` list, bootout `tailscaled-loops`, delete the `loops` device in the tailnet admin (operator) **so the name is free**; then on the guest's Caddy: `loops.<tailnet>.ts.net { bind tailscale/loops; tls { get_certificate tailscale }; reverse_proxy 127.0.0.1:8929; handle_errors { … static fallback from the guest's `dashboard/` + `reports/`, 502 status kept } }` — the browser's `Host` passes through **untouched** (never `header_up Host`: it is the §13.1 credential; forging it 403s every request or blinds the gate), same for the fallback; `caddy reload`, never restart (PLAN.md rule). No LAN vhost (D7).
5. **Dual-fleet check, then retire llm** — `loopctl list` on llm shows every loop paused; `systemctl --user list-timers` on the guest shows the set; only then `loopctl uninstall` every loop on llm (plists gone — llm becomes data host only; `loopctl` still works there for probes and local runs). Precondition re-checked: **zero installed on llm**.
6. **Verify** — garden over the tailnet shows 巡 for the install set; `loop-sensei` runs clean; the llm reboot-recovery workflow is marked retired for the fleet; CLAUDE.md fleet-state line updated.

### 8.3 Staging and rehearsal (done in this change)

On the guest, as `svc`: replace the tar copy with a **`git clone https://github.com/ytubecoder/roops ~/projects/loops`** (F10 — deploys become `git pull`); uninstall the orphan `hello-loop` timer and delete its scratch state; write `.env` (§3 fleet-host column); `loopctl probe keygen`, add the printed `~/.ssh/config` stanza (`HostName 192.168.1.52` until a DNS record exists) and `ssh-keyscan` line; on llm `probe-server --authorize --write` and set llm's `.env` (data-host column); `apt install gh` on the guest (admin, root work — a package, not a credential). Rehearsal = `loopctl snapshot --force` of the **live** llm state restored into a **rehearsal root** on the guest (`LOOPS_ROOT=~/projects/loops-rehearsal`, `LOOPS_SYSTEMD_UNIT_DIR` under it), then `loopctl requirements`, a supervised `loopctl run` of every portable and probe-backed loop, `loopctl serve` in the foreground on a rehearsal port with a garden check — **no `install`, no `console install`** in the rehearsal. The rehearsal root is deleted afterwards; the live db on llm is never touched.

## 9. Testing

Hermetic, both hosts, `bash tests/run-tests.sh` green on macOS and on the guest.

- `test_loopconf.py`: `.env` grammar (quoted/bare/comment/expansion/upper-case keys/lower-case key rejected/malformed → error with line), `requires=` grammar (kinds, dedupe, max, unknown kind), `load_env` precedence.
- `test_loopctl.py`/`test_loopctl_systemd.py`: `requirements` matrix (live and `--no-live`); install refusal per kind; `probe:` via a fake `bin/probe`; linger/tz/XDG refusals with fake `loginctl`/zoneinfo; `console install/uninstall/status` on both backends with fake `launchctl`/`systemctl`, singleton refusal, self-verify timeout teardown; `snapshot`/`restore` round-trip counts and refusals (live lock, existing rows, `--force`); backend-neutral failure strings; new verbs in the known-verb set.
- `test_runner.sh`: unmet requirement → `precheck-failed` row, no precheck, no engine (fake engine canary); `.env` reaches precheck and render but **not** the engine (canary adapter dumps its env); explicit export beats `.env`; malformed `.env` → `harness-error`; `TMPDIR` under state before the lock.
- `test_probe.py` (new): server parsing/refusals (§5.4 item 2 list), stdin canary, header grammar, timeout kills a TERM-ignoring child, log line shape and description omission, self-prune, `--authorize` line / duplicate / `--replace`; client local mode, remote mode against a fake `ssh` (`LOOPS_SSH` seam) asserting the `--` placement, exit 75 on 255, `--check`/`--list`/`--out` (0600); a tar probe extracted with the data filter rejecting `..`/absolute/links.
- `test_dashboard.py`: launchd pin on existing fixtures; `_schedule_loaded` under both backends + the drift test against `loopctl.unit_files_present`; tooltip text.
- `test_console.py`: systemd fixture class — `/api/state`, `/rounds` 200 with units present, 409 without; `scheduler_loaded` via fake `systemctl` with `XDG_RUNTIME_DIR` asserted.
- Per-loop: `test_kagi_ban.py` precheck tests run **everywhere** against a fake `probes/av-scan` (host/version flow through to the page); `ads-x` with a fake ledger of **three** batches (month-to-date and $/day asserted) and the exit-75 wording; `gc-actions` with fake tar probes and `ticket-add` receiving a title **with spaces** and a description with the `[loop:gc-actions | id]` prefix (idempotency on the second run); `tailnet-zones` with a fake probe; `apply_tickets.py` payload encoding.
- `html_selfcontained`/token-drift unchanged.

## 10. Rollout order (peon work packages)

**WP1** `.env` loader (+ `.gitignore`) + `requires=` (parser, `requirements` verb, validate/install/run gating; `probe:` calls `bin/probe --check`, stubbed by a fake in tests) + known-verb set. **WP2** probe channel (server, client, built-ins, header grammar, `probes/README.md`, tests). **WP3** backend-aware dashboard/console predicates + `console install` + install-time host checks + TMPDIR + wording/docs. **WP4** the eight probes + per-loop retrofits + their tests. **WP5** `snapshot`/`restore` + cutover runbook + CLAUDE.md/README/SKILL docs. Order: WP1 → (WP2 ‖ WP3) → WP4 → WP5 (the runbook names verbs from every package, so it is frozen last). Foreman (fable) stages the guest and runs the rehearsal (§8.3) after WP4/WP5 merge — live/judgment work stays with the foreman.

## 11. INTERFACES amendments shipped with this change

§1 layout (`probes/`, `.env`, `state/probe-log/`, `state/tmp/`); §4.1 (env load after step 3; requirement check before step 4; TMPDIR); §5 table (`requires=`); §5.0 (`env` subcommand, `load_env`, key regex); new §5.3 "Host requirements"; §6.1 (engine env: `.env` keys are stripped before the adapter); §8 verbs (`requirements`, `console`, `probe`, `snapshot`, `restore`; known-verb set); §8.1 (install refusals: requirements, linger, tz, XDG; backend-neutral wording); §10 (install-state rule = `unit_files_present` per backend; mirror + drift test); §13 (console service; `plist_present` meaning; `scheduler_loaded` dispatch + XDG); new §14 "Probe channel" (server grammar, built-ins, header, encoding for write probes, client exits, security properties, log + prune, two-checkout deploy rule). `docs/LOOP_AUTHORING.md` q13 + probe authoring section; `docs/HARNESS_PLAN.md` gets a one-paragraph pointer (rationale lives here).

## 12. Questions held for the end (product/loop level — not blocking the build)

Collected in the final report, one batch. **Flip-blocking:** phoneapp-cost-sync — move (owner copies the credential, declares `file:`) or uninstall before the flip; kagami on the guest — `gh auth login` as `svc` vs a PAT file (both paths exist in the precheck); free the `loops` tailnet node name (delete llm's device) or accept a new name. **Non-blocking:** tailnet-zones owner sign-off + whether it should write `/srv/infra/zones` on the guest; `llm.home.arpa` DNS on the MikroTik (the IP works meanwhile); indexing `ytubecoder/roops` in maguyva; gc's unauthenticated `0.0.0.0:8787` (maguyva's open note); whether a fleet dead-man alarm ticket should be opened now.
