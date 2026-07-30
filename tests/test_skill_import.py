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

    # --- fix round 1 -----------------------------------------------------

    def test_blocked_reasons_names_credentials(self):
        r = self._an("needs-creds")
        self.assertTrue(r["blocked"])
        self.assertTrue(r["blocked_reasons"])
        self.assertTrue(any("credentials" in reason for reason in r["blocked_reasons"]))
        # fix-round-2 #3: every blocking reason is prefixed for a downstream
        # printer to discriminate without parsing trailing text.
        self.assertTrue(
            all(reason.startswith("[blocking] ") for reason in r["blocked_reasons"])
        )

    def test_blocked_reasons_empty_when_not_blocked(self):
        r = self._an("clean-check")
        self.assertFalse(r["blocked"])
        self.assertEqual(r["blocked_reasons"], [])

    def test_mcp_blocked_reasons_names_mcp(self):
        r = self._an("mcp-only")
        self.assertTrue(r["blocked"])
        self.assertTrue(any("mcp" in reason for reason in r["blocked_reasons"]))
        self.assertTrue(
            all(reason.startswith("[blocking] ") for reason in r["blocked_reasons"])
        )

    def test_mcp_cli_equivalent_scoped_to_same_file_not_whole_bundle(self):
        # A CLI mention in a DIFFERENT bundled file than the mcp mention must
        # NOT excuse the mcp dependency — this is the exact scenario the
        # reviewer used to flip `blocked` under the over-widened "whole
        # bundle" scope.
        s = skill_import.parse_skill(os.path.join(FIX, "mcp-only"))
        s["files"] = list(s["files"]) + [
            {"relpath": "notes.txt", "text": "Then git log the result."}
        ]
        r = skill_import.analyze(s)
        self.assertTrue(r["blocked"])
        self.assertTrue(
            any("no CLI equivalent" in reason for reason in r["blocked_reasons"])
        )
        self.assertTrue(
            all(reason.startswith("[blocking] ") for reason in r["blocked_reasons"])
        )

    def test_mcp_cli_equivalent_in_same_file_unblocks(self):
        # The CLI mention IS in the same source (SKILL.md/body) as the mcp
        # mention this time — the heuristic should recognize it and unblock.
        # blocked_reasons is non-empty here even though blocked is False:
        # [info] entries only ever appear for mcp-with-equivalent skills.
        s = skill_import.parse_skill(os.path.join(FIX, "mcp-only"))
        s["body"] = s["body"] + "\nThen `git log` the result.\n"
        r = skill_import.analyze(s)
        self.assertFalse(r["blocked"])
        self.assertTrue(any("not blocked" in reason for reason in r["blocked_reasons"]))
        self.assertTrue(
            all(reason.startswith("[info] ") for reason in r["blocked_reasons"])
        )

    def test_q7_axes_value_is_always_a_string(self):
        # Type must be stable whether q7_axes is plain "derived" (clean-check)
        # or overwritten to "incompatible" (mutating, via mutation flag).
        for fixture in ("clean-check", "mutating"):
            r = self._an(fixture)
            self.assertIsInstance(r["rubric"]["q7_axes"]["value"], str)

    def test_axis_raise_context_has_drafted_justification(self):
        r = self._an("mutating")
        raise_items = {
            a["question_id"]: a
            for a in r["answers_needed"]
            if a["question_id"] in skill_import.AXIS_RAISE_IDS
        }
        self.assertIn("raise_perm_remote_mutation", raise_items)
        self.assertIn(
            "Draft justification:",
            raise_items["raise_perm_remote_mutation"]["context"],
        )

    def test_q5_scope_missing_for_bare_h1_and_offered_to_agent(self):
        # None of the fixtures have a real Scope/Exclusions heading — a bare
        # document title must not count as "answered".
        r = self._an("clean-check")
        self.assertEqual(r["rubric"]["q5_scope"]["bucket"], "missing")
        q5_items = [a for a in r["answers_needed"] if a["question_id"] == "q5_scope"]
        self.assertEqual(len(q5_items), 1)
        self.assertEqual(q5_items[0]["suggested_answerer"], "agent")

    def test_q5_scope_answered_by_real_scope_heading(self):
        s = skill_import.parse_skill(os.path.join(FIX, "clean-check"))
        s["body"] = "# Repo hygiene check\n\n## Scope\n\n~/projects only.\n" + s["body"]
        r = skill_import.analyze(s)
        self.assertEqual(r["rubric"]["q5_scope"]["bucket"], "answered")
        self.assertFalse(
            any(a["question_id"] == "q5_scope" for a in r["answers_needed"])
        )

    def test_precheck_filters_non_command_inline_backticks(self):
        s = skill_import.parse_skill(os.path.join(FIX, "clean-check"))
        s["body"] = s["body"] + "\nSee `metrics` and `~/projects/foo` for details.\n"
        r = skill_import.analyze(s)
        self.assertFalse(any("metrics" in l for l in r["precheck_proposal"]))
        self.assertFalse(any("~/projects/foo" in l for l in r["precheck_proposal"]))

    def test_shell_and_sh_fences_scanned(self):
        s = skill_import.parse_skill(os.path.join(FIX, "clean-check"))
        s["body"] = (
            s["body"] + "\n```sh\ngit status\n```\n\n```shell\nrm -rf /tmp/x\n```\n"
        )
        r = skill_import.analyze(s)
        joined = "\n".join(r["precheck_proposal"])
        self.assertIn("git status", joined)
        self.assertIn("rm -rf /tmp/x", joined)


class TestIsReadOnlyCommand(unittest.TestCase):
    """Direct unit-test table for `_is_read_only_command` (fix round 1 #1):
    the reviewer's six escapes that a head-token-only classifier mislabels
    as read-only, plus the legitimate positives that must stay read-only.
    precheck.sh runs UNSANDBOXED, so this annotation is the only safety
    signal a human has — false "read-only?" labels are the highest-stakes
    class of bug here."""

    # --- the six escapes: must be MUTATING (False) ---
    def test_compound_with_mutating_tail_segment(self):
        self.assertFalse(
            skill_import._is_read_only_command("git status && rm -rf build")
        )

    def test_xargs_into_mutating_command(self):
        self.assertFalse(skill_import._is_read_only_command("ls | xargs rm -rf"))

    def test_find_delete_flag(self):
        self.assertFalse(
            skill_import._is_read_only_command("find . -name '*.log' -delete")
        )

    def test_redirect_into_a_file(self):
        self.assertFalse(skill_import._is_read_only_command("cat payload > /etc/hosts"))

    def test_curl_post_verb(self):
        self.assertFalse(
            skill_import._is_read_only_command(
                "curl -X POST -d @p https://example.com/hook"
            )
        )

    def test_tail_follow_never_terminates(self):
        self.assertFalse(skill_import._is_read_only_command("tail -f /var/log/x"))

    # --- legitimate positives: must stay read-only? (True) ---
    def test_git_status(self):
        self.assertTrue(skill_import._is_read_only_command("git status"))

    def test_git_dash_c_log_oneline(self):
        self.assertTrue(skill_import._is_read_only_command("git -C <p> log --oneline"))

    def test_curl_plain_get(self):
        self.assertTrue(
            skill_import._is_read_only_command("curl https://example.com/health")
        )

    def test_grep(self):
        self.assertTrue(skill_import._is_read_only_command("grep -n foo file.txt"))

    def test_wc_l(self):
        self.assertTrue(skill_import._is_read_only_command("wc -l"))

    def test_pipe_of_two_read_only_segments_stays_read_only(self):
        self.assertTrue(
            skill_import._is_read_only_command(
                "git -C <repo> log --oneline @{u}.. 2>/dev/null | wc -l"
            )
        )

    def test_safe_stderr_redirect_not_flagged_as_write(self):
        self.assertTrue(skill_import._is_read_only_command("git status 2>/dev/null"))

    # --- fix round 2: the reviewer's follow-up escape probes ---

    def test_placeholder_glued_to_real_redirect_stdin_and_stdout(self):
        self.assertFalse(skill_import._is_read_only_command("cat <payload>/etc/hosts"))

    def test_placeholder_glued_to_trailing_char(self):
        self.assertFalse(skill_import._is_read_only_command("cat <a>b"))

    def test_placeholder_glued_to_url_redirect(self):
        self.assertFalse(skill_import._is_read_only_command("curl <url>/tmp/pwned"))

    def test_placeholder_glued_to_path_redirect(self):
        self.assertFalse(skill_import._is_read_only_command("git status <x>/tmp/pwned"))

    def test_redirect_still_caught_next_to_whitespace_delimited_placeholder(self):
        self.assertFalse(skill_import._is_read_only_command("cat x ><target>"))
        self.assertFalse(skill_import._is_read_only_command("cat x > <file>"))

    def test_bare_ampersand_backgrounds_a_mutating_command(self):
        self.assertFalse(
            skill_import._is_read_only_command("git status & rm -rf build")
        )

    def test_dollar_paren_command_substitution_demoted(self):
        self.assertFalse(skill_import._is_read_only_command("ls $(rm -rf build)"))

    def test_backtick_command_substitution_demoted(self):
        self.assertFalse(skill_import._is_read_only_command("echo `rm -rf build`"))

    def test_dev_null_lookalike_filename_not_tolerated(self):
        self.assertFalse(skill_import._is_read_only_command("cat a >/dev/nullx"))

    def test_curl_long_form_request_verb(self):
        self.assertFalse(
            skill_import._is_read_only_command(
                "curl --request POST https://example.com/hook"
            )
        )

    def test_tail_capital_f_never_terminates(self):
        self.assertFalse(skill_import._is_read_only_command("tail -F /var/log/x"))

    def test_curl_dash_o_writes_to_disk(self):
        self.assertFalse(
            skill_import._is_read_only_command("curl -o out.txt https://example.com")
        )

    def test_curl_dash_capital_o_writes_to_disk(self):
        self.assertFalse(
            skill_import._is_read_only_command("curl -O https://example.com/file")
        )

    # --- fix round 2: legitimate positives that must NOT regress ---

    def test_git_status_fd_redirect_then_pipe_stays_read_only(self):
        # The reviewer's probe-verified pattern for the new `&` split: this
        # must NOT be mis-split at the `&` inside `2>&1`.
        self.assertTrue(skill_import._is_read_only_command("git status 2>&1 | wc -l"))

    def test_grep_dash_o_stays_read_only(self):
        # grep's -o ("only matching") must not be confused with curl's -o.
        self.assertTrue(skill_import._is_read_only_command("grep -o foo file.txt"))


if __name__ == "__main__":
    unittest.main()
