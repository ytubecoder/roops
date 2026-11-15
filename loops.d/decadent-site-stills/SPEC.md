# decadent-site-stills: intake spec

1. Purpose & stop condition
Keep the Decadent public site's machine-screen stills (`public-site/public/images/tablet/`) and the DE1
hero in step with the app on `main`, so the site shows today's screen. Owner ask, 2026-11-15: the daily
screenshot job should run on the loop runner. Per firing: either `main` has not moved since the last clean
render, or the stills were re-made and, when they differ, pushed to `site/stills-refresh` (GitHub's
site-stills workflow opens or refreshes the review PR). Cross-run: a `stills:<condition>` finding resolves
once a firing renders cleanly again.

2. Agentic pattern
Human-in-the-loop outer shape: the loop proposes new stills as a PR; a person merges or closes it.
Inside a firing there is no model at all unless the job fails, and then it is single-shot interpretation
of the captured output.

3. Type & data flow (precheck gathers vs engine interprets)
type=watchdog. `precheck.sh` fetches svc's dedicated clone `~/projects/decadent`, compares origin/main with
`state/decadent-site-stills/last-ok`, and when it moved checks it out clean, refreshes npm deps and
chromium when `public-site/package-lock.json` changed, then runs decadent's `scripts/stills-refresh.sh`
(render with Playwright, re-make the hero when the idle screen changed, compare, commit, push). The engine
only reads that output on a non-zero exit.

4. Cadence
`interval:1h`. The old GitHub job ran daily plus on every push to main; hourly with a skip when main has
not moved gives at most an hour's lag after an app change for the cost of a git fetch. A render takes
about 10 s. Missed firings while the guest is down are simply the next firing's work.

5. Scope & exclusions
In scope: the tablet stills, `stills.json`, the DE1 hero images. Excluded: the phone showcase images and
their arrows (rebuilt by every site build, `build-showcase.mjs`), merging PRs, deploying, and any branch but
`site/stills-refresh`. The clone is the loop's own: precheck resets it hard every render.

6. Guardrails verbatim
- "Everything is report/propose-only" (INTERFACES §0): the loop proposes through a PR and never touches
  `main`; merging stays with a person.
- Decadent: "Nothing deploys from here: the site ships inside the app's deploy (D-020) once the PR is merged."
- Keep one renderer: firstparty and GitHub's Ubuntu runners anti-alias differently (<0.1% of pixels), so
  the GitHub workflow no longer renders.

7. Permission axes + justification
Engine at the floor on all four axes. The one remote mutation, `git push -f` of `site/stills-refresh` on
ytubecoder/decadent, is trusted deterministic precheck code (kagami and vecomap-unmapped precedent) through
a repo-scoped read-write deploy key `~/.ssh/decadent-deploy` (ssh alias `github-decadent`). svc holds no
GitHub token: the PR is opened by decadent's site-stills workflow on that push with its own GITHUB_TOKEN.
Host setup without sudo: chromium's missing libnss3/libnspr4/libasound2t64 and the webp tools are unpacked
from Debian .debs (`apt-get download` + `dpkg-deb -x`) under `~/.local/opt/chromium-libs/root`; Pillow is a
uv venv at `~/.cache/decadent-stills/venv`.

8. Finding identity (what a finding IS + finding_id derivation rule)
A finding is "the stills job fails in this way". `finding_id` = `stills:<condition>` from a fixed list
(`render-failed`, `caption-mismatch`, `hero-failed`, `diff-failed`, `git-failed`, `deps-failed`,
`browser-failed`, `other`). No commit, still name, time or run id. Verbatim rule in `prompt.md`.

9. Tier-1 semantics (ok/warn/alert meaning)
`ok`: silent green, main unchanged or the stills re-made (and pushed when they differ). `alert`: the job
failed or a caption broke; sticky while it lasts, because a failed main is not recorded and the next firing
tries again. This loop never uses `warn`.

10. Tier-2 metrics + panels
None; `dashboard.json` is `{"panels": []}` on purpose. The heartbeat and headline say everything.

11. Engine/model + budget
`engine=codex`, default model. Zero tokens on every healthy firing; a failure costs one short diagnosis
(~15k tokens with the codex baseline) per firing until fixed (snooze it if a fix will take a while).
`timeout_s=600`; the precheck itself is capped at 300 s and normally takes 10–60 s (npm ci after a
lockfile change is the long case).

12. Page output
No page.
