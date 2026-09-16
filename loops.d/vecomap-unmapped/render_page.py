#!/usr/bin/env python3
"""vecomap-unmapped snapshot page — summary table + downscaled overlays.

Deterministic: no model, no network, no randomness. Overlays are inlined as
data: JPEG via the tracer venv's cv2 (≤ 600 px, quality 60). Images beyond
40 keys are dropped so the page stays under the 8 MiB envelope.
"""
from __future__ import annotations

import base64
import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_IMAGE_KEYS = 40
MAX_EDGE = 600
JPEG_QUALITY = 60

_KIT_HEADER_RE = re.compile(r"\A/\*.*?\*/\s*", re.DOTALL)


def _read_required(path: Path, what: str) -> str:
    if not path.is_file():
        sys.exit(f"FATAL: missing {what}: {path}")
    return path.read_text(encoding="utf-8")


def _strip_header(text: str) -> str:
    return _KIT_HEADER_RE.sub("", text.lstrip()).lstrip("\n")


def _jpeg_data_uri(path: Path) -> str | None:
    try:
        import cv2  # type: ignore
    except ImportError as exc:
        sys.exit(f"FATAL: cv2 unavailable in tracer venv: {exc}")
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        return None
    height, width = img.shape[:2]
    longest = max(height, width)
    if longest > MAX_EDGE:
        scale = MAX_EDGE / float(longest)
        img = cv2.resize(
            img,
            (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    ok, buf = cv2.imencode(
        ".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY]
    )
    if not ok:
        return None
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def main() -> None:
    out_dir = Path(os.environ.get("OUT_DIR") or "")
    pagekit = Path(os.environ.get("PAGEKIT") or "")
    page_out = os.environ.get("PAGE_OUT") or ""
    loop = os.environ.get("LOOP_NAME") or "vecomap-unmapped"
    run_id = os.environ.get("RUN_ID") or ""
    if not out_dir or not page_out:
        sys.exit("FATAL: OUT_DIR and PAGE_OUT required")

    summary_path = out_dir / "summary.json"
    if not summary_path.is_file():
        sys.exit(f"FATAL: missing summary.json: {summary_path}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(summary, dict):
        sys.exit("FATAL: summary.json is not an object")

    kit_css = _strip_header(_read_required(pagekit / "kit.css", "pagekit kit.css"))
    toggle_js = _strip_header(_read_required(pagekit / "toggle.js", "pagekit toggle.js"))

    generated = str(summary.get("generated_at") or "")
    if not generated:
        generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    traced = int(summary.get("traced") or 0)
    confident = list(summary.get("confident") or [])
    low = list(summary.get("low") or [])
    failed = list(summary.get("failed") or [])
    superseded = list(summary.get("superseded") or [])
    aging = list(summary.get("aging") or [])
    per_key = [r for r in (summary.get("per_key") or []) if isinstance(r, dict)]
    branch = summary.get("pushed_branch") or "none"
    pushed = bool(summary.get("pushed"))
    dry_run = bool(summary.get("dry_run"))

    envelope = {
        "meta": {
            "loop": loop,
            "run_id": run_id,
            "generated_at": generated,
            "title": "vecomap-unmapped — tracer estimates",
            "page_class": "snapshot",
            "totals": {
                "traced": traced,
                "confident": len(confident),
                "pushed": int(pushed),
                "low": len(low),
                "failed": len(failed),
                "aging": len(aging),
                "superseded": len(superseded),
            },
        },
        "data": {
            "pushed_branch": summary.get("pushed_branch"),
            "dry_run": dry_run,
            "confident": confident,
            "low": low,
            "failed": failed,
            "superseded": superseded,
            "aging": aging,
            "per_key": per_key,
            "fatal": summary.get("fatal"),
        },
    }
    env_block = (
        '<script type="application/json" id="report-data">'
        + json.dumps(envelope).replace("</", "<\\/")
        + "</script>"
    )

    stats = [
        ("traced", str(traced)),
        ("confident", str(len(confident))),
        ("pushed", "yes" if pushed else ("dry-run" if dry_run and branch != "none" else "no")),
        ("low", str(len(low))),
        ("failed", str(len(failed))),
        ("aging", str(len(aging))),
    ]
    stat_html = "".join(
        f'<div class="stat"><div class="n">{_esc(v)}</div>'
        f'<div class="l">{_esc(k)}</div></div>'
        for k, v in stats
    )

    table_rows = []
    for row in per_key:
        key = row.get("key") or "?"
        conf = row.get("confidence") or "—"
        status = row.get("status") or "—"
        table_rows.append(
            "<tr>"
            f"<td><code>{_esc(key)}</code></td>"
            f"<td>{_esc(status)}</td>"
            f"<td>{_esc(conf)}</td>"
            f"<td>{_esc(row.get('anchors'))}</td>"
            f"<td>{_esc(row.get('inliers'))}</td>"
            f"<td>{_esc(row.get('message'))}</td>"
            "</tr>"
        )
    if not table_rows:
        table_rows.append('<tr><td colspan="6">no keys traced this run</td></tr>')

    def _list_block(title: str, keys: list[object]) -> str:
        if not keys:
            return (
                f'<div class="group"><div class="ghead"><h3>{_esc(title)}</h3>'
                f'<span class="gmeta">0</span></div>'
                f'<p class="gblurb">none</p></div>'
            )
        items = "".join(f"<li><code>{_esc(k)}</code></li>" for k in keys)
        return (
            f'<div class="group"><div class="ghead"><h3>{_esc(title)}</h3>'
            f'<span class="gmeta">{len(keys)}</span></div>'
            f"<ul class=\"keys\">{items}</ul></div>"
        )

    cards = []
    for idx, row in enumerate(per_key):
        key = str(row.get("key") or "")
        if not key:
            continue
        conf = str(row.get("confidence") or "—")
        status = str(row.get("status") or "—")
        img_html = ""
        overlay = out_dir / "trace" / f"{key}.overlay.png"
        if idx < MAX_IMAGE_KEYS and overlay.is_file():
            uri = _jpeg_data_uri(overlay)
            if uri:
                img_html = (
                    f'<img class="ov" alt="overlay {_esc(key)}" src="{uri}">'
                )
        elif idx >= MAX_IMAGE_KEYS:
            img_html = '<p class="gblurb">overlay omitted (40-image cap)</p>'
        tiny = f"https://tinyurl.com/{key}"
        area = f"https://vecomap.pages.dev/#area={key}"
        cards.append(
            f'<div class="group" id="key-{_esc(key)}">'
            f'<div class="ghead"><h3><code>{_esc(key)}</code></h3>'
            f'<span class="gmeta">{_esc(status)} · {_esc(conf)}</span></div>'
            f'<p class="gblurb">anchors {_esc(row.get("anchors"))} · '
            f'inliers {_esc(row.get("inliers"))} · style {_esc(row.get("style"))}</p>'
            f'<p><a href="{_esc(tiny)}">tinyurl.com/{_esc(key)}</a>'
            f' · <a href="{_esc(area)}">vecomap #{_esc(key)}</a></p>'
            f"{img_html}</div>"
        )

    fatal = summary.get("fatal")
    fatal_html = (
        f'<p class="fatal">precheck error: {_esc(fatal)}</p>' if fatal else ""
    )
    branch_line = f"branch {branch}" + (
        " (committed, not pushed)" if dry_run and branch != "none" else ""
    )

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>vecomap-unmapped — tracer estimates</title>
<script>{toggle_js}</script>
<style>{kit_css}
.stats{{grid-template-columns:repeat(3,1fr)}}
table.keys{{width:100%;border-collapse:collapse;font:13px var(--mono);margin-top:12px}}
table.keys th,table.keys td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--hair);vertical-align:top}}
table.keys th{{color:var(--nibi);font-weight:600;font-size:11px;letter-spacing:.08em;text-transform:uppercase}}
ul.keys{{margin:10px 0 0 1.2em;font:13px var(--mono)}}
.ov{{max-width:600px;width:100%;height:auto;border:1px solid var(--hair);margin:10px 0 0}}
.fatal{{color:var(--shu);margin:12px 0}}
.hero{{font-size:clamp(40px,7vw,72px)}}
</style></head><body>
<div class="wrap">
<header class="hd">
  <div>
    <div class="kicker">vecomap-unmapped · snapshot · run {_esc(run_id)}
      <button id="theme-toggle" type="button" aria-label="toggle theme">◐</button></div>
    <h1 class="hero">estimates</h1>
    <p>{_esc(branch_line)}</p>
    {fatal_html}
  </div>
</header>
<div class="stats">{stat_html}</div>
<div class="group">
  <div class="ghead"><h3>this run</h3>
    <span class="gmeta">{traced} traced</span></div>
  <table class="keys">
    <thead><tr><th>key</th><th>status</th><th>confidence</th><th>anchors</th><th>inliers</th><th>note</th></tr></thead>
    <tbody>{"".join(table_rows)}</tbody>
  </table>
</div>
{_list_block("superseded candidates", superseded)}
{_list_block("aging provisionals", aging)}
{"".join(cards)}
<footer>generated {_esc(generated)} · loop {_esc(loop)} · overlays as data JPEG ≤600px q60 ·
max {MAX_IMAGE_KEYS} images</footer>
</div>
{env_block}
</body></html>"""

    Path(page_out).write_text(page, encoding="utf-8")


if __name__ == "__main__":
    main()
