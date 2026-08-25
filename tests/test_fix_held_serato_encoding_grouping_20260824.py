"""Tests for the one-time fix_held_serato_encoding_grouping_20260824.py script.

The core question this script answers: when a resave changes the strict
Serato digest but NOT the payload digest (mutagen's known encoding-byte
normalization), is it safe to keep the write instead of rolling it back?
These tests control the before/after digest values directly (mocking
apply_new_music_intake_plan.serato_digest / serato_payload_digest) so the
three real branches -- no digest drift, encoding-only drift, real payload
drift -- are each proven deterministically, independent of whatever
mutagen version happens to be installed.

Everything here is synthetic. No real batch data, no real music library.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import apply_new_music_intake_plan as intake  # noqa: E402
import fix_held_serato_encoding_grouping_20260824 as fixscript  # noqa: E402
import metadata_batch  # noqa: E402
from fixtures import make_library, add_serato_frame  # noqa: E402


class _FakeTxn:
    def __init__(self):
        self.rollback_fns = []
        self.mutated = False
        self.records = []

    def on_rollback(self, fn):
        self.rollback_fns.append(fn)

    def mark_mutated(self):
        self.mutated = True

    def record(self, **kwargs):
        self.records.append(kwargs)


class WriteOneTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = make_library(self.root, [{
            "relative": "track.mp3", "genre": "Hip-Hop", "grouping": "", "v2_version": 3,
        }])["track.mp3"]
        add_serato_frame(self.path)
        self.tag_backup_root = self.root / "tag-backups"
        self.undo_path = self.root / "undo.jsonl"

    def _write(self, add_terms=("Clean",), index=1):
        txn = _FakeTxn()
        record = fixscript._write_one(
            txn, self.path, add_terms=list(add_terms),
            tag_backup_root=self.tag_backup_root, index=index, undo_path=self.undo_path,
        )
        return txn, record

    def test_no_digest_drift_accepted_normally(self):
        txn, record = self._write()
        self.assertTrue(txn.mutated)
        self.assertFalse(record["serato_encoding_only_diff_accepted"])
        self.assertEqual(intake.read_grouping(self.path)[0], ["Clean"])
        self.assertEqual(intake.read_genre(self.path), "Hip-Hop")

    def test_encoding_only_drift_is_accepted_not_rolled_back(self):
        real_payload_digest = intake.serato_payload_digest(self.path)
        calls = {"n": 0}
        real_strict = intake.serato_digest

        def fake_strict(path):
            calls["n"] += 1
            # First call is the "before" read; second is "after" -- simulate
            # the strict digest changing while payload stays identical.
            if calls["n"] == 1:
                return "before-strict-digest"
            return "after-strict-digest-DIFFERENT"

        with mock.patch.object(intake, "serato_digest", side_effect=fake_strict), \
             mock.patch.object(intake, "serato_payload_digest", return_value=real_payload_digest):
            txn, record = self._write()
        self.assertTrue(txn.mutated)
        self.assertTrue(record["serato_encoding_only_diff_accepted"])
        self.assertEqual(intake.read_grouping(self.path)[0], ["Clean"])

    def test_real_payload_drift_is_refused_and_rolled_back(self):
        calls = {"n": 0}

        def fake_strict(path):
            calls["n"] += 1
            return "before" if calls["n"] == 1 else "after-different"

        payload_calls = {"n": 0}

        def fake_payload(path):
            payload_calls["n"] += 1
            return "payload-before" if payload_calls["n"] == 1 else "payload-after-DIFFERENT"

        before_bytes = self.path.read_bytes()
        with mock.patch.object(intake, "serato_digest", side_effect=fake_strict), \
             mock.patch.object(intake, "serato_payload_digest", side_effect=fake_payload):
            txn = _FakeTxn()
            with self.assertRaisesRegex(RuntimeError, "PAYLOAD changed"):
                fixscript._write_one(
                    txn, self.path, add_terms=["Clean"],
                    tag_backup_root=self.tag_backup_root, index=1, undo_path=self.undo_path,
                )
        # The write did happen (tag_changed path ran) but the caller (engine.item's
        # __exit__) is responsible for invoking the registered rollback on
        # exception -- verify a rollback was actually registered and that
        # running it restores the original bytes.
        self.assertEqual(len(txn.rollback_fns), 1)
        txn.rollback_fns[0]()
        self.assertEqual(self.path.read_bytes(), before_bytes)

    def test_audio_payload_change_is_refused(self):
        with mock.patch.object(intake, "audio_payload", side_effect=[b"before", b"AFTER-DIFFERENT", b"AFTER-DIFFERENT"]):
            txn = _FakeTxn()
            with self.assertRaisesRegex(RuntimeError, "audio payload changed"):
                fixscript._write_one(
                    txn, self.path, add_terms=["Clean"],
                    tag_backup_root=self.tag_backup_root, index=1, undo_path=self.undo_path,
                )

    def test_grouping_verification_failure_is_refused(self):
        # add_terms containing a value that combined_grouping would produce,
        # then corrupt what's actually on disk via a mocked read to simulate
        # a verification mismatch.
        real_read_grouping = intake.read_grouping
        calls = {"n": 0}

        def fake_read_grouping(path):
            calls["n"] += 1
            if calls["n"] <= 1:
                return real_read_grouping(path)
            return (["Something", "Unexpected"], 3)

        with mock.patch.object(intake, "read_grouping", side_effect=fake_read_grouping):
            txn = _FakeTxn()
            with self.assertRaisesRegex(RuntimeError, "Grouping tag verification failed"):
                fixscript._write_one(
                    txn, self.path, add_terms=["Clean"],
                    tag_backup_root=self.tag_backup_root, index=1, undo_path=self.undo_path,
                )

    def test_tag_backup_is_written_and_matches_original(self):
        original = self.path.read_bytes()
        txn, record = self._write()
        backup_path = Path(record["tag_backup"])
        self.assertTrue(backup_path.is_file())
        self.assertEqual(backup_path.read_bytes(), original)


class RunPreflightGuardTests(unittest.TestCase):
    """Covers run()'s pre-flight checks -- these must refuse BEFORE any
    lock/backup/write happens, so they're testable without touching T5."""

    def test_refuses_when_live_sha_no_longer_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_library(Path(tmp), [{
                "relative": "track.mp3", "genre": "Hip-Hop", "grouping": "", "v2_version": 3,
            }])["track.mp3"]
            fake_groups = [(str(Path(tmp)), [(str(path), "0" * 64, ["Clean"])])]
            with mock.patch.object(fixscript, "GROUPS", fake_groups):
                with self.assertRaisesRegex(RuntimeError, "no longer matches"):
                    fixscript.run(apply_writes=False)

    def test_refuses_when_grouping_is_not_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_library(Path(tmp), [{
                "relative": "track.mp3", "genre": "Hip-Hop", "grouping": "Already Set", "v2_version": 3,
            }])["track.mp3"]
            sha = intake.sha256_file(path)
            fake_groups = [(str(Path(tmp)), [(str(path), sha, ["Clean"])])]
            with mock.patch.object(fixscript, "GROUPS", fake_groups):
                with self.assertRaisesRegex(RuntimeError, "expected empty Grouping"):
                    fixscript.run(apply_writes=False)

    def test_dry_run_passes_when_state_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = make_library(Path(tmp), [{
                "relative": "track.mp3", "genre": "Hip-Hop", "grouping": "", "v2_version": 3,
            }])["track.mp3"]
            sha = intake.sha256_file(path)
            fake_groups = [(str(Path(tmp)), [(str(path), sha, ["Clean"])])]
            with mock.patch.object(fixscript, "GROUPS", fake_groups):
                result = fixscript.run(apply_writes=False)
        self.assertEqual(result["mode"], "dry-run")
        self.assertTrue(result["all_live_state_matches_held_expectation"])


class HardcodedDataTests(unittest.TestCase):
    """Sanity checks on the hardcoded GROUPS table itself."""

    def test_exactly_eight_items_across_three_groups(self):
        self.assertEqual(len(fixscript.GROUPS), 3)
        total = sum(len(items) for _, items in fixscript.GROUPS)
        self.assertEqual(total, 8)

    def test_no_duplicate_paths(self):
        paths = [path for _, items in fixscript.GROUPS for path, _, _ in items]
        self.assertEqual(len(paths), len(set(paths)))

    def test_every_item_has_exactly_one_add_term(self):
        for _, items in fixscript.GROUPS:
            for _path, _sha, terms in items:
                self.assertEqual(len(terms), 1)
                self.assertIn(terms[0], {"Clean", "Dirty"})

    def test_sha256_values_look_valid(self):
        for _, items in fixscript.GROUPS:
            for _path, sha, _terms in items:
                self.assertEqual(len(sha), 64)
                int(sha, 16)  # raises if not valid hex


if __name__ == "__main__":
    unittest.main()
