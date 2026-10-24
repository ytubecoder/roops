"""Hermetic competitor-pulse collect.py tests: fake fetchers, no network."""

import json
import runpy
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
C = runpy.run_path(str(ROOT / "loops.d/competitor-pulse/collect.py"))
NOW = datetime(2026, 10, 15, tzinfo=timezone.utc)
REGISTRY = """schema_version: 2
competitors:
  - {name: Tracked Tool, slug: tracked, status: tracked, repos: [o/tracked],
     voice_hints: ["tracked mcp"], packages: {npm: [tracked-pkg], brew: [tracked]},
     news_urls: ["https://example.test/changelog"]}
  - {name: Watched Tool, slug: watched, status: watch, repos: [o/watched]}
  - {name: Gone, slug: gone, status: rejected, repos: [o/gone]}
"""


def fns(stars=100, fail=None):
    def stars_fn(repo):
        if fail == "stars":
            raise C["FetchError"]("http 403")
        return {"stars": stars, "pushed_at": "2026-10-14T00:00:00Z"}

    return {
        "stars": stars_fn,
        "rels": lambda repo: [
            {"repo": repo, "tag": "v1", "title": "v1", "published_at": "2026-10-10T00:00:00Z",
             "url": f"https://github.com/{repo}/releases/tag/v1"}
        ],
        "dl": lambda kind, pkg: 5000,
        "hn": lambda q, now: (7, [{"title": "T", "url": "https://hn.test/1", "points": 9, "created_at": "x"}]),
        "tv": lambda row, key: [
            {"title": "Launch", "url": "https://news.test/a", "published": "2026-10-12",
             "source": "news.test", "snippet": "s"}
        ],
        "page": lambda url: {"title": "Changelog", "url": url, "published": None,
                             "source": "example.test", "snippet": "x"},
        "site": lambda url, now: C["site_reading"](url, now, get=lambda u: "<p>Stable page text long enough.</p>"),
        "rd": lambda row, key: (
            2,
            [{"title": "Tracked vs X", "url": "https://www.reddit.com/r/mcp/comments/a1/x/",
              "subreddit": "mcp", "score": 12, "created_at": "2026-10-14T00:00:00Z"}],
            [{"title": "Tracked vs X", "url": "https://www.reddit.com/r/mcp/comments/a1/x/",
              "subreddit": "mcp", "score": 12, "num_comments": 3,
              "created_at": "2026-10-14T00:00:00Z", "excerpt": "which one indexes a repo"}],
        ),
    }


class Pulse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        reg = self.dir / "registry.yaml"
        reg.write_text(REGISTRY)
        self.rows = C["load_registry"](reg)

    def collect(self, **kw):
        return [C["collect_competitor"](r, NOW, "k", fns(**kw), reddit_key="rg") for r in self.rows]

    def test_registry_filters_rejected(self):
        self.assertEqual([r["slug"] for r in self.rows], ["tracked", "watched"])

    def test_watch_rows_skip_news_and_tavily(self):
        tracked, watched = self.collect()
        self.assertEqual(len(tracked["news"]), 2)
        self.assertEqual(watched["news"], [])
        self.assertEqual(tracked["downloads"]["npm"], {"tracked-pkg": 5000})
        self.assertEqual(tracked["mentions"]["hn_90d"], 7)

    def test_error_becomes_string_not_crash(self):
        tracked, _ = self.collect(fail="stars")
        self.assertIn("github: http 403", tracked["errors"])
        self.assertTrue(tracked["releases"])

    def test_missing_key_is_an_error_row(self):
        c = C["collect_competitor"](self.rows[0], NOW, None, fns())
        self.assertIn("tavily: no key", c["errors"])

    def test_first_run_everything_new_then_quiet(self):
        comps = self.collect()
        cache = C["load_cache"](self.dir / "cache")
        diffs = C["apply_cache"](comps, cache)
        text = C["render"](comps, diffs)
        self.assertIn("## Tracked Tool (tracked)", text)
        self.assertIn("NEW release o/tracked v1 2026-10-10", text)
        self.assertIn("https://news.test/a", text)
        self.assertNotIn("ERRORS:", text)
        C["save_cache"](self.dir / "cache", C["next_cache"](comps, cache))
        again = self.collect()
        cache = C["load_cache"](self.dir / "cache")
        self.assertEqual(C["render"](again, C["apply_cache"](again, cache)), "")

    def test_stars_delta_prev_stars_and_percent(self):
        comps = self.collect(stars=100)
        cache = C["load_cache"](self.dir / "cache")
        C["save_cache"](self.dir / "cache", C["next_cache"](comps, cache))
        grown = self.collect(stars=125)
        cache = C["load_cache"](self.dir / "cache")
        diffs = C["apply_cache"](grown, cache)
        self.assertEqual(grown[0]["stars"]["o/tracked"]["prev_stars"], 100)
        self.assertIn("(+25, +25.0%)", C["render"](grown, diffs))

    def test_failed_star_fetch_keeps_previous_reading(self):
        cache = C["load_cache"](self.dir / "cache")
        C["save_cache"](self.dir / "cache", C["next_cache"](self.collect(stars=100), cache))
        cache = C["load_cache"](self.dir / "cache")
        new = C["next_cache"](self.collect(fail="stars"), cache)
        self.assertEqual(new["stars"]["tracked"], {"o/tracked": 100})

    def test_errors_alone_print_and_block_cap(self):
        comps = self.collect(fail="stars")
        cache = {"stars": {}, "releases": set(), "news": set(), "hn": {}}
        diffs = C["apply_cache"](comps, cache)
        long_title = "x" * 5000
        comps[0]["news"] = [
            {"title": long_title, "url": f"https://n.test/{i}", "published": None, "source": "n", "snippet": ""}
            for i in range(40)
        ]
        diffs = C["apply_cache"](comps, cache)
        block = C["build_block"](comps[0], diffs["tracked"])
        self.assertLessEqual(len(block), C["BLOCK_CAP"])
        self.assertIn("ERRORS:\ntracked: github: http 403", C["render"](comps, diffs))

    def test_total_cap_truncates_with_note(self):
        comps = self.collect()
        comps[0]["errors"] = ["tavily: http 500"] * 5000
        text = C["render"](comps, C["apply_cache"](comps, {"stars": {}, "releases": set(), "news": set(), "hn": {}}))
        self.assertLessEqual(len(text.encode()), C["STDOUT_CAP"])
        self.assertIn("TRUNCATED", text)

    def test_visible_text_strips_scripts_and_tags(self):
        title, text = C["visible_text"](
            "<html><head><title>Hi &amp; bye</title></head><body><script>var a=1</script>"
            "<p>Hello   <b>world</b></p><style>p{}</style></body></html>"
        )
        self.assertEqual((title, text), ("Hi & bye", "Hello world"))

    def test_pulse_json_shape_roundtrip(self):
        comps = self.collect()
        json.dumps(comps)
        for key in ("slug", "name", "status", "stars", "releases", "downloads", "mentions", "news", "errors"):
            self.assertIn(key, comps[0])

    def test_reddit_posts_print_once_then_only_count_moves(self):
        comps = self.collect()
        self.assertEqual(comps[0]["mentions"]["reddit_30d"], 2)
        self.assertEqual(comps[1]["mentions"]["reddit_30d"], 2)  # watch rows get Reddit too
        cache = C["load_cache"](self.dir / "cache")
        text = C["render"](comps, C["apply_cache"](comps, cache))
        self.assertIn("KNOWN COMPETITORS (registry rows): Tracked Tool (tracked), Watched Tool (watched)", text)
        self.assertIn("NEW reddit 2026-10-14 r/mcp (12 pts) Tracked vs X | https://www.reddit.com/r/mcp/comments/a1/x/", text)
        self.assertIn("  > which one indexes a repo", text)
        self.assertIn("Reddit 30d mentions: 2 (prev none)", text)
        C["save_cache"](self.dir / "cache", C["next_cache"](comps, cache))
        cache = C["load_cache"](self.dir / "cache")
        self.assertEqual(cache["reddit_count"], {"tracked": 2, "watched": 2})
        again = self.collect()
        text = C["render"](again, C["apply_cache"](again, cache))
        self.assertEqual(text, "")  # same posts, same count: quiet week

    def test_missing_reddit_key_is_an_error_row(self):
        c = C["collect_competitor"](self.rows[1], NOW, None, fns())
        self.assertIn("redditgrow: no key", c["errors"])
        self.assertIsNone(c["mentions"]["reddit_30d"])

    def test_old_cache_without_reddit_keys_still_works(self):
        comps = self.collect()
        cache = {"stars": {}, "releases": set(), "news": set(), "hn": {}}
        diffs = C["apply_cache"](comps, cache)
        self.assertEqual(len(diffs["tracked"]["new_reddit"]), 1)
        self.assertIn("reddit", C["next_cache"](comps, cache))

    def test_reddit_terms_add_distinctive_tokens_only(self):
        self.assertEqual(C["reddit_terms"]({"slug": "cursor", "brand_terms": ["cursor ai", "cursor editor"]}),
                         ["cursor ai", "cursor editor", "cursor"])
        self.assertEqual(C["reddit_terms"]({"slug": "cc", "brand_terms": ["claude context mcp"]}),
                         ["claude context mcp"])
        self.assertEqual(C["reddit_terms"]({"slug": "x", "name": "Pharaoh"}), ["Pharaoh"])

    def test_reddit_job_posts_are_dropped(self):
        row = {"slug": "sourcegraph", "name": "Sourcegraph", "status": "tracked", "brand_terms": ["sourcegraph"]}
        rows = [
            {"id": "1", "title": "Sourcegraph [HIRING] a Tech Lead - Code Plane", "selftext": "", "subreddit": "jobboardsearch",
             "score": 1, "num_comments": 0, "created_utc": "1792420361", "permalink": "/r/jobboardsearch/comments/1/x/"},
            {"id": "2", "title": "Using Sourcegraph Deep Search in Claude Code", "selftext": "", "subreddit": "sourcegraph",
             "score": 2, "num_comments": 0, "created_utc": "1792420361", "permalink": "/r/sourcegraph/comments/2/y/"},
        ]
        count, top, _posts = C["reddit_mentions"](row, "rg", get=lambda url, headers=None, body=None: {"items": rows})
        self.assertEqual(count, 1)
        self.assertEqual(top[0]["url"], "https://www.reddit.com/r/sourcegraph/comments/2/y/")

    def test_reddit_relevance_gate(self):
        row = {"slug": "amp", "name": "Amp", "status": "watch", "brand_terms": ["amp"]}
        rows = [
            {"id": "1", "title": "Brave dog tugs a rope with Amp", "selftext": "", "subreddit": "dogs",
             "score": 5, "num_comments": 0, "created_utc": "1792420361", "permalink": "/r/dogs/comments/1/x/"},
            {"id": "2", "title": "Amp vs Cody for a large codebase", "selftext": "", "subreddit": "somewhere",
             "score": 9, "num_comments": 2, "created_utc": "1792420361", "permalink": "/r/somewhere/comments/2/y/"},
            {"id": "3", "title": "amplifier build", "selftext": "", "subreddit": "mcp",
             "score": 1, "num_comments": 0, "created_utc": "0", "permalink": "/r/mcp/comments/3/z/"},
            {"id": "4", "title": "trying Amp today", "selftext": "", "subreddit": "ClaudeCode",
             "score": 1, "num_comments": 0, "created_utc": "1792420361", "url": "https://www.reddit.com/r/ClaudeCode/comments/4/w/"},
        ]
        count, top, posts = C["reddit_mentions"](row, "rg", get=lambda url, headers=None, body=None: {"items": rows})
        self.assertEqual(count, 2)
        self.assertEqual([p["url"] for p in top], [
            "https://www.reddit.com/r/somewhere/comments/2/y/",
            "https://www.reddit.com/r/ClaudeCode/comments/4/w/",
        ])
        self.assertNotIn("author", posts[0])
        self.assertEqual(posts[0]["created_at"], "2026-10-19T14:32:41Z")


PAGE_V1 = """<html><head><title>Acme pricing</title><script>var x = "Hidden script text that is long";</script></head>
<body><nav><a>Home navigation link text that is long</a></nav>
<h1>Acme indexes your whole codebase for agents</h1>
<p>Plans start at twenty dollars per seat per month. Teams get shared context.</p>
<p>We use cookies to improve your experience on this website.</p>
<p>Short line</p><p>2026-10-24</p>
<footer>Copyright 2026 Acme Inc. All rights reserved worldwide.</footer></body></html>"""
PAGE_V2 = PAGE_V1.replace("<p>Short line</p>", "<p>New: Acme MCP server ships repo-wide context for every agent.</p>")
SITE_REGISTRY = """schema_version: 2
competitors:
  - {name: Acme, slug: acme, status: tracked, seed_urls: ["https://acme.test/", "https://acme.test/pricing",
     "https://acme.test/a", "https://acme.test/b"]}
"""


class Site(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        reg = self.dir / "registry.yaml"
        reg.write_text(SITE_REGISTRY)
        self.row = C["load_registry"](reg)[0]
        self.calls = []

    def run_once(self, page, fail_url=None):
        def get(url):
            self.calls.append(url)
            if url == fail_url:
                raise C["FetchError"]("http 503")
            return page

        f = fns()
        f["site"] = lambda url, now: C["site_reading"](url, now, get=get)
        c = C["collect_competitor"](self.row, NOW, "k", f, reddit_key="rg")
        cache = C["load_cache"](self.dir / "cache")
        diffs = C["apply_cache"]([c], cache)
        text = C["build_block"](c, diffs["acme"])
        C["save_cache"](self.dir / "cache", C["next_cache"]([c], cache))
        return c, text

    def test_lines_skip_noise_short_and_boilerplate(self):
        lines = C["site_lines"](PAGE_V1)
        self.assertIn("Acme indexes your whole codebase for agents", lines)
        self.assertIn("Plans start at twenty dollars per seat per month.", lines)
        self.assertIn("Teams get shared context.", lines)
        joined = " ".join(lines)
        for gone in ("Hidden script", "navigation", "cookies", "Short line", "2026-10-24", "rights reserved"):
            self.assertNotIn(gone, joined)

    def test_first_reading_change_then_quiet(self):
        c, text = self.run_once(PAGE_V1)
        self.assertEqual(len(self.calls), 3)  # cap 3 urls per row
        self.assertIn("SITE first reading https://acme.test/ (3 lines)", text)
        s = c["site"]["https://acme.test/"]
        self.assertTrue(s["first_reading"])
        self.assertEqual(s["title"], "Acme pricing")
        cached = json.loads((self.dir / "cache/site.json").read_text())
        self.assertEqual(len(cached["https://acme.test/"]["lines"]), 3)
        self.assertTrue(cached["https://acme.test/"]["sha"])

        c, text = self.run_once(PAGE_V2)
        line = "New: Acme MCP server ships repo-wide context for every agent."
        self.assertIn("SITE CHANGE https://acme.test/ (+1/\u22120)", text)
        self.assertIn("  + " + line, text)
        s = c["site"]["https://acme.test/pricing"]
        self.assertEqual((s["first_reading"], s["changed"], s["added"], s["removed_count"]), (False, True, [line], 0))
        public = C["public"](c)["site"]["https://acme.test/"]
        self.assertEqual(set(public), {"fetched_at", "title", "first_reading", "changed", "added", "removed_count"})

        c, text = self.run_once(PAGE_V2)
        self.assertNotIn("SITE", text)
        self.assertFalse(c["site"]["https://acme.test/"]["changed"])

    def test_fetch_error_is_a_row_error_and_keeps_old_reading(self):
        self.run_once(PAGE_V1)
        c, text = self.run_once(PAGE_V2, fail_url="https://acme.test/pricing")
        self.assertIn("site acme.test: http 503", c["errors"])
        self.assertIn("SITE CHANGE https://acme.test/ ", text)  # other urls still ran
        cached = json.loads((self.dir / "cache/site.json").read_text())
        self.assertNotIn("New: Acme", " ".join(cached["https://acme.test/pricing"]["lines"]))

    def test_old_cache_without_site_json_loads(self):
        cache_dir = self.dir / "cache"
        cache_dir.mkdir()
        (cache_dir / "stars.json").write_text("{}")
        cache = C["load_cache"](cache_dir)
        self.assertEqual(cache["site"], {})
        c, text = self.run_once(PAGE_V1)
        self.assertIn("SITE first reading", text)
        legacy = {"stars": {}, "releases": set(), "news": set(), "hn": {}}
        C["apply_cache"]([c], legacy)
        self.assertIn("site", C["next_cache"]([c], legacy))


CAND_REGISTRY = """schema_version: 2
competitors:
  - {name: Known, slug: known, status: tracked, repos: [Org/Known], domains: [known.test],
     seed_urls: ["https://github.com/org/seeded"]}
  - {name: Gone, slug: gone, status: rejected, repos: [org/gone-row]}
rejected:
  - {name: Nope, domains: [nope.test], reason: x}
"""


def cand(i, **kw):
    row = {"name": f"o/c{i}", "kind": "github repo", "repo": f"o/c{i}", "domain": None, "stars": 10,
           "description": f"Candidate {i} indexes code", "html_url": f"https://github.com/o/c{i}",
           "evidence_urls": [], "why_surfaced": [], "score": float(i), "verdict": ""}
    row.update(kw)
    return row


def cand_fns(news_url="https://news.test/c"):
    return {
        "home": lambda domain: f"{domain} title — meta",
        "tv": lambda row, key: [{"title": f"{row['name']} launch", "url": news_url, "published": "2026-10-20",
                                 "source": "news.test", "snippet": ""}],
        "rd": lambda row, key: (1, [{"title": "t", "url": "https://www.reddit.com/r/mcp/comments/z/",
                                     "subreddit": "mcp", "score": 3, "created_at": "2026-10-20T00:00:00Z"}],
                                [{"title": "t", "url": "https://www.reddit.com/r/mcp/comments/z/", "subreddit": "mcp",
                                  "score": 3, "num_comments": 0, "created_at": "2026-10-20T00:00:00Z", "excerpt": ""}]),
    }


class Candidates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.reg = self.dir / "registry.yaml"
        self.reg.write_text(CAND_REGISTRY)
        self.runs = self.dir / "runs"
        self.seen = self.dir / "seen.json"
        self.seen.write_text(json.dumps({"o/c12": {"first_seen": "2026-10-15", "best_score": 12, "name": "o/c12"}}))
        rows = [cand(i) for i in range(1, 13)] + [
            cand(90, repo="org/known", name="org/known"),
            cand(91, repo="ORG/seeded", name="ORG/seeded"),
            cand(92, repo="org/gone-row"),
            cand(93, kind="product domain", repo=None, domain="docs.nope.test", name="Nope Docs"),
            cand(94, verdict="rejected"),
            cand(95, kind="product domain", repo=None, domain="www.fresh.test", name="Fresh", html_url=None),
        ]
        for name, data in (("20261015T000000Z-competitor-discovery-aaa", [cand(99)]),
                           ("20261021T000000Z-competitor-discovery-bbb", rows)):
            d = self.runs / name / "discovery" / name[:8]
            d.mkdir(parents=True)
            (d / "candidates.json").write_text(json.dumps({"schema_version": 1, "candidates": data}))
        (self.runs / "20261022T000000Z-competitor-discovery-ccc").mkdir()  # newer run, no output yet

    def gather(self, **kw):
        return C["gather_candidates"](self.reg, self.runs, self.seen, "k", "rg", cand_fns(**kw))

    def test_selection_skips_covered_and_verdicted_and_caps(self):
        cands, source = self.gather()
        self.assertIn("20261021T000000Z-competitor-discovery-bbb", source)
        self.assertEqual(len(cands), C["CANDIDATE_CAP"])
        keys = [c["key"] for c in cands]
        self.assertEqual(keys[0], "fresh.test")  # score 95 first; repo-less keys by domain
        for gone in ("org/known", "org/seeded", "org/gone-row", "docs.nope.test", "o/c94"):
            self.assertNotIn(gone, keys)
        self.assertEqual(keys[1:], ["o/c12", "o/c11", "o/c10", "o/c9", "o/c8", "o/c7", "o/c6"])

    def test_output_shape(self):
        cands, _ = self.gather()
        fresh, c12 = cands[0], cands[1]
        self.assertEqual(fresh["what"], "fresh.test title — meta")
        self.assertEqual(fresh["url"], "https://fresh.test/")
        self.assertEqual(c12["what"], "Candidate 12 indexes code")
        self.assertEqual(c12["first_seen"], "2026-10-15")
        self.assertEqual(c12["news"], [{"title": "c12 launch", "url": "https://news.test/c", "date": "2026-10-20"}])
        self.assertEqual(c12["reddit_30d"], 1)
        pub = C["public"](c12)
        self.assertEqual(set(pub), {"key", "name", "kind", "url", "repo", "domain", "score", "first_seen", "what",
                                    "news", "reddit_30d", "reddit_top", "errors"})
        json.dumps(pub)

    def test_missing_discovery_dir_is_empty(self):
        cands, source = C["gather_candidates"](self.reg, self.dir / "absent", self.seen, "k", "rg", cand_fns())
        self.assertEqual((cands, source), ([], None))
        self.assertEqual(C["render"]([], {}, cands, {}), "")

    def test_no_keys_skip_searches(self):
        cands, _ = C["gather_candidates"](self.reg, self.runs, self.seen, None, None, cand_fns())
        self.assertEqual(cands[1]["news"], [])
        self.assertIsNone(cands[1]["reddit_30d"])
        self.assertEqual(cands[1]["errors"], [])

    def test_block_then_news_not_reprinted(self):
        cache_dir = self.dir / "cache"
        cands, _ = self.gather()
        cache = C["load_cache"](cache_dir)
        text = C["render"]([], {}, cands, C["apply_candidate_cache"](cands, cache))
        self.assertIn(C["CANDIDATES_HEAD"], text)
        self.assertIn("### o/c12 (o/c12)", text)
        self.assertIn("what: Candidate 12 indexes code", text)
        self.assertIn("score: 12.0  first seen: 2026-10-15", text)
        self.assertIn("NEW news 2026-10-20 c12 launch | https://news.test/c", text)
        self.assertIn("Reddit 30d mentions: 1", text)
        self.assertIn("NEW reddit 2026-10-20 r/mcp (3 pts) t | https://www.reddit.com/r/mcp/comments/z/", text)
        C["save_cache"](cache_dir, C["next_cache"]([], cache, cands))
        again, _ = self.gather()
        cache = C["load_cache"](cache_dir)
        self.assertEqual(C["render"]([], {}, again, C["apply_candidate_cache"](again, cache)), "")
        fresh_news, _ = self.gather(news_url="https://news.test/d")
        text = C["render"]([], {}, fresh_news, C["apply_candidate_cache"](fresh_news, cache))
        self.assertIn("https://news.test/d", text)
        self.assertNotIn("https://news.test/c", text)
        self.assertNotIn("NEW reddit", text)

    def test_candidate_error_lands_in_errors(self):
        f = cand_fns()
        f["home"] = lambda domain: (_ for _ in ()).throw(C["FetchError"]("timeout"))
        cands, _ = C["gather_candidates"](self.reg, self.runs, self.seen, "k", "rg", f)
        self.assertIn("home fresh.test: timeout", cands[0]["errors"])
        text = C["render"]([], {}, cands, C["apply_candidate_cache"](cands, {}))
        self.assertIn("candidate fresh.test: home fresh.test: timeout", text)


if __name__ == "__main__":
    unittest.main()
