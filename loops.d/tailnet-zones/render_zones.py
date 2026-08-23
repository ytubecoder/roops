#!/usr/bin/env python3
"""tailnet-zones render_zones.py — deterministic zone-diagram snapshot page.

Renders $OUT_DIR/zones-model.json (built by precheck's build_model.py) as a
self-contained report page on pagekit: kit.css + toggle.js inlined, envelope
id `report-data`, page_class snapshot. The zone-diagram vocabulary (posture
bands, zone cards, flow rows) is an extension styled entirely with kit tokens
plus one extra hue pair (family purple) defined for both themes.

Editorial strings from zones-meta.json (who/notes/points/lede) are trusted
repo-authored HTML and render unescaped; everything derived from the policy
(chip labels, ports, test targets) is escaped.
"""

import argparse
import datetime
import html
import json
import os
import pathlib
import re
from string import Template

_PAGEKIT_DIR = pathlib.Path(
    os.environ.get("PAGEKIT") or pathlib.Path(__file__).resolve().parents[2] / "pagekit"
)
_KIT_HEADER_RE = re.compile(r"\A/\*.*?\*/\s*", re.DOTALL)


def load_kit_asset(name):
    """kit.css / toggle.js body, header comment stripped. Missing is a broken
    checkout — fail the render so page-render.log names the file."""
    path = _PAGEKIT_DIR / name
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(
            f"render_zones.py: cannot read {path} — the shared page kit is "
            "unavailable; refusing to render without it."
        ) from exc
    return _KIT_HEADER_RE.sub("", raw).rstrip("\n")


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


ZONE_CSS = """
/* tailnet-zones extension — zone-diagram vocabulary on kit tokens.
   One extra hue pair beyond the kit: --fam (family purple), both themes. */
:root{--fam:#6E4FA3}
@media (prefers-color-scheme: dark){:root{--fam:#A98BD9}}
:root[data-theme="dark"]{--fam:#A98BD9}
:root[data-theme="light"]{--fam:#6E4FA3}
.hue-you{--zc:var(--koke)} .hue-dmz{--zc:var(--ochre)} .hue-app{--zc:var(--ai)}
.hue-fam{--zc:var(--fam)} .hue-off{--zc:var(--nibi)}
.lede{font-size:14.5px;color:var(--nibi);max-width:68ch;margin:10px 0 34px}
.lede strong{color:var(--sumi);font-weight:600}
.band{display:flex;align-items:baseline;gap:12px;margin:34px 0 14px}
.btag{font:700 12px var(--mono);letter-spacing:.13em;text-transform:uppercase;
  color:var(--zc);white-space:nowrap}
.bnote{font-size:12.5px;color:var(--nibi-faint)}
.band::after{content:"";flex:1;height:1px;align-self:center;opacity:.45;
  background:linear-gradient(to right,var(--zc),transparent)}
.zones{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
.zone{background:color-mix(in srgb,var(--zc) 6%,transparent);
  border:1px solid color-mix(in srgb,var(--zc) 40%,var(--hair));
  border-radius:6px;padding:15px 16px 16px}
.zone.hue-off{background:transparent;border-style:dashed}
.zhead{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin-bottom:2px}
.zname{font-weight:650;font-size:15px}
.zid{font:12px var(--mono);color:var(--zc);border-radius:999px;padding:1px 8px;
  border:1px solid color-mix(in srgb,var(--zc) 55%,transparent);white-space:nowrap}
.zwho{font-size:13px;color:var(--nibi);margin:0 0 10px}
.chips{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:10px}
.chip{font:12px var(--mono);background:var(--washi-shade);
  border:1px solid var(--hair2);border-radius:4px;padding:2px 7px;white-space:nowrap}
.chip.dim{color:var(--nibi-faint);border-style:dashed}
.chip.lbl{border:none;background:none;color:var(--nibi-faint);padding-left:0;font-size:11px}
.zpost{font-size:13px;margin:0}
.zpost b{font-weight:650}
.zpost .in{color:var(--zc)}
.flow{display:grid;gap:12px;align-items:center;padding:10px 14px;margin-bottom:8px;
  grid-template-columns:minmax(160px,1fr) minmax(190px,1.1fr) minmax(160px,1.4fr);
  background:color-mix(in srgb,var(--washi-shade) 60%,transparent);
  border:1px solid var(--hair);border-radius:6px}
.fsrc{text-align:right}
.fdst{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.fchip{display:inline-block;font:600 13px var(--mono);color:var(--zc);
  background:color-mix(in srgb,var(--zc) 9%,transparent);
  border:1px solid color-mix(in srgb,var(--zc) 50%,transparent);
  border-radius:4px;padding:3px 9px;white-space:nowrap}
.fnote{font-size:12px;color:var(--nibi-faint)}
.fmid{display:flex;flex-direction:column;align-items:center;gap:3px;min-width:0}
.ports{font:12px var(--mono);color:var(--nibi);white-space:nowrap}
.wire{position:relative;width:100%;height:2px;background:var(--sumi);opacity:.7}
.wire::after{content:"";position:absolute;right:-1px;top:-4px;
  border-left:8px solid var(--sumi);
  border-top:5px solid transparent;border-bottom:5px solid transparent}
.flow.deny{border-style:dashed;background:transparent}
.flow.deny .wire{opacity:.9;
  background:repeating-linear-gradient(90deg,var(--shu) 0 6px,transparent 6px 11px)}
.flow.deny .wire::after{border-left-color:var(--shu)}
.flow.deny .ports{color:var(--shu);font-weight:600}
.flow.pin{border-color:color-mix(in srgb,var(--ochre) 40%,var(--hair));
  background:color-mix(in srgb,var(--ochre) 5%,transparent)}
.xmark{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);
  background:var(--washi);color:var(--shu);font-weight:700;font-size:13px;
  line-height:1;padding:0 5px}
.whykill{font-size:12px;color:var(--nibi-faint)}
.trow{display:grid;grid-template-columns:220px 1fr;gap:14px;padding:10px 8px;
  border-bottom:1px solid var(--hair)}
.tsrc{font:500 13px var(--mono);text-align:right}
.tchips{display:flex;flex-wrap:wrap;gap:5px}
.tchip{font:12px var(--mono);border-radius:4px;padding:2px 7px;white-space:nowrap}
.tchip.ok{color:var(--koke);border:1px solid color-mix(in srgb,var(--koke) 50%,transparent)}
.tchip.no{color:var(--shu);border:1px solid color-mix(in srgb,var(--shu) 50%,transparent)}
.opts{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
  gap:14px;margin-top:8px}
.opt{background:color-mix(in srgb,var(--washi-shade) 60%,transparent);
  border:1px solid color-mix(in srgb,var(--koke) 45%,var(--hair));
  border-radius:6px;padding:16px 18px 18px}
.verdict{display:inline-block;font:600 11px var(--mono);letter-spacing:.1em;
  text-transform:uppercase;border-radius:999px;padding:2px 10px;margin-bottom:8px;
  color:var(--koke);border:1px solid color-mix(in srgb,var(--koke) 60%,transparent)}
.opt h3{margin:0 0 6px;font-size:15px}
.opt ul{margin:5px 0 0;padding-left:18px;font-size:13.5px}
.opt li{margin-bottom:6px}
.rejected{margin-top:10px;font-size:12.5px;color:var(--nibi-faint)}
.notes{border-top:1px solid var(--hair);margin-top:52px;padding-top:20px;
  font-size:13px;color:var(--nibi);max-width:72ch}
.notes p{margin:0 0 10px}
.notes b{color:var(--sumi);font-weight:600}
code{font-family:var(--mono);font-size:.9em}
@media(max-width:640px){
  .flow{grid-template-columns:1fr;gap:6px}
  .fsrc{text-align:left}
  .fmid{flex-direction:row;justify-content:flex-start;gap:10px}
  .wire{width:52px;flex:none}
  .trow{grid-template-columns:1fr}
  .tsrc{text-align:left}
}
"""


def fchips(chips):
    out = []
    for c in chips:
        out.append(f'<span class="fchip hue-{esc(c["hue"])}">{esc(c["label"])}</span>')
        if c.get("note"):
            out.append(f'<span class="fnote">{esc(c["note"])}</span>')
    return " ".join(out)


def flow_row(row, cls=""):
    note = (
        f'<span class="fnote">{esc(row["dst_note"])}</span>'
        if row.get("dst_note")
        else ""
    )
    return f"""<div class="flow{cls}">
  <div class="fsrc">{fchips(row["src"])}</div>
  <div class="fmid"><span class="ports">{esc(row["ports"])}</span><span class="wire"></span></div>
  <div class="fdst">{fchips(row["dst"])}{note}</div>
</div>"""


def deny_row(row):
    src = " ".join(
        f'<span class="fchip hue-{esc(c["hue"])}">{esc(c["label"])}</span>'
        for c in row["src"]
    )
    dst = " ".join(
        f'<span class="fchip hue-{esc(c["hue"])}">{esc(c["label"])}</span>'
        for c in row["dst"]
    )
    return f"""<div class="flow deny">
  <div class="fsrc">{src}</div>
  <div class="fmid"><span class="ports">blocked</span><span class="wire"><span class="xmark">✕</span></span></div>
  <div class="fdst">{dst} <span class="whykill">{esc(row["why"])}</span></div>
</div>"""


def zone_card(z):
    chips = []
    for c in z["chips"]:
        if c.get("kind") == "label":
            chips.append(f'<span class="chip lbl">{esc(c["text"])}</span>')
        else:
            dim = " dim" if c.get("dim") else ""
            chips.append(f'<span class="chip{dim}">{esc(c["text"])}</span>')
    posture = ""
    if z.get("posture_in") or z.get("posture_out"):
        posture = (
            f'<p class="zpost"><b class="in">In:</b> {esc(z["posture_in"])} · '
            f"<b>Out:</b> {esc(z['posture_out'])}</p>"
        )
    return f"""<div class="zone hue-{esc(z["hue"])}">
  <div class="zhead"><span class="zname">{esc(z["name"])}</span><span class="zid">{esc(z["badge"])}</span></div>
  <p class="zwho">{z["who"]}</p>
  <div class="chips">{"".join(chips)}</div>
  {posture}
</div>"""


def band_block(band):
    zones = "\n".join(zone_card(z) for z in band["zones"])
    return f"""<div class="band hue-{esc(band["hue"])}"><span class="btag">{esc(band["label"])}</span><span class="bnote">{esc(band["note"])}</span></div>
<div class="zones">
{zones}
</div>"""


def test_row(t):
    chips = [f'<span class="tchip ok">✓ {esc(a)}</span>' for a in t["accept"]]
    chips += [f'<span class="tchip no">✕ {esc(d)}</span>' for d in t["deny"]]
    return (
        f'<div class="trow"><div class="tsrc">{esc(t["src"])}</div>'
        f'<div class="tchips">{"".join(chips)}</div></div>'
    )


def section(title, note, body):
    return f'<h2>{esc(title)}</h2>\n<p class="sub">{esc(note)}</p>\n{body}'


PAGE = Template("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$title — $tailnet</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><rect x='1' y='1' width='8' height='8' rx='2' fill='%23279a83'/></svg>">
<script>
$toggle_js
</script>
<style>
$kit_css
$zone_css
</style>
</head>
<body>
<div class="wrap">
  <header class="hd">
    <div>
      <p class="kicker">$tailnet · tailnet zones · $source_label</p>
      <p class="hero">$device_count<span class="unit"><strong>devices under default-deny</strong>
        — anything not drawn here is blocked</span></p>
    </div>
    <p class="meta">
      policy <b>$policy_sha</b><br>
      source <b>$source</b><br>
      generated <b>$generated</b><br>
      run <b>$run_id</b><br>
      <button id="theme-toggle" type="button" aria-label="toggle theme">◐</button>
    </p>
  </header>

  <p class="lede">$lede</p>

  <div class="stats">
    <div class="stat" style="--i:1"><p class="n">$n_grants</p><p class="l">grants — the entire policy</p></div>
    <div class="stat" style="--i:2"><p class="n md">$n_pins</p><p class="l">pinned exceptions</p></div>
    <div class="stat" style="--i:3"><p class="n">$n_tests</p><p class="l">enforced invariants</p></div>
    <div class="stat" style="--i:4"><p class="n">$n_zones</p><p class="l">zones across $n_bands bands</p></div>
  </div>

  <h2>The groups</h2>
$bands

$flows_section

$defaults_section

$pins_section
$unclassified_section
$deny_section

$tests_section

  <h2>How it's built</h2>
  <div class="opts">
    <div class="opt">
      <span class="verdict">$verdict</span>
      <h3>$how_heading</h3>
      <ul>$how_points</ul>
    </div>
  </div>
  <p class="rejected">$rejected</p>

  <div class="notes">
$notes
  </div>

  <footer>
    data <span class="cmd">zones-model.json</span> · policy sha <span class="cmd">$policy_sha</span> ($source) ·
    inventory + prose <span class="cmd">site/zones-meta.json</span><br>
    render <span class="cmd">render_zones.py</span> · loop <span class="cmd">$loop</span> ·
    raw envelope embedded as <span class="cmd">#report-data</span> · regenerated every run — this page cannot drift from policy
  </footer>
</div>
<script type="application/json" id="report-data">$envelope</script>
</body>
</html>
""")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("model_json", type=pathlib.Path)
    ap.add_argument("-o", "--out", type=pathlib.Path, required=True)
    ap.add_argument("--loop", required=True)
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()

    try:
        model = json.loads(args.model_json.read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"render_zones.py: cannot load model: {exc}")

    counts = model["counts"]
    n_zones = sum(len(b["zones"]) for b in model["bands"])
    rendered = datetime.datetime.now(tz=datetime.timezone.utc)
    source_live = model["source"] == "live"

    unclassified_section = ""
    if model["unclassified"]:
        unclassified_section = section(
            "Unplaced grants",
            "— live policy rules the renderer cannot categorize; also raised as findings",
            "\n".join(flow_row(r) for r in model["unclassified"]),
        )

    envelope = {
        "meta": {
            "loop": args.loop,
            "run_id": args.run_id,
            "generated_at": rendered.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "title": f"{model['title']} — {model['tailnet']}",
            "page_class": "snapshot",
            "source": model["source"],
            "policy_sha": model["policy_sha"],
            "totals": {
                "grants": counts["policy.grants"],
                "pins": counts["policy.pins"],
                "tests": counts["policy.tests"],
                "devices": model["device_count"],
                "source_live": int(source_live),
            },
        },
        "data": model,
    }

    page = PAGE.substitute(
        kit_css=load_kit_asset("kit.css"),
        toggle_js=load_kit_asset("toggle.js"),
        zone_css=ZONE_CSS.strip(),
        title=esc(model["title"]),
        tailnet=esc(model["tailnet"]),
        source_label="live policy" if source_live else "repo snapshot",
        source=esc(model["source"]),
        policy_sha=esc(model["policy_sha"]),
        generated=rendered.astimezone().strftime("%Y-%m-%d %H:%M"),
        run_id=esc(args.run_id),
        device_count=model["device_count"],
        lede=model["lede"],
        n_grants=counts["policy.grants"],
        n_pins=counts["policy.pins"],
        n_tests=counts["policy.tests"],
        n_zones=n_zones,
        n_bands=len(model["bands"]),
        bands="\n".join(band_block(b) for b in model["bands"]),
        flows_section=section(
            "Zone flows",
            model["sections"]["flows_note"],
            "\n".join(flow_row(r) for r in model["flows"]),
        ),
        defaults_section=section(
            "Defaults",
            model["sections"]["defaults_note"],
            "\n".join(flow_row(r) for r in model["defaults"]),
        ),
        pins_section=section(
            "Pinned exceptions",
            model["sections"]["pins_note"],
            "\n".join(flow_row(r, " pin") for r in model["pins"]),
        ),
        unclassified_section=unclassified_section,
        deny_section=section(
            "Killed by default-deny",
            model["sections"]["deny_note"],
            "\n".join(deny_row(r) for r in model["deny_rows"]),
        ),
        tests_section=section(
            "Enforced invariants",
            model["sections"]["tests_note"],
            "\n".join(test_row(t) for t in model["tests"]),
        ),
        verdict=esc(model["how_built"]["verdict"]),
        how_heading=esc(model["how_built"]["heading"]),
        how_points="".join(f"<li>{p}</li>" for p in model["how_built"]["points"]),
        rejected=model["how_built"]["rejected"],
        notes="\n".join(f"    <p>{p}</p>" for p in model["notes"]),
        loop=esc(args.loop),
        envelope=json.dumps(envelope).replace("</", "<\\/"),
    )
    args.out.write_text(page)
    print(f"wrote {args.out} ({args.out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
