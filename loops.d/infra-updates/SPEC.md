# infra-updates — intake spec

Built 2026-11-11 from the sysadmin session that patched the estate two days
running and found (a) Plex had to be a manual deb, (b) the operator wants new
third-party releases to age before they are installed, (c) nobody wants to
re-run a ten-host sweep by hand to find out what is pending.

1. Purpose & stop condition
Catch everything pending across the in-scope estate (pve1 and its guests
firstparty / thirdparty / evilempire / CT ntp, the HAOS VM, the media box,
and llm itself) and grade it against the hold policy in
`~/projects/sysadmin/update-policy.md`: Debian items apply now, third-party
items wait until the upstream release is 7 days old, a CISA KEV hit overrides
the hold. Per-firing "done": the digest is turned into findings with
ready / wait / apply-now verdicts (tier-1 `warn` when something is ready,
`alert` when a KEV-named fix or an unattended-upgrades miss is pending, `ok`
when everything pending is still inside its hold). Cross-run "done": a finding
resolves when the item leaves the digest, i.e. the human applied it.

2. Agentic pattern
Human-in-the-loop, as every v1 loop: the loop proposes, Generalissimo applies
(by hand or by telling an agent) and `loopctl ack/dismiss/snooze` carries the
disposition. Inside one invocation it is a single-shot interpretation of an
already-computed digest. v2 aspiration, recorded only: an `apply` mode that
takes `ready` findings and runs the apt/compose/ha commands on a schedule.
Not built; the harness is one invocation per firing and report-only.

3. Type & data flow (precheck gathers vs engine interprets)
`type=agent`. `precheck.sh` does all the work that needs no judgement: ssh to
each Debian host (parallel), refresh apt lists where it may without a
password, `apt list --upgradable` + `apt-cache policy` for each candidate's
repo URL, running vs newest installed kernel, HAOS os/core/supervisor/add-on
versions through `qm guest exec 105`, thirdparty's running image tags, Plex and
Immich versions from their unauthenticated local endpoints, `brew outdated`
and `softwareupdate -l` on llm, then GitHub releases / plex.tv for upstream
release dates and the CISA KEV feed for the last 30 days. A Python block
classifies every item and prints a compact digest with a verdict per row
and a `## counts` line. It prints **nothing** when nothing is pending, so a
quiet estate never invokes the engine. The engine turns rows into findings
with the documented ids, picks severities, writes the human report and copies
the counts into metrics. It discovers nothing itself (no network, no exec).

4. Cadence
`daily:07:30` local. A day is the natural grain of apt-daily timers and of
the 7-day hold (ages advance once a day). Missed firings coalesce at wake;
staleness tolerance is a day or two with no consequence beyond a later
proposal.

5. Scope & exclusions
In scope: the seven hosts above and the components precheck.sh names (apt
packages, kernels, HAOS stack, thirdparty compose images, Plex, Immich, brew,
macOS, Tailscale.app). Excluded, by operator decision on 2026-11-10: the
Diskstation (DSM 7.1.1 is end-of-life, "no alternative", file sharing only),
the MikroTik router and the Windows desktop ("not in scope right now"). Also
excluded: applying anything (see §6), Docker images on media other than
Immich (none exist), the dev-tailnet userspace tailscaled agents on llm (same
brew formula as the CLI, covered by it). KEV matching includes Synology and
MikroTik names on purpose: an exploited-in-the-wild entry for an out-of-scope
box is still worth one line.

6. Guardrails
Verbatim from the harness: "Every loop is report/propose-only, mechanically,
not by prompt text." From the operator (2026-11-11): "lets pin so we dont get
the latest and greatest … sometimes there are attacks in the latest so its
nice to delay latest sometimes to let the dust settle - probably applicable to
everything in our stack." From `sysadmin/CLAUDE.md`: "Never put secrets in
this directory" — precheck.sh carries no credentials; media is probed without
sudo. From the sysadmin memory "Password reminder, not reset" and the
no-secrets rule: the loop never touches credentials. The precheck's only
remote writes are `apt-get update` list refreshes on hosts whose admin has
passwordless sudo; nothing is installed, restarted or rebooted.

7. Permission axes + justification
`perm_fs_write=report_only`, `perm_network=none`, `perm_local_exec=none`,
`perm_remote_mutation=none` — the floor. All I/O is in precheck.sh, which the
axes do not govern (it is a plain runner script). No dangerous combination is
approached: the engine has neither network nor exec. `requires="os:darwin,
bin:ssh, bin:python3, bin:curl"` because the probes rely on llm's ssh config
and keys.

8. Finding identity (what a finding IS + finding_id derivation rule)
A finding is one pending item on one host (or one standing condition: reboot
pending, rolling tag, stale lists, unreachable probe, KEV hit).
`finding_id` = `<host>:<component>:<condition>` with host and component taken
from the digest row (lower-case, spaces → `-`, parenthetical dropped) and a
fixed condition label per section: `pending`, `kernel:reboot-pending`,
`estate:kev:<cve>`, `<container>:rolling-tag`, `apt-lists:stale`,
`probe:unreachable`. Versions, dates, ages, counts and verdicts never enter
the id, so `media:plexmediaserver:pending` is one finding from the day a new
Plex appears until the day it is installed, whatever versions pass through.
Same rule in `prompt.md` § Finding identity.

9. Tier-1 semantics (ok/warn/alert meaning)
`ok`: things are pending but every one is still inside its hold window (or
only reboot/info items). `warn`: at least one item is `ready` or `apply-now`,
or a host's apt lists are stale. `alert`: a KEV entry names a product with a
pending fix, a debian-security item is still pending on a host that runs
unattended-upgrades, or a host is unreachable. A completely quiet estate never
reaches the engine (skipped-precheck, amber by harness convention).

10. Tier-2 metrics + panels
`updates.pending`, `updates.ready`, `updates.apply_now`, `updates.waiting`,
`updates.unknown_age`, `reboot.pending`, `kev.matches`, `hosts.unreachable`,
`apt.stale_lists` — all numeric, copied from the digest's `## counts` line.
`dashboard.json`: number panels for ready (warn 1 / alert 5), apply_now
(warn 1 / alert 3), waiting (neutral), kev.matches (alert at 1),
reboot.pending (neutral), hosts.unreachable (warn 1 / alert 2); a 30-day trend
on updates.pending.

11. Engine/model + budget
`engine=codex`, default model (interpretation of a <4 KiB digest needs no
frontier model). Expected a few thousand tokens per invoked run; zero on quiet
days. `retry_transient=1`. `timeout_s=600`: the precheck is capped at 300 s by
the runner and measured at ~90 s (six parallel ssh probes, `softwareupdate -l`
up to 120 s under its own timeout, ~12 small curls); the engine needs well
under the remainder. Digest well under the 64 KiB precheck cap.

12. Page output
None. The findings view plus `latest.md` carry everything; the sysadmin repo's
`update-policy.md` is the durable policy text.
