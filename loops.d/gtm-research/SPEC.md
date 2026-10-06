# gtm-research — intake

## 1. Purpose & stop condition
Gather current public evidence for GTM hypotheses. A firing ends with a validated source-backed report; a finding resolves when its evidence gap disappears.

## 2. Agentic pattern
Outer Human-in-the-loop, one interpretation inside. No autonomous action or iterative repair.

## 3. Type & data flow
Agent. Trusted precheck saves the reviewed llm probe's output to inputs/public-search.json. Deterministic collect.py fetches the existing four fixed GitHub issue searches, six records each, 18-second request timeouts and 1MB response caps, then validates and merges the probe into inputs/research.json. Precheck stdout is capped at 64KiB UTF-8. GC pulls contract and inputs to validate provenance before publishing.

## 4. Cadence
Weekly Monday 09:30 host local time, before daily synthesis. Eight-day freshness tolerance; public issues do not justify daily churn.

## 5. Scope & exclusions
Public GitHub issue records for GitNexus, codebase-memory-mcp, Claude Context and Serena, updated in the last 30 days, plus public Reddit/web evidence about codebase context/retrieval, MCP tools and coding agents. No mailbox, customer responses, authenticated browsers, service mutations or campaign actions. Credentials remain on llm; only the reviewed probe reads them.

## 6. Guardrails
Report/propose-only. Never approve a proposition. Never expose identities or raw mailbox data. The browser never crosses the network.

## 7. Permission axes + justification
report_only / none / none / none. Trusted precheck performs GitHub HTTPS GETs; the llm probe performs search-only HTTPS POSTs to the fixed Tavily endpoint. The model cannot access the network, credentials or mutate application state.

## 8. Finding identity
One recurring evidence gap/implication, gtm-research:<condition>, conditions in prompt. Source IDs are stable URL hashes, not finding IDs.

## 9. Tier-1 semantics
ok successful collection including explicitly empty results, warn partial collection gaps, alert all queries failed. Partial success is model-eligible; all-source failure exits precheck 1. Probe/transport failures become four failed Tavily queries without hiding successful GitHub collection. Failed collection never refreshes old evidence dates. Output exceeding 64KiB fails closed with output_limit and no records.

## 10. Tier-2 metrics + panels
research object inside metrics JSON string. GC renders it with query coverage and links. No numeric panels.

## 11. Engine/model + budget
Codex default, timeout 600 seconds, default retry_transient 1, several thousand tokens weekly. Four GitHub requests plus four Tavily basic searches per firing, no search retries/pagination/fallback providers. Reviewed probe required on both hosts; main agent owns delivery and supervised live verification.

## 12. Page output
None; GC /gtm is the review surface. Contract report remains on the loop dashboard.

## Public search and wire contract

The probe reads only the existing `mcp_servers.tavily.env.TAVILY_API_KEY` field
from `~/.codex/config.toml`, using Python 3.11+ tomllib. No environment fallback,
config crawling, key copying or credential output. `--check` validates config
only, with no API call or writes. Four fixed searches: two restricted to
reddit.com and two excluding reddit.com/github.com. Basic depth, top five,
explicit 90-day start/end dates, no answers/raw content/images/auto parameters,
20-second request timeouts, 1MB response caps, no redirects. A 105-second process
alarm sits inside the probe server's 120-second timeout. Provider date filtering
does not independently verify freshness; missing publication dates stay null.

Input schema_version 1: generated_at, sources[], and merged families keyed by
public_github/public_reddit/public_web. Each source has source_type, query,
provider (github/tavily), method (issue_search/search), window
{start_date,end_date,days}, limit, status (ok/error), empty (boolean), nullable error,
result_count, unique_count, duplicate_count, rejected_count, records[].
result_count counts inspected top-N results, not provider total hits.
Record fields: id (pub- plus first 16 hex SHA256 exact URL), url, observed_at,
published_at (nullable), text. URLs require public HTTPS syntax; local/reserved
hosts, all IP literals, userinfo, credential query parameters and nonstandard ports are rejected.
Result URLs are never fetched. Title and content share one sanitized excerpt
budget of 22 words/300 characters; no separate title field is emitted.
Exact URL duplicates belong to the first query in fixed order; later queries
retain result/duplicate counts but never repeat the record.
Family counters: queries, successful_queries, failed_queries, empty_queries,
result_count, unique_count, duplicate_count, rejected_count. Sum unique_count
for observation denominators. Probe JSON uses the same envelope without families.
Error categories: config, auth, quota, http, network, timeout, size_limit,
malformed, transport, output_limit. Never emit exception messages or bodies.

Model output retains the research metrics schema with at most 18 unique
observations, chosen across relevant families without quotas. Each observation
requires sentiment (positive/negative/mixed/neutral/unknown) and short
sentiment_rationale, describing only
its cited excerpt. Technical issues are not generalized market/customer
sentiment. Polarity remains the separate positioning-hypothesis judgment.
No public_themes field. GC derives origin/theme counts from collected inputs
and validated observations. Public search snippets are not full threads and
never count as customer survey/mail responses or customer validation.
