import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_grouping_normalization_plan as build_plan_mod  # noqa: E402
from fixtures import make_library  # noqa: E402


class GroupingNormalizationPlanTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.library = self.tmp / "Music"
        self.library.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def write_audit_report(self, grouping_values_in_use: dict) -> Path:
        report_path = self.tmp / "tag-audit.json"
        report_path.write_text(json.dumps({"grouping_values_in_use": grouping_values_in_use}))
        return report_path

    def build(self, canonical="Old Head", pattern=None, grouping_values_in_use=None):
        report_path = self.write_audit_report(grouping_values_in_use or {})
        vocab = build_plan_mod.build_vocabulary(report_path)
        pat = (
            re_compile(pattern) if pattern else build_plan_mod.default_pattern(canonical)
        )
        return build_plan_mod.build_plan(self.library, canonical, pat, vocab)

    def test_case_only_variant_is_auto_fixed(self):
        make_library(self.library, [{"relative": "a.mp3", "grouping": "old head"}])
        plan = self.build(grouping_values_in_use={"Old Head": 5})
        self.assertEqual(plan["counts"]["auto_fix"], 1)
        self.assertEqual(plan["items"][0]["combined_grouping"], "Old Head")
        self.assertEqual(plan["items"][0]["current_grouping"], ["old head"])

    def test_exact_canonical_value_is_not_flagged_as_a_fix(self):
        make_library(self.library, [{"relative": "a.mp3", "grouping": "Old Head"}])
        plan = self.build(grouping_values_in_use={"Old Head": 5})
        self.assertEqual(plan["counts"]["auto_fix"], 0)
        self.assertEqual(plan["counts"]["needs_review"], 0)

    def test_comma_joined_known_tokens_are_auto_fixed_and_reordered_as_semicolon_join(self):
        make_library(self.library, [{"relative": "a.mp3", "grouping": "Plex, Old Head"}])
        plan = self.build(grouping_values_in_use={"Plex": 10, "Old Head": 5})
        self.assertEqual(plan["counts"]["auto_fix"], 1)
        self.assertEqual(plan["items"][0]["combined_grouping"], "Plex; Old Head")

    def test_space_joined_known_tokens_resolve_via_greedy_longest_match(self):
        make_library(self.library, [{"relative": "a.mp3", "grouping": "White Oldies old head"}])
        plan = self.build(grouping_values_in_use={"White Oldies": 20, "Old Head": 5})
        self.assertEqual(plan["counts"]["auto_fix"], 1)
        self.assertEqual(plan["items"][0]["combined_grouping"], "White Oldies; Old Head")

    def test_unknown_trailing_token_is_not_guessed_and_goes_to_review(self):
        make_library(self.library, [{"relative": "a.mp3", "grouping": "old head party"}])
        plan = self.build(grouping_values_in_use={"Old Head": 5})
        self.assertEqual(plan["counts"]["auto_fix"], 0)
        self.assertEqual(plan["counts"]["needs_review"], 1)
        self.assertEqual(plan["needs_review"][0]["current_grouping"], "old head party")

    def test_non_mp3_format_with_a_real_match_is_reported_as_unsupported_not_dropped(self):
        # Fabricating a real synthetic FLAC/M4A container is expensive and
        # beside the point here -- this proves build_plan's own branching
        # (id3_version is None -> unsupported_format, never silently
        # skipped), not the mutagen read path for those formats.
        made = make_library(self.library, [{"relative": "a.mp3", "grouping": "old head"}])
        flac_path = self.library / "b.flac"
        flac_path.write_bytes(b"not a real flac, just needs to exist")
        original = build_plan_mod.read_grouping_raw

        def fake_read(path):
            if path == flac_path:
                return (["old head"], None)
            return original(path)

        with patch.object(build_plan_mod, "read_grouping_raw", side_effect=fake_read):
            plan = self.build(grouping_values_in_use={"Old Head": 5})

        self.assertEqual(plan["counts"]["unsupported_format"], 1)
        self.assertEqual(plan["unsupported_format"][0]["path"], str(flac_path))
        self.assertEqual(plan["unsupported_format"][0]["current_grouping"], "old head")
        self.assertEqual(plan["counts"]["auto_fix"], 1)  # a.mp3 still resolved normally
        self.assertEqual(made["a.mp3"].suffix, ".mp3")

    def test_typo_with_custom_pattern_is_flagged_for_review_not_auto_corrected(self):
        make_library(self.library, [{"relative": "a.mp3", "grouping": "old heda"}])
        plan = self.build(pattern=r"old\s*h[ea]+[ad]", grouping_values_in_use={"Old Head": 5})
        self.assertEqual(plan["counts"]["auto_fix"], 0)
        self.assertEqual(plan["counts"]["needs_review"], 1)

    def test_semicolon_joined_compound_value_only_fixes_the_matching_segment(self):
        # This is the real production shape: Old Head already properly
        # joined with an unrelated Event/Clean-Dirty segment via the shared
        # "; " convention. Those other segments are arbitrary free text --
        # they must survive untouched, not get run through vocab matching.
        make_library(self.library, [
            {"relative": "a.mp3", "grouping": "old head; Event: LIKE SUGAR ON MY TONGUE; Clean"},
        ])
        plan = self.build(grouping_values_in_use={"Old Head": 5})
        self.assertEqual(plan["counts"]["auto_fix"], 1)
        self.assertEqual(
            plan["items"][0]["combined_grouping"],
            "Old Head; Event: LIKE SUGAR ON MY TONGUE; Clean",
        )

    def test_semicolon_joined_value_with_one_unresolvable_segment_goes_to_review_whole(self):
        make_library(self.library, [{"relative": "a.mp3", "grouping": "old head party; Clean"}])
        plan = self.build(grouping_values_in_use={"Old Head": 5})
        self.assertEqual(plan["counts"]["auto_fix"], 0)
        self.assertEqual(plan["counts"]["needs_review"], 1)
        self.assertEqual(plan["needs_review"][0]["current_grouping"], "old head party; Clean")

    def test_plan_shape_matches_apply_grouping_plan_expectations(self):
        make_library(self.library, [{"relative": "a.mp3", "grouping": "old head"}])
        plan = self.build(grouping_values_in_use={"Old Head": 5})
        self.assertEqual(plan["mode"], "preview-only")
        self.assertTrue(plan["rules"]["approval_required"])
        item = plan["items"][0]
        self.assertEqual(set(item), {"path", "current_grouping", "combined_grouping", "id3_version"})

    def test_no_library_file_is_ever_modified(self):
        made = make_library(self.library, [{"relative": "a.mp3", "grouping": "old head"}])
        before = made["a.mp3"].read_bytes()
        self.build(grouping_values_in_use={"Old Head": 5})
        after = made["a.mp3"].read_bytes()
        self.assertEqual(before, after)


def re_compile(pattern: str):
    import re
    return re.compile(pattern, re.IGNORECASE)


if __name__ == "__main__":
    unittest.main()
