"""Safety tests for plan-scoped tag-only backups.

All sources and backup destinations are synthetic TemporaryDirectory paths.
No test touches the real library, Serato data, or T5 volume.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import backup_manifest  # noqa: E402
import metadata_batch  # noqa: E402


class ScopedTagOnlyBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = self.root / "Music" / "Hip Hop"
        self.library.mkdir(parents=True)
        self.t5 = self.root / "T5"
        self.t5.mkdir()
        self.backup = self.t5 / "backups" / "hip-hop-tag-only"
        self.manifest = self.t5 / "backups" / "hip-hop-tag-only-manifest.json"
        self.plan_path = self.root / "plan.json"

    def _write_source(self, relative: str, payload: bytes) -> Path:
        path = self.library / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def _plan(self, items: list[dict]) -> None:
        self.plan_path.write_text(json.dumps({"items": items}), encoding="utf-8")

    def _item(self, source: Path, relative: str, *, move=False, tag_change=True) -> dict:
        return {
            "source": str(source),
            "source_relative": relative,
            "sha256": metadata_batch.sha256_file(source),
            "move": move,
            "tag_change": tag_change,
        }

    def test_copies_only_tag_change_items_and_manifest_authorizes_only_them(self):
        target = self._write_source("2026/target.mp3", b"target-bytes")
        untouched = self._write_source("2026/untouched.mp3", b"untouched-bytes")
        self._plan([
            self._item(target, "2026/target.mp3"),
            self._item(untouched, "2026/untouched.mp3", tag_change=False),
        ])

        manifest = metadata_batch.create_verified_tag_only_backup(
            self.library, self.plan_path, self.backup, self.manifest,
            t5_volume=self.t5,
        )

        self.assertEqual(manifest["status"], "PASS")
        self.assertEqual(manifest["scope_mode"], "explicit-files")
        self.assertEqual(manifest["file_count"], 1)
        self.assertEqual((self.backup / "2026/target.mp3").read_bytes(), b"target-bytes")
        self.assertFalse((self.backup / "2026/untouched.mp3").exists())
        self.assertEqual(
            backup_manifest.require_item_backed_up(manifest, self.library, target),
            metadata_batch.sha256_file(target),
        )
        with self.assertRaises(backup_manifest.BackupGateError):
            backup_manifest.require_item_backed_up(manifest, self.library, untouched)

    def test_verifier_fails_scoped_manifest_when_copy_differs(self):
        source = self._write_source("song.mp3", b"source")
        self.backup.mkdir(parents=True)
        (self.backup / "song.mp3").write_bytes(b"different")
        manifest = backup_manifest.build_scoped_manifest(
            self.library, self.backup, ["song.mp3"], backup_id="test-scoped"
        )
        self.assertEqual(manifest["status"], "FAIL")
        self.assertTrue(any("differs" in problem for problem in manifest["problems"]))

    def test_refuses_any_move_plan(self):
        source = self._write_source("song.mp3", b"song")
        self._plan([self._item(source, "song.mp3", move=True)])
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "containing moves"):
            metadata_batch.create_verified_tag_only_backup(
                self.library, self.plan_path, self.backup, self.manifest,
                t5_volume=self.t5,
            )
        self.assertFalse(self.backup.exists())

    def test_refuses_source_relative_mismatch_before_copy(self):
        source = self._write_source("song.mp3", b"song")
        self._plan([self._item(source, "wrong.mp3")])
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "source/source_relative mismatch"):
            metadata_batch.create_verified_tag_only_backup(
                self.library, self.plan_path, self.backup, self.manifest,
                t5_volume=self.t5,
            )
        self.assertFalse(self.backup.exists())

    def test_refuses_hash_drift_before_copy(self):
        source = self._write_source("song.mp3", b"original")
        item = self._item(source, "song.mp3")
        source.write_bytes(b"changed")
        self._plan([item])
        with self.assertRaisesRegex(metadata_batch.BatchRefused, "source hash changed"):
            metadata_batch.create_verified_tag_only_backup(
                self.library, self.plan_path, self.backup, self.manifest,
                t5_volume=self.t5,
            )
        self.assertFalse(self.backup.exists())


if __name__ == "__main__":
    unittest.main()
