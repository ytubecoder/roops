"""Read-only formatter for GC's versioned conversion decisions.

Never derives a performance verdict from CTR, impressions or a missing row.
The model receives the exact scope and blockers needed to review a proposal.
"""

import json


def decision_digest(scoreboard, variants=None):
    lines = ["## Versioned conversion evidence (review/shadow)"]
    if not isinstance(scoreboard, dict):
        return lines + ["- INPUT GAP: scoreboard unavailable; no performance decision."]
    attribution = scoreboard.get("attribution") or {}
    lines.append("- evidence window: " + json.dumps(attribution.get("evidence_window"), sort_keys=True))
    rows = [row for section in (scoreboard.get("networks") or {}).values()
            for row in (section or {}).get("rows") or []]
    for row in rows:
        vid = row.get("variant_id")
        if variants is not None and vid not in variants:
            continue
        decision = row.get("evaluator") or {}
        if decision.get("version") != 1 or not decision.get("decision_id"):
            lines.append(f"- {vid}: INPUT GAP: legacy/missing decision payload; no evaluator kill is actionable.")
            continue
        keys = ("version", "decision_id", "objective", "objective_note", "action", "band",
                "actionable", "reason", "targets", "proposed_targets", "window",
                "attribution_source", "attribution_age_s", "blockers", "uncertainty",
                "reference_cohort", "budget_effect", "authority")
        lines.append(f"- {vid}: " + json.dumps({k: decision.get(k) for k in keys}, sort_keys=True))
    lines.append("- Performance proposals require current versioned band E evidence and human review. Analyst restructures need a separate reason and exact scope.")
    lines.append("- Signup sessions are a proxy; repository_linked is not completed indexing. Modal events are diagnostic. Free-user costs and collected revenue may be unknown.")
    lines.append("- Recommendation disappearance is withdrawal, not execution or a successful outcome. Cite decision ID, reviewed decision, journal result and mature outcome separately.")
    return lines
