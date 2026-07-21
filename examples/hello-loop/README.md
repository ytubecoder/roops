# hello-loop

Pilot example loop (`type=agent`) kept permanently under `examples/` as a
regression fixture (docs/INTERFACES.md §1, §11 "Pilot" clause). It is never
installed to launchd. See `SPEC.md` for the full intake interview and
`docs/LOOP_AUTHORING.md` for the general process this loop demonstrates.

## What it does

`precheck.sh` scans the fixture files in `world/` (`alpha.md`, `beta.md`,
`gamma.md`) for a line starting `TODO:` and prints a compact summary. The
engine turns that into findings — one per file with an open TODO, using the
stable id `<filename-without-extension>:has-todo` (see `prompt.md`'s
`## Finding identity` section) — plus two numeric metrics (`world.files`,
`world.todo_files`) for the dashboard panels in `dashboard.json`.

Shipped state: `alpha.md` and `beta.md` have an open TODO, `gamma.md`
doesn't. So a normal run emits exactly 2 findings (`alpha:has-todo`,
`beta:has-todo`), `status=warn`, `world.files=3`, `world.todo_files=2`.

## Running it supervised (real engine)

```bash
bin/loopctl validate hello-loop --from examples
bin/loopctl run hello-loop --from examples          # foreground, streams progress
bin/loopctl status hello-loop --from examples
cat reports/hello-loop/latest.md                     # or open dashboard/loops.html
```

## Flipping a finding by hand (resolution lifecycle)

The fixture world is meant to be edited by a tester to watch a finding
resolve:

1. Run the loop once (see above) — note `alpha:has-todo` and
   `beta:has-todo` both appear in `reports/hello-loop/latest.json` and in
   `bin/loopctl findings hello-loop --from examples`.
2. Either delete `world/alpha.md`, or edit it so its `TODO:` line is
   removed/rewritten (e.g. change `TODO: revisit auth flow...` to
   `DONE: revisited auth flow`).
3. Run the loop again. `alpha:has-todo` no longer appears in the emitted
   findings; the runner marks the sqlite `findings` row's `resolved_at` for
   it (Amendment 1 resolution lifecycle) while `beta:has-todo` continues
   with `times_seen` incremented.
4. Restore `world/alpha.md` (or re-add a `TODO:` line) and run a third
   time: `alpha:has-todo` reappears as the *same* `finding_id` —
   `resolved_at` clears and `times_seen` keeps counting from where it left
   off (it is not a new finding).
5. `git checkout -- world/alpha.md` (or hand-restore the file above) to put
   the fixture back the way this loop expects it for other testers/CI.

## Disposition workflow

```bash
bin/loopctl findings hello-loop --from examples
bin/loopctl dismiss hello-loop alpha:has-todo --note "known, tracked elsewhere" --from examples
bin/loopctl run hello-loop --from examples           # re-run: latest.json now omits it
bin/loopctl reopen hello-loop alpha:has-todo --from examples
```

## Fake-engine hermetic test

`tests/test_examples.sh` exercises this loop end-to-end with
`engines/fake.sh` (never a real engine, never the network) using the canned
contract in `fixtures/contract.json`: run twice → identical finding ids,
`times_seen=2`; dismiss `alpha:has-todo` → a third run's promoted
`latest.json` omits it while the audit-trail `contract.json` keeps it. See
that file to run it directly, or `bash tests/run-tests.sh` for the full
suite.
