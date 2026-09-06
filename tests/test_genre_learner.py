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

    def test_juke_footwork_legacy_compound_is_not_training_label(self):
        parsed = self.parse("Juke & Footwork")
        self.assertEqual(parsed["status"], "legacy_compound_review")
        self.assertEqual(parsed["labels"], [])

    def test_genuine_multi_style_metadata_is_not_forced_single(self):
        parsed = self.parse("Ballroom; House")
        self.assertEqual(parsed["status"], "multi_label")
        self.assertEqual(set(parsed["labels"]), {"Ballroom", "House"})

    def test_context_terms_do_not_become_genre(self):
        self.assertEqual(self.parse("Twerk")["status"], "context_only")
        self.assertEqual(self.parse("Lounge Vibe")["status"], "context_only")

    def test_approved_legacy_normalizations(self):
        self.assertEqual(self.parse("Afrobeats")["labels"], ["Afrobeat"])
        self.assertEqual(self.parse("Baile Funk")["labels"], ["Baile"])
        self.assertEqual(self.parse("ATL Bass")["labels"], ["Atlanta Bass"])

    def test_sparse_genres_are_not_folded(self):
        counts = {"Deep House": 79, "House": 5000}
        status = genre_learner.training_status(counts, self.rules)
        self.assertEqual(status["Deep House"], "not_yet_trainable")
        self.assertEqual(status["House"], "trainable")

    def test_every_canonical_genre_has_exactly_one_internal_family(self):
        mapping = genre_learner.genre_to_family(self.rules)
        self.assertEqual(set(mapping), set(self.rules["canonical_genres"]))


if __name__ == "__main__":
    unittest.main()
