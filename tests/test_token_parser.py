"""Non-vacuity self-test for token_parser (WP3).

Without these, test_token_drift.py could pass vacuously if the parser always
returned empty or identical maps. Mirrors tests/test_html_selfcontained.py.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from token_parser import (
    compare_token_maps,
    extract_dark_tokens,
    extract_light_tokens,
    parse_root_block,
)

# Minimal garden-shaped CSS: base :root + data-theme dark override.
LIGHT_DARK_CSS = """
:root {
  color-scheme: light;
  --sumi: #1C1A17;
  --washi: #F2EDE3;
  --nibi: #8C8578;
  --nibi-faint: #ABA495;
  --serif: "Hiragino Mincho ProN", Georgia, serif;
  --mono: ui-monospace, Menlo, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --sumi: #E7E9EC;
    --washi: #0E0F12;
    --nibi: #9AA1AB;
    --nibi-faint: #5D6570;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --sumi: #E7E9EC;
  --washi: #0E0F12;
  --nibi: #9AA1AB;
  --nibi-faint: #5D6570;
}
:root[data-theme="light"] {
  color-scheme: light;
  --sumi: #1C1A17;
  --washi: #F2EDE3;
  --nibi: #8C8578;
  --nibi-faint: #ABA495;
}
"""

# Same light values, but dark --nibi drifted.
DRIFTED_DARK_CSS = """
:root {
  --sumi: #1C1A17;
  --washi: #F2EDE3;
  --nibi: #8C8578;
  --nibi-faint: #ABA495;
  --serif: Georgia, serif;
  --mono: Menlo, monospace;
}
:root[data-theme="dark"] {
  --sumi: #E7E9EC;
  --washi: #0E0F12;
  --nibi: #DEADBE;
  --nibi-faint: #5D6570;
}
"""

# Dark block missing --nibi-faint.
MISSING_TOKEN_CSS = """
:root {
  --sumi: #1C1A17;
  --washi: #F2EDE3;
  --nibi: #8C8578;
  --nibi-faint: #ABA495;
  --serif: Georgia, serif;
  --mono: Menlo, monospace;
}
:root[data-theme="dark"] {
  --sumi: #E7E9EC;
  --washi: #0E0F12;
  --nibi: #9AA1AB;
}
"""


class ParseRootBlockTests(unittest.TestCase):
    def test_one_declaration_per_line(self):
        body = "--sumi: #1C1A17;\n  --washi: #F2EDE3;\n"
        self.assertEqual(
            parse_root_block(body),
            {"--sumi": "#1C1A17", "--washi": "#F2EDE3"},
        )

    def test_ignores_non_custom_properties(self):
        body = "color-scheme: light;\n--sumi: #1C1A17;\n"
        self.assertEqual(parse_root_block(body), {"--sumi": "#1C1A17"})


class ExtractTests(unittest.TestCase):
    def test_light_includes_fonts_and_role_tokens(self):
        light = extract_light_tokens(LIGHT_DARK_CSS)
        self.assertIn("--serif", light)
        self.assertIn("--mono", light)
        self.assertEqual(light["--washi"], "#F2EDE3")
        self.assertEqual(light["--sumi"], "#1C1A17")

    def test_dark_excludes_fonts(self):
        dark = extract_dark_tokens(LIGHT_DARK_CSS)
        self.assertNotIn("--serif", dark)
        self.assertNotIn("--mono", dark)
        self.assertEqual(dark["--washi"], "#0E0F12")
        self.assertEqual(dark["--nibi-faint"], "#5D6570")

    def test_dark_uses_data_theme_block_not_media_query_only(self):
        # Attribute block is the comparison axis; values match the media seed here.
        dark = extract_dark_tokens(LIGHT_DARK_CSS)
        self.assertEqual(dark["--nibi"], "#9AA1AB")


class CompareTokenMapsTests(unittest.TestCase):
    def test_identical_blocks_report_no_diff(self):
        a = extract_light_tokens(LIGHT_DARK_CSS)
        b = extract_light_tokens(LIGHT_DARK_CSS)
        only_a, only_b, mismatches = compare_token_maps(a, b)
        self.assertEqual(only_a, set())
        self.assertEqual(only_b, set())
        self.assertEqual(mismatches, {})

    def test_missing_token_is_caught(self):
        a = extract_dark_tokens(LIGHT_DARK_CSS)
        b = extract_dark_tokens(MISSING_TOKEN_CSS)
        only_a, only_b, mismatches = compare_token_maps(a, b)
        self.assertIn("--nibi-faint", only_a)
        self.assertEqual(only_b, set())
        # Remaining shared tokens still match (no false value-mismatch).
        self.assertNotIn("--nibi", mismatches)

    def test_value_mismatch_is_caught(self):
        a = extract_dark_tokens(LIGHT_DARK_CSS)
        b = extract_dark_tokens(DRIFTED_DARK_CSS)
        only_a, only_b, mismatches = compare_token_maps(a, b)
        self.assertEqual(only_a, set())
        self.assertEqual(only_b, set())
        self.assertIn("--nibi", mismatches)
        self.assertEqual(mismatches["--nibi"], ("#9AA1AB", "#DEADBE"))


if __name__ == "__main__":
    unittest.main()
