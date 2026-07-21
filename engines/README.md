# engines/ — the engine adapter interface

An engine adapter (`engines/<engine>.sh`) is a thin, dumb translator between
the loops harness and one agentic-CLI engine. It maps the loop's permission
axes to that CLI's flags, runs the CLI once, and writes four files. **It
contains no loop logic** — a loop's behavior lives entirely in its
`prompt.md` / `precheck.sh` / `dashboard.json`, never in an adapter.

This document mirrors `docs/INTERFACES.md` §6 (the adapter interface) and
summarizes §7 (the verified flag-mapping tables for `codex` and `claude`).
`docs/INTERFACES.md` is the source of truth; if this file and §6/§7 ever
disagree, INTERFACES.md wins and this file is stale.

## Invocation

The runner (`bin/run-loop.sh`) execs `engines/<engine>.sh` with **no
arguments** — every input arrives via environment variables — inside its own
process group, under a runner-owned timeout. The adapter must not add a
timeout of its own (macOS has no GNU `timeout`/`gtimeout` anyway); the
runner sends `TERM` to the process group at `timeout_s`, waits a grace
period, then `KILL`s it.

## Input environment (§6.1)

| var | meaning |
|---|---|
| `LOOP_NAME` | loop name |
| `RUN_ID` | run id |
| `LOOPS_ROOT` | repo root (adapters use this to locate `bin/redact.py`) |
| `WORKDIR` | engine working root (absolute, exists) |
| `PROMPT_FILE` | absolute path to the fully-composed prompt (§6.2) |
| `OUT_DIR` | absolute `state/runs/<run_id>` (exists, `0700`) |
| `TIMEOUT_S` | advisory only — the **runner** owns enforcement |
| `SCHEMA_FILE` | absolute path to `contract/contract.schema.json` |
| `MODEL` | may be empty ⇒ engine default |
| `PERM_FS_WRITE` | `none` \| `report_only` \| `workdir` |
| `PERM_NETWORK` | `none` \| `full` |
| `PERM_LOCAL_EXEC` | `none` \| `allowlist` \| `full` |
| `PERM_REMOTE_MUTATION` | `none` \| `allowlist` |
| `EXEC_ALLOWLIST` | comma-separated command patterns, possibly empty |
| `LOOP_TYPE` | `agent` \| `watchdog` |

`PROMPT_FILE` already contains the runner-composed prompt — `prompt.md` plus
the optional `PRIOR FINDINGS` and `PRECHECK OUTPUT` blocks (§6.2). The
adapter reads it verbatim and pipes it to the CLI's stdin; it never edits
prompt content.

## Output files (§6.3, all inside `OUT_DIR`)

| file | required | content |
|---|---|---|
| `contract.json.tmp` | yes on success | exactly the engine's schema-conforming final message — a single JSON object, nothing else. The **runner** validates and renames it to `contract.json`; the adapter never validates or renames. |
| `usage.json` | best-effort | raw usage/telemetry as emitted by the CLI, verbatim; `{}` when unavailable. Written on both success and failure paths. |
| `engine.log` | always | the CLI's stdout+stderr, concatenated, piped through `bin/redact.py` (never write unredacted CLI output to disk). |
| `engine.status` | always | exactly one line: `status=<ok\|auth-failed\|tool-denied\|transient\|engine-failed> exit=<n>`, where `<n>` is the adapter's own exit code below. |

## Exit codes (§6.4)

| exit | meaning | `engine.status` |
|---|---|---|
| `0` | success | `status=ok exit=0` |
| `10` | auth/credential failure | `status=auth-failed exit=10` |
| `11` | a required tool was denied by the permission layer | `status=tool-denied exit=11` |
| `12` | transient failure (HTTP 429, 5xx, network unreachable/reset, provider "overloaded") — the **only** class the runner retries | `status=transient exit=12` |
| `1` | any other engine failure | `status=engine-failed exit=1` |

The runner maps these to `runner_status` values (`completed` /
`auth-failed` / `tool-denied` / `engine-failed` / `engine-failed` after
retries exhausted) and its own timeout kill separately to
`engine-timeout`. Auth (10), tool-denied (11), and other (1) failures are
**never** retried — only 12 is.

## The fresh-session rule

Adapters must **never** pass a resume/session-continuation flag (`codex exec
resume`, `claude --resume`/`--continue`, …). Every firing is a brand-new
engine session; cross-run memory is the findings table + `PRIOR FINDINGS`
injection (§3/§6.2), not model session state. A resumed session would be a
second, unauditable memory channel that grows context per firing.

## §7 flag mapping (VERIFIED — see `docs/ENGINE_PROBES.md`)

Both adapters share two facts from §7.1:
- **One schema for both engines.** `contract/contract.schema.json` is
  written to the strict-OpenAI-structured-output subset (every object
  `additionalProperties:false`, all properties `required`) because codex
  rejects free-form objects. `metrics` is therefore a JSON-string field, not
  a nested object.
- **Prompt via stdin**, never as a CLI argument (`codex exec … - <
  "$PROMPT_FILE"`, `claude -p … < "$PROMPT_FILE"`) — avoids `ARG_MAX` and
  quoting hazards.

### codex (`engines/codex.sh`)

```
codex exec --skip-git-repo-check --ephemeral -C "$WORKDIR" \
  -s <sandbox> [-c sandbox_workspace_write.network_access=true] \
  --output-schema "$SCHEMA_FILE" -o "$OUT_DIR/last-message.json" --json \
  ${MODEL:+-m "$MODEL"} - < "$PROMPT_FILE"
```

| axis | flag |
|---|---|
| `PERM_FS_WRITE=none\|report_only` (floor) | `-s read-only` |
| `PERM_FS_WRITE=workdir` | `-s workspace-write` |
| `PERM_NETWORK=full` | `-c sandbox_workspace_write.network_access=true` |
| `PERM_LOCAL_EXEC` (any value) | no dedicated flag — codex enforces this via sandbox + credential scoping, not a CLI flag |

The two flag rows above are independent conditions, not an if/elif chain:
`PERM_NETWORK=full` adds the `-c` key regardless of `PERM_FS_WRITE` (the
adapter does not silently escalate the sandbox mode just to satisfy a
network request — see "Known gaps" below).

- Success: exit `0`; the `-o` file is exactly the final JSON object — copy
  it verbatim to `contract.json.tmp`.
- Usage: the JSONL `turn.completed` event's `.usage` object
  (`input_tokens`, `cached_input_tokens`, `output_tokens`,
  `reasoning_output_tokens`) → `usage.json`. Codex never emits a cost field.
- Failure: exit `1` with `error`/`turn.failed` events; classify the
  `error` message / stderr text: `401|unauthorized|login` → 10;
  `429|rate limit|5xx|overloaded|stream disconnected|connection|network`
  → 12; else → 1. Sandbox denials produce no distinct signal on codex (11
  is claude-primary).
- Empty `MODEL` ⇒ omit `-m` (engine config default applies).

### claude (`engines/claude.sh`)

```
claude -p --output-format json --json-schema "$(cat "$SCHEMA_FILE")" \
  ${MODEL:+--model "$MODEL"} --tools <set> [--allowedTools …] \
  --setting-sources "" --strict-mcp-config --no-session-persistence \
  --disable-slash-commands < "$PROMPT_FILE"
```

| axis | flag |
|---|---|
| floor / `PERM_LOCAL_EXEC=none` | `--tools ""` — no tools at all |
| `PERM_LOCAL_EXEC=allowlist` | `--tools "Bash"` + one `--allowedTools "Bash(<pattern>)"` per `EXEC_ALLOWLIST` entry |
| `PERM_LOCAL_EXEC=full` | `--tools "Bash"` with **no** `--allowedTools` narrowing (unrestricted) |
| `PERM_FS_WRITE`, `PERM_NETWORK` | no dedicated flag in v1 — see "Known gaps" |

`--json-schema` takes the schema as a **JSON string**: pass the file
content (`"$(cat "$SCHEMA_FILE")"`), not the path.

- Success: exit `0`; stdout is a single JSON object. Extract
  **`structured_output`** (the parsed, schema-conformant object) →
  `contract.json.tmp`; the whole object → `usage.json` (includes
  `total_cost_usd`, `usage`, `modelUsage`, `permission_denials`,
  `session_id`, `num_turns`).
- Failure: `is_error:true` / `subtype != "success"` / non-zero exit.
  `api_error_status` carries the HTTP status when present. Classify:
  401/403 → 10; 429/5xx/overloaded → 12; missing `structured_output` **and**
  non-empty `permission_denials` → 11; else → 1.
- **Never pass `--dangerously-skip-permissions`.** The user's interactive
  shell aliases `claude` to add that flag; scripts invoke the plain binary
  and must not add it — permissions are the enforcement layer, not a
  convenience to bypass.

## Known gaps / documented assumptions (flag down for review)

The §7.3 claude table only specifies two axis-driven rows
(`--tools ""` floor and `PERM_LOCAL_EXEC=allowlist`); it does not specify
`PERM_LOCAL_EXEC=full` or any `PERM_FS_WRITE`/`PERM_NETWORK` → claude-flag
mapping. This adapter makes two explicit, documented choices where the
frozen contract was silent, rather than guessing silently:

1. **`PERM_LOCAL_EXEC=full` ⇒ `--tools "Bash"` with no `--allowedTools`.**
   Inferred from the CLI's grants-vs-narrows tool model (`--tools` grants a
   category; `--allowedTools` narrows within it — omitting it leaves the
   grant unrestricted), consistent with `allowlist`'s narrowed form. Not
   independently probed.
2. **`PERM_FS_WRITE` and `PERM_NETWORK` do not affect claude's `--tools`
   flags at all** (unlike codex, where `PERM_FS_WRITE` drives the sandbox
   mode). No loop in the initial fleet uses `perm_fs_write=workdir`
   (§5.2), and the §7.3 table's only fs_write-adjacent note is "the
   adapter writes all files, the model none" for the floor row — read as
   confirming claude-side file writes are never engine-native in v1.

Both are safe-by-default (they never grant more than the table explicitly
specifies) and are called out here so a controller reviewing §7 against a
real `perm_local_exec=full` or `perm_fs_write=workdir` claude loop can
confirm or override them before that combination ships.

## Adding a new engine adapter

1. Create `engines/<name>.sh`, executable, `#!/usr/bin/env bash`,
   `set -euo pipefail`.
2. Read the input environment (§6.1 table above) — do not invent new env
   vars; if the CLI needs something not listed here, that's a contract gap
   to raise, not something to smuggle in.
3. Map the four permission axes to that CLI's actual flags. Prefer the
   floor (least access) when an axis value has no specified mapping, and
   document any inferred/uncertain mapping in this file's "Known gaps"
   section rather than guessing silently.
4. Pipe `PROMPT_FILE` via stdin; never pass the prompt as an argument.
5. Never pass a resume/session-continuation flag (see "fresh-session rule"
   above).
6. Write all four output files (§6.3) on every path, success or failure.
   `engine.log` must go through `bin/redact.py` — pipe the CLI's raw
   stdout+stderr through it before writing to disk; never write unredacted
   CLI output.
7. Classify failures into the five `engine.status` buckets (§6.4) and exit
   with the matching code. Do not add your own timeout — `TIMEOUT_S` is
   advisory; the runner enforces it via a process-group kill.
8. Add `loops.d/*/loop.conf`'s `engine` field allowed values and
   `bin/loopconf.py`'s validation accordingly (outside this directory —
   coordinate with whoever owns that file).
9. Add adapter tests in `tests/test_adapters.sh` following the existing
   pattern: a PATH-shimmed fake CLI that records argv (NUL-delimited — the
   schema-content argument is a single multi-line shell word) and emits
   canned output, exercising every axis combination, both success and each
   failure classification, redaction, and the absence of forbidden flags.
   Never invoke the real CLI in a test (§11) — it costs money and needs
   auth.
