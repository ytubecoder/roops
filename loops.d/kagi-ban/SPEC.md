# kagi-ban — intake spec

1. Purpose & stop condition
Recurring machine-exposure audit per `~/projects/av-audit/LOOP_HANDOFF.md`: run
Automic Vault's read-only scanner, diff against yesterday's committed baseline,
and surface every open exposure as a durable finding while rendering the full
inventory as a snapshot report page. Per-firing stop condition: the engine has
re-emitted every CURRENT EXPOSURES line as a finding with the precheck's
finding_id, copied the precheck counts into metrics, and the render step has
produced a gated snapshot page of the full scan. Cross-run stop condition: a
finding resolves when that exposure no longer appears in the current scan
(runner sets `resolved_at` when the id is absent); kagi-ban never hardens,
saves, or remediates — it only reports.

2. Agentic pattern
Outer shape Human-in-the-loop (mandatory v1): kagi-ban proposes exposures as
findings; generalissimo dispositions them (ack/dismiss/snooze/reopen). It never
runs `av harden/save/inject/open`, never mutates the machine, and never iterates
across firings. Inside the single invocation: plain interpretation — precheck
gathered the scan + diff digest with all counts and finding_ids; the engine
classifies severity mapping and writes the tier-1 narrative. V2 aspiration,
recorded honestly: optional bridges from APPROVED findings to a human-driven
remediation checklist outside the harness (native headless plan) — explicitly
not built here.

3. Type & data flow (precheck gathers vs engine interprets)
type=agent. precheck.sh deterministically (trusted, unsandboxed): locates the
`av` binary, runs `av scan --json` into `$OUT_DIR/scan.json`, diffs finding
keys against `$LOOPS_ROOT/state/loop-data/kagi-ban/scan-prev.json`, computes
all counts (total/high/medium/new/resolved/ongoing/first_run), labels each
current exposure NEW or ONGOING with a stable `av:<source>:<sha8>` id, lists
RESOLVED ids for prose, and stages the new baseline under
`$OUT_DIR/loop-data.commit/scan-prev.json` for the runner's post-promotion
commit. The engine interprets only: maps severities, copies metrics and
finding_ids verbatim, writes headline + report_markdown. It never re-runs av,
never recomputes counts, never invents ids (model-emitted metrics get believed
— house gotcha; precheck owns the numbers).

4. Cadence
daily:07:40 local — morning exposure snapshot before the workday; staleness
expectation 24h. launchd coalescing of a missed calendar firing to one-at-wake
is acceptable. Not installed yet by this task (supervised runs only until the
fleet install step).

5. Scope & exclusions
In scope: one machine's Automic Vault scan output (findings with source,
severity, affected paths); the previous committed scan for diff; the full
snapshot page inventory. Excluded: any write path on the machine via av
(`harden`, `save`, `inject`, `open` — LOOP_HANDOFF hard constraint); remote
mutation; scanning other hosts; re-deriving findings from live files outside
the scan JSON; maguyva or other external tools in the engine. The probe is
local `av` only, and only `scan` / `--version`. The subject of the report is
the probe host (the machine where `av` actually runs), not the runner host;
the page names that host (`subject: …`) from `probe_host` on the scan
document.

6. Guardrails
- Report/propose-only: never `av harden`, `av save`, `av inject`, or `av open`
  (LOOP_HANDOFF hard constraint). Alert on finding counts and data, not on
  `av scan` exit code (scan exits 0 even with findings).
- "Everything is report/propose-only. No component ever commits, pushes, or
  mutates a project outside `$LOOPS_ROOT`" (docs/INTERFACES.md §0) for harness
  side-effects; machine remediation is out of band.
- All counts and finding_ids are computed in precheck and copied by the engine
  — never recomputed by the model.
- Guardrails live in prompt.md AND in the floor permission axes — never by
  prompt text alone.

7. Permission axes + justification
The full report-only floor: perm_fs_write=report_only, perm_network=none,
perm_local_exec=none, perm_remote_mutation=none. The engine needs nothing —
it interprets an injected digest. The `av scan` runs in trusted precheck.sh
(unsandboxed bash), which is not governed by these axes. No axis raised; no
dangerous combo approached. Working-directory write sandbox + `--tools` +
`perm_network=none` contain the model; the allowlist is intent only.

8. Finding identity (what a finding IS + finding_id derivation rule)
A finding is "this machine currently has exposure X from detector source S
affecting this sorted set of paths." finding_id =
`av:<source>:<sha8>` where sha8 is the first 8 hex chars of sha256 over the
sorted affected path list joined by `|` (NO line numbers — they shift;
volatile identity is forbidden). Precheck computes every id; the engine copies
them verbatim. Same source + same paths tomorrow = same finding
(times_seen increments); a different path set is a different finding. Source
of truth: prompt.md `## Finding identity`.

9. Tier-1 semantics (ok/warn/alert meaning)
`ok` — current-exposure list is empty (clean scan). When findings are present,
effective_status is driven by max unsuppressed finding severity (INTERFACES
§4.5) and the declared status is secondary. Severity mapping: high/critical →
`alert`, medium → `warn`, anything else → `info`. `status_reason` examples:
`clean`, `exposures_present`, `new_exposures`. RESOLVED items are narrative
only — not findings.

10. Tier-2 metrics + panels
Metrics (precomputed by precheck, copied as the metrics JSON string under
`av.*`): `av.total`, `av.high`, `av.medium`, `av.new`, `av.resolved` (and
ongoing/first_run live in the digest for the headline, not required as panel
keys). dashboard.json: number on av.total (higher_is_worse, warn 1 / alert 19,
missing gap); number on av.high (higher_is_worse, warn 1 / alert 18, missing
gap); number on av.new (higher_is_worse, warn 1 / alert 3, missing gap);
trend on av.total (30d, hold). Panel colour is cosmetic; status comes from
findings.

11. Engine/model + budget
engine=codex (default; pure interpretation at the floor). model= (engine
default). Expected tokens/run: low thousands input (digest only — engine never
sees the full HTML page payload), high hundreds output for findings +
narrative. retry_transient=1 (default). timeout_s=300 — interpretation only;
the 900s default would pad a hang. Budget rule: engine sees only the precheck
digest, not the raw multi-KB scan inventory (that lives on the page).

## 12. Page output (q12)
Yes — page class `snapshot`. The page is the full scan inventory (not just the
diff): every finding from this run's `scan.json`, grouped by detector category,
with a stat strip of totals (high severity count, medium count, tools affected,
exposed paths). render.sh invokes render_page.py (copy-with-provenance of
av-audit render_report.py) with SCAN_JSON + --loop + --run-id → $PAGE_OUT.
Envelope id `report-data`; meta carries loop/run_id/generated_at/title/
page_class plus host/av_version/scanned_at/totals. Dismissal silences the
finding channel, not the audit document on the page.
