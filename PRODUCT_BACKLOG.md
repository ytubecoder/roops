# Product Backlog — Loops

## WIP

## For Review

## Backlog

## Ideas

## Bugs

## Icebox

## Done

### B-01: Lane A pilot: record engine cost per run
Priority: medium | Status: done
Commit: 5e74eae
Spec: A:b-01-lane-a-pilot-record-engine-cost-per-run
Verified: exit=0 commit=5e74eae at=2026-07-23T02:33:16 cmd=tests/run-tests.sh
    passed: 158, failed: 0
    == /Users/llm/projects/loops/tests/test_examples.sh ==
    == tests/test_examples.sh: examples/hello-loop e2e (fake engine) ==
    == tests/test_examples.sh: examples/hello-watchdog e2e (fake engine) ==
    passed: 35, failed: 0
    == /Users/llm/projects/loops/tests/test_runner.sh ==
    == bin/run-loop.sh: completed ok/warn/alert ==
    == bin/run-loop.sh: skipped-overlap ==
    == bin/run-loop.sh: precheck (agent) ==
    == bin/run-loop.sh: watchdog ==
    == bin/run-loop.sh: engine-timeout / stale-green ==
    == bin/run-loop.sh: contract-violation ==
    == bin/run-loop.sh: transient retry / non-retried failures ==
    == bin/run-loop.sh: harness-error ==
    == bin/run-loop.sh: suppression / idempotence ==
    == bin/run-loop.sh: retention pruning ==
    == bin/run-loop.sh: enabled=false ==
    == bin/run-loop.sh: --dry-run ==
    == bin/run-loop.sh: prompt composition ==
    passed: 115, failed: 0

### B-02: Declare the verify command in WORKFLOW.toml
Priority: medium | Status: done
Commit: 5e74eae
Add [verify] to WORKFLOW.toml so the Ticket Takeaway close gate runs tests/run-tests.sh deterministically instead of each agent improvising a test command. Config only — the loops harness itself behaves identically.
Spec: C:none - config only: adds WORKFLOW.toml [verify]; no loops harness behaviour changes, so no spec delta
Verified: exit=0 commit=5e74eae at=2026-07-23T02:35:21 cmd=tests/run-tests.sh
    passed: 158, failed: 0
    == /Users/llm/projects/loops/tests/test_examples.sh ==
    == tests/test_examples.sh: examples/hello-loop e2e (fake engine) ==
    == tests/test_examples.sh: examples/hello-watchdog e2e (fake engine) ==
    passed: 35, failed: 0
    == /Users/llm/projects/loops/tests/test_runner.sh ==
    == bin/run-loop.sh: completed ok/warn/alert ==
    == bin/run-loop.sh: skipped-overlap ==
    == bin/run-loop.sh: precheck (agent) ==
    == bin/run-loop.sh: watchdog ==
    == bin/run-loop.sh: engine-timeout / stale-green ==
    == bin/run-loop.sh: contract-violation ==
    == bin/run-loop.sh: transient retry / non-retried failures ==
    == bin/run-loop.sh: harness-error ==
    == bin/run-loop.sh: suppression / idempotence ==
    == bin/run-loop.sh: retention pruning ==
    == bin/run-loop.sh: enabled=false ==
    == bin/run-loop.sh: --dry-run ==
    == bin/run-loop.sh: prompt composition ==
    passed: 115, failed: 0

## Won't Do

