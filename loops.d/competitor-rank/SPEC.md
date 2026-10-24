# competitor-rank — intake spec

Authored 2026-10-24. Design of record: `maguyva-marketing/gc-actions/specs/competitor-profiles.md`
(the `/competitors` profile cards). Registry: `gc-actions/specs/inputs/competitor-registry.yaml`
(schema_version 2, read from `$MAGUYVA_REPO`, default `~/projects/maguyva-marketing`). This loop
turns the data competitor-pulse and gtm-research already collect into a weekly relevance score per
competitor, so the profile cards can be ordered by how much each one matters to Maguyva rather
than by registry order alone.

## 1. Purpose & stop condition
Score every non-rejected registry competitor and every open discovery candidate on four
components (features, price, positioning, sentiment) plus a tier. A firing is done when the
contract validates and `metrics.rank` holds one entry per scored key. A finding resolves when its
condition stops appearing: the tier settles, the candidate drops out of the candidate list or
gets a registry verdict, or a tracked row has evidence again.

## 2. Agentic pattern
Outer Human-in-the-loop (Generalissimo turns `rank:promote` findings into registry rows or
rejections), one scoring step inside a single invocation. No iteration across firings.

## 3. Type & data flow
Agent. `precheck.sh` runs `assemble.py`, which reads only local files: the registry, the newest
`state/runs/*-competitor-pulse-*/pulse.json` (news, Reddit, releases, traction, and when present
per-row `site` changes and top-level `candidates`), `reports/competitor-pulse/latest.json`
(engine `signal` per slug), `reports/gtm-research/latest.json` (`competitor_voices`
liked/disliked/evidence_links by registry name, the same join GC `_voice_by_name` uses) and
`reports/competitor-rank/latest.json` (last week's `rank`). It prints a MAGUYVA reference
section, a PRIOR RANK section, one compact section per competitor, a CANDIDATES part and a
`sources:` footer naming found and missing inputs, and writes the same data to
`$OUT_DIR/rank-input.json`. Text fields are tag-stripped and clipped. The stdout is never empty
while the registry reads, so the engine runs weekly. The engine scores the block.

## 4. Cadence
Weekly Monday 12:30 local, one hour after competitor-pulse (Mon 11:30, under 10 minutes), so the
newest pulse is fresh. Positioning moves slowly; a missed firing coalesces into one at wake.

## 5. Scope & exclusions
Registry rows with status `tracked` or `watch`, plus candidates listed in pulse.json. Rows with
status `rejected` and the registry `rejected:` list are excluded and printed only as a do-not-rank
list. No network, no registry edits, no new data collection.

## 6. Guardrails
Report/propose-only. The precheck block is the only ground truth; evidence urls must be copied
from it. The engine never edits the registry and never invents urls. A candidate's fate is
Generalissimo's verdict, never the loop's.

## 7. Permission axes + justification
report_only / none / none / none (the floor). The precheck reads local files and writes only into
its own run dir. No dangerous combination is approached.

## 8. Finding identity
`rank:tier-change:<slug>` (tier differs from PRIOR RANK; warn when entering core, info otherwise),
`rank:promote:<key>` (info; a candidate scored core or adjacent), `rank:thin-evidence:<slug>`
(info; a tracked row with no pulse or voice evidence this cycle). No tiers, scores, dates or
counts in ids.

## 9. Tier-1 semantics
ok: ranked. warn: at least one row entered core (a warn `rank:tier-change`). alert: never used.
An unreadable registry exits precheck 1 (precheck-failed).

## 10. Tier-2 metrics + panels
`metrics` is a JSON string `{"rank": {"<slug|cand:key>": {"features", "price", "positioning",
"sentiment", "tier", "why": {...}, "evidence": [...], "candidate"}}, "rubric_version": 1}`. The
harness flattens it to `rank.<key>.<component>`. The composite is not computed by the engine;
Growth Console weights the components. No `dashboard.json` panels (the raw fallback panel shows
the metrics); `/competitors` is the review surface.

## 11. Engine/model + budget
Codex default, timeout 900 seconds, one retry on transient failure. Zero network calls. Roughly
15-30 thousand tokens per firing (a 30-60 KB dossier in, about 25-30 scored entries out).
`precheck_cap_bytes=196608` leaves room for a growing registry and candidate list; each
competitor section is capped to a few short lines.

## 12. Page output
None. Growth Console `/competitors` is the review surface.
