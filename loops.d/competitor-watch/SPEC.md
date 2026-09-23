# competitor-watch — intake spec

Ticket B-27 (`maguyva-marketing/gc-actions/specs/B-27.md`) [T-31 | ALL-14: COMP-11..14, SEO-25].
Intake originally written 2026-08-27 in `gc-actions/deliverables/B-27/loop-design.md`; transcribed
here per that doc's own note that "the SPEC.md is a fill-in rather than a rewrite." Two deviations
from the literal design doc, both required by this harness's real capabilities (documented in full
in `precheck.sh`'s header and this SPEC's §3/§7): (a) watch 4 uses a sitemap-proxy method instead
of the tavily API, since `credential_env` is hard-disabled in this harness's v1; (b) cross-run
diffing uses a self-contained `cache/` directory inside this loop's own folder, since there is no
documented mechanism for a precheck script to locate a prior run's `OUT_DIR`.

**2026-09-18 addendum:** a fifth named competitive threat, "Ref Plans depth creep" (watch 5),
was added out-of-band from B-27's original design (ref.tools' "Ref Plans" product was not part of
that original teardown). It follows the exact same twelve-heading contract and the exact same
precheck fetch-and-diff/cache pattern as the original four threats. The backlink-gap watch shifted
from watch 5 to watch 6 to make room for it.

## 1. Purpose & stop condition
Five named competitive threats (Sourcegraph/Amp self-serve re-entry, editor/CLI-native context
becoming good-enough, OSS local-indexer commoditization, Augment SEO encroachment, Ref Plans depth
creep) plus a backlink-gap watch need a RECURRING read, not the one-off teardown done in the
2026-07-21 competitor-analysis. Watch 5 (Ref Plans depth creep) was added 2026-09-18, after this
loop's original B-27 build, following the same twelve-heading contract this SPEC already used.
The loop reports; it never acts. **Per-firing done:** a fresh set of findings
written for this run, each watch evaluated or explicitly marked source-unavailable. **Cross-run
done:** a finding gets `resolved_at` when a later run observes its condition gone.

## 2. Agentic pattern
Human-in-the-loop (the fleet's universal v1 outer shape): the loop surfaces a signal, Generalissimo
decides whether it changes anything via `loopctl ack/dismiss/snooze`. Inside one engine invocation
it is Plan-then-Execute: read the precheck digest → judge which diffs cross a wake-threshold →
emit findings. No cross-invocation retry machinery, no resumed session.

## 3. Type & data flow (precheck gathers vs engine interprets)
`type=agent`. `precheck.sh` does ALL fetching (unsandboxed, house pattern): sourcegraph.com/pricing
snapshot diffed vs a self-contained `cache/` copy; Claude Code changelog + Cursor blog heads,
diffed the same way; GitHub stars/last-pushed for the four named OSS indexer repos; sitemap checks
on augmentcode.com and maguyva.ai for the three contestable query slugs (**deviation**: the design
doc specified live SERP checks via the tavily API — not buildable under this harness, since
`credential_env` is reserved/hard-disabled in v1 per `docs/INTERFACES.md` §5 rule 8, and a
launchd-run precheck.sh has no other way to receive an API key; the sitemap-URL-match proxy needs
no credential and is genuinely buildable, at the cost of testing "was a page published targeting
this phrase" rather than "does it rank"); `docs.ref.tools/plans/getting-started/your-first-plan`
and `docs.ref.tools/plans/getting-started/intro` (the latter found via
`docs.ref.tools/sitemap.xml`, easily discoverable — no other Ref Plans docs page describes its
indexing/analysis capability more directly than these two), diffed the same way, watching for Ref
Plans' own docs to start claiming dependency-graph/call-graph/blast-radius/structural analysis
rather than its current documented shallow codebase-reading behavior; and B-23's status line from
`gc-actions/PRODUCT_BACKLOG.md` for the backlink-gap dependency check. The engine only interprets
the resulting digest — it never fetches, and it never decides something is "probably fine" without
saying so.

## 4. Cadence
`monthly:01:20:30` — the 1st of the month, 20:30 Manila, after the daily ads-loop evening stagger
has cleared. Monthly because these threats move on a quarter-scale; a weekly version would report
noise as movement. Staleness expectation: 30 days: a missed firing (e.g. the Mac asleep) coalesces
into a single catch-up firing at wake, per launchd calendar-schedule semantics — this loop's cadence
is tolerant of that (a threat that actually re-enters self-serve doesn't need same-day detection).

## 5. Scope & exclusions
IN: the five threats and the backlink watch above (four from B-27's original design table, plus
the 2026-09-18 Ref Plans depth-creep addition, same table conventions). OUT:
anything requiring a paid SEO API (Ahrefs/Semrush gate theirs at $449-549/mo — not spent, per
[[reference_seo_aeo_tooling_economics]]), anything requiring a logged-in session, any browser
automation or CDP (never touches the OpenTwins Chrome or its lease), and any form of writing to
Maguyva's own ad or site systems. This loop has no write path at all beyond its own report and its
own `cache/` diffing directory.

## 6. Guardrails verbatim
- Read-only, no browser automation, no CDP, never touches the OpenTwins Chrome or its lease.
- No paid API — confirmed buildable within this constraint after the watch-4 deviation above.
- Never opens a PR, files a ticket, or edits a runbook — findings only.
- If a source is unreachable, that is a finding with a `source-unavailable` condition, never a
  silent skip — generalized in `prompt.md` to all six watches, not just the backlink one the
  design doc named explicitly, since the same principle obviously applies to any of them.

## 7. Permission axes + justification
`perm_fs_write=report_only` — the engine never writes anywhere except its own run/report
artifacts; all real writing (cache diffing, precheck's raw snapshots) happens in the unsandboxed
`precheck.sh`, which is never governed by these axes at all (`docs/LOOP_AUTHORING.md` §4). This is
a deliberate correction of the design doc's literal `perm_fs_write=workdir` — that value exists for
loops whose *engine* needs to write files itself (e.g. `ads-x`'s emit-script pattern); this loop's
engine only ever needs to emit the schema-enforced JSON contract, which `report_only` already
covers. `perm_network=none` — the engine gets no network; precheck already fetched everything it
needs. `perm_local_exec=none` — the engine only interprets already-gathered text. `perm_remote_mutation=none`
— nothing remote is ever touched. All four sit at the fleet's report-only floor; no dangerous-combo
justification needed since no axis is raised above its default.

## 8. Finding identity (what a finding IS + finding_id derivation rule)
A finding is **one watch observing one condition**. `finding_id` = `<threat-slug>:<condition>` —
`sourcegraph:selfserve-signal`, `editor-native:persistent-repo-index`, `oss-indexers:traction-jump`,
`augment-serp:codebase-context-mcp`, `augment-serp:give-claude-code-repo-context`,
`augment-serp:mcp-server-codebase-map`, `ref-plans:graph-analysis-signal` (fires when Ref Plans'
own docs start claiming dependency-graph/call-graph/blast-radius/structural analysis — not for
breadth claims like more file types or languages), `backlink-gap:new-referring-domain` (reserved,
cannot fire until B-23 lands), `backlink-gap:source-unavailable`,
`backlink-gap:dependency-may-be-satisfiable`, and generalized `<threat-slug>:source-unavailable`
for any watch whose fetch fails outright. See `prompt.md`'s `## Finding identity` section
(verbatim source of truth) for the full derivation rule and watch-6's once-only emission exception.

## 9. Tier-1 semantics (ok/warn/alert meaning)
`alert` — at least one watch crossed its wake-threshold this run (a real competitive signal).
`warn` — no watch crossed a threshold, but at least one source was unreachable this run (excluding
watch 6's steady-state "B-23 still not done," which is expected and does not warrant `warn` on its
own once already reported once). `ok` — all reachable sources checked clean, nothing crossed a
threshold.

## 10. Tier-2 metrics + panels
Metrics (inside the `metrics` JSON string): `sources.reached` (0-6, numeric), `sources.unreachable`
(numeric), `findings.raised` (numeric), `findings.resolved` (numeric). `dashboard.json` declares: a
`number` panel on `findings.raised` (`higher_is_worse`, thresholds warn 1 / alert 3, gap on
missing), a `number` panel on `sources.unreachable` (`higher_is_worse`, thresholds warn 1 / alert 3,
gap on missing), and a `trend` panel on `sources.reached` (30-day... really month-over-month given
this loop's cadence — window_days:180 to show ~6 firings) so an unreachable source is visible as a
dip rather than looking like "no news."

## 11. Engine/model + budget
`engine=codex` — the standing preference for scheduled loops (claude tokens reserved for
development, per Generalissimo's 2026-08-05 call, [[feedback_codex_preferred_loop_engine]]).
`model=` blank (engine default). Expected tokens/run: low-to-moderate — precheck's digest is
capped (sitemaps grepped down to matching lines, GitHub API responses reduced to two fields, diffs
capped at 60 lines) and the engine's job is judgment on a handful of diffs, not open-ended research
(a few thousand input tokens, a few hundred output tokens). `retry_transient=1` (default).
`timeout_s=900` (default) — generous relative to the trivial interpretation task; the real time
cost is precheck's six watches' worth of sequential HTTP fetches (each capped at 20s via
`curl -m 20`), not engine reasoning.

## 12. Page output
None — this loop's findings surface through the standard dashboard tier-1/tier-2 rendering; no
dedicated report page (`docs/REPORT_PAGES.md`) is needed for a monthly threat-watch of this size.
