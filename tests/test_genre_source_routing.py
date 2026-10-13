"""Regression tests for known source-only genre misroutes.

These tests read repository JSON only; they never open the user's library.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_version_genre_plan as version_plan  # noqa: E402


def rules(filename: str) -> dict:
    return json.loads((ROOT / "references" / filename).read_text(encoding="utf-8"))


class SourceRoutingFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.version = rules("version-genre-rules.json")
        cls.legacy = rules("genre-rules.json")

    def analyze(self, raw: str, filename: str = "") -> dict:
        return version_plan.analyze_genre(
            {"genre": raw, "filename": filename},
            self.version,
        )

    def test_atlanta_bass_tag_does_not_become_miami_bass(self):
        for value in ("atl bass", "atlanta bass"):
            with self.subTest(value=value):
                row = self.analyze(value)
                self.assertEqual(row["resolution_status"], "RESOLVED")
                self.assertEqual(row["proposed_genre"], "Atlanta Bass")
        self.assertEqual(self.version["tag_aliases"]["atl bass, old head party"], "Atlanta Bass")
        self.assertEqual(self.analyze("miami bass")["proposed_genre"], "Miami Bass")

    def test_atl_bass_filename_rule_has_correct_target(self):
        row = self.analyze("", "DJ Example - ATL Bass Edit.mp3")
        self.assertEqual(row["proposed_genre"], "Atlanta Bass")
        self.assertEqual(row["resolution_status"], "RESOLVED")

    def test_twerk_is_purpose_grouping_and_cannot_be_assigned_as_genre(self):
        for raw in ("twerk", "Twerk, Old Head Party"):
            with self.subTest(raw=raw):
                result = self.analyze(raw)
                self.assertEqual(result["resolution_status"], "REVIEW")
                self.assertIsNone(result["proposed_genre"])
                self.assertIn("Twerk", result["known_contexts"])
        recognized = version_plan.classify_grouping(
            "song.mp3", "", "Twerk", self.version
        )
        self.assertIn("Twerk", recognized["recognized_existing"])
        self.assertEqual(self.version["context_aliases"]["twerk"], "Twerk")
        self.assertEqual(self.version["grouping_terms"]["twerk"], "Twerk")

    def test_garage_jungle_requires_review_not_fake_hybrid_genre(self):
        result = self.analyze("garage jungle")
        self.assertEqual(result["resolution_status"], "REVIEW")
        self.assertEqual(result["unknown_tokens"], ["garage jungle"])
        self.assertIsNone(result["proposed_genre"])

    def test_afro_house_keeps_its_style_and_afrotech_is_not_equated(self):
        rule = [row for row in self.legacy["keyword_rules"] if "afro house" in row["match"]]
        self.assertEqual(len(rule), 1)
        self.assertEqual(rule[0]["genre"], "Afro House")
        self.assertIn("Afro House", self.legacy["canonical_genres"])
        other = [row for row in self.legacy["keyword_rules"] if "afrotech" in row["match"]]
        self.assertEqual(other, [])
        # Ordered first-match rules must prioritize the specific style.
        indexes = {id(row): i for i, row in enumerate(self.legacy["keyword_rules"])}
        self.assertLess(indexes[id(rule[0])], next(i for i, row in enumerate(
            self.legacy["keyword_rules"]
        ) if row["genre"] == "House"))

    def test_generic_club_rule_does_not_swallow_twerk_or_bounce(self):
        club = [r for r in self.legacy["keyword_rules"] if r["genre"] == "Club"]
        self.assertEqual(len(club), 1)
        self.assertNotIn("twerk", club[0]["match"])
        self.assertNotIn("bounce", club[0]["match"])


if __name__ == "__main__":
    unittest.main()
