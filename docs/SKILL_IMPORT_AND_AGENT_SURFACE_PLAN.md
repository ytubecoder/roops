# Skill import + agent surface — design plan

> **Status: APPROVED DESIGN, revised after council review (codex + grok, 2026-07-30).** The
> mechanical changes land as an explicit amendment to `docs/INTERFACES.md` (frozen contract —
> amend, never drift); this document is the design rationale, sibling to `docs/HARNESS_PLAN.md`.
> Review artifacts: the council round's consolidated triage lives in the session log; both
> reviewers' verbatim output is preserved in the session scratchpad only (findings that mattered
> are folded in below).

## 0. What this is

Two features, one foundation:

1. **Skill import** — turn an existing Agent Skill (a `SKILL.md` directory from `~/.claude/skills`,
   a project's `.claude/skills`, or a plugin) into a loop, via a mechanical gap analysis against
   the loop-authoring rubric, so people can schedule workflows they already have.
2. **Agent surface** — make loops usable as a *service* by agents working in any project:
   a principled, agent-native CLI surface (AXI-style), provenance/observability instead of
   approval gates, tags for campaign/project grouping, and a distributable `loops` skill as the
   discoverability layer.

## 1. The user experience this is designed around

- The user installs the **loops skill** in their agent (any project, any machine with access to
  the loops root). From then on the agent knows loops exists, what it's for, and how to drive it.
- Either the **agent offers** ("you run this SEO audit skill every week by hand — want me to push
  it to loops?") or the **user invokes** ("push this skill over to loops so it runs on a
  schedule").
- The agent runs the gap analysis and comes back with **a few questions** — only the ones the
  skill genuinely doesn't answer — plus **a few presentation options** (which metrics become
  dashboard panels, thresholds, panel types). Each needed answer is classified
  **agent-can-default** vs **ask-the-user**, so the interaction stays short: the agent answers
  what it can from its own context, the user picks from options where taste matters.
- The result appears on the **dashboard**, tagged to its project/campaign, with provenance
  ("imported from ~/.claude/skills/seo-audit by claude/maguyva, 2026-07-30").
- **The dashboard's job is low-fi interactivity that provokes action**: when a run surfaces
  something valuable, the user should be able to act on it *right away* — paste a ready-made
  disposition command, or paste a ready-made "hand this to your agent" prompt. Beyond that, the
  user talks to their agent; loops is the generic platform that runs the recurring part so the
  operator doesn't have to.
- Typical imported loops are exactly the harness's home turf: recurring checks, digests,
  monitors — and local maintenance jobs like *regenerate another project's dashboard* (supported
  today by `perm_fs_write=workdir` + a `notes` justification; no doctrine change).

## 2. Decisions settled during design (do not relitigate without new facts)

1. **CLI-first; MCP rejected for v1.** The management surface is `bin/loopctl`, made
   agent-native per the AXI principles (axi.md — "the answer is principled design, not protocol
   choice"; their benchmarks show a principled CLI beating MCP on success, cost, and turns,
   chiefly because MCP schema overhead inflates every turn). This also matches the house
   doctrine already in CLAUDE.md ("prefer CLI/curl over MCP"). If a non-shell context ever
   needs access, the answer is a thin subprocess wrapper over `loopctl --json` — one
   implementation, ever. Cross-machine push (WSL → Mac) when needed is SSH-wrapped loopctl over
   the tailnet, still not MCP.
2. **No human approval gate in v1; observability + one mechanical precondition.** Any agent may
   add/import/install a loop. The compensating controls are (a) a permanent, visible audit
   trail (provenance + lifecycle events + live-run indicator) — the user always has the
   underlying agent to reprompt or kill — and (b) a **mechanical run-first precondition**
   (council round, adopted): `loopctl install` refuses a loop that has no non-failed
   supervised run row, with the refusal message saying to `loopctl run <name>` first. The
   agent itself can satisfy it — no human in the path — it simply makes the documented gauntlet
   order (validate → supervised run → install) mechanically enforced instead of advisory.
   A `require_approval`-style knob is a recorded future setting, default off.
3. **Import is static and zero-token; the supervising agent does the judgment.** `loopctl
   import` never invokes a model. It parses, classifies, extracts, and scaffolds; reshaping
   prose and answering questions is the supervising agent's job, in *its* context, guided by
   `docs/SKILL_IMPORT.md`. The gauntlet is unchanged — import pre-fills, it never bypasses a
   gate. Reshaping *quality* is explicitly not the tool's job; the tool guarantees mechanics.
4. **Report/propose-only doctrine unchanged.** A skill that *acts* (deploys, sends, pushes)
   is reshaped to propose-only: the loop emits the action it would take as a finding. The
   approve→action bridge stays the separate open thread it already is
   (`docs/OPEN_THREADS_WARMSTART.md`). Local-write maintenance loops (the
   "regenerate a dashboard" class) use the existing `perm_fs_write=workdir` axis + justification.
5. **The import trust model follows the house invariant** (README "What Can Actually Change
   Things"): deterministic code the *human* trusts gets full power; the model gets a sandbox.
   `precheck.sh` is trusted UNSANDBOXED bash — therefore **import never writes executable
   extracted commands** (council round, both reviewers). Extraction produces *commented-out*
   proposals only (§4.1). `--actor` is advisory free text for observability, not
   authentication — consistent with "observability, not security."

## 3. Foundation (the INTERFACES.md amendment)

### 3.1 `tags=` in `loop.conf`
- Optional. Comma-separated entries, each matching `^[a-z][a-z0-9:_-]{1,40}$` — the `:` enables
  the `campaign:summer-launch` / `project:maguyva` convention without inventing structure.
- Normalization at parse (council round): empty entries rejected, duplicates removed
  order-preserving, max 8 tags per loop.
- `loopctl list --tag <t>` filters by **exact tag match** (not substring); tags appear in both
  `list --json` and `status --json` (shape: `"tags": ["project:x"]`); dashboard renders tag
  chips and a tag filter, grouping loops by project/campaign.

### 3.2 `loop_events` — lifecycle audit trail (sqlite)
```sql
CREATE TABLE IF NOT EXISTS loop_events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  loop_name TEXT NOT NULL,
  event     TEXT NOT NULL,   -- created|imported|installed|uninstalled|paused|resumed
  actor     TEXT NOT NULL,   -- $USER default, or e.g. "claude/maguyva-session"
  ts        TEXT NOT NULL,   -- ISO8601 Z
  detail    TEXT             -- JSON: {"source_skill","origin_project","skill_sha256",
                             --        "answers_provenance":{"q4_cadence":"user",...}, ...}
);
CREATE INDEX IF NOT EXISTS idx_events_loop_ts ON loop_events(loop_name, ts DESC);
```
- Amendment-level contract (council round): written via `db.py record-event`; read via
  `db.py query loop-events [--loop L] [--limit N]`. `validate` deliberately records **no**
  event (audit spam — it's a build gate, not a lifecycle change). `loopctl new` records
  `created`; `import --apply` records `imported` (parity). `schema_meta.schema_version` stays
  `1` — this is an idempotent additive table, same treatment as Amendment 1. Events are kept
  forever (like all sqlite rows); events for a deleted loop dir are a feature (historical
  provenance), not an orphan bug.
- Provenance = the `created`/`imported` event's actor + detail, rendered per loop on the
  dashboard and via `loopctl status`. `loop_events` is the **single source** of provenance —
  no provenance fields in `loop.conf`. Dispositions keep their own table; `loop_events` is
  lifecycle only.

### 3.3 Live-run visibility
- The runner regenerates the dashboard right after the `start-run` insert — **non-blocking**
  (council round): acquire `_dashboard.lock` with `--wait-s 0`, skip the regen entirely if
  held; never delay the run. End-of-run regen keeps its existing `--wait-s 30`. "Running now"
  may therefore lag under contention — documented, accepted.
- Rendering trichotomy (closes the gap both reviewers flagged): a row with
  `finished_at IS NULL` renders **running** while `now - started_at ≤ timeout_s` (live badge),
  **overdue** (amber tint, still "running") in `(timeout_s, timeout_s + 120s]`, and **died**
  (red, harness-problem marker — existing §4.6 rule, unchanged) beyond that.

### 3.4 Dashboard additions
- Provenance line per loop; a recent-events strip (from `loop_events`); tag chips + filter.
- **Per-finding action affordances** (extends the 2026-07-29 failure-UX pattern): each
  unsuppressed open finding renders (a) the ready-to-paste disposition commands and (b) a
  collapsed **paste-into-your-agent block** — a deterministic, generator-templated prompt
  built from the finding as rendered from **sqlite + the suppression-filtered `latest.json`**
  (the existing §10 source rule; the `findings` table alone has no `detail` column — codex
  catch) plus static path references. Template language (grok catch): it must distinguish
  "suppress/dispose via loopctl" from "act on this in your own agent's permissioned context",
  and must **never use the word "approve"** — ack ≠ approval is settled doctrine
  (OPEN_THREADS). Paste commands include `--root` whenever the generating root ≠
  `$HOME/projects/loops`.

## 4. `loopctl import` — the gap-analysis importer

### 4.1 `loopctl import <skill-path> --analyze [--json]`
Purely static (stdlib Python), zero tokens. Parses the skill, then classifies every item of the
eleven-question intake rubric (`docs/LOOP_AUTHORING.md` §2, stable ids `q1_purpose` …
`q11_budget`) into one of four buckets:

| Bucket | Meaning | Examples |
|---|---|---|
| `answered` | The skill states it | purpose (from description), scope (partial) |
| `derived` | Statically inferable — confirm, don't ask | type, engine recommendation, floor axes, commented precheck proposal, tags from origin project |
| `missing` | Must be answered | cadence, finding identity, tier-1 semantics, metrics/panels, budget |
| `incompatible` | Harness can't run it as written — reshaped or blocked, stated per item | interactivity, mutation, MCP dependence, credentials, iterate-until-success, conversational-context assumptions |

The `--json` form carries `analyzer_version`, `skill_sha256` (content hash of the parsed
inputs), and an `answers_needed` array: `{question_id, prompt, context, options[],
suggested_answerer: "agent"|"user"}` — the "give info, show options" pattern, ready for a
supervising agent to relay. Presentation choices (q10: which metrics become panels, panel
types, thresholds) are first-class `options[]` entries with `suggested_answerer:"user"` where
taste matters, `"agent"` where a sane default exists.

**Supported input layouts** (council round — a matrix, not endless special-casing): a directory
containing `SKILL.md` (bundled `references/`/`scripts/`/assets read as context), or a bare
`SKILL.md` path. Frontmatter parsing is a **flat `key: value` subset only** (stdlib-only rule —
no YAML parser; nested structures are kept as opaque text and noted in the report). Caps:
files > 256 KiB skipped with a note, binary files skipped, symlinks not followed, ≤ 50 files
read. Unreadable referenced files degrade to a note, never a crash.

**Derivations, concretely:**
- *Permission axes — floor-first* (council round, replaces "lowest sufficient from scan"):
  the proposal is **always the report-only floor**. The tool-usage scan (Bash/network/
  write/MCP mentions) is used to *flag* what the skill appears to need, and any raise above
  the floor becomes an `answers_needed` item with `suggested_answerer:"user"` and a drafted
  justification — never a silently pre-raised axis. Rationale: house doctrine is floor unless
  justified, and network/file I/O belongs in the trusted precheck, not the sandboxed engine.
  Engine-specific truth is encoded: for `engine=codex`, `perm_network=full` requires
  `perm_fs_write=workdir` (INTERFACES §5.2 check 7) — if engine-side network is genuinely
  unavoidable, the analyzer says so and recommends `engine=claude` or the codex tradeoff
  explicitly.
- *Precheck proposal — commented, never executable* (council round, both reviewers — this is
  a trust boundary, not a convenience): deterministic-looking steps in the skill (greps,
  curls, file scans, `git status`-class commands) are emitted into the scaffolded
  `precheck.sh` as **commented-out candidate lines**, each annotated by a conservative
  read-only heuristic (`# [read-only?]` / `# [MUTATING — do not enable]`). The scaffold's
  active precheck body is the safe template. Prechecks run as trusted UNSANDBOXED bash;
  uncommenting is a deliberate act by the supervising agent/human, and the import answers
  record that it happened. The token-cost value of precheck extraction is real but it is a
  *proposal*, not an automation.
- *Engine:* a Claude-idiom skill (tool names, Claude-specific conventions) recommends
  `engine=claude`; otherwise the codex fleet default — subject to the axis-aware rule above.
- *Auth/credentials — an explicit dimension* (council round, both reviewers): the analyzer
  detects secret/API-key/OAuth/MCP-auth dependence. `credential_env` is RESERVED and
  hard-fails validate; launchd runs with minimal env. Outcomes:

| Situation | Bucket | `--apply` behavior |
|---|---|---|
| Needs API key / OAuth / MCP auth | `incompatible` (blocked) | Refuse, unless the answers explicitly acknowledge the block → scaffold with `schedule=manual` + a SPEC warning section |
| MCP-only tooling, no CLI equivalent | `incompatible` (blocked) | Same as above; blocked tools named in SPEC |
| No extractable deterministic steps | `derived` (empty proposal) | Scaffold the plain template precheck; never pretend extraction happened |

**Reshaping rules** (applied in the scaffold, documented in `docs/SKILL_IMPORT.md`):
interactivity → decisions become findings, dispositions are the answer channel; mutation →
propose-only findings ("the action I would take"); MCP calls → CLI/curl equivalents or blocked
per the table; iterate-until-success → single-shot check-and-report; "the current repo/file" →
explicit `workdir` + scope answer. Plus the environmental teachings: metrics-as-string,
brace-free shell payloads, and the §4.5 effective-status rule (non-empty findings discard the
declared status — a run that must surface red emits zero findings).

**Naming** (council round): loop name derives from the skill name — lowercase, `_`/spaces →
`-`, invalid chars stripped, truncated to fit `^[a-z][a-z0-9-]{1,40}$`; `--name` overrides
(fleet naming convention — see CLAUDE.md "Naming new loops" — is the supervising agent's
suggestion to make, recorded in `answers_needed`). If `loops.d/<name>` already exists,
`--apply` refuses; `--overwrite` proceeds and records the fact in the `imported` event detail.

### 4.2 `loopctl import <skill-path> --apply --answers answers.json [--name N] [--actor A] [--overwrite]`
`answers.json` is the response form of `answers_needed`: a JSON object carrying the
`analyzer_version` + `skill_sha256` it was produced against (apply refuses stale answers if
the skill content changed — re-analyze first) and a map of `question_id` → chosen value (a
selected option's id, or free text where the question is open), plus optional per-answer
provenance (`"user"` / `"agent"`) which lands in the `imported` event detail. Omitted ids fall
back to the analyzer's derived default where one exists, and otherwise remain `[FILL:]` in the
scaffold so `loopctl validate` catches them.

Scaffolds `loops.d/<name>/` fully pre-filled: `SPEC.md` with all eleven sections answered (no
`[FILL:]` left when answers are complete), `prompt.md` = reshaped skill body + the contract
sections + `## Finding identity`, `loop.conf` incl. tags and axes, the template `precheck.sh`
with commented proposals, `dashboard.json` from the chosen panels. Records the `imported`
event with source path, hash + actor. Never installs. Exits by printing the next steps:
`loopctl validate` → `loopctl run` (read the report against ground truth) → `loopctl install`.

**Success criterion** (council round — grok): passing `validate` is necessary, never
sufficient. The definition of done for an import includes a supervised run read against
ground truth, and — mechanically, in the test suite — finding-id stability across two runs of
an unchanged world. Validate cannot see a volatile `finding_id` rule or a precheck that
empty-skips forever; the supervised run is where those show up.

### 4.3 `docs/SKILL_IMPORT.md`
The rubric, bucket definitions, reshaping rules, blocked-outcome table, and the answers.json
shape as a standalone document — the dual of `LOOP_AUTHORING.md` for imports, cross-linked
both ways (LOOP_AUTHORING §7 gains `import` as the alternate entry path beside `new`). It
doubles as the manual recipe for any agent (or human) without the tool, and is the source the
loops skill teaches from.

## 5. Agent surface

### 5.1 AXI-compliance pass over agent-facing verbs
- Pre-computed aggregates in `loopctl status` (fleet counts, needs_attention, spend) — no
  N-round-trip enumeration. In-scope for this pass (council round): fix the known
  `cmd_status` blanking when the latest row is `started`/`skipped-overlap` (OPEN_THREADS §3) —
  "definitive, never blank" cannot ship on top of that bug.
- Definitive empty states ("0 loops installed", "0 open findings") — never blank output.
- Compact default output; `--json` escape hatch on all read verbs (we take AXI's principles,
  not its TOON format dependency).
- Content-first: bare `loopctl` prints the live fleet summary and exits **0** (a deliberate
  behavior change from usage-on-stderr exit 2 — council round; `--help` and unknown-verb
  exit-2 behavior unchanged).
- Already true, kept deliberately: structured exit codes (0/1/2), no interactive prompts on any
  agent path, fail-loud on unknown flags/keys.

### 5.2 The `loops` skill (the discoverability layer — AXI principle 7)
In-repo `skills/loops/SKILL.md`, installed to `~/.claude/skills/loops` (install/update = copy
or symlink from the repo; documented in the skill's own header). Teaches an agent: what loops
is (report-only recurring runner), when to **offer** an import (the user repeatedly runs a
skill/workflow by hand that fits the check-and-report shape), how to run the import end-to-end
(analyze → relay `answers_needed` → apply → gauntlet), tag + fleet naming conventions, the
`--actor` convention, `LOOPS_ROOT`/`--root` discovery for non-default roots, and what
`install` means (goes live on launchd, requires a prior non-failed supervised run).
Honest scope (council round): this skill covers Claude-family agents; every other agent gets
the same capability via `docs/SKILL_IMPORT.md` + the AXI-compliant CLI — the claim is
"CLI for any agent, skill for Claude", not "skill for any agent".

## 6. Testing (hermetic, house conventions)

- `loopconf` tags parsing: valid/invalid entries, dedupe, max-count, unknown-key behaviour
  unchanged; `list --tag` exact-match filtering.
- `loop_events`: writes from each lifecycle verb (incl. `created` from `new`), actor default
  `$USER` and `--actor` override, `record-event`/`query loop-events` round-trip, idempotent
  schema add on an **existing** populated DB.
- `--analyze` fixtures: clean check-shaped skill, interactive skill, mutating skill,
  credential/MCP-blocked skill, no-deterministic-steps skill, frontmatter edge cases
  (nested YAML kept opaque, oversized/binary/symlink inputs skipped with notes) — asserting
  bucket classification, floor-first axes, blocked outcomes, and that every extracted precheck
  line in the scaffold is **commented** (the unsafe-extraction fixture).
- `--apply`: canned answers produce a loop that passes `loopctl validate` with zero edits;
  stale `skill_sha256` refused; name sanitization; collision refused without `--overwrite`;
  dangerous-combo configs still fail validate post-import; two fake-engine runs of the
  scaffold produce stable `finding_id`s (the id-stability criterion).
- Install precondition: `install` refuses with no non-failed run row; passes after one.
- Dashboard: provenance/tags rendering; running/overdue/died trichotomy; start-of-run regen
  skips (not blocks) under a held lock and never fails the run; paste blocks sourced from
  sqlite + filtered `latest.json`, containing disposition verbs and no "approve" wording.
  Fake engine, canned contracts throughout.

## 7. Future directions (recorded, not built)

- **Script-migration suggester** ("de-agentification"): after enough runs, flag loops whose
  engine output is near-invariant relative to precheck input — "this loop's judgment step looks
  deterministic; consider moving it into precheck.sh / a pure script" — lowering token cost.
  Possibly itself a meta-loop reading the runs/metrics tables. This is the path to "a pretty
  full valued loop runner": agent-run first, cheapened into scripts as they prove stable.
- **Approval-gate knob** (allow-all vs approve-gate) as a global setting, default allow-all.
- **Cross-machine push** (WSL → Mac over the tailnet): SSH-wrapped loopctl.
- **Approve→action bridge** — unchanged open thread; the per-finding paste-into-agent block is
  the v1 stand-in (the human carries the decision into their agent's own permission context).

## 8. Build order (council round — phased so nothing teaches an incomplete surface)

1. **Foundation:** tags + `loop_events` + `--actor` + list/status/dashboard chips + provenance.
2. **Live-run visibility** (the non-blocking lock policy + trichotomy).
3. **Per-finding paste blocks** (extends the shipped failure-UX pattern — low risk).
4. **`import --analyze`** + fixtures + `docs/SKILL_IMPORT.md`.
5. **`import --apply`** + install run-first precondition + LOOP_AUTHORING cross-link.
6. **AXI status/list polish** (incl. the status blanking fix, bare-`loopctl` summary).
7. **`skills/loops`** last — it teaches a surface that by then actually exists.

Each phase lands with its INTERFACES.md amendment delta and its tests; the amendment is
written as numbered section deltas (the Amendment-1 pattern), not design prose.
