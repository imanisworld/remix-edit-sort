"""Source-only checks that old sorter lanes agree with the PR #37 Genre master.

Synthetic records only. No music, folder, Serato, or Rekordbox writes.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import build_version_genre_plan as version_sort  # noqa: E402
import scan_library as remix_sort  # noqa: E402


def ref(name: str) -> dict:
    return json.loads((REPO / "references" / name).read_text(encoding="utf-8"))


class GenreReconciliationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.master = ref("genre-learner-rules.json")
        cls.legacy = ref("genre-rules.json")
        cls.version = ref("version-genre-rules.json")
        cls.canonical = set(cls.master["canonical_genres"])

    def version_result(self, genre: str = "", filename: str = "Song.mp3") -> dict:
        return version_sort.analyze_genre(
            {"genre": genre, "filename": filename}, self.version
        )

    def remix_result(self, phrase: str):
        # A synthetic item with no remixer, artist, or source-folder evidence.
        return remix_sort.classify_genre({
            "remixer_genre": None,
            "remixer": None, "remixer_raw": "",
            "original_artists": [],
            "source_folder": "",
            "title_clean": phrase,
            "artist_raw": "", "album_raw": "", "uploader": "",
        })[0]

    def test_one_canonical_vocabulary_for_both_sorters(self):
        self.assertEqual(self.legacy["canonical_genres"], self.master["canonical_genres"])
        self.assertEqual(len(self.canonical), 76)
        for rule in self.legacy["keyword_rules"]:
            self.assertIn(rule["genre"], self.canonical, rule)
        for genre in self.legacy["bpm_ranges"]:
            if not genre.startswith("_"):
                self.assertIn(genre, self.canonical, genre)
        for input_tag, target in self.version["tag_aliases"].items():
            for genre in target.split(";"):
                self.assertIn(genre.strip(), self.canonical, (input_tag, target))
        for rule in self.version["filename_keywords"]:
            self.assertIn(rule["genre"], self.canonical, rule)
        for genre in self.legacy["folder_names"]:
            self.assertIn(genre, self.canonical)
        for genre in self.version["folder_names"]:
            self.assertIn(genre, self.canonical)

    def test_atlanta_bass_and_miami_bass_remain_separate(self):
        self.assertIn("Atlanta Bass", self.canonical)
        self.assertIn("Miami Bass", self.canonical)
        for tag, expected in (
            ("ATL Bass", "Atlanta Bass"),
            ("atlanta bass", "Atlanta Bass"),
            ("Miami Bass", "Miami Bass"),
        ):
            with self.subTest(tag=tag):
                self.assertEqual(
                    self.version_result(tag)["proposed_genre"], expected
                )
        compound = self.version_result("atl bass, old head party")
        self.assertEqual(compound["resolution_status"], "PARTIALLY_RESOLVED")
        self.assertEqual(compound["recognized_genres"], ["Atlanta Bass"])
        self.assertEqual(compound["known_contexts"], ["Old Head"])
        self.assertIsNone(compound["proposed_genre"])
        self.assertEqual(self.remix_result("ATL Bass Edit"), "Atlanta Bass")
        self.assertEqual(self.remix_result("Miami Bass Edit"), "Miami Bass")

    def test_filename_cannot_override_a_conflicting_real_genre_tag(self):
        result = self.version_result(
            "Miami Bass", "DJ - ATL Bass 2026 Remix.mp3"
        )
        self.assertEqual(result["resolution_status"], "RESOLVED")
        self.assertEqual(result["proposed_genre"], "Miami Bass")
        self.assertEqual(result["confidence"], "tag")
        result = self.version_result(
            "Atlanta Bass", "DJ - Miami Bass Version.mp3"
        )
        self.assertEqual(result["proposed_genre"], "Atlanta Bass")

    def test_explicit_filename_recovers_only_blank_or_other(self):
        for raw in ("", "Other"):
            with self.subTest(raw=raw):
                result = self.version_result(raw, "Producer - ATL Bass Edit.mp3")
                self.assertEqual(result["proposed_genre"], "Atlanta Bass")
        ambiguous = self.version_result("Garage Jungle", "ATL Bass Edit.mp3")
        self.assertEqual(ambiguous["resolution_status"], "REVIEW")
        self.assertIsNone(ambiguous["proposed_genre"])

    def test_ambiguous_tags_and_context_are_held(self):
        for raw in (
            "Garage", "Garage Jungle", "Bass", "Drum", "Jazzy House",
            "R&B Throwbacks", "Alternative Hip Hop", "funk", "soul",
        ):
            with self.subTest(raw=raw):
                result = self.version_result(raw)
                self.assertNotEqual(result["resolution_status"], "RESOLVED")
                self.assertIsNone(result["proposed_genre"])
        for raw, context in (("Twerk", "Twerk"), ("Dance", "Dance")):
            with self.subTest(raw=raw):
                result = self.version_result(raw)
                self.assertEqual(result["resolution_status"], "REVIEW")
                self.assertIn(context, result["known_contexts"])
                self.assertIsNone(result["proposed_genre"])
                self.assertEqual(
                    self.version["grouping_terms"][raw.casefold()], context
                )

    def test_specific_styles_do_not_collapse_into_nearby_broad_genres(self):
        examples = (
            ("Afro House", "Afro House"),
            ("Afrotech", "Afro Tech"),
            ("Tech House", "Tech House"),
            ("Bass House", "Bass House"),
            ("Garage House", "Garage House"),
            ("Speed Garage", "Speed Garage"),
            ("UK Garage", "UK Garage"),
            ("Bassline", "Bassline"),
            ("Afro Baile", "Afro Baile"),
            ("Baile Funk", "Baile"),
            ("Groovy Bounce", "Groovy Bounce"),
            ("NOLA Bounce", "Bounce"),
            ("Guaracha", "Guaracha"),
            ("Cumbia", "Cumbia"),
            ("Reggaeton", "Reggaeton"),
            ("Dembow", "Dembow"),
            ("Soca", "Soca"),
            ("Dancehall", "Dancehall"),
            ("Ghetto House", "Ghetto House"),
            ("Ghetto Tech", "Ghettotech"),
            ("Footwork", "Footwork"),
            ("Juke", "Juke"),
            ("Chicago Drill", "Chicago Drill"),
            ("Sexy Drill", "Sexy Drill"),
            ("Jersey Drill", "Jersey Drill"),
        )
        for phrase, expected in examples:
            with self.subTest(phrase=phrase):
                self.assertEqual(self.remix_result(phrase), expected)

    def test_generic_ambiguous_keyword_does_not_force_style(self):
        for phrase in ("Some Club Edit", "Bass Mix", "Dub Version",
                       "Garage Mix", "Drum Edit", "Old Afrobeat Edit"):
            with self.subTest(phrase=phrase):
                self.assertIsNone(self.remix_result(phrase))

    def test_artist_mapping_normalizes_old_names_without_guessing(self):
        for old, new in (
            ("Ghetto Tech", "Ghettotech"),
            ("UKG", "UK Garage"),
            ("Vogue", "Ballroom Vogue"),
            ("Baile Funk", "Baile"),
            ("Ghetto House", "Ghetto House"),
            ("Funk/Soul", "Funk & Soul"),
            ("Miami Bass", "Miami Bass"),
            ("Atlanta Bass", "Atlanta Bass"),
        ):
            with self.subTest(old=old):
                self.assertEqual(remix_sort.canonical_artist_genre(old), new)
        for ambiguous in ("Club", "Soul", "jersey/atl club", "random"):
            with self.subTest(ambiguous=ambiguous):
                self.assertIsNone(remix_sort.canonical_artist_genre(ambiguous))


if __name__ == "__main__":
    unittest.main()
