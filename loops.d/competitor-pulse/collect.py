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

Two lighter passes ride along (2026-10-24): a website feature watch that diffs
the visible text of every tracked row's seed_urls against cache/site.json, and
a pass over the newest competitor-discovery candidates.json (candidates no
registry row covers and Generalissimo has not given a verdict on): what it is,
one Tavily news search and one RedditGrow search each, capped at CANDIDATE_CAP.
"""

import hashlib
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
SITE_TIMEOUT_S = 20
SITE_MAX_BODY = 3_000_000
SITE_URL_CAP = 3  # seed urls fetched per tracked row
SITE_LINE_MIN = 25
SITE_LINE_MAX = 300  # stored length per line
SITE_LINES_CAP = 800  # stored lines per url
SITE_ADDED_CAP = 8  # pulse.json
SITE_ADDED_PRINT = 6  # stdout
SITE_ADDED_CHARS = 220
SITE_BOILERPLATE = (
    "cookie", "consent", "all rights reserved", "privacy policy", "terms of service",
    "terms of use", "accept all", "do not sell", "javascript is disabled", "enable javascript",
    "skip to content", "skip to main", "©", "copyright",
    # GitHub page chrome; star/fork counters ride on the "must be signed in" lines
    "you must be signed in", "another tab or window", "reload to refresh", "switched accounts",
)
CANDIDATE_CAP = 8
CANDIDATE_WHAT_CHARS = 300
_PACE = {"last": 0.0}


class FetchError(Exception):
    """Short, secret-free reason (never a URL or response body)."""


# --------------------------------------------------------------- fetchers


def _pace():
    wait = PACE_S - (time.monotonic() - _PACE["last"])
    if wait > 0:
        time.sleep(wait)
    _PACE["last"] = time.monotonic()


def http_get(url, headers=None, body=None, timeout=TIMEOUT_S, max_body=MAX_BODY):
    """Return response bytes or raise FetchError('http 404' / 'timeout' / ...)."""
    _pace()
    hdrs = {"User-Agent": UA, "Accept": "application/json, text/html;q=0.8"}
    hdrs.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        hdrs["Content-Type"] = "application/json"
    try:
        with urlopen(Request(url, data=data, headers=hdrs), timeout=timeout) as r:
            raw = r.read(max_body + 1)
    except HTTPError as exc:
        raise FetchError(f"http {exc.code}") from None
    except TimeoutError:
        raise FetchError("timeout") from None
    except Exception:  # URLError, ssl, reset: no message (could embed the URL)
        raise FetchError("network") from None
    if len(raw) > max_body:
        raise FetchError("too large")
    return raw


def get_json(url, headers=None, body=None):
    try:
        return json.loads(http_get(url, headers, body))
    except ValueError:
        raise FetchError("malformed") from None


def get_text(url):
    return http_get(url).decode("utf-8", "replace")


def get_site(url):
    """Anonymous GET for the website watch (longer timeout, larger body)."""
    raw = http_get(url, {"Accept": "text/html,application/xhtml+xml"}, timeout=SITE_TIMEOUT_S, max_body=SITE_MAX_BODY)
    return raw.decode("utf-8", "replace")


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


def _host(value):
    host = (value or "").strip().lower()
    if "://" in host:
        host = urlsplit(host).hostname or ""
    host = host.split("/")[0]
    return host.removeprefix("www.")


def registry_coverage(path):
    """(repos, domains) named anywhere in the registry, rejected rows and list included."""
    data = yaml.safe_load(Path(path).read_text()) or {}
    repos, domains = set(), set()
    rows = [r for r in (data.get("competitors") or []) if isinstance(r, dict)]
    rows += [r for r in (data.get("rejected") or []) if isinstance(r, dict)]
    for row in rows:
        repos |= {r.strip().lower() for r in _list(row.get("repos"))}
        domains |= {h for d in _list(row.get("domains")) if (h := _host(d))}
        for url in _list(row.get("seed_urls")):
            host = _host(url)
            if host == "github.com":
                parts = urlsplit(url).path.strip("/").split("/")
                if len(parts) >= 2:
                    repos.add(f"{parts[0]}/{parts[1]}".lower())
            elif host:
                domains.add(host)
    return repos, domains


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


_SITE_DROP = r"(?is)<(script|style|noscript|svg|head|nav|footer|template|iframe|form)\b.*?</\1>"
_SITE_BREAK = (
    r"(?i)<(?:br|hr)\b[^>]*>|</?(?:p|h[1-6]|li|ul|ol|div|section|article|main|aside|header|tr|td|th|"
    r"dt|dd|dl|table|blockquote|figcaption|button|summary|details|label|option|pre)\b[^>]*>"
)
_SITE_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_SITE_NOISE = re.compile(
    r"^[\W\d_]*$|^(?:[\d\W_]|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)*$|"
    r"\b\d+\s+(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago\b",
    re.IGNORECASE,
)


def site_title(raw):
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.DOTALL | re.IGNORECASE)
    return " ".join(html.unescape(m.group(1)).split())[:200] if m else ""


def site_keep(line):
    """A line counts for the diff: long enough, not numbers/dates only, not boilerplate."""
    if len(line) < SITE_LINE_MIN or _SITE_NOISE.search(line):
        return False
    low = line.lower()
    return not any(b in low for b in SITE_BOILERPLATE)


def site_lines(raw):
    """Visible text of a page as de-duplicated sentence/heading lines, in page order."""
    body = re.sub(r"(?s)<!--.*?-->", " ", raw)
    body = re.sub(_SITE_DROP, " ", body)
    body = re.sub(_SITE_BREAK, "\n", body)
    body = html.unescape(re.sub(r"<[^>]+>", " ", body))
    out, seen = [], set()
    for block in body.split("\n"):
        block = " ".join(block.split())
        for line in _SITE_SENT.split(block) if block else []:
            line = line.strip()[:SITE_LINE_MAX]
            if site_keep(line) and line not in seen:
                seen.add(line)
                out.append(line)
                if len(out) >= SITE_LINES_CAP:
                    return out
    return out


def site_reading(url, now, get=get_site):
    raw = get(url)
    lines = site_lines(raw)
    return {
        "fetched_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "title": site_title(raw) or urlsplit(url).hostname or url,
        "first_reading": True,
        "changed": False,
        "added": [],
        "removed_count": 0,
        "_lines": lines,
        "_sha": hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16],
    }


def homepage_summary(domain, get=get_site):
    """<title> + meta description of https://<domain>/, at most 300 chars."""
    raw = get("https://" + domain + "/")
    desc = ""
    for pat in (
        r"<meta[^>]+(?:name|property)=[\"'](?:og:)?description[\"'][^>]*content=[\"']([^\"']*)",
        r"<meta[^>]+content=[\"']([^\"']*)[\"'][^>]*(?:name|property)=[\"'](?:og:)?description[\"']",
    ):
        m = re.search(pat, raw, re.IGNORECASE)
        if m:
            desc = " ".join(html.unescape(m.group(1)).split())
            break
    title = site_title(raw)
    return _clip(" — ".join(x for x in (title, desc) if x), CANDIDATE_WHAT_CHARS)


# ------------------------------------------------------------- candidates


def find_candidates_file(runs_root):
    """Newest state/runs/*-competitor-discovery-*/discovery/<date>/candidates.json, or None."""
    root = Path(runs_root)
    if not root.is_dir():
        return None
    for run in sorted(root.glob("*-competitor-discovery-*"), key=lambda p: p.name, reverse=True):
        files = sorted(run.glob("discovery/*/candidates.json"), key=lambda p: p.parent.name, reverse=True)
        if files:
            return files[0]
    return None


def load_candidates(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return []
    rows = data.get("candidates") if isinstance(data, dict) else data
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def load_seen(path):
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def candidate_key(cand):
    repo = cand.get("repo")
    if isinstance(repo, str) and repo.strip():
        return repo.strip().lower()
    return _host(cand.get("domain") or "")


def candidate_covered(cand, coverage):
    repos, domains = coverage
    repo = cand.get("repo")
    if isinstance(repo, str) and repo.strip().lower() in repos:
        return True
    host = _host(cand.get("domain") or "")
    return bool(host) and any(host == d or host.endswith("." + d) for d in domains)


def select_candidates(cands, coverage, cap=CANDIDATE_CAP):
    keep = [
        c for c in cands
        if candidate_key(c) and not str(c.get("verdict") or "").strip() and not candidate_covered(c, coverage)
    ]
    keep.sort(key=lambda c: _num(c.get("score")) or 0, reverse=True)
    return keep[:cap]


def candidate_term(cand):
    """Search term: the repo name for a repo (owner/ is noise), else the candidate name."""
    repo = cand.get("repo")
    if isinstance(repo, str) and "/" in repo:
        return repo.split("/")[-1]
    return str(cand.get("name") or cand.get("domain") or "").strip()


def collect_candidate(cand, seen, tavily_key=None, reddit_key=None, fns=None):
    f = {"home": homepage_summary, "tv": tavily_news, "rd": reddit_mentions}
    f.update(fns or {})
    key = candidate_key(cand)
    domain = _host(cand.get("domain") or "") or None
    repo = cand.get("repo") if isinstance(cand.get("repo"), str) and cand.get("repo") else None
    term = candidate_term(cand) or key
    out = {
        "key": key,
        "name": str(cand.get("name") or key),
        "kind": cand.get("kind"),
        "url": cand.get("html_url") or (f"https://{domain}/" if domain else None),
        "repo": repo,
        "domain": domain,
        "score": _num(cand.get("score")),
        "first_seen": (seen.get(key) or {}).get("first_seen") if isinstance(seen.get(key), dict) else None,
        "what": "",
        "news": [],
        "reddit_30d": None,
        "reddit_top": [],
        "errors": [],
        "_reddit": [],
    }

    def err(source, exc):
        out["errors"].append(f"{source}: {exc if isinstance(exc, FetchError) else 'malformed'}")

    if repo or not domain:
        out["what"] = _clip(" ".join(str(cand.get("description") or "").split()), CANDIDATE_WHAT_CHARS)
    else:
        try:
            out["what"] = f["home"](domain)
        except Exception as exc:
            err(f"home {domain}", exc)
    pseudo = {"slug": key, "name": term, "brand_terms": [term]}
    if tavily_key:
        try:
            out["news"] = [
                {"title": n.get("title") or "", "url": n["url"], "date": n.get("published")}
                for n in f["tv"](pseudo, tavily_key)
                if isinstance(n, dict) and n.get("url")
            ]
        except Exception as exc:
            err("tavily", exc)
    if reddit_key:
        try:
            out["reddit_30d"], out["reddit_top"], out["_reddit"] = f["rd"](pseudo, reddit_key)
        except Exception as exc:
            err("redditgrow", exc)
    return out


def public(value):
    """Copy for pulse.json without the private `_` keys (cache-only data)."""
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items() if not str(k).startswith("_")}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


# ------------------------------------------------------------ orchestration


def collect_competitor(row, now, tavily_key=None, fns=None, reddit_key=None):
    f = {
        "site": site_reading,
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
        "site": {},
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
        for url in _list(row.get("seed_urls"))[:SITE_URL_CAP]:
            try:
                out["site"][url] = f["site"](url, now)
            except Exception as exc:
                err(f"site {urlsplit(url).hostname}", exc)
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
        "site": rd("site.json", {}),
        "cand_news": rd("candidates-news.json", {}),
        "cand_reddit": rd("candidates-reddit.json", {}),
    }


def site_diff(entry, prev):
    """Fill first_reading/changed/added/removed_count of one reading against its cached lines."""
    lines = entry.get("_lines") or []
    if not isinstance(prev, dict) or not isinstance(prev.get("lines"), list):
        entry.update(first_reading=True, changed=False, added=[], removed_count=0)
        return
    old, new = set(prev["lines"]), set(lines)
    added = [x for x in lines if x not in old]
    entry.update(
        first_reading=False,
        changed=bool(added) or bool(old - new),
        added=[_clip(x, SITE_ADDED_CHARS) for x in added[:SITE_ADDED_CAP]],
        removed_count=len(old - new),
    )
    entry["_added_total"] = len(added)


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
        for url, entry in (c.get("site") or {}).items():
            site_diff(entry, (cache.get("site") or {}).get(url))
    return diffs


def apply_candidate_cache(candidates, cache):
    """Per-key new news/Reddit items; a key never listed before counts as a first listing."""
    seen_news = cache.get("cand_news") or {}
    seen_rd = cache.get("cand_reddit") or {}
    diffs = {}
    for c in candidates:
        old_news = set(seen_news.get(c["key"]) or [])
        old_rd = set(seen_rd.get(c["key"]) or [])
        diffs[c["key"]] = {
            "first_listing": c["key"] not in seen_news,
            "new_news": [n for n in c["news"] if n["url"] not in old_news],
            "new_reddit": [p for p in c.get("_reddit") or [] if p.get("url") and p["url"] not in old_rd],
        }
    return diffs


def next_cache(competitors, cache, candidates=None):
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
    site = dict(cache.get("site") or {})
    for c in competitors:
        for url, entry in (c.get("site") or {}).items():
            if isinstance(entry.get("_lines"), list):  # failed fetches keep the old reading
                site[url] = {"fetched_at": entry.get("fetched_at"), "lines": entry["_lines"], "sha": entry.get("_sha")}
    cand_news = {k: list(v) for k, v in (cache.get("cand_news") or {}).items() if isinstance(v, list)}
    cand_rd = {k: list(v) for k, v in (cache.get("cand_reddit") or {}).items() if isinstance(v, list)}
    for c in candidates or []:
        cand_news[c["key"]] = sorted(set(cand_news.get(c["key"]) or []) | {n["url"] for n in c["news"]})
        urls = {p["url"] for p in c.get("_reddit") or [] if p.get("url")}
        cand_rd[c["key"]] = sorted(set(cand_rd.get(c["key"]) or []) | urls)
    return {
        "site": site,
        "cand_news": cand_news,
        "cand_reddit": cand_rd,
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
        ("site.json", "site"),
        ("candidates-news.json", "cand_news"),
        ("candidates-reddit.json", "cand_reddit"),
    )
    for name, key in names:
        if key not in new:
            continue
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
    lines.extend(site_block_lines(c.get("site") or {}))
    return _capped(f"## {c['name']} ({c['slug']})", lines)


def site_block_lines(site):
    lines = []
    for url, s in site.items():
        if s.get("first_reading"):
            lines.append(f"SITE first reading {url} ({len(s.get('_lines') or [])} lines)")
        elif s.get("changed"):
            total = s.get("_added_total", len(s.get("added") or []))
            lines.append(f"SITE CHANGE {url} (+{total}/\u2212{s.get('removed_count', 0)})")
            lines.extend(f"  + {line}" for line in (s.get("added") or [])[:SITE_ADDED_PRINT])
    return lines


def _capped(head, lines):
    if not lines:
        return ""
    kept, size = [head], len(head)
    for line in lines:
        if size + len(line) + 1 > BLOCK_CAP - 20:
            kept.append("...(more omitted)")
            break
        kept.append(line)
        size += len(line) + 1
    return "\n".join(kept)


def build_candidate_block(c, diff):
    """### block for one discovery candidate, or '' when nothing is new for it."""
    if not (diff["first_listing"] or diff["new_news"] or diff["new_reddit"]):
        return ""
    lines = [
        f"what: {_clip(c['what'], CANDIDATE_WHAT_CHARS) if c['what'] else '(no description)'}",
        f"score: {c['score'] if c['score'] is not None else 'n/a'}  first seen: {c['first_seen'] or 'unknown'}",
    ]
    if c.get("url"):
        lines.append(f"url: {c['url']}")
    for n in diff["new_news"]:
        date = str(n.get("date") or "")[:16]
        lines.append(" ".join(f"NEW news {date} {_clip(n['title'], 110)} | {n['url']}".split()))
    if c["reddit_30d"] is not None:
        lines.append(f"Reddit 30d mentions: {c['reddit_30d']}")
    new_reddit = sorted(diff["new_reddit"], key=lambda p: (p.get("score") or 0), reverse=True)
    for p in new_reddit[:REDDIT_NEW_CAP]:
        date = str(p.get("created_at") or "")[:10]
        lines.append(
            " ".join(
                f"NEW reddit {date} r/{p['subreddit']} ({p['score']} pts) {_clip(p['title'], 100)} | {p['url']}".split()
            )
        )
        if p.get("excerpt"):
            lines.append(f"  > {_clip(p['excerpt'], 160)}")
    return _capped(f"### {c['name']} ({c['key']})", lines)


CANDIDATES_HEAD = "## CANDIDATES (discovery; not in the registry; verdict is Generalissimo's)"


def render(competitors, diffs, candidates=None, cand_diffs=None):
    parts = [b for c in competitors if (b := build_block(c, diffs[c["slug"]]))]
    cand_parts = [
        b for c in candidates or [] if (b := build_candidate_block(c, (cand_diffs or {}).get(c["key"]) or
                                                                     {"first_listing": True, "new_news": c["news"],
                                                                      "new_reddit": c.get("_reddit") or []}))
    ]
    if cand_parts:
        parts.append(CANDIDATES_HEAD + "\n\n" + "\n\n".join(cand_parts))
    errors = [f"{c['slug']}: {e}" for c in competitors for e in c["errors"]]
    errors += [f"candidate {c['key']}: {e}" for c in candidates or [] for e in c["errors"]]
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


def discovery_runs_root():
    root = os.environ.get("PULSE_DISCOVERY_RUNS")
    if root:
        return Path(root)
    return Path(os.environ.get("LOOPS_ROOT") or HERE.parents[1]) / "state/runs"


def discovery_seen_path():
    root = Path(os.environ.get("LOOPS_ROOT") or HERE.parents[1])
    return root / "loops.d/competitor-discovery/cache/seen.json"


def gather_candidates(reg_path, runs_root, seen_path, tavily_key=None, reddit_key=None, fns=None):
    """(candidates, source_path_or_None); absence or a bad file means no candidates, never a crash."""
    source = find_candidates_file(runs_root)
    if source is None:
        return [], None
    try:
        coverage = registry_coverage(reg_path)
    except Exception:
        return [], str(source)
    picked = select_candidates(load_candidates(source), coverage)
    seen = load_seen(seen_path)
    return [collect_candidate(c, seen, tavily_key, reddit_key, fns) for c in picked], str(source)


def tavily_key():
    """TAVILY_API_KEY from the environment (the harness .env, exported into the
    precheck by run-loop.sh), else the llm codex config via the shared probe."""
    key = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if key and not any(ch.isspace() for ch in key):
        return key
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
    source = find_candidates_file(discovery_runs_root())
    key = tavily_key() if source or any(r["status"] == "tracked" for r in rows) else None
    rd_key = redditgrow_key()
    competitors = [collect_competitor(r, now, key, reddit_key=rd_key) for r in rows]
    candidates, cand_source = gather_candidates(registry_path(), discovery_runs_root(), discovery_seen_path(), key, rd_key)
    cache = load_cache(cache_dir)
    diffs = apply_cache(competitors, cache)
    cand_diffs = apply_candidate_cache(candidates, cache)
    pulse = {
        "schema_version": 1,
        "generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_days": WINDOW_DAYS,
        "reddit_window_days": 30,
        "competitors": public(competitors),
        "candidates": public(candidates),
        "candidates_source": cand_source,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pulse.json").write_text(json.dumps(pulse, indent=1))
    text = render(competitors, diffs, candidates, cand_diffs)
    save_cache(cache_dir, next_cache(competitors, cache, candidates))
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
