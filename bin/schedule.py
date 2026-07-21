#!/usr/bin/env python3
"""bin/schedule.py — §5.1 schedule grammar parser. Single implementation,
imported by loopctl and dashboard/generate.py.

    parse(spec) -> {kind, launchd: {...}, expected_interval_s: int}

`expected_interval_s` is used for staleness detection on the dashboard.
For `manual`, `expected_interval_s == 0` is a sentinel meaning "infinite /
exempt from staleness" — manual loops are never flagged stale (§5.1,
§10 "manual loops are exempt").

All calendar times in the grammar are LOCAL, matching launchd semantics.
"""
import argparse
import json
import sys

WEEKDAYS = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}


def _parse_hhmm(text: str):
    parts = text.split(":")
    if len(parts) != 2:
        raise ValueError(f"invalid time {text!r}: expected HH:MM")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        raise ValueError(f"invalid time {text!r}: expected numeric HH:MM")
    if not (0 <= hour <= 23):
        raise ValueError(f"invalid hour in {text!r}: must be 0-23")
    if not (0 <= minute <= 59):
        raise ValueError(f"invalid minute in {text!r}: must be 0-59")
    return hour, minute


def parse(spec: str) -> dict:
    if spec is None or spec == "":
        raise ValueError("empty schedule spec")

    if spec == "manual":
        return {"kind": "manual", "launchd": {}, "expected_interval_s": 0}

    if spec.startswith("interval:"):
        raw = spec[len("interval:"):]
        if len(raw) < 2 or raw[-1] not in ("m", "h"):
            raise ValueError(
                f"invalid interval spec {spec!r}: expected interval:<N>m or interval:<N>h"
            )
        try:
            n = int(raw[:-1])
        except ValueError:
            raise ValueError(f"invalid interval spec {spec!r}: N must be an integer")
        if n <= 0:
            raise ValueError(f"invalid interval spec {spec!r}: N must be positive")
        seconds = n * 60 if raw[-1] == "m" else n * 3600
        return {
            "kind": "interval",
            "launchd": {"StartInterval": seconds},
            "expected_interval_s": seconds,
        }

    if spec.startswith("daily:"):
        raw = spec[len("daily:"):]
        hour, minute = _parse_hhmm(raw)
        return {
            "kind": "daily",
            "launchd": {"StartCalendarInterval": {"Hour": hour, "Minute": minute}},
            "expected_interval_s": 86400,
        }

    if spec.startswith("times:"):
        raw = spec[len("times:"):]
        times = raw.split(",")
        if not times or any(t == "" for t in times):
            raise ValueError(f"invalid times spec {spec!r}: expected comma-separated HH:MM list")
        entries = []
        for t in times:
            hour, minute = _parse_hhmm(t)
            entries.append({"Hour": hour, "Minute": minute})
        return {
            "kind": "times",
            "launchd": {"StartCalendarInterval": entries},
            "expected_interval_s": 86400 // len(entries),
        }

    if spec.startswith("weekly:"):
        raw = spec[len("weekly:"):]
        parts = raw.split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"invalid weekly spec {spec!r}: expected weekly:<day>:HH:MM")
        day_name, time_part = parts
        day_key = day_name.lower()
        if day_key not in WEEKDAYS:
            raise ValueError(
                f"invalid weekday {day_name!r} in {spec!r}: expected one of {sorted(WEEKDAYS)}"
            )
        hour, minute = _parse_hhmm(time_part)
        return {
            "kind": "weekly",
            "launchd": {
                "StartCalendarInterval": {
                    "Hour": hour,
                    "Minute": minute,
                    "Weekday": WEEKDAYS[day_key],
                }
            },
            "expected_interval_s": 7 * 86400,
        }

    if spec.startswith("monthly:"):
        raw = spec[len("monthly:"):]
        parts = raw.split(":", 1)
        if len(parts) != 2:
            raise ValueError(f"invalid monthly spec {spec!r}: expected monthly:<DD>:HH:MM")
        day_part, time_part = parts
        try:
            day = int(day_part)
        except ValueError:
            raise ValueError(f"invalid day-of-month {day_part!r} in {spec!r}")
        if not (1 <= day <= 31):
            raise ValueError(f"invalid day-of-month {day_part!r} in {spec!r}: must be 1-31")
        hour, minute = _parse_hhmm(time_part)
        return {
            "kind": "monthly",
            "launchd": {
                "StartCalendarInterval": {"Hour": hour, "Minute": minute, "Day": day}
            },
            "expected_interval_s": 30 * 86400,
        }

    raise ValueError(
        f"unrecognized schedule spec {spec!r}: expected one of manual, interval:<N>m|h, "
        "daily:HH:MM, times:HH:MM[,HH:MM...], weekly:<day>:HH:MM, monthly:<DD>:HH:MM"
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="schedule.py")
    sub = p.add_subparsers(dest="verb")
    parse_cmd = sub.add_parser("parse")
    parse_cmd.add_argument("spec")
    parse_cmd.add_argument("--json", action="store_true")

    args = p.parse_args(argv)
    if args.verb != "parse":
        p.print_usage(sys.stderr)
        return 2

    try:
        result = parse(args.spec)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result))
    else:
        print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
