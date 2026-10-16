import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backup_manifest  # noqa: E402
import check_serato_crate_paths as checker  # noqa: E402
import compose_serato_rename_map as compose  # noqa: E402
import mutation_engine  # noqa: E402
import rewrite_serato_paths as rewrite  # noqa: E402
from test_rewrite_serato_paths import FakeHeartbeat, otrk, text, tlv  # noqa: E402

RULE_BYTES = tlv("rurt", tlv("tvcn", text("genre")) + tlv("trft", text("Afro")))


class SmartCrateRepointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.serato = self.root / "_Serato_"
        (self.serato / "Subcrates" / "Serato Stems").mkdir(parents=True)
        (self.serato / "SmartCrates").mkdir(parents=True)
        self.library = self.root / "Music"
        self.old_dir = self.library / "Afrobeat"
        self.new_dir = self.library / "Afrobeats"
        self.new_dir.mkdir(parents=True)
        (self.new_dir / "Song.mp3").write_bytes(b"audio")
        self.old = str(self.old_dir / "Song.mp3").lstrip("/")
        self.new = str(self.new_dir / "Song.mp3").lstrip("/")
        self.rules = rewrite.normalize_rename_map([{"from": str(self.old_dir), "to": str(self.new_dir)}])
        self.database = tlv("vrsn", text("2.0")) + otrk(tlv("pfil", text(self.new)))
        self.scrate = (
            tlv("vrsn", text("1.0")) + RULE_BYTES + tlv("rlut", text("1"))
            + otrk(tlv("ptrk", text(self.old)))
        )
        self.stems = tlv("vrsn", text("1.0")) + otrk(tlv("ptrk", text(self.old)))
        (self.serato / "database V2").write_bytes(self.database)
        (self.serato / "SmartCrates" / "Afro.scrate").write_bytes(self.scrate)
        (self.serato / "Subcrates" / "Serato Stems" / "Stems.crate").write_bytes(self.stems)

    def manifest(self):
        backup = self.root / "backup"
        shutil.copytree(self.serato, backup)
        manifest = backup_manifest.build_manifest(self.serato, backup)
        self.assertEqual(manifest["status"], "PASS", manifest["problems"])
        return manifest, backup

    def apply(self, plan, manifest, journal):
        return rewrite.apply_rewrite_plan(
            plan=plan, serato_dir=self.serato, library_root=self.library, manifest=manifest,
            heartbeat=FakeHeartbeat(), journal_path=journal,
            post_check_path=self.root / "post.json", result_path=self.root / "result.json",
        )

    def test_sources_include_smart_crates_and_nested_crates(self):
        relatives = [str(p.relative_to(self.serato)) for p in rewrite.serato_source_files(self.serato)]
        self.assertIn("SmartCrates/Afro.scrate", relatives)
        self.assertIn("Subcrates/Serato Stems/Stems.crate", relatives)

    def test_preview_counts_smart_crate_refs_and_keeps_rule_bytes(self):
        plan = rewrite.build_preview(self.serato, self.rules)
        self.assertEqual(plan["summary"]["smart_crate_references_changed"], 1)
        self.assertEqual(plan["summary"]["crate_references_changed"], 1)
        rewritten, _ = rewrite.rewrite_serato_bytes(self.scrate, "SmartCrates/Afro.scrate", self.rules)
        self.assertIn(RULE_BYTES, rewritten)
        self.assertEqual((self.serato / "SmartCrates" / "Afro.scrate").read_bytes(), self.scrate)

    def test_apply_repoints_smart_crate_with_backup_and_post_check(self):
        plan = rewrite.build_preview(self.serato, self.rules)
        manifest, backup = self.manifest()
        journal = self.root / "j.jsonl"
        result = self.apply(plan, manifest, journal)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(checker.parse_crate(self.serato / "SmartCrates" / "Afro.scrate"), [self.new])
        self.assertEqual(checker.parse_crate(self.serato / "Subcrates" / "Serato Stems" / "Stems.crate"), [self.new])
        self.assertEqual((backup / "SmartCrates" / "Afro.scrate").read_bytes(), self.scrate)
        self.assertTrue(mutation_engine.recover(journal)["clean"])

    def test_failed_apply_restores_smart_crate(self):
        plan = rewrite.build_preview(self.serato, self.rules)
        manifest, _ = self.manifest()
        with mock.patch.object(checker, "resolve", return_value=self.root / "nope"):
            with self.assertRaises(mutation_engine.MutationVerificationError):
                self.apply(plan, manifest, self.root / "j2.jsonl")
        self.assertEqual((self.serato / "SmartCrates" / "Afro.scrate").read_bytes(), self.scrate)
        self.assertEqual((self.serato / "Subcrates" / "Serato Stems" / "Stems.crate").read_bytes(), self.stems)

    def test_database_ghost_collision_is_refused_and_crates_only_works(self):
        ghost_db = self.database + otrk(tlv("pfil", text(self.old)))
        (self.serato / "database V2").write_bytes(ghost_db)
        with self.assertRaisesRegex(rewrite.SeratoRewriteError, "already have a record"):
            rewrite.build_preview(self.serato, self.rules)
        plan = rewrite.build_preview(self.serato, self.rules, crates_only=True)
        self.assertEqual(plan["summary"]["database_references_changed"], 0)
        path = self.root / "plan.json"
        rewrite.write_json(path, plan)
        validated = rewrite.load_and_validate_plan(path, self.serato, require_targets=True)
        manifest, _ = self.manifest()
        result = self.apply(validated, manifest, self.root / "j3.jsonl")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual((self.serato / "database V2").read_bytes(), ghost_db)

    def test_compose_chains_every_move_and_reports_unmappable_and_ghosts(self):
        inner = self.library / "Afrobeat" / "Afrobeat"
        (self.new_dir / "DIRTY" / "Afrobeats").mkdir(parents=True)
        (self.new_dir / "DIRTY" / "Afrobeats" / "Hot.mp3").write_bytes(b"a")
        deep_old = str(inner / "Hot.mp3").lstrip("/")
        lost = str(inner / "Gone.mp3").lstrip("/")
        (self.serato / "SmartCrates" / "All.scrate").write_bytes(
            tlv("vrsn", text("1.0")) + otrk(tlv("ptrk", text(deep_old))) + otrk(tlv("ptrk", text(lost)))
        )
        (self.serato / "database V2").write_bytes(self.database + otrk(tlv("pfil", text(self.old))))
        maps = [
            rewrite.normalize_rename_map([{"from": str(inner), "to": str(self.old_dir / "Afrobeats")}]),
            rewrite.normalize_rename_map([{"from": str(self.old_dir), "to": str(self.new_dir)}]),
            rewrite.normalize_rename_map([{
                "from": str(self.new_dir / "Afrobeats" / "Hot.mp3"),
                "to": str(self.new_dir / "DIRTY" / "Afrobeats" / "Hot.mp3"),
            }]),
        ]
        result = compose.compose(self.serato, maps)
        mapping = {item["from"].lstrip("/"): item["to"].lstrip("/") for item in result["map"]}
        self.assertEqual(mapping[deep_old], str(self.new_dir / "DIRTY" / "Afrobeats" / "Hot.mp3").lstrip("/"))
        self.assertEqual(mapping[self.old], self.new)
        self.assertEqual([u["from"] for u in result["report"]["unmappable"]], [lost])
        self.assertEqual(result["report"]["database_ghosts_with_live_twin"], [self.old])
        rules = rewrite.normalize_rename_map(result["map"])
        plan = rewrite.build_preview(self.serato, rules, crates_only=True)
        self.assertEqual(plan["summary"]["smart_crate_references_changed"], 2)

    def test_backslash_file_name_is_repointed_when_name_is_unchanged(self):
        odd = str(self.old_dir / "\\Kupe.mp3").lstrip("/")
        (self.new_dir / "\\Kupe.mp3").write_bytes(b"a")
        (self.serato / "SmartCrates" / "Odd.scrate").write_bytes(tlv("vrsn", text("1.0")) + otrk(tlv("ptrk", text(odd))))
        maps = [rewrite.normalize_rename_map([{"from": str(self.old_dir), "to": str(self.new_dir)}])]
        result = compose.compose(self.serato, maps)
        self.assertIn("/" + odd, [m["from"] for m in result["map"]])
        self.assertEqual(result["report"]["unmappable"], [])
        rules = rewrite.normalize_rename_map(result["map"])
        plan = rewrite.build_preview(self.serato, rules, crates_only=True)
        self.assertEqual(plan["summary"]["smart_crate_references_changed"], 2)

    def test_backslash_as_folder_separator_or_renamed_file_is_refused(self):
        with self.assertRaisesRegex(rewrite.SeratoRewriteError, "backslash"):
            rewrite.normalize_rename_map([{"from": "/Users/a\\b/x.mp3", "to": "/Users/c/x.mp3"}])
        with self.assertRaisesRegex(rewrite.SeratoRewriteError, "backslash"):
            rewrite.normalize_rename_map([{"from": "/Users/a/\\x.mp3", "to": "/Users/c/y.mp3"}])

    def test_checker_audits_smart_crates(self):
        report = checker.audit(self.serato, self.library)
        crates = {c for cat in report.values() if isinstance(cat, list) for e in cat for c in e.get("crates", [])}
        self.assertIn("smart:Afro", crates)
        self.assertIn("Serato Stems/Stems", crates)


if __name__ == "__main__":
    unittest.main()
