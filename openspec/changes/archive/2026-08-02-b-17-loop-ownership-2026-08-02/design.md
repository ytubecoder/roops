# B-17 design — loop ownership

## Context

The contract (docs/INTERFACES.md §5) has no notion of who a loop belongs to. `tags=` exists
(Amendment 2) with full CLI + dashboard plumbing, but no loop uses it and tags are plural,
optional, and undifferentiated — the wrong shape for a singular, mandatory attribution.
The generalissimo's steer: owner should be required, but never a blocker — the process
assumes one and the user overrides it later, including from the garden page.

Constraints that bind this design:
- INTERFACES.md is frozen; every observable change here ships as an explicit §5/§8/§10
  amendment in the same commit.
- Generated pages fetch nothing and mutate nothing; the garden page is static HTML.
- Python stdlib only; parsing lives in `bin/loopconf.py` alone (single-parser rule).
- Another agent shares this checkout (b-13 change in flight) — touch only B-17 files.

## Goals / Non-Goals

**Goals:**
- Every loop resolves to exactly one owner on every surface, with "assumed" visibly
  distinct from "explicit".
- One-command owner edits (`set-owner`) and a copy-affordance from the garden page.
- Owner filtering: `loopctl list --owner X` and a client-side garden dropdown.
- All 9 fleet confs stamped in the same change.

**Non-Goals:**
- No owner registry or filesystem verification (an owner is a label, not a path — the
  harness must not couple to `~/projects` layout; WSL portability).
- No new `loop_events` type for owner changes (`loops.d/` is git-tracked; git history is
  the audit trail, matching `set-schedule` precedent).
- No report-page or `serve` console changes.
- No multi-owner. One slug per loop.

## Decisions

1. **First-class `owner=` field, not an `owner:` tag convention.** Owner is singular and
   semantically mandatory; tags are plural/optional and burn one of 8 slots. Rejected:
   reserved-tag approach (cheaper but encodes schema truth as convention).

2. **Required-but-assumed, resolved in one place.** Parser default is `None` (absence is
   never a parse error; a *present but malformed* value is, like any field). The canonical
   helper lives in `bin/loopconf.py` — `resolve_owner(conf) -> (owner, assumed)` with
   `DEFAULT_OWNER = "loops"`; `loopctl` calls it directly. `dashboard/generate.py` carries
   a lockstep MIRROR instead of importing it: its lazy-seam doctrine requires generating
   against roots where `bin/loopconf.py` does not exist (the hermetic dashboard tests
   assert exactly that), so the mirror is pinned by a drift test in
   `tests/test_dashboard.py` — the same canonical-copy pattern as the token blocks vs
   `pagekit/kit.css`. Rejected: hard-required (user explicitly declined blockers); silent
   parser default (hides which loops still need an explicit owner); a root-based lazy
   `resolve_owner` seam (crashes the no-loopconf.py-on-disk test roots).

3. **Grammar `^[a-z][a-z0-9-]{1,40}$`** — the `name` grammar. Owners look like project
   dirs (`maguyva-marketing`, `loops`) and sort/render cleanly.

4. **`set-owner` mirrors `set-schedule`**: validate grammar BEFORE any write,
   `_rewrite_conf_key`, best-effort dashboard regen, no launchd interaction, no event row.
   Unlike set-schedule there is no plist to re-render — owner is not in the plist.

5. **`validate` gains additive non-fatal `notices`.** `_validate_one` (errors) is
   untouched; an assumed owner yields a `notices` entry in `--json`
   (`{"ok", "errors", "notices"}`) and an indented `note:` line under `OK <name>` in table
   form. Exit code unchanged by notices.

6. **`new`/`import --apply` always stamp explicit `owner=`** (default `loops`,
   `--owner <slug>` flag on both). Tooling-scaffolded loops never land assumed; only
   hand-rolled confs can, and they still resolve.

7. **Garden UI**: owner chip in the summary tier styled apart from tags (主 prefix glyph,
   garden vocabulary), dimmed + `title` hint when assumed; `data-owner="<resolved>"` always
   emitted on `.loop-row` (same always-emit rationale as `data-tags`, fix round 1
   precedent). Filter bar gains an always-rendered owner `<select>` ("all owners" +
   distinct resolved owners); the existing tag select still renders only when tags exist.
   One combined client-side function applies owner ∧ tag; `loopsFilterByTag` is replaced,
   not duplicated. Clicking the chip copies `loopctl set-owner <name> <owner>` to the
   clipboard (navigator.clipboard with execCommand fallback, inline JS, zero fetches) —
   the edit affordance without a mutating page.

## Risks / Trade-offs

- [Unknown-key hard-fail means old checkouts can't parse new confs] → single-repo tool,
  no version skew across machines beyond git pull; acceptable, same as every prior field.
- [`data-owner` join with the space-separated `data-tags` filter JS could drift] → one
  combined filter function with its own tests; dashboard tests assert both attributes.
- [Clipboard API absent in odd browsers] → execCommand fallback; worst case the chip
  title still names the command.
- [Shared checkout mid-session] → re-fetch/rebase before commit; scope strictly to B-17
  files (no touching b-13 change dir).

## Migration Plan

Same-commit stamping: `ads-google|ads-intl|ads-program|ads-reddit|ads-x` →
`owner=maguyva-marketing`; `kagi-ban|loop-sensei|hello-loop|hello-watchdog` →
`owner=loops`. Placed adjacent to `name=`/`description=` in each conf. Rollback = revert
the commit (no state/db migration; sqlite untouched).

## Open Questions

(none — remaining choices are line-level)
