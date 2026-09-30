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
The pattern is script → agent, run across two firings.
- **Firing N:** the precheck scans. The codex engine makes the editorial
  judgment and prepares edits and a manifest, offline.
- **Firing N+1:** the precheck publishes that pass through the project's
  deterministic `tools/feedback-publish.sh`. The engine only reports the
  result, or fixes a rejected pass.

3. Type & data flow
`type=agent`.

Precheck without a pending manifest:
- fast-forwards the checkout
- fetches `/api/review/state` and runs `tools/review-feedback-scan.mjs`
- emits `mode: editorial` with the scan, or empty stdout when there is nothing
  to do

The engine prepares source edits and
`docs/author-feedback/pending/<run_id>.json`, then runs the build and tests.
Its sandbox is read-only for `.git` and it has no network.

Precheck with a pending manifest runs `tools/feedback-publish.sh --resume`
under a 270 s budget. The publish script:
- stages, checks and commits
- posts replies with idempotent mutation IDs
- pushes main, deploys and verifies the live bytes
- closes the ledger and writes the run log
- redeploys the ledger asset

The precheck then emits `published`, `fix` (a rejection before anything was
posted, capped at 3 rounds), `publish-failed` or `publish-timeout`. The last
two resume on the next firing.

4. Cadence
`interval:15m`. A pass is prepared in one firing and published in the next, so
a note turns Completed about 15–30 minutes after the pass starts. A firing
with nothing to do costs no tokens.

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
The engine runs with `perm_fs_write=workdir`, where workdir is the dedicated
checkout. Every other axis stays at the floor: no network, no remote mutation.
`perm_local_exec` is `full`, but only for the build and tests. The first
design gave the engine full network and the publish step. The first live run
showed that codex's workspace-write sandbox keeps `.git` read-only, so
publishing moved into trusted precheck code (the vecomap/kagami precedent),
which is strictly safer.

**Owner sign-off, 2026-09-30.** The owner explicitly chose full autonomy:
"Full: deploy to prod". This is the amendment `docs/OPEN_THREADS.md` §1 requires
for a scheduled network write. It covers exactly three mutations, all in
precheck via `tools/feedback-publish.sh`:
- reply and revert notes through the site's own `/api/review/feedback`
- `git push` of `main` on `ytubecoder/directorsactorspodcast`
- `wrangler pages deploy` of the `dapodcast` project

The credentials, all on firstparty only:
- `~/.ssh/dapodcast-deploy`: a deploy key with write access to this one repo
- `~/.config/dapodcast/cloudflare.env` (mode 600): a Pages-edit and D1-read
  token

The harness `.env` holds no credentials for this loop, and the engine never
sees them.

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
