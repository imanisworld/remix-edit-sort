from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
import tempfile
import time
import unittest
import unicodedata
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import library_index  # noqa: E402


class LibraryIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.music = self.base / "music"
        self.music.mkdir()
        self.db = self.base / "state" / "library.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def write_audio(self, relative: str, data: bytes = b"not-real-audio") -> Path:
        path = self.music / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def scan(self):
        return library_index.scan(self.music, self.db)

    def test_initial_scan(self):
        self.write_audio("a.mp3", b"alpha")
        self.write_audio("b.flac", b"beta")
        result = self.scan()
        self.assertEqual(result["files"], 2)
        self.assertEqual(result["new"], 2)
        self.assertTrue(result["completed_successfully"])

        conn = sqlite3.connect(self.db)
        try:
            rows = conn.execute("SELECT extension, sha256 FROM files ORDER BY extension").fetchall()
        finally:
            conn.close()
        self.assertEqual({row[0] for row in rows}, {".mp3", ".flac"})
        self.assertTrue(all(len(row[1]) == 64 for row in rows))

    def test_unchanged_incremental_scan_does_not_rehash(self):
        self.write_audio("a.mp3", b"alpha")
        self.scan()
        with mock.patch.object(library_index, "sha256_file", side_effect=AssertionError("rehash")):
            result = self.scan()
        self.assertEqual(result["unchanged"], 1)
        self.assertEqual(result["changed"], 0)

    def test_changed_content(self):
        path = self.write_audio("a.mp3", b"alpha")
        first = self.scan()
        self.assertEqual(first["new"], 1)
        time.sleep(0.002)
        path.write_bytes(b"different-content")
        os.utime(path, None)
        second = self.scan()
        self.assertEqual(second["changed"], 1)
        self.assertEqual(second["unchanged"], 0)

    def test_new_and_removed(self):
        old = self.write_audio("old.mp3", b"old")
        first = self.scan()
        old.unlink()
        self.write_audio("new.wav", b"new")
        result = self.scan()
        self.assertEqual(result["new"], 1)
        self.assertEqual(result["removed"], 1)
        self.assertEqual(library_index.status(self.db)["current_files"], 1)

        conn = sqlite3.connect(self.db)
        try:
            history = conn.execute(
                "SELECT state, canonical_path FROM scan_files WHERE scan_id=? ORDER BY state",
                (result["scan_id"],),
            ).fetchall()
            old_scan_history = conn.execute(
                "SELECT COUNT(*) FROM scan_files WHERE scan_id=?",
                (first["scan_id"],),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual({row[0] for row in history}, {"NEW", "REMOVED"})
        self.assertEqual(old_scan_history, 1)

    def test_unambiguous_move(self):
        old = self.write_audio("old/a.mp3", b"same")
        self.scan()
        new = self.music / "new" / "a.mp3"
        new.parent.mkdir()
        old.rename(new)
        result = self.scan()
        self.assertEqual(result["moved"], 1)
        self.assertEqual(result["new"], 0)
        self.assertEqual(result["removed"], 0)
        self.assertEqual(library_index.status(self.db)["current_files"], 1)

    def test_ambiguous_move_remains_ambiguous(self):
        a = self.write_audio("one/a.mp3", b"same")
        b = self.write_audio("two/b.mp3", b"same")
        self.scan()
        a.unlink()
        b.unlink()
        self.write_audio("three/c.mp3", b"same")
        result = self.scan()
        self.assertEqual(result["moved"], 0)
        self.assertEqual(result["new"], 1)
        self.assertEqual(result["removed"], 2)
        conn = sqlite3.connect(self.db)
        try:
            ambiguous = conn.execute(
                "SELECT move_ambiguous FROM scan_files WHERE scan_id=? AND state='NEW'",
                (result["scan_id"],),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(ambiguous, 1)

    def test_one_removed_to_two_identical_new_files_is_ambiguous_not_greedy_move(self):
        old = self.write_audio("old/a.mp3", b"same")
        self.scan()
        old.unlink()
        self.write_audio("new/a.mp3", b"same")
        self.write_audio("new/b.mp3", b"same")

        result = self.scan()
        self.assertEqual(result["moved"], 0)
        self.assertEqual(result["new"], 2)
        self.assertEqual(result["removed"], 1)
        self.assertEqual(library_index.status(self.db)["current_files"], 2)

        conn = sqlite3.connect(self.db)
        try:
            rows = conn.execute(
                "SELECT state, move_ambiguous FROM scan_files WHERE scan_id=? ORDER BY canonical_path",
                (result["scan_id"],),
            ).fetchall()
        finally:
            conn.close()
        new_rows = [row for row in rows if row[0] == "NEW"]
        self.assertEqual(len(new_rows), 2)
        self.assertTrue(all(row[1] == 1 for row in new_rows))

    def test_unicode_and_case_identity(self):
        composed = unicodedata.normalize("NFC", "Beyonce\u0301.MP3")
        path = self.write_audio(composed, b"x")
        snap = library_index.snapshot_path(path)
        self.assertEqual(snap.path_identity, unicodedata.normalize("NFC", snap.canonical_path).casefold())
        self.assertEqual(library_index.audio_extension(path), ".mp3")

    def test_case_identity_collision_fails_closed_on_case_sensitive_hosts(self):
        lower = self.write_audio("track.mp3", b"a")
        upper = self.music / "TRACK.MP3"
        try:
            upper.write_bytes(b"b")
        except OSError:
            self.skipTest("host filesystem does not permit case-distinct names")
        if lower.samefile(upper):
            self.skipTest("host filesystem is case-insensitive")
        with self.assertRaises(RuntimeError):
            self.scan()

    def test_malformed_audio_records_error_without_aborting(self):
        self.write_audio("broken.mp3", b"this is not mp3 data")
        result = self.scan()
        self.assertEqual(result["files"], 1)
        self.assertGreaterEqual(result["errors"], 1)
        conn = sqlite3.connect(self.db)
        try:
            error = conn.execute("SELECT parse_error FROM files").fetchone()[0]
        finally:
            conn.close()
        self.assertTrue(error)

    def test_failed_scan_preserves_previous_trusted_state(self):
        path = self.write_audio("a.mp3", b"alpha")
        first = self.scan()
        time.sleep(0.002)
        path.write_bytes(b"changed")
        os.utime(path, None)
        with mock.patch.object(library_index, "parse_metadata", side_effect=RuntimeError("injected failure")):
            with self.assertRaises(RuntimeError):
                self.scan()
        status = library_index.status(self.db)
        self.assertEqual(status["latest_completed_scan"]["scan_id"], first["scan_id"])
        conn = sqlite3.connect(self.db)
        try:
            sha = conn.execute("SELECT sha256 FROM files").fetchone()[0]
            failed = conn.execute(
                "SELECT COUNT(*) FROM scans WHERE completed_successfully=0"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(sha, hashlib.sha256(b"alpha").hexdigest())
        self.assertEqual(failed, 1)

    def test_supported_format_dispatch(self):
        for ext in ("mp3", "flac", "aiff", "aif", "m4a", "aac", "wav"):
            self.write_audio(f"track.{ext}", ext.encode())
        result = self.scan()
        self.assertEqual(result["files"], 7)
        self.assertEqual(result["new"], 7)

    def test_database_inside_music_root_is_rejected(self):
        self.write_audio("a.mp3")
        with self.assertRaises(ValueError):
            library_index.scan(self.music, self.music / "library.sqlite3")

    def test_database_is_bound_to_first_successful_music_root(self):
        self.write_audio("a.mp3", b"alpha")
        first = self.scan()
        status = library_index.status(self.db)
        self.assertEqual(status["bound_root"], library_index.canonicalize_path(self.music))

        other = self.base / "other-music"
        other.mkdir()
        (other / "b.mp3").write_bytes(b"beta")
        with self.assertRaises(ValueError):
            library_index.scan(other, self.db)

        after = library_index.status(self.db)
        self.assertEqual(after["latest_completed_scan"]["scan_id"], first["scan_id"])
        self.assertEqual(after["current_files"], 1)
        conn = sqlite3.connect(self.db)
        try:
            scan_count = conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0]
            sha = conn.execute("SELECT sha256 FROM files").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(scan_count, 1)
        self.assertEqual(sha, hashlib.sha256(b"alpha").hexdigest())

    def test_failed_first_scan_does_not_bind_database_root(self):
        self.write_audio("a.mp3", b"alpha")
        with mock.patch.object(library_index, "parse_metadata", side_effect=RuntimeError("injected failure")):
            with self.assertRaises(RuntimeError):
                self.scan()
        self.assertIsNone(library_index.status(self.db)["bound_root"])

        other = self.base / "other-music"
        other.mkdir()
        (other / "b.mp3").write_bytes(b"beta")
        result = library_index.scan(other, self.db)
        self.assertTrue(result["completed_successfully"])
        self.assertEqual(library_index.status(self.db)["bound_root"], library_index.canonicalize_path(other))

    def test_symlink_is_not_followed(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.mp3").write_bytes(b"secret")
        try:
            (self.music / "linked").symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        result = self.scan()
        self.assertEqual(result["files"], 0)


if __name__ == "__main__":
    unittest.main()
