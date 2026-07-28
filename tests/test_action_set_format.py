"""Two-part actionator id grammar for the ads loops' emit/validate scripts.

Loads each loop's validator via importlib (five prefix-substituted clones —
same behavior, different PREFIX/ALLOWED_SOURCES constants).

Sibling loops (ads-intl/reddit/x/program) do NOT carry the grammar constants
until Task 7 propagates them. Assertions that touch a sibling therefore SKIP
themselves when that sibling's validator lacks a PREFIX constant — "until
propagation (Task 7)". Task 7 needs no edit here: the moment a sibling grows
PREFIX the skip evaporates and the assertion activates. Only ads-google is
required to pass today."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LOOPS = {
    "ads-google": ("ADG", {"EV", "CMP", "JRN", "BUD", "INP"}),
    "ads-intl": ("ADI", {"EV", "CMP", "JRN", "BUD", "INP"}),
    "ads-reddit": ("ADR", {"EV", "CMP", "JRN", "BUD", "INP"}),
    "ads-x": ("ADX", {"EV", "CMP", "JRN", "BUD", "INP"}),
    "ads-program": ("ADP", {"PRG", "BUD", "INP"}),
}


def _load(loop, script="validate_action_set"):
    p = REPO / "loops.d" / loop / "bin" / f"{script}.py"
    spec = importlib.util.spec_from_file_location(f"{script}_{loop}", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _has_grammar(mod):
    # Until propagation (Task 7): siblings lack the two-part grammar constants.
    return hasattr(mod, "PREFIX") and hasattr(mod, "ALLOWED_SOURCES")


def _write_set(tmp, ids, struck=(), context_extra=None):
    aset = Path(tmp) / "action-set"
    (aset / "actions").mkdir(parents=True)
    lines = ["> generated: 2026-07-28T00:00:00Z", "", "# Action register", ""]
    for aid in ids:
        if aid in struck:
            lines += [f"## ~~{aid} — t~~", "- **Struck:** resolved", ""]
        else:
            lines += [f"## {aid} — t", "- **Outcome:** o", ""]
            (aset / "actions" / f"{aid}.md").write_text(
                "> generated: 2026-07-28T00:00:00Z\n\nbody\n"
            )
    (aset / "ACTIONS.md").write_text("\n".join(lines))
    ctx = {
        "loop": "ads-google",
        "run_id": "r",
        "generated": "2026-07-28",
        "engine": "claude",
        "action_ids": list(ids),
    }
    ctx.update(context_extra or {})
    (aset / "context.json").write_text(json.dumps(ctx))
    return aset


class TestGrammar(unittest.TestCase):
    def test_all_five_validators_accept_two_part_and_legacy(self):
        checked = 0
        for loop, (prefix, sources) in LOOPS.items():
            v = _load(loop)
            if not _has_grammar(v):
                continue  # until propagation (Task 7)
            checked += 1
            self.assertEqual(v.PREFIX, prefix)
            self.assertEqual(set(v.ALLOWED_SOURCES), sources)
            src = sorted(sources)[0]
            for good in (f"{prefix}-03", f"{prefix}-{src}-08"):
                self.assertTrue(v.ID_RE.match(good), (loop, good))
            self.assertFalse(v.ID_RE.match(f"{prefix}-ZZ-08"), loop)
        self.assertGreaterEqual(checked, 1, "ads-google grammar must be present")

    def test_program_rejects_network_sources(self):
        v = _load("ads-program")
        if not _has_grammar(v):
            self.skipTest("ads-program grammar not propagated yet (Task 7)")
        self.assertFalse(v.ID_RE.match("ADP-CMP-08"))
        self.assertTrue(v.ID_RE.match("ADP-PRG-08"))

    def test_new_ids_must_carry_source(self):
        v = _load("ads-google")
        with tempfile.TemporaryDirectory() as tmp:
            aset = _write_set(tmp, ["ADG-08"])
            errs = v.validate(
                aset, {"high_water": 7, "prior_ids": [], "prior_open_ids": []}
            )
            self.assertTrue(any("source designator" in e for e in errs), errs)

    def test_carried_legacy_id_passes(self):
        v = _load("ads-google")
        with tempfile.TemporaryDirectory() as tmp:
            aset = _write_set(tmp, ["ADG-03", "ADG-CMP-08"])
            errs = v.validate(
                aset,
                {
                    "high_water": 7,
                    "prior_ids": ["ADG-03"],
                    "prior_open_ids": ["ADG-03"],
                },
            )
            self.assertEqual(errs, [])

    def test_numeric_part_unique_across_sources(self):
        v = _load("ads-google")
        with tempfile.TemporaryDirectory() as tmp:
            aset = _write_set(tmp, ["ADG-CMP-08", "ADG-JRN-08"])
            errs = v.validate(aset)
            self.assertTrue(any("numeric part" in e for e in errs), errs)

    def test_reuse_below_high_water_still_caught(self):
        v = _load("ads-google")
        with tempfile.TemporaryDirectory() as tmp:
            aset = _write_set(tmp, ["ADG-CMP-05"])
            errs = v.validate(
                aset, {"high_water": 7, "prior_ids": [], "prior_open_ids": []}
            )
            self.assertTrue(any("REUSED" in e for e in errs), errs)


class TestEmit(unittest.TestCase):
    def test_emit_writes_source_line_and_map(self):
        e = _load("ads-google", "emit_action_set")
        payload = {
            "loop": "ads-google",
            "run_id": "r1",
            "engine": "claude",
            "actions": [
                {
                    "id": "ADG-CMP-08",
                    "title": "t",
                    "status": "open",
                    "outcome": "o",
                    "exception": "x",
                    "resolution_evidence": "r",
                },
                {
                    "id": "ADG-03",
                    "title": "legacy",
                    "status": "open",
                    "outcome": "o",
                    "exception": "x",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            import io, os

            os.environ["GENERATED_TS"] = "2026-07-28T00:00:00Z"
            old_stdin = sys.stdin
            sys.stdin = io.StringIO(json.dumps(payload))
            try:
                rc = e.main(["emit", "--out", tmp])
            finally:
                sys.stdin = old_stdin
                del os.environ["GENERATED_TS"]
            self.assertEqual(rc, 0)
            brief = (Path(tmp) / "action-set" / "actions" / "ADG-CMP-08.md").read_text()
            self.assertIn("- **Source:** CMP — campaign/delivery evaluation", brief)
            legacy = (Path(tmp) / "action-set" / "actions" / "ADG-03.md").read_text()
            self.assertNotIn("- **Source:**", legacy)
            ctx = json.loads((Path(tmp) / "action-set" / "context.json").read_text())
            self.assertEqual(
                ctx["action_sources"], {"ADG-CMP-08": "CMP", "ADG-03": None}
            )


if __name__ == "__main__":
    unittest.main()
