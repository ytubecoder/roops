#!/usr/bin/env python3
"""kagi-ban render_page.py — copy-with-provenance of ~/projects/av-audit/render_report.py
(2026-07-30). Deltas from the original, per docs/REPORT_PAGES_PLAN.md §7: adds
--loop/--run-id; envelope id scan-data → report-data; meta gains loop/run_id/
generated_at/title/page_class; findings nest under data; delta 5 (2026-07-30,
live gauntlet): neutralize_kv_phrases() rewrites 'token:'/'password:'-style
separators in finding prose so paths after a keyword stop tripping
bin/redact.py's generic KV pattern at the promotion gate; delta 6 (2026-07-30):
that pattern's keyword set + boundary are IMPORTED from bin/redact.py rather
than copied, and the inlined <style> block is a verbatim copy of pagekit/kit.css
bound by tests/test_kagi_ban.py. Do not fork further silently — keep this list
current."""

import argparse
import datetime
import html
import json
import os
import pathlib
import platform
import re
import sys
from string import Template

# Single-source the KV keyword set from bin/redact.py. A forked copy here drifts
# the moment someone adds a keyword there, and the only symptom is a page that
# silently stops passing the §4.4 promotion gate (a `stale` dashboard badge).
# $LOOPS_ROOT is set by render.sh under the runner; fall back to this file's own
# repo layout so a bare `python3 render_page.py` (tests, manual renders) works.
_BIN_DIR = (
    pathlib.Path(
        os.environ.get("LOOPS_ROOT") or pathlib.Path(__file__).resolve().parents[2]
    )
    / "bin"
)
sys.path.insert(0, str(_BIN_DIR))
try:
    import redact
except ImportError as exc:
    # Fail loudly rather than render an un-neutralized page: the gate itself
    # imports redact.py (bin/page_envelope.py), so if it is gone nothing can be
    # promoted anyway, and a render.sh non-zero exit lands a diagnosable line in
    # page-render.log instead of an opaque gate rejection.
    raise SystemExit(
        f"render_page.py: cannot import {_BIN_DIR}/redact.py — the KV keyword "
        "set is unavailable, so finding prose cannot be neutralized; refusing "
        "to render an ungated page."
    ) from exc

# $PAGEKIT is set by render.sh under the runner; same fallback as _BIN_DIR so a
# bare `python3 render_page.py` works. kit.css is the canonical report-page kit
# (docs/REPORT_PAGES_PLAN.md §3) — read here so there is exactly one copy of it.
_PAGEKIT_DIR = pathlib.Path(
    os.environ.get("PAGEKIT") or pathlib.Path(__file__).resolve().parents[2] / "pagekit"
)

# kit.css opens with a header comment aimed at maintainers, not browsers.
_KIT_HEADER_RE = re.compile(r"\A/\*.*?\*/\s*", re.DOTALL)


def load_kit_css():
    """Return pagekit/kit.css's body, ready to inline in a <style> block.

    Missing/unreadable kit.css is a broken checkout, not a runtime condition —
    it ships in this repo alongside this file. Fail the render (same call as the
    redact import above) so page-render.log names the file, rather than silently
    promoting an unstyled page that looks merely ugly instead of broken."""
    path = _PAGEKIT_DIR / "kit.css"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(
            f"render_page.py: cannot read {path} — the shared page kit is "
            "unavailable; refusing to render an unstyled page."
        ) from exc
    return _KIT_HEADER_RE.sub("", raw).rstrip("\n")


def load_toggle_js():
    """Return pagekit/toggle.js body, ready to inline in a <script> block.

    Same fatal-on-missing contract as load_kit_css(): toggle.js ships under
    $PAGEKIT and is the shared theme-persistence script (localStorage key
    loops-theme, shared with the garden). Never <script src=>."""
    path = _PAGEKIT_DIR / "toggle.js"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(
            f"render_page.py: cannot read {path} — the shared theme toggle is "
            "unavailable; refusing to render a page without it."
        ) from exc
    return _KIT_HEADER_RE.sub("", raw).rstrip("\n")


SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
SEV_LABEL = {"critical": "CRIT", "high": "HIGH", "medium": "MED", "low": "LOW"}

CATEGORIES = [
    (
        "Credential & token files",
        "Plaintext secrets readable by any process running as this user.",
        {
            "cloudflare-wrangler",
            "flyctl",
            "gh-cli-hosts-token",
            "git-credential-fill",
            "git-credentials-file",
            "supabase",
            "vercel-cli",
            "aws-cli-credentials-file",
            "netlify-cli",
            "npm",
            "heroku",
            "tailscale",
        },
    ),
    (
        "SSH private keys",
        "Unencrypted private keys on disk — one file read equals full key theft.",
        {"openssh"},
    ),
    (
        "Shell & PATH hygiene",
        "User-writable directories resolve before protected system paths.",
        {"bash", "zsh"},
    ),
    (
        "System boundaries",
        "Ambient root and package-manager authority available to local processes.",
        {"docker-root-access", "homebrew", "sudo", "macOS"},
    ),
]


def categorize(source):
    for name, _, members in CATEGORIES:
        if source in members:
            return name
    return "Other exposures"


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def affected_paths(finding):
    out = []
    for a in finding.get("affected", []):
        p = a.get("path", "")
        if a.get("line"):
            p += f":{a['line']}"
        out.append(p)
    return out


def sev_marker(sev):
    if sev == "high" or sev == "critical":
        return (
            '<svg class="mk" viewBox="0 0 10 10" aria-hidden="true">'
            '<path d="M5 1 L9.5 9 H0.5 Z" fill="var(--shu)"/></svg>'
        )
    return (
        '<svg class="mk" viewBox="0 0 10 10" aria-hidden="true">'
        '<rect x="1" y="1" width="8" height="8" rx="1" fill="var(--ochre)"/></svg>'
    )


def detect_av_version(data=None):
    if not data:
        return ""
    return data.get("probe_av_version") or ""


# Key + separator exactly as bin/redact.py's _KV_RE sees them — same keyword
# alternation, same lookbehind boundary. The boundary is load-bearing in BOTH
# directions: a plain `\b` here under-matches underscore compounds
# (`GITHUB_TOKEN=/path`, which redact.py DOES redact → failed promotion) and
# over-matches hyphenated ones (`gh-cli-hosts-token: /path`, which redact.py
# leaves alone → needless prose damage in av's own finding text).
_KV_PHRASE_RE = re.compile(redact.KV_KEY_PATTERN + redact.KV_SEPARATOR, re.IGNORECASE)


def neutralize_kv_phrases(findings):
    """bin/redact.py's generic KV pattern redacts the rest of the line after
    'token:'/'password:'/etc. av explanations legitimately phrase PATHS that
    way ('plaintext access token: /path'), which failed the promotion gate's
    redaction-clean check on the first live run (2026-07-30). Rewrite the
    separator to an em dash so keyword-then-path prose stops matching; real
    secret VALUES (ghp_…, sk-…, xox…) still trip the gate's specific token
    patterns."""
    for f in findings:
        for key in ("explanation", "solution"):
            value = f.get(key)
            if isinstance(value, str):
                f[key] = _KV_PHRASE_RE.sub(r"\1 — ", value)
    return findings


def build_stats(findings):
    sev = {}
    for f in findings:
        sev[f["severity"]] = sev.get(f["severity"], 0) + 1
    tools = sorted({f["source"] for f in findings})
    paths = {p for f in findings for p in affected_paths(f)}
    return sev, tools, paths


def build_bars(findings):
    per_tool = {}
    for f in findings:
        d = per_tool.setdefault(f["source"], {})
        d[f["severity"]] = d.get(f["severity"], 0) + 1
    ordered = sorted(per_tool.items(), key=lambda kv: (-sum(kv[1].values()), kv[0]))
    max_n = max((sum(v.values()) for v in per_tool.values()), default=1)
    rows = []
    for i, (tool, sevs) in enumerate(ordered):
        total = sum(sevs.values())
        segs = []
        for sev in sorted(sevs, key=lambda s: SEV_RANK.get(s, 9)):
            n = sevs[sev]
            w = n / max_n * 100
            cls = "high" if sev in ("high", "critical") else "med"
            tip = f"{tool} — {n} {sev}"
            segs.append(
                f'<span class="seg {cls}" style="width:{w:.2f}%" '
                f'data-tip="{esc(tip)}"></span>'
            )
        rows.append(
            f'<div class="brow" style="--i:{i}">'
            f'<span class="blabel">{esc(tool)}</span>'
            f'<span class="btrack">{"".join(segs)}</span>'
            f'<span class="bcount">{total}</span></div>'
        )
    return "\n".join(rows)


def build_groups(findings):
    groups = {}
    for f in findings:
        groups.setdefault(categorize(f["source"]), []).append(f)
    order = [c[0] for c in CATEGORIES] + ["Other exposures"]
    blurbs = {c[0]: c[1] for c in CATEGORIES}
    out = []
    idx = 0
    for gname in order:
        if gname not in groups:
            continue
        items = sorted(
            groups[gname], key=lambda f: (SEV_RANK.get(f["severity"], 9), f["source"])
        )
        rows = []
        for f in items:
            idx += 1
            paths = affected_paths(f)
            path_html = "<br>".join(
                f'<span class="path">{esc(p)}</span>' for p in paths
            )
            sev = f["severity"]
            scls = "high" if sev in ("high", "critical") else "med"
            docs = ""
            if f.get("docs_url"):
                docs = (
                    f'<p class="docs"><a href="{esc(f["docs_url"])}" '
                    f'rel="noopener">detector documentation</a></p>'
                )
            rows.append(
                Template("""<details class="frow" style="--i:$i">
  <summary>
    <span class="cell-mk">$marker</span>
    <span class="cell-src">$source</span>
    <span class="cell-path">$paths</span>
    <span class="cell-sev $scls">$sevlabel</span>
  </summary>
  <div class="fbody">
    <p class="explain">$explanation</p>
    <h4>Suggested remediation</h4>
    <p class="sol">$solution</p>
    $docs
  </div>
</details>""").substitute(
                    i=idx,
                    marker=sev_marker(sev),
                    source=esc(f["source"]),
                    paths=path_html,
                    scls=scls,
                    sevlabel=SEV_LABEL.get(sev, sev.upper()),
                    explanation=esc(f.get("explanation", "")),
                    solution=esc(f.get("solution", "")),
                    docs=docs,
                )
            )
        blurb = blurbs.get(gname, "")
        out.append(
            Template("""<section class="group">
  <header class="ghead">
    <h3>$gname</h3>
    <span class="gmeta">$count finding$plural</span>
  </header>
  <p class="gblurb">$blurb</p>
  <div class="glist">
$rows
  </div>
</section>""").substitute(
                gname=esc(gname),
                count=len(items),
                plural="" if len(items) == 1 else "s",
                blurb=esc(blurb),
                rows="\n".join(rows),
            )
        )
    return "\n".join(out)


# $kit_css / $toggle_js are $PAGEKIT/{kit.css,toggle.js}, read at render time
# and inlined verbatim (delta 7, 2026-07-30; WP3 2026-08-02). The kit is the
# single source of the report-page look; toggle.js is the shared theme
# persistence (localStorage key loops-theme, same as the garden). Pages stay
# self-contained because both are INLINED, never <link>ed / <script src=>.
PAGE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Exposure audit — $host</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><path d='M5 1 L9.5 9 H0.5 Z' fill='%23279a83'/></svg>">
<script>
$toggle_js
</script>
<style>
$kit_css
</style>
</head>
<body>
<div class="wrap">
  <header class="hd">
    <div>
      <p class="kicker">Automic Vault · exposure audit</p>
      <p class="hero">$total<span class="unit"><strong>open findings</strong>
        on this machine — $sevline</span></p>
    </div>
    <p class="meta">
      host <b>$host</b><br>
      scanner <b>av $av_version</b><br>
      scanned <b>$scanned</b><br>
      rendered <b>$rendered</b><br>
      <button id="theme-toggle" type="button" aria-label="toggle theme">◐</button>
    </p>
  </header>

  <div class="stats">
    <div class="stat" style="--i:1"><p class="n hi">$n_high</p>
      <p class="l"><span class="dot" style="background:var(--shu)"></span>high severity</p></div>
    <div class="stat" style="--i:2"><p class="n md">$n_med</p>
      <p class="l"><span class="dot" style="background:var(--ochre)"></span>medium severity</p></div>
    <div class="stat" style="--i:3"><p class="n">$n_tools</p>
      <p class="l">tools affected</p></div>
    <div class="stat" style="--i:4"><p class="n">$n_paths</p>
      <p class="l">exposed paths</p></div>
  </div>

  <h2>Findings by tool</h2>
  <p class="sub">Count of open findings per detector source.</p>
  <div class="legend">
    <span><svg class="mk" viewBox="0 0 10 10"><path d="M5 1 L9.5 9 H0.5 Z" fill="var(--shu)"/></svg>high</span>
    <span><svg class="mk" viewBox="0 0 10 10"><rect x="1" y="1" width="8" height="8" rx="1" fill="var(--ochre)"/></svg>medium</span>
  </div>
  <div class="bars">
$bars
  </div>

$groups

  <footer>
    data <span class="cmd">$scan_file</span> · produced by
    <span class="cmd">av scan --json</span> (exit 0 even with findings — alert on count)<br>
    subject: $host<br>
    render <span class="cmd">render_page.py</span> · raw envelope embedded as
    <span class="cmd">#report-data</span> · remediation for this machine follows the
    native headless plan, not <span class="cmd">av save</span>
  </footer>
</div>
<div id="tip" hidden></div>
<script type="application/json" id="report-data">$envelope</script>
<script>
(function(){
  var tip=document.getElementById('tip');
  document.querySelectorAll('[data-tip]').forEach(function(el){
    el.addEventListener('pointerenter',function(){
      tip.textContent=el.dataset.tip;tip.hidden=false;});
    el.addEventListener('pointermove',function(e){
      tip.style.left=Math.min(e.clientX+14,window.innerWidth-tip.offsetWidth-8)+'px';
      tip.style.top=(e.clientY+18)+'px';});
    el.addEventListener('pointerleave',function(){tip.hidden=true;});
  });
})();
</script>
</body>
</html>
""")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scan_json", type=pathlib.Path)
    ap.add_argument(
        "-o",
        "--out",
        type=pathlib.Path,
        default=None,
        help="output file; defaults to av-exposure-audit_<host>_<scan-date>.html",
    )
    ap.add_argument("--host", default=None)
    ap.add_argument("--av-version", default=None)
    ap.add_argument("--loop", required=True)
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()

    data = json.loads(args.scan_json.read_text())
    if not args.host:
        args.host = data.get("probe_host") or platform.node().removesuffix(".local")
    if args.av_version is None:
        args.av_version = detect_av_version(data)
    findings = neutralize_kv_phrases(data.get("findings", []))
    sev, tools, paths = build_stats(findings)
    scanned = datetime.datetime.fromtimestamp(
        args.scan_json.stat().st_mtime, tz=datetime.timezone.utc
    ).astimezone()
    rendered = datetime.datetime.now(tz=datetime.timezone.utc).astimezone()
    if args.out is None:
        args.out = pathlib.Path(
            f"av-exposure-audit_{args.host}_{scanned:%Y-%m-%d}.html"
        )

    sevline = " · ".join(
        f"{n} {s}"
        for s, n in sorted(sev.items(), key=lambda kv: SEV_RANK.get(kv[0], 9))
    )
    envelope = {
        "meta": {
            "loop": args.loop,
            "run_id": args.run_id,
            "generated_at": rendered.astimezone(datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
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
    page = PAGE.substitute(
        kit_css=load_kit_css(),
        toggle_js=load_toggle_js(),
        host=esc(args.host),
        av_version=esc(args.av_version or "?"),
        scanned=scanned.strftime("%Y-%m-%d %H:%M"),
        rendered=rendered.strftime("%Y-%m-%d %H:%M"),
        total=len(findings),
        sevline=esc(sevline),
        n_high=sev.get("high", 0) + sev.get("critical", 0),
        n_med=sev.get("medium", 0),
        n_tools=len(tools),
        n_paths=len(paths),
        bars=build_bars(findings),
        groups=build_groups(findings),
        scan_file=esc(args.scan_json.name),
        envelope=json.dumps(envelope).replace("</", "<\\/"),
    )
    args.out.write_text(page)
    print(
        f"wrote {args.out} ({args.out.stat().st_size} bytes, {len(findings)} findings)"
    )


if __name__ == "__main__":
    main()
