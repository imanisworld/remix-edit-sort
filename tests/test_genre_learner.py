"""Unit tests for genre learner taxonomy behavior.

No test touches the real library, Serato data, network, or external drives.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import genre_learner  # noqa: E402


class GenreLearnerTaxonomyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = genre_learner.load_rules(REPO_ROOT / "references/genre-learner-rules.json")

    def parse(self, value):
        return genre_learner.classify_raw_genre(value, self.rules)

    def test_specific_house_genres_do_not_fold_to_house(self):
        self.assertEqual(self.parse("Deep House")["labels"], ["Deep House"])
        self.assertEqual(self.parse("Bass House")["labels"], ["Bass House"])
        self.assertEqual(self.parse("Afro House")["labels"], ["Afro House"])

    def test_bass_is_not_house(self):
        self.assertEqual(self.parse("Miami Bass")["labels"], ["Miami Bass"])
        self.assertEqual(self.parse("Atlanta Bass")["labels"], ["Atlanta Bass"])
        self.assertEqual(self.parse("Bassline")["labels"], ["Bassline"])
        self.assertEqual(self.parse("Dubstep")["labels"], ["Dubstep"])
        self.assertEqual(self.parse("Bass")["status"], "unknown")

    def test_dembow_and_riddim_remain_atomic(self):
        self.assertEqual(self.parse("Dembow")["labels"], ["Dembow"])
        self.assertEqual(self.parse("Riddim")["labels"], ["Riddim"])

    def test_drill_subtypes_remain_atomic_and_nyc_alias_is_supported(self):
        self.assertEqual(self.parse("Sexy Drill")["labels"], ["Sexy Drill"])
        self.assertEqual(self.parse("NYC Drill")["labels"], ["NY Drill"])
        self.assertEqual(self.parse("Drill")["status"], "unknown")

    def test_juke_footwork_uses_operator_approved_juke_mapping(self):
        parsed = self.parse("Juke & Footwork")
        self.assertEqual(parsed["status"], "single")
        self.assertEqual(parsed["labels"], ["Juke"])

    def test_genuine_multi_style_metadata_is_not_forced_single(self):
        parsed = self.parse("Ballroom; House")
        self.assertEqual(parsed["status"], "multi_label")
        self.assertEqual(set(parsed["labels"]), {"Ballroom Vogue", "House"})

    def test_context_terms_do_not_become_genre(self):
        self.assertEqual(self.parse("Twerk")["status"], "context_only")
        self.assertEqual(self.parse("Lounge")["status"], "context_only")
        self.assertEqual(self.parse("Lounge Vibe")["status"], "context_only")
        self.assertNotIn("Lounge", self.rules["canonical_genres"])

    def test_operator_context_aliases_return_canonical_grouping_terms(self):
        cases = {
            "Holiday": ["Holiday"],
            "Y2K": ["Y2K"],
            "Black Y2K": ["Black Y2K"],
            "White OOtz": ["White OOtz"],
            "white": ["White OOtz"],
            "wch": ["White OOtz"],
            "white edits": ["White OOtz"],
            "white club": ["White OOtz"],
            "white club house": ["White OOtz"],
            "dnb white": ["White OOtz"],
            "Women": ["Women"],
            "Old Head Party": ["Old Head"],
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                parsed = self.parse(raw)
                self.assertEqual(parsed["status"], "context_only")
                self.assertEqual(parsed["labels"], [])
                self.assertEqual(parsed["grouping"], expected)

    def test_genre_modifier_and_context_modifier_patterns(self):
        cases = {
            "House Edit": (["House"], ["Edit"]),
            "R&B Remix": (["R&B"], ["Remix"]),
            "Juke Flip": (["Juke"], ["Remix"]),
            "Lounge Edit": ([], ["Lounge", "Edit"]),
        }
        for raw, (genres, grouping) in cases.items():
            with self.subTest(raw=raw):
                parsed = self.parse(raw)
                self.assertEqual(parsed["labels"], genres)
                self.assertEqual(parsed["grouping"], grouping)
                self.assertFalse(parsed["unknown"])
        # Historical Afrobeat has not been approved as interchangeable with
        # Afrobeats. Keep the version context, but review the style.
        ambiguous = self.parse("Afrobeat Bootleg")
        self.assertEqual(ambiguous["labels"], [])
        self.assertEqual(ambiguous["grouping"], ["Remix"])
        self.assertEqual(ambiguous["unknown"], ["Afrobeat"])

    def test_afro_and_phunk_operator_mappings(self):
        ambiguous = self.parse("afro")
        self.assertEqual(ambiguous["labels"], [])
        self.assertEqual(ambiguous["unknown"], ["afro"])
        self.assertEqual(self.parse("Afrobeats")["labels"], ["Afrobeats"])
        self.assertEqual(self.parse("Afro House")["labels"], ["Afro House"])
        self.assertEqual(self.parse("Afro Tech")["labels"], ["Afro Tech"])
        phunk = self.parse("phunk")
        self.assertEqual(phunk["labels"], ["Breaks"])
        self.assertEqual(phunk["grouping"], ["Phunk"])

    def test_approved_legacy_normalizations(self):
        self.assertEqual(self.parse("Afrobeats")["labels"], ["Afrobeats"])
        self.assertEqual(self.parse("Baile Funk")["labels"], ["Baile"])
        self.assertEqual(self.parse("ATL Bass")["labels"], ["Atlanta Bass"])

    def test_sparse_genres_are_not_folded(self):
        counts = {"Deep House": 79, "House": 5000}
        status = genre_learner.training_status(counts, self.rules)
        self.assertEqual(status["Deep House"], "not_yet_trainable")
        self.assertEqual(status["House"], "trainable")

    def test_protected_ampersand_phrases_never_leak_placeholders(self):
        parsed = self.parse("Alternative R&B; Pop")
        self.assertEqual(parsed["status"], "multi_label")
        self.assertEqual(parsed["labels"], ["Alternative R&B", "Pop"])
        parsed = self.parse("Nu R&B")
        self.assertEqual(parsed["status"], "single")
        self.assertEqual(parsed["labels"], ["Alternative R&B"])
        parsed = self.parse("Drum & Bass / Jungle, funk & soul")
        self.assertEqual(parsed["status"], "multi_label")
        self.assertEqual(parsed["labels"], ["Drum & Bass", "Jungle", "Funk & Soul"])
        for value in ("Alternative R&B; Pop", "Nu R&B", "Juke & Footwork; House"):
            for token in genre_learner._split_genre_string(value):
                self.assertNotIn("__PROTECT_", token)

    def test_fixed_holdout_is_reused_and_excluded_from_training(self):
        grouped = {
            "House": [{"path": f"/lib/house-{i}.mp3", "genre": "House"} for i in range(30)],
            "Rock": [{"path": f"/lib/rock-{i}.mp3", "genre": "Rock"} for i in range(12)],
        }
        _, holdout_a, _ = genre_learner.split_library_examples(grouped, self.rules)
        frozen = {row["path"] for row in holdout_a}
        train_b, holdout_b, report = genre_learner.split_library_examples(
            grouped, self.rules, fixed_holdout=frozen, cap_overrides={"House": 5}
        )
        self.assertEqual({row["path"] for row in holdout_b}, frozen)
        self.assertTrue(frozen.isdisjoint({row["path"] for row in train_b}))
        self.assertEqual(report["House"]["train"], 5)
        self.assertEqual(report["House"]["holdout_source"], "fixed")
        self.assertEqual(report["Rock"]["train"], 12 - report["Rock"]["holdout"])

    def test_external_training_examples_are_excluded_from_prediction_targets(self):
        targets = ["/lib/a.mp3", "/lib/b.mp3", "/lib/c.mp3"]
        final_training = [
            {
                "path": "/lib/a.mp3",
                "genre": "House",
                "artist": "Artist A",
                "title": "Song A",
                "source_type": "external",
                "feat": [1.0],
            },
            {
                "path": "/references/b.mp3",
                "genre": "Rock",
                "artist": "Artist B",
                "title": "Song B",
                "source_type": "external",
                "feat": [2.0],
            },
            {
                "path": "/lib/c.mp3",
                "genre": "Pop",
                "artist": "Artist C",
                "title": "Song C",
                "source_type": "library",
                "feat": [3.0],
            },
        ]
        library_rows = [
            {"file": "/lib/a.mp3", "artist": "Artist A", "title": "Song A"},
            {"file": "/lib/b.mp3", "artist": "Artist B", "title": "Song B"},
            {"file": "/lib/c.mp3", "artist": "Artist C", "title": "Song C"},
        ]
        kept, excluded = genre_learner.filter_prediction_targets_against_training(
            targets, final_training, library_rows
        )
        self.assertEqual(kept, ["/lib/c.mp3"])
        by_file = {row["file"]: row["reasons"] for row in excluded}
        self.assertIn("same_local_path_as_external_training", by_file["/lib/a.mp3"])
        self.assertIn("same_artist_title_as_external_training", by_file["/lib/b.mp3"])

    def test_cap_override_rejects_noncanonical_genre(self):
        self.assertEqual(genre_learner.parse_cap_overrides("R&B=800, Pop=600", self.rules), {"R&B": 800, "Pop": 600})
        with self.assertRaises(genre_learner.LearnerError):
            genre_learner.parse_cap_overrides("Drill=10", self.rules)

    def test_every_canonical_genre_has_exactly_one_internal_family(self):
        mapping = genre_learner.genre_to_family(self.rules)
        self.assertEqual(set(mapping), set(self.rules["canonical_genres"]))


if __name__ == "__main__":
    unittest.main()
