# dev-asks-watch — prompt

`precheck.sh` has already read every issue and PR our work account has filed
in `Synacy/maguyva-frontend` and `Synacy/rise-data-platform` (the two repos
the Maguyva developers answer in), diffed them against the previous run, and
printed only what changed. **You are not being asked to check GitHub — you
cannot, and the precheck already did.** You are being asked to write the
changes up so Generalissimo can act on each one in a single read.

The PRECHECK OUTPUT block is your only ground truth. Do not speculate past
it, do not soften it, and do not invent an outcome a comment does not state.

## What the precheck lines mean

- **`NEW ASK FILED`** — an ask we filed since the last run. Informational;
  one `info` finding so the dashboard shows it is now being watched.
- **`NEW COMMENT from <login> (THEM)`** / **`NEW REVIEW:… (THEM)`** — a
  developer responded. This is the finding that matters: quote the excerpt,
  say what they appear to be asking or stating, and name the next action
  ("reply", "verify on prod", "nothing — acknowledgement only"). Severity
  `warn` when the ask is still OPEN and the latest word is theirs (they are
  waiting on us or have acted and we should verify); `info` when it is a
  closing acknowledgement.
- **`NEW COMMENT … (us)`** — our own follow-up. Not a finding on its own;
  mention it in the report only if it explains a state change.
- **`STATE OPEN -> CLOSED`** on an issue — closed is **not** the same as
  declined or delivered: the developers often hand-apply then close, and
  sometimes close as won't-do. Quote the closing comment if one is in the
  output; if there is none, say "closed without comment — outcome unknown,
  verify". Severity `warn` (someone must verify on production).
- **`STATE OPEN -> MERGED`** on a PR — delivered to main; `warn` until
  verified live, with "verify on production" as the action.
- **`STATE OPEN -> CLOSED`** on a PR (not merged) — not taken as-is; `warn`,
  action "read the closing comment, decide whether to re-raise".
- **`STALE — open with no activity for N days`** — nobody has touched it for
  N days. `info`, action "nudge the developer or decide it no longer
  matters". The precheck re-emits this every 14 further days; do not
  escalate it just because it recurs.
- **`VANISHED from listing`** — the item no longer appears in the author
  listing (deleted or transferred). `warn`, action "check the URL by hand".
- **`BASELINE`** — first run. Emit NO findings; write a short report that
  lists what is now watched (open asks first) and set `status=ok`.
- **`CURRENT OPEN ASKS (context)`** — context for your report, not a list
  of findings. `AWAITING US` flags where the last word is theirs.

## Finding identity

A finding is **one change on one ask**. `finding_id` = `<repo-short>#<number>:<condition>`
where `<repo-short>` is the repo name without the org (`maguyva-frontend`,
`rise-data-platform`), `<number>` is the issue/PR number, and `<condition>`
is exactly one of: `responded` (a developer comment or review arrived and
the ask is still open), `closed` (issue closed), `merged` (PR merged),
`closed-unmerged` (PR closed without merge), `stale`, `vanished`, `filed`.
Examples: `maguyva-frontend#711:responded`, `rise-data-platform#438:stale`.
Never embed dates, comment counts, run ids or excerpts in the id. The same
condition on the same ask is the same finding across runs; a second
developer comment on an already-`responded` ask re-emits
`…:responded` with the new excerpt in `detail` — it does not mint a new id.

## Tier-1 semantics

- `ok` — only `info` findings (a baseline, a new filing, a stale nudge, a
  closing acknowledgement).
- `warn` — at least one ask needs a human action now: a developer replied
  and is waiting, something merged/closed and must be verified, or an item
  vanished.
- `alert` — never emitted by this loop. Credential and transport failures
  are handled by the precheck exiting non-zero before you run.

`status_reason` is a short machine category: `baseline`, `dev_replied`,
`delivery_to_verify`, `stale_only`, `filed_only`, `mixed`.

## Report

`report_markdown`: a heading, then one short section per change in the
order the precheck printed them (ask title + link, what changed, quoted
excerpt, next action), then an "Open asks" list copied from the context
block with the AWAITING US flags kept. Plain operator prose. No advice
about how to build the fix — the developers own that.

## Metrics

Copy the numbers from the precheck's `## TOTALS` line into `metrics` as a
JSON **string**: `"{\"asks.open\": 2, \"asks.awaiting_us\": 1, \"asks.total\": 16, \"asks.changed\": 3}"`
where `asks.changed` is the number of change lines you were given (0 on a
baseline run). Use `"{}"` only if the TOTALS line is missing.

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
