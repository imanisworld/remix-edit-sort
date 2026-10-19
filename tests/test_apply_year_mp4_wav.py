"""apply_year_bpm_plan.py: .mp4 (\xa9day) and .wav (existing ID3 chunk only). Synthetic fixtures."""

import json
import subprocess
import sys
import tempfile
import unittest
import uuid
import wave
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
for entry in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import fixtures  # noqa: E402
import media_fixtures  # noqa: E402
import backup_manifest  # noqa: E402
import apply_year_bpm_plan as Y  # noqa: E402
from mutagen.id3 import GEOB, TIT2  # noqa: E402
from mutagen.mp4 import MP4, MP4FreeForm  # noqa: E402
from mutagen.wave import WAVE  # noqa: E402

APPLY = SCRIPTS_DIR / "apply_year_bpm_plan.py"
FOLDER_LOCK = SCRIPTS_DIR / "folder_lock.py"


def run(*args):
    return subprocess.run([sys.executable, *[str(a) for a in args]], capture_output=True, text=True, cwd=REPO_ROOT)


def write_wav(path, tagged):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as h:
        h.setnchannels(1); h.setsampwidth(2); h.setframerate(44100)
        h.writeframes(b"\x00\x00\x01\x00" * 512)
    if tagged:
        w = WAVE(path); w.add_tags()
        w.tags.add(TIT2(encoding=3, text=["All That Matters"]))
        w.tags.add(GEOB(encoding=0, mime="application/octet-stream", desc="Serato Overview", data=b"\x01\x02ov"))
        w.save(v2_version=3)
    return path


class Mp4WavYearTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.root = self.tmp / "Lib"
        self.mp4 = media_fixtures.write_tiny(self.root / "Sky Club.mp4", "m4a")
        a = MP4(self.mp4); a.tags["----:com.serato.dj:overview"] = [MP4FreeForm(b"serato-bytes")]
        a.tags["tmpo"] = [91]; a.save()
        self.wav = write_wav(self.root / "All That Matters.wav", True)
        self.bare_wav = write_wav(self.root / "bare.wav", False)
        self.backup = fixtures.mirror_backup(self.root, self.tmp / "backup")
        self.manifest = fixtures.passing_manifest(self.root, self.backup, self.tmp / "manifest.json")
        self.lock = f"TEST-FIXTURE-yearmw-{uuid.uuid4().hex}"
        r = run(FOLDER_LOCK, "acquire", "--folder", self.lock, "--session", "s", "--agent", "test")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def tearDown(self):
        run(FOLDER_LOCK, "release", "--folder", self.lock, "--session", "s", "--force", "--reason", "teardown")
        self._tmp.cleanup()

    def item(self, path, year):
        cy, cb, v = Y.read_year_bpm(path)
        return {"path": str(path), "sha256": backup_manifest.sha256_file(path), "id3_version": v,
                "current_year": cy, "current_bpm": cb, "proposed_year": year, "proposed_bpm": None}

    def apply(self, items):
        plan = self.tmp / "plan.json"
        plan.write_text(json.dumps({"mode": "preview-only", "rules": {"approval_required": True}, "items": items}))
        return run(APPLY, "--plan", plan, "--backup", self.backup, "--backup-manifest", self.manifest,
                   "--backup-relative-to", self.root, "--out-dir", self.tmp / "out", "--label", "t", "--apply",
                   "--lock-folder", self.lock, "--session", "s")

    def test_mp4_and_wav_exact_value_readback_audio_serato_undo(self):
        before = {p: p.read_bytes() for p in (self.mp4, self.wav)}
        audio = {p: Y.audio_payload(p) for p in before}
        res = self.apply([self.item(self.mp4, "2012"), self.item(self.wav, "2013")])
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(json.loads(res.stdout)["tags_changed"], 2)
        self.assertEqual(Y.read_year_bpm(self.mp4), ("2012", "91", None))
        self.assertEqual(Y.read_year_bpm(self.wav), ("2013", "", 3))
        self.assertEqual(MP4(self.mp4).tags["\xa9day"], ["2012"])
        self.assertEqual(self.wav.read_bytes()[:4], b"RIFF")
        self.assertEqual(self.wav.read_bytes()[8:12], b"WAVE")
        self.assertEqual(WAVE(self.wav).tags.getall("GEOB")[0].data, b"\x01\x02ov")
        self.assertEqual(bytes(MP4(self.mp4).tags["----:com.serato.dj:overview"][0]), b"serato-bytes")
        for p, a in audio.items():
            self.assertEqual(Y.audio_payload(p), a)
        undo = run(APPLY, "--undo", json.loads(res.stdout)["undo_log"], "--lock-folder", self.lock, "--session", "s")
        self.assertEqual(undo.returncode, 0, undo.stdout + undo.stderr)
        for p, data in before.items():
            self.assertEqual(p.read_bytes(), data)

    def test_wav_without_id3_chunk_is_refused_untouched(self):
        before = self.bare_wav.read_bytes()
        res = self.apply([self.item(self.bare_wav, "2013")])
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("no ID3 chunk", res.stdout + res.stderr)
        self.assertEqual(self.bare_wav.read_bytes(), before)
        with self.assertRaises(RuntimeError):
            Y.write_year_bpm(self.bare_wav, "2013", None, 4)
        self.assertEqual(self.bare_wav.read_bytes(), before)

    def test_stale_mp4_plan_refused(self):
        it = self.item(self.mp4, "2012")
        a = MP4(self.mp4); a.tags["\xa9day"] = ["2020"]; a.save()
        res = self.apply([it])
        self.assertNotEqual(res.returncode, 0)
        self.assertEqual(MP4(self.mp4).tags["\xa9day"], ["2020"])


if __name__ == "__main__":
    unittest.main()
