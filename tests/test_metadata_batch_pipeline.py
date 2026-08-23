"""Regression tests for the durable metadata-batch refusal gates.

All media is synthetic and all writable roots are TemporaryDirectory paths.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
import unicodedata
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import apply_approved_batch  # noqa: E402
import backup_manifest  # noqa: E402
import folder_lock  # noqa: E402
import lock_guard  # noqa: E402
import metadata_batch  # noqa: E402
import mutation_engine  # noqa: E402
import prepare_metadata_batch  # noqa: E402
from fixtures import make_library  # noqa: E402


class StateMachineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.handoff = Path(self.temp.name) / "handoff"
        self.handoff.mkdir()
        self.state_path = self.handoff / "current-batch.json"
        self.batch_dir = self.handoff / "batches" / "b1"
        self.batch_dir.mkdir(parents=True)

    def test_exact_forward_state_machine(self):
        metadata_batch.start_batch(self.state_path, "b1", self.batch_dir,
                                   handoff_root=self.handoff)
        for state in (
            "PREVIEWED", "HUMAN_APPROVED", "PLANNED", "PREFLIGHT_PASS",
            "BACKED_UP", "APPLIED", "VERIFIED", "SERATO_SPOTCHECK", "COMPLETE",
        ):
            metadata_batch.transition(self.state_path, state, handoff_root=self.handoff)
        final = metadata_batch.read_json(self.state_path)
        self.assertEqual(final["state"], "COMPLETE")
        self.assertEqual([entry["state"] for entry in final["history"]], [
            "DISCOVERED", "PREVIEWED", "HUMAN_APPROVED", "PLANNED",
            "PREFLIGHT_PASS", "BACKED_UP", "APPLIED", "VERIFIED",
            "SERATO_SPOTCHECK", "COMPLETE",
        ])

    def test_skipping_a_gate_is_refused(self):
        metadata_batch.start_batch(self.state_path, "b1", self.batch_dir,
                                   handoff_root=self.handoff)
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "invalid batch transition"):
            metadata_batch.transition(self.state_path, "HUMAN_APPROVED",
                                      handoff_root=self.handoff)

    def test_failure_transitions_to_blocked_and_cannot_start_over(self):
        metadata_batch.start_batch(self.state_path, "b1", self.batch_dir,
                                   handoff_root=self.handoff)
        metadata_batch.transition(self.state_path, "BLOCKED", reason="test failure",
                                  handoff_root=self.handoff)
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "is BLOCKED"):
            metadata_batch.start_batch(self.state_path, "b2", self.handoff / "b2",
                                       handoff_root=self.handoff)

    def test_rollback_failed_is_distinct_from_blocked(self):
        metadata_batch.start_batch(self.state_path, "b1", self.batch_dir,
                                   handoff_root=self.handoff)
        metadata_batch.transition(self.state_path, "PREVIEWED", handoff_root=self.handoff)
        metadata_batch.transition(
            self.state_path, "ROLLBACK_FAILED", reason="restore failed",
            handoff_root=self.handoff,
        )
        self.assertEqual(metadata_batch.read_json(self.state_path)["state"], "ROLLBACK_FAILED")

    def test_owned_writes_are_path_confined(self):
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "must resolve under"):
            metadata_batch.atomic_write_json(
                Path(self.temp.name) / "outside.json", {"x": 1}, handoff_root=self.handoff
            )


class BackupConfinementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "library" / "scope"
        self.source.mkdir(parents=True)
        (self.source / "track.bin").write_bytes(b"synthetic")
        self.t5 = self.root / "T5"
        self.t5.mkdir()

    def test_full_backup_is_verified_and_never_overwritten(self):
        destination = self.t5 / "Backups" / "b1" / "S001" / "source"
        manifest_path = destination.parent / "backup-manifest.json"
        manifest = metadata_batch.create_verified_backup(
            self.source, destination, manifest_path, t5_volume=self.t5
        )
        self.assertEqual(manifest["status"], "PASS")
        self.assertEqual((destination / "track.bin").read_bytes(), b"synthetic")
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "already exists"):
            metadata_batch.create_verified_backup(
                self.source, destination, manifest_path, t5_volume=self.t5
            )

    def test_backup_outside_t5_is_refused_before_copy(self):
        outside = self.root / "outside" / "source"
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "must resolve under"):
            metadata_batch.create_verified_backup(
                self.source, outside, outside.parent / "manifest.json", t5_volume=self.t5
            )
        self.assertFalse(outside.exists())

    def test_symlinked_scope_is_refused(self):
        real = self.root / "real.bin"
        real.write_bytes(b"real")
        (self.source / "linked.bin").symlink_to(real)
        destination = self.t5 / "Backups" / "b1" / "source"
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "contains symlinks"):
            metadata_batch.create_verified_backup(
                self.source, destination, destination.parent / "manifest.json",
                t5_volume=self.t5,
            )
        self.assertFalse(destination.exists())

    def test_corrupted_backup_after_manifest_is_refused_by_engine(self):
        destination = self.t5 / "Backups" / "b1" / "source"
        manifest_path = destination.parent / "manifest.json"
        metadata_batch.create_verified_backup(
            self.source, destination, manifest_path, t5_volume=self.t5
        )
        manifest = backup_manifest.require_pass(manifest_path, self.source)
        (destination / "track.bin").write_bytes(b"corrupted after PASS")
        journal = self.root / "recovery.jsonl"
        engine = mutation_engine.MutationEngine(
            journal, heartbeat=None, manifest=manifest,
            source_root=self.source, label="corrupt-backup-test",
        )
        engine.start({"items": 1})
        with self.assertRaisesRegex(mutation_engine.MutationRefused, "backup copy"):
            with engine.item(
                1, kind=mutation_engine.METADATA,
                source=self.source / "track.bin",
                expected_sha256=metadata_batch.sha256_file(self.source / "track.bin"),
            ):
                self.fail("mutation body must never be entered")


class PreparationEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = self.root / "Music"
        self.serato = self.root / "_Serato_"
        (self.serato / "SmartCrates").mkdir(parents=True)

    def test_mp3_v23_selected_v22_and_ambiguous_held(self):
        make_library(self.library, [
            {"relative": "House/_GENRE NEEDS CHECK/good.mp3", "genre": "house", "v2_version": 3},
            {"relative": "House/_GENRE NEEDS CHECK/old.mp3", "genre": "house", "v2_version": 2},
            {"relative": "House/_GENRE NEEDS CHECK/ambiguous.mp3", "genre": "house",
             "title": "My Radio Edit", "v2_version": 3},
        ])
        rows, excluded = prepare_metadata_batch.discover(self.library, self.serato, 150)
        self.assertEqual([Path(row["path"]).name for row in rows], ["good.mp3"])
        self.assertEqual(rows[0]["proposed_genre"], "House")
        self.assertEqual(excluded["ID3v2.2"], 1)
        self.assertEqual(excluded["Radio Edit"], 1)

    def test_new_grouping_uses_logical_order(self):
        make_library(self.library, [{
            "relative": "R&B/_GENRE NEEDS CHECK/Song (Clean Acapella).mp3",
            "genre": "r&b", "title": "Song (Clean Acapella)", "v2_version": 4,
        }])
        rows, _ = prepare_metadata_batch.discover(self.library, self.serato, 150)
        self.assertEqual(rows[0]["proposed_grouping"], "Clean; Acapella")
        self.assertEqual(rows[0]["grouping_add_terms"], ["Clean", "Acapella"])

    def test_smart_crate_genre_and_grouping_rules_are_parsed(self):
        def tlv(key, payload):
            return key.encode("ascii") + len(payload).to_bytes(4, "big") + payload

        def text(value):
            return value.encode("utf-16-be")

        def rule(field_id, value):
            return tlv("rurt", b"".join((
                tlv("trft", text("cond_con_str")),
                tlv("urkt", field_id.to_bytes(4, "big")),
                tlv("trpt", text(value)),
            )))

        payload = b"".join((
            tlv("rart", tlv("brut", b"\x01")),
            rule(9, "house"), rule(19, "edit"),
        ))
        (self.serato / "SmartCrates" / "House edit.scrate").write_bytes(payload)
        rules = prepare_metadata_batch.parse_smart_crates(self.serato)
        self.assertEqual(
            [(item["field"], item["operator"], item["value"], item["match_all"]) for item in rules],
            [("Genre", "contains", "house", True), ("Grouping", "contains", "edit", True)],
        )

    def test_full_prepare_writes_durable_validated_artifacts_only_under_handoff(self):
        make_library(self.library, [{
            "relative": "House/_GENRE NEEDS CHECK/good.mp3",
            "genre": "house", "title": "Good", "v2_version": 3,
        }])
        handoff = self.root / "repo-handoff"
        handoff.mkdir()
        master = prepare_metadata_batch.prepare(
            self.library, self.serato, handoff, 150, "mp3"
        )
        self.assertEqual(master["candidate_count"], 1)
        self.assertEqual(master["master_sha"], metadata_batch.canonical_sha(master))
        state = metadata_batch.read_json(handoff / "current-batch.json")
        self.assertEqual(state["state"], "PREVIEWED")
        self.assertTrue(Path(master["preview"]["md_path"]).is_file())
        self.assertTrue(Path(master["preview"]["csv_path"]).is_file())
        self.assertTrue(Path(master["scopes"][0]["plan_path"]).is_file())
        self.assertFalse((self.root / "Backups").exists())


class ApprovalAndPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.handoff = self.root / "handoff"
        self.batch_dir = self.handoff / "batches" / "b1"
        self.plans = self.batch_dir / "plans"
        self.plans.mkdir(parents=True)
        self.t5 = self.root / "T5"
        self.t5.mkdir()
        self.library = self.root / "Music"
        made = make_library(self.library, [{
            "relative": "House/_GENRE NEEDS CHECK/song.mp3", "genre": "house",
            "title": "Song", "v2_version": 3,
        }])
        self.track = made["House/_GENRE NEEDS CHECK/song.mp3"]
        current = prepare_metadata_batch.read_track(self.track)
        self.scope_root = self.track.parent.resolve()
        self.plan_path = self.plans / "S001.json"
        plan = {
            "mode": "preview-only", "root": str(self.scope_root),
            "rules": {"approval_required": True},
            "counts": {"items": 1, "genre_fixes": 1, "grouping_fixes": 0},
            "items": [{
                "path": str(self.track.resolve()), "sha256": current["sha256"],
                "genre_fix": {"from": "house", "to": "House"},
            }],
        }
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")
        candidate = {
            **current, "proposed_genre": "House", "proposed_grouping": "",
            "grouping_add_terms": [], "scope_id": "S001", "smart_crate_effects": [],
        }
        self.master_path = self.batch_dir / "master-manifest.json"
        self.preview_md = self.batch_dir / "preview.md"
        self.preview_csv = self.batch_dir / "preview.csv"
        self.preview_md.write_text("synthetic preview\n", encoding="utf-8")
        self.preview_csv.write_text("synthetic,csv\n", encoding="utf-8")
        self.master = {
            "schema_version": 1, "batch_id": "b1", "repo_commit": metadata_batch.repo_head(),
            "safety_file_sha256": metadata_batch.safety_file_hashes(),
            "candidate_count": 1, "ready_for_human_approval": True,
            "preview": {
                "md_path": str(self.preview_md.resolve()),
                "md_sha256": metadata_batch.sha256_file(self.preview_md),
                "csv_path": str(self.preview_csv.resolve()),
                "csv_sha256": metadata_batch.sha256_file(self.preview_csv),
            },
            "candidates": [candidate],
            "scopes": [{
                "scope_id": "S001", "source_root": str(self.scope_root),
                "lock_folder": "unit-test-unique-folder",
                "plan_path": str(self.plan_path.resolve()),
                "plan_sha256": metadata_batch.sha256_file(self.plan_path), "item_count": 1,
                "backup_root": str((self.t5 / "b1/S001/source").resolve()),
                "backup_manifest": str((self.t5 / "b1/S001/backup-manifest.json").resolve()),
                "evidence_root": str((self.t5 / "b1/S001/evidence").resolve()),
            }],
        }
        self.master["master_sha"] = metadata_batch.canonical_sha(self.master)
        self.master_path.write_text(json.dumps(self.master), encoding="utf-8")
        state = metadata_batch.start_batch(
            self.handoff / "current-batch.json", "b1", self.batch_dir,
            handoff_root=self.handoff,
        )
        metadata_batch.transition(
            self.handoff / "current-batch.json", "PREVIEWED", handoff_root=self.handoff,
            updates={"master_path": str(self.master_path.resolve()),
                     "master_sha": self.master["master_sha"]},
        )

    def _approve(self):
        return prepare_metadata_batch.approve(
            self.master["master_sha"], "Unit Tester", self.handoff
        )

    def _preflight(self):
        lock_root = self.root / "locks"
        with mock.patch.object(folder_lock, "LOCKS_DIR", lock_root):
            return apply_approved_batch.preflight(
                self.master["master_sha"], self.handoff, self.t5,
                mount_checker=lambda _: True,
            )

    def test_wrong_master_sha_cannot_be_approved(self):
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "master SHA mismatch"):
            prepare_metadata_batch.approve("0" * 64, "Unit Tester", self.handoff)
        self.assertFalse((self.batch_dir / "approval.json").exists())

    def test_preflight_requires_durable_approval(self):
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "HUMAN_APPROVED"):
            self._preflight()

    def test_exact_approval_and_unchanged_bundle_pass_preflight(self):
        approval = self._approve()
        master, loaded_approval = self._preflight()
        self.assertEqual(master["master_sha"], self.master["master_sha"])
        self.assertEqual(loaded_approval, approval)
        self.assertEqual(
            approval["plan_sha256"],
            {"S001": self.master["scopes"][0]["plan_sha256"]},
        )

    def test_plan_tamper_is_refused(self):
        self._approve()
        self.plan_path.write_text(self.plan_path.read_text() + "\n", encoding="utf-8")
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "plan hash mismatch"):
            self._preflight()

    def test_preview_tamper_is_refused(self):
        self._approve()
        self.preview_md.write_text("changed after approval\n", encoding="utf-8")
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "preview hash mismatch"):
            self._preflight()

    def test_live_hash_drift_is_refused(self):
        self._approve()
        with self.track.open("ab") as handle:
            handle.write(b"drift")
        with self.assertRaisesRegex(RuntimeError, "file hash changed since survey|live sha256 drift"):
            self._preflight()

    def test_repo_commit_drift_is_refused(self):
        self._approve()
        with mock.patch.object(metadata_batch, "repo_head", return_value="different"):
            with self.assertRaisesRegex(metadata_batch.BatchRefused, "repo HEAD differs"):
                self._preflight()

    def test_gated_safety_file_drift_is_refused(self):
        self._approve()
        changed = dict(self.master["safety_file_sha256"])
        changed["scripts/mutation_engine.py"] = "0" * 64
        with mock.patch.object(metadata_batch, "safety_file_hashes", return_value=changed):
            with self.assertRaisesRegex(metadata_batch.BatchRefused, "safety files changed"):
                self._preflight()

    def test_explicit_metadata_drift_gate(self):
        row = dict(self.master["candidates"][0])
        row["genre"] = "different approved value"
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "live genre drift"):
            apply_approved_batch._validate_live({"candidates": [row]})

    def test_explicit_id3_version_drift_gate(self):
        row = dict(self.master["candidates"][0])
        row["id3_major"] = 4
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "live id3_major drift"):
            apply_approved_batch._validate_live({"candidates": [row]})

    def _assert_cross_plan_alias_refused_before_mutation(self, first_path, second_path):
        second = self.plans / "S002.json"
        second.write_text(self.plan_path.read_text(encoding="utf-8"), encoding="utf-8")
        master = dict(self.master)
        master["scopes"] = [
            self.master["scopes"][0],
            {**self.master["scopes"][0], "scope_id": "S002",
             "plan_path": str(second.resolve()),
             "plan_sha256": metadata_batch.sha256_file(second)},
        ]
        master["candidate_count"] = 2
        master["candidates"] = [
            {**self.master["candidates"][0], "path": first_path, "scope_id": "S001"},
            {**self.master["candidates"][0], "path": second_path, "scope_id": "S002"},
        ]
        master.pop("master_sha", None)
        master["master_sha"] = metadata_batch.canonical_sha(master)
        self.master = master
        self.master_path.write_text(json.dumps(master), encoding="utf-8")
        state = metadata_batch.read_json(self.handoff / "current-batch.json")
        state["master_sha"] = master["master_sha"]
        metadata_batch.atomic_write_json(
            self.handoff / "current-batch.json", state, handoff_root=self.handoff
        )
        self._approve()

        validated = [
            ([{"path": first_path}], []),
            ([{"path": second_path}], []),
        ]
        with (
            mock.patch.object(apply_approved_batch.intake, "load_and_validate",
                              side_effect=validated),
            mock.patch.object(apply_approved_batch, "_lock_command") as lock_command,
            mock.patch.object(metadata_batch, "create_verified_backup") as backup,
        ):
            with self.assertRaisesRegex(metadata_batch.BatchRefused,
                                        "effective filesystem path collision"):
                apply_approved_batch.apply_batch(
                    master["master_sha"], self.handoff, self.t5,
                    mount_checker=lambda _: True,
                )
        lock_command.assert_not_called()
        backup.assert_not_called()
        self.assertEqual(
            metadata_batch.read_json(self.handoff / "current-batch.json")["state"],
            "BLOCKED",
        )

    def test_cross_plan_exact_duplicate_collision_refused_before_mutation(self):
        path = "/synthetic/Music/Track.mp3"
        self._assert_cross_plan_alias_refused_before_mutation(path, path)

    def test_cross_plan_case_alias_collision_refused_before_mutation(self):
        self._assert_cross_plan_alias_refused_before_mutation(
            "/synthetic/Music/Track.mp3", "/synthetic/music/track.MP3"
        )

    def test_cross_plan_unicode_alias_collision_refused_before_mutation(self):
        composed = "/synthetic/Music/Café.mp3"
        decomposed = unicodedata.normalize("NFD", composed)
        self.assertNotEqual(composed, decomposed)
        self._assert_cross_plan_alias_refused_before_mutation(composed, decomposed)

    def test_real_validator_failure_is_refused_even_with_matching_plan_hash(self):
        plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        plan["root"] = str(self.library.resolve())
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")
        master = dict(self.master)
        master["scopes"] = [{
            **self.master["scopes"][0],
            "plan_sha256": metadata_batch.sha256_file(self.plan_path),
        }]
        with self.assertRaisesRegex(metadata_batch.BatchRefused,
                                    "plan validation refused.*plan root"):
            apply_approved_batch._validate_plans(master, self.handoff)

    def test_cli_known_validator_runtime_error_is_clean_refusal_and_durable_block(self):
        plan = json.loads(self.plan_path.read_text(encoding="utf-8"))
        plan["root"] = str(self.library.resolve())
        self.plan_path.write_text(json.dumps(plan), encoding="utf-8")
        master = dict(self.master)
        master["scopes"] = [{
            **self.master["scopes"][0],
            "plan_sha256": metadata_batch.sha256_file(self.plan_path),
        }]
        master.pop("master_sha", None)
        master["master_sha"] = metadata_batch.canonical_sha(master)
        self.master = master
        self.master_path.write_text(json.dumps(master), encoding="utf-8")
        state_path = self.handoff / "current-batch.json"
        state = metadata_batch.read_json(state_path)
        state["master_sha"] = master["master_sha"]
        metadata_batch.atomic_write_json(state_path, state, handoff_root=self.handoff)
        self._approve()

        output = io.StringIO()
        with (
            mock.patch.object(metadata_batch, "HANDOFF_ROOT", self.handoff),
            mock.patch.object(sys, "argv", [
                "apply_approved_batch.py", "--master-sha", master["master_sha"],
            ]),
            mock.patch("sys.stdout", output),
            mock.patch.object(apply_approved_batch, "_lock_command") as lock_command,
        ):
            return_code = apply_approved_batch.main()
        self.assertEqual(return_code, 2)
        self.assertTrue(output.getvalue().startswith("REFUSED:"))
        self.assertNotIn("Traceback", output.getvalue())
        self.assertIn("plan validation refused", output.getvalue())
        lock_command.assert_not_called()
        state = metadata_batch.read_json(state_path)
        self.assertEqual(state["state"], "BLOCKED")
        self.assertIn("plan validation refused", state["reason"])

    def test_backup_path_outside_t5_is_refused_in_preflight(self):
        scope = {**self.master["scopes"][0],
                 "backup_root": str((self.root / "outside/source").resolve())}
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "must resolve under"):
            apply_approved_batch._validate_t5(
                {"scopes": [scope]}, self.t5, mount_checker=lambda _: True
            )

    def test_missing_t5_mount_is_refused(self):
        self._approve()
        lock_root = self.root / "locks"
        with mock.patch.object(folder_lock, "LOCKS_DIR", lock_root):
            with self.assertRaisesRegex(metadata_batch.BatchRefused, "not mounted"):
                apply_approved_batch.preflight(
                    self.master["master_sha"], self.handoff, self.t5,
                    mount_checker=lambda _: False,
                )

    def test_even_a_stale_existing_lock_is_refused(self):
        self._approve()
        lock_root = self.root / "locks"
        lock_root.mkdir()
        lock_path = lock_root / "unit-test-unique-folder.lock.json"
        lock_path.write_text(json.dumps({
            "folder": "unit-test-unique-folder", "session": "old",
            "started_at": "2000-01-01T00:00:00+00:00",
            "last_renewed_at": "2000-01-01T00:00:00+00:00",
        }), encoding="utf-8")
        with mock.patch.object(folder_lock, "LOCKS_DIR", lock_root):
            with self.assertRaisesRegex(metadata_batch.BatchRefused, "stale locks are not auto-reclaimed"):
                apply_approved_batch.preflight(
                    self.master["master_sha"], self.handoff, self.t5,
                    mount_checker=lambda _: True,
                )

    def test_fresh_held_lock_is_refused(self):
        self._approve()
        lock_root = self.root / "locks"
        lock_root.mkdir()
        lock_path = lock_root / "unit-test-unique-folder.lock.json"
        timestamp = metadata_batch.now()
        lock_path.write_text(json.dumps({
            "folder": "unit-test-unique-folder", "session": "active",
            "started_at": timestamp, "last_renewed_at": timestamp,
        }), encoding="utf-8")
        with mock.patch.object(folder_lock, "LOCKS_DIR", lock_root):
            with self.assertRaisesRegex(metadata_batch.BatchRefused,
                                        "existing mutation lock blocks batch"):
                apply_approved_batch.preflight(
                    self.master["master_sha"], self.handoff, self.t5,
                    mount_checker=lambda _: True,
                )


class FailedApplyClassificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.t5 = Path(self.temp.name) / "T5"
        self.evidence = self.t5 / "evidence"
        self.evidence.mkdir(parents=True)

    def _write_journal(self, records):
        path = self.evidence / "fixture-recovery-1.jsonl"
        path.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        return path

    def _stateful_batch(self, scope_item_counts):
        handoff = self.t5 / "handoff"
        batch_dir = handoff / "batches" / "b1"
        batch_dir.mkdir(parents=True)
        state_path = handoff / "current-batch.json"
        metadata_batch.start_batch(
            state_path, "b1", batch_dir, handoff_root=handoff
        )
        metadata_batch.transition(state_path, "PREVIEWED", handoff_root=handoff)
        metadata_batch.transition(state_path, "HUMAN_APPROVED", handoff_root=handoff)

        scopes = []
        candidates = []
        for index, item_count in enumerate(scope_item_counts, start=1):
            scope_id = f"S{index:03d}"
            source_root = self.t5 / f"scope-{index}"
            source_root.mkdir()
            scopes.append({
                "scope_id": scope_id,
                "source_root": str(source_root),
                "backup_root": str(self.t5 / f"backup-{index}"),
                "backup_manifest": str(self.t5 / f"manifest-{index}.json"),
                "evidence_root": str(self.t5 / f"evidence-{index}"),
                "plan_path": str(self.t5 / f"plan-{index}.json"),
                "lock_folder": f"Folder {index}",
                "item_count": item_count,
            })
            for item_index in range(item_count):
                candidates.append({
                    "scope_id": scope_id,
                    "path": f"/synthetic/{scope_id}/track-{item_index}.mp3",
                    "smart_crate_effects": [],
                })
        return handoff, state_path, {
            "batch_id": "b1", "repo_commit": "abc",
            "scopes": scopes, "candidates": candidates,
        }

    @staticmethod
    def _successful_lock(command, folder, session, master):
        if command == "acquire":
            return {"acquired": True, "lock": {"run_id": f"{folder}-run"}}
        return {"released": True, "lock": {"run_id": f"{folder}-run"}}

    @staticmethod
    def _scope_proof(master, scope, result):
        files = sum(row["scope_id"] == scope["scope_id"] for row in master["candidates"])
        return {
            "files": files,
            "unauthorized_changes": 0,
            "recovery": {"clean": True, "runs": {"fixture": {"complete": True}}},
        }

    def test_interrupted_apply_is_blocked_not_treated_as_clean(self):
        self._write_journal([
            {"type": "run_start", "run_id": "r1"},
            {"type": "intent", "run_id": "r1", "index": 1,
             "kind": "metadata", "source": "/synthetic/song.mp3"},
        ])
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "interrupted apply"):
            apply_approved_batch.inspect_failed_run(self.evidence, self.t5)

    def test_failed_rollback_raises_distinct_severe_condition(self):
        self._write_journal([
            {"type": "run_start", "run_id": "r1"},
            {"type": "intent", "run_id": "r1", "index": 1,
             "kind": "metadata", "source": "/synthetic/song.mp3",
             "backup_path": "/synthetic/backup.mp3"},
            {"type": "mutated", "run_id": "r1", "index": 1},
            {"type": "rolled_back", "run_id": "r1", "index": 1,
             "restored": False, "rollback_error": "forced restore failure"},
        ])
        with self.assertRaisesRegex(metadata_batch.RollbackFailed, "ROLLBACK_FAILED"):
            apply_approved_batch.inspect_failed_run(self.evidence, self.t5)

    def test_first_scope_failure_prevents_every_later_scope(self):
        scopes = []
        for index in (1, 2):
            root = self.t5 / f"scope-{index}"
            root.mkdir()
            scopes.append({
                "scope_id": f"S00{index}", "source_root": str(root),
                "backup_root": str(self.t5 / f"backup-{index}"),
                "backup_manifest": str(self.t5 / f"manifest-{index}.json"),
                "evidence_root": str(self.t5 / f"evidence-{index}"),
                "plan_path": str(self.t5 / f"plan-{index}.json"),
                "lock_folder": f"Folder {index}",
            })
        master = {"batch_id": "b1", "repo_commit": "abc", "scopes": scopes}
        failed_child = mock.Mock(returncode=2, stdout="", stderr="forced interruption")
        lock_result = {"acquired": True, "released": True, "lock": {"run_id": "r1"}}
        with (
            mock.patch.object(apply_approved_batch, "preflight", return_value=(master, {})),
            mock.patch.object(metadata_batch, "transition") as transition,
            mock.patch.object(metadata_batch, "update_scope"),
            mock.patch.object(metadata_batch, "read_json", return_value={"state": "PREFLIGHT_PASS"}),
            mock.patch.object(metadata_batch, "create_verified_backup",
                              return_value={"status": "PASS", "manifest_sha256": "m"}),
            mock.patch.object(apply_approved_batch, "_validate_t5"),
            mock.patch.object(apply_approved_batch, "_lock_command", return_value=lock_result),
            mock.patch.object(lock_guard, "LockHeartbeat"),
            mock.patch.object(subprocess, "run", return_value=failed_child) as child_run,
            mock.patch.object(apply_approved_batch, "inspect_failed_run",
                              side_effect=metadata_batch.BatchRefused("interrupted apply")),
        ):
            with self.assertRaisesRegex(metadata_batch.BatchRefused, "interrupted apply"):
                apply_approved_batch.apply_batch(
                    "approved", self.t5 / "handoff", self.t5,
                    mount_checker=lambda _: True,
                )
        self.assertEqual(child_run.call_count, 1, "scope 2 must never start")
        self.assertTrue(any(call.args[1] == "BLOCKED" for call in transition.call_args_list))

    def test_real_three_scope_run_records_failed_scope_and_leaves_later_scope_not_started(self):
        handoff, state_path, master = self._stateful_batch([1, 1, 1])
        calls = []

        def child_run(command, **kwargs):
            calls.append(command)
            out_dir = Path(command[command.index("--out-dir") + 1])
            if len(calls) == 1:
                return subprocess.CompletedProcess(
                    command, 0,
                    stdout=json.dumps({
                        "result_path": str(out_dir / "result.json"),
                        "recovery_journal": str(out_dir / "unused-recovery-1.jsonl"),
                    }),
                    stderr="",
                )
            journal = out_dir / "forced-recovery-1.jsonl"
            journal.write_text(
                json.dumps({"type": "run_start", "run_id": "r2"}) + "\n" +
                json.dumps({
                    "type": "intent", "run_id": "r2", "index": 1,
                    "kind": "metadata", "source": "/synthetic/S002/track-0.mp3",
                }) + "\n",
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(
                command, 2, stdout="", stderr="forced interruption"
            )

        with (
            mock.patch.object(apply_approved_batch, "preflight", return_value=(master, {})),
            mock.patch.object(metadata_batch, "create_verified_backup",
                              return_value={"status": "PASS", "manifest_sha256": "m"}),
            mock.patch.object(apply_approved_batch, "_lock_command",
                              side_effect=self._successful_lock),
            mock.patch.object(lock_guard, "LockHeartbeat"),
            mock.patch.object(subprocess, "run", side_effect=child_run),
            mock.patch.object(apply_approved_batch, "_verify_scope",
                              side_effect=self._scope_proof),
        ):
            with self.assertRaisesRegex(metadata_batch.BatchRefused, "interrupted apply"):
                apply_approved_batch.apply_batch(
                    "approved", handoff, self.t5, mount_checker=lambda _: True
                )

        self.assertEqual(len(calls), 2, "scope 3 must never be invoked")
        state = metadata_batch.read_json(state_path)
        self.assertEqual(state["state"], "BLOCKED")
        self.assertEqual(
            (state["scopes"]["S001"]["state"], state["scopes"]["S001"]["execution"]),
            ("VERIFIED", "COMPLETED"),
        )
        self.assertEqual(
            (state["scopes"]["S002"]["state"], state["scopes"]["S002"]["execution"]),
            ("BLOCKED", "FAILED"),
        )
        self.assertEqual(
            (state["scopes"]["S003"]["state"], state["scopes"]["S003"]["execution"]),
            ("PREFLIGHT_PASS", "NOT_STARTED"),
        )
        self.assertFalse((handoff / "batches/b1/apply-summary.json").exists())

    def test_real_two_scope_success_summary_matches_completed_scope_state_and_counts(self):
        handoff, state_path, master = self._stateful_batch([1, 2])
        calls = []

        def child_run(command, **kwargs):
            calls.append(command)
            out_dir = Path(command[command.index("--out-dir") + 1])
            return subprocess.CompletedProcess(
                command, 0,
                stdout=json.dumps({
                    "result_path": str(out_dir / "result.json"),
                    "recovery_journal": str(out_dir / "unused-recovery-1.jsonl"),
                }),
                stderr="",
            )

        with (
            mock.patch.object(apply_approved_batch, "preflight", return_value=(master, {})),
            mock.patch.object(metadata_batch, "create_verified_backup",
                              return_value={"status": "PASS", "manifest_sha256": "m"}),
            mock.patch.object(apply_approved_batch, "_lock_command",
                              side_effect=self._successful_lock),
            mock.patch.object(lock_guard, "LockHeartbeat"),
            mock.patch.object(subprocess, "run", side_effect=child_run),
            mock.patch.object(apply_approved_batch, "_verify_scope",
                              side_effect=self._scope_proof),
        ):
            summary = apply_approved_batch.apply_batch(
                "approved", handoff, self.t5, mount_checker=lambda _: True
            )

        self.assertEqual(len(calls), 2)
        self.assertEqual(summary["BATCH RESULT"], "PASS")
        self.assertEqual(summary["FILES VERIFIED"], 3)
        self.assertEqual(summary["SCOPES VERIFIED"], 2)
        self.assertEqual(summary["SCOPE RESULTS"], {
            "S001": {
                "state": "VERIFIED", "execution": "COMPLETED", "files_verified": 1,
            },
            "S002": {
                "state": "VERIFIED", "execution": "COMPLETED", "files_verified": 2,
            },
        })
        state = metadata_batch.read_json(state_path)
        self.assertEqual(state["state"], "VERIFIED")
        self.assertEqual(
            {scope_id: (entry["state"], entry["execution"], entry["files"])
             for scope_id, entry in state["scopes"].items()},
            {
                "S001": ("VERIFIED", "COMPLETED", 1),
                "S002": ("VERIFIED", "COMPLETED", 2),
            },
        )
        self.assertEqual(
            metadata_batch.read_json(handoff / "batches/b1/apply-summary.json"),
            summary,
        )

    def test_rollback_failure_sets_scope_and_batch_to_distinct_severe_state(self):
        scopes = []
        for index in (1, 2):
            root = self.t5 / f"scope-{index}"
            root.mkdir()
            scopes.append({
                "scope_id": f"S00{index}", "source_root": str(root),
                "backup_root": str(self.t5 / f"backup-{index}"),
                "backup_manifest": str(self.t5 / f"manifest-{index}.json"),
                "evidence_root": str(self.t5 / f"evidence-{index}"),
                "plan_path": str(self.t5 / f"plan-{index}.json"),
                "lock_folder": f"Folder {index}",
            })
        master = {"batch_id": "b1", "repo_commit": "abc", "scopes": scopes}
        failed_child = mock.Mock(returncode=2, stdout="", stderr="forced rollback failure")
        lock_result = {"acquired": True, "released": True, "lock": {"run_id": "r1"}}
        with (
            mock.patch.object(apply_approved_batch, "preflight", return_value=(master, {})),
            mock.patch.object(metadata_batch, "transition") as transition,
            mock.patch.object(metadata_batch, "update_scope") as update_scope,
            mock.patch.object(metadata_batch, "read_json", return_value={"state": "PREFLIGHT_PASS"}),
            mock.patch.object(metadata_batch, "create_verified_backup",
                              return_value={"status": "PASS", "manifest_sha256": "m"}),
            mock.patch.object(apply_approved_batch, "_validate_t5"),
            mock.patch.object(apply_approved_batch, "_lock_command", return_value=lock_result),
            mock.patch.object(lock_guard, "LockHeartbeat"),
            mock.patch.object(subprocess, "run", return_value=failed_child) as child_run,
            mock.patch.object(apply_approved_batch, "inspect_failed_run",
                              side_effect=metadata_batch.RollbackFailed("restore failed")),
        ):
            with self.assertRaisesRegex(metadata_batch.RollbackFailed, "restore failed"):
                apply_approved_batch.apply_batch(
                    "approved", self.t5 / "handoff", self.t5,
                    mount_checker=lambda _: True,
                )
        self.assertEqual(child_run.call_count, 1, "scope 2 must never start")
        self.assertTrue(any(
            call.args[1:3] == ("S001", "ROLLBACK_FAILED")
            for call in update_scope.call_args_list
        ))
        self.assertTrue(any(
            call.args[1] == "ROLLBACK_FAILED" for call in transition.call_args_list
        ))


if __name__ == "__main__":
    unittest.main()
