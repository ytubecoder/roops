# hello-watchdog

Pilot example loop (`type=watchdog`) kept permanently under `examples/` as
a regression fixture (docs/INTERFACES.md §1, §11 "Pilot" clause). It is
never installed to launchd. See `SPEC.md` for the full intake interview and
`docs/LOOP_AUTHORING.md` for the general process this loop demonstrates.

## What it does

`precheck.sh` — which **is** the job for a watchdog (docs/INTERFACES.md
§4.1) — reads the first non-comment line of `target.txt` as a URL and
probes it with `curl --max-time 5 --fail`. Exit 0 → silent-green (no engine
invocation, just a heartbeat). Nonzero exit → the runner escalates to the
diagnosis engine (`prompt.md`), which classifies the failure and emits a
`target:<condition>` finding.

## Green path for local testing (two options)

**Option 1 — `file://` URL (default, zero setup).** The shipped
`target.txt` points at `file:///dev/null`, which `curl` reads successfully
(verified on macOS: exit 0) with no network and no external process:

```bash
bin/loopctl run hello-watchdog --from examples   # silent-green, no engine invoked
```

**Option 2 — a real local HTTP server.**

```bash
cd /tmp && mkdir -p hw-demo && cd hw-demo && echo ok > index.html
python3 -m http.server 8931 --bind 127.0.0.1 &
echo 'http://127.0.0.1:8931/index.html' > /path/to/examples/hello-watchdog/target.txt
bin/loopctl run hello-watchdog --from examples   # silent-green
kill %1   # stop the server when done
git checkout -- examples/hello-watchdog/target.txt   # restore the fixture default
```

## Flipping it to failing (escalation path)

Point `target.txt` at something that can't be reached, then run again:

```bash
echo 'file:///nonexistent-hello-watchdog-target-xyz' > examples/hello-watchdog/target.txt
bin/loopctl run hello-watchdog --from examples
# -> precheck.sh exits nonzero, runner escalates, diagnosis engine runs,
#    a target:unreachable finding is emitted, loop_status/effective_status
#    are alert (sticky per §4.3) regardless of the diagnosis's own outcome.
git checkout -- examples/hello-watchdog/target.txt   # restore the fixture default
```

Or, with the HTTP server from Option 2 still running, request a path that
404s (`curl --fail` turns that into a nonzero exit, same escalation path):

```bash
echo 'http://127.0.0.1:8931/does-not-exist' > examples/hello-watchdog/target.txt
```

## Disposition workflow

```bash
bin/loopctl findings hello-watchdog --from examples
bin/loopctl dismiss hello-watchdog target:unreachable --note "known outage, tracked elsewhere" --from examples
bin/loopctl run hello-watchdog --from examples   # re-run: latest.json findings array omits it
```

Note: even with the finding dismissed, `loop_status`/`effective_status`
stay `alert` while the probe keeps failing (watchdog stickiness overrides
suppression, docs/INTERFACES.md §4.3) — suppression only ever hides the
*finding*, never the fact that the probe itself is down.

## Fake-engine hermetic test

`tests/test_examples.sh` exercises the escalation path end-to-end with
`engines/fake.sh` (never a real engine, never real network — the failing
target used by the test is a nonexistent `file://` path, not an external
host) using the canned contract in `fixtures/contract.json`: run twice with
a failing target → identical `target:unreachable` finding, `times_seen=2`;
dismiss it → a third run's promoted `latest.json` omits the finding while
`effective_status` stays `alert` (sticky). See that file to run it
directly, or `bash tests/run-tests.sh` for the full suite.
