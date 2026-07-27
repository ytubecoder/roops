# hello-loop — intake spec

1. Purpose & stop condition
Permanent pilot/regression fixture for the harness (docs/HARNESS_PLAN.md
step 6). Per-firing stop condition: report which fixture files under
`world/` currently have an open `TODO:` marker (tier-1 `status`/`findings`
for that single run). Cross-run stop condition: a given file's finding
(`<subject>:has-todo`) stops appearing once the file's TODO is resolved or
the file is deleted — the findings-memory lifecycle (Amendment 1) marks it
`resolved_at`, not the engine.

2. Agentic pattern
Outer shape is Human-in-the-loop (Amendment 1): the engine proposes findings
each firing, generalissimo reviews the dashboard and dispositions (ack/dismiss/snooze)
via `loopctl`, and the propose→dispose→re-propose cycle across firings IS
the approve/repeat arrow — there is no in-harness auto-fix. Inside the
single engine invocation the pattern is trivial interpretation (no
ReAct/Plan-then-Execute needed — precheck.sh already gathered everything
deterministically; the engine only classifies and formats). No v2
aspiration recorded: this loop is intentionally as simple as the contract
allows, since its job is to exercise the contract, not to explore agentic
depth.

3. Type & data flow (precheck gathers vs engine interprets)
type=agent. `precheck.sh` deterministically (zero network, zero LLM) scans
`world/*.md`, greps each for a line starting `TODO:`, and prints a compact
per-file summary plus two summary lines (`world files: N`,
`world.todo_files: N`). The engine never reads `world/` itself — it only
interprets the `PRECHECK OUTPUT` block the runner injects, classifies each
file as TODO/clean, and emits stable-id findings + the two numeric metrics.
This is the script->agent pattern (docs/INTERFACES.md §4.1/§6.2) in its
purest form.

4. Cadence
`daily:09:00` (local). Why: this is a pilot/regression fixture, not a real
production loop — daily is just a plausible, low-noise default for a loop
of this shape (staleness expectation ~24h) and matches the spirit of a
"morning digest" loop a real TODO-sweep would be. Examples are never
installed to launchd (docs/INTERFACES.md §1), so this schedule is
illustrative only; verification runs it manually / via `loopctl run`.

5. Scope & exclusions
In scope: the fixture files inside `examples/hello-loop/world/` only.
Explicitly excluded: anything outside this loop's own directory — no real
project repos, no `~/projects/*` scanning (that's a different, real loop
candidate in `docs/LOOPS_WARMSTART.md`, out of scope here). maguyva and any
other project-analysis tool are hard-excluded, consistent with the
project-wide guardrail; this loop never invokes an external tool at all.

6. Guardrails verbatim
- "Everything is report/propose-only. No component ever commits, pushes, or
  mutates a project outside `$LOOPS_ROOT`." (docs/INTERFACES.md §0) — this
  loop cannot mutate anything: `perm_fs_write=report_only`,
  `perm_network=none`, `perm_local_exec=none`, `perm_remote_mutation=none`.
- "Fresh engine session per firing" (§0) — no resume/session flags are used
  by either engine adapter; not loop-specific, enforced at the adapter
  layer for every loop including this one.
- These same two guardrails are embedded in `prompt.md`'s "Output contract"
  / "Findings prompt contract" sections AND enforced independently via the
  permission axes below — never by prompt text alone (docs/INTERFACES.md
  §0, §5.2).

7. Permission axes + justification
`perm_fs_write=report_only` — the engine never needs to write anywhere
except its own run/report artifacts; precheck.sh already did all the
reading. `perm_network=none` — no external calls are ever needed to
classify fixture text. `perm_local_exec=none` — the engine only interprets
already-gathered text, no reason to run shell commands. `perm_remote_mutation=none`
— nothing remote is ever touched. All four sit exactly at the fleet's
report-only floor; no dangerous-combo justification is needed because no
axis is raised above its default.

8. Finding identity (what a finding IS + finding_id derivation rule)
A finding is "this fixture file currently has an open TODO marker."
`finding_id` = `<subject>:has-todo`, where `<subject>` is the fixture file's
name without its extension (`world/alpha.md` → `alpha`). Deterministic and
stable across runs for the same file: no timestamp, no TODO line text, no
line number, no run id, no count is embedded. See `prompt.md`'s
`## Finding identity` section (verbatim source of truth) and README.md for
how to flip a finding by editing `world/`.

9. Tier-1 semantics (ok/warn/alert meaning)
`ok` — no scanned file has an open TODO (a fully clean world). `warn` — one
or more, but not all, scanned files have an open TODO (the common case with
the shipped fixture: 2 of 3). `alert` — every scanned file has an open TODO
(the fixture world has gone completely stale). This loop never needs a
"harness broke" alert of its own — those are runner_status concerns, not
loop_status ones.

10. Tier-2 metrics + panels
Metrics emitted (inside the `metrics` JSON string): `world.files` (total
`world/*.md` files scanned, numeric) and `world.todo_files` (count with an
open TODO, numeric). `dashboard.json` declares: a `number` panel on
`world.todo_files` (`higher_is_worse`, thresholds `warn:1, alert:3`, gap on
missing — panel colouring only, never overrides `loop_status`) and a
`trend` panel on `world.files` (`window_days:30`, hold on missing) so the
recurrence/regression-fixture nature of this loop is visible as a time
series across the repeated test runs that exercise it.

11. Engine/model + budget
`engine=codex`, `model=` (empty — engine default). Expected tokens/run: low
— the prompt is a few KB, the precheck output is a handful of lines, and
there is no tool use (~1-3k input tokens, well under codex's ~12.8k system
baseline plus payload per docs/ENGINE_PROBES.md; a few hundred output
tokens). `retry_transient=1` (default) — a pilot fixture has no urgency
that would justify raising it. `timeout_s=300` — generous relative to the
trivial interpretation task, capped well under the 900s default since there
is no real work to time out on.
