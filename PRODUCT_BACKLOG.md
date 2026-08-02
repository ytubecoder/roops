# Product Backlog — Loops

## WIP

### B-13: Dashboard run-now button — fire a loop run from the garden page (phase-1 manual trigger)
Priority: medium | Status: in-progress
Spec: B:b-13-dashboard-run-now-2026-08-02

### B-14: WP1 garden reorg: accordion rows, English glosses, retire reports.html (docs/workpackages/2026-08-02-wp1-garden-reorg.md)
Priority: high | Status: in-progress

## For Review

### B-04: Roops rebrand: landing site + UI concept (pancaking, ledger, tokonoma, engawa)
Priority: medium | Status: for-review

### B-05: Dashboard failure surfacing: error_detail on page, agent-handoff block, inline report drawer
Priority: medium | Status: for-review

### B-06: loop-sensei: fleet examiner loop — built, verified (real-engine planted-failure runs), installed to launchd
Priority: medium | Status: for-review

### B-07: Garden dashboard: roops design applied to generate.py (stamps, tokonoma, 巡/休 install state; §10 style+staleness amendment)
Priority: medium | Status: for-review

### B-08: Report pages (third output tier) + kagi-ban pilot loop
Priority: medium | Status: for-review

### B-09: set-schedule: best-effort dashboard regen (false 400 fix) + close §10 paused-staleness question
Priority: medium | Status: for-review

### B-10: Report-pages follow-up wave: KV single-sourcing, kit.css as the real kit, self-containment by reference
Priority: medium | Status: for-review

### B-11: Dashboard: mockup-parity right-side controls (rounds switch + hanko finding buttons)
Priority: medium | Status: for-review

### B-12: Skill import (loopctl import) + agent surface: tags, provenance, AXI CLI, loops skill
Priority: medium | Status: for-review

## Backlog

### B-03: Ads loops phase 1: reliable runs + manual run-everything trigger + clean schedules screen
Priority: high | Status: proposed

### B-15: WP2 garden dark mode: token dark values + theme toggle (docs/workpackages/2026-08-02-wp2-dark-mode.md)
Priority: medium | Status: proposed

### B-16: WP3 pagekit unification: kit.css on shared tokens + token drift test (docs/workpackages/2026-08-02-wp3-pagekit-unification.md)
Priority: medium | Status: proposed

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
Add [verify] to WORKFLOW.toml so the Ticket Takeaway close gate runs tests/run-tests.sh deterministically instead of each agent improvising a test command. Config only — the loops harness itself behaves identically. Spec: C:none - config only: adds WORKFLOW.toml [verify]; no loops harness behaviour changes, so no spec delta Verified: exit=0 commit=5e74eae at=2026-07-23T02:35:21 cmd=tests/run-tests.sh passed: 158, failed: 0 == /Users/llm/projects/loops/tests/test_examples.sh == == tests/test_examples.sh: examples/hello-loop e2e (fake engine) == == tests/test_examples.sh: examples/hello-watchdog e2e (fake engine) == passed: 35, failed: 0 == /Users/llm/projects/loops/tests/test_runner.sh == == bin/run-loop.sh: completed ok/warn/alert == == bin/run-loop.sh: skipped-overlap == == bin/run-loop.sh: precheck (agent) == == bin/run-loop.sh: watchdog == == bin/run-loop.sh: engine-timeout / stale-green == == bin/run-loop.sh: contract-violation == == bin/run-loop.sh: transient retry / non-retried failures == == bin/run-loop.sh: harness-error == == bin/run-loop.sh: suppression / idempotence == == bin/run-loop.sh: retention pruning == == bin/run-loop.sh: enabled=false == == bin/run-loop.sh: --dry-run == == bin/run-loop.sh: prompt composition == passed: 115, failed: 0
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

