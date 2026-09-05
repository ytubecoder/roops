# loop-sensei — intake spec

1. Purpose & stop condition
The fleet's examiner (先生 — the honorific covers both teacher and physician,
which is the role): when another loop's latest run FAILED at the harness level,
diagnose why from the run's own artifacts and propose a fix — as a finding, so
the diagnosis flows through the same stamp/disposition arrow as everything
else. Per-firing stop condition: every loop whose latest run failed has one
finding carrying a Cause/Fix/Evidence diagnosis; a healthy fleet means the
engine is never invoked at all (empty precheck → skipped-precheck, zero
tokens — the ma (間) of the roops garden: silence is the healthy state).
Cross-run stop condition: a finding resolves (`resolved_at` set by the runner)
when that loop's latest run stops being a failure — i.e. the loop recovered or
was fixed; loop-sensei never counts or tracks this itself.

2. Agentic pattern
Outer shape Human-in-the-loop (mandatory v1): loop-sensei proposes diagnoses;
generalissimo dispositions them or pastes the proposed fix into an agent. It
never applies a fix, retries a failed loop, or iterates across firings. Inside
the single invocation: plain interpretation — the precheck gathered all
evidence; the engine classifies cause and formulates the fix. V2 aspiration,
recorded honestly: a bridge where an APPROVED diagnosis becomes an executable
order on the audited dry-run→execute→confirm path (the approve→action thread,
docs/OPEN_THREADS.md §1) — explicitly not built here.

3. Type & data flow (precheck gathers vs engine interprets)
type=agent. precheck.sh deterministically (read-only, zero network, zero
judgment): enumerates loops.d/* (excluding itself), queries sqlite for each
loop's latest run, selects the failures — runner_status in the seven §4.3
failure statuses, plus the `died` pseudo-class (finished_at NULL past
timeout_s+120s, same rule as the dashboard) — and prints one evidence block
per failing loop: run row fields, last-6 runner_status history, capped tails
(40 lines / 4 KiB) of engine.status, engine.log, precheck.out from the run
dir. It also precomputes each block's `finding_id: <loop>:<class>` and the
metrics JSON, both of which the engine must copy verbatim (the
"model-emitted metrics get believed" lesson, applied to identity too).
Capped at 8 detailed blocks; overflow loops are named, never hidden. The
engine interprets: root cause, proposed fix, severity — judgment only.

4. Cadence
daily:20:00 local — after the ads stagger (18:00–19:00) so it examines today's
firings while they're fresh. Staleness expectation 24h; launchd coalescing of
missed calendar firings to one-at-wake is fine for a daily examiner. Not
installed yet (supervised runs only, consistent with the whole fleet as of
2026-07-29).

5. Scope & exclusions
In scope: every loop under loops.d/ EXCEPT itself, judged solely on its latest
run's harness-level outcome. Excluded: examples/ (regression fixtures);
loop-sensei itself (at examination time its own latest run row is the
in-flight run — a prior failure would be masked; its failures stay visible on
the dashboard like any loop's); content-level judgments (a loop that ran
cleanly but reported alert findings is doing its job — that is that loop's
business, not sensei's); skipped-precheck / skipped-overlap / in-flight runs
(not failures); anything outside $LOOPS_ROOT — it reads no other project,
ever. maguyva hard-exclude: n/a, no external tool is ever invoked.

6. Guardrails
- "Everything is report/propose-only. No component ever commits, pushes, or
  mutates a project outside `$LOOPS_ROOT`." (docs/INTERFACES.md §0) — sensei
  proposes fixes; it never applies one.
- "Harness internals are a frozen contract — conform or amend it explicitly,
  never drift" (CLAUDE.md) — embedded in prompt.md: a proposed fix that would
  touch bin/, engines/, or dashboard/ must say so explicitly.
- "Fresh engine session per firing" (§0) — adapter-enforced, as for every loop.
- All of the above are embedded in prompt.md AND enforced independently by
  the permission axes — never by prompt text alone.

7. Permission axes + justification
The full report-only floor: perm_fs_write=report_only, perm_network=none,
perm_local_exec=none, perm_remote_mutation=none. The engine needs nothing —
it interprets an injected digest; even the reading was done by precheck.sh,
which as a plain runner-invoked script is not governed by these axes (it
reads sqlite read-only-mode and run-dir files, all local). No axis raised, no
dangerous combo approached. The runner's redaction pass (§4.4) covers the
precheck output, and tails are kept short as belt-and-braces against leaking
another loop's captured secrets into this loop's prompt.

8. Finding identity (what a finding IS + finding_id derivation rule)
A finding is "loop X's latest run is currently failing with class Y."
finding_id = `<loop_name>:<class>` where class = the failed run's
runner_status (precheck-failed | engine-failed | engine-timeout | auth-failed
| tool-denied | contract-violation | harness-error) or the pseudo-class
`died`. Precomputed by precheck.sh and copied verbatim by the engine — the
model never derives identity. No run id, timestamp, count, or diagnosis text
embedded: the same loop failing the same way tomorrow is the same finding
(times_seen increments); a different class is a different finding; the
diagnosis in detail/report may evolve without changing identity. Source of
truth: prompt.md `## Finding identity`.

9. Tier-1 semantics (ok/warn/alert meaning)
`ok` — unreachable by design: a healthy fleet produces an empty precheck, so
the engine never runs and the run records skipped-precheck (amber on the
dashboard; that amber-when-healthy is the documented harness semantic for
"nothing to interpret", accepted deliberately over spending tokens daily to
say "fine"). `warn` — every failing loop is an operational failure class
(engine-failed / engine-timeout / precheck-failed). `alert` — at least one
failure is harness-class (auth-failed, tool-denied, contract-violation,
harness-error, died). Because findings are non-empty whenever the engine
runs, effective_status is derived from finding severities and the declared
status is redundant (§4.5) — prompt.md instructs the engine to set it
consistently anyway.

10. Tier-2 metrics + panels
Metrics (precomputed by precheck, copied verbatim as the metrics string):
`fleet.loops_checked` (loops with ≥1 run examined), `fleet.failing` (loops
whose latest run failed), `fleet.died` (of those, died pseudo-class).
dashboard.json: number panel on fleet.failing (higher_is_worse, warn 1 /
alert 3, missing gap — a skipped run means "nothing failing", not a data
hole to carry forward); trend on fleet.failing (30d, gap); number on
fleet.died (higher_is_worse, warn 1 / alert 1 — any died run is a harness
problem); number on fleet.loops_checked (neutral, hold). Panel colour is
cosmetic (§3.3); status comes from findings.

11. Engine/model + budget
engine=codex (default; nothing here needs claude — no file writes, pure
interpretation at the floor, which codex maps to a read-only sandbox).
model= (engine default). Expected tokens/run: ZERO on a healthy fleet
(skipped-precheck); when firing, digest is a few KiB (≤8 capped evidence
blocks) → low thousands input, high hundreds output for the diagnoses.
retry_transient=1 (default). timeout_s=300 — interpretation only; the 900s
default would just pad a hang.
