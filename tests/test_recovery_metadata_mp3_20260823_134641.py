"""Tests for the one-time recover_metadata_mp3_20260823_134641.py script.

All batch identity constants are patched to a small synthetic batch so
these tests never touch the real production batch, its real T5 backups,
or the real music library. The synthetic batch mirrors the real one's
actual shape: two already-VERIFIED/COMPLETED scopes, an "S009" scope that
failed on an NFC/NFD backup mismatch and has a real recovery-evidence PASS
manifest sitting next to the original real FAIL manifest, and two more
PREFLIGHT_PASS/NOT_STARTED scopes after it.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import unicodedata
from pathlib import Path
from unittest import mock

import sys

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import apply_approved_batch  # noqa: E402
import backup_manifest  # noqa: E402
import lock_guard  # noqa: E402
import metadata_batch  # noqa: E402
import recover_metadata_mp3_20260823_134641 as recovery  # noqa: E402
from fixtures import make_library  # noqa: E402


class RecoveryScriptTestBase(unittest.TestCase):
    BATCH_ID = "test-batch-recovery"
    COMPLETED_IDS = ("S001", "S002")
    RECOVERY_IDS = ("S009", "S010", "S011")  # "S009" is the hardcoded backup-reuse scope.

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.t5 = Path(self.temp.name) / "T5"
        self.t5.mkdir()
        self.handoff = self.t5 / "handoff"
        self.state_path = self.handoff / "current-batch.json"
        self.batch_dir = self.handoff / "batches" / self.BATCH_ID

        self.const_patches = [
            mock.patch.object(recovery, "BATCH_ID", self.BATCH_ID),
            mock.patch.object(recovery, "COMPLETED_SCOPE_IDS", self.COMPLETED_IDS),
            mock.patch.object(recovery, "RECOVERY_SCOPE_IDS", self.RECOVERY_IDS),
        ]
        for patch in self.const_patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _build_batch(self):
        """Real durable state on disk, mirroring the actual production
        batch's shape after S001/S002 verified and S009 failed."""
        scopes, candidates = [], []
        for scope_id in self.COMPLETED_IDS + self.RECOVERY_IDS:
            source_root = self.t5 / f"source-{scope_id}"
            source_root.mkdir(parents=True)
            scopes.append({
                "scope_id": scope_id,
                "source_root": str(source_root),
                "lock_folder": f"Folder-{scope_id}",
                "plan_path": str(self.batch_dir / "plans" / f"{scope_id}.json"),
                "plan_sha256": f"plan-sha-{scope_id}",
                "item_count": 1,
                "backup_root": str(self.t5 / f"backup-{scope_id}"),
                "backup_manifest": str(self.t5 / f"manifests/{scope_id}/backup-manifest.json"),
                "evidence_root": str(self.t5 / f"evidence-{scope_id}"),
            })
            candidates.append({
                "scope_id": scope_id, "path": f"/synthetic/{scope_id}/track.mp3",
                "smart_crate_effects": [],
            })

        real_safety = metadata_batch.safety_file_hashes()
        stale_safety = {
            **real_safety,
            "scripts/backup_manifest.py": "stale-pre-fix-hash",
            "scripts/apply_approved_batch.py": "stale-pre-fix-hash",
        }
        master = {
            "schema_version": 1, "batch_id": self.BATCH_ID, "repo_commit": metadata_batch.repo_head(),
            "candidate_count": len(candidates), "safety_file_sha256": stale_safety,
            "preview": {}, "scopes": scopes, "candidates": candidates,
        }
        master["master_sha"] = metadata_batch.canonical_sha(master)
        sha_patch = mock.patch.object(recovery, "ORIGINAL_MASTER_SHA", master["master_sha"])
        sha_patch.start()
        self.addCleanup(sha_patch.stop)
        self.batch_dir.mkdir(parents=True)
        master_path = self.batch_dir / "master-manifest.json"
        master_path.write_text(json.dumps(master, ensure_ascii=False), encoding="utf-8")

        metadata_batch.start_batch(self.state_path, self.BATCH_ID, self.batch_dir, handoff_root=self.handoff)
        metadata_batch.transition(self.state_path, "PREVIEWED", handoff_root=self.handoff)
        metadata_batch.transition(
            self.state_path, "HUMAN_APPROVED", handoff_root=self.handoff,
            updates={
                "master_path": str(master_path), "master_sha": master["master_sha"],
                "approval_path": str(self.batch_dir / "approval.json"),
            },
        )
        seeded = {
            sid: {"state": "PREFLIGHT_PASS", "execution": "NOT_STARTED",
                  "history": [{"state": "PREFLIGHT_PASS", "at": metadata_batch.now()}]}
            for sid in self.COMPLETED_IDS + self.RECOVERY_IDS
        }
        metadata_batch.transition(self.state_path, "PLANNED", handoff_root=self.handoff, updates={"scopes": seeded})
        metadata_batch.transition(self.state_path, "PREFLIGHT_PASS", handoff_root=self.handoff)
        metadata_batch.transition(self.state_path, "BACKED_UP", handoff_root=self.handoff)
        metadata_batch.transition(self.state_path, "APPLIED", handoff_root=self.handoff)

        for scope_id in self.COMPLETED_IDS:
            metadata_batch.update_scope(self.state_path, scope_id, "BACKED_UP",
                                        details={"execution": "IN_PROGRESS"}, handoff_root=self.handoff)
            metadata_batch.update_scope(self.state_path, scope_id, "APPLIED",
                                        details={"execution": "IN_PROGRESS"}, handoff_root=self.handoff)
            metadata_batch.update_scope(
                self.state_path, scope_id, "VERIFIED",
                details={"execution": "COMPLETED", "files": 1,
                         "recovery": {"clean": True, "rollback_failures": 0}},
                handoff_root=self.handoff,
            )

        s009 = scopes[len(self.COMPLETED_IDS)]
        source_root, backup_root = Path(s009["source_root"]), Path(s009["backup_root"])
        name_nfc = unicodedata.normalize("NFC", "Beyoncé - Track.mp3")
        name_nfd = unicodedata.normalize("NFD", "Beyoncé - Track.mp3")
        make_library(source_root, [{"relative": name_nfc}])
        backup_root.mkdir(parents=True)
        (backup_root / name_nfd).write_bytes((source_root / name_nfc).read_bytes())

        original_manifest_path = Path(s009["backup_manifest"])
        original_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        fail_doc = {
            "manifest_version": backup_manifest.MANIFEST_VERSION, "backup_id": "orig",
            "timestamp": metadata_batch.now(), "source_root": str(source_root),
            "source_root_identity": backup_manifest._identity(source_root),
            "backup_root": str(backup_root), "file_count": 1, "total_bytes": 1,
            "files": [{"path": name_nfc, "sha256": "x", "bytes": 1, "backed_up": False}],
            "serato": {"required": False, "present": False, "result": "NOT_REQUIRED"},
            "verifier": backup_manifest.VERIFIER_NAME, "verifier_version": backup_manifest.MANIFEST_VERSION,
            "repo_sha": metadata_batch.repo_head(),
            "problems": [f"missing from backup: {name_nfc}"], "status": "FAIL",
        }
        fail_doc["manifest_sha256"] = backup_manifest.canonical_digest(fail_doc)
        backup_manifest.write_manifest(fail_doc, original_manifest_path)

        recovery_manifest_path = recovery._recovery_manifest_path(s009)
        fresh = backup_manifest.build_manifest(source_root, backup_root, backup_id="recovery-fix")
        self.assertEqual(fresh["status"], backup_manifest.STATUS_PASS, "test fixture backup must genuinely PASS")
        backup_manifest.write_manifest(fresh, recovery_manifest_path)

        metadata_batch.update_scope(
            self.state_path, "S009", "BLOCKED",
            details={"execution": "FAILED", "failure": "backup manifest FAIL"},
            handoff_root=self.handoff,
        )
        metadata_batch.transition(self.state_path, "BLOCKED", reason="S009 backup FAIL", handoff_root=self.handoff)
        return master

    def _create_approval(self):
        return recovery.create_recovery_approval("test operator", handoff_root=self.handoff)

    def _hand_edit_state(self, mutate) -> None:
        """Directly rewrite current-batch.json, bypassing transition()'s
        forward-only validation -- used to simulate corruption/drift that a
        legitimate state transition could never produce, not a real state
        change made through the normal machinery."""
        state = metadata_batch.read_json(self.state_path)
        mutate(state)
        metadata_batch.atomic_write_json(self.state_path, state, handoff_root=self.handoff)

    def _mocked_apply(self, *, child_returncode_by_scope=None):
        """Run apply_recovery_batch with the child subprocess/lock/verify
        boundary mocked, exactly like this repo's existing
        FailedApplyClassificationTests real-state-machine tests."""
        child_returncode_by_scope = child_returncode_by_scope or {}
        lock_result = {"acquired": True, "released": True, "lock": {"run_id": "r1"}}
        calls = []

        real_run = subprocess.run

        def fake_lock(command, folder, session, master):
            return lock_result

        def fake_child(command, **kwargs):
            if "apply_new_music_intake_plan.py" not in command[1]:
                return real_run(command, **kwargs)  # e.g. metadata_batch.repo_head()'s git call
            calls.append(command)
            scope_id = command[command.index("--label") + 1].split("-")[-2]
            out_dir = Path(command[command.index("--out-dir") + 1])
            rc = child_returncode_by_scope.get(scope_id, 0)
            if rc != 0:
                return subprocess.CompletedProcess(command, rc, stdout="", stderr="forced failure")
            return subprocess.CompletedProcess(
                command, 0,
                stdout=json.dumps({
                    "result_path": str(out_dir / "result.json"),
                    "recovery_journal": str(out_dir / "unused-recovery.jsonl"),
                }),
                stderr="",
            )

        def fake_verify(master, scope, result, *, t5_volume=None):
            files = sum(row["scope_id"] == scope["scope_id"] for row in master["candidates"])
            return {"files": files, "unauthorized_changes": 0,
                    "recovery": {"clean": True, "runs": {"r": {"complete": True}}, "rollback_failures": 0}}

        with (
            mock.patch.object(apply_approved_batch, "_validate_plans"),
            mock.patch.object(apply_approved_batch, "_validate_live"),
            mock.patch.object(apply_approved_batch, "_validate_t5"),
            mock.patch.object(apply_approved_batch, "_validate_locks"),
            mock.patch.object(apply_approved_batch, "_lock_command", side_effect=fake_lock),
            mock.patch.object(lock_guard, "LockHeartbeat"),
            mock.patch.object(metadata_batch, "create_verified_backup",
                              return_value={"status": "PASS", "manifest_sha256": "m"}),
            mock.patch.object(subprocess, "run", side_effect=fake_child),
            mock.patch.object(apply_approved_batch, "_verify_scope", side_effect=fake_verify),
        ):
            result = recovery.apply_recovery_batch(handoff_root=self.handoff, t5_volume=self.t5,
                                                    mount_checker=lambda _: True)
        return result, calls


class CreateRecoveryApprovalTests(RecoveryScriptTestBase):
    def test_requires_all_completed_scopes_verified(self):
        self._build_batch()
        self._hand_edit_state(lambda s: s["scopes"]["S002"].update({"state": "APPLIED"}))
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "S002 is not VERIFIED"):
            self._create_approval()

    def test_requires_fixed_gated_file_hashes_present(self):
        master = self._build_batch()
        real_safety = metadata_batch.safety_file_hashes()
        master["safety_file_sha256"] = real_safety  # simulate: fix was never applied
        master["master_sha"] = metadata_batch.canonical_sha(master)
        sha_patch = mock.patch.object(recovery, "ORIGINAL_MASTER_SHA", master["master_sha"])
        sha_patch.start()
        self.addCleanup(sha_patch.stop)
        (self.batch_dir / "master-manifest.json").write_text(json.dumps(master), encoding="utf-8")
        self._hand_edit_state(lambda s: s.update({"master_sha": master["master_sha"]}))
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "requires the fixed"):
            self._create_approval()

    def test_requires_original_manifest_still_says_fail(self):
        master = self._build_batch()
        s009 = master["scopes"][len(self.COMPLETED_IDS)]
        doc = json.loads(Path(s009["backup_manifest"]).read_text())
        doc["status"] = "PASS"
        Path(s009["backup_manifest"]).write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "no longer says FAIL"):
            self._create_approval()

    def test_refuses_if_fresh_rehash_disagrees_with_recovery_manifest(self):
        master = self._build_batch()
        s009 = master["scopes"][len(self.COMPLETED_IDS)]
        # Corrupt the live backup after the recovery manifest was written.
        (Path(s009["backup_root"]) / unicodedata.normalize("NFD", "Beyoncé - Track.mp3")).write_bytes(b"tampered")
        with self.assertRaisesRegex(
            metadata_batch.BatchRefused,
            "disagrees with the recorded recovery manifest|backup re-hash is not PASS",
        ):
            self._create_approval()

    def test_binds_only_remaining_scope_plan_hashes(self):
        self._build_batch()
        approval = self._create_approval()
        self.assertEqual(set(approval["remaining_plan_sha256"]), set(self.RECOVERY_IDS))
        self.assertTrue(set(approval["completed_scope_ids"]).isdisjoint(approval["remaining_plan_sha256"]))

    def test_approval_sha256_is_self_consistent(self):
        self._build_batch()
        approval = self._create_approval()
        recomputed = metadata_batch.canonical_sha(approval, excluded=("recovery_approval_sha256",))
        self.assertEqual(approval["recovery_approval_sha256"], recomputed)


class PreflightRecoveryTests(RecoveryScriptTestBase):
    def test_refuses_if_completed_scope_state_altered_after_approval(self):
        self._build_batch()
        self._create_approval()
        # A files-count drift keeps state/execution matching VERIFIED/COMPLETED
        # so this specifically exercises the "matches approval" comparison,
        # not the earlier VERIFIED/COMPLETED precondition.
        self._hand_edit_state(lambda s: s["scopes"]["S001"].update({"files": 999}))
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "no longer matches"):
            recovery.preflight_recovery(handoff_root=self.handoff, t5_volume=self.t5,
                                        mount_checker=lambda _: True)

    def test_refuses_if_gated_file_hash_drifts_after_approval(self):
        self._build_batch()
        self._create_approval()
        drifted = dict(metadata_batch.safety_file_hashes())
        drifted["scripts/mutation_engine.py"] = "drifted-hash"
        with mock.patch.object(metadata_batch, "safety_file_hashes", return_value=drifted):
            with self.assertRaisesRegex(metadata_batch.BatchRefused, "gated safety files changed"):
                recovery.preflight_recovery(handoff_root=self.handoff, t5_volume=self.t5,
                                            mount_checker=lambda _: True)

    def test_refuses_if_recovery_approval_tampered(self):
        self._build_batch()
        self._create_approval()
        approval_path = recovery._recovery_approval_path(self.handoff)
        doc = json.loads(approval_path.read_text())
        doc["approved_by"] = "someone else entirely"
        approval_path.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "altered"):
            recovery.preflight_recovery(handoff_root=self.handoff, t5_volume=self.t5,
                                        mount_checker=lambda _: True)

    def test_refuses_if_original_fail_manifest_touched_after_approval(self):
        master = self._build_batch()
        self._create_approval()
        s009 = master["scopes"][len(self.COMPLETED_IDS)]
        Path(s009["backup_manifest"]).write_text('{"status": "FAIL", "tampered": true}', encoding="utf-8")
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "original S009 FAIL manifest changed"):
            recovery.preflight_recovery(handoff_root=self.handoff, t5_volume=self.t5,
                                        mount_checker=lambda _: True)

    def test_refuses_if_own_script_hash_changes_after_approval(self):
        self._build_batch()
        self._create_approval()
        script_path = Path(recovery.__file__).resolve()
        original = script_path.read_bytes()
        script_path.write_bytes(original + b"\n# unreviewed post-approval edit\n")
        self.addCleanup(lambda: script_path.write_bytes(original))
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "own content changed"):
            recovery.preflight_recovery(handoff_root=self.handoff, t5_volume=self.t5,
                                        mount_checker=lambda _: True)

    def test_returns_recovery_master_excluding_completed_scopes(self):
        self._build_batch()
        self._create_approval()
        with (
            mock.patch.object(apply_approved_batch, "_validate_plans"),
            mock.patch.object(apply_approved_batch, "_validate_live"),
            mock.patch.object(apply_approved_batch, "_validate_t5"),
            mock.patch.object(apply_approved_batch, "_validate_locks"),
        ):
            recovery_master, approval, full_master = recovery.preflight_recovery(
                handoff_root=self.handoff, t5_volume=self.t5, mount_checker=lambda _: True
            )
        self.assertEqual({s["scope_id"] for s in recovery_master["scopes"]}, set(self.RECOVERY_IDS))
        self.assertTrue(all(c["scope_id"] in self.RECOVERY_IDS for c in recovery_master["candidates"]))
        self.assertEqual(len(full_master["scopes"]), len(self.COMPLETED_IDS) + len(self.RECOVERY_IDS))

    def test_requires_batch_blocked(self):
        self._build_batch()
        self._create_approval()
        self._hand_edit_state(lambda s: s.update({"state": "APPLIED"}))
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "requires batch BLOCKED"):
            recovery.preflight_recovery(handoff_root=self.handoff, t5_volume=self.t5,
                                        mount_checker=lambda _: True)


class ApplyRecoveryBatchTests(RecoveryScriptTestBase):
    def test_resume_starts_at_s009_not_s001(self):
        self._build_batch()
        self._create_approval()
        _, calls = self._mocked_apply()
        first_root = calls[0][calls[0].index("--root") + 1]
        self.assertIn("S009", first_root)
        for call in calls:
            root = call[call.index("--root") + 1]
            self.assertNotIn("S001", root)
            self.assertNotIn("S002", root)

    def test_completed_scopes_cannot_be_invoked_structurally(self):
        self._build_batch()
        self._create_approval()
        real_update_scope = metadata_batch.update_scope
        real_transition = metadata_batch.transition

        def guarded_update_scope(state_path, scope_id, *a, **kw):
            if scope_id in self.COMPLETED_IDS:
                raise AssertionError(f"update_scope must never be called for completed scope {scope_id}")
            return real_update_scope(state_path, scope_id, *a, **kw)

        with mock.patch.object(metadata_batch, "update_scope", side_effect=guarded_update_scope):
            self._mocked_apply()

    def test_s009_backup_is_reused_not_recopied(self):
        self._build_batch()
        self._create_approval()
        calls = {"create_verified_backup": 0}
        real = metadata_batch.create_verified_backup

        def spy_create_backup(source_root, *a, **kw):
            calls["create_verified_backup"] += 1
            if "S009" in str(source_root):
                raise AssertionError("S009's backup must be reused, not recopied")
            return {"status": "PASS", "manifest_sha256": "m"}

        real_run = subprocess.run

        def fake_run(command, **kw):
            if "apply_new_music_intake_plan.py" not in command[1]:
                return real_run(command, **kw)  # e.g. metadata_batch.repo_head()'s git call
            return subprocess.CompletedProcess(
                command, 0,
                stdout=json.dumps({
                    "result_path": str(Path(command[command.index("--out-dir") + 1]) / "r.json"),
                    "recovery_journal": str(Path(command[command.index("--out-dir") + 1]) / "j.jsonl"),
                }),
                stderr="",
            )

        with (
            mock.patch.object(apply_approved_batch, "_validate_plans"),
            mock.patch.object(apply_approved_batch, "_validate_live"),
            mock.patch.object(apply_approved_batch, "_validate_t5"),
            mock.patch.object(apply_approved_batch, "_validate_locks"),
            mock.patch.object(apply_approved_batch, "_lock_command",
                              return_value={"acquired": True, "released": True, "lock": {"run_id": "r"}}),
            mock.patch.object(lock_guard, "LockHeartbeat"),
            mock.patch.object(metadata_batch, "create_verified_backup", side_effect=spy_create_backup),
            mock.patch.object(subprocess, "run", side_effect=fake_run),
            mock.patch.object(apply_approved_batch, "_verify_scope",
                              return_value={"files": 1, "unauthorized_changes": 0,
                                           "recovery": {"clean": True, "runs": {}, "rollback_failures": 0}}),
        ):
            recovery.apply_recovery_batch(handoff_root=self.handoff, t5_volume=self.t5,
                                          mount_checker=lambda _: True)
        self.assertEqual(calls["create_verified_backup"], 2, "only S010 and S011 recopy")

    def test_s009_passes_recovery_manifest_not_original_to_child_apply(self):
        self._build_batch()
        self._create_approval()
        _, calls = self._mocked_apply()
        s009_call = next(c for c in calls if "--root" in c and "S009" in c[c.index("--root") + 1])
        backup_manifest_arg = s009_call[s009_call.index("--backup-manifest") + 1]
        self.assertIn("recovery-evidence", backup_manifest_arg)
        self.assertNotIn(backup_manifest_arg, str(Path(backup_manifest_arg).parent.parent / "backup-manifest.json"))

    def test_original_fail_manifest_untouched_by_apply(self):
        master = self._build_batch()
        self._create_approval()
        s009 = master["scopes"][len(self.COMPLETED_IDS)]
        before = Path(s009["backup_manifest"]).read_bytes()
        self._mocked_apply()
        after = Path(s009["backup_manifest"]).read_bytes()
        self.assertEqual(before, after)

    def test_failure_at_s010_stops_s011(self):
        self._build_batch()
        self._create_approval()
        with self.assertRaises(metadata_batch.BatchRefused):
            self._mocked_apply(child_returncode_by_scope={"S010": 2})
        state = metadata_batch.read_json(self.state_path)
        self.assertEqual(state["state"], "BLOCKED")
        self.assertEqual(state["scopes"]["S009"]["state"], "VERIFIED")
        self.assertEqual(state["scopes"]["S010"]["state"], "BLOCKED")
        self.assertEqual(
            (state["scopes"]["S011"]["state"], state["scopes"]["S011"]["execution"]),
            ("PREFLIGHT_PASS", "NOT_STARTED"),
        )

    def test_s009_success_required_before_s010_starts(self):
        self._build_batch()
        self._create_approval()
        with self.assertRaises(metadata_batch.BatchRefused):
            self._mocked_apply(child_returncode_by_scope={"S009": 2})
        state = metadata_batch.read_json(self.state_path)
        self.assertEqual(state["scopes"]["S009"]["state"], "BLOCKED")
        self.assertEqual(
            (state["scopes"]["S010"]["state"], state["scopes"]["S010"]["execution"]),
            ("PREFLIGHT_PASS", "NOT_STARTED"),
        )

    def test_full_recovery_reaches_verified_with_all_150_synthetic_files_accounted(self):
        self._build_batch()
        self._create_approval()
        summary, _ = self._mocked_apply()
        self.assertEqual(summary["BATCH RESULT"], "PASS")
        # 2 completed (1 file each) + 3 recovered (1 file each) = 5 total.
        self.assertEqual(summary["FILES VERIFIED"], len(self.COMPLETED_IDS) + len(self.RECOVERY_IDS))
        self.assertEqual(summary["SCOPES VERIFIED"], len(self.COMPLETED_IDS) + len(self.RECOVERY_IDS))
        state = metadata_batch.read_json(self.state_path)
        self.assertEqual(state["state"], "VERIFIED")
        for scope_id in self.COMPLETED_IDS + self.RECOVERY_IDS:
            self.assertEqual(
                (state["scopes"][scope_id]["state"], state["scopes"][scope_id]["execution"]),
                ("VERIFIED", "COMPLETED"),
            )

    def test_completed_scope_evidence_untouched_after_recovery_run(self):
        self._build_batch()
        self._create_approval()
        before = metadata_batch.read_json(self.state_path)["scopes"]["S001"]
        self._mocked_apply()
        after = metadata_batch.read_json(self.state_path)["scopes"]["S001"]
        self.assertEqual(before, after)

    def test_double_invocation_after_success_is_refused(self):
        self._build_batch()
        self._create_approval()
        self._mocked_apply()
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "requires batch BLOCKED"):
            recovery.preflight_recovery(handoff_root=self.handoff, t5_volume=self.t5,
                                        mount_checker=lambda _: True)


if __name__ == "__main__":
    unittest.main()
