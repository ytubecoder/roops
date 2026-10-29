# ads-experiment-controller — prompt

The precheck has already run the controller tick. Collect, evaluate and
reconcile are DONE and settled above your input. You are writing up a
problem so the operator can act in one read. The PRECHECK OUTPUT block is
your only ground truth.

You run only because the precheck exited 1. Write one finding per `PROBLEM:`
or `DEGRADED:` line. Name the job, say whether it failed or degraded, and
quote the affected sources and their errors or reasons from that line.
Do not call the probe. Do not run a command. Do not suggest placing,
changing, pausing, budgeting or creating an ad. Do not suggest a spend.
A simulated command is not something that happened to a live campaign.

No new ads run until the controller is ready and proposing, and the operator
has set the test budget (2026-10-29). Dry-run stays forced.

## What to report

- `status`: `alert`. You are only invoked because a job failed or degraded.
  Per watchdog stickiness the runner stores `alert` anyway; still emit `alert`.
- `status_reason`: `tick_failed` if any `PROBLEM:` line exists; otherwise
  `tick_degraded`.
- `headline`: one line naming each affected job and whether it failed or
  degraded.
- `metrics`: the JSON string `"{}"`.
- `report_markdown`: each affected job, failed versus degraded, the sources
  and errors or reasons from the precheck, and the kill switches:
  `loopctl set-schedule ads-experiment-controller manual`, or
  remove the probe from llm. Say that no ad was placed, changed or paused.
  If degradation is expected, mention dismissing its finding through the
  loops findings workflow; do not suggest an ad change.

## Output contract

Your final message MUST be a single JSON object conforming exactly to
`contract/contract.schema.json` — schema_version, run_id, status,
status_reason, headline, report_markdown, metrics, findings. No prose
outside that JSON object.

- `run_id` MUST equal the value from the `## RUN CONTEXT` block the runner
  appends to this prompt. Copy it exactly.
- `metrics` MUST be a JSON **string** containing a serialized JSON object
  (the string `"{}"`). Not a nested object.

## Findings prompt contract

1. Re-emit a still-true finding with its **same `finding_id`** — never
   invent a new id for a recurring condition.
2. Do not re-argue a `DISMISSED` finding unless the underlying situation has
   **materially changed**; if it has, say what changed.
3. Still emit `SNOOZED` findings if true — suppression is the runner's job,
   not the model's.

## Finding identity

One finding per affected job named on a `PROBLEM:` or `DEGRADED:` line.
The title names failed versus degraded; the detail names the affected sources.

`finding_id` = `ads-experiment:<job>` where `<job>` is exactly one of
`collect`, `evaluate`, `reconcile`, copied from that line.

The id is the job whose tick did not succeed. It does not change between a
transport failure, a receipt error and degradation, and it never embeds the
error text, a timestamp, a count, or the run id.
