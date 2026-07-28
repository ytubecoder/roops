# Open threads — warmstart for next session

> **Ephemeral.** Only things NOT done. Delete sections as they resolve; delete the file when empty.
> Facts below were verified in-environment 2026-07-22..27 — re-verify anything load-bearing before building on it.
> Standing process rule from generalissimo: **"check what I'm saying instead of me check what you're saying"** — verify his claims against the environment first; when he's right say so, when you were wrong say so plainly.

## 1. MAIN THREAD — approve→action bridge (design open, awaiting generalissimo's answer)

His pattern: the system computes as if autonomous but pauses at each gate; approvals accumulate until "you've approved this N times — set standing approval?"; over time it becomes *more* autonomous. Scaled-down v1 he asked for: **when he approves, the recommendation is marked approved and recorded durably, so a later action loop can create actions from it. The latter loops don't exist yet; this stage must "do its share properly."**

Verified facts:
- `loops.d/ads-google` is BUILT, validated, supervised-run only — **not installed**. `engine=claude`, `daily:18:00`, report_only floor.
- Its findings **ARE actions**, not descriptions: `finding_id = ads-google:ADG-NN`, per-action briefs with `order.*` blocks (full placement ids), `resolution_evidence`, `status: open|struck`, ids never reused after strike. Emitted by `bin/emit_action_set.py` (+ register + `context.json`).
- `loopctl ack` writes a disposition meaning **"stop nagging" — NOT "recommendation sustained."** Do not overload `ack`; approval needs its own verb/record.
- DMP plugin already has the actions/execution layer (`execute-action.md`: dry-run → `--execute` → `--execute --confirm`, audit log under `~/.claude-marketing/brands/{brand}/executions/`). **BUT Google Ads is OAuth-only there** — `execute_blocked_reason: "use MCP path"`; `connector_executor.py` cannot fire it. The first action loop would need the Claude+MCP path.
- **Two id spaces, no mapping**: ADG-NN (loop-local, per-run) vs DMP's named registry actions (via `connector_resolver`). This is the missing bridge.
- Guardrails still in force (verbatim): *"No scheduled process ever calls `service.record_and_apply()` (or any network write API…)"*; CDP is never cron'd; ad-kill proposals need an explicit go; loops never git-write maguyva-marketing or edit runbooks. An action loop that auto-applies approvals would violate these — widening is generalissimo's explicit amendment, never inherited.
- **Before designing the bridge, read `docs/ads-actions-loops-warmstart.md` in `~/projects/maguyva-marketing`** (it is NOT in loops/docs) — authoritative build-order for the five per-network ads loops (google → intl → reddit → x → program); may already specify the handoff.

**Open question put to him, unanswered:** should approval live in the loops harness (new disposition verb, e.g. `approve`) or in GC, where ads-google's SPEC already says decisions get recorded ("applies/declines via `record_and_apply` + runbook")? If GC, the harness only needs the ack≠approval distinction made explicit.

## 2. Approval counting / standing approval

Wanted regardless of #1's answer: count approvals per `finding_id`; at some threshold surface "approved N× — set approval status for next time?". Standing approval is a **recorded status only** — report/propose-only stays until explicitly widened.

## 3. Relay to architect — INTERFACES.md defects (RE-VERIFIED 2026-07-28: stale, one left)

- ~~§4.5 / §4.6 never written~~ — both exist (INTERFACES.md:324, :343). §4.5 is the
  load-bearing one: **non-empty `findings` overrides the declared `status` with the max
  finding severity; empty `findings` lets `status` through.**
- ~~§9.1 omits `findings`~~ — present, with a "required but MAY be empty" bullet.
- **Remaining (cosmetic):** §4 sections run 4.1, 4.2, 4.3, **4.5, 4.6, 4.4** — §4.4
  (Redaction pass) sits at line 362, after §4.6.
- **New (minor, harness):** `cmd_status` (`bin/loopctl:745`) does `last-runs LIMIT 1`
  ordered by `started_at DESC` with **no filter on `runner_status`**, so a `started` or
  `skipped-overlap` row — both NULL headline/effective_status — blanks the status display
  even when a completed run sits right behind it. Observed live 2026-07-28. Not fixed
  (harness internals are frozen).

## 3b. Ads loops status + next steps → `docs/ADS_LOOPS_FOLLOWUP_WARMSTART.md`

The three open ads-loop issues (every second run dies · nothing installed under launchd ·
`/schedules` lists the legacy check-in rows twice), the acceptance bar, and the next build
all live in that file, each with its re-check command. Work from it, not from here.

**Two corrections to what this file said earlier on 2026-07-28 — both were mine, both wrong:**

- ~~"where may an action id come from" is PAUSED~~ — **settled.** Actions come *strictly*
  from campaign report recommendations (the DMP/CRO pattern); infrastructure problems are
  run status only and mint no id. All five prompts still violate this at `prompt.md:44`
  and :161 — removing it is approved work, not an open question.
- ~~"four sibling loops nobody asked for"~~ — **wrong framing.** All-network checks were
  always the intent; only the *sequencing* (they landed before the console work) and the
  unrequested commits were out of line. The clones produce real, network-specific output —
  see the follow-up doc's table. What IS true: ads-google carries three fixes (authoritative
  metrics, high-water ids, zero-findings failure protocol) that the four siblings do not.

Repo hygiene, still true: subagents made 4 commits here (`7f9e2fa`, `ede268f`, `dc716e9`,
`a2c0128`) and 22 in `maguyva-marketing` despite "run no git commands" briefs. Nothing is
pushed; this repo has no remote.

## 4. Loop-selection leftovers (themes approved in LOOPS_WARMSTART.md; individual definitions NOT)

- Per-loop design pass still owed for every approved theme: P1 P2 P3 S1 S2 S3 M1 H1 H2 F1 F2.
- **Archetype decision (time-sensitive):** add `kind: monitor|digest` to loop.conf with `findings` optional for digests — the current contract models Digest badly and Pipeline not at all. Needed before F1/F2.
- F1 stocks: alert-shaped (Monitor) or summary-shaped (Digest)? Unanswered.
- P1 dead-man's switch prerequisite: agree the push-not-pull heartbeat change across services first.
- P2 reachability: build the probe half ourselves, or consume a monitoring service and keep only diagnosis?
- **Ticket consolidation loop** (he asked for it): proposed cross-project triage where killing tickets is half the output; ticket IDs give free finding identity → best pilot candidate. Lead question never answered: is the report "what do I work on next" or "what should I stop pretending I'll do"?
- Project-specific coding loops: proposed generic loop + per-repo `.loops/checks.md` overlay (push-not-pull). Not yet accepted.
- Parked for its own session: the coding work unit (CI/security/secrets/quality + OpenSpec pipeline) — one unit, don't spec loops individually. Background only, do not reopen: paperclip/hermes/openclaw as alternative host.

## Machine infra (not loops, but blocks nothing — has its own warmstart)
- **Caddy consolidation + launchd rename:** `~/.config/dev-tailnet/WARMSTART_CADDY_CLEANUP.md`
  — **A0 and Job B DONE 2026-07-28** (status log there has the evidence). The localhost:2019
  admin collision is gone (vane's caddy moved to :2029, so `caddy reload --address
  localhost:2019` now deterministically hits the dev-tailnet instance), and all 16 launchd
  labels are `com.generalissimo.dev-tailnet.*` (owner name in anything new: **generalissimo**).
  **Job A1 (migrate vane, retire `~/caddy-tailscale`) is BLOCKED — "dont touch vane at all"
  (generalissimo, 2026-07-28).** There are **two vanes and they must never be consolidated**:
  `vane` = 100.69.211.49 on **llm** (tsnet node fronting `~/projects/Vane` on :8347) and
  `vane-mm` = 100.71.78.96 on **mm**. A1's "delete the old vane machine" step sits next to a
  `vane-mm` row in the same admin console — match on IP, never the name, and only with a fresh
  explicit go. Nothing actionable remains here; delete this section whenever you like.
