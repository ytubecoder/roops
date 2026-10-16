"""Bounded event digest that never presents a recent correction as a prefix."""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MAX_BYTES = 18000
OLDER_TEXT_CHARS = 220


def event_digest(payload, fetched_at, account_timezone=None):
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        return ["- INPUT GAP: program-events missing or malformed."]
    now = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    try:
        zone = ZoneInfo(account_timezone) if account_timezone else timezone.utc
    except (ValueError, TypeError, KeyError):
        zone = timezone.utc
    today = now.astimezone(zone).date()
    start = today - timedelta(days=27)
    lines = [
        f"- Recent event window: {start} through {today}, timezone={zone}; newest first, full event fields. Older text previews are explicitly incomplete.",
        "- Full source: inputs/program-events.json. Never infer a resolution or permission from a preview or omitted event. An omitted recent event is an evidence gap.",
    ]
    entries = []
    invalid = 0
    for index, event in enumerate(payload["events"]):
        if not isinstance(event, dict):
            invalid += 1
            continue
        try:
            day = datetime.fromisoformat(str(event.get("date"))).date()
        except (TypeError, ValueError):
            day = None
        # Uncertain dates receive full-field treatment, never a guessed-old cut.
        full = day is None or day >= start
        category = (
            "recent_full"
            if day is not None and start <= day <= today
            else ("date_unverified_full" if full else "older_preview")
        )
        entries.append(
            (full, day.isoformat() if day else "9999", index, category, event)
        )
    entries.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    shown = {"recent_full": 0, "date_unverified_full": 0, "older_preview": 0}
    omitted = {key: 0 for key in shown}
    omitted_chars = {key: 0 for key in shown}
    size = sum(len(line.encode()) + 1 for line in lines)
    for full, _, index, category, event in entries:
        visible = dict(event)
        text = event.get("text")
        original_chars = len(text) if isinstance(text, str) else 0
        if not full and isinstance(text, str):
            visible["text"] = text[:OLDER_TEXT_CHARS]
        hidden = original_chars - len(visible["text"]) if isinstance(text, str) else 0
        line = "- PROGRAM_EVENT " + json.dumps(
            {
                "source": f"inputs/program-events.json events[{index}]",
                "mode": category,
                "text_complete": hidden == 0,
                "omitted_text_chars": hidden,
                "event": visible,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        # Keep a complete recent event or explicitly omit it. A prefix can hide
        # the qualifier or correction that reverses its opening statement.
        if size + len(line.encode()) + 1 > MAX_BYTES - 1200:
            omitted[category] += 1
            omitted_chars[category] += original_chars
            continue
        lines.append(line)
        shown[category] += 1
        size += len(line.encode()) + 1
    lines.append(
        "- Program-event limits: "
        + json.dumps(
            {
                "byte_cap": MAX_BYTES,
                "total_events": len(payload["events"]),
                "shown": shown,
                "omitted_events": omitted,
                "omitted_event_text_chars": omitted_chars,
                "malformed_events": invalid,
            },
            separators=(",", ":"),
        )
    )
    if omitted["recent_full"] or omitted["date_unverified_full"] or invalid:
        lines.append(
            "- INPUT GAP: full recent/undated program events were omitted or malformed. Read the raw input before resolving affected findings; do not treat a prefix as a completed decision."
        )
    return lines
