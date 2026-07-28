# loops

![Python 3](https://img.shields.io/badge/python-3-blue)
![Platform: macOS / launchd](https://img.shields.io/badge/platform-macOS%20%2F%20launchd-lightgrey)
![Tests: 593 passing](https://img.shields.io/badge/tests-593%20passing-brightgreen)
![Works with Codex CLI](https://img.shields.io/badge/works%20with-Codex%20CLI-orange)
![Works with Claude Code](https://img.shields.io/badge/works%20with-Claude%20Code-blueviolet)

```
                 ┌──────────────────────────────────────────────────┐
                 │  L O O P S                                       │
                 │  ═════════                                       │
   ⏰ 09:00 ─────▶│  precheck ─▶ engine ─▶ contract ─▶ findings      │─────┐
                 └──────────────────────────────────────────────────┘     │
                            ▲                                             │
                            │          it reports. it never acts.         ▼
                            │                                    ┌─────────────────┐
                            └──── ack · dismiss · snooze ────────│   you decide    │
                                       · reopen                  └─────────────────┘
```

> Scheduled agents that watch your machine and shut up when told.

A thin harness for recurring agent jobs. You describe a job in plain markdown, `launchd` fires it on a schedule, a headless model runs it, and what comes back is a list of **findings** — not a diff, not a commit, not an API call.

Every loop is report-only, and that's enforced at the engine's permission layer, not by asking the model nicely in a prompt.

Findings keep the same ID across runs, so the same problem is the same row tomorrow. When you dismiss one, the **runner** stops showing it — the model is never trusted to remember it dropped the subject.

**Status: harness built and live-verified (2026-07-22).** Real launchd firing, both engines, findings memory and dispositions, enforcement denial, and the full dashboard state matrix have all been proven on this machine. Seven loops are defined; none are installed yet.

## Install

### One-liner

```bash
git clone https://github.com/ytubecoder/loops.git ~/projects/loops && cd ~/projects/loops && bash tests/run-tests.sh
```

No installer and no dependencies — it's Python 3 and bash against the CLIs you already have. The test suite is hermetic (no network, no real engines); if it passes, the harness works.

### Or tell your agent

> Clone https://github.com/ytubecoder/loops to ~/projects/loops, run `bash tests/run-tests.sh` to confirm the harness is sound, then read `docs/LOOP_AUTHORING.md` and walk me through the intake interview to build my first loop.

### After install

1. `./bin/loopctl list` — see what's defined
2. `./bin/loopctl run hello-loop` — supervised foreground run, nothing scheduled
3. `./bin/loopctl new <name>` — scaffold your own via the intake interview
4. `./bin/loopctl dashboard` — regenerate `dashboard/loops.html`

Authoring guide: [`docs/LOOP_AUTHORING.md`](docs/LOOP_AUTHORING.md)

## How a Loop Runs

```
  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
  │ SCHEDULE │──▶│ PRECHECK │──▶│  ENGINE  │──▶│ CONTRACT │──▶│ FINDINGS │
  └──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
     launchd      cheap bail     codex/claude    validate      ack/dismiss
     fires        if inputs      report-only     or the run     snooze
                  are missing    permissions     is failed      reopen
```

| Step | What happens | Gate |
|------|-------------|------|
| **1. Fire** | `launchd` starts `bin/run-loop.sh <name>` on the loop's schedule. An fcntl lock means a slow run never overlaps its own next firing. | Loop `enabled` + not already running |
| **2. Precheck** | `precheck.sh` bails cheaply when inputs are absent — no point paying for a model to discover a file is missing. | Exit 0 to proceed |
| **3. Inject** | Prior findings and run context are composed into the prompt, so the model knows what it already told you. | — |
| **4. Run** | The engine runs headless under permission axes that deny writes. Report-only is a property of the sandbox, not the prompt. | Engine-level denial |
| **5. Validate** | Output must satisfy the contract in `docs/INTERFACES.md`. Malformed output fails the run rather than silently reporting nothing. | Schema + finding-identity check |
| **6. Promote** | Findings upsert by stable ID, dispositions apply, reports promote atomically, dashboard regenerates, old runs retire. | Suppression filter |

**Stage change** = the runner's job. **Disposition change** = yours, via `loopctl`. The model never does either.

## Report-Only, and Why It's Structural

The whole point is a job you can leave running unattended, which only works if it *cannot* surprise you.

| Layer | What it guarantees |
|-------|-------------------|
| **Permission axes** | The engine is launched without write capability. A model that decides to "just fix it" is denied by the sandbox. |
| **Contract validation** | Output that doesn't match the schema fails the run. No half-parsed findings. |
| **Stable finding IDs** | The same issue is the same row across runs — findings accumulate a history instead of re-arriving as new noise. |
| **Runner-side suppression** | Dismissed and snoozed findings are filtered by the runner, before you see them. The model still emits them; an audit copy stays in `state/runs/<id>/contract.json`. |

That last row is the one that matters most in practice. "Stop nagging me about this" is enforced by code, not by trusting a fresh session to honor a note in its prompt.

## Commands

```
./bin/loopctl new <name>        # scaffold — intake interview → SPEC.md + templates
./bin/loopctl validate <name>   # grammar + dangerous-combo checks + finding-identity check
./bin/loopctl run <name>        # supervised foreground run
./bin/loopctl list              # every loop: type, engine, schedule, enabled, installed
./bin/loopctl status <name>     # last run, headline, health
./bin/loopctl install <name>    # plist → bootstrap → kickstart → verified run row
./bin/loopctl uninstall <name>  # remove from launchd
./bin/loopctl pause <name>      # stop firing, keep the definition
./bin/loopctl resume <name>
./bin/loopctl dashboard         # regenerate dashboard/loops.html
```

### Findings

```
./bin/loopctl findings <name>              # open findings for a loop
./bin/loopctl ack <finding-id>             # seen, stop surfacing it
./bin/loopctl dismiss <finding-id> --note  # not a problem, and here's why
./bin/loopctl snooze <finding-id> --until  # not now, come back later
./bin/loopctl reopen <finding-id>          # it's back
```

`ack` means *stop nagging* — it does **not** mean *the recommendation was accepted*. Approval is a separate concept and deliberately isn't overloaded onto this verb.

## Engines

| Engine | Role |
|--------|------|
| `engines/codex.sh` | **Default.** `codex exec --output-last-message --output-schema` |
| `engines/claude.sh` | Switchable per loop via `loop.conf`. `claude -p --json-schema` |
| `engines/fake.sh` | Hermetic test stub — double-gated, never reachable in production |

Prompts are engine-neutral by design, so a loop isn't married to the model that happened to be cheapest the week it was written. Verified CLI behavior for both engines is recorded in [`docs/ENGINE_PROBES.md`](docs/ENGINE_PROBES.md) — that file is evidence, not documentation of intent.

## Layout

```
bin/run-loop.sh          # the runner: lock → precheck → inject → engine → validate → promote
bin/loopctl              # the CLI you actually use
engines/                 # codex · claude · fake
loops.d/<name>/          # loop.conf · precheck.sh · prompt.md · dashboard.json · SPEC.md
examples/                # hello-loop (daily agent) · hello-watchdog (15m interval)
state/loops.sqlite       # runs · heartbeats · metrics · findings · dispositions (WAL)
reports/<name>/          # per-run markdown + atomically-promoted latest.* (suppression-filtered)
dashboard/loops.html     # static: fleet view + per-loop panels + findings
tests/run-tests.sh       # 593 hermetic tests — no network, no real engines
```

## Docs

| File | Purpose |
|---|---|
| [`docs/LOOP_AUTHORING.md`](docs/LOOP_AUTHORING.md) | **Start here to build a loop** — intake interview, contract, worked example |
| [`docs/INTERFACES.md`](docs/INTERFACES.md) | Frozen mechanical contract every component implements |
| [`docs/HARNESS_PLAN.md`](docs/HARNESS_PLAN.md) | Finalized harness design (plan-checked 3× with codex) |
| [`docs/HARNESS_PLAN_AMENDMENT_1.md`](docs/HARNESS_PLAN_AMENDMENT_1.md) | Findings memory / human-in-the-loop amendment |
| [`docs/ENGINE_PROBES.md`](docs/ENGINE_PROBES.md) | Verified live CLI behavior of codex + claude |
| [`docs/LOOPS_WARMSTART.md`](docs/LOOPS_WARMSTART.md) | Loop candidate selection state |
| [`docs/OPEN_THREADS_WARMSTART.md`](docs/OPEN_THREADS_WARMSTART.md) | Unfinished design threads — read before assuming something is settled |
| [`CLAUDE.md`](CLAUDE.md) | Cold-start pointers + non-negotiables for agents |

## Notes

- **macOS today, WSL later.** All paths are `$HOME`-relative. macOS has no `flock` and no GNU `timeout`, so the harness uses an fcntl lock helper and runner-owned process-group timeouts.
- **Every firing is a fresh session.** No conversation carries over; prior findings are re-injected explicitly. That's what makes suppression trustworthy.
- **Per-run tokens and cost** land in SQLite; the dashboard shows a 7-day spend roll-up.

**Compatible with:** [Codex CLI](https://github.com/openai/codex) · [Claude Code](https://claude.ai/code)
