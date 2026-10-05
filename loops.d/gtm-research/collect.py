"""Bounded public GitHub research; no credentials, browser or mutation."""

import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

REPOS = (
    "abhigyanpatwari/GitNexus",
    "DeusData/codebase-memory-mcp",
    "zilliztech/claude-context",
    "oraios/serena",
)


def collect(now=None, fetch=None):
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(days=30)).date().isoformat()
    stamp = now.isoformat()
    sources = []
    for repo in REPOS:
        query = f"repo:{repo} is:issue updated:>={since}"
        source = {"query": query, "status": "ok", "error": None, "records": []}
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
                raise ValueError("missing issue results")
            for item in payload["items"][:6]:
                address = item.get("html_url", "")
                if not address.startswith(f"https://github.com/{repo}/issues/"):
                    continue
                text = " ".join(
                    (
                        str(item.get("title", "")) + " " + str(item.get("body") or "")
                    ).split()
                )
                text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[address]", text)
                text = " ".join(text.split()[:22])[:300]
                source["records"].append(
                    {
                        "id": "pub-"
                        + hashlib.sha256(address.encode()).hexdigest()[:16],
                        "url": address,
                        "observed_at": stamp,
                        "published_at": item.get("created_at"),
                        "text": text or "No excerpt",
                    }
                )
        except Exception as exc:
            source.update(status="error", error=type(exc).__name__, records=[])
        sources.append(source)
    return {"schema_version": 1, "generated_at": stamp, "sources": sources}


if __name__ == "__main__":
    result = collect()
    print(json.dumps(result, ensure_ascii=False))
    sys.exit(0 if any(s["status"] == "ok" for s in result["sources"]) else 1)
