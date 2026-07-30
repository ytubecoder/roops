import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bin"))
import loopconf
import skill_import

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "skills")


def _read(path):
    with open(path) as f:
        return f.read()


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


class TestApply(unittest.TestCase):
    """Direct-call coverage of `apply()` (Task 12) — the CLI-level tests in
    tests/test_loopctl.py cover the end-to-end `loopctl import --apply`
    contract; these exercise `apply()`'s own precedence/refusal rules in
    isolation, in particular controller ruling 1 (an explicit `answers`
    entry wins over the rubric's value, including for already-`answered`/
    `derived` buckets) which the loopctl-level CLEAN_ANSWERS fixture never
    happens to exercise since it only answers ids the rubric left missing."""

    def _skill_and_analysis(self, fixture="clean-check"):
        skill = skill_import.parse_skill(os.path.join(FIX, fixture))
        analysis = skill_import.analyze(skill)
        return skill, analysis

    def _answers(self, analysis, **overrides):
        base = {
            "analyzer_version": analysis["analyzer_version"],
            "skill_sha256": analysis["skill_sha256"],
            "answers": {},
            "provenance": {},
            "acknowledge_blocked": False,
        }
        base.update(overrides)
        return base

    def _tmpdir(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def test_explicit_answer_wins_over_already_derived_rubric_value(self):
        # q2_pattern is "derived" for clean-check (never in answers_needed) —
        # ruling 1 says an explicit answers["answers"] entry for it still
        # wins over the rubric's own derived value.
        skill, analysis = self._skill_and_analysis()
        self.assertEqual(analysis["rubric"]["q2_pattern"]["bucket"], "derived")
        answers = self._answers(
            analysis, answers={"q2_pattern": "custom pattern override"}
        )
        dest = self._tmpdir()
        skill_import.apply(
            skill, analysis, answers, os.path.join(dest, "repo-hygiene-check")
        )
        spec = _read(os.path.join(dest, "repo-hygiene-check", "SPEC.md"))
        self.assertIn("custom pattern override", spec)
        self.assertNotIn(
            "v1 loops are Human-in-the-loop by design", spec
        )  # the rubric's derived value was overridden, not appended

    def test_explicit_answer_wins_over_frontmatter_answered_value(self):
        # q1_purpose is "answered" from frontmatter description — ruling 1
        # says an explicit answers entry still wins over that too.
        skill, analysis = self._skill_and_analysis()
        self.assertEqual(analysis["rubric"]["q1_purpose"]["bucket"], "answered")
        answers = self._answers(
            analysis, answers={"q1_purpose": "a totally different purpose"}
        )
        dest = self._tmpdir()
        skill_import.apply(
            skill, analysis, answers, os.path.join(dest, "repo-hygiene-check")
        )
        spec = _read(os.path.join(dest, "repo-hygiene-check", "SPEC.md"))
        self.assertIn("a totally different purpose", spec)

    def test_unanswered_id_with_no_rubric_value_stays_fill(self):
        # No derived-default fallback exists (ruling 2): an id that's
        # "missing" in the rubric AND absent from answers must stay [FILL:.
        skill, analysis = self._skill_and_analysis()
        answers = self._answers(analysis)  # no answers at all
        dest = self._tmpdir()
        skill_import.apply(
            skill, analysis, answers, os.path.join(dest, "repo-hygiene-check")
        )
        spec = _read(os.path.join(dest, "repo-hygiene-check", "SPEC.md"))
        self.assertIn("[FILL:", spec)

    def test_apply_returns_the_written_paths(self):
        skill, analysis = self._skill_and_analysis()
        answers = self._answers(analysis)
        dest = os.path.join(self._tmpdir(), "repo-hygiene-check")
        written = skill_import.apply(skill, analysis, answers, dest)
        self.assertEqual(
            sorted(os.path.basename(p) for p in written),
            ["SPEC.md", "dashboard.json", "loop.conf", "precheck.sh", "prompt.md"],
        )
        for p in written:
            self.assertTrue(os.path.isfile(p))

    def test_apply_precheck_is_executable(self):
        skill, analysis = self._skill_and_analysis()
        answers = self._answers(analysis)
        loop_dir = os.path.join(self._tmpdir(), "repo-hygiene-check")
        skill_import.apply(skill, analysis, answers, loop_dir)
        self.assertTrue(os.access(os.path.join(loop_dir, "precheck.sh"), os.X_OK))

    def test_apply_raises_on_stale_sha256(self):
        skill, analysis = self._skill_and_analysis()
        answers = self._answers(analysis, skill_sha256="0" * 64)
        dest = self._tmpdir()
        with self.assertRaises(skill_import.SkillApplyError):
            skill_import.apply(skill, analysis, answers, os.path.join(dest, "x"))

    def test_apply_raises_on_analyzer_version_mismatch(self):
        skill, analysis = self._skill_and_analysis()
        answers = self._answers(analysis, analyzer_version="999")
        dest = self._tmpdir()
        with self.assertRaises(skill_import.SkillApplyError):
            skill_import.apply(skill, analysis, answers, os.path.join(dest, "x"))

    def test_apply_raises_when_blocked_without_acknowledgement(self):
        skill, analysis = self._skill_and_analysis("needs-creds")
        self.assertTrue(analysis["blocked"])
        answers = self._answers(analysis)  # acknowledge_blocked defaults False
        dest = self._tmpdir()
        with self.assertRaises(skill_import.SkillApplyError):
            skill_import.apply(skill, analysis, answers, os.path.join(dest, "x"))

    def test_acknowledge_blocked_string_false_does_not_acknowledge(self):
        # Round-1 review minor: acknowledge_blocked must be checked `is
        # True`, not `bool(...)` — the JSON string "false" is truthy in
        # Python and must NOT be treated as acknowledgement.
        skill, analysis = self._skill_and_analysis("needs-creds")
        answers = self._answers(analysis, acknowledge_blocked="false")
        dest = self._tmpdir()
        with self.assertRaises(skill_import.SkillApplyError):
            skill_import.apply(skill, analysis, answers, os.path.join(dest, "x"))

    def test_apply_blocked_with_acknowledgement_forces_manual_schedule(self):
        skill, analysis = self._skill_and_analysis("needs-creds")
        answers = self._answers(
            analysis,
            answers={"q4_cadence": "daily:07:30"},  # would NOT be manual otherwise
            acknowledge_blocked=True,
        )
        dest = self._tmpdir()
        loop_dir = os.path.join(dest, "stripe-failed-charges")
        skill_import.apply(skill, analysis, answers, loop_dir)
        conf = _read(os.path.join(loop_dir, "loop.conf"))
        self.assertIn("schedule=manual", conf)
        spec = _read(os.path.join(loop_dir, "SPEC.md"))
        self.assertIn("## BLOCKED — read before scheduling", spec)

    # --- round-1 review defect #1: prompt.md truncation --------------------

    def test_prompt_body_with_finding_identity_heading_does_not_truncate_contract(
        self,
    ):
        # Reviewer-reported defect: _render_prompt_md spliced the skill body
        # in BEFORE locating "## Finding identity" in the text, so a skill
        # body that itself contains that heading made text.index() find the
        # BODY's copy — truncating everything after it, including the
        # Output contract and Findings prompt contract sections.
        # `loopctl validate` did not catch this (it only greps for the
        # heading's presence, never what follows it).
        skill, analysis = self._skill_and_analysis()
        skill["body"] = (
            skill["body"] + "\n\n## Finding identity\n\n"
            "The skill's OWN (irrelevant) heading, not the loop's contract "
            "section.\n"
        )
        answers = self._answers(analysis, answers={"q8_finding_identity": "x:y"})
        dest = self._tmpdir()
        loop_dir = os.path.join(dest, "repo-hygiene-check")
        skill_import.apply(skill, analysis, answers, loop_dir)
        prompt = _read(os.path.join(loop_dir, "prompt.md"))
        self.assertIn("## Output contract", prompt)
        self.assertIn("`metrics` MUST be a JSON **string**", prompt)
        self.assertIn("## Findings prompt contract", prompt)
        self.assertIn("Re-emit a still-true finding", prompt)
        self.assertIn("Do not re-argue a `DISMISSED` finding", prompt)
        self.assertIn("Still emit `SNOOZED` findings", prompt)
        # Both headings survive (the skill's own in-body one + the
        # template's real one) and the loop's OWN Finding identity content
        # (from q8, not the skill's in-body text) is what actually fills it.
        self.assertEqual(prompt.count("## Finding identity"), 2)
        self.assertIn("x:y", prompt)

    # --- round-1 review defect #2: free-text budget scraping ---------------

    def test_structured_budget_keys_land_in_loop_conf(self):
        skill, analysis = self._skill_and_analysis()
        answers = self._answers(
            analysis,
            answers={
                "q11_budget": "engine default model; ~1k tokens; retry 1; timeout 300"
            },
            model="claude-sonnet-5",
            timeout_s=600,
            retry_transient=2,
        )
        loop_dir = os.path.join(self._tmpdir(), "repo-hygiene-check")
        skill_import.apply(skill, analysis, answers, loop_dir)
        conf = _read(os.path.join(loop_dir, "loop.conf"))
        self.assertIn("model=claude-sonnet-5", conf)
        self.assertIn("timeout_s=600", conf)
        self.assertIn("retry_transient=2", conf)

    def test_free_text_budget_prose_never_becomes_config(self):
        # The reviewer's own reproducer strings: regex-scraping q11_budget's
        # free text invented config values from unrelated prose. q11_budget
        # is SPEC.md §11 prose ONLY now — it must never set loop.conf keys.
        cases = [
            ("codex, no model override needed; timeout 900", ["model=override"]),
            (
                "use the default model unless cost spikes; timeout 30 minutes",
                ["model=unless", "timeout_s=30"],
            ),
            ("Model is whatever the engine defaults to.", ["model=is"]),
        ]
        for prose, forbidden in cases:
            skill, analysis = self._skill_and_analysis()
            answers = self._answers(analysis, answers={"q11_budget": prose})
            loop_dir = os.path.join(self._tmpdir(), "repo-hygiene-check")
            skill_import.apply(skill, analysis, answers, loop_dir)
            conf = _read(os.path.join(loop_dir, "loop.conf"))
            for bad in forbidden:
                self.assertNotIn(bad, conf, msg=f"prose={prose!r}")
            # No structured key was given at all — neither line should exist.
            self.assertNotIn("model=", conf, msg=f"prose={prose!r}")
            self.assertNotIn("timeout_s=", conf, msg=f"prose={prose!r}")

    def test_invalid_timeout_s_refused(self):
        skill, analysis = self._skill_and_analysis()
        for bad in (10, 99999, "300", 30.5, True):
            answers = self._answers(analysis, timeout_s=bad)
            dest = self._tmpdir()
            with self.assertRaises(skill_import.SkillApplyError, msg=f"bad={bad!r}"):
                skill_import.apply(skill, analysis, answers, os.path.join(dest, "x"))

    def test_invalid_retry_transient_refused(self):
        skill, analysis = self._skill_and_analysis()
        for bad in (-1, 4, "1", 1.5):
            answers = self._answers(analysis, retry_transient=bad)
            dest = self._tmpdir()
            with self.assertRaises(skill_import.SkillApplyError, msg=f"bad={bad!r}"):
                skill_import.apply(skill, analysis, answers, os.path.join(dest, "x"))

    def test_invalid_model_refused(self):
        skill, analysis = self._skill_and_analysis()
        for bad in ("", "   ", 123):
            answers = self._answers(analysis, model=bad)
            dest = self._tmpdir()
            with self.assertRaises(skill_import.SkillApplyError, msg=f"bad={bad!r}"):
                skill_import.apply(skill, analysis, answers, os.path.join(dest, "x"))

    def test_valid_timeout_s_boundaries_accepted(self):
        skill, analysis = self._skill_and_analysis()
        for good in (30, 7200):
            answers = self._answers(analysis, timeout_s=good)
            loop_dir = os.path.join(self._tmpdir(), "repo-hygiene-check")
            skill_import.apply(skill, analysis, answers, loop_dir)
            conf = _read(os.path.join(loop_dir, "loop.conf"))
            self.assertIn(f"timeout_s={good}", conf)

    # --- round-1 review minors ----------------------------------------------

    def test_malformed_answers_field_shape_refused_before_any_write(self):
        # {"answers": ["q1_purpose"]} (a list, not an object) must refuse
        # loudly and leave NOTHING behind — a half-written loops.d/<name>/
        # would make the next, correct attempt fail with a spurious
        # "already exists".
        skill, analysis = self._skill_and_analysis()
        answers = self._answers(analysis, answers=["q1_purpose"])
        dest = self._tmpdir()
        loop_dir = os.path.join(dest, "repo-hygiene-check")
        with self.assertRaises(skill_import.SkillApplyError):
            skill_import.apply(skill, analysis, answers, loop_dir)
        self.assertFalse(os.path.exists(loop_dir))

    def test_malformed_provenance_shape_refused(self):
        skill, analysis = self._skill_and_analysis()
        answers = self._answers(analysis, provenance=["user"])
        dest = self._tmpdir()
        with self.assertRaises(skill_import.SkillApplyError):
            skill_import.apply(skill, analysis, answers, os.path.join(dest, "x"))

    def test_whitespace_only_answer_falls_back_to_fill(self):
        # A whitespace-only answer must not count as "provided" — it would
        # otherwise blank out a SPEC section with no [FILL: marker left to
        # trip loopctl validate's safety net. Isolate section 8 specifically
        # (other sections stay unanswered too, so a bare "[FILL: anywhere in
        # the doc" check would pass even with the bug — the bug blanks out
        # exactly the ONE section whose answer was whitespace-only).
        skill, analysis = self._skill_and_analysis()
        answers = self._answers(analysis, answers={"q8_finding_identity": "   "})
        loop_dir = os.path.join(self._tmpdir(), "repo-hygiene-check")
        skill_import.apply(skill, analysis, answers, loop_dir)
        spec = _read(os.path.join(loop_dir, "SPEC.md"))
        section_8 = spec.split("8. Finding identity", 1)[1].split(
            "9. Tier-1 semantics", 1
        )[0]
        self.assertIn("[FILL:", section_8)

    def test_answer_ending_in_backslash_refused(self):
        # loop.conf's KEY=value grammar (bin/loopconf.py's _parse_value)
        # recognizes ONLY the two-character sequence backslash+quote as an
        # escape — there is no separate backslash-escaping rule. A run of
        # one-or-more literal backslashes immediately before the closing
        # quote is THEREFORE ALWAYS misread as escaping that closing quote
        # itself, no matter how the backslash is encoded (verified
        # empirically against loopconf._parse_value for N=1..5 trailing
        # backslashes, with and without doubling) — there is no valid
        # encoding. Refuse loudly rather than emit a scaffold
        # `loopconf.parse()` will itself (correctly, but unhelpfully)
        # reject as "unterminated quoted value".
        skill, analysis = self._skill_and_analysis()
        answers = self._answers(
            analysis, answers={"q1_purpose": "ends with a backslash\\"}
        )
        dest = self._tmpdir()
        with self.assertRaises(skill_import.SkillApplyError):
            skill_import.apply(skill, analysis, answers, os.path.join(dest, "x"))

    def test_answer_with_interior_backslash_quote_still_roundtrips(self):
        # The interior case (a literal backslash immediately before a
        # literal quote WITHIN the content, not at the very end) IS
        # representable under loopconf's grammar and must keep round-
        # tripping correctly.
        skill, analysis = self._skill_and_analysis()
        value = 'back\\"slash-quote-adjacent'
        answers = self._answers(analysis, answers={"q1_purpose": value})
        loop_dir = os.path.join(self._tmpdir(), "repo-hygiene-check")
        skill_import.apply(skill, analysis, answers, loop_dir)
        conf, errors = loopconf.parse(os.path.join(loop_dir, "loop.conf"))
        self.assertEqual(errors, [])
        self.assertEqual(conf["description"], value)

    def test_spec_template_section_count_drift_raises_not_asserts(self):
        # `assert` vanishes under `python -O` — the section-count invariant
        # must raise a real exception instead.
        original_template = skill_import._SPEC_MD_TEMPLATE
        skill_import._SPEC_MD_TEMPLATE = (
            "# __NAME__ — x\n\n1. only one section\n[FILL: x]\n"
        )
        self.addCleanup(setattr, skill_import, "_SPEC_MD_TEMPLATE", original_template)
        with self.assertRaises(RuntimeError):
            skill_import._render_spec_md("x", {}, {})

    # --- round-2 review: model / schedule shape, non-dict answers ----------

    def test_model_with_internal_whitespace_refused(self):
        # loop.conf writes model= BARE (unquoted) — a value containing a
        # space truncates at the space (loopconf's "bare value must not
        # contain spaces" error) or, with an embedded newline, injects a
        # bogus extra KEY=value line. Refuse loudly instead.
        skill, analysis = self._skill_and_analysis()
        for bad in ("gpt 4 turbo", "a\nb", "  ", ""):
            answers = self._answers(analysis, model=bad)
            dest = self._tmpdir()
            loop_dir = os.path.join(dest, "x")
            with self.assertRaises(skill_import.SkillApplyError, msg=f"bad={bad!r}"):
                skill_import.apply(skill, analysis, answers, loop_dir)
            self.assertFalse(os.path.exists(loop_dir), msg=f"bad={bad!r}")

    def test_model_single_token_accepted(self):
        skill, analysis = self._skill_and_analysis()
        answers = self._answers(analysis, model="gpt-4-turbo")
        loop_dir = os.path.join(self._tmpdir(), "repo-hygiene-check")
        skill_import.apply(skill, analysis, answers, loop_dir)
        conf = _read(os.path.join(loop_dir, "loop.conf"))
        self.assertIn("model=gpt-4-turbo", conf)

    def test_free_text_cadence_not_matching_schedule_grammar_refused(self):
        # Identical shape to the model bug: free-text cadence like "daily at
        # 07:30" would otherwise pass straight through to loop.conf's
        # schedule= and only fail later, opaquely, inside loopconf.parse().
        skill, analysis = self._skill_and_analysis()
        for bad in ("daily at 07:30", "every day at 7:30am", "weekdays"):
            answers = self._answers(analysis, answers={"q4_cadence": bad})
            dest = self._tmpdir()
            loop_dir = os.path.join(dest, "x")
            with self.assertRaises(skill_import.SkillApplyError, msg=f"bad={bad!r}"):
                skill_import.apply(skill, analysis, answers, loop_dir)
            self.assertFalse(os.path.exists(loop_dir), msg=f"bad={bad!r}")

    def test_valid_cadence_grammar_accepted(self):
        skill, analysis = self._skill_and_analysis()
        answers = self._answers(analysis, answers={"q4_cadence": "weekly:mon:08:00"})
        loop_dir = os.path.join(self._tmpdir(), "repo-hygiene-check")
        skill_import.apply(skill, analysis, answers, loop_dir)
        conf = _read(os.path.join(loop_dir, "loop.conf"))
        self.assertIn("schedule=weekly:mon:08:00", conf)

    def test_non_dict_top_level_answers_refused(self):
        skill, analysis = self._skill_and_analysis()
        for bad in (["x"], "hello", 42, []):
            dest = self._tmpdir()
            loop_dir = os.path.join(dest, "x")
            with self.assertRaises(skill_import.SkillApplyError, msg=f"bad={bad!r}"):
                skill_import.apply(skill, analysis, bad, loop_dir)
            self.assertFalse(os.path.exists(loop_dir), msg=f"bad={bad!r}")

    def test_none_top_level_answers_still_falls_back_to_empty_dict(self):
        # None is the documented "no answers given" convenience default —
        # NOT a malformed shape. It must still refuse (blocked-without-
        # acknowledgement, for a fixture that's actually blocked) rather
        # than crash, proving `None` reaches the ordinary refusal path
        # instead of the new type-guard.
        skill, analysis = self._skill_and_analysis("needs-creds")
        with self.assertRaises(skill_import.SkillApplyError):
            skill_import.apply(skill, analysis, None, os.path.join(self._tmpdir(), "x"))

    def test_backslash_refusal_names_description_not_a_rubric_id(self):
        # Round-2 review: the field named in the refusal message must be
        # accurate regardless of whether the value came from an explicit
        # q1_purpose answer or the skill's own frontmatter description
        # (rubric "answered" bucket) — it must never claim "q1_purpose" when
        # the value in question is actually the frontmatter-derived one.
        skill, analysis = self._skill_and_analysis()
        # clean-check's frontmatter description doesn't end in a backslash,
        # so mutate the rubric directly to simulate a frontmatter-derived
        # value that does, WITHOUT going through an explicit q1_purpose
        # answer — proving the message names the field, not the source.
        rubric = json.loads(json.dumps(analysis["rubric"]))
        rubric["q1_purpose"] = {"bucket": "answered", "value": "ends with backslash\\"}
        analysis = dict(analysis)
        analysis["rubric"] = rubric
        answers = self._answers(analysis)
        dest = self._tmpdir()
        try:
            skill_import.apply(skill, analysis, answers, os.path.join(dest, "x"))
            self.fail("expected SkillApplyError")
        except skill_import.SkillApplyError as e:
            self.assertIn("description", str(e))
            self.assertNotIn("q1_purpose", str(e))


if __name__ == "__main__":
    unittest.main()
