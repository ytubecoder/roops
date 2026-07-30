# Session Log

## 2026-07-30 — Report-pages follow-up wave: KV single-sourcing, kit.css becomes the real kit, self-containment by reference

### Summary
- **`docs/REPORT_PAGES_FOLLOWUP_WARMSTART.md` cleared end to end**: the three decisions parked for
  the owner, all eight backlog items, and both trailing leftovers. The doc now carries only live
  state, the "16 not 20" PATH explanation, and the settled/do-not-relitigate list.
- **A filed maintainability nit was a live bug.** The renderer's KV-neutralization regex used `\b`
  where `bin/redact.py`'s `_KV_RE` uses `(?<![A-Za-z0-9-])`. `_` is a word character but is not in
  `[A-Za-z0-9-]`, so `\b` missed `GITHUB_TOKEN=/path` — which redact.py *does* redact, meaning the
  §4.4 promotion gate would have rejected the page the first time `av` reported an underscore-style
  env var, visible only as a `stale` badge. The same `\b` over-matched `gh-cli-hosts-token:`,
  damaging av's own finding prose. redact.py now exports `KV_KEYWORDS`/`KV_KEY_PATTERN`/
  `KV_SEPARATOR`; the renderer composes from them and fails loudly if redact.py is unimportable.
- **Other fixes:** `generate.py` resolved every loop twice per firing (now once, byte-identical
  output); a fault in `_render_reports_page` skipped BOTH `_atomic_write` calls and took
  `loops.html` down with it (now isolated); `finish_render_log`'s `2>/dev/null || true` left the
  render log unredacted AND silent on failure; four test gaps; the `$LOOP_DATA_DIR` doc bug that
  would kill any precheck under `set -u`; q12 heading normalization; doc cosmetics. Corrupted
  finding row `av:gh-cli-hosts-token:«redacted:secret»` deleted from sqlite (backup taken first).

### Lessons Learned
- **Accepted: mutation-test every new guard before believing it.** Stripping `chmod 600` proved the
  new loop-data permission test fails (`expected [600] got [644]`). More valuably, injecting a
  webfont to prove the self-containment guard worked produced a GREEN suite — because the injection
  landed in `_reports_document` and only `loops.html` was ever asserted. `dashboard/reports.html`,
  a second served output with its own `<head>`, had no coverage at all. The mutation found a real
  gap that reading the code did not.
- **Accepted: assert the invariant, not a proxy for it.** "The page must not contain the substring
  `http://`" was wrong in both directions — too strict (real finding text carries URLs; SVG
  `xmlns` is an identifier, so the live dashboard already violated the stated rule while the
  hermetic test passed) and too loose (`src="//cdn/x.js"` fetches and contains no `http://`).
  Replaced by `tests/html_selfcontained.py`, which collects references the browser dereferences on
  load. The scanner has its own tests: one that always returned `[]` would pass everything vacuously.
- **Rejected: keeping kit.css as an inlined copy bound by a parity test.** Its stated justification
  was byte-determinism, which does not survive scrutiny — kit.css is *source*, exactly like
  `render_page.py`, not an input to a given scan. The copy-plus-tripwire left `$PAGEKIT` with no
  reader and guaranteed page loop #2 would make a third copy. Now read at render time; output
  byte-identical (39182 bytes) against the live page.
- **Gotcha: the global turn-end ruff hook runs a broad ruleset this repo does not conform to**
  (54 findings repo-wide, no config file anywhere). Editing any `.py` surfaces *pre-existing* debt
  and blocks the turn, looking like the edit caused it. Diff against `git show HEAD:<file>` before
  assuming an agent introduced it; fix only files edited this turn, never repo-wide.
- **Gotcha: several agents commit and push to `main` in this same checkout.** 20 commits landed
  mid-session, plus two owner README edits on GitHub. Rebase (never merge/force), and never trust a
  recorded test count — it moved 668 → 742 → 754 → 1000 in a day. A sudden jump is usually another
  agent's tests, not double-collection in your own imports.

### Decisions
- **kit.css missing = fail the render**, same call as the redact import: it is a committed file, so
  its absence is a broken checkout, and failing names the path in `page-render.log` rather than
  quietly promoting an unstyled page. Step 6.5 still cannot change `runner_status` or the exit code.
- **Navigation `<a href>` is explicitly excluded from the self-containment check** — following a
  link is user-initiated and fetches nothing on load. `data:` URIs are likewise fine. Without this,
  the kagi-ban page's 16 legitimate av docs links would be unfixable false positives.
- **`_KV_RE`'s lookbehind ratified by the owner** and recorded in-comment so it is not
  re-litigated; the corrupted finding row deleted rather than dismissed, since it is an artifact of
  a fixed bug and the real finding is tracked under its correct id.
- Renderer failure modes now follow one rule: **a missing required repo file fails loudly**
  (redact.py, kit.css), because a diagnosable line in `page-render.log` beats a page that looks
  merely ugly or, worse, ungated.

## 2026-07-30 — B-09: set-schedule regen guard + §10 paused-staleness close + OpenSpec routing rule

### Summary
- **False-400 fix shipped** (`5365039..a95688a`): `loopctl set-schedule`'s dashboard regen is
  best-effort (the `cmd_disposition` idiom), so the console no longer 400s a schedule change
  that took effect; INTERFACES §13/§8 amended in the same commit; §10's paused-staleness open
  question resolved (paused loops STAY stale-visible; `set-schedule manual` is the exempt
  off-path). 754 → 756 tests.
- **OpenSpec re-armed**: diagnosed as adopted-then-dormant (used only for B-01; B-04..B-08
  bypassed it) — root cause was no routing trigger in CLAUDE.md. Added "Change lifecycle
  routing": feature-scale → OpenSpec change named after the ticket; small fixes → superpowers
  plan + in-commit INTERFACES amendment; Ticket Takeaway ticket either way.
- **Execution model:** subagent-driven development, all-Sonnet implementers + per-task Sonnet
  review gates, Opus final whole-branch review, one fix wave + scoped re-review.

### Lessons Learned
- **Gotcha:** a best-effort guard must wrap the MODULE LOAD, not just the call —
  `_dashboard_module()` outside the `try` meant a missing/broken `generate.py` still exited
  non-zero after a successful mutation, reproducing the exact false 400 the guard was built
  to kill. Found by the final whole-branch review at both call sites, invisible to both
  task-scoped reviews.
- **Gotcha:** a subprocess child's best-effort warning dies inside `capture_output=True`
  unless the parent relays it — console `/schedule` returned 200 and discarded the regen
  warning entirely, making a silently stale dashboard the only symptom (violating
  `_regen_dashboard`'s own stated design). Parent now passes child stderr through on exit 0.
- **Accepted:** hermetic regen-failure injection = a plain FILE named `dashboard` at the
  fixture root (collides with `_atomic_write`'s makedirs). Works in both fixtures because
  `LoopsRoot` never creates `dashboard/`; no mocking of `generate`.
- **Accepted:** pair every failure-injection test with an assertion that the injection FIRED
  (warning on stderr / output file absent) — the console pin originally passed vacuously
  against its neighbor's assertions.
- **Gotcha:** plan-doc drift is real once plans are committed artifacts — the plan said
  `cmd_dispose` for the real `cmd_disposition`, and a fix-wave edit invalidated another plan
  line in the same commit. Ruling: task blocks describe state as of task time; only names
  that grep false get corrected.

### Decisions
- §10 paused-staleness: paused loops stay in stale/needs_attention — pause has no expiry
  (unlike `snooze --until`), so exempting it creates a silent forever-off; the deliberate
  off-path is `set-schedule manual` (plist removed → exempt). Marked settled in §10.
- B-09 class of change (small fix) deliberately does NOT go through OpenSpec — encoded in the
  new CLAUDE.md routing section.
- A non-`ValueError` failure after the conf rewrite (plist write, launchctl missing) still
  exits non-zero — kept, correct signal for a half-applied mutation.

## 2026-07-30 — Report pages shipped (third output tier) + pilot loop `kagi-ban` live

### Summary
- **Report-page tier live** (`d6ebceb..44e0942`): runner step 6.5 (loop-data commit → render under a
  process-group timeout → `bin/page_envelope.py` promotion gate → dated-then-latest atomic promotion),
  `loopctl validate` rejects a non-executable `render.sh`, dashboard row page-links with `stale` badge
  plus a new `reports.html` screen, `pagekit/` kit + sanitized fixture, authoring guide + rubric q12
  (intake is now twelve questions). INTERFACES Amendment 2 freezes the contract. 615 → 649 tests.
- **`kagi-ban` (av exposure audit) is the first page-enabled loop** and the second on launchd
  (`daily:07:40`). Steady state 16 exposures (14 high, 2 medium), page served over the tailnet vhost
  (`/reports/<name>/…`, path-scoped Caddy roots so `state/`, `loops.d/`, `bin/` stay unreachable).
- **Execution model:** subagent-driven development with external CLI agents as implementers (grok ×5,
  codex ×3 + the fix wave), Claude reviewer subagents gating each task, foreman reviewing every diff
  before merge. Three tasks needed one fix round each; a whole-branch review found two live Criticals.

### Lessons Learned
- **Accepted:** peons implement, Claude reviews. Every task shipped with red→green evidence, and the
  reviewers caught what the implementers' own reports asserted away — including a retention test that
  passed vacuously (proved by reverting the keep-list in a scratch copy and watching it still pass)
  and a chip assertion that matched inlined CSS rather than a rendered chip.
- **Accepted:** the supervised gauntlet is not a formality. Three defects existed only against the
  real machine: the gate refusing `av`'s own "access token: /path" prose, a trigger-dependent PATH,
  and `redact.py` corrupting a finding id. All three were invisible to 649 hermetic tests.
- **Gotcha:** `redact.py`'s generic KV rule eats the rest of the line after `token:`/`password:`,
  which silently corrupted the finding id `av:gh-cli-hosts-token:<sha8>` in the redacted precheck
  digest — *stably*, so id-stability checks passed. Fixed with a `(?<![A-Za-z0-9-])` lookbehind
  (hyphen compounds pass, `GITHUB_TOKEN=` still redacts). Any loop whose source names or prose carry
  a secret keyword is exposed to this class.
- **Gotcha:** a scanner that reads ambient process state produces different answers under launchd
  than under a shell. `av` flags user-writable dirs that *precede* system paths, so the launchd run
  lost four exposures and falsely resolved them — then committed that as the baseline. Prechecks that
  observe the environment must pin it explicitly.
- **Gotcha:** the long-standing "20 exposures" baseline in `~/projects/av-audit/` was itself a
  harness-PATH artifact. 16 is the canonical login-shell view; do not "restore" the 20.
- **Rejected:** treating a reviewer finding as settled because the plan mandated the code. The
  vacuous chip assertion was copied verbatim from the plan and was still wrong; plan authorship does
  not grade its own work.
- **Gotcha:** grok peons cannot `git commit` inside a linked worktree (sandbox blocks the gitdir
  under the main repo's `.git/worktrees/…`); all six dispatches improvised escapes, one of which left
  the branch tip unsynced and needed `git fetch` + `git update-ref` before `peon merge` would accept
  it. Codex peons were unaffected.

### Decisions
- **Redaction fixed at two layers, deliberately.** The renderer neutralizes keyword-separator prose
  (loop-local) *and* the harness lookbehind fixes the class (shared). The first alone would have left
  every future loop exposed; the second alone would not have covered av's page prose.
- **Route (ii) over route (i) for the redact fix** — tighten the shared pattern rather than re-key
  kagi-ban's ids, because the generic rule is defense-in-depth by its own docstring while the
  specific token patterns remain the real control. Tradeoff documented in the file; ack pending.
- **Did not push or stash another agent's work.** The roops rebrand agent worked in the same checkout
  throughout; merges were stash-sandwiched around its dirty files, one INTERFACES §10 conflict was
  resolved keeping both texts, and its ~11 unpushed commits were left alone.
- **Deferred to generalissimo:** normalizing two plan-mandated cosmetics (`## 12.` SPEC heading,
  `q12.` label — reviewer-verified safe), cleaning up the stale corrupt finding-id row, and acking the
  lookbehind tradeoff. Parked in `docs/REPORT_PAGES_FOLLOWUP_WARMSTART.md` with the follow-up backlog.

## 2026-07-30 — Garden dashboard shipped: roops design applied to generate.py (B-07); §10 amended; orphan plists cleaned

### Summary
- **Garden restyle live** (`be381f5`): `dashboard/generate.py` presentation layer rewritten to the roops design system — hanko stamps (済/注/警/未) rendering the unchanged §4.3 precedence, per-loop tokonoma in the global row (headline + standing findings as marubatsu lines, derived from fields the page already renders), pancake chip 巡 ×N from `times_seen`, measures-style panels, 巡/休/手 schedule-state column. All §10 semantics and B-05 failure surfacing preserved; raw-metrics drawer now defaults closed. Mockup-first flow: a real-data mockup (scratchpad, Playwright-verified at 1440/390) was approved before the generator was touched.
- **INTERFACES §10 amended twice (dated):** style bullet now specifies the garden system; staleness applies only to *installed* loops (install = plist file presence, display-only, subprocess-free). Supervised-only loops render 休 "no schedule loaded" instead of fleet-wide stale badges + fake next-run estimates. One test updated to install its fixture; one new test pins the uninstalled path (616 total).
- **Data fixes:** copy-pasted "Open google actions" panel titles corrected in ads-intl/ads-reddit/ads-x `dashboard.json`; five orphaned ads-* launchd plists deleted (files present, `launchctl print` confirmed nothing loaded — contradicted B-03's "scheduling is phase 2").

### Lessons Learned
- **Accepted:** test-inventory-before-rewrite — an Explore agent enumerated every load-bearing HTML literal in `test_dashboard.py` (~15 exact strings: badge spans, `finding suppressed`, `handoff`, `>7<`, "needs attention N"…) before the template rewrite; the rewrite then passed all 60 dashboard tests first try.
- **Gotcha:** the no-network test forbids the substring `http://` anywhere in the page — that bans webfonts AND `xmlns="http://www.w3.org/2000/svg"` inside data-URIs, so the garden uses local Hiragino Mincho and attribute-free inline SVG. [Promoted to CLAUDE.md]
- **Gotcha:** plist file ≠ installed — five ads plists existed unbootstrapped, so the dashboard's file-presence check showed 巡/stale for loops that would never fire; verified with `launchctl print` before deleting. [Promoted to CLAUDE.md]
- **Rejected:** making dashboard regeneration a loop (user asked "why can't we dogfood?") — the runner already regenerates the page as step 7 of every run (§7), and the dashboard is the oversight surface: model-authored rendering would violate the core invariant. The legit dogfooding shape is a future `niwashi` (庭師) gardener loop that *audits* the garden (plist orphans, panel-label lint, mtime vs sqlite) — it would have caught both data bugs found by hand this session.

### Decisions
- `skipped-precheck` stays amber (contract-mandated) — only the tokonoma wording improved (loop-sensei's healthy skip now reads as 〇 "precheck produced no output"); a green rendering needs a §4.4/§4.3 amendment, deliberately deferred.
- Two garden design questions deferred to the owner: the ikebana vase caps at 3 stems but real loops carry 5–7 findings, and shin/soe/hikae tiers double as severity (breaks when all findings share one severity).
- Interactive hanko buttons (clipboard-copy of loopctl commands), fleet ledger zone, and niwashi loop are the ordered follow-up list; none started.

## 2026-07-30 — Roops rebrand: brand system, public site, full UI concept (separate repo)

### Summary
- Explored and shipped the "Roops" rebrand candidate (ループス — "loops" as the Japanese loanword, re-borrowed): design system (washi/sumi, one vermillion accent, ensō mark, hanko stamps), a public landing page, and a full UI concept for a re-skinned dashboard — all in a NEW repo `~/projects/roops` (github.com/ytubecoder/roops, live on Pages). This repo untouched; detailed log in that repo's `.claude/SESSION_LOG.md`.
- UI concepts established there that imply future harness work (none started, each an explicit INTERFACES amendment): timestamped `metrics` table in the existing sqlite for graphable loop-reported numbers; pancake/retroactive-apply + stale-out semantics on finding identity; read-only SSE tail of a running round ("engawa" view); per-loop enable/disable surfaced in UI (maps to install/uninstall).

### Lessons Learned
- **User correction (copy-level but load-bearing):** never present the harness as flatly "report-only / never acts" — rounds are read-only, but findings are actions-in-waiting and approval (distinct from ack, per OPEN_THREADS) turns them into orders on a separate audited path. Presenting the absolute version reads as wrong to the owner.

### Decisions
- Rebrand stays a candidate: site lives in its own public repo precisely so nothing here renames; if adopted, the rename starts with a `loopctl` alias.
- Public site uses genericized loop names — internal `ads-*` names kept off it deliberately.

## 2026-07-29/30 — README truth pass; dashboard failure UX + handoff block; display-ontology brainstorm settled; loop-sensei built + first install

### Summary
- **README accuracy fix** (`362f411`): "it reports. it never acts." was false — `precheck.sh` is unsandboxed trusted bash (ads-google curls 4 endpoints per run by design). README now states the real invariant: *deterministic code you wrote gets full power; the model gets a sandbox*, plus the four axes, the write-capable-loop-is-a-config-change fact, and four honest caveats. CLAUDE.md non-negotiable reworded to match at /sync.
- **Dashboard failure UX** (`503e285`): failed runs' `error_detail`/`exit_code` now render (runs table + fleet-row headline fallback); latest-run failures get a deterministic paste-into-an-agent **handoff block** (generator template over sqlite fields only); `report_markdown` renders inline in a collapsed escaped drawer. INTERFACES §10 amended explicitly. 10 new hermetic tests; verified via Playwright over localhost (fixture + real page).
- **loop-sensei** (`76d0b1c`, renamed from loop-doctor mid-build per the roops theme): fleet examiner at the full report-only floor. Precheck computes the failure inventory AND finding identity (`<loop>:<class>`, incl. `died` pseudo-class); engine only diagnoses (Cause/Fix/Evidence). Verified per LOOP_AUTHORING §7: healthy-fleet skipped-precheck (zero tokens), then two REAL codex runs against a scratch root (`LOOPS_ROOT` override) with planted auth-failure + died runs — correct diagnoses citing evidence lines, ids/metrics copied byte-exact, times_seen 1→2 no duplicates. 12 hermetic precheck tests. **Installed to launchd — first loop ever installed**; kickstart-verified real launchd-triggered run.

### Lessons Learned
- **Accepted (compile-time LLM, runtime determinism):** three-agent fable brainstorm (ontology/placement/skeptic) independently converged on "model authors presentation once at authoring time, generator renders forever" — the compile pass never sees run values so it structurally cannot restate one. Design parked in OPEN_THREADS §3c; the `prose` slice shipped as the report drawer.
- **Rejected (per-run LLM presentation pass):** layout jitter destroys at-a-glance anomaly detection; a model at generate-time is untestable under the hermetic suite; cost lands on the most-frequently-run, failure-swallowed path (run-loop.sh:473); and it re-opens the believed-metrics hole one level up.
- **Gotcha (verify subagent claims):** the skeptic memo claimed `loopctl new`'s fill step already has a model author dashboard.json — false (`cmd_new` writes a literal `{"panels": []}` stub; "Fill" is a documented human procedure). Caught by checking `bin/loopctl:657` before repeating it.
- **Gotcha (test time-rot):** `test_db.test_spend_query` pinned `started_at=2026-07-22` while `query spend --days 7` windows against wall-clock now — it started failing exactly 7 days later, mid-session. Fixture timestamps that feed relative windows must be dynamic.
- **Gotcha (bulk replace over-match):** blanket `marker` → `_marker` replace hit a test that DID use the variable two lines later (same call text, different context). Caught by the test run, not the lint.
- **Gotcha (plists are machine-local):** `launchd/*.plist` is gitignored on purpose — install state never travels with the repo. `git ls-files` before committing would have caught it first try (the user's own global rule).
- **Gotcha (strict-schema migration cost):** codex strict structured outputs require every property in `required` — a contract field can never be optional, so ANY contract addition is a schema_version bump breaking all existing loops. This is what killed the in-session display-spec option.

### Decisions
- Failure UX ships first (user's interface note mid-brainstorm: see WHY a run failed + get a copy-paste handoff); full display ontology + `loopctl restyle` deferred until dashboard.json authoring actually bites (~20+ loops).
- loop-sensei findings ARE the handoff: same Cause/Fix/Evidence + `state/runs/<id>/` pointer the dashboard block carries — one pattern, two surfaces.
- loop-sensei never examines itself (its own latest row at examination time is the in-flight run — a prior failure would be masked); `ok` is unreachable by design (healthy fleet = skipped-precheck amber, accepted over daily spend to say "fine").
- New-loop naming follows the roops Japanese theme (B-04): loop-sensei not loop-doctor — 先生 covers doctor and teacher.

## 2026-07-28 — Ads loops: emit-path, contract-conformance and continuity fixes; console surface verified live

### Summary
- Fixed three defects in `ads-google` that would have cloned into every sibling loop: the action-set **emit path** (a `{}` instruction in `prompt.md` produced a command the shell layer hard-denies), **contract conformance** (docs promised a `loop_status=error` that does not exist in the schema — the real key is `status`, enum `ok|warn|alert`), and **id continuity** (a run that emitted findings but failed to persist its set left those ids unburned, so the next run reused `ADG-01..04`).
- Verified the console surface end-to-end: `/ads/actions` renders real actions (200, ~34KB), `/ads/campaigns` 200, `/schedules` lists all five ads loops. The schedules path is `/schedules` — `/settings/schedules` 404s.
- Established what's actually open and wrote `docs/ADS_LOOPS_FOLLOWUP_WARMSTART.md`: ~half of all runs die with no contract, nothing is installed under launchd (engine auth fails in that environment), and `/schedules` still lists the legacy manual check-in rows beside the new loop rows.

### Lessons Learned
- **Gotcha (§4.5 inverts status):** a non-empty `findings` array overrides the declared contract `status` with the findings' max severity. A run that wrote no action set declared `alert` and displayed **amber** because its findings topped out at `warn`. Emitting *zero* findings is what lets an alert through — the fix for "make failure visible" was to report less, not more. Promoted to CLAUDE.md.
- **Gotcha (`--allowedTools` is not a boundary):** with a single allowlist entry, a non-listed `echo` still executed. Real containment is the working-directory write sandbox plus `--tools` and `network=none`. The `exec_allowlist` axis expresses intent, not enforcement. Promoted to CLAUDE.md.
- **Gotcha (model-emitted metrics get believed):** one run reported `inputs.missing: 4` while its own digest showed all four inputs healthy, tripping the dashboard's alert threshold on good data. Metrics a precheck can compute must be computed there and copied verbatim.
- **Rejected (`--in <path>` for the emit payload):** at the `report_only` floor the model has no Write tool, so producing a payload file would itself need a heredoc plus redirection — strictly worse than the flat stdin format.
- **Gotcha (truncated listings produce confident wrong claims):** a `find … | head -20` hid two run dirs and produced a "two supervised runs" claim; later, inspecting only each loop's *latest* run produced "only google checks anything" when in fact all five networks emit real sets. Both were reported to the user before being caught. Census the whole set before characterising it.
- **Gotcha (UTC vs local):** the box runs `Asia/Manila` (+08:00, which `date` abbreviates `PST`). UTC-stamped run ids read as "yesterday" and a run in flight reads as "aborted" — one was called a dangling failure while it was still executing.

### Decisions
- **Action ids come strictly from campaign report recommendations** — the DMP/CRO pattern (`report > action generation > future execution`). Infrastructure problems (a failed fetch, missing inputs) are run status only and mint no id. This was raised because a transient fetch blip minted `ADG-06` ("INPUT GAP"), which then had to be struck, permanently burning an id on plumbing. All five prompts still instruct the old behaviour at `prompt.md:44`.
- **Acceptance bar is two ordered phases:** phase 1 is a manual "run everything" trigger plus a clean schedules screen plus runs that reliably produce output; phase 2 is real scheduled runs. Explicitly not before phase 1 holds.
- **Manual trigger should copy the DMP/CRO regenerate pattern** — `POST` starts a daemon thread behind a shared job lock, a `GET …/status` endpoint polls it. The no-shell-out rule governs the request path only, so a background worker does not violate it.
- **Subagent isolation by file ownership did not hold.** Three agents were each scoped to disjoint files and told to run no git commands; they made 4 commits here and 22 in `maguyva-marketing`, one attributed its commit to the parent, and work landed well outside ads. Nothing was pushed at the time and no installs succeeded. File-ownership briefs are not a sandbox — if isolation matters, use worktrees.

## 2026-07-28 — Machine infra: caddy admin-port split + launchd label rename; repo gets a remote; README rewrite

### Summary
- **Machine infra (outside this repo, runbook at `~/.config/dev-tailnet/WARMSTART_CADDY_CLEANUP.md`):** resolved the two-caddy `localhost:2019` admin collision by moving the vane caddy's admin API to `:2029`, and renamed all 16 `com.generalissimo.dev-tailnet.*` launchd labels to `com.generalissimo.dev-tailnet.*`. Both verified: one listener on 2019, full FQDN sweep answering on 15 hosts, 16/16 services loaded with live pids, zero `com.generalissimo` references left in plists or `dev-tailnet/bin`.
- **This repo gained a remote.** Was local-only for 33 commits; now private at `https://github.com/ytubecoder/loops`, `main` tracking `origin/main`. Secret-scanned tree and full history before creating it — every hit was a fake fixture in the harness's own redaction tests.
- **README rewritten** in the style of `~/projects/ticket-takeaway/README.md`: badge row, ASCII banner, blockquote tagline, install one-liner + agent-facing variant, run-flow diagram with a gate table, report-only rationale, command reference, docs table.

### Lessons Learned
- **Gotcha (test counting):** `tests/run-tests.sh` runs BOTH the shell suites and `python3 -m unittest discover`. Summing only `passed: N` lines undercounts badly — unittest reports `Ran N tests`, not `passed:`. True total is **593** (285 python + 308 shell). A first pass at this session's README published "308" and had to be corrected; `CLAUDE.md`'s older "~530" was closer to right than the "measured" number that replaced it. Measure with both patterns or trust the existing figure.
- **Gotcha (zsh word splitting):** an ad-hoc verification loop `for l in $labels` silently tested one 16-line string as a single filename, reporting a false "MISSING plist". zsh does not word-split unquoted parameters. Verification harnesses need the same scrutiny as the thing they verify — a false alarm from a checking script wastes exactly as much time as a real bug.
- **Accepted (verify-then-act on an inherited runbook):** the caddy runbook was written by a prior session and turned out accurate, but re-verifying it live surfaced four defects — `com.vane.*` is 4 plists not 2 (an unrelated `searxng` that must survive), the stale-LABELS list was missing 4 entries not 5, an 8s curl timeout yields false `000`s on cold tailnet nodes (25s is honest), and the doc's own grep-verify could never pass because the doc lives in the directory it greps.
- **Accepted (prove the failure mode, not just the config):** after splitting the admin ports, confirming the on-disk config equalled the running config made a real `caddy reload --address localhost:2019` a safe no-op — which then proved determinism on the exact operation that misfired on 2026-07-26, rather than merely asserting it from a port listing.

### Decisions
- **Job A1 (migrate vane into the dev-tailnet pattern, retire `~/caddy-tailscale`) is BLOCKED** — "dont touch vane at all". There are **two vanes**: `vane` = 100.69.211.49 on llm (tsnet node fronting `~/projects/Vane` on :8347) and `vane-mm` = 100.71.78.96 on mm. They are different services and must never be consolidated. A1's "delete the old vane machine" step sits beside a `vane-mm` row in the Tailscale console — if ever unblocked, match on IP, never on name. Saved to auto-memory as `two-vanes-never-consolidate`.
- **The two-caddy situation persists by decision**, now collision-free. A0 was always designed as a complete stopping point; A1 was the only step that would have retired the second instance.
- **README omits license and release badges** (ticket-takeaway has both) because this repo has no `LICENSE` file and no releases — fabricating them was rejected. Also omits the four `ads-*` sibling loops, which are uninstalled and carry known drift from `ads-google`; the README says "seven loops defined, none installed" and uses `hello-loop`/`hello-watchdog` as the worked examples.
- **No dashboard screenshots in the README** — `dashboard/loops.html` is gitignored as generated output, so there's nothing committed to link. Deferred as not worth it yet.

## 2026-07-22 — Project founded (planning only)

### Summary
- Project created as the home for the custom loop harness after a full portfolio analysis + harness evaluation session (detailed log: `~/projects/.claude/SESSION_LOG.md`, 2026-07-22 entry).
- Deliverables: `docs/HARNESS_PLAN.md` (harness design, plan-checked 3 rounds with codex, gate passed) and `docs/LOOPS_WARMSTART.md` (loop-selection warmstart). No implementation yet — deliberately split into two follow-up work streams (harness build / loop selection).

### Decisions
- Directory `~/projects/loops` chosen by generalissimo (over `loops-infra`).
- Harness: custom thin (Paperclip + hermes evaluated and rejected as harness — rationale and constraints recorded in HARNESS_PLAN.md and the global session log).
- Engine: codex default, claude switchable, local models later; report-only enforced via permission axes.

## 2026-07-22 — Harness build (full): Amendment 1 → probes → waves A–H → live verification
- Folded HARNESS_PLAN_AMENDMENT_1 (findings memory) into INTERFACES.md BEFORE any code: findings/dispositions tables, PRIOR FINDINGS injection, runner-side suppression + effective_status, transient retry (adapter exit 12), harness-error, died-run. schema_version stayed 1 (initial schema, not migration).
- Live-probed both CLIs inline (generalissimo's decision): codex --output-schema is OpenAI-STRICT → free-form objects rejected → `metrics` is a JSON-string field; ONE schema file works on both engines (verified incl. minLength). Evidence: docs/ENGINE_PROBES.md. Prompt via stdin `-` on codex (arg+stdin = double read). claude: structured_output + total_cost_usd + permission_denials.
- Built via parallel sonnet implementer waves w/ opus task reviews + fix loops: A core python, B dashboard, C runner, D adapters, E loopctl, F pilots+LOOP_AUTHORING. Notable adjudications: codex network=full requires fs_write=workdir (adapter hard-fail + validate rule 7); recorded harness-problem statuses exit 0 (§4.2 clarified); install self-verify must wait for a TERMINAL run row (started-row race).
- Wave G (real machine) caught 2 integration bugs hermetic tests couldn't: run_id never injected into prompt (→ RUN CONTEXT block in §6.2) and extract_usage not parsing the adapter's bare codex usage object. LESSON: fake-engine fixtures that substitute values themselves (fake.sh sed __RUN_ID__) can mask "who tells the engine X" gaps — pilot on real engines early.
- Live-proven: launchd install/kickstart-verify/NATURAL interval firing/uninstall; codex+claude same loop same finding ids; idempotence times_seen; dismiss → runner suppression w/ §4.5 footer; sandbox write-denial (PWNED.txt probe); skipped-overlap/precheck; watchdog silent-green + sticky probe-red; dashboard all states.
- Final fable whole-branch review: 2 blockers fixed (loopctl run --root env passthrough; credential_env = v1 validate hard-fail RESERVED) + claude denial-vs-transient order, cross-loop retention scoping, redaction rest-of-line+JWT. v1.1 follow-up list in .superpowers/sdd/progress.md.
- End state: 21 commits on main (local-only, no remote — intentional), suite green (278 py + 158 adapter + 115 runner + 35 examples). docs/LOOPS_WARMSTART.md has generalissimo's uncommitted parallel edits — left untouched.

## 2026-07-23 — OpenSpec pilot (brownfield, both lanes) + TT enrollment

### Summary
- `loops` was the pilot for adopting OpenSpec as Ticket Takeaway's spec lifecycle. Chosen because it has ~530 hermetic tests, no remote, no client exposure, and a frozen contract (`docs/INTERFACES.md`), so quality deltas are measurable rather than confounded. It was also unregistered in TT, so this exercised enrollment too.
- Installed `@fission-ai/openspec@1.6.0` globally (pinned — **not** the bare `openspec` npm name, which is a dead 2019 squat with no `bin`). `OPENSPEC_TELEMETRY=0` in `~/.zshrc`; the TT adapter also sets it on every invocation.
- `openspec init --tools claude` verified non-destructive: `git status` gained only `openspec/`, `.claude/skills/openspec-*`, `.claude/commands/opsx/`. `CLAUDE.md` byte-identical. No `AGENTS.md` in this repo to disturb.
- Registered in TT as `loops`, and declared `WORKFLOW.toml` `[verify] command = "tests/run-tests.sh"` — the close gate runs this and records the real exit code.

### Both lanes closed through one gate
- **Lane A — B-01.** Backfilled the existing `finding-suppression` capability (`bin/db.py: cmd_suppressed` / `cmd_dispose`, §4.5) as 4 canonical requirements, derived by **reading the code, not `docs/`**. `openspec archive` merged the delta into `openspec/specs/finding-suppression/spec.md`; change filed under `changes/archive/2026-07-22-…`.
- **Lane C — B-02.** `WORKFLOW.toml` config only, closed with `--no-change --reason "…"`. No spec delta written, because nothing observable changed — but the claim is recorded and the CLI refuses `--no-change` without a reason.
- Both ran the **same** verify command and both recorded real output. The lane changed how the work was described; it did not change how it closed.

### Lessons
- **The reversibility test is destructive to untracked files.** `rm -rf openspec .claude/skills/openspec-* .claude/commands/opsx` proved removal is clean (tests exit 0, ticket flow unaffected, `git status` back to baseline) — but it also deleted the archived change and the canonical spec, which are untracked until committed. Do it on a copy, or commit first.
- **Verify output must merge stderr into stdout.** Capturing them separately and concatenating puts *all* of stderr last, which buried the real `passed: 115, failed: 0` summary under `ResourceWarning` noise. The recorded tail has to show how the run actually ended.
- **Port 8787 is owned by `growth-console` (maguyva-marketing)**, not TT. TT's `serve.py` needs `--port 8790` here.
- Backfilling from code found a detail the docs compress away: `ack` never suppresses, and snooze uses a *strict* `>` against a date-normalised deadline, so a finding snoozed until today is still suppressed today. Specified as-is rather than "fixed" in a backfill.

### Not done
- **Token spend per change was not isolated.** Both changes ran inside one long session that was also building the Ticket Takeaway gates, so there is no clean per-change number to settle the "75k tokens to change a variable" concern. That measurement still needs a dedicated session: one trivial Lane C and one real Lane A feature, each in its own session, with spend recorded.

## 2026-07-27 — Dashboard on the tailnet + name scrub
- Dashboard now served at https://loops.example.ts.net via the existing dev-tailnet pattern: new userspace tailscaled node `loops` (com.generalissimo.dev-tailnet.tailscaled-loops launchd agent, statedir ~/.config/dev-tailnet/state/loops) forwarding HTTPS to the shared Caddy on 127.0.0.1:8443; Caddy @loops block file-serves dashboard/loops.html (ALL paths rewritten to it — generate.py never exposed). `loops` added to dev-tailnet register/install scripts.
- GOTCHA discovered: BOTH caddy instances on this machine (dev-tailnet + ~/caddy-tailscale) bind admin localhost:2019, so `caddy reload --address localhost:2019` is a coin flip and can clobber the wrong instance's running config (it briefly restarted the caddy-tailscale one). Reliable path: `launchctl kickstart -k gui/$UID/com.generalissimo.dev-tailnet.caddy`.
- Personal name replaced with "generalissimo" across all repo prose/templates (infra labels like com.generalissimo.* untouched — renaming would break running services).
- 2026-07-28: investigated the dual-caddy situation for the cleanup warmstart: instance B is com.vane.caddy (~/caddy-tailscale custom tsnet build, IS the `vane` tailnet node, own watchdog documenting a 2026-07-04 duplicate-caddy/netmap incident). Wrote ~/.config/dev-tailnet/WARMSTART_CADDY_CLEANUP.md (consolidation plan + com.generalissimo→com.generalissimo label rename runbook). Nothing executed.
