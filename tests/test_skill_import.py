import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import skill_import

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "skills")


class TestParseSkill(unittest.TestCase):
    def test_dir_layout(self):
        s = skill_import.parse_skill(os.path.join(FIX, "clean-check"))
        self.assertEqual(s["frontmatter"]["name"], "repo-hygiene-check")
        self.assertIn("git", s["body"])
        self.assertEqual(len(s["sha256"]), 64)

    def test_bare_file_layout(self):
        s = skill_import.parse_skill(os.path.join(FIX, "clean-check", "SKILL.md"))
        self.assertEqual(s["frontmatter"]["name"], "repo-hygiene-check")

    def test_missing_skill_md_raises(self):
        with self.assertRaises(skill_import.SkillParseError):
            skill_import.parse_skill(tempfile.mkdtemp())

    def test_nested_frontmatter_degrades_with_note(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "SKILL.md"), "w") as f:
            f.write("---\nname: x\nmeta:\n  nested: true\n---\nbody\n")
        s = skill_import.parse_skill(d)
        self.assertEqual(s["frontmatter"], {})
        self.assertTrue(any("frontmatter" in n for n in s["notes"]))

    def test_binary_and_oversize_skipped_with_notes(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "SKILL.md"), "w") as f:
            f.write("---\nname: x\ndescription: y\n---\nbody\n")
        with open(os.path.join(d, "blob.bin"), "wb") as f:
            f.write(b"\x00\x01")
        with open(os.path.join(d, "big.txt"), "w") as f:
            f.write("a" * (257 * 1024))
        s = skill_import.parse_skill(d)
        self.assertEqual([x["relpath"] for x in s["files"]], [])
        self.assertEqual(len(s["notes"]), 2)


if __name__ == "__main__":
    unittest.main()
