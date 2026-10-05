# dapodcast-mamori — intake spec

1. Purpose & stop condition
The production review store holds work that exists nowhere else: the author's
notes, our replies, and wording edited on the site. Every hour, this loop
mirrors it into `backups/production/` in the directorsactorspodcast repo.
"Done" for a firing means the backup either matches production or has been
committed.

2. Agentic pattern
The script is the job. The engine runs only to diagnose a failure.

3. Type & data flow
`type=watchdog`. The precheck:
- hard-resets a dedicated clone, `~/projects/dapodcast-backup`
- runs `tools/backup-production.mjs` (public GET only)
- commits and pushes when the backup changed

4. Cadence
`interval:1h`. A firing where nothing changed makes no commit.

5. Scope & exclusions
Only the public review state. The D1 export is out of scope: the loop's
Cloudflare token deliberately lacks D1 edit and export permission. A full SQL
dump stays manual: `tools/review-db.sh export`.

6. Guardrails
- A malformed response is refused, so the good backup is never overwritten.
- Commits only ever touch `backups/production/`.
- A push that fails is retried with a rebase, at most 3 times.

7. Permission axes + justification
The engine is at the floor. The single remote mutation is a git push to main,
made in trusted precheck code with the repo-scoped deploy key. It falls under
the dapodcast owner sign-off of 2026-09-30 (see `dapodcast-henshu/SPEC.md` §7).

8. Finding identity
`backup-failed:production`.

9. Tier-1 semantics
- `ok`: unchanged, or committed.
- `alert`: the precheck failed, which is sticky.

10. Tier-2 metrics + panels
None.

11. Engine/model + budget
codex, used only on failure.

12. Page output
None.
