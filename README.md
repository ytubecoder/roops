# loops

Recurring automated "loops" for the ~/projects portfolio: scheduled headless agent jobs
(commit-hygiene sweeps, review-queue digests, uptime watchdogs → diagnosis, CI/dependency/security
checks, …) run by a custom thin harness.

**Status: harness BUILT and live-verified (2026-07-22).** Real launchd firing, both engines,
findings memory + dispositions, enforcement denial, and the full dashboard state matrix have all
been proven on this machine (pilot evidence in the runs table). Loop selection is in progress in
`docs/LOOPS_WARMSTART.md`.

## System Overview

```
launchd (scheduler)
  └─ bin/run-loop.sh <name>            # lock → precheck → PRIOR FINDINGS + RUN CONTEXT injection
       │                               #   → engine → validate contract → findings upsert
       │                               #   → suppression → atomic promote → dashboard → retention
       ├─ engines/codex.sh             # DEFAULT: codex exec --output-last-message --output-schema
       ├─ engines/claude.sh            # claude -p --output-format json --json-schema
       ├─ engines/fake.sh              # hermetic test stub (double-gated, never in production)
       ├─ loops.d/<name>/              # loop.conf · precheck.sh · prompt.md · dashboard.json · SPEC.md
       ├─ state/loops.sqlite           # runs · heartbeats · metrics · findings · dispositions (WAL)
       ├─ reports/<name>/…             # per-run markdown + atomically-promoted latest.* (suppression-filtered)
       └─ dashboard/loops.html         # static: global fleet view + per-loop panels + findings/dispositions
```

Human-in-the-loop: loops are report/propose-only (enforced by permission axes at the engine
level); findings carry stable ids across runs; `loopctl findings/ack/dismiss/snooze/reopen` is
the approve/repeat arrow (dismissed findings are suppressed by the runner, never by the model).

## Daily driver

```
bin/loopctl new <name>        # scaffold (intake-driven SPEC.md + prompt/conf templates)
bin/loopctl validate <name>   # grammar + 7 dangerous-combo checks + finding-identity check
bin/loopctl run <name>        # supervised foreground run
bin/loopctl install <name>    # plist → bootstrap → kickstart → verified terminal run row
bin/loopctl findings <name>   # open findings; ack/dismiss/snooze/reopen to disposition
bin/loopctl dashboard         # regenerate dashboard/loops.html
bash tests/run-tests.sh       # full hermetic suite (no network, no real engines)
```

## Docs

| File | Purpose |
|---|---|
| `docs/LOOP_AUTHORING.md` | **Start here to build a loop** — intake interview, contract, worked example |
| `docs/INTERFACES.md` | Frozen mechanical contract every component implements |
| `docs/HARNESS_PLAN.md` | Finalized harness design (plan-checked 3× with codex) |
| `docs/HARNESS_PLAN_AMENDMENT_1.md` | Findings memory / human-in-the-loop amendment |
| `docs/ENGINE_PROBES.md` | Verified live CLI behavior of codex + claude (evidence for §7) |
| `docs/LOOPS_WARMSTART.md` | Loop candidate selection state |
| `CLAUDE.md` | Cold-start pointers + non-negotiables for agents |
