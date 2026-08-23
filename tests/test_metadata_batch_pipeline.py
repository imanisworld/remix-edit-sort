"""Regression tests for the durable metadata-batch refusal gates.

All media is synthetic and all writable roots are TemporaryDirectory paths.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import apply_approved_batch  # noqa: E402
import folder_lock  # noqa: E402
import metadata_batch  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
