"""Only explicitly approved Genre-to-folder destinations may appear in a sort.

Synthetic tests only; does not open, write, or move real music.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import apply_version_genre_plan as apply_version  # noqa: E402
import build_plan as remix  # noqa: E402
import build_version_genre_plan as version  # noqa: E402
import check_genre_naming as naming  # noqa: E402
import scan_library  # noqa: E402

EXPECTED = naming.APPROVED_FOLDER_NAMES
# Owner decisions 2026-10-15: these stay held (no destination, no Genre write).
HELD = ("Dub", "Breaks", "Electro", "Dubstep", "Miami Bass",
        "Atlanta Bass", "Afro Tech", "Gqom", "Kuduro", "Afro Baile",
        "New Wave", "Chicago Drill", "UK Drill", "NY Drill", "Sexy Drill",
        "Bronx Drill", "Jersey Drill")


def read_rules(name):
    return json.loads((ROOT / "references" / name).read_text(encoding="utf-8"))


class ApprovedGenreFolderTests(unittest.TestCase):
    def test_move_only_apply_results_allow_missing_genre_counts(self):
        # This field is only for reporting. The real move-only plan lacks
        # counts.genres, so never raise after the batch has completed.
        self.assertIsNone(apply_version.optional_genre_counts({}))
        self.assertIsNone(apply_version.optional_genre_counts({"counts": {}}))
        self.assertIsNone(apply_version.optional_genre_counts(
            {"counts": {"version_categories": {"_NEEDS CHECK": 398}}}))
        self.assertIsNone(apply_version.optional_genre_counts({"counts": []}))
        self.assertIsNone(apply_version.optional_genre_counts(
            {"counts": {"genres": []}}))
        self.assertEqual(
            apply_version.optional_genre_counts({"counts": {"genres": {"R&B": 2}}}),
            {"R&B": 2},
        )

    def test_both_committed_rule_files_contain_only_the_approved_entries(self):
        old, new = read_rules("genre-rules.json"), read_rules("version-genre-rules.json")
        self.assertEqual(old["folder_names"], EXPECTED)
        self.assertEqual(new["folder_names"], EXPECTED)
        self.assertEqual(naming.noncanonical_outputs(ROOT / "references"), [])
        # Spot-check the owner's exact live spellings and parent-folder choices.
        self.assertEqual(len(EXPECTED), 59)
        for genre, folder in (("Hip-Hop", "Hip Hop"), ("R&B", "R&B"),
                              ("Afrobeats", "Afrobeats"),  # folder renamed 2026-10-16
                              ("Ballroom Vogue", "Ballroom"), ("Funk & Soul", "Funk"),
                              ("Bounce", "New Orleans Bounce"), ("Hardgroove", "Hard GROOVE"),
                              ("Riddim", "RIDDIM"), ("Deep House", "House"),
                              ("Alternative R&B", "R&B"), ("UK Garage", "GarageJungle")):
            self.assertEqual(EXPECTED[genre], folder)
        for genre in HELD:
            self.assertNotIn(genre, EXPECTED)
        # Every master Genre is either approved or explicitly held.
        master = read_rules("genre-learner-rules.json")["canonical_genres"]
        self.assertEqual(set(master), set(EXPECTED) | set(HELD))

    def test_every_approved_genre_tag_is_recognized_by_the_version_pipeline(self):
        rules = read_rules("version-genre-rules.json")
        for genre in EXPECTED:
            with self.subTest(genre=genre):
                result = version.analyze_genre({"genre": genre, "filename": "x.mp3"}, rules)
                self.assertEqual(result["proposed_genre"], genre)

    def test_all_other_master_genres_hold_even_if_the_folder_name_matches(self):
        master = read_rules("genre-learner-rules.json")["canonical_genres"]
        self.assertEqual(len(master), 76)
        version_rules = read_rules("version-genre-rules.json")
        with mock.patch.dict(remix.FOLDER_NAMES, EXPECTED, clear=True):
            for genre in master:
                with self.subTest(genre=genre):
                    expected = EXPECTED.get(genre)
                    self.assertEqual(remix.genre_dir(genre), expected)
                    self.assertEqual(
                        version.genre_folder_for(genre, version_rules),
                        expected if expected is not None else version.NEEDS_GENRE,
                    )
        for held in HELD:
            self.assertNotIn(held, EXPECTED)
            self.assertEqual(version.genre_folder_for(held, version_rules), version.NEEDS_GENRE)
        self.assertEqual(version.genre_folder_for("R&B; Alternative", version_rules),
                         version.NEEDS_GENRE)

    def test_malformed_or_missing_rule_map_denies_destination(self):
        for rules in ({}, {"folder_names": None}, {"folder_names": []},
                      {"folder_names": {"House": ""}},
                      {"folder_names": {"House": "../Other"}},
                      {"folder_names": {"House": "Other/Thing"}}):
            with self.subTest(rules=rules):
                self.assertEqual(version.genre_folder_for("House", rules), version.NEEDS_GENRE)

    def test_remix_plan_holds_recognized_genre_without_retaging_it(self):
        source = {
            "path": "/synthetic/Song.mp3", "filename": "Song.mp3",
            "title_clean": "Song", "original_artists": ["Artist"],
            "remixer": None, "remixer_raw": "", "remix_type": "",
            "is_remix": False, "is_long_mix": False, "duration": 200,
            "genre": "Dubstep", "existing_genre": "Dubstep",
            "existing_bpm": None, "year": "",
        }
        result = remix.plan_one(source, {})
        self.assertEqual(result["bucket"], "genre_needs_check")
        self.assertTrue(result["needs_review"])
        self.assertFalse(result["folder_approved"])
        self.assertEqual(result["held_genre"], "Dubstep")
        self.assertEqual(result["dest_dir"], "_Unsorted/_GENRE NEEDS CHECK")
        self.assertEqual(result["tags"]["genre"], "Dubstep")
        self.assertNotIn("#genre-dubstep", result["tags"]["comment_tags"])

        # A smaller style files into its parent folder and keeps its own tag.
        source["genre"] = "Deep House"
        source["existing_genre"] = "Deep House"
        parent = remix.plan_one(source, {})
        self.assertTrue(parent["folder_approved"])
        self.assertEqual(parent["dest_dir"], "Originals/House/Artist")
        self.assertEqual(parent["tags"]["genre"], "Deep House")

        source["genre"] = "R&B"
        source["existing_genre"] = "R&B"
        approved = remix.plan_one(source, {})
        self.assertEqual(approved["bucket"], "original")
        self.assertTrue(approved["folder_approved"])
        self.assertEqual(approved["dest_dir"], "Originals/R&B/Artist")

    def test_apply_rejects_old_plans_and_wrong_case_destinations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "Source.mp3"
            source.write_bytes(b"sample")
            import hashlib
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            plan = {
                "mode": "preview-only", "rules": {"approval_required": True},
                "items": [{
                    "source": str(source), "sha256": digest,
                    "destination": str(root / "CLEAN" / "Dubstep" / "Source.mp3"),
                    "destination_relative": "CLEAN/Dubstep/Source.mp3",
                    "version_category": "CLEAN",
                    "proposed_genre": "Dubstep", "tag_change": False,
                    "move": True,
                }],
            }
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan))
            with self.assertRaisesRegex(RuntimeError, "unapproved Genre folder"):
                apply_version.load_and_validate(root, plan_path)

            # Even an approved canonical tag must not use its default name
            # when the disk's approved spelling differs only in case.
            plan["items"][0]["proposed_genre"] = "R&B"
            plan["items"][0]["destination"] = str(root / "CLEAN" / "R&b" / "Source.mp3")
            plan["items"][0]["destination_relative"] = "CLEAN/R&b/Source.mp3"
            plan_path.write_text(json.dumps(plan))
            with self.assertRaisesRegex(RuntimeError, "unapproved Genre folder"):
                apply_version.load_and_validate(root, plan_path)

    def test_existing_folder_name_is_not_genre_proof(self):
        # House is an approved destination, but that does not make every
        # untagged track already in the House folder a House track.
        track = {
            "existing_genre": "", "filename": "Song.mp3",
            "remixer_genre": None, "remixer": None, "remixer_raw": "",
            "original_artists": [], "source_folder": "House",
            "title_clean": "Song", "artist_raw": "", "album_raw": "", "uploader": "",
        }
        self.assertEqual(scan_library.classify_genre(track), (None, "unclassified"))
        track["source_folder"] = "R&b"
        self.assertEqual(scan_library.classify_genre(track), ("R&B", "existing_folder"))

    def test_version_cli_routes_held_to_review_without_genre_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "library"
            root.mkdir()
            root = root.resolve()  # macOS /var -> /private/var symlink identity
            source_rows = []
            approved = ("R&B", "Neo Soul", "Alternative", "Drum & Bass",
                        "Ghettotech", "House", "Deep House", "Latin")
            for style in approved + ("Atlanta Bass", "Miami Bass", "Dubstep"):
                path = root / (style + " (Clean).mp3")
                path.write_bytes(b"synthetic not audio")
                source_rows.append([str(path), path.name, style, "Synthetic", "a" * 64])
            inventory = Path(tmp) / "inventory.csv"
            with inventory.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["full_path", "filename", "genre", "title", "sha256"])
                writer.writerows(source_rows)
            out = Path(tmp) / "output"
            with mock.patch.object(sys, "argv", [
                "build_version_genre_plan.py", "--root", str(root),
                "--inventory", str(inventory), "--out-dir", str(out),
            ]):
                version.main()
            by_genre = {
                item["current_genre"]: item
                for item in json.loads((out / "plan.json").read_text())["items"]
            }
            for style in approved:
                physical = EXPECTED[style]
                item = by_genre[style]
                self.assertEqual(item["proposed_genre"], style)
                self.assertIsNone(item["held_genre"])
                self.assertEqual(item["destination_relative"],
                                 f"CLEAN/{physical}/{style} (Clean).mp3")
            for held in ("Atlanta Bass", "Miami Bass", "Dubstep"):
                item = by_genre[held]
                self.assertEqual(item["destination_relative"],
                                 f"CLEAN/_GENRE NEEDS CHECK/{held} (Clean).mp3")
                self.assertIsNone(item["proposed_genre"])
                self.assertFalse(item["tag_change"])
                if held == "Dubstep":
                    # The current version rules do not recognize this tag;
                    # unknown evidence is held by the pre-existing path.
                    self.assertIsNone(item["held_genre"])
                else:
                    self.assertEqual(item["held_genre"], held)
                    self.assertIn("not approved", item["genre_evidence"])
            # The builder may create only local reports, never touch source bytes.
            for style in by_genre:
                self.assertEqual((root / (style + " (Clean).mp3")).read_bytes(),
                                 b"synthetic not audio")


if __name__ == "__main__":
    unittest.main()
