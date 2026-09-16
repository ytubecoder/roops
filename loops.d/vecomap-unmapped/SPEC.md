# vecomap-unmapped — intake spec

1. Purpose & stop condition
Trace VECO transformer-area map images for keys the outage map cannot draw,
commit high/medium-confidence polygons under `data/candidates/` on a
`tracer/<YYYYMMDD-HHMM>` branch, and push that branch so vecomap's
`tracer-pr` workflow can regenerate the layer and open the PR. Per-firing
"done": the unmapped list was fetched, eligible keys were traced up to the
cap, a candidate branch was pushed when any key reached high or medium
confidence, superseded candidates and aging provisionals were listed, and
`summary.json` was written. Silent-green (no engine) when nothing was
traced and nothing is superseded or aging. Cross-run "done": an
`unmapped:<key>` finding resolves when that key is no longer emitted
(candidate already on main, or the next firing has nothing new for it);
`tracer-failed:<key>` resolves when a later firing traces it or the key
leaves the unmapped list; `candidate-superseded:<key>` resolves when the
owner deletes `data/candidates/<key>.*` after the KML gained the
placemark; `provisional-aging:<key>` resolves when the area is no longer
provisional (hand-drawn polygon won). Merge of the tracer PR is the human
approval gate — this loop never merges.

2. Agentic pattern
Outer shape Human-in-the-loop, per the honest v1 mapping — the approval
arrow is the PR MERGE on ytubecoder/vecomap, not a disposition verb.
Inside a firing there is no agentic pattern at all: tracing, git, and the
push are deterministic precheck work (kagami precedent). The engine is a
single-shot interpreter of `summary.json`. No iterate-until-success
anywhere; a later firing retraces a key only when the attempt is older
than 30 days or the tracer version changed. V2 aspiration, recorded
honestly and not built: auto-merge high-confidence PRs.

3. Type & data flow (precheck gathers vs engine interprets)
`type=watchdog`: `precheck.sh` **is** the job (docs/INTERFACES.md §4.1).
Exit 0 → silent-green, heartbeat ok=1, no engine. Non-zero → escalate,
heartbeat ok=0, engine interprets already-gathered text. Precheck
(deterministic, unsandboxed):
- refresh `~/projects/vecomap` with `git fetch -q origin && git checkout
  -q -B main origin/main` (never change remotes; deploy-key alias
  `github-vecomap` is already on that checkout);
- `GET $VECOMAP_API_BASE/api/admin/unmapped?include=provisional` with
  `X-Admin-Secret: $VECOMAP_ADMIN_SECRET` (both from `$LOOPS_ROOT/.env`,
  exported to the precheck by the runner);
- choose up to `VECOMAP_MAX_KEYS` (default 10, 200 s wall-clock budget under the 300 s precheck cap) keys with
  `provisional=false`, no `data/candidates/<key>.geojson` on main, and no
  fresh matching attempt in `$LOOPS_ROOT/state/vecomap-unmapped/attempted.json`;
- refresh OSM caches when `cebu_named_ways.json` is older than 60 days
  (best effort);
- `python -m area_tracer resolve` then `trace` via the tracer venv;
- when any key is high/medium, branch `tracer/<YYYYMMDD-HHMM>`, copy
  geojson + overlay into `data/candidates/`, commit as vecomap-tracer,
  `git push origin <branch>` unless `VECOMAP_DRY_RUN=1`;
- detect superseded candidates (geojson on disk AND a KML placemark of
  the same `<name>`) without Node; do not delete;
- age provisionals first seen by this loop more than 30 days ago;
- write `$OUT_DIR/summary.json` and a short markdown for the engine.
Engine: copy precheck's metrics, emit the contract findings below, write
a 5-line `report_markdown`. It recomputes nothing, has no tools, no
network, no credentials.

4. Cadence
`times:07:40,19:40` local — twice a day, after VECO's morning and evening
advisory windows, so a new unmapped key is a same-day estimate rather
than waiting overnight. Staleness expectation 12h (`86400 / 2`). Calendar
coalescing after sleep is fine: one catch-up firing traces the backlog
up to the 10-key cap (the rest wait for the next firing). New unmapped keys arrive a few times a year; most
firings are silent-green.

5. Scope & exclusions
In scope: unmapped (`provisional=false`) keys from the admin API; tracing
via `tools/area-tracer`; pushing `data/candidates/<key>.geojson` and
`<key>.overlay.png` on a `tracer/*` branch; reporting superseded
candidates and 30-day-old provisionals. Explicitly excluded: touching
`main`; opening or merging pull requests (vecomap's `tracer-pr` workflow
does that); committing low-confidence or failed keys; deleting
`data/candidates/<key>.*` (report only); changing git remotes; running
Node / `npm run build:areas` (no Node on the guest); any maguyva or
external analysis tool; any mutation other than the one branch push in
precheck.

6. Guardrails
Verbatim, from project docs:
- "deterministic code you wrote gets full power; the model gets a
  sandbox" (README, "What Can Actually Change Things") — the only remote
  mutation lives in precheck; the engine is at the floor on all axes.
- "Everything is report/propose-only. No component ever commits, pushes,
  or mutates a project outside `$LOOPS_ROOT`." (docs/INTERFACES.md §0) —
  binds the **engine**. Precheck is trusted unsandboxed code, same as
  kagami's PR push and every watchdog probe.
- "Fresh engine session per firing" (§0) — enforced at the adapter.
- Watchdog stickiness (docs/INTERFACES.md §4.3): "if the probe failed,
  the run's loop_status AND effective_status are alert regardless of what
  the diagnosis engine returns and regardless of suppression."
- This loop never merges, never pushes to `main`, never deletes
  candidates, and never commits a key whose confidence is not `high` or
  `medium` (vecomap SPEC R-676, R-677).

7. Permission axes + justification
All four axes at the report-only floor for the ENGINE
(`report_only/none/none/none` — nothing uncommented in loop.conf, same
as kagami). The engine gets no `credential_env`, no network, no exec: it
physically cannot hold the pen. The one remote mutation (`git push origin
tracer/<YYYYMMDD-HHMM>` on ytubecoder/vecomap) is performed by
precheck.sh — trusted deterministic code, the same trust rule
LOOP_AUTHORING §4 applies to watchdog probes ("a plain, unsandboxed
script … never governed by this axis at all"). Credential: the checkout's
existing deploy key (`~/.ssh/vecomap-deploy`, ssh Host `github-vecomap`);
the loop never changes remotes. `perm_remote_mutation` stays `none`
because that axis governs the engine, not precheck; the validator does
not require `remote_mutation_justification` at the floor (kagami
precedent). Dangerous combos: none tripped; the nearest (combo 2, raising
`perm_remote_mutation` without a justification, or combo 4, a mutating
`git push` on an engine allowlist) is avoided by keeping git out of the
engine's reach entirely.

8. Finding identity (what a finding IS + finding_id derivation rule)
A finding is one unmapped key's tracer outcome, or a candidate that the
hand-drawn KML has superseded, or a provisional estimate that has sat
for more than 30 days. `finding_id` is one of:
- `unmapped:<key>` — estimate pushed on `<branch>`, confidence X (info)
- `tracer-low:<key>` — traced, confidence low; owner should trace by
  hand or check the image (info)
- `tracer-failed:<key>` — resolve or trace failed; detail includes the
  report message, e.g. a tinyurl 404 (warn)
- `candidate-superseded:<key>` — present as `data/candidates/<key>.geojson`
  and as a `<name>` placemark in `data/raw/vecomap.kml`; delete those
  candidate files in vecomap (info)
- `provisional-aging:<key>` — still an estimate more than 30 days after
  this loop first saw it as provisional (warn)
`<key>` is the durable tinyurl code. No timestamps, run ids, counts, or
branch names in the id — those belong in `detail` / `status_reason`.

9. Tier-1 semantics (ok/warn/alert meaning)
`ok` — silent-green: nothing traced, nothing superseded, nothing aging;
precheck exit 0, heartbeat ok=1, no engine. `warn` — the engine's own
emission when it is invoked and the findings are only info/warn (failed
traces, aging provisionals); it is not what the dashboard stores.
`alert` — any escalation (precheck non-zero). Watchdog stickiness
(INTERFACES §4.3) forces stored `loop_status` and `effective_status` to
`alert` whenever the probe failed, regardless of the engine's declared
status or finding severities. This loop never emits finding severity
`alert`; warn is the ceiling on a finding. `status_reason` is the pushed
branch name when one exists, else `no_branch`.

10. Tier-2 metrics + panels
All computed in precheck and copied verbatim by the engine (`metrics:`
line): `traced` (keys resolve+trace ran this firing; number, neutral),
`confident` (high or medium this firing; number, neutral), `pushed`
(1 if a branch was pushed — 0 on dry-run or no confident keys; number,
neutral), `low` (low-confidence traces; number, higher_is_worse, warn at
1), `failed` (resolve/trace failures; number, higher_is_worse, warn at
1), `aging` (provisionals older than 30 days; number, higher_is_worse,
warn at 1). `superseded` is also in the metrics object (raw fallback).
`dashboard.json` declares the six named panels; undeclared keys still
surface in the raw fallback.

11. Engine/model + budget
`engine=codex`, default model. Engine only on escalation — expected a
few firings a month (new unmapped keys are rare; superseded/aging rarer).
Expected a few hundred tokens/run of interpretation plus the ~12.8k
codex system-prompt baseline; output is a short JSON contract.
`retry_transient` default 1. `timeout_s=2400` — the budget is for
precheck's tracing (up to 10 keys, OCR + resolve, 200 s budget) and a short engine
invocation, not for a long model session. Harness still caps precheck at
`min(timeout_s, 300)` = 300s (INTERFACES §4.1); a 25-key run may not
finish inside that cap and will pick up the rest next firing via
`attempted.json`. Do not raise engine axes to dodge the cap.

12. Page output
Yes — class `snapshot`. `render.sh` reads `$OUT_DIR/summary.json` and
`$OUT_DIR/trace/<key>.overlay.png`, inlines pagekit, and writes one
self-contained HTML page: a summary stat strip + table, then each traced
key with confidence, links to `https://tinyurl.com/<key>` and
`https://vecomap.pages.dev/#area=<key>`, and a downscaled overlay as a
`data:` JPEG (tracer venv python + cv2, longest side ≤ 600 px, quality
60). Images drop after 40 keys so the page stays under the 8 MiB
envelope. No network, no model, no randomness. Silent-green runs never
render (runner step 6.5). Stat strip: traced · confident · pushed ·
low · failed · aging.
