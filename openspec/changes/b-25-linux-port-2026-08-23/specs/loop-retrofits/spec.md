# WP4 — the seven probes + per-loop retrofits

Peon spec. Self-contained: you have no conversation context. Parent design:
`openspec/changes/b-25-linux-port-2026-08-23/design.md` §5.1 (probe table), §7 (per-loop table)
— read both in full. House rules: `CLAUDE.md`, `docs/INTERFACES.md` §0 and the new §14 (probe
channel, written by WP2), `probes/README.md` (header grammar, `--check` contract). WP1–WP3 are
merged: `bin/probe`, `bin/probe-server`, `bin/probe_core.py`, `probes/echo-test` exist and are
tested; `loop.conf` accepts `requires=`; `bin/loopconf.py` has `load_env`.

## 0. Context in three sentences

Loops now run on a Linux guest; data that stays on the Mac is reached with `bin/probe <name>`,
which runs `probes/<name>` locally when `LOOPS_PROBE_HOST` is unset and over ssh otherwise — a
loop never branches on the host. This package writes the seven real probes and moves the five
host-coupled loops onto them, deleting every direct read of `~/.growth-console`, `~/.opentwins`,
`/Applications/…`, `/bin/zsh -l`, and llm-only project trees from `loops.d/`. Every loop also
declares `requires=`.

## 1. Probes (each: executable, header per `probes/README.md`, `--check` implemented, **no stdin**)

All probes are `#!/usr/bin/env python3` unless stated. They read their inputs from env (the
server injects the data host's `.env`; defaults below match today's precheck defaults). None may
print secrets. `--check` prints one line and exits 0/1 without side effects.

| probe | header | body |
|---|---|---|
| `av-scan` (bash) | `probe-timeout-s: 280`, `probe-writes: none`, `probe-output: json`, `probe-reads: $AV_BIN, /bin/zsh -l, Automic Vault Info.plist` | `AV_BIN="${AV_BIN:-/Applications/Automic Vault.app/Contents/MacOS/av}"`; if zsh exists: `AUDIT_PATH="$(/bin/zsh -l -c 'printf %s "$PATH"' 2>/dev/null \| tail -n 1)"` and **`export PATH="$AUDIT_PATH"`** when it contains `/bin` (this is the 2026-07-30 PATH-hygiene fix — the scan must run on the login PATH, not the unit PATH); `av_version` from `"$AV_BIN" --version \| head -n1` (fallback: `CFBundleShortVersionString` from `/Applications/Automic Vault.app/Contents/Info.plist` via `python3 -c 'import plistlib…'`; fallback `unknown`); run `"$AV_BIN" scan --json` to a temp file; then `python3` merges three keys into the top level of that document — `probe_host` (`platform.node()`), `probe_av_version`, `probe_login_path` — and prints it. **`findings` must remain a top-level key.** `--check`: `AV_BIN` executable → `ok av-scan <AV_BIN>` else exit 1. |
| `opentwins-lock-signal` | timeout default, `probe-output: json`, reads `~/.opentwins/workspaces/agent-twitter/memory/` | last 3 files matching `20??-??-??.md` (sorted); for each, the markers from `loops.d/ads-x/precheck.sh:211-217` (`account has been locked`, `account/access`, case-insensitive); output `{"files":[names],"hits":[{"file":name,"line":lineno}]}` — never file contents. Missing dir → `{"files":[],"hits":[]}`, exit 0. `--check`: dir exists → ok. |
| `ads-x-ledger` | `probe-output: json`, reads `~/.growth-console/ads.db` (env `ADS_DB` override) | open `mode=ro` (`sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)`); batches via `SELECT batch_id, MAX(imported_at) AS ia FROM x_cache GROUP BY batch_id ORDER BY ia`; for each, the `_decode_batch` function copied **verbatim** from `loops.d/ads-x/precheck.sh:121-141`; output `{"batches":[{"batch_id","imported_at","life","window","rows","bad","at_cap","headroom"}]}` oldest-first. Missing db → `{"batches":[],"error":"…"}`, exit 0. `--check`: db file exists → ok. |
| `dmp-actions` | `probe-output: tar`, reads `$DMP_OUTPUT_DIR` (default `$HOME/projects/digital-marketing-pro/output/maguyva`) | the **newest 30** run dirs by `dir_key` (copy from `loops.d/gc-actions/precheck.sh:140-143`), and from each only `actions/register.yaml` and `ACTIONS.md` when present; `tarfile.open(fileobj=sys.stdout.buffer, mode="w\|")` with **relative** arcnames `<dirname>/actions/register.yaml` etc.; skip symlinks; stop and exit 1 with `cap exceeded` on stderr past 32 MiB. `--check`: dir exists. |
| `gc-actions-files` | `probe-output: tar`, reads `$GC_ACTIONS_DIR` (default `$HOME/projects/maguyva-marketing/gc-actions`) | tar of `action-sources.yaml`, `action-ticket-map.yaml`, `PRODUCT_BACKLOG.md` (those that exist; arcnames = bare file names); cap 8 MiB. `--check`: dir exists and at least `PRODUCT_BACKLOG.md` readable. |
| `sysadmin-tailnet` | `probe-output: tar`, reads `$TAILNET_SETUP_DIR` (default **`$HOME/projects/sysadmin/tailnet`**) | tar of `docs/policy-live.hujson` and `site/zones-meta.json` (arcnames keep those relative paths); cap 4 MiB; a missing file → exit 1 naming it. `--check`: both files readable. |
| `ticket-add` **(write)** | `probe-writes: one ticket via tickets-cli add`, `probe-output: text`, reads `$TICKETS_CLI` (default `$HOME/.claude/ticket-takeaway/tickets-cli.py`), `probes/ticket-add.allow` | exactly one argv: base64url (`base64.urlsafe_b64decode`, pad as needed) of a JSON object; decoded size ≤ 6144 bytes; keys exactly `{project,title,section,priority,description}`; `project` must be a line of `probes/ticket-add.allow` (ship the file containing `maguyva-actions`); `section == "ideas"`; `priority in ("high","medium","low")`; `1 <= len(title) <= 200`, no newline; `description` ≤ 4096 chars and `startswith("[loop:")`. Any violation → stderr `refused: <reason>`, exit 64, nothing run. Then `subprocess.run(["python3", TICKETS_CLI, "add", project, title, "--section", "ideas", "--priority", priority, "--description", description], timeout=60)` — argv, never a shell; pass its stdout through and exit with its code. `--check`: `TICKETS_CLI` is a file. |

`probes/README.md`: add a row per probe (what it reads, whether it writes).

## 2. Per-loop retrofits

Rule for every loop: go through `bin/probe` unconditionally; never `if [ -n "$LOOPS_PROBE_HOST" ]`.
Transport failure is exit **75** from `bin/probe`: print the loop's existing "input gap" wording
plus the literal `probe transport failed (llm unreachable)` and continue where the loop already
tolerates a missing input; exit non-zero only where the loop already hard-fails on that input.

### 2.1 `kagi-ban`
- `precheck.sh`: delete lines 8–25 (the `AV_BIN` check, the `/bin/zsh -l` block, `AV_VERSION`,
  the direct scan). Replace with `"$LOOPS_ROOT/bin/probe" av-scan --out "$OUT_DIR/scan.json"` —
  exit 75 → `echo "ERROR: av-scan probe transport failed (llm unreachable)" >&2; exit 1` (this
  loop cannot report without its scan — unchanged semantics); other non-zero → exit 1 as today.
  `AV_VERSION` for the python block comes from `scan.json`'s `probe_av_version` (read it in the
  heredoc instead of argv 4). Keep `current_findings` reading top-level `findings`.
- `render_page.py`: `detect_av_version()` (`:170-175`) reads `probe_av_version` from the loaded
  scan document (fallback `""`); wherever the page prints the host (`platform.node()` — grep it),
  use `probe_host` from the document (fallback `platform.node()`). Add a visible line near the
  existing `av scan --json` note: `subject: <probe_host>`.
- `SPEC.md`: one paragraph — the subject is the probe host (where `av` runs), not the runner host;
  the report names it.
- `loop.conf`: `requires="probe:av-scan"`.
- `tests/test_kagi_ban.py`: remove the `skipUnless(darwin)` decorator (`:74-79`); `run_precheck`
  installs a fake `probes/av-scan` in the temp root (copy the header from the real one; body:
  `cat "$SCAN_FIXTURE"` with the three `probe_*` keys merged — use the existing `make_stub_av`
  fixture JSON) and ensures `bin/probe` + `bin/probe_core.py` + `bin/loopconf.py` +
  `bin/requirements.py` are copied into the temp root's `bin/` (local mode). Existing
  assertions stay; add `test_precheck_transport_failure_exits_1` (fake `probes/av-scan` absent
  and `LOOPS_PROBE_HOST=x` with `LOOPS_SSH=<fake exiting 255>` → exit 1, stderr has `transport`)
  and `test_page_shows_probe_host_and_version` (render with a fixture carrying
  `probe_host: "llm"`, `probe_av_version: "2.4.0"` → both strings in the page).

### 2.2 `ads-x`
- `precheck.sh` python block: **delete** the sqlite snapshot peek (`:88-108`) — replace with a
  read of `$INPUTS/x-cache.json` fetched by a new `fetch x-cache "/api/ads/x-cache"` line next
  to the other four (`:45-48`): `snapshot_at = body["snapshot_at"]`, `snapshot_age_days =
  body["age_days"]` (nulls → the existing UNKNOWN wording).
- Ledger (`:112-201`): delete `_decode_batch` and the sqlite block; before the python heredoc,
  in bash: `"$LOOPS_ROOT/bin/probe" ads-x-ledger --out "$INPUTS/x-ledger.json" || echo "probe_exit=$?" > "$INPUTS/x-ledger.exit"`.
  In python: load `x-ledger.json` → `decoded = [(b["imported_at"], b["batch_id"], b) for b in
  batches if b["rows"] - b["bad"] > 0]`; the rest of the digest math (`:157-201`) unchanged, it
  already works on `(ia, bid, dict)` tuples. If the exit file says 75 → print `- ledger
  unreadable: probe transport failed (llm unreachable) — treat as input gap.`
- Account signal (`:205-230`): replace the file scan with `bin/probe opentwins-lock-signal
  --out "$INPUTS/x-lock.json"` (same `|| … .exit` pattern); python prints the same three
  messages from `hits` (file names from `hits[*].file`, newest last), the transport-failure
  line on 75.
- `loop.conf`: `requires="env:GC_BASE, bin:curl, probe:ads-x-ledger, probe:opentwins-lock-signal"`.
- Tests (new `tests/test_ads_x_precheck.py`): temp root with fake `bin/probe` local mode and
  fake probes (`ads-x-ledger` printing a **three-batch** fixture spanning a month boundary,
  `opentwins-lock-signal` printing one hit), a fake `GC_BASE` served by a `http.server` thread
  answering the five endpoints with minimal JSON (`x-cache` with `age_days: 4.2`): assert the
  digest contains `x_cache_age: 4.2`, `STALE`, `TRUE lifetime spend $`, `serving rate`, `month
  attribution`, `lock/access-wall markers present`; a second run with `LOOPS_PROBE_HOST=x` and a
  fake ssh exiting 255 → digest contains `probe transport failed` twice and the precheck still
  exits 0; no `~/.growth-console` or `~/.opentwins` path string remains in `precheck.sh`
  (grep assertion).

### 2.3 `gc-actions`
- `precheck.sh`: replace `:19-20` with
  ```bash
  REMOTE="$OUT_DIR/remote"; mkdir -p "$REMOTE"
  "$LOOPS_ROOT/bin/probe" dmp-actions --out "$REMOTE/dmp.tar"
  "$LOOPS_ROOT/bin/probe" gc-actions-files --out "$REMOTE/gc.tar"
  python3 - "$REMOTE" <<'PY'   # extract both with tarfile filter="data" into $REMOTE/dmp and $REMOTE/gc
  PY
  DMP_ROOT="$REMOTE/dmp"; GC_ACTIONS_DIR="$REMOTE/gc"
  ```
  Exit 75 from either probe → `echo "ERROR: probe transport failed (llm unreachable)" >&2;
  exit 1` (this loop has nothing to read without them). Extraction helper: put it in
  `bin/probe_core.py` as `extract_tar(path, dest)` (WP2 left room for it; if not present, add it
  there — allowed file) using `tarfile.open(path).extractall(dest, filter="data")`.
- `bin/apply_tickets.py`: `CLI` constant removed; `GC_DIR` now comes from a fresh
  `bin/probe gc-actions-files --out <tmp>` + `extract_tar` at start (the board may have changed
  since the precheck — dedupe must see the latest). `create_ticket` builds
  `payload = base64.urlsafe_b64encode(json.dumps({...}).encode()).decode().rstrip("=")` and runs
  `[LOOPS_ROOT/bin/probe, "ticket-add", payload]`; treat exit 75 as failed with the transport
  message; keep `MAX_OPS`, the `[loop:gc-actions` check, the `covered` dedupe.
- `loop.conf`: `requires="probe:dmp-actions, probe:gc-actions-files, probe:ticket-add"`.
- Tests (new `tests/test_gc_actions_probe.py`): fake `dmp-actions`/`gc-actions-files` probes
  emitting tars built from fixture trees; run the precheck → digest mentions the register
  count; run `apply_tickets.py` with a fake `ticket-add` probe that decodes its payload into a
  log: assert the decoded JSON has the title **with spaces** intact, `section == "ideas"`, the
  description starting `[loop:gc-actions | `; second run with the fake board now containing that
  id → `already covered — skipped`; exit 75 → `failed` counted and the transport message.

### 2.4 `tailnet-zones` (owner `network-system` — mechanical change only)
- `precheck.sh`: replace `:10-12` with a probe fetch into `$OUT_DIR/remote/` (`sysadmin-tailnet
  --out …`, `extract_tar`), then `SNAPSHOT="$OUT_DIR/remote/docs/policy-live.hujson"`,
  `META="$OUT_DIR/remote/site/zones-meta.json"`; exit 75 → `ERROR: … transport failed`, exit 1
  (hard-fails today on a missing file too). The token-file live fetch (`:26-42`) unchanged.
- `loop.conf`: `requires="bin:curl, probe:sysadmin-tailnet"`.
- Test (new `tests/test_tailnet_zones_precheck.py`): fake probe tar from a fixture; precheck
  reaches `build_model.py` with the extracted paths (assert via a stub `build_model.py` in the
  temp root that prints its argv) and exits 0; missing-file probe exit 1 → precheck exit 1.

### 2.5 `kagami`
- `precheck.sh:13-17`: guard —
  ```bash
  if command -v zsh >/dev/null 2>&1; then
    AUDIT_PATH="$(zsh -l -c 'printf %s "$PATH"' 2>/dev/null | tail -n 1)"
  else
    AUDIT_PATH=""
  fi
  ```
  (the `case` below already handles the empty string with a WARN).
- `loop.conf`: `requires="bin:gh, bin:git, bin:shasum, bin:curl"`.
- Test: in `tests/test_kagami_fixture.py` (or a new small file) run `bash -n precheck.sh` and a
  `PATH`-stripped invocation of just the first 20 lines? — NO: instead assert by grep that the
  literal `/bin/zsh -l` no longer appears in `loops.d/kagami/precheck.sh` and that
  `command -v zsh` does.

### 2.6 Declarations only
- `ads-google`, `ads-intl`, `ads-reddit`, `ads-program`: `requires="env:GC_BASE, bin:curl"`.
- `hello-watchdog`: `requires="bin:curl"`.
- `ads-program/SPEC.md`: one sentence — it reads its four sibling loops' newest action sets from
  the shared `state/runs/`, so the five ads loops must run on the same host/root.
- A fleet-wide test in `tests/test_loopctl.py`: `loopctl validate --all` over the REAL `loops.d/`
  (read-only, `--root` = repo root) exits 0 — every `requires=` line parses.

## 3. Hard constraints

- **Allowlist**: `probes/*` (new files + `README.md`), `bin/probe_core.py` (only to add
  `extract_tar`), `loops.d/kagi-ban/*`, `loops.d/ads-x/*`, `loops.d/gc-actions/*`,
  `loops.d/tailnet-zones/*`, `loops.d/kagami/precheck.sh`, `loops.d/kagami/loop.conf`,
  `loops.d/ads-google/loop.conf`, `loops.d/ads-intl/loop.conf`, `loops.d/ads-reddit/loop.conf`,
  `loops.d/ads-program/loop.conf`, `loops.d/ads-program/SPEC.md`, `loops.d/hello-watchdog/loop.conf`,
  `tests/test_kagi_ban.py`, `tests/test_ads_x_precheck.py`, `tests/test_gc_actions_probe.py`,
  `tests/test_tailnet_zones_precheck.py`, `tests/test_kagami_fixture.py`, `tests/test_loopctl.py`,
  `tests/fixtures/*`, `PEON_REPORT.md`. Do NOT touch `loops.d/phoneapp-cost-sync`,
  `loops.d/flickki-*` (other agents'), `bin/probe`, `bin/probe-server`, `bin/run-loop.sh`,
  `bin/loopctl`, `dashboard/`.
- No loop's `perm_*` axes or `exec_allowlist` change. No loop gains a hostname.
- Tests never touch the network, never read `~/.growth-console`/`~/.opentwins`/`/Applications`,
  never call the real `tickets-cli.py` (fake probes everywhere; `LOOPS_SSH` fake for 75).
- Verify: `bash tests/run-tests.sh` → 0.

## 4. Definition of Done

- Every test in §2 exists by name, asserts what is written, passes; suite green.
- `grep -rn "growth-console\|\.opentwins\|/Applications\|/bin/zsh -l\|tailnet-setup" loops.d/
  --include=precheck.sh --include=*.py` returns nothing outside comments that explain history.
- `PEON_REPORT.md`: files touched, verify tail, deviations with reasons.
