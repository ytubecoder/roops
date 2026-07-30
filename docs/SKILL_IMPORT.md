# Importing an Agent Skill as a loop

> **Audience:** anyone (human or agent) converting an existing Claude Agent Skill (a `SKILL.md`,
> optionally with bundled `references/`/`scripts/`/assets) into a loop. This document is the dual
> of `docs/LOOP_AUTHORING.md` for imports — read that one first for the mental model (§1), the
> eleven-question intake interview (§2), the contract (§3), permission axes (§4), and the schedule
> grammar (§5); this doc only covers what's different about starting from an existing skill instead
> of a blank `loopctl new`. Everything downstream of the intake interview — the contract, the
> gates, the disposition workflow — is identical either way.
>
> Verified against the code as shipped through Task 10: `bin/skill_import.py`'s `parse_skill()` +
> `analyze()`, and `bin/loopctl import <skill-path> --analyze [--json]`. `--apply` is not built yet
> (Task 12) — every mention below is marked as such.
>
> If you're jumping straight to §7 or §8: read §6 first, before uncommenting any proposed precheck
> line.

## 1. What import does and does not do

`loopctl import <skill-path> --analyze` is **static, stdlib-only, and zero-token.** It never
invokes a model. It reads `SKILL.md` (frontmatter + body) plus bundled files, then scans the
combined text with regex. Six of those regexes (`RE_INTERACTIVITY`, `RE_MUTATION`, `RE_MCP`,
`RE_CREDENTIALS`, `RE_ITERATION`, `RE_NETWORK`) are the *detection flags* the rest of the analysis
is built on; two more scan the same text for narrower purposes — `RE_CLAUDE_IDIOM`
(`mcp__`/`AskUserQuestion`/`.claude/`/`allowed-tools`) drives the `engine` recommendation, and a
numeric-content hint (`count`/`number of`/`wc -l`/a bare digit) decides whether `q10_metrics`
offers table/list panel options in addition to number/trend. The result is a structured gap
analysis: what the skill already answers, what can be mechanically derived, what's missing, and
what the harness genuinely can't run as written. That's it. `parse_skill()` and `analyze()` in
`bin/skill_import.py` do not read the network, do not shell out, and do not import anything beyond
`hashlib`, `os`, `re`.

What this buys you: a full read of a real skill costs nothing and produces an accurate skeleton
of the eleven-question intake interview (`docs/LOOP_AUTHORING.md` §2) before a human or a
supervising agent spends a single token thinking about it.

What it explicitly does **not** buy you:

- **The gates are unchanged.** `validate → supervised run → install` (`docs/LOOP_AUTHORING.md` §7)
  is exactly the same gauntlet a hand-built loop goes through. Import produces a scaffold that
  still has to pass `loopctl validate`, still needs a supervised run read against ground truth,
  and still needs a non-failed run before `loopctl install` will touch launchd. Nothing about
  having come from an existing skill exempts a loop from any of this.
- **Reshaping quality is not the tool's job.** The analyzer flags *that* a skill assumes
  interactivity, or mutates something, or depends on MCP — it drafts a one-sentence reshaping note
  per flag (§4 below), but turning "ask the user which environment" into a well-designed finding
  with a good `finding_id` rule is a judgment call for the supervising agent (or human), not
  something six detection-flag regexes can do. The tool guarantees mechanics (parses correctly,
  hashes the input, classifies consistently); it does not guarantee the resulting loop is a *good*
  loop.
- **It does not itself change any permission axis.** `analyze()` always proposes the report-only
  floor (`perm_fs_write=report_only, perm_network=none, perm_local_exec=none,
  perm_remote_mutation=none`) — see §4. Any flag the scan detects that implies the skill wants
  more than the floor becomes an `answers_needed` question with a drafted justification, never a
  silent pre-raise. `axes` is built once as a literal dict in `analyze()` and never mutated
  afterward — the only way a raise gets expressed is through `answers_needed`.

## 2. The eleven-question rubric

`analyze()`'s `rubric` dict has exactly eleven keys, `RUBRIC_IDS` in `bin/skill_import.py`, one
per question in `docs/LOOP_AUTHORING.md` §2's intake interview, in the same order:

| Rubric id | LOOP_AUTHORING.md §2 question |
|---|---|
| `q1_purpose` | 1. Purpose & stop condition |
| `q2_pattern` | 2. Agentic pattern |
| `q3_type` | 3. Type & data flow |
| `q4_cadence` | 4. Cadence (+ why) |
| `q5_scope` | 5. Scope & exclusions |
| `q6_guardrails` | 6. Guardrails, verbatim |
| `q7_axes` | 7. Permission axes + justification |
| `q8_finding_identity` | 8. Finding identity — REQUIRED |
| `q9_semantics` | 9. Tier-1 semantics |
| `q10_metrics` | 10. Tier-2 metrics + panels |
| `q11_budget` | 11. Engine/model + budget |

Two more question ids can appear in `answers_needed` but are **not** rubric ids — they don't
appear in `RUBRIC_IDS` or the `rubric` dict, and they're not part of the `q1..q11` namespace on
purpose (they're axis-raise proposals gated on detected flags, not intake-rubric answers):

- `raise_perm_network` — offered only when the `network` flag fired.
- `raise_perm_remote_mutation` — offered only when the `mutation` flag fired.

Both are listed in `AXIS_RAISE_IDS` in `bin/skill_import.py`.

## 3. The four buckets

Every rubric item lands in exactly one bucket (`RUBRIC_BUCKETS` in `bin/skill_import.py`):

| Bucket | Meaning | What it means for the supervising agent |
|---|---|---|
| `answered` | The skill states it directly | Relay the extracted value for confirmation — don't re-ask from scratch. |
| `derived` | Statically inferable from the skill's shape | Confirm, don't ask. The analyzer already computed a reasonable value (type, engine recommendation, floor axes, the script→agent precheck split). |
| `missing` | Never statically answerable | Must actually be asked — of the user for most of these (cadence, finding identity, tier-1 semantics, metrics/panels, budget), or of the agent itself for `q5_scope` when no real scope heading exists (the supervising agent usually has enough project context to propose scope without bothering the user). |
| `incompatible` | The harness can't run this as written | The skill assumes something v1 doesn't support (synchronous interaction, iterate-until-success, MCP dependence, a mutation above the floor). Reshape per §4, or leave it blocked per §5 if reshaping isn't possible. |

Concretely, exactly what `analyze()` derives per bucket (from `_build_rubric` in
`bin/skill_import.py`):

- `q1_purpose`: `answered` from the frontmatter `description:` field if present, else `missing`.
- `q2_pattern`, `q3_type`: always `derived` — the v1 Human-in-the-loop outer shape and the
  `type=agent` recommendation are constants, not scan results (see the note below on `type`).
- `q4_cadence`, `q8_finding_identity`, `q9_semantics`, `q10_metrics`, `q11_budget`: always
  `missing` — none of these are statically inferable from skill text, ever.
- `q5_scope`: `answered` only if a body heading actually looks like a scope/exclusions statement
  (matches `scope|exclusions?|out of scope|what this does not do`, case-insensitive) — a bare
  document title like `# Repo hygiene check` does **not** count. Otherwise `missing`, offered to
  the agent (`suggested_answerer: "agent"`) rather than the user.
- `q6_guardrails`, `q7_axes`: always `derived` — the report-only floor plus a summary of the six
  detected flags.
- Any rubric item named in `INCOMPATIBLE_RUBRIC_MAP` (`q2_pattern`, `q3_type`, `q7_axes`) whose
  paired flag fires gets its bucket **overridden** to `incompatible`, with a drafted reshaping note
  replacing whatever value it had (§4).

`analyze()` also always appends a note to the top-level `notes` array: `type` recommendation is a
v1 constant (`"agent"`) — watchdog classification (single-probe health check) is not attempted by
the static analyzer; reassess manually if the skill looks like a watchdog candidate.

## 4. Reshaping rules

These are the exact reshaping notes `INCOMPATIBLE_RUBRIC_MAP` in `bin/skill_import.py` drafts when
its paired detection flag fires, transcribed verbatim (each becomes the rubric item's `value` when
its bucket flips to `incompatible`):

| Detected flag | Rubric item flipped | Reshaping note (verbatim) |
|---|---|---|
| `interactivity` | `q2_pattern` | "Assumes synchronous user interaction during a run ('ask the user' / wait-for-approval), but v1 loops run unattended each firing (docs/LOOP_AUTHORING.md §2) — reshape: surface as a finding for async human review instead of an inline prompt." |
| `iteration` | `q2_pattern` | "Assumes iterate-until-success across invocations ('until tests pass' / 'retry until'), but v1 is single-shot per firing with no cross-firing retry loop — reshape: check once per firing and report; a human is the repeat mechanism." |
| `mcp` | `q3_type` | "Depends on an MCP server tool the harness engine may not have configured — reshape to an equivalent CLI/curl call, or leave blocked if none exists." |
| `mutation` | `q7_axes` | "Performs a mutating action (git push/deploy/npm publish/send email or sms/post to/gh pr create/rm -rf) but the harness floor is report_only/none/none/none — reshape to propose-only: the mutating command is emitted as a commented precheck line a human must uncomment deliberately." |
| `credentials` | `q7_axes` | "Requires credentials/API keys/OAuth/bearer tokens the harness has no injection story for yet — blocked until a credential-handling design exists." |

Only `q2_pattern`, `q3_type`, and `q7_axes` can flip to `incompatible` — those are the only keys
`INCOMPATIBLE_RUBRIC_MAP` covers. When more than one flag on the same rubric item fires (e.g. both
`interactivity` and `iteration` on `q2_pattern`), both reshaping notes are joined with a space.

One reshaping rule from the design has no dedicated regex flag and isn't rubric-encoded: **"the
current repo/file" → explicit `workdir` + scope answer.** Skills written for interactive use
routinely assume "the repo I'm sitting in" or "the file I just opened" as an implicit target. A
loop has no such context — it always runs against an explicit `workdir` (`loop.conf`) fired on a
schedule, with no session memory of what a human was just looking at. This shows up as `q5_scope`
staying `missing` (§3) rather than as an `incompatible` flip; resolving it is part of answering
`q5_scope`, not a separate flag.

### The environmental teachings

These aren't skill-specific reshaping rules — they're harness facts every imported loop's
`prompt.md` has to respect regardless of what the source skill assumed, because they're properties
of the runner and the engines, not of any one loop:

- **`metrics` is a JSON string, not a nested object** (`docs/LOOP_AUTHORING.md` §3.2). Forced by
  `codex exec --output-schema`'s strict structured-output mode, which rejects genuinely free-form
  objects before generation starts. A skill's own "return this JSON" instructions need rewriting
  to say "a JSON object serialized into a string" — `"{}"` for "nothing to report" is the example
  worth stating explicitly, since a non-parsing or non-object `metrics` string is a hard
  `contract-violation`.
- **Brace-free shell payloads.** The claude engine's Bash permission matcher hard-denies any
  allowlisted command containing both a brace and a quote character ("expansion obfuscation") —
  before the command runs, invisibly to the script itself (`docs/ENGINE_PROBES.md`). A skill whose
  engine session needs to deliver structured data on stdin (JSON heredocs, `{...}` payloads) needs
  a brace-free format instead — a flat `key: value` + `[section]` form, for example. This is a
  hard denial, not a style preference; do not let a model invent smuggling workarounds (`tr`, hex
  escapes, process substitution) — remove the brace.
- **Effective-status: non-empty findings discard the declared status** (`docs/INTERFACES.md` §4.5).
  If the emitted `findings` array is non-empty, `effective_status` is recomputed as the max
  severity of the *unsuppressed* findings — the engine's own declared `status` field is discarded
  entirely in that case. Only an **empty** `findings` array lets the declared `status` through. A
  skill reshaped into a loop that must be able to surface `alert` on some real-world condition has
  to emit that condition as a finding of severity `alert` — a run that needs to show red while
  emitting zero findings will not show red; it'll show whatever `status` says, and a non-empty
  findings array will override even that.

## 5. Blocked outcomes

Some situations don't get reshaped — they get blocked. `_build_blocked_info` in
`bin/skill_import.py` sets `blocked: true` and appends a reason to `blocked_reasons` (each
prefixed `[blocking]` or `[info]` so a downstream consumer can tell an actual block from an
informational note without parsing prose):

| Situation | Bucket | `--apply` behavior (Task 12 — not built yet, described as the contract) |
|---|---|---|
| Needs API key / OAuth / MCP auth (credentials flag fires) | `incompatible`, `blocked=true` | Refuses unless `answers.json` sets `acknowledge_blocked: true`, in which case it scaffolds with `schedule=manual` plus a `## BLOCKED — read before scheduling` section in `SPEC.md` naming the blockers. |
| MCP-only tooling with no CLI/curl equivalent in the *same source* as the mcp mention | `incompatible`, `blocked=true` | Same acknowledge-and-scaffold-manual path; the blocked tool names land in `SPEC.md`. |
| No extractable deterministic steps (empty `precheck_proposal`) | `derived`, `blocked=false` | Not a block at all — scaffolds the plain template `precheck.sh`, never pretends extraction happened. |

Two things worth being precise about, verified against `bin/skill_import.py`:

- **Credentials always block.** Any credential-like match (`api_key`, `oauth`, `bearer token`, a
  `SCREAMING_SNAKE_KEY/TOKEN/SECRET`-shaped token, or `credential`/`credentials` — the regex is
  `credentials?\b`) sets `blocked=true` unconditionally — there's no CLI-equivalent escape hatch the
  way there is for MCP.
- **MCP blocks unless a CLI equivalent is found in the *same file* as the MCP mention.** `curl` and
  a fixed list of known CLIs (`git`, `gh`, `aws`, `gcloud`, `kubectl`, `docker`, `npm`, `npx`,
  `yarn`, `pnpm`, `stripe`, `vercel`, `wrangler`, `psql`, `mysql`, `ssh`, `rsync`, `terraform`,
  `ansible`, `supabase` — `KNOWN_CLIS`) count as "an equivalent exists." A CLI mention in some
  *other* bundled file doesn't excuse an MCP call actually used in `SKILL.md` — this is
  deliberately narrow (fix-round-1 of Task 9's review), and the analyzer records the mcp decision
  in `blocked_reasons` either way, including the non-blocking case (`[info] mcp: CLI equivalent
  'curl' found in SKILL.md — not blocked`), so the decision is never silent.

Right now, `--apply` is unimplemented: `loopctl import <path> --apply` prints
`"--apply: not implemented yet (Task 12)"` to stderr and exits 2, regardless of `--answers`. Use
`--analyze` (§1, §7) plus the manual recipe (§8) until Task 12 lands.

## 6. Trust — read this before uncommenting anything

**This is the load-bearing safety section of this document. It is not optional reading.**

`analyze()`'s `precheck_proposal` extracts command-looking lines from the skill body (backtick
spans and fenced ```` ```bash ````/```` ```sh ````/```` ```shell ```` blocks) and emits every single
one **commented out**, each annotated `# [read-only?] <cmd>` or `# [MUTATING — do not enable]
<cmd>`. `_propose_precheck` in `bin/skill_import.py` guarantees this: nothing it produces is ever
live precheck code. Uncommenting a line into a real `precheck.sh` is a deliberate human (or
supervising-agent) act, not something import does for you.

Why this matters as much as it does: **`precheck.sh` runs as trusted, UNSANDBOXED bash.** The four
permission axes (`docs/LOOP_AUTHORING.md` §4) govern the *engine invocation* — what the model can
do inside its sandboxed session. They govern nothing about `precheck.sh`. A precheck script has
the full run of whatever the launchd job's user account can do, full stop. This is why every
extracted candidate stays commented rather than becoming live code the moment it's classified
`[read-only?]` — the classification is a hint for a human doing the reviewing, not a gate the
system enforces on its own.

**`[read-only?]` is an advisory heuristic hint. It is NOT a guarantee and NOT a security
boundary.** The classifier (`_is_read_only_command` and friends in `bin/skill_import.py`) walks
the leading command of every `|`/`;`/`&&`/`||`/`&`-delimited segment against a short allowlist
(`ls`, `find`, `grep`, `rg`, `wc`, `cat`, `curl`, `head`, `tail`, `stat`, `du`, `df` — plus `git`
restricted to `status`/`log`/`diff` subcommands) and a set of full-line danger overrides (write
redirects, `find -delete`/`-exec`, non-GET `curl -X`, `curl -o`/`-O`, `tail -f`/`-F`, `xargs` into
a non-read-only command). Two separate rounds of adversarial review already found and fixed real
escapes that got mislabeled `[read-only?]` before shipping:

- `git status && rm -rf build`
- `ls | xargs rm -rf`
- `cat <payload>/etc/hosts` (a bash stdin-redirect-then-stdout-redirect, not a doc placeholder —
  the placeholder-stripping logic that made `git -C <repo> status` safe originally swallowed this
  one's real `>` too)
- `git status & rm -rf build` (bare `&` backgrounding, not `&&`)

Fixing those closed specific holes; it did not close the category. **Known residual escapes remain
in the same family**, deliberately not chased further because a stdlib regex heuristic over
freeform command text cannot become a real shell parser:

- `curl --output <file>`, `curl -T <file>` / `--upload-file`, `curl -d @<file>` — long-form and
  upload/read-from-file forms the write-detection only checks `-o`/`-O` and `-X`/`--request` for.
- Clustered short options, e.g. `curl -fsSLo out url` — the `-o`/`-O` check looks for a standalone
  token, not a flag bundled inside a cluster.
- `find ... -execdir ...`, `-okdir`, `-fprintf` — only bare `-exec\b`/`-delete\b` are checked, and
  `-exec\b`'s word boundary doesn't even match `-execdir` (no `\w`/non-`\w` transition between
  `c` and `d`).
- Process substitution, `<(...)` — the command-substitution demotion checks for `$(` and a
  backtick, not `<(`.

**A human MUST read every line before uncommenting it.** The safety property this design actually
provides is narrow and mechanical: *the line starts out commented, so nothing runs without
someone deliberately removing a `#`.* The safety property it does **not** provide is that the
`[read-only?]`/`[MUTATING — do not enable]` label is correct. Treat every proposed line as
unverified until a human has read it — the label is a hint about where to look harder, not a
verdict to trust.

## 7. `answers.json` shape

`answers.json` is the response form of `answers_needed` — the file `--apply` will consume once
Task 12 lands. This shape is the Task 12 contract (`docs/plans/2026-07-30-skill-import-and-agent-surface.md`
Task 12), not yet implemented:

```json
{
  "analyzer_version": "1",
  "skill_sha256": "…",
  "answers": {
    "question_id": "chosen value — a selected option's id, or free text"
  },
  "provenance": {
    "question_id": "user | agent"
  },
  "acknowledge_blocked": false
}
```

- `analyzer_version` + `skill_sha256` must match what `--analyze` produced for this exact skill
  content — `apply` refuses stale answers if the skill changed underneath them (re-run `--analyze`
  first, don't hand-patch the hash).
- `answers` maps `question_id` → the chosen value: a selected `options[].id` where the question
  offered options, free text where it's open. There is no fallback default for an omitted
  `answers_needed` id — every rubric item that's actually `missing` (§3) is a bare `{"bucket":
  "missing"}` with no `value` to fall back to. An omitted id simply stays `[FILL: ...]` in the
  scaffold, and `loopctl validate` hard-fails while any `[FILL:` marker remains
  (`docs/LOOP_AUTHORING.md` §7) — `apply` never invents an answer. Rubric items already in the
  `answered`/`derived` buckets need no `answers.json` entry at all: `apply` fills those straight
  from the rubric's own `value` (§3), not from `answers` — `answers.json` only ever needs to cover
  what `answers_needed` actually asked for.
- `provenance` is optional, per-answer, `"user"` or `"agent"` — it lands in the `imported` sqlite
  event's detail, so it's visible later which answers a human actually chose versus which ones the
  supervising agent proposed on its own (e.g. `q5_scope` when `suggested_answerer` was `"agent"`).
- `acknowledge_blocked` (default `false`) is required `true` to `apply` a skill whose analysis came
  back `blocked` (§5) — without it, apply refuses outright.

**Filled example**, against the `clean-check` fixture (`tests/fixtures/skills/clean-check`) —
`--analyze --json` on that fixture reports `skill_sha256:
abfdf21dc02cf0bae24104e0a5158d40f71e18e5ecc3b083a6248d68c939901c`:

```json
{
  "analyzer_version": "1",
  "skill_sha256": "abfdf21dc02cf0bae24104e0a5158d40f71e18e5ecc3b083a6248d68c939901c",
  "answers": {
    "q1_purpose": "Report dirty/unpushed repos; done per-firing = report written; cross-run done = repo becomes clean",
    "q4_cadence": "daily:07:30",
    "q5_scope": "~/projects only; exclude maguyva",
    "q8_finding_identity": "<repo-dir-name>:<condition> where condition is dirty|unpushed",
    "q9_semantics": "ok=all clean; warn=any dirty/unpushed; alert=never",
    "q10_metrics": "{\"panels\":[{\"title\":\"Dirty\",\"metric\":\"repos.dirty\",\"type\":\"number\"}]}",
    "q11_budget": "engine default model; ~1k tokens; retry 1; timeout 300"
  },
  "provenance": {
    "q4_cadence": "user",
    "q5_scope": "agent"
  },
  "acknowledge_blocked": false
}
```

Once Task 12 ships, this scaffolds `loops.d/repo-hygiene-check/` fully pre-filled — `SPEC.md` with
all eleven sections answered, `prompt.md` (reshaped skill body + contract sections + `## Finding
identity` from `q8_finding_identity`), `loop.conf` (floor axes and tags, plus `schedule` from
`q4_cadence`, `engine`/`model`/`retry_transient`/`timeout_s` from `q11_budget`), the template
`precheck.sh` with the commented proposals from §6, and `dashboard.json` built from the
`q10_metrics` answer — then records an `imported` sqlite event and prints the next steps: `loopctl
validate` → `loopctl run` → `loopctl install`. It never installs by itself.

## 8. The manual recipe — doing this without the tool

Every agent (Claude or otherwise) can do this by hand; `loopctl import --analyze` is a convenience
that saves the mechanical first pass, not a capability nothing else can replicate.

1. **Read the skill.** `SKILL.md` frontmatter (`name`, `description`) plus body, plus any bundled
   `references/`/`scripts/` files that carry real content (skip binaries, oversized files, and
   symlinked directories — `parse_skill()` does the same for the same reasons: a hostile or
   oversized skill directory shouldn't be able to blow up the read).
2. **Walk the eleven questions** (`docs/LOOP_AUTHORING.md` §2) against what the skill says,
   marking each `answered` (the skill states it), `derived` (you can work it out from the shape of
   the skill), or `missing` (has to be asked) — §3 of this doc.
3. **Scan for the six red flags**, by eye, over the whole skill (body + bundled files): does it
   assume a human answers a question mid-run (interactivity)? Does it push/deploy/publish/send/post
   anything (mutation)? Does it call an MCP tool (mcp)? Does it need an API key, OAuth token, or
   bearer token (credentials)? Does it retry or iterate until something passes (iteration)? Does it
   hit the network (network, `curl`/`http(s)://`/"api call"/`fetch`/webhook)?
4. **Apply the reshaping rules for anything that fired** (§4): interactivity becomes a finding, not
   a prompt; mutation becomes propose-only (a documented action the loop *would* take, never one it
   takes); MCP becomes an equivalent CLI/curl call, or gets named as blocked if there's genuinely
   no equivalent; iterate-until-success becomes a single check-once-per-firing; "the current
   repo/file" becomes an explicit `workdir` plus a written scope answer. Rewrite the skill's own
   "return this JSON" instructions to the metrics-as-string convention (§4), and rewrite any
   structured stdin payload to brace-free form if the target engine is claude (§4).
5. **Decide blocked vs. reshapable** (§5). Credentials always block. MCP blocks unless the same
   file that calls it also names a CLI/curl equivalent you can substitute. If it blocks and you
   still want the scaffold, note the block explicitly in `SPEC.md` and set `schedule=manual` — a
   blocked loop should never be scheduled to fire unattended.
6. **Scaffold by hand:** `loopctl new <name> --type agent --engine codex|claude` (§7 of
   `LOOP_AUTHORING.md`), then fill `SPEC.md`'s eleven sections with what steps 2–5 produced,
   `prompt.md` with the reshaped skill body plus the contract sections and a `## Finding identity`
   heading documenting your `q8` rule, `precheck.sh` with the deterministic bits you identified
   (start every extracted candidate line commented — §6 — and read each one before uncommenting
   it), and `dashboard.json` with panels for whatever metrics you decided on.
7. **Run the same gates as any other loop:** `loopctl validate` → `loopctl run` (read the report
   against ground truth) → `loopctl install`. Nothing about having started from a skill changes
   this.

---

See also: `docs/LOOP_AUTHORING.md` (the base authoring guide this document extends — mental model,
contract, permission axes, schedule grammar, the disposition workflow) and
`docs/SKILL_IMPORT_AND_AGENT_SURFACE_PLAN.md` §4 (the original design this doc transcribes) and
`docs/plans/2026-07-30-skill-import-and-agent-surface.md` (the task-by-task implementation plan,
including Task 12's `apply()` contract in full).
