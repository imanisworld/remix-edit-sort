"""FLAC and M4A support in apply_grouping_plan.py and apply_year_bpm_plan.py.

Real (tiny) FLAC/M4A fixture files from tests/media, a real folder lock, a
genuinely verified backup manifest, and Serato data planted in the file that
must survive every write byte-for-byte.
"""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
MEDIA = TESTS_DIR / "media"
for entry in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import backup_manifest  # noqa: E402
import fixtures  # noqa: E402
import mutation_engine  # noqa: E402
import tag_formats  # noqa: E402
from mutagen.flac import FLAC  # noqa: E402
from mutagen.mp4 import MP4, MP4FreeForm  # noqa: E402

FOLDER_LOCK = SCRIPTS_DIR / "folder_lock.py"
GROUPING_APPLY = SCRIPTS_DIR / "apply_grouping_plan.py"
YEAR_APPLY = SCRIPTS_DIR / "apply_year_bpm_plan.py"
SERATO_BLOB = b"SERATO-TEST-PAYLOAD\x00\x01\x02"


def run(*args):
    return subprocess.run([sys.executable, *[str(a) for a in args]],
                          capture_output=True, text=True, cwd=REPO_ROOT)


def make_flac(path: Path, grouping=None, date=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MEDIA / "tiny.flac", path)
    audio = FLAC(path)
    if audio.tags is None:
        audio.add_tags()
    audio.tags["SERATO_MARKERS_V2"] = ["U2VyYXRvTWFya2VyczI="]
    audio.tags["SERATO_ANALYSIS"] = ["AgE="]
    if grouping:
        audio.tags["GROUPING"] = [grouping]
    if date:
        audio.tags["DATE"] = [date]
    audio.save()
    return path


def make_m4a(path: Path, grouping=None, day=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MEDIA / "tiny.m4a", path)
    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    audio.tags["----:com.serato.dj:markersv2"] = [MP4FreeForm(SERATO_BLOB)]
    if grouping:
        audio.tags["\xa9grp"] = [grouping]
    if day:
        audio.tags["\xa9day"] = [day]
    audio.save()
    return path


class FlacM4aTagWriterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.root = self.tmp / "library"
        self.root.mkdir()
        self.out = self.tmp / "out"
        self.out.mkdir()
        self.lock_folder = f"TEST-FIXTURE-flac-m4a-{uuid.uuid4().hex}"
        self.session = "flac-m4a-test"
        got = run(FOLDER_LOCK, "acquire", "--folder", self.lock_folder,
                  "--session", self.session, "--agent", "test")
        self.assertEqual(got.returncode, 0, got.stdout + got.stderr)

    def tearDown(self):
        run(FOLDER_LOCK, "release", "--folder", self.lock_folder, "--session",
            self.session, "--force", "--reason", "test teardown")
        self._tmp.cleanup()

    def backup(self):
        backup = fixtures.mirror_backup(self.root, self.tmp / "backup")
        manifest = fixtures.passing_manifest(self.root, backup, self.tmp / "manifest.json")
        return backup, manifest

    def locked(self):
        return ("--lock-folder", self.lock_folder, "--session", self.session)

    def journal(self):
        journals = sorted(p for p in self.out.glob("*recovery-*.jsonl") if "undo" not in p.name)
        self.assertEqual(len(journals), 1, journals)
        return journals[0]

    # ---- Grouping -------------------------------------------------------

    def _grouping_roundtrip(self, path: Path, current: list, combined: str):
        before_bytes = path.read_bytes()
        before_audio = tag_formats.audio_payload(path)
        before_serato = tag_formats.serato_digest(path)
        backup, manifest = self.backup()
        plan = {"mode": "preview-only", "rules": {"approval_required": True}, "items": [{
            "path": str(path), "current_grouping": current,
            "combined_grouping": combined, "id3_version": None,
        }]}
        plan_path = self.tmp / "grouping-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        result = run(GROUPING_APPLY, "--root", self.root, "--plan", plan_path,
                     "--backup", backup, "--backup-manifest", manifest,
                     "--out-dir", self.out, "--label", "t", "--apply", *self.locked())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["grouping_tags_changed"], 1)
        self.assertEqual(tag_formats.read_grouping(path), [combined])
        self.assertEqual(tag_formats.audio_payload(path), before_audio)
        self.assertEqual(tag_formats.serato_digest(path), before_serato)
        self.assertTrue(mutation_engine.recover(self.journal())["clean"])

        undone = run(GROUPING_APPLY, "--undo", payload["undo_log"], *self.locked())
        self.assertEqual(undone.returncode, 0, undone.stdout + undone.stderr)
        self.assertEqual(path.read_bytes(), before_bytes)

    def test_flac_grouping_appends_and_preserves_audio_and_serato(self):
        path = make_flac(self.root / "a.flac", grouping="Existing")
        self._grouping_roundtrip(path, ["Existing"], "Existing; Clean")

    def test_flac_grouping_on_file_without_grouping(self):
        path = make_flac(self.root / "b.flac")
        self._grouping_roundtrip(path, [], "Remix")

    def test_m4a_grouping_appends_and_preserves_audio_and_serato(self):
        path = make_m4a(self.root / "c.m4a", grouping="Existing")
        self._grouping_roundtrip(path, ["Existing"], "Existing; Dirty")

    def test_grouping_plan_drift_is_refused_before_any_write(self):
        path = make_flac(self.root / "d.flac", grouping="Changed")
        before = path.read_bytes()
        backup, manifest = self.backup()
        plan = {"mode": "preview-only", "rules": {"approval_required": True}, "items": [{
            "path": str(path), "current_grouping": ["Original"],
            "combined_grouping": "Original; Clean", "id3_version": None,
        }]}
        plan_path = self.tmp / "p.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        result = run(GROUPING_APPLY, "--root", self.root, "--plan", plan_path,
                     "--backup", backup, "--backup-manifest", manifest,
                     "--out-dir", self.out, "--apply", *self.locked())
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_bytes(), before)

    # ---- Year / BPM -----------------------------------------------------

    def _year_apply(self, path: Path, current_year: str, year, bpm, current_bpm=""):
        backup, manifest = self.backup()
        plan = {"mode": "preview-only", "rules": {"approval_required": True}, "items": [{
            "path": str(path), "id3_version": None,
            "current_year": current_year, "current_bpm": current_bpm,
            "proposed_year": year, "proposed_bpm": bpm,
        }]}
        plan_path = self.tmp / "year-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        return run(YEAR_APPLY, "--plan", plan_path, "--backup", backup,
                   "--backup-manifest", manifest, "--backup-relative-to", self.root,
                   "--out-dir", self.out, "--apply", *self.locked())

    def test_flac_year_and_bpm_written_to_date_and_bpm(self):
        path = make_flac(self.root / "y.flac")
        before_bytes = path.read_bytes()
        before_audio = tag_formats.audio_payload(path)
        before_serato = tag_formats.serato_digest(path)
        result = self._year_apply(path, "", "1997", "96")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(tag_formats.read_year_bpm(path), ("1997", "96"))
        self.assertEqual(FLAC(path).tags["DATE"], ["1997"])
        self.assertEqual(tag_formats.audio_payload(path), before_audio)
        self.assertEqual(tag_formats.serato_digest(path), before_serato)
        self.assertTrue(mutation_engine.recover(self.journal())["clean"])
        undo_log = json.loads(result.stdout)["undo_log"]
        undone = run(YEAR_APPLY, "--undo", undo_log, *self.locked())
        self.assertEqual(undone.returncode, 0, undone.stdout + undone.stderr)
        self.assertEqual(path.read_bytes(), before_bytes)

    def test_m4a_year_written_to_day_and_bpm_to_tmpo(self):
        path = make_m4a(self.root / "y.m4a", day="2024-05-01")
        before_audio = tag_formats.audio_payload(path)
        before_serato = tag_formats.serato_digest(path)
        result = self._year_apply(path, "2024", "2023", "120")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(MP4(path).tags["\xa9day"], ["2023"])
        self.assertEqual(MP4(path).tags["tmpo"], [120])
        self.assertEqual(tag_formats.audio_payload(path), before_audio)
        self.assertEqual(tag_formats.serato_digest(path), before_serato)

    def test_m4a_fractional_bpm_is_refused_and_rolled_back(self):
        path = make_m4a(self.root / "z.m4a")
        before = path.read_bytes()
        result = self._year_apply(path, "", None, "120.5")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_bytes(), before)

    # ---- Serato detection --------------------------------------------------

    def test_serato_digest_detects_flac_comment_and_application_block_changes(self):
        path = make_flac(self.root / "s.flac")
        original = tag_formats.serato_digest(path)
        audio = FLAC(path)
        audio.tags["GROUPING"] = ["not serato"]
        audio.save()
        self.assertEqual(tag_formats.serato_digest(path), original)
        audio = FLAC(path)
        audio.tags["SERATO_MARKERS_V2"] = ["changed"]
        audio.save()
        self.assertNotEqual(tag_formats.serato_digest(path), original)

    def test_serato_digest_detects_m4a_atom_changes(self):
        path = make_m4a(self.root / "s.m4a")
        original = tag_formats.serato_digest(path)
        audio = MP4(path)
        audio.tags["----:com.serato.dj:markersv2"] = [MP4FreeForm(b"different")]
        audio.save()
        self.assertNotEqual(tag_formats.serato_digest(path), original)

    def test_unsupported_formats_fail_closed(self):
        with self.assertRaises(RuntimeError):
            tag_formats.read_grouping(self.root / "x.wav")


if __name__ == "__main__":
    unittest.main()
