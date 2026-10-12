"""Read-only formatter for GC's versioned conversion decisions.

Never derives a performance verdict from CTR, impressions or a missing row.
The model receives the exact scope and blockers needed to review a proposal.

Compact by construction: the harness caps precheck output (INTERFACES.md §4,
64 KiB default), and the verbose form of this block alone ran 145 KB for
ads-google and 262 KB for ads-program, which pushed the authoritative
budget/journal/METRICS/prior-set blocks past the cap for a month. Values
shared by every row are printed once, blocker strings are deduplicated into a
legend, target fields print only where they differ from the block's defaults,
and `reason` is dropped when it is just the blockers joined. Nothing
per-variant is lost: every row keeps its decision id, blockers and targets
(including silent and paused ones).
"""

import json

_ROW_KEYS = (
    "version",
    "decision_id",
    "objective",
    "objective_note",
    "business_objective",
    "objective_status",
    "diagnostic_objective",
    "diagnostic_band",
    "action",
    "band",
    "actionable",
    "reason",
    "targets",
    "proposed_targets",
    "window",
    "attribution_source",
    "attribution_age_s",
    "blockers",
    "uncertainty",
    "reference_cohort",
    "budget_effect",
    "authority",
)
# Always printed per row, never hoisted into SHARED.
_PER_ROW = {"decision_id", "targets", "blockers", "reason"}
# Target identity is always printed as `leg:ad_group_id@campaign_id`.
_TARGET_ID_KEYS = ("leg", "ad_group_id", "campaign_id")
_TARGET_SHORT = {
    "ad_group_status": "ags",
    "campaign_status": "cs",
    "primary_status": "ps",
    "state": "st",
    "evidence_state": "ev",
    "surfaces": "sf",
    "observed_band_e": "obe",
    "actionable": "act",
}


def _dumps(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _scalar(value):
    """Bare token for simple strings, JSON otherwise — keeps rows greppable."""
    if isinstance(value, str) and value and not any(c in value for c in " ;[]=,\"'"):
        return value
    return _dumps(value)


def _compact_target(target, defaults):
    if not isinstance(target, dict):
        return _dumps(target)
    head = (
        f"{target.get('leg') or '-'}:{target.get('ad_group_id') or '-'}"
        f"@{target.get('campaign_id') or '-'}"
    )
    extra = []
    for k, v in sorted(target.items()):
        if k in _TARGET_ID_KEYS:
            continue
        if k in defaults and defaults[k] == _dumps(v):
            continue
        extra.append(f"{_TARGET_SHORT.get(k, k)}={_scalar(v)}")
    return " ".join([head] + extra)


def decision_digest(scoreboard, variants=None):
    lines = ["## Versioned conversion evidence (review/shadow)"]
    if not isinstance(scoreboard, dict):
        return lines + ["- INPUT GAP: scoreboard unavailable; no performance decision."]
    attribution = scoreboard.get("attribution") or {}
    lines.append(
        "- evidence window: "
        + json.dumps(attribution.get("evidence_window"), sort_keys=True)
    )
    rows = [
        row
        for section in (scoreboard.get("networks") or {}).values()
        for row in (section or {}).get("rows") or []
    ]
    decided = []  # (vid, decision) with a versioned payload
    legacy = []  # vids without one
    for row in rows:
        vid = row.get("variant_id")
        if variants is not None and vid not in variants:
            continue
        decision = row.get("evaluator") or {}
        if decision.get("version") not in (1, 2) or not decision.get("decision_id"):
            legacy.append(vid)
            continue
        decided.append((vid, decision))

    if decided:
        # SHARED: keys whose value is identical on every decided row.
        shared = {}
        if len(decided) > 1:
            for key in _ROW_KEYS:
                if key in _PER_ROW:
                    continue
                values = {_dumps(d.get(key)) for _, d in decided}
                if len(values) == 1:
                    value = decided[0][1].get(key)
                    if key == "attribution_age_s" and isinstance(value, (int, float)):
                        value = round(value)
                    shared[key] = value
        # TARGET DEFAULTS: most common value per non-identity target field.
        counts = {}
        for _, d in decided:
            for t in d.get("targets") or []:
                if not isinstance(t, dict):
                    continue
                for k, v in t.items():
                    if k in _TARGET_ID_KEYS:
                        continue
                    counts.setdefault(k, {}).setdefault(_dumps(v), 0)
                    counts[k][_dumps(v)] += 1
        defaults = {k: max(c, key=c.get) for k, c in counts.items()}
        # BLOCKERS legend: each distinct string once, rows reference ids.
        legend = {}
        for _, d in decided:
            for b in d.get("blockers") or []:
                legend.setdefault(str(b), f"B{len(legend) + 1}")

        lines.append(
            "- format: `- <variant>: id=<decision_id> blockers=<legend ids> <key>=<value> ... "
            "targets=[<leg>:<ad_group_id>@<campaign_id> <field>=<value> ...; ...]`. Keys in SHARED "
            "are not repeated per row; `reason` is omitted when it equals the blockers joined by "
            "'; '; a target shows only fields that differ from TARGET DEFAULTS. Target field "
            "short names: "
            + " ".join(f"{s}={k}" for k, s in _TARGET_SHORT.items())
            + "."
        )
        lines.append("- SHARED (identical on every row below): " + _dumps(shared))
        lines.append(
            "- TARGET DEFAULTS: "
            + _dumps(
                {
                    _TARGET_SHORT.get(k, k): json.loads(v)
                    for k, v in sorted(defaults.items())
                }
            )
        )
        lines.append("- BLOCKERS legend:")
        for text, bid in legend.items():
            lines.append(f"  {bid}: {text}")

        for vid, d in decided:
            parts = [f"id={d.get('decision_id')}"]
            parts.append(
                "blockers="
                + (",".join(legend[str(b)] for b in (d.get("blockers") or [])) or "-")
            )
            for key in _ROW_KEYS:
                if key in shared or key in ("decision_id", "blockers", "targets"):
                    continue
                value = d.get(key)
                if key == "reason":
                    if value == "; ".join(str(b) for b in (d.get("blockers") or [])):
                        continue
                elif key == "attribution_age_s" and isinstance(value, (int, float)):
                    value = round(value)
                parts.append(f"{key}={_scalar(value)}")
            targets = "; ".join(
                _compact_target(t, defaults) for t in (d.get("targets") or [])
            )
            parts.append(f"targets=[{targets}]")
            lines.append(f"- {vid}: " + " ".join(parts))

    for vid in legacy:
        lines.append(
            f"- {vid}: INPUT GAP: legacy/missing decision payload; no evaluator kill is actionable."
        )
    lines.append(
        "- Performance proposals require current versioned band E evidence and human review. Analyst restructures need a separate reason and exact scope."
    )
    lines.append(
        "- Acquisition objective: first successful codebase sync; business outcome: collected payment. Signup sessions and repository_linked are setup diagnostics, not completed syncing or evidence of qualified acquisition. Missing successful-sync evidence blocks performance recommendations; version 1 signup bands cannot override this objective. Free-user costs and collected revenue may be unknown."
    )
    lines.append(
        "- Recommendation disappearance is withdrawal, not execution or a successful outcome. Cite decision ID, reviewed decision, journal result and mature outcome separately."
    )
    return lines
