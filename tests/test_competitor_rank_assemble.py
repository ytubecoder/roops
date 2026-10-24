"""Hermetic competitor-rank assemble.py tests: tmp loops root + registry, no network."""

import json
import os
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "loops.d/competitor-rank/assemble.py"
A = runpy.run_path(str(SCRIPT))

REGISTRY = """schema_version: 2
updated: 2026-10-24
competitors:
  - name: Tracked Tool
    slug: tracked
    status: tracked
    kind: hosted code search MCP
    capabilities:
      - one
      - two
      - three
      - four
      - five
      - six: with a colon
      - seven
    pricing: free tier plus $20
    threat: {level: high, note: "same buyer"}
    our_angle: we index your repos
  - name: Watched Tool
    slug: watched
    status: watch
    kind: local CLI
  - name: Quiet Tool
    slug: quiet
    status: tracked
    kind: editor
  - name: Gone Row
    slug: gone
    status: rejected
rejected:
  - { name: Old Junk, reason: "not a competitor" }
"""

LONG = "x" * 500


def pulse_doc(candidates=True):
    doc = {
        "schema_version": 1,
        "generated_at": "2026-10-20T11:35:00Z",
        "competitors": [
            {
                "slug": "tracked",
                "name": "Tracked Tool",
                "status": "tracked",
                "stars": {"o/tracked": {"stars": 120, "prev_stars": 100}},
                "downloads": {"npm": {"tracked": 5000}},
                "releases": [
                    {
                        "repo": "o/tracked",
                        "tag": f"v{i}",
                        "published_at": "2026-10-10T00:00:00Z",
                        "url": f"https://github.com/o/tracked/releases/v{i}",
                    }
                    for i in range(5)
                ],
                "mentions": {
                    "hn_90d": 4,
                    "reddit_30d": 2,
                    "reddit_top": [
                        {
                            "title": "Top post",
                            "url": "https://www.reddit.com/r/mcp/comments/top/",
                            "subreddit": "mcp",
                            "score": 30,
                            "created_at": "2026-10-12T00:00:00Z",
                        }
                    ],
                },
                "news": [
                    {
                        "title": f"<b>Launch</b> {i}",
                        "url": f"https://news.test/{i}",
                        "published": "Sun, 11 Oct 2026 19:00:00 GMT",
                    }
                    for i in range(7)
                ],
                "reddit": [
                    {
                        "title": "Tracked vs X",
                        "url": "https://www.reddit.com/r/mcp/comments/a1/",
                        "subreddit": "mcp",
                        "score": 12,
                        "created_at": "2026-10-14T00:00:00Z",
                        "excerpt": LONG,
                    }
                ],
                "site": {
                    "https://tracked.test/pricing": {
                        "added": ["New Team plan $30", "<div>raw</div>"],
                        "changed": True,
                        "first_reading": False,
                    },
                    "https://tracked.test/": {
                        "added": ["whole page"],
                        "first_reading": True,
                    },
                },
                "errors": [],
            },
            {
                "slug": "watched",
                "name": "Watched Tool",
                "status": "watch",
                "stars": {},
                "releases": [],
                "mentions": {"hn_90d": 0, "reddit_30d": 0, "reddit_top": []},
                "news": [],
                "reddit": [],
            },
        ],
    }
    if candidates:
        doc["candidates"] = [
            {
                "key": "newtool",
                "name": "New Tool",
                "kind": "repo graph MCP",
                "url": "https://newtool.test",
                "score": 7,
                "what": "indexes repos for agents",
                "reddit_30d": 3,
                "news": [
                    {
                        "title": "New Tool raises",
                        "url": "https://news.test/nt",
                        "date": "2026-10-18",
                    }
                ],
                "reddit_top": [
                    {
                        "title": "Anyone tried New Tool?",
                        "url": "https://www.reddit.com/r/x/comments/nt/",
                        "subreddit": "x",
                        "score": 4,
                        "created_at": "2026-10-17T00:00:00Z",
                    }
                ],
            }
        ]
    return doc


def contract(run_id, metrics):
    return {
        "schema_version": 1,
        "run_id": run_id,
        "status": "ok",
        "status_reason": "x",
        "headline": "h",
        "report_markdown": "r",
        "metrics": json.dumps(metrics),
        "findings": [],
    }


class Assemble(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "loops"
        self.registry = Path(self.tmp.name) / "registry.yaml"
        self.registry.write_text(REGISTRY)

    def write_pulse(self, candidates=True):
        run = self.root / "state/runs/20261020T113000Z-competitor-pulse-abc123"
        run.mkdir(parents=True)
        (run / "pulse.json").write_text(json.dumps(pulse_doc(candidates)))
        older = self.root / "state/runs/20261013T113000Z-competitor-pulse-old000"
        older.mkdir(parents=True)
        (older / "pulse.json").write_text(json.dumps({"competitors": []}))
        rep = self.root / "reports/competitor-pulse"
        rep.mkdir(parents=True)
        (rep / "latest.json").write_text(
            json.dumps(
                contract(
                    run.name,
                    {
                        "pulse": {
                            "tracked": {
                                "signal": "Shipped a pricing change",
                                "latest": [],
                            }
                        }
                    },
                )
            )
        )

    def write_voice(self):
        rep = self.root / "reports/gtm-research"
        rep.mkdir(parents=True)
        (rep / "latest.json").write_text(
            json.dumps(
                contract(
                    "20261019T093000Z-gtm-research-v1",
                    {
                        "research": {},
                        "competitor_voices": [
                            {
                                "competitor": "Tracked Tool",
                                "liked": ["fast", "cheap", "good docs", "mcp", "fifth"],
                                "disliked": ["flaky index"],
                                "implication": "i",
                                "confidence": "low",
                                "evidence_links": [
                                    "https://forum.test/1",
                                    "https://forum.test/2",
                                    "https://forum.test/3",
                                    "https://forum.test/4",
                                ],
                                "observed_at": "2026-10-19T09:31:00+00:00",
                            }
                        ],
                    },
                )
            )
        )

    def write_prior(self):
        rep = self.root / "reports/competitor-rank"
        rep.mkdir(parents=True)
        (rep / "latest.json").write_text(
            json.dumps(
                contract(
                    "20261013T123000Z-competitor-rank-p1",
                    {
                        "rank": {
                            "tracked": {
                                "features": 80,
                                "price": 40,
                                "positioning": 75,
                                "sentiment": None,
                                "tier": "core",
                            }
                        },
                        "rubric_version": 1,
                    },
                )
            )
        )

    def build(self):
        return A["build"](self.root, self.registry)

    def test_full_block_no_prior(self):
        self.write_pulse()
        self.write_voice()
        text, doc = self.build()
        self.assertIn("# MAGUYVA (reference)", text)
        self.assertIn("hosted MCP server that indexes your GitHub repositories", text)
        self.assertIn(
            "# PRIOR RANK (slug: tier features/price/positioning/sentiment)\nnone", text
        )
        self.assertIn("## Tracked Tool (tracked) [tracked]", text)
        self.assertIn("## Watched Tool (watched) [watch]", text)
        self.assertIn("signal: Shipped a pricing change", text)
        self.assertIn("news: 2026-10-11 Launch 0 | https://news.test/0", text)
        self.assertEqual(text.count("news: 2026-10-11 Launch"), 5)
        self.assertIn(
            "reddit: 2026-10-14 r/mcp (12) Tracked vs X | https://www.reddit.com/r/mcp/comments/a1/",
            text,
        )
        self.assertIn("reddit: 2026-10-12 r/mcp (30) Top post", text)
        self.assertIn("site: https://tracked.test/pricing + New Team plan $30", text)
        self.assertIn("site: https://tracked.test/ first reading (no diff yet)", text)
        self.assertNotIn("whole page", text)
        self.assertNotIn("<div>", text)
        self.assertNotIn("<b>", text)
        self.assertIn(
            "releases: v0 (2026-10-10); v1 (2026-10-10); v2 (2026-10-10)\n", text
        )
        self.assertIn("voice liked: fast; cheap; good docs; mcp\n", text)
        self.assertIn("voice disliked: flaky index", text)
        self.assertIn(
            "voice links: https://forum.test/1 https://forum.test/2 https://forum.test/3\n",
            text,
        )
        self.assertIn("capabilities: one; two; three; four; five; six: with a colon\n", text)
        self.assertIn("threat: high (same buyer)", text)
        self.assertIn(
            "traction: stars 120, downloads 5000, hn_90d 4, reddit_30d 2", text
        )
        quiet = text.split("## Quiet Tool (quiet) [tracked]")[1].split("\n\n")[0]
        self.assertIn("pulse: no pulse data", quiet)
        self.assertIn("evidence: none this cycle", quiet)
        self.assertIn("pulse: 20261020T113000Z-competitor-pulse-abc123", text)
        self.assertIn("voice: gtm-research 2026-10-19", text)
        self.assertIn("prior_rank: none (first run)", text)
        self.assertEqual(
            [c["slug"] for c in doc["competitors"]], ["tracked", "watched", "quiet"]
        )

    def test_rejected_excluded(self):
        self.write_pulse()
        text, doc = self.build()
        self.assertNotIn("(gone)", text)
        self.assertNotIn("## Old Junk", text)
        self.assertIn(
            "# REJECTED (registry verdict; never rank or promote)\nGone Row, Old Junk",
            text,
        )
        self.assertNotIn("gone", [c["slug"] for c in doc["competitors"]])

    def test_prior_rank_printed(self):
        self.write_pulse()
        self.write_prior()
        text, _ = self.build()
        self.assertIn(
            "# PRIOR RANK (slug: tier features/price/positioning/sentiment)\ntracked: core 80/40/75/-",
            text,
        )
        self.assertIn(
            "prior_rank: 2026-10-13 (20261013T123000Z-competitor-rank-p1)", text
        )

    def test_missing_pulse(self):
        text, doc = self.build()
        for name in (
            "Tracked Tool (tracked)",
            "Watched Tool (watched)",
            "Quiet Tool (quiet)",
        ):
            self.assertIn(f"## {name}", text)
        self.assertEqual(text.count("pulse: no pulse data"), 3)
        self.assertIn("pulse: missing (no competitor-pulse run with pulse.json)", text)
        self.assertIn("pulse_signals: missing", text)
        self.assertIn("voice: missing", text)
        self.assertIn("## CANDIDATES\nnone", text)
        self.assertIn("candidates: absent from pulse.json", text)
        self.assertFalse(doc["competitors"][0]["has_evidence"])

    def test_candidates(self):
        self.write_pulse()
        text, doc = self.build()
        self.assertIn("### New Tool (cand:newtool)", text)
        self.assertIn("what: indexes repos for agents", text)
        self.assertIn("score: 7, reddit_30d 3", text)
        self.assertIn("news: 2026-10-18 New Tool raises | https://news.test/nt", text)
        self.assertIn(
            "Anyone tried New Tool? | https://www.reddit.com/r/x/comments/nt/", text
        )
        self.assertEqual(doc["candidates"][0]["key"], "newtool")
        self.assertIn("candidates: 1 in pulse.json", text)

    def test_candidates_absent(self):
        self.write_pulse(candidates=False)
        text, _ = self.build()
        self.assertIn("## CANDIDATES\nnone", text)
        self.assertNotIn("cand:", text)

    def test_truncation(self):
        self.write_pulse()
        text, _ = self.build()
        line = next(ln for ln in text.splitlines() if ln.startswith("  > "))
        self.assertEqual(len(line), 4 + A["EXCERPT_CHARS"])
        self.assertTrue(line.endswith("…"))
        self.assertEqual(A["clip"]("a <p>b</p>  &amp; c", 50), "a b & c")
        self.assertEqual(len(A["clip"](LONG, 20)), 20)

    def test_fallback_registry_parser(self):
        data = A["_fallback_registry"](REGISTRY)
        rows = data["competitors"]
        self.assertEqual(
            [r["slug"] for r in rows], ["tracked", "watched", "quiet", "gone"]
        )
        self.assertEqual(rows[0]["capabilities"][:2], ["one", "two"])
        self.assertEqual(rows[0]["threat"], {"level": "high", "note": "same buyer"})
        self.assertEqual(data["rejected"], [{"name": "Old Junk"}])

    def test_main_writes_rank_input(self):
        self.write_pulse()
        out = Path(self.tmp.name) / "out"
        repo = Path(self.tmp.name) / "repo"
        reg = repo / A["REGISTRY_REL"]
        reg.parent.mkdir(parents=True)
        reg.write_text(REGISTRY)
        env = {
            **os.environ,
            "LOOPS_ROOT": str(self.root),
            "OUT_DIR": str(out),
            "MAGUYVA_REPO": str(repo),
        }
        proc = subprocess.run(
            [sys.executable, str(SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("## Tracked Tool (tracked) [tracked]", proc.stdout)
        doc = json.loads((out / "rank-input.json").read_text())
        self.assertEqual(doc["schema_version"], 1)
        self.assertEqual(doc["competitors"][0]["news"][0]["url"], "https://news.test/0")
        self.assertEqual(doc["candidates"][0]["key"], "newtool")

    def test_main_unreadable_registry(self):
        env = {
            **os.environ,
            "LOOPS_ROOT": str(self.root),
            "OUT_DIR": str(Path(self.tmp.name) / "o"),
            "MAGUYVA_REPO": str(Path(self.tmp.name) / "nope"),
        }
        proc = subprocess.run(
            [sys.executable, str(SCRIPT)],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("registry: unreadable", proc.stdout)


if __name__ == "__main__":
    unittest.main()
