# WARMSTART — after the B-25 cutover (read this first, then CLAUDE.md "Fleet state")

Written 2026-08-23 at the end of the session that built B-25 and executed the cutover. This is
the pickup document for the next session: what is live, what the generalissimo still has to do
(one thing: kagami's PAT), and the follow-ups in priority order. Delete this file when §3 is
empty.

## 1. Live state (verified at the end of the session)

| piece | where | how to check |
|---|---|---|
| Fleet: 12 loops armed (five ads, gc-actions, hello-loop, hello-watchdog, kagi-ban, loop-sensei, tailnet-zones, phoneapp-cost-sync) | `firstparty` (`ssh firstparty-svc`), user `svc`, systemd user timers, Manila tz, linger on | `XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user list-timers \| grep loops-` |
| Console | `loops-console.service` on the guest, 127.0.0.1:8929 | `bin/loopctl console status` on the guest |
| Tailnet URL `https://loops.rhino-balance.ts.net/` | minted by the GUEST's caddy-tailscale (`bind tailscale/loops`, default tag:app-private key) → `127.0.0.1:8929`, Host passed through; console-down → static garden **with 502** | open it from a tailnet device (llm is `tag:dmz` and cannot); `sudo journalctl -u caddy-ts` logger `tailscale` |
| LAN read-only view `http://loops.home.arpa/` | guest Caddy, static `dashboard/` + `reports/`, `/api/*` → 404 by design (D7) | `curl -s -o /dev/null -w '%{http_code}' http://loops.home.arpa/` from llm → 200 |
| Zones diagram | `infra.*` `/zones/` (LAN + tailnet) now serves `reports/tailnet-zones/latest.html` from the guest; refreshed by the loop at 08:10 | `curl -H 'Host: infra.home.arpa' http://127.0.0.1/zones/` on the guest |
| llm | data host only: 0 `com.loops.*` jobs; `bin/probe-server` + `probes/` behind the forced-command key (last line of `~/.ssh/authorized_keys`); `.env` has `GC_BASE=http://127.0.0.1:8787` and must NEVER set `LOOPS_PROBE_HOST` | `launchctl list \| grep -c com.loops` → 0; `state/probe-log/<date>.log` is the audit trail |
| Retired on llm | dev-tailnet Caddy `@loops` route + fallback (note left in the Caddyfile), `tailscaled-loops` agent, the interim `com.roops.console-bridge` (plists in `~/.config/dev-tailnet/retired-2026-08-23/`), `~/.claude/workflows/loops-reboot-recovery.txt` (header says RETIRED) | — |
| State | moved cold via `loopctl snapshot`/`restore`: 1522 runs / 92 findings / 2 dispositions / 42 events — identical on both sides at the flip | `bin/db.py query …` on the guest |
| Deploy rule | push from llm, `git pull` on the guest; `probes/` / `bin/probe*` must land on BOTH before a loop using them is installed (`loopctl probe status` shows drift; `loopctl requirements` treats drift as unmet) | both checkouts at `git rev-parse HEAD` |

Design + council review: `openspec/changes/b-25-linux-port-2026-08-23/` (`design.md`, `review.md`,
`tasks.md`). Runbook that was executed: `workflows/firstparty-cutover.txt`. Ticket B-25 is in
For Review on the loops board; I-01 (dead-man alarm) is in Ideas.

## 2. The one thing waiting on the generalissimo — kagami's PAT (then I install kagami)

kagami is installed **nowhere** right now (it was uninstalled on llm with the rest; it needs
GitHub write access to do its job: clone `ytubecoder/ytubecoder.github.io`, push branch
`roops/mock-garden-refresh`, open/update ONE PR via `gh pr` when the public mock garden drifts).
The precheck prefers a PAT file and uses it for both `gh` (`GH_TOKEN`) and `git` (x-access-token),
so the narrowest grant is:

1. GitHub → Settings → Developer settings → **Fine-grained personal access tokens** → Generate.
   - Resource owner: `ytubecoder`. Repository access: **Only select repositories →
     `ytubecoder/ytubecoder.github.io`** (nothing else).
   - Repository permissions: **Contents: Read and write**, **Pull requests: Read and write**
     (Metadata: Read is added automatically). No account permissions. Expiry: your call (the
     loop will start failing with `auth-failed`/`gh pr` errors when it lapses — visible on the garden).
2. Put it on the guest (the directory already exists, mode 700): from llm run
   `ssh firstparty-svc 'umask 077; cat > ~/.config/roops-kagami/pat'`, paste the token, Ctrl-D.
   Then `ssh firstparty-svc 'wc -c ~/.config/roops-kagami/pat; ls -l ~/.config/roops-kagami/pat'`
   (expect ~90+ bytes, `-rw-------`).
3. Tell the next session "kagami PAT is in place" — it will `git pull` on the guest, run
   `bin/loopctl requirements kagami` (add `file:~/.config/roops-kagami/pat` to kagami's
   `requires=` first, commit, pull), then `LOOPCTL_INSTALL_POLL_TIMEOUT_S=600 bin/loopctl install
   kagami`. The install's self-verify run may open/update the mirror PR — that is its job.

**Fast-follow you asked for:** "make the gh process go through the UI like a real app" — i.e. a
GitHub OAuth device-flow / App install from the garden console instead of a hand-pasted PAT.
Filed as a follow-up below (§3, item 2); it needs an INTERFACES §13 amendment (the console
would hold a credential for the first time) and should be its own B-ticket.

## 3. Follow-ups, in order

1. **Install kagami** after §2 (one command, described there).
2. **Ticket: GitHub auth for kagami via the console UI** (device flow / GitHub App), replacing the
   PAT file — feature-scale, OpenSpec; touches INTERFACES §13 and the B-23 "no secrets in the
   repo/garden" rules.
3. **Monitoring audit input:** `docs/MONITORING_NOTES.md` (signals that already exist, the
   `action_set.written=0` blind spot, I-01's shape). Read it into the cross-service audit.
4. **gc portability input:** `~/projects/maguyva-marketing/growth-console/PORTABILITY_NOTES_FROM_LOOPS_B25.md`
   (how the guest consumes gc, the two `ads.db`/opentwins reads served by probes, what can't
   leave llm, the `0.0.0.0:8787` note). Hand the file name to the gc agent.
5. **Index `ytubecoder/roops` in maguyva** (operator; the B-25 audit had to run locally).
6. **DNS:** `loops.home.arpa` exists (→ guest). `llm.home.arpa` still does not; `GC_BASE` stays
   the IP on the guest until it does (then change one line in the guest's `.env`).
7. **phoneapp-cost-sync** runs on the guest but its loop tree is still **untracked** (owner's
   call to commit; the repo is public — the tree references the credential file only by path).
8. **flickki-live-tests / flickki-watchdog**: decommissioned by their owner 2026-08-08; not on the
   guest; `enabled=false` on llm. Leave.
9. **Engine env allowlist** (`env -i` for the adapter) — deferred in design §3; today `.env`
   keys are stripped, everything else inherited.
10. **Dashboard "next run" is computed in UTC** while schedules are local (pre-existing, both
    hosts) — small fix in `dashboard/generate.py:406-445`.
11. Housekeeping on llm: ~8 idle `grok --cwd …/maguyva-marketing/…/fakehome` TUI processes from
    2026-08-15 are still resident (another session's); kill if unwanted.

## 4. Gotchas this session paid for (already in CLAUDE.md / SESSION_LOG — repeated here for pickup)

- codex's Linux sandbox needs `bubblewrap` + `codex-code-mode-host` (0.149+); without them
  `perm_local_exec=allowlist` loops show `completed` but cannot write action sets.
- `loopctl` resolves its root from `LOOPS_ROOT`/`--root`, never from its own location — with two
  checkouts on one host always pass `--root`.
- `peon merge` refuses while the other agents' untracked `loops.d/*` dirs exist → `git stash
  push -u` around it; a hand-resolved merge leaves `PEON_REPORT.md` tracked → `git rm` it before
  the next dispatch.
- Appending to a `loop.conf` without a trailing newline glues the new key onto `notes=`.
- Never `header_up Host` on any proxy in front of the console (it is the §13.1 credential).
