# competitor-keywords — intake spec

1. Purpose & stop condition
Keep the Google-side picture of the competitive field current without a human running the scan: which keywords three or more competitors' pages attract that ours do not, how many people search each brand by name, and what Google thinks our own pages are about. Built 2026-10-15 (design of record: maguyva-marketing `gc-actions/specs/competitor-profiles.md` §2a). Per-firing "done": the scan ran, the diff against `cache/summary.json` was printed, and the engine wrote one finding per movement line (or one `no-movement` info). Cross-run "done": a `new-gap` finding resolves when the keyword leaves the shared-gap list or Generalissimo acks it; a `brand-volume-move` finding resolves when the next run no longer shows a 25 % move.

2. Agentic pattern
Outer shape Human-in-the-loop: the loop proposes "this keyword is now shared, this brand moved"; Generalissimo decides whether any page or campaign changes. Inside one invocation a single-shot interpretation of the precheck diff. Nothing to explore, no tool use.

3. Type & data flow (precheck gathers vs engine interprets)
`type=agent`. `precheck.sh` sources `$MAGUYVA_REPO/.env` (default `~/projects/maguyva-marketing`), runs `google-ads-tools/competitor-keyword-scan.py --out "$OUT_DIR/keyword-scan"` (url-seeded ideas per tracked seed URL, historical metrics for registry `brand_terms` plus maguyva), then a python step reads `results.json` and diffs against `cache/summary.json`: shared-gap keyword set (3+ competitors, not us, volume >= 50) with max volume, per-slug brand volume totals, our baseline keyword set. Prints NEW GAP KEYWORD, DROPPED GAP KEYWORD, BRAND VOLUME MOVE (>= 25 % and >= 20 searches absolute), BASELINE CHANGE lines and a 5-line SUMMARY. Stdout is never empty by design; the engine reports "no movement" when only the summary appears. First run prints the summary, "FIRST RUN — baseline established" and the top 10 shared-gap keywords. The cache is overwritten after printing. `results.json`, `REPORT.md`, `gap.csv` under `$OUT_DIR/keyword-scan/` are the contract for Growth Console.

4. Cadence
`monthly:03:21:00` local. Keyword Planner volumes are monthly buckets; more frequent runs only spend quota. Missed firings coalesce at wake. Staleness expectation about 35 days.

5. Scope & exclusions
In scope: `status: tracked` registry rows' `seed_urls` (cap 40 URLs per run), brand terms for tracked and watch rows, us = maguyva.ai pages. Geo US, language en. Excluded: rejected rows, any write to the ad account, any paid SEO API.

6. Guardrails
- Read-only Google Ads use: GenerateKeywordIdeas, GenerateKeywordHistoricalMetrics, GAQL constant lookups only. No campaigns, plans or criteria are created.
- Credentials are sourced into the precheck process only and never printed; the scan log stays in `$OUT_DIR/scan.log`, only its last lines show on failure.
- Report-only: the only writes are `cache/summary.json` and the runner-owned `$OUT_DIR`.
- Dismissed findings are suppressed by the runner; prompt.md carries the three findings rules verbatim.

7. Permission axes + justification
`perm_fs_write=report_only`, `perm_network=none`, `perm_local_exec=none`, `perm_remote_mutation=none` — the floor. Network and credentials live in `precheck.sh`, which is unsandboxed and not governed by the axes (LOOP_AUTHORING §4); the engine needs no network, shell or credential. No dangerous combo is approached.

8. Finding identity (what a finding IS + finding_id derivation rule)
A finding is one keyword or brand in one condition. `finding_id` = `keywords:<condition>[:<subject>]`, conditions `new-gap`, `dropped-gap`, `brand-volume-move`, `baseline-change`, `no-movement`, `source-unavailable`; subject is the lower-case keyword or the registry slug. No dates, volumes or run ids in the id. Identical rule in prompt.md.

9. Tier-1 semantics (ok/warn/alert meaning)
`ok`: only info findings. `warn`: a new gap keyword >= 1,000/mo, a brand volume move >= 50 %, or the source unavailable. `alert`: never emitted by the engine; a failed scan makes the precheck exit 1 (precheck-failed, zero tokens).

10. Tier-2 metrics + panels
`gap.shared_count` (trend), `baseline.keywords` (trend), `changes.total` (number per run, higher_is_worse, warn 10, alert 40). See `dashboard.json`.

11. Engine/model + budget
`engine=codex`, model blank. A few thousand tokens per month. `timeout_s=900` covers the scan (up to about 40 sequential Keyword Planner calls with throttling) plus the write-up.

12. Page output
None. Growth Console `/competitors` reads the run dir read-only for the keyword and brand panels.
