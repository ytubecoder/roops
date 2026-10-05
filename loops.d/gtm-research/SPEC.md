# gtm-research — intake

## 1. Purpose & stop condition
Gather current public evidence for GTM hypotheses. A firing ends with a validated source-backed report; a finding resolves when its evidence gap disappears.

## 2. Agentic pattern
Outer Human-in-the-loop, one interpretation inside. No autonomous action or iterative repair.

## 3. Type & data flow
Agent. Deterministic collect.py fetches four fixed GitHub issue searches, six records each, 18-second request timeouts and 1MB response caps. The engine sees only bounded source excerpts. GC pulls contract and inputs to validate provenance before publishing.

## 4. Cadence
Weekly Monday 09:30 host local time, before daily synthesis. Eight-day freshness tolerance; public issues do not justify daily churn.

## 5. Scope & exclusions
Public GitHub issue records for GitNexus, codebase-memory-mcp, Claude Context and Serena, updated in the last 30 days. No mailbox, identities, credentials, authenticated browsers, writes to services or campaign actions.

## 6. Guardrails
Report/propose-only. Never approve a proposition. Never expose identities or raw mailbox data. The browser never crosses the network.

## 7. Permission axes + justification
report_only / none / none / none. Only trusted precheck makes bounded public HTTPS GET requests. The model cannot access the network or mutate application state.

## 8. Finding identity
One recurring evidence gap/implication, gtm-research:<condition>, conditions in prompt. Source IDs are stable URL hashes, not finding IDs.

## 9. Tier-1 semantics
ok useful research, warn partial collection gaps, alert no usable sources. Failed collection never refreshes old evidence dates.

## 10. Tier-2 metrics + panels
research object inside metrics JSON string. GC renders it with query coverage and links. No numeric panels.

## 11. Engine/model + budget
Codex default, timeout 600 seconds, default retry_transient 1, several thousand tokens weekly. Four requests per firing, no paid search provider.

## 12. Page output
None; GC /gtm is the review surface. Contract report remains on the loop dashboard.
