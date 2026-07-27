# ads-google — intake spec

Build-order STEP 2 of `docs/ads-actions-loops-warmstart.md` (in the
maguyva-marketing repo). One of five planned per-network ads check loops; this
is the first, built end-to-end before cloning to intl/reddit/x/program.

1. Purpose & stop condition
This loop is the scheduled Google-network (non-intl) ads check-in. It reads the
Growth Console ads JSON surface, surfaces the exceptions a human should act on
(CTR/spend/verdict movement, delivery anomalies, budget-guard headroom,
review/serving state), and distils them into a per-run **action set** of
read-only, context-linked briefs (`ADG-NN`). It never applies anything.
- **Per-firing "done":** a fresh, valid action set is written for this run and
  the tier-1 status reflects it (`ok` = zero open actions, `warn` = open actions
  to read, `alert` = critical delivery/spend problem or input gap or invalid set).
- **Cross-run "done":** a finding (= one open action) gets `resolved_at` set when
  a later run observes its condition resolved and the set strikes the action.

2. Agentic pattern
Outer shape is **Human-in-the-loop** (the honest v1 mapping): the loop proposes
an action set; generalissimo is the "repeat" mechanism (he applies/declines via
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
exceptions, assigns/keeps `ADG-NN` ids, writes suggested orders with full
placement ids, and emits the contract. All umami/ads reads stay behind GC's
single rate limiter (never imports `console.ads.service`).

4. Cadence
`daily:18:00` (Manila local = ~06:00 EDT), first in the morning stagger so the
downstream loops read a fresh google set. Daily because the program moves
daily and the design is "a complete fresh set each day so generalissimo reads the latest,
never catches up." **NOT installed in this phase** — supervised `loopctl run`
only. launchd sleep coalesces missed calendar firings into one at wake (best-
effort); the freshness guarantees live in the set/registry logic, not timing.

5. Scope & exclusions
**In scope:** Google-network experiments EXCEPT intl — derived at run time from
the `/api/ads/campaigns` registry (google cards minus `g-intl` and `retired`).
Today: `g-msg` (g1–g8; google-search-jul26, google-dg-jul26) + `g-theme`
(g13–g16; google-build/-memory/-dg2-jul26). Scope is NEVER hardcoded — a new
google campaign appears automatically; an intl one never does.
**Explicitly excluded:** `g-intl` (g9–g12, owned by the future `ads-intl`
loop); X / Reddit networks; the `retired` card; any WRITE (record_and_apply,
ads API, git, CDP/browser). No cross-network/program checks — those are the
future `ads-program` loop.

6. Guardrails (verbatim, from the warmstart + repo memory)
- "Write policy: strictly read-only. No scheduled process ever calls
  `service.record_and_apply()` (or any network write API, or Postiz, or git on
  this repo)."
- "CDP is never cron'd" — this loop never touches the OpenTwins Chrome or lease.
- "Loops never git-write this repo and never edit the runbooks; runbook updates
  are generalissimo's decision log."
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
`ads-google:<ADG-NN>`. The `ADG-NN` id is the durable per-exception identity:
stable across runs for the same real-world condition (carried forward from the
prior set), never reused after a strike, embedding NO volatile data. A struck
(resolved) action emits no finding. **Dismissal ≠ strike:** dismissing a finding
is a runner-side nag-stop; it does NOT strike the action. Striking happens only
when a later run observes the condition resolved (or generalissimo's decision log says so),
so the loop keeps re-emitting `ads-google:ADG-NN` every run while it is still
true. (Documented in prompt.md `## Finding identity`.)

9. Tier-1 semantics
- `ok` — zero open actions this run; the google program is quiet.
- `warn` — one or more open actions for generalissimo to read (the normal daily state
  while the message test is live). Not a harness problem; it is the deliverable.
- `alert` — a critical delivery/spend problem (e.g. an ENABLED campaign serving
  zero, google actual MTD spend at the cap), a missing critical input in the
  digest, or an action set that failed validation (`status_reason=
  action_set_invalid`).

10. Tier-2 metrics + panels
`metrics` (JSON string) keys → `dashboard.json` panels:
- `actions.open` — number panel (higher_is_worse, warn 1 / alert 5) + a 30-day
  trend panel (open-action count over time).
- `actions.struck` — count of actions struck this run (raw fallback panel).
- `scope.variants` — number panel (neutral): in-scope variant count (12 today).
- `scope.campaigns` — count of in-scope campaigns (raw fallback).
- `inputs.missing` — number panel (higher_is_worse, warn 1 / alert 2): how many
  of the four GC endpoints failed to fetch — a data-health signal.

11. Engine/model + budget
`engine=claude` (forced — see §7), `model=` blank (claude default). Expected
tokens/run: low-to-mid — one digest (a few KB) in, a short structured analysis +
a couple of allowlisted Bash calls, contract out; order of a few thousand
tokens. `retry_transient=1` (default). `timeout_s=900` (15 min — proportional to
one interpret-and-write pass, well under the ceiling). `retention_days=730`
(sets live in run dirs; keep long, revisit via the GC size indicator).
