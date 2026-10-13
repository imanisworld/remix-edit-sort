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
        self.assertEqual(row["destination"], str(self.final / "test.mp3"))
        self.assertFalse(result["approved_apply"])
        self.assertTrue((self.landing / "test.mp3").exists())
        self.assertFalse((self.final / "test.mp3").exists())

    def test_not_approved_or_ambiguous_goes_to_one_review_queue(self):
        self.add("unreviewed.mp3")
        self.add("ambiguous.mp3", genre="Garage Jungle", data=b"another")
        result = self.preview()
        self.assertEqual(result["counts"]["REVIEW_CANDIDATE"], 2)
        self.assertEqual(
            {row["destination"] for row in result["items"]},
            {str(self.review / "unreviewed.mp3"), str(self.review / "ambiguous.mp3")},
        )

    def test_stale_or_wrong_genre_approval_cannot_route(self):
        item = self.add("test.mp3")
        item["genre"] = "House"
        self.approvals["items"].append(item)
        self.assertEqual(self.preview()["items"][0]["status"], "REVIEW_CANDIDATE")

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
        self.assertEqual(self.preview()["items"][0]["status"], "REVIEW_CANDIDATE")

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
        for invalid in ("../elsewhere", "music to move", "/tmp"):
            with self.subTest(invalid=invalid):
                self.destinations["review_destination"] = invalid
                with self.assertRaises(promotion.PromotionRefused):
                    self.preview()

    def test_missing_real_destination_fails_closed(self):
        self.add("test.mp3")
        self.destinations["review_destination"] = "does not exist"
        with self.assertRaises(promotion.PromotionRefused):
            self.preview()

    def test_symlinked_destination_is_rejected(self):
        self.add("test.mp3")
        link = self.lib / "shortcut"
        link.symlink_to(self.review, target_is_directory=True)
        self.destinations["review_destination"] = "shortcut"
        with self.assertRaises(promotion.PromotionRefused):
            self.preview()


if __name__ == "__main__":
    unittest.main()
