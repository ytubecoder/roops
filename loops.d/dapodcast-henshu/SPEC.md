# dapodcast-henshu — intake spec

The full design is in `directorsactorspodcast`, at
`docs/superpowers/specs/2026-09-30-feedback-loop-design.md` (CR-61).

1. Purpose & stop condition
The loop turns author feedback left on https://dapodcast.pages.dev into
published editorial outcomes without the owner's involvement. Each note or
direct edit gets:
- a bilingual editorial reply on the app's Feedback tab
- a CR
- source changes to the English briefs and the Polish text
- a deployment and a run log

"Done" is judged at two levels:
- **Per firing:** there was nothing new (a zero-token skip), or every new item
  was published or held.
- **Cross-run:** every production feedback ID is in the project's action
  ledger as `deployed`, `held` or `reverted`. The Feedback tab shows this per
  item: Queued, In progress, Completed, Declined or Reverted.

2. Agentic pattern
The pattern is script → agent → script. A read-only precheck decides whether
there is work. The codex engine does the editorial judgment and edits the
checkout. The project's own deterministic `tools/feedback-publish.sh` does
every mutation.

3. Type & data flow
`type=agent`.
- **Precheck** fast-forwards the checkout, fetches
  `/api/review/state`, and runs `tools/review-feedback-scan.mjs`. It prints the
  mode (`editorial` or `resume`) and the scan. Its stdout is empty when there
  is nothing to do.
- **The engine** writes the source edits and
  `docs/author-feedback/pending/<run_id>.json`, then calls the publish
  script.
- **The publish script**:
  - runs the build and tests
  - commits
  - posts replies with idempotent mutation IDs
  - pushes main and deploys
  - verifies the live bytes
  - moves the ledger to `deployed`
  - writes the run log
  - redeploys the ledger asset

4. Cadence
`interval:1h`. Author feedback arrives in bursts during review rounds, and an
hour is fast enough for the badge to turn Completed the same session. An empty
hour costs no tokens.

5. Scope & exclusions
In scope: author notes and direct edits in the production review store.

Excluded:
- the retired preview D1
- `theatre_podcast_full_master_brief_pl.md`
- recording-lab work
- anything the scanner does not list

The loop never resets, imports or migrates D1.

6. Guardrails
The project's CLAUDE.md applies in full:
- The briefs are canonical.
- No question is added, dropped or reworded without a CR.
- Polish submissions are preserved verbatim.
- Author answers are never invented.

The publish script adds these:
- It posts nothing unless the build, verify, parity, tests and structural
  counts pass.
- Replies are idempotent, keyed by `henshu:<run>:<source>`.
- It resumes after a crash without duplicating anything.
- On a race, it rebases and re-checks, and aborts on conflict.

Precheck adds these:
- It alerts on a dirty checkout, unexpected local commits, an unreachable API,
  or orphaned replied/applied ledger records.
- Revert is by owner request only (`tools/revert-run.sh CR-NN`).

7. Permission axes + justification
The engine runs with:
- `perm_fs_write=workdir`, where workdir is the dedicated checkout
- `perm_network=full`
- `perm_local_exec=full`
- `i_accept_unrestricted=true`
- `perm_remote_mutation=allowlist`

**Owner sign-off, 2026-09-30.** The owner explicitly chose full autonomy:
"Full: deploy to prod". This is the amendment `docs/OPEN_THREADS.md` §1 requires
for a scheduled network write. It covers exactly three mutations:
- reply and revert notes through the site's own `/api/review/feedback`
- `git push` of `main` on `ytubecoder/directorsactorspodcast`
- `wrangler pages deploy` of the `dapodcast` project

Why the engine and not a hook: prechecks and `render.sh` are capped at 300 s,
and render failures are ignored. Publishing needs about 5–10 minutes and must
alert when it fails.

The credentials, all on firstparty only:
- `~/.ssh/dapodcast-deploy`: a deploy key with write access to this one repo
- `~/.config/dapodcast/cloudflare.env` (mode 600): a Pages-edit and D1-read
  token

The harness `.env` holds no credentials for this loop.

8. Finding identity
- `held:<source id>`: an item waiting on the owner. It resolves when the
  owner's decision lands and a later run publishes the item.
- `publish-failed:<run_id>`: resolves when a resume run completes.
- `checks-failed:<run_id>`: resolves when a human fixes the checkout.

9. Tier-1 semantics
- `ok`: published, or resumed to done.
- `warn`: something is held, or the checks keep failing.
- `alert`: a publish failure after posting (resume pending), or a precheck
  fatal.

10. Tier-2 metrics + panels
`new_feedback`, `new_edits`, `published`, `held`, `resumable`. See
`dashboard.json`.

11. Engine/model + budget
The engine is codex, the skill's native engine; claude tokens stay reserved
for development. The model is the engine default, with `timeout_s=3600`. A
typical editorial firing runs a single CR.

12. Page output
None. The report markdown names the CR, the deployment and the run log.
