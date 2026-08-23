# support-migration-refactor — brief for the loops project agent

Left here 2026-08-22 by the **pve1 migration** agent (`~/projects/sysadmin/pve1/`).

**This is a problem statement, not a plan.** It states the objective and the
issues found while trying to move the fleet, with the evidence for each. It
deliberately does **not** propose solutions — the operator's intent is that this
project decides how to solve them, with the fuller context you have.

---

## 1. The objective

The `pve1` migration moves always-on services off the `llm` Mac mini onto three
trust-separated Debian 13 KVM guests on a Proxmox host, so llm gets its RAM and
CPU back for interactive work.

The loops fleet was one of the services in scope. It is scheduled work that runs
unattended, so it is a good fit for a guest, and there is a second motive: under
launchd, `install` = `launchctl bootstrap` with nothing in
`~/Library/LaunchAgents`, so **a reboot silently kills the whole fleet and
nothing alarms**. That happened on 7 Aug and was found by hand on 10 Aug.

**The fleet has been descoped from the migration** (operator decision,
2026-08-22). Everything else in the migration is being finished so it can be
declared complete without the fleet. The fleet is now this project's call:
either finish moving it, or make the loops that cannot move work remotely, or
decide some of them stay on llm permanently. That decision is yours.

Nothing was cut over. **The fleet is untouched and still running on llm** — all
13 loops still bootstrapped, nothing uninstalled, no state copied.

---

## 2. What already shipped into this repo — do not redo it

All pushed. The mechanism works; the blockers below are all elsewhere.

| Commit | What |
|---|---|
| `b485843` | `bin/schedule.py` emits a `systemd` timer form beside the `launchd` one. Grammar unchanged, so no `loop.conf` or `SPEC.md` in the fleet moves. INTERFACES §5.1 amended. |
| `d2e8e71` | `loopctl`'s install backend is platform-dispatched. `install` / `uninstall` / `pause` / `resume` / `set-schedule` / `_is_installed` branch on `_install_backend()`. launchd path byte-identical; its 170 tests still pass. INTERFACES §8.1 amended. |
| `90051e9` | The suite passes on Linux as well as macOS — 786 python tests both ways, plus every shell fixture. |
| `6989d08` | `WARMSTART_SYSTEMD.md` — what was verified on the guest, and the per-loop portability matrix. |
| maguyva `0f29428` | `GET /api/ads/x-cache` in growth-console, so `ads-x` can read the X snapshot age over HTTP instead of opening `ads.db`. |

Ticket **B-24** is in For Review on the loops board; **B-26** on maguyva's.

**Verified on the real guest, not asserted:** a supervised run completed as the
`svc` user; `loopctl install hello-loop` wrote and armed a timer and its
post-install verification poll passed; and the timer **survived `qm reboot 101`
with no manual intervention**, which is the launchd failure the whole exercise
was about.

New seams, all mirroring `LOOPS_LAUNCHCTL`: `LOOPS_SYSTEMCTL`,
`LOOPS_SYSTEMD_UNIT_DIR`, and `LOOPS_INSTALL_BACKEND` (which lets the macOS
suite exercise the Linux path — `test_loopctl.py`'s fixture pins it to
`launchd`).

---

## 3. The issues found

Each was observed, not inferred. Nothing here has a chosen answer.

### 3.1 `loops.sqlite` is one database for the whole fleet

It holds runs, findings and **dispositions** for every loop. If part of the
fleet runs on llm and part on the guest, that state splits: each host's
dashboard goes half-stale, and a finding dismissed on one host is not suppressed
on the other, because the runner reads its own db. The console and garden are a
single UI.

This is the issue that stopped the cutover. Every other issue below is
per-loop; this one is fleet-wide and answering it probably changes what the
others need.

### 3.2 `kagi-ban` cannot run on Linux at all

Two independent reasons, both verified on the guest:

- `AV_BIN` defaults to `/Applications/Automic Vault.app/Contents/MacOS/av`.
  There is no Linux `av`.
- Its precheck reads the login PATH via `/bin/zsh -l`. Debian does not ship
  zsh, so the precheck exits **127** before the stub `av` is ever reached. That
  is what made three of its tests fail on the guest; they now skip off darwin.

There is also a semantic point, separate from the mechanics: it audits **the
host it runs on**. Moving it would silently change its subject from llm to the
guest without changing anything that says so.

### 3.3 `ads-x` reads `ads.db` twice, and only one read is served

The snapshot-age read is now `GET /api/ads/x-cache`. The **monthly spend ledger**
is a second, separate read: per-row `spend_usd` plus positional decoding of
`raw_json.cells` (`cells[-1]` = TOTAL REMAINING, `cells[-3]` = TOTAL BUDGET, and
`header` is off-by-one against `cells` — the precheck's own comment warns never
to zip them). That decode is not served anywhere.

Context you may not have: the live `gc` process on llm still runs pre-`0f29428`
code, so `/api/ads/x-cache` currently **404s**. It is LaunchAgent
`ai.maguyva.growth-console-dashboard`, serving from the maguyva working tree,
which had another agent's uncommitted changes — so restarting it was left to
that project rather than done from the migration.

### 3.4 `gc-actions` reads two llm-local project trees

`$HOME/projects/digital-marketing-pro/output/maguyva` and
`$HOME/projects/maguyva-marketing/gc-actions`. Both honour env overrides
(`DMP_OUTPUT_DIR`, `GC_ACTIONS_DIR`), so the paths are not hardcoded — but the
trees themselves are on llm.

The migration plan assumed a `command=`-restricted ssh key from the guest back
to llm would cover this and four other loops. That probe script was never
designed or written.

### 3.5 `kagami` needs a credential moved

`gh` (not installed on the guest) plus a PAT at `~/.config/roops-kagami/pat`.
It is the one loop in the fleet with remote mutation — it opens PRs on the
public pages repo. Moving that credential onto a new host was treated as an
operator decision, not an unattended one.

### 3.6 `tailnet-zones` is already broken, and it blocks something outside this repo

Its precheck points at `$HOME/projects/tailnet-setup`. That repo was **renamed
to `sysadmin` on 2026-08-21**, so the path does not exist and the loop has been
precheck-failing since. `TAILNET_SETUP_DIR` overrides it.

`owner=network-system`, so the migration deliberately did not touch it. Worth
knowing: **it is the generator for the zone diagram that `sysadmin`'s planned
`infra` page was going to serve at `/zones/`.** That page is now shipping
architecture-only because of this. Source data is at
`~/projects/sysadmin/tailnet/site/zones-meta.json` (fresh, 2026-08-21); no
rendered zones page exists anywhere yet.

### 3.7 Three loops belong to other agents

`phoneapp-cost-sync` (needs `~/.config/phoneapp/cost-sync.env`),
`flickki-live-tests` (also wants `npm`, absent on the guest) and
`flickki-watchdog`. All three are **untracked in git** — they are another
agent's in-flight work. The migration left them alone and never staged them.

### 3.8 `hello-loop` is installed on the guest right now

`loops-hello-loop.timer`, `daily:09:00` local, running as `svc`. It was left
armed deliberately as the live proof that a systemd install survives a reboot.

With the fleet descoped it is an orphan: it writes to a **scratch** state db on
the guest, it is invisible to the fleet's dashboard on llm, and it spends codex
tokens daily. `loopctl uninstall hello-loop` removes it. The operator has not
said which they want; it is listed here because it is now yours to inherit.

---

## 4. Facts about the guest you will need

`firstparty.home.arpa` / `192.168.1.61`, VMID 101 on pve1.

| | |
|---|---|
| Repo copy | `firstparty:/home/svc/projects/loops`, checked out from a tar, **no `.git`** |
| Runs as | `svc`, uid 1001, linger on. **Not** `admin` — `admin` has passwordless sudo and is the human/deploy account |
| Access | `ssh firstparty-svc` for anything service-related; `ssh firstparty` only for root work |
| Unit files | `~/.config/systemd/user/loops-<name>.{service,timer}` |
| `systemctl --user` over ssh | needs `XDG_RUNTIME_DIR=/run/user/1001` — a non-login shell does not set it |
| Timezone | `Asia/Manila`, changed 2026-08-22. It was `Etc/UTC`, the cloud-image default, which would have fired every schedule 8 hours off |
| `GC_BASE` | `http://192.168.1.52:8787` in `~/projects/loops/.env`. **`llm.home.arpa` has no DNS record** — the plan and the run queue both assumed it did |
| No `rsync` | Minimal Debian. Use tar over ssh |
| `sqlite3` CLI | Installed 2026-08-22 for `tests/runner_test_helpers.sh`. Nothing at runtime needs it |
| Engines | codex 0.149.0, claude 2.1.239, grok 1.0.5 — all authenticated as `svc`. **B-25 rehearsal 2026-08-23:** codex's Linux sandbox needs `bubblewrap` (apt) and the `codex-code-mode-host` companion next to `codex` (0.149+); both were missing and are now installed. Without them every `perm_local_exec=allowlist` loop (the five ads loops) completes but cannot write its action set |
| Not installed | `gh`, `npm`, `node`, `jq`, Docker (Docker is deliberate — group membership is root-equivalent) |

State migration, if it happens, is a **cold** copy: `loops.sqlite` carries the
dispositions, and losing them makes the whole fleet re-nag on settled findings.

---

## 5. Where the rest of the detail lives

- `WARMSTART_SYSTEMD.md` in this repo — what was verified on the guest, the
  full per-loop matrix, and the systemd/launchd differences that are
  load-bearing.
- `~/projects/sysadmin/pve1/PLAN.md` Task 20 — the migration's own record of why
  the cutover did not happen. Tasks 17 and 18 there carry the corrections that
  became the two commits above.
- `~/projects/sysadmin/.claude/SESSION_LOG.md`, entry 2026-08-22 — the narrative.
