"""Regression tests for the final 2026-09-10 learner taxonomy decisions."""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import genre_learner  # noqa: E402


class FinalLearnerTaxonomyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = genre_learner.load_rules(
            REPO_ROOT / 'references/genre-learner-rules.json'
        )

    def parse(self, raw):
        return genre_learner.classify_raw_genre(raw, self.rules)

    def test_afro_and_garage_house_remain_distinct_genres(self):
        self.assertEqual(self.parse('Afro House')['labels'], ['Afro House'])
        self.assertEqual(self.parse('Garage House')['labels'], ['Garage House'])

    def test_breakbeat_normalizes_to_breaks(self):
        parsed = self.parse('Breakbeat')
        self.assertEqual(parsed['status'], 'single')
        self.assertEqual(parsed['labels'], ['Breaks'])
        self.assertEqual(parsed['grouping'], [])

    def test_nu_wave_normalizes_to_new_wave(self):
        parsed = self.parse('Nu Wave')
        self.assertEqual(parsed['status'], 'single')
        self.assertEqual(parsed['labels'], ['New Wave'])
        self.assertIn('New Wave', self.rules['canonical_genres'])

    def test_world_is_context_not_genre(self):
        parsed = self.parse('World')
        self.assertEqual(parsed['status'], 'context_only')
        self.assertEqual(parsed['labels'], [])
        self.assertEqual(parsed['grouping'], ['World'])
        self.assertNotIn('World', self.rules['canonical_genres'])


if __name__ == '__main__':
    unittest.main()
