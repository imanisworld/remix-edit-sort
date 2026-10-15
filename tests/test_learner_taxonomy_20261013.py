"""Regression tests for the 2026-10-13 Genre Master Review decisions."""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / 'scripts'
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import genre_learner  # noqa: E402


class GenreMasterReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = genre_learner.load_rules(
            REPO_ROOT / 'references/genre-learner-rules.json'
        )
        cls.canonical = cls.rules['canonical_genres']

    def parse(self, raw):
        return genre_learner.classify_raw_genre(raw, self.rules)

    def assertSingle(self, raw, genre, grouping=()):
        parsed = self.parse(raw)
        self.assertEqual(parsed['labels'], [genre], raw)
        self.assertEqual(parsed['grouping'], list(grouping), raw)

    def assertContextOnly(self, raw, grouping):
        parsed = self.parse(raw)
        self.assertEqual(parsed['status'], 'context_only', raw)
        self.assertEqual(parsed['labels'], [], raw)
        self.assertEqual(parsed['grouping'], [grouping], raw)

    def assertNeedsCheck(self, raw):
        parsed = self.parse(raw)
        self.assertEqual(parsed['labels'], [], raw)
        self.assertEqual(parsed['grouping'], [], raw)

    def test_afrobeats_is_the_master_name(self):
        self.assertIn('Afrobeats', self.canonical)
        self.assertNotIn('Afrobeat', self.canonical)
        for raw in ('Afrobeats', 'Afro Beats'):
            self.assertSingle(raw, 'Afrobeats')
        self.assertNeedsCheck('Afrobeat')

    def test_ballroom_vogue_has_no_slash(self):
        self.assertIn('Ballroom Vogue', self.canonical)
        self.assertFalse(any('/' in genre for genre in self.canonical))
        for raw in ('Ballroom', 'Vogue', 'Ballroom Vogue'):
            self.assertSingle(raw, 'Ballroom Vogue')

    def test_new_master_genres(self):
        for genre in ('Tech House', 'Afro Tech', 'Latin Pop', 'Blues',
                      'Classic Rock', 'Soft Rock', 'Pop Rock'):
            self.assertIn(genre, self.canonical)
            self.assertSingle(genre, genre)
        self.assertIn('Dub', self.canonical)
        self.assertNeedsCheck('Dub')

    def test_tech_house_and_afro_tech_stay_distinct(self):
        self.assertSingle('Tech House', 'Tech House')
        self.assertSingle('Afrotech', 'Afro Tech')
        self.assertSingle('Afro House', 'Afro House')

    def test_funk_soul_boogie_stay_merged(self):
        # Decision 08, chosen directly by the user: keep merged as Funk & Soul.
        for raw in ('Funk', 'Soul', 'Boogie', 'Funk & Soul'):
            self.assertSingle(raw, 'Funk & Soul')

    def test_style_descriptor_moves_to_grouping(self):
        # Decisions 15, 18, 19: the style stays Genre, the descriptor goes to Grouping.
        self.assertSingle('R&B Throwbacks', 'R&B', ['Throwback'])
        self.assertSingle('Alternative Hip Hop', 'Hip-Hop', ['Alternative'])
        self.assertSingle('Alternative Hip-Hop', 'Hip-Hop', ['Alternative'])
        self.assertSingle('Jazzy House', 'Deep House', ['Jazzy'])

    def test_undecided_bare_tags_stay_held(self):
        # The user decided the master names, not what a bare legacy tag means:
        # "Afrobeat" may be Fela-era, "Dub" may be a dub mix.
        for raw in ('Afrobeat', 'Dub'):
            self.assertEqual(self.parse(raw)['status'], 'legacy_compound_review')

    def test_function_tags_are_grouping_not_genre(self):
        self.assertContextOnly('Twerk', 'Twerk')
        self.assertContextOnly('Dance', 'Dance')
        self.assertContextOnly('Club', 'Club')
        for genre in ('Twerk', 'Dance', 'Club'):
            self.assertNotIn(genre, self.canonical)

    def test_ambiguous_tags_need_check(self):
        for raw in ('Garage', 'Garage Jungle', 'Bass', 'Drum'):
            self.assertNeedsCheck(raw)

    def test_mapped_tags(self):
        self.assertSingle('Nu Disco', 'Disco')
        self.assertSingle('Nu R&B', 'Alternative R&B')
        self.assertSingle('Ghetto Tech', 'Ghettotech')


if __name__ == '__main__':
    unittest.main()
