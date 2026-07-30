"""Tests for the self-containment scanner itself.

Without these the scanner is unfalsifiable: a version that always returned []
would make every `assert_self_contained` call pass vacuously. The two tables
below are the contract — what must be caught, and what must not be.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from html_selfcontained import external_subresources

# Shapes that fetch on load. A page containing any of these is not offline-safe.
FETCHING = {
    "external stylesheet": '<link rel="stylesheet" href="https://cdn.example/x.css">',
    "webfont @import": '<style>@import url("https://fonts.example/css?f=x");</style>',
    "@font-face url()": "<style>@font-face{src:url(https://f.example/a.woff2)}</style>",
    "remote script": '<script src="http://cdn.example/a.js"></script>',
    # The case a raw `http://` substring ban misses entirely.
    "protocol-relative script": '<script src="//cdn.example/a.js"></script>',
    "tracking pixel": '<img src="http://tracker.example/pixel.gif">',
    "srcset candidate": '<img srcset="https://x.example/a.png 1x, local.png 2x">',
    "inline style url()": '<div style="background:url(https://x.example/b.png)"></div>',
    "preconnect": '<link rel="preconnect" href="https://fonts.example">',
    "remote favicon": '<link rel="icon" href="https://x.example/f.ico">',
    "iframe": '<iframe src="https://x.example/embed"></iframe>',
    "object data": '<object data="https://x.example/o.svg"></object>',
}

# Shapes that fetch nothing. Banning these would mean mangling honest content.
INERT = {
    # The real dashboard contains exactly this: a probe reporting a dead URL.
    "url in escaped text": "<pre>curl_exit 7 for http://127.0.0.1:9/dead</pre>",
    "svg xmlns in data uri": (
        '<link rel="icon" href="data:image/svg+xml,'
        "<svg xmlns='http://www.w3.org/2000/svg'></svg>\">"
    ),
    "navigation link": '<a href="https://github.com/automic-vault/av">docs</a>',
    "data uri image": '<img src="data:image/png;base64,AAAA">',
    "relative stylesheet": '<link rel="stylesheet" href="kit.css">',
    "in-page anchor": '<a href="#top">top</a>',
    "inlined style block": "<style>:root{--bg:#0e0f12}</style>",
    "canonical link": '<link rel="canonical" href="https://example.com/page">',
}


class SelfContainmentScannerTests(unittest.TestCase):
    def test_fetching_shapes_are_caught(self):
        for label, markup in FETCHING.items():
            with self.subTest(shape=label):
                self.assertTrue(
                    external_subresources(markup),
                    f"{label} fetches on load but was not caught",
                )

    def test_inert_shapes_are_allowed(self):
        for label, markup in INERT.items():
            with self.subTest(shape=label):
                self.assertEqual(
                    external_subresources(markup),
                    [],
                    f"{label} fetches nothing but was flagged",
                )

    def test_reports_what_it_found(self):
        found = external_subresources('<img src="https://x.example/a.png">')
        self.assertEqual(found, [("img", "src", "https://x.example/a.png")])

    def test_empty_and_degenerate_input(self):
        for markup in ("", "<html></html>", "plain text", "<img>", '<img src="">'):
            self.assertEqual(external_subresources(markup), [])


if __name__ == "__main__":
    unittest.main()
