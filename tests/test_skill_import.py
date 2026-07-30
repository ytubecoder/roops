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


class TestAnalyze(unittest.TestCase):
    def _an(self, fixture):
        return skill_import.analyze(
            skill_import.parse_skill(os.path.join(FIX, fixture))
        )

    def test_clean_check_shape(self):
        r = self._an("clean-check")
        self.assertEqual(r["axes"]["perm_network"], "none")  # floor-first
        self.assertFalse(r["blocked"])
        self.assertEqual(r["rubric"]["q8_finding_identity"]["bucket"], "missing")
        self.assertTrue(all(l.startswith("#") for l in r["precheck_proposal"]))
        self.assertTrue(any("[read-only?] " in l for l in r["precheck_proposal"]))
        needed = {a["question_id"] for a in r["answers_needed"]}
        self.assertLessEqual({"q4_cadence", "q8_finding_identity"}, needed)

    def test_interactive_flagged(self):
        self.assertTrue(self._an("interactive")["flags"]["interactivity"])

    def test_mutating_flagged_and_annotated(self):
        r = self._an("mutating")
        self.assertTrue(r["flags"]["mutation"])
        self.assertTrue(any("MUTATING" in l for l in r["precheck_proposal"]))
        self.assertEqual(r["axes"]["perm_remote_mutation"], "none")  # floor stays

    def test_credentials_blocked(self):
        self.assertTrue(self._an("needs-creds")["blocked"])

    def test_mcp_only_blocked_and_claude_idiom(self):
        r = self._an("mcp-only")
        self.assertTrue(r["blocked"])
        self.assertEqual(r["engine"], "claude")

    def test_name_sanitized(self):
        s = skill_import.parse_skill(os.path.join(FIX, "clean-check"))
        s["frontmatter"]["name"] = (
            "My_Big Skill!! With A Really Long Name Overflowing Everything"
        )
        n = skill_import.analyze(s)["proposed_name"]
        self.assertRegex(n, r"^[a-z][a-z0-9-]{1,40}$")


if __name__ == "__main__":
    unittest.main()
