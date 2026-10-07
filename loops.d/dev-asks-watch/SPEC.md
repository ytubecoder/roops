# dev-asks-watch — intake spec

1. Purpose & stop condition
Catch movement on the developer asks we have filed for Maguyva — every issue and PR authored by our work account (tomokagami = tom@utbox.net) in `Synacy/maguyva-frontend` and `Synacy/rise-data-platform` — so nothing a developer says, closes or merges sits unread, and nothing we filed goes quietly stale. Built 2026-10-07 after frontend#711. Per-firing "done": the precheck found no change (skipped-precheck, zero tokens) or the engine wrote up every change line it was given as one finding each. Cross-run "done": a finding resolves when the condition stops being true — a `responded` finding resolves once we reply (the latest word is ours again) or the ask closes; `closed`/`merged` findings resolve when Generalissimo acks them; `stale` resolves on any new activity.

2. Agentic pattern
Outer shape Human-in-the-loop (v1 rule): the loop proposes "read this / verify that / nudge", Generalissimo acts via GitHub or loopctl dispositions. Inside one invocation the engine does a single-shot interpretation of precheck output — no ReAct, nothing to explore. v2 aspiration, deliberately not built: auto-verifying a merge on production (curl the live site) — that would need network for the engine and judgment about what "verified" means per ask; it stays a human step.

3. Type & data flow (precheck gathers vs engine interprets)
`type=agent`. `precheck.sh` does all the work: `gh issue list` + `gh pr list` (`--author tomokagami --state all`, JSON incl. comments and reviews) per repo, normalised into a snapshot keyed `<repo-short>#<number>`, diffed against `cache/snapshot.json`, printing only: new asks, state changes (OPEN→CLOSED/MERGED), new comments/reviews with a 400-char excerpt and whether the author is us or them, stale nudges (open, untouched ≥14d, re-emitted every further 14d via a bucket counter), vanished items, plus a "current open asks" context block and a TOTALS line. Empty stdout when nothing changed → skipped-precheck. First run with no cache prints a BASELINE listing. The engine only labels each change (finding), quotes the excerpt, names the next action, and copies TOTALS into metrics. Cache is overwritten after a successful fetch+diff; if the engine run then fails, that diff is not replayed — the dated copy under `$OUT_DIR/inputs/` (raw.jsonl + snapshot.json) is the audit trail. Known gap accepted for v1, same as competitor-watch.

4. Cadence
`daily:09:30` local, right after gc-health-watch (09:15) so the morning reads arrive together. Developer replies are a same-day matter but never a same-hour one. Missed firings coalesce into one at wake (launchd calendar semantics); the diff is against the cache, not against "yesterday", so a coalesced firing still reports everything that moved. Staleness expectation 24h.

5. Scope & exclusions
In scope: issues and PRs authored by `tomokagami` in `Synacy/maguyva-frontend` and `Synacy/rise-data-platform`, all states (closed/merged ones are tracked so a reopen or late comment is caught). Excluded: anything we did not author (other people's issues, dev-initiated PRs like frontend#680 — those are tracked by hand in `prod-queue/WATCH.md`); the public `ytubecoder` account (has filed nothing in these repos; add to `AUTHORS` in precheck.sh if that changes); any write to GitHub; any judgement about whether a merged change actually works on production (human verification, see §2). The project-wide maguyva hard-exclude does not apply — this loop reads only our own tickets' metadata and comments.

6. Guardrails
- "Never act as the tom@utbox.net GitHub account unless Generalissimo explicitly asks for it … Standing exception: checking the status of OUR OWN outstanding asks, read-only, needs no permission." (memory feedback_never_use_utbox_github_unasked) — this loop is exactly and only that exception.
- "Report/propose-only. Never mutate outside `$LOOPS_ROOT`." The only writes are `cache/snapshot.json` inside the loop dir and the runner-owned `$OUT_DIR`.
- "Never switch the active gh account." Token fetched per run with `gh auth token -u tomokagami`, exported for the precheck process only, never printed or logged.
- "Closed ≠ declined: devs often hand-apply then close" (memory project_prod_queue_pr_outcomes) — embedded in prompt.md's closed-state wording.
- Dismissed findings are suppressed by the runner; prompt.md carries the three findings rules verbatim.

7. Permission axes + justification
`perm_fs_write=report_only`, `perm_network=none`, `perm_local_exec=none`, `perm_remote_mutation=none` — the floor. The GitHub reads live in `precheck.sh`, which is unsandboxed and not governed by the axes (LOOP_AUTHORING §4); the engine needs nothing. No dangerous combo is approached: no exec allowlist, no credential_env, no remote mutation. Read-only scoping of the credential itself is not available (the stored token is the account's full gh OAuth token), so the belt-and-braces is the precheck containing only `gh issue list`/`gh pr list` invocations and the engine having no shell at all — the model physically cannot run a `gh` command.

8. Finding identity (what a finding IS + finding_id derivation rule)
A finding is one change on one ask. `finding_id` = `<repo-short>#<number>:<condition>`, `<condition>` ∈ {`responded`, `closed`, `merged`, `closed-unmerged`, `stale`, `vanished`, `filed`}. Example `maguyva-frontend#711:responded`. No dates, counts, excerpts or run ids in the id; a further developer comment on an already-responded ask re-emits the same id with a new `detail`. Identical rule in prompt.md `## Finding identity`.

9. Tier-1 semantics (ok/warn/alert meaning)
`ok`: only info findings (baseline, new filing, stale nudge, closing acknowledgement). `warn`: a human action is due — a developer replied and the latest word is theirs, a merge/close needs production verification, a PR was closed unmerged, an item vanished. `alert`: never emitted by the engine; credential/transport failure makes the precheck exit 1, which the runner records as precheck-failed (alert) with zero tokens.

10. Tier-2 metrics + panels
`asks.open` (number, neutral), `asks.awaiting_us` (number, higher_is_worse, warn 1, alert 3), `asks.total` (number, neutral), `asks.changed` (number per run, neutral). `dashboard.json` renders `asks.awaiting_us` and `asks.open` as `number` panels with `missing: hold` (a skipped day carries the last value), `asks.changed` as a 30-day `trend` with `missing: gap`.

11. Engine/model + budget
`engine=codex`, `model=` blank (engine default; the job is labelling a short diff). Expected tokens on a changed day: a few thousand (precheck output is capped by excerpt length; ~16 asks today). Quiet days: zero (skipped-precheck). `retry_transient` default 1. `timeout_s=300` — two gh listings per repo and a short write-up; 900 would be the ceiling, not the need.

12. Page output
None. The findings view and `latest.md` are the whole product; the human acts on GitHub, not on a page.
