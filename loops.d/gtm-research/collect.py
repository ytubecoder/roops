"""Bounded GitHub collection and validated merge of the llm search probe.

The probe (llm) carries the tracked competitor registry and its voice searches;
this side runs the GitHub issue searches and builds the per-competitor index.
"""

import argparse
import hashlib
import json
import runpy
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# The four positioning searches never depend on the registry; they are the
# research inputs GC validates and must keep working when the probe fails.
REPOS = (
    "abhigyanpatwari/GitNexus",
    "DeusData/codebase-memory-mcp",
    "zilliztech/claude-context",
    "oraios/serena",
)
MAX_EXTRA_REPOS = 8
GITHUB_LIMIT = 6
GITHUB_PAUSE_S = 2.5
_PACE = {"sent": 0}
PUBLIC = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "probes/gtm-public-search")
)
COUNTERS = PUBLIC["COUNTERS"]


def github_spec(repo, now, competitor=None):
    since = (now - timedelta(days=30)).date().isoformat()
    return {
        "query": f"repo:{repo} is:issue updated:>={since}",
        "source_type": "public_github",
        "competitor": competitor,
        "provider": "github",
        "method": "issue_search",
        "window": {
            "start_date": since,
            "end_date": now.date().isoformat(),
            "days": 30,
        },
        "limit": GITHUB_LIMIT,
        "status": "ok",
        "empty": True,
        "error": None,
        "records": [],
        "result_count": 0,
        "unique_count": 0,
        "duplicate_count": 0,
        "rejected_count": 0,
        "truncated": False,
        "truncated_count": 0,
    }


def _github_search(repo, source, stamp, fetch=None):
    url = "https://api.github.com/search/issues?" + urlencode(
        {
            "q": source["query"],
            "sort": "updated",
            "order": "desc",
            "per_page": GITHUB_LIMIT,
        }
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
    rows = payload["items"][:GITHUB_LIMIT]
    source["result_count"] = len(rows)
    seen = set()
    prefix = f"https://github.com/{repo}/issues/"
    for item in rows:
        if not isinstance(item, dict):
            source["rejected_count"] += 1
            continue
        address = item.get("html_url", "")
        if not PUBLIC["safe_url"](address) or not address.lower().startswith(
            prefix.lower()
        ):
            source["rejected_count"] += 1
            continue
        if address in seen:
            source["duplicate_count"] += 1
            continue
        seen.add(address)
        text = " ".join(
            (str(item.get("title", "")) + " " + str(item.get("body") or "")).split()
        )
        text = PUBLIC["excerpt"](text)
        source["records"].append(
            {
                "id": "pub-" + hashlib.sha256(address.encode()).hexdigest()[:16],
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


def collect_repo(repo, now, fetch=None, competitor=None):
    source = github_spec(repo, now, competitor)
    try:
        if fetch is None and _PACE["sent"]:
            # Unauthenticated GitHub search trips its burst limit on back-to-back
            # requests (403 -> auth). A short fixed pause keeps six searches legal.
            time.sleep(GITHUB_PAUSE_S)
        _PACE["sent"] += 1
        _github_search(repo, source, now.isoformat(), fetch)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        PUBLIC["fail_source"](source, PUBLIC["error_category"](exc))
    return source


def collect(now=None, fetch=None):
    now = now or datetime.now(timezone.utc)
    sources = [collect_repo(repo, now, fetch) for repo in REPOS]
    return {"schema_version": 1, "generated_at": now.isoformat(), "sources": sources}


def _check_counters(source, original, limit):
    for field in COUNTERS + ("truncated_count",):
        value = original.get(field)
        if type(value) is not int or not 0 <= value <= limit:
            raise ValueError
        source[field] = value
    truncated = original.get("truncated")
    if type(truncated) is not bool or truncated != (source["truncated_count"] > 0):
        raise ValueError
    source["truncated"] = truncated
    if source["result_count"] != sum(
        source[k]
        for k in (
            "unique_count",
            "duplicate_count",
            "rejected_count",
            "truncated_count",
        )
    ):
        raise ValueError


def _check_source(original, source, family, query, now, stamp, seen, competitor=None):
    """Recheck one probe source against its recomputed spec; returns the clean copy."""
    for field in (
        "query",
        "source_type",
        "provider",
        "method",
        "window",
        "limit",
        "competitor",
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
    if not isinstance(records, list) or len(records) > source["limit"]:
        raise ValueError
    _check_counters(source, original, source["limit"])
    if source["unique_count"] != len(records):
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
        check = PUBLIC["source_spec"](family, query, now, competitor)
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
    return source


def _read_probe(path, probe_error):
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
    return incoming, stamp, datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def _merge_registry(result, incoming, stamp, then, now, fetch):
    """Registry, voice sources and extra GitHub repos. Any defect fails this block only."""
    registry = {
        "status": "error",
        "error": "malformed",
        "updated": None,
        "competitors": [],
    }
    plan_block = {
        "planned": 0,
        "run": 0,
        "truncated": False,
        "domains": list(PUBLIC["VOICE_DOMAINS"]),
    }
    voice_sources = []
    try:
        block = incoming["registry"]
        if block.get("status") not in {"ok", "error"}:
            raise ValueError
        if block["status"] == "error":
            if block.get("error") not in PUBLIC["ERRORS"] or block.get("competitors"):
                raise ValueError
            registry.update(error=block["error"])
        else:
            competitors = PUBLIC["clean_competitors"](block.get("competitors"))
            updated = block.get("updated")
            if updated is not None and (
                not isinstance(updated, str) or len(updated) > 10
            ):
                raise ValueError
            registry.update(
                status="ok", error=None, updated=updated, competitors=competitors
            )
        plan = PUBLIC["voice_plan"](registry["competitors"])
        plan_block.update(
            planned=len(plan),
            run=min(len(plan), PUBLIC["MAX_VOICE_SEARCHES"]),
            truncated=len(plan) > PUBLIC["MAX_VOICE_SEARCHES"],
        )
        plan = plan[: PUBLIC["MAX_VOICE_SEARCHES"]]
        block_plan = incoming.get("voice_plan")
        if block_plan != plan_block:
            raise ValueError
        originals = incoming.get("voice_sources")
        if not isinstance(originals, list) or len(originals) != len(plan):
            raise ValueError
        seen = {}
        for original, (slug, hint) in zip(originals, plan):
            source = PUBLIC["source_spec"](PUBLIC["VOICE_FAMILY"], hint, then, slug)
            voice_sources.append(
                _check_source(
                    original,
                    source,
                    PUBLIC["VOICE_FAMILY"],
                    hint,
                    now,
                    stamp,
                    seen.setdefault(slug, set()),
                    slug,
                )
            )
    except (ValueError, TypeError, KeyError, AttributeError, PUBLIC["CollectionError"]):
        registry = {
            "status": "error",
            "error": "malformed",
            "updated": None,
            "competitors": [],
        }
        plan_block = {
            "planned": 0,
            "run": 0,
            "truncated": False,
            "domains": list(PUBLIC["VOICE_DOMAINS"]),
        }
        voice_sources = []
    result["registry"] = registry
    result["voice_plan"] = plan_block
    result["voice_sources"] = voice_sources
    # Tag the fixed repos and search the registry's other repos.
    slug_by_repo = {}
    for competitor in registry["competitors"]:
        for repo in competitor["repos"]:
            slug_by_repo.setdefault(repo.lower(), competitor["slug"])
    for source in result["sources"]:
        if source["source_type"] == "public_github" and source["provider"] == "github":
            repo = source["query"].split(" ", 1)[0].removeprefix("repo:").lower()
            source["competitor"] = slug_by_repo.get(repo)
    known = {repo.lower() for repo in REPOS}
    extra = [(repo, slug) for repo, slug in slug_by_repo.items() if repo not in known]
    result["github_plan"] = {
        "fixed": len(REPOS),
        "planned": len(REPOS) + len(extra),
        "run": len(REPOS) + min(len(extra), MAX_EXTRA_REPOS),
        "truncated": len(extra) > MAX_EXTRA_REPOS,
    }
    for repo, slug in extra[:MAX_EXTRA_REPOS]:
        exact = next(
            r for c in registry["competitors"] for r in c["repos"] if r.lower() == repo
        )
        result["sources"].append(collect_repo(exact, now, fetch, slug))


def merge_public(result, path, probe_error=None, fetch=None):
    now = datetime.fromisoformat(result["generated_at"])
    seen = {r["url"] for s in result["sources"] for r in s["records"]}
    sources = []
    incoming = None
    try:
        incoming, stamp, then = _read_probe(path, probe_error)
        for original, (family, query) in zip(incoming["sources"], PUBLIC["QUERIES"]):
            source = PUBLIC["source_spec"](family, query, then)
            sources.append(
                _check_source(original, source, family, query, now, stamp, seen)
            )
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        sources = PUBLIC["failed_sources"](now, probe_error or "malformed")
        incoming = None
    result["sources"].extend(sources)
    if incoming is None:
        result["registry"] = {
            "status": "error",
            "error": probe_error or "malformed",
            "updated": None,
            "competitors": [],
        }
        result["voice_plan"] = {
            "planned": 0,
            "run": 0,
            "truncated": False,
            "domains": list(PUBLIC["VOICE_DOMAINS"]),
        }
        result["voice_sources"] = []
        result["github_plan"] = {
            "fixed": len(REPOS),
            "planned": len(REPOS),
            "run": len(REPOS),
            "truncated": False,
        }
        return result
    _merge_registry(result, incoming, stamp, then, now, fetch)
    return result


def _competitor_index(result):
    index = []
    for competitor in result.get("registry", {}).get("competitors", []):
        slug = competitor["slug"]
        mine = [
            s
            for s in result["sources"] + result.get("voice_sources", [])
            if s.get("competitor") == slug
        ]
        records = [r for s in mine for r in s["records"]]
        failed = sum(s["status"] == "error" for s in mine)
        truncated = sum(s["truncated_count"] for s in mine)
        if not mine:
            status = "unsearched"
        elif failed == len(mine):
            status = "error"
        elif failed or truncated:
            status = "partial"
        elif not records:
            status = "empty"
        else:
            status = "ok"
        index.append(
            {
                "name": competitor["name"],
                "slug": slug,
                "repos": competitor["repos"],
                "voice_hints": competitor["voice_hints"],
                "queries": len(mine),
                "failed_queries": failed,
                "truncated_records": truncated,
                "record_ids": [r["id"] for r in records],
                "url_count": len({r["url"] for r in records}),
                "status": status,
            }
        )
    return index


def _annotate(result):
    families = {}
    for source in result["sources"] + result["voice_sources"]:
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
                "truncated_count": 0,
            },
        )
        counts["queries"] += 1
        counts["successful_queries"] += source["status"] in PUBLIC["SUCCESS"]
        counts["failed_queries"] += source["status"] == "error"
        counts["empty_queries"] += source["empty"]
        for field in COUNTERS + ("truncated_count",):
            counts[field] += source[field]
    result["families"] = families
    result["competitors"] = _competitor_index(result)


def _trimmed(result):
    return sum(s["truncated_count"] for s in result["sources"] + result["voice_sources"])


def finalize(result):
    result.setdefault("voice_sources", [])
    # Explicit record trimming (voice first, then research) before failing closed;
    # families and the competitor index are recomputed after every trim.
    for _ in range(4):
        _annotate(result)
        before = _trimmed(result)
        encoded = PUBLIC["fit_output"](result, ("voice_sources", "sources"))
        if encoded is None:
            for source in result["sources"] + result["voice_sources"]:
                PUBLIC["fail_source"](source, "output_limit")
            continue
        if _trimmed(result) == before:
            break
    else:
        _annotate(result)
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
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
