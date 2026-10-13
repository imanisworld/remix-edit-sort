"""Genre tag names vs. existing folder names (folder_names maps)."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import build_plan  # noqa: E402
import build_version_genre_plan as version_plan  # noqa: E402
import scan_library  # noqa: E402


class VersionPlanFolderTests(unittest.TestCase):
    def test_mapped_genre_keeps_existing_folder(self):
        rules = {"folder_names": {"Afrobeats": "Afrobeat"}}
        self.assertEqual(version_plan.genre_folder_for("Afrobeats", rules), "Afrobeat")

    def test_unmapped_genre_uses_its_own_name(self):
        self.assertEqual(version_plan.genre_folder_for("House", {"folder_names": {}}), "House")
        self.assertEqual(version_plan.genre_folder_for("House", {}), "House")

    def test_no_genre_routes_to_needs_check(self):
        self.assertEqual(version_plan.genre_folder_for(None, {"folder_names": {"x": "y"}}), version_plan.NEEDS_GENRE)


class RemixTreeFolderTests(unittest.TestCase):
    def test_genre_dir_uses_map(self):
        with mock.patch.dict(build_plan.FOLDER_NAMES, {"Ballroom Vogue": "Vogue"}, clear=True):
            self.assertEqual(build_plan.genre_dir("Ballroom Vogue"), "Vogue")
            self.assertEqual(build_plan.genre_dir("House"), "House")

    def test_existing_old_folder_reads_back_as_its_genre(self):
        with mock.patch.dict(scan_library.FOLDER_GENRES, {"Vogue": "Ballroom Vogue"}, clear=True), \
             mock.patch.dict(scan_library.RULES, {"canonical_genres": ["Ballroom Vogue"]}):
            track = {"remixer_genre": None, "original_artists": [], "source_folder": "Vogue"}
            self.assertEqual(scan_library.classify_genre(track)[0], "Ballroom Vogue")


class CommittedMapTests(unittest.TestCase):
    def test_committed_maps_are_flat_string_maps(self):
        for name in ("genre-rules.json", "version-genre-rules.json"):
            folder_names = json.loads((REPO_ROOT / "references" / name).read_text(encoding="utf-8"))["folder_names"]
            self.assertIsInstance(folder_names, dict)
            for genre, folder in folder_names.items():
                self.assertIsInstance(genre, str)
                self.assertIsInstance(folder, str)
                self.assertNotIn("/", folder)


if __name__ == "__main__":
    unittest.main()
