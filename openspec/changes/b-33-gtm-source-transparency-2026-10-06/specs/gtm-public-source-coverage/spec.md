## ADDED Requirements

### Requirement: Bounded multi-origin collection
The weekly research precheck SHALL collect fixed Reddit, wider-web and GitHub searches with explicit query, origin, retrieval method, window, limit, dates and record identities. All requests and output sizes MUST be bounded.

#### Scenario: Multiple source families
- **WHEN** the public research precheck succeeds
- **THEN** each record belongs to its verified origin and unique URLs are counted independently of customer responses

### Requirement: Credential containment
The llm probe SHALL use only the existing configured search credential against the fixed official endpoint. It MUST NOT expose credentials to logs, command arguments, firstparty or model inputs, and MUST NOT mutate service configuration.

#### Scenario: Missing configuration
- **WHEN** the existing credential cannot be read
- **THEN** the probe reports a nonsecret configuration failure and does not install, request or invent another credential

### Requirement: Honest evidence coverage
Empty successful queries, failed queries and selected observations SHALL remain distinguishable. Document-level interpretation MUST NOT claim representative customer sentiment or pool public records with survey respondents.

#### Scenario: Partial failure
- **WHEN** Reddit collection fails but other origins succeed
- **THEN** the report identifies the Reddit gap and interprets only collected records

#### Scenario: Indexed snippet
- **WHEN** a finding comes from a search excerpt
- **THEN** its analysis acknowledges limited context and retains the original URL without claiming full-thread collection
