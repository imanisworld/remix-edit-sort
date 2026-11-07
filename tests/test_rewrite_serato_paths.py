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
        self.assertEqual(plan["database_record_manifest"]["source_record_count"], 2)
        self.assertEqual(plan["database_record_manifest"]["proposed_record_count"], 2)
        self.assertEqual(
            [record["record_index"] for record in plan["database_record_manifest"]["proposed_records"]],
            [1, 2],
        )
        self.assertEqual((self.serato / "database V2").read_bytes(), self.original_database)
        self.assertEqual((self.serato / "Subcrates" / "Set.crate").read_bytes(), self.original_crate)

    def test_crates_only_preview_tolerates_untouched_database_alias_record(self):
        database = self.serato / "database V2"
        alias = "/" + str(self.library / "Alias.mp3").lstrip("/")
        database.write_bytes(
            database.read_bytes() + otrk(tlv("pfil", text(alias)))
        )

        plan = rewrite.build_preview(self.serato, self.rules, crates_only=True)

        self.assertTrue(plan["crates_only"])
        self.assertEqual(
            plan["database_record_manifest"]["source_records"][-1]["paths"][0]["path"],
            alias,
        )

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
        self.assertEqual(post["database_record_preservation"]["status"], "PASS")
        self.assertEqual(post["database_record_preservation"]["record_count"], 2)
        self.assertEqual(post["canonical_crate_occurrences"]["status"], "PASS")
        self.assertEqual(post["canonical_crate_occurrences"]["verified_count"], 1)
        self.assertTrue(mutation_engine.recover(journal)["clean"])
        self.assertEqual(json.loads(result_path.read_text(encoding="utf-8"))["status"], "PASS")

    def test_failed_per_file_target_check_restores_verified_backup(self):
        plan, _ = self.write_plan()
        manifest, _ = self.make_manifest()
        journal = self.root / "rollback-recovery.jsonl"
        nonexistent = self.root / "definitely-not-present"

        with mock.patch.object(checker, "resolve", return_value=nonexistent):
            with self.assertRaises(rewrite.SeratoRewriteError):
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

    def test_post_check_exception_is_written_before_rollback(self):
        plan, _ = self.write_plan()
        manifest, _ = self.make_manifest()
        post_check = self.root / "failed-post-check.json"
        result_path = self.root / "failed-result.json"

        with mock.patch.object(
            rewrite,
            "run_post_check",
            side_effect=SystemExit("synthetic post-check failure"),
        ):
            with self.assertRaises(rewrite.SeratoRewriteError):
                rewrite.apply_rewrite_plan(
                    plan=plan,
                    serato_dir=self.serato,
                    library_root=self.library,
                    manifest=manifest,
                    heartbeat=FakeHeartbeat(),
                    journal_path=self.root / "post-failure-recovery.jsonl",
                    post_check_path=post_check,
                    result_path=result_path,
                )

        evidence = json.loads(post_check.read_text(encoding="utf-8"))
        self.assertEqual(evidence["status"], "FAIL")
        self.assertIn("synthetic post-check failure", evidence["error"])
        self.assertEqual((self.serato / "database V2").read_bytes(), self.original_database)
        self.assertEqual((self.serato / "Subcrates" / "Set.crate").read_bytes(), self.original_crate)

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

    def test_post_check_detects_collapsed_database_record(self):
        plan, _ = self.write_plan()
        for item in plan["files"]:
            source = self.serato / item["path"]
            proposed, _ = rewrite.rewrite_serato_bytes(
                source.read_bytes(), item["path"], plan["renames"]
            )
            source.write_bytes(proposed)
        database = self.serato / "database V2"
        records = rewrite.parse_tlv_strict(database.read_bytes(), "database V2")
        database.write_bytes(
            b"".join(rewrite.encode_tlv(tag, value) for tag, value in records[:-1])
        )

        post = rewrite.run_post_check(self.serato, self.library, plan)

        preservation = post["database_record_preservation"]
        self.assertEqual(preservation["status"], "FAIL")
        self.assertEqual(preservation["expected_record_count"], 2)
        self.assertEqual(preservation["record_count"], 1)
        self.assertEqual(post["rewrite_verification"]["status"], "FAIL")

    def test_post_check_rejects_noncanonical_pointer_left_in_changed_crate(self):
        crate_path = self.serato / "Subcrates" / "Set.crate"
        alias = "/" + self.new_stored
        plan = rewrite.build_preview(self.serato, self.rules)
        for item in plan["files"]:
            source = self.serato / item["path"]
            proposed, _ = rewrite.rewrite_serato_bytes(
                source.read_bytes(), item["path"], plan["renames"]
            )
            source.write_bytes(proposed)
        crate_path.write_bytes(
            crate_path.read_bytes() + otrk(tlv("ptrk", text(alias)))
        )

        post = rewrite.run_post_check(self.serato, self.library, plan)

        crate_check = post["canonical_crate_occurrences"]
        self.assertEqual(crate_check["status"], "FAIL")
        self.assertEqual(len(crate_check["noncanonical_occurrences"]), 1)
        self.assertEqual(
            crate_check["noncanonical_occurrences"][0]["path"],
            alias,
        )
        self.assertEqual(post["rewrite_verification"]["status"], "FAIL")


class RawPtrkRewriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.serato = self.root / "_Serato_"
        (self.serato / "Subcrates").mkdir(parents=True)
        self.alias = "/Users/dj/Music/Ghetto Supastar.mp3"
        self.canonical = self.alias[1:]
        (self.serato / "database V2").write_bytes(
            tlv("vrsn", text("2.0"))
            + otrk(tlv("pfil", text(self.alias)))
            + otrk(tlv("pfil", text(self.canonical)))
        )
        self.crate = self.serato / "Subcrates" / "fIX BITRATE.crate"
        self.original_crate = tlv("vrsn", text("1.0")) + b"".join(
            otrk(tlv("ptrk", text(self.alias))) for _ in range(412)
        )
        self.crate.write_bytes(self.original_crate)
        self.spec = {
            "rewrites": [
                {
                    "crate": "Subcrates/fIX BITRATE.crate",
                    "record_index": 289,
                    "outer_record_index": 289,
                    "field_index": 0,
                    "from": self.alias,
                    "to": self.canonical,
                }
            ]
        }

    def make_manifest(self):
        backup = self.root / "backup"
        shutil.copytree(self.serato, backup)
        manifest = backup_manifest.build_manifest(self.serato, backup)
        self.assertEqual(manifest["status"], "PASS", manifest["problems"])
        return manifest, backup

    def make_explicit_manifest(self):
        backup_root = self.root / "explicit-backup-root"
        backup_root.mkdir()
        concrete = self.root / "deduped-copy.crate"
        concrete.write_bytes(self.original_crate)
        manifest = backup_manifest.build_explicit_paths_manifest(
            self.serato,
            backup_root,
            [
                {
                    "path": "Subcrates/fIX BITRATE.crate",
                    "backup_path": str(concrete),
                    "backup_mode": "existing-hash-copy",
                }
            ],
        )
        self.assertEqual(manifest["status"], "PASS", manifest["problems"])
        return manifest, concrete

    def test_preview_rewrites_one_of_412_duplicates_without_collapsing(self):
        plan = rewrite.build_raw_ptrk_preview(self.serato, self.spec)

        self.assertEqual(plan["summary"]["crates_changed"], 1)
        self.assertEqual(plan["summary"]["occurrences_changed"], 1)
        item = plan["files"][0]
        self.assertEqual(len(item["source_occurrences"]), 412)
        self.assertEqual(len(item["proposed_occurrences"]), 412)
        self.assertEqual(
            item["proposed_occurrences"][288]["path"], self.canonical
        )
        self.assertEqual(
            sum(
                occurrence["path"] == self.alias
                for occurrence in item["proposed_occurrences"]
            ),
            411,
        )
        change = item["rewrites"][0]
        self.assertEqual(change["from"], self.alias)
        self.assertEqual(change["to"], self.canonical)
        self.assertNotEqual(change["old_ptrk_tlv_hex"], change["new_ptrk_tlv_hex"])
        self.assertEqual(self.crate.read_bytes(), self.original_crate)

    def test_apply_uses_one_engine_item_and_preserves_database(self):
        plan = rewrite.build_raw_ptrk_preview(self.serato, self.spec)
        manifest, backup = self.make_manifest()
        heartbeat = FakeHeartbeat()
        database_before = (self.serato / "database V2").read_bytes()

        result = rewrite.apply_raw_ptrk_plan(
            plan=plan,
            serato_dir=self.serato,
            manifest=manifest,
            heartbeat=heartbeat,
            journal_path=self.root / "raw-journal.jsonl",
            post_check_path=self.root / "raw-post-check.json",
            result_path=self.root / "raw-result.json",
        )

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["crates_changed"], 1)
        self.assertEqual(result["occurrences_changed"], 1)
        self.assertEqual(heartbeat.beats, 1)
        occurrences = checker.parse_crate_occurrences(self.crate)
        self.assertEqual(len(occurrences), 412)
        self.assertEqual(
            sum(item["path"] == self.canonical for item in occurrences), 1
        )
        self.assertEqual(
            (self.serato / "database V2").read_bytes(), database_before
        )
        self.assertEqual(
            (backup / "Subcrates" / "fIX BITRATE.crate").read_bytes(),
            self.original_crate,
        )

    def test_post_check_failure_rolls_crate_back(self):
        plan = rewrite.build_raw_ptrk_preview(self.serato, self.spec)
        manifest, concrete = self.make_explicit_manifest()

        with mock.patch.object(
            rewrite,
            "raw_ptrk_post_check",
            side_effect=rewrite.SeratoRewriteError("synthetic raw failure"),
        ):
            with self.assertRaises(rewrite.SeratoRewriteError):
                rewrite.apply_raw_ptrk_plan(
                    plan=plan,
                    serato_dir=self.serato,
                    manifest=manifest,
                    heartbeat=FakeHeartbeat(),
                    journal_path=self.root / "rollback-journal.jsonl",
                    post_check_path=self.root / "rollback-post-check.json",
                    result_path=self.root / "rollback-result.json",
                )

        self.assertEqual(self.crate.read_bytes(), self.original_crate)
        self.assertEqual(concrete.read_bytes(), self.original_crate)
        result = json.loads(
            (self.root / "rollback-result.json").read_text(encoding="utf-8")
        )
        self.assertEqual(result["status"], "ROLLED_BACK")

    def test_item_rollback_failure_is_reported_partial(self):
        plan = rewrite.build_raw_ptrk_preview(self.serato, self.spec)
        manifest, _ = self.make_manifest()
        source_occurrences = plan["files"][0]["source_occurrences"]
        expected_occurrences = plan["files"][0]["proposed_occurrences"]

        with (
            mock.patch.object(
                rewrite,
                "path_occurrence_manifest",
                side_effect=[
                    source_occurrences,
                    expected_occurrences,
                    expected_occurrences,
                    RuntimeError("synthetic verification failure"),
                ],
            ),
            mock.patch.object(
                mutation_engine,
                "restore_from_backup",
                side_effect=OSError("synthetic restore failure"),
            ),
        ):
            with self.assertRaisesRegex(
                rewrite.SeratoRewriteError, "PARTIAL"
            ):
                rewrite.apply_raw_ptrk_plan(
                    plan=plan,
                    serato_dir=self.serato,
                    manifest=manifest,
                    heartbeat=FakeHeartbeat(),
                    journal_path=self.root / "partial-journal.jsonl",
                    post_check_path=self.root / "partial-post-check.json",
                    result_path=self.root / "partial-result.json",
                )

        result = json.loads(
            (self.root / "partial-result.json").read_text(encoding="utf-8")
        )
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(
            result["crates_left_rewritten"],
            ["Subcrates/fIX BITRATE.crate"],
        )

    def test_completion_journal_failure_is_reported_partial(self):
        plan = rewrite.build_raw_ptrk_preview(self.serato, self.spec)
        manifest, _ = self.make_manifest()
        original_append = mutation_engine.RecoveryJournal.append

        def fail_completion(journal, record):
            if record.get("type") == "completed":
                raise OSError("synthetic completion WAL failure")
            return original_append(journal, record)

        with mock.patch.object(
            mutation_engine.RecoveryJournal,
            "append",
            autospec=True,
            side_effect=fail_completion,
        ):
            with self.assertRaisesRegex(
                rewrite.SeratoRewriteError, "PARTIAL"
            ):
                rewrite.apply_raw_ptrk_plan(
                    plan=plan,
                    serato_dir=self.serato,
                    manifest=manifest,
                    heartbeat=FakeHeartbeat(),
                    journal_path=self.root / "wal-failure-journal.jsonl",
                    post_check_path=self.root / "wal-failure-post.json",
                    result_path=self.root / "wal-failure-result.json",
                )

        result = json.loads(
            (self.root / "wal-failure-result.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(
            result["crates_left_rewritten"],
            ["Subcrates/fIX BITRATE.crate"],
        )

    def test_noop_and_non_leading_slash_only_changes_are_refused(self):
        for old, new in (
            (self.canonical, self.canonical),
            (self.canonical, "Users/dj/Music/Other.mp3"),
            (self.alias, "Users/dj/Music/Other.mp3"),
            ("//" + self.canonical, self.canonical),
        ):
            spec = {
                "rewrites": [
                    {
                        **self.spec["rewrites"][0],
                        "from": old,
                        "to": new,
                    }
                ]
            }
            with self.subTest(old=old, new=new):
                with self.assertRaisesRegex(
                    rewrite.SeratoRewriteError, "one leading slash"
                ):
                    rewrite.normalize_raw_ptrk_spec(spec)

    def test_occurrence_mismatch_and_hash_drift_are_refused(self):
        mismatched = json.loads(json.dumps(self.spec))
        mismatched["rewrites"][0]["record_index"] = 288
        with self.assertRaisesRegex(
            rewrite.SeratoRewriteError, "do not match"
        ):
            rewrite.build_raw_ptrk_preview(self.serato, mismatched)

        plan = rewrite.build_raw_ptrk_preview(self.serato, self.spec)
        self.crate.write_bytes(self.original_crate + tlv("vrsn", text("drift")))
        manifest, _ = self.make_manifest()
        with self.assertRaisesRegex(
            rewrite.SeratoRewriteError, "changed since preview"
        ):
            rewrite.apply_raw_ptrk_plan(
                plan=plan,
                serato_dir=self.serato,
                manifest=manifest,
                heartbeat=FakeHeartbeat(),
                journal_path=self.root / "drift-journal.jsonl",
                post_check_path=self.root / "drift-post-check.json",
                result_path=self.root / "drift-result.json",
            )

    def test_serialized_plan_tampering_is_refused(self):
        plan = rewrite.build_raw_ptrk_preview(self.serato, self.spec)
        plan_path = self.root / "raw-plan.json"
        rewrite.write_json(plan_path, plan)
        rewrite.load_and_validate_raw_ptrk_plan(plan_path, self.serato)

        variants = []
        missing_file = json.loads(json.dumps(plan))
        missing_file["files"] = []
        variants.append(missing_file)
        changed_tlv = json.loads(json.dumps(plan))
        changed_tlv["files"][0]["rewrites"][0]["old_ptrk_tlv_hex"] = "00"
        variants.append(changed_tlv)
        changed_summary = json.loads(json.dumps(plan))
        changed_summary["summary"]["occurrences_changed"] = 2
        variants.append(changed_summary)
        missing_rewrite = json.loads(json.dumps(plan))
        missing_rewrite["rewrites"] = []
        variants.append(missing_rewrite)

        for index, variant in enumerate(variants):
            with self.subTest(index=index):
                rewrite.write_json(plan_path, variant)
                with self.assertRaises(rewrite.SeratoRewriteError):
                    rewrite.load_and_validate_raw_ptrk_plan(
                        plan_path, self.serato
                    )

    def test_apply_boundary_refuses_approval_field_tampering(self):
        plan = rewrite.build_raw_ptrk_preview(self.serato, self.spec)
        manifest, _ = self.make_manifest()
        for field, value in (
            ("mode", "apply"),
            ("approval_required", False),
        ):
            tampered = json.loads(json.dumps(plan))
            tampered[field] = value
            with self.subTest(field=field):
                with self.assertRaisesRegex(
                    rewrite.SeratoRewriteError, "type/version/mode"
                ):
                    rewrite.apply_raw_ptrk_plan(
                        plan=tampered,
                        serato_dir=self.serato,
                        manifest=manifest,
                        heartbeat=FakeHeartbeat(),
                        journal_path=self.root / f"{field}-journal.jsonl",
                        post_check_path=self.root / f"{field}-post.json",
                        result_path=self.root / f"{field}-result.json",
                    )


class SeratoClosedGuardTests(unittest.TestCase):
    def test_only_a_serato_app_bundle_executable_counts_as_running(self):
        listing = [
            "/bin/zsh",
            "/usr/bin/python3",
            "/Applications/Serato DJ Pro.app/Contents/MacOS/Serato DJ Pro",
            "/Applications/Serato Studio 2.app/Contents/MacOS/Serato Studio",
            "/Users/dj/Music/_Serato_/not-an-app",
        ]
        running = rewrite.find_serato_processes(lambda: listing)
        self.assertEqual(len(running), 2)
        rewrite.require_serato_closed(lambda: listing[:2] + listing[4:])
        with self.assertRaisesRegex(rewrite.SeratoRewriteError, "Serato is running"):
            rewrite.require_serato_closed(lambda: listing)

    def test_an_unreadable_process_table_fails_closed(self):
        with mock.patch.object(rewrite.subprocess, "run", side_effect=OSError("no ps")):
            with self.assertRaisesRegex(rewrite.SeratoRewriteError, "cannot verify"):
                rewrite.require_serato_closed()

    def test_this_process_does_not_look_like_serato(self):
        # The live process table of whatever machine runs the tests: a repo
        # path or script name containing "serato" must never be mistaken for the app.
        self.assertEqual(
            [c for c in rewrite.find_serato_processes() if "rewrite_serato" in c or "python" in c.lower()],
            [],
        )


class LiteralTargetDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / "Beats").mkdir()
        (self.root / "Beats" / "x.mp3").write_bytes(b"x")

    def rules(self, target):
        return rewrite.normalize_rename_map([{"from": "Users/dj/Old", "to": str(target).lstrip("/")}])

    def test_exactly_spelled_directory_passes(self):
        self.assertEqual(rewrite.nonliteral_rule_target_directories(self.rules(self.root / "Beats"), {1}), [])

    def test_case_alias_of_a_directory_is_flagged_on_case_insensitive_volumes(self):
        if not (self.root / "beats").exists():
            self.skipTest("case-sensitive volume: an alias cannot exist")
        flagged = rewrite.nonliteral_rule_target_directories(self.rules(self.root / "beats"), {1})
        self.assertEqual(len(flagged), 1)

    def test_unused_rules_and_file_targets_are_ignored(self):
        self.assertEqual(rewrite.nonliteral_rule_target_directories(self.rules(self.root / "beats"), set()), [])
        self.assertEqual(
            rewrite.nonliteral_rule_target_directories(self.rules(self.root / "Beats" / "x.mp3"), {1}), []
        )

    def test_post_check_and_apply_gate_flag_a_pending_case_only_folder_rename(self):
        if not (self.root / "beats").exists():
            self.skipTest("case-sensitive volume: an alias cannot exist")
        serato = self.root / "_Serato_"
        (serato / "Subcrates").mkdir(parents=True)
        old = "Users/dj/Old/x.mp3"
        alias = str(self.root / "beats" / "x.mp3").lstrip("/")
        (serato / "database V2").write_bytes(
            tlv("vrsn", text("2.0")) + otrk(tlv("pfil", text(old)))
        )
        rules = rewrite.normalize_rename_map([{"from": "Users/dj/Old", "to": str(self.root / "beats").lstrip("/")}])
        plan = rewrite.build_preview(serato, rules)
        plan_path = self.root / "plan.json"
        rewrite.write_json(plan_path, plan)
        self.assertEqual(plan["files"][0]["rewrites"][0]["to"], alias)
        # The file is reachable through the alias, so the per-file check alone passes ...
        self.assertTrue((self.root / "beats" / "x.mp3").is_file())
        # ... but the rule's destination folder is not the literal spelling on disk.
        with self.assertRaisesRegex(rewrite.SeratoRewriteError, "not spelled exactly"):
            rewrite.load_and_validate_plan(plan_path, serato, require_targets=True)


if __name__ == "__main__":
    unittest.main()
