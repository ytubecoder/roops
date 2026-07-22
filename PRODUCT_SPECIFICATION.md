# Product Specification — Loops

### B-01: Lane A pilot: record engine cost per run
Priority: medium | Status: released
Released: 2026-07-23 | Commit: 5e74eae
Verified: `tests/run-tests.sh` exit 0
Spec: `b-01-lane-a-pilot-record-engine-cost-per-run` archived to openspec/specs/ (4 added)

### B-02: Declare the verify command in WORKFLOW.toml
Priority: medium | Status: released
Released: 2026-07-23 | Commit: 5e74eae
Verified: `tests/run-tests.sh` exit 0
Add [verify] to WORKFLOW.toml so the Ticket Takeaway close gate runs tests/run-tests.sh deterministically instead of each agent improvising a test command. Config only — the loops harness itself behaves identically.
