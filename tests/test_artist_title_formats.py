"""Artist/Title format safety and preview tag-reader regressions.

All media is synthetic and lives in a temporary directory. No production
library or Serato path is opened by these tests.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
import wave
from pathlib import Path
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
for entry in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import apply_artist_title_plan  # noqa: E402
import backup_manifest  # noqa: E402
import fixtures  # noqa: E402
import media_fixtures  # noqa: E402
import tag_formats  # noqa: E402
import standardize_names  # noqa: E402
from mutagen.id3 import ID3  # noqa: E402


APPLY = SCRIPTS_DIR / "apply_artist_title_plan.py"
FOLDER_LOCK = SCRIPTS_DIR / "folder_lock.py"


def run(*args):
    return subprocess.run(
        [sys.executable, *[str(arg) for arg in args]],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


def write_wav(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(b"\x00\x00\x01\x00" * 512)
    return path


class ArtistTitleFormatTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "library"
        self.root.mkdir()
        self.lock_folder = f"TEST-FIXTURE-artist-title-{uuid.uuid4().hex}"
        self.session = "artist-title-test"
        acquired = run(
            FOLDER_LOCK, "acquire", "--folder", self.lock_folder,
            "--session", self.session, "--agent", "test",
        )
        self.assertEqual(acquired.returncode, 0, acquired.stdout + acquired.stderr)

    def tearDown(self):
        run(
            FOLDER_LOCK, "release", "--folder", self.lock_folder,
            "--session", self.session, "--force", "--reason", "test teardown",
        )
        self._tmp.cleanup()

    def apply_plan(self, path: Path, current_artist: str, current_title: str,
                   proposed_artist: str, proposed_title: str):
        backup = self.tmp / "backup"
        if backup.exists():
            shutil.rmtree(backup)
        shutil.copytree(self.root, backup)
        manifest = fixtures.passing_manifest(
            self.root, backup, self.tmp / "manifest.json"
        )
        plan = {
            "mode": "preview-only",
            "rules": {"approval_required": True},
            "items": [{
                "path": str(path),
                "sha256": backup_manifest.sha256_file(path),
                "current_artist": current_artist,
                "current_title": current_title,
                "proposed_artist": proposed_artist,
                "proposed_title": proposed_title,
            }],
        }
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        result = run(
            APPLY, "--plan", plan_path, "--backup", backup,
            "--backup-manifest", manifest, "--backup-relative-to", self.root,
            "--out-dir", self.tmp / "out", "--apply",
            "--lock-folder", self.lock_folder, "--session", self.session,
        )
        return result

    def test_wav_apply_keeps_riff_header_and_audio_payload(self):
        path = write_wav(self.root / "track.wav")
        before_payload = apply_artist_title_plan.audio_payload(path)

        result = self.apply_plan(path, "", "", "Artist", "Title")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["tags_changed"], 1)
        self.assertEqual(path.read_bytes()[:4], b"RIFF")
        self.assertEqual(apply_artist_title_plan.audio_payload(path), before_payload)
        self.assertEqual(
            apply_artist_title_plan.read_artist_title(path)[:2], ("Artist", "Title")
        )

    def test_m4a_apply_exact_value_readback_audio_serato_and_undo(self):
        from mutagen.mp4 import MP4
        path = media_fixtures.write_tiny(self.root / "02 Pharmacy.m4a", "m4a")
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        audio.tags["\xa9nam"] = ["Pharmacy"]
        audio.tags["----:com.serato.dj:markersv2"] = [b"SERATO-TEST"]
        audio.save()
        original = path.read_bytes()
        before_audio = apply_artist_title_plan.audio_payload(path)
        before_serato = tag_formats.serato_digest(path)
        self.assertEqual(apply_artist_title_plan.read_artist_title(path), ("", "Pharmacy", None))

        result = self.apply_plan(path, "", "Pharmacy", "Shordie Shordie", "Pharmacy")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["tags_changed"], 1)
        self.assertEqual(apply_artist_title_plan.read_artist_title(path), ("Shordie Shordie", "Pharmacy", None))
        self.assertEqual(apply_artist_title_plan.audio_payload(path), before_audio)
        self.assertEqual(tag_formats.serato_digest(path), before_serato)

        undone = run(APPLY, "--undo", payload["undo_log"],
                     "--lock-folder", self.lock_folder, "--session", self.session)
        self.assertEqual(undone.returncode, 0, undone.stdout + undone.stderr)
        self.assertEqual(path.read_bytes(), original)

    def test_m4a_stale_plan_is_refused_without_writing(self):
        path = media_fixtures.write_tiny(self.root / "x.m4a", "m4a")
        before = path.read_bytes()
        result = self.apply_plan(path, "Someone Else", "", "Shordie Shordie", "")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_bytes(), before)

    def test_id3v22_apply_is_skipped_byte_for_byte(self):
        path = fixtures.write_id3v22_mp3(
            self.root / "old.mp3", title="Old Title", artist="Old Artist"
        )
        before = path.read_bytes()
        self.assertEqual(ID3(path).version, (2, 2, 0))

        result = self.apply_plan(
            path, "Old Artist", "Old Title", "New Artist", "New Title"
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["tags_changed"], 0)
        self.assertEqual(len(payload["tags_skipped_id3v22"]), 1)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(ID3(path).version, (2, 2, 0))

    def test_direct_id3v22_write_is_refused_before_save(self):
        path = fixtures.write_id3v22_mp3(self.root / "old.mp3", title="Old")
        before = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "not writable without an upgrade"):
            apply_artist_title_plan.write_artist_title(path, "Artist", "Title", 2)
        self.assertEqual(path.read_bytes(), before)

    def test_truncated_wav_data_chunk_is_rejected(self):
        malformed = b"RIFF" + (100).to_bytes(4, "little") + b"WAVEdata" + (50).to_bytes(4, "little") + b"tiny"
        with self.assertRaisesRegex(RuntimeError, "truncated WAV chunk"):
            apply_artist_title_plan._wav_data_chunk(malformed)


class StandardizeNamesTagReaderTests(unittest.TestCase):
    def test_reads_real_id3v22_without_upgrading_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = fixtures.write_id3v22_mp3(
                Path(tmp) / "old.mp3", title="Title", artist="Artist"
            )
            before = path.read_bytes()
            self.assertEqual(standardize_names.read_tag_pair(path), ("Title", "Artist"))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(ID3(path).version, (2, 2, 0))

    def test_flac_values_are_read_as_strings(self):
        fake = {"title": ["FLAC Title"], "artist": ["FLAC Artist"]}
        with patch.object(standardize_names, "FLAC", return_value=fake):
            self.assertEqual(
                standardize_names.read_tag_pair("fixture.flac"),
                ("FLAC Title", "FLAC Artist"),
            )

    def test_m4a_values_are_read_as_strings_and_none_is_empty(self):
        fake = {"\xa9nam": [123], "\xa9ART": [None]}
        with patch.object(standardize_names, "MP4", return_value=fake):
            self.assertEqual(standardize_names.read_tag_pair("fixture.m4a"), ("123", ""))

    def test_unreadable_or_unsupported_format_returns_empty_strings(self):
        self.assertEqual(standardize_names.read_tag_pair("fixture.wav"), ("", ""))
        with patch.object(standardize_names, "FLAC", side_effect=RuntimeError("bad")):
            self.assertEqual(standardize_names.read_tag_pair("fixture.flac"), ("", ""))


if __name__ == "__main__":
    unittest.main()
