# competitor-pulse — intake spec

Design of record: `maguyva-marketing/gc-actions/specs/competitor-profiles.md` §2c. Registry:
`gc-actions/specs/inputs/competitor-registry.yaml` (schema_version 2, read from `$MAGUYVA_REPO`,
default `~/projects/maguyva-marketing`).

**2026-10-21 addendum:** Reddit mentions joined the sources after Generalissimo asked why competitor
information still needed manual runs. `collect.py` now calls RedditGrow `/reddit/search` once per
registry row (tracked and watch) for the first `brand_terms` entry, last 30 days, and keeps a post
only when the term appears as a whole word AND the post reads as developer talk (a dev subreddit or
developer vocabulary); RedditGrow's own monitor matched "Amp" on a dog video, so the gate is ours.
Numbers land in `pulse.json` (`mentions.reddit_30d`, `mentions.reddit_top`, `reddit[]`), new posts
print as `NEW reddit` lines, and a `KNOWN COMPETITORS` line lets the engine raise
`pulse:unregistered-tool:<tool>` for a tool named on Reddit that no registry row covers (the verdict
stays Generalissimo's, design §4). The key is `REDDITGROW_API_KEY`, lifted from the repo `.env` by
`precheck.sh` (that single variable, never the whole file, never printed). Budget: 21 calls per
firing against RedditGrow's 100/hour.

## 1. Purpose & stop condition
Give each monitored competitor's profile card a weekly "latest" feed, Reddit mentions and traction numbers. A firing is done when `pulse.json` is written, the cache advanced and, if anything was new, a validated contract delivered. A finding resolves when its condition stops appearing in the block.

## 2. Agentic pattern
Outer Human-in-the-loop, one interpretation step inside. No iteration across firings.

## 3. Type & data flow
Agent. `precheck.sh` runs `collect.py`, which does every network read and writes `$OUT_DIR/pulse.json` (full standing numbers, spec §2c shape). Stdout carries only what is new versus `cache/` (stars delta, new release urls, new news urls, HN 90-day count and its delta, Reddit 30-day count and new Reddit post urls), at most 1,500 characters per competitor and 48 KiB total, then an `ERRORS:` section. Empty stdout means nothing new and no errors, so the engine is skipped. The engine writes `latest` bullets and a `signal` line per competitor. Growth Console `competitor_pulse.load_pulse()` joins `pulse.json` with the engine's metrics and drops any bullet whose url was not collected.

## 4. Cadence
Weekly Monday 11:30 local, after gtm-research (09:30). Stars, releases and news move on a weekly scale; a missed firing coalesces into one at wake.

## 5. Scope & exclusions
Registry rows with status `tracked` (GitHub, packages, Hacker News, Reddit, Tavily news, `news_urls` pages) and `watch` (GitHub, packages, Hacker News, Reddit only). `rejected` rows are never read. No authenticated browsing, no paid SEO API, no mutation.

## 6. Guardrails
Report/propose-only. The precheck block is the only ground truth; `latest` urls must be copied from it. Output is public data, treated as untrusted text. The Tavily key is read from the exact field `mcp_servers.tavily.env.TAVILY_API_KEY` of `~/.codex/config.toml`, scrubbed, never printed; the RedditGrow key is the single `REDDITGROW_API_KEY` line of the repo `.env`, exported by precheck.sh, never printed; error strings are short reasons (`http 403`, `timeout`), never URLs or bodies.

## 7. Permission axes + justification
report_only / none / none / none (the floor). GitHub calls are read-only public-metadata GETs under Generalissimo's own ytubecoder account (`gh auth token`, exported as GITHUB_TOKEN by precheck.sh; same pattern dev-asks-watch uses); 403/429 becomes a `github: rate limited` error row. All network reads are in the unsandboxed precheck, anonymous except one Tavily search per tracked competitor. The engine needs no network, exec or write access. No dangerous combination is approached.

## 8. Finding identity
One finding per competitor and condition: `pulse:<condition>:<slug>` with condition release, news, traction-jump, reddit or source-unavailable; plus `pulse:unregistered-tool:<tool>` for a tool named on Reddit that is not a registry row. No timestamps, tags or counts.

## 9. Tier-1 semantics
ok: collected with no errors. warn: some sources failed (also the severity of source-unavailable and traction-jump findings). alert: every source failed. A registry that cannot be read exits precheck 1 (precheck-failed).

## 10. Tier-2 metrics + panels
`metrics` is a JSON string `{"pulse": {"<slug>": {"latest": [{"text","url"}], "signal"}}}`; the harness flattens it to `pulse.<slug>.latest` and `pulse.<slug>.signal`. GC reads the original from the run's `contract.json`. `$OUT_DIR/pulse-summary.json` is NOT written: codex runs in a read-only sandbox under `report_only` (INTERFACES §7.2), so the metrics string is the only engine channel. `dashboard.json` has no panels.

## 11. Engine/model + budget
Codex default, timeout 900 seconds, one retry on transient failure. A few thousand tokens per firing, zero when nothing is new. Collection makes roughly 80 requests (GitHub 2 per repo, 4 package registries, 2 HN queries, 1 RedditGrow search per row, 1 Tavily search per tracked row) at 1 second pacing, inside the precheck ceiling.

## 12. Page output
None. Growth Console `/competitors` is the review surface.
