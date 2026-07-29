# Skill import + agent surface — design plan

> **Status: DRAFT — pending generalissimo review (2026-07-30).** Once approved, the mechanical
> changes land as an explicit amendment to `docs/INTERFACES.md` (frozen contract — amend, never
> drift) and this document becomes the design rationale, sibling to `docs/HARNESS_PLAN.md`.

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
2. **No approval gate in v1; observability instead.** Any agent may add/import/install a loop.
   The compensating control is a permanent, visible audit trail (provenance + lifecycle events +
   live-run indicator) — the user always has the underlying agent to reprompt or kill. A
   `require_approval`-style knob is a recorded future setting, default off.
3. **Import is static and zero-token; the supervising agent does the judgment.** `loopctl
   import` never invokes a model. It parses, classifies, extracts, and scaffolds; reshaping
   prose and answering questions is the supervising agent's job, in *its* context, guided by
   `docs/SKILL_IMPORT.md`. The existing gauntlet (validate → supervised run → install) is
   unchanged — import pre-fills, it never bypasses a gate.
4. **Report/propose-only doctrine unchanged.** A skill that *acts* (deploys, sends, pushes)
   is reshaped to propose-only: the loop emits the action it would take as a finding. The
   approve→action bridge stays the separate open thread it already is
   (`docs/OPEN_THREADS_WARMSTART.md`). Local-write maintenance loops (the
   "regenerate a dashboard" class) use the existing `perm_fs_write=workdir` axis + justification.

## 3. Foundation (the INTERFACES.md amendment)

### 3.1 `tags=` in `loop.conf`
- Optional. Comma-separated entries, each matching `^[a-z][a-z0-9:_-]{1,40}$` — the `:` enables
  the `campaign:summer-launch` / `project:maguyva` convention without inventing structure.
- `loopctl list --tag <t>` filters; `--json` includes tags; dashboard renders tag chips and a
  tag filter, grouping loops by project/campaign.

### 3.2 `loop_events` — lifecycle audit trail (sqlite)
```sql
CREATE TABLE IF NOT EXISTS loop_events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  loop_name TEXT NOT NULL,
  event     TEXT NOT NULL,   -- created|imported|validated|installed|uninstalled|paused|resumed
  actor     TEXT NOT NULL,   -- $USER default, or e.g. "claude/maguyva-session"
  ts        TEXT NOT NULL,   -- ISO8601 Z
  detail    TEXT             -- JSON: {"source_skill": "...", "origin_project": "...", ...}
);
```
- Written by the corresponding `loopctl` verbs; every lifecycle verb gains `--actor` (default
  `$USER`). Dispositions keep their own table — no duplication; `loop_events` is lifecycle only.
- Provenance = the `created`/`imported` event's actor + detail, rendered per loop on the
  dashboard and via `loopctl status`.

### 3.3 Live-run visibility
The runner regenerates the dashboard (best-effort, same `_dashboard.lock` discipline) right
after the `start-run` insert, not only at the end. A run row with `finished_at IS NULL` inside
its `timeout_s` window renders as **running now**; the existing died-run rule (§4.6) continues
to catch the hung case. A dashboard-regen failure never affects the run (unchanged rule).

### 3.4 Dashboard additions
- Provenance line per loop; a recent-events strip (from `loop_events`); tag chips + filter.
- **Per-finding action affordances** (extends the 2026-07-29 failure-UX pattern): each open
  finding renders (a) the ready-to-paste disposition commands (already partially present) and
  (b) a collapsed **paste-into-your-agent block** — a deterministic, generator-templated prompt
  built only from sqlite finding fields (`finding_id`, `title`, `severity`, `detail`,
  recurrence) + static path references, that the user can paste into any agent session to act
  on the finding in that agent's own permissioned context. Loops still never acts; the
  dashboard makes the human arrow ergonomic.

## 4. `loopctl import` — the gap-analysis importer

### 4.1 `loopctl import <skill-path> --analyze [--json]`
Purely static (stdlib Python), zero tokens. Parses SKILL.md frontmatter + body + bundled
scripts/references, then classifies every item of the eleven-question intake rubric
(`docs/LOOP_AUTHORING.md` §2) into one of four buckets:

| Bucket | Meaning | Examples |
|---|---|---|
| `answered` | The skill states it | purpose (from description), scope (partial) |
| `derived` | Statically inferable — confirm, don't ask | type, engine recommendation, permission axes from observed tool usage, precheck candidate, tags from origin project |
| `missing` | Must be answered | cadence, finding identity, tier-1 semantics, metrics/panels, budget |
| `incompatible` | Harness can't run it as written — reshaped, with the reshaping stated | interactivity, mutation, MCP dependence, iterate-until-success, conversational-context assumptions |

The `--json` form carries an `answers_needed` array: `{question_id, prompt, context, options[],
suggested_answerer: "agent"|"user"}` — the "give info, show options" pattern, ready for a
supervising agent to relay. Presentation choices (Q10: which metrics become panels, panel
types, thresholds) are first-class `options[]` entries with `suggested_answerer:"user"` where
taste matters, `"agent"` where a sane default exists.

**Derivations, concretely:**
- *Permission axes:* scan for Bash/network/write/MCP usage → propose axis values at the lowest
  sufficient level; anything above the floor carries a drafted justification for the human/agent
  to confirm into `SPEC.md`.
- *Precheck extraction:* deterministic steps in the skill (greps, curls, file scans, `git
  status`-class commands) become a candidate `precheck.sh`, honouring the script→agent split —
  this is where most of the import's token-cost value lives.
- *Engine:* a Claude-idiom skill (tool names, Claude-specific conventions) recommends
  `engine=claude`; otherwise the codex fleet default.

**Reshaping rules** (applied in the scaffold, documented in `docs/SKILL_IMPORT.md`):
interactivity → decisions become findings, dispositions are the answer channel; mutation →
propose-only findings ("the action I would take"); MCP calls → CLI/curl equivalents or flagged
blocked; iterate-until-success → single-shot check-and-report; "the current repo/file" →
explicit `workdir` + scope answer. Plus the environmental flags: metrics-as-string, brace-free
shell payloads.

### 4.2 `loopctl import <skill-path> --apply --answers answers.json [--name N] [--actor A]`
`answers.json` is the response form of `answers_needed`: a JSON object mapping `question_id` →
chosen value (a selected option's id, or free text where the question is open); omitted ids fall
back to the analyzer's derived default where one exists, and otherwise remain `[FILL:]` in the
scaffold so `loopctl validate` catches them. Scaffolds `loops.d/<name>/` fully pre-filled: `SPEC.md` with all eleven sections answered (no
`[FILL:]` left when answers are complete), `prompt.md` = reshaped skill body + the contract
sections + `## Finding identity`, `loop.conf` incl. tags and axes, candidate `precheck.sh`,
`dashboard.json` from the chosen panels. Records the `imported` event with source path + actor.
Never installs. Exits by printing the next steps: `loopctl validate` → `loopctl run` (read the
report against ground truth) → `loopctl install`.

### 4.3 `docs/SKILL_IMPORT.md`
The rubric, bucket definitions, reshaping rules, and the answers.json shape as a standalone
document — it doubles as the manual recipe for any agent (or human) without the tool, and is
the source the loops skill teaches from.

## 5. Agent surface

### 5.1 AXI-compliance pass over agent-facing verbs
- Pre-computed aggregates in `loopctl status` (fleet counts, needs_attention, spend) — no
  N-round-trip enumeration.
- Definitive empty states ("0 loops installed", "0 open findings") — never blank output.
- Compact default output; `--json` escape hatch on all read verbs (we take AXI's principles,
  not its TOON format dependency).
- Content-first: bare `loopctl` prints the live fleet summary, not usage text.
- Already true, kept deliberately: structured exit codes (0/1/2), no interactive prompts on any
  agent path, fail-loud on unknown flags/keys.

### 5.2 The `loops` skill (the discoverability layer — AXI principle 7)
In-repo `skills/loops/SKILL.md`, installed to `~/.claude/skills/loops`. Teaches an agent:
what loops is (report-only recurring runner), when to **offer** an import (the user repeatedly
runs a skill/workflow by hand that fits the check-and-report shape), how to run the import
end-to-end (analyze → relay `answers_needed` → apply → gauntlet), tag conventions, the
`--actor` convention, and what `install` means (goes live on launchd). This delivers the
discoverability MCP schemas would have provided, without the per-turn schema tax.

## 6. Testing (hermetic, house conventions)

- `loopconf` tags parsing (valid/invalid entries, unknown-key behaviour unchanged).
- `loop_events` writes from each lifecycle verb; actor default and `--actor` override.
- `--analyze` against three fixture skills: clean check-shaped, interactive, mutating —
  asserting bucket classification, axes derivation, and `answers_needed` shape.
- `--apply` with canned answers produces a loop that passes `loopctl validate` with zero edits.
- Dashboard: provenance/tags/running-now rendering; per-finding paste blocks built only from
  sqlite fields (fake engine, canned contracts throughout).

## 7. Future directions (recorded, not built)

- **Script-migration suggester** ("de-agentification"): after enough runs, flag loops whose
  engine output is near-invariant relative to precheck input — "this loop's judgment step looks
  deterministic; consider moving it into precheck.sh / a pure script" — lowering token cost.
  Possibly itself a meta-loop reading the runs/metrics tables. This is the path to "a pretty
  full valued loop runner": agent-run first, cheapened into scripts as they prove stable.
- **Approval-gate knob** (allow-all vs approve-gate) as a global setting, default allow-all.
- **Cross-machine push** (WSL → Mac over the tailnet): SSH-wrapped loopctl.
- **Approve→action bridge** — unchanged open thread; the per-finding paste-into-agent block is
  the v1 stand-in (the human carries approval into their agent's own permission context).
