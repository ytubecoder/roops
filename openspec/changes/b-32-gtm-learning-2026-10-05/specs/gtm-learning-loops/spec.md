## ADDED Requirements

### Requirement: Changed-evidence daily assessment
The daily loop SHALL emit an assessment in contract metrics with input hash, cited sentiment, candidate rationale, changes and next tests. It MUST NOT approve a proposition.

#### Scenario: No new evidence
- **WHEN** the digest hash equals the latest successful assessment hash
- **THEN** precheck exits quietly without invoking the engine

### Requirement: Weekly source collection
The weekly loop SHALL collect bounded public records with stable IDs, exact queries, HTTPS URLs and observation times before interpretation.

#### Scenario: Source unavailable
- **WHEN** one source fails
- **THEN** coverage records the failure and no evidence is invented for that source

### Requirement: Local feedback publication
The mailbox probe SHALL publish local feedback and health after each recorded check, including empty and failed checks, without sending email.

#### Scenario: Quiet success
- **WHEN** no new replies arrive
- **THEN** GC still receives a successful check timestamp and current imported feedback
