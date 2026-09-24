# gtm-synthesis — intake spec

1. Purpose & stop condition
Refresh the private GTM evidence snapshots daily and report failure. A firing is done when the probe returns status ok with updated/unchanged synthesis; cross-run failure is done when the same condition disappears.

2. Agentic pattern
Outer Human-in-the-loop. The successful path is deterministic and zero-token; a single engine invocation diagnoses a failed probe, without retrying or repairing.

3. Type & data flow (precheck gathers vs engine interprets)
Watchdog. precheck invokes the reviewed llm `gtm-refresh` probe, which atomically merges the approved baseline and anonymized research evidence, synthesizes the candidate, and writes health. The engine sees only failure output.

4. Cadence
`daily:10:15`: replies are checked every four hours, while a daily strategy projection is frequent enough and avoids meaningless intra-day churn.

5. Scope & exclusions
In scope: anonymized GTM snapshots and deterministic candidate/health refresh. Excluded: email sending, inbox reads, core-app access, campaign changes, human approval changes, and model-written positioning.

6. Guardrails
Report/propose-only. Never approve a proposition. Never expose identities or raw mailbox data. Never mutate outside the reviewed probe's private GTM snapshot directory.

7. Permission axes + justification
All engine axes remain at the floor: `report_only / none / none / none`. The reviewed probe performs bounded local writes on llm; this is the same trusted probe boundary used by research-replies.

8. Finding identity (what a finding IS + finding_id derivation rule)
A finding is one durable refresh failure. `gtm-synthesis:<condition>` with condition from probe-transport, snapshot-refresh, candidate-synthesis, unknown; no volatile values.

9. Tier-1 semantics (ok/warn/alert meaning)
ok = refresh and synthesis succeeded. alert = probe or refresh failed. warn is unused.

10. Tier-2 metrics + panels
No engine metrics on healthy runs; watchdog heartbeat is authoritative. Failure diagnosis may emit `{}`. No custom panels.

11. Engine/model + budget
Codex default model, zero tokens on success, at most a few hundred on failure, retry_transient=1, timeout_s=180.

12. Page output
None. The product output is Growth Console `/gtm`; the loop dashboard only exposes heartbeat/failure.
