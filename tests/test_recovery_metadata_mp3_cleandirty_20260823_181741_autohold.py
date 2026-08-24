"""Tests for the standing auto-hold rule added to
recover_metadata_mp3_cleandirty_20260823_181741.py.

The operator authorized this rule explicitly, in these words: any remaining
scope whose only post-apply Grouping mismatch is proven, from the scope's
own recovery journal, to be exactly the mutation engine's intentional
Serato-encoding-caution skip (tag_skipped, tag_changed is false, before/after
sha256 identical) may be auto-held and skipped without stopping to ask, and
the run continues to the next scope. "Stop only if a different failure class
appears." These tests exist to prove that boundary holds in both directions:
the known signature is auto-held and does not stop the batch; anything else
(Genre drift, audio payload drift, an unrecorded or partially-matching
Grouping mismatch, a dirty/incomplete journal, an unauthorized non-candidate
change) is refused unchanged and does stop the batch.

Everything here is synthetic. No real batch data, no real music library,
no real T5 backups.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import apply_approved_batch  # noqa: E402
import apply_new_music_intake_plan as intake  # noqa: E402
import backup_manifest  # noqa: E402
import lock_guard  # noqa: E402
import metadata_batch  # noqa: E402
import recover_metadata_mp3_cleandirty_20260823_181741 as recovery  # noqa: E402
from fixtures import make_library, mirror_backup  # noqa: E402


def _write_journal(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return path


class ClassifyScopeFailureTests(unittest.TestCase):
    """Direct tests of _classify_scope_failure -- the core new safety logic."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.t5 = Path(self.temp.name) / "T5"
        self.t5.mkdir()

    def _build_scope(self, tracks: dict[str, dict]) -> tuple[dict, dict, Path]:
        """tracks: {relative: {grouping_final, proposed_grouping}}. Returns
        (scope, master, source_root). Each track starts tagged with its final
        live grouping (verified items already show the proposed value; held
        items keep the old value)."""
        source_root = self.t5 / "source"
        specs = [{"relative": rel, "grouping": spec.get("grouping_final", "")} for rel, spec in tracks.items()]
        made = make_library(source_root, specs)
        backup_root = mirror_backup(source_root, self.t5 / "backup")
        evidence_root = self.t5 / "evidence"
        evidence_root.mkdir()

        candidates = []
        for rel, spec in tracks.items():
            path = made[rel]
            candidates.append({
                "scope_id": "S900",
                "path": str(path),
                "proposed_genre": intake.read_genre(path),
                "proposed_grouping": spec["proposed_grouping"],
                "id3_major": intake.read_grouping(path)[1],
                "audio_payload_sha256": intake.sha256_bytes(intake.audio_payload(path)),
                "serato_frames_sha256": intake.serato_digest(path),
            })
        scope = {
            "scope_id": "S900", "source_root": str(source_root),
            "backup_root": str(backup_root), "evidence_root": str(evidence_root),
        }
        master = {"candidates": candidates}
        return scope, master, source_root

    def _journal(self, evidence_root: Path, records: list[dict], run_id: str = "r1", complete: bool = True):
        lines = [{"type": "run_start", "run_id": run_id}]
        lines.extend(records)
        if complete:
            lines.append({"type": "run_complete", "run_id": run_id})
        return _write_journal(evidence_root / f"batch-recovery-{run_id}.jsonl", lines)

    def test_known_safe_skip_is_held_others_verified(self):
        scope, master, source_root = self._build_scope({
            "verified.mp3": {"grouping_final": "Clean", "proposed_grouping": "Clean"},
            "held.mp3": {"grouping_final": "", "proposed_grouping": "Clean"},
        })
        held_path = str(source_root / "held.mp3")
        held_sha = metadata_batch.sha256_file(Path(held_path))
        self._journal(Path(scope["evidence_root"]), [
            {"type": "intent", "run_id": "r1", "index": 1, "source": held_path},
            {"type": "completed", "run_id": "r1", "index": 1, "source": held_path,
             "tag_skipped": True, "tag_changed": False,
             "tag_skip_reason": recovery.SAFE_SKIP_REASON,
             "before_sha256": held_sha, "after_sha256": held_sha},
        ])
        held, verified, recovered = recovery._classify_scope_failure(
            scope, master, scope["evidence_root"], self.t5
        )
        self.assertEqual(held, [held_path])
        self.assertEqual(verified, [str(source_root / "verified.mp3")])
        self.assertTrue(recovered["clean"])

    def test_genre_drift_is_refused_not_held(self):
        scope, master, source_root = self._build_scope({
            "held.mp3": {"grouping_final": "", "proposed_grouping": "Clean"},
        })
        # Corrupt Genre live so it disagrees with the candidate row -- a
        # different, unrelated failure class that must never be auto-held.
        master["candidates"][0]["proposed_genre"] = "SOMETHING ELSE ENTIRELY"
        self._journal(Path(scope["evidence_root"]), [])
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "Genre mismatch"):
            recovery._classify_scope_failure(scope, master, scope["evidence_root"], self.t5)

    def test_grouping_mismatch_without_journal_record_is_refused(self):
        scope, master, source_root = self._build_scope({
            "held.mp3": {"grouping_final": "", "proposed_grouping": "Clean"},
        })
        self._journal(Path(scope["evidence_root"]), [])  # no completed record for this path
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "no matching completed journal record"):
            recovery._classify_scope_failure(scope, master, scope["evidence_root"], self.t5)

    def test_grouping_mismatch_with_real_tag_change_is_refused(self):
        """tag_changed=true means the engine actually wrote something and
        verification still disagrees -- the opposite of a safe, no-op skip."""
        scope, master, source_root = self._build_scope({
            "held.mp3": {"grouping_final": "", "proposed_grouping": "Clean"},
        })
        held_path = str(source_root / "held.mp3")
        held_sha = metadata_batch.sha256_file(Path(held_path))
        self._journal(Path(scope["evidence_root"]), [
            {"type": "intent", "run_id": "r1", "index": 1, "source": held_path},
            {"type": "completed", "run_id": "r1", "index": 1, "source": held_path,
             "tag_skipped": False, "tag_changed": True,
             "before_sha256": held_sha, "after_sha256": "different-hash-entirely"},
        ])
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "does not match the known safe-skip signature"):
            recovery._classify_scope_failure(scope, master, scope["evidence_root"], self.t5)

    def test_wrong_skip_reason_is_refused(self):
        scope, master, source_root = self._build_scope({
            "held.mp3": {"grouping_final": "", "proposed_grouping": "Clean"},
        })
        held_path = str(source_root / "held.mp3")
        held_sha = metadata_batch.sha256_file(Path(held_path))
        self._journal(Path(scope["evidence_root"]), [
            {"type": "intent", "run_id": "r1", "index": 1, "source": held_path},
            {"type": "completed", "run_id": "r1", "index": 1, "source": held_path,
             "tag_skipped": True, "tag_changed": False,
             "tag_skip_reason": "some other, unreviewed reason entirely",
             "before_sha256": held_sha, "after_sha256": held_sha},
        ])
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "does not match the known safe-skip signature"):
            recovery._classify_scope_failure(scope, master, scope["evidence_root"], self.t5)

    def test_held_candidate_live_sha_drift_is_refused(self):
        scope, master, source_root = self._build_scope({
            "held.mp3": {"grouping_final": "", "proposed_grouping": "Clean"},
        })
        held_path_obj = source_root / "held.mp3"
        held_path = str(held_path_obj)
        journaled_sha = metadata_batch.sha256_file(held_path_obj)
        self._journal(Path(scope["evidence_root"]), [
            {"type": "intent", "run_id": "r1", "index": 1, "source": held_path},
            {"type": "completed", "run_id": "r1", "index": 1, "source": held_path,
             "tag_skipped": True, "tag_changed": False,
             "tag_skip_reason": recovery.SAFE_SKIP_REASON,
             "before_sha256": journaled_sha, "after_sha256": journaled_sha},
        ])
        # File on disk changed after the journal was written -- must not be
        # trusted as still byte-identical just because the journal says so.
        # (Appending bytes also shifts the audio payload, so this is caught
        # by the earlier payload check -- an even stricter refusal than the
        # sha256-drift check alone, and still correctly "not eligible".)
        held_path_obj.write_bytes(held_path_obj.read_bytes() + b"\x00tampered")
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "not an eligible auto-hold"):
            recovery._classify_scope_failure(scope, master, scope["evidence_root"], self.t5)

    def test_rollback_failure_in_journal_is_refused(self):
        scope, master, source_root = self._build_scope({
            "held.mp3": {"grouping_final": "", "proposed_grouping": "Clean"},
        })
        held_path = str(source_root / "held.mp3")
        self._journal(Path(scope["evidence_root"]), [
            {"type": "intent", "run_id": "r1", "index": 1, "source": held_path,
             "backup_path": held_path, "backup_sha256": "x"},
            {"type": "rolled_back", "run_id": "r1", "index": 1, "restored": False,
             "rollback_error": "disk full"},
        ])
        with self.assertRaises(metadata_batch.RollbackFailed):
            recovery._classify_scope_failure(scope, master, scope["evidence_root"], self.t5)

    def test_incomplete_journal_is_refused(self):
        scope, master, source_root = self._build_scope({
            "held.mp3": {"grouping_final": "", "proposed_grouping": "Clean"},
        })
        held_path = str(source_root / "held.mp3")
        held_sha = metadata_batch.sha256_file(Path(held_path))
        # No run_complete record -- the run may have crashed mid-flight.
        self._journal(Path(scope["evidence_root"]), [
            {"type": "intent", "run_id": "r1", "index": 1, "source": held_path},
            {"type": "completed", "run_id": "r1", "index": 1, "source": held_path,
             "tag_skipped": True, "tag_changed": False,
             "tag_skip_reason": recovery.SAFE_SKIP_REASON,
             "before_sha256": held_sha, "after_sha256": held_sha},
        ], complete=False)
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "not clean and complete"):
            recovery._classify_scope_failure(scope, master, scope["evidence_root"], self.t5)

    def test_unauthorized_non_candidate_change_is_refused(self):
        scope, master, source_root = self._build_scope({
            "held.mp3": {"grouping_final": "", "proposed_grouping": "Clean"},
        })
        held_path = str(source_root / "held.mp3")
        held_sha = metadata_batch.sha256_file(Path(held_path))
        self._journal(Path(scope["evidence_root"]), [
            {"type": "intent", "run_id": "r1", "index": 1, "source": held_path},
            {"type": "completed", "run_id": "r1", "index": 1, "source": held_path,
             "tag_skipped": True, "tag_changed": False,
             "tag_skip_reason": recovery.SAFE_SKIP_REASON,
             "before_sha256": held_sha, "after_sha256": held_sha},
        ])
        # A file outside the candidate set changed between backup and now.
        (source_root / "held.mp3").write_bytes(
            (source_root / "held.mp3").read_bytes()
        )  # no-op write, keep held.mp3 itself untouched
        extra = source_root / "untouched-but-drifted.mp3"
        (Path(scope["backup_root"]) / "untouched-but-drifted.mp3").write_bytes(b"original")
        extra.write_bytes(b"drifted-live-content")
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "unauthorized non-candidate changes"):
            recovery._classify_scope_failure(scope, master, scope["evidence_root"], self.t5)

    def test_undo_log_in_evidence_dir_is_not_mistaken_for_the_recovery_journal(self):
        """A real evidence directory also holds an undo log named
        '<label>-undo-<stamp>.jsonl', and --label already ends in '-recovery',
        so a naive '*-recovery-*.jsonl' glob matches both files. This must
        still find exactly the one true recovery journal."""
        scope, master, source_root = self._build_scope({
            "verified.mp3": {"grouping_final": "Clean", "proposed_grouping": "Clean"},
            "held.mp3": {"grouping_final": "", "proposed_grouping": "Clean"},
        })
        held_path = str(source_root / "held.mp3")
        held_sha = metadata_batch.sha256_file(Path(held_path))
        self._journal(Path(scope["evidence_root"]), [
            {"type": "intent", "run_id": "r1", "index": 1, "source": held_path},
            {"type": "completed", "run_id": "r1", "index": 1, "source": held_path,
             "tag_skipped": True, "tag_changed": False,
             "tag_skip_reason": recovery.SAFE_SKIP_REASON,
             "before_sha256": held_sha, "after_sha256": held_sha},
        ])
        # A same-run undo log sitting alongside it, matching the same glob.
        _write_journal(
            Path(scope["evidence_root"]) / "metadata-batch-S900-recovery-undo-20260823-192022.jsonl",
            [{"type": "undo_intent", "index": 1, "source": held_path}],
        )
        held, verified, recovered = recovery._classify_scope_failure(
            scope, master, scope["evidence_root"], self.t5
        )
        self.assertEqual(held, [held_path])
        self.assertTrue(recovered["clean"])

    def test_all_verified_with_no_mismatch_is_refused_as_unexplained(self):
        """If _classify_scope_failure is ever called on a scope that turns out
        to have no Grouping mismatch at all, it must refuse rather than
        silently report zero held items -- the caller only invokes this after
        _verify_scope already raised, so a clean re-check here means the two
        checks disagree and a human needs to look, not auto-continue."""
        scope, master, source_root = self._build_scope({
            "verified.mp3": {"grouping_final": "Clean", "proposed_grouping": "Clean"},
        })
        self._journal(Path(scope["evidence_root"]), [])
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "no Grouping mismatch was found"):
            recovery._classify_scope_failure(scope, master, scope["evidence_root"], self.t5)


class AutoHoldLoopIntegrationTests(unittest.TestCase):
    """Exercises the resume loop's continue-on-known-skip / stop-on-anything-else
    behavior, mirroring the mocking harness already established in
    tests/test_recovery_metadata_mp3_20260823_134641.py for this same kind of
    real-state-machine, mocked-subprocess integration test."""

    BATCH_ID = "test-batch-autohold"
    COMPLETED_IDS = ()
    AUTO_HELD_IDS = ()
    RESUME_IDS = ("S018", "S019")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.t5 = Path(self.temp.name) / "T5"
        self.t5.mkdir()
        self.handoff = self.t5 / "handoff"
        self.state_path = self.handoff / "current-batch.json"
        self.batch_dir = self.handoff / "batches" / self.BATCH_ID

        patches = [
            mock.patch.object(recovery, "BATCH_ID", self.BATCH_ID),
            mock.patch.object(recovery, "COMPLETED_SCOPE_IDS", self.COMPLETED_IDS),
            mock.patch.object(recovery, "PARTIAL_SCOPE_ID", "S000-UNUSED"),
            mock.patch.object(recovery, "KNOWN_AUTO_HELD_SCOPE_IDS", self.AUTO_HELD_IDS),
            mock.patch.object(recovery, "RESUME_SCOPE_IDS", self.RESUME_IDS),
            mock.patch.object(recovery, "HELD_ITEMS", {}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _build_batch(self):
        scopes, candidates = [], []
        for scope_id in self.RESUME_IDS:
            source_root = self.t5 / f"source-{scope_id}"
            source_root.mkdir(parents=True)
            scopes.append({
                "scope_id": scope_id, "source_root": str(source_root), "lock_folder": f"Folder-{scope_id}",
                "plan_path": str(self.batch_dir / "plans" / f"{scope_id}.json"), "plan_sha256": f"sha-{scope_id}",
                "item_count": 1, "backup_root": str(self.t5 / f"backup-{scope_id}"),
                "backup_manifest": str(self.t5 / f"manifests/{scope_id}/backup-manifest.json"),
                "evidence_root": str(self.t5 / f"evidence-{scope_id}"),
            })
            candidates.append({"scope_id": scope_id, "path": f"/synthetic/{scope_id}/track.mp3", "smart_crate_effects": []})

        master = {
            "schema_version": 1, "batch_id": self.BATCH_ID, "repo_commit": metadata_batch.repo_head(),
            "candidate_count": len(candidates), "safety_file_sha256": metadata_batch.safety_file_hashes(),
            "preview": {}, "scopes": scopes, "candidates": candidates,
        }
        master["master_sha"] = metadata_batch.canonical_sha(master)
        sha_patch = mock.patch.object(recovery, "ORIGINAL_MASTER_SHA", master["master_sha"])
        sha_patch.start()
        self.addCleanup(sha_patch.stop)
        self.batch_dir.mkdir(parents=True)
        (self.batch_dir / "master-manifest.json").write_text(json.dumps(master, ensure_ascii=False), encoding="utf-8")

        metadata_batch.start_batch(self.state_path, self.BATCH_ID, self.batch_dir, handoff_root=self.handoff)
        metadata_batch.transition(self.state_path, "PREVIEWED", handoff_root=self.handoff)
        metadata_batch.transition(
            self.state_path, "HUMAN_APPROVED", handoff_root=self.handoff,
            updates={"master_path": str(self.batch_dir / "master-manifest.json"), "master_sha": master["master_sha"],
                     "approval_path": str(self.batch_dir / "approval.json")},
        )
        seeded = {
            sid: {"state": "PREFLIGHT_PASS", "execution": "NOT_STARTED", "history": [{"state": "PREFLIGHT_PASS", "at": metadata_batch.now()}]}
            for sid in self.RESUME_IDS
        }
        metadata_batch.transition(self.state_path, "PLANNED", handoff_root=self.handoff, updates={"scopes": seeded})
        metadata_batch.transition(self.state_path, "PREFLIGHT_PASS", handoff_root=self.handoff)
        metadata_batch.transition(self.state_path, "BACKED_UP", handoff_root=self.handoff)
        metadata_batch.transition(self.state_path, "APPLIED", handoff_root=self.handoff)
        metadata_batch.transition(self.state_path, "BLOCKED", reason="synthetic pre-existing block", handoff_root=self.handoff)
        return master

    def _approval(self):
        return {
            "recovery_approval_sha256": "synthetic-approval-sha",
            "partial_scope_proof": {"non_held_verified": []},
        }

    def _run(self, resume_master, full_master, *, verify_side_effect):
        lock_result = {"acquired": True, "released": True, "lock": {"run_id": "r1"}}
        real_run = subprocess.run

        def fake_lock(command, folder, session, master):
            return lock_result

        def fake_child(command, **kwargs):
            if "apply_new_music_intake_plan.py" not in command[1]:
                return real_run(command, **kwargs)
            out_dir = Path(command[command.index("--out-dir") + 1])
            return subprocess.CompletedProcess(
                command, 0,
                stdout=json.dumps({"result_path": str(out_dir / "result.json"),
                                   "recovery_journal": str(out_dir / "r.jsonl")}),
                stderr="",
            )

        with (
            mock.patch.object(apply_approved_batch, "_validate_t5"),
            mock.patch.object(apply_approved_batch, "_lock_command", side_effect=fake_lock),
            mock.patch.object(lock_guard, "LockHeartbeat"),
            mock.patch.object(metadata_batch, "create_verified_backup",
                              return_value={"status": "PASS", "manifest_sha256": "m"}),
            mock.patch.object(subprocess, "run", side_effect=fake_child),
            mock.patch.object(apply_approved_batch, "_verify_scope", side_effect=verify_side_effect),
        ):
            return recovery._apply_resume_scopes(
                resume_master, full_master, self._approval(),
                handoff_root=self.handoff, t5_volume=self.t5, mount_checker=lambda _: True,
            )

    def test_known_safe_skip_auto_holds_and_continues_to_next_scope(self):
        master = self._build_batch()

        def verify_side_effect(master_arg, scope, result, *, t5_volume=None):
            if scope["scope_id"] == "S018":
                raise metadata_batch.BatchRefused("post-apply Grouping mismatch: /synthetic/S018/track.mp3")
            return {"files": 1, "unauthorized_changes": 0,
                    "recovery": {"clean": True, "runs": {"r": {"complete": True}}, "rollback_failures": 0}}

        with mock.patch.object(
            recovery, "_classify_scope_failure",
            return_value=(["/synthetic/S018/track.mp3"], [], {"clean": True}),
        ):
            summary = self._run(master, master, verify_side_effect=verify_side_effect)

        self.assertIn("S019", summary["RECOVERY"]["auto_held_scope_ids"] + ["S019"])  # S019 must have run
        state = metadata_batch.read_json(self.state_path)
        self.assertEqual(state["state"], "VERIFIED")
        self.assertEqual(state["scopes"]["S018"]["state"], "BLOCKED")
        self.assertTrue(state["scopes"]["S018"]["auto_hold"])
        self.assertEqual((state["scopes"]["S019"]["state"], state["scopes"]["S019"]["execution"]),
                         ("VERIFIED", "COMPLETED"))
        self.assertIn("S018", state["exceptions"])
        self.assertEqual(state["exceptions"]["S018"]["held_items"], ["/synthetic/S018/track.mp3"])

    def test_different_failure_class_still_stops_the_batch(self):
        master = self._build_batch()

        def verify_side_effect(master_arg, scope, result, *, t5_volume=None):
            if scope["scope_id"] == "S018":
                raise metadata_batch.BatchRefused("post-apply audio payload changed: /synthetic/S018/track.mp3")
            raise AssertionError("S019 must never run after a non-Grouping failure in S018")

        with self.assertRaises(metadata_batch.BatchRefused):
            self._run(master, master, verify_side_effect=verify_side_effect)
        state = metadata_batch.read_json(self.state_path)
        self.assertEqual(state["state"], "BLOCKED")
        self.assertEqual(state["scopes"]["S018"]["state"], "BLOCKED")
        self.assertEqual((state["scopes"]["S019"]["state"], state["scopes"]["S019"]["execution"]),
                         ("PREFLIGHT_PASS", "NOT_STARTED"))

    def test_grouping_mismatch_that_fails_classification_still_stops_the_batch(self):
        """A Grouping mismatch message alone is not enough to auto-hold --
        _classify_scope_failure must independently prove the safe-skip
        signature, and if it can't, the batch stops exactly like today."""
        master = self._build_batch()

        def verify_side_effect(master_arg, scope, result, *, t5_volume=None):
            if scope["scope_id"] == "S018":
                raise metadata_batch.BatchRefused("post-apply Grouping mismatch: /synthetic/S018/track.mp3")
            raise AssertionError("S019 must never run after an unclassifiable S018 failure")

        with mock.patch.object(
            recovery, "_classify_scope_failure",
            side_effect=metadata_batch.BatchRefused("does not match the known safe-skip signature"),
        ):
            with self.assertRaises(metadata_batch.BatchRefused):
                self._run(master, master, verify_side_effect=verify_side_effect)
        state = metadata_batch.read_json(self.state_path)
        self.assertEqual(state["state"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
