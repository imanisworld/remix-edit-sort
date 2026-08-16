import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

sys.path.insert(0, str(Path(__file__).resolve().parent))

import audit_maintenance as am  # noqa: E402
from fixtures import make_library  # noqa: E402


class TagAuditTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.library = self.tmp / "Music"
        self.library.mkdir()
        self.out_dir = self.tmp / "reports"

    def tearDown(self):
        self._tmp.cleanup()

    def run_audit(self, baseline=None, full=False, update_baseline=False):
        out_path = self.out_dir / "tag-audit.json"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        return am.run_tags_audit(out_path, self.library, baseline, full, update_baseline)

    def test_exact_canonical_genre_raises_no_finding(self):
        make_library(self.library, [{"relative": "a.mp3", "genre": "House", "bpm": "128", "key": "8A"}])
        report = self.run_audit()
        self.assertIn("House", report["canonical_genres_matched_exactly"])
        self.assertEqual(report["genre_case_or_whitespace_variants"], [])
        self.assertEqual(report["genre_non_canonical"], [])

    def test_case_variant_of_canonical_genre_is_flagged_separately_from_unknown(self):
        make_library(self.library, [{"relative": "a.mp3", "genre": "house", "bpm": "128", "key": "8A"}])
        report = self.run_audit()
        self.assertEqual(len(report["genre_case_or_whitespace_variants"]), 1)
        variant = report["genre_case_or_whitespace_variants"][0]
        self.assertEqual(variant["canonical"], "House")
        self.assertEqual(variant["value_seen"], ["house"])
        self.assertEqual(report["genre_non_canonical"], [])

    def test_unknown_genre_not_in_canonical_list_is_flagged(self):
        make_library(self.library, [{"relative": "a.mp3", "genre": "Vaporwave", "bpm": "128", "key": "8A"}])
        report = self.run_audit()
        self.assertEqual(len(report["genre_non_canonical"]), 1)
        self.assertEqual(report["genre_non_canonical"][0]["value_seen"], ["Vaporwave"])

    def test_empty_genre_bpm_key_are_counted(self):
        make_library(self.library, [{"relative": "a.mp3"}])
        report = self.run_audit()
        self.assertEqual(report["empty_genre"]["count"], 1)
        self.assertEqual(report["empty_bpm"]["count"], 1)
        self.assertEqual(report["key_notation"]["counts"]["empty"], 1)

    def test_camelot_and_standard_key_notation_both_classified_and_mix_detected(self):
        make_library(self.library, [
            {"relative": "a.mp3", "genre": "House", "key": "8A"},
            {"relative": "b.mp3", "genre": "House", "key": "Abm"},
        ])
        report = self.run_audit()
        self.assertEqual(report["key_notation"]["counts"]["camelot"], 1)
        self.assertEqual(report["key_notation"]["counts"]["standard"], 1)
        self.assertTrue(report["key_notation"]["mixed_camelot_and_standard"])

    def test_grouping_values_are_tallied(self):
        make_library(self.library, [
            {"relative": "a.mp3", "genre": "House", "grouping": "Clean"},
            {"relative": "b.mp3", "genre": "House", "grouping": "Clean"},
            {"relative": "c.mp3", "genre": "House", "grouping": "Dirty"},
        ])
        report = self.run_audit()
        self.assertEqual(report["grouping_values_in_use"], {"Clean": 2, "Dirty": 1})

    def test_baseline_suppresses_previously_known_non_canonical_value_until_full(self):
        make_library(self.library, [{"relative": "a.mp3", "genre": "Vaporwave"}])
        baseline_path = self.tmp / "baseline.json"

        first = self.run_audit(baseline=baseline_path, update_baseline=True)
        self.assertTrue(first["baseline_updated"])
        self.assertEqual(json.loads(baseline_path.read_text())["known_non_canonical_genre_values"], ["vaporwave"])

        second = self.run_audit(baseline=baseline_path)
        self.assertEqual(second["genre_non_canonical"], [])
        self.assertEqual(second["genre_non_canonical_suppressed_by_baseline"], 1)

        third = self.run_audit(baseline=baseline_path, full=True)
        self.assertEqual(len(third["genre_non_canonical"]), 1)

    def test_baseline_still_surfaces_a_genuinely_new_value(self):
        make_library(self.library, [{"relative": "a.mp3", "genre": "Vaporwave"}])
        baseline_path = self.tmp / "baseline.json"
        self.run_audit(baseline=baseline_path, update_baseline=True)

        make_library(self.library, [{"relative": "b.mp3", "genre": "Synthwave"}])
        second = self.run_audit(baseline=baseline_path)
        self.assertEqual(len(second["genre_non_canonical"]), 1)
        self.assertEqual(second["genre_non_canonical"][0]["value_seen"], ["Synthwave"])

    def test_update_baseline_is_opt_in_not_automatic(self):
        make_library(self.library, [{"relative": "a.mp3", "genre": "Vaporwave"}])
        baseline_path = self.tmp / "baseline.json"
        report = self.run_audit(baseline=baseline_path, update_baseline=False)
        self.assertFalse(report["baseline_updated"])
        self.assertFalse(baseline_path.exists())

    def test_no_library_file_is_ever_modified(self):
        made = make_library(self.library, [
            {"relative": "a.mp3", "genre": "house", "bpm": "128", "key": "8A", "grouping": "Clean"},
        ])
        path = made["a.mp3"]
        before = path.read_bytes()

        self.run_audit()

        after = path.read_bytes()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
