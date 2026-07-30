# Report pages — a third output tier for loops

> **Status: APPROVED DESIGN, revised after council review (codex + grok, 1 pass, 2026-07-30).**
> The mechanical changes land as an explicit amendment to `docs/INTERFACES.md` (frozen contract —
> amend, never drift); this document is the design rationale, sibling to `docs/HARNESS_PLAN.md`
> and `docs/SKILL_IMPORT_AND_AGENT_SURFACE_PLAN.md`. Reviewer output is preserved in the session
> scratchpad; findings that mattered are folded in below. The build artifact
> `docs/REPORT_PAGES.md` (the authoring guide) is produced by the implementation, not this
> document.

## 0. What this is

Loops currently have two output tiers: the **row** (status light, headline, `dashboard.json`
panels, findings) and the **report** (markdown: `latest.md` + the dashboard drawer). Some loops
hold more detail than either can carry — a machine-exposure audit with 20 findings, each with
paths, explanations, and remediations, wants a designed page, not a markdown blob.

This design adds an optional third tier: the **report page** — a rich, self-contained HTML
detail page per run, linked from the loop's row and browsable from a new reports screen. The
approved benchmark for the quality bar is the Automic Vault exposure audit page
(`~/projects/av-audit/av-exposure-audit_llm_2026-07-30.html`, Generalissimo-approved; integration brief in
that directory's `LOOP_HANDOFF.md`).

**The /goal:** build the generic pathway first, then prove it by adding the av-audit skill as
the first page-enabled loop through the same documented process any future skill will use. Zero
special-casing in the harness = pathway proven.

## 1. Decisions settled (brainstorm + council; do not relitigate without new facts)

1. **Approach: contract + shared page kit** ("semi standard"). A hard page contract (§2) plus a
   SHOULD-level shared CSS/layout kit (§3) extracted from the benchmark page. Rejected: fully
   bespoke pages (no consistency); a declarative `page.json` + single shared renderer (big
   component-library build before anything ships; benchmark page would need rebuilding;
   expressiveness ceiling). If renderers converge, extracting a declarative form later is the
   same "de-agentification" move already recorded in the skill-import plan §7.
2. **Model design-time, deterministic run-time.** The model's design allowances are exercised
   ONCE, at authoring/import time, by the supervising agent: (a) the row — headline semantics +
   `dashboard.json` panels (existing rubric q10 machinery); (b) the page — a renderer built on
   the page kit. What runs every firing is deterministic precheck-class code. The engine never
   writes HTML at run time (house invariant; also the brace+quote shell denial makes
   engine-emitted markup a non-starter).
3. **Page-enabled by convention, not config:** an executable `loops.d/<name>/render.sh` — the
   `precheck.sh` pattern. No new `loop.conf` key. (Present-but-not-executable = validate FAIL;
   absent = not page-enabled, never an error.)
4. **Two page classes** (council round — both reviewers caught the contradiction between
   "suppression-respecting" and "render the full scan"):
   - A **domain-snapshot page** renders deterministically-captured world state in full (the
     av-audit case: the complete current scan). Dismissing a loop *finding* silences the nag
     channel; it does not redact the audit document. A snapshot page MAY annotate items whose
     corresponding loop finding is dismissed/snoozed (from `LATEST_JSON` + sqlite-derived
     info the runner provides — v1: optional), but MUST NOT hide them.
   - Any part of any page that presents **the loop's findings as findings** (open items, action
     sets, "what needs attention") MUST source them from the suppression-filtered `LATEST_JSON`,
     never raw `contract.json`. A dismissed finding must not resurface *as a finding*.
   - Every page declares its class in the envelope (`meta.page_class`:
     `"snapshot" | "findings"`); the authoring guide explains the choice.
5. **No sqlite schema change.** Pages are discovered by filesystem convention
   (`reports/<name>/latest.html` + dated siblings), exactly how `latest.md` links work today.
6. **Render failure is non-fatal but visible** — the dashboard-failure precedent (INTERFACES
   §4.1 step 7): never changes run status or exit code; surfaced via the capped render log and
   the reports screen (stale marker / "no page yet" entry, §5.2).
7. **Serving = the existing Caddy vhost, fixed** (worked fragment + acceptance checks:
   Appendix A). `loops.example.ts.net` currently rewrites every path to `loops.html`;
   nothing under `reports/` is reachable. The fix uses **path-scoped roots** — never
   `root * $LOOPS_ROOT` — so `state/` stays unreachable. Machine-local config change, per the
   dev-tailnet runbook (reload via `launchctl kickstart`, never admin :2019).
8. **Render runs inside the per-loop lock**, after contract promotion, with an **additive**
   budget (`min(timeout_s, 300)`, not deducted from the engine's budget); `duration_ms`
   includes render time. Consequence: a tight-interval loop with a slow renderer sees more
   `skipped-overlap` — acceptable, documented.

## 2. The page contract (tier-3, MUST rules)

A page-enabled loop's `render.sh` must produce a page satisfying:

1. **One self-contained HTML file.** Zero network *fetches* (no CDN scripts/styles, no fonts,
   no remote images, no iframes, no `@import`/`url(http…)`); renders identically from `file://`
   and the tailnet vhost. Inline everything; assets as `data:` URIs. Outbound `<a href>`
   navigation links are allowed. The promotion gate enforces this mechanically (§4.1) — a
   heuristic scan, documented as such, not a proof.
2. **Embedded data envelope.** Exactly one
   `<script type="application/json" id="report-data">` block:
   ```json
   {"meta": {"loop": "<name>", "run_id": "<run id>", "generated_at": "<ISO8601Z>",
             "title": "<page title>", "page_class": "snapshot",
             "totals": {"findings": 20, "sev_high": 18}},
    "data": { }}
   ```
   Required: `meta.loop`, `meta.run_id`, `meta.generated_at` (parseable ISO8601 `Z`),
   `meta.title`, `meta.page_class`. Optional: `meta.totals` — a FLAT object; values numbers or
   short strings (≤ 64 chars), rendered as chips on the reports screen. `data` is free-form
   loop-specific payload. JSON inside the block must escape the sequence `</` (emit `<\/` or `\u003c/`) so
   payload can never terminate the script element. Anything that ingests pages parses this
   block via `bin/page_envelope.py` (§4.3) — **never scrape display HTML** (the av-audit
   precedent).
3. **Deterministic.** Same inputs → same page (modulo `generated_at`). No model calls, no
   network, no randomness at render time. The renderer is precheck-class trusted code — the
   authoring guide treats its body as high-trust (a model-written renderer is reviewed before
   it is made executable; the import scaffolder emits it inert, §6).
4. **Declared inputs only.** The renderer reads: its run dir (`OUT_DIR` — precheck-captured
   artifacts such as `scan.json`), the promoted suppression-filtered `latest.json`
   (`LATEST_JSON`), its own loop dir, the page kit (`PAGEKIT`), and its loop-private state
   (`state/loop-data/<name>/`, read-only — writes go through the commit pattern, §6). It never
   reads raw `contract.json`. Findings-vs-snapshot semantics per §1.4.
5. **No secret values — mechanically enforced.** Pages are served on the tailnet.
   Paths/names of exposures are fine (the benchmark page lists file paths); secret VALUES are
   never embedded. Enforcement: the promotion gate runs `bin/redact.py` over the page in check
   mode — **if redaction would alter the page, promotion fails** (loud log, no publish). The
   pilot's test suite includes a fixture-secret scan that must be caught. Render logs are
   additionally redacted as a matter of course.

## 3. The page kit (SHOULD rules)

- `pagekit/kit.css` + `pagekit/README.md` in the loops repo: palette, header/hero, stat strip,
  grouped detail rows (`<details>` pattern), footer, tooltip — extracted from the benchmark
  page. Palette is the validated set (dataviz six-checks, surface `#0e0f12`): high `#d84f63`,
  medium `#b48c1a`, accent `#279a83`. Severity is never color-alone (markers + text labels).
  The README documents the envelope-escaping rule (§2.2) with a copy-paste snippet.
- Renderers SHOULD inline `kit.css` at render time (self-containment rule) and build their body
  from the kit's patterns. Deviation is allowed — the kit is the "semi" in semi-standard — but
  the envelope + contract rules are not negotiable.
- `pagekit/reference/` vendors the quality bar as a **sanitized regeneration** (council round —
  the real benchmark embeds this machine's actual exposure paths and must not enter the repo):
  a fixture `scan.json` with fake paths + the page rendered from it by the pilot renderer.
  Provenance header points at `~/projects/av-audit/`. The fixture doubles as the hermetic test
  input (§8).

## 4. Harness mechanics (the INTERFACES amendment, summarized)

### 4.1 Runner step (new, between promotion and finish-run/dashboard regen)

Order within a run: contract promotion → loop-data commit (§6) → render → finish-run →
dashboard regen. If `loops.d/<name>/render.sh` is executable AND the run promoted (completed +
validated):

- Run it with cwd `loops.d/<name>/`, in its own process group, timeout `min(timeout_s, 300)`
  (the precheck cap, additive per §1.8), env: `LOOP_NAME`, `RUN_ID`, `LOOPS_ROOT`, `OUT_DIR`,
  `LATEST_JSON` (absolute path to the promoted suppression-filtered copy), `LOOP_DATA_DIR`
  (absolute, read-only by convention), `PAGEKIT` (absolute path to `pagekit/`), `PAGE_OUT`
  (absolute target: `state/runs/<id>/page.html`).
- stdout+stderr → `state/runs/<id>/page-render.log`, redacted via `bin/redact.py`, capped at
  64 KiB (the precheck cap; truncate + marker).
- **Promotion gate (deterministic, runner-owned, all via `bin/page_envelope.py` §4.3):**
  `PAGE_OUT` exists, non-empty, valid UTF-8, ≤ 8 MiB; exactly one `#report-data` block that
  parses with all required `meta` fields, `meta.run_id == RUN_ID`, `meta.loop == LOOP_NAME`,
  parseable `generated_at`, valid `page_class`, flat well-typed `totals`; the
  no-external-fetch heuristic scan passes (§2.1); the redaction-clean check passes (§2.5).
  Pass → promote by write-tmp-then-rename in the target dir: dated
  `reports/<name>/<YYYY-MM-DD-HHMM>.html` first, then `latest.html` (crash between the two
  renames leaves dated history without a fresh latest — the stale marker catches it; order is
  therefore dated-first). Fail (bad exit, timeout, any gate check) → **no promotion**, previous
  `latest.html` untouched, run status and exit code unchanged, reason in `page-render.log`.
- Runs that don't promote a contract (failed/timeout/skipped, watchdog silent-green) never
  render — the stale-green guarantee extends to pages. `--dry-run` never renders.

### 4.2 Retention + validate + loopctl

- Dated pages prune with the existing `retention_days` sweep. The runner's keep-list is
  currently the literal names `latest.md`, `latest.json` (council round — grok verified the
  live code; a `latest.*` glob is NOT what ships): the amendment **adds `latest.html` to
  `keep_names` explicitly**.
- `loopctl validate`: `render.sh` present but not executable = FAIL. (Static checks only; the
  supervised run is where render quality shows up — "validate is necessary, never sufficient".)
- `loopctl run` prints the promoted page path when one was produced.

### 4.3 `bin/page_envelope.py` — the single envelope implementation

One stdlib-only helper used by BOTH the runner gate and `dashboard/generate.py` (council round —
without it the two diverge): extract the `#report-data` block (exactly-one enforced), parse,
validate required fields/types, run the no-external-fetch scan. CLI:
`page_envelope.py check --file F [--expect-run-id ID --expect-loop L]` → exit 0/1 with reasons
to stderr; `page_envelope.py meta --file F` → meta JSON to stdout. Importable as a module.

## 5. Surfaces

### 5.1 Dashboard row
The Report cell links `../reports/<name>/latest.html` when it exists, with the markdown link
retained secondary (`report · md`). If the page's `meta.run_id` doesn't match the loop's latest
promoted run (§5.2 rule), the link gains a visible `stale` badge — the page stays primary
(visible-not-hidden), the badge says it lags. No other row changes — the row-side design
allowance is the existing headline + `dashboard.json` machinery.

### 5.2 Reports screen
`dashboard/reports.html`, generated by `generate.py` in the same invocation as `loops.html`
(each file written tmp+rename; per-file atomic, the pair is not — documented), cross-linked both
ways, styled with the page kit, fully self-contained (kit CSS inlined, not linked).

- **Listing rule:** one entry per loop that is page-enabled (executable `render.sh`) OR has any
  pages on disk. Page-enabled with no `latest.html` yet → entry with a "no page yet — last
  render failed or hasn't run" marker (council round — first-render-failure must be visible).
  Pages on disk but `render.sh` removed → entry marked historical, no stale badge.
- **Per entry:** title, `generated_at` (relative + absolute), totals chips, `page_class`, link
  to `latest.html`, reverse-chron dated history **listed from filenames only** (no dated-page
  parsing — council round, regen cost), capped at 30 entries with a count of the rest.
- **Stale rule (precise):** the loop's **latest promoted run** = newest runs row with
  `runner_status = completed` and a non-null `contract_path`. If that run's `run_id` ≠
  `latest.html`'s `meta.run_id`, the entry (and the row link, §5.1) is marked stale.
- Envelope parse failure on `latest.html` → "no meta" marker + bare link; the generator never
  crashes on page content. Only `latest.html` is ever parsed (≤ 8 MiB by gate guarantee).
- `generate.py` grows a `--reports-out` flag (default `dashboard/reports.html`); `--out` keeps
  its current meaning. INTERFACES §10's source rule extends to: reads sqlite,
  `reports/*/latest.*` **including the `latest.html` envelope via `page_envelope.py`**, and
  `loops.d/*/{loop.conf,dashboard.json}` — display HTML is never scraped.

### 5.3 Serving
Caddy vhost change per Appendix A. Relative links (`../reports/<name>/latest.html` from
`dashboard/loops.html`) resolve correctly under both `file://` and the vhost mapping. The
`reports/` tree is `0700`/`0600` (INTERFACES §0), so the vhost only works because Caddy runs as
the same user — stated, verified in acceptance. Serving exposes `latest.md`/`latest.json`/dated
files under `/reports/*` too: intentional (same tailnet trust domain as the page; the row's md
link depends on it).

## 6. Loop-private durable state + authoring/import integration

- **`state/loop-data/<name>/`** joins the INTERFACES §1 layout: `0700` dirs, `0600` files,
  trusted-code-only **writes via the commit pattern**: precheck writes candidate files to
  `$OUT_DIR/loop-data.commit/`; the runner moves them (per-file rename) into
  `state/loop-data/<name>/` **only after successful contract promotion** (council round — both
  reviewers: a precheck that updates its own baseline can consume a NEW finding that a failed
  run never promoted; at-least-once semantics require commit-on-promotion). Renderers read it
  via `LOOP_DATA_DIR`, never write it. The engine has no write path to it at any axis; under
  codex's read-only sandbox it can technically *read* it — documented honestly, not prevented.
  Size is loop-owned (the authoring guide requires a bound — e.g. kagi-ban keeps exactly one
  previous scan); never auto-pruned.
- **Rubric q12 — page output** (added to `docs/LOOP_AUTHORING.md`'s intake, stable id
  `q12_page`): does this loop need a full page? If yes: `page_class` (§1.4), which data lands
  on it, and the page's groups/stats. Answered by the supervising agent + user at authoring
  time, like q10 panels. Touch-count is real (council round): the eleven-question count is
  hard-wired in LOOP_AUTHORING prose, the SPEC template, and `loopctl new` scaffolding — all
  updated, not just the doc.
- **`docs/REPORT_PAGES.md`** (build artifact): the contract, envelope shape + escaping snippet,
  kit usage, page-class guidance, worked example, error semantics, secrets rule, and the
  benchmark pointer — what the import-time agent designs from.
- **Skill-import plan delta** (recorded in `SKILL_IMPORT_AND_AGENT_SURFACE_PLAN.md` as a touch
  target so the import implementer can't miss it): q12 rides the existing `answers_needed` flow
  (analyzer heuristic: skill produces a rich artifact/HTML → suggest a page,
  `suggested_answerer:"user"`); `--apply` scaffolds the `render.sh` stub **inert** —
  non-executable, body commented — same trust rule as extracted precheck lines ("import never
  writes executable extracted commands"). Making it executable is the deliberate act.
- This design does not block on, and is not blocked by, the import build phases.

## 7. The proof: av-audit as the first page-enabled loop

Authored via `loopctl new` + the extended rubric — the manual path the importer will later
mechanize. Per `~/projects/av-audit/LOOP_HANDOFF.md` (hard constraints honored: report-only,
never `av harden/save/inject/open`, alert on data not exit code, no browser auto-open):

- **Name:** `kagi-ban` (鍵番, "key guard") — roops naming theme.
- **precheck.sh** (trusted): `av --version`; `av scan --json > $OUT_DIR/scan.json`; diff
  against `state/loop-data/kagi-ban/scan-prev.json` (READ only — the updated baseline goes to
  `$OUT_DIR/loop-data.commit/scan-prev.json`, committed by the runner on promotion, §6).
  **Finding key: `(source, sorted affected paths)` — no line numbers** (council round — line
  numbers are exactly the volatile identity LOOP_AUTHORING forbids; a token moving within a
  file is the same exposure; lines stay in `detail`). Digest to stdout: full current exposure
  list with stable keys, each labeled NEW / ONGOING / RESOLVED-since-last-run, plus
  metrics-grade counts.
- **Engine (codex, floor permissions):** interprets the digest only — **re-emits ALL currently
  true exposures as findings with their stable ids** (council round — codex: emitting only NEW
  keys violates prompt-contract rule 1 and makes `upsert-findings` auto-resolve still-true
  findings; recurrence/suppression is the runner's job). NEW/RESOLVED are digest labels the
  engine reflects in prose + `status_reason`, never recomputes. All counts come from the
  precheck digest verbatim (the "model-emitted metrics get believed" gotcha). This deviates
  from the handoff's NEW-only signal design deliberately; dismissals of known-accepted
  exposures (e.g. homebrew PATH) then work exactly like every other loop.
- **render.sh:** runs a **copy-with-provenance** of `render_report.py` (handoff allows copy;
  documented deltas: `--loop`/`--run-id` args; envelope id `report-data`; meta gains `loop`,
  `run_id`, `page_class:"snapshot"`; scan payload nests under `data`; `</`-escaping per §2.2)
  on `$OUT_DIR/scan.json` → `PAGE_OUT`. The delta list is noted in av-audit's README per the
  "don't fork silently" rule. Page class: **snapshot** — the full current scan renders
  regardless of dispositions (§1.4).
- **Schedule** daily (exact time chosen at authoring); dismissal of known-accepted findings via
  the normal `loopctl dismiss` flow, keyed on the stable id.
- **Acceptance:** the handoff's checks (headless no-tty run, ≈20-finding baseline reproduced or
  diff explained, no lingering `av` process, no GUI launch) + the gauntlet (validate →
  supervised run read against ground truth → install) + the redaction-clean gate passing on the
  real page + the page reachable from the row link and the reports screen **over the tailnet
  vhost** (Appendix A checks — the Caddy fix is on the pilot's critical path).

## 8. Error handling & testing (hermetic, house conventions)

Fake-renderer fixtures throughout (no real `av`, no network; the sanitized `pagekit/reference/`
fixture is the realistic input):

- Successful render promotes dated + latest atomically; both byte-identical; dated-first order.
- Failing / hanging (`SLEEP_S`) / bad-exit render: previous `latest.html` untouched, run row
  unchanged (`runner_status`, `exit_code`), capped `page-render.log` written.
- Promotion gate matrix: missing/duplicate envelope, missing required meta fields, wrong
  `run_id`/`loop`, unparseable `generated_at`, nested `totals`, oversize page, non-UTF8,
  external-fetch markup, fixture secret (redaction-clean trip) → no promotion, each with a
  distinct logged reason.
- First run ever (no previous `latest.html`) + render failure → "no page yet" entry, row has no
  html link, nothing crashes.
- Non-promoted run (contract-violation, engine-timeout), watchdog silent-green, `--dry-run` →
  renderer never invoked.
- `render.sh` absent → not page-enabled, no entry (unless pages exist on disk → historical).
- Loop-data commit: candidate committed only on promotion; failed run leaves baseline
  untouched; per-file rename.
- Retention prunes dated pages, never `latest.html` (keep-list assertion).
- `generate.py`: html link preferred over md; stale badge on `meta.run_id` mismatch; "no meta"
  fallback; history from filenames only, capped; both outputs written per-file atomically in
  one invocation; concurrent regen under `_dashboard.lock` unchanged.
- `validate`: non-executable `render.sh` fails.
- Caddy checks are live-only (Appendix A) — the hermetic suite never touches serving.
- Pilot live acceptance per §7 (supervised, not hermetic — the same split the import plan uses).

## 9. Build order

1. **Amendment:** INTERFACES numbered deltas — §1 layout (`pagekit/`, `reports/**/*.html`,
   `state/loop-data/`, `dashboard/reports.html`, run-dir `page.html` + `page-render.log`),
   §4.1 render step + gate + loop-data commit, retention keep-list, §8 validate/run additions,
   §10 surfaces + source rule. Tier-1 contract unchanged.
2. **Envelope helper first** (council round): `bin/page_envelope.py` + its unit tests — the
   runner and the generator must share one parser before either exists.
3. **Page kit:** extract `pagekit/` from the benchmark; build the sanitized reference fixture.
4. **Runner:** render step, promotion gate, loop-data commit, retention, fake-renderer suite.
5. **Surfaces:** `generate.py` row link + `reports.html` + stale/no-page markers; Caddy vhost
   fix + Appendix A acceptance (on the pilot's critical path).
6. **Docs:** `docs/REPORT_PAGES.md` authoring guide; LOOP_AUTHORING q12 (doc + template +
   scaffold count updates); skill-import plan delta note.
7. **Pilot:** author `kagi-ban` through the documented process; gauntlet; live acceptance.

Each step lands with its tests; the amendment is written as numbered section deltas (the
Amendment-1 pattern), not design prose.

## Appendix A — Caddy vhost fragment + acceptance (machine-local, dev-tailnet runbook applies)

Replace the current rewrite-all block with **path-scoped roots** (never `root * $LOOPS_ROOT`):

```caddyfile
@loops host loops.example.ts.net
handle @loops {
	handle_path /reports/* {
		root * /Users/llm/projects/loops/reports
		file_server
	}
	handle {
		root * /Users/llm/projects/loops/dashboard
		try_files {path} /loops.html
		file_server
	}
}
```

- `/` and unknown paths → `dashboard/loops.html` (current behavior preserved);
  `/reports.html` → the reports screen; `/reports/<name>/...` → the reports tree. `state/`,
  `loops.d/`, `bin/` are outside both roots — unreachable by construction.
- Caddy runs as user `llm` (required: `reports/` is `0700`/`0600`).
- Reload per the dev-tailnet runbook: `launchctl kickstart` — never the admin `:2019` API.
- **Acceptance (live, after step 5):**
  `curl -sk https://loops.example.ts.net/` → 200 dashboard;
  `…/reports.html` → 200 reports screen;
  `…/reports/<name>/latest.html` → 200 page;
  `…/state/loops.sqlite` and `…/reports/../state/loops.sqlite` → 404/400 (traversal);
  all over `https://` (ts.net is HSTS-preloaded — an `http://` 200 from curl proves nothing).
