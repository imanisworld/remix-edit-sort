"""Tests for the read-only unresolved-token proposal builder."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import propose_genre_aliases as proposals  # noqa: E402


class GenreAliasProposalTests(unittest.TestCase):
    def setUp(self):
        self.rules = REPO_ROOT / "references" / "genre-learner-rules.json"

    def build(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "tokens.json"
            source.write_text(json.dumps(rows), encoding="utf-8")
            return proposals.build(source, self.rules)

    def test_aggregates_normalized_variants_without_losing_counts(self):
        report = self.build([
            {"token": "Dance", "count": 100, "example": "a.mp3"},
            {"token": "dance", "count": 2, "example": "b.mp3"},
            {"token": "DANCE!", "count": 1, "example": "c.mp3"},
        ])
        self.assertEqual(report["summary"]["source_rows"], 3)
        self.assertEqual(report["summary"]["normalized_tokens"], 1)
        self.assertEqual(report["summary"]["track_occurrences"], 103)
        row = report["proposals"][0]
        self.assertEqual(row["count"], 103)
        self.assertEqual(row["status"], "decision_required")
        self.assertEqual(row["variants"], ["Dance", "dance", "DANCE!"])

    def test_approved_and_operator_directed_mappings_are_separate(self):
        report = self.build([
            {"token": "Juke & Footwork", "count": 420},
            {"token": "wch", "count": 39},
            {"token": "phunk", "count": 3},
            {"token": "East Coast Hip Hop", "count": 14},
            {"token": "psychedelic pop", "count": 12},
            {"token": "indie rock", "count": 12},
        ])
        by_token = {row["token"]: row for row in report["proposals"]}
        self.assertEqual(by_token["Juke & Footwork"]["genre"], ["Juke"])
        self.assertEqual(by_token["wch"]["grouping"], ["White OOtz"])
        self.assertEqual(by_token["phunk"]["genre"], ["Breaks"])
        self.assertEqual(by_token["phunk"]["grouping"], ["Phunk"])
        self.assertEqual(by_token["East Coast Hip Hop"]["genre"], ["Hip-Hop"])
        self.assertEqual(by_token["East Coast Hip Hop"]["grouping"], ["East Coast"])
        self.assertEqual(by_token["psychedelic pop"]["genre"], ["Rock"])
        self.assertEqual(by_token["indie rock"]["grouping"], ["Indie Rock"])

    def test_compound_known_phrases_are_proposals_not_silent_rules(self):
        row = self.build([
            {"token": "techno house", "count": 10},
        ])["proposals"][0]
        self.assertEqual(row["status"], "partial_phrase_proposal")
        self.assertEqual(set(row["genre"]), {"Techno", "House"})

    def test_markdown_contains_a_row_for_every_normalized_token(self):
        report = self.build([
            {"token": "Holiday", "count": 3},
            {"token": "Bollywood", "count": 2},
        ])
        rendered = proposals.markdown(report)
        self.assertIn("| 3 | Holiday |", rendered)
        self.assertIn("| 2 | Bollywood |", rendered)
        self.assertEqual(rendered.count("\n| ") - 2, 2)

    def test_checked_in_review_covers_the_complete_current_source(self):
        report = json.loads(
            (REPO_ROOT / "references" / "genre-alias-proposals-20260910.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(report["summary"]["source_rows"], 606)
        self.assertEqual(report["summary"]["normalized_tokens"], 552)
        self.assertEqual(report["summary"]["track_occurrences"], 1949)
        self.assertEqual(
            sum(row["count"] for row in report["proposals"]), 1949,
        )
        self.assertEqual(
            len({row["normalized"] for row in report["proposals"]}), 552,
        )


if __name__ == "__main__":
    unittest.main()
