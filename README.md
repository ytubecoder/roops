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
                            ▲       your script, then a model             │
                            │       that can't act on its own             ▼
                            │                                    ┌─────────────────┐
                            └──── ack · dismiss · snooze ────────│   you decide    │
                                       · reopen                  └─────────────────┘
```

> Scheduled agents that watch your machine and shut up when told.

A thin harness for putting work you already do by hand onto a schedule. You describe the job in plain markdown, `launchd` fires it, a deterministic script of yours gathers the facts, a headless model interprets them, and what comes back is a **contract** — findings, metrics, and a report that land on a static dashboard.

Capability is **per-loop and opt-in**. The default floor gives the model no filesystem write, no network, and no tools at all, so a loop you leave running unattended can't surprise you. Widening any axis is a config change with a written justification — never something the model can grant itself.

Findings keep the same ID across runs, so the same problem is the same row tomorrow. When you dismiss one, the **runner** stops showing it — the model is never trusted to remember it dropped the subject.

**Status: harness built and live-verified (2026-07-22).** Real launchd firing, both engines, findings memory and dispositions, enforcement denial, and the full dashboard state matrix have all been proven on this machine. Eight loops are defined and every one of them sits at or near the report-only floor; `loop-sensei` — the fleet examiner that diagnoses failed loops — is the first installed to launchd, the rest run supervised-only.

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
| **2. Precheck** | `precheck.sh` — your own bash, run unsandboxed — gathers the facts and bails cheaply when inputs are absent. No point paying for a model to discover a file is missing. | Exit 0 to proceed |
| **3. Inject** | Prior findings and run context are composed into the prompt, so the model knows what it already told you. | — |
| **4. Run** | The engine runs headless under the loop's four permission axes. Whatever the model can and can't do is a property of the sandbox, not the prompt. | Engine-level denial |
| **5. Validate** | Output must satisfy the contract in `docs/INTERFACES.md`. Malformed output fails the run rather than silently reporting nothing. | Schema + finding-identity check |
| **6. Promote** | Findings upsert by stable ID, dispositions apply, reports promote atomically, dashboard regenerates, old runs retire. | Suppression filter |

**Stage change** = the runner's job. **Disposition change** = yours, via `loopctl`. The model never does either.

## What Can Actually Change Things

A loop has three lanes with very different power, and it's worth knowing which is which before you write one.

| Lane | Sandboxed? | In practice |
|---|---|---|
| **`precheck.sh`** | **No.** Plain bash, `exec`'d directly by the runner under a timeout | Where network and file I/O belong. Trusted code *you* wrote, at full user privilege |
| **The engine** | **Yes**, by four permission axes | Default floor `report_only / none / none / none`: codex gets `-s read-only`, claude gets `--tools ""` |
| **Findings → you** | n/a | `ack` · `dismiss` · `snooze` · `reopen` via `loopctl`. The model never sets a disposition |

So the invariant is not "loops don't change things." It's **deterministic code you wrote gets full power; the model gets a sandbox.** `loops.d/ads-google/precheck.sh` curls four HTTP endpoints on every run — that's the design, not a leak. What's contained is the part you didn't write line by line.

### The four axes

Set per loop in `loop.conf`; semantics in [`docs/INTERFACES.md`](docs/INTERFACES.md) §5.2.

| Axis | Values | Default |
|---|---|---|
| `perm_fs_write` | `none` · `report_only` · `workdir` | `report_only` (its own run dir only) |
| `perm_network` | `none` · `full` | `none` |
| `perm_local_exec` | `none` · `allowlist` · `full` | `none` |
| `perm_remote_mutation` | `none` · `allowlist` | `none` |

A write-capable loop — one that fixes a bug, cleans up code, updates a database — is therefore a **config change, not a harness change**. `loopctl validate` hard-fails seven dangerous combinations rather than warning about them, so widening is deliberate and reviewed.

Four things to know before you widen:

- **`exec_allowlist` is intent, not a boundary.** A non-allowlisted `echo` still ran under a one-entry allowlist (probed 2026-07-28). Real containment is the write sandbox, the exposed tool set, and `perm_network=none`.
- **`perm_fs_write=workdir` is spec'd and adapter-mapped but not live-verified** — no loop in the current fleet uses it.
- **`credential_env` is reserved and not implemented**; `validate` hard-fails a non-empty value. A loop needing secrets reads them in the precheck, from the plist's `EnvironmentVariables`.
- **`perm_remote_mutation != none` requires a written `remote_mutation_justification`** — the harness refuses to let you spend money or push anywhere anonymously.

### What holds regardless of the axes

| Layer | What it guarantees |
|-------|-------------------|
| **Contract validation** | Output that doesn't match the schema fails the run. No half-parsed findings, and no promotion — a failed run leaves the previous `latest.*` untouched. |
| **Stable finding IDs** | The same issue is the same row across runs, accumulating a history instead of re-arriving as new noise. |
| **Runner-side suppression** | Dismissed and snoozed findings are filtered by the runner before you see them. The model still emits them; an audit copy stays in `state/runs/<id>/contract.json`. |

That last row matters most in practice. "Stop nagging me about this" is enforced by code, not by trusting a fresh session to honor a note in its prompt.

## What Lands on the Dashboard

`dashboard/loops.html` is a single static self-contained file (inline CSS/JS, no network, opened as `file://`), rewritten via tmp-file + rename after every run. Since 2026-07-30 it renders in the roops garden style — hanko status stamps (済 ok · 注 warn · 警 alert · 未 no data) over the same precedence rules, a per-loop tokonoma alcove on each fleet row, and a 巡/休/手 column showing whether a schedule is actually loaded. A loop feeds it through four channels:

| Channel | Contract field | Rendered as |
|---|---|---|
| **Status + headline** | `status`, `headline` | Precedence-resolved stamp and the tokonoma's first line on the fleet row |
| **Report** | `report_markdown` | Promoted to `reports/<name>/latest.md`, linked from the row |
| **Metrics** | `metrics` | Panels declared in the loop's `dashboard.json` — `number` · `table` · `list` · `trend` (sparkline over N days) |
| **Findings** | `findings` | Per-loop list with recurrence, a 巡 ×N chip for repeats, and disposition text — `3rd report · dismissed 2026-06-01 ("note")` |

Undeclared metrics are **never hidden** — they render in a capped raw-fallback panel, so a loop can start emitting something new without a dashboard change. The page also carries fleet counts, a `needs_attention` roll-up, 7-day token spend, stale-loop detection (installed loops only — a supervised-only loop shows 休 "no schedule loaded" instead), died-run detection, and a recent-runs table per loop.

One gotcha worth internalizing before you write a loop that wants to show red: **a non-empty `findings` array discards the declared `status`** and the light becomes the max severity of the unsuppressed findings (§4.5). A run that must surface red emits zero findings.

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

`ack` means *stop nagging* — it does **not** mean *the recommendation was accepted*. Approval is a separate concept and deliberately isn't overloaded onto this verb. Wiring an approved finding through to something that executes it is still an open design thread ([`docs/OPEN_THREADS_WARMSTART.md`](docs/OPEN_THREADS_WARMSTART.md) §1), so today the arrow stops at you.

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
| [`docs/ADS_LOOPS_FOLLOWUP_WARMSTART.md`](docs/ADS_LOOPS_FOLLOWUP_WARMSTART.md) | The five ads loops: current state, open issues with re-check commands, acceptance bar |
| [`CLAUDE.md`](CLAUDE.md) | Cold-start pointers + non-negotiables for agents |

## Notes

- **macOS today, WSL later.** All paths are `$HOME`-relative. macOS has no `flock` and no GNU `timeout`, so the harness uses an fcntl lock helper and runner-owned process-group timeouts.
- **Every firing is a fresh session.** No conversation carries over; prior findings are re-injected explicitly. That's what makes suppression trustworthy.
- **Per-run tokens and cost** land in SQLite; the dashboard shows a 7-day spend roll-up.

**Compatible with:** [Codex CLI](https://github.com/openai/codex) · [Claude Code](https://claude.ai/code)
