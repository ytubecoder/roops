# Loop authoring — the complete guide

> **Audience:** anyone (human or agent) building a new loop. This document, plus `loopctl new`
> and a `loopctl validate` loop, is meant to be **sufficient on its own** — you should not need to
> read `docs/INTERFACES.md` to build a valid loop. (If you find yourself needing to, that's a bug
> in this doc — fix this file rather than routing around it.)
>
> Worked reference: `examples/hello-loop` (agent) and `examples/hello-watchdog` (watchdog) are
> real, `loopctl validate`-clean loops kept permanently in the repo as regression fixtures. Every
> example command below either matches what they contain or was run against them directly. They
> are never installed to launchd — copy their shape, don't repurpose the directories themselves.

## 1. How a loop runs — the mental model

One launchd firing = one lock = at most one engine invocation = one promotion decision. Nothing
about a loop's schedule ever compounds risk: a hung run cannot corrupt the next one, a bad
emission cannot overwrite a good report, and a loop that finds nothing costs (close to) zero
tokens.

```
launchd fires (or you run `loopctl run <name>` / `bin/run-loop.sh <name> --trigger manual`)
  │
  ▼
run-loop.sh: resolve loop.conf, acquire the per-loop lock (non-blocking)
  │  contention? -> record runner_status=skipped-overlap, exit 0. Not an error.
  ▼
precheck.sh (if present) — deterministic, capped, redacted stdout capture
  │  type=agent:  exit 0 + EMPTY stdout -> skipped-precheck (amber), no engine invoked, exit 0.
  │               exit 0 + non-empty stdout -> continue, injected into the prompt.
  │               non-zero exit -> precheck-failed (alert), no engine invoked, exit 0.
  │  type=watchdog: precheck IS the job. exit 0 -> silent-green, heartbeat ok=1, DONE, no engine.
  │                 non-zero / failure output -> escalate (continue below), heartbeat ok=0.
  ▼
PRIOR FINDINGS injection — the runner (never the model) renders every one of this loop's
currently-open findings from sqlite: id, "seen N× since <date>", current disposition. This is
the mechanical memory that makes recurring findings not repeat themselves forever (§2, "the
honest v1 mapping").
  ▼
Engine invocation — a FRESH session every time (no --resume/--continue, ever). The composed
prompt = prompt.md + RUN CONTEXT block (always — this is the ONLY place the model learns the
run_id it must echo back, §6.2) + PRIOR FINDINGS block (if any) + PRECHECK OUTPUT block (if any).
Runner-owned process-group timeout (TERM -> 10s grace -> KILL); partial engine.log is preserved
either way.
  ▼
Contract validation — the engine's final JSON message is validated against
contract/contract.schema.json. Invalid or missing -> contract-violation (alert), NOTHING IS
PROMOTED. Valid -> atomically renamed into place.
  ▼
Findings upsert (sqlite) — matched by finding_id: seen before -> times_seen++, last_seen_*
updated; a finding from the previous run that's absent this run -> resolved_at = now; a finding
that reappears after being resolved -> resolved_at clears, times_seen keeps counting (it is the
same finding, not a new one).
  ▼
Suppression (runner-side, mechanical, never the model's job) — findings currently dismissed, or
snoozed with snooze_until in the future, are filtered out of the PROMOTED view (latest.json,
latest.md, the dashboard) but stay verbatim in the per-run audit copy
(state/runs/<id>/contract.json). effective_status is recomputed from the UNSUPPRESSED findings.
  ▼
Promotion — write-tmp-then-rename latest.json / latest.md / the dated report. Only a run that
BOTH completed AND validated ever reaches this step — a timeout or a bad emission leaves the
previous latest.* exactly as it was.
  ▼
Dashboard regeneration (best-effort; a dashboard failure never changes the run's recorded status)
```

**The guarantees this buys you, stated plainly:**

- **Stale-green is impossible.** The dashboard can only ever show the last run that both
  completed and passed schema validation — never a hung or malformed one silently overwriting a
  good result.
- **Every loop is report/propose-only, mechanically, not by prompt text.** The engine physically
  cannot write outside its run dir, reach the network, run shell commands, or mutate anything
  remote unless the corresponding permission axis (§4) is explicitly raised above its default —
  and raising it requires a written justification that `loopctl validate` checks for.
- **Suppression is runner-side.** A loop's `prompt.md` tells the model to keep re-emitting a
  dismissed finding if it's still true (§3) — the runner is what actually hides it from the
  promoted view. This is deliberate: never trust a model to reliably suppress its own output:
  dispositions are enforced by code that ships once, not re-derived correctly by every invocation.

## 2. The intake interview

Before scaffolding anything, walk through these eleven questions — with generalissimo if it's a real loop,
or as a self-interview if you're an agent producing the `SPEC.md` yourself. Answer them **in this
exact order**; `loopctl new` seeds `SPEC.md` with these same eleven headings (§7), so the
interview and the spec file are the same document.

**1. Purpose & stop condition.** What is this loop for, and what does "done" look like at two
different scales: **per-firing** (this maps directly onto tier-1 `status` semantics — see §9 in
your `SPEC.md`) and **cross-run** (this maps onto the findings lifecycle — a finding's "done" is
`resolved_at` getting set because the condition stopped being true).

**2. Agentic pattern.** Which shape does the work inside a single engine invocation actually take?

| Pattern | Shape | Useful for | Reason step |
|---|---|---|---|
| ReAct (most common) | Act → Observe → Reason → Repeat | complex tasks, coding, research | yes |
| Simple Iterate | Generate → Review (AI/rules) → Repeat | content/design iterations | optional |
| Plan-then-Execute | full plan first → execute steps | structured projects | mostly upfront |
| Verification Loop | Generate → Test/Check → Fix if bad | coding, math, clear tests | light |
| Human-in-the-loop | AI proposes → Human approves → Repeat | high-stakes work | minimal |
| Feedback-only | Output → External feedback → Refine | vibe-based generation | optional |

**The honest v1 mapping.** Every v1 loop's *outer* shape is **Human-in-the-loop** — full stop.
This isn't a simplification for the interview's sake, it's the actual architecture (Harness Plan
Amendment 1): propose-only + dispositions (`loopctl ack/dismiss/snooze/reopen`) + findings memory
(PRIOR FINDINGS injection, §1) together implement the "approve → repeat" arrow. A loop doesn't get
to auto-fix, auto-retry-until-success, or iterate across firings — a human (generalissimo, via `loopctl`) is
the repeat mechanism. ReAct and Plan-then-Execute can absolutely happen **inside** a single engine
invocation, steered by how you structure `prompt.md` (e.g. "first enumerate X, then for each,
check Y, then summarize") — that's fine and common. What's explicitly **out of v1** is a
Verification Loop or any other iterate-until-success pattern **across** invocations — the runner
is hard-wired to one engine invocation per firing (§1), and there is no `max_iterations`, no
self-verification cycle, no resumed session. **If the intake interview lands on "this needs to
try, check, and retry until it succeeds"**: do not build that machinery. Record the v2 aspiration
honestly in `SPEC.md` §2, and shape a v1-conformant single-shot version instead (e.g. "check once
per firing and report" rather than "keep trying until it works"). Never silently build unsupported
machinery to satisfy an interview answer the harness doesn't support yet.

**3. Type & data flow.** `agent` or `watchdog`? For `watchdog`, `precheck.sh` **is** the job (it
alone determines silent-green vs. escalation) — see `examples/hello-watchdog`. For either type,
be explicit about the split: what does `precheck.sh` gather **deterministically** (zero-token,
zero-judgment — grep, curl, `git status`, file stats) versus what is the **engine** asked to
**interpret**? This script→agent pattern is the harness's primary cost control (§6) — precheck.sh
should do everything that doesn't require judgment, and the prompt should make it obvious the
engine's job starts from precheck's output, not from re-discovering the world itself.

**4. Cadence (+ why).** Pick a schedule (§5 grammar) and say why that interval — daily digest?
hourly watchdog? What's the staleness expectation if a firing gets missed (§5, dashboard `stale`
detection)?

**5. Scope & exclusions.** What's explicitly in scope, and — just as important — what's
explicitly **excluded**? Name specific exclusions where they apply (e.g. the project-wide maguyva
hard-exclude) rather than leaving scope implicit.

**6. Guardrails, verbatim.** Copy the exact guardrail language this loop must respect from project
docs (e.g. "report/propose-only," "never mutate outside `$LOOPS_ROOT`," any loop-specific rule).
These get embedded **twice**: literally, in `prompt.md`, AND enforced independently via the
permission axes (§4) — a guardrail that exists only as prompt text is not a guardrail per this
harness's design; it's an unenforced suggestion.

**7. Permission axes + justification.** Pick values for all four axes (§4). Anything above the
report-only floor (`report_only / none / none / none`) needs a **written** justification in
`SPEC.md` — not because the tooling requires prose (though `loopctl validate`'s dangerous-combo
checks do enforce some of this mechanically), but because a human reviewing the spec later needs
to know *why* this loop needed more than the floor. List any dangerous combo (§4's numbered list)
this loop's configuration comes close to, even if it doesn't trip one.

**8. Finding identity — REQUIRED.** What **is** a finding for this loop (one sentence), and what
is the exact `finding_id` derivation rule? This is the single most load-bearing answer in the
interview: get it wrong and every finding either duplicates itself endlessly (id embeds something
volatile) or silently merges unrelated conditions (id too coarse). §3 covers the rule in full;
`loopctl validate` hard-fails a loop whose `prompt.md` doesn't document this under a
`## Finding identity` heading.

**9. Tier-1 semantics.** For *this* loop specifically, what do `ok`, `warn`, and `alert` mean?
("This loop never uses `warn`" is a perfectly valid answer, as `examples/hello-watchdog`
demonstrates — say so explicitly rather than leaving it to be inferred.)

**10. Tier-2 metrics + panels.** What numeric (or table/list) values does this loop emit inside
`metrics`, and how does `dashboard.json` render each one (§3.3)? At minimum, name every metric key
and its intended panel type.

**11. Engine/model + budget.** `engine=codex|claude`, `model=` (blank for engine default), a rough
expected tokens/run (order of magnitude is fine — "a few hundred tokens" vs. "tens of thousands"
matters more than precision), `retry_transient` (default 1, max 3), `timeout_s` (30–7200, default
900 — pick something proportional to the actual work, not the ceiling).

**Mapping to `SPEC.md`:** `loopctl new <name>` scaffolds `loops.d/<name>/SPEC.md` (or
`examples/<name>/SPEC.md` with `--from examples`) with exactly these eleven numbered headings,
each holding a `[FILL: ...]` placeholder. `loopctl validate` hard-fails while any `[FILL:` marker
remains — the spec is not decoration, it's a build gate. Filling it in **is** answering the
interview; `examples/hello-loop/SPEC.md` and `examples/hello-watchdog/SPEC.md` are full worked
answers to all eleven questions if you want to see the shape a real answer takes.

## 3. The contract

### 3.1 Tier-1 shape

```json
{
  "schema_version": 1,
  "run_id": "…",
  "status": "ok | warn | alert",
  "status_reason": "short_machine_category",
  "headline": "one line, e.g. '3 repos unpushed, 1 has no remote'",
  "report_markdown": "# full human report…",
  "metrics": "{\"repos\": {\"dirty\": 2}}",
  "findings": [
    {
      "finding_id": "cookingapp:no-remote",
      "title": "cookingapp has 23 unpushed commits and no remote",
      "severity": "info | warn | alert",
      "detail": "…"
    }
  ]
}
```

This is the **entire** emission — the schema-enforced final message from the engine IS the
report; there is no second free-text channel that can get lost or fall out of sync. `run_id` must
equal the run id the runner gave you — the runner always appends a `## RUN CONTEXT` block to the
composed prompt (after `prompt.md`, before PRIOR FINDINGS/PRECHECK OUTPUT, §6.2) with the exact
`run_id` value to copy in; the model has no other way to learn it. A mismatch is a hard
`contract-violation`.

**Findings rules (the three every `prompt.md` embeds, and why):**

1. **Re-emit a still-true finding with its same `finding_id`** — never invent a new id for a
   recurring condition. This is what lets the runner increment `times_seen` instead of creating a
   duplicate.
2. **Do not re-argue a `DISMISSED` finding** unless the underlying situation has *materially*
   changed — and if it has, say what changed. generalissimo dismissed it once; don't make him read the same
   argument every day.
3. **Still emit `SNOOZED` findings if true.** Suppression is the runner's job (§1), not the
   model's — if the engine silently drops a snoozed finding, the runner has nothing to un-suppress
   when the snooze expires, and the finding's `last_seen_at`/`times_seen` stop updating.

**`finding_id` derivation — the one rule that matters most:** deterministic and stable across
runs *for the same real-world condition*, and it must **never** embed volatile data — no
timestamps, no run ids, no counts, no line numbers that can shift. Derive it from the durable
identity of the thing being reported: `<subject>:<condition>`. Identity is entirely **loop-
defined** — you decide what "the same finding" means for your loop, and you document that rule
under a `## Finding identity` heading in `prompt.md` (§2, question 8). `examples/hello-loop` uses
a fixture filename as the subject (`alpha:has-todo`); `examples/hello-watchdog` uses a fixed
literal subject (`target:unreachable`) because it only ever watches one thing. **The engine emits
identity only** — it never computes recurrence, age, or "3rd time seen"; that's the runner's job,
derived from sqlite, and a `prompt.md` that asks the model to count its own history is a bug.

### 3.2 `metrics` is a JSON *string*, on purpose

```json
"metrics": "{\"repos\": {\"dirty\": 2}}"
```

Not a nested object. This looks awkward, but it's forced by empirical fact
(`docs/ENGINE_PROBES.md`): `codex exec --output-schema` uses OpenAI's *strict* structured-output
mode, which requires every object in the schema to declare `additionalProperties:false` and list
every property in `required` — a genuinely free-form object is rejected with a 400 before
generation even starts. `metrics` needs to be free-form **in content** (every loop's metrics look
different), so the schema encodes it as a string and the runner (`db.py record-metrics`) parses
and flattens it after the fact. `validate_contract.py` checks the string parses as a JSON object;
a non-parsing or non-object `metrics` string is a `contract-violation`. When you're writing
`prompt.md`, say this explicitly and give an example (`"{}"` for "nothing to report") — this is
the single most common way a first-time loop fails schema validation.

**Flattening rule:** top-level keys of the parsed object become metric keys; nested objects
flatten with `.` (`repos.dirty` → key `repos.dirty`, depth-capped at 3); arrays are stored whole,
not flattened. Numbers/booleans go to the numeric column (bool → 0/1); everything else is stored
as JSON text. This is why `examples/hello-loop`'s `dashboard.json` refers to the metric as
`world.todo_files` even though the engine only ever emits a flat `{"world.todo_files": 2}` (no
nesting needed there, but the dotted-path convention is worth following even for flat keys, since
it's what a real multi-domain loop will need).

### 3.3 `dashboard.json` panel reference

```json
{"panels":[
  {"title":"Dirty repos","metric":"repos.dirty","type":"number","unit":"repos",
   "direction":"higher_is_worse","thresholds":{"warn":1,"alert":5},"missing":"gap"},
  {"title":"Unpushed","metric":"repos.unpushed","type":"trend","window_days":30,"missing":"hold"}
]}
```

- **`type`**: `number` (a single current value, optionally coloured by `thresholds`) | `table`
  (metric value must be an array of objects; columns = the union of keys, stable order) | `list`
  (array of scalars) | `trend` (reads the metric's history from sqlite over `window_days`,
  default 30 — needs several runs to look like anything; a fresh loop's trend panel is just a
  point until it's fired a few times).
- **`direction`**: `higher_is_worse | lower_is_worse | neutral` — governs whether a value above
  `thresholds.warn`/`thresholds.alert` colours the panel amber/red. **This coloring is cosmetic
  only** — it never overrides the contract's own `loop_status`/`effective_status`; a loop can have
  a red metric panel while the dashboard's actual status light is green, if that's genuinely what
  the loop intends.
- **`missing`**: `hold` (carry the previous value forward, visually marked stale) or `gap`
  (render a hole — nothing plotted for that point).
- `dashboard.json` may be `{"panels": []}` (see `examples/hello-watchdog` — a single-target
  watchdog has no numeric trend worth declaring) or even absent entirely; either way you still get
  the tier-1 status row plus a **raw fallback panel** — undeclared metrics are never hidden, they
  render capped (2 KiB) with a link to the full report. Declaring panels is about presentation,
  not about whether data shows up at all.

## 4. Permission axes

Four independent axes, every one defaulting to the report-only floor:
`perm_fs_write=report_only`, `perm_network=none`, `perm_local_exec=none`,
`perm_remote_mutation=none`. A loop that never sets any of these lines in `loop.conf` is already
at the floor — most loops, including both examples, never need to raise anything.

| axis | values | semantics |
|---|---|---|
| `perm_fs_write` | `none` \| `report_only` \| `workdir` | `none`: the engine may not write anywhere at all. `report_only`: may write only inside its own `state/runs/<run_id>` dir (the contract/report path) — this is enough for almost every loop, since the schema-enforced final message *is* the whole report. `workdir`: may write inside `workdir` — requires a `notes` justification (dangerous combo 5) and, as of the pilot fleet, no loop actually needs it. |
| `perm_network` | `none` \| `full` | `full` permits outbound network **for the engine**. This does not by itself grant the right to mutate anything — that's `perm_remote_mutation`. Note: for a `type=watchdog` loop, the *probe* (`precheck.sh`, e.g. `curl`) is a plain, unsandboxed script and is never governed by this axis at all — only the diagnosis engine invocation is (see `examples/hello-watchdog/SPEC.md` §7). |
| `perm_local_exec` | `none` \| `allowlist` \| `full` | `none`: no shell commands. `allowlist`: only commands matching `exec_allowlist` (comma-separated, quoted patterns). `full`: unrestricted local commands, still bounded by whatever `perm_fs_write` allows. |
| `perm_remote_mutation` | `none` \| `allowlist` | The right to change remote state — push, open a PR, post, spend money. `none` is the fleet default, and is enforced by allowlists + read-only credentials, never by prompt wording. Raising this **requires** a non-empty `remote_mutation_justification` in `loop.conf` (dangerous combo 2). |

**Read-only credentials for remote-capable CLIs:** if a loop needs to *read* something via a tool
that's also capable of mutation (`gh`, `aws`, `gcloud`, `git`, …), prefer scoping the credential
itself read-only where the platform supports it (e.g. a fine-grained, read-only-scoped GitHub
PAT) over relying solely on the command allowlist. Credentials are named via `credential_env`
(comma-separated env var names — the values are passed through to the engine and are never
logged); belt-and-braces is the point — a scoped-read command pattern *and* a credential that
physically cannot mutate anything is stronger than either alone.

**`loopctl validate`'s dangerous-combination checks — hard failures, not warnings:**

1. `perm_network=full` **and** `perm_local_exec != none` **and** `exec_allowlist` is empty/absent
   — unrestricted network plus unrestricted-or-unlisted local exec with nothing scoping what gets
   run is the textbook exfiltration/abuse shape.
2. `perm_remote_mutation != none` **without** a non-empty `remote_mutation_justification` — a
   mutation-capable loop must say, in writing, why.
3. `perm_local_exec=full` **and** `perm_network=full` — no loop in the fleet needs this; it
   requires an explicit `i_accept_unrestricted=true` override to pass validate at all, so it can
   never happen by accident.
4. An `exec_allowlist` entry that names a remote-capable CLI in a bare or mutating form — a bare
   tool name (`gh`, `git`, `npm`, `curl`, `aws`, `gcloud`, `vercel`, `wrangler`, `supabase`) or an
   entry whose first tokens match a known mutating verb (`gh pr create`, `git push`, `npm
   publish`, `aws … delete`, …) is rejected. Scoped read forms (`gh run list`, `git status`, `npm
   outdated`) are fine.
5. `perm_fs_write=workdir` **without** `notes` explaining why — the workdir-write axis is rare
   enough that every use should be self-documenting.
6. `type=watchdog` without an executable `precheck.sh`; an unparseable `schedule`; a missing
   engine adapter (`engines/<engine>.sh`); `name` not matching the loop's own directory name.
7. `engine=codex` **and** `perm_network=full` **and** `perm_fs_write != workdir` — codex's sandbox
   can only grant network access under `workspace-write`; under a read-only sandbox the network
   key is silently inert, so this combo can never do anything useful. It's a config contradiction,
   not a stronger guardrail — `loopctl validate` catches it before you find out the hard way that
   your "network-enabled" loop never actually reached the network.

## 5. Schedule grammar + launchd sleep semantics

| form | meaning | launchd mapping | staleness expectation |
|---|---|---|---|
| `manual` | never scheduled | (install refuses) | ∞ — exempt from stale detection |
| `interval:15m` / `interval:2h` | every N | `StartInterval` (seconds) | N |
| `daily:07:30` | every day at local 07:30 | `StartCalendarInterval{Hour,Minute}` | 24h |
| `times:07:30,19:30` | those local times daily | array of `StartCalendarInterval` | 86400 / count |
| `weekly:mon:08:00` | that weekday, local | `+Weekday` (0=Sun…6=Sat) | 7d |
| `monthly:01:09:00` | that day-of-month, local | `+Day` | 30d |

All calendar times are **local**, matching launchd's own semantics.

**launchd + sleep — document it, don't fight it:** a calendar-based schedule (`daily`, `times`,
`weekly`, `monthly`) that's missed because the machine was asleep **coalesces into a single firing
at wake** — you don't get N catch-up firings for N missed days, you get one. An `interval:`-based
schedule's firings **during** sleep are simply lost, not queued or coalesced. Consequence: "next
run" on the dashboard is explicitly best-effort — it's computed from the schedule, not from any
promise the machine will actually be awake at that instant. A loop whose staleness matters (a
watchdog you actually rely on) should have a cadence tolerant of this, not one that assumes
perfect punctuality.

## 6. Token/cost discipline

There is no new mechanism here beyond what's already described above — cost control is a design
property, not a separate system:

- **Precheck gating is the primary lever.** `type=agent`: an empty precheck (nothing worth
  reporting) means the engine is never invoked at all — `skipped-precheck`, amber, zero tokens.
  `type=watchdog`: a healthy probe means the engine is never invoked — silent-green, zero tokens,
  every single firing, forever, unless something actually breaks. A watchdog on a healthy target
  costs nothing beyond a curl call and a sqlite insert.
- **The script→agent pattern.** `precheck.sh` does deterministic gathering (grep, curl, `git
  status`, file stats — anything that doesn't need judgment) for effectively zero tokens; the
  engine is only ever asked to *interpret* already-gathered, capped, redacted output. Writing a
  loop's `precheck.sh` to do as much of the real work as possible, and its `prompt.md` to do as
  little re-discovery as possible, is the single highest-leverage cost decision you make when
  authoring a loop.
- **Per-loop `model=`.** Leave it blank for the engine default, or pin something cheaper/faster
  for a loop whose job is genuinely simple interpretation (see `examples/hello-loop/SPEC.md` §11
  for the reasoning behind picking — or not picking — a specific model tier).
- **Spend is visible, not just controlled.** Every run's token usage (nullable — codex has no cost
  field, only tokens; claude has both) lands in sqlite and surfaces as 7-day spend on the
  dashboard, per loop. If a loop is expensive, that shows up somewhere you'll actually see it.
- **Fresh session per firing, always.** No loop, ever, uses a resume/continuation flag. This is
  partly a cost property (a resumed session's context grows every firing, which is not a cost
  curve you want on an unattended recurring job) and partly an auditability one (§1) — v1 has no
  `SESSION_HINT` mechanism and doesn't need one, because cross-run memory is the mechanical
  findings/PRIOR-FINDINGS system, not model memory.

## 7. Build process walkthrough

```
1. Spec        — walk the eleven-question intake interview (§2) with generalissimo (or self-interview).
2. Scaffold     — `loopctl new <name> --type agent|watchdog --engine codex|claude`
                   (add `--from examples` if this is a pilot/regression fixture, not a real loop).
                   (or: `loopctl import <skill-path> --analyze / --apply` — see
                   `docs/SKILL_IMPORT.md` — when converting an existing Agent Skill)
3. Fill         — loop.conf, precheck.sh, prompt.md, dashboard.json, SPEC.md. No [FILL:] left.
4. Validate     — `loopctl validate <name> [--from examples]` — exit 0 required before anything else.
5. Supervised run — `loopctl run <name> [--from examples]` (foreground, streams progress). Read
                   the resulting report AND the dashboard rendering side-by-side against ground
                   truth — does the headline match what actually happened? Do the findings have
                   the ids you expect? Contract *compliance* is verified by tooling (validate +
                   schema enforcement), but compliance isn't the same as being RIGHT — that's what
                   this step is for.
6. Install      — `loopctl install <name>` (real loops only — never `examples/`). Generates the
                   plist, `launchctl bootstrap`s it, then `launchctl kickstart`s it and verifies a
                   fresh, non-failed run row actually appeared before declaring success. Refuses
                   `schedule=manual` and refuses a loop that fails validate. Env/auth issues (e.g.
                   codex/claude credentials under launchd's minimal environment) only ever surface
                   in this real launchd context — that's the entire reason this step exists rather
                   than stopping at "the plist bootstrapped OK."
```

**`loopctl new` scaffolding, exactly** (verified by running it in a throwaway root):

```
$ bin/loopctl new my-loop --root <tmp> --type agent --engine codex
scaffolded my-loop at <tmp>/loops.d/my-loop
```

produces `loop.conf`, `prompt.md`, `SPEC.md`, `dashboard.json` (`{"panels": []}`), and an
executable `precheck.sh` — every text field seeded with either a real default (permission axes
commented out at their defaults) or a `TODO`/`[FILL: ...]` marker. `prompt.md` is pre-seeded with
a `## Finding identity` heading (so the heading-presence check already passes out of the box), but
its content is a `[FILL: ...]` hint you must replace with your loop's actual derivation rule.
Validated straight off the scaffold (engine adapter present, i.e. against a real repo root),
`loopctl validate` fails on exactly one thing: **`SPEC.md` still contains `[FILL:` placeholders**
— fill in all eleven sections (§2) and it passes.

### The disposition workflow — the human side of the loop

This is the "repeat" arrow from §2's honest v1 mapping, made concrete:

```
$ bin/loopctl findings hello-loop --from examples
finding_id      severity  age  times_seen  disposition
--------------  --------  ---  ----------  -----------
alpha:has-todo  warn      0    1           open
beta:has-todo   info      0    1           open

$ bin/loopctl dismiss hello-loop alpha:has-todo --note "known, tracked elsewhere"
dismiss hello-loop alpha:has-todo

$ bin/loopctl findings hello-loop --from examples
finding_id      severity  age  times_seen  disposition
--------------  --------  ---  ----------  -------------------------------------------------
alpha:has-todo  warn      0    1           dismissed 2026-07-21 ("known, tracked elsewhere")
beta:has-todo   info      0    1           open
```

`ack` (acknowledge without hiding it), `dismiss` (note **required** — it's the audit trail),
`snooze --until YYYY-MM-DD` (until **required**), and `reopen` (clears a prior dismiss/snooze) are
all thin wrappers over the same append-only `dispositions` table, plus a dashboard regen so the
change is visible immediately. The dashboard itself stays static HTML — dispositions only ever
enter through this CLI (Amendment 1, Change 4, Option A).

## 8. Worked example — `hello-loop`, start to finish

`examples/hello-loop` (type=agent) is the reference this whole document is checked against.

**1. Spec (§2 answered in full):** see `examples/hello-loop/SPEC.md`. Purpose: scan a fixture
`world/` directory for files with an open `TODO:` marker. Agentic pattern: Human-in-the-loop
outer shape, trivial single-shot interpretation inside (precheck already did the work).
Finding identity: `<filename-without-extension>:has-todo`.

**2. Scaffold:** `loopctl new hello-loop --from examples --type agent --engine codex` (already
done — this is what produced the initial `loop.conf`/`prompt.md`/`SPEC.md`/`dashboard.json`/
`precheck.sh` skeleton, subsequently filled in).

**3. Fill:**
- `world/alpha.md`, `world/beta.md`, `world/gamma.md` — the fixture "world," two files with a
  `TODO:` line, one without.
- `precheck.sh` — greps `world/*.md` for `^TODO:`, prints a per-file summary plus
  `world files: N` / `world.todo_files: N` totals. Zero network, deterministic, always non-empty.
- `prompt.md` — interprets that precheck output, emits one finding per TODO file
  (`## Finding identity` documents the `<subject>:has-todo` rule), sets `status=warn` when some
  (not all) files have an open TODO, and emits `world.files` / `world.todo_files` as numeric
  `metrics`.
- `dashboard.json` — a `number` panel on `world.todo_files` (thresholds warn 1 / alert 3) and a
  `trend` panel on `world.files` (30-day window).

**4. Validate:**
```
$ bin/loopctl validate hello-loop --from examples
OK hello-loop
```

**5. Supervised run** (against the shipped fixture — a real `loopctl run` invokes the real codex
adapter; the transcript below is from the FAKE-engine hermetic path `tests/test_examples.sh`
automates, which exercises the exact same runner logic without spending tokens):
```
$ bin/loopctl findings hello-loop --from examples
finding_id      severity  age  times_seen  disposition
--------------  --------  ---  ----------  -----------
alpha:has-todo  warn      0    1           open
beta:has-todo   info      0    1           open
```
This matches ground truth: `world/alpha.md` and `world/beta.md` both have a `TODO:` line,
`world/gamma.md` doesn't — 2 findings, `status=warn`, `world.files=3`, `world.todo_files=2`,
exactly as `SPEC.md` §9/§10 said they should be.

**6. Install:** never, for an example — `examples/` loops stay pilot/regression fixtures forever
(docs/INTERFACES.md §1). A real loop built the same way would run `loopctl install hello-loop`
here instead.

**Resolution lifecycle, tried by hand** (see `examples/hello-loop/README.md` for the full
transcript): edit `world/alpha.md` to remove its `TODO:` line (or delete the file), re-run — the
`alpha:has-todo` finding stops appearing in the emission, and the runner sets its sqlite row's
`resolved_at`. Restore the file, run again — the *same* `finding_id` reappears, `resolved_at`
clears, and `times_seen` keeps counting from where it left off. This is the whole point of §1's
findings-upsert step made tangible: identity survives across a finding's absence and return.

**Suppression, exercised automatically:** `tests/test_examples.sh` runs `hello-loop` twice against
an identical canned contract (proving idempotence: same `finding_id`s, `times_seen` reaches 2, no
duplicate rows), dismisses `alpha:has-todo` via `loopctl dismiss … --root <hermetic-root>`, then
runs a third time and asserts the promoted `latest.json` omits `alpha:has-todo` while
`state/runs/<id>/contract.json` (the audit copy) still has it verbatim — proof that suppression is
enforced by the runner, not by the model choosing to stop mentioning it. `examples/hello-watchdog`
gets the identical treatment, with the added wrinkle that a watchdog's `effective_status` stays
`alert` even after its one finding is dismissed, because the *probe* is still failing (§4.3
stickiness) — suppression hides the finding, never the fact that something is actually down.
