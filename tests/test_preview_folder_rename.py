"""Synthetic validation for a folder-rename preview. No library paths are moved."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import preview_folder_rename as preview  # noqa: E402
import rewrite_serato_paths  # noqa: E402

SHA_A = "a" * 64
SHA_B = "b" * 64


def item(file_id=1, sha256=SHA_A, source="Shelf/Old/a.mp3", destination="Shelf/New/a.mp3", **changes):
    base = {"file_id": file_id, "sha256": sha256, "from": source, "to": destination}
    base.update(changes)
    return base


def plan(*items):
    return {"schema_version": 1, "items": list(items)}


class FolderRenamePreviewTests(unittest.TestCase):
    def test_unique_hashed_rename_matches_existing_serato_map_rules(self):
        result = preview.build_preview(plan(item()))
        self.assertEqual(result["mode"], "preview-only")
        self.assertFalse(result["apply_authorized"])
        self.assertFalse(result["proof_of_move"])
        self.assertEqual(result["items"][0]["state"], "CANDIDATE")
        rules = rewrite_serato_paths.normalize_rename_map(result["serato_rename_map"])
        self.assertEqual(rules, result["serato_rename_map"]["renames"])
        self.assertEqual(rules[0]["from"], "Shelf/Old/a.mp3")
        self.assertEqual(rules[0]["to"], "Shelf/New/a.mp3")
        self.assertIn("rewrite_serato_paths.py preview", result["existing_tools"]["serato"])
        self.assertIn("rekordbox_relocation_preview.py", result["existing_tools"]["rekordbox"])
        self.assertIn("does not parse", " ".join(result["unsupported"]))

    def test_case_and_unicode_collisions_stay_unresolved(self):
        cafe_nfd = "Shelf/New/cafe\u0301.mp3"
        cafe_nfc = "Shelf/New/caf\u00e9.mp3"
        result = preview.build_preview(plan(
            item(1, SHA_A, "Shelf/Old/a.mp3", "Shelf/New/Song.mp3"),
            item(2, SHA_B, "Shelf/Old/b.mp3", "Shelf/new/song.mp3"),
            item(3, "c" * 64, "Shelf/Old/c.mp3", cafe_nfd),
            item(4, "d" * 64, "Shelf/Old/d.mp3", cafe_nfc),
        ))
        self.assertTrue(all(row["state"] == "REVIEW" for row in result["items"]))
        self.assertIsNone(result["serato_rename_map"])
        self.assertFalse(result["serato_rename_map_ready"])

    def test_missing_identity_noop_and_destination_collision_do_not_emit_moves(self):
        result = preview.build_preview(plan(
            item(sha256=""),
            item(7, SHA_A, "Shelf/Keep/a.mp3", "Shelf/Keep/a.mp3"),
            item(8, SHA_A, "Shelf/Left/a.mp3", "Shelf/Same/a.mp3"),
            item(9, SHA_B, "Shelf/Right/b.mp3", "Shelf/Same/a.mp3"),
        ))
        states = [row["state"] for row in result["items"]]
        self.assertEqual(states, ["REVIEW", "NO_CHANGE", "BLOCKED", "BLOCKED"])
        self.assertIsNone(result["serato_rename_map"])

    def test_chained_folder_moves_stay_blocked_for_a_separate_plan(self):
        result = preview.build_preview(plan(
            item(1, SHA_A, "Shelf/A/track.mp3", "Shelf/B/track.mp3"),
            item(2, SHA_B, "Shelf/B/track.mp3", "Shelf/C/track.mp3"),
        ))
        self.assertTrue(all(row["state"] == "BLOCKED" for row in result["items"]))
        self.assertIsNone(result["serato_rename_map"])
        self.assertTrue(all("chained" in row["reason"] for row in result["items"]))

    def test_repeat_preview_is_identical_and_invents_no_live_folders(self):
        payload = plan(item())
        first = preview.build_preview(payload)
        second = preview.build_preview(payload)
        self.assertEqual(first, second)
        encoded = json.dumps(preview.build_preview(plan()))
        self.assertNotIn("Afrobeat", encoded)
        self.assertNotIn("Hip Hop", encoded)
        self.assertEqual(json.loads(encoded)["items"], [])

    def test_cli_prints_the_preview_and_leaves_the_fixture_untouched(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "plan.json"
            path.write_text(json.dumps(plan(item())), encoding="utf-8")
            before = path.read_bytes()
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = preview.main(["--plan", str(path)])
            self.assertEqual(code, 0)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(json.loads(stdout.getvalue())["items"][0]["state"], "CANDIDATE")


if __name__ == "__main__":
    unittest.main()
