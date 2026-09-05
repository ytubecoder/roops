# docs/ — static or ephemeral, nothing in between

Every doc in this repo is one of two things. Decide which before you create one.

## Static — lives here, is maintained

- **The contract** — `INTERFACES.md` (frozen; conform or amend explicitly)
- **How to build things** — `LOOP_AUTHORING.md`, `SKILL_IMPORT.md`, `REPORT_PAGES.md`
- **Design rationale** — `HARNESS_PLAN.md`, `HARNESS_PLAN_AMENDMENT_1.md`, `superpowers/specs/`
- **Live registers** — `LOOP_SELECTION.md` (approved themes), `OPEN_THREADS.md`
  (unfinished design work), `KAGAMI_SETUP.md`
- **Build records** — `superpowers/plans/`, `plans/`, `workpackages/`, `openspec/changes/`

🚨 **A static doc must not carry live system state.** What is installed, what ran,
what is armed, how many findings — those are truth claims about a running fleet,
stale the moment they are written, and the reader has to check anyway.
**Write the query, not the answer:** `loopctl list`, `loopctl status <loop>`,
`loopctl findings <loop>`, or `systemctl --user -M svc@ list-timers` on firstparty.

## Ephemeral — write it, use it, delete it

Session handoffs. Their only job is to let a context be cleared and the next
session resume. They are not reference and must not be cited.

- Keep them in the scratchpad, not the repo.
- If one is committed anyway, delete or archive it the moment its job is done.
- `docs/_archive/warmstarts/` holds the retired ones. Do not read them, do not
  cite them.

The tell that you are writing an ephemeral doc: it has a "current status", a
"next steps" list, or a sentence starting "I was in the middle of".
