# Engine probes — observed CLI behavior (2026-07-22)

> Live probes run inline by the controller on this machine. These are **observations, not
> guesses**. Versions: `codex-cli 0.144.3` (`/opt/homebrew/bin/codex`), `Claude Code 2.1.217`
> (`/Users/llm/.local/bin/claude`). §7 of `INTERFACES.md` is the distilled contract; this file is
> the evidence.

## Probe setup

Mini contract schema (same shapes as `contract/contract.schema.json`): top-level object with
`additionalProperties:false`, all properties required, `status`/`severity` string enums,
`schema_version` integer `enum:[1]`, `findings` array of closed objects, and `metrics` — first as
a free-form object, then as a JSON-string field. Prompt: canned "emit exactly these values"
instruction, no tools.

## codex

### Probe 1 — free-form `metrics` object: REJECTED
`codex exec --output-schema` routes the schema into OpenAI **strict structured outputs**. With
`"metrics": {"type":"object"}` the API returned 400 before any generation:

```
invalid_json_schema: In context=('properties', 'metrics'), 'additionalProperties' is required
to be supplied and to be false.
```

Events: `thread.started`, `turn.started`, `error`, `turn.failed`. Process exit code **1**. No
`--output-last-message` file written. Strict mode consequences: every object must carry
`additionalProperties:false` and list every property in `required`; open/free-form objects are
impossible. **Therefore tier-2 `metrics` is a JSON-string field in the shared schema** (§9).

### Probe 2 — strict-compatible schema: SUCCESS
```
codex exec --skip-git-repo-check --ephemeral -s read-only -C "$PWD" \
  --output-schema schema2.json -o codex-last.json --json - < prompt.txt
```
- Exit **0**. `-o` file contains **exactly the final JSON object** (single line, no wrapper),
  schema-conformant: integer `enum:[1]`, string enums, nested closed `findings` objects, and the
  metrics JSON-string all honoured verbatim.
- stdout (with `--json`) is JSONL: `thread.started`, `turn.started`, `item.completed`
  (agent_message duplicates the final JSON in `.item.text`), and
  `turn.completed` with usage:
  ```json
  {"type":"turn.completed","usage":{"input_tokens":12756,"cached_input_tokens":1408,
   "output_tokens":184,"reasoning_output_tokens":83}}
  ```
  **No cost field** — `cost_usd` stays NULL for codex runs.
- ~10 s wall; ~12.8k input tokens baseline (codex system prompt included).
- **Prompt-arg gotcha:** with a prompt argument AND open stdin, codex prints
  `Reading additional input from stdin...` and reads both. Adapters must pass the prompt as
  `-` with stdin redirected from `PROMPT_FILE` (also dodges ARG_MAX/quoting).

### Probe 3 — config-key validation (no API cost)
`--strict-config` validates `-c` overrides before any network call:
- `-c bogus_key_xyz=true` → `Error loading config.toml: unknown configuration field 'bogus_key_xyz'`.
- `-c sandbox_workspace_write.network_access=true -c bogus_key_xyz=true` → errors **only** on
  `bogus_key_xyz` ⇒ `sandbox_workspace_write.network_access` is a recognized key.

User config default model: `gpt-5.5` (`model_reasoning_effort = "xhigh"`). Empty `MODEL` ⇒ omit
`-m` and this default applies.

### codex failure classification (documented, not all probed)
No distinct exit codes for auth/permission failures were observed — API-level failure exits 1
with `error`/`turn.failed` events. Adapter classifies by matching the `error` event message /
stderr: `401|unauthorized|login` → 10; `429|rate limit|5xx|overloaded|stream disconnected|
connection` → 12; else 1. Sandbox denials do **not** surface as a distinct process signal (the
sandboxed command fails; the model narrates it) — on codex, enforcement is the sandbox actually
blocking, and exit 11 is rare/heuristic.

## claude

### Probe — same schema, structured output: SUCCESS
```
claude -p --output-format json --json-schema "$(cat schema2.json)" --model haiku \
  --tools "" --setting-sources "" --strict-mcp-config --no-session-persistence \
  --disable-slash-commands "<prompt>"
```
- Exit **0**. stdout is a **single JSON object** with (observed keys):
  `type:"result"`, `subtype:"success"`, `is_error:false`, `result` (final text, here the JSON as
  a string), **`structured_output`** (the parsed object, schema-conformant — this is what the
  adapter copies to `contract.json.tmp`), `total_cost_usd` (0.018568 — real cost field),
  `usage` (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`,
  `output_tokens`, …), `modelUsage` (per-model tokens + `costUSD`), **`permission_denials`**
  (array — tool-denied evidence), `session_id`, `num_turns`, `duration_ms`, `stop_reason`,
  `api_error_status`.
- `--json-schema` accepts the schema as a **JSON string** (file content via `"$(cat …)"`).
- The exact same schema file works on both CLIs ⇒ the strict-OpenAI subset **is** the
  intersection; no engine-specific schema needed.
- ~7 s wall on haiku.

### Environment note
The user's interactive shell aliases `claude` to `claude --dangerously-skip-permissions`.
Aliases do not apply inside scripts, but adapters must invoke the plain binary and must NEVER
pass that flag — permissions are the enforcement layer.

### claude failure classification (documented, not all probed)
`is_error:true` / `subtype != "success"` / non-zero exit mark failure; `api_error_status`
carries the HTTP status when API-level. Adapter: 401/403 → 10; 429/5xx/overloaded → 12;
missing/invalid `structured_output` with non-empty `permission_denials` → 11; else 1.

## Minimal launchd environment (both engines)
`HOME`, `PATH` containing `/opt/homebrew/bin` (codex) and `$HOME/.local/bin` (claude), plus
`LOOPS_ROOT`. codex auth lives under `~/.codex/`; claude auth uses the keychain/`~/.claude` —
keychain access from a launchd job is exactly what `loopctl install`'s kickstart-verify (§8.1)
exists to prove.

## Follow-up probe (2026-07-22, post-build)
The committed `contract/contract.schema.json` — including `minLength:1` on `run_id` and
`finding_id` — was probed against `codex exec --output-schema` directly: accepted, exit 0,
conformant emission. String `minLength` is fine in codex's strict mode; the schema file is
verified as-committed.
