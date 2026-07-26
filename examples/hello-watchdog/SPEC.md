# hello-watchdog — intake spec

1. Purpose & stop condition
Permanent pilot/regression fixture (docs/HARNESS_PLAN.md step 6) exercising
the `type=watchdog` path end to end. Per-firing stop condition: is the
configured target (`target.txt`) reachable right now (tier-1 `status` for
this run — always sticky `alert` while the probe is failing, per §4.3)?
Cross-run stop condition: the `target:<condition>` finding stops being
emitted, and the runner marks it `resolved_at`, once the probe starts
succeeding again (silent-green — no engine invocation at all, so no
finding is even re-argued).

2. Agentic pattern
Outer shape is Human-in-the-loop, same as every v1 loop (Amendment 1): a
failing probe proposes a diagnosis, generalissimo reviews and dispositions it, and
recurring probe failures re-propose with the same stable finding id rather
than re-litigating. Inside the single diagnosis invocation (only fired on
escalation) the pattern is pure interpretation — classify a curl exit code
into one of a handful of failure buckets. No ReAct loop, no tool use, no v2
aspiration: a watchdog's precheck.sh already is the "verification" step: it
is the check, not a hint for the engine to re-check.

3. Type & data flow (precheck gathers vs engine interprets)
type=watchdog: `precheck.sh` **is** the job (docs/INTERFACES.md §4.1). It
reads the first non-comment line of `target.txt` as a URL and runs
`curl --max-time 5 --fail --silent --show-error` against it, deterministically
classifying success/failure by curl's own exit code (works uniformly across
`http(s)://` and `file://` schemes — no HTTP-specific parsing needed since
`--fail` already turns 4xx/5xx into a nonzero exit). Exit 0 → silent-green,
no engine invocation, just a heartbeat row. Exit nonzero → the runner
escalates, injecting the captured `PRECHECK OUTPUT` (target, curl_exit,
http_code, result) into the diagnosis engine's prompt; the engine only
interprets that already-gathered text into a `finding`/`headline`, it never
re-probes anything itself.

4. Cadence
`interval:15m`. Why: a watchdog's whole value is catching an outage sooner
than a human would notice, so a short fixed interval (not a once-daily
calendar slot) matches the "is this thing up" shape; staleness expectation
is therefore ~15 minutes (docs/INTERFACES.md §5.1) — an overdue heartbeat
past 1.5× that is what the dashboard's stale detection would flag for a
real installed watchdog. As with hello-loop, this schedule is illustrative
only: examples are never installed to launchd.

5. Scope & exclusions
In scope: exactly one target URL, read from this loop's own `target.txt`.
Explicitly excluded: multi-target probing, retries beyond the harness's own
`retry_transient` (that axis governs adapter-level transient failures, not
probe retries — a single curl attempt per firing is intentional, since
`interval:15m` itself is the retry cadence), and any real production
endpoint — this is a fixture loop, so `target.txt` defaults to a local,
zero-network `file://` path (see README.md for pointing it at something
real). maguyva / any external analysis tool: hard-excluded, same
project-wide guardrail as hello-loop; this loop only ever shells out to
`curl`.

6. Guardrails verbatim
- "Everything is report/propose-only. No component ever commits, pushes, or
  mutates a project outside `$LOOPS_ROOT`." (docs/INTERFACES.md §0) — curl
  is read-only (GET-equivalent) against the configured target; the
  diagnosis engine sits at the report-only permission floor and cannot
  mutate anything.
- "Fresh engine session per firing" (§0) — enforced at the adapter layer,
  applies to this loop's diagnosis invocations exactly as to every other
  loop's.
- Watchdog-specific guardrail (docs/INTERFACES.md §4.3): "if the probe
  failed, the run's loop_status AND effective_status are alert regardless
  of what the diagnosis engine returns and regardless of suppression" —
  embedded in `prompt.md`'s "Watchdog stickiness" section AND enforced
  mechanically by the runner, never trusted to the model.

7. Permission axes + justification
`perm_fs_write=report_only`, `perm_network=none`, `perm_local_exec=none`,
`perm_remote_mutation=none` — the fleet's report-only floor. The diagnosis
engine never needs network access itself (the probe already ran, outside
the engine, before the engine is ever invoked) and never needs to execute
commands (it interprets pre-captured text). All four axes sit at their
default; no dangerous-combo justification is needed.

8. Finding identity (what a finding IS + finding_id derivation rule)
A finding is "the configured target is currently failing its probe, in
this failure class." `finding_id` = `target:<condition>`, where `target` is
a fixed literal (this loop watches exactly one target, so the URL itself is
never part of the id — it's configuration, not identity) and `<condition>`
is one of a small fixed vocabulary (`unreachable`, `http-error`, `timeout`)
derived from the probe's curl exit code. See `prompt.md`'s
`## Finding identity` section (verbatim source of truth).

9. Tier-1 semantics (ok/warn/alert meaning)
`ok` — silent-green: the probe succeeded, no engine ever ran, heartbeat
`ok=1`. `alert` — the probe failed; per watchdog stickiness this is always
the stored status while the probe is down, regardless of the diagnosis
engine's own opinion or any disposition on the finding. This loop never
uses `warn` — a probe target is either up (silent-green `ok`) or the harness
treats it as needing attention (`alert`); there is no partial-credit state
for a single binary reachability check.

10. Tier-2 metrics + panels
None. This loop has no numeric trend metric worth tracking (a single
target's up/down state is already fully represented by tier-1
status/heartbeats) — `dashboard.json` declares `{"panels": []}` on purpose,
per docs/INTERFACES.md §9.3 ("dashboard.json may be absent ⇒ tier-1 row +
raw fallback only"; an explicit empty panel list documents that this was a
deliberate choice, not an oversight).

11. Engine/model + budget
`engine=codex`, `model=` (empty — engine default). Expected tokens/run: the
diagnosis engine only fires on escalation (rare for a healthy target), and
even then the prompt + injected precheck output is a few hundred tokens
plus the ~12.8k codex system-prompt baseline (docs/ENGINE_PROBES.md);
output is a short JSON diagnosis, a few hundred tokens. `retry_transient=1`
(default). `timeout_s=120` — short, since diagnosis is pure text
interpretation with no tool calls; well under the 900s default.
