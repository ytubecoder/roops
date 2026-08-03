#!/usr/bin/env python3
"""tailnet-zones build_model.py — deterministic page-model builder.

Runs inside trusted precheck. Parses the Tailscale policy (hujson), classifies
every grant (zone flow / default / raw-IP pin / unclassified), joins the
display metadata from the tailnet-setup repo (inventory + prose stay in that
local-only repo, never in this one), computes every count and finding_id, and
emits: the render model ($OUT_DIR/zones-model.json), the staged baseline for
the runner's post-promotion commit, and the engine digest on stdout.

Drift safety rule: nothing from the policy is ever silently dropped — a grant
this script cannot classify still renders (in its own section) AND becomes a
finding, so the page can disagree with the policy only visibly.
"""

import argparse
import datetime
import hashlib
import json
import pathlib
import re
import sys

IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def strip_hujson(text):
    """Return plain JSON: // and /* */ comments removed (string-aware),
    trailing commas dropped."""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return re.sub(r",(\s*[\]}])", r"\1", "".join(out))


def load_policy(path):
    try:
        return json.loads(strip_hujson(pathlib.Path(path).read_text()))
    except (OSError, ValueError) as exc:
        print(f"ERROR: policy at {path} unparseable: {exc}", file=sys.stderr)
        sys.exit(1)


def sha12(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()[:12]


def rule_lines(policy):
    lines = []
    for kind in ("grants", "ssh", "tests"):
        for rule in policy.get(kind, []) or []:
            lines.append(f"{kind[:-1]} {json.dumps(rule, sort_keys=True)}")
    return lines


class Model:
    def __init__(self, meta, hosts):
        self.meta = meta
        self.hosts = hosts
        self.unmapped = []  # actors (IPs or names) with no display mapping

    def resolve(self, entry):
        """hosts alias -> IP; everything else passes through."""
        return self.hosts.get(entry, entry)

    def chip(self, entry):
        entry = self.resolve(entry)
        if IP_RE.match(entry):
            name = self.meta["ip_names"].get(entry)
            if name:
                return {"label": name, "hue": self.meta["ip_hues"].get(entry, "off")}
            self.unmapped.append(entry)
            return {"label": entry, "hue": "off"}
        actor = self.meta["actor_labels"].get(entry)
        if actor:
            chip = {"label": actor["label"], "hue": actor["hue"]}
            if actor.get("note"):
                chip["note"] = actor["note"]
            return chip
        self.unmapped.append(entry)
        return {"label": entry, "hue": "off"}


def grant_key(model, grant):
    srcs = "+".join(model.resolve(s) for s in grant.get("src", []))
    dsts = "+".join(model.resolve(d) for d in grant.get("dst", []))
    return f"{srcs}→{dsts}:{','.join(grant.get('ip', []))}"


def classify(model, grant):
    ends = [model.resolve(e) for e in grant.get("src", []) + grant.get("dst", [])]
    if any(IP_RE.match(e) for e in ends):
        return "pin"
    if grant.get("src") == ["autogroup:member"] and grant.get("dst") == [
        "autogroup:self"
    ]:
        return "default"
    if all(d.startswith("tag:") for d in grant.get("dst", [])):
        return "flow"
    return "unclassified"


def ports_label(ips):
    return "any port" if ips == ["*"] else " · ".join(ips)


def chip_text(chip, today):
    """Zone-card inventory chip: append staleness from last_seen."""
    text = chip.get("name", "")
    if chip.get("last_seen"):
        seen = datetime.date.fromisoformat(chip["last_seen"])
        days = (today - seen).days
        suffix = f"offline {days}d" if chip.get("offline") else f"{days}d"
        return {"text": f"{text} · {suffix}", "dim": True}
    if chip.get("label"):
        return {"text": chip["label"], "kind": "label"}
    return {"text": text, "dim": bool(chip.get("dim"))}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--policy", required=True)
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--source", required=True, choices=["live", "snapshot"])
    ap.add_argument("--fetch-error", default="")
    ap.add_argument("--prev", required=True)
    ap.add_argument("--out-model", required=True)
    ap.add_argument("--commit-prev", required=True)
    args = ap.parse_args()

    policy = load_policy(args.policy)
    snapshot = load_policy(args.snapshot)
    meta = json.loads(pathlib.Path(args.meta).read_text())
    today = datetime.date.today()

    model = Model(meta, policy.get("hosts", {}) or {})
    flows, defaults, pins, unclassified = [], [], [], []
    unannotated_pins, unclassified_ids = [], []

    for grant in policy.get("grants", []) or []:
        kind = classify(model, grant)
        key = grant_key(model, grant)
        ann = (
            meta["pin_annotations"].get(key)
            if kind == "pin"
            else meta["flow_annotations"].get(key)
        ) or {}
        row = {
            "src": [model.chip(s) for s in grant.get("src", [])],
            "dst": [model.chip(d) for d in grant.get("dst", [])],
            "ports": ann.get("ports_label") or ports_label(grant.get("ip", [])),
            "dst_note": ann.get("dst_note") or ann.get("note") or "",
            "temp": bool(ann.get("temp")),
            "key": key,
        }
        if kind == "pin":
            if not ann:
                unannotated_pins.append(key)
                row["dst_note"] = "undocumented pin — annotate in zones-meta.json"
            pins.append(row)
        elif kind == "default":
            defaults.append(row)
        elif kind == "flow":
            flows.append(row)
        else:
            gid = sha12(grant)[:8]
            unclassified_ids.append(gid)
            row["dst_note"] = f"unclassified grant {gid} — renderer cannot place it"
            unclassified.append(row)

    tests = [
        {
            "src": t.get("src", ""),
            "accept": t.get("accept", []) or [],
            "deny": t.get("deny", []) or [],
        }
        for t in policy.get("tests", []) or []
    ]

    bands = []
    for band in meta["bands"]:
        zones = [
            {
                "hue": z["hue"],
                "name": z["name"],
                "badge": z["badge"],
                "who": z["who"],
                "chips": [chip_text(c, today) for c in z["chips"]],
                "posture_in": z.get("posture_in", ""),
                "posture_out": z.get("posture_out", ""),
            }
            for z in meta["zones"]
            if z["band"] == band["id"]
        ]
        bands.append({**band, "zones": zones})

    policy_sha = sha12(policy)
    snapshot_sha = sha12(snapshot)
    snapshot_stale = args.source == "live" and policy_sha != snapshot_sha

    prev_path = pathlib.Path(args.prev)
    prev = None
    if prev_path.is_file():
        try:
            prev = json.loads(prev_path.read_text())
        except ValueError:
            prev = None
    current_lines = rule_lines(policy)
    if prev:
        prev_lines = set(prev.get("rule_lines", []))
        added = [ln for ln in current_lines if ln not in prev_lines]
        removed = sorted(prev_lines - set(current_lines))
        changed = prev.get("sha") != policy_sha
        first_run = False
    else:
        added, removed, changed, first_run = [], [], False, True

    findings = []
    if args.fetch_error:
        findings.append(
            (
                "ALERT",
                "policy:fetch-failed",
                "live policy fetch failed; page fell back to the repo snapshot",
                args.fetch_error,
            )
        )
    elif args.source == "snapshot":
        findings.append(
            (
                "WARN",
                "source:snapshot-fallback",
                "page generated from the repo snapshot, not the live policy",
                "no read credential at ~/.config/tailscale-policy-read.token — "
                "console-side policy edits are invisible until the snapshot is "
                "refreshed (see tailnet-setup WARMSTART for the credential decision)",
            )
        )
    if snapshot_stale:
        findings.append(
            (
                "WARN",
                "records:snapshot-stale",
                "live policy differs from the tailnet-setup repo snapshot",
                f"live sha {policy_sha} vs snapshot sha {snapshot_sha} — refresh "
                "docs/policy-live.hujson per workflows/policy-change.txt step 6",
            )
        )
    for actor in sorted(set(model.unmapped)):
        findings.append(
            (
                "WARN",
                f"policy:unmapped-actor:{actor}",
                f"policy references {actor} with no display mapping",
                "add it to ip_names/ip_hues or actor_labels in "
                "tailnet-setup/site/zones-meta.json — it renders raw until then",
            )
        )
    for key in unannotated_pins:
        findings.append(
            (
                "WARN",
                f"policy:unannotated-pin:{key}",
                f"pin {key} has no annotation",
                "document why it exists in pin_annotations in zones-meta.json "
                "(repo rule: every pin carries its why)",
            )
        )
    for gid in unclassified_ids:
        findings.append(
            (
                "WARN",
                f"policy:unclassified-grant:{gid}",
                f"grant {gid} fits no renderer category",
                "it renders in the fallback section; extend build_model.py "
                "classification if this shape is now expected",
            )
        )

    counts = {
        "policy.grants": len(policy.get("grants", []) or []),
        "policy.flows": len(flows),
        "policy.defaults": len(defaults),
        "policy.pins": len(pins),
        "policy.ssh_rules": len(policy.get("ssh", []) or []),
        "policy.tests": len(tests),
        "policy.changed": int(changed),
        "sync.live": int(args.source == "live"),
        "sync.snapshot_stale": int(snapshot_stale),
        "sync.unmapped_actors": len(set(model.unmapped)),
        "sync.unannotated_pins": len(unannotated_pins),
        "sync.unclassified_grants": len(unclassified),
        "inventory.devices": meta.get("device_count", 0),
    }

    out = {
        "model_version": 1,
        "source": args.source,
        "fetch_error": args.fetch_error,
        "policy_sha": policy_sha,
        "snapshot_sha": snapshot_sha,
        "snapshot_stale": snapshot_stale,
        "generated_date": today.isoformat(),
        "tailnet": meta["tailnet"],
        "title": meta["title"],
        "lede": meta["lede"],
        "device_count": meta.get("device_count", 0),
        "counts": counts,
        "bands": bands,
        "flows": flows,
        "defaults": defaults,
        "pins": pins,
        "unclassified": unclassified,
        "tests": tests,
        "deny_rows": meta["deny_rows"],
        "sections": meta["sections"],
        "how_built": meta["how_built"],
        "notes": meta["notes"],
    }
    pathlib.Path(args.out_model).write_text(json.dumps(out, indent=1))
    pathlib.Path(args.commit_prev).write_text(
        json.dumps({"sha": policy_sha, "rule_lines": current_lines}, indent=1)
    )

    # ---- engine digest ----
    source_note = {
        ("live", False): "live",
        ("snapshot", False): "snapshot (no read credential)",
        ("snapshot", True): f"snapshot (FETCH FAILED: {args.fetch_error})",
    }[(args.source, bool(args.fetch_error))]
    print("tailnet-zones sync digest")
    print(f"source: {source_note}")
    print(
        f"policy_sha: {policy_sha}  snapshot_sha: {snapshot_sha}  "
        f"match: {'n/a' if args.source == 'snapshot' else ('no — STALE' if snapshot_stale else 'yes')}"
    )
    print("counts: " + " ".join(f"{k}={v}" for k, v in counts.items()))
    if first_run:
        print("changes_vs_previous_run: first run — no baseline yet")
    elif not (added or removed):
        print("changes_vs_previous_run: none")
    else:
        print(f"changes_vs_previous_run: {len(added)} added, {len(removed)} removed")
        for line in added[:8]:
            print(f"  + {line}")
        for line in removed[:8]:
            print(f"  - {line}")
    print()
    if findings:
        print("FINDINGS (re-emit each line as one finding, finding_id verbatim):")
        for sev, fid, title, detail in findings:
            print(f"{sev} {fid} | {title} | {detail}")
    else:
        print("findings: none — clean sync")


if __name__ == "__main__":
    main()
