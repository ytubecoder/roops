# WP1 — fleet `.env` seam + `requires=` host requirements

Peon spec. Self-contained: you have no conversation context. Parent design:
`openspec/changes/b-25-linux-port-2026-08-23/design.md` §3, §4, §6.4 (read them; they are the
authority for WHY — this file is the authority for WHAT). House rules that bind you:
`CLAUDE.md` (repo root) and `docs/INTERFACES.md` §0 (macOS-safe shell: no `flock`, `timeout`,
`sed -i`, `date -d`, `realpath -m`; bash 3.2-compatible; Python stdlib only).

## 0. Context in three sentences

The harness is moving from macOS/launchd to Debian/systemd and must keep running on both. Today
nothing reads `$LOOPS_ROOT/.env`, so host configuration (e.g. `GC_BASE`) never reaches a
scheduled run; and nothing lets a loop say "I need `gh` / a credential file / a probe on this
host", so a loop that cannot run here fails at 2 am instead of refusing to install. This package
adds both seams.

## 1. Deliverables

### 1.1 `bin/loopconf.py` — `load_env` + `env` subcommand

- `ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")`.
- `load_env(root: str) -> dict[str, str]`: reads `<root>/.env` if it exists (missing file → `{}`).
  Reuses the existing `_split_line`, `_parse_value`, `_validate_trailer`, `_expand_home` helpers
  (`bin/loopconf.py:123-189`) — **do not call `parse()`**, whose `KEY_RE` is lower-case and whose
  field table would reject every key. Rules: one `KEY=value` per line; `#` comments and blank lines
  ignored; bare or double-quoted values; a literal leading `$HOME`/`~` expanded in EVERY value
  (no per-field typing here); max 64 keys; a duplicate key is an error. Any malformed line raises
  `EnvFileError(f"{path}:{lineno}: {msg}")` (new exception class, subclass of `ValueError`).
- CLI: `loopconf.py env --root R [--json]` → prints the dict as JSON (or `KEY=value` lines);
  exit 1 with the error on stderr if malformed. Register it in `build_parser()` (`:355-368`)
  next to `parse`/`get`.
- `FIELDS` (`:46-111`) gains `"requires": {"required": False, "type": "requires", "default": None}`
  and `_typecheck` (`:295-352`) gains the `requires` type, modelled on `tags` (`:331-346`):
  comma-split, strip, no empty entry, each item must match
  `REQ_RE = re.compile(r"^(os|bin|file|env|probe):[^,\s]+$")`, deduped order-preserving, max 16,
  error text names the bad item and the allowed kinds. `os:` value must be `darwin` or `linux`.
  Parsed value: the list of item strings (e.g. `["bin:gh", "probe:av-scan"]`).

### 1.2 `bin/requirements.py` — NEW, the single implementation

Importable module + CLI, same shape as `bin/lock.py` / `bin/db.py`.

```
check(root, conf, *, live: bool, env: dict | None = None) -> list[tuple[str, bool, str]]
    # one (item, ok, detail) per requires= item, in declaration order; [] when none declared
```

Per kind:

| kind | ok when | detail examples |
|---|---|---|
| `os:X` | `sys.platform.startswith(X)` (`linux` matches `linux*`) | `host is darwin` |
| `bin:NAME` | `shutil.which(NAME, path=unit_path)` where `unit_path` is the SAME string `bin/loopctl:_runtime_path(home)` builds (`bin/loopctl:706-717`). Copy that function into `requirements.py` as `runtime_path(home)` and make `loopctl` import it from there (one source). `home = os.path.expanduser("~")`. | `not on unit PATH (/opt/homebrew/bin:…)` |
| `file:PATH` | `$HOME`/`~` expanded; `os.path.isfile` and `os.access(R_OK)` | `missing`, `not readable` |
| `env:KEY` | `KEY` non-empty in `env` (the caller passes `{**os.environ-with-.env-applied}`; the CLI builds it itself: `os.environ` first, then `.env` keys only where unset) | `unset or empty` |
| `probe:NAME` | `live=True`: run `[<root>/bin/probe, "--check", NAME]` (subprocess, 30 s timeout, `cwd=root`) → exit 0. If `<root>/bin/probe` does not exist → not ok, detail `bin/probe missing`. `live=False`: if `LOOPS_PROBE_HOST` (from `env`) is set and non-empty → ok iff the key file (`LOOPS_PROBE_KEY` or `~/.ssh/loops-probe`) is a readable file; else (local mode) ok iff `<root>/probes/NAME` is an executable file. Never touches the network when `live=False`. | `probe check exited 3: …` / `probe key missing: ~/.ssh/loops-probe` / `probes/NAME not executable` |

`bin/probe` is built by another package; this package only calls it. Tests use a fake.

CLI: `requirements.py check --root R --loop NAME [--from loops.d] [--no-live] [--json]` → parses
the loop's `loop.conf` via `loopconf.parse`, loads `.env` via `loopconf.load_env`, calls `check`,
prints a table (or JSON `{"loop": NAME, "items": [{"item","ok","detail"}], "ok": bool}`), exit 0
if all ok, 1 if any unmet, 2 on usage/parse error (parse errors, malformed `.env`). No `loop.conf`
→ exit 2 with `loop not found`.

### 1.3 `bin/loopctl`

- New verb `requirements [<name> ...] [--json] [--no-live]` (positionals; zero names = every
  loop in `--from`; parents `common_sub` like `validate`, `bin/loopctl:1780-1783`). Output: one
  line per loop `OK <name>` / `UNMET <name>` followed by `  - <item>: <detail>` for unmet items
  (and, in non-JSON mode, `  + <item>` for met ones); a loop with no `requires=` prints `OK <name>
  (no requirements declared)`. JSON: `{name: {"ok": bool, "items": [...]}}`. Exit 1 if any loop
  unmet, else 0. Add `"requirements"` to the `dispatch` dict in `main()` (`:1890-1910`) — that
  dict is also the known-verb set used by the swallowed-verb guard, so adding it there is
  required, not optional.
- `cmd_validate` (`:802-842`): after `_owner_notices`, append requirement notices: for each
  unmet item (`live=True`) `f"requirement unmet on this host: {item} — {detail}"`. Notices never
  change the exit code (same rule as owner notices, `:845-859`).
- `cmd_install` (`:1137-1246`): after the `_validate_one` refusal (`:1154-1159`) and BEFORE the
  run-first precondition (`:1167-1173`), refuse when any item is unmet (`live=True`):
  `refusing to install {name}: requirement unmet — {item} ({detail})` (one line per unmet item),
  return 1. Same refusal, same wording with the verb swapped, in `_set_enabled` when
  `enabled_value` is True (`resume`, `:1275-1302`) and in `_apply_schedule` when the new spec is
  not `manual` (`:1313-1360`) — both would arm a timer.
- `cmd_new` and `_cmd_import_apply` scaffolds: emit a commented line
  `# requires=   # host needs: os:linux|darwin, bin:<name>, file:<path>, env:<KEY>, probe:<name>`
  after the `tags` line of the generated `loop.conf` (find the generated template in
  `cmd_new`, `:755-800`, and the import writer, `:1575-1688`).
- `_runtime_path` becomes `from requirements import runtime_path` (module loaded the same way
  `loopconf` is at `:51-70`); keep the name `_runtime_path` as an alias so existing call sites
  (`:285`, `:736`) are untouched.

### 1.4 `bin/run-loop.sh`

- **Top of file**, immediately after `ROOT=` (`:41`) and before any function definition:
  ```bash
  export TMPDIR="${TMPDIR:-$ROOT/state/tmp}"
  mkdir -p "$TMPDIR" && chmod 700 "$TMPDIR"
  ```
  (`mktemp` at `:109-110` must see it.)
- **After step 3's `db_start_run`** (`:433`) and before the step-4 precheck block (`:557`):
  1. Load `.env`: `ENV_JSON="$("$PY" "$ROOT/bin/loopconf.py" env --root "$ROOT" --json)"`; on
     non-zero exit → `finalize_and_finish harness-error alert alert - env_file_invalid "<stderr
     first line>" - - 1 "..."` (use the existing `finalize_and_finish` signature exactly as the
     `precheck-failed` call at `:638` does; look at how `harness-error` is produced elsewhere in
     the file and match it). For each key in the JSON **not already set** in the environment
     (`[ -z "${!key+x}" ]` is bash-3.2-safe), `export key=value`. Record EVERY key the file
     declares (set or not) in `ENV_FILE_KEYS` (space-separated) — that is the strip list for the
     engine, and it must include keys the unit/shell had already set.
  2. Requirement check: `"$PY" "$ROOT/bin/requirements.py" check --root "$ROOT" --loop "$NAME"
     --from "$FROM_DIR" --no-live --json` → if exit 1, `finalize_and_finish precheck-failed alert
     alert - requirement_unmet "requirement unmet: <item> — <detail>" - - 1 "..."` (first unmet
     item in the message, all of them in `error_detail`), **no precheck, no engine**. Exit 2 →
     `harness-error`.
- **Engine spawn** (`_engine_runner_fn`, `:684-691`): strip every key in `ENV_FILE_KEYS`
  (every key named in `.env`, whether the runner exported it or it was already in the
  environment) before `exec`: build `env -u K1 -u K2 …` in front of the existing `exec "$ENGINE_ADAPTER"`
  (keep the inline `LOOP_NAME=… exec` assignments working — simplest is `exec env ${UNSET_ARGS}
  LOOP_NAME=… "$ENGINE_ADAPTER"`; `env -u` is POSIX on both macOS and Debian). Precheck
  (`:566-570`) and render (`:956-958`) keep the exported env.
- Keep `--trigger` vocabulary and every existing exit code unchanged.

### 1.5 `.gitignore`

Add `.env` (repo root). Same commit as the loader.

### 1.6 `docs/INTERFACES.md` amendments (same commit)

- §1 layout: `.env` (gitignored, machine-local host config), `state/tmp/`.
- §4.1: new step "3a" text after step 3: env load (after start-run, harness-error on malformed,
  export-if-unset, keys recorded) and requirement check (config-only, `precheck-failed`,
  no engine). Step 5/§6.1: "keys exported from `.env` are unset for the adapter".
- §5 table: `requires` row (`no`, comma list of `kind:value`, max 16, kinds listed, "required-
  but-assumed: absence = portable"). §5.0: `env` subcommand + `load_env` + `ENV_KEY_RE`.
- New §5.3 "Host requirements" (the kind table from §1.2, live vs config-only rule, which
  verbs refuse). Keep it mechanical; rationale lives in the design.
- §8 verb table: `requirements`. §8.1: install refusal step "1a: requirements (live)".
- Do not renumber existing sections.

## 2. Mandated tests (names + assertions; the reviewer audits these, not your implementation)

`tests/test_loopconf.py` (extend):
- `test_load_env_missing_file_is_empty` — no `.env` → `{}`.
- `test_load_env_grammar` — bare, quoted-with-spaces, `\"` escape, `#` comment line, trailing
  comment after quoted value, `$HOME/x` and `~/x` expanded, blank lines; asserts exact dict.
- `test_load_env_rejects_lowercase_key` — `gc_base=1` → `EnvFileError` whose message contains
  `:1:`.
- `test_load_env_rejects_duplicate_and_malformed` — duplicate key; line without `=`; unbalanced
  quote — each raises with the line number.
- `test_load_env_max_keys` — 65 keys → error.
- `test_requires_parse_ok` — `requires="bin:gh, probe:av-scan, bin:gh"` → `["bin:gh",
  "probe:av-scan"]` (deduped).
- `test_requires_rejects_unknown_kind_and_os_value` — `auth:gh` and `os:windows` are parse
  errors naming the item.
- `test_requires_max_16` and `test_requires_absent_is_none`.
- `test_env_cli_json_and_error_exit` — `loopconf.py env --root R --json` prints the dict; a
  malformed file exits 1.

`tests/test_requirements.py` (new; build a temp root with `bin/loopconf.py` copied or imported
from `REPO_ROOT` the way `tests/test_loopctl.py:150-178` loads modules):
- `test_os_kind` — `os:<this platform>` ok, the other not.
- `test_bin_kind_uses_unit_path_not_callers_path` — put a fake executable in a temp dir that is
  on `os.environ["PATH"]` but NOT in `runtime_path(home)`: `bin:fake` is **unmet**; put one
  under `~/.local/bin` (monkeypatch `HOME` to a temp home) → met.
- `test_file_kind_expands_home_and_checks_readable` — `file:~/x` met after creating it; unmet
  when missing; unmet when mode 000 (skip on root).
- `test_env_kind_reads_dotenv_only_when_unset` — `.env` has `GC_BASE=a`; with `GC_BASE` unset →
  met; with an empty `env` dict and no `.env` → unmet; explicit env value wins over `.env`.
- `test_probe_kind_live_calls_bin_probe_check` — fake `bin/probe` script that logs its argv and
  exits per `FAKE_PROBE_EXIT`; assert argv `["--check", "av-scan"]`, met on 0, unmet on 3 with
  the detail containing the exit code; missing `bin/probe` → unmet `bin/probe missing`.
- `test_probe_kind_config_only_never_spawns` — `live=False`: fake `bin/probe` is NOT executed
  (canary file absent); local mode → met iff `probes/av-scan` executable; remote mode
  (`LOOPS_PROBE_HOST=x`) → met iff key file exists.
- `test_cli_exit_codes_and_json` — `check --json` shape, exit 0/1/2.

`tests/test_loopctl.py` (extend, reuse `LoopsRoot`):
- `test_requirements_verb_matrix_and_exit` — two loops, one unmet → `UNMET` line with the item,
  exit 1; `--json` shape; no names = all loops.
- `test_validate_notices_unmet_requirements_without_failing` — `OK <name>` plus a
  `note: requirement unmet on this host: bin:definitely-not-a-binary — …` line, exit 0.
- `test_install_refuses_unmet_requirement_before_run_first_check` — a loop with
  `requires=bin:definitely-not-a-binary` and NO prior run: stderr contains `requirement unmet`
  and does NOT contain `no non-failed supervised run` (ordering), exit 1, no plist written, no
  launchctl call.
- `test_resume_and_set_schedule_refuse_unmet` — both exit 1 with `requirement unmet`; `pause`
  still works; `set-schedule manual` still works.
- `test_requirements_is_a_known_verb` — `loopctl --actor requirements` errors with the
  "ambiguous invocation" message (this is how the known-verb set is exercised today).
- `test_new_scaffold_has_requires_comment` — `loopctl new` output `loop.conf` contains
  `# requires=`.

`tests/test_runner.sh` (extend, using `runner_test_helpers.sh` + `engines/fake.sh`):
- `test_env_file_reaches_precheck_and_render_not_engine` — `.env` with `GC_BASE=http://x`; precheck echoes
  `GC_BASE=$GC_BASE`; a `render.sh` in the loop dir also echoes it into a file under `$OUT_DIR`
  (assert the key is still present AFTER the engine ran — the strip is an `env -u` prefix on the
  adapter child only, never an `unset` in the runner); fake engine's `engine.log` records its env (check what `fake.sh` already
  logs — extend it to dump `GC_BASE` if needed, the file is test-only); assert precheck.out
  contains `http://x` and the engine log does NOT.
- `test_env_file_explicit_export_wins_but_is_still_stripped_from_engine` — `GC_BASE=http://shell`
  exported before `run_runner` with `.env` declaring `GC_BASE=http://x` → precheck sees
  `http://shell`; the engine log shows NO `GC_BASE` at all (the key is named in `.env`, so it is
  stripped even though the runner did not set it).
- `test_env_file_malformed_is_harness_error` — `.env` with `bad line` → `runner_status=
  harness-error`, `error_detail` contains `.env:1`, no engine invoked (fake engine canary).
- `test_requirement_unmet_is_precheck_failed_without_engine` — `requires=bin:definitely-not-a-
  binary` → `runner_status=precheck-failed`, `loop_status=alert`, `error_detail` contains
  `requirement unmet: bin:definitely-not-a-binary`, `precheck.out` absent or empty, engine not
  invoked.
- `test_tmpdir_under_state` — a precheck that prints `$TMPDIR`; assert it is
  `$root/state/tmp` when the test unsets `TMPDIR`; directory mode 700.

## 3. Hard constraints

- **Allowlist** (the only files you may create/modify): `bin/loopconf.py`, `bin/requirements.py`,
  `bin/loopctl`, `bin/run-loop.sh`, `engines/fake.sh`, `.gitignore`, `docs/INTERFACES.md`,
  `tests/test_loopconf.py`, `tests/test_requirements.py`, `tests/test_loopctl.py`,
  `tests/test_runner.sh`, `tests/runner_test_helpers.sh`, `PEON_REPORT.md`.
- Do NOT touch `dashboard/`, `bin/console.py`, `loops.d/`, `probes/`, `bin/probe*` (other packages).
- Do NOT change any existing exit code, status enum, or `--trigger` token. Do not rename
  `state/launchd-logs/`.
- Verify command: `bash tests/run-tests.sh` — must exit 0 with no network. Run it before you
  report; paste the last 15 lines of its output into `PEON_REPORT.md`.
- No new third-party dependencies. Python stdlib only. Shell stays bash-3.2/macOS-safe.
- Never run `loopctl install` against the real fleet; tests use temp roots and the fake launchctl.

## 4. Definition of Done

- Every test in §2 exists by the given name, asserts what is written, and passes.
- `bash tests/run-tests.sh` exits 0.
- `docs/INTERFACES.md` carries the §1.6 amendments in the same commit.
- `PEON_REPORT.md` lists: files touched, the verify output tail, and anything in this spec you
  could not do exactly as written (with why) — deviations are fine if reported, silent
  deviations are not.
