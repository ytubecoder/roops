# ads-experiment-candidates — prompt

The precheck has already run the candidates tick. Draft generation is DONE
and settled above your input. You are writing up a problem so the operator
can act in one read. The PRECHECK OUTPUT block is your only ground truth.

You run only because the precheck exited 1. Write one finding for the
`PROBLEM:` or `DEGRADED:` line, naming the job (`candidates`) and whether it
failed or degraded. Quote the affected sources and their errors or reasons
from that line. Do not call the probe. Do not run a command. Do not suggest
placing, changing, pausing, budgeting or creating an ad. Do not suggest
adopting, launching or editing a draft into the registry. AI drafts are
proposals only, shown on the GC experiment page.

No new ads run until the controller is ready and proposing, and the operator
has set the test budget (2026-10-29). Dry-run stays forced.

## What to report

- `status`: `alert`. You are only invoked because the job failed or degraded.
  Per watchdog stickiness the runner stores `alert` anyway; still emit `alert`.
- `status_reason`: `tick_failed` for a `PROBLEM:` line; `tick_degraded` for
  a `DEGRADED:` line.
- `headline`: one line naming the candidates job and whether it failed or
  degraded.
- `metrics`: the JSON string `"{}"`.
- `report_markdown`: failed versus degraded, the affected sources and their
  errors or reasons from the precheck, and the kill switch:
  `loopctl set-schedule ads-experiment-candidates manual`, or remove the
  probe from llm. Say that no ad was placed, changed or paused, and that
  drafts are proposals only.
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

This loop has one job, so failure or degradation has one finding.
The title names failed versus degraded; the detail names the affected sources.

`finding_id` = `ads-experiment:candidates`.

The id does not embed the error text, a timestamp, a count, or the run id.
A transport failure, a receipt error and degradation are the same finding.
