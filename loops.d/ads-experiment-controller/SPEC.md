# ads-experiment-controller — intake spec

**Owner:** maguyva-marketing · **Type:** watchdog · **Schedule:** interval:30m · **Engine:** codex (failure write-ups only)

Design: maguyva-marketing `openspec/changes/add-ads-experiment-controller` D3.
Probe contract: `docs/specs/ads-experiment-tick-U9b.md`.

## No ads mutation

The controller only observes and simulates. The probe forces `dry_run: true`.
The GC CLI refuses a tick that is not dry-run. No probe argument can name an
order, an amount, a budget, a campaign or an ads resource.

Operator rule (2026-10-29): no new ads run until the controller is ready and
proposing, and the operator has set the test budget.

1. Purpose & stop condition

Keep the ads experiment ledger current: each firing collects observations,
evaluates runs and reconciles simulated commands. Per firing, done is a
quiet heartbeat (every job `ok` or `skipped: locked`) or one finding per
job that did not succeed. Across runs, a finding is done when that job's
next tick succeeds and the runner sets `resolved_at`. This loop does not
finish the experiment and it does not launch ads.

2. Agentic pattern

Outer shape is Human-in-the-loop, like every v1 loop. The precheck is the
whole check. The engine runs only on exit 1 and writes the failure up. There
is no retry-until-success across firings. The 30-minute schedule is the
next attempt. No v2 aspiration: live execution is a later, separate pilot
and is out of this loop.

3. Type & data flow (precheck gathers vs engine interprets)

`type=watchdog`. `precheck.sh` is the job. For `collect`, then `evaluate`,
then `reconcile`, it builds a request (`issued_at` now, `expires_at` now
+ 9 minutes, `request_id` `$RUN_ID:<job>`, or a UTC timestamp when `RUN_ID`
is unset), base64url-encodes it, and calls `bin/probe ads-experiment-tick`.
Exit 0 when every receipt has `ok: true` or `skipped: locked`. Exit 1, with
a short digest, when transport fails or a receipt is not ok. The engine
reads that digest. It does not call the probe and it does not decide whether
the tick failed.

4. Cadence

`interval:30m`. Collection and protective simulation need to be newer than
a human check-in, and the candidates draft is a separate weekly loop so
this firing stays short. Staleness expectation is about 30 minutes. Interval
firings missed during sleep are lost, not queued. That is acceptable: the
next wake firing collects again, and this stage does not spend.

5. Scope & exclusions

In scope: the three jobs above, on the experiments the GC CLI already
enumerates. Explicitly excluded: placing, changing or pausing an ad;
editing `experiments.yaml`; promoting a candidate; the weekly `candidates`
job (that is `ads-experiment-candidates`); any spend; any network call by
the engine. The probe's own reads (Google Ads read-only GAQL, Stripe
snapshot, Umami cache) happen inside the GC CLI on llm, not in this prompt.

6. Guardrails verbatim

- No ads mutation. The controller only observes and simulates. The probe
  forces dry-run. The GC CLI refuses anything else.
- Operator rule (2026-10-29): no new ads run until the controller is ready
  and proposing, and the operator has set the test budget.
- Report/propose-only for the engine. It must not call the probe or suggest
  any ad change.
- Fresh engine session per firing. Cross-run memory is the findings table,
  not a resumed session.
- Watchdog stickiness: if the precheck failed, stored status stays `alert`
  regardless of the engine's wording and regardless of suppression.

7. Permission axes + justification

`perm_fs_write=report_only`, `perm_network=none`, `perm_local_exec=none`,
`perm_remote_mutation=allowlist`, `exec_allowlist="bin/probe ads-experiment-tick"`.

The engine sits at the report-only floor on network and local exec. It
never calls the probe. The precheck does, unsandboxed, which is the
watchdog job and is not governed by `perm_network`.

`perm_remote_mutation=allowlist` is raised because the tick writes
experiment-controller ledger rows in GC's `ads.db` on llm: observations,
decisions, simulated commands and job runs. It never places, changes or
pauses an ad. Justification is in `loop.conf`. This is dangerous-combo
rule 2 (mutation requires a written justification), satisfied by
`remote_mutation_justification`. It is not rule 1, 3 or 7 (network stays
`none`). The allowlist entry is `bin/probe ads-experiment-tick`, not a bare
tool and not a mutating verb form (rule 4).

8. Finding identity (what a finding IS + finding_id derivation rule)

A finding is "this job's tick did not succeed." `finding_id` =
`ads-experiment:<job>` for `<job>` in `collect`, `evaluate`, `reconcile`.
One finding per `PROBLEM:` line. Transport failure and receipt failure share
the id. The id never embeds the error text, a timestamp, a count or the run
id. See `prompt.md` `## Finding identity`.

9. Tier-1 semantics (ok/warn/alert meaning)

`ok` — silent-green. All three receipts were `ok` or `skipped: locked`. No
engine ran. `alert` — the precheck exited 1. Watchdog stickiness keeps the
stored status `alert` while that is true. This loop never uses `warn`. A
tick is either quiet or it needs a person.

10. Tier-2 metrics + panels

None. The heartbeat and the finding ids carry the state. `dashboard.json`
is `{"panels": []}` on purpose. The prompt emits `metrics` as the JSON
string `"{}"`. Undeclared metrics would still show on the raw fallback
panel; this loop does not emit any.

11. Engine/model + budget

`engine=codex`, `model=` empty (engine default). The engine runs only on
failure. The prompt plus the precheck digest is a few hundred tokens plus
the codex baseline. Output is one short JSON object per failing job.
`retry_transient` stays at the default 1. `timeout_s=900`, matching the
loop budget. Each probe call is capped at 600s by the probe header and the
CLI subprocess at 560s; a healthy tick is far shorter. `retention_days=90`.

12. Page output

None. Job receipts and simulated commands are shown on the GC experiment
page. This loop does not render a report page.
