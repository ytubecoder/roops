# B-17 — Loop ownership: owner= field, set-owner verb, garden owner chips + filtering

## Why

Every loop serves some project or process — `kagi-ban` audits this machine for the harness
itself, the five `ads-*` loops exist for maguyva-marketing — but nothing in the contract
records that, so the fleet reads as an undifferentiated list. As the fleet grows past a
handful of loops, the generalissimo needs to see at a glance whose loops these are and
narrow the garden to one owner's slice.

## What Changes

- New first-class `loop.conf` key `owner=` (slug, same grammar family as `name`):
  the project/process the loop belongs to. **Required-but-assumed**: a missing owner is
  never a hard failure anywhere; every surface resolves it to `loops` and flags it as
  assumed, so nothing is ever unowned but an assumption stays visible and correctable.
- `loopctl set-owner <loop> <slug>` — rewrites the key in place (same shape as
  `set-schedule`: validate first, `_rewrite_conf_key`, best-effort dashboard regen).
- `loopctl list`/`status --json` rows gain `"owner"` + `"owner_assumed"`; `list --owner X`
  exact-match filter (parallel to `--tag`); the `list` table gains an owner column.
- `loopctl new` and `loopctl import --apply` always stamp an explicit `owner=`
  (default `loops`, overridable via `--owner`), so tooling-scaffolded loops never land assumed.
- `loopctl validate` emits a non-fatal notice for an assumed owner.
- Garden dashboard: per-loop owner chip in the summary tier (主 glyph; dimmed when assumed),
  `data-owner` attribute, an always-visible owner `<select>` in the filter bar combining with
  the existing tag filter (client-side only, zero fetches), and click-to-copy of the
  ready-made `loopctl set-owner` command as the edit affordance (the static page never mutates).
- Migration in the same change: `ads-google/intl/reddit/x/program` → `maguyva-marketing`;
  `kagi-ban`, `loop-sensei`, `hello-loop`, `hello-watchdog` → `loops`.
- `docs/INTERFACES.md` §5 amendment ships with the code (field table row + amendment note).

## Capabilities

### New Capabilities
- `loop-ownership`: the owner field's grammar and resolution rule, CLI plumbing
  (set-owner, list/status JSON, --owner filter, new/import stamping, validate notice),
  and the garden display/filter/copy-affordance behavior.

### Modified Capabilities

(none — `finding-suppression` is untouched)

## Impact

- `bin/loopconf.py` — new key in the field table (parse + `get`).
- `bin/loopctl` — set-owner verb, list/status plumbing, `--owner` filter, new/import
  stamping, validate notice.
- `dashboard/generate.py` — owner chip, `data-owner`, filter bar select + combined
  client-side filter JS, copy-command affordance.
- `loops.d/*/loop.conf` — all 9 confs stamped.
- `docs/INTERFACES.md` — §5 field table + amendment (frozen contract: amended explicitly,
  never drifted).
- Tests: `tests/` parser/CLI/dashboard suites extended; `tests/html_selfcontained.py`
  must stay green (no new fetches).
- Out of scope: owner registry / filesystem verification, new `loop_events` type
  (git history of `loops.d/` is the audit trail), report pages, the `serve` console.
