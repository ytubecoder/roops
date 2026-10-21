#!/usr/bin/env python3
# ruff: noqa: BLE001  (every source is best-effort by design: a failure becomes an errors row)
"""competitor-pulse collector: registry rows -> pulse.json + compact stdout diff.

Anonymous, best-effort, paced reads (GitHub, npm, PyPI, crates, Homebrew, HN
Algolia) plus Tavily news for tracked rows (key read by the gtm-public-search
probe's exact-field loader, never printed) and Reddit mentions for every row
via RedditGrow's /reddit/search (key = REDDITGROW_API_KEY, exported by
precheck.sh from the repo .env, never printed). An error becomes a short string
in that competitor's `errors`; nothing here crashes the run. Empty stdout means
nothing new anywhere, so the harness skips the engine.
"""

import html
import json
import os
import re
import runpy
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

import yaml

HERE = Path(__file__).resolve().parent
UA = "maguyva-marketing competitor-pulse"
TIMEOUT_S = 15
PACE_S = 1.0
MAX_BODY = 1_000_000
WINDOW_DAYS = 90
BLOCK_CAP = 1500
STDOUT_CAP = 48 * 1024
REGISTRY_REL = "gc-actions/specs/inputs/competitor-registry.yaml"
TAVILY_URL = "https://api.tavily.com/search"
REDDITGROW_SEARCH_URL = "https://redditgrow.ai/api/v1/reddit/search"
REDDIT_TIME = "month"  # Reddit search window; the count is "last 30 days"
REDDIT_LIMIT = 25
REDDIT_TOP = 3
REDDIT_NEW_CAP = 4  # new posts printed per competitor per run
# A post counts as a mention only when the brand term appears as a whole word
# AND the post reads as developer talk (RedditGrow's own monitor matched "Amp"
# on a dog video; Reddit search matches job ads for "sourcegraph").
REDDIT_CONTEXT_TERMS = (
    "code", "codebase", "repo", "repository", "mcp", "agent", "ide", "developer",
    "programming", "llm", "claude", "cursor", "copilot", "github", "api", "cli",
    "editor", "vscode", "software", "engineer", "refactor", "index", "search",
    "open source", "open-source", "ai",
)
# Tokens of a multi-word brand term that are too generic to stand alone
# ("claude context mcp" must match as a phrase; "cursor ai" may match "cursor").
REDDIT_GENERIC_TOKENS = frozenset(
    {
        "code", "tools", "tool", "mcp", "editor", "plans", "plan", "context", "claude",
        "codebase", "memory", "index", "indexing", "ai", "search", "grep", "deep",
        "server", "agent", "graph", "local", "enterprise", "cloud", "pro", "open",
    }
)
# Hiring posts mention every vendor by name and say nothing about the product.
REDDIT_JOB_MARKERS = ("hiring", "job", "career", "remote work", "recruit")
REDDIT_DEV_SUBS = frozenset(
    {
        "claudecode", "claudeai", "mcp", "cursor", "githubcopilot", "chatgptcoding",
        "codex", "ai_agents", "vscode", "programming", "coding", "localllama",
        "artificial", "machinelearning", "devops", "webdev", "softwareengineering",
        "experienceddevs", "cscareerquestions", "rust", "golang", "python",
        "javascript", "typescript", "java", "neovim", "emacs", "vibecoding",
        "aiagents", "llmdevs", "openai", "chatgpt", "ollama", "selfhosted",
        "sourcegraph", "augmentcode", "windsurf", "aider", "codeium", "tabnine",
    }
)
_PACE = {"last": 0.0}


class FetchError(Exception):
    """Short, secret-free reason (never a URL or response body)."""


# --------------------------------------------------------------- fetchers


def _pace():
    wait = PACE_S - (time.monotonic() - _PACE["last"])
    if wait > 0:
        time.sleep(wait)
    _PACE["last"] = time.monotonic()


def http_get(url, headers=None, body=None):
    """Return response bytes or raise FetchError('http 404' / 'timeout' / ...)."""
    _pace()
    hdrs = {"User-Agent": UA, "Accept": "application/json, text/html;q=0.8"}
    hdrs.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    try:
        with urlopen(Request(url, data=data, headers=hdrs), timeout=TIMEOUT_S) as r:
            raw = r.read(MAX_BODY + 1)
    except HTTPError as exc:
        raise FetchError(f"http {exc.code}") from None
    except TimeoutError:
        raise FetchError("timeout") from None
    except Exception:  # URLError, ssl, reset: no message (could embed the URL)
        raise FetchError("network") from None
    if len(raw) > MAX_BODY:
        raise FetchError("too large")
    return raw


def get_json(url, headers=None, body=None):
    try:
        return json.loads(http_get(url, headers, body))
    except ValueError:
        raise FetchError("malformed") from None


def get_text(url):
    return http_get(url).decode("utf-8", "replace")


# ---------------------------------------------------------------- registry


def load_registry(path):
    data = yaml.safe_load(Path(path).read_text())
    rows = []
    for row in (data or {}).get("competitors") or []:
        if not isinstance(row, dict):
            continue
        if row.get("status") not in ("tracked", "watch") or not row.get("slug"):
            continue
        rows.append(row)
    return rows


def _list(value):
    return [x for x in value if isinstance(x, str) and x.strip()] if isinstance(value, list) else []


def hn_query(row):
    hints = _list(row.get("voice_hints"))
    return hints[0] if hints else row.get("name", row["slug"])


def reddit_terms(row):
    """Terms a Reddit post must contain (whole word); the first is the search query.

    Full brand terms always count; a single token of a multi-word term counts too
    when it is distinctive (4+ chars, not generic), so "cursor ai" also accepts a
    post that just says "Cursor" while "claude context mcp" stays a phrase.
    """
    terms = _list(row.get("brand_terms")) or _list(row.get("voice_hints"))[:1]
    terms = terms or [row.get("name", row["slug"])]
    out = list(terms)
    for term in terms:
        for tok in term.lower().split():
            if len(tok) >= 4 and tok not in REDDIT_GENERIC_TOKENS and tok not in {t.lower() for t in out}:
                out.append(tok)
    return out


def reddit_is_job_post(post):
    sub = (post.get("subreddit") or "").lower()
    title = (post.get("title") or "").lower()
    return any(m in sub for m in REDDIT_JOB_MARKERS) or "[hiring]" in title or "hiring" in title


# ------------------------------------------------------------- per-source


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _gh_headers():
    hdr = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:  # read-only public metadata; anonymous when unset
        hdr["Authorization"] = "Bearer " + token
    return hdr


def gh_get(url, get=get_json):
    try:
        return get(url, _gh_headers())
    except FetchError as exc:
        if str(exc) in ("http 403", "http 429"):
            raise FetchError("rate limited") from None
        raise


def gh_stars(repo, get=get_json):
    info = gh_get("https://api.github.com/repos/" + quote(repo, safe="/"), get)
    return {"stars": _num(info.get("stargazers_count")), "pushed_at": info.get("pushed_at")}


def gh_releases(repo, get=get_json):
    rows = gh_get("https://api.github.com/repos/" + quote(repo, safe="/") + "/releases?per_page=3", get)
    return [
        {
            "repo": repo,
            "tag": r.get("tag_name"),
            "title": r.get("name") or r.get("tag_name"),
            "published_at": r.get("published_at"),
            "url": r.get("html_url"),
        }
        for r in rows[:3]
    ]


def download_count(kind, pkg, get=get_json):
    p = quote(pkg, safe="@/") if kind == "npm" else quote(pkg, safe="")
    if kind == "npm":
        return _num(get(f"https://api.npmjs.org/downloads/point/last-month/{p}").get("downloads"))
    if kind == "pypi":
        return _num(get(f"https://pypistats.org/api/packages/{p}/recent")["data"].get("last_month"))
    if kind == "crates":
        return _num(get(f"https://crates.io/api/v1/crates/{p}")["crate"].get("recent_downloads"))
    if kind == "brew":
        value = get(f"https://formulae.brew.sh/api/formula/{p}.json")["analytics"]["install"]["30d"]
        if isinstance(value, dict):
            return sum(v for v in value.values() if _num(v) is not None)
        return _num(value)
    raise FetchError("unknown package kind")


def hn_mentions(query, now, get=get_json):
    since = int((now - timedelta(days=WINDOW_DAYS)).timestamp())
    base = {"query": f'"{query}"', "numericFilters": f"created_at_i>{since}"}
    count = get(
        "https://hn.algolia.com/api/v1/search?"
        + urlencode({**base, "tags": "(story,comment)", "hitsPerPage": 0})
    ).get("nbHits")
    hits = get(
        "https://hn.algolia.com/api/v1/search?"
        + urlencode({**base, "tags": "story", "hitsPerPage": 30})
    ).get("hits", [])
    hits = sorted(hits, key=lambda h: h.get("points") or 0, reverse=True)[:3]
    top = [
        {
            "title": h.get("title") or "",
            "url": h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}",
            "points": h.get("points") or 0,
            "created_at": h.get("created_at"),
        }
        for h in hits
    ]
    return _num(count), top


def tavily_news(row, key, get=get_json):
    hints = _list(row.get("voice_hints"))
    query = f"{row['name']} {hints[0]}" if hints else row["name"]
    body = {
        "query": query,
        "topic": "news",
        "days": 30,
        "max_results": 5,
        "search_depth": "basic",
        "include_answer": False,
    }
    data = get(TAVILY_URL, {"Authorization": "Bearer " + key}, body)
    out = []
    for r in data.get("results") or []:
        url = r.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            continue
        out.append(
            {
                "title": " ".join(str(r.get("title") or "").split())[:200],
                "url": url,
                "published": r.get("published_date"),
                "source": urlsplit(url).hostname,
                "snippet": " ".join(str(r.get("content") or "").split())[:300],
            }
        )
    return out[:5]


def _whole_word(term, text):
    return re.search(r"(?<![a-z0-9])" + re.escape(term.lower()) + r"(?![a-z0-9])", text) is not None


def reddit_relevant(post, terms):
    """Whole-word brand term + developer context (sub or vocabulary)."""
    text = f"{post.get('title') or ''} {post.get('excerpt') or ''}".lower()
    if reddit_is_job_post(post):
        return False
    if not any(_whole_word(t, text) for t in terms):
        return False
    if (post.get("subreddit") or "").lower() in REDDIT_DEV_SUBS:
        return True
    return any(_whole_word(t, text) for t in REDDIT_CONTEXT_TERMS)


def reddit_mentions(row, key, get=get_json):
    """RedditGrow /reddit/search for the first brand term, gated by reddit_relevant.

    Returns (count_30d, top_by_score[:3], posts) where posts carry url, title,
    subreddit, score, created_at and a 240-char excerpt — never author.
    """
    terms = reddit_terms(row)
    query = urlencode({"q": terms[0], "sort": "new", "time": REDDIT_TIME, "limit": REDDIT_LIMIT})
    data = get(REDDITGROW_SEARCH_URL + "?" + query, {"Authorization": "Bearer " + key})
    rows = data.get("items") if isinstance(data, dict) else None
    if rows is None and isinstance(data, dict):
        rows = data.get("data")
    if not isinstance(rows, list):
        raise FetchError("malformed")
    posts = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        url = r.get("url") or ""
        permalink = r.get("permalink") or ""
        if isinstance(permalink, str) and permalink.startswith("/"):
            url = "https://www.reddit.com" + permalink
        if not isinstance(url, str) or not url.startswith("https://"):
            continue
        try:
            created = datetime.fromtimestamp(int(float(r.get("created_utc") or 0)), timezone.utc)
            created_at = created.strftime("%Y-%m-%dT%H:%M:%SZ") if created.year > 2000 else None
        except (TypeError, ValueError, OverflowError, OSError):
            created_at = None
        post = {
            "title": " ".join(str(r.get("title") or "").split())[:200],
            "url": url,
            "subreddit": str(r.get("subreddit") or "")[:60],
            "score": _num(_int(r.get("score"))) or 0,
            "num_comments": _num(_int(r.get("num_comments"))) or 0,
            "created_at": created_at,
            "excerpt": " ".join(str(r.get("selftext") or "").split())[:240],
        }
        if reddit_relevant(post, terms):
            posts.append(post)
    top = sorted(posts, key=lambda p: (p["score"], p["num_comments"]), reverse=True)[:REDDIT_TOP]
    return len(posts), [{k: p[k] for k in ("title", "url", "subreddit", "score", "created_at")} for p in top], posts


def _int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def visible_text(raw, limit=400):
    title = re.search(r"<title[^>]*>(.*?)</title>", raw, re.DOTALL | re.IGNORECASE)
    body = re.sub(r"(?is)<(script|style|noscript|svg|head)\b.*?</\1>", " ", raw)
    body = re.sub(r"(?s)<!--.*?-->", " ", body)
    body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    body = " ".join(body.split())[:limit]
    return " ".join(html.unescape(title.group(1)).split())[:200] if title else "", body


def news_page(url, get=get_text):
    title, text = visible_text(get(url))
    return {
        "title": title or urlsplit(url).hostname or url,
        "url": url,
        "published": None,
        "source": urlsplit(url).hostname,
        "snippet": text,
    }


# ------------------------------------------------------------ orchestration


def collect_competitor(row, now, tavily_key=None, fns=None, reddit_key=None):
    f = {
        "stars": gh_stars,
        "rels": gh_releases,
        "dl": download_count,
        "hn": hn_mentions,
        "tv": tavily_news,
        "page": news_page,
        "rd": reddit_mentions,
    }
    f.update(fns or {})
    out = {
        "slug": row["slug"],
        "name": row.get("name", row["slug"]),
        "status": row["status"],
        "stars": {},
        "releases": [],
        "downloads": {"npm": {}, "pypi": {}, "crates": {}, "brew": {}},
        "mentions": {"hn_90d": None, "hn_top": [], "reddit_30d": None, "reddit_top": []},
        "reddit": [],
        "news": [],
        "errors": [],
    }

    def err(source, exc):
        out["errors"].append(f"{source}: {exc if isinstance(exc, FetchError) else 'malformed'}")

    for repo in _list(row.get("repos")):
        try:
            out["stars"][repo] = {**f["stars"](repo), "prev_stars": None}
        except Exception as exc:
            err("github", exc)
        try:
            out["releases"].extend(f["rels"](repo))
        except Exception as exc:
            err("github releases", exc)
    pk = row.get("packages") if isinstance(row.get("packages"), dict) else {}
    for kind in ("npm", "pypi", "crates", "brew"):
        for pkg in _list(pk.get(kind)):
            try:
                out["downloads"][kind][pkg] = f["dl"](kind, pkg)
            except Exception as exc:
                err(f"{kind} {pkg}", exc)
    try:
        out["mentions"]["hn_90d"], out["mentions"]["hn_top"] = f["hn"](hn_query(row), now)
    except Exception as exc:
        err("hn", exc)
    if reddit_key:
        try:
            count, top, posts = f["rd"](row, reddit_key)
            out["mentions"]["reddit_30d"], out["mentions"]["reddit_top"] = count, top
            out["reddit"] = posts
        except Exception as exc:
            err("redditgrow", exc)
    else:
        out["errors"].append("redditgrow: no key")
    if row["status"] == "tracked":
        if tavily_key:
            try:
                out["news"].extend(f["tv"](row, tavily_key))
            except Exception as exc:
                err("tavily", exc)
        else:
            out["errors"].append("tavily: no key")
        for url in _list(row.get("news_urls")):
            try:
                out["news"].append(f["page"](url))
            except Exception as exc:
                err(f"page {urlsplit(url).hostname}", exc)
    return out


# ----------------------------------------------------------------- caching


def load_cache(cache_dir):
    def rd(name, default):
        try:
            value = json.loads((Path(cache_dir) / name).read_text())
            return value if isinstance(value, type(default)) else default
        except (OSError, ValueError):
            return default

    return {
        "stars": rd("stars.json", {}),
        "releases": set(rd("releases.json", [])),
        "news": set(rd("news.json", [])),
        "hn": rd("hn.json", {}),
        "reddit": set(rd("reddit.json", [])),
        "reddit_count": rd("reddit-count.json", {}),
    }


def apply_cache(competitors, cache):
    """Fill prev_stars; return per-slug new items (also what stdout prints)."""
    diffs = {}
    for c in competitors:
        prev_stars = cache["stars"].get(c["slug"]) or {}
        for repo, s in c["stars"].items():
            s["prev_stars"] = prev_stars.get(repo)
        seen_reddit = cache.get("reddit") or set()
        diffs[c["slug"]] = {
            "new_releases": [r for r in c["releases"] if r.get("url") and r["url"] not in cache["releases"]],
            "new_news": [n for n in c["news"] if n["url"] not in cache["news"]],
            "prev_hn": cache["hn"].get(c["slug"]),
            "new_reddit": [p for p in c.get("reddit") or [] if p["url"] not in seen_reddit],
            "prev_reddit": (cache.get("reddit_count") or {}).get(c["slug"]),
        }
    return diffs


def next_cache(competitors, cache):
    stars = dict(cache["stars"])
    hn = dict(cache["hn"])
    for c in competitors:
        got = {r: s["stars"] for r, s in c["stars"].items() if s.get("stars") is not None}
        if got:  # a failed fetch must not erase the previous reading
            stars[c["slug"]] = {**(stars.get(c["slug"]) or {}), **got}
        if c["mentions"]["hn_90d"] is not None:
            hn[c["slug"]] = c["mentions"]["hn_90d"]
    rel = set(cache["releases"]) | {r["url"] for c in competitors for r in c["releases"] if r.get("url")}
    news = set(cache["news"]) | {n["url"] for c in competitors for n in c["news"]}
    reddit = set(cache.get("reddit") or set()) | {p["url"] for c in competitors for p in c.get("reddit") or []}
    reddit_count = dict(cache.get("reddit_count") or {})
    for c in competitors:
        if c["mentions"].get("reddit_30d") is not None:
            reddit_count[c["slug"]] = c["mentions"]["reddit_30d"]
    return {
        "stars": stars,
        "hn": hn,
        "releases": sorted(rel),
        "news": sorted(news),
        "reddit": sorted(reddit),
        "reddit_count": reddit_count,
    }


def save_cache(cache_dir, new):
    d = Path(cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    names = (
        ("stars.json", "stars"),
        ("hn.json", "hn"),
        ("releases.json", "releases"),
        ("news.json", "news"),
        ("reddit.json", "reddit"),
        ("reddit-count.json", "reddit_count"),
    )
    for name, key in names:
        tmp = d / (name + ".tmp")
        tmp.write_text(json.dumps(new[key], sort_keys=True))
        tmp.replace(d / name)


# ------------------------------------------------------------------ stdout


def _clip(text, n):
    return text if len(text) <= n else text[: n - 1] + "…"


def build_block(c, diff):
    """Compact block for one competitor, or '' when nothing is new."""
    lines = []
    for r in diff["new_releases"]:
        lines.append(f"NEW release {r['repo']} {r['tag']} {str(r.get('published_at') or '')[:10]} {r['url']}")
    for n in diff["new_news"]:
        date = str(n.get("published") or "")[:16]
        lines.append(" ".join(f"NEW news {date} {_clip(n['title'], 110)} | {n['url']}".split()))
    for repo, s in c["stars"].items():
        now_, prev = s.get("stars"), s.get("prev_stars")
        if now_ is None:
            continue
        if prev is None:
            lines.append(f"stars {repo}: {now_} (first reading)")
        elif now_ != prev:
            pct = (now_ - prev) / prev * 100 if prev else 0
            lines.append(f"stars {repo}: {now_} ({now_ - prev:+d}, {pct:+.1f}%)")
    hn, prev = c["mentions"]["hn_90d"], diff["prev_hn"]
    if hn is not None and hn != prev:
        lines.append(f"HN 90d mentions: {hn} (prev {prev if prev is not None else 'none'})")
        for t in c["mentions"]["hn_top"][:1]:
            lines.append(f"HN top: {_clip(t['title'], 80)} ({t['points']} pts) {t['url']}")
    new_reddit = sorted(diff.get("new_reddit") or [], key=lambda p: (p.get("score") or 0), reverse=True)
    rd, prev_rd = c["mentions"].get("reddit_30d"), diff.get("prev_reddit")
    if new_reddit or (rd is not None and rd != prev_rd):
        lines.append(f"Reddit 30d mentions: {rd} (prev {prev_rd if prev_rd is not None else 'none'})")
    for p in new_reddit[:REDDIT_NEW_CAP]:
        date = str(p.get("created_at") or "")[:10]
        lines.append(
            " ".join(
                f"NEW reddit {date} r/{p['subreddit']} ({p['score']} pts) {_clip(p['title'], 100)} | {p['url']}".split()
            )
        )
        if p.get("excerpt"):
            lines.append(f"  > {_clip(p['excerpt'], 160)}")
    if len(new_reddit) > REDDIT_NEW_CAP:
        lines.append(f"  (+{len(new_reddit) - REDDIT_NEW_CAP} more new Reddit posts in pulse.json)")
    if not lines:
        return ""
    head = f"## {c['name']} ({c['slug']})"
    kept, size = [head], len(head)
    for line in lines:
        if size + len(line) + 1 > BLOCK_CAP - 20:
            kept.append("...(more omitted)")
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept)


def render(competitors, diffs):
    parts = [b for c in competitors if (b := build_block(c, diffs[c["slug"]]))]
    errors = [f"{c['slug']}: {e}" for c in competitors for e in c["errors"]]
    if errors:
        parts.append("ERRORS:\n" + "\n".join(errors))
    if not parts:
        return ""
    if any("NEW reddit " in b for b in parts):
        # Lets the engine spot a tool named in a Reddit post that no registry row covers.
        known = ", ".join(f"{c['name']} ({c['slug']})" for c in competitors)
        parts.insert(0, "KNOWN COMPETITORS (registry rows): " + known)
    text = "\n\n".join(parts) + "\n"
    raw = text.encode()
    if len(raw) > STDOUT_CAP:
        note = "\n[TRUNCATED: output exceeded 48 KiB; later competitors/errors omitted]\n"
        text = raw[: STDOUT_CAP - len(note.encode())].decode("utf-8", "ignore") + note
    return text


# -------------------------------------------------------------------- main


def registry_path():
    repo = os.environ.get("MAGUYVA_REPO") or str(Path.home() / "projects/maguyva-marketing")
    return Path(repo) / REGISTRY_REL


def tavily_key():
    try:
        probe = runpy.run_path(str(HERE.parents[1] / "probes/gtm-public-search"))
        return probe["key_from_config"]()
    except Exception:
        return None


def redditgrow_key():
    """REDDITGROW_API_KEY from the environment (precheck.sh exports it from the repo .env)."""
    key = (os.environ.get("REDDITGROW_API_KEY") or "").strip()
    return key if key and not any(ch.isspace() for ch in key) else None


def main():
    out_dir = Path(os.environ.get("OUT_DIR") or ".")
    cache_dir = Path(os.environ.get("PULSE_CACHE_DIR") or HERE / "cache")
    now = datetime.now(timezone.utc)
    try:
        rows = load_registry(registry_path())
    except Exception:
        print("ERRORS:\nregistry: unreadable")
        return 1
    key = tavily_key() if any(r["status"] == "tracked" for r in rows) else None
    rd_key = redditgrow_key()
    competitors = [collect_competitor(r, now, key, reddit_key=rd_key) for r in rows]
    cache = load_cache(cache_dir)
    diffs = apply_cache(competitors, cache)
    pulse = {
        "schema_version": 1,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_days": WINDOW_DAYS,
        "reddit_window_days": 30,
        "competitors": competitors,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pulse.json").write_text(json.dumps(pulse, indent=1))
    text = render(competitors, diffs)
    save_cache(cache_dir, next_cache(competitors, cache))
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
