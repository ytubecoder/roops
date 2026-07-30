# Report pages — authoring guide

Design rationale + settled decisions: `docs/REPORT_PAGES_PLAN.md`. Mechanical contract:
`docs/INTERFACES.md` §4.1 step 6.5 + §12. This doc is the HOW for loop authors.

## When to add a page (rubric q12)

Add a page when the loop's world state carries more structured detail than the row +
markdown report can show (inventories, per-item remediation, grouped scans). Skip it when
the headline + findings + report_markdown already tell the whole story. Then choose the
page class:
- `snapshot` — the page renders deterministically-captured world state IN FULL (an audit
  document). Dismissing a loop finding silences the nag channel, not the document.
- `findings` — any part of the page that presents the loop's findings AS findings must
  come from the suppression-filtered `latest.json` (`LATEST_JSON`), never raw
  `contract.json`.

## The moving parts

1. `loops.d/<name>/render.sh` — executable = page-enabled. Runs AFTER contract promotion
   with cwd `loops.d/<name>/` and env `LOOP_NAME RUN_ID LOOPS_ROOT OUT_DIR LATEST_JSON
   LOOP_DATA_DIR PAGEKIT PAGE_OUT`. Write the finished page to `$PAGE_OUT`. Deterministic
   code only: no model calls, no network, no randomness.
2. The page: one self-contained HTML file built on `pagekit/kit.css` (inline it), with
   EXACTLY ONE envelope block — see `pagekit/README.md` for the copy-paste snippet.
   Required meta: loop, run_id, generated_at (ISO8601Z), title, page_class. Optional:
   totals (flat; becomes chips on the reports screen).
3. The gate: promotion happens only if `bin/page_envelope.py check` passes — envelope
   valid, run_id/loop match, ≤ 8 MiB, no external-fetch markup, and REDACTION-CLEAN:
   if `bin/redact.py` would change your page, it does not publish. Never put secret
   VALUES on a page; paths/names are fine.
4. Failure semantics: a broken renderer never fails the run. Look in
   `state/runs/<id>/page-render.log`; the reports screen shows "no page yet" or a
   `stale` badge until a render succeeds again.
5. Loop-private durable state: read baselines from `$LOOP_DATA_DIR`; write updated
   baselines to `$OUT_DIR/loop-data.commit/` — the runner commits them only when the
   run promotes, so failed runs never consume state. Keep it bounded (e.g. exactly one
   previous snapshot). `$LOOP_DATA_DIR` is exported to `render.sh` only (step 1) — a
   precheck (`set -u`, only `LOOP_NAME RUN_ID LOOPS_ROOT WORKDIR OUT_DIR`) that needs a
   baseline must read it directly from `$LOOPS_ROOT/state/loop-data/<name>/` instead
   (see kagi-ban's `precheck.sh`).

## Worked example

`loops.d/kagi-ban/` is the reference implementation: precheck captures `scan.json` +
computes the diff; `render.sh` renders the snapshot page via a copy-with-provenance of
av-audit's renderer; `pagekit/reference/reference-page.html` is the rendered benchmark
(from the sanitized fixture). Quality bar: match it.

## Import integration

`q12_page` rides the skill-import `answers_needed` flow once `loopctl import` exists;
`--apply` scaffolds `render.sh` INERT (non-executable, body commented). Making it
executable is the deliberate act, same trust rule as extracted precheck lines.
