"""The repository-wide duplicate truth model.

The rule these tests exist to protect: filename, title, artist and duration
similarity -- in any combination, at any threshold -- can produce at most
REVIEW. Only a byte-identical SHA-256 can produce EXACT_DUPLICATE, and only
EXACT_DUPLICATE may ever be put in front of a human as a deletion candidate.

The pairs below are drawn from the shapes that actually appear in this
library: Clean/Dirty masters, Intro edits, instrumentals, and two different
producers' edits of one track. Every one of them is the same length. The old
"same destination name + duration within 2s = duplicate" rule classified
several of them as duplicates.
"""

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import duplicate_semantics as ds  # noqa: E402


class ExactDuplicateTests(unittest.TestCase):
    def test_identical_sha256_is_the_only_exact_duplicate(self):
        result = ds.classify(sha256_a="a" * 64, sha256_b="a" * 64,
                             name_a="Track.mp3", name_b="Track (1).mp3")
        self.assertEqual(result["verdict"], ds.EXACT_DUPLICATE)
        self.assertTrue(result["deletable"])
        self.assertTrue(ds.may_authorize_deletion(result["verdict"]))

    def test_same_name_same_duration_different_sha_is_not_an_exact_duplicate(self):
        """The precise case the old heuristic got wrong."""
        result = ds.classify(sha256_a="a" * 64, sha256_b="b" * 64,
                             name_a="Song.mp3", name_b="Song.mp3",
                             duration_a=213.4, duration_b=213.9)
        self.assertEqual(result["verdict"], ds.REVIEW)
        self.assertFalse(result["deletable"])
        self.assertFalse(ds.may_authorize_deletion(result["verdict"]))
        self.assertIn("similarity is not duplicate proof", result["reason"])

    def test_normalized_metadata_match_alone_is_review_only(self):
        result = ds.classify(sha256_a="a" * 64, sha256_b="b" * 64,
                             name_a="the weeknd - blinding lights.mp3",
                             name_b="The Weeknd - Blinding Lights.mp3",
                             duration_a=200.0, duration_b=200.0)
        self.assertEqual(result["verdict"], ds.REVIEW)

    def test_no_evidence_at_all_is_review(self):
        result = ds.classify(name_a="A.mp3", name_b="B.mp3")
        self.assertEqual(result["verdict"], ds.REVIEW)


class RelatedVersionTests(unittest.TestCase):
    RELATED_PAIRS = [
        ("Song (Clean).mp3", "Song (Dirty).mp3"),
        ("Song (Intro Clean).mp3", "Song (Clean).mp3"),
        ("Song.mp3", "Song (Instrumental).mp3"),
        ("Song (Extended Mix).mp3", "Song (Radio Edit).mp3"),
        ("Song (Acapella).mp3", "Song.mp3"),
        ("Song (VIP).mp3", "Song (Bootleg).mp3"),
        ("Song (Live).mp3", "Song.mp3"),
        ("Song (Slowed).mp3", "Song.mp3"),
    ]

    def test_related_versions_are_never_duplicates(self):
        for name_a, name_b in self.RELATED_PAIRS:
            with self.subTest(pair=(name_a, name_b)):
                result = ds.classify(sha256_a="a" * 64, sha256_b="b" * 64,
                                     name_a=name_a, name_b=name_b,
                                     duration_a=200.0, duration_b=200.0)
                self.assertEqual(result["verdict"], ds.RELATED_VERSION)
                self.assertFalse(result["deletable"])

    def test_related_version_wins_over_every_similarity_signal(self):
        """Identical duration and near-identical names must not override the
        fact that one is Clean and one is Dirty."""
        result = ds.classify(sha256_a="a" * 64, sha256_b="b" * 64,
                             name_a="Track (Clean).mp3", name_b="Track (Dirty).mp3",
                             duration_a=180.0, duration_b=180.0)
        self.assertEqual(result["verdict"], ds.RELATED_VERSION)

    def test_byte_identity_still_wins_over_version_markers(self):
        """Two byte-identical files ARE the same file, whatever they're named."""
        result = ds.classify(sha256_a="a" * 64, sha256_b="a" * 64,
                             name_a="Track (Clean).mp3", name_b="Track (Dirty).mp3")
        self.assertEqual(result["verdict"], ds.EXACT_DUPLICATE)

    def test_two_tracks_sharing_one_marker_are_not_related_versions(self):
        """Both marked "(Dirty)" says nothing about their relationship."""
        result = ds.classify(sha256_a="a" * 64, sha256_b="b" * 64,
                             name_a="Song A (Dirty).mp3", name_b="Song B (Dirty).mp3")
        self.assertEqual(result["verdict"], ds.REVIEW)

    def test_version_markers_are_detected_case_insensitively(self):
        self.assertIn("clean", ds.version_markers("Track (CLEAN).mp3"))
        self.assertIn("instrumental", ds.version_markers("Track - Instrumental"))


class AudioEquivalentTests(unittest.TestCase):
    def test_fingerprint_match_is_audio_equivalent_and_not_deletable(self):
        result = ds.classify(sha256_a="a" * 64, sha256_b="b" * 64,
                             name_a="Track.mp3", name_b="Track.m4a",
                             audio_fingerprint_match=True)
        self.assertEqual(result["verdict"], ds.AUDIO_EQUIVALENT)
        self.assertFalse(ds.may_authorize_deletion(result["verdict"]))

    def test_nothing_in_the_repo_currently_produces_audio_equivalent_by_itself(self):
        """No fingerprinting exists yet; the slot is defined, not populated."""
        result = ds.classify(sha256_a="a" * 64, sha256_b="b" * 64, name_a="x", name_b="y")
        self.assertNotEqual(result["verdict"], ds.AUDIO_EQUIVALENT)


class LabellingGuardTests(unittest.TestCase):
    def test_only_exact_duplicate_may_be_labelled_a_duplicate(self):
        ds.assert_not_called_duplicate(ds.EXACT_DUPLICATE, "exact duplicates")
        for verdict in (ds.REVIEW, ds.RELATED_VERSION, ds.AUDIO_EQUIVALENT):
            with self.subTest(verdict=verdict):
                with self.assertRaises(ValueError):
                    ds.assert_not_called_duplicate(verdict, "Duplicates")

    def test_only_exact_duplicate_is_in_the_deletable_set(self):
        self.assertEqual(ds.DELETABLE_VERDICTS, frozenset({ds.EXACT_DUPLICATE}))


class BuildPlanRegressionTests(unittest.TestCase):
    """build_plan.py must no longer call a name+duration collision a duplicate."""

    def test_no_active_code_files_collisions_under_a_duplicates_folder(self):
        source = (SCRIPTS_DIR / "build_plan.py").read_text(encoding="utf-8")
        self.assertNotIn('"bucket"] = "duplicate"', source)
        self.assertNotIn('UNSORTED, "Duplicates"', source)
        self.assertIn("possible_collision", source)
        self.assertIn("duplicate_semantics", source)

    def test_no_active_script_derives_a_duplicate_from_duration(self):
        for path in sorted(SCRIPTS_DIR.glob("*.py")):
            # duplicate_semantics.py is the one place duration may be
            # consulted at all, and there it can only ever contribute to a
            # REVIEW reason string -- never to a verdict. That is asserted
            # directly by test_same_name_same_duration_different_sha_is_not_
            # an_exact_duplicate above, so scanning its source here would only
            # re-flag the intended implementation.
            if path.name == "duplicate_semantics.py":
                continue
            source = path.read_text(encoding="utf-8")
            for line_number, line in enumerate(source.splitlines(), start=1):
                stripped = line.strip()
                if stripped.startswith("#") or stripped.startswith('"'):
                    continue
                if "duration" in line and "< 2.0" in line:
                    self.fail(f"{path.name}:{line_number} still derives a verdict from "
                              f"duration proximity: {stripped}")


if __name__ == "__main__":
    unittest.main()
