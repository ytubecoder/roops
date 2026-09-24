# gtm-synthesis — prompt

Diagnose a deterministic GTM snapshot refresh failure from the precheck output.
Do not retry, mutate files, approve a proposition, access inbox data, or infer
missing evidence. Report the concrete failing condition and the human action.

## Output contract

Your final message MUST be a single JSON object conforming exactly to
`contract/contract.schema.json` — schema_version, run_id, status,
status_reason, headline, report_markdown, metrics, findings. No prose
outside that JSON object.

- `run_id` MUST equal the value from the `## RUN CONTEXT` block the runner
  appends to this prompt — copy it exactly; never invent your own.
- `metrics` MUST be a JSON **string** containing a serialized JSON object
  (e.g. the string `"{}"` when there is nothing to report) — not a nested
  JSON object.
- `findings` is required but MAY be an empty array.

## Findings prompt contract

1. Re-emit a still-true finding with its **same `finding_id`** — never
   invent a new id for a recurring condition.
2. Do not re-argue a `DISMISSED` finding unless the underlying situation has
   **materially changed**; if it has, say what changed.
3. Still emit `SNOOZED` findings if true — suppression is the runner's job,
   not the model's.

## Finding identity

Use `gtm-synthesis:<condition>` where condition is one of `probe-transport`,
`snapshot-refresh`, `candidate-synthesis`, or `unknown`. Never include a time,
run id, count, file line, or transient exception text.
