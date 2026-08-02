# loop-ownership Specification

## Purpose
TBD - created by archiving change b-17-loop-ownership-2026-08-02. Update Purpose after archive.
## Requirements
### Requirement: owner field parsing and grammar
`bin/loopconf.py` SHALL accept an optional `owner=` key whose value matches
`^[a-z][a-z0-9-]{1,40}$`. A present-but-malformed value SHALL be a parse error. An absent
key SHALL NOT be an error and SHALL parse as `None`.

#### Scenario: valid owner parses
- **WHEN** a `loop.conf` contains `owner=maguyva-marketing`
- **THEN** `parse()` returns `conf["owner"] == "maguyva-marketing"` with no errors

#### Scenario: malformed owner is a parse error
- **WHEN** a `loop.conf` contains `owner=Maguyva Marketing!`
- **THEN** `parse()` returns an error naming the `owner` key and `loopctl validate` fails

#### Scenario: absent owner is not an error
- **WHEN** a `loop.conf` has no `owner=` line
- **THEN** `parse()` returns `conf["owner"] is None` with no owner-related error

### Requirement: single resolution rule
`bin/loopconf.py` SHALL export `DEFAULT_OWNER = "loops"` and
`resolve_owner(conf) -> (owner: str, assumed: bool)`: the conf's explicit owner with
`assumed=False`, or `DEFAULT_OWNER` with `assumed=True` when absent. `loopctl` SHALL
obtain the resolved owner through this helper. `dashboard/generate.py` SHALL carry a
lockstep mirror (it must generate against roots without `bin/loopconf.py` on disk) whose
equivalence to the canonical helper is pinned by a drift test.

#### Scenario: dashboard mirror cannot drift
- **WHEN** the drift test compares `generate.resolve_owner`/`generate.DEFAULT_OWNER`
  against `loopconf.resolve_owner`/`loopconf.DEFAULT_OWNER` on explicit and absent inputs
- **THEN** both implementations return identical results, and the test fails on any
  divergence

#### Scenario: explicit owner resolves as not assumed
- **WHEN** `resolve_owner({"owner": "loops"})` is called
- **THEN** it returns `("loops", False)`

#### Scenario: absent owner resolves to the assumed default
- **WHEN** `resolve_owner({"owner": None})` is called
- **THEN** it returns `("loops", True)`

### Requirement: set-owner verb
`loopctl set-owner <name> <slug>` SHALL validate the slug against the owner grammar
BEFORE any write, rewrite only the `owner` key of the loop's `loop.conf` via the existing
conf-rewrite helper, then regenerate the dashboard best-effort (regen failure warns but
does not fail the verb). It SHALL NOT touch launchd state and SHALL NOT record a
`loop_events` row.

#### Scenario: valid set-owner rewrites and regenerates
- **WHEN** `loopctl set-owner hello-loop maguyva-marketing` runs against a conf without an
  `owner=` line
- **THEN** the conf gains/updates `owner=maguyva-marketing`, all other lines are
  preserved, the dashboard is regenerated, and the exit code is 0

#### Scenario: malformed slug writes nothing
- **WHEN** `loopctl set-owner hello-loop "Bad Owner"` runs
- **THEN** the exit code is non-zero and the `loop.conf` bytes are unchanged

#### Scenario: unknown loop fails
- **WHEN** `loopctl set-owner no-such-loop loops` runs
- **THEN** the exit code is non-zero with a loop-not-found message

### Requirement: list and status owner plumbing
`loopctl list --json` and `status --json` loop rows SHALL each gain `"owner"` (resolved)
and `"owner_assumed"` (bool). `loopctl list` (table form) SHALL gain an `owner` column
showing the resolved owner. `loopctl list --owner X` SHALL filter rows to an exact match
on the resolved owner, composable with `--tag`, and a zero-match filter SHALL print the
same distinct not-empty message shape the `--tag` filter uses.

#### Scenario: JSON rows carry resolved owner
- **WHEN** `loopctl list --json` runs over a fleet where `ads-google` has
  `owner=maguyva-marketing` and `hello-loop` has no owner
- **THEN** the `ads-google` row has `"owner": "maguyva-marketing", "owner_assumed": false`
  and the `hello-loop` row has `"owner": "loops", "owner_assumed": true`

#### Scenario: --owner filters exactly
- **WHEN** `loopctl list --owner loops` runs
- **THEN** only rows whose resolved owner equals `loops` remain (no substring matching)

#### Scenario: zero-match --owner names the filter
- **WHEN** `loopctl list --owner nobody` runs over a non-empty fleet
- **THEN** the output states 0 loops matched `--owner nobody` and the fleet total, not the
  empty-fleet message

### Requirement: scaffolding stamps an explicit owner
`loopctl new` and `loopctl import --apply` SHALL write an explicit `owner=` line into the
scaffolded `loop.conf` — `loops` by default, overridable with `--owner <slug>` (validated
against the owner grammar).

#### Scenario: new stamps the default owner
- **WHEN** `loopctl new demo-loop` scaffolds a loop
- **THEN** the generated `loop.conf` contains an explicit `owner=loops` line

#### Scenario: new honors --owner
- **WHEN** `loopctl new demo-loop --owner maguyva-marketing` scaffolds a loop
- **THEN** the generated `loop.conf` contains `owner=maguyva-marketing`

### Requirement: validate notices for assumed owners
`loopctl validate` SHALL emit a non-fatal notice when a loop's owner is assumed: table
form prints an indented `note:` line under the loop's `OK`/`FAIL` line; `--json` rows
gain a `"notices": [...]` list. Notices SHALL NOT affect the exit code.

#### Scenario: assumed owner yields OK plus notice
- **WHEN** `loopctl validate` runs on an otherwise-valid loop with no `owner=` line
- **THEN** the loop reports `OK`, exit code 0, and a notice saying the owner is assumed
  `loops` and how to set it

#### Scenario: explicit owner yields no notice
- **WHEN** `loopctl validate` runs on a loop with `owner=loops`
- **THEN** the `--json` row's `notices` list is empty

### Requirement: garden owner display
Each garden loop row SHALL show the resolved owner as a chip in the summary tier, styled
distinctly from tag chips with the 主 glyph prefix. An assumed owner SHALL render visually
dimmed with a `title` hint naming the assumption and the `set-owner` remedy. Every
`.loop-row` SHALL carry `data-owner="<resolved owner>"` (always emitted, never absent).

#### Scenario: explicit owner chip
- **WHEN** the dashboard renders a loop with `owner=maguyva-marketing`
- **THEN** its row contains an owner chip reading 主 maguyva-marketing and
  `data-owner="maguyva-marketing"`

#### Scenario: assumed owner chip is flagged
- **WHEN** the dashboard renders a loop with no `owner=` line
- **THEN** its owner chip renders with the assumed styling class and a `title` attribute,
  and `data-owner="loops"`

### Requirement: garden owner filtering
The garden filter bar SHALL always render an owner `<select>` listing "all owners" plus
each distinct resolved owner. Owner and tag filters SHALL combine as a conjunction in one
client-side function. The page SHALL remain fully self-contained (zero on-load fetches;
`tests/html_selfcontained.py` passes).

#### Scenario: owner select always present
- **WHEN** the dashboard is generated for a fleet with no tags at all
- **THEN** the filter bar renders with the owner select (tag select absent), listing every
  distinct resolved owner

#### Scenario: filters conjoin
- **WHEN** an owner and a tag are both selected in the rendered page's JS
- **THEN** only rows matching BOTH remain visible

### Requirement: owner edit copy affordance
Clicking a garden owner chip SHALL copy the ready-made command
`loopctl set-owner <loop> <owner>` to the clipboard using inline JS only
(navigator.clipboard with a non-network fallback). The page SHALL NOT mutate any file or
issue any request.

#### Scenario: chip click copies the command
- **WHEN** the owner chip for `ads-google` (owner `maguyva-marketing`) is clicked
- **THEN** the clipboard receives `loopctl set-owner ads-google maguyva-marketing` and no
  network request is made

### Requirement: fleet migration
All nine fleet confs SHALL carry explicit owners in this change:
`ads-google`, `ads-intl`, `ads-program`, `ads-reddit`, `ads-x` →
`owner=maguyva-marketing`; `kagi-ban`, `loop-sensei`, `hello-loop`, `hello-watchdog` →
`owner=loops`.

#### Scenario: no fleet loop is assumed after migration
- **WHEN** `loopctl list --json` runs on this repo after the change
- **THEN** every row has `"owner_assumed": false`

