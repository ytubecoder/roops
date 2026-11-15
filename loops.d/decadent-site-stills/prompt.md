# decadent-site-stills: diagnosis prompt

You are only invoked when `precheck.sh` (the actual job, docs/INTERFACES.md §4.1) exited non-zero while
re-making the Decadent public site's machine-screen stills from the app on `main`. Read the
`PRECHECK OUTPUT` block appended below and explain what went wrong and what a person should do. You do
not re-run anything, fetch anything, or open files: the output is all you have.

## What the output means

- The first line starts `stills:` and names the step that failed, with the 12-character `main` commit.
- `could not make the stills ... (tablet-stills.mjs exit N)`: the Playwright script that drives the
  machine screen failed. The lines after it are the tail of its log. A selector timeout usually means the
  app's screen changed and `public-site/scripts/tablet-stills.mjs` still looks for the old control; a
  `browserType.launch` error means chromium or its unpacked libraries on firstparty broke
  (`~/.local/opt/chromium-libs`, `~/.cache/ms-playwright`).
- `A caption no longer matches the screen`: the stills were made (and pushed to `site/stills-refresh`,
  so the review PR has them), but a word that the site's caption or alt text promises is gone from a
  still. The listed lines name the still and the missing words. The fix is in decadent:
  `public-site/showcase/tablet-claims.json`, `public-site/components/showcase/tablet-still.tsx` or
  `public-site/content/home.ts`, in the open stills PR.
- `could not re-make the DE1 hero` / `could not compare the stills`: Pillow-side failures
  (`~/.cache/decadent-stills/venv`).
- `git fetch failed`, `push of site/stills-refresh failed`: GitHub or the deploy key
  `~/.ssh/decadent-deploy` (svc on firstparty). `npm ci or playwright install failed`: dependency refresh
  after the lockfile changed.

## What to report

- `status`: `alert` (the runner keeps a failed watchdog at `alert` whatever you emit).
- `status_reason`: one of `render_failed`, `caption_mismatch`, `hero_failed`, `diff_failed`,
  `git_failed`, `deps_failed`, `browser_failed`, `other`.
- `headline`: one line naming the failure and the commit, e.g.
  `"caption mismatch at 5d6f6e0abc12: rate still lost 'Start shot'"`.
- `report_markdown`: what failed, the evidence lines quoted from the output, and the next step for a
  person, naming the file to change. Keep it short.
- `metrics`: the JSON string `"{}"`.
- One finding for the failure, per the identity rule below.

## Output contract

Your final message MUST be a single JSON object conforming exactly to `contract/contract.schema.json`:
schema_version, run_id, status, status_reason, headline, report_markdown, metrics, findings. No prose
outside that JSON object.

- `run_id` MUST equal the value from the `## RUN CONTEXT` block the runner appends; copy it exactly.
- `metrics` MUST be a JSON **string** containing a serialized JSON object, e.g. `"{}"`.

## Findings prompt contract

1. Re-emit a still-true finding with its **same `finding_id`**; never invent a new id for a recurring
   condition.
2. Do not re-argue a `DISMISSED` finding unless the underlying situation has **materially changed**; if
   it has, say what changed.
3. Still emit `SNOOZED` findings if true; suppression is the runner's job, not the model's.

## Finding identity

A finding is "the stills job fails in this way". `finding_id` = `stills:<condition>`, where
`<condition>` is the `status_reason` with `_` replaced by `-` (`stills:render-failed`,
`stills:caption-mismatch`, `stills:git-failed`, …). Never put the commit, a still name, a word, a time or
a run id in the id: a new commit with the same kind of failure is the same finding.
