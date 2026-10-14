# gtm-research — intake

## 1. Purpose & stop condition
Gather current public evidence for GTM hypotheses and refresh the per-competitor voice of user weekly. A firing ends with a validated source-backed report; a finding resolves when its evidence gap disappears.

## 2. Agentic pattern
Outer Human-in-the-loop, one interpretation inside. No autonomous action or iterative repair.

## 3. Type & data flow
Agent. Trusted precheck saves the reviewed llm probe's output to inputs/public-search.json: the four fixed positioning searches, the tracked competitor registry it read from the maguyva-marketing checkout on llm, and bounded per-competitor voice searches. Deterministic collect.py fetches the four fixed GitHub issue searches (six records each, 18-second request timeouts, 1MB response caps), re-validates the probe output, runs one more GitHub issue search per registry repo not already covered (at most 8 extra), tags every source with its competitor slug, builds a per-competitor index and merges everything into inputs/research.json. Precheck stdout is capped at 64KiB UTF-8; collect.py trims whole records explicitly (voice first) before failing closed. GC pulls contract and inputs to validate provenance before publishing research and replacing the loop-sourced voice rows on /competitors.

## 4. Cadence
Weekly Monday 09:30 host local time, before daily synthesis. Eight-day freshness tolerance; public issues do not justify daily churn. The /competitors voice snapshot is stamped on import so its 168-hour freshness window refreshes weekly.

## 5. Scope & exclusions
Public GitHub issue records for every `tracked` registry competitor with repos (the fixed four — GitNexus, codebase-memory-mcp, Claude Context, Serena — plus registry extras such as ref-tools-mcp and CodeGraph), updated in the last 30 days; public Reddit/web evidence about codebase context/retrieval, MCP tools and coding agents; and per-competitor voice searches by `voice_hints` for tracked competitors without repos (Sourcegraph, ref.tools Plans, Augment, Cursor), restricted to reddit.com, news.ycombinator.com, forum.cursor.com and github.com. GitHub Discussions are out of scope (no REST search index). `watch` and `rejected` registry rows are never searched. No mailbox, customer responses, authenticated browsers, service mutations or campaign actions. Credentials remain on llm; only the reviewed probe reads them.

## 6. Guardrails
Report/propose-only. Never approve a proposition. Never expose identities or raw mailbox data. The browser never crosses the network. Voice entries cite only collected URLs; GC rejects any other.

## 7. Permission axes + justification
report_only / none / none / none. Trusted precheck performs GitHub HTTPS GETs; the llm probe performs search-only HTTPS POSTs to the fixed Tavily endpoint and reads one fixed registry file. The model cannot access the network, credentials or mutate application state.

## 8. Finding identity
One recurring evidence gap/implication, gtm-research:<condition>, conditions in prompt. Source IDs are stable URL hashes, not finding IDs.

## 9. Tier-1 semantics
ok successful collection including explicitly empty results, warn partial collection gaps, alert all research queries failed. Partial success is model-eligible; all-research-source failure exits precheck 1 (voice sources alone never decide exit). Probe/transport failures become four failed Tavily queries plus an errored registry (no voice sources) without hiding successful GitHub collection. A defective registry or voice block fails only that block; the positioning sources survive. Failed collection never refreshes old evidence dates. Records dropped to fit 64KiB are counted on their source (`truncated`, `truncated_count`) and in `families`; only an untrimmable output fails closed with output_limit and no records.

## 10. Tier-2 metrics + panels
research object inside metrics JSON string, plus competitor_voices[] (one row per searched competitor). GC renders research with query coverage and links and replaces loop-sourced voice rows on /competitors. No numeric panels.

## 11. Engine/model + budget
Codex default, timeout 600 seconds, default retry_transient 1, several thousand tokens weekly. Up to 12 GitHub requests plus four positioning Tavily searches and at most 12 voice Tavily searches per firing (currently 9 from the registry), no search retries/pagination/fallback providers. Probe timeout 180 seconds (165-second internal alarm; earlier searches survive a deadline, later ones are explicit timeouts) inside the 300-second precheck ceiling. Reviewed probe required on both hosts; main agent owns delivery and supervised live verification.

## 12. Page output
None; GC /gtm is the review surface for research and /competitors for voices. Contract report remains on the loop dashboard.

## Public search and wire contract

The probe reads only the existing `mcp_servers.tavily.env.TAVILY_API_KEY` field
from `~/.codex/config.toml`, using Python 3.11+ tomllib, and the tracked rows of
`gc-actions/specs/inputs/competitor-registry.yaml` under `$MAGUYVA_REPO`
(default `~/projects/maguyva-marketing`) with PyYAML `safe_load` (200KB cap).
No environment fallback for the key, config crawling, key copying or credential
output. `--check` validates config and registry only, with no API call or
writes; a missing registry or PyYAML is unmet. Four fixed searches: two
restricted to reddit.com and two excluding reddit.com/github.com. Then one
voice search per `voice_hint` of each tracked competitor without repos, in
registry order, capped at 12 (`voice_plan.truncated` says when the cap bit),
restricted to reddit.com, news.ycombinator.com, forum.cursor.com and github.com.
Basic depth, top five, explicit 90-day start/end dates, no answers/raw
content/images/auto parameters, 20-second request timeouts, 1MB response caps,
no redirects. A 165-second process alarm sits inside the probe's 180-second
timeout; searches already finished survive, the rest are `timeout`. Provider
date filtering does not independently verify freshness; missing publication
dates stay null.

Input schema_version 1: generated_at, sources[], voice_sources[], registry,
voice_plan, github_plan, competitors[] and merged families keyed by
public_github/public_reddit/public_web/public_voice. Each source has
source_type, query, competitor (registry slug or null), provider
(github/tavily), method (issue_search/search), window
{start_date,end_date,days}, limit, status (ok/error), empty (boolean), nullable
error, result_count, unique_count, duplicate_count, rejected_count, truncated
(boolean), truncated_count, records[]. result_count counts inspected top-N
results, not provider total hits, and equals unique + duplicate + rejected +
truncated. Record fields: id (pub- plus first 16 hex SHA256 exact URL), url,
observed_at, published_at (nullable), text. URLs require public HTTPS syntax;
local/reserved hosts, all IP literals, userinfo, credential query parameters and
nonstandard ports are rejected. Result URLs are never fetched. Title and content
share one sanitized excerpt budget of 22 words/300 characters; no separate title
field is emitted. Exact URL duplicates belong to the first query in fixed order
(research families share one set; voice dedupes per competitor); later queries
retain result/duplicate counts but never repeat the record.
`registry` is {status, error, updated, competitors[{name, slug, status, repos[],
voice_hints[]}]} with at most 40 tracked rows, slugs `[a-z0-9-]`, names 80
characters, 5 repos and 6 hints each. `competitors[]` (collect.py) adds per
competitor: queries, failed_queries, truncated_records, record_ids[], url_count
and status (ok/empty/partial/error/unsearched).
Family counters: queries, successful_queries, failed_queries, empty_queries,
result_count, unique_count, duplicate_count, rejected_count, truncated_count.
Sum unique_count for observation denominators. Probe JSON uses the same envelope
without families, github_plan or competitors.
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

`competitor_voices[]` carries one row per searched competitor: competitor
(registry display name, exact), liked[] and disliked[] (at most 4 paraphrases
each), implication (one sentence), confidence (low/medium/high), evidence_links[]
(collected record URLs tagged with that competitor only) and observed_at. GC
rejects an unknown competitor name, any URL not collected for that competitor,
and repeated competitors; drops and counts identity-bearing rows; caps
confidence (low under 3 distinct URLs, medium under 5); then replaces the
loop-sourced voice row by competitor name (fresh liked/disliked/implication/
confidence/observed_at = run finished_at) keeping the union of evidence links
capped at the newest 12, and stamps the snapshot generated_at with the import
time. An invalid voices payload records an error in learning-status.json and
keeps the previous competitors.json.
