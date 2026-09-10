import json
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import backup_manifest  # noqa: E402
import check_serato_crate_paths as checker  # noqa: E402
import mutation_engine  # noqa: E402
import rewrite_serato_paths as rewrite  # noqa: E402


def tlv(tag: str, value: bytes) -> bytes:
    return tag.encode("ascii") + struct.pack(">I", len(value)) + value


def text(value: str) -> bytes:
    return value.encode("utf-16-be")


def otrk(fields: bytes) -> bytes:
    return tlv("otrk", fields)


class FakeHeartbeat:
    def __init__(self):
        self.beats = 0

    def beat(self):
        self.beats += 1


class RenameMapTests(unittest.TestCase):
    def test_longest_component_boundary_match_wins(self):
        rules = rewrite.normalize_rename_map([
            {"from": "/Users/dj/Music/DNB", "to": "/Users/dj/Music/Drum & Bass"},
            {"from": "/Users/dj/Music/DNB/Song.wav", "to": "/Users/dj/Music/Drum & Bass/Song.flac"},
        ])
        exact, rule = rewrite.rewrite_path("Users/dj/Music/DNB/Song.wav", rules)
        neighboring, no_rule = rewrite.rewrite_path("Users/dj/Music/DNB-old/Song.wav", rules)
        self.assertEqual(exact, "Users/dj/Music/Drum & Bass/Song.flac")
        self.assertEqual(rule["source_index"], 2)
        self.assertEqual(neighboring, "Users/dj/Music/DNB-old/Song.wav")
        self.assertIsNone(no_rule)

    def test_chained_destination_is_refused(self):
        with self.assertRaisesRegex(rewrite.SeratoRewriteError, "chained/nested"):
            rewrite.normalize_rename_map([
                {"from": "Users/dj/Music/A", "to": "Users/dj/Music/B"},
                {"from": "Users/dj/Music/B", "to": "Users/dj/Music/C"},
            ])

    def test_plan_rules_keep_original_source_indexes_after_longest_first_sort(self):
        rules = rewrite.normalize_rename_map([
            {"from": "Users/dj/Music/A", "to": "Users/dj/Music/C"},
            {"from": "Users/dj/Music/A/long", "to": "Users/dj/Music/D"},
        ])
        self.assertEqual([rule["source_index"] for rule in rules], [2, 1])
        self.assertEqual(rewrite.normalize_rename_map(rules), rules)


class StrictTlvTests(unittest.TestCase):
    def test_torn_container_is_refused(self):
        with self.assertRaisesRegex(rewrite.SeratoRewriteError, "truncated"):
            rewrite.parse_tlv_strict(b"pfil\x00\x00\x00\x08ab", "fixture")

    def test_rewrite_preserves_unrelated_bytes_and_utf16_terminator(self):
        old = "Users/dj/Music/DNB/Song.mp3"
        new = "Users/dj/Music/Drum & Bass/Song.mp3"
        unknown = b"\x00\xff\x10opaque\x00"
        source = (
            tlv("vrsn", text("2.0"))
            + tlv("zzzz", unknown)
            + otrk(
                tlv("pfil", text(old + "\x00"))
                + tlv("tsng", text("Title"))
                + tlv("blob", unknown)
            )
        )
        rules = rewrite.normalize_rename_map({old: new})
        rewritten, changes = rewrite.rewrite_serato_bytes(source, "database V2", rules)

        self.assertEqual(len(changes), 1)
        outer = rewrite.parse_tlv_strict(rewritten, "result")
        self.assertEqual(dict(outer)["zzzz"], unknown)
        track = dict(rewrite.parse_tlv_strict(dict(outer)["otrk"], "track"))
        self.assertEqual(track["tsng"], text("Title"))
        self.assertEqual(track["blob"], unknown)
        self.assertEqual(track["pfil"], text(new + "\x00"))


class SeratoRewriteWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.serato = self.root / "_Serato_"
        (self.serato / "Subcrates").mkdir(parents=True)
        self.library = self.root / "Music"
        self.old_dir = self.library / "DNB"
        self.new_dir = self.library / "Drum & Bass"
        self.new_dir.mkdir(parents=True)
        self.new_track = self.new_dir / "Song.mp3"
        self.new_track.write_bytes(b"audio")
        self.old_stored = str(self.old_dir / "Song.mp3").lstrip("/")
        self.new_stored = str(self.new_track).lstrip("/")
        self.rules = rewrite.normalize_rename_map([
            {"from": str(self.old_dir), "to": str(self.new_dir)}
        ])
        database = (
            tlv("vrsn", text("2.0"))
            + otrk(tlv("pfil", text(self.old_stored)) + tlv("tsng", text("Song")))
            + otrk(tlv("pfil", text(str(self.library / "Keep.mp3").lstrip("/"))))
        )
        crate = (
            tlv("vrsn", text("1.0"))
            + otrk(tlv("ptrk", text(self.old_stored)) + tlv("brev", b"unchanged"))
        )
        (self.serato / "database V2").write_bytes(database)
        (self.serato / "Subcrates" / "Set.crate").write_bytes(crate)
        self.original_database = database
        self.original_crate = crate

    def write_plan(self) -> tuple[dict, Path]:
        plan = rewrite.build_preview(self.serato, self.rules)
        path = self.root / "plan.json"
        rewrite.write_json(path, plan)
        return plan, path

    def make_manifest(self) -> tuple[dict, Path]:
        backup = self.root / "serato-backup"
        shutil.copytree(self.serato, backup)
        manifest = backup_manifest.build_manifest(self.serato, backup)
        self.assertEqual(manifest["status"], "PASS", manifest["problems"])
        manifest_path = self.root / "backup-manifest.json"
        backup_manifest.write_manifest(manifest, manifest_path)
        return manifest, backup

    def test_preview_is_a_read_only_database_and_crate_diff(self):
        plan = rewrite.build_preview(self.serato, self.rules)
        self.assertEqual(plan["summary"]["files_changed"], 2)
        self.assertEqual(plan["summary"]["references_changed"], 2)
        self.assertEqual(plan["summary"]["database_references_changed"], 1)
        self.assertEqual(plan["summary"]["crate_references_changed"], 1)
        self.assertEqual(plan["files"][0]["rewrites"][0]["from"], self.old_stored)
        self.assertEqual(plan["files"][0]["rewrites"][0]["to"], self.new_stored)
        self.assertEqual((self.serato / "database V2").read_bytes(), self.original_database)
        self.assertEqual((self.serato / "Subcrates" / "Set.crate").read_bytes(), self.original_crate)

    def test_plan_validation_refuses_new_or_changed_crates(self):
        _, plan_path = self.write_plan()
        (self.serato / "Subcrates" / "Appeared.crate").write_bytes(tlv("vrsn", text("1.0")))
        with self.assertRaisesRegex(rewrite.SeratoRewriteError, "inventory changed"):
            rewrite.load_and_validate_plan(plan_path, self.serato)

    def test_plan_validation_refuses_missing_rewrite_targets(self):
        missing_rules = rewrite.normalize_rename_map([
            {"from": str(self.old_dir), "to": str(self.library / "Not There")}
        ])
        plan = rewrite.build_preview(self.serato, missing_rules)
        plan_path = self.root / "missing-plan.json"
        rewrite.write_json(plan_path, plan)
        with self.assertRaisesRegex(rewrite.SeratoRewriteError, "do not exist"):
            rewrite.load_and_validate_plan(
                plan_path, self.serato, require_targets=True
            )

    def test_apply_uses_backup_engine_and_standard_post_check(self):
        plan, plan_path = self.write_plan()
        validated = rewrite.load_and_validate_plan(
            plan_path, self.serato, require_targets=True
        )
        manifest, backup = self.make_manifest()
        heartbeat = FakeHeartbeat()
        journal = self.root / "recovery.jsonl"
        post_check = self.root / "post-check.json"
        result_path = self.root / "result.json"

        result = rewrite.apply_rewrite_plan(
            plan=validated,
            serato_dir=self.serato,
            library_root=self.library,
            manifest=manifest,
            heartbeat=heartbeat,
            journal_path=journal,
            post_check_path=post_check,
            result_path=result_path,
        )

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["files_changed"], 2)
        self.assertEqual(result["references_changed"], 2)
        self.assertGreaterEqual(heartbeat.beats, 2)
        self.assertEqual(checker.parse_database(self.serato / "database V2")[0]["path"], self.new_stored)
        self.assertEqual(checker.parse_crate(self.serato / "Subcrates" / "Set.crate"), [self.new_stored])
        self.assertEqual((backup / "database V2").read_bytes(), self.original_database)
        self.assertEqual((backup / "Subcrates" / "Set.crate").read_bytes(), self.original_crate)
        post = json.loads(post_check.read_text(encoding="utf-8"))
        self.assertEqual(post["rewrite_verification"]["status"], "PASS")
        self.assertEqual(post["rewrite_verification"]["unresolved_rewritten_targets"], [])
        self.assertTrue(mutation_engine.recover(journal)["clean"])
        self.assertEqual(json.loads(result_path.read_text(encoding="utf-8"))["status"], "PASS")

    def test_failed_per_file_target_check_restores_verified_backup(self):
        plan, _ = self.write_plan()
        manifest, _ = self.make_manifest()
        journal = self.root / "rollback-recovery.jsonl"
        nonexistent = self.root / "definitely-not-present"

        with mock.patch.object(checker, "resolve", return_value=nonexistent):
            with self.assertRaises(mutation_engine.MutationVerificationError):
                rewrite.apply_rewrite_plan(
                    plan=plan,
                    serato_dir=self.serato,
                    library_root=self.library,
                    manifest=manifest,
                    heartbeat=FakeHeartbeat(),
                    journal_path=journal,
                    post_check_path=self.root / "unused-post.json",
                    result_path=self.root / "unused-result.json",
                )

        self.assertEqual((self.serato / "database V2").read_bytes(), self.original_database)
        self.assertEqual((self.serato / "Subcrates" / "Set.crate").read_bytes(), self.original_crate)
        recovery = mutation_engine.recover(journal)
        self.assertTrue(recovery["clean"], recovery)

    def test_post_check_detects_non_path_serato_drift(self):
        plan, _ = self.write_plan()
        for item in plan["files"]:
            source = self.serato / item["path"]
            proposed, _ = rewrite.rewrite_serato_bytes(
                source.read_bytes(), item["path"], plan["renames"]
            )
            source.write_bytes(proposed)
        database = self.serato / "database V2"
        database.write_bytes(database.read_bytes().replace(text("2.0"), text("2.1"), 1))

        post = rewrite.run_post_check(self.serato, self.library, plan)
        self.assertEqual(post["source_integrity"]["status"], "FAIL")
        self.assertEqual(post["source_integrity"]["hash_mismatch"], ["database V2"])
        self.assertEqual(post["rewrite_verification"]["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
