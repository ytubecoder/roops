# tailnet-zones — intake spec

1. Purpose & stop condition
The tailnet zone diagram (served on the owner's dev box, fronted by
tailscale serve → the dev-tailnet Caddy) used to be a hand-edited HTML file
that drifted from the actual Tailscale policy. This loop regenerates the page
from the policy itself on every firing and reports sync drift. Per-firing
"done": the model builder produced a page model from the freshest available
policy source, the engine re-emitted every precheck finding with its verbatim
id and copied the counts, and render.sh promoted the regenerated snapshot
page. Cross-run "done": a finding resolves when its condition stops being
true — the credential gets decided (`source:snapshot-fallback`), the repo
snapshot gets refreshed (`records:snapshot-stale`), the display metadata
learns a new actor (`policy:unmapped-actor:*`), a pin gets documented
(`policy:unannotated-pin:*`) — and the runner sets `resolved_at`.

2. Agentic pattern
Outer shape Human-in-the-loop (mandatory v1): the loop proposes drift findings;
generalissimo dispositions them via loopctl. Inside the single invocation:
plain interpretation — precheck gathered everything, the engine only writes the
tier-1 narrative. The page regeneration itself is not a mutation of anything
outside the harness: the page IS the loop's report page, promoted by the
runner; the web server routes the public URL at the promoted `latest.html`.
V2 aspirations, recorded honestly: (a) read the devices API (read-scoped) so
the inventory chips auto-sync too — today inventory comes from the
tailnet-setup repo's zones-meta.json; (b) no iterate-until-success anywhere.

3. Type & data flow (precheck gathers vs engine interprets)
type=agent. precheck.sh deterministically (trusted, unsandboxed): resolves the
policy source — if a read-only credential exists at
`~/.config/tailscale-policy-read.token` it GETs the live policy
(`/api/v2/tailnet/-/acl`, hujson accept, 30s cap); otherwise it copies the
tailnet-setup repo snapshot (`docs/policy-live.hujson`) — then runs
build_model.py: hujson→JSON, classifies every grant (zone flow / default /
raw-IP pin / unclassified), joins display metadata from the tailnet-setup
repo's `site/zones-meta.json` (inventory + prose live in that LOCAL-ONLY repo,
never in this pushed one), computes all counts and finding_ids, diffs rule
lines against the previous run's baseline
(`state/loop-data/tailnet-zones/policy-prev.json`), writes
`$OUT_DIR/zones-model.json` for render.sh, stages the new baseline under
`$OUT_DIR/loop-data.commit/`, and prints the digest. The engine interprets
only: maps severities, copies ids and counts verbatim, writes headline +
report_markdown. Its stdout digest is always non-empty, so the engine runs
every firing and a clean day is a green `ok`, not an amber skip.

4. Cadence
daily:08:10 local — a policy-sync page needs at-most-daily freshness, and the
findings channel (not the page) is what nags. Staleness expectation 24h;
launchd coalescing of missed calendar firings to one-at-wake is acceptable.
After a deliberate policy change, the policy-change workflow in the
tailnet-setup repo runs `loopctl run tailnet-zones` for an immediate rebuild
rather than waiting for the schedule.

5. Scope & exclusions
In scope: the tailnet policy document (grants, ssh, tests, hosts) from live
API read or repo snapshot; the zones-meta display metadata; the rendered
snapshot page; drift between live policy, repo snapshot, and metadata.
Excluded: ANY policy mutation (this loop never POSTs — the policy-change
workflow with validate-first + If-Match stays the only write path); the
devices API (v2); probing actual reachability (the postflip verification
matrix owns that); serving-infra health (tailscale serve / Caddy watchdogs are
not this loop); maguyva and all other external tools in the engine.

6. Guardrails
- "Never store the Tailscale API token in this repo, in ~/.claude, or anywhere
  persistent on llm" (tailnet-setup CLAUDE.md) — this loop ships with NO
  credential and works from the snapshot; if the owner later decides to mint a
  READ-SCOPED credential for live reads, it lives at
  `~/.config/tailscale-policy-read.token` (0600) and is still never a
  policy-admin token. The loop nags `source:snapshot-fallback` until that
  decision is made, and `loopctl dismiss` is the "decided: no" channel.
- Report/propose-only: the loop never mutates the policy, the tailnet-setup
  repo, or anything outside `$LOOPS_ROOT` (INTERFACES §0). The page is the
  loop's own promoted report page.
- Nothing from the policy is silently dropped: an unrenderable grant still
  renders in a fallback section AND becomes a finding.
- All counts and finding_ids are computed in precheck and copied by the engine
  (model-emitted metrics get believed — house gotcha).
- Guardrails live in prompt.md AND in the floor permission axes — never by
  prompt text alone.

7. Permission axes + justification
The full report-only floor: perm_fs_write=report_only, perm_network=none,
perm_local_exec=none, perm_remote_mutation=none. The engine needs nothing —
it interprets an injected digest. The policy GET runs in trusted precheck.sh
(unsandboxed bash, not governed by the axes), is read-only by construction
(GET only, no POST path exists in the script), and is belt-and-braces scoped:
the credential file, if the owner ever creates it, should be a read-scoped
OAuth client secret, so even the trusted script physically cannot mutate
policy. No axis raised; no dangerous combo approached.

8. Finding identity (what a finding IS + finding_id derivation rule)
A finding is a condition of the policy→page pipeline — never a policy rule
itself (rules are content, rendered on the page). Derivations:
`source:snapshot-fallback` and `policy:fetch-failed` and
`records:snapshot-stale` are fixed literals (each watches exactly one
condition); `policy:unmapped-actor:<actor>` embeds the unmapped actor string;
`policy:unannotated-pin:<src→dst:ports>` embeds the pin's canonical key
(alias-resolved endpoints joined with `+`); `policy:unclassified-grant:<sha8>`
embeds the first 8 hex chars of sha256 over the canonical grant JSON. All are
stable across runs for the same condition and embed no volatile data.
Precheck computes every id; the engine copies them verbatim.

9. Tier-1 semantics (ok/warn/alert meaning)
`ok` — clean sync: page regenerated, no findings (requires the live source
once a credential exists; until then a snapshot-generated page is at best
`warn`). `warn` — page regenerated but something needs a human: snapshot
fallback, stale repo snapshot, mapping gap, undocumented pin, unclassified
grant. `alert` — the pipeline degraded: credential present but the live read
failed (`policy:fetch-failed`); unparseable policy/meta exits precheck
non-zero → runner's `precheck-failed` alert (engine never invoked, previous
page stays promoted). status_reason: `clean_sync`, `snapshot_fallback`,
`records_stale`, `fetch_failed`, `policy_changed`, `mapping_gaps`.

10. Tier-2 metrics + panels
Flat keys copied verbatim from the digest: `policy.grants`, `policy.flows`,
`policy.defaults`, `policy.pins`, `policy.ssh_rules`, `policy.tests`,
`policy.changed` (0/1), `sync.live` (0/1), `sync.snapshot_stale` (0/1),
`sync.unmapped_actors`, `sync.unannotated_pins`, `sync.unclassified_grants`,
`inventory.devices`. dashboard.json: number on sync.live (neutral — the
status light, not the panel, carries the judgment); number on policy.pins
(neutral, hold); number on policy.tests (lower_is_worse, hold — losing
invariants is the bad direction); number on sync.unmapped_actors
(higher_is_worse, warn 1 / alert 3, gap); trend on policy.grants (30d, hold).

11. Engine/model + budget
engine=codex (fleet default; pure interpretation at the floor). model=
(engine default). Expected tokens/run: low thousands input (the digest is
~30 lines), hundreds output (usually 0–1 findings + a short narrative).
retry_transient=1 (default). timeout_s=300 — interpretation only.

12. Page output
Yes — page class `snapshot`: the page IS the product (the zone diagram the
6443 route serves). render.sh → render_zones.py renders
`$OUT_DIR/zones-model.json` on pagekit (kit.css + toggle.js inlined, envelope
id `report-data`, totals: grants/pins/tests/devices/source_live). Sections:
header with policy-source provenance + stat strip; zone cards in posture
bands (from zones-meta); zone flows / defaults / pinned exceptions rendered
LIVE from the grants block; enforced denials (editorial red rows); the
policy's `tests` block rendered as invariants; how-it's-built + reading
notes; provenance footer (source, policy sha, run id). Zone hues extend the
kit palette with one extra token pair (family purple) defined for both
themes. Dismissing a finding silences the nag channel, never the document.
