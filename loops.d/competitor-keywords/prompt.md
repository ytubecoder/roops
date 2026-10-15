# competitor-keywords — prompt

`precheck.sh` has already run the Google Keyword Planner scan of every tracked
competitor's pages and every registry brand name, compared the result with the
previous run, and printed only the movement plus a 5-line standing summary.
**You cannot query Google and are not asked to.** The PRECHECK OUTPUT block is
your only ground truth. Do not speculate past it and do not invent a number.

## What the precheck lines mean

- **`NEW GAP KEYWORD: <kw> — N/mo`** — three or more competitors' pages now
  attract this keyword and ours do not. One finding. Severity `warn` when
  N >= 1,000, otherwise `info`. Say in one sentence why a developer typing it
  might (or might not) want a codebase-context tool; generic or platform terms
  ("github login", "pdf file") are noise, say so.
- **`DROPPED GAP KEYWORD: <kw> — was N/mo`** — it left the shared-gap list
  (a competitor stopped attracting it, or we now do). One `info` finding.
- **`BRAND VOLUME MOVE: <name> (<slug>) A -> B/mo (+P%)`** — total monthly
  searches for that brand's registry names moved by 25 % or more. Severity
  `warn` when |P| >= 50, otherwise `info`. Say which direction and that it is a
  Google estimate in rounded buckets.
- **`BASELINE CHANGE`** — the set of keywords Google associates with our own
  pages changed. One `info` finding, listing the added/removed examples given.
- **`FIRST RUN — baseline established`** — emit NO movement findings. Write a
  short report with the summary and the top shared-gap keywords listed, and
  set `status=ok`, `status_reason=baseline`.
- **`SUMMARY …` lines only (no movement lines)** — nothing moved. Emit exactly
  one `info` finding with `finding_id` `keywords:no-movement`, title
  "no movement", and the five summary facts in `detail`.
- **`INPUT GAP: …`** — the scan did not run. The runner normally stops before
  you see this; if you do, emit one `warn` finding
  `keywords:source-unavailable` quoting the line.

## Finding identity

A finding is **one keyword or one brand observed in one condition**.
`finding_id` is `keywords:<condition>:<subject>` style kept stable across
runs, using exactly these conditions:

- `keywords:new-gap` + `:<keyword>` — e.g. `keywords:new-gap:mcp server`
- `keywords:dropped-gap` + `:<keyword>`
- `keywords:brand-volume-move` + `:<slug>` — e.g. `keywords:brand-volume-move:cursor`
- `keywords:baseline-change`
- `keywords:no-movement` (only when nothing else moved)
- `keywords:source-unavailable`

Lower-case the keyword exactly as printed. Never embed dates, volumes,
percentages or run ids in the id; put them in `detail`. The same condition on
the same subject is the same finding across runs.

## Tier-1 semantics

- `ok` — only `info` findings (baseline, no movement, dropped keywords,
  small brand moves).
- `warn` — at least one `warn` finding: a new gap keyword of 1,000/mo or more,
  or a brand volume move of 50 % or more, or the source was unavailable.
- `alert` — never emitted.

`status_reason`: `baseline`, `no_movement`, `new_gap_keywords`,
`brand_volume_move`, `baseline_change`, `mixed`, `source_unavailable`.

## Report

`report_markdown`: a heading, the standing summary as a short list, then one
short section per movement type in the order the precheck printed them.
Plain operator prose. No advice on how to write pages or buy ads.

## Headline and metrics

`headline` is one line of at most 120 characters, e.g.
"3 new gap keywords (top: 'mcp server' 2,400/mo); Cursor brand +62%" or
"No movement: 25 shared gap keywords, 41 baseline keywords".

`metrics` is a JSON **string**: `"{\"gap.shared_count\": N, \"baseline.keywords\": N, \"changes.total\": N}"`
using the SUMMARY line numbers (shared gap, baseline) and the count of
movement lines you were given (`changes.total`, 0 when none).

## Output contract

Your final message MUST be a single JSON object conforming exactly to
`contract/contract.schema.json` — schema_version, run_id, status,
status_reason, headline, report_markdown, metrics, findings. No prose
outside that JSON object.

- `run_id` MUST equal the value from the `## RUN CONTEXT` block the runner
  appends to this prompt — copy it exactly; never invent your own.
- `metrics` MUST be a JSON **string** containing a serialized JSON object
  (`"{}"` when there is nothing to report), not a nested object.
- `findings` is required but MAY be an empty array.

## Findings prompt contract

1. Re-emit a still-true finding with its **same `finding_id`** — never
   invent a new id for a recurring condition.
2. Do not re-argue a `DISMISSED` finding unless the underlying situation has
   **materially changed**; if it has, say what changed.
3. Still emit `SNOOZED` findings if true — suppression is the runner's job,
   not the model's.
