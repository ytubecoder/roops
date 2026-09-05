# SPEC — gc-actions

Twelve-question intake per `docs/LOOP_AUTHORING.md` §2. Origin: Generalissimo's
approved Q2 design, 2026-08-12 (spec maguyva-marketing/docs/superpowers/specs/2026-07-25-dmp-actions-pipeline-design.md
+ the lavish approval artifact `.lavish/action-ticket-approval.html`). This loop
automates the 2026-08-11/12 manual collation of the DMP (/dmp) + CRO (/cro)
action registers into ticket-takeaway tickets.

## 1. Purpose & stop condition

Keep the GC action registers and the `maguyva-actions` ticket board reconciled:
every open register action is either covered (mapped/ticketed/folded/struck/
deliberately-deferred) or surfaced as a proposal ticket in the board's Ideas
section for Generalissimo's triage.

Per-firing "done": the digest was judged, findings emitted, and (post-promotion)
any `create_ticket` ops applied idempotently. Cross-run "done": a finding
resolves (`resolved_at` set) when its action id gains coverage — a map row, a
board ticket, or a register strike; the loop goes quiet (`ok`, zero findings)
when all open actions are covered.

## 2. Agentic pattern

Human-in-the-loop outer shape (the v1 architecture): the loop proposes, and
Generalissimo is the repeat mechanism — he triages Ideas tickets on the board
and disposes findings via `loopctl ack/dismiss/snooze`. Inside one invocation:
trivial single-shot interpretation — precheck has already computed every set
difference; the engine composes proposals and judges conflicts.

**Recorded v2 aspiration (not built):** judging whether an action's real-world
definition-of-done is now MET (with evidence) — e.g. probing whether a page
shipped or a number changed. That needs evidence probes in precheck (curl of
maguyva.ai surfaces, GC API reads) that v1 deliberately omits. v1 never claims
done-ness; it only reports coverage gaps and data conflicts.

## 3. Type & data flow

`type=agent`. Deterministic side (`precheck.sh`, unsandboxed, read-only):
enumerates every run dir under the DMP output root
(`~/projects/digital-marketing-pro/output/maguyva`), parses `register.yaml`
(preferred) or `ACTIONS.md` (both generated `~~struck~~` and hand-written
`- **Struck** bullet` conventions), newest run dir wins per action id (date +
HHMM ordering, mirroring `console/runnames.py`); parses the action↔ticket map
(`maguyva-marketing/gc-actions/action-ticket-map.yaml`, disposition-aware —
`uncovered` rows do NOT count as coverage) and the board file
(`gc-actions/PRODUCT_BACKLOG.md`, action ids greppd from descriptions); prints
condensed per-action lines + the mechanical set differences. Engine side:
judges the diffs, composes `create_ticket` ops (fenced JSON inside finding
detail), decides severity/status. Apply side (`render.sh` → `bin/apply_tickets.py`,
post-promotion, harness-trusted, unsandboxed): reads the PROMOTED `latest.json`
only — runner suppression already applied — dedupes and creates Ideas tickets
via `~/.claude/ticket-takeaway/tickets-cli.py`. The engine cannot write the
ticket DB (codex sandbox) and must not; the hook is the single write path.

## 4. Cadence (+ why)

`weekly:mon:20:00` local (Mac TZ Asia/Manila) = Monday 08:00 ET — a weekly
reconciliation matches how often registers actually change (audits are
~quarterly; ad-hoc runs occasional), and 20:00 Manila sits after the daily ads
stagger (18:00–19:40) so launchd windows never overlap. Missed firings coalesce
to one at wake; staleness expectation 7d, harmless — the board does not rot in
a week.

## 5. Scope & exclusions

In scope: every run dir under the DMP output root, current and future (the
precheck enumerates, never hardcodes run names); the `maguyva-actions` board;
the action↔ticket map. Exclusions: the maguyva-marketing main board (its B-12/
B-15 fold-ins are represented in the map, not re-derived); prod-queue
(`PQ-*` — its own workflow); the ads actionator sets (`AD*-*` ids — own loops);
real-world done-ness verification (v2, §2); ANY write outside the apply hook's
Ideas-section `add` (no moves, edits, accepts, strikes, approvals — the
register strike workflow stays human).

## 6. Guardrails, verbatim

- "Nothing auto-approves or auto-strikes." (approved Q2 design)
- "Express approval = the user personally clicking Release in GC /today —
  NOTHING else" (repo CLAUDE.md; this loop touches no reddit drafts, listed
  because it is the house pattern: robots propose, Generalissimo disposes).
- "Machines may ONLY append candidates" (KOL registry rule, same pattern —
  this loop may only append Ideas tickets).
- "report/propose-only" at engine level, enforced by the permission floor, not
  prompt text alone.
- Ticket writes go ONLY to project `maguyva-actions`, ONLY section `ideas`,
  ONLY via `tickets-cli.py add` (apply_tickets.py hardcodes all three).

## 7. Permission axes + justification

`perm_fs_write=report_only`, `perm_network=none`, `perm_local_exec=none`,
`perm_remote_mutation=none` — the floor on all four axes. The engine only
interprets precheck output (codex report_only = read-only sandbox, correct
here). No dangerous combo applies. The ticket-creating side effect lives in
`render.sh`/`apply_tickets.py`, which is a runner-invoked unsandboxed script
like precheck — governed by review of this loop dir, not by engine axes; it
runs only after validation + promotion + suppression, so a dismissed finding
or a contract-violating run can never create a ticket. (Same doctrine slot as
ads-x's emit script, but post-promotion instead of engine-invoked, which is
why THIS loop keeps the floor while ads-x needed workdir.)

## 8. Finding identity

A finding = one register condition needing human attention.
`<action-id>:unticketed` (open action, no map/board coverage — e.g.
`AEO-10:unticketed`), `<action-id>:register-map-conflict` (register status
contradicts map disposition), `input:<slug>` (digest integrity problem, slug
from the file/dir concerned). No dates, counts, run ids, or ticket ids ever
appear in a finding_id. Documented under `## Finding identity` in prompt.md.

## 9. Tier-1 semantics

`ok` — zero findings: all open actions covered, no conflicts, no problems.
`warn` — one or more `:unticketed` or `:register-map-conflict` findings (the
normal "work for Generalissimo" state). `alert` — any `input:*` finding: the
digest itself is unreliable (missing map/board, unparseable register), so this
run's coverage claims cannot be trusted.

## 10. Tier-2 metrics + panels

Metrics (flat keys in the JSON-string): `registers.scanned`, `actions.total`,
`actions.open`, `actions.struck`, `actions.uncovered`, `proposals`,
`conflicts`, `problems`. Panels (dashboard.json): number on
`actions.uncovered` (warn 1 / alert 5, higher_is_worse, hold), number on
`proposals` (neutral, gap), number on `conflicts` (warn 1 / alert 3, hold),
90-day trend on `actions.open`.

## 11. Engine/model + budget

`engine=codex` (Generalissimo's standing 2026-08-05 engine call — claude
tokens are for development; codex file auth works under launchd), engine
default model. Expected tokens/run: a few thousand (the digest is ~12 KB and
the judgment is small); `retry_transient` default 1; `timeout_s=900` — far
above the expected minutes, below the point a hung run blocks the evening.

## 12. Page output

No report page in v1 — `render.sh` exists for the apply hook, deliberately
writes no `$PAGE_OUT`, so every promoted run logs a failed page gate in
`page-render.log`. That log line is EXPECTED and harmless; the apply hook's
own stdout (created/skipped/failed counts) lands in the same log and is the
thing worth reading. If a page is wanted later: `findings` page class over the
proposal history (v2, together with §2's evidence probes).
