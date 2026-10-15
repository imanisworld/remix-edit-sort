"""Tests for the release-year reissue guard."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import guard_reissue_years as guard  # noqa: E402
import validate_year_evidence as validate  # noqa: E402


def hit(title="Album", year=1999, grade="confirmed_primary", store="Store A"):
    return {
        "store": store, "album_title": title, "year": year,
        "url": f"https://example.com/{store.replace(' ', '-').lower()}",
        "evidence_grade": grade,
    }


def item(*hits, embedded_year=None):
    return {
        "key": "artist--track", "subject": "Artist — Track",
        "embedded_year": embedded_year, "store_hits": list(hits),
    }


class ReissueYearGuardTests(unittest.TestCase):
    def test_every_reissue_title_marker_holds_the_year(self):
        titles = {
            "Album (2011 Remastered)": "remaster",
            "Album Deluxe Edition": "deluxe",
            "Album 25th Anniversary": "anniversary",
            "Artist Greatest Hits": "greatest_hits",
            "Album Live at Home": "live",
        }
        for title, marker in titles.items():
            with self.subTest(title=title):
                entry = guard.guard_entry(item(hit(title, 2011)), "2026-09-10")
                self.assertIsNone(entry["year"])
                self.assertEqual(entry["evidence_grade"], "unknown")
                self.assertEqual(entry["review_status"], "reissue_review_required")
                self.assertIn(marker, entry["guard_flags"][0]["markers"])
                self.assertEqual([], validate.validate({"schema_version": 1, "entries": [entry]}))

    def test_store_year_after_embedded_year_is_held(self):
        entry = guard.guard_entry(item(hit(year=2015), embedded_year=1997), "2026-09-10")
        self.assertIsNone(entry["year"])
        self.assertEqual(entry["guard_flags"][0]["code"], "store_year_after_embedded")
        self.assertEqual(entry["guard_flags"][0]["later_store_years"], [2015])

    def test_disagreeing_store_years_are_held(self):
        entry = guard.guard_entry(
            item(hit(year=1999), hit(year=2001, store="Store B")), "2026-09-10",
        )
        self.assertIsNone(entry["year"])
        self.assertIn(
            "store_year_disagreement", {flag["code"] for flag in entry["guard_flags"]},
        )
        self.assertEqual(entry["store_year_candidates"], [1999, 2001])

    def test_consistent_store_year_is_selected_with_conservative_grade(self):
        entry = guard.guard_entry(
            item(
                hit(year=1999, grade="confirmed_primary"),
                hit(year=1999, grade="confirmed_secondary", store="Store B"),
                embedded_year=1999,
            ),
            "2026-09-10",
        )
        self.assertEqual(entry["year"], 1999)
        self.assertEqual(entry["evidence_grade"], "confirmed_secondary")
        self.assertEqual(entry["review_status"], "store_year_consistent")
        self.assertFalse(entry["guard_flags"])
        self.assertEqual([], validate.validate({"schema_version": 1, "entries": [entry]}))

    def test_existing_registry_key_requires_explicit_replace(self):
        registry = {
            "schema_version": 1,
            "entries": [{"key": "artist--track", "year": 1998}],
        }
        replacement = guard.guard_entry(item(hit(year=1999)), "2026-09-10")
        with self.assertRaisesRegex(guard.YearGuardError, "already contains"):
            guard.merge_registry(registry, [replacement])
        merged = guard.merge_registry(registry, [replacement], replace_existing=True)
        self.assertEqual(merged["entries"], [replacement])


if __name__ == "__main__":
    unittest.main()
