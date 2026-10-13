"""Synthetic end-to-end checks for the user-run DJ Library Review tool.

Only temporary files and SQLite fixtures are used. No music library, player
database, original History export, or installed app is required by tests.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import dj_library_review as check  # noqa: E402

HASH = lambda data: hashlib.sha256(data).hexdigest()


class LibraryReviewTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.home = Path(work.name)
        self.incoming = self.home / "Downloads"
        self.music = self.home / "Music"
        self.output = self.home / "Reports"
        self.incoming.mkdir()
        self.music.mkdir()
        self.db = self.home / "dj-index.sqlite3"
        self.library_rows = []

    def audio(self, name="song.mp3", data=b"audio-one", *, music=False):
        base = self.music if music else self.incoming
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def index(self):
        conn = sqlite3.connect(self.db)
        conn.executescript("""
            CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE scans(scan_id INTEGER PRIMARY KEY,
                               completed_at TEXT,completed_successfully INTEGER);
            CREATE TABLE files(file_id INTEGER PRIMARY KEY,canonical_path TEXT,
                               display_path TEXT,size_bytes INTEGER,sha256 TEXT);
        """)
        conn.executemany("INSERT INTO meta VALUES (?,?)", [
            ("schema_version", "3"), ("root_canonical", str(self.music))
        ])
        conn.execute("INSERT INTO scans VALUES (1,'2026-10-13T10:00:00Z',1)")
        for i, path in enumerate(self.library_rows, start=1):
            data = path.read_bytes()
            conn.execute("INSERT INTO files VALUES (?,?,?,?,?)", (
                i, str(path.resolve()), str(path), len(data), HASH(data)))
        conn.commit()
        conn.close()
        return self.db

    def test_no_index_does_not_claim_library_duplicate_check(self):
        self.audio()
        data = check.inspect_downloads(self.incoming, None)
        self.assertEqual(data["audio_files_scanned"], 1)
        row = data["items"][0]
        self.assertEqual(row["status"], "NEEDS_REVIEW")
        self.assertEqual(row["library_duplicate_check"], "NOT_CHECKED_NO_INDEX")
        self.assertIn("GENRE_MISSING_OR_UNDECIDED", row["reasons"])

    def test_duplicate_different_names_same_bytes_in_incoming(self):
        self.audio("Artist - Song.mp3", b"same")
        self.audio("Totally Different Title.flac", b"same")
        data = check.inspect_downloads(self.incoming, None)
        self.assertEqual(data["status_counts"]["DUPLICATE_REVIEW"], 2)
        self.assertEqual(
            data["items"][0]["same_bytes_in_incoming"],
            ["Totally Different Title.flac"]
        )
        self.assertEqual(
            data["items"][1]["same_bytes_in_incoming"],
            ["Artist - Song.mp3"]
        )
        self.assertTrue((self.incoming / "Artist - Song.mp3").exists())
        self.assertTrue((self.incoming / "Totally Different Title.flac").exists())

    def test_same_filename_different_bytes_is_never_duplicate(self):
        self.audio("song.mp3", b"first")
        source = self.audio("song.mp3", b"second", music=True)
        self.library_rows = [source]
        index = check.read_index(self.index())
        data = check.inspect_downloads(self.incoming, index)
        self.assertEqual(data["items"][0]["same_bytes_in_library"], [])
        self.assertEqual(data["items"][0]["status"], "NEEDS_REVIEW")

    def test_confirmed_bytes_against_existing_library_index(self):
        self.audio("new-download.mp3", b"same")
        saved = self.audio("Existing/older-name.aiff", b"same", music=True)
        self.library_rows = [saved]
        index = check.read_index(self.index())
        data = check.inspect_downloads(self.incoming, index)
        row = data["items"][0]
        self.assertEqual(row["status"], "DUPLICATE_REVIEW")
        self.assertEqual(row["same_bytes_in_library"][0]["file_id"], 1)
        self.assertEqual(row["same_bytes_in_library"][0]["verified_sha256"], HASH(b"same"))

    def test_changed_or_missing_index_target_cannot_prove_duplicate(self):
        self.audio("new-download.mp3", b"same")
        saved = self.audio("older-name.mp3", b"same", music=True)
        self.library_rows = [saved]
        index = check.read_index(self.index())
        saved.write_bytes(b"different")
        row = check.inspect_downloads(self.incoming, index)["items"][0]
        self.assertEqual(row["same_bytes_in_library"], [])
        saved.unlink()
        self.assertEqual(
            check.inspect_downloads(self.incoming, index)["items"][0]["same_bytes_in_library"], []
        )

    def test_partial_download_and_symlink_are_not_read(self):
        self.audio("okay.mp3", b"one")
        self.audio("unfinished.mp3.crdownload", b"two")
        (self.incoming / "linked.mp3").symlink_to(self.music / "private.mp3")
        data = check.inspect_downloads(self.incoming, None)
        self.assertEqual(data["audio_files_scanned"], 1)
        self.assertEqual(len(data["skipped_partial_downloads"]), 1)
        self.assertEqual(len(data["skipped_symlinks"]), 1)

    def test_max_files_is_visible_in_report(self):
        self.audio("first.mp3", b"one")
        self.audio("second.mp3", b"two")
        data = check.inspect_downloads(self.incoming, None, max_files=1)
        self.assertTrue(data["scan_truncated"])
        self.assertEqual(data["audio_files_scanned"], 1)
        self.assertEqual(data["audio_files_discovered"], 2)
        self.assertIn("incomplete", data["warnings"][0])

    def test_symlinked_intake_root_is_refused(self):
        alias = self.home / "alias"
        alias.symlink_to(self.incoming, target_is_directory=True)
        with self.assertRaisesRegex(check.ReviewRefused, "symlinked"):
            check.inspect_downloads(alias, None)

    def test_nonexistent_index_refused_without_creating_database(self):
        absent = self.home / "absent.sqlite3"
        with self.assertRaises(check.ReviewRefused):
            check.read_index(absent)
        self.assertFalse(absent.exists())

    def test_unfinished_index_scan_cannot_be_used(self):
        self.index()
        con = sqlite3.connect(self.db)
        con.execute("DELETE FROM scans")
        con.commit()
        con.close()
        with self.assertRaisesRegex(check.ReviewRefused, "no completed scan"):
            check.read_index(self.db)

    def _history_fixture(self):
        audio = self.audio("Library Track.mp3", b"record", music=True)
        self.library_rows = [audio]
        self.index()
        export = self.home / "Serato History.csv"
        export.write_text("unverified,history,data\n", encoding="utf-8")
        raw = check.fingerprints([export])
        events = [{
            "event_id": "gig-1", "event_timezone": "America/Indiana/Indianapolis",
            "set_time_verified": True,
            "set_start_local": "2026-11-21T22:00:00",
            "set_end_local": "2026-11-22T02:00:00"
        }]
        plays = [{
            "play_id": "play-1", "played_at": "2026-11-22T00:30:00-05:00",
            "played_status": "PLAYED", "identity_status": "EXACT_INDEXED_FILE",
            "file_id": 1, "track_sha256": HASH(b"record"),
            "source_artifact_sha256": raw[0]["sha256"], "session_id": "serato-session-1",
        }]
        return raw, events, plays, audio

    def test_normalized_history_can_only_make_a_candidate(self):
        raw, events, plays, audio = self._history_fixture()
        result = check.inspect_history_from_values(events, plays, check.read_index(self.db), raw)
        row = result["plays"][0]
        self.assertEqual(row["state"], "CANDIDATE_ONLY")
        self.assertEqual(row["performance_date"], "2026-11-21")
        self.assertEqual(row["played_local_date"], "2026-11-22")
        self.assertFalse(result["proof_of_play"])
        self.assertEqual(result["raw_history_parser"], "NOT_IMPLEMENTED_REAL_EXPORT_SAMPLE_REQUIRED")
        self.assertTrue(audio.exists())

    def test_wrong_history_export_fingerprint_stays_review(self):
        raw, events, plays, _ = self._history_fixture()
        plays[0]["source_artifact_sha256"] = "b" * 64
        result = check.inspect_history_from_values(events, plays, check.read_index(self.db), raw)
        self.assertEqual(result["plays"][0]["state"], "REVIEW")
        self.assertIn("original History export", result["identity_checks"][0]["reasons"][0])

    def test_wrong_track_sha_or_bad_file_id_stays_review(self):
        raw, events, plays, _ = self._history_fixture()
        for change in ({"track_sha256": "a" * 64}, {"file_id": 1234}):
            with self.subTest(change=change):
                row = dict(plays[0], **change)
                outcome = check.inspect_history_from_values(events, [row], check.read_index(self.db), raw)
                self.assertEqual(outcome["plays"][0]["state"], "REVIEW")

    def test_rehash_of_changed_track_blocks_history_match(self):
        raw, events, plays, music = self._history_fixture()
        music.write_bytes(b"changed after index")
        outcome = check.inspect_history_from_values(events, plays, check.read_index(self.db), raw)
        self.assertEqual(outcome["plays"][0]["state"], "REVIEW")

    def test_raw_history_alone_is_not_a_play_record(self):
        raw, _, _, _ = self._history_fixture()
        self.assertEqual(raw[0]["parsing"], "UNVERIFIED_EXPORT_CONTENT")
        self.assertFalse(any("played_at" in record for record in raw))

    def test_missing_raw_history_export_refuses_playback_matching(self):
        self.index()
        events = self.home / "events.json"
        plays = self.home / "plays.json"
        events.write_text("[]")
        plays.write_text("[]")
        with self.assertRaisesRegex(check.ReviewRefused, "source History export"):
            check.inspect_history(events, plays, check.read_index(self.db), [])

    def test_outside_app_inputs_require_database(self):
        with self.assertRaisesRegex(check.ReviewRefused, "index"):
            check.build_report(self.incoming, None, self.home, None, None, None, [], 50)
        with self.assertRaisesRegex(check.ReviewRefused, "index"):
            check.build_report(self.incoming, None, None, self.home / "export.xml", None, None, [], 50)

    def test_reject_report_destination_inside_sources(self):
        self.audio()
        with self.assertRaisesRegex(check.ReviewRefused, "outside"):
            check._safe_report_root(self.incoming / "reports", [self.incoming])
        with self.assertRaisesRegex(check.ReviewRefused, "outside"):
            check._safe_report_root(self.music / "reports", [self.music])

    def test_html_escapes_untrusted_file_and_metadata(self):
        html = check.render_html({
            "index": {"state": "NOT_SUPPLIED"},
            "downloads": {
                "audio_files_scanned": 1,
                "scan_truncated": False,
                "items": [{"relative_path": "<script>alert(1)</script>.mp3",
                           "status": "NEEDS_REVIEW", "genre_tag": "<img src=x>",
                           "reasons": ["TEST"]}],
            },
            "source_drift": None, "event_play_candidates": None
        })
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img", html)

    def test_cli_writes_external_reports_only_and_never_moves_audio(self):
        file = self.audio("DJ - Song.mp3", b"music bytes")
        rc = check.run([
            "--incoming", str(self.incoming),
            "--out-dir", str(self.output),
        ])
        self.assertEqual(rc, 0)
        reports = list(self.output.glob("review-*/review.json"))
        self.assertEqual(len(reports), 1)
        doc = json.loads(reports[0].read_text())
        self.assertEqual(doc["downloads"]["audio_files_scanned"], 1)
        self.assertFalse(doc["automatic_moves_enabled"])
        self.assertEqual(file.read_bytes(), b"music bytes")
        self.assertTrue(reports[0].with_name("review.html").is_file())

    def test_cli_refuses_report_inside_downloads_before_writes(self):
        file = self.audio("song.mp3")
        target = self.incoming / "report-output"
        rc = check.run([
            "--incoming", str(self.incoming),
            "--out-dir", str(target),
        ])
        self.assertEqual(rc, 1)
        self.assertFalse(target.exists())
        self.assertTrue(file.exists())


if __name__ == "__main__":
    unittest.main()
