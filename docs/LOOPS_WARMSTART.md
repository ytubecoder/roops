# Loops Warmstart — candidate selection & evidence (from 2026-07-21 analysis session)

**Scope of this file:** choosing, specifying, and discussing the LOOPS themselves — nothing about harness internals. The harness design/build plan is `~/projects/loops/docs/HARNESS_PLAN.md` (build contract/process will land as `docs/LOOP_AUTHORING.md` once the harness is built — loops cannot be built until then, but selection/specification can start now). Generalissimo reviews candidates as: yes/no/comment per row; recommendations pre-filled so silence = accept.

**Environment facts (measured 2026-07-21):**
- 68 dirs in `~/projects`; 38 git repos; 24 dirty; 10 with unpushed commits; **6 repos have unpushed commits and NO remote** (calorie-counter-simple 43, claude-quality 76, cookingapp 23, hermes-email-triage 16, hermes-withmem 6, stuntsclone 4) — single-machine data-loss risk.
- `~/projects/maguyva` is a broken working state (19,874 staged deletions) — **hard-exclude from any sweep; flag once for manual attention**.
- Ticket-takeaway SQLite (`~/.claude/ticket-takeaway/`): 69 tickets `for-review` (21 stale >30d — ticket-takeaway project itself: 21 stuck since 2026-05-17); `scheduled_events` empty.
- maguyva-marketing `workflows/*.txt` carry explicit `# Frequency:` headers (google/reddit/intl ads check-ins: "every 1-2 days"; stack-health check: "fine as a periodic sweep") and are run by hand today.
- flickki suffered a multi-day silent pg_cron outage: sweep 401'd every tick while `cron.job_run_details` showed "succeeded"; truth lives in `internal_job_heartbeats` + `net._http_response.status_code`.
- Public sites: taskform.pro, flickki.com, maguyva.ai, openclaw.ai/docs.openclaw.ai, openbrick.vercel.app, wiki.stunts.hu (+ tailnet-only services: translate/househelp/vane/openbrick — exclude from public probes).
- 8 repos have GitHub Actions (stocky, Vane, GoodForm, openclaw, syndicate-clone, phoneapp, flickki, andrzejsiedlecki.pl); ~23 have package.json; phoneapp git log is dominated by Dependabot merges.
- GoodForm constraint: Vercel Hobby = crons at most daily (sub-daily in vercel.json fails every prod build).

**Candidate table (rec pre-filled; Generalissimo yes/no/comments):**

| # | Loop | Scope | Type | Cadence | Rec |
|---|------|-------|------|---------|-----|
| 1 | Commit/push hygiene sweep (dirty/unpushed/no-remote; report-only; exclude maguyva) | global | script→agent | daily | YES |
| 2 | Review-queue digest (ticket DB by age; suggest `/review-tickets` target) | global | script→agent | weekly | YES |
| 3 | maguyva ads check-ins per project workflows + weekly stack-health sweep (propose-only) | maguyva-marketing | agent | daily | YES |
| 4 | flickki cron-heartbeat monitor (heartbeats + http status; never trust job_run_details) | flickki | script→agent | 2×/day | YES |
| 5 | Prod uptime watchdog → agent diagnosis (public sites + tailnet, silent-green) | global | watchdog | 15–30 min | YES |
| 6 | CI stuck/red sweep (gh across 8 Actions repos; red/stuck >24h) | global | script→agent | daily | YES |
| 7 | Dependency/supply-chain check (npm outdated+audit, pin drift, Dependabot triage) | global | script→agent | weekly | YES |
| 8 | Security review pass (changed code only; medusa advisory-only) | global | agent | weekly | YES |
| 9 | Data-leak/hygiene scan (secrets, committed .env, keys) | global | script→agent | weekly | YES |
| 10 | Code-cleaning digest (`PYTHONPATH=$HOME/projects/claude-quality python3 -m quality.cli scan`) | global | script→agent | monthly | YES |
| 11 | README/marketing copy consistency (maguyva docs mandate weekly brand-facts scrape of maguyva.ai) | global + maguyva | agent | monthly (maguyva weekly) | YES |
| 12 | Stale-data refresh (scrape-wigle designed for daily incremental, 100 req/day budget) | scrape-wigle | script | daily | NO — dormant since March |
| 13 | Meta loop-opportunity scanner (scan workflows/frequency headers, repeated routines; proposals only) | global | agent | monthly | YES |

**Hard guardrails (from project docs — embed verbatim in loop prompts AND enforce via loop.conf permission axes):**
- maguyva: kol-scout/kol-research "ON-DEMAND ONLY, never cron"; CDP ad writes "never cron"; ad-kill proposals "do NOT auto-apply… get an explicit go" from Generalissimo; Reddit publishing human-gated.
- hermes-email-triage: dry-run/label-only, never send/reply/delete/archive; live runs gated on Gmail OAuth + local model (both unwired).
- All loops: report/propose-only — no auto-commit/push/mutation (default permission axes enforce this; widening requires explicit justification in the loop spec).
- medusa scanner: advisory, never gating.
- Engine-neutral prompts (no engine-specific tooling; prefer CLI/curl over MCP). Default engine codex; claude available; check `codex mcp list` before assuming an MCP server exists there.

**Build process:** each YES loop goes through `docs/LOOP_AUTHORING.md`: spec → `loopctl new` → build → `loopctl validate` (incl. permission-combo checks) → supervised big-bang run reviewed by Generalissimo → `loopctl install` (verified via real launchd kickstart). Tier-1 contract (schema-enforced status/headline/metrics) mandatory; tier-2 custom panels + metric metadata declared in `dashboard.json` per loop.

**Open items for this context:** Generalissimo's yes/no/comments on the table; cadence tuning; loop #12 revival question; whether #7 folds Dependabot triage in or splits it; claude's broken `feedbacks` MCP (hardcoded `/home/user/...` path) — fix or remove when touched.
