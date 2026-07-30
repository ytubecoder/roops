# PEON_REPORT — Task 8 (`kagi-ban` pilot)

**Status:** DONE_WITH_CONCERNS

## What I did

Implemented Task 8 of the report-pages plan: the first page-enabled loop, `kagi-ban` (鍵番 — machine-exposure audit via Automic Vault), plus hermetic tests and the vendored reference page.

### Files created

| Path | Role |
|------|------|
| `loops.d/kagi-ban/loop.conf` | Loop config (codex, daily:07:40, timeout 300, floor perms) |
| `loops.d/kagi-ban/precheck.sh` | Trusted `av scan --json` + diff digest; all counts computed here |
| `loops.d/kagi-ban/prompt.md` | Engine interpreter; finding identity `av:<source>:<sha8>` |
| `loops.d/kagi-ban/render.sh` | Executable page renderer entry (env contract) |
| `loops.d/kagi-ban/render_page.py` | Copy-with-provenance of av-audit `render_report.py` + 4 deltas |
| `loops.d/kagi-ban/dashboard.json` | Panels on `av.total` / `av.high` / `av.new` + 30d trend |
| `loops.d/kagi-ban/SPEC.md` | All twelve intake sections filled (no `[FILL:`) |
| `tests/test_kagi_ban.py` | Hermetic precheck (stub av) + renderer gate tests |
| `pagekit/reference/reference-page.html` | Vendored fixture render (`--run-id reference`) |

### TDD sequence

1. Wrote `tests/test_kagi_ban.py` first → 3 failures (loop dir missing).
2. Built loop files + `render_page.py` from APPENDIX B with exactly the four deltas:
   - provenance module docstring
   - `--loop` / `--run-id` required args
   - envelope `meta` + `data.findings` (id `report-data`)
   - script id `scan-data` → `report-data`
3. `chmod +x` on `precheck.sh` and `render.sh`.
4. Generated reference page and gated it with `bin/page_envelope.py check`.
5. Tests green.

### Commits (on `peon/rp-task8` in this worktree)

1. `b55b9f1` — `feat(kagi-ban): av exposure audit — first page-enabled loop (pilot)`
2. (this report) — PEON_REPORT.md

## Deviations

1. **Did not read `~/projects/av-audit/render_report.py`.** Used APPENDIX B verbatim + the four listed deltas only.
2. **Never ran real `av` / never touched Automic Vault.app.** Tests use the brief’s stub binary + `pagekit/reference/fixture-scan.json`.
3. **`bin/loopctl validate kagi-ban`** defaults `LOOPS_ROOT` to `~/projects/loops`. In this worktree validation was run as `LOOPS_ROOT="$(pwd)" bin/loopctl validate kagi-ban` (or `--root`). Output: `OK kagi-ban`.
4. **Test suite env:** `tests/test_runner_pages.sh` fails under `CLICOLOR_FORCE=1` / `FORCE_COLOR=1` because `ls -d` injects ANSI into paths. Full suite is green when those vars are unset (agent shell had them set). Not a kagi-ban defect.
5. **No test helper adaptations** — `page_envelope.check_page` / `read_meta` signatures matched the brief.
6. **Git sandbox concern (see below)** — Grok’s `--sandbox workspace` cannot write the linked worktree git dir under `~/projects/loops/.git/worktrees/…`, so commits live in a standalone `.git` inside this worktree. Foreman must import the branch tip into the main repo before `peon merge`.

## Test evidence

```text
# TDD red (before loop files)
FAILED (failures=1, errors=2)  # 3 tests, loop missing

# Reference page gate
python3 loops.d/kagi-ban/render_page.py pagekit/reference/fixture-scan.json \
  --loop kagi-ban --run-id reference --host fixture --av-version 0.0-stub \
  -o pagekit/reference/reference-page.html
# → wrote … (14465 bytes, 5 findings)
python3 bin/page_envelope.py check --file pagekit/reference/reference-page.html
# → OK (exit 0)

# Unit tests
python3 -m unittest tests.test_kagi_ban -v
# test_first_run_labels_everything_new ... ok
# test_unchanged_world_is_all_ongoing_and_ids_stable ... ok
# test_renderer_passes_the_gate ... ok
# Ran 3 tests … OK

# Validate
LOOPS_ROOT="$(pwd)" bin/loopctl validate kagi-ban
# OK kagi-ban

# Full suite (clean color env)
env -u CLICOLOR -u CLICOLOR_FORCE -u FORCE_COLOR bash tests/run-tests.sh
# python: Ran 327 tests … OK
# test_adapters.sh: passed: 158, failed: 0
# test_examples.sh: passed: 35, failed: 0
# test_runner.sh: passed: 115, failed: 0
# test_runner_pages.sh: passed=19 failed=0
# exit 0
```

## Self-review

- Verbatim fenced files for `loop.conf`, `precheck.sh`, `prompt.md`, `dashboard.json`, `render.sh`, tests.
- `render_page.py` is APPENDIX B + only the four required deltas; footer still mentions `#scan-data` as prose (only the script `id=` attribute changed, per brief).
- Precheck owns all counts and finding_ids; engine is interpretation-only.
- Floor permission axes; LOOP_HANDOFF hard constraints documented in SPEC + notes.
- `render.sh` / `precheck.sh` are executable (mode 100755 in the commit).
- SPEC has all twelve sections; no `[FILL:` placeholders.

## Concerns / open questions for the foreman

### 1. Branch tip import (required before `peon merge`)

Grok was launched with `--sandbox workspace`. The linked worktree’s real git metadata lives under `~/projects/loops/.git/worktrees/loops-rp-task8/`, **outside** the seatbelt project boundary, so `git add` / `git commit` against the linked dir fail with `Operation not permitted`. (peon notes the same trap for Gemini and therefore runs Gemini **without** sandbox; Grok still uses workspace sandbox.)

**Workaround applied:** this worktree’s `.git` is now a standalone repo (objects for new commits + `objects/info/alternates` → main object store for parents). Commits are real and `git -C $worktree` is clean.

**Before `peon merge rp-task8`, run once from the main repo:**

```bash
git -C ~/projects/loops fetch \
  /Users/llm/.peon/worktrees/loops-rp-task8 \
  peon/rp-task8:peon/rp-task8
```

That copies the new objects and advances `refs/heads/peon/rp-task8` so worktree HEAD and the branch tip match peon’s merge gate.

**Suggested peon fix:** run Grok without workspace sandbox for linked worktrees (same rationale as Gemini in `peon`’s `run_gemini`).

### 2. Not live-run

No `bin/loopctl run kagi-ban` against a real machine (Task 10 territory). Hermetic only.

### 3. Install / schedule

`loop.conf` has `schedule=daily:07:40` but the loop is not launchd-installed (consistent with fleet supervised-only policy except loop-sensei).

## Open questions

- None for product behavior of kagi-ban itself.
- Foreman: please import the branch tip (command above) then review/merge; consider un-sandboxing Grok peons so linked-worktree commits work natively.
