# loops

Recurring automated "loops" for the ~/projects portfolio: scheduled headless agent jobs (commit-hygiene sweeps, review-queue digests, uptime watchdogs → diagnosis, CI/dependency/security checks, …) run by a custom thin harness.

**Status: planning complete, implementation not started.**

## System Overview (planned)

```
launchd (scheduler)
  └─ bin/run-loop.sh <name>            # lock → precheck → engine → validate contract → promote → dashboard
       ├─ engines/codex.sh             # DEFAULT: codex exec --output-last-message --output-schema
       ├─ engines/claude.sh            # claude -p --output-format json --json-schema
       ├─ loops.d/<name>/              # loop.conf · precheck.sh · prompt.md · dashboard.json
       ├─ state/loops.sqlite           # runs + heartbeats (WAL)
       ├─ reports/<name>/…             # per-run markdown + atomically-promoted latest.*
       └─ dashboard/loops.html         # static: global fleet view + per-loop panels
```

## Docs

| File | Purpose |
|---|---|
| `docs/HARNESS_PLAN.md` | Finalized harness design + implementation steps (plan-checked 3× with codex) |
| `docs/LOOPS_WARMSTART.md` | Loop candidate selection: 13-row table, evidence, guardrails |
| `CLAUDE.md` | Cold-start pointers + non-negotiables for agents |

Repo `git init`, harness code, `loopctl`, and `docs/LOOP_AUTHORING.md` arrive with the harness build (step 2+ of the plan).
