from __future__ import annotations

import base64
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from mutagen.id3 import GEOB, ID3

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import serato_performance_metadata as spm  # noqa: E402


def entry(name: str, data: bytes) -> bytes:
    return name.encode("ascii") + b"\x00" + struct.pack(">I", len(data)) + data


def markers2_body() -> bytes:
    color = entry("COLOR", b"\x00\x11\x22\x33")
    cue_data = (
        b"\x00"
        + b"\x02"
        + struct.pack(">I", 12_345)
        + b"\x00"
        + bytes((204, 0, 0))
        + b"\x00\x00"
        + "Drop".encode("utf-8")
        + b"\x00"
    )
    loop_data = (
        b"\x00"
        + b"\x01"
        + struct.pack(">I", 20_000)
        + struct.pack(">I", 24_000)
        + b"\xff\xff\xff\xff"
        + b"\x00"
        + bytes((39, 170, 225))
        + b"\x00"
        + b"\x01"
        + "Loop 8".encode("utf-8")
        + b"\x00"
    )
    inner = (
        b"\x01\x01"
        + color
        + entry("CUE", cue_data)
        + entry("LOOP", loop_data)
        + entry("BPMLOCK", b"\x01")
        + entry("FUTURE", b"abc")
        + b"\x00"
    )
    return b"\x01\x01" + base64.b64encode(inner)


def beatgrid_body() -> bytes:
    return (
        b"\x01\x00"
        + struct.pack(">I", 2)
        + struct.pack(">fI", 1.0, 4)
        + struct.pack(">ff", 3.0, 120.0)
        + b"\x7f"
    )


def wrapped_non_id3(description: str, body: bytes) -> bytes:
    raw = spm.NON_ID3_WRAPPER_PREFIX + description.encode("ascii") + b"\x00" + body
    return base64.b64encode(raw).rstrip(b"=")


class SeratoPerformanceMetadataTests(unittest.TestCase):
    def test_decode_markers2_preserves_cues_loops_color_and_unknown_entries(self):
        decoded = spm.decode_markers2(markers2_body())

        self.assertEqual(decoded["track_color"], [17, 34, 51])
        self.assertTrue(decoded["bpm_lock"])
        self.assertEqual(
            decoded["cues"],
            [
                {
                    "index": 2,
                    "position_ms": 12_345,
                    "color": [204, 0, 0],
                    "name": "Drop",
                }
            ],
        )
        self.assertEqual(
            decoded["loops"],
            [
                {
                    "index": 1,
                    "start_ms": 20_000,
                    "end_ms": 24_000,
                    "color": [39, 170, 225],
                    "locked": True,
                    "name": "Loop 8",
                }
            ],
        )
        self.assertEqual(decoded["unknown_entries"], [{"name": "FUTURE", "length": 3}])

    def test_markers2_fails_closed_on_truncated_entry(self):
        inner = b"\x01\x01" + b"CUE\x00" + struct.pack(">I", 50) + b"short" + b"\x00"
        body = b"\x01\x01" + base64.b64encode(inner)
        with self.assertRaises(spm.SeratoPerformanceError):
            spm.decode_markers2(body)

    def test_markers2_rejects_invalid_base64_instead_of_repairing_it(self):
        with self.assertRaises(spm.SeratoPerformanceError):
            spm.decode_markers2(b"\x01\x01abcde")

    def test_decode_beatgrid_and_convert_to_bpm_anchors(self):
        markers = spm.decode_beatgrid(beatgrid_body())
        anchors = spm.beatgrid_to_anchors(markers)

        self.assertEqual(len(markers), 2)
        self.assertFalse(markers[0]["terminal"])
        self.assertEqual(markers[0]["beats_till_next"], 4)
        self.assertTrue(markers[1]["terminal"])
        self.assertAlmostEqual(markers[1]["bpm"], 120.0, places=5)
        self.assertAlmostEqual(anchors[0]["position_ms"], 1000.0, places=5)
        self.assertAlmostEqual(anchors[0]["bpm"], 120.0, places=5)
        self.assertAlmostEqual(anchors[1]["position_ms"], 3000.0, places=5)
        self.assertAlmostEqual(anchors[1]["bpm"], 120.0, places=5)

    def test_beatgrid_fails_closed_on_length_mismatch(self):
        with self.assertRaises(spm.SeratoPerformanceError):
            spm.decode_beatgrid(beatgrid_body()[:-1])

    def test_non_id3_wrapper_verifies_embedded_field_name(self):
        body = markers2_body()
        wrapped = wrapped_non_id3(spm.MARKERS2_DESC, body)
        self.assertEqual(
            spm.unwrap_non_id3_field(wrapped, expected_description=spm.MARKERS2_DESC),
            body,
        )
        with self.assertRaises(spm.SeratoPerformanceError):
            spm.unwrap_non_id3_field(wrapped, expected_description=spm.BEATGRID_DESC)

    def test_id3_inspection_is_read_only_and_decodes_both_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.mp3"
            tags = ID3()
            tags.add(
                GEOB(
                    encoding=0,
                    mime="application/octet-stream",
                    filename="",
                    desc=spm.MARKERS2_DESC,
                    data=markers2_body(),
                )
            )
            tags.add(
                GEOB(
                    encoding=0,
                    mime="application/octet-stream",
                    filename="",
                    desc=spm.BEATGRID_DESC,
                    data=beatgrid_body(),
                )
            )
            tags.save(path, v2_version=4)
            before_bytes = path.read_bytes()
            before_stat = path.stat()

            report = spm.inspect_file(path)

            after_stat = path.stat()
            self.assertEqual(path.read_bytes(), before_bytes)
            self.assertEqual(after_stat.st_size, before_stat.st_size)
            self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
            self.assertEqual(
                report["frames_present"],
                [spm.BEATGRID_DESC, spm.MARKERS2_DESC],
            )
            self.assertEqual(report["markers2"]["cues"][0]["name"], "Drop")
            self.assertEqual(report["markers2"]["loops"][0]["name"], "Loop 8")
            self.assertAlmostEqual(report["beatgrid"]["anchors"][0]["bpm"], 120.0, places=5)
            self.assertFalse(report["writes_audio"])
            self.assertFalse(report["writes_serato"])
            self.assertFalse(report["writes_rekordbox"])
            self.assertFalse(report["writes_library_index"])

    def test_symlink_is_rejected_before_audio_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.mp3"
            tags = ID3()
            tags.save(target, v2_version=4)
            link = root / "link.mp3"
            link.symlink_to(target)
            with self.assertRaises(spm.SeratoPerformanceError):
                spm.read_serato_frames(link)


if __name__ == "__main__":
    unittest.main()
