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
                     "genre-naming-baseline.json"):
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

    def test_baseline_matches_current_rules_exactly(self):
        actual = naming.noncanonical_outputs(self.root)
        baseline = self.load("genre-naming-baseline.json")["existing_deviations"]
        self.assertEqual(naming.deviation_drift(actual, baseline), ([], []))
        self.assertEqual(naming.main(["--references", str(self.root),
                                      "--baseline", str(self.root / "genre-naming-baseline.json")]), 0)

    def test_new_noncanonical_genre_fails(self):
        data = self.load("version-genre-rules.json")
        data["tag_aliases"]["new bogus tag"] = "Bogus Genre"
        self.save("version-genre-rules.json", data)
        actual = naming.noncanonical_outputs(self.root)
        baseline = self.load("genre-naming-baseline.json")["existing_deviations"]
        extra, stale = naming.deviation_drift(actual, baseline)
        self.assertEqual(len(extra), 1)
        self.assertEqual(extra[0]["target"], "Bogus Genre")
        self.assertEqual(stale, [])

    def test_resolved_rule_requires_removing_exception(self):
        data = self.load("version-genre-rules.json")
        data["tag_aliases"]["ghetto tech"] = "Ghettotech"
        self.save("version-genre-rules.json", data)
        actual = naming.noncanonical_outputs(self.root)
        baseline = self.load("genre-naming-baseline.json")["existing_deviations"]
        extra, stale = naming.deviation_drift(actual, baseline)
        self.assertEqual(extra, [])
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0]["key"], "ghetto tech")
        self.assertEqual(stale[0]["target"], "Ghetto Tech")

    def test_duplicate_baseline_exception_fails(self):
        actual = naming.noncanonical_outputs(self.root)
        baseline = self.load("genre-naming-baseline.json")["existing_deviations"]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            naming.deviation_drift(actual, baseline + [baseline[0]])

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
        data["tag_aliases"]["two styles"] = "House; Ghetto Tech"
        self.save("version-genre-rules.json", data)
        actual = naming.noncanonical_outputs(self.root)
        found = [row for row in actual if row["key"] == "two styles"]
        self.assertEqual([x["target"] for x in found], ["Ghetto Tech"])


if __name__ == "__main__":
    unittest.main()
