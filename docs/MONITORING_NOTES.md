# Monitoring notes — what the loops fleet could tell a monitoring/notification audit

Written 2026-08-23 after the B-25 cutover, for the planned cross-service monitoring audit.
Suggestions, not commitments. Each row says where the signal already exists so the audit can
decide push vs pull without new instrumentation. Ticket I-01 (fleet dead-man alarm) on the loops
board is the one concrete proposal; everything else here is inventory.

## 1. Signals that exist today (no new code to read them)

| signal | source | today's surface | note |
|---|---|---|---|
| a loop missed its firing | `state/loops.sqlite` `runs.started_at` vs `schedule` → `expected_interval_s` (+ grace) | garden "stale" badge (pull only) | the Aug 7–10 outage: everything stale, nobody looked. I-01 turns this into a push |
| a run failed | `runs.runner_status` ∉ {completed, skipped-precheck, skipped-overlap} | garden red light + `loop-sensei` daily diagnosis | `harness-error`/`auth-failed`/`tool-denied` mean "fix the harness", not "the loop found something" (INTERFACES §4.3) |
| a run died mid-flight | `runs.finished_at IS NULL` older than `timeout_s + 120s` | garden "died" (derived) | power loss / SIGKILL |
| the engine could not execute (sandbox) | `status_reason=action_set_invalid` + `metrics.action_set.written=0` while `runner_status=completed` | **invisible** today — the run shows green-ish | exactly what hid the missing `bubblewrap`/`codex-code-mode-host` on the guest; worth a metric-threshold alert on `action_set.written=0` for the ads loops |
| engine auth expired | `runner_status=auth-failed` | garden red | codex file auth (`~/.codex`) vs claude keychain/`~/.claude` differ per host |
| token spend | `runs.tokens_*`, `cost_usd`, `usage.json` | garden 7-day spend | a per-day ceiling would catch a loop stuck in retries |
| probe channel down (guest→llm) | precheck prints `probe transport failed (llm unreachable)` and `loopctl probe status` ≠ 0; server side `state/probe-log/<date>.log` on llm | digest text only | `exit 75` is the distinct code; a `ping` every N minutes from the guest is the cheapest liveness check of llm's sshd + key |
| probe drift (two checkouts) | `loopctl probe status` (hash per probe) / `loopctl requirements` unmet `probe:` | CLI only | happens after any `git push` that is not pulled on the other host |
| console down | `loopctl console status` exit ≠ 0; tailnet URL serves the static garden with **502** (deliberate) | HTTP status | the 502 is the machine-detectable signal — do not "fix" it to 200 |
| timers not armed / linger off / tz drift | `systemctl --user list-timers`, `loginctl show-user svc -p Linger`, `/etc/localtime` vs `LOOPS_EXPECT_TZ` | `loopctl install` refuses; nothing checks afterwards | a reboot that loses linger would silently disarm everything — the one residual reboot risk |
| guest itself down | nothing on the guest can say so | — | needs the cross-host half of I-01: llm (or any tailnet device) probing the guest |
| gc unreachable from the guest | ads prechecks print `inputs: … MISSING` and `inputs.missing` metric | garden amber/red via the loop's own findings | `GC_BASE` is an IP; no DNS for `llm.home.arpa` |
| disk / retention | `state/runs` count (1509 dirs, ~90 MB at cutover), `retention_days` per loop | none | `reports/` + `state/runs` pruning runs at the end of each run; a stuck fleet stops pruning too |
| tailnet node (loops) offline | Caddy journal on the guest (`journalctl -u caddy-ts`, logger `tailscale`); tailnet admin | none | node key expiry = the URL dies while everything else is healthy; the LAN `http://loops.home.arpa` read-only view is the fallback |

## 2. Shapes that fit this fleet

- **Push, not pull, and one channel.** Every signal above is already computed; what is missing is
  a sender. One `loops-deadman.timer` (I-01) on the guest that evaluates rows 1–4 and 9 and sends
  ONE deduped message per loop per day is enough for the fleet; it must run from a scheduler the
  fleet does not share a failure mode with (a systemd timer on the guest qualifies; the cross-host
  probe from llm covers the guest).
- **Treat `completed` + "could not write" as a failure.** Row 4 is the lesson of this cutover: the
  per-loop contract's `metrics` carry `action_set.written`; the dashboard's existing metric
  thresholds (INTERFACES §9.3) can alert on it without engine changes.
- **Probe channel liveness is a two-host property.** `loopctl probe status` exit code on the guest
  is the single check; the server log on llm is the audit trail, not the alarm.
- **The open design thread still applies:** `docs/LOOP_SELECTION.md` Open items — "P1 dead-man's
  switch prerequisite: agree the push-not-pull heartbeat change across services first." The audit
  is the place to settle that.

## 3. What not to monitor

- Individual findings (they have their own disposition flow: ack/dismiss/snooze).
- The 502-on-fallback status as an error in its own right (it IS the console-down signal).
- Launchd on llm — nothing of the fleet runs there any more; only `sshd` for the probe key.
