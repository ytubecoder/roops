#!/usr/bin/env python3
"""kagami snapshot page — nightly mirror check matrix.

Renders $OUT_DIR/matrix.json (written by precheck.sh) into a self-contained
report page on pagekit. Deterministic: no model calls, no network, no randomness.
"""

import argparse
import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

MARU, BATSU = "〇", "×"  # 〇 pass · × fail (marubatsu, never emoji)

CHECK_BLURBS = {
    "regenerate": "dashboard/generate.py over the pinned fixture root, pinned --now",
    "self-contained": "tests/html_selfcontained.py — the page fetches nothing on load",
    "name-leak": "no real loop names, paths, or hosts in the public artifact",
    "token-drift": "generate.py palette ↔ pagekit/kit.css still in lockstep",
}


def read_required(path: Path, what: str) -> str:
    if not path.is_file():
        sys.exit(f"FATAL: missing {what}: {path}")
    return path.read_text(encoding="utf-8")


def strip_header_comment(css: str) -> str:
    css = css.lstrip()
    if css.startswith("/*"):
        end = css.find("*/")
        if end != -1:
            css = css[end + 2 :].lstrip("\n")
    return css


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("matrix", help="path to matrix.json")
    ap.add_argument("--loop", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args()

    m = json.loads(Path(args.matrix).read_text(encoding="utf-8"))
    pagekit = Path(os.environ["PAGEKIT"])
    kit_css = strip_header_comment(
        read_required(pagekit / "kit.css", "pagekit kit.css")
    )
    toggle_js = strip_header_comment(
        read_required(pagekit / "toggle.js", "pagekit toggle.js")
    )

    checks = m.get("checks", [])
    passed = sum(1 for c in checks if c.get("ok"))
    failed = len(checks) - passed
    drift = bool(m.get("drift"))
    pr = m.get("pr") or {}
    pr_state = pr.get("state", "none")
    pr_url = pr.get("url") or ""
    live = str(m.get("live", "?"))
    nbytes = int(m.get("artifact_bytes") or 0)
    now = datetime.now(timezone.utc)

    envelope = {
        "meta": {
            "loop": args.loop,
            "run_id": args.run_id,
            "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "title": "kagami — mock-garden mirror check",
            "page_class": "snapshot",
            "totals": {
                "checks_passed": passed,
                "checks_failed": failed,
                "drift": int(drift),
                "pr_open": int(pr_state in ("opened", "updated", "open")),
                "artifact_bytes": nbytes,
            },
        },
        "data": m,
    }
    env_block = (
        '<script type="application/json" id="report-data">'
        + json.dumps(envelope).replace("</", "<\\/")
        + "</script>"
    )

    if drift:
        if pr_state in ("opened", "updated", "open"):
            drift_line = "drifted — refresh PR awaits merge"
        else:
            drift_line = "drifted — no PR (gate or machinery failure)"
    else:
        drift_line = "live page matches the regenerated artifact"

    rows = []
    for c in checks:
        name = str(c.get("name", "?"))
        ok = bool(c.get("ok"))
        mark = MARU if ok else BATSU
        cls = "ok" if ok else "bad"
        note = str(c.get("note") or "") or CHECK_BLURBS.get(name, "")
        rows.append(
            f'<details class="group" name="check"><summary class="ghead">'
            f'<span class="mark {cls}">{mark}</span> check {html.escape(name)}'
            f'</summary><div class="fbody"><p>{html.escape(note)}</p></div></details>'
        )

    pr_html = html.escape(pr_state)
    if pr_url:
        pr_html = f'<a href="{html.escape(pr_url)}">{html.escape(pr_state)}</a>'
    if pr.get("note"):
        pr_html += f" — {html.escape(str(pr['note']))}"

    stats = [
        ("checks", f"{passed}/{len(checks)}"),
        ("drift", "yes" if drift else "no"),
        ("PR", pr_state),
        ("live", live),
        ("bytes", f"{nbytes:,}"),
    ]
    stat_html = "".join(
        f'<div class="stat"><div class="k">{html.escape(k)}</div>'
        f'<div class="v">{html.escape(v)}</div></div>'
        for k, v in stats
    )

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>kagami — mock-garden mirror check</title>
<script>{toggle_js}</script>
<style>{kit_css}
.mark {{ font-family: var(--serif); margin-right: .4em; }}
.mark.ok {{ color: var(--koke, #6B7A5C); }}
.mark.bad {{ color: var(--shu, #C73E2B); }}
</style></head><body>
<div class="wrap">
<header class="hd">
  <div class="kicker">kagami 鏡 · snapshot · run {html.escape(args.run_id)}
    <button id="theme-toggle" type="button" aria-label="toggle theme">◐</button></div>
  <h1 class="hero">mock-garden mirror check</h1>
  <p>{html.escape(drift_line)}</p>
</header>
<div class="stats">{stat_html}</div>
{"".join(rows)}
<footer>generated {now.strftime("%Y-%m-%dT%H:%M:%SZ")} · loop kagami ·
artifact sha256 {html.escape(str(m.get("artifact_sha256") or "n/a")[:16])}</footer>
</div>
{env_block}
</body></html>"""

    Path(args.out).write_text(page, encoding="utf-8")


if __name__ == "__main__":
    main()
