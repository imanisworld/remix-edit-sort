"""apply_genre_refinement_plan.py: .wav Genre (existing ID3 chunk only) and the
explicit --allow-serato-studio-content opt-in. Synthetic fixtures only."""

import json
import subprocess
import sys
import tempfile
import unittest
import uuid
import wave
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
for entry in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import apply_genre_refinement_plan as refine  # noqa: E402
import backup_manifest  # noqa: E402
import fixtures  # noqa: E402
import mutation_engine  # noqa: E402
from mutagen.id3 import GEOB, TCON, TIT2  # noqa: E402
from mutagen.wave import WAVE  # noqa: E402

FOLDER_LOCK = SCRIPTS_DIR / "folder_lock.py"


def run(*args):
    return subprocess.run([sys.executable, *[str(a) for a in args]], capture_output=True, text=True, cwd=REPO_ROOT)


def chunks(data):
    out, pos = {}, 12
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        out[cid] = data[pos + 8:pos + 8 + size]
        pos += 8 + size + (size & 1)
    return out


def write_wav(path, tagged, genre="Trap, EDM"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as h:
        h.setnchannels(1); h.setsampwidth(2); h.setframerate(44100)
        h.writeframes(b"\x00\x00\x01\x00" * 512)
    # an extra non-ID3 chunk (like the pack files' cue/LIST/bext) must survive
    data = bytearray(path.read_bytes())
    extra = b"cue " + (4).to_bytes(4, "little") + b"\x00\x00\x00\x00"
    data += extra
    data[4:8] = (len(data) - 8).to_bytes(4, "little")
    path.write_bytes(bytes(data))
    if tagged:
        w = WAVE(path); w.add_tags()
        w.tags.add(TIT2(encoding=3, text=["Kick"]))
        w.tags.add(TCON(encoding=3, text=[genre]))
        w.tags.add(GEOB(encoding=0, mime="application/octet-stream", desc="Serato Overview", data=b"\x01\x02ov"))
        w.save(v2_version=4)
    return path


class GenreWavTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.root = self.tmp / "Content"
        self.lock_folder = f"TEST-FIXTURE-genre-wav-{uuid.uuid4().hex}"
        self.session = "genre-wav-test"
        r = run(FOLDER_LOCK, "acquire", "--folder", self.lock_folder, "--session", self.session, "--agent", "test")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.wav = write_wav(self.root / "Drums/Kit/01 Kick.wav", True)
        self.bare = write_wav(self.root / "Drums/Kit/02 Bare.wav", False)

    def tearDown(self):
        run(FOLDER_LOCK, "release", "--folder", self.lock_folder, "--session", self.session,
            "--force", "--reason", "test teardown")
        self._tmp.cleanup()

    def plan(self, paths, current):
        plan = {
            "mode": "preview-only",
            "rules": {"approval_required": True, "genre_metadata_only": True, "no_move": True, "no_delete": True},
            "items": [{
                "path": str(p), "folder": "Drums", "combined_status": "A_ONLY",
                "current_genre": current, "proposed_genre": "DJ Tools",
                "provenance": {"current_sha256": backup_manifest.sha256_file(p)},
            } for p in paths],
        }
        path = self.tmp / "plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        return path

    def preflight(self):
        backup = fixtures.mirror_backup(self.root, self.tmp / "backup")
        manifest = fixtures.passing_manifest(self.root, backup, self.tmp / "manifest.json")
        return mutation_engine.run_preflight(
            lock_folder=self.lock_folder, session=self.session, manifest_path=manifest, source_root=self.root)

    def test_wav_with_id3_chunk_written_audio_chunks_serato_preserved_and_undo(self):
        before = self.wav.read_bytes()
        out = self.tmp / "out"; out.mkdir()
        with mock.patch.object(refine, "LIBRARY_ROOT", self.root):
            heartbeat, manifest = self.preflight()
            refine.apply(self.plan([self.wav], "Trap, EDM"), manifest, out, "wav", heartbeat)
        after = self.wav.read_bytes()
        self.assertEqual(refine.read_genre(self.wav), "DJ Tools")
        self.assertEqual(after[:4], b"RIFF"); self.assertEqual(after[8:12], b"WAVE")
        self.assertEqual(int.from_bytes(after[4:8], "little"), len(after) - 8)
        b, a = chunks(before), chunks(after)
        for cid in (b"fmt ", b"data", b"cue "):
            self.assertEqual(a[cid], b[cid], cid)
        tags = WAVE(self.wav).tags
        self.assertEqual(tags.getall("GEOB")[0].data, b"\x01\x02ov")
        self.assertEqual(str(tags.getall("TIT2")[0]), "Kick")
        self.assertEqual(len(tags.getall("TCON")), 1)
        result = json.loads(next(out.glob("wav-result-*.json")).read_text())
        self.assertEqual(result["genre_tags_changed"], 1)

        undo_log = next(out.glob("wav-undo-*.jsonl"))
        with mock.patch.object(refine, "LIBRARY_ROOT", self.root):
            heartbeat, _ = self.preflight()
            refine.undo(undo_log, "wav", heartbeat)
        self.assertEqual(self.wav.read_bytes(), before)

    def test_wav_without_id3_chunk_is_skipped_untouched(self):
        before = self.bare.read_bytes()
        self.assertEqual(refine.read_genre(self.bare), "")
        self.assertEqual(refine.tag_skip_reason(self.bare), refine.WAV_NO_ID3_SKIP)
        out = self.tmp / "out"; out.mkdir()
        with mock.patch.object(refine, "LIBRARY_ROOT", self.root):
            heartbeat, manifest = self.preflight()
            refine.apply(self.plan([self.bare], ""), manifest, out, "bare", heartbeat)
        self.assertEqual(self.bare.read_bytes(), before)
        rec = json.loads(next(out.glob("bare-undo-*.jsonl")).read_text().splitlines()[0])
        self.assertTrue(rec["tag_skipped"]); self.assertFalse(rec["tag_changed"])
        with self.assertRaises(RuntimeError):
            refine.write_genre(self.bare, "DJ Tools")
        self.assertEqual(self.bare.read_bytes(), before)


class SeratoStudioOptInTests(unittest.TestCase):
    def test_studio_path_refused_without_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "plan.json"
            p.write_text(json.dumps({
                "mode": "preview-only",
                "rules": {"approval_required": True, "genre_metadata_only": True, "no_move": True, "no_delete": True},
                "items": [{"path": str(refine.SERATO_STUDIO_CONTENT_ROOT / "Serato Packs/Core/x.wav"),
                           "folder": "Serato Packs", "combined_status": "A_ONLY", "current_genre": "Trap",
                           "proposed_genre": "DJ Tools", "provenance": {"current_sha256": "0" * 64}}],
            }))
            self.assertEqual(refine.LIBRARY_ROOT, Path("/Users/djb.a.e/Music/DJ-Music/Music"))
            with self.assertRaisesRegex(RuntimeError, "outside library root"):
                refine.load_and_validate(p)

    def _preflight_root(self, extra):
        seen = {}

        def fake(**kwargs):
            seen.update(kwargs)
            raise SystemExit("stop")
        argv = ["x", "--plan", "/nonexistent/plan.json", "--apply", "--backup-manifest", "/m.json",
                "--lock-folder", "F", "--session", "S", *extra]
        original = refine.LIBRARY_ROOT
        try:
            with mock.patch.object(sys, "argv", argv), mock.patch.object(mutation_engine, "run_preflight", fake):
                with self.assertRaises(SystemExit):
                    refine.main()
        finally:
            refine.LIBRARY_ROOT = original
        return seen["source_root"]

    def test_opt_in_switches_whole_run_root(self):
        self.assertEqual(self._preflight_root(["--allow-serato-studio-content"]), refine.SERATO_STUDIO_CONTENT_ROOT)
        self.assertEqual(self._preflight_root([]), Path("/Users/djb.a.e/Music/DJ-Music/Music"))

    def test_studio_root_is_the_real_trailing_space_folder(self):
        self.assertEqual(str(refine.SERATO_STUDIO_CONTENT_ROOT), "/Users/djb.a.e/Music/Serato Studio /Content")


if __name__ == "__main__":
    unittest.main()
