# Harness Plan — Amendment 1: close the human-in-the-loop arrow

> **Status: AUTHORIZED AMENDMENT to `HARNESS_PLAN.md` REVISION 9.** Raised and approved by generalissimo, 2026-07-22, in the loop-selection context.
>
> `CLAUDE.md` tells you not to relitigate decisions settled in `HARNESS_PLAN.md`. That still holds. This is not relitigation — it is generalissimo reopening one specific gap on purpose. Everything not named in "Unchanged" below stays exactly as REVISION 9 specifies.
>
> **Timing: this lands before `bin/run-loop.sh`, `contract/contract.schema.json`, and the sqlite schema are written.** At the time of writing, the repo has `git init` + empty skeleton dirs and no code. Because nothing exists yet, this is **not a migration** — it is the initial schema. Contract stays `schema_version: 1`.

## Why

generalissimo's working definition of a loop (the one driving loop selection): **a loop needs repetition and a stop condition.** Judged against that, REVISION 9's design is a scheduled one-shot, not a loop:

- Per launchd firing: lock → precheck → **one** engine invocation → validate → promote → exit.
- Repetition is supplied entirely by launchd. There is no goal and no stop condition.
- More importantly, **each run is blind to every run before it.** `state/loops.sqlite` records runs, but `prompt.md` is static and no prior state is injected.

The consequence is concrete and predictable. Every loop is `report/propose-only`, i.e. the human-in-the-loop pattern *AI proposes → human approves → repeat* — but the **repeat** arrow does not exist. Run #12 of the security sweep re-reports, in identical words and with identical confidence, the finding generalissimo dismissed on runs #1–#11. That is the mechanism by which the weekly/monthly advisory loops become noise generalissimo stops opening, which would take the whole fleet with them.

This amendment adds the missing arrow. That is all it does.

## Unchanged (do not widen scope)

Explicitly still in force, and **not** what this amendment is about:

- **`report/propose-only` stays.** No auto-mutation of projects. Default permission axes unchanged (read-only, no-network, report-dir-write-only).
- **Type-2 / goal-based loops stay OUT of v1.** No iterate-until-success, no `max_iterations`, no self-verification cycle, no auto-fix. The runner remains **one engine invocation per firing**.
- Permission axes, real enforcement for remote-capable CLIs, `loopctl validate` dangerous-combo checks — unchanged.
- Runner-owned process-group timeouts, fcntl lock, atomic promotion of `latest.*`, per-run contract atomicity, status model and precedence, engine adapter interface, launchd install/kickstart verification — all unchanged.
- Tier-1/tier-2 reporting split and `dashboard.json` panel metadata — unchanged. `findings` (below) is a **new tier-1 field**, not a tier-2 metric.

## Change 1 — findings get stable identity (tier-1 contract)

The contract currently emits a `headline` and free-form `metrics`. Neither can be compared across runs. Add a tier-1 `findings` array:

```json
{
  "schema_version": 1,
  "run_id": "…",
  "status": "ok | warn | alert",
  "status_reason": "short machine-usable category",
  "headline": "3 repos unpushed, 1 has no remote",
  "report_markdown": "…",
  "metrics": { },
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

`findings` MAY be empty (a clean run). Rules:

- **`finding_id` must be deterministic and stable across runs** for the same real-world condition, and must NOT embed volatile data — no timestamps, no run ids, no commit counts, no line numbers that shift. Derive it from the durable identity of the thing: `<subject>:<condition>`.
- Identity is **loop-defined**. Each loop's `prompt.md` MUST document its `finding_id` derivation rule, and `docs/LOOP_AUTHORING.md` MUST require this in the spec template.
- The **engine emits identity only**. It does NOT compute recurrence, age, or "3rd time seen" — the runner derives all of that from sqlite. Never trust the model to count its own history.

## Change 2 — findings and dispositions persist (sqlite)

Alongside `runs` and `heartbeats`:

```sql
CREATE TABLE findings (
  finding_id     TEXT NOT NULL,
  loop_name      TEXT NOT NULL,
  title          TEXT NOT NULL,
  severity       TEXT NOT NULL,
  first_seen_run TEXT NOT NULL,
  first_seen_at  TEXT NOT NULL,
  last_seen_run  TEXT NOT NULL,
  last_seen_at   TEXT NOT NULL,
  times_seen     INTEGER NOT NULL DEFAULT 1,
  resolved_at    TEXT,               -- set when a run stops reporting it
  PRIMARY KEY (loop_name, finding_id)
);

CREATE TABLE dispositions (
  loop_name    TEXT NOT NULL,
  finding_id   TEXT NOT NULL,
  action       TEXT NOT NULL,        -- ack | dismiss | snooze | reopen
  note         TEXT,
  snooze_until TEXT,
  created_at   TEXT NOT NULL
);
```

Runner, on each valid run: upsert findings (increment `times_seen`, update `last_seen_*`), and mark previously-open findings absent from this run as `resolved_at = now`. A finding that reappears after resolution is a *new occurrence of a known id* — `times_seen` continues, `resolved_at` clears.

Dispositions are append-only history; current state is the latest row per `(loop_name, finding_id)`.

## Change 3 — prior state feeds the next run (runner prompt assembly)

`run-loop.sh` composes the engine prompt from `prompt.md` **plus** a runner-generated block, in the same way `PRECHECK OUTPUT` is already injected:

```
PRIOR FINDINGS (generated by the runner — authoritative; do not recompute)
  cookingapp:no-remote   seen 12× since 2026-05-04   DISMISSED 2026-06-01 ("intentional, local scratch repo")
  stuntsclone:unpushed   seen 3× since 2026-07-08    open
  claude-quality:no-remote  seen 9× since 2026-05-04  SNOOZED until 2026-09-01
```

Prompt contract for every loop:
- Re-emit a still-true finding with its **same `finding_id`** — do not invent a new id for a recurring condition.
- Do not re-argue a `DISMISSED` finding unless the underlying situation has **materially changed**; if it has, say what changed.
- `SNOOZED` findings: still emit them if true. Suppression is the runner's job, not the model's.

**Suppression is enforced at the runner, not by prompt.** Consistent with REVISION 9's principle that guarantees are mechanical: the runner filters dismissed and unexpired-snoozed findings out of the promoted report and the dashboard, regardless of what the engine emits. `status` is computed from *unsuppressed* findings only — a loop whose only remaining finding is dismissed goes green.

## Change 4 — an input channel (OPEN DECISION — generalissimo to settle with the architect)

Dispositions have to come from somewhere. The dashboard is specced as **static HTML** (`generate.py → loops.html`, atomic tmp→rename), which cannot accept input. Two options:

**Option A — CLI, dashboard stays static (recommended for v1).**
```
loopctl findings <loop>                       # list open findings + ids + age + times_seen
loopctl ack <loop> <finding_id> [--note …]
loopctl dismiss <loop> <finding_id> --note …  # note REQUIRED — it is the audit trail
loopctl snooze <loop> <finding_id> --until 2026-09-01
loopctl reopen <loop> <finding_id>
```
Dashboard renders recurrence and disposition as **text** ("3rd report · dismissed 2026-06-01"). Preserves every property three codex rounds hardened: static, atomic, no server, no moving parts, openable from anywhere. generalissimo acts in the terminal.

**Option B — dashboard accepts input.** Buttons writing dispositions; requires a local server or a writable sidecar the generator merges. Costs the static/atomic guarantee and adds a runtime component to a system whose selling point is that it has none.

**Recommendation: A for v1.** B stays possible later — the sqlite tables above are identical either way, so choosing A now forecloses nothing. generalissimo has flagged the dashboard as the part he cares about most, so this is his call, not the architect's.

## Downstream doc/verification updates

- **`docs/LOOP_AUTHORING.md`** (step 5): spec template gains a required *finding identity* section — what a finding is for this loop, and the `finding_id` derivation rule. Contract docs gain `findings`.
- **`loopctl validate`**: fail a loop whose `prompt.md` does not document a `finding_id` derivation rule.
- **Verification matrix** (plan §Verification) gains an idempotence check:
  > Run a loop twice against an unchanged world → **identical `finding_id`s**, `times_seen` increments, no duplicate `findings` rows, promoted report unchanged in substance.
  >
  > Dismiss a finding → next run's promoted report and dashboard omit it even though the engine still emits it (runner-side suppression proven, not prompt-side).
- **Pilot** (step 6): `hello-loop` emits ≥2 findings with stable ids so the recurrence path and one disposition are exercised as regression fixtures.

## What this amendment does NOT authorize

No auto-mutation. No iterate-until-stop. No widening of any permission axis. No change to engine adapters or the launchd path. If implementing the above appears to require any of those, stop and raise it with generalissimo rather than widening scope.
