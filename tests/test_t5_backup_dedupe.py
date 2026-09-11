"""T5 SHA-256 backup reuse must preserve the mutation-engine recovery contract."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
for value in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if value not in sys.path:
        sys.path.insert(0, value)

import backup_manifest  # noqa: E402
import mutation_engine  # noqa: E402
import t5_backup_dedupe as dedupe  # noqa: E402


class T5BackupDedupeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.source = self.tmp / "source"
        self.t5 = self.tmp / "T5"
        self.source.mkdir()
        self.t5.mkdir()
        self.index_path = self.tmp / "t5-index.json"
        self.manifest_path = self.tmp / "manifest.json"
        self.lane = self.t5 / "lane-backup"

    def tearDown(self):
        self._tmp.cleanup()

    def _write_source(self, relative: str, data: bytes) -> Path:
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def _write_t5(self, relative: str, data: bytes) -> Path:
        path = self.t5 / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def _build_index(self) -> dict:
        index = dedupe.build_index(self.t5)
        dedupe.write_index(index, self.index_path)
        return dedupe.load_index(self.index_path)

    def _prepare(self) -> dict:
        return dedupe.prepare(
            index_path=self.index_path,
            source_root=self.source,
            lane_backup_root=self.lane,
            manifest_out=self.manifest_path,
            backup_id="test-t5-dedupe",
        )

    def test_matching_content_is_reused_without_duplicate_lane_copy(self):
        source = self._write_source("album/song.mp3", b"same-content")
        existing = self._write_t5("older-backups/song-copy.mp3", source.read_bytes())
        self._build_index()

        report = self._prepare()

        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["reused_index"], 1)
        self.assertEqual(report["copied"], 0)
        self.assertFalse((self.lane / "album/song.mp3").exists())
        manifest = backup_manifest.require_pass(self.manifest_path, self.source)
        self.assertEqual(
            backup_manifest.backup_path_for_item(manifest, self.source, source),
            existing.resolve(),
        )
        self.assertEqual(
            backup_manifest.require_item_backed_up(manifest, self.source, source),
            dedupe.sha256_file(source),
        )

    def test_unmatched_content_is_copied_verified_indexed_and_recoverable(self):
        source = self._write_source("album/new.mp3", b"brand-new-content")
        self._build_index()

        report = self._prepare()

        copied = self.lane / "album/new.mp3"
        self.assertEqual(report["copied"], 1)
        self.assertTrue(copied.is_file())
        self.assertEqual(dedupe.sha256_file(copied), dedupe.sha256_file(source))
        refreshed = dedupe.load_index(self.index_path)
        self.assertIn(
            dedupe.sha256_file(source),
            refreshed["by_sha256"],
        )
        manifest = backup_manifest.require_pass(self.manifest_path, self.source)
        self.assertEqual(
            backup_manifest.backup_path_for_item(manifest, self.source, source),
            copied.resolve(),
        )

    def test_refresh_discovers_new_identical_t5_file_and_reuses_it(self):
        source = self._write_source("new.mp3", b"payload")
        self._build_index()
        later = self._write_t5("later/existing.mp3", source.read_bytes())

        report = self._prepare()

        self.assertEqual(report["reused_index"], 1)
        self.assertEqual(report["copied"], 0)
        manifest = backup_manifest.require_pass(self.manifest_path, self.source)
        self.assertEqual(
            backup_manifest.backup_path_for_item(manifest, self.source, source),
            later.resolve(),
        )

    def test_missing_indexed_t5_path_fails_closed_before_manifest_or_copy(self):
        self._write_source("song.mp3", b"payload")
        indexed = self._write_t5("old/song.mp3", b"other")
        self._build_index()
        indexed.unlink()

        with self.assertRaises(dedupe.BackupDedupeError):
            self._prepare()

        self.assertFalse(self.manifest_path.exists())
        self.assertFalse((self.lane / "song.mp3").exists())

    def test_selected_candidate_is_rehashed_even_if_size_and_mtime_are_unchanged(self):
        source = self._write_source("song.mp3", b"AAAAAAAA")
        candidate = self._write_t5("old/song.mp3", source.read_bytes())
        self._build_index()
        original_stat = candidate.stat()
        candidate.write_bytes(b"BBBBBBBB")
        os.utime(
            candidate,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )

        with self.assertRaises(dedupe.BackupDedupeError) as ctx:
            self._prepare()

        self.assertIn("stale/corrupt", str(ctx.exception))
        self.assertFalse(self.manifest_path.exists())

    def test_duplicate_same_hash_candidates_choose_deterministically(self):
        source = self._write_source("song.mp3", b"same")
        expected = self._write_t5("A/copy.mp3", source.read_bytes())
        self._write_t5("Z/copy.mp3", source.read_bytes())
        self._build_index()

        self._prepare()

        manifest = backup_manifest.require_pass(self.manifest_path, self.source)
        self.assertEqual(
            backup_manifest.backup_path_for_item(manifest, self.source, source),
            expected.resolve(),
        )

    def test_tampered_index_digest_is_refused(self):
        self._write_t5("old/song.mp3", b"payload")
        self._build_index()
        raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        raw["files"][0]["sha256"] = "0" * 64
        self.index_path.write_text(json.dumps(raw), encoding="utf-8")

        with self.assertRaises(dedupe.BackupDedupeError) as ctx:
            dedupe.load_index(self.index_path)
        self.assertIn("digest mismatch", str(ctx.exception))

    def test_explicit_backup_path_is_written_to_recovery_intent(self):
        source = self._write_source("song.mp3", b"same")
        existing = self._write_t5("archive/copy.mp3", source.read_bytes())
        self._build_index()
        self._prepare()
        manifest = backup_manifest.require_pass(self.manifest_path, self.source)
        journal_path = self.tmp / "recovery.jsonl"
        engine = mutation_engine.MutationEngine(
            journal_path,
            None,
            manifest,
            self.source,
            "test",
        )
        engine.start({"items": 1})

        with engine.item(
            1,
            kind=mutation_engine.METADATA,
            source=source,
            expected_sha256=dedupe.sha256_file(source),
        ) as txn:
            txn.record(test_only=True)
        engine.finish({"status": "PASS"})

        intents = [
            row for row in mutation_engine.RecoveryJournal(journal_path).read()
            if row.get("type") == "intent"
        ]
        self.assertEqual(len(intents), 1)
        self.assertEqual(Path(intents[0]["backup_path"]), existing.resolve())
        self.assertEqual(intents[0]["backup_sha256"], dedupe.sha256_file(source))

    def test_legacy_manifest_still_uses_backup_root_relative_path(self):
        source = self._write_source("nested/song.mp3", b"legacy")
        legacy_backup = self.tmp / "legacy-backup"
        target = legacy_backup / "nested/song.mp3"
        target.parent.mkdir(parents=True)
        target.write_bytes(source.read_bytes())
        manifest = backup_manifest.build_manifest(self.source, legacy_backup)
        self.assertEqual(manifest["status"], "PASS")

        self.assertEqual(
            backup_manifest.backup_path_for_item(manifest, self.source, source),
            target.resolve(),
        )


if __name__ == "__main__":
    unittest.main()
