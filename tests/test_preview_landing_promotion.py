"""Synthetic, read-only checks for landing-folder promotion preview."""
from __future__ import annotations

import csv
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import preview_landing_promotion as promotion  # noqa: E402


class LandingPromotionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.lib = Path(temp.name) / "Music"
        self.landing = self.lib / "music to move"
        self.final = self.lib / "R&b"
        self.review = self.lib / "_GENRE NEEDS CHECK"
        for folder in (self.landing, self.final, self.review):
            folder.mkdir(parents=True)
        self.inventory = Path(temp.name) / "inventory.csv"
        self.rows = []
        self.destinations = {
            "schema_version": 1,
            "review_destination": "_GENRE NEEDS CHECK",
            "routes": {"R&B": {"default": "R&b"}},
        }
        self.master = {"canonical_genres": ["R&B", "House", "Hip-Hop", "Afro House"]}
        self.approvals = {"items": []}
        self.holds = {"items": []}

    def add(self, relative, genre="R&B", title="Song", data=b"test-audio"):
        source = self.landing / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        self.rows.append({
            "full_path": str(source),
            "sha256": digest,
            "genre": genre,
            "title": title,
        })
        return {"source_relative": relative, "sha256": digest,
                "genre": genre, "approved_by": "operator",
                "evidence": "explicit reviewed track and genre"}

    def rows_as_approved(self, relative):
        row = next(row for row in self.rows if Path(row["full_path"]).relative_to(self.landing).as_posix() == relative)
        return {"source_relative": relative, "sha256": row["sha256"],
                "genre": row["genre"], "approved_by": "operator",
                "evidence": "explicit reviewed track and genre"}

    def preview(self):
        with self.inventory.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=("full_path", "sha256", "genre", "title"))
            writer.writeheader()
            writer.writerows(self.rows)
        return promotion.make_preview(
            self.lib, self.landing, self.inventory,
            self.destinations, self.approvals, self.holds, self.master
        )

    def test_approved_tag_and_confirmed_folder_becomes_candidate_only(self):
        approval = self.add("test.mp3")
        self.approvals["items"].append(approval)
        result = self.preview()
        row = result["items"][0]
        self.assertEqual(row["status"], "ROUTE_CANDIDATE")
        self.assertEqual(row["destination"], str(self.final.resolve() / "test.mp3"))
        self.assertFalse(result["approved_apply"])
        self.assertTrue((self.landing / "test.mp3").exists())
        self.assertFalse((self.final / "test.mp3").exists())

    def test_not_approved_or_ambiguous_goes_to_one_review_queue(self):
        self.add("unreviewed.mp3")
        self.add("ambiguous.mp3", genre="Garage Jungle", data=b"another")
        result = self.preview()
        self.assertEqual(result["counts"]["NEEDS_REVIEW"], 2)
        self.assertTrue(all(row["destination"] is None for row in result["items"]))
        self.assertTrue((self.landing / "unreviewed.mp3").exists())
        self.assertTrue((self.landing / "ambiguous.mp3").exists())

    def test_stale_or_wrong_genre_approval_cannot_route(self):
        item = self.add("test.mp3")
        item["genre"] = "House"
        self.approvals["items"].append(item)
        self.assertEqual(self.preview()["items"][0]["status"], "NEEDS_REVIEW")

    def test_named_active_event_is_held_in_landing(self):
        item = self.add("gig.mp3")
        self.approvals["items"].append(item)
        self.holds["items"].append(item)
        row = self.preview()["items"][0]
        self.assertEqual(row["status"], "EVENT_HOLD")
        self.assertIsNone(row["destination"])

    def test_events_subfolders_are_protected_by_default(self):
        self.add("Events/November Party/live.mp3")
        row = self.preview()["items"][0]
        self.assertEqual(row["status"], "EVENT_HOLD")

    def test_version_specific_route_preserves_existing_structure(self):
        item = self.add("CLEAN/test.mp3")
        self.destinations["routes"]["R&B"] = {
            "versions": {"CLEAN": "R&b"},
        }
        self.approvals["items"].append(item)
        row = self.preview()["items"][0]
        self.assertEqual(row["version"], "CLEAN")
        self.assertEqual(row["status"], "ROUTE_CANDIDATE")

    def test_missing_version_route_defaults_to_review(self):
        item = self.add("test.mp3")
        self.destinations["routes"]["R&B"] = {"versions": {"CLEAN": "R&b"}}
        self.approvals["items"].append(item)
        self.assertEqual(self.preview()["items"][0]["status"], "NEEDS_REVIEW")

    def test_existing_destination_is_never_overwritten(self):
        item = self.add("test.mp3")
        (self.final / "test.mp3").write_bytes(b"different source")
        self.approvals["items"].append(item)
        row = self.preview()["items"][0]
        self.assertEqual(row["status"], "BLOCKED")
        self.assertIn("collision", row["reason"])

    def test_two_sources_to_same_target_never_collide(self):
        a = self.add("A/test.mp3", data=b"one")
        b = self.add("B/test.mp3", data=b"two")
        self.approvals["items"].extend([a, b])
        rows = self.preview()["items"]
        self.assertEqual([r["status"] for r in rows].count("ROUTE_CANDIDATE"), 1)
        self.assertEqual([r["status"] for r in rows].count("BLOCKED"), 1)

    def test_changed_file_invalidates_preview(self):
        item = self.add("test.mp3")
        (self.landing / "test.mp3").write_bytes(b"changed")
        with self.assertRaisesRegex(promotion.PromotionRefused, "hash changed"):
            self.preview()

    def test_unlisted_file_invalidates_preview(self):
        self.add("test.mp3")
        (self.landing / "other.mp3").write_bytes(b"not inventoried")
        with self.assertRaisesRegex(promotion.PromotionRefused, "stale inventory"):
            self.preview()

    def test_unknown_event_hold_fails_closed(self):
        self.add("test.mp3")
        self.holds["items"].append({
            "source_relative": "absent.mp3",
            "sha256": "a" * 64,
        })
        with self.assertRaisesRegex(promotion.PromotionRefused, "not in current inventory"):
            self.preview()

    def test_hold_hash_change_fails_closed(self):
        self.add("test.mp3")
        self.holds["items"].append({
            "source_relative": "test.mp3",
            "sha256": "b" * 64,
        })
        with self.assertRaisesRegex(promotion.PromotionRefused, "event hold content hash"):
            self.preview()

    def test_destination_outside_library_or_in_landing_is_rejected(self):
        self.add("test.mp3")
        approved = self.add("safe.mp3", data=b"safe")
        self.approvals["items"].append(approved)
        for invalid in ("../elsewhere", "music to move", "/tmp"):
            with self.subTest(invalid=invalid):
                self.destinations["routes"]["R&B"] = {"default": invalid}
                self.assertEqual(self.preview()["items"][-1]["status"], "BLOCKED")

    def test_missing_real_destination_fails_closed(self):
        self.add("test.mp3")
        self.destinations["routes"]["R&B"] = {"default": "does not exist"}
        self.approvals["items"].append(self.rows_as_approved("test.mp3"))
        self.assertEqual(self.preview()["items"][0]["status"], "BLOCKED")

    def test_symlinked_destination_is_rejected(self):
        self.add("test.mp3")
        link = self.lib / "shortcut"
        link.symlink_to(self.review, target_is_directory=True)
        self.destinations["routes"]["R&B"] = {"default": "shortcut"}
        self.approvals["items"].append(self.rows_as_approved("test.mp3"))
        self.assertEqual(self.preview()["items"][0]["status"], "BLOCKED")


    def test_casefold_existing_filename_collision_is_blocked(self):
        approval = self.add("test.mp3")
        self.approvals["items"].append(approval)
        (self.final / "TEST.MP3").write_bytes(b"other")
        row = self.preview()["items"][0]
        self.assertEqual(row["status"], "BLOCKED")
        self.assertIsNone(row["destination"])

    def test_lowercase_events_folder_is_held(self):
        self.add("events/private wedding/track.mp3")
        self.assertEqual(self.preview()["items"][0]["status"], "EVENT_HOLD")

    def test_empty_destination_map_does_not_make_new_folders(self):
        self.add("test.mp3")
        self.destinations = {"schema_version": 1, "routes": {}}
        row = self.preview()["items"][0]
        self.assertEqual(row["status"], "NEEDS_REVIEW")
        self.assertIsNone(row["destination"])
        self.assertFalse((self.lib / "test.mp3").exists())


    def test_stale_approval_hash_is_refused(self):
        item = self.add("track.mp3")
        item["sha256"] = "f" * 64
        self.approvals["items"].append(item)
        with self.assertRaisesRegex(promotion.PromotionRefused, "approval content hash is stale"):
            self.preview()

    def test_conflicting_folder_and_filename_versions_are_reviewed(self):
        item = self.add("CLEAN/Artist - Song (Dirty).mp3")
        self.approvals["items"].append(item)
        row = self.preview()["items"][0]
        self.assertEqual(row["status"], "NEEDS_REVIEW")
        self.assertIn("version evidence conflicts", row["reason"])
        self.assertIsNone(row["destination"])

    def test_multiple_version_folder_markers_require_review(self):
        item = self.add("CLEAN/DIRTY/test.mp3")
        self.approvals["items"].append(item)
        row = self.preview()["items"][0]
        self.assertEqual(row["status"], "NEEDS_REVIEW")

    def test_empty_inventory_is_valid_when_landing_is_empty(self):
        result = self.preview()
        self.assertEqual(result["counts"], {"scanned": 0})
        self.assertEqual(result["items"], [])

    def test_unlisted_file_in_empty_inventory_fails_closed(self):
        (self.landing / "extra.mp3").write_bytes(b"not in inventory")
        with self.assertRaisesRegex(promotion.PromotionRefused, "stale inventory"):
            self.preview()

    def test_serato_map_uses_volume_relative_paths_only_for_ready_tracks(self):
        approved = self.add("Ready.mp3")
        self.approvals["items"].append(approved)
        self.add("Needs Review.mp3", data=b"needs")
        held = self.add("Next Set.mp3", data=b"gig")
        self.holds["items"].append(held)
        preview = self.preview()
        result = promotion.serato_rename_map(preview, Path("/"))
        self.assertEqual(len(result["renames"]), 1)
        mapping = result["renames"][0]
        self.assertEqual(mapping["from"], str(self.landing.resolve() / "Ready.mp3").lstrip("/"))
        self.assertEqual(mapping["to"], str(self.final.resolve() / "Ready.mp3").lstrip("/"))
        self.assertTrue((self.landing / "Ready.mp3").exists())

    def test_serato_map_uses_no_references_when_all_need_review(self):
        self.add("unreviewed.mp3")
        self.assertEqual(
            promotion.serato_rename_map(self.preview(), Path("/"))["renames"], []
        )

    def test_serato_volume_root_cannot_be_an_arbitrary_subdirectory(self):
        approved = self.add("test.mp3")
        self.approvals["items"].append(approved)
        with self.assertRaisesRegex(promotion.PromotionRefused, "mounted volume root"):
            promotion.serato_rename_map(self.preview(), self.lib)

    def test_serato_candidate_map_is_accepted_by_existing_rewrite_validator(self):
        from rewrite_serato_paths import normalize_rename_map
        approved = self.add("test.mp3")
        self.approvals["items"].append(approved)
        candidate_map = promotion.serato_rename_map(self.preview(), Path("/"))
        rules = normalize_rename_map(candidate_map)
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["from"], candidate_map["renames"][0]["from"])
        self.assertEqual(rules[0]["to"], candidate_map["renames"][0]["to"])


    def test_non_text_approval_fields_never_authorize_a_move(self):
        for approved_by, evidence in (
            (None, "reviewed"), (True, "reviewed"), (123, "reviewed"),
            ("operator", None), ("operator", False), ("operator", 3),
            (" ", "reviewed"), ("operator", " "),
        ):
            with self.subTest(approved_by=approved_by, evidence=evidence):
                self.rows.clear()
                self.approvals["items"].clear()
                item = self.add("test.mp3")
                item["approved_by"] = approved_by
                item["evidence"] = evidence
                self.approvals["items"].append(item)
                result = self.preview()
                self.assertEqual(result["items"][0]["status"], "NEEDS_REVIEW")
                self.assertIsNone(result["items"][0]["destination"])

    def test_symlinked_landing_root_is_rejected_before_resolution(self):
        self.add("test.mp3")
        alias = self.lib / "landing-link"
        alias.symlink_to(self.landing, target_is_directory=True)
        with self.inventory.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=("full_path", "sha256", "genre", "title"))
            writer.writeheader()
            writer.writerows(self.rows)
        with self.assertRaisesRegex(promotion.PromotionRefused, "symlinks"):
            promotion.make_preview(
                self.lib, alias, self.inventory,
                self.destinations, self.approvals, self.holds, self.master
            )

    def test_symlinked_library_root_is_rejected_before_resolution(self):
        self.add("test.mp3")
        alias = self.lib.parent / "music-link"
        alias.symlink_to(self.lib, target_is_directory=True)
        with self.inventory.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=("full_path", "sha256", "genre", "title"))
            writer.writeheader()
            writer.writerows(self.rows)
        with self.assertRaisesRegex(promotion.PromotionRefused, "symlinks"):
            promotion.make_preview(
                alias, self.landing, self.inventory,
                self.destinations, self.approvals, self.holds, self.master
            )

if __name__ == "__main__":
    unittest.main()
