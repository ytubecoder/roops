"""Cross-file token parity: garden (dashboard/generate.py) vs pagekit/kit.css (WP3).

One method per axis so a failure names which set drifted.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from token_parser import (
    compare_token_maps,
    extract_css_string_from_generate_py,
    extract_dark_tokens,
    extract_light_tokens,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GENERATE_PY = os.path.join(_ROOT, "dashboard", "generate.py")
_KIT_CSS = os.path.join(_ROOT, "pagekit", "kit.css")


def _load_garden_css() -> str:
    with open(_GENERATE_PY, encoding="utf-8") as f:
        return extract_css_string_from_generate_py(f.read())


def _load_kit_css() -> str:
    with open(_KIT_CSS, encoding="utf-8") as f:
        return f.read()


class TestTokenDrift(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.garden = _load_garden_css()
        cls.kit = _load_kit_css()
        cls.garden_light = extract_light_tokens(cls.garden)
        cls.kit_light = extract_light_tokens(cls.kit)
        cls.garden_dark = extract_dark_tokens(cls.garden)
        cls.kit_dark = extract_dark_tokens(cls.kit)

    def test_light_token_name_sets_match(self):
        only_g, only_k, _ = compare_token_maps(self.garden_light, self.kit_light)
        self.assertEqual(
            only_g,
            set(),
            f"light tokens only in garden (missing from kit.css): {sorted(only_g)}",
        )
        self.assertEqual(
            only_k,
            set(),
            f"light tokens only in kit.css (not in garden): {sorted(only_k)}",
        )
        self.assertTrue(
            self.garden_light,
            "parser returned empty light set for garden — vacuous",
        )

    def test_dark_token_name_sets_match(self):
        only_g, only_k, _ = compare_token_maps(self.garden_dark, self.kit_dark)
        self.assertEqual(
            only_g,
            set(),
            f"dark tokens only in garden (missing from kit.css): {sorted(only_g)}",
        )
        self.assertEqual(
            only_k,
            set(),
            f"dark tokens only in kit.css (not in garden): {sorted(only_k)}",
        )
        self.assertTrue(
            self.garden_dark,
            "parser returned empty dark set for garden — vacuous",
        )
        # Font tokens must not appear in dark comparison sets.
        self.assertNotIn("--serif", self.garden_dark)
        self.assertNotIn("--mono", self.kit_dark)

    def test_light_token_values_match(self):
        _, _, mismatches = compare_token_maps(self.garden_light, self.kit_light)
        self.assertEqual(
            mismatches,
            {},
            "light value drift:\n"
            + "\n".join(
                f"  {k}: garden={gv!r} kit={kv!r}"
                for k, (gv, kv) in sorted(mismatches.items())
            ),
        )

    def test_dark_token_values_match(self):
        _, _, mismatches = compare_token_maps(self.garden_dark, self.kit_dark)
        self.assertEqual(
            mismatches,
            {},
            "dark value drift:\n"
            + "\n".join(
                f"  {k}: garden={gv!r} kit={kv!r}"
                for k, (gv, kv) in sorted(mismatches.items())
            ),
        )


if __name__ == "__main__":
    unittest.main()
