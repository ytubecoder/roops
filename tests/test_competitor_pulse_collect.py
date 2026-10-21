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
        count, top, posts = C["reddit_mentions"](row, "rg", get=lambda url, headers=None, body=None: {"items": rows})
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


if __name__ == "__main__":
    unittest.main()
