# research-replies — intake spec

1. Purpose & stop condition
Surface every reply, bounce and unsubscribe to the founder-led user-research
emails (cohort 2 to never-linked signups, cohort 3 to repo-linked ones) (send plan: maguyva-marketing `growth-console/brands/maguyva/outreach/
user-research-send-plan.md`) so Generalissimo never reads mailbox@maguyva.ai by
hand. Per firing: "done" = every new inbox message since the probe's UID cursor
is bucketed and each reply/bounce/unsubscribe is a finding with the reply quoted
and a proposed research coding. Cross-run: a reply finding is a one-shot alarm
(it resolves on the next run); the durable record is `user-research-replies.csv`
on llm, which the probe appends to. The loop is retired (uninstalled) when
replies stop, roughly six weeks after the send.

2. Agentic pattern
Outer shape Human-in-the-loop (v1 mapping). Inside one invocation: plain
interpretation of precheck text — read each quoted reply, decide the answer
number, propose the coding, judge whether a human reply is warranted. No
iteration across firings. v2 aspiration (recorded, not built): let the engine
write its proposed coding back into the CSV columns; today a human copies it.

3. Type & data flow (precheck gathers vs engine interprets)
`type=agent`. Precheck calls `probe:research-replies-read` on llm, which does
everything deterministic: IMAP `BODY.PEEK` of INBOX since the send start and
above the UID cursor; sender→send-list matching (From address, else
In-Reply-To/References against the send log's Message-IDs); bucketing into
reply / bounce / auto-reply / unsubscribe / unmatched by headers and subject
patterns; quoted-history stripping; cohort from the send-list repo_linked column; CSV append
keyed by UID (hand-coded columns untouched); unsubscribe marks on the send list;
cursor move. Precheck prints only what is new; empty stdout when nothing is
new (skipped-precheck, zero tokens). The engine interprets: the answers to the
two (cohort 2) or three (cohort 3) open questions, the proposed coding,
severity, and whether a human should reply.

4. Cadence
`interval:4h` for the first week after the send (replies cluster in the first
72 h; a same-day human reply to someone who named a broken step is the point).
After ~2026-10-01 edit to `daily:09:30` and reinstall. Staleness: a missed
firing during sleep is simply the next one 4 h later; nothing compounds because
the cursor moves only after a successful probe read.

5. Scope & exclusions
In scope: INBOX of mailbox@maguyva.ai, messages dated on/after the first real
send. Excluded: any other folder (Sent, Spam, the `phish-IOC` label), any
message before the send window, cohorts 2/3 (never sent), the optional
follow-up question (a manual decision the finding surfaces; never sent by the
loop), and every mailbox mutation — the probe never marks read, replies,
forwards or deletes.

6. Guardrails
Verbatim from the send plan: "Replies are monitored by a loop, not by him.
Findings alert him; he never reads the inbox by hand." · "Read-only: never
marks read, never replies, never deletes." · "The optional follow-up question in
the emails doc is a manual decision; the finding surfaces it, the agent never
sends it unasked." From the emails doc: "Do not treat response counts as market
prevalence. Code themes, preserve exact quotes." Project-wide: report/propose-
only; never mutate outside `$LOOPS_ROOT` from the engine.

7. Permission axes + justification
`perm_fs_write=report_only`, `perm_network=none`, `perm_local_exec=none`,
`perm_remote_mutation=none` — the floor. The IMAP read and the two local
CSV writes happen inside the probe on llm (trusted reviewed code, header
`probe-writes` states them; `no-record` arg for a look-only read). No
dangerous combo approached. The engine sees reply text and email addresses of
real users in its prompt; that is the job, and the report stays on the loops
hosts (no page output, §12).

8. Finding identity (what a finding IS + finding_id derivation rule)
A finding is one person's reply, one bounced address, one unsubscribe, one
reply-looking unmatched sender, or one probe error category. Ids:
`reply:<email>` · `bounce:<bounced_email>` (or
`bounce-uid-<uid>:unknown-recipient`) · `unsubscribe:<email>` ·
`unmatched:<domain>` · `probe:<category>`. Emails lower-case. No timestamps,
counts, run ids or reply text in an id. Same rule in prompt.md.

9. Tier-1 semantics (ok/warn/alert meaning)
`ok`: only positive/still-using replies, thank-yous, auto-replies or
unmatched noise. `warn`: at least one reply naming a barrier or a reason
they stopped, any unsubscribe, or 3–9 new bounces. `alert`: any probe error (IMAP auth/connect,
cursor) or 10+ new bounces in one run (deliverability problem — stop the send
and look). A firing with nothing new never reaches the engine
(skipped-precheck, amber, expected most of the time).

10. Tier-2 metrics + panels
`replies.total` (number + 30-day trend), `replies.rate_pct` (number),
`replies.new` (number), `bounces.new` (number, warn 3 / alert 10),
`unsubscribes.total` (number, warn 5 / alert 15), `replies.by_cohort` (table of
{cohort,count}). Declared in dashboard.json.

11. Engine/model + budget
`engine=codex`, model blank (engine default). Expected a few thousand tokens per
invoked run (a handful of short replies quoted twice); zero on the common
nothing-new firing. `retry_transient` default 1. `timeout_s=300` (the probe
itself caps at 180 s).

12. Page output
None. Findings + `latest.md` are enough; the replies CSV on llm is the record.
