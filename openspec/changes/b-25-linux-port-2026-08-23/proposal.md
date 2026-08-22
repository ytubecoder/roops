# B-25 — Linux port: app/loop separation, host requirements, probes, firstparty cutover

## Why

The fleet must leave the `llm` Mac mini for the `firstparty` Debian guest: llm gets its RAM
back, and systemd user timers with linger survive a reboot, which launchd bootstraps do not
(7–10 Aug outage). B-24 proved the mechanism on the guest; the cutover stalled because the
fleet is not host-portable loop by loop and `loops.sqlite` has no host dimension
(`support-migration-refactor.md`). This change makes the **app** host-neutral, makes each
**loop** declare what it needs from a host, and gives loops that need llm-local data a
read-mostly probe channel (sshd + one forced command on llm — zero resident RAM), so the fleet
can move as one unit with one db and one console.

## What Changes

- `$LOOPS_ROOT/.env` becomes a real seam: loaded by the runner with the strict `loop.conf`
  grammar (today nothing reads it — scheduled ads runs on the guest would silently lose `GC_BASE`).
- `loop.conf` gains `requires=` (`os:`/`bin:`/`file:`/`env:`/`probe:` items); `validate` notices,
  `install` refuses, `run` records `precheck-failed` without spawning an engine; new
  `loopctl requirements` answers "can this fleet run here".
- Probe channel: `probes/<name>` scripts (reviewed, trusted), `bin/probe-server` as the
  `restrict,command=` forced command on the data host, `bin/probe` client with local mode (macOS
  keeps working unchanged). Eight probes; one bounded write (`ticket-add`).
- Dashboard/console install-state detection becomes backend-aware (today: plist presence only —
  on Linux every loop renders 休 and the rounds switch 409s); `loopctl console install|uninstall|
  status` replaces the hand-written console plist; install checks linger, timezone, XDG on systemd.
- Retrofits: `kagi-ban` (subject stays llm via `av-scan`), `ads-x` (ledger + lock signal via
  probes), `gc-actions` (tree probes + `ticket-add`), `tailnet-zones` (path fix + probe),
  `kagami`/ads loops declare requirements.
- `loopctl snapshot`/`restore` for the cold state copy; `workflows/firstparty-cutover.txt`;
  guest staged and a full rehearsal run against a copy of real state. The final flip waits for
  the operator's answers (credentials, tailnet name, DNS).

Out of scope: dead-man alarm, a loops-client daemon, flickki loops, moving gc/OpenTwins/tickets.

## Capabilities

### New
- `host-requirements`: a loop declares host needs; the harness enforces them per host.
- `probe-channel`: named, reviewed probes executed on a data host through a forced ssh command,
  with a local mode.
- `state-snapshot`: consistent cold copy/restore of fleet state.

### Modified
- `install-backend` (B-24): install-state detection, console service, host checks.

## Impact

`bin/loopconf.py`, `bin/loopctl`, `bin/run-loop.sh`, `bin/console.py`, `dashboard/generate.py`,
new `bin/probe`, `bin/probe-server`, `probes/`, five `loops.d/*` prechecks, tests, `docs/INTERFACES.md`
(§1, §4.1, §5, §8, §8.1, §10, §13, new §14), `docs/LOOP_AUTHORING.md`, README, CLAUDE.md,
`skills/loops/SKILL.md`. Design: `design.md` in this directory.
