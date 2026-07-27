# Loops — selection state

**Scope:** choosing and specifying the LOOPS themselves. Harness internals live in
`docs/HARNESS_PLAN.md` + `docs/INTERFACES.md`; the amendment that added cross-run memory is
`docs/HARNESS_PLAN_AMENDMENT_1.md`. Loops cannot be *built* until `docs/LOOP_AUTHORING.md` exists,
but selection and specification proceed now.

**Status as of 2026-07-22 (generalissimo's review session):** the original 13-candidate table has been
**reset to NO across the board** and rebuilt thematically from generalissimo's own model. What follows
replaces that table.

> **Themes are approved. Individual loop definitions are NOT.** generalissimo's words: *"I want to keep
> thematically, though I'm not sure I'm buying the actual loop defined there — I'll probably want to
> dive into each one."* Treat every row below as an agreed *area of concern*, not an agreed spec.
> Each still needs its own design pass before `loopctl new`.

## generalissimo's model of a loop

Adopted from the reference conversation generalissimo supplied (Grok, "loops in relation to AI vibing"):

- A loop needs only two things: **repetition** and **a stop condition**. ReAct
  (Act→Observe→Reason→Repeat) is one popular pattern, not a requirement.
- Four common categories: **turn-based/execution**, **goal-based/task** (runs until verifiable
  success), **time-based/proactive** (scheduled), and **higher-level product/system** loops.

Two consequences for this project:

1. The harness as designed runs **one engine invocation per launchd firing** — repetition comes
   entirely from the scheduler. That covers time-based/proactive loops only. Goal-based loops
   (iterate-until-success) are explicitly **out of v1**.
2. Human-in-the-loop is *AI proposes → human approves → **repeat***. The original design had no
   repeat arrow: every run was blind to every prior run. Amendment 1 adds it (finding identity,
   dispositions, `PRIOR FINDINGS` injection, runner-side suppression).

## Archetypes — OPEN ISSUE, blocks contract finalisation

The approved slate spans four shapes. The harness contract (`status` + `findings` + dispositions)
models the first well, the second passably, and the last two badly or not at all.

| Archetype | Shape | Fits contract? | Examples below |
|---|---|---|---|
| **Monitor** | Recurring condition; findings have identity, recur, resolve, can be dismissed | Yes — designed for it | P1–P3, S1–S3, H1, H2 |
| **Advisor** | Emits *proposals* for judgement; lifecycle is accept/reject, not fix/resolve | Partially | M1 |
| **Digest** | New content every run *by design*; nothing ever resolves | **No** | F1?, F2 |
| **Pipeline** | Per-work-item state machine; runs until done; **mutates** | **No** | the coding work unit |

**The problem:** a news digest has no finding, nothing resolves, and every run is new by intent.
Its `status` would be permanently `ok`, making its dashboard light meaningless — and Amendment 1
makes `findings[]` a tier-1 field.

**Proposed fix (not yet sent to the architect):** add a `kind` field to `loop.conf`
(`monitor | digest`), make `findings` optional, and let digest loops render as content panels rather
than status lights. This is a one-line change now and a schema migration later.

## Approved themes

| # | Theme / loop | What it covers | Archetype | Cadence | Notes |
|---|---|---|---|---|---|
| **P1** | **Dead-man's switch** | Services self-report heartbeats; the loop reports **who has gone quiet**. Absorbs the flickki cron monitor, generalised scheduled-job liveness, the MOTD-listed services, and externally-run services. | Monitor | ~2×/day | Requires a **push-not-pull change in the projects**: each service emits a heartbeat. Rationale: a dead scheduler cannot report its own death, so absence-of-signal is the only observable failure — but one external observer is enough, and new services register for free. |
| **P2** | **Reachability + diagnosis** | Public sites down → agent diagnoses root cause. | Monitor | 15–30 min | Pinging is a commodity (Better Stack &c. free tiers); the **diagnosis** is the only novel part. Open: build the probe half at all, or consume a service? |
| **P3** | **TLS + domain expiry** | Cert expiry and domain registration across the public domains; warn at 30/14/7 days. | Monitor | daily | generalissimo: *"this is a production check."* Cheap, deterministic, silent, and catastrophic if missed. |
| **S1** | **Pinning drift** | Where projects are **not pinned**. | Monitor | weekly | Reshape of the old dependency loop. |
| **S2** | **Advisories for what we run/maintain** | Issues in the specific packages generalissimo depends on or publishes — *not* blanket `npm audit` transitive noise. | Monitor | weekly | Reshape of the old dependency loop. |
| **S3** | **Auto-update risk** | Things set to update themselves that could bite: Dependabot automerge, unpinned `^` ranges, unattended upgrades. | Monitor | weekly | generalissimo: *"lookout for auto update things that might get us in trouble."* |
| **M1** | **Paid ads check-in** | generalissimo: *"something to check on paid ads I want."* Existing maguyva-marketing `workflows/*.txt` carry explicit `# Frequency:` headers (google/reddit/intl: "every 1-2 days") and are run **by hand today**. | Advisor | ~daily | Theme approved, loop definition not. Heaviest guardrails in the fleet (below). |
| **H1** | **Non-git data at risk** | Git remotes cover repos; this covers what they don't — ticket-takeaway sqlite, local `.env`/config, other unbacked local state. **Report-only for now** (generalissimo's explicit call: *"report for now should be the outcome of this one"*). | Monitor | weekly | Grouped by generalissimo with H2 under "loop control". Note they differ: H1 is about *generalissimo's data*, H2 about *the fleet*. |
| **H2** | **Fleet-noise / dead-loop check** | "In 30 days you acted on 0 findings across loops X, Y, Z" — the honest signal that a loop has become wallpaper. Reads the `dispositions` table. | Monitor | monthly | Nearly free post-Amendment 1. |
| **F1** | **Stock monitoring** | generalissimo: *"ones that monitor stocks."* | **Ambiguous** | TBD | **Open question:** alert-shaped (*"tell me when X moves >5%"* → Monitor, findings resolve) or summary-shaped (*"daily portfolio digest"* → Digest)? Different contracts. |
| **F2** | **News cruise + summarise** | generalissimo: *"cruise for news and summarise it."* | Digest | TBD | generalissimo: *"I guess I'm thinking of those sort of like scripts that run on a crontab — and need to define."* Blocked on the archetype decision above. |

## Parked — the coding work unit

generalissimo is treating all coding-related automation as **one work unit, specified in a single pass**, in
its own session. Do not spec these individually here.

In scope for that unit:

- CI red/stuck sweep (was #6 — `gh run list` across the 8 Actions repos; agreed it should be a
  watchdog, not an agent every tick).
- Security review pass (was #8), secret/data-leak scan (was #9), code-cleaning digest (was #10).
- **The spec-driven development flow.** generalissimo: *"I define the coding spec according to using maybe
  [OpenSpec](https://openspec.dev/) as a guideline, so loops that iterate on the idea to take it
  forward (maybe with human in the loop or waiting for some minimum level of info) but once it's
  been gated through it should be taken forward for development and then testing etc all linking
  back to the spec."*

Notes carried forward for that session (OpenSpec deliberately **not** explored further yet):

- OpenSpec keeps `openspec/specs/` as durable truth plus `openspec/changes/<id>/` holding
  `proposal.md`, `design.md`, `tasks.md` and spec deltas — all checked into git. So **the repo is
  already the pipeline's state store**; harness sqlite is not needed for it.
- **The human-in-the-loop gate already exists: PR review.** Better than inventing an approval channel.
- A clock tick that sweeps `openspec/changes/` and advances anything whose gate is satisfied is
  expressible as a scheduled loop — event-driven infrastructure is not required for v1.
- **The blocker is permissions.** "Development and then testing" means writing code, i.e. the
  auto-mutation axis the plan bans outright. This is the one place report-only genuinely has to
  bend — bounded domain, sandbox, PR-gated.

## Killed

| Was | Why |
|---|---|
| #1 Commit/push hygiene sweep | It's a **chore, not a loop** — 6 repos currently have unpushed commits and no remote. Fix those once; run #2 onward finds nothing. A thin new-repo sentinel may return later. |
| #2 Review-queue digest | A count you already know (69 for-review, 21 stale) is noise. Only a *triage* version would be worth building, and that's a different loop. |
| #11b Global README/marketing consistency | Invented work. (The maguyva brand-facts scrape may survive inside M1.) |
| #12 scrape-wigle refresh | Project dormant since March. |
| #13 Meta loop-opportunity scanner | A type-4 system loop — fits generalissimo's model, but has no data to reason about until the fleet has run for months. Revisit ~6 months after first install. |
| maguyva broken working state (19,874 staged deletions) | Not a loop — a one-off manual todo. Hard-exclude `~/projects/maguyva` from any sweep meanwhile. |

## Environment facts (measured 2026-07-21)

- 68 dirs in `~/projects`; 38 git repos; 24 dirty; 10 with unpushed commits; **6 repos have unpushed
  commits and NO remote** (calorie-counter-simple 43, claude-quality 76, cookingapp 23,
  hermes-email-triage 16, hermes-withmem 6, stuntsclone 4) — single-machine data-loss risk.
- `~/projects/maguyva` is a broken working state (19,874 staged deletions) — **hard-exclude from any
  sweep**.
- Ticket-takeaway SQLite (`~/.claude/ticket-takeaway/`): 69 tickets `for-review` (21 stale >30d —
  ticket-takeaway itself: 21 stuck since 2026-05-17); `scheduled_events` empty.
- maguyva-marketing `workflows/*.txt` carry explicit `# Frequency:` headers (google/reddit/intl ads
  check-ins: "every 1-2 days"; stack-health check: "fine as a periodic sweep"), run by hand today.
- flickki suffered a multi-day **silent** pg_cron outage: the sweep 401'd every tick while
  `cron.job_run_details` reported "succeeded". Truth lived in `internal_job_heartbeats` +
  `net._http_response.status_code`. **This is the origin of P1.**
- Public sites: taskform.pro, flickki.com, maguyva.ai, openclaw.ai/docs.openclaw.ai,
  openbrick.vercel.app, wiki.stunts.hu (+ tailnet-only: translate/househelp/vane/openbrick —
  exclude from public probes).
- 8 repos have GitHub Actions (stocky, Vane, GoodForm, openclaw, syndicate-clone, phoneapp, flickki,
  andrzejsiedlecki.pl); ~23 have package.json; phoneapp's git log is dominated by Dependabot merges.
- GoodForm constraint: Vercel Hobby = crons at most daily (sub-daily in `vercel.json` fails every
  prod build).

## Hard guardrails (embed verbatim in loop prompts AND enforce via `loop.conf` permission axes)

- **maguyva:** kol-scout/kol-research are "ON-DEMAND ONLY, never cron"; CDP ad writes "never cron";
  ad-kill proposals "do NOT auto-apply… get an explicit go" from generalissimo; Reddit publishing human-gated.
- **hermes-email-triage:** dry-run/label-only, never send/reply/delete/archive; live runs gated on
  Gmail OAuth + local model (both unwired).
- **All loops:** report/propose-only — no auto-commit/push/mutation. Enforced by default permission
  axes (`report_only / none / none / none`), never by prompt text. Widening requires explicit
  justification in the loop spec. *(The coding work unit is the one place this may be revisited —
  bounded, sandboxed, PR-gated.)*
- **medusa scanner:** advisory, never gating.
- **Engine-neutral prompts** — no engine-specific tooling; prefer CLI/curl over MCP. Default engine
  codex; claude available; check `codex mcp list` before assuming an MCP server exists there.

## Build process

Each approved loop goes through `docs/LOOP_AUTHORING.md`: spec → `loopctl new` → build →
`loopctl validate` (incl. permission-combo checks) → supervised big-bang run reviewed by generalissimo →
`loopctl install` (verified via real launchd kickstart). Tier-1 contract (schema-enforced
status/headline/metrics/findings) mandatory; tier-2 custom panels + metric metadata declared in
`dashboard.json` per loop.

## Open items

1. **Archetype/`kind` decision** — send the architect the `kind` + optional-`findings` change before
   `contract/contract.schema.json` is finalised. **Time-sensitive.**
2. **F1 shape** — alert-shaped or summary-shaped? Determines its archetype.
3. **P1 prerequisite** — agree the push-not-pull heartbeat change across the services.
4. **P2 scope** — build the probe half, or consume a monitoring service and keep only the diagnosis?
5. **Per-loop design passes** — every row above still needs one; themes are approved, specs are not.
6. **Defects in `docs/INTERFACES.md`** (found 2026-07-22): §4.5 (suppression) and §4.6 (transient
   retries) are referenced from §3, §4.1, §4.3 and §10 but were never written; §9.1's tier-1 shape
   omits the `findings` field that §3/§4.1 depend on.
7. Claude's broken `feedbacks` MCP (hardcoded `/home/user/...` path) — fix or remove when touched.
