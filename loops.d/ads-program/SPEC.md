# ads-program — intake spec

Build-order STEP 2 of `docs/ads-actions-loops-warmstart.md` (in the
maguyva-marketing repo). One of five planned per-network ads check loops; this
is the first, built end-to-end before cloning to intl/reddit/x/program.

1. Purpose & stop condition
This loop is the scheduled CROSS-NETWORK program check — the fifth loop,
running after the four network loops. It reads the Growth Console ads JSON
surface plus the network loops' newest action sets from `$LOOPS_ROOT` run
dirs (read-only), surfaces PROGRAM-level exceptions no single network loop
owns (budget totals vs caps with the committed-basis binding order, the
~$1k/mo real-spend soft target, device-policy sync across networks as a
recommend-manual-check, bring-up holes, upstream set gaps), and distils them
into a per-run **action set** of read-only briefs (`ADP-NN`). It REFERENCES
network action ids and never duplicates their suggested orders. It never
applies anything.
- **Per-firing "done":** a fresh, valid action set is written for this run and
  the status that actually SURFACES reflects it. Because this loop normally emits
  findings, what surfaces is `effective_status` = max severity of the unsuppressed
  findings (INTERFACES.md §4.5) — **not** the contract `status` this loop declares.
  Read §9 before relying on either.
- **Cross-run "done":** a finding (= one open action) gets `resolved_at` set when
  a later run observes its condition resolved and the set strikes the action.

2. Agentic pattern
Outer shape is **Human-in-the-loop** (the honest v1 mapping): the loop proposes
an action set; Generalissimo is the "repeat" mechanism (he applies/declines via
`record_and_apply` and records the decision in the runbook). Inside the single
engine invocation the work is Plan-then-Execute: read the digest → enumerate
exceptions → write + validate the set → emit the contract. No iterate-across-
invocations machinery (no retry-until-success) — that is out of v1 by design.
v2 aspiration (not built): a GC "record assessment" affordance could pre-fill an
`experiment_assessment` row from an action brief — still human-clicked.

3. Type & data flow (precheck gathers vs engine interprets)
`type=agent`. **precheck.sh** (unsandboxed, the only network I/O — not governed
by perm_network) curls the four LOCAL GC endpoints (`/api/ads/scoreboard`,
`/api/ads/campaigns`, `/api/ads/journal?limit=60`, `/api/ads/program-events`)
into `state/runs/<id>/inputs/*.json`, **derives scope from the experiments
registry at run time** (google cards except intl/retired), and prints a compact
deterministic digest (per-variant impr/CTR/spend/cpc/verdict, evaluator-gate
flag, budget headroom + committed-vs-actual note, google journal tail, program
events, and the PRIOR action set's register for stable-ID continuity). The
**engine** only interprets that digest: judges which lines are genuine
exceptions, assigns/keeps `ADP-NN` ids, writes suggested orders with full
placement ids, and emits the contract. All umami/ads reads stay behind GC's
single rate limiter (never imports `console.ads.service`).

4. Cadence
`daily:19:40` (Manila local = ~07:40 EDT), LAST in the morning stagger
(google → intl → reddit → x → ads-program). Daily because the program moves
daily and the design is "a complete fresh set each day so Generalissimo reads the
latest, never catches up." Installed via `loopctl install` in phase 4. launchd
sleep coalesces missed calendar firings into one at wake (best-effort); the
freshness guarantees live in the set/registry logic, not timing.
Runs LAST so it can read same-day network sets — but never TRUSTS the order:
each sibling set carries a freshness label and a missing/stale set is reported
as a gap in this loop's own set, never a run failure.

5. Scope & exclusions
**In scope (program-level only):** budget totals vs configured caps (monthly
$1,600 / google $500; cited as configured intent — the config file is not
readable via GC) and the ~$1,000/mo REAL-spend soft target; the
committed-binds-first guard behavior; device-policy sync ACROSS networks
(recommend the manual verification commands, never assert device state);
bring-up holes (pending campaigns, missing policy re-assertion); journal
anomalies that tell a cross-network story; upstream set gaps.
**Explicitly excluded:** per-campaign/per-variant exceptions (the network
loops own them — REFERENCE their ids like `ads-google:ADG-03`, never duplicate
their orders); any WRITE (record_and_apply, ads API, git, CDP/browser); any
GAQL/device-state read (phase-1 inputs cannot see it — the loop recommends
`mobile-off.py` / `mobile-off.mjs --verify` as manual checks instead).

6. Guardrails (verbatim, from the warmstart + repo memory)
- "Write policy: strictly read-only. No scheduled process ever calls
  `service.record_and_apply()` (or any network write API, or Postiz, or git on
  this repo)."
- "CDP is never cron'd" — this loop never touches the OpenTwins Chrome or lease.
- "Loops never git-write this repo and never edit the runbooks; runbook updates
  are Generalissimo's decision log."
- "A loop may RECOMMEND a `program_events.yaml` append as an action, never
  perform it."
- Positive-spend note (intl 2026-07-17 lesson): "the guard binds on committed
  first" — any positive-spend suggested order must state committed-vs-actual
  basis and whether the guard will refuse it.
- Live ad copy freeze / never edit variants.yaml for fact drift — this loop
  proposes, never edits.
These are embedded in `prompt.md` AND enforced by the permission axes (§7).

7. Permission axes + justification
`perm_fs_write=report_only`, `perm_network=none`, `perm_local_exec=allowlist`,
`perm_remote_mutation=none`.
- **network=none:** the engine never reaches the network; precheck.sh (an
  unsandboxed script, not governed by this axis per §7.3) does all fetching.
- **remote_mutation=none:** nothing this loop does can change remote state.
- **local_exec=allowlist:** exactly two shipped, network-free, non-mutating
  scripts — `emit_action_set.py` (writes the set + self-validates) and
  `validate_action_set.py` (re-checks). No bare/mutating remote-capable CLI, so
  dangerous-combo #4 does not trip; #1/#3/#7 need `perm_network=full` (we're
  none); #2/#5 need remote_mutation/workdir (neither).
- **fs_write=report_only:** the only writes are this run's action-set files.
  **Why an emit script instead of the engine writing files directly, and why
  claude (not the codex default):** the report_only floor gives the *model* no
  filesystem write under either adapter (codex report_only → read-only sandbox
  where only the CLI writes `-o`; the claude floor exposes no Write tool —
  INTERFACES.md §7.2/§7.3). The only harness-sanctioned way for the engine
  session to write separate md files is an allowlisted local command. Under
  claude, allowlisted Bash reaches the real filesystem; under codex the same
  command would be blocked by the read-only sandbox — so **codex cannot express
  this loop and claude is forced** ("harness default codex unless dangerous-
  combo/adapter rules force otherwise"). This is a documented deviation from the
  warmstart's "allowlist = validator only" wording (the warmstart assumed
  report_only let the engine write the md files; neither adapter does). It stays
  within the harness contract — only documented axes are used, and network +
  remote mutation remain at the floor.
  **Payload format constraint (learned from the first supervised run,
  2026-07-26/27):** Claude Code's Bash permission matcher hard-denies any
  command whose text combines a brace character with a quote ("expansion
  obfuscation"), so a JSON payload can NEVER be delivered to the allowlisted
  script (heredoc, argument, or otherwise). Probed empirically: heredocs,
  quotes, and `[section]` brackets all pass; brace+quote never does. The emit
  script therefore takes a brace-free FLAT sectioned format from the engine
  (JSON retained for tests/manual use only) and rejects braces with a clear
  error.

8. Finding identity (what a finding IS + finding_id derivation)
A **finding** is one still-open action in this run's set. `finding_id` =
`ads-program:<action id verbatim>` — two-part `ADP-<SRC>-NN` for actions minted
after 2026-07-28, legacy `ADP-NN` for carried ones. The action id is the durable
per-exception identity: stable across runs for the same real-world condition
(carried forward from the prior set), never reused after a strike, embedding NO
volatile data. A struck (resolved) action emits no finding. **Dismissal ≠
strike:** dismissing a finding is a runner-side nag-stop; it does NOT strike the
action. Striking happens only when a later run observes the condition resolved
(or Generalissimo's decision log says so), so the loop keeps re-emitting the
finding under its id every run while it is still true. (Documented in prompt.md
`## Finding identity`.)

**Register + brief conventions (ads-local — state them here so nobody closes an
action from the wrong side).** Required by the warmstart's "Action register +
brief contract"; enforced by `bin/validate_action_set.py`:
- **ID pattern `^ADP-(?:(?:PRG|BUD|INP)-)?\d{2,}$`** — new ids are
  two-part `ADP-<SRC>-NN` (source = provenance designator; the number is ONE
  per-loop sequence shared across sources); single-part legacy ids remain
  valid only while carried forward. Sibling loops use the same shape with
  their own prefix (`ADG-` google, `ADI-` intl, `ADR-` reddit, `ADX-` x — those network loops' allowed sources are `EV|CMP|JRN|BUD|INP`).
- **IDs are NEVER reused after a strike.** A new id's number is always (max
  number ever seen) + 1, with the minter's chosen source designator; a
  genuinely-first run starts at `ADP-<SRC>-01`. Bare single-part ids are never
  minted anew.
- **Register syntax deliberately mirrors the DMP register shape** (`## <ID> —
  <title>` headings, the same strike convention) so the GC reader can borrow
  `dmp_actions`'s regexes instead of inventing a dialect. These conventions are
  **ads-local**: they are documented here and in the sibling loop SPECs, and must
  NOT be entangled with digital-marketing-pro's `_conventions.md`.
- **Each set is COMPLETE** — the latest set alone is the whole current truth, so
  a human reads only the newest one and never catches up.
- **Two directions of closure, one owner each.** A *strike* (robot) happens only
  when a later run observes the condition resolved, or Generalissimo's decision
  log says so. A *dismissal/snooze* (human) is runner-side nag-stop only: it
  suppresses the finding from `latest.json`/dashboard but leaves the action OPEN
  in the set, and the verbatim emission stays in
  `state/runs/<id>/contract.json`. Neither one performs the other's job.

9. Tier-1 semantics

**Key names (verified against `contract/contract.schema.json` + two real runs).**
The contract field the engine emits is **`status`** — there is NO `loop_status`
key in the contract, and the schema is `additionalProperties: false`, so emitting
one would be a `contract-violation`. `loop_status` is the *sqlite column* that
stores the emitted `status` verbatim (`bin/run-loop.sh:824`, `bin/db.py:30`); it
is a storage name, not a wire name. The enum is exactly `ok | warn | alert` —
**there is no `error` status.** (The warmstart plan doc's "the engine reports
loop_status=error" wording is wrong on both counts; do not copy it into the
sibling loops.)

**What the loop DECLARES vs what SURFACES.** Two different values are stored per
run, and the dashboard/`loopctl` show the second:
- `loop_status` — this loop's declared contract `status`, stored verbatim.
- `effective_status` — per INTERFACES.md §4.5: **if the `findings` array is
  non-empty, `effective_status` = max severity of the *unsuppressed* findings**
  (`info`→`ok`, `warn`→`warn`, `alert`→`alert`; all suppressed → `ok`). Only when
  `findings` is empty does `effective_status` fall back to the declared `status`.

Because this loop emits one finding per open action, **the declared `status` is
normally ignored.** Consequences that must be designed around, not worked around:

- 🚨 **A run-integrity failure must ALSO be emitted as a finding with
  `severity: alert`, or it is silently downgraded.** Verified on the first
  supervised run (`20260726T190729Z-ads-program-58e835`): the engine correctly
  declared `status=alert` with `status_reason=action_set_invalid` because the
  action set was never written — but its four findings maxed at `warn`, so
  §4.5 computed `effective_status=warn` and the dashboard showed AMBER for a run
  that produced no action set at all. The claim "a malformed set fails the run
  visibly" is only true if an alert-severity finding carries it.
- A set whose actions are ALL `severity: info` surfaces as **`ok` (green)**, even
  though the loop declares `warn`. Reserve `info` for genuinely observe-only
  actions that are fine to show green. **Data-integrity caveats (no callable
  verdict, broken CTR baseline, unverifiable ledger) are `warn`, never `info`**
  (review round, 2026-07-28): if the other warns strike while such a caveat
  persists, an `info` caveat would surface the set green under an active
  data-integrity problem.
- When every finding is dismissed/snoozed, `effective_status` is `ok` by design —
  that is the nag-stop working, not a lost signal.

**Declared-status vocabulary** (still emitted, still stored, still the fallback
when there are zero findings):
- `ok` — zero open actions this run; the google program is quiet. With no
  findings, this is also what surfaces.
- `warn` — one or more open actions for Generalissimo to read (the normal daily
  state while the message test is live). Not a harness problem; it is the
  deliverable.
- `alert` — a critical delivery/spend problem (e.g. an ENABLED campaign serving
  zero, google actual MTD spend at the cap), a missing critical input in the
  digest, or an action set that failed validation
  (`status_reason=action_set_invalid`). **Always pair with an alert-severity
  finding** per the rule above.

`status_reason` is a short machine category (§9.1). Categories this loop uses:
`action_set_invalid`, `input_gap_prior_action_set_not_persisted`,
`input_gap_endpoints_missing`.

10. Tier-2 metrics + panels

**Wire encoding (INTERFACES.md §9.1 — get this exactly right).** The `metrics`
field is a **JSON string containing a serialized JSON object** (`"{}"` when
empty) — the string encoding is forced by codex's strict structured-output mode.
`validate_contract.py` rejects a non-parsing or non-object string as a
`contract-violation`. The *values inside* that object should be **numbers**, not
strings: `db.py flatten_metrics` routes numbers and booleans to the `num` column
and everything else to `text` (`bin/db.py:276-289`), and the number/trend panels
below read `num`. Stringifying the values would silently blank every panel.
(Both real runs emitted integer values correctly.)

Keys → `dashboard.json` panels:
- `actions.open` — number panel (higher_is_worse, warn 1 / alert 5) + a 30-day
  trend panel (open-action count over time).
- `actions.struck` — count of actions struck this run (raw fallback panel).
- `scope.variants` — number panel (neutral): in-scope variant count (12 today).
- `scope.campaigns` — count of in-scope campaigns (raw fallback).
- `inputs.missing` — number panel (higher_is_worse, warn 1 / alert 2): how many
  of the four GC endpoints failed to fetch — a data-health signal.
- `action_set.written` — `1` when this run persisted a valid set, `0` when it did
  not (raw fallback panel; emitted by the first supervised run to record exactly
  the failure §9 describes). Emit it every run so the "set missing" condition is
  queryable in sqlite independently of the finding severity.

11. Engine/model + budget
`engine=claude` (forced — see §7), `model=` blank (claude default). Expected
tokens/run: low-to-mid — one digest (a few KB) in, a short structured analysis +
a couple of allowlisted Bash calls, contract out; order of a few thousand
tokens. `retry_transient=1` (default). `timeout_s=900` (15 min — proportional to
one interpret-and-write pass, well under the ceiling). `retention_days=730`
(sets live in run dirs; keep long, revisit via the GC size indicator).
