# Skill Import + Agent Surface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `docs/SKILL_IMPORT_AND_AGENT_SURFACE_PLAN.md` — tags/provenance foundation, live-run visibility, per-finding paste blocks, `loopctl import` (analyze/apply), AXI CLI polish, and the distributable `loops` skill.

**Architecture:** Additive amendment to a frozen contract: new `loop.conf` key (`tags`), new sqlite table (`loop_events`), new stdlib-Python module (`bin/skill_import.py`) wired into `bin/loopctl`, and dashboard/generate.py rendering additions. Import is static (zero model invocations); all gates (validate → run → install) unchanged, plus one new mechanical install precondition.

**Tech Stack:** bash + Python 3 stdlib ONLY (no pip packages, no node). sqlite3 CLI/module, launchd. Tests: hermetic bash + `unittest` via `tests/run-tests.sh`.

## Global Constraints

- **Stdlib only; no new dependencies** (INTERFACES §0). No `yaml`, no `jsonschema`.
- **All paths `$HOME`-relative**; resolve root as `LOOPS_ROOT="${LOOPS_ROOT:-$HOME/projects/loops}"`.
- **macOS: never** `flock`, `timeout`/`gtimeout`, GNU `sed -i`, `date -d`. Timestamps: `date -u +%Y-%m-%dT%H:%M:%SZ` / `datetime.now(timezone.utc)`, second precision, `Z` suffix.
- **Every INTERFACES.md edit is marked** with `(Amendment 2 — 2026-07-30)` inline, in the task that needs it. `schema_meta.schema_version` stays `1`.
- **Tests are hermetic**: own `mktemp -d` LOOPS_ROOT, fake engine only (`tests/` fixtures), no network, never touch real `state/`. Full suite `bash tests/run-tests.sh` must pass at the end of every task.
- **Commit at the end of every task, then `git push`** (house rule). Commit trailer:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- Loop name grammar: `^[a-z][a-z0-9-]{1,40}$`. Tag grammar: `^[a-z][a-z0-9:_-]{1,40}$`, max 8, deduped order-preserving.
- Design doc (spec) for all rationale: `docs/SKILL_IMPORT_AND_AGENT_SURFACE_PLAN.md`. Do not re-decide settled items (§2 there).

---

## Phase 1 — Foundation (tags, loop_events, actor, provenance)

### Task 1: `tags=` key in loopconf

**Files:**
- Modify: `bin/loopconf.py` (FIELDS dict at :44, `_typecheck` at :244)
- Test: `tests/test_loopconf.py`

**Interfaces:**
- Produces: `parse(path)` conf dict gains key `"tags"`: `list[str] | None` (normalized). Everything downstream (loopctl, dashboard) reads `conf.get("tags") or []`.

- [ ] **Step 1: Write failing tests** (append to `tests/test_loopconf.py`, matching its existing style of writing a temp loop.conf and asserting on `parse()` output):

```python
class TestTags(unittest.TestCase):
    def _parse_with(self, tags_line):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "loop.conf")
        with open(p, "w") as f:
            f.write("name=t1\ndescription=x\ntype=agent\nengine=codex\nschedule=manual\n")
            if tags_line is not None:
                f.write(tags_line + "\n")
        return loopconf.parse(p)

    def test_tags_absent_defaults_none(self):
        conf, errors = self._parse_with(None)
        self.assertEqual(errors, [])
        self.assertIsNone(conf["tags"])

    def test_tags_parse_dedupe_order(self):
        conf, errors = self._parse_with('tags="project:x, campaign:y, project:x"')
        self.assertEqual(errors, [])
        self.assertEqual(conf["tags"], ["project:x", "campaign:y"])

    def test_tags_invalid_entry_fails(self):
        _conf, errors = self._parse_with('tags="Project:X"')   # uppercase
        self.assertTrue(any("tags" in e for e in errors))

    def test_tags_empty_entry_fails(self):
        _conf, errors = self._parse_with('tags="a,,b"')
        self.assertTrue(any("tags" in e for e in errors))

    def test_tags_max_eight(self):
        nine = ",".join(f"t{i}" for i in range(9))
        _conf, errors = self._parse_with(f'tags="{nine}"')
        self.assertTrue(any("tags" in e for e in errors))
```

- [ ] **Step 2: Run to verify failure:** `python3 -m unittest tests.test_loopconf -v` → the 5 new tests FAIL (`KeyError: 'tags'` / no error emitted).

- [ ] **Step 3: Implement.** In `FIELDS` (after `"notes"` entry, `bin/loopconf.py:83`):

```python
    "tags": {"required": False, "type": "tags", "default": None},
```

In `_typecheck` add a branch (mirror the existing `"list"` branch's shape):

```python
    if ftype == "tags":
        entries = [e.strip() for e in str(raw_value).split(",")]
        if any(e == "" for e in entries):
            return None, f"{key}: empty tag entry"
        pat = re.compile(r"^[a-z][a-z0-9:_-]{1,40}$")
        bad = [e for e in entries if not pat.match(e)]
        if bad:
            return None, f"{key}: invalid tag(s) {bad} (need ^[a-z][a-z0-9:_-]{{1,40}}$)"
        deduped = list(dict.fromkeys(entries))
        if len(deduped) > 8:
            return None, f"{key}: max 8 tags, got {len(deduped)}"
        return deduped, None
```

(Adapt the `return` convention to `_typecheck`'s actual signature — it may append to an errors list instead of returning tuples; follow the surrounding branches exactly.)

- [ ] **Step 4: Run tests:** `python3 -m unittest tests.test_loopconf -v` → PASS, and the pre-existing tests still pass.

- [ ] **Step 5: Amend INTERFACES.** Add a row to the §5 field table:
`| tags | no | comma-separated, each ^[a-z][a-z0-9:_-]{1,40}$, deduped order-preserving, max 8 | — | grouping/filtering only; exact-match filter (Amendment 2 — 2026-07-30) |`

- [ ] **Step 6: Full suite, commit:** `bash tests/run-tests.sh` → green. `git add -A && git commit -m "feat(conf): tags= key with normalization (Amendment 2)" && git push`

### Task 2: `loop_events` table + `db.py record-event` / `query loop-events`

**Files:**
- Modify: `bin/db.py` (schema SQL block :92, dispatch/main :850, `build_parser` :770, query functions :719)
- Test: `tests/test_db.py`

**Interfaces:**
- Produces CLI: `db.py record-event --root R --loop L --event E --actor A [--detail JSON]` (exit 0; validates E against enum) and `db.py query loop-events [--loop L] [--limit N]` → JSON array newest-first: `[{id, loop_name, event, actor, ts, detail}]`.
- Event enum: `created|imported|installed|uninstalled|paused|resumed` (NO `validated` — settled, spec §3.2).

- [ ] **Step 1: Failing tests** (append to `tests/test_db.py`, using its existing temp-root helper pattern):

```python
class TestLoopEvents(unittest.TestCase):
    def test_record_and_query_roundtrip(self):
        root = self._mkroot()  # reuse existing helper; else tempfile.mkdtemp() + init
        rc = db.main(["record-event", "--root", root, "--loop", "l1",
                      "--event", "created", "--actor", "tester",
                      "--detail", '{"source_skill": "/tmp/s"}'])
        self.assertEqual(rc, 0)
        out = self._capture_query(root, "loop-events", loop="l1")  # json.loads of stdout
        self.assertEqual(out[0]["event"], "created")
        self.assertEqual(out[0]["actor"], "tester")
        self.assertEqual(json.loads(out[0]["detail"])["source_skill"], "/tmp/s")

    def test_unknown_event_rejected(self):
        root = self._mkroot()
        rc = db.main(["record-event", "--root", root, "--loop", "l1",
                      "--event", "validated", "--actor", "t"])
        self.assertNotEqual(rc, 0)

    def test_init_idempotent_on_existing_db(self):
        root = self._mkroot()
        db.main(["init", "--root", root])
        db.main(["init", "--root", root])  # second init on populated DB: no error
```

- [ ] **Step 2: Verify failure:** `python3 -m unittest tests.test_db -v` → FAIL (unknown verb `record-event`).

- [ ] **Step 3: Implement.** Append to the schema SQL string (before `schema_meta`):

```sql
CREATE TABLE IF NOT EXISTS loop_events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  loop_name TEXT NOT NULL,
  event     TEXT NOT NULL,
  actor     TEXT NOT NULL,
  ts        TEXT NOT NULL,
  detail    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_loop_ts ON loop_events(loop_name, ts DESC);
```

New command + query (place `cmd_record_event` after `cmd_dispose` :634, `query_loop_events` after `query_spend` :719):

```python
LOOP_EVENTS = ("created", "imported", "installed", "uninstalled", "paused", "resumed")

def cmd_record_event(args) -> int:
    if args.event not in LOOP_EVENTS:
        print(f"unknown event {args.event!r} (allowed: {', '.join(LOOP_EVENTS)})", file=sys.stderr)
        return 1
    if args.detail is not None:
        try:
            json.loads(args.detail)
        except ValueError:
            print("detail must be valid JSON", file=sys.stderr)
            return 1
    conn = connect(args.root); init_db(conn)
    with conn:
        conn.execute(
            "INSERT INTO loop_events(loop_name, event, actor, ts, detail) VALUES (?,?,?,?,?)",
            (args.loop, args.event, args.actor, now_iso(), args.detail),
        )
    return 0

def query_loop_events(conn, args):
    sql = "SELECT * FROM loop_events"
    params = []
    if args.loop:
        sql += " WHERE loop_name = ?"; params.append(args.loop)
    sql += " ORDER BY ts DESC, id DESC LIMIT ?"; params.append(args.limit)
    return _rows_to_dicts(conn.execute(sql, params).fetchall())
```

Wire parser (in `build_parser` after `disp_p`):

```python
    ev_p = sub.add_parser("record-event")
    ev_p.add_argument("--root", default=_default_root())
    ev_p.add_argument("--loop", required=True)
    ev_p.add_argument("--event", required=True)
    ev_p.add_argument("--actor", required=True)
    ev_p.add_argument("--detail", default=None)
```

Add `"record-event": cmd_record_event` to the dispatch dict and `"loop-events": query_loop_events` to the query-name dispatch in `cmd_query`.

- [ ] **Step 4: Run:** `python3 -m unittest tests.test_db -v` → PASS.

- [ ] **Step 5: Amend INTERFACES** §3: append the table DDL + the two CLI lines to the `db.py` surface block, marked `(Amendment 2 — 2026-07-30)`, with the note: "`validate` records no event (audit spam); events kept forever; orphaned events for deleted loops are historical record."

- [ ] **Step 6: Full suite, commit:** `bash tests/run-tests.sh` → green. `git commit -am "feat(db): loop_events lifecycle audit table (Amendment 2)" && git push`

### Task 3: lifecycle verbs record events; global `--actor`

**Files:**
- Modify: `bin/loopctl` (common parser ~:1023, `cmd_new` :636, `cmd_install` :788, `cmd_uninstall` :885, `cmd_pause`/`cmd_resume` :923-930)
- Test: `tests/test_loopctl.py`

**Interfaces:**
- Consumes: `db.py record-event` (Task 2).
- Produces: every lifecycle verb accepts `--actor <str>` (default `os.environ.get("USER", "unknown")`); helper `_record_event(root, loop, event, actor, detail: dict | None)`.

- [ ] **Step 1: Failing tests** (append to `tests/test_loopctl.py`, following its subprocess/tmp-root conventions):

```python
    def test_new_records_created_event_with_default_actor(self):
        root = self._scaffold_root()
        self._loopctl(root, "new", "evt-loop", "--type", "agent", "--engine", "codex")
        events = self._db_query_json(root, "loop-events", loop="evt-loop")
        self.assertEqual(events[0]["event"], "created")
        self.assertEqual(events[0]["actor"], os.environ.get("USER", "unknown"))

    def test_actor_flag_overrides(self):
        root = self._scaffold_root()
        self._loopctl(root, "new", "evt-loop2", "--type", "agent", "--engine", "codex",
                      "--actor", "claude/testproj")
        events = self._db_query_json(root, "loop-events", loop="evt-loop2")
        self.assertEqual(events[0]["actor"], "claude/testproj")

    def test_pause_resume_record_events(self):
        root = self._scaffold_root_with_valid_loop("evt3")
        self._loopctl(root, "pause", "evt3")
        self._loopctl(root, "resume", "evt3")
        names = [e["event"] for e in self._db_query_json(root, "loop-events", loop="evt3")]
        self.assertEqual(names[:2], ["resumed", "paused"])  # newest first
```

- [ ] **Step 2: Verify failure** (`--actor` unknown flag / no events rows).

- [ ] **Step 3: Implement.** Add to the shared `common` parser: `common.add_argument("--actor", default=os.environ.get("USER", "unknown"))`. Helper next to the existing `_db_query`:

```python
def _record_event(root, loop, event, actor, detail=None):
    argv = [sys.executable, os.path.join(root_bin(root), "db.py"), "record-event",
            "--root", root, "--loop", loop, "--event", event, "--actor", actor]
    if detail:
        argv += ["--detail", json.dumps(detail)]
    subprocess.run(argv, check=False)  # best-effort: an audit write never fails the verb
```

(If `bin/loopctl` imports `db` as a module the way it loads the dashboard module — follow that pattern instead of subprocess; match `_db_query`'s existing mechanism.) Call sites: end of successful `cmd_new` → `("created", detail={"type": args.type, "engine": args.engine})`; successful `cmd_install` (after kickstart-verify passes) → `installed`; `cmd_uninstall` → `uninstalled`; `cmd_pause`/`cmd_resume` → `paused`/`resumed`.

- [ ] **Step 4: Run:** `python3 -m unittest tests.test_loopctl -v` → PASS.

- [ ] **Step 5: Amend INTERFACES** §8: `--actor` added to global flags line; event-recording sentence per verb, marked `(Amendment 2 — 2026-07-30)`.

- [ ] **Step 6: Full suite, commit + push:** `"feat(loopctl): lifecycle events + --actor (Amendment 2)"`

### Task 4: `list --tag` filter; tags + provenance in list/status JSON

**Files:**
- Modify: `bin/loopctl` (`cmd_list` :713, `cmd_status` :736, parser wiring ~:1038)
- Test: `tests/test_loopctl.py`

**Interfaces:**
- Produces: `loopctl list --tag project:x` (exact match); `list --json` rows gain `"tags": [...]`; `status --json` rows gain `"tags"` and `"provenance": {"event", "actor", "ts"} | None` (latest `created`/`imported` event).

- [ ] **Step 1: Failing tests:**

```python
    def test_list_tag_filter_exact(self):
        root = self._scaffold_root_with_valid_loop("tagged", extra_conf='tags="project:x"')
        self._scaffold_valid_loop(root, "untagged")
        rows = json.loads(self._loopctl(root, "list", "--tag", "project:x", "--json"))
        self.assertEqual([r["name"] for r in rows], ["tagged"])
        rows = json.loads(self._loopctl(root, "list", "--tag", "project", "--json"))
        self.assertEqual(rows, [])  # exact match, not substring

    def test_status_json_includes_tags_and_provenance(self):
        root = self._scaffold_root_with_valid_loop("tagged", extra_conf='tags="project:x"')
        self._loopctl(root, "new", "fresh", "--type", "agent", "--engine", "codex",
                      "--actor", "claude/t")
        rows = json.loads(self._loopctl(root, "status", "fresh", "--json"))
        self.assertEqual(rows[0]["provenance"]["actor"], "claude/t")
        self.assertEqual(rows[0]["provenance"]["event"], "created")
```

- [ ] **Step 2: Verify failure.**

- [ ] **Step 3: Implement.** `cmd_list`: add `"tags": conf.get("tags") or []` to each row; after building rows, `if args.tag: rows = [r for r in rows if args.tag in r["tags"]]`; parser: `list_p = sub.add_parser("list", parents=[common]); list_p.add_argument("--tag", default=None)` (replacing the bare `sub.add_parser("list", ...)` line at :1038). Table output gains a `tags` column (comma-joined). `cmd_status`: per row add tags, and:

```python
        ev = _db_query(args.root, "loop-events", loop=name, limit=10)
        prov = next((e for e in ev if e["event"] in ("created", "imported")), None)
        row["provenance"] = ({"event": prov["event"], "actor": prov["actor"], "ts": prov["ts"]}
                             if prov else None)
```

- [ ] **Step 4: Run:** PASS. **Step 5:** INTERFACES §8 verb-table line updated (`list [--tag]`), marked Amendment 2. **Step 6:** Full suite, commit + push: `"feat(loopctl): tag filter + provenance in status (Amendment 2)"`

### Task 5: dashboard — tag chips, provenance line, recent-events strip

**Files:**
- Modify: `dashboard/generate.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `loop_events` rows (direct sqlite read is permitted — INTERFACES §3), `conf["tags"]`.
- Produces: per-loop section renders `<span class="tag">project:x</span>` chips; a provenance line `imported from <source> by <actor>, <date>`; global strip `<section id="recent-events">` (last 15 events); a client-side tag filter `<select id="tag-filter">` that hides non-matching loop rows/sections (vanilla JS, inline — the page stays a self-contained single file, no network).

- [ ] **Step 1: Failing tests** (test_dashboard.py builds a temp root, inserts sqlite rows, runs generate, asserts on the HTML string — follow its existing helpers):

```python
    def test_tags_and_provenance_render(self):
        root = self._root_with_loop("tagged", conf_extra='tags="project:x"')
        self._insert_event(root, "tagged", "imported", "claude/maguyva",
                           detail='{"source_skill": "~/.claude/skills/seo-audit"}')
        html = self._generate(root)
        self.assertIn('class="tag"', html)
        self.assertIn("project:x", html)
        self.assertIn("claude/maguyva", html)
        self.assertIn("seo-audit", html)

    def test_recent_events_strip(self):
        root = self._root_with_loop("l1")
        self._insert_event(root, "l1", "created", "tester")
        html = self._generate(root)
        self.assertIn('id="recent-events"', html)
        self.assertIn("created", html)
```

- [ ] **Step 2: Verify failure.** **Step 3: Implement** in generate.py: a `load_loop_events(conn, limit=15)` + per-loop latest created/imported lookup; render chips next to the loop name in both the fleet row and the per-loop section; events strip under the existing top strip; tag filter select populated from the union of tags, JS `onchange` toggles `display:none` on rows/sections lacking the tag (match by `data-tags="a b c"` attribute). Keep the existing visual language (dense, dark-friendly).

- [ ] **Step 4: Run** `python3 -m unittest tests.test_dashboard -v` → PASS. **Step 5:** INTERFACES §10 bullet: "Tags + provenance + recent-events strip (Amendment 2 — 2026-07-30): rendered from loop.conf tags + loop_events; tag filter is client-side only." **Step 6:** Full suite, commit + push: `"feat(dashboard): tags, provenance, events strip (Amendment 2)"`

---

## Phase 2 — Live-run visibility

### Task 6: running/overdue trichotomy + start-of-run non-blocking regen

**Files:**
- Modify: `dashboard/generate.py` (status-light resolution), `bin/run-loop.sh` (immediately after `db.py start-run` :390)
- Test: `tests/test_dashboard.py`, `tests/test_runner.sh`

**Interfaces:**
- Produces rendering rule: row with `finished_at IS NULL`: age ≤ `timeout_s` → **running** (pulsing badge, not a failure); age in `(timeout_s, timeout_s+120]` → **overdue** (amber, "still running, past timeout"); age > `timeout_s+120` → **died** (existing rule, unchanged).

- [ ] **Step 1: Failing tests:**

```python
    def test_run_states_trichotomy(self):
        root = self._root_with_loop("l1", conf_extra="timeout_s=60")
        self._insert_unfinished_run(root, "l1", started_secs_ago=10)
        self.assertIn("running", self._generate(root))
        self._reset_runs(root); self._insert_unfinished_run(root, "l1", started_secs_ago=90)
        self.assertIn("overdue", self._generate(root))
        self._reset_runs(root); self._insert_unfinished_run(root, "l1", started_secs_ago=300)
        self.assertIn("died", self._generate(root))
```

Shell (append a case to `tests/test_runner.sh`, using `runner_test_helpers.sh` + fake engine): hold `state/locks/_dashboard.lock` with a background `bin/lock.py acquire --name _dashboard` process, run the loop, assert the run row is `completed` (a held dashboard lock never fails or delays the run) — then release and assert a normal run regenerates.

- [ ] **Step 2: Verify failures.** **Step 3: Implement.** generate.py: in the died-run resolution, split by age vs `timeout_s` (already loaded from loop.conf) per the trichotomy; running/overdue count toward neither `needs_attention` (running) nor harness-problem (overdue is amber attention only). run-loop.sh, directly after the `start-run` line:

```bash
# (Amendment 2) best-effort "running now" regen — NEVER blocks or fails the run
"$PY" "$ROOT/bin/lock.py" check --name _dashboard >/dev/null 2>&1 && \
  "$PY" "$ROOT/dashboard/generate.py" --root "$ROOT" >/dev/null 2>&1 || true
```

(`lock.py check` exits 0 only when free — a held lock skips the regen entirely; the end-of-run regen at step 7 keeps its `--wait-s 30`.)

- [ ] **Step 4: Run both test files** → PASS. **Step 5:** INTERFACES §4.1 step 3 gains the regen sentence; §10 died-run bullet becomes the trichotomy, both marked Amendment 2. **Step 6:** Full suite, commit + push: `"feat: live-run visibility, non-blocking start regen (Amendment 2)"`

---

## Phase 3 — Per-finding paste blocks

### Task 7: disposition commands + paste-into-your-agent block per finding

**Files:**
- Modify: `dashboard/generate.py` (per-loop findings list section)
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: open findings from sqlite + the suppression-filtered `reports/<name>/latest.json` (existing §10 source rule — `detail` lives ONLY in latest.json, not the findings table).
- Produces: per unsuppressed open finding, a collapsed `<details class="finding-handoff">` containing a deterministic template. Template MUST NOT contain the word "approve".

- [ ] **Step 1: Failing tests:**

```python
    def test_finding_paste_block(self):
        root = self._root_with_finding("l1", "repo:no-remote", severity="warn",
                                       detail="23 unpushed commits")
        html = self._generate(root)
        self.assertIn("finding-handoff", html)
        self.assertIn("repo:no-remote", html)
        self.assertIn("23 unpushed commits", html)      # detail from latest.json
        self.assertIn("loopctl dismiss l1 repo:no-remote --note", html)
        self.assertNotIn("approve", html.lower())

    def test_paste_block_includes_root_when_nondefault(self):
        root = self._root_with_finding("l1", "a:b")      # temp root ≠ ~/projects/loops
        self.assertIn(f"--root {root}", self._generate(root))
```

- [ ] **Step 2: Verify failure.** **Step 3: Implement.** Template function (values HTML-escaped; `detail` clamped 2 KiB):

```python
def finding_handoff_text(loop, f, root, root_flag):
    return (
        f"A scheduled report-only loop ('{loop}') flagged this finding "
        f"(id {f['finding_id']}, severity {f['severity']}, seen {f['times_seen']}x "
        f"since {f['first_seen_at'][:10]}):\n\n"
        f"  {f['title']}\n\n  {f.get('detail', '')}\n\n"
        f"Context files: reports/{loop}/latest.md and state/runs/ under {root}.\n"
        f"The loop only reports; decide and act in YOUR context and permissions.\n"
        f"If instead this should stop being reported, suppress it:\n"
        f"  {root}/bin/loopctl dismiss {loop} {f['finding_id']}{root_flag} --note \"...\"\n"
        f"  {root}/bin/loopctl snooze {loop} {f['finding_id']}{root_flag} --until YYYY-MM-DD\n"
    )
```

`root_flag = f" --root {root}" if os.path.realpath(root) != os.path.realpath(os.path.expanduser("~/projects/loops")) else ""`. Merge `detail` by `finding_id` from `latest.json`'s findings array; sqlite supplies recurrence fields. Render inside `<details><summary>hand to an agent</summary><pre>…</pre></details>` next to the existing disposition text.

- [ ] **Step 4: Run** → PASS. **Step 5:** INTERFACES §10 findings-list bullet gains the paste-block sentence (sources, no-approve rule), marked Amendment 2. **Step 6:** Full suite, commit + push: `"feat(dashboard): per-finding agent handoff blocks (Amendment 2)"`

---

## Phase 4 — `loopctl import --analyze`

### Task 8: skill parser (`bin/skill_import.py`, part 1)

**Files:**
- Create: `bin/skill_import.py`
- Create: `tests/fixtures/skills/clean-check/SKILL.md`, `tests/fixtures/skills/interactive/SKILL.md`, `tests/fixtures/skills/mutating/SKILL.md`, `tests/fixtures/skills/needs-creds/SKILL.md`, `tests/fixtures/skills/mcp-only/SKILL.md`
- Test: `tests/test_skill_import.py`

**Interfaces:**
- Produces: `parse_skill(path: str) -> dict` with keys `{"skill_dir", "frontmatter": dict[str,str], "body": str, "files": [{"relpath","text"}], "notes": [str], "sha256": str}`. Raises `SkillParseError(msg)` only when no SKILL.md is found. `ANALYZER_VERSION = "1"`.
- Layout matrix: arg is a dir containing `SKILL.md` OR a direct path to a `SKILL.md`. Bundled files read: ≤ 50 files, each ≤ 256 KiB, symlinks NOT followed, binary (NUL byte in first 8 KiB) skipped — all skips appended to `notes`.
- Frontmatter: flat `key: value` lines between leading `---` fences ONLY (stdlib rule — no YAML lib); a line that isn't flat `key: value` makes the whole frontmatter parse degrade to `{}` + a note ("frontmatter not flat key:value; kept as body text").
- `sha256`: hex digest over SKILL.md text + each read file's relpath+text, sorted by relpath.

- [ ] **Step 1: Write the five fixtures.** `clean-check/SKILL.md`:

```markdown
---
name: repo-hygiene-check
description: Check ~/projects repos for dirty worktrees and unpushed commits
---

# Repo hygiene check

Run `git -C <repo> status --porcelain` for each repo under ~/projects.
Run `git -C <repo> log --oneline @{u}.. 2>/dev/null | wc -l` to count unpushed commits.
Summarize repos that are dirty or unpushed in a short report with one line per repo.
```

`interactive/SKILL.md` body includes: `Ask the user which environment to check before proceeding.` `mutating/SKILL.md` body includes: `` Then run `git push` and `vercel deploy --prod` to ship the fix. `` `needs-creds/SKILL.md` body includes: `Requires STRIPE_API_KEY in the environment; call the Stripe API for yesterday's failed charges.` `mcp-only/SKILL.md` body includes: `Use mcp__playwright__browser_navigate to load the page and mcp__playwright__browser_snapshot to read it.` Each with a flat frontmatter block (name + description).

- [ ] **Step 2: Failing tests:**

```python
import unittest, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import skill_import

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "skills")

class TestParseSkill(unittest.TestCase):
    def test_dir_layout(self):
        s = skill_import.parse_skill(os.path.join(FIX, "clean-check"))
        self.assertEqual(s["frontmatter"]["name"], "repo-hygiene-check")
        self.assertIn("git", s["body"])
        self.assertEqual(len(s["sha256"]), 64)

    def test_bare_file_layout(self):
        s = skill_import.parse_skill(os.path.join(FIX, "clean-check", "SKILL.md"))
        self.assertEqual(s["frontmatter"]["name"], "repo-hygiene-check")

    def test_missing_skill_md_raises(self):
        with self.assertRaises(skill_import.SkillParseError):
            skill_import.parse_skill(tempfile.mkdtemp())

    def test_nested_frontmatter_degrades_with_note(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "SKILL.md"), "w") as f:
            f.write("---\nname: x\nmeta:\n  nested: true\n---\nbody\n")
        s = skill_import.parse_skill(d)
        self.assertEqual(s["frontmatter"], {})
        self.assertTrue(any("frontmatter" in n for n in s["notes"]))

    def test_binary_and_oversize_skipped_with_notes(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "SKILL.md"), "w") as f:
            f.write("---\nname: x\ndescription: y\n---\nbody\n")
        with open(os.path.join(d, "blob.bin"), "wb") as f:
            f.write(b"\x00\x01")
        with open(os.path.join(d, "big.txt"), "w") as f:
            f.write("a" * (257 * 1024))
        s = skill_import.parse_skill(d)
        self.assertEqual([x["relpath"] for x in s["files"]], [])
        self.assertEqual(len(s["notes"]), 2)
```

- [ ] **Step 3: Verify failure** (`ModuleNotFoundError: skill_import`), then **Step 4: implement** `parse_skill` exactly to the interface above (walk with `os.walk(followlinks=False)`, sort relpaths, apply caps, hashlib.sha256). **Step 5:** `python3 -m unittest tests.test_skill_import -v` → PASS. Register the new test file in `tests/run-tests.sh` if it enumerates files explicitly. **Step 6:** Full suite, commit + push: `"feat(import): skill parser with layout matrix + caps"`

### Task 9: analyzer — buckets, flags, floor-first axes, precheck proposal, naming

**Files:**
- Modify: `bin/skill_import.py`
- Test: `tests/test_skill_import.py`

**Interfaces:**
- Produces: `analyze(skill: dict) -> dict` returning:

```python
{
  "analyzer_version": "1", "skill_sha256": "...",
  "proposed_name": "repo-hygiene-check",          # sanitized, ^[a-z][a-z0-9-]{1,40}$
  "type": "agent", "engine": "codex",             # engine may be "claude" (Claude-idiom)
  "axes": {"perm_fs_write": "report_only", "perm_network": "none",
            "perm_local_exec": "none", "perm_remote_mutation": "none"},  # ALWAYS the floor
  "flags": {"interactivity": bool, "mutation": bool, "mcp": bool,
             "credentials": bool, "iteration": bool, "network": bool},
  "blocked": bool,                                  # credentials or mcp-without-cli-equivalent
  "rubric": {"q1_purpose": {"bucket": "answered", "value": "..."} , ...
             "q8_finding_identity": {"bucket": "missing"}, ...},  # all 11, ids q1_purpose..q11_budget
  "precheck_proposal": ["# [read-only?] git -C <repo> status --porcelain", ...],  # EVERY line starts with '#'
  "answers_needed": [{"question_id": "q4_cadence", "prompt": "...", "context": "...",
                       "options": [{"id": "daily_morning", "label": "daily:07:30"}, ...],
                       "suggested_answerer": "user"}, ...],
  "notes": [...],
}
```

- Detection heuristics (regex, case-insensitive, over body + files' text):
  - interactivity: `ask the user|askuserquestion|wait for (approval|confirmation)|prompt the user`
  - mutation: `\bgit push\b|\bdeploy\b|\bnpm publish\b|\bsend (an? )?(email|sms|message)\b|\bpost to\b|gh pr create|\brm -rf\b`
  - mcp: `\bmcp__[a-z0-9_]+__[a-z0-9_]+\b|\bMCP\b`
  - credentials: `api[_ -]?key|oauth|bearer token|[A-Z][A-Z0-9_]{4,}_(KEY|TOKEN|SECRET)|credentials?\b`
  - iteration: `until (it|the test|tests) pass|retry until|keep trying|iterate until`
  - network: `\bcurl\b|\bhttps?://|api call|fetch\b|webhook`
  - Claude-idiom (engine=claude): `mcp__|AskUserQuestion|\.claude/|allowed-tools`
- Read-only precheck annotation: first token of a candidate command line ∈ `{git status, git log, git diff, ls, find, grep, rg, wc, cat, curl, head, tail, stat, du, df}` scoped-read forms → `# [read-only?] <line>`; anything else → `# [MUTATING — do not enable] <line>`. Candidate lines = backtick-quoted shell snippets and fenced `bash` blocks in the body.
- Blocked rule: `credentials` → blocked; `mcp` → blocked unless the body also names a CLI equivalent (heuristic: `curl` or a known CLI appears in the same file). `answers_needed` always includes `q4_cadence`, `q8_finding_identity`, `q9_semantics`, `q10_metrics`, `q11_budget` (never statically answerable); plus a per-axis raise question ONLY when `flags.network or flags.mutation` (suggested_answerer "user", with a drafted justification string in `context`).

- [ ] **Step 1: Failing tests:**

```python
class TestAnalyze(unittest.TestCase):
    def _an(self, fixture):
        return skill_import.analyze(skill_import.parse_skill(os.path.join(FIX, fixture)))

    def test_clean_check_shape(self):
        r = self._an("clean-check")
        self.assertEqual(r["axes"]["perm_network"], "none")            # floor-first
        self.assertFalse(r["blocked"])
        self.assertEqual(r["rubric"]["q8_finding_identity"]["bucket"], "missing")
        self.assertTrue(all(l.startswith("#") for l in r["precheck_proposal"]))
        self.assertTrue(any("[read-only?] " in l for l in r["precheck_proposal"]))
        needed = {a["question_id"] for a in r["answers_needed"]}
        self.assertLessEqual({"q4_cadence", "q8_finding_identity"}, needed)

    def test_interactive_flagged(self):
        self.assertTrue(self._an("interactive")["flags"]["interactivity"])

    def test_mutating_flagged_and_annotated(self):
        r = self._an("mutating")
        self.assertTrue(r["flags"]["mutation"])
        self.assertTrue(any("MUTATING" in l for l in r["precheck_proposal"]))
        self.assertEqual(r["axes"]["perm_remote_mutation"], "none")    # floor stays

    def test_credentials_blocked(self):
        self.assertTrue(self._an("needs-creds")["blocked"])

    def test_mcp_only_blocked_and_claude_idiom(self):
        r = self._an("mcp-only")
        self.assertTrue(r["blocked"]); self.assertEqual(r["engine"], "claude")

    def test_name_sanitized(self):
        s = skill_import.parse_skill(os.path.join(FIX, "clean-check"))
        s["frontmatter"]["name"] = "My_Big Skill!! With A Really Long Name Overflowing Everything"
        n = skill_import.analyze(s)["proposed_name"]
        self.assertRegex(n, r"^[a-z][a-z0-9-]{1,40}$")
```

- [ ] **Step 2: Verify failure**, **Step 3: implement** (`_detect_flags`, `_propose_precheck`, `_sanitize_name` — lowercase, `[_ ]+`→`-`, strip non `[a-z0-9-]`, collapse `-{2,}`, trim to 41 chars, prefix `x-` if it doesn't start with a letter), **Step 4: run** → PASS. **Step 5:** Full suite, commit + push: `"feat(import): static analyzer with floor-first axes"`

### Task 10: `loopctl import --analyze` wiring

**Files:**
- Modify: `bin/loopctl` (new `cmd_import`, subparser after `findings_p` ~:1057)
- Test: `tests/test_loopctl.py`

**Interfaces:**
- Produces: `loopctl import <skill-path> --analyze [--json]`. `--json` prints the full `analyze()` dict. Human form prints: header (name/type/engine/blocked), a rubric table (question, bucket, value-or-—), flags line, the commented precheck proposal, and a numbered "answers needed" list. Exit 0 on success (blocked skills still analyze fine), 1 on `SkillParseError`, 2 on usage.

- [ ] **Step 1: Failing tests:**

```python
    def test_import_analyze_json(self):
        root = self._scaffold_root()
        out = self._loopctl(root, "import", os.path.join(FIX, "clean-check"), "--analyze", "--json")
        r = json.loads(out)
        self.assertEqual(r["analyzer_version"], "1")
        self.assertIn("answers_needed", r)

    def test_import_analyze_missing_path_exits_1(self):
        root = self._scaffold_root()
        rc = self._loopctl_rc(root, "import", "/nonexistent", "--analyze")
        self.assertEqual(rc, 1)
```

- [ ] **Step 2: Verify failure**, **Step 3: implement** (`import skill_import` beside the existing loopconf import; subparser: positional `skill_path`, `--analyze` / `--apply` mutually exclusive group, `--answers`, `--name`, `--overwrite`; `--apply` prints "not implemented yet" exit 2 until Task 12), **Step 4: run** → PASS. **Step 5:** INTERFACES §8 verb list gains `import` (Amendment 2). **Step 6:** Full suite, commit + push: `"feat(loopctl): import --analyze"`

### Task 11: `docs/SKILL_IMPORT.md` + cross-links

**Files:**
- Create: `docs/SKILL_IMPORT.md`
- Modify: `docs/LOOP_AUTHORING.md` (§7 build process — `import` as alternate entry beside `new`)

- [ ] **Step 1: Write `docs/SKILL_IMPORT.md`** with these sections (content transcribed from the design doc §4, kept in sync with the code shipped in Tasks 8–10): (1) What import does and does not do (static, zero-token, gates unchanged, reshaping quality is the supervising agent's job); (2) the eleven rubric ids `q1_purpose`…`q11_budget` mapped to LOOP_AUTHORING §2; (3) the four buckets; (4) the reshaping rules verbatim (interactivity→findings; mutation→propose-only; MCP→CLI/curl or blocked; iterate-until-success→single-shot; current-repo→explicit workdir) plus the environmental teachings (metrics-as-string, brace-free payloads, §4.5 effective-status/empty-findings-for-red); (5) the blocked-outcomes table (creds/MCP/no-steps); (6) the trust rule: precheck proposals are commented, uncommenting is deliberate, precheck is trusted UNSANDBOXED bash; (7) answers.json shape with a filled example (analyzer_version, skill_sha256, answers map, per-answer provenance); (8) the manual recipe (how to do all of this without the tool). 
- [ ] **Step 2:** In `docs/LOOP_AUTHORING.md` §7 step 2, add: `(or: loopctl import <skill-path> --analyze / --apply — see docs/SKILL_IMPORT.md — when converting an existing Agent Skill)`.
- [ ] **Step 3:** Full suite (docs-only, still run it), commit + push: `"docs: SKILL_IMPORT.md import recipe + authoring cross-link"`

---

## Phase 5 — `import --apply` + install precondition

### Task 12: `--apply` scaffolding

**Files:**
- Modify: `bin/skill_import.py` (add `apply()`), `bin/loopctl` (`cmd_import` apply branch)
- Test: `tests/test_skill_import.py`, `tests/test_loopctl.py`

**Interfaces:**
- Produces: `skill_import.apply(skill, analysis, answers: dict, dest_dir: str) -> list[str]` (paths written). `answers` = `{"analyzer_version": "1", "skill_sha256": "...", "answers": {"q4_cadence": "daily:07:30", "q8_finding_identity": "<repo>:unpushed", ...}, "provenance": {"q4_cadence": "user", ...}, "acknowledge_blocked": false}`.
- Rules: `skill_sha256` mismatch vs re-parsed skill → refuse (exit 1, "stale answers — re-run --analyze"). `blocked and not acknowledge_blocked` → refuse. `blocked and acknowledge_blocked` → scaffold with `schedule=manual` and a `## BLOCKED — read before scheduling` section in SPEC.md naming the blockers. Existing `loops.d/<name>` → refuse unless `--overwrite`. Writes: `loop.conf` (answers + floor axes + tags), `SPEC.md` (eleven sections from rubric+answers; unanswered stay `[FILL: ...]`), `prompt.md` (reshaped body + contract sections + `## Finding identity` from q8 + the three findings rules + metrics-as-string instruction — reuse the `loopctl new` template as the frame, splicing the skill body into the task section), `precheck.sh` (safe template + commented proposal lines), `dashboard.json` (panels from q10 answer, else `{"panels": []}`). Then `_record_event(root, name, "imported", actor, {"source_skill": path, "skill_sha256": ..., "answers_provenance": ..., "overwrite": bool})`.

- [ ] **Step 1: Failing tests** (the load-bearing ones):

```python
CLEAN_ANSWERS = {
  "analyzer_version": "1", "skill_sha256": None,   # filled by helper from analysis
  "answers": {
    "q1_purpose": "Report dirty/unpushed repos; done per-firing = report written; cross-run done = repo becomes clean",
    "q4_cadence": "daily:07:30", "q5_scope": "~/projects only; exclude maguyva",
    "q8_finding_identity": "<repo-dir-name>:<condition> where condition is dirty|unpushed",
    "q9_semantics": "ok=all clean; warn=any dirty/unpushed; alert=never",
    "q10_metrics": '{"panels":[{"title":"Dirty","metric":"repos.dirty","type":"number"}]}',
    "q11_budget": "engine default model; ~1k tokens; retry 1; timeout 300",
  },
  "provenance": {"q4_cadence": "user"}, "acknowledge_blocked": False,
}

    def test_apply_scaffold_passes_validate(self):
        root = self._scaffold_root()
        self._write_answers(root, "answers.json", CLEAN_ANSWERS, fixture="clean-check")
        self._loopctl(root, "import", FIX + "/clean-check", "--apply",
                      "--answers", root + "/answers.json", "--actor", "claude/t")
        rc = self._loopctl_rc(root, "validate", "repo-hygiene-check")
        self.assertEqual(rc, 0)
        events = self._db_query_json(root, "loop-events", loop="repo-hygiene-check")
        self.assertEqual(events[0]["event"], "imported")
        pre = open(root + "/loops.d/repo-hygiene-check/precheck.sh").read()
        for line in pre.splitlines():
            if "git " in line:
                self.assertTrue(line.lstrip().startswith("#"))   # proposals stay commented

    def test_apply_stale_hash_refused(self):
        # answers carry a wrong sha256 → exit 1, nothing written
    def test_apply_collision_refused_without_overwrite(self):
        # second apply without --overwrite → exit 1; with --overwrite → 0 + detail records it
    def test_apply_blocked_needs_acknowledgement(self):
        # needs-creds: refuse; with acknowledge_blocked=true → schedule=manual + SPEC warning
    def test_apply_dangerous_combo_still_fails_validate(self):
        # post-scaffold, hand-edit conf to perm_remote_mutation=allowlist w/o justification →
        # loopctl validate exit 1 (import grants no immunity)
```

(Write the four sketched tests out fully in the same style as the first.)

- [ ] **Step 2: Verify failures**, **Step 3: implement `apply()`** per the interface (build SPEC/prompt from the same template files `loopctl new` uses — read them from `bin/loopctl`'s template source rather than duplicating strings), **Step 4: run** → PASS. **Step 5:** INTERFACES §8: `import --apply` semantics paragraph (stale-hash, overwrite, blocked rules), Amendment 2. **Step 6:** Full suite, commit + push: `"feat(import): --apply scaffolding with answers contract"`

### Task 13: install run-first precondition

**Files:**
- Modify: `bin/loopctl` (`cmd_install` :788, before plist generation)
- Test: `tests/test_loopctl.py`

**Interfaces:**
- Produces: `install` refuses (exit 1, message `run 'loopctl run <name>' first — install requires a non-failed supervised run`) when the loop has zero runs with `runner_status IN ('completed','skipped-precheck')`. Applies to ALL loops (not only imports — the gauntlet was always the doctrine; now it's mechanical).

- [ ] **Step 1: Failing test:**

```python
    def test_install_refuses_without_prior_run(self):
        root = self._scaffold_root_with_valid_loop("fresh1", schedule="interval:15m")
        rc, err = self._loopctl_rc_err(root, "install", "fresh1")
        self.assertEqual(rc, 1)
        self.assertIn("loopctl run", err)
```

(Existing install tests already simulate a successful run via the fake engine / LOOPS_LAUNCHCTL stub — confirm they still pass, inserting a completed run row where needed.)

- [ ] **Step 2: Verify failure**, **Step 3: implement** in `cmd_install` after the validate gate:

```python
    ok_runs = [r for r in _db_query(root, "last-runs", loop=name, limit=50)
               if r.get("runner_status") in ("completed", "skipped-precheck")]
    if not ok_runs:
        print(f"refusing to install {name}: no non-failed supervised run recorded — "
              f"run 'loopctl run {name}' first", file=sys.stderr)
        return 1
```

- [ ] **Step 4: run** → PASS (fix any existing install tests by seeding a run row — that is the point of the change). **Step 5:** INTERFACES §8.1 gains precondition item 0 (Amendment 2); LOOP_AUTHORING §7 step 6 sentence updated. **Step 6:** Full suite, commit + push: `"feat(loopctl): install requires a prior non-failed run (Amendment 2)"`

### Task 14: end-to-end id-stability fixture (import → two runs)

**Files:**
- Test: `tests/test_skill_import_e2e.sh` (new; register in `tests/run-tests.sh` beside `test_examples.sh`)

- [ ] **Step 1: Write the shell test:** hermetic root; run `loopctl import fixtures/skills/clean-check --apply` with the canned answers; point the loop's engine at `engines/fake.sh` (same mechanism `test_examples.sh` uses) with a canned contract emitting findings `alpha:dirty`, `beta:unpushed`; `loopctl run` twice; assert via `db.py query open-findings`: same two `finding_id`s, `times_seen == 2`, no duplicates; assert promoted `latest.json` exists and `effective_status == "warn"`.
- [ ] **Step 2: Run it standalone** (`bash tests/test_skill_import_e2e.sh`) → PASS. **Step 3:** Full suite, commit + push: `"test(import): e2e id-stability across two runs"`

---

## Phase 6 — AXI polish

### Task 15: `status` aggregates + blanking fix + definitive empty states

**Files:**
- Modify: `bin/loopctl` (`cmd_status` :736, `cmd_list` :713, `cmd_findings` :938)
- Test: `tests/test_loopctl.py`

**Interfaces:**
- Produces: `status` (no name) prints a leading aggregate line `fleet: N loops · ok X · warn Y · alert Z · needs_attention W · spend7d $S` (computed from `db.py query loops-summary` + `spend`; `--json` gains a top-level `{"fleet": {...}, "loops": [...]}` envelope — table form unchanged below the aggregate line). Blanking fix: when a loop's latest row is `skipped-overlap` or unfinished, fall back to the newest row with a terminal `runner_status` for status/headline and add `"in_flight": true` when an unfinished row exists. Empty states: `list` with zero loops prints `0 loops (loops.d empty)`; `findings` with none prints `0 open findings for <loop>`; both exit 0.

- [ ] **Step 1: Failing tests:**

```python
    def test_status_aggregate_line(self):
        root = self._scaffold_root_with_valid_loop("s1")
        out = self._loopctl(root, "status")
        self.assertIn("fleet:", out.splitlines()[0])

    def test_status_falls_back_past_overlap_row(self):
        root = self._scaffold_root_with_valid_loop("s2")
        self._insert_run(root, "s2", runner_status="completed", effective_status="ok",
                         headline="all good")
        self._insert_run(root, "s2", runner_status="skipped-overlap")
        rows = json.loads(self._loopctl(root, "status", "s2", "--json"))["loops"]
        self.assertEqual(rows[0]["headline"], "all good")

    def test_findings_empty_state(self):
        root = self._scaffold_root_with_valid_loop("s3")
        out = self._loopctl(root, "findings", "s3")
        self.assertIn("0 open findings", out)
```

- [ ] **Step 2: Verify failure**, **Step 3: implement** (fallback: `last-runs --limit 10`, pick first row whose `runner_status` not in `("skipped-overlap",)` and `finished_at` set), **Step 4: run** → PASS. **Step 5:** INTERFACES §8: status/list output amendments. **Step 6:** Full suite, commit + push: `"feat(loopctl): AXI status aggregates, overlap fallback, empty states"`

### Task 16: bare `loopctl` → fleet summary, exit 0

**Files:**
- Modify: `bin/loopctl` (`main` :1072)
- Test: `tests/test_loopctl.py`

- [ ] **Step 1: Failing test:**

```python
    def test_bare_invocation_prints_summary_exit_0(self):
        root = self._scaffold_root_with_valid_loop("b1")
        rc, out = self._loopctl_rc_out(root)          # no verb (only --root)
        self.assertEqual(rc, 0)
        self.assertIn("fleet:", out)

    def test_unknown_verb_still_exit_2(self):
        rc = self._loopctl_rc(self._scaffold_root(), "frobnicate")
        self.assertEqual(rc, 2)
```

- [ ] **Step 2: Verify failure** (bare currently exits 2 with usage), **Step 3: implement** in `main`: `if args.verb is None: return cmd_status(args)` (argparse `dest="verb"` already yields None; ensure `common` defaults — root/from_dir/json — exist on the bare namespace by parsing `[]` through a parser that carries them; simplest: `sub.required = False` plus defaults applied when verb is None). Content-first: this IS live data, not help; `--help` unchanged. **Step 4: run** → PASS. **Step 5:** INTERFACES §8 exit-code note (bare = summary, exit 0 — Amendment 2). **Step 6:** Full suite, commit + push: `"feat(loopctl): content-first bare invocation"`

---

## Phase 7 — the `loops` skill

### Task 17: `skills/loops/SKILL.md`

**Files:**
- Create: `skills/loops/SKILL.md`
- Modify: `README.md` (one "Agent surface" paragraph pointing at the skill + SKILL_IMPORT.md)

- [ ] **Step 1: Write the skill.** Frontmatter `name: loops`, `description: Use when the user wants recurring checks/reports scheduled, mentions the loops dashboard, or repeatedly runs the same check/skill by hand — push it to the loops harness (report-only scheduled runner) via loopctl.` Body sections, in order: (1) What loops is — report-only, findings/dispositions, dashboard — one paragraph; (2) The surface: `loopctl` (bare = fleet summary; `list --tag`, `status --json`, `findings`), always pass `--actor "claude/<project>"`; (3) When to OFFER an import (user repeatedly runs a check-shaped workflow by hand) — and when NOT to (needs credentials/OAuth, must take actions, needs mid-run questions — explain propose-only reshaping instead); (4) Import walkthrough: `--analyze --json` → relay `answers_needed` to the user (options verbatim; answer `suggested_answerer:"agent"` items yourself from project context) → write answers.json → `--apply` → `validate` → `run` (read the report against ground truth!) → `install` (goes live on launchd; requires the prior run); (5) Conventions: tags `project:<name>`/`campaign:<name>`; loop naming follows the fleet's Japanese theme (see loops CLAUDE.md — e.g. loop-sensei); never uncomment a `[MUTATING — do not enable]` precheck line; (6) Where things live: `LOOPS_ROOT` default `~/projects/loops`, pass `--root` otherwise; dashboard at `dashboard/loops.html`; full recipe `docs/SKILL_IMPORT.md`. Install instruction in the skill header comment: `ln -s ~/projects/loops/skills/loops ~/.claude/skills/loops` (or copy).
- [ ] **Step 2:** README paragraph. **Step 3:** Full suite, commit + push: `"feat: distributable loops skill (agent front door)"`

### Task 18: closeout

- [ ] **Step 1:** `bash tests/run-tests.sh` full green; note the new test count.
- [ ] **Step 2:** Re-read `docs/SKILL_IMPORT_AND_AGENT_SURFACE_PLAN.md` §§3–6 against the shipped code; fix any doc/code drift IN THE DOCS (the design doc is rationale, INTERFACES is contract — both must match reality).
- [ ] **Step 3:** Update `CLAUDE.md` (loops) test count + one line under "Start here" pointing at `docs/SKILL_IMPORT.md`; update `docs/OPEN_THREADS_WARMSTART.md` if the paste-block work changes the approve→action framing (it should only reference it).
- [ ] **Step 4:** Final commit + push: `"chore: skill-import/agent-surface closeout — docs sync"`

---

## Self-review (done at write time)

- **Spec coverage:** design §3.1→Task 1/4/5; §3.2→Task 2/3; §3.3→Task 6; §3.4→Task 5/7; §4.1→Task 8/9/10; §4.3→Task 11; §4.2→Task 12; §2.2 precondition→Task 13; §4.2 success criterion→Task 14; §5.1→Task 15/16; §5.2→Task 17; §8 phases→plan order. Design §7 (futures) intentionally has no tasks.
- **Placeholders:** Task 12 Step 1 sketches four tests by name with one-line specs — the implementer writes them out in the shown style; all other code blocks are complete. Integration line numbers are anchors as of 2026-07-30 (`bin/loopctl` @ HEAD 9286ea6) — re-grep if drifted.
- **Type consistency:** `parse_skill`/`analyze`/`apply` signatures consistent across Tasks 8/9/10/12; event enum consistent across Tasks 2/3/12; rubric ids `q1_purpose`…`q11_budget` consistent across Tasks 9/11/12.
