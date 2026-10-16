"""Compact, recommendation-only Search findings before the bulky raw digest."""

import json


def intent_digest(snapshot, campaign_ids, fresh):
    lines = ["## Product-fit recommendations (28 complete days; recommendation only)"]
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("schema") != 2
        or not snapshot.get("review_window")
    ):
        return lines + [
            "- INPUT GAP: 28-day Search intent/action evidence is unavailable; do not substitute seven-day totals or broad evaluator verdicts."
        ]
    lines.append(
        "- Review window: "
        + json.dumps(snapshot["review_window"])
        + "; timezone="
        + str(snapshot.get("time_zone"))
    )
    lines.append(
        "- Policy: "
        + json.dumps(snapshot.get("intent_review_policy", {}), separators=(",", ":"))
    )
    lines.append(
        "- Native conversion actions: "
        + json.dumps(snapshot.get("conversion_evidence", {}), separators=(",", ":"))
    )
    if not fresh:
        lines.append(
            "- INPUT GAP: recommendations below are stale/provisional; refresh evidence before an attended decision."
        )
    owned = {str(cid) for cid in campaign_ids}
    rows = [
        r
        for r in snapshot.get("intent_recommendations", [])
        if str(r.get("campaign_id")) in owned
    ]
    shown, size = [], 0
    for row in rows:
        encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        if size + len(encoded.encode()) > 22000:
            break
        shown.append(encoded)
        size += len(encoded.encode())
    lines.append(
        "- Recommendation limits: "
        + json.dumps(
            {
                "eligible_cached_rows": len(rows),
                "shown": len(shown),
                "truncated": len(shown) < len(rows),
                "producer": snapshot.get("limits", {}).get("intent_recommendations"),
            }
        )
    )
    lines.extend("- INTENT_REVIEW " + row for row in shown)
    if not rows:
        lines.append(
            "- No explicit product-fit candidates in the owned cached recommendations. This does not establish clean targeting or complete semantic coverage."
        )
    lines.append(
        "- Treat these as CMP analyst recommendations, independent of Band E eligibility. Keep exact keyword resources and per-target metrics. Omit order.* and placement lines; scheduled jobs never execute these recommendations. Preserve sibling keywords and budget semantics. Read raw inputs/search-query-review.json when a displayed limit matters."
    )
    return lines
