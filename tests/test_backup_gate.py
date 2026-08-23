"""The verified-backup manifest gate.

A directory existing is not proof of a backup. These tests pin down what the
gate actually requires before it will authorize a production mutation, and --
just as importantly -- that PASS is a computed result, not something a caller
can assert.
"""

import json
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
for path in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import backup_manifest  # noqa: E402
import fixtures  # noqa: E402


class BackupGateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.source = self.tmp / "Hip Hop"
        fixtures.make_library(self.source, [
            {"relative": "a.mp3", "genre": "Hip Hop"},
            {"relative": "Clean/b (Clean).mp3", "genre": "Hip Hop"},
        ])

    def tearDown(self):
        self._tmp.cleanup()

    def _good_manifest(self) -> Path:
        fixtures.mirror_backup(self.source, self.tmp / "backup")
        return fixtures.passing_manifest(self.source, self.tmp / "backup", self.tmp / "m.json")

    # ------------------------------------------------------------------
    def test_complete_backup_passes(self):
        manifest = backup_manifest.require_pass(self._good_manifest(), self.source)
        self.assertEqual(manifest["status"], "PASS")
        self.assertEqual(manifest["file_count"], 2)
        self.assertGreater(manifest["total_bytes"], 0)
        for field in backup_manifest.REQUIRED_FIELDS:
            self.assertIn(field, manifest)

    def test_an_empty_directory_is_not_a_backup(self):
        """The exact hole the old `backup.is_dir()` check left open."""
        (self.tmp / "empty").mkdir()
        manifest_path = fixtures.passing_manifest(self.source, self.tmp / "empty", self.tmp / "m.json")
        raw = json.loads(manifest_path.read_text())
        self.assertEqual(raw["status"], "FAIL")
        with self.assertRaises(backup_manifest.BackupGateError):
            backup_manifest.require_pass(manifest_path, self.source)

    def test_incomplete_backup_fails(self):
        backup = fixtures.mirror_backup(self.source, self.tmp / "backup")
        (backup / "a.mp3").unlink()          # backup died partway through
        manifest_path = fixtures.passing_manifest(self.source, backup, self.tmp / "m.json")
        with self.assertRaises(backup_manifest.BackupGateError) as ctx:
            backup_manifest.require_pass(manifest_path, self.source)
        self.assertIn("not PASS", str(ctx.exception))

    def test_corrupted_backup_copy_fails(self):
        backup = fixtures.mirror_backup(self.source, self.tmp / "backup")
        (backup / "a.mp3").write_bytes(b"truncated")
        manifest_path = fixtures.passing_manifest(self.source, backup, self.tmp / "m.json")
        raw = json.loads(manifest_path.read_text())
        self.assertEqual(raw["status"], "FAIL")
        self.assertTrue(any("differs from source" in problem for problem in raw["problems"]))
        with self.assertRaises(backup_manifest.BackupGateError):
            backup_manifest.require_pass(manifest_path, self.source)

    def test_nfc_source_matches_nfd_backup_when_bytes_are_identical(self):
        """exFAT may decompose a copied filename without changing the file."""
        backup = self.tmp / "backup"
        backup.mkdir()
        source_file = self.source / "a.mp3"
        backup_file = backup / "copy.mp3"
        backup_file.write_bytes(source_file.read_bytes())
        nfc = "Beyoncé - PURE_HONEY - Beyoncé.mp3"
        nfd = unicodedata.normalize("NFD", nfc)
        self.assertNotEqual(nfc, nfd)

        with mock.patch.object(
            backup_manifest,
            "_walk_audio",
            side_effect=[{nfc: source_file}, {nfd: backup_file}],
        ):
            manifest = backup_manifest.build_manifest(self.source, backup)

        self.assertEqual(manifest["status"], "PASS")
        self.assertEqual(manifest["file_count"], 1)
        self.assertEqual(manifest["files"][0]["path"], nfc)
        self.assertTrue(manifest["files"][0]["matched"])
        self.assertEqual(
            manifest["files"][0]["sha256"],
            manifest["files"][0]["backup_sha256"],
        )
        manifest_path = self.tmp / "unicode-manifest.json"
        backup_manifest.write_manifest(manifest, manifest_path)
        loaded = backup_manifest.require_pass(manifest_path, self.source)
        self.assertEqual(
            backup_manifest.require_item_backed_up(
                loaded, self.source, self.source / nfd
            ),
            manifest["files"][0]["sha256"],
        )

    def test_canonical_path_collisions_fail_closed_in_either_tree(self):
        backup = self.tmp / "backup"
        backup.mkdir()
        source_file = self.source / "a.mp3"
        other_source = self.source / "Clean/b (Clean).mp3"
        backup_file = backup / "copy.mp3"
        backup_file.write_bytes(source_file.read_bytes())
        nfc = "Beyoncé.mp3"
        nfd = unicodedata.normalize("NFD", nfc)

        cases = (
            (
                {nfc: source_file, nfd: other_source},
                {nfc: backup_file},
                "source tree",
            ),
            (
                {nfc: source_file},
                {nfc: backup_file, nfd: backup_file},
                "backup tree",
            ),
        )
        for source_files, backup_files, tree_name in cases:
            with self.subTest(tree=tree_name), mock.patch.object(
                backup_manifest,
                "_walk_audio",
                side_effect=[source_files, backup_files],
            ):
                manifest = backup_manifest.build_manifest(self.source, backup)
            self.assertEqual(manifest["status"], "FAIL")
            self.assertTrue(
                any(
                    f"canonical path collision in {tree_name}" in problem
                    for problem in manifest["problems"]
                )
            )

    def test_manifest_for_a_different_folder_is_refused(self):
        """Yesterday's Events backup must not authorize a Hip Hop apply."""
        other = self.tmp / "Events"
        fixtures.make_library(other, [{"relative": "x.mp3", "genre": "Events"}])
        fixtures.mirror_backup(other, self.tmp / "other-backup")
        manifest_path = fixtures.passing_manifest(other, self.tmp / "other-backup", self.tmp / "m.json")

        backup_manifest.require_pass(manifest_path, other)  # valid for its own root
        with self.assertRaises(backup_manifest.BackupGateError) as ctx:
            backup_manifest.require_pass(manifest_path, self.source)
        self.assertIn("Refusing to authorize", str(ctx.exception))

    def test_hand_edited_status_is_detected(self):
        """Flipping FAIL to PASS by hand must not get past the gate."""
        (self.tmp / "empty").mkdir()
        manifest_path = fixtures.passing_manifest(self.source, self.tmp / "empty", self.tmp / "m.json")
        raw = json.loads(manifest_path.read_text())
        self.assertEqual(raw["status"], "FAIL")
        raw["status"] = "PASS"
        manifest_path.write_text(json.dumps(raw))
        with self.assertRaises(backup_manifest.BackupGateError) as ctx:
            backup_manifest.require_pass(manifest_path, self.source)
        self.assertIn("altered since it was written", str(ctx.exception))

    def test_deleting_an_entry_is_detected(self):
        manifest_path = self._good_manifest()
        raw = json.loads(manifest_path.read_text())
        raw["files"].pop()
        manifest_path.write_text(json.dumps(raw))
        with self.assertRaises(backup_manifest.BackupGateError) as ctx:
            backup_manifest.require_pass(manifest_path, self.source)
        self.assertIn("altered since it was written", str(ctx.exception))

    def test_reformatting_the_manifest_is_not_treated_as_tampering(self):
        """The digest covers content, not whitespace or key order."""
        manifest_path = self._good_manifest()
        raw = json.loads(manifest_path.read_text())
        manifest_path.write_text(json.dumps(dict(reversed(list(raw.items()))), indent=8))
        manifest = backup_manifest.require_pass(manifest_path, self.source)
        self.assertEqual(manifest["status"], "PASS")

    def test_missing_required_fields_are_refused(self):
        manifest_path = self._good_manifest()
        raw = json.loads(manifest_path.read_text())
        del raw["repo_sha"]
        manifest_path.write_text(json.dumps(raw))
        with self.assertRaises(backup_manifest.BackupGateError) as ctx:
            backup_manifest.require_pass(manifest_path, self.source)
        self.assertIn("missing required field", str(ctx.exception))

    def test_missing_manifest_file_is_refused(self):
        with self.assertRaises(backup_manifest.BackupGateError):
            backup_manifest.require_pass(self.tmp / "nope.json", self.source)

    def test_unparseable_manifest_is_refused(self):
        bad = self.tmp / "bad.json"
        bad.write_text("{not json")
        with self.assertRaises(backup_manifest.BackupGateError):
            backup_manifest.require_pass(bad, self.source)

    # ------------------------------------------------------------------
    def test_serato_evidence_is_required_when_the_run_declares_it(self):
        fixtures.mirror_backup(self.source, self.tmp / "backup")
        manifest_path = fixtures.passing_manifest(self.source, self.tmp / "backup", self.tmp / "m.json")
        # Passes without Serato...
        backup_manifest.require_pass(manifest_path, self.source)
        # ...and is refused when the run says Serato data is in scope.
        with self.assertRaises(backup_manifest.BackupGateError) as ctx:
            backup_manifest.require_pass(manifest_path, self.source, serato_required=True)
        self.assertIn("Serato backup evidence", str(ctx.exception))

    def test_serato_backup_present_and_non_empty_passes(self):
        serato = self.tmp / "_Serato_backup"
        fixtures.make_library(serato, [{"relative": "database V2", "seed": 99}])
        fixtures.mirror_backup(self.source, self.tmp / "backup")
        manifest_path = fixtures.passing_manifest(
            self.source, self.tmp / "backup", self.tmp / "m.json",
            serato_backup=serato, serato_required=True,
        )
        manifest = backup_manifest.require_pass(manifest_path, self.source, serato_required=True)
        self.assertEqual(manifest["serato"]["result"], "PASS")

    def test_empty_serato_backup_directory_fails(self):
        serato = self.tmp / "_Serato_backup"
        serato.mkdir()
        fixtures.mirror_backup(self.source, self.tmp / "backup")
        manifest_path = fixtures.passing_manifest(
            self.source, self.tmp / "backup", self.tmp / "m.json",
            serato_backup=serato, serato_required=True,
        )
        with self.assertRaises(backup_manifest.BackupGateError):
            backup_manifest.require_pass(manifest_path, self.source, serato_required=True)

    # ------------------------------------------------------------------
    def test_per_item_evidence_covers_only_files_in_the_backup(self):
        manifest = backup_manifest.require_pass(self._good_manifest(), self.source)
        digest = backup_manifest.require_item_backed_up(
            manifest, self.source, self.source / "a.mp3")
        self.assertEqual(len(digest), 64)

        # A file that appeared AFTER the backup was taken has no evidence,
        # so it cannot be mutated even though the run's manifest says PASS.
        fixtures.write_mp3(self.source / "new-arrival.mp3", seed=77)
        with self.assertRaises(backup_manifest.BackupGateError) as ctx:
            backup_manifest.require_item_backed_up(
                manifest, self.source, self.source / "new-arrival.mp3")
        self.assertIn("no verified backup evidence", str(ctx.exception))

    def test_file_outside_the_backed_up_root_is_refused(self):
        manifest = backup_manifest.require_pass(self._good_manifest(), self.source)
        outside = self.tmp / "elsewhere.mp3"
        fixtures.write_mp3(outside, seed=5)
        with self.assertRaises(backup_manifest.BackupGateError):
            backup_manifest.require_item_backed_up(manifest, self.source, outside)

    def test_there_is_no_way_to_stamp_pass_on_an_arbitrary_directory(self):
        """build_manifest computes PASS from evidence; there is no override.

        If a future change added a `force_pass` style flag, this test is the
        one that should start failing.
        """
        signature = backup_manifest.build_manifest.__code__.co_varnames
        for forbidden in ("force", "force_pass", "assume_pass", "skip_hash", "trust"):
            self.assertNotIn(forbidden, signature)
        (self.tmp / "random").mkdir()
        manifest = backup_manifest.build_manifest(self.source, self.tmp / "random")
        self.assertEqual(manifest["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
