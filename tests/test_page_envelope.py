"""Hermetic tests for bin/page_envelope.py (INTERFACES Amendment 2 §12)."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import page_envelope  # noqa: E402


def make_page(meta=None, body_extra="", envelope_count=1):
    meta = meta if meta is not None else {
        "loop": "demo",
        "run_id": "20260730T000000Z-demo-abc123",
        "generated_at": "2026-07-30T00:00:01Z",
        "title": "Demo page",
        "page_class": "snapshot",
        "totals": {"findings": 2},
    }
    envelope = json.dumps({"meta": meta, "data": {}}).replace("</", "<\\/")
    block = f'<script type="application/json" id="report-data">{envelope}</script>'
    return (
        "<!DOCTYPE html><html><head><title>t</title></head><body>"
        + body_extra
        + block * envelope_count
        + "</body></html>"
    )


class PageEnvelopeTests(unittest.TestCase):
    def write(self, content, mode="w"):
        fd, path = tempfile.mkstemp(suffix=".html")
        with os.fdopen(fd, mode) as f:
            f.write(content)
        self.addCleanup(os.remove, path)
        return path

    def test_valid_page_passes(self):
        path = self.write(make_page())
        self.assertEqual(page_envelope.check_page(path), [])

    def test_expect_run_id_and_loop_mismatch(self):
        path = self.write(make_page())
        errs = page_envelope.check_page(path, expect_run_id="other", expect_loop="nope")
        self.assertTrue(any("run_id" in e for e in errs))
        self.assertTrue(any("loop" in e for e in errs))

    def test_missing_envelope_fails(self):
        path = self.write("<html><body>no envelope</body></html>")
        self.assertTrue(page_envelope.check_page(path))

    def test_duplicate_envelope_fails(self):
        path = self.write(make_page(envelope_count=2))
        errs = page_envelope.check_page(path)
        self.assertTrue(any("exactly one" in e for e in errs))

    def test_missing_required_meta_field_fails(self):
        meta = {"loop": "demo", "run_id": "r", "generated_at": "2026-07-30T00:00:01Z",
                "page_class": "snapshot"}  # no title
        path = self.write(make_page(meta=meta))
        self.assertTrue(any("title" in e for e in page_envelope.check_page(path)))

    def test_bad_generated_at_fails(self):
        meta = {"loop": "demo", "run_id": "r", "generated_at": "yesterday",
                "title": "t", "page_class": "snapshot"}
        path = self.write(make_page(meta=meta))
        self.assertTrue(any("generated_at" in e for e in page_envelope.check_page(path)))

    def test_bad_page_class_fails(self):
        meta = {"loop": "demo", "run_id": "r", "generated_at": "2026-07-30T00:00:01Z",
                "title": "t", "page_class": "fancy"}
        path = self.write(make_page(meta=meta))
        self.assertTrue(any("page_class" in e for e in page_envelope.check_page(path)))

    def test_nested_totals_fails(self):
        meta = {"loop": "demo", "run_id": "r", "generated_at": "2026-07-30T00:00:01Z",
                "title": "t", "page_class": "snapshot", "totals": {"nested": {"x": 1}}}
        path = self.write(make_page(meta=meta))
        self.assertTrue(any("totals" in e for e in page_envelope.check_page(path)))

    def test_external_fetch_markup_fails(self):
        path = self.write(make_page(body_extra='<script src="https://cdn.example/x.js"></script>'))
        self.assertTrue(any("external" in e for e in page_envelope.check_page(path)))

    def test_plain_anchor_href_is_allowed(self):
        path = self.write(make_page(body_extra='<a href="https://docs.example/page">docs</a>'))
        self.assertEqual(page_envelope.check_page(path), [])

    def test_secret_value_fails_redaction_clean(self):
        path = self.write(make_page(body_extra="<pre>ghp_" + "a" * 24 + "</pre>"))
        self.assertTrue(any("redaction" in e for e in page_envelope.check_page(path)))

    def test_oversize_page_fails(self):
        path = self.write(make_page(body_extra="x" * (page_envelope.MAX_PAGE_BYTES + 1)))
        self.assertTrue(any("8 MiB" in e or "size" in e for e in page_envelope.check_page(path)))

    def test_read_meta_returns_meta(self):
        path = self.write(make_page())
        meta = page_envelope.read_meta(path)
        self.assertEqual(meta["loop"], "demo")
        self.assertEqual(meta["totals"]["findings"], 2)

    def test_read_meta_none_on_garbage(self):
        path = self.write("<html>nope</html>")
        self.assertIsNone(page_envelope.read_meta(path))


if __name__ == "__main__":
    unittest.main()
