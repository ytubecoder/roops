# Open threads — unfinished design work

**Static register.** Design questions that are genuinely open, with the facts
needed to answer them. Not a session handoff, not a status page: no run state,
no "what I did today". Delete a section when it is settled; if a thread becomes
scheduled work, move it to `PRODUCT_BACKLOG.md` and delete it here.

Loop *themes* and candidate selection live in `docs/LOOP_SELECTION.md`.

## 1. The approve→action bridge (MAIN THREAD — needs Generalissimo's answer)

His pattern: the system computes as if autonomous but pauses at each gate;
approvals accumulate until "you have approved this N times — set standing
approval?"; over time it becomes *more* autonomous. The scaled-down v1 he asked
for: when he approves, the recommendation is marked approved and recorded
durably, so a later action loop can create actions from it.

Facts the design has to respect:

- **`loopctl ack` means "stop nagging", NOT "recommendation sustained."** Do not
  overload it — approval needs its own verb or its own record.
- **Google Ads is OAuth-only in the DMP plugin's action layer**
  (`execute_blocked_reason: "use MCP path"`); `connector_executor.py` cannot fire
  it. The first action loop would need the Claude+MCP path.
- **Two id spaces with no mapping:** loop-local per-run ids (`ads-google:ADG-NN`)
  versus DMP's named registry actions. This gap IS the bridge.
- **The standing guardrails are not inherited by an action loop.** No scheduled
  process calls `service.record_and_apply()` or any network write API; CDP is
  never cron'd; loops never git-write maguyva-marketing. Widening is
  Generalissimo's explicit amendment each time, never assumed. (One narrow
  exemption exists and is documented in `loops.d/ads-hard-cut/SPEC.md` §7.)

**The open question:** should approval live in the harness (a new disposition
verb, e.g. `approve`) or in Growth Console, where ads-google's SPEC already says
decisions get recorded? If GC, the harness only needs the ack≠approval
distinction made explicit.

**v1 stand-in, shipped 2026-07-30 and NOT a resolution:** the dashboard renders a
deterministic, generator-templated "paste this into your agent" prompt for each
unsuppressed open finding (`docs/INTERFACES.md` §10, Amendment 2) — built only
from sqlite + `latest.json`, never model output, and the template MUST NEVER say
"approve". It hands the decision to the reader's own agent instead of executing.

## 2. Approval counting / standing approval

Wanted regardless of how §1 is answered: count approvals per `finding_id`, and at
some threshold surface "approved N× — set approval status for next time?".
Standing approval is a **recorded status only** — report/propose-only stays until
explicitly widened.

## 3. `docs/INTERFACES.md` §4 section order (cosmetic)

Sections run 4.1, 4.2, 4.3, **4.5, 4.6, 4.4** — §4.4 (Redaction pass) sits after
§4.6. Numbering is correct; only the physical order is wrong. Left alone
deliberately: INTERFACES is the frozen contract and other docs cite its section
numbers, so this is a reorder-in-place with no renumbering, whenever someone is
already editing that file.

## 4. Display ontology — design SETTLED 2026-07-29, build deferred

A three-agent brainstorm converged and Generalissimo chose to ship the failure-UX
slice first. The settled design, if and when it gets built:

- **Compile-time, never runtime.** An LLM authors a loop's display spec ONCE at
  authoring time (a `loopctl restyle`-style verb), committed and diff-reviewed;
  `generate.py` renders it deterministically forever. Per-run LLM passes were
  REJECTED: layout jitter kills at-a-glance anomaly detection, a hermetic suite
  cannot test a model at generate time, and a compile pass never sees run values
  so it structurally cannot restate a number.
- Vocabulary: 5 blocks (`stat` with folded sparkline, `flag`, `items`, `table`,
  `prose`) × 3 slots (hero ≤3 / grid ≤8 / drawer). Everything value-bearing is a
  ref (`metric:<key>`, `contract:<field>`) substituted by the generator; no block
  may name a color; thresholds only proposed when SPEC.md states them. Drift
  badge = hash of the metric key-set vs `compiled_from_keys`.
- Already shipped from this design: the `prose` block as the inline report drawer
  (INTERFACES §10 amendment), plus failure surfacing and the agent-handoff block.
- **Trigger to build the rest:** when hand-writing `dashboard.json` actually bites
  (~20+ loops), or when a loop needs `items` (a ranked list with prose tails).
