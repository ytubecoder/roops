# finding-suppression Specification

## Purpose
TBD - created by archiving change b-01-lane-a-pilot-record-engine-cost-per-run. Update Purpose after archive.
## Requirements
### Requirement: Dismissed findings are suppressed indefinitely

The harness SHALL exclude a finding from promoted artifacts whenever its current
disposition is `dismiss`, with no expiry. A dismissal requires a note, so the
suppression always carries a recorded reason.

#### Scenario: A dismissed finding stops appearing

- **GIVEN** an unresolved finding whose current disposition is `dismiss`
- **WHEN** the runner asks for the suppression set for any timestamp
- **THEN** the finding is included in that set
- **AND** the entry carries the dismissal's note and creation time
- **AND** `snooze_until` is null

#### Scenario: Dismissal without a note is refused

- **WHEN** a caller disposes a finding as `dismiss` with no note
- **THEN** the command fails with a non-zero exit code
- **AND** no disposition is recorded

### Requirement: Snoozed findings are suppressed until their deadline passes

The harness SHALL suppress a finding whose current disposition is `snooze` only
while its `snooze_until` is strictly later than the evaluation timestamp, with
both values normalised so that a bare date counts as inclusive through the end
of that day.

#### Scenario: A snooze that has not yet expired suppresses

- **GIVEN** an unresolved finding snoozed until a date after the evaluation timestamp
- **WHEN** the runner asks for the suppression set
- **THEN** the finding is included, with its `snooze_until` reported

#### Scenario: An expired snooze stops suppressing

- **GIVEN** an unresolved finding whose `snooze_until` is at or before the evaluation timestamp
- **WHEN** the runner asks for the suppression set
- **THEN** the finding is not included, and it reappears in promoted artifacts

#### Scenario: Snooze without a deadline is refused

- **WHEN** a caller disposes a finding as `snooze` with no `--until`
- **THEN** the command fails with a non-zero exit code

### Requirement: Acknowledgement records attention without hiding the finding

The harness SHALL NOT suppress a finding on the basis of an `ack` disposition.
`ack`, `reopen`, and the absence of any disposition are all equally
non-suppressing, so acknowledging a finding leaves it visible.

#### Scenario: An acknowledged finding still appears

- **GIVEN** an unresolved finding whose current disposition is `ack`
- **WHEN** the runner asks for the suppression set
- **THEN** the finding is not included

#### Scenario: Reopening cancels an earlier dismissal

- **GIVEN** a finding that was dismissed and then reopened
- **WHEN** the runner asks for the suppression set
- **THEN** the finding is not included, because only the current disposition counts

### Requirement: Suppression is applied by the runner, not trusted to the model

The harness SHALL compute the suppression set outside the agent and apply it when
promoting artifacts, so that a loop stops re-reporting a dismissed finding
without depending on the model to remember the dismissal.

#### Scenario: Only unresolved findings are considered

- **GIVEN** a finding that has been resolved
- **WHEN** the runner asks for the suppression set
- **THEN** the finding is not included, regardless of its disposition history

