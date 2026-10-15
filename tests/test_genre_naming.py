"""Fail-closed tests for the read-only genre naming consistency gate."""
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

import check_genre_naming as naming  # noqa: E402


class GenreNamingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for file in ("genre-learner-rules.json", "genre-rules.json", "version-genre-rules.json",
                     ):
            (self.root / file).write_text(
                (REPO_ROOT / "references" / file).read_text(encoding="utf-8"),
                encoding="utf-8",
            )

    def load(self, name):
        return json.loads((self.root / name).read_text(encoding="utf-8"))

    def save(self, name, obj):
        (self.root / name).write_text(
            json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def test_both_pipelines_are_canonical(self):
        self.assertEqual(naming.noncanonical_outputs(self.root), [])
        self.assertEqual(naming.main(["--references", str(self.root)]), 0)

    def test_new_noncanonical_genre_fails(self):
        data = self.load("version-genre-rules.json")
        data["tag_aliases"]["new bogus tag"] = "Bogus Genre"
        self.save("version-genre-rules.json", data)
        actual = naming.noncanonical_outputs(self.root)
        self.assertEqual(len(actual), 1)
        self.assertEqual(actual[0]["target"], "Bogus Genre")
        self.assertEqual(naming.main(["--references", str(self.root)]), 1)

    def test_old_spelling_regression_fails(self):
        data = self.load("version-genre-rules.json")
        data["tag_aliases"]["ghetto tech"] = "Ghetto Tech"
        self.save("version-genre-rules.json", data)
        self.assertEqual(
            naming.noncanonical_outputs(self.root)[0]["target"], "Ghetto Tech"
        )

    def test_legacy_master_drift_fails(self):
        data = self.load("genre-rules.json")
        data["canonical_genres"].remove("Atlanta Bass")
        self.save("genre-rules.json", data)
        with self.assertRaisesRegex(ValueError, "exactly match"):
            naming.noncanonical_outputs(self.root)

    def test_folder_map_cannot_introduce_unknown_style(self):
        data = self.load("version-genre-rules.json")
        data["folder_names"]["Wrong"] = "ValidFolder"
        self.save("version-genre-rules.json", data)
        with self.assertRaisesRegex(ValueError, "noncanonical key"):
            naming.noncanonical_outputs(self.root)

    def test_bad_master_alias_fails(self):
        data = self.load("genre-learner-rules.json")
        data["aliases"]["bad tag"] = "nonexistent genre"
        self.save("genre-learner-rules.json", data)
        with self.assertRaisesRegex(ValueError, "noncanonical target"):
            naming.noncanonical_outputs(self.root)

    def test_case_collisions_in_master_fail(self):
        data = self.load("genre-learner-rules.json")
        data["canonical_genres"].append("house")
        self.save("genre-learner-rules.json", data)
        with self.assertRaisesRegex(ValueError, "case/Unicode"):
            naming.noncanonical_outputs(self.root)

    def test_semicolon_targets_are_checked_independently(self):
        data = self.load("version-genre-rules.json")
        data["tag_aliases"]["two styles"] = "House; Bogus Genre"
        self.save("version-genre-rules.json", data)
        actual = naming.noncanonical_outputs(self.root)
        found = [row for row in actual if row["key"] == "two styles"]
        self.assertEqual([x["target"] for x in found], ["Bogus Genre"])


if __name__ == "__main__":
    unittest.main()
