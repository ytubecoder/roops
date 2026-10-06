"""Bounded GitHub collection and validated merge of the llm search probe."""

import argparse
import hashlib
import json
import runpy
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPOS = (
    "abhigyanpatwari/GitNexus",
    "DeusData/codebase-memory-mcp",
    "zilliztech/claude-context",
    "oraios/serena",
)
PUBLIC = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "probes/gtm-public-search")
)


def collect(now=None, fetch=None):
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=30)).date().isoformat()
    stamp = now.isoformat()
    sources = []
    for repo in REPOS:
        query = f"repo:{repo} is:issue updated:>={since}"
        source = {
            "query": query,
            "source_type": "public_github",
            "provider": "github",
            "method": "issue_search",
            "window": {
                "start_date": since,
                "end_date": now.date().isoformat(),
                "days": 30,
            },
            "limit": 6,
            "status": "ok",
            "empty": True,
            "error": None,
            "records": [],
            "result_count": 0,
            "unique_count": 0,
            "duplicate_count": 0,
            "rejected_count": 0,
        }
        try:
            url = "https://api.github.com/search/issues?" + urlencode(
                {"q": query, "sort": "updated", "order": "desc", "per_page": 6}
            )
            if fetch:
                payload = fetch(url)
            else:
                request = Request(
                    url,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "Maguyva-GTM-research",
                    },
                )
                with urlopen(request, timeout=18) as response:
                    body = response.read(1_000_001)
                    if len(body) > 1_000_000:
                        raise ValueError("response too large")
                    payload = json.loads(body)
            if not isinstance(payload.get("items"), list):
                raise TypeError("missing issue results")
            rows = payload["items"][:6]
            source["result_count"] = len(rows)
            seen = set()
            for item in rows:
                if not isinstance(item, dict):
                    source["rejected_count"] += 1
                    continue
                address = item.get("html_url", "")
                if not PUBLIC["safe_url"](address) or not address.startswith(
                    f"https://github.com/{repo}/issues/"
                ):
                    source["rejected_count"] += 1
                    continue
                if address in seen:
                    source["duplicate_count"] += 1
                    continue
                seen.add(address)
                text = " ".join(
                    (
                        str(item.get("title", "")) + " " + str(item.get("body") or "")
                    ).split()
                )
                text = PUBLIC["excerpt"](text)
                source["records"].append(
                    {
                        "id": "pub-"
                        + hashlib.sha256(address.encode()).hexdigest()[:16],
                        "url": address,
                        "observed_at": stamp,
                        "published_at": PUBLIC["timestamp"](item.get("created_at")),
                        "text": text or "No excerpt",
                    }
                )
            source["unique_count"] = len(source["records"])
            source["empty"] = not rows
            if rows and source["rejected_count"] == len(rows):
                source.update(status="error", error="malformed")
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            source.update(
                status="error",
                empty=False,
                error=PUBLIC["error_category"](exc),
                records=[],
                unique_count=0,
                result_count=0,
                duplicate_count=0,
                rejected_count=0,
            )
        sources.append(source)
    return {"schema_version": 1, "generated_at": stamp, "sources": sources}


def merge_public(result, path, probe_error=None):
    now = datetime.fromisoformat(result["generated_at"])
    try:
        if probe_error:
            raise ValueError
        with Path(path).open("rb") as stream:
            raw = stream.read(PUBLIC["MAX_OUTPUT"] + 1)
        if len(raw) > PUBLIC["MAX_OUTPUT"]:
            raise ValueError
        incoming = json.loads(raw)
        if incoming.get("schema_version") != 1 or len(incoming["sources"]) != 4:
            raise ValueError
        stamp = PUBLIC["timestamp"](incoming.get("generated_at"))
        if not stamp:
            raise ValueError
        sources = []
        seen = {r["url"] for s in result["sources"] for r in s["records"]}
        for original, (family, query) in zip(incoming["sources"], PUBLIC["QUERIES"]):
            source = PUBLIC["source_spec"](
                family, query, datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            )
            for field in (
                "query",
                "source_type",
                "provider",
                "method",
                "window",
                "limit",
            ):
                if original.get(field) != source[field]:
                    raise ValueError
            status = original.get("status")
            if status not in {"ok", "error"}:
                raise ValueError
            error = original.get("error")
            if (status == "error" and error not in PUBLIC["ERRORS"]) or (
                status != "error" and error is not None
            ):
                raise ValueError
            records = original.get("records")
            if not isinstance(records, list) or len(records) > 5:
                raise ValueError
            for field in (
                "result_count",
                "unique_count",
                "duplicate_count",
                "rejected_count",
            ):
                value = original.get(field)
                if type(value) is not int or not 0 <= value <= 5:
                    raise ValueError
                source[field] = value
            if source["unique_count"] != len(records) or source["result_count"] != sum(
                source[k] for k in ("unique_count", "duplicate_count", "rejected_count")
            ):
                raise ValueError
            empty = original.get("empty")
            if type(empty) is not bool or empty != (
                status == "ok" and source["result_count"] == 0
            ):
                raise ValueError
            if status == "error" and records:
                raise ValueError
            source.update(status=status, error=error, empty=empty)
            for record in records:
                url = record.get("url")
                if not PUBLIC["safe_url"](url):
                    raise ValueError
                # Recheck family restrictions, URL hash and all published data.
                check = PUBLIC["source_spec"](family, query, now)
                PUBLIC["normalize_results"](
                    check,
                    {
                        "results": [
                            {
                                "url": url,
                                "content": record.get("text"),
                                "published_date": record.get("published_at"),
                            }
                        ]
                    },
                    stamp,
                    set(),
                )
                if (
                    not check["records"]
                    or record.get("id") != check["records"][0]["id"]
                    or record.get("observed_at") != stamp
                ):
                    raise ValueError
                if url in seen:
                    source["duplicate_count"] += 1
                    continue
                seen.add(url)
                source["records"].append(check["records"][0])
            source["unique_count"] = len(source["records"])
            sources.append(source)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        sources = PUBLIC["failed_sources"](now, probe_error or "malformed")
    result["sources"].extend(sources)
    return result


def finalize(result):
    families = {}
    for source in result["sources"]:
        counts = families.setdefault(
            source["source_type"],
            {
                "queries": 0,
                "successful_queries": 0,
                "failed_queries": 0,
                "empty_queries": 0,
                "result_count": 0,
                "unique_count": 0,
                "duplicate_count": 0,
                "rejected_count": 0,
            },
        )
        counts["queries"] += 1
        counts["successful_queries"] += source["status"] in PUBLIC["SUCCESS"]
        counts["failed_queries"] += source["status"] == "error"
        counts["empty_queries"] += source["empty"]
        for field in (
            "result_count",
            "unique_count",
            "duplicate_count",
            "rejected_count",
        ):
            counts[field] += source[field]
    result["families"] = families
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
    if len(encoded.encode()) > PUBLIC["MAX_OUTPUT"]:
        for source in result["sources"]:
            source.update(
                status="error",
                error="output_limit",
                empty=False,
                records=[],
                result_count=0,
                unique_count=0,
                duplicate_count=0,
                rejected_count=0,
            )
        return finalize(result)
    return encoded, 0 if any(
        s["status"] in PUBLIC["SUCCESS"] for s in result["sources"]
    ) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-search", type=Path)
    parser.add_argument("--probe-error", choices=("transport", "timeout"))
    args = parser.parse_args()
    result = merge_public(collect(), args.public_search, args.probe_error)
    output, status = finalize(result)
    sys.stdout.write(output)
    sys.exit(status)
