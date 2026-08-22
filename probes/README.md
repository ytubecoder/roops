# probes/

A probe is trusted unsandboxed code on the host it runs on, same class as
`precheck.sh`. The narrowing is WHICH scripts exist, reviewed in git.
`ticket-add` (a later package) is the only probe that writes.

Built-ins `ping`, `list`, and `check` are not files. Those three names are
reserved: a file by one of those names is refused by the server and omitted
from `list`.

This directory currently ships only `echo-test` (test-only: `probe-writes:
none`, `probe-output: text`; prints argv joined by `|`; `--check` prints
`ok echo-test`). Real probes (`av-scan`, `ticket-add`, …) arrive in WP4.

## Header grammar

The server parses the first 20 lines. The block must start at line 2 (right
after the shebang) and parsing stops at the first line that is not a
`# probe-*:` line — a `probe-timeout-s` written further down in an ordinary
comment is ignored and cannot change the timeout.

```
#!/usr/bin/env bash
# probe: <name>                 (must equal the file name)
# probe-timeout-s: <int>        (optional; default 120; values above 600 are clamped to 600)
# probe-writes: <text>          (required; "none" or a statement)
# probe-output: json|tar|text   (required)
# probe-reads: <text>           (required; free text)
```

A probe whose header is missing a required line, or whose `probe:` name
mismatches the file name, is refused (`refused: bad header: …`, exit 64).
The header is part of the review contract, not decoration.

## `--check` contract

Every probe, when invoked with the single argument `--check`, verifies its
own inputs exist (binary, db, directory…), prints one line, exits 0 (ok) or
1 (unmet), and touches nothing.

## Two-checkout deploy rule

After cutover the probes execute from **llm's** checkout and the loops from
**firstparty's**. The probe key cannot pull. A change to `probes/`,
`bin/probe`, or `bin/probe-server` is pushed from llm (the dev checkout,
always newest) and pulled on firstparty before any loop that uses it is
installed. `loopctl requirements` treats a name the server does not list,
or lists with a different content hash, as unmet — drift is a hard gate,
not a warning.
