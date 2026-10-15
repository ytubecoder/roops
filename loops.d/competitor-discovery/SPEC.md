# competitor-discovery — intake spec

1. Purpose & stop condition
Surface tools that may compete with Maguyva and are not yet in `gc-actions/specs/inputs/competitor-registry.yaml` (tracked, watch or rejected), including the structural-search / self-hosted code-search class. Per-firing "done": every candidate unseen in earlier runs is reported once as a finding; a quiet week prints nothing and the engine is skipped. Built 2026-10-15 from `gc-actions/specs/competitor-profiles.md` §2b.

2. Agentic pattern
Outer shape human-in-the-loop: the loop proposes candidates, Generalissimo gives the verdict (accept = `tracked`, reject = `rejected` with a reason). Inside one invocation the engine does single-shot labelling of a short printed list. No exploration.

3. Type & data flow (precheck gathers vs engine interprets)
`type=agent`. `precheck.sh` runs `scripts/competitor-discovery.py --out-root $OUT_DIR/discovery --max-candidates 40` (11 anonymous GitHub queries paced 6.5 s, 6 Tavily queries), diffs `candidates.json` against `cache/seen.json` (`{key: {first_seen, best_score, name}}`, key = repo or domain, lower-case) and prints only new candidates, the top-5 standing list and a stats row. Empty stdout = skipped. Output contract for Growth Console: `$OUT_DIR/discovery/<date>/candidates.json`.

4. Cadence
`weekly:tue:10:30` local. Missed firings coalesce; the diff is against the cache, so nothing is lost. Staleness expectation 8 days.

5. Scope & exclusions
In scope: GitHub repository search and Tavily web search for codebase context / code intelligence / structural search tools. Excluded: anything already in the registry (any status, by repo or domain suffix), aggregator and social domains, repos under 300 stars or idle over 120 days (script filters).

6. Guardrails
- Never print the Tavily key. The script reads the exact field `mcp_servers.tavily.env.TAVILY_API_KEY` of `~/.codex/config.toml` and echoes no provider error bodies.
- GitHub reads use `gh auth token -u ytubecoder` (Generalissimo's own account, read-only GETs) when that login exists on the host, anonymous otherwise; the token is never printed. Added 2026-10-15 after the anonymous 60/h limit was exhausted by three competitor prechecks sharing one IP.
- Never edit the registry: the verdict is Generalissimo's (design §4).
- Writes: only the runner-owned `$OUT_DIR` and this loop's `cache/seen.json`.
- Dismissed findings are suppressed by the runner; prompt.md carries the three findings rules verbatim.

7. Permission axes + justification
`perm_fs_write=report_only`, `perm_network=none`, `perm_local_exec=none`, `perm_remote_mutation=none` — the floor. All network use is in `precheck.sh` (unsandboxed, LOOP_AUTHORING §4); the engine needs none. No dangerous combo: no exec allowlist, no credential_env, no remote mutation.

8. Finding identity (what a finding IS + finding_id derivation rule)
A finding is one new candidate. `finding_id` = `discovery:candidate:<key>`, key = repo or domain lower-case with `/` replaced by `-`. Search failures: `discovery:source-unavailable` (one per run). No dates, scores or star counts in ids.

9. Tier-1 semantics (ok/warn/alert meaning)
`ok`: only info findings. `warn`: a candidate with stars >= 5000 or score >= 60, or a failed query. `alert`: never emitted by the engine; a missing script/registry or a script crash makes the precheck exit 1 (precheck-failed).

10. Tier-2 metrics + panels
`candidates.total` (trend, hold), `candidates.new` (number, neutral), `queries.ok`, `queries.failed` (number, higher_is_worse, warn 1, alert 4). A quiet week skips the engine, so these metrics are absent (gap).

11. Engine/model + budget
`engine=codex`, model blank. A first run with up to 40 new candidates is a few thousand tokens; later weeks are usually zero. `timeout_s=700`: the supervised run took 95 s precheck + 224 s engine for 40 new candidates (319 s total).

12. Page output
None. The findings view and Growth Console `/competitors` (reads `candidates.json`) are the product.
