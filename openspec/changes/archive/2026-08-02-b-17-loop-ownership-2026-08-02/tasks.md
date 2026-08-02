# B-17 tasks

## 1. Parser (bin/loopconf.py)

- [x] 1.1 Add `owner` to the field table: optional, new `slug` value type (`^[a-z][a-z0-9-]{1,40}$`), default None; malformed-present value is a parse error
- [x] 1.2 Add `DEFAULT_OWNER = "loops"` and `resolve_owner(conf) -> (owner, assumed)` next to the parse API
- [x] 1.3 Parser tests: valid / malformed / absent owner; resolve_owner both branches

## 2. CLI (bin/loopctl)

- [x] 2.1 `set-owner <name> <slug>` verb: grammar check before write, `_rewrite_conf_key`, best-effort dashboard regen (set-schedule shape, minus plist work); wire subparser + dispatch
- [x] 2.2 `list`: rows gain resolved `owner`/`owner_assumed` via `resolve_owner`; table gains `owner` column; `--owner` exact filter composable with `--tag`; zero-match message names the owner filter (parity with the --tag fix-wave message)
- [x] 2.3 `status --json` loop rows gain `owner`/`owner_assumed`
- [x] 2.4 `new` + `import --apply`: stamp explicit `owner=` (default loops) and add `--owner` flag (validated) to both
- [x] 2.5 `validate`: additive non-fatal notices — assumed owner → `notices` list in `--json` rows, indented `note:` line in table form; exit code untouched
- [x] 2.6 CLI tests: set-owner (rewrite/malformed-no-write/unknown loop), list --owner + JSON fields + column, new/import stamping, validate notices; respect the two argparse `_common_parser` flavors (TestGlobalFlagPlacement precedent) when adding flags

## 3. Dashboard (dashboard/generate.py)

- [x] 3.1 Resolve owner per loop via `loopconf.resolve_owner`; add `owner`/`owner_assumed` to the per-loop dict
- [x] 3.2 Owner chip in the summary tier: 主 prefix, distinct .owner-chip style (light+dark via existing role tokens — no new token values, so no kit.css/token-drift coupling), dimmed class + title hint when assumed
- [x] 3.3 Always-emit `data-owner="<resolved>"` on `.loop-row` alongside `data-tags`
- [x] 3.4 Filter bar: owner `<select>` always rendered ("all owners" + distinct resolved owners); keep tag select conditional; replace `loopsFilterByTag` with one combined conjunction filter reading both selects
- [x] 3.5 Chip click → clipboard copy of `loopctl set-owner <name> <owner>` (navigator.clipboard + execCommand fallback, inline, zero fetches)
- [x] 3.6 Dashboard tests: chip render (explicit + assumed), data-owner attr, owner select always present, combined filter JS presence, copy affordance markup; `tests/html_selfcontained.py` stays green

## 4. Migration + contract

- [x] 4.1 Stamp all 9 confs: ads-* → maguyva-marketing; kagi-ban/loop-sensei/hello-loop/hello-watchdog → loops (adjacent to name=/description=)
- [x] 4.2 INTERFACES.md amendment (dated, explicit): §5 field table row for `owner` + resolution rule; §8 set-owner verb + list/status/validate JSON additions; §10 garden owner chip/filter/copy affordance
- [x] 4.3 docs/LOOP_AUTHORING.md: mention owner in the intake/scaffold flow (one line, not a new interview question)

## 5. Ship

- [x] 5.1 Full suite `bash tests/run-tests.sh` green; fix only fallout from edited files (ruff-hook debt rule)
- [x] 5.2 Regenerate the live dashboard and eyeball the rendered HTML (chips, select, filter JS)
- [x] 5.3 Re-fetch (shared checkout), commit code+amendment+migration together, push
- [x] 5.4 B-17 → review; `openspec archive` the change
