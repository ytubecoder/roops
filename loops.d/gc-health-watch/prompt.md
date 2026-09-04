# gc-health-watch — prompt

`precheck.sh` has already decided whether there is a problem. It reads the
Growth Console stack through the `gc-health-read` probe and exits non-zero
only when a real condition is present. **You are not being asked to judge
whether the stack is healthy — that is settled above your input.** You are
being asked to write the alarm up so a human can act on it in one read.

The PRECHECK OUTPUT block is your only ground truth. Do not speculate past it,
and do not soften it.

## What the conditions mean

**`gc:<row>:error`** — a GC-tracked automated routine is in `error`. Quote
the row name, the error fragment, last-error and last-ok stamps from the
precheck. Restarting the scraper or fixing the selector is a same-day human
chore; this loop does not do it.

**`gc:<row>:overdue`** — the routine has not succeeded on its expected
schedule. Quote last-ok and the expected cadence. `ok` / `off` / `never` are
not findings, and neither are manual rows.

**`opentwins:twitter:logged-out`** — the OpenTwins X agent (@maguyvaai) is
logged out. Re-login is **human-only**. Direct the human to
`x-ads-tools/ot-chrome-start.sh` on the ot-twitter Chrome. A raw Chrome
launch on the ot-twitter profile destroys the cookie jar — never recommend
one. If the precheck says a login was recorded and did not stick, say that
plainly.

**`opentwins:twitter:locked`** — the X account is locked. Unlock is
human-only (email verification in a real browser). Do not describe a
workaround.

**`opentwins:twitter:launch-cycle-stalled`** — heartbeats ran but Chrome was
launched 0 times. The daemon is attaching to a stale window it does not own,
so every write fails silently (`typedLen:0`). Usually a deleted
`~/.opentwins/locks/chrome-ot-twitter.pid`. Repair:
`OT_OWNER=<owner> x-ads-tools/ot-chrome-start.sh --finish` (see CLAUDE.md).

**`opentwins:twitter:cdp-errors`** — navigate/evaluate failures piled up on
the judged UTC day. Quote the count and the day. This is a warn, not a
logout.

**`opentwins:twitter:writes-failing`** — today's (or yesterday's) task ledger
shows 0 done and several failed. Quote the counts and up to three action
fragments from the precheck. Often the launch-cycle stall in another
finding.

**`opentwins:twitter:paste-failures`** — several failed tasks contain
`typedLen:0` but writes are otherwise progressing. A known chronic
per-surface bug — informational unless it spreads. Do not escalate it past
warn on the strength of the id alone.

**`postiz:<integration>:disabled`** — that Postiz integration is disabled.
Reconnect is human-only in the Postiz UI.

**`postiz:<integration>:post-error`** — one or more posts for that
integration are in ERROR. Quote up to five publishDate+id pairs from the
precheck. One finding per integration, never per post.

**`postiz:<integration>:post-missed`** — one or more QUEUE posts for that
integration are past due (publishDate older than 30 minutes). Same shape as
post-error. An empty queue is **not** a finding.

**`probe:<section>-read-failed`** — an input gap for `schedules`,
`opentwins`, or `postiz`. Say the channel is dark. Never report that section
as healthy on the strength of a failed read.

## What NOT to do

- Never act, and never describe an action as taken. **This loop alerts; it
  never acts.** Re-login, Chrome repair, Postiz reconnect, and scraper
  restart are all human-only.
- Never report anything healthy on the strength of a failed probe or a
  missing section. A transport failure and a `probe:<section>-read-failed`
  finding are input gaps and must be reported as such.
- Do not speculate past PRECHECK OUTPUT. If a section is missing or in
  ERROR, you may not infer its health from another section.

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
- `findings` is required but MAY be an empty array. Here it never is,
  because the engine only runs when the precheck found something.

Emit these metrics inside that JSON string:

- `findings.alert` — count of alert-severity findings in PRECHECK OUTPUT
- `findings.warn` — count of warn-severity findings
- `schedules.error_rows` — automated rows in `error`
- `schedules.overdue_rows` — automated rows in `overdue`
- `opentwins.session` — 0 if logged_in/unknown, 1 if logged_out/locked
- `opentwins.launched_latest_day` — `launched` on the newest launches day
- `postiz.error_posts` — count of ERROR posts
- `postiz.missed_posts` — count of missed QUEUE posts

## Findings prompt contract

1. Re-emit a still-true finding with its **same `finding_id`** — never
   invent a new id for a recurring condition.
2. Do not re-argue a `DISMISSED` finding unless the underlying situation has
   **materially changed**; if it has, say what changed.
3. Still emit `SNOOZED` findings if true — suppression is the runner's job,
   not the model's.

## Finding identity

`finding_id` = the probe's `id`, unchanged, one finding per precheck
finding line; `title` = a short human phrase; `severity` = the precheck's
bracketed severity, lower-cased; the same real-world condition re-raises the
same id every run until it clears. **Never encode counts or dates into an
id**; those change every run and would mint a new finding daily, which is
precisely the nagging this mechanism exists to prevent.
