# WARMSTART — the fleet on Linux (systemd)

Written 2026-08-22 by the `pve1` migration, Task 20 of
`~/projects/sysadmin/pve1/PLAN.md`. Read this before installing any loop on
`firstparty`, and before assuming the fleet has moved. **It has not. The
mechanism is proven; the cutover is not done.**

## 1. What is proven, on the real guest

| Claim | Evidence, 2026-08-22 |
|---|---|
| The harness runs on Linux | Full suite green on `firstparty` (Debian 13, python 3.13.5): 786 python tests + every shell fixture, exit 0 |
| `install` writes and arms a systemd timer | `loopctl install hello-loop` → `installed hello-loop -> ~/.config/systemd/user/loops-hello-loop.timer` |
| The install self-verification still works | Its post-install poll found a fresh non-failed run, same as launchd's kickstart path (§8.1 step 5) |
| The engine is authenticated as `svc` | `loopctl run hello-loop` completed: `runner_status=completed`, `warn`, "2 of 3 world files have an open TODO" — the fixture's expected result |
| **Installs survive a reboot** | `qm reboot 101`; after boot, `uptime -p` = "up 0 minutes" and `list-timers` still shows the timer **active**, next run 09:00, with **no manual intervention** |
| Schedules fire in the right timezone | The timer reads `Sat 2026-08-22 09:00:00 PST` for `schedule=daily:09:00` |

The reboot result is the whole point of the migration. Under launchd, `install`
= `launchctl bootstrap` with nothing in `~/Library/LaunchAgents`, so a reboot
silently killed the entire fleet and nothing alarmed — found by hand three days
later (7 Aug → 10 Aug). Under systemd the units live in the user unit dir, the
`timers.target.wants` symlink persists, `loginctl enable-linger svc` is on, and
`Persistent=true` catches up a calendar firing missed while the host was down.

**`~/.claude/workflows/loops-reboot-recovery.txt` does not apply to the Linux
fleet.** Leave it in place for as long as any loop still runs on `llm`.

## 2. Set the timezone before installing anything

`firstparty` was `Etc/UTC`, the Debian cloud image default. `llm` and `pve1` are
both `Asia/Manila`. §5.1 schedule times are **local** in launchd and systemd
alike, so the fleet's 18:00–19:40 Manila stagger would have fired at
02:00–03:40. Set 2026-08-22:

```bash
sudo timedatectl set-timezone Asia/Manila
```

Do this before any install. A fleet installed first needs every timer re-armed.

## 3. Where things are

| | |
|---|---|
| Repo | `firstparty:/home/svc/projects/loops` (copied with tar over ssh — **the guest has no `rsync`**) |
| Runs as | `svc`, uid 1001, linger on. **Not** `admin` |
| Unit files | `~/.config/systemd/user/loops-<name>.{service,timer}` |
| `systemctl --user` over ssh | needs `XDG_RUNTIME_DIR=/run/user/1001` in a non-login shell |
| `GC_BASE` | `http://192.168.1.52:8787` in `~/projects/loops/.env` |
| State | **not migrated.** The live `loops.sqlite` is still on `llm` |

**`llm.home.arpa` does not exist.** The plan says `GC_BASE=http://llm.home.arpa:8787`;
there is no such DNS record (the eight `home.arpa` records do not include `llm`),
so the IP is used. Adding the record on the MikroTik is an operator action.

`/api/ads/x-cache` (Task 19) returns **404** from the guest, because the live
`gc` process on `llm` still runs the pre-Task-19 code. It is LaunchAgent
`ai.maguyva.growth-console-dashboard`, serving from the maguyva working tree —
which currently holds another agent's uncommitted changes, so restarting it is
that agent's call, not the migration's. `/api/ads/scoreboard` and
`/api/ads/journal` both answer 200 from the guest already.

## 4. Which loops can actually move

This is the reason the cutover did not happen. Task 20 assumed the fleet was
host-portable. It is not.

| Loop | Moves? | What stops it |
|---|---|---|
| `ads-google`, `ads-intl`, `ads-program`, `ads-reddit` | ✅ | Nothing. `GC_BASE` only |
| `hello-loop`, `hello-watchdog` | ✅ | Nothing. Pilot fixtures |
| `loop-sensei` | ✅ | Nothing — but it examines the fleet, so it belongs wherever the fleet ends up |
| `ads-x` | ⚠️ | Reads `ads.db` **twice**. Task 19 serves the snapshot age; the monthly spend ledger (per-row `spend_usd` + positional `raw_json.cells`) is still a direct file read |
| `gc-actions` | ⚠️ | Reads two `llm`-local trees: `$HOME/projects/digital-marketing-pro/output/maguyva` and `$HOME/projects/maguyva-marketing/gc-actions`. Both honour env overrides, so an ssh probe or a mount is needed |
| `kagami` | ⛔ | Needs `gh` (not installed) **and** a GitHub PAT at `~/.config/roops-kagami/pat`. Moving a credential is an operator decision. It also opens PRs — the one loop in the fleet with remote mutation |
| `kagi-ban` | ⛔ | **Architecturally cannot move.** `av` is a macOS `.app`, and the precheck reads the login PATH via `/bin/zsh -l`, which Debian does not ship (exit 127). It also audits *the host it runs on* — moving it would silently change its subject from `llm` to `firstparty` |
| `tailnet-zones` | ⛔ | Already broken, and not this project's to fix: its precheck points at `$HOME/projects/tailnet-setup`, which was renamed to `sysadmin` on 2026-08-21. `owner=network-system` |
| `phoneapp-cost-sync` | ⛔ | Needs `~/.config/phoneapp/cost-sync.env`, a credential. Another agent's loop, untracked in git |
| `flickki-live-tests`, `flickki-watchdog` | ⛔ | Another agent's in-flight loops, untracked, never installed on `llm`. `flickki-live-tests` also needs `npm` |

So 7 of 13 installed loops are cleanly portable, 2 need work, and 4 cannot move
as they stand.

## 5. The decision the cutover needs first

**Do not move part of the fleet without answering this: where does
`loops.sqlite` live?**

It is one database holding runs, findings and **dispositions** for every loop.
Splitting the fleet across two hosts splits that state:

- Each host's dashboard shows stale rows for the other host's loops.
- Dismissed and snoozed findings are suppressed by the runner reading *its own*
  db, so a finding settled on one host re-nags from the other.
- The console/garden is a single UI. There would be two, each half-right.

Three ways out, none of them free:

1. **Move everything.** Needs the four ⛔ loops resolved — a PAT decision for
   `kagami`, and accepting that `kagi-ban` cannot follow.
2. **Move nothing yet.** Finish `ads-x` and `gc-actions`, decide `kagami`, then
   cut over in one step. Costs nothing while it waits: the fleet works today.
3. **Split deliberately**, with `kagi-ban` (and anything else macOS-bound)
   staying on `llm` as a second, one-loop fleet with its own db, and accept two
   dashboards.

Option 2 is the cheapest to reverse and the easiest to explain. Nothing here
picks one — that is the operator's call.

## 6. What is running on the guest right now

`hello-loop` only, installed and armed at `daily:09:00`, left deliberately as
the live proof of reboot persistence. It is the pilot fixture: report-only
floor, `perm_network=none`, and it reads a bundled `world/` directory. Remove it
with `loopctl uninstall hello-loop` if the noise or the codex tokens are not
wanted.

The state db on the guest is a **scratch** db created by that run. It will be
overwritten at cutover by the real one from `llm`, which must be copied cold —
`loops.sqlite` carries the findings dispositions, and losing them makes the
whole fleet re-nag on settled findings.

## 7. Follow-ups for this repo's own agent

- The suite is now host-independent. `LOOPS_INSTALL_BACKEND` pins a backend;
  `test_loopctl.py`'s fixture forces `launchd`, and `test_loopctl_systemd.py`
  covers the systemd path. `test_kagi_ban.py`'s precheck tests skip off darwin.
- `sqlite3` had to be installed on the guest for the suite —
  `tests/runner_test_helpers.sh` shells out to the CLI. Nothing at runtime does.
- OpenSpec archival, if wanted, for B-24.
