# ads-experiment-candidates — intake spec

**Owner:** maguyva-marketing · **Type:** watchdog · **Schedule:** weekly:mon:10:00 · **Engine:** codex (failure write-ups only)

Design: maguyva-marketing `openspec/changes/add-ads-experiment-controller` D3.
Probe contract: `docs/specs/ads-experiment-tick-U9b.md`.

## No ads mutation

The candidates job only drafts proposals. The probe forces `dry_run: true`.
The GC CLI refuses a tick that is not dry-run. A draft is not an ad, and
nothing in this loop places, changes or pauses one.

Operator rule (2026-10-29): no new ads run until the controller is ready and
proposing, and the operator has set the test budget.

1. Purpose & stop condition

Once a week, ask the GC CLI to draft candidate experiments and store them
as proposals. Per firing, done is a quiet heartbeat (the receipt is `ok` or
`skipped: locked`) or one finding that the candidates job failed. Across
runs, the finding is done when the next weekly tick succeeds and the runner
sets `resolved_at`. Promoting a draft into the registry is a human edit.
This loop does not do it.

2. Agentic pattern

Outer shape is Human-in-the-loop. The precheck is the whole check. The
engine runs only on exit 1 and writes the failure up. The AI that drafts
candidates runs on llm, inside the probe, under the GC CLI's own timeout.
This loop does not iterate on the draft. There is no v2 aspiration here:
applying a draft would be a later, separate, operator-confirmed step.

3. Type & data flow (precheck gathers vs engine interprets)

`type=watchdog`. `precheck.sh` is the job. It builds one request for
`candidates` (`issued_at` now, `expires_at` now + 9 minutes, `request_id`
`$RUN_ID:candidates`, or a UTC timestamp when `RUN_ID` is unset),
base64url-encodes it, and calls `bin/probe ads-experiment-tick`. Exit 0
when the receipt is `ok` or `skipped: locked`. Exit 1, with a short digest,
when transport fails or the receipt is not ok. The engine quotes that
digest. It does not call the probe and it does not judge the drafts.

4. Cadence

`weekly:mon:10:00`, local time. Drafts are proposals a person reads on the
GC experiment page. They do not need the 30-minute cadence of collect,
evaluate and reconcile (those stay on `ads-experiment-controller`). A missed
Monday coalesces to one firing at wake. Staleness expectation is 7 days.
A week-late draft is still only a proposal.

5. Scope & exclusions

In scope: the single `candidates` job. Explicitly excluded: placing,
changing or pausing an ad; editing `experiments.yaml`; promoting a draft;
collect, evaluate and reconcile (those are `ads-experiment-controller`);
any spend; any network call by the engine. Search-query and GTM context
loaded by the GC CLI stays inside that CLI.

6. Guardrails verbatim

- No ads mutation. The controller only observes and simulates. The probe
  forces dry-run. The GC CLI refuses anything else.
- Operator rule (2026-10-29): no new ads run until the controller is ready
  and proposing, and the operator has set the test budget.
- AI drafts are proposals only, shown on the GC experiment page.
- Report/propose-only for the engine. It must not call the probe or suggest
  any ad change, and it must not suggest launching a draft.
- Fresh engine session per firing.
- Watchdog stickiness: a failed precheck stays `alert` in stored status.

7. Permission axes + justification

`perm_fs_write=report_only`, `perm_network=none`, `perm_local_exec=none`,
`perm_remote_mutation=allowlist`, `exec_allowlist="bin/probe ads-experiment-tick"`.

Same split as the controller loop. The engine never calls the probe. The
precheck does. The raised remote-mutation axis covers ledger rows on llm:
candidate drafts and the candidates job run. It does not cover an ad
change. `remote_mutation_justification` in `loop.conf` satisfies
dangerous-combo rule 2. Network stays `none`, so rules 1, 3 and 7 do not
apply. The allowlist entry is scoped to this probe (rule 4).

8. Finding identity (what a finding IS + finding_id derivation rule)

A finding is "the candidates tick did not succeed." `finding_id` =
`ads-experiment:candidates`. One finding. Transport failure and receipt
failure share it. The id never embeds the error text, a timestamp, a count
or the run id. See `prompt.md` `## Finding identity`.

9. Tier-1 semantics (ok/warn/alert meaning)

`ok` — silent-green. The candidates receipt was `ok` or `skipped: locked`.
No engine ran. `alert` — the precheck exited 1. This loop never uses
`warn`. A weekly draft either ran or it needs a person. The content of a
successful draft is not a finding; it is a proposal on the GC page.

10. Tier-2 metrics + panels

None. `dashboard.json` is `{"panels": []}`. The prompt emits `metrics` as
the JSON string `"{}"`. Draft quality is reviewed on the GC experiment
page, not as a harness metric.

11. Engine/model + budget

`engine=codex`, `model=` empty (engine default). The engine runs only when
the weekly tick fails. That prompt is a few hundred tokens plus the codex
baseline. The draft itself is produced on llm inside the probe (GC timeout
420s, inside the probe's 600s cap), not by this engine. `retry_transient`
stays at the default 1. `timeout_s=900`. `retention_days=90`.

12. Page output

None. AI drafts are proposals only, shown on the GC experiment page. This
loop does not render a report page.
