# Report Pages (third output tier) Implementation Plan

**Status:** shipped 2026-07-30, live (`kagi-ban` installed `daily:07:40`; pages served over the
tailnet vhost). As-built deltas + what the live gauntlet changed: `## Shipped (as built)` at the
end of this file. The follow-up wave (decisions, backlog, leftovers) closed 2026-07-30 — nothing
outstanding; live state and the settled/do-not-relitigate list:
`docs/REPORT_PAGES_FOLLOWUP_WARMSTART.md`.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the optional report-page output tier to the loops harness (spec: `docs/REPORT_PAGES_PLAN.md`) and prove it by shipping `kagi-ban`, the av-audit exposure loop, as the first page-enabled loop.

**Architecture:** A page-enabled loop ships an executable `loops.d/<name>/render.sh` (the `precheck.sh` convention). After contract promotion the runner commits loop-private durable state, runs the renderer under a process-group timeout, gates the output through `bin/page_envelope.py` (envelope + self-containment + redaction-clean checks), and atomically promotes `reports/<name>/<stamp>.html` + `latest.html`. `dashboard/generate.py` links pages from rows and generates a `reports.html` screen. Serving is the existing Caddy tailnet vhost with path-scoped roots.

**Tech Stack:** bash 3.2-safe shell, Python 3 stdlib only, plain-bash + unittest hermetic tests (`tests/run-tests.sh`). No new dependencies.

## Global Constraints

- All paths `$HOME`-relative at runtime; scripts resolve `LOOPS_ROOT="${LOOPS_ROOT:-$HOME/projects/loops}"` (INTERFACES §0).
- macOS: no `flock`, no GNU `timeout`, no `sed -i` GNU form, no `date -d`; system bash is 3.2 (INTERFACES §0).
- Python stdlib only; bash + python3 only (INTERFACES §0).
- `state/`, `reports/`, `state/runs/` dirs `0700`; files in them `0600` (INTERFACES §0).
- Timestamps ISO-8601 UTC `Z`, second precision (INTERFACES §0).
- Page contract MUSTs (spec §2): self-contained single HTML file; exactly one `<script type="application/json" id="report-data">` envelope with required `meta.loop/run_id/generated_at/title/page_class`; deterministic renderer; declared inputs only; redaction-clean.
- Render failure NEVER changes run status or exit code (spec §1.6).
- Page size cap 8 MiB; render log cap 64 KiB; render timeout `min(timeout_s, 300)`, additive (spec §4.1, §1.8).
- Promotion order: dated `.html` first, then `latest.html`, both write-tmp-then-rename (spec §4.1).
- Retention keep-list gains `latest.html` explicitly (spec §4.2).
- Engine never writes HTML; renderers are precheck-class trusted code (spec §1.2).
- Every task ends with `bash tests/run-tests.sh` green and a commit + push (house rule: always push after commit).
- Hermetic tests never touch the real `state/`, `reports/`, launchd, network, or a real engine/`av` binary (INTERFACES §11).

---

### Task 1: INTERFACES.md Amendment 2 (report pages)

**Files:**
- Modify: `docs/INTERFACES.md`

**Interfaces:**
- Consumes: `docs/REPORT_PAGES_PLAN.md` (the spec; already committed).
- Produces: the frozen-contract deltas every later task implements. Later tasks cite "(Amendment 2)".

- [ ] **Step 1: Add the layout delta to §1**

In the §1 repository-layout block, add these lines (keep alphabetical-ish placement with the existing entries):

```
  bin/page_envelope.py             # report-page envelope check/extract (Amendment 2, §12)
  pagekit/kit.css                  # shared page kit (Amendment 2)
  pagekit/README.md
  pagekit/reference/               # sanitized benchmark fixture + rendered reference page
  loops.d/<name>/render.sh         # OPTIONAL page renderer (Amendment 2; executable = page-enabled)
  state/loop-data/<name>/          # loop-private durable state, 0700/0600 (Amendment 2)
  reports/<name>/YYYY-MM-DD-HHMM.html + latest.html   # promoted report pages (Amendment 2)
  dashboard/reports.html           # reports screen (Amendment 2, §10)
```

And in the gitignore note sentence, add `dashboard/reports.html` alongside `dashboard/loops.html`.

- [ ] **Step 2: Add §4.1 step 6.5 (render + loop-data commit)**

Insert after step 6 in §4.1, before step 7:

```markdown
6.5. **Loop-data commit + report page render (Amendment 2 — only for a run that promoted
   in step 6; failures in this step NEVER change runner_status, loop_status, or the exit
   code — the step-7 dashboard-failure precedent):**
   - **Loop-data commit:** every regular file in `state/runs/<id>/loop-data.commit/` is
     moved (per-file rename) into `state/loop-data/<name>/` (`0700` dir, `0600` files,
     created on demand). This is the ONLY write path into `state/loop-data/` — prechecks
     read the previous state from there but write candidates into the run dir, so a run
     that fails before promotion never consumes state (at-least-once semantics).
   - **Render:** if `loops.d/<name>/render.sh` exists and is executable, run it with cwd
     `loops.d/<name>/`, own process group, timeout `min(timeout_s, 300)` (additive to the
     engine budget; `duration_ms` includes it), env: `LOOP_NAME`, `RUN_ID`, `LOOPS_ROOT`,
     `OUT_DIR`, `LATEST_JSON` (absolute path to the promoted `reports/<name>/latest.json`),
     `LOOP_DATA_DIR` (absolute; read-only by convention), `PAGEKIT` (absolute `pagekit/`),
     `PAGE_OUT` (absolute `state/runs/<id>/page.html`). stdout+stderr →
     `state/runs/<id>/page-render.log`, capped 64 KiB, redacted via `bin/redact.py`.
   - **Promotion gate** (all via `bin/page_envelope.py check`, §12): exit 0 required —
     file exists, non-empty, UTF-8, ≤ 8 MiB, exactly one `#report-data` envelope, required
     meta fields typed and parseable, `meta.run_id` == RUN_ID, `meta.loop` == loop name,
     no-external-fetch heuristic passes, redaction-clean (redacting the page is a no-op).
   - Gate pass → promote by write-tmp-then-rename inside `reports/<name>/`: dated
     `<YYYY-MM-DD-HHMM>.html` FIRST, then `latest.html`; print
     `page promoted: reports/<name>/<dated>.html` to stdout. Gate fail / render error /
     timeout → no promotion, previous `latest.html` untouched, reason appended to
     `page-render.log`.
   - Runs that do not reach step 6.5 (skips, failures, watchdog silent-green, `--dry-run`)
     never render.
```

- [ ] **Step 3: Amend retention (§4.1 step 8)**

Change the step-8 sentence `latest.*` never pruned to: `latest.md`, `latest.json`, `latest.html` never pruned (the runner's keep-list names all three explicitly — Amendment 2).

- [ ] **Step 4: Amend §8 (loopctl) and §10 (dashboard)**

In §8 under `loopctl validate` additions, add:

```markdown
- **Amendment 2:** `render.sh` present but not executable = FAIL (absent = fine, loop is
  simply not page-enabled).
```

In §10, add a bullet:

```markdown
- **Report pages (Amendment 2):** the generator also writes `dashboard/reports.html`
  (same invocation; each output tmp+rename — per-file atomic, the pair is not). Row
  Report cells prefer `../reports/<name>/latest.html` (md link kept secondary); a page
  whose envelope `meta.run_id` ≠ the loop's latest promoted run (newest row with
  `runner_status='completed'` AND non-NULL `contract_path`) gets a `stale` badge. The
  reports screen lists every loop that is page-enabled or has pages on disk: totals chips
  + title + generated_at from the `latest.html` envelope (via `bin/page_envelope.py` —
  display HTML is never scraped; only `latest.html` is ever parsed), dated history from
  filenames only (capped 30), "no page yet" / "no meta" / historical markers per
  `docs/REPORT_PAGES_PLAN.md` §5.2. The §10 read-set gains the `latest.html` envelope.
```

- [ ] **Step 5: Add §12 (page envelope helper contract)**

Append a new section:

```markdown
## 12. `bin/page_envelope.py` — report-page envelope (Amendment 2)

Single stdlib-only implementation used by the runner gate AND `dashboard/generate.py`.

```
page_envelope.py check --file F [--expect-run-id ID] [--expect-loop L]
page_envelope.py meta  --file F
```

`check`: exit 0 = promotable; exit 1 with one reason per stderr line. Checks: readable,
non-empty, UTF-8, ≤ 8 MiB; exactly one `<script type="application/json" id="report-data">`
block; JSON parses; `meta.loop`, `meta.run_id`, `meta.generated_at` (`%Y-%m-%dT%H:%M:%SZ`),
`meta.title` (non-empty str), `meta.page_class` ∈ {snapshot, findings} all present;
`meta.totals` when present is a flat object with number or ≤64-char string values;
`--expect-*` mismatches; external-fetch heuristics (`<script src=`, `<link href="http`,
`<img src="http`, `<iframe`, `@import`, `url(http`); redaction-clean (`bin/redact.py` over
the full page text must be a no-op). `meta`: prints the parsed `meta` object as JSON to
stdout (exit 1 + reasons if extraction fails). Importable: `check_page(path, expect_run_id=None,
expect_loop=None) -> list[str]` (empty = pass) and `read_meta(path) -> dict | None`.
```

- [ ] **Step 6: Commit**

```bash
cd ~/projects/loops
git add docs/INTERFACES.md
git commit -m "docs(interfaces): Amendment 2 — report pages (render step, envelope helper, surfaces)"
git push
```

---

### Task 2: `bin/page_envelope.py` + tests

**Files:**
- Create: `bin/page_envelope.py`
- Test: `tests/test_page_envelope.py`

**Interfaces:**
- Consumes: `bin/redact.py` (`from redact import redact` — same directory).
- Produces: CLI `check`/`meta` + module functions `check_page(path, expect_run_id=None, expect_loop=None) -> list[str]`, `read_meta(path) -> dict | None`, constant `MAX_PAGE_BYTES = 8 * 1024 * 1024`. Task 4 shells out to `check`; Task 6 imports `read_meta`/`check_page` via the generator's `_load_module_from_path` helper.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_page_envelope.py`:

```python
"""Hermetic tests for bin/page_envelope.py (INTERFACES Amendment 2 §12)."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import page_envelope  # noqa: E402


def make_page(meta=None, body_extra="", envelope_count=1):
    meta = meta if meta is not None else {
        "loop": "demo",
        "run_id": "20260730T000000Z-demo-abc123",
        "generated_at": "2026-07-30T00:00:01Z",
        "title": "Demo page",
        "page_class": "snapshot",
        "totals": {"findings": 2},
    }
    envelope = json.dumps({"meta": meta, "data": {}}).replace("</", "<\\/")
    block = f'<script type="application/json" id="report-data">{envelope}</script>'
    return (
        "<!DOCTYPE html><html><head><title>t</title></head><body>"
        + body_extra
        + block * envelope_count
        + "</body></html>"
    )


class PageEnvelopeTests(unittest.TestCase):
    def write(self, content, mode="w"):
        fd, path = tempfile.mkstemp(suffix=".html")
        with os.fdopen(fd, mode) as f:
            f.write(content)
        self.addCleanup(os.remove, path)
        return path

    def test_valid_page_passes(self):
        path = self.write(make_page())
        self.assertEqual(page_envelope.check_page(path), [])

    def test_expect_run_id_and_loop_mismatch(self):
        path = self.write(make_page())
        errs = page_envelope.check_page(path, expect_run_id="other", expect_loop="nope")
        self.assertTrue(any("run_id" in e for e in errs))
        self.assertTrue(any("loop" in e for e in errs))

    def test_missing_envelope_fails(self):
        path = self.write("<html><body>no envelope</body></html>")
        self.assertTrue(page_envelope.check_page(path))

    def test_duplicate_envelope_fails(self):
        path = self.write(make_page(envelope_count=2))
        errs = page_envelope.check_page(path)
        self.assertTrue(any("exactly one" in e for e in errs))

    def test_missing_required_meta_field_fails(self):
        meta = {"loop": "demo", "run_id": "r", "generated_at": "2026-07-30T00:00:01Z",
                "page_class": "snapshot"}  # no title
        path = self.write(make_page(meta=meta))
        self.assertTrue(any("title" in e for e in page_envelope.check_page(path)))

    def test_bad_generated_at_fails(self):
        meta = {"loop": "demo", "run_id": "r", "generated_at": "yesterday",
                "title": "t", "page_class": "snapshot"}
        path = self.write(make_page(meta=meta))
        self.assertTrue(any("generated_at" in e for e in page_envelope.check_page(path)))

    def test_bad_page_class_fails(self):
        meta = {"loop": "demo", "run_id": "r", "generated_at": "2026-07-30T00:00:01Z",
                "title": "t", "page_class": "fancy"}
        path = self.write(make_page(meta=meta))
        self.assertTrue(any("page_class" in e for e in page_envelope.check_page(path)))

    def test_nested_totals_fails(self):
        meta = {"loop": "demo", "run_id": "r", "generated_at": "2026-07-30T00:00:01Z",
                "title": "t", "page_class": "snapshot", "totals": {"nested": {"x": 1}}}
        path = self.write(make_page(meta=meta))
        self.assertTrue(any("totals" in e for e in page_envelope.check_page(path)))

    def test_external_fetch_markup_fails(self):
        path = self.write(make_page(body_extra='<script src="https://cdn.example/x.js"></script>'))
        self.assertTrue(any("external" in e for e in page_envelope.check_page(path)))

    def test_plain_anchor_href_is_allowed(self):
        path = self.write(make_page(body_extra='<a href="https://docs.example/page">docs</a>'))
        self.assertEqual(page_envelope.check_page(path), [])

    def test_secret_value_fails_redaction_clean(self):
        path = self.write(make_page(body_extra="<pre>ghp_" + "a" * 24 + "</pre>"))
        self.assertTrue(any("redaction" in e for e in page_envelope.check_page(path)))

    def test_oversize_page_fails(self):
        path = self.write(make_page(body_extra="x" * (page_envelope.MAX_PAGE_BYTES + 1)))
        self.assertTrue(any("8 MiB" in e or "size" in e for e in page_envelope.check_page(path)))

    def test_read_meta_returns_meta(self):
        path = self.write(make_page())
        meta = page_envelope.read_meta(path)
        self.assertEqual(meta["loop"], "demo")
        self.assertEqual(meta["totals"]["findings"], 2)

    def test_read_meta_none_on_garbage(self):
        path = self.write("<html>nope</html>")
        self.assertIsNone(page_envelope.read_meta(path))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/projects/loops && python3 -m unittest tests.test_page_envelope -v`
Expected: FAIL/ERROR with `ModuleNotFoundError: No module named 'page_envelope'`.

- [ ] **Step 3: Implement `bin/page_envelope.py`**

```python
#!/usr/bin/env python3
"""bin/page_envelope.py — report-page envelope check/extract (Amendment 2, §12).

The SINGLE implementation used by both the runner's promotion gate and
dashboard/generate.py, so the two can never diverge. Stdlib only.

CLI:
  page_envelope.py check --file F [--expect-run-id ID] [--expect-loop L]
  page_envelope.py meta  --file F
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from redact import redact  # noqa: E402

MAX_PAGE_BYTES = 8 * 1024 * 1024
MAX_TOTALS_STR = 64

_ENVELOPE_RE = re.compile(
    r'<script\s+type="application/json"\s+id="report-data"\s*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
_REQUIRED_META = ("loop", "run_id", "generated_at", "title", "page_class")
_PAGE_CLASSES = ("snapshot", "findings")
# Heuristic external-fetch markers (spec §2.1). Plain <a href> anchors are
# deliberately NOT matched — navigation links are allowed, fetches are not.
_FETCH_PATTERNS = (
    ("script src", re.compile(r"<script[^>]*\bsrc\s*=", re.IGNORECASE)),
    ("link href to remote", re.compile(r"<link[^>]*\bhref\s*=\s*[\"']?https?:", re.IGNORECASE)),
    ("img src to remote", re.compile(r"<img[^>]*\bsrc\s*=\s*[\"']?https?:", re.IGNORECASE)),
    ("iframe", re.compile(r"<iframe", re.IGNORECASE)),
    ("css @import", re.compile(r"@import\b", re.IGNORECASE)),
    ("css url() to remote", re.compile(r"url\(\s*[\"']?https?:", re.IGNORECASE)),
)


def _load(path):
    """Returns (text, errors). Reads at most MAX_PAGE_BYTES + 1 bytes."""
    errors = []
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return None, [f"unreadable: {exc}"]
    if size == 0:
        return None, ["empty file"]
    if size > MAX_PAGE_BYTES:
        return None, [f"size {size} exceeds 8 MiB cap"]
    try:
        with open(path, "rb") as f:
            raw = f.read(MAX_PAGE_BYTES + 1)
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, ["not valid UTF-8"]
    except OSError as exc:
        return None, [f"unreadable: {exc}"]
    return text, errors


def _extract(text):
    """Returns (envelope_dict | None, errors)."""
    blocks = _ENVELOPE_RE.findall(text)
    if len(blocks) != 1:
        return None, [f"exactly one report-data envelope required, found {len(blocks)}"]
    try:
        envelope = json.loads(blocks[0].replace("<\\/", "</"))
    except (ValueError, TypeError) as exc:
        return None, [f"envelope JSON does not parse: {exc}"]
    if not isinstance(envelope, dict) or not isinstance(envelope.get("meta"), dict):
        return None, ["envelope must be an object with a meta object"]
    return envelope, []


def _validate_meta(meta):
    errors = []
    for field in _REQUIRED_META:
        value = meta.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"meta.{field} missing or not a non-empty string")
    gen = meta.get("generated_at")
    if isinstance(gen, str):
        try:
            datetime.strptime(gen, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            errors.append("meta.generated_at is not ISO8601Z (%Y-%m-%dT%H:%M:%SZ)")
    if isinstance(meta.get("page_class"), str) and meta["page_class"] not in _PAGE_CLASSES:
        errors.append(f"meta.page_class must be one of {_PAGE_CLASSES}")
    totals = meta.get("totals")
    if totals is not None:
        if not isinstance(totals, dict):
            errors.append("meta.totals must be a flat object")
        else:
            for key, value in totals.items():
                if isinstance(value, bool) or isinstance(value, (int, float)):
                    continue
                if isinstance(value, str) and len(value) <= MAX_TOTALS_STR:
                    continue
                errors.append(
                    f"meta.totals.{key} must be a number or a string of <= {MAX_TOTALS_STR} chars"
                )
    return errors


def check_page(path, expect_run_id=None, expect_loop=None):
    """Full promotion-gate check. Returns [] when promotable, else reasons."""
    text, errors = _load(path)
    if text is None:
        return errors
    envelope, extract_errors = _extract(text)
    errors.extend(extract_errors)
    if envelope is not None:
        meta = envelope["meta"]
        errors.extend(_validate_meta(meta))
        if expect_run_id is not None and meta.get("run_id") != expect_run_id:
            errors.append(f"meta.run_id {meta.get('run_id')!r} != expected {expect_run_id!r}")
        if expect_loop is not None and meta.get("loop") != expect_loop:
            errors.append(f"meta.loop {meta.get('loop')!r} != expected {expect_loop!r}")
    for label, pattern in _FETCH_PATTERNS:
        if pattern.search(text):
            errors.append(f"external fetch markup: {label}")
    if redact(text) != text:
        errors.append("redaction-clean check failed: page contains secret-shaped content")
    return errors


def read_meta(path):
    """Best-effort meta for display surfaces. None when unreadable/invalid."""
    text, _ = _load(path)
    if text is None:
        return None
    envelope, errors = _extract(text)
    if envelope is None or errors:
        return None
    return envelope["meta"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_check = sub.add_parser("check")
    p_check.add_argument("--file", required=True)
    p_check.add_argument("--expect-run-id", default=None)
    p_check.add_argument("--expect-loop", default=None)
    p_meta = sub.add_parser("meta")
    p_meta.add_argument("--file", required=True)
    args = parser.parse_args(argv)

    if args.cmd == "check":
        errors = check_page(args.file, args.expect_run_id, args.expect_loop)
        for err in errors:
            print(err, file=sys.stderr)
        return 1 if errors else 0
    meta = read_meta(args.file)
    if meta is None:
        print("no valid report-data envelope", file=sys.stderr)
        return 1
    print(json.dumps(meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/projects/loops && python3 -m unittest tests.test_page_envelope -v`
Expected: all PASS. Then run the whole suite: `bash tests/run-tests.sh` — green.

- [ ] **Step 5: Commit**

```bash
cd ~/projects/loops
git add bin/page_envelope.py tests/test_page_envelope.py
git commit -m "feat: page_envelope helper — report-page gate + meta reader (Amendment 2 §12)"
git push
```

---

### Task 3: Page kit (`pagekit/`)

**Files:**
- Create: `pagekit/kit.css`, `pagekit/README.md`, `pagekit/reference/fixture-scan.json`
- Source (read-only): `~/projects/av-audit/av-exposure-audit_llm_2026-07-30.html` (style block, lines 9–117), `~/projects/av-audit/scan-latest.json` (shape reference only — real paths must NOT be copied)

**Interfaces:**
- Produces: `pagekit/kit.css` (inlined by renderers), `pagekit/reference/fixture-scan.json` (input for Task 8's renderer test and the rendered reference page). Runner exports `PAGEKIT=$ROOT/pagekit` (Task 4).

- [ ] **Step 1: Extract `pagekit/kit.css`**

Copy the full `<style>` body from the benchmark page (`~/projects/av-audit/av-exposure-audit_llm_2026-07-30.html`, everything between `<style>` and `</style>`) into `pagekit/kit.css`, then prepend this header comment:

```css
/* pagekit/kit.css — shared report-page kit (docs/REPORT_PAGES_PLAN.md §3).
 * Extracted from the approved av-audit benchmark page (2026-07-30).
 * Palette validated (dataviz six-checks, surface #0e0f12):
 *   high #d84f63 · medium #b48c1a · accent #279a83.
 * Severity is never color-alone: pair colors with markers + text labels.
 * Renderers INLINE this file (self-containment rule) — never <link> it. */
```

Keep the CSS verbatim otherwise (class names `.wrap .hd .kicker .hero .stats .stat .brow .group .frow .fbody footer #tip` are the kit's layout vocabulary; `pagekit/README.md` documents them).

- [ ] **Step 2: Write `pagekit/README.md`**

```markdown
# pagekit — the shared report-page kit

Rules of the road: `docs/REPORT_PAGES_PLAN.md` §2 (contract, MUST) + §3 (kit, SHOULD).

## Using the kit

- Inline `kit.css` into your page's `<style>` at render time. Never `<link>` it —
  pages must be self-contained (zero network fetches; `<a href>` links are fine).
- Layout vocabulary: `.wrap` page column · `.hd`/`.kicker`/`.hero` header · `.stats`/`.stat`
  stat strip · `.brow` label+bar rows · `.group`/`.ghead`/`.frow`/`.fbody` grouped
  detail rows (`<details>`) · `footer` provenance line · `#tip` tooltip.
- Palette: high `#d84f63`, medium `#b48c1a`, accent `#279a83` on surface `#0e0f12`.
  Severity always gets a marker/text label as well as color.

## The envelope (required on every page)

Emit EXACTLY ONE block, escaping `</` inside the JSON so the payload can never
terminate the script element:

    envelope = {"meta": {"loop": loop, "run_id": run_id,
                          "generated_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                          "title": title, "page_class": "snapshot",
                          "totals": {...flat numbers/short strings...}},
                "data": {...loop-specific payload...}}
    html_block = ('<script type="application/json" id="report-data">'
                  + json.dumps(envelope).replace("</", "<\\/") + "</script>")

Verify locally: `bin/page_envelope.py check --file page.html`.

## reference/

`fixture-scan.json` — a sanitized av-scan-shaped fixture (fake paths, fake homedir).
The rendered reference page (`reference/reference-page.html`, produced by the kagi-ban
renderer in its build task) is the quality benchmark; the original Generalissimo-approved page
lives outside the repo at `~/projects/av-audit/` and must not be vendored (it embeds
this machine's real exposure paths).
```

- [ ] **Step 3: Write `pagekit/reference/fixture-scan.json`**

Shape-match `~/projects/av-audit/scan-latest.json` but with entirely fictional paths (a fixture user `taro` on host `fixture`), 5 findings across 3 sources so bars + groups + both severities render:

```json
{
  "gui_path": "/Applications/Automic Vault.app",
  "findings": [
    {"source": "github-cli", "severity": "high",
     "affected": [{"path": "/Users/taro/.config/gh/hosts.yml", "line": 3}],
     "explanation": "A plaintext OAuth token is stored in the GitHub CLI hosts file.",
     "solution": "Move the token into an encrypted store and remove the plaintext copy.",
     "docs_url": "https://example.invalid/docs/github-cli", "homepage": "https://cli.github.com"},
    {"source": "github-cli", "severity": "high",
     "affected": [{"path": "/Users/taro/.config/gh/config.yml", "line": null}],
     "explanation": "The CLI config grants credential helpers broad scope.",
     "solution": "Restrict helper scope to the hosts that need it.",
     "docs_url": "https://example.invalid/docs/github-cli", "homepage": "https://cli.github.com"},
    {"source": "ssh-keys", "severity": "high",
     "affected": [{"path": "/Users/taro/.ssh/id_ed25519", "line": null}],
     "explanation": "An SSH private key is stored without a passphrase.",
     "solution": "Re-key with a passphrase or move the key to a hardware token.",
     "docs_url": "https://example.invalid/docs/ssh", "homepage": "https://www.openssh.com"},
    {"source": "path-hygiene", "severity": "medium",
     "affected": [{"path": "/opt/fixturebrew/bin", "line": null}],
     "explanation": "A group-writable directory appears early in PATH.",
     "solution": "Tighten the directory mode or move it later in PATH.",
     "docs_url": "https://example.invalid/docs/path", "homepage": "https://example.invalid"},
    {"source": "path-hygiene", "severity": "medium",
     "affected": [{"path": "/Users/taro/bin", "line": null}],
     "explanation": "A user-writable directory shadows system binaries.",
     "solution": "Audit the directory contents and PATH ordering.",
     "docs_url": "https://example.invalid/docs/path", "homepage": "https://example.invalid"}
  ]
}
```

- [ ] **Step 4: Commit**

```bash
cd ~/projects/loops
git add pagekit/
git commit -m "feat: page kit — shared report-page CSS + sanitized reference fixture"
git push
```

---

### Task 4: Runner render step + loop-data commit + retention

**Files:**
- Modify: `bin/run-loop.sh` (constants block ~line 44; retention `keep_names` ~line 516; new step 6.5 between the promotion block and the final `finalize_and_finish`, ~line 896–911)
- Test: `tests/test_runner_pages.sh`

**Interfaces:**
- Consumes: `bin/page_envelope.py check` (Task 2); `tests/runner_test_helpers.sh` (`new_hermetic_root`, `make_loop`, `make_precheck`, `write_contract_fixture`, `run_runner`, assertions); fake engine via `LOOPS_ENGINE_OVERRIDE=fake LOOPS_ALLOW_FAKE_ENGINE=1`.
- Produces: env contract for renderers (`LOOP_NAME RUN_ID LOOPS_ROOT OUT_DIR LATEST_JSON LOOP_DATA_DIR PAGEKIT PAGE_OUT`), promoted `reports/<name>/<stamp>.html` + `latest.html`, `state/runs/<id>/page-render.log`, loop-data commit semantics, runner stdout line `page promoted: reports/<name>/<stamp>.html`. Tasks 5, 6, 8 rely on all of these.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_runner_pages.sh` (mirror `test_runner.sh`'s structure — source the helpers, one function per case, summary + exit):

```bash
#!/usr/bin/env bash
# tests/test_runner_pages.sh — hermetic tests for the Amendment 2 render step
# (report pages): loop-data commit, render gate, promotion, retention.
set -uo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/runner_test_helpers.sh"

# Every hermetic root needs page_envelope.py + redact.py (already copied by
# new_hermetic_root's bin/*.py glob) and a pagekit dir for $PAGEKIT.
seed_pagekit() { mkdir -p "$1/pagekit"; touch "$1/pagekit/kit.css"; }

# make_render <loop_dir> — writes render.sh from stdin, executable.
make_render() {
  local dir="$1"
  cat > "$dir/render.sh"
  chmod +x "$dir/render.sh"
}

# A renderer that emits a minimal VALID page for whatever RUN_ID/LOOP_NAME
# the runner hands it.
good_renderer_body() {
  cat <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
python3 - "$PAGE_OUT" "$LOOP_NAME" "$RUN_ID" <<'PY'
import json, sys, datetime
out, loop, run_id = sys.argv[1:4]
meta = {"loop": loop, "run_id": run_id,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "title": "Test page", "page_class": "snapshot", "totals": {"findings": 1}}
envelope = json.dumps({"meta": meta, "data": {}}).replace("</", "<\\/")
open(out, "w").write("<!DOCTYPE html><html><body>"
    f'<script type="application/json" id="report-data">{envelope}</script>'
    "</body></html>")
PY
EOF
}

test_successful_render_promotes_dated_and_latest() {
  local root; root="$(new_hermetic_root)"; seed_pagekit "$root"
  local dir; dir="$(make_loop "$root" pageloop agent)"
  write_contract_fixture "$root/fixture-contract.json" ok '[]'
  good_renderer_body | make_render "$dir"
  FAKE_CONTRACT_FILE="$root/fixture-contract.json" run_runner "$root" pageloop
  assert_eq "runner exit 0" 0 "$RUNNER_EXIT"
  assert_file_exists "latest.html promoted" "$root/reports/pageloop/latest.html"
  local dated
  dated="$(ls "$root/reports/pageloop/" | grep -c '^[0-9-]*[0-9]\.html$')"
  assert_eq "one dated page" 1 "$dated"
  assert_contains "stdout announces page" "$RUNNER_STDOUT" "page promoted: reports/pageloop/"
  cmp -s "$root/reports/pageloop/latest.html" \
    "$root/reports/pageloop/"$(ls "$root/reports/pageloop/" | grep '^[0-9-]*[0-9]\.html$') \
    && tr_ok || tr_fail "dated and latest byte-identical"
  rm -rf "$root"
}

test_failing_renderer_leaves_latest_untouched_and_run_completed() {
  local root; root="$(new_hermetic_root)"; seed_pagekit "$root"
  local dir; dir="$(make_loop "$root" pageloop agent)"
  write_contract_fixture "$root/fixture-contract.json" ok '[]'
  mkdir -p "$root/reports/pageloop"
  printf 'PREVIOUS' > "$root/reports/pageloop/latest.html"
  printf '#!/usr/bin/env bash\nexit 3\n' | make_render "$dir"
  FAKE_CONTRACT_FILE="$root/fixture-contract.json" run_runner "$root" pageloop
  assert_eq "runner exit 0" 0 "$RUNNER_EXIT"
  assert_eq "latest.html untouched" "PREVIOUS" "$(cat "$root/reports/pageloop/latest.html")"
  local status
  status="$(sqlite3 "$root/state/loops.sqlite" \
    "SELECT runner_status FROM runs ORDER BY started_at DESC LIMIT 1")"
  assert_eq "run still completed" completed "$status"
  local run_dir; run_dir="$(ls -d "$root/state/runs/"*pageloop* | head -n1)"
  assert_file_exists "render log written" "$run_dir/page-render.log"
  rm -rf "$root"
}

test_gate_rejects_wrong_run_id() {
  local root; root="$(new_hermetic_root)"; seed_pagekit "$root"
  local dir; dir="$(make_loop "$root" pageloop agent)"
  write_contract_fixture "$root/fixture-contract.json" ok '[]'
  cat <<'EOF' | make_render "$dir"
#!/usr/bin/env bash
python3 - "$PAGE_OUT" "$LOOP_NAME" <<'PY'
import json, sys
out, loop = sys.argv[1:3]
meta = {"loop": loop, "run_id": "WRONG", "generated_at": "2026-07-30T00:00:00Z",
        "title": "t", "page_class": "snapshot"}
env = json.dumps({"meta": meta, "data": {}}).replace("</", "<\\/")
open(out, "w").write(f'<script type="application/json" id="report-data">{env}</script>')
PY
EOF
  FAKE_CONTRACT_FILE="$root/fixture-contract.json" run_runner "$root" pageloop
  assert_file_missing "no promotion on run_id mismatch" "$root/reports/pageloop/latest.html"
  rm -rf "$root"
}

test_gate_rejects_secret_shaped_content() {
  local root; root="$(new_hermetic_root)"; seed_pagekit "$root"
  local dir; dir="$(make_loop "$root" pageloop agent)"
  write_contract_fixture "$root/fixture-contract.json" ok '[]'
  cat <<'EOF' | make_render "$dir"
#!/usr/bin/env bash
python3 - "$PAGE_OUT" "$LOOP_NAME" "$RUN_ID" <<'PY'
import json, sys, datetime
out, loop, run_id = sys.argv[1:4]
meta = {"loop": loop, "run_id": run_id,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "title": "t", "page_class": "snapshot"}
env = json.dumps({"meta": meta, "data": {}}).replace("</", "<\\/")
open(out, "w").write("<html><body><pre>ghp_" + "a"*24 + "</pre>"
    f'<script type="application/json" id="report-data">{env}</script></body></html>')
PY
EOF
  FAKE_CONTRACT_FILE="$root/fixture-contract.json" run_runner "$root" pageloop
  assert_file_missing "no promotion of secret-shaped page" "$root/reports/pageloop/latest.html"
  local run_dir; run_dir="$(ls -d "$root/state/runs/"*pageloop* | head -n1)"
  assert_contains "reason logged" "$(cat "$run_dir/page-render.log")" "redaction"
  rm -rf "$root"
}

test_loop_data_commit_only_on_promotion() {
  local root; root="$(new_hermetic_root)"; seed_pagekit "$root"
  local dir; dir="$(make_loop "$root" pageloop agent)"
  make_precheck "$dir" <<'EOF'
#!/usr/bin/env bash
mkdir -p "$OUT_DIR/loop-data.commit"
printf 'baseline-v2' > "$OUT_DIR/loop-data.commit/baseline.txt"
echo "digest line"
EOF
  # Case A: contract invalid -> no commit.
  write_contract_fixture "$root/fixture-contract.json" ok '[]'
  FAKE_CONTRACT_INVALID=1 FAKE_CONTRACT_FILE="$root/fixture-contract.json" \
    run_runner "$root" pageloop
  assert_file_missing "no commit on contract violation" \
    "$root/state/loop-data/pageloop/baseline.txt"
  # Case B: valid run -> committed.
  FAKE_CONTRACT_FILE="$root/fixture-contract.json" run_runner "$root" pageloop
  assert_eq "baseline committed" "baseline-v2" \
    "$(cat "$root/state/loop-data/pageloop/baseline.txt")"
  rm -rf "$root"
}

test_retention_keeps_latest_html() {
  local root; root="$(new_hermetic_root)"; seed_pagekit "$root"
  local dir; dir="$(make_loop "$root" pageloop agent "retention_days=1")"
  write_contract_fixture "$root/fixture-contract.json" ok '[]'
  good_renderer_body | make_render "$dir"
  mkdir -p "$root/reports/pageloop"
  printf 'old' > "$root/reports/pageloop/2020-01-01-0000.html"
  printf 'keep' > "$root/reports/pageloop/latest.html"
  touch -t 202001010000 "$root/reports/pageloop/2020-01-01-0000.html" \
    "$root/reports/pageloop/latest.html"
  FAKE_CONTRACT_FILE="$root/fixture-contract.json" run_runner "$root" pageloop
  assert_file_missing "old dated page pruned" "$root/reports/pageloop/2020-01-01-0000.html"
  assert_file_exists "latest.html survives retention" "$root/reports/pageloop/latest.html"
  rm -rf "$root"
}

test_no_render_sh_means_no_pages() {
  local root; root="$(new_hermetic_root)"; seed_pagekit "$root"
  make_loop "$root" plainloop agent >/dev/null
  write_contract_fixture "$root/fixture-contract.json" ok '[]'
  FAKE_CONTRACT_FILE="$root/fixture-contract.json" run_runner "$root" plainloop
  assert_eq "runner exit 0" 0 "$RUNNER_EXIT"
  assert_file_missing "no page for plain loop" "$root/reports/plainloop/latest.html"
  rm -rf "$root"
}

test_successful_render_promotes_dated_and_latest
test_failing_renderer_leaves_latest_untouched_and_run_completed
test_gate_rejects_wrong_run_id
test_gate_rejects_secret_shaped_content
test_loop_data_commit_only_on_promotion
test_retention_keeps_latest_html
test_no_render_sh_means_no_pages

echo "test_runner_pages: passed=$TR_PASSED failed=$TR_FAILED"
[ "$TR_FAILED" -eq 0 ]
```

Note: check `engines/fake.sh` + `runner_test_helpers.sh` for the exact env-var names the fake engine uses for its contract fixture (`FAKE_CONTRACT_FILE` above) and for forcing an invalid contract (`FAKE_CONTRACT_INVALID`); `tests/test_runner.sh` uses them — copy its exact invocation idiom if the names differ.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `bash tests/test_runner_pages.sh`
Expected: FAIL on every page assertion (`latest.html` never appears; loop-data commit never happens) — the runner has no render step yet.

- [ ] **Step 3: Implement the runner changes**

3a. Retention keep-list (`prune_dir` call in the `prune_retention` heredoc):

```python
prune_dir(os.path.join(root, "reports", name), keep_names=("latest.md", "latest.json", "latest.html"))
```

3b. New step 6.5, inserted in `bin/run-loop.sh` immediately after the `PROMOTE_OUT` variable-extraction block (after `CONTRACT_PATH_REL=...`, before the watchdog-stickiness lines):

```bash
# ---------------------------------------------------------------------------
# Step 6.5 (Amendment 2): loop-data commit + report page render. Only for a
# promoted run; NOTHING here may change runner_status / exit code.
# ---------------------------------------------------------------------------

RENDER_SH="$LOOP_DIR/render.sh"
PAGE_TMP="$OUT_DIR/page.html"
RENDER_LOG="$OUT_DIR/page-render.log"
LOOP_DATA_DIR="$ROOT/state/loop-data/$NAME"

commit_loop_data() {
  local commit_dir="$OUT_DIR/loop-data.commit"
  [ -d "$commit_dir" ] || return 0
  mkdir -p "$LOOP_DATA_DIR"
  chmod 700 "$ROOT/state/loop-data" "$LOOP_DATA_DIR" 2>/dev/null || true
  local f
  for f in "$commit_dir"/*; do
    [ -f "$f" ] || continue
    mv -f "$f" "$LOOP_DATA_DIR/$(basename "$f")" 2>/dev/null || \
      log_err "loop-data commit failed for $(basename "$f") (ignored)"
    chmod 600 "$LOOP_DATA_DIR/$(basename "$f")" 2>/dev/null || true
  done
  return 0
}

_render_runner_fn() {
  cd "$LOOP_DIR"
  LOOP_NAME="$NAME" RUN_ID="$RUN_ID" LOOPS_ROOT="$ROOT" OUT_DIR="$OUT_DIR" \
    LATEST_JSON="$REPORT_DIR/latest.json" LOOP_DATA_DIR="$LOOP_DATA_DIR" \
    PAGEKIT="$ROOT/pagekit" PAGE_OUT="$PAGE_TMP" \
    exec "$RENDER_SH" > "$OUT_DIR/.render.raw.log" 2>&1
}

finish_render_log() {
  # Cap (64 KiB) + redact the raw render log into page-render.log, then
  # append $1 as the outcome line.
  local raw="$OUT_DIR/.render.raw.log" size=0
  [ -f "$raw" ] && size=$(wc -c < "$raw" | tr -d ' ')
  if [ "$size" -gt "$PRECHECK_CAP_BYTES" ]; then
    dd if="$raw" of="$RENDER_LOG" bs=1 count="$PRECHECK_CAP_BYTES" 2>/dev/null
    printf '\n...[TRUNCATED: render log exceeded 64KiB cap]\n' >> "$RENDER_LOG"
  else
    cp "$raw" "$RENDER_LOG" 2>/dev/null || : > "$RENDER_LOG"
  fi
  printf '%s\n' "$1" >> "$RENDER_LOG"
  chmod 600 "$RENDER_LOG" 2>/dev/null || true
  redact_file_inplace "$RENDER_LOG"
  rm -f "$raw"
  return 0
}

render_page() {
  [ -f "$RENDER_SH" ] && [ -x "$RENDER_SH" ] || return 0
  local render_timeout="$CONF_TIMEOUT_S"
  if [ "$render_timeout" -gt "$PRECHECK_MAX_TIMEOUT_S" ]; then
    render_timeout="$PRECHECK_MAX_TIMEOUT_S"
  fi
  run_with_pgroup_timeout "$render_timeout" _render_runner_fn
  if [ "$RWT_TIMED_OUT" = "1" ]; then
    finish_render_log "RENDER FAILED: timed out after ${render_timeout}s"
    return 0
  fi
  if [ "$RWT_EXIT_CODE" != "0" ]; then
    finish_render_log "RENDER FAILED: render.sh exit $RWT_EXIT_CODE"
    return 0
  fi
  local gate_errors
  if ! gate_errors="$("$PY" "$ROOT/bin/page_envelope.py" check --file "$PAGE_TMP" \
      --expect-run-id "$RUN_ID" --expect-loop "$NAME" 2>&1)"; then
    finish_render_log "PROMOTION GATE FAILED: ${gate_errors}"
    return 0
  fi
  local dated_html="$(date -u +%Y-%m-%d-%H%M).html"
  local tmp_dated="$REPORT_DIR/.page.dated.tmp" tmp_latest="$REPORT_DIR/.page.latest.tmp"
  cp "$PAGE_TMP" "$tmp_dated" && cp "$PAGE_TMP" "$tmp_latest" || {
    finish_render_log "RENDER FAILED: could not stage promotion copies"
    rm -f "$tmp_dated" "$tmp_latest"
    return 0
  }
  chmod 600 "$tmp_dated" "$tmp_latest" 2>/dev/null || true
  mv "$tmp_dated" "$REPORT_DIR/$dated_html"      # dated FIRST (spec §4.1)
  mv "$tmp_latest" "$REPORT_DIR/latest.html"
  finish_render_log "page promoted: reports/$NAME/$dated_html"
  printf 'page promoted: reports/%s/%s\n' "$NAME" "$dated_html"
  return 0
}

commit_loop_data
render_page
```

Note `REPORT_DIR` is already set by step 6 (line ~802) before this point, and both new functions `return 0` on every path — `set -e` safety.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `bash tests/test_runner_pages.sh` — all cases pass.
Then: `bash tests/run-tests.sh` — the full suite stays green (existing runner tests must not notice the new step: no `render.sh` → no behavior change).

- [ ] **Step 5: Commit**

```bash
cd ~/projects/loops
git add bin/run-loop.sh tests/test_runner_pages.sh
git commit -m "feat(runner): Amendment 2 step 6.5 — loop-data commit, page render, promotion gate, retention keep"
git push
```

---

### Task 5: `loopctl` validate rule + page path in `run` output

**Files:**
- Modify: `bin/loopctl` (validate section, next to the rule-6 watchdog/precheck check at ~line 393)
- Test: `tests/test_loopctl.py` (append cases)

**Interfaces:**
- Consumes: validate's existing per-loop check pattern (list of failure strings).
- Produces: validate failure string `render.sh present but not executable`. (`loopctl run` needs no change: it streams the runner's stdout, which now includes the `page promoted:` line from Task 4.)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_loopctl.py` (match the file's existing fixture idiom for creating a loop dir and invoking validate — reuse its helper that scaffolds a valid loop, then):

```python
def test_validate_fails_on_non_executable_render_sh(self):
    loop_dir = self.make_valid_loop("pageloop")          # existing helper in this file
    render = os.path.join(loop_dir, "render.sh")
    with open(render, "w") as f:
        f.write("#!/usr/bin/env bash\nexit 0\n")
    os.chmod(render, 0o644)                              # present, NOT executable
    code, out, err = self.run_loopctl("validate", "pageloop")
    self.assertEqual(code, 1)
    self.assertIn("render.sh present but not executable", out + err)

def test_validate_passes_with_executable_render_sh(self):
    loop_dir = self.make_valid_loop("pageloop2")
    render = os.path.join(loop_dir, "render.sh")
    with open(render, "w") as f:
        f.write("#!/usr/bin/env bash\nexit 0\n")
    os.chmod(render, 0o755)
    code, out, err = self.run_loopctl("validate", "pageloop2")
    self.assertEqual(code, 0)
```

(Adapt helper names to what `tests/test_loopctl.py` actually defines — read the file first; it already scaffolds valid loops for the other validate rules.)

- [ ] **Step 2: Run to verify the new tests fail**

Run: `python3 -m unittest tests.test_loopctl -v` — the two new cases fail (validate currently ignores `render.sh`).

- [ ] **Step 3: Implement in `bin/loopctl`**

Next to the rule-6 precheck check:

```python
# Amendment 2: an optional render.sh makes the loop page-enabled — but only
# when executable; present-and-inert is always a mistake worth failing loudly.
render_sh = os.path.join(loop_dir, "render.sh")
if os.path.isfile(render_sh) and not os.access(render_sh, os.X_OK):
    failures.append("render.sh present but not executable")
```

(Use the same append-a-failure-string mechanism the surrounding rules use — match names exactly.)

- [ ] **Step 4: Run tests, then the full suite**

Run: `python3 -m unittest tests.test_loopctl -v` then `bash tests/run-tests.sh`. All green.

- [ ] **Step 5: Commit**

```bash
cd ~/projects/loops
git add bin/loopctl tests/test_loopctl.py
git commit -m "feat(loopctl): validate fails a non-executable render.sh (Amendment 2)"
git push
```

---

### Task 6: Dashboard surfaces — row page link + `reports.html`

**Files:**
- Modify: `dashboard/generate.py` (`_resolve_loop` ~line 986, `_render_loop_row` report cell ~line 862, `generate()` ~line 1073, `main()` ~line 1208; new functions `_latest_promoted_run`, `_page_state`, `_render_reports_page`)
- Test: `tests/test_dashboard.py` (append cases using its existing `FixtureRoot` helper)

**Interfaces:**
- Consumes: `bin/page_envelope.py` (`read_meta`, `check_page`) loaded via the file's existing `_load_module_from_path` helper; `reports/<name>/latest.html` + dated `*.html` on disk; sqlite `runs`.
- Produces: `dashboard/reports.html`; row Report cell linking `../reports/<name>/latest.html` (+ `stale` badge span `class="badge page-stale"`); `generate()` keyword `reports_out_file=None`; CLI flag `--reports-out`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dashboard.py` (using the existing `FixtureRoot`, `add_loop`, `add_run`, `write_latest_json` helpers; add a small `write_latest_html` helper next to them):

```python
def _fixture_page(loop, run_id, title="Fixture page"):
    import json as _json
    meta = {"loop": loop, "run_id": run_id, "generated_at": "2026-07-30T00:00:01Z",
            "title": title, "page_class": "snapshot", "totals": {"findings": 5}}
    env = _json.dumps({"meta": meta, "data": {}}).replace("</", "<\\/")
    return ("<!DOCTYPE html><html><body>"
            f'<script type="application/json" id="report-data">{env}</script>'
            "</body></html>")


class ReportPagesDashboardTests(unittest.TestCase):
    def setUp(self):
        self.fx = FixtureRoot()
        self.addCleanup(self.fx.cleanup)
        self.conn = self.fx.init_db()

    def _promoted_run(self, name, run_id, started):
        self.fx.add_run(self.conn, run_id, name, started, finished_at=started,
                        runner_status="completed", loop_status="ok",
                        effective_status="ok", headline="h")
        self.conn.execute("UPDATE runs SET contract_path=? WHERE run_id=?",
                          (f"state/runs/{run_id}/contract.json", run_id))
        self.conn.commit()

    def _write_page(self, name, content):
        d = os.path.join(self.fx.root, "reports", name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "latest.html"), "w") as f:
            f.write(content)

    def test_row_links_page_when_fresh(self):
        self.fx.add_loop("pgl")
        self._promoted_run("pgl", "20260730T000000Z-pgl-abc123", iso(NOW))
        self.fx.write_latest_json("pgl", {"findings": [], "report_markdown": ""})
        self._write_page("pgl", _fixture_page("pgl", "20260730T000000Z-pgl-abc123"))
        html = generate.generate(root=self.fx.root, now=NOW,
                                 loopconf_parse=fake_loopconf_parse(),
                                 schedule_parse=fake_schedule_parse(), return_html=True)
        self.assertIn("../reports/pgl/latest.html", html)
        self.assertNotIn("page-stale", html)

    def test_row_page_gets_stale_badge_on_run_id_mismatch(self):
        self.fx.add_loop("pgl")
        self._promoted_run("pgl", "20260730T000000Z-pgl-abc123", iso(NOW))
        self.fx.write_latest_json("pgl", {"findings": [], "report_markdown": ""})
        self._write_page("pgl", _fixture_page("pgl", "OLD-RUN-ID"))
        html = generate.generate(root=self.fx.root, now=NOW,
                                 loopconf_parse=fake_loopconf_parse(),
                                 schedule_parse=fake_schedule_parse(), return_html=True)
        self.assertIn("page-stale", html)

    def test_reports_page_lists_entry_with_chips_and_history(self):
        self.fx.add_loop("pgl")
        self._promoted_run("pgl", "20260730T000000Z-pgl-abc123", iso(NOW))
        self._write_page("pgl", _fixture_page("pgl", "20260730T000000Z-pgl-abc123"))
        d = os.path.join(self.fx.root, "reports", "pgl")
        for stamp in ("2026-07-28-0100", "2026-07-29-0100"):
            with open(os.path.join(d, f"{stamp}.html"), "w") as f:
                f.write("x")
        reports_html = generate.generate_reports(
            root=self.fx.root, now=NOW, loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(), return_html=True)
        self.assertIn("Fixture page", reports_html)
        self.assertIn("findings", reports_html)          # totals chip label
        self.assertIn("2026-07-29-0100.html", reports_html)

    def test_reports_page_marks_page_enabled_loop_with_no_page(self):
        d = self.fx.add_loop("bare")
        render = os.path.join(d, "render.sh")
        with open(render, "w") as f:
            f.write("#!/usr/bin/env bash\nexit 0\n")
        os.chmod(render, 0o755)
        reports_html = generate.generate_reports(
            root=self.fx.root, now=NOW, loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(), return_html=True)
        self.assertIn("no page yet", reports_html)

    def test_reports_page_no_meta_fallback(self):
        self.fx.add_loop("bad")
        self._write_page("bad", "<html><body>not a real page</body></html>")
        reports_html = generate.generate_reports(
            root=self.fx.root, now=NOW, loopconf_parse=fake_loopconf_parse(),
            schedule_parse=fake_schedule_parse(), return_html=True)
        self.assertIn("no meta", reports_html)

    def test_generate_writes_both_files_atomically(self):
        self.fx.add_loop("pgl")
        generate.generate(root=self.fx.root, now=NOW,
                          loopconf_parse=fake_loopconf_parse(),
                          schedule_parse=fake_schedule_parse())
        self.assertTrue(os.path.isfile(
            os.path.join(self.fx.root, "dashboard", "loops.html")))
        self.assertTrue(os.path.isfile(
            os.path.join(self.fx.root, "dashboard", "reports.html")))
```

(Adapt to `test_dashboard.py`'s actual call signatures — if `generate.generate` has no `return_html`/`generate_reports` yet, that is exactly what Step 3 adds; keep the names shown here so Step 3 implements to them.)

- [ ] **Step 2: Run to verify the new tests fail**

Run: `python3 -m unittest tests.test_dashboard -v` — new cases fail (`generate_reports` missing, no page links).

- [ ] **Step 3: Implement in `dashboard/generate.py`**

3a. Load the envelope helper next to the existing loopconf/schedule loaders:

```python
def _default_page_envelope(root):
    path = os.path.join(root, "bin", "page_envelope.py")
    if not os.path.isfile(path):
        return None
    try:
        return _load_module_from_path(path, "loops_page_envelope")
    except Exception:  # noqa: BLE001 — §10: degrade, never crash the page
        return None
```

3b. `_latest_promoted_run(conn, loop_name)` beside `_latest_run`:

```python
def _latest_promoted_run(conn, loop_name):
    cur = conn.execute(
        "SELECT run_id FROM runs WHERE loop_name = ? AND runner_status = 'completed' "
        "AND contract_path IS NOT NULL ORDER BY started_at DESC LIMIT 1",
        (loop_name,),
    )
    row = cur.fetchone()
    return row[0] if row else None
```

3c. `_page_state(root, name, conn, envelope_mod)` — computed inside `_resolve_loop` and stored on the loop dict:

```python
def _page_state(root, name, conn, envelope_mod):
    """Returns {enabled, href, meta, stale, dated:[names], historical}."""
    report_dir = os.path.join(root, "reports", name)
    latest = os.path.join(report_dir, "latest.html")
    render_sh = os.path.join(root, "loops.d", name, "render.sh")
    enabled = os.path.isfile(render_sh) and os.access(render_sh, os.X_OK)
    dated = []
    if os.path.isdir(report_dir):
        pat = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{4}\.html$")
        dated = sorted((e for e in os.listdir(report_dir) if pat.match(e)), reverse=True)
    state = {"enabled": enabled, "href": None, "meta": None, "stale": False,
             "dated": dated, "historical": bool(dated or os.path.isfile(latest)) and not enabled}
    if not os.path.isfile(latest):
        return state
    state["href"] = f"../reports/{name}/latest.html"
    meta = envelope_mod.read_meta(latest) if envelope_mod else None
    state["meta"] = meta
    if enabled and meta is not None:
        promoted = _latest_promoted_run(conn, name)
        if promoted is not None and meta.get("run_id") != promoted:
            state["stale"] = True
    return state
```

3d. Row report cell in `_render_loop_row` (replacing the current single-link logic around line 862):

```python
page = loop.get("page") or {}
links = []
if page.get("href"):
    badge = ' <span class="badge page-stale">stale</span>' if page.get("stale") else ""
    links.append(f'<a href="{e(page["href"])}">page</a>{badge}')
if loop["report_href"]:
    label = "md" if page.get("href") else "latest"
    links.append(f'<a href="{e(loop["report_href"])}">{label}</a>')
report_link = " · ".join(links)
```

3e. `_render_reports_page(loops, now)` — a second document assembled from the same kit-flavored inline CSS as `loops.html` (reuse the page's existing CSS constants; add `.badge.page-stale`, `.chip`, `.no-page` styles):

```python
def _render_reports_page(loops, now):
    entries = []
    for loop in loops:
        page = loop.get("page") or {}
        if not (page.get("enabled") or page.get("href") or page.get("dated")):
            continue
        name = loop["name"]
        meta = page.get("meta")
        if page.get("href") and meta:
            chips = "".join(
                f'<span class="chip">{e(str(k))} {e(str(v))}</span>'
                for k, v in (meta.get("totals") or {}).items()
            )
            stale = ' <span class="badge page-stale">stale</span>' if page.get("stale") else ""
            hist = ' <span class="badge historical">historical</span>' if page.get("historical") else ""
            head = (f'<a href="{e(page["href"])}">{e(meta.get("title") or name)}</a>'
                    f"{stale}{hist} <span class=\"muted\">{e(meta.get('page_class') or '')}"
                    f" · {e(format_relative(meta.get('generated_at'), now))}"
                    f" ({e(meta.get('generated_at') or '')})</span>")
        elif page.get("href"):
            head = (f'<a href="{e(page["href"])}">{e(name)}</a> '
                    '<span class="badge no-meta">no meta</span>')
            chips = ""
        else:
            head = f'{e(name)} <span class="badge no-page">no page yet — last render failed or has not run</span>'
            chips = ""
        dated = page.get("dated") or []
        shown = dated[:30]
        more = f' <span class="muted">+{len(dated) - 30} older</span>' if len(dated) > 30 else ""
        history = " ".join(
            f'<a href="../reports/{e(name)}/{e(d)}">{e(d)}</a>' for d in shown
        ) + more
        entries.append(f'<section class="report-entry"><h2>{head}</h2>'
                       f'<div class="chips">{chips}</div>'
                       f'<div class="history">{history}</div></section>')
    body = "".join(entries) or '<p class="muted">No page-enabled loops yet.</p>'
    return _reports_document(body, now)   # same doc-shell pattern as loops.html
```

3f. `generate()` gains `reports_out_file=None, return_html=False`; after writing `loops.html` it builds and `_atomic_write`s `reports.html` (each file its own tmp+rename — per-file atomic, pair is not). Add a thin `generate_reports(...)` wrapper used by tests. `main()` gains `--reports-out`.

- [ ] **Step 4: Run tests, then the full suite**

Run: `python3 -m unittest tests.test_dashboard -v` then `bash tests/run-tests.sh`. All green (existing dashboard tests unchanged: loops without pages render exactly as before).

- [ ] **Step 5: Commit**

```bash
cd ~/projects/loops
git add dashboard/generate.py tests/test_dashboard.py
git commit -m "feat(dashboard): report-page row links + reports.html screen (Amendment 2)"
git push
```

---

### Task 7: Docs — authoring guide, q12, skill-import delta

**Files:**
- Create: `docs/REPORT_PAGES.md`
- Modify: `docs/LOOP_AUTHORING.md` (§2 intake — add q12; every "eleven" count reference: lines near 82, 84, 169, 173, 382, 416), `bin/loopctl` (`_SPEC_TEMPLATE` — add the twelfth heading), `docs/SKILL_IMPORT_AND_AGENT_SURFACE_PLAN.md` (§4.1/§4.2 delta note)

**Interfaces:**
- Consumes: spec §2/§3/§6 (`docs/REPORT_PAGES_PLAN.md`).
- Produces: `q12_page` rubric id; SPEC template heading `## 12. Page output (q12)`; the authoring guide Task 8's pilot follows.

- [ ] **Step 1: Write `docs/REPORT_PAGES.md`**

Content (write it fully — this is the authoring guide the import-time agent designs from):

```markdown
# Report pages — authoring guide

Design rationale + settled decisions: `docs/REPORT_PAGES_PLAN.md`. Mechanical contract:
`docs/INTERFACES.md` §4.1 step 6.5 + §12. This doc is the HOW for loop authors.

## When to add a page (rubric q12)

Add a page when the loop's world state carries more structured detail than the row +
markdown report can show (inventories, per-item remediation, grouped scans). Skip it when
the headline + findings + report_markdown already tell the whole story. Then choose the
page class:
- `snapshot` — the page renders deterministically-captured world state IN FULL (an audit
  document). Dismissing a loop finding silences the nag channel, not the document.
- `findings` — any part of the page that presents the loop's findings AS findings must
  come from the suppression-filtered `latest.json` (`LATEST_JSON`), never raw
  `contract.json`.

## The moving parts

1. `loops.d/<name>/render.sh` — executable = page-enabled. Runs AFTER contract promotion
   with cwd `loops.d/<name>/` and env `LOOP_NAME RUN_ID LOOPS_ROOT OUT_DIR LATEST_JSON
   LOOP_DATA_DIR PAGEKIT PAGE_OUT`. Write the finished page to `$PAGE_OUT`. Deterministic
   code only: no model calls, no network, no randomness.
2. The page: one self-contained HTML file built on `pagekit/kit.css` (inline it), with
   EXACTLY ONE envelope block — see `pagekit/README.md` for the copy-paste snippet.
   Required meta: loop, run_id, generated_at (ISO8601Z), title, page_class. Optional:
   totals (flat; becomes chips on the reports screen).
3. The gate: promotion happens only if `bin/page_envelope.py check` passes — envelope
   valid, run_id/loop match, ≤ 8 MiB, no external-fetch markup, and REDACTION-CLEAN:
   if `bin/redact.py` would change your page, it does not publish. Never put secret
   VALUES on a page; paths/names are fine.
4. Failure semantics: a broken renderer never fails the run. Look in
   `state/runs/<id>/page-render.log`; the reports screen shows "no page yet" or a
   `stale` badge until a render succeeds again.
5. Loop-private durable state: read baselines from `$LOOP_DATA_DIR`; write updated
   baselines to `$OUT_DIR/loop-data.commit/` — the runner commits them only when the
   run promotes, so failed runs never consume state. Keep it bounded (e.g. exactly one
   previous snapshot).

## Worked example

`loops.d/kagi-ban/` is the reference implementation: precheck captures `scan.json` +
computes the diff; `render.sh` renders the snapshot page via a copy-with-provenance of
av-audit's renderer; `pagekit/reference/reference-page.html` is the rendered benchmark
(from the sanitized fixture). Quality bar: match it.

## Import integration

`q12_page` rides the skill-import `answers_needed` flow once `loopctl import` exists;
`--apply` scaffolds `render.sh` INERT (non-executable, body commented). Making it
executable is the deliberate act, same trust rule as extracted precheck lines.
```

- [ ] **Step 2: Add q12 to `docs/LOOP_AUTHORING.md`**

In §2, after q11, add:

```markdown
**q12. Page output** — does this loop need a full report page (`docs/REPORT_PAGES.md`)?
If yes: which page class (`snapshot` | `findings`), which data lands on it, and what its
groups/stat-strip show. Default: no page.
```

Update every "eleven questions/headings/sections" reference to "twelve" (lines near 82, 84, 169, 173, 382, 416 — grep for `eleven` to catch all). Add `render.sh` to the §7 build-walkthrough file list.

- [ ] **Step 3: Add the twelfth heading to `loopctl`'s SPEC template**

In `bin/loopctl`'s `_SPEC_TEMPLATE`, after the eleventh section, append:

```markdown
## 12. Page output (q12)
[FILL: none — OR page class (snapshot|findings), what the page shows, groups/stats]
```

Check `tests/test_loopctl.py` for template-shape assertions and update counts there if any assert on the heading list.

- [ ] **Step 4: Record the delta in the skill-import plan**

In `docs/SKILL_IMPORT_AND_AGENT_SURFACE_PLAN.md` §4.1, after the presentation-choices sentence, add:

```markdown
(**Report-pages delta, 2026-07-30:** the rubric now carries `q12_page` — see
`docs/REPORT_PAGES_PLAN.md` §6. The analyzer suggests a page when the skill produces a
rich artifact/HTML, `suggested_answerer:"user"`; `--apply` scaffolds `render.sh` INERT —
non-executable, commented body — under the same never-write-executable-extractions rule.)
```

- [ ] **Step 5: Run the suite, commit**

Run: `bash tests/run-tests.sh` (loopctl template tests updated if needed).

```bash
cd ~/projects/loops
git add docs/REPORT_PAGES.md docs/LOOP_AUTHORING.md docs/SKILL_IMPORT_AND_AGENT_SURFACE_PLAN.md bin/loopctl tests/
git commit -m "docs: report-pages authoring guide + rubric q12 (twelve-question intake)"
git push
```

---

### Task 8: Pilot `kagi-ban` — loop files + hermetic tests + reference page

**Files:**
- Create: `loops.d/kagi-ban/loop.conf`, `loops.d/kagi-ban/SPEC.md`, `loops.d/kagi-ban/prompt.md`, `loops.d/kagi-ban/precheck.sh`, `loops.d/kagi-ban/render.sh`, `loops.d/kagi-ban/render_page.py`, `loops.d/kagi-ban/dashboard.json`, `pagekit/reference/reference-page.html`
- Source (read-only, copy-with-provenance): `~/projects/av-audit/render_report.py`
- Test: `tests/test_kagi_ban.py`

**Interfaces:**
- Consumes: Task 4's env contract + loop-data commit; Task 3's fixture; `~/projects/av-audit/LOOP_HANDOFF.md` constraints (report-only; never `av harden/save/inject/open`; alert on data, not exit code).
- Produces: the first page-enabled loop; `render_page.py` CLI `render_page.py SCAN_JSON --loop L --run-id R -o OUT`; precheck digest format (below); finding-id rule `av:<source>:<sha8>`.

- [ ] **Step 1: `loop.conf`**

```
name=kagi-ban
description="Daily machine-exposure audit via Automic Vault scan (read-only detector); diffs against the previous scan and renders the full snapshot page"
type=agent
engine=codex
schedule=daily:07:40
timeout_s=300
# floor permissions on every axis (defaults): report_only / none / none / none
notes="av scan runs in trusted precheck; engine only interprets the digest. NEVER av harden/save/inject/open (LOOP_HANDOFF hard constraint)."
```

- [ ] **Step 2: `precheck.sh`**

```bash
#!/usr/bin/env bash
# kagi-ban precheck — trusted deterministic gathering (script→agent pattern).
# Runs the Automic Vault scanner READ-ONLY, diffs against the committed
# baseline, emits a digest for the engine. All counts are computed HERE
# (model-emitted metrics get believed — house gotcha).
set -euo pipefail

AV_BIN="${AV_BIN:-/Applications/Automic Vault.app/Contents/MacOS/av}"

if [ ! -x "$AV_BIN" ]; then
  echo "ERROR: av binary not found at $AV_BIN" >&2
  exit 1
fi

AV_VERSION="$("$AV_BIN" --version 2>/dev/null | head -n1 || echo unknown)"
"$AV_BIN" scan --json > "$OUT_DIR/scan.json"

mkdir -p "$OUT_DIR/loop-data.commit"

python3 - "$OUT_DIR/scan.json" "$LOOPS_ROOT/state/loop-data/kagi-ban/scan-prev.json" \
  "$OUT_DIR/loop-data.commit/scan-prev.json" "$AV_VERSION" <<'PY'
import hashlib
import json
import shutil
import sys

scan_path, prev_path, commit_path, av_version = sys.argv[1:5]

def keys(path):
    """finding key: av:<source>:<sha8 of sorted affected paths> (no line
    numbers — volatile identity is forbidden, LOOP_AUTHORING §2)."""
    try:
        with open(path) as f:
            findings = json.load(f).get("findings") or []
    except (OSError, ValueError):
        return {}
    out = {}
    for item in findings:
        paths = sorted(a.get("path") or "" for a in item.get("affected") or [])
        digest = hashlib.sha256("|".join(paths).encode()).hexdigest()[:8]
        key = f"av:{item.get('source')}:{digest}"
        out[key] = {"source": item.get("source"), "severity": item.get("severity"),
                    "paths": paths}
    return out

current = keys(scan_path)
previous = keys(prev_path)
new = sorted(set(current) - set(previous))
resolved = sorted(set(previous) - set(current))
ongoing = sorted(set(current) & set(previous))
sev_high = sum(1 for v in current.values() if v["severity"] in ("high", "critical"))
sev_med = sum(1 for v in current.values() if v["severity"] == "medium")
first_run = not previous

print(f"av_version: {av_version}")
print(f"counts: total={len(current)} high={sev_high} medium={sev_med} "
      f"new={len(new)} resolved={len(resolved)} ongoing={len(ongoing)} "
      f"first_run={'yes' if first_run else 'no'}")
print()
print("CURRENT EXPOSURES (finding_id | severity | source | paths) — the engine")
print("re-emits EVERY line below as a finding with exactly this finding_id:")
for key in sorted(current):
    item = current[key]
    label = "NEW" if key in new else "ONGOING"
    print(f"{label} {key} | {item['severity']} | {item['source']} | {';'.join(item['paths'])}")
for key in resolved:
    item = previous[key]
    print(f"RESOLVED {key} | was {item['severity']} | {item['source']}")

shutil.copyfile(scan_path, commit_path)
PY
```

- [ ] **Step 3: `prompt.md`**

```markdown
# kagi-ban — machine-exposure audit interpreter

You are the interpretation step of a report-only loop. The PRECHECK OUTPUT below is
ground truth from the Automic Vault scanner; you never run commands, never recompute
counts, and never invent data.

Emit the tier-1 contract:
- `status`: `ok` when the current-exposure list is empty; otherwise leave findings to
  drive the effective status (a run with findings gets its displayed status from the
  max unsuppressed severity — INTERFACES §4.5).
- `status_reason`: short category, e.g. `exposures_present`, `new_exposures`, `clean`.
- `headline`: one line — totals plus what changed, e.g.
  "20 exposures (18 high); 1 new since yesterday, 2 resolved".
- `findings`: re-emit EVERY line of the CURRENT EXPOSURES list as one finding — the
  line's `finding_id` verbatim, `severity` mapped (high/critical → alert, medium →
  warn, anything else → info), `title` = source + short path summary, `detail` = the
  paths plus NEW/ONGOING label and, for NEW items, what appeared. Do NOT emit findings
  for RESOLVED lines — mention them in the report prose instead.
- `metrics` (JSON string): copy the precheck `counts:` numbers verbatim, e.g.
  `"{\"av\": {\"total\": 20, \"high\": 18, \"medium\": 2, \"new\": 1, \"resolved\": 2}}"`.
- `report_markdown`: brief narrative — what is new, what resolved, what remains; the
  full inventory lives on the report page, not here.

## Finding identity
`av:<source>:<sha8>` where sha8 = first 8 hex chars of sha256 over the sorted affected
path list (NO line numbers — they shift). The precheck computes every id; copy them
verbatim, never derive your own.

Findings prompt-contract rules (harness-wide):
1. Re-emit a still-true finding with its same `finding_id` — never invent a new id for
   a recurring condition.
2. Do not re-argue a DISMISSED finding unless the situation materially changed; if it
   has, say what changed.
3. Still emit SNOOZED findings if true — suppression is the runner's job, not yours.
```

- [ ] **Step 4: `render_page.py` (copy-with-provenance) + `render.sh`**

Copy `~/projects/av-audit/render_report.py` to `loops.d/kagi-ban/render_page.py`, then apply exactly these deltas (documented in the file header):

1. Replace the module docstring's first line with:
   ```python
   """kagi-ban render_page.py — copy-with-provenance of ~/projects/av-audit/render_report.py
   (2026-07-30). Deltas from the original, per docs/REPORT_PAGES_PLAN.md §7: adds
   --loop/--run-id; envelope id scan-data → report-data; meta gains loop/run_id/
   generated_at/title/page_class; findings nest under data. Do not fork further
   silently — keep this list current."""
   ```
2. In `main()` add:
   ```python
   ap.add_argument("--loop", required=True)
   ap.add_argument("--run-id", required=True)
   ```
3. Replace the `envelope = {...}` block with:
   ```python
   envelope = {
       "meta": {
           "loop": args.loop,
           "run_id": args.run_id,
           "generated_at": rendered.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "title": f"Exposure audit — {args.host}",
           "page_class": "snapshot",
           "host": args.host,
           "av_version": args.av_version,
           "scanned_at": scanned.isoformat(timespec="seconds"),
           "source_file": args.scan_json.name,
           "totals": {
               "findings": len(findings),
               **{f"sev_{k}": v for k, v in sev.items()},
               "tools": len(tools),
               "paths": len(paths),
           },
       },
       "data": {"findings": findings},
   }
   ```
4. In the `PAGE` template, change `id="scan-data"` to `id="report-data"` (the `.replace("</", "<\\/")` escaping already exists — keep it).

`render.sh`:

```bash
#!/usr/bin/env bash
# kagi-ban render.sh — deterministic snapshot page from this run's scan.json.
set -euo pipefail
exec python3 "$LOOPS_ROOT/loops.d/kagi-ban/render_page.py" "$OUT_DIR/scan.json" \
  --loop "$LOOP_NAME" --run-id "$RUN_ID" -o "$PAGE_OUT"
```

`chmod +x loops.d/kagi-ban/precheck.sh loops.d/kagi-ban/render.sh`.

- [ ] **Step 5: `dashboard.json` + `SPEC.md`**

`dashboard.json`:

```json
{"panels":[
  {"title":"Exposures","metric":"av.total","type":"number","unit":"findings",
   "direction":"higher_is_worse","thresholds":{"warn":1,"alert":19},"missing":"gap"},
  {"title":"High severity","metric":"av.high","type":"number","unit":"findings",
   "direction":"higher_is_worse","thresholds":{"warn":1,"alert":18},"missing":"gap"},
  {"title":"New this run","metric":"av.new","type":"number","unit":"findings",
   "direction":"higher_is_worse","thresholds":{"warn":1,"alert":3},"missing":"gap"},
  {"title":"Exposures (trend)","metric":"av.total","type":"trend","window_days":30,"missing":"hold"}
]}
```

`SPEC.md`: fill all twelve headings from the intake (purpose: recurring machine-exposure audit per `~/projects/av-audit/LOOP_HANDOFF.md`; type agent; precheck gathers scan + diff, engine interprets; schedule daily:07:40; engine codex; floor axes; finding identity `av:<source>:<sha8>`; metrics `av.*`; budget: engine sees only the digest; q12: page class snapshot, full scan inventory, stat strip = totals). No `[FILL:` may remain (validate enforces).

- [ ] **Step 6: Hermetic tests + the reference page**

Create `tests/test_kagi_ban.py`:

```python
"""Hermetic kagi-ban tests: precheck digest against a stub av binary, and the
renderer against the sanitized fixture. Never touches the real av app."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
LOOP = os.path.join(REPO, "loops.d", "kagi-ban")
FIXTURE = os.path.join(REPO, "pagekit", "reference", "fixture-scan.json")

sys.path.insert(0, os.path.join(REPO, "bin"))
import page_envelope  # noqa: E402


def make_stub_av(dirpath, scan_json_path):
    stub = os.path.join(dirpath, "av")
    with open(stub, "w") as f:
        f.write("#!/usr/bin/env bash\n"
                'if [ "$1" = "--version" ]; then echo "av 0.0-stub"; exit 0; fi\n'
                f'cat "{scan_json_path}"\n')
    os.chmod(stub, 0o755)
    return stub


class KagiBanPrecheckTests(unittest.TestCase):
    def run_precheck(self, root, scan_json_path):
        out_dir = os.path.join(root, "state", "runs", "test-run")
        os.makedirs(out_dir, exist_ok=True)
        stub = make_stub_av(root, scan_json_path)
        env = dict(os.environ, AV_BIN=stub, OUT_DIR=out_dir, LOOPS_ROOT=root,
                   LOOP_NAME="kagi-ban", RUN_ID="test-run", WORKDIR=root)
        proc = subprocess.run(["bash", os.path.join(LOOP, "precheck.sh")],
                              capture_output=True, text=True, env=env, cwd=LOOP)
        return proc, out_dir

    def test_first_run_labels_everything_new(self):
        with tempfile.TemporaryDirectory() as root:
            proc, out_dir = self.run_precheck(root, FIXTURE)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("first_run=yes", proc.stdout)
            self.assertIn("NEW av:github-cli:", proc.stdout)
            self.assertNotIn("ONGOING", proc.stdout)
            self.assertTrue(os.path.isfile(
                os.path.join(out_dir, "loop-data.commit", "scan-prev.json")))

    def test_unchanged_world_is_all_ongoing_and_ids_stable(self):
        with tempfile.TemporaryDirectory() as root:
            proc1, out_dir = self.run_precheck(root, FIXTURE)
            committed = os.path.join(root, "state", "loop-data", "kagi-ban")
            os.makedirs(committed, exist_ok=True)
            os.replace(os.path.join(out_dir, "loop-data.commit", "scan-prev.json"),
                       os.path.join(committed, "scan-prev.json"))
            proc2, _ = self.run_precheck(root, FIXTURE)
            self.assertIn("new=0", proc2.stdout)
            self.assertIn("resolved=0", proc2.stdout)
            ids1 = sorted(line.split()[1] for line in proc1.stdout.splitlines()
                          if line.startswith(("NEW ", "ONGOING ")))
            ids2 = sorted(line.split()[1] for line in proc2.stdout.splitlines()
                          if line.startswith(("NEW ", "ONGOING ")))
            self.assertEqual(ids1, ids2)


class KagiBanRendererTests(unittest.TestCase):
    def test_renderer_passes_the_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "page.html")
            proc = subprocess.run(
                [sys.executable, os.path.join(LOOP, "render_page.py"), FIXTURE,
                 "--loop", "kagi-ban", "--run-id", "test-run", "-o", out,
                 "--host", "fixture", "--av-version", "0.0-stub"],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            errors = page_envelope.check_page(out, expect_run_id="test-run",
                                              expect_loop="kagi-ban")
            self.assertEqual(errors, [])
            meta = page_envelope.read_meta(out)
            self.assertEqual(meta["page_class"], "snapshot")
            self.assertEqual(meta["totals"]["findings"], 5)


if __name__ == "__main__":
    unittest.main()
```

Run the renderer once against the fixture to produce the vendored reference page:

```bash
cd ~/projects/loops
python3 loops.d/kagi-ban/render_page.py pagekit/reference/fixture-scan.json \
  --loop kagi-ban --run-id reference --host fixture --av-version 0.0-stub \
  -o pagekit/reference/reference-page.html
python3 bin/page_envelope.py check --file pagekit/reference/reference-page.html
```

- [ ] **Step 7: Run the tests, validate, full suite**

```bash
python3 -m unittest tests.test_kagi_ban -v     # all pass
bin/loopctl validate kagi-ban                   # passes (twelve SPEC sections, executable render.sh)
bash tests/run-tests.sh                         # whole suite green
```

- [ ] **Step 8: Commit**

```bash
cd ~/projects/loops
git add loops.d/kagi-ban tests/test_kagi_ban.py pagekit/reference/reference-page.html
git commit -m "feat(kagi-ban): av exposure audit — first page-enabled loop (pilot)"
git push
```

---

### Task 9: Caddy vhost fix (machine-local, live)

**Files:**
- Modify: `~/.config/dev-tailnet/Caddyfile` (the `@loops` block, lines ~130–137) — NOT in the loops repo; follow `~/.config/dev-tailnet/WARMSTART_CADDY_CLEANUP.md` for the reload procedure.

**Interfaces:**
- Consumes: `dashboard/loops.html`, `dashboard/reports.html`, `reports/**` (Tasks 4, 6).
- Produces: `https://loops.example.ts.net/` (dashboard), `/reports.html`, `/reports/<name>/…`.

- [ ] **Step 1: Replace the `@loops` block**

Current block rewrites every path to `/loops.html`. Replace with (path-scoped roots — NEVER `root * <repo>`; `state/`, `loops.d/`, `bin/` stay unreachable by construction):

```caddyfile
	@loops host loops.example.ts.net
	handle @loops {
		handle_path /reports/* {
			root * /Users/llm/projects/loops/reports
			file_server
		}
		handle {
			root * /Users/llm/projects/loops/dashboard
			try_files {path} /loops.html
			file_server
		}
	}
```

- [ ] **Step 2: Reload Caddy per the runbook**

Use the `launchctl kickstart` command documented in `~/.config/dev-tailnet/WARMSTART_CADDY_CLEANUP.md` — never the admin `:2019` API.

- [ ] **Step 3: Acceptance curls (all over https — ts.net is HSTS-preloaded; an http 200 proves nothing)**

```bash
curl -sk -o /dev/null -w '%{http_code}\n' https://loops.example.ts.net/                       # 200
curl -sk https://loops.example.ts.net/ | grep -q '<h1>loops</h1>' && echo dashboard-ok
curl -sk -o /dev/null -w '%{http_code}\n' https://loops.example.ts.net/reports.html            # 200
curl -sk -o /dev/null -w '%{http_code}\n' 'https://loops.example.ts.net/state/loops.sqlite'    # 200 from try_files fallback is WRONG — must be the dashboard HTML, verify:
curl -sk 'https://loops.example.ts.net/state/loops.sqlite' | head -c 100 | grep -q 'SQLite' && echo LEAK || echo state-unreachable-ok
curl -sk 'https://loops.example.ts.net/reports/../state/loops.sqlite' | head -c 100 | grep -q 'SQLite' && echo LEAK || echo traversal-ok
```

Expected: `dashboard-ok`, both `…-ok` lines, no `LEAK`. (The `try_files` fallback serves `loops.html` for unknown paths — same as today's rewrite — so the sqlite check verifies CONTENT, not status code.)

- [ ] **Step 4: Verify page reachability once kagi-ban has run (after Task 10)**

```bash
curl -sk -o /dev/null -w '%{http_code}\n' https://loops.example.ts.net/reports/kagi-ban/latest.html  # 200
```

No commit in the loops repo (machine-local config). Note the change in the dev-tailnet runbook file if it keeps a change log.

---

### Task 10: Pilot live gauntlet (supervised — the /goal proof)

**Files:** none new — this is the live acceptance run per spec §7 + `~/projects/av-audit/LOOP_HANDOFF.md`.

- [ ] **Step 1: Supervised run**

```bash
cd ~/projects/loops
bin/loopctl validate kagi-ban        # must pass
bin/loopctl run kagi-ban             # supervised foreground run
```

Expected stdout includes `page promoted: reports/kagi-ban/<stamp>.html`.

- [ ] **Step 2: Read the run against ground truth (the handoff's acceptance checks)**

- `reports/kagi-ban/latest.html` exists, non-empty; `bin/page_envelope.py meta --file reports/kagi-ban/latest.html` prints meta with `page_class: snapshot`.
- Findings ≈ the 20-finding baseline (18 high / 2 medium) from `~/projects/av-audit/scan-latest.json` — diff empty or explained (`first_run=yes` on the first run labels all as NEW; that is correct and expected).
- `sqlite3 state/loops.sqlite "SELECT runner_status, effective_status, headline FROM runs ORDER BY started_at DESC LIMIT 1"` → `completed`, effective status from finding severities (alert while 18 highs are undismissed — correct posture).
- No lingering process: `pgrep -f "Automic Vault" || echo none` → `none`; no GUI app launched.
- Dashboard: `dashboard/loops.html` row for kagi-ban links `page`; `dashboard/reports.html` lists the entry with totals chips, no stale badge.
- Tailnet: Task 9 Step 4 curl returns 200.

- [ ] **Step 3: Second supervised run (id stability + diff behavior)**

Run `bin/loopctl run kagi-ban` again. Verify: `new=0`, all findings ONGOING with identical `finding_id`s (`sqlite3 state/loops.sqlite "SELECT finding_id, times_seen FROM findings WHERE loop_name='kagi-ban'"` shows `times_seen` = 2 for every id, no new rows); the page's stale badge is absent (fresh `run_id`).

- [ ] **Step 4: Exercise one disposition**

```bash
bin/loopctl findings kagi-ban
bin/loopctl dismiss kagi-ban 'av:<the-homebrew-PATH-finding-id>' --note "known-accepted PATH exposure (LOOP_HANDOFF)"
bin/loopctl run kagi-ban
```

Verify: the dismissed finding is absent from `reports/kagi-ban/latest.json` and greyed on the dashboard, but the exposure STILL renders on the snapshot page (spec §1.4 — dismissal silences the nag channel, not the audit document).

- [ ] **Step 5: Install (goes live on launchd)**

```bash
bin/loopctl install kagi-ban
```

Install self-verifies via kickstart (INTERFACES §8.1): confirm a fresh run row with a non-failed `runner_status` appeared. Then `bin/loopctl status kagi-ban`.

- [ ] **Step 6: Declare the /goal**

The pathway is proven if kagi-ban shipped with ZERO special-casing in the harness (grep the diff since Task 4 for `kagi-ban` outside `loops.d/kagi-ban/`, `tests/test_kagi_ban.py`, and `pagekit/reference/` — must be empty). Report the result to generalissimo with the tailnet page URL.

---

## Self-review notes (spec-coverage check)

- Spec §1.1–1.8 → Tasks 1, 4 (decisions encoded in amendment + runner). §2 contract → Tasks 2 (gate), 3 (kit), 8 (conforming renderer). §3 kit → Task 3 (+ reference page in Task 8). §4 mechanics → Tasks 1, 2, 4, 5. §5 surfaces → Tasks 6, 9. §6 loop-data/q12/import-delta → Tasks 4 (commit pattern), 7 (docs). §7 pilot → Tasks 8, 10. §8 test matrix → Tasks 2, 4, 5, 6, 8 (each case named in a test). §9 build order → task order. Appendix A → Task 9.
- Deliberate deviation from spec §7: the copy-delta note lives in `render_page.py`'s header + `docs/REPORT_PAGES.md`, NOT in av-audit's README — the handoff forbids modifying `~/projects/av-audit/` and that rule wins over the spec's README-note sentence.
- Type consistency: `page_envelope.check_page/read_meta/MAX_PAGE_BYTES` (Task 2) are the names Tasks 4 (CLI `check`), 6 (module), 8 (tests) use. Env names in Task 4 (`LATEST_JSON LOOP_DATA_DIR PAGEKIT PAGE_OUT`) match Tasks 8's scripts and the amendment text. Finding-id `av:<source>:<sha8>` consistent across precheck, prompt, SPEC, tests.

---

## Shipped (as built) — 2026-07-30

Executed via subagent-driven development with external CLI agents ("peons") as implementers:
grok for tasks 1–3, 5, 7–8; codex for the delicate runner (4) and dashboard (6) tasks; Claude
reviewer subagents gated each task on spec + quality; tasks 9–10 done in-session. Commit range
`d6ebceb..44e0942` on main. Suite grew 615 → 649 hermetic tests.

### Deltas from the plan as written

- **Task 4 fake-engine knob:** the plan guessed `FAKE_CONTRACT_INVALID`; the real one is
  `FAKE_INVALID=1`. The runner tests also expanded from the plan's 7 cases to 19.
- **Task 6 anchors were stale.** The garden restyle (`be381f5`, a parallel effort) moved every
  line anchor and replaced the doc shell; `reports.html` was built in the garden idiom, not the
  plan's dark-slate snippets. The plan's logic (queries, state dict, badge semantics, caps,
  fallbacks) survived unchanged. `.gitignore` gained `dashboard/reports.html`.
- **Task 7 symbol name:** `_SPEC_MD_TEMPLATE`, not the plan's `_SPEC_TEMPLATE`.
- **Task 8 renderer gained a fifth delta** beyond the plan's four (see below).
- **Task 9 launchd label:** the dev-tailnet runbook named `com.generalissimo.dev-tailnet.caddy`; the real
  service is `com.generalissimo.dev-tailnet.caddy`. Runbook corrected.

### What the live gauntlet changed (none of it findable hermetically)

1. **Run 1 did not promote a page.** The gate worked exactly as designed: `av`'s own finding prose
   ("plaintext access token: /Users/…") trips `bin/redact.py`'s generic KV rule, so the page failed
   redaction-clean while the run stayed `completed` — the never-fail-the-run invariant, proven
   live. Fixed by renderer **delta 5** (`neutralize_kv_phrases`, em-dash separator) + a regression
   test.
2. **`redact.py` was corrupting a finding identity.** In the redacted precheck digest,
   `av:gh-cli-hosts-token:<sha8> | …` lost everything after the keyword; the engine received and
   sqlite persisted `av:gh-cli-hosts-token:«redacted:secret»`. Stably corrupt, so id-stability
   checks passed. Fixed harness-side with a `(?<![A-Za-z0-9-])` lookbehind: hyphen compounds pass,
   underscore env-vars (`GITHUB_TOKEN=`) still redact, specific token patterns unaffected.
3. **The audit's world-view depended on its trigger.** The launchd run saw 16 exposures where shell
   runs saw 20, marked four real exposures resolved, and committed that as the baseline.
   `precheck.sh` now pins the login-shell PATH. **16 (14 high, 2 medium) is the canonical count** —
   `av` flags user-writable dirs that *precede* system paths, so the old 20 (and av-audit's own
   baseline) was an artifact of the Claude Code harness's PATH ordering. Consecutive runs now
   report `new=0 resolved=0`.
4. **Corrupt scan JSON now hard-fails the precheck** instead of reading as a clean machine (a
   deferred minor elevated after defect 3 demonstrated the "silently fewer findings" mode live).

### Goal verification

Zero special-casing: grepping the harness-side diff for `kagi-ban` returns only the worked-example
prose in `docs/REPORT_PAGES.md`. Dismissal semantics confirmed live — a dismissed finding is
suppressed from `latest.json` and greyed on the dashboard while still rendering on the snapshot
page (§1.4).
