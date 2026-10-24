#!/usr/bin/env python3
"""competitor-rank assembler: local files -> ranking dossier on stdout + rank-input.json.

No network. Reads (all read-only):
  - the competitor registry (maguyva-marketing gc-actions/specs/inputs/competitor-registry.yaml)
  - the newest competitor-pulse run dir holding pulse.json (state/runs/*-competitor-pulse-*)
  - reports/competitor-pulse/latest.json   (engine `signal` per slug)
  - reports/gtm-research/latest.json       (metrics.competitor_voices: liked/disliked per name)
  - reports/competitor-rank/latest.json    (this loop's previous ranking, absent on run one)
Writes $OUT_DIR/rank-input.json (the same data as JSON, for audit). A registry that cannot be
read prints an ERRORS line and exits 1 (precheck-failed); every other missing input is named in
the `sources:` footer so the engine can say "thin evidence" honestly.
"""

import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only on hosts without PyYAML
    yaml = None

HERE = Path(__file__).resolve().parent
REGISTRY_REL = "gc-actions/specs/inputs/competitor-registry.yaml"
PULSE_GLOB = "*-competitor-pulse-*"
CAPS_MAX = 6
NEWS_MAX = 5
REDDIT_MAX = 5
SITE_MAX = 6
RELEASES_MAX = 3
VOICE_MAX = 4
VOICE_LINKS_MAX = 3
CAND_NEWS_MAX = 3
CAND_REDDIT_MAX = 3
EXCERPT_CHARS = 160
TITLE_CHARS = 140
FIELD_CHARS = 240

# Our side, held constant. Sources:
#   - sentence: maguyva-marketing gc-actions/specs/inputs/I-04-I-05-canonical-copy.md §2 (frozen)
#   - capabilities + pricing: maguyva-marketing growth-console/brands/maguyva/persona.md line 22
MAGUYVA = {
    "what": (
        "a hosted MCP server that indexes your GitHub repositories, so coding agents query your "
        "code itself instead of guessing from a few pasted files"
    ),
    "capabilities": [
        "semantic, structural, graph, text and grounded search over a hosted index",
        "11 MCP tools",
        "279+ languages",
        "no local indexer",
        "works with Claude Code, Cursor, Codex CLI, Copilot CLI, Gemini CLI, Cline and 24+ MCP clients",
    ],
    "pricing": (
        "per workspace, not per seat: Free $0 (3 repos / 50K lines), Operator $19, Crew $49, "
        "Fleet $149 unlimited users"
    ),
    "buyer": "developers and small teams using coding agents on large codebases, self-serve",
}

_TAG_RE = re.compile(r"<[^>]{0,400}>")
_WS_RE = re.compile(r"\s+")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_RUN_TS_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})T")


# ------------------------------------------------------------------ helpers


def clip(value, n):
    """Plain one-line text: tags stripped, entities decoded, whitespace collapsed, capped."""
    if value is None:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", str(value)))
    text = _WS_RE.sub(" ", text).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def day(value):
    """YYYY-MM-DD from an ISO or RFC 2822 date, '' when unknown."""
    text = str(value or "").strip()
    if not text:
        return ""
    if _ISO_RE.match(text):
        return text[:10]
    try:
        return parsedate_to_datetime(text).strftime("%Y-%m-%d")
    except (TypeError, ValueError, IndexError):
        return clip(text, 16)


def run_day(run_id):
    m = _RUN_TS_RE.match(str(run_id or ""))
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def _read_json(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def contract_metrics(path):
    """(run_id, parsed metrics dict) of a promoted latest.json, or (None, None)."""
    doc = _read_json(path)
    if doc is None:
        return None, None
    raw = doc.get("metrics")
    try:
        metrics = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        metrics = None
    return doc.get("run_id"), metrics if isinstance(metrics, dict) else None


def _flat(value):
    """A YAML list item that parsed as a mapping ("Deep Search: agentic ...") back to text."""
    if isinstance(value, dict):
        return "; ".join(f"{k}: {v}" for k, v in value.items())
    return value


def _list(value):
    return [v for v in value if v not in (None, "")] if isinstance(value, list) else []


# ----------------------------------------------------------------- registry


def _fallback_registry(text):
    """Tolerant line parser for the registry's own layout, used only without PyYAML.

    Understands `competitors:` rows (`  - name:` at indent 2, scalar keys at indent 4,
    `>-` folded scalars, block lists at indent 6, a flow `threat: {level, note}`) and the
    `rejected:` list's flow names. Anything else is ignored.
    """
    rows, rejected, section, row, key, mode = [], [], None, None, None, None

    def scalar(v):
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            return v[1:-1]
        return v

    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        line = raw.strip()
        if indent == 0:
            section = line.split(":", 1)[0]
            row = None
            continue
        if section == "rejected" and line.startswith("- "):
            m = re.search(r"name:\s*([^,}]+)", line)
            if m:
                rejected.append({"name": scalar(m.group(1))})
            continue
        if section != "competitors":
            continue
        if indent == 2 and line.startswith("- "):
            row = {}
            rows.append(row)
            line, indent = line[2:], 4
        if row is None:
            continue
        if indent == 4 and ":" in line:
            key, _, val = line.partition(":")
            key, val = key.strip(), val.strip()
            mode = None
            if val in (">-", ">", "|", "|-"):
                row[key], mode = "", "fold"
            elif val == "":
                row[key], mode = [], "list"
            elif val.startswith("{") and key == "threat":
                lv = re.search(r"level:\s*([\w-]+)", val)
                nt = re.search(r"note:\s*\"([^\"]*)\"", val) or re.search(
                    r"note:\s*([^,}]+)", val
                )
                row[key] = {
                    "level": lv.group(1) if lv else "",
                    "note": nt.group(1).strip() if nt else "",
                }
            elif val.startswith("["):
                row[key] = [scalar(x) for x in val.strip("[]").split(",") if x.strip()]
            else:
                row[key] = scalar(val)
        elif indent >= 6 and key:
            if mode == "list" and line.startswith("- "):
                row[key].append(scalar(line[2:]))
            elif mode == "fold":
                row[key] = (row[key] + " " + line).strip()
    return {"competitors": rows, "rejected": rejected}


def load_registry(path):
    """(ranked rows, rejected names). Ranked = competitors with status tracked|watch."""
    text = Path(path).read_text()
    data = yaml.safe_load(text) if yaml is not None else _fallback_registry(text)
    if not isinstance(data, dict):
        raise TypeError("registry is not a mapping")
    rows, rejected = [], []
    for row in data.get("competitors") or []:
        if not isinstance(row, dict) or not row.get("slug"):
            continue
        if row.get("status") in ("tracked", "watch"):
            rows.append(row)
        elif row.get("status") == "rejected":
            rejected.append(str(row.get("name") or row["slug"]))
    for row in data.get("rejected") or []:
        if isinstance(row, dict) and row.get("name"):
            rejected.append(str(row["name"]))
    return rows, rejected, data.get("updated")


# -------------------------------------------------------------------- inputs


def latest_pulse(loops_root):
    """(run_id, pulse dict) from the newest competitor-pulse run dir holding pulse.json."""
    runs = Path(loops_root) / "state" / "runs"
    if not runs.is_dir():
        return None, None
    for d in sorted(runs.glob(PULSE_GLOB), key=lambda p: p.name, reverse=True):
        data = _read_json(d / "pulse.json")
        if data is not None:
            return d.name, data
    return None, None


def pulse_signals(loops_root):
    run_id, metrics = contract_metrics(
        Path(loops_root) / "reports/competitor-pulse/latest.json"
    )
    pulse = (metrics or {}).get("pulse")
    if not isinstance(pulse, dict):
        return run_id, {}
    out = {}
    for slug, v in pulse.items():
        if isinstance(v, dict) and v.get("signal"):
            out[str(slug)] = clip(v["signal"], FIELD_CHARS)
    return run_id, out


def voices(loops_root):
    """(run_id, observed day, {competitor name: voice row}) — mirrors GC _voice_by_name."""
    run_id, metrics = contract_metrics(
        Path(loops_root) / "reports/gtm-research/latest.json"
    )
    rows = (metrics or {}).get("competitor_voices")
    out, observed = {}, ""
    for v in rows if isinstance(rows, list) else []:
        if isinstance(v, dict) and v.get("competitor"):
            out[str(v["competitor"])] = v
            observed = observed or day(v.get("observed_at"))
    if metrics is None:
        return None, "", {}
    return run_id, observed or run_day(run_id), out


def prior_rank(loops_root):
    run_id, metrics = contract_metrics(
        Path(loops_root) / "reports/competitor-rank/latest.json"
    )
    rank = (metrics or {}).get("rank")
    if not isinstance(rank, dict) or not rank:
        return None, {}
    return run_id, {str(k): v for k, v in rank.items() if isinstance(v, dict)}


# ------------------------------------------------------------------ evidence


def _news(items, cap):
    out = []
    for n in _list(items):
        if isinstance(n, dict) and n.get("url") and n.get("title"):
            out.append(
                {
                    "date": day(n.get("date") or n.get("published")),
                    "title": clip(n["title"], TITLE_CHARS),
                    "url": str(n["url"]),
                }
            )
    return out[:cap]


def _reddit(items, cap):
    out, seen = [], set()
    for p in items:
        if (
            not isinstance(p, dict)
            or not p.get("url")
            or p["url"] in seen
            or not p.get("title")
        ):
            continue
        seen.add(p["url"])
        out.append(
            {
                "date": day(p.get("created_at")),
                "subreddit": clip(p.get("subreddit"), 40),
                "score": p.get("score"),
                "title": clip(p["title"], TITLE_CHARS),
                "excerpt": clip(p.get("excerpt"), EXCERPT_CHARS),
                "url": str(p["url"]),
            }
        )
    return out[:cap]


def _site(site):
    lines = []
    if not isinstance(site, dict):
        return lines
    for url, info in site.items():
        if not isinstance(info, dict):
            continue
        if info.get("first_reading"):
            lines.append({"url": str(url), "text": "first reading (no diff yet)"})
            continue
        for added in _list(info.get("added")):
            lines.append({"url": str(url), "text": "+ " + clip(added, FIELD_CHARS)})
        if not _list(info.get("added")) and info.get("changed"):
            lines.append({"url": str(url), "text": "changed (no added lines)"})
    return lines[:SITE_MAX]


def _releases(items):
    out = []
    for r in _list(items):
        if isinstance(r, dict) and (r.get("tag") or r.get("title")):
            out.append(
                {
                    "name": clip(r.get("tag") or r.get("title"), 60),
                    "date": day(r.get("published_at")),
                    "url": str(r.get("url") or ""),
                }
            )
    return out[:RELEASES_MAX]


def _traction(p):
    stars = 0
    for s in (
        (p.get("stars") or {}).values() if isinstance(p.get("stars"), dict) else []
    ):
        if isinstance(s, dict) and isinstance(s.get("stars"), int):
            stars += s["stars"]
    dl = 0
    for kind in (
        (p.get("downloads") or {}).values()
        if isinstance(p.get("downloads"), dict)
        else []
    ):
        for n in kind.values() if isinstance(kind, dict) else []:
            if isinstance(n, int):
                dl += n
    m = p.get("mentions") if isinstance(p.get("mentions"), dict) else {}
    return {
        "stars": stars,
        "downloads": dl,
        "hn_90d": m.get("hn_90d"),
        "reddit_30d": m.get("reddit_30d"),
    }


def competitor_entry(row, pulse_row, signal, voice):
    threat = row.get("threat") if isinstance(row.get("threat"), dict) else {}
    entry = {
        "name": clip(row.get("name"), 80),
        "slug": str(row["slug"]),
        "status": row.get("status"),
        "kind": clip(row.get("kind"), FIELD_CHARS),
        "capabilities": [
            clip(_flat(c), 160) for c in _list(row.get("capabilities"))[:CAPS_MAX]
        ],
        "pricing": clip(row.get("pricing"), FIELD_CHARS),
        "threat": {
            "level": clip(threat.get("level"), 20),
            "note": clip(threat.get("note"), 200),
        },
        "our_angle": clip(row.get("our_angle"), FIELD_CHARS),
        "pulse": pulse_row is not None,
        "signal": signal or "",
        "news": [],
        "reddit": [],
        "site": [],
        "releases": [],
        "traction": None,
        "voice": None,
    }
    if pulse_row is not None:
        mentions = (
            pulse_row.get("mentions")
            if isinstance(pulse_row.get("mentions"), dict)
            else {}
        )
        entry["news"] = _news(pulse_row.get("news"), NEWS_MAX)
        entry["reddit"] = _reddit(
            _list(pulse_row.get("reddit")) + _list(mentions.get("reddit_top")),
            REDDIT_MAX,
        )
        entry["site"] = _site(pulse_row.get("site"))
        entry["releases"] = _releases(pulse_row.get("releases"))
        entry["traction"] = _traction(pulse_row)
    if voice:
        entry["voice"] = {
            "liked": [clip(x, 200) for x in _list(voice.get("liked"))[:VOICE_MAX]],
            "disliked": [
                clip(x, 200) for x in _list(voice.get("disliked"))[:VOICE_MAX]
            ],
            "implication": clip(voice.get("implication"), FIELD_CHARS),
            "confidence": clip(voice.get("confidence"), 10),
            "links": [
                str(u) for u in _list(voice.get("evidence_links"))[:VOICE_LINKS_MAX]
            ],
        }
    v = entry["voice"] or {}
    entry["has_evidence"] = bool(
        entry["signal"]
        or entry["news"]
        or entry["reddit"]
        or entry["releases"]
        or [s for s in entry["site"] if s["text"].startswith("+")]
        or v.get("liked")
        or v.get("disliked")
    )
    return entry


def candidate_entry(c):
    return {
        "key": clip(c.get("key"), 60),
        "name": clip(c.get("name") or c.get("key"), 80),
        "kind": clip(c.get("kind"), FIELD_CHARS),
        "url": str(c.get("url") or ""),
        "score": c.get("score"),
        "what": clip(c.get("what"), FIELD_CHARS),
        "news": _news(c.get("news"), CAND_NEWS_MAX),
        "reddit_30d": c.get("reddit_30d"),
        "reddit": _reddit(_list(c.get("reddit_top")), CAND_REDDIT_MAX),
    }


# -------------------------------------------------------------------- render


def _fmt_score(v):
    return "-" if v is None else str(v)


def render_prior(prior):
    if not prior:
        return ["none"]
    out = []
    for key in sorted(prior):
        r = prior[key]
        scores = "/".join(
            _fmt_score(r.get(k))
            for k in ("features", "price", "positioning", "sentiment")
        )
        out.append(f"{key}: {clip(r.get('tier'), 12) or '-'} {scores}")
    return out


def _reddit_lines(items, indent=""):
    lines = []
    for p in items:
        score = f" ({p['score']})" if isinstance(p.get("score"), int) else ""
        lines.append(
            f"{indent}reddit: {p['date']} r/{p['subreddit']}{score} {p['title']} | {p['url']}".replace(
                "  ", " "
            )
        )
        if p.get("excerpt"):
            lines.append(f"{indent}  > {p['excerpt']}")
    return lines


def render_competitor(e):
    lines = [f"## {e['name']} ({e['slug']}) [{e['status']}]"]
    lines.append(f"kind: {e['kind'] or '-'}")
    if e["capabilities"]:
        lines.append("capabilities: " + "; ".join(e["capabilities"]))
    lines.append(f"pricing: {e['pricing'] or '-'}")
    t = e["threat"]
    lines.append(
        f"threat: {t['level'] or '-'}" + (f" ({t['note']})" if t["note"] else "")
    )
    lines.append(f"our_angle: {e['our_angle'] or '-'}")
    if not e["pulse"]:
        lines.append("pulse: no pulse data")
    else:
        if e["signal"]:
            lines.append(f"signal: {e['signal']}")
        tr = e["traction"] or {}
        lines.append(
            f"traction: stars {tr.get('stars', 0)}, downloads {tr.get('downloads', 0)}, "
            f"hn_90d {_fmt_score(tr.get('hn_90d'))}, reddit_30d {_fmt_score(tr.get('reddit_30d'))}"
        )
        for n in e["news"]:
            lines.append(f"news: {n['date'] or 'undated'} {n['title']} | {n['url']}")
        lines.extend(_reddit_lines(e["reddit"]))
        for s in e["site"]:
            lines.append(f"site: {s['url']} {s['text']}")
        if e["releases"]:
            lines.append(
                "releases: "
                + "; ".join(
                    f"{r['name']}" + (f" ({r['date']})" if r["date"] else "")
                    for r in e["releases"]
                )
            )
    v = e["voice"]
    if v:
        lines.append(
            "voice liked: " + ("; ".join(v["liked"]) if v["liked"] else "none")
        )
        lines.append(
            "voice disliked: " + ("; ".join(v["disliked"]) if v["disliked"] else "none")
        )
        if v["links"] and (v["liked"] or v["disliked"]):
            lines.append("voice links: " + " ".join(v["links"]))
    else:
        lines.append("voice: none")
    if not e["has_evidence"]:
        lines.append("evidence: none this cycle")
    return "\n".join(lines)


def render_candidate(c):
    lines = [f"### {c['name']} (cand:{c['key']})"]
    if c["kind"]:
        lines.append(f"kind: {c['kind']}")
    if c["url"]:
        lines.append(f"url: {c['url']}")
    lines.append(f"what: {c['what'] or '-'}")
    lines.append(
        f"score: {_fmt_score(c['score'])}, reddit_30d {_fmt_score(c['reddit_30d'])}"
    )
    for n in c["news"]:
        lines.append(f"news: {n['date'] or 'undated'} {n['title']} | {n['url']}")
    lines.extend(_reddit_lines(c["reddit"]))
    return "\n".join(lines)


def build(loops_root, registry_path, now=None):
    """(stdout text, rank-input dict). Raises on an unreadable registry."""
    now = now or datetime.now(timezone.utc)
    rows, rejected, reg_updated = load_registry(registry_path)
    pulse_run, pulse = latest_pulse(loops_root)
    signal_run, signals = pulse_signals(loops_root)
    voice_run, voice_day, voice_by_name = voices(loops_root)
    prior_run, prior = prior_rank(loops_root)

    by_slug = {}
    for c in (pulse or {}).get("competitors") or []:
        if isinstance(c, dict) and c.get("slug"):
            by_slug[str(c["slug"])] = c
    entries = [
        competitor_entry(
            r,
            by_slug.get(str(r["slug"])),
            signals.get(str(r["slug"])),
            voice_by_name.get(str(r.get("name"))),
        )
        for r in rows
    ]
    raw_cands = (pulse or {}).get("candidates")
    cands = [
        candidate_entry(c)
        for c in _list(raw_cands)
        if isinstance(c, dict) and c.get("key")
    ]

    sources = {
        "registry": f"{registry_path} (updated {reg_updated or '?'}; {len(rows)} ranked, {len(rejected)} rejected)",
        "pulse": f"{pulse_run} (generated {(pulse or {}).get('generated_at') or '?'})"
        if pulse_run
        else "missing (no competitor-pulse run with pulse.json)",
        "pulse_signals": signal_run
        or "missing (no reports/competitor-pulse/latest.json)",
        "candidates": f"{len(cands)} in pulse.json"
        if isinstance(raw_cands, list)
        else "absent from pulse.json",
        "voice": f"gtm-research {voice_day} ({voice_run})"
        if voice_run
        else "missing (no reports/gtm-research/latest.json)",
        "prior_rank": f"{run_day(prior_run)} ({prior_run})"
        if prior_run
        else "none (first run)",
    }

    parts = [
        "# MAGUYVA (reference)\n"
        + "\n".join(
            [
                f"what: Maguyva is {MAGUYVA['what']}.",
                "capabilities: " + "; ".join(MAGUYVA["capabilities"]),
                f"pricing: {MAGUYVA['pricing']}",
                f"buyer: {MAGUYVA['buyer']}",
            ]
        )
    ]
    parts.append(
        "# PRIOR RANK (slug: tier features/price/positioning/sentiment)\n"
        + "\n".join(render_prior(prior))
    )
    if rejected:
        parts.append(
            "# REJECTED (registry verdict; never rank or promote)\n"
            + ", ".join(clip(n, 60) for n in rejected)
        )
    parts.append("# COMPETITORS")
    parts.extend(render_competitor(e) for e in entries)
    parts.append("## CANDIDATES\n" + ("" if cands else "none"))
    parts.extend(render_candidate(c) for c in cands)
    parts.append("sources:\n" + "\n".join(f"{k}: {v}" for k, v in sources.items()))
    text = "\n\n".join(p.rstrip("\n") for p in parts) + "\n"

    doc = {
        "schema_version": 1,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": sources,
        "maguyva": MAGUYVA,
        "prior_rank": prior,
        "rejected": rejected,
        "competitors": entries,
        "candidates": cands,
    }
    return text, doc


# ---------------------------------------------------------------------- main


def registry_path():
    repo = os.environ.get("MAGUYVA_REPO") or str(
        Path.home() / "projects/maguyva-marketing"
    )
    return Path(repo) / REGISTRY_REL


def main():
    loops_root = Path(os.environ.get("LOOPS_ROOT") or HERE.parents[1])
    out_dir = Path(os.environ.get("OUT_DIR") or ".")
    try:
        text, doc = build(loops_root, registry_path())
    except Exception as exc:  # noqa: BLE001 - OSError, yaml.YAMLError, ValueError: never a traceback
        print(f"ERRORS:\nregistry: unreadable ({type(exc).__name__})")
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "rank-input.json").write_text(json.dumps(doc, indent=1))
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
