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
import plistlib
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
            '<path d="M5 1 L9.5 9 H0.5 Z" fill="var(--high)"/></svg>'
        )
    return (
        '<svg class="mk" viewBox="0 0 10 10" aria-hidden="true">'
        '<rect x="1" y="1" width="8" height="8" rx="1" fill="var(--med)"/></svg>'
    )


def detect_av_version():
    try:
        with open("/Applications/Automic Vault.app/Contents/Info.plist", "rb") as f:
            return plistlib.load(f).get("CFBundleShortVersionString", "")
    except (OSError, plistlib.InvalidFileException):
        return ""


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


# The <style> body below is a VERBATIM copy of pagekit/kit.css (minus that file's
# leading header comment). Pages must be self-contained, so the kit is inlined,
# not read from $PAGEKIT at render time — that keeps the page byte-deterministic
# for a given scan and means an unreadable kit.css can never break promotion.
# tests/test_kagi_ban.py::PagekitParityTests holds the two in sync; edit both.
PAGE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Exposure audit — $host</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><path d='M5 1 L9.5 9 H0.5 Z' fill='%23279a83'/></svg>">
<style>
:root{
  --bg:#0e0f12; --panel:#14161a; --line:#22252b; --line2:#2c3037;
  --ink:#e7e9ec; --sub:#9aa1ab; --mut:#5d6570;
  --accent:#279a83; --high:#d84f63; --med:#b48c1a;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
::selection{background:var(--accent);color:#06120f}
html{color-scheme:dark}
body{
  background:var(--bg);color:var(--ink);
  font:16px/1.55 -apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif;
  -webkit-font-smoothing:antialiased;
}
a{color:var(--accent);text-underline-offset:3px}
.wrap{max-width:1060px;margin:0 auto;padding:56px 40px 80px}
@keyframes rise{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.brow,.frow,.stat{animation:rise .45s cubic-bezier(.16,1,.3,1) both;
  animation-delay:calc(var(--i,0)*35ms)}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}

/* header */
.hd{display:grid;grid-template-columns:2fr 1fr;gap:32px;align-items:end;
  padding-bottom:36px}
.kicker{font:600 11px/1 var(--mono);letter-spacing:.22em;color:var(--accent);
  text-transform:uppercase;margin-bottom:22px}
.hero{font-size:clamp(64px,9vw,104px);font-weight:700;letter-spacing:-.045em;
  line-height:.9;font-family:var(--mono)}
.hero .unit{display:block;font:400 15px/1.4 -apple-system,sans-serif;
  letter-spacing:0;color:var(--sub);margin-top:14px}
.hero .unit strong{color:var(--ink);font-weight:600}
.meta{font:12px/2 var(--mono);color:var(--mut);text-align:right}
.meta b{color:var(--sub);font-weight:500}

/* stat strip */
.stats{display:grid;grid-template-columns:repeat(4,1fr);
  border-top:1px solid var(--line)}
.stat{padding:20px 20px 24px;border-left:1px solid var(--line)}
.stat:first-child{border-left:0;padding-left:0}
.stat .n{font:600 30px/1 var(--mono);letter-spacing:-.02em}
.stat .l{font-size:12px;color:var(--mut);margin-top:8px;
  display:flex;align-items:center;gap:7px}
.dot{width:8px;height:8px;border-radius:2px;display:inline-block}
.n.hi,.cell-sev.high{color:var(--high)} .n.md,.cell-sev.med{color:var(--med)}

/* bars */
h2{font-size:14px;font-weight:600;letter-spacing:.01em;margin:52px 0 6px}
.sub{font-size:13px;color:var(--mut);margin-bottom:22px}
.legend{display:flex;gap:22px;font-size:12px;color:var(--sub);margin-bottom:18px}
.legend span{display:flex;align-items:center;gap:7px}
.mk{width:10px;height:10px;flex:none}
.brow{display:grid;grid-template-columns:190px 1fr 44px;gap:14px;
  align-items:center;padding:5px 0}
.blabel{font:12px/1.2 var(--mono);color:var(--sub);text-align:right;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.btrack{display:flex;gap:2px;height:14px}
.seg{height:14px;border-radius:0 4px 4px 0;transition:filter .15s}
.seg:first-child{border-radius:2px 4px 4px 2px}
.seg.high{background:var(--high)} .seg.med{background:var(--med)}
.seg:hover{filter:brightness(1.25)}
.bcount{font:600 13px var(--mono);color:var(--sub)}

/* groups */
.group{margin-top:46px}
.ghead{display:flex;align-items:baseline;justify-content:space-between;
  border-bottom:1px solid var(--line2);padding-bottom:10px}
.ghead h3{font-size:15px;font-weight:600;letter-spacing:-.01em}
.gmeta{font:12px var(--mono);color:var(--mut)}
.gblurb{font-size:13px;color:var(--mut);margin:10px 0 4px;max-width:62ch}
.frow{border-bottom:1px solid var(--line)}
.frow summary{display:grid;grid-template-columns:16px 180px 1fr 52px;gap:14px;
  align-items:center;padding:13px 8px;cursor:pointer;list-style:none;
  transition:background .15s}
.frow summary::-webkit-details-marker{display:none}
.frow summary:hover{background:var(--panel)}
.frow summary:active{transform:scale(.997)}
.frow[open] summary{background:var(--panel)}
.cell-src{font:500 13px var(--mono)}
.cell-path{font:12px var(--mono);color:var(--sub);overflow-wrap:anywhere}
.cell-sev{font:600 11px var(--mono);letter-spacing:.08em;text-align:right}
.fbody{padding:6px 8px 22px 30px;max-width:74ch}
.fbody h4{font:600 11px/1 var(--mono);letter-spacing:.18em;color:var(--mut);
  text-transform:uppercase;margin:16px 0 8px}
.explain{font-size:14px;color:var(--ink)}
.sol{font-size:13.5px;color:var(--sub)}
.docs{margin-top:12px;font-size:13px}
.path{color:var(--sub)}

/* footer + tooltip */
footer{margin-top:64px;border-top:1px solid var(--line);padding-top:18px;
  font:11.5px/1.9 var(--mono);color:var(--mut)}
footer .cmd{color:var(--sub)}
#tip{position:fixed;z-index:10;background:#1b1e24;border:1px solid var(--line2);
  color:var(--ink);font:12px var(--mono);padding:5px 9px;border-radius:5px;
  pointer-events:none;white-space:nowrap;
  box-shadow:0 4px 16px rgba(4,6,10,.5)}
#tip[hidden]{display:none}

@media(max-width:760px){
  .wrap{padding:32px 18px 56px}
  .hd{grid-template-columns:1fr;gap:18px}
  .meta{text-align:left}
  .stats{grid-template-columns:repeat(2,1fr)}
  .stat:nth-child(3){border-left:0;padding-left:0}
  .brow{grid-template-columns:110px 1fr 36px}
  .frow summary{grid-template-columns:16px 1fr 52px}
  .cell-path{grid-column:2/4}
}
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
      rendered <b>$rendered</b>
    </p>
  </header>

  <div class="stats">
    <div class="stat" style="--i:1"><p class="n hi">$n_high</p>
      <p class="l"><span class="dot" style="background:var(--high)"></span>high severity</p></div>
    <div class="stat" style="--i:2"><p class="n md">$n_med</p>
      <p class="l"><span class="dot" style="background:var(--med)"></span>medium severity</p></div>
    <div class="stat" style="--i:3"><p class="n">$n_tools</p>
      <p class="l">tools affected</p></div>
    <div class="stat" style="--i:4"><p class="n">$n_paths</p>
      <p class="l">exposed paths</p></div>
  </div>

  <h2>Findings by tool</h2>
  <p class="sub">Count of open findings per detector source.</p>
  <div class="legend">
    <span><svg class="mk" viewBox="0 0 10 10"><path d="M5 1 L9.5 9 H0.5 Z" fill="var(--high)"/></svg>high</span>
    <span><svg class="mk" viewBox="0 0 10 10"><rect x="1" y="1" width="8" height="8" rx="1" fill="var(--med)"/></svg>medium</span>
  </div>
  <div class="bars">
$bars
  </div>

$groups

  <footer>
    data <span class="cmd">$scan_file</span> · produced by
    <span class="cmd">av scan --json</span> (exit 0 even with findings — alert on count)<br>
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
    ap.add_argument("--host", default=platform.node().removesuffix(".local"))
    ap.add_argument("--av-version", default=detect_av_version())
    ap.add_argument("--loop", required=True)
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()

    data = json.loads(args.scan_json.read_text())
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
