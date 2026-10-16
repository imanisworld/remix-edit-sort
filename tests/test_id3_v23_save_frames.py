"""Every ID3 writer keeps an ID3v2.3 tag spec-correct.

mutagen's default ID3(path) load converts a v2.3 tag to its v2.4 model
(TYER/TDAT -> TDRC, TORY -> TDOR, IPLS -> TIPL), and save(v2_version=3) does
not convert back. Before the fix, writing Grouping to a v2.3 file with raw
frames [TIT2, TYER] left [TIT2, TDRC, TIT1]. These tests read the frame IDs
straight from the file bytes, so mutagen's own translation cannot hide them.

All media is synthetic and lives in a temporary directory.
"""

import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
for entry in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import apply_artist_title_plan  # noqa: E402
import apply_clean_dirty_grouping  # noqa: E402
import apply_clean_dirty_move_plan  # noqa: E402
import apply_genre_needs_check_folder_plan  # noqa: E402
import apply_genre_refinement_plan  # noqa: E402
import apply_grouping_plan  # noqa: E402
import apply_needs_check_recheck_genre_plan  # noqa: E402
import apply_new_music_intake_plan  # noqa: E402
import apply_version_genre_plan  # noqa: E402
import apply_year_bpm_plan  # noqa: E402
import fixtures  # noqa: E402
from mutagen.id3 import (  # noqa: E402
    ID3, IPLS, TCON, TDAT, TDRC, TIT2, TORY, TSOP, TYER,
)
from mutagen.wave import WAVE  # noqa: E402

V24_ONLY_FROM_TRANSLATION = {"TDRC", "TDOR", "TIPL"}


def raw_frames(data: bytes) -> tuple[int, list[tuple[str, bytes]]]:
    """-> (major version, [(frame id, body)]) read from the tag bytes."""
    start = data.index(b"ID3")
    data = data[start:]
    major = data[3]
    size = int.from_bytes(bytes(b & 0x7F for b in data[6:10]), "big")
    body = data[10:10 + size]
    frames, pos = [], 0
    while pos + 10 <= len(body) and body[pos] != 0:
        frame_id = body[pos:pos + 4].decode("ascii")
        size_bytes = body[pos + 4:pos + 8]
        if major == 4:
            frame_size = int.from_bytes(bytes(b & 0x7F for b in size_bytes), "big")
        else:
            frame_size = int.from_bytes(size_bytes, "big")
        frames.append((frame_id, body[pos + 10:pos + 10 + frame_size]))
        pos += 10 + frame_size
    return major, frames


def frame_ids(path: Path) -> tuple[int, list[str]]:
    major, frames = raw_frames(Path(path).read_bytes())
    return major, sorted(frame_id for frame_id, _ in frames)


def raw_text(path: Path, frame_id: str) -> str:
    """Decoded text of one raw frame (v2.3 encodings 0/1 only)."""
    _, frames = raw_frames(Path(path).read_bytes())
    (body,) = [b for fid, b in frames if fid == frame_id]
    codec = "latin-1" if body[0] == 0 else "utf-16"
    return body[1:].decode(codec).rstrip("\x00")


def add_v23_extras(tags) -> None:
    """Frames that mutagen's v2.4 translation rewrites or update_to_v23()
    would drop: a date split over TYER/TDAT, TORY, IPLS, an iTunes TSOP
    sort frame, and a "(17)"-style genre."""
    tags.add(TIT2(encoding=1, text=["Song"]))
    tags.add(TYER(encoding=1, text=["2019"]))
    tags.add(TDAT(encoding=1, text=["0105"]))
    tags.add(TORY(encoding=1, text=["2001"]))
    tags.add(IPLS(encoding=1, people=[["mix", "Someone"]]))
    tags.add(TSOP(encoding=1, text=["Sort Name"]))
    tags.add(TCON(encoding=1, text=["(17)"]))


def write_v23_mp3(path: Path) -> Path:
    fixtures.write_mp3(path)
    tags = ID3()
    add_v23_extras(tags)
    tags.save(path, v2_version=3)
    return path


BASE_V23_IDS = ["IPLS", "TCON", "TDAT", "TIT2", "TORY", "TSOP", "TYER"]


class ID3v23WriterFrameTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="id3-v23-frames-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def mp3(self, name="track.mp3") -> Path:
        path = write_v23_mp3(self.tmp / name)
        self.assertEqual(frame_ids(path), (3, BASE_V23_IDS))
        return path

    def assert_v23_kept(self, path: Path, added=(), genre_written=False):
        major, ids = frame_ids(path)
        self.assertEqual(major, 3)
        self.assertFalse(V24_ONLY_FROM_TRANSLATION & set(ids), ids)
        self.assertEqual(ids, sorted(set(BASE_V23_IDS) | set(added)))
        self.assertEqual(raw_text(path, "TYER"), "2019")
        self.assertEqual(raw_text(path, "TDAT"), "0105")
        self.assertEqual(raw_text(path, "TSOP"), "Sort Name")
        if not genre_written:
            self.assertEqual(raw_text(path, "TCON"), "(17)")

    def test_grouping_writers(self):
        for module in (apply_grouping_plan, apply_clean_dirty_grouping,
                       apply_clean_dirty_move_plan):
            with self.subTest(module=module.__name__):
                path = self.mp3(f"{module.__name__}.mp3")
                module.write_grouping(path, "Energy: Starter", 3)
                self.assert_v23_kept(path, added=["TIT1"])
                self.assertEqual(raw_text(path, "TIT1"), "Energy: Starter")

    def test_genre_writers(self):
        for module in (apply_version_genre_plan, apply_genre_refinement_plan,
                       apply_needs_check_recheck_genre_plan,
                       apply_genre_needs_check_folder_plan):
            with self.subTest(module=module.__name__):
                path = self.mp3(f"{module.__name__}.mp3")
                module.write_genre(path, "Afrobeats")
                self.assert_v23_kept(path, genre_written=True)
                self.assertEqual(raw_text(path, "TCON"), "Afrobeats")

    def test_artist_title_writer_mp3(self):
        path = self.mp3()
        apply_artist_title_plan.write_artist_title(path, "Tems", "Me & U", 3)
        self.assert_v23_kept(path, added=["TPE1"])
        self.assertEqual(raw_text(path, "TPE1"), "Tems")
        self.assertEqual(raw_text(path, "TIT2"), "Me & U")

    def test_artist_title_writer_wav(self):
        path = self.tmp / "track.wav"
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b"\x00\x01" * 800)
        audio = WAVE(path)
        audio.add_tags()
        add_v23_extras(audio.tags)
        audio.save(path, v2_version=3)
        self.assertEqual(frame_ids(path), (3, BASE_V23_IDS))

        apply_artist_title_plan.write_artist_title(path, "Tems", "Free Mind", 3)
        self.assert_v23_kept(path, added=["TPE1"])
        self.assertEqual(raw_text(path, "TIT2"), "Free Mind")

    def test_year_writer_v23_writes_tyer_only(self):
        path = self.mp3()
        apply_year_bpm_plan.write_year_bpm(path, "2020", "120", 3)
        major, ids = frame_ids(path)
        self.assertEqual(major, 3)
        self.assertNotIn("TDRC", ids)
        # TDAT (day/month of the old date) goes with the old year, as the
        # whole TDRC date does on a v2.4 file.
        self.assertEqual(ids, sorted((set(BASE_V23_IDS) - {"TDAT"}) | {"TBPM"}))
        self.assertEqual(raw_text(path, "TYER"), "2020")
        self.assertEqual(raw_text(path, "TBPM"), "120")
        # The writer's own read-back (translated model) still sees the year.
        self.assertEqual(str(ID3(path).getall("TDRC")[0]), "2020")

    def test_year_writer_repairs_a_leaked_tdrc(self):
        """A v2.3 file already carrying an off-spec TDRC (from the old bug)
        gets a clean TYER-only year when its Year is rewritten."""
        path = self.tmp / "leaked.mp3"
        fixtures.write_mp3(path)
        tags = ID3()
        tags.add(TIT2(encoding=1, text=["Song"]))
        tags.add(TDRC(encoding=1, text=["2018"]))
        tags.save(path, v2_version=3)
        self.assertEqual(frame_ids(path), (3, ["TDRC", "TIT2"]))
        apply_year_bpm_plan.write_year_bpm(path, "2018", None, 3)
        self.assertEqual(frame_ids(path), (3, ["TIT2", "TYER"]))

    def test_intake_write_metadata_v23(self):
        path = self.mp3()
        apply_new_music_intake_plan.write_metadata(
            path, genre_value=None, grouping_value="Energy: Shake", version=3,
        )
        self.assert_v23_kept(path, added=["TIT1"])

    def test_intake_year_write_verifies(self):
        path = self.mp3()
        apply_new_music_intake_plan.write_metadata(
            path, genre_value="Afrobeats", grouping_value=None, version=3,
            year_value="2021",
        )
        apply_new_music_intake_plan.verify_year_field(path, "2021", 3)
        self.assertEqual(
            frame_ids(path)[1],
            sorted(set(BASE_V23_IDS) - {"TDAT"}),
        )
        self.assertEqual(raw_text(path, "TYER"), "2021")

    def test_intake_v22_upgrade_writes_v23_frames(self):
        path = fixtures.write_id3v22_mp3(
            self.tmp / "old.mp3", genre="Pop", title="Song", year="2004",
            picture_data=b"\x89PNG-test", comment="note",
        )
        apply_new_music_intake_plan.write_metadata(
            path, genre_value=None, grouping_value="Source: Test", version=3,
            id3_upgrade=dict(apply_new_music_intake_plan.ID3_V22_TO_V23),
        )
        major, ids = frame_ids(path)
        self.assertEqual(major, 3)
        self.assertEqual(ids, ["APIC", "COMM", "TCON", "TIT1", "TIT2", "TYER"])
        self.assertEqual(raw_text(path, "TYER"), "2004")
        self.assertEqual(ID3(path).getall("APIC")[0].mime, "image/png")

    def test_v24_save_unchanged(self):
        """A v2.4 file still goes through mutagen's v2.4 model as before."""
        path = self.tmp / "v24.mp3"
        fixtures.write_mp3(path)
        fixtures.tag_mp3(path, title="Song", year="2019", v2_version=4)
        apply_grouping_plan.write_grouping(path, "Energy: Reset", 4)
        self.assertEqual(frame_ids(path), (4, ["TDRC", "TIT1", "TIT2"]))


if __name__ == "__main__":
    unittest.main()
