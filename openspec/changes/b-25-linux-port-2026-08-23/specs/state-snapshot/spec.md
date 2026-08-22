# WP5 — `loopctl snapshot` / `restore`, the cutover runbook, fleet docs

Peon spec. Self-contained: you have no conversation context. Parent design:
`openspec/changes/b-25-linux-port-2026-08-23/design.md` §8 (read it; §8.2 is the runbook's
content), §6.4 docs bullets. House rules: `CLAUDE.md`, `docs/INTERFACES.md` §0. WP1–WP4 are
merged: the verbs `requirements`, `console`, `probe` exist; `bin/probe` exists; loops declare
`requires=`.

## 0. Context in three sentences

The fleet's whole state — runs, findings, and the human **dispositions** that stop settled
findings from re-nagging — lives in `state/loops.sqlite` plus `state/runs/`, `state/loop-data/`,
`reports/`. Moving it to the Linux guest must be a consistent cold copy, mechanical and checked
by counts. The cutover itself is a runbook the operator executes once, after answering three
questions; this package writes the tooling and the runbook, it does not execute the cutover.

## 1. Deliverables

### 1.1 `loopctl snapshot <out.tar.gz> [--force]`

- Refuse (exit 1, message listing them) if any `state/locks/*.lock` names a live pid — reuse the
  liveness logic in `bin/lock.py` (find its "is the pid alive" helper; do not reimplement) —
  unless `--force`, which prints `WARNING: N live lock(s) — in-flight runs will be partial in the
  snapshot` and continues.
- Consistent db copy: `src = sqlite3.connect(db_path)`; `dst = sqlite3.connect(tmp)`;
  `src.backup(dst)`; close both. Never copy the `-wal`/`-shm` files.
- Tar (`tarfile`, `w:gz`) with arcnames rooted at `state/loops.sqlite`, `state/runs/…`,
  `state/loop-data/…`, `reports/…`; **exclude** `state/locks`, `state/launchd-logs`, `state/tmp`,
  `state/probe-log`, `state/kagami-fixture`, `launchd/`, anything else. Skip symlinks. Write via a
  temp file in the target dir, rename into place, mode 0600.
- Print counts from the backed-up db: `runs=<n> findings=<n> dispositions=<n> loop_events=<n>
  run_dirs=<n> reports=<n>` and the archive size. `--json` prints the same as a dict.

### 1.2 `loopctl restore <in.tar.gz> [--force]`

- Refuse (exit 1) when the target root's `state/loops.sqlite` exists and has `runs` rows, unless
  `--force`. With `--force`, the managed paths are **replaced, never merged**.
- Procedure: extract into `state/.restore-<pid>/` with `tarfile.open(...).extractall(dest,
  filter="data")` (refuses absolute paths, `..`, links); verify the archive contained
  `state/loops.sqlite`; then for each managed path (`state/loops.sqlite` (+ remove any stale
  `-wal`/`-shm`), `state/runs`, `state/loop-data`, `reports`): rename the existing one to
  `<path>.pre-restore-<pid>` if present, rename the extracted one into place, then delete the
  `.pre-restore-*`. A failure before the swap leaves the previous state untouched; print the
  step reached.
- Re-apply modes: dirs 0700, files 0600 under `state/` and `reports/`.
- Run `db.py init` (schema no-op on a current db) and then the same count line as `snapshot`.
  Exit 1 if the archive's embedded count file (write `state/.snapshot-counts.json` into the tar
  at snapshot time) disagrees with what was restored. Then regenerate the dashboard
  (`loopctl dashboard`, best-effort, warn on failure).
- Both verbs register in the `dispatch` dict (known-verb set) and take `common_sub` parents.

### 1.3 `workflows/firstparty-cutover.txt`

Same format as `workflows/verify-loop-supervised.txt` (header block; `## Prerequisites`;
`## Steps` with `Run:` / `Expected outcome:` / `If fails:` per step). Content = design §8.2
verbatim in command form, in this order, with these exact facts:

- Header: Project loops; Frequency once; Description: move the fleet from llm (macOS/launchd) to
  firstparty (Debian/systemd) as one unit — one db, one console.
- Prerequisites (each a command and its expected output): operator answers recorded for
  phoneapp-cost-sync, kagami auth, tailnet name; on the guest `bin/loopctl requirements` exit 0
  for the install set; `curl -s -o /dev/null -w '%{http_code}' "$GC_BASE/api/ads/x-cache"` → 200;
  `bin/loopctl probe status` exit 0 (no drift); `loops.d/kagami/leak-terms.local.txt` present on
  the guest; `git -C ~/projects/loops status` clean on both hosts and same commit.
- Step 1 freeze llm: `bin/loopctl console uninstall`; `for l in $(bin/loopctl list --json | …
  installed); do bin/loopctl pause $l; done`; wait until `ls state/locks` has no live pid
  (`python3 bin/lock.py …` — use whatever `lock.py` offers) and `launchctl list | grep com.loops`
  shows no running pid.
- Step 2 move state: `bin/loopctl snapshot /tmp/loops-state.tgz` (no `--force`); `cat
  /tmp/loops-state.tgz | ssh firstparty-svc 'cat > /tmp/loops-state.tgz'` (no rsync on the
  guest); on the guest: `bin/loopctl uninstall hello-loop` (if present), `rm -rf state/loops.sqlite*
  state/runs state/loop-data reports` (the scratch state), `bin/loopctl restore
  /tmp/loops-state.tgz`; compare the two count lines.
- Step 3 arm the guest: install order `hello-watchdog hello-loop loop-sensei ads-google ads-intl
  ads-reddit ads-x ads-program gc-actions kagi-ban tailnet-zones kagami` (kagami last; each
  install runs a real self-verify run — a ~10-run sequence, kagami's may open/update the mirror
  PR, which is its job); `LOOPCTL_INSTALL_POLL_TIMEOUT_S=600 bin/loopctl install <name>`;
  `bin/loopctl console install`.
- Step 4 ingress: on llm remove `loops` from `PROJECTS` in
  `~/.config/dev-tailnet/bin/register-tailscaled.sh`, `launchctl bootout gui/$(id -u)/
  com.generalissimo.dev-tailnet.tailscaled-loops`, delete the `loops` device in the tailnet admin
  console (OPERATOR), remove the `@loops` route + `@loopsdown` fallback from
  `~/.config/dev-tailnet/Caddyfile` and `launchctl kickstart -k` the dev-tailnet caddy (per
  `~/.config/dev-tailnet/WARMSTART_CADDY_CLEANUP.md`); on the guest add to `/etc/caddy/Caddyfile`
  the vhost from design §8.2 step 4 (write it out in full in the runbook: `bind tailscale/loops`,
  `tls { get_certificate tailscale }`, `reverse_proxy 127.0.0.1:8929` **with no `header_up Host`**,
  `handle_errors` serving `/home/svc/projects/loops/dashboard` + `/reports/*` from
  `/home/svc/projects/loops/reports` via `file_server` keeping the 502 status), `sudo caddy
  validate --config /etc/caddy/Caddyfile --adapter caddyfile` then `sudo systemctl reload
  caddy-ts` (never restart); verify `curl -sk https://loops.<tailnet>.ts.net/api/state` from a
  tailnet device → 200.
- Step 5 dual-fleet check then retire llm: on llm `bin/loopctl list` → every loop paused/not
  running; on the guest `XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user list-timers` → the
  set; only then on llm `for l in …; do bin/loopctl uninstall $l; done` and confirm
  `launchctl list | grep -c com.loops` → 0 (precondition: zero installed on llm).
- Step 6 verify: garden shows 巡 for the install set; `bin/loopctl run loop-sensei` completed;
  edit `~/.claude/workflows/loops-reboot-recovery.txt` header to say "RETIRED for the fleet
  2026-xx-xx — llm is data host only; keep only if a loop is ever re-installed on llm"; update
  `CLAUDE.md` fleet-state line.
- Rollback (one section): on llm `bin/loopctl resume` each loop + `console install`; on the guest
  `loopctl uninstall` each; Caddy routes back. The llm state was never deleted until step 5, so
  rollback before step 5 is free.

### 1.4 Docs

- `CLAUDE.md` (repo): replace the **Fleet state** paragraph's first sentence with a dated
  "cutover pending — see `workflows/firstparty-cutover.txt`; B-25" note and shorten the 🚨 block
  to "launchd-only failure; on systemd units persist (linger)". Add to "Start here": *Moving or
  copying fleet state:* `loopctl snapshot`/`restore`. Keep everything else.
- `README.md`: a short "Hosts" section — macOS (launchd) and Linux (systemd) are both supported;
  `.env` is host config; `requires=` declares loop needs; `bin/probe` reaches a data host;
  `loopctl snapshot/restore` moves state. Five sentences, link to the design doc.
- `docs/INTERFACES.md`: §8 verb table gains `snapshot`, `restore` (with the refusal rules and the
  replace-not-merge rule); §1 notes `state/.snapshot-counts.json` inside archives only.
- `~/.claude/workflows/loops-reboot-recovery.txt` is OUTSIDE this repo — do not touch it; the
  runbook step 6 tells the operator what to edit.

## 2. Mandated tests (`tests/test_loopctl.py`, extend; reuse `LoopsRoot`)

- `test_snapshot_refuses_live_lock_unless_force` — create `state/locks/x.lock` containing
  `os.getpid()` (alive): exit 1 with the lock name; `--force` → exit 0 and `WARNING: 1 live
  lock`. A lock with a dead pid does not refuse.
- `test_snapshot_contents_and_counts` — seed the db with 2 runs, 1 finding, 1 disposition via
  `db.py` CLI verbs (use the same calls `tests/test_db.py` uses), two `state/runs/<id>/` dirs with
  a file, `reports/a/latest.md`, plus files under `state/locks`, `state/launchd-logs`, `state/tmp`,
  `state/probe-log`, `launchd/`: the archive contains exactly the managed paths (list member
  names), no excluded path, no `-wal`; stdout has `runs=2 findings=1 dispositions=1`; archive
  mode 0600; `state/.snapshot-counts.json` present in the archive.
- `test_restore_refuses_existing_rows_then_force_replaces` — restore into a root whose db has a
  run → exit 1; seed the target `state/runs/stale/` and `reports/stale/`; `--force` → exit 0,
  `state/runs/stale` GONE, the archive's run dirs present, counts printed equal the snapshot's,
  no `.pre-restore-*` left, dirs 0700/files 0600, `dashboard/loops.html` regenerated (exists).
- `test_restore_rejects_unsafe_archive` — a hand-built tar with `../evil` and an absolute
  member → exit 1, nothing written outside `state/.restore-*`, target state untouched.
- `test_restore_count_mismatch_fails` — tamper the counts file inside a copy of the archive →
  exit 1 with `count mismatch`.
- `test_snapshot_restore_roundtrip_preserves_dispositions` — snapshot, restore into a fresh
  root, `db.py query suppressed` (or whatever verb lists dispositions) returns the same
  disposition.
- `test_snapshot_and_restore_are_known_verbs` — `loopctl --actor snapshot` → ambiguous.
- `test_cutover_runbook_exists_and_names_every_loop` — `workflows/firstparty-cutover.txt`
  exists, contains `Step` sections 1–6, the string `header_up Host` preceded by `no` on the same
  line, and every loop name from the real `loops.d/` that has a `launchd/`-style schedule
  (exclude `flickki-*`, `phoneapp-cost-sync`) appears in step 3.

## 3. Hard constraints

- **Allowlist**: `bin/loopctl`, `bin/db.py` (only if a count helper is needed), `bin/lock.py`
  (only to expose an existing liveness helper), `workflows/firstparty-cutover.txt`, `CLAUDE.md`,
  `README.md`, `docs/INTERFACES.md`, `tests/test_loopctl.py`, `PEON_REPORT.md`.
- `snapshot` never modifies the source root; `restore` never writes outside the target root.
- No network; no real `ssh`; tests use temp roots only. Never run `snapshot` against the real
  `~/projects/loops` state in a test.
- Verify: `bash tests/run-tests.sh` → 0.

## 4. Definition of Done

- Every §2 test exists by name, asserts what is written, passes; suite green.
- Runbook + docs + INTERFACES in the same commit.
- `PEON_REPORT.md`: files touched, verify tail, deviations with reasons.
