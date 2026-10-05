# drip-scheduler — SPEC

**Owner:** maguyva-marketing · **Type:** watchdog · **Schedule:** interval:15m · **Engine:** codex (write-ups only)

## Purpose
The always-on clock for maguyva drip campaigns. Campaigns are created, edited and
switched on/off by Generalissimo in the GC Drips tab (`/drips`); this loop makes
the switched-on ones actually run: each tick reconciles mailbox@ and sends the
emails whose delay has elapsed.

## Mechanism
`precheck.sh` → `bin/probe drip-tick` (forced-command ssh to llm) →
`growth-console/.venv/bin/python -m console.dashboard.drip_scheduler tick --json`.
All decisions live in growth-console's `drip_scheduler.tick()`; the probe and the
precheck add no logic. Spec: maguyva-marketing `openspec/changes/drip-campaigns/`.

## Permission grant (why this loop may send)
Narrow amendment to "loops never send", granted by Generalissimo 2026-10-05: a
scheduled job may send drip emails ONLY for campaigns he turned ON in GC. Gates
(all in growth-console): on/off + on-version pin (an edit turns a campaign off),
`DRIP_SEND_ENABLED` kill switch in the repo `.env`, single-run fcntl lock, canary
of 5 per email held until a reconcile, auto-off on canary hard bounce or a ≥3%
bounce tick, one send per (enrollment, email).

## Outputs
- Quiet tick → exit 0 → green heartbeat, zero tokens.
- Problem (send failures, auto-off, tick error, transport) → exit 1 → codex
  writes findings. Everything is also visible on GC `/drips` (activity log).

## Kill switches
1. Turn the campaign off in GC `/drips`.
2. `DRIP_SEND_ENABLED=0` in `maguyva-marketing/.env` (ticks still reconcile).
3. `loopctl set-schedule drip-scheduler manual` (stops the clock).

## Deploy
Probe changes land on BOTH hosts (push from llm, `git pull` on firstparty) before
install; `loopctl probe status` shows drift. Install on firstparty only.
