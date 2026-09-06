from __future__ import annotations

import base64
import struct
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

from mutagen.id3 import GEOB, ID3

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rekordbox_index  # noqa: E402
import serato_performance_metadata as spm  # noqa: E402
import serato_rekordbox_performance_verify as verifier  # noqa: E402


def entry(name: str, data: bytes) -> bytes:
    return name.encode("ascii") + b"\x00" + struct.pack(">I", len(data)) + data


def markers2_body() -> bytes:
    cue = (
        b"\x00\x02"
        + struct.pack(">I", 12_345)
        + b"\x00"
        + bytes((204, 0, 0))
        + b"\x00\x00"
        + b"Drop\x00"
    )
    loop = (
        b"\x00\x01"
        + struct.pack(">I", 20_000)
        + struct.pack(">I", 24_000)
        + b"\xff\xff\xff\xff"
        + b"\x00"
        + bytes((39, 170, 225))
        + b"\x00\x01"
        + b"Loop 8\x00"
    )
    inner = (
        b"\x01\x01"
        + entry("COLOR", b"\x00\x11\x22\x33")
        + entry("CUE", cue)
        + entry("LOOP", loop)
        + entry("BPMLOCK", b"\x01")
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


def location(path: Path) -> str:
    return "file://localhost" + quote(str(path), safe="/")


class SeratoRekordboxPerformanceVerifyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.audio = self.tmp / "Track.mp3"

    def tearDown(self):
        self._tmp.cleanup()

    def write_serato_audio(self, *, markers=True, grid=True, malformed=False) -> None:
        tags = ID3()
        if markers:
            body = b"\x01\x01broken" if malformed else markers2_body()
            tags.add(
                GEOB(
                    encoding=0,
                    mime="application/octet-stream",
                    filename="",
                    desc=spm.MARKERS2_DESC,
                    data=body,
                )
            )
        if grid:
            tags.add(
                GEOB(
                    encoding=0,
                    mime="application/octet-stream",
                    filename="",
                    desc=spm.BEATGRID_DESC,
                    data=beatgrid_body(),
                )
            )
        tags.save(self.audio, v2_version=4)

    def track(self, *, cue_start="12.345", cue_name="Drop", cue_rgb=(204, 0, 0),
              loop_start="20.000", loop_end="24.000", loop_rgb=(39, 170, 225),
              tempos=None, extra_mark=False):
        if tempos is None:
            tempos = [("1.000", "120.00"), ("3.000", "120.00")]
        marks = [
            {
                "Name": cue_name,
                "Type": "0",
                "Start": cue_start,
                "Num": "2",
                "Red": str(cue_rgb[0]),
                "Green": str(cue_rgb[1]),
                "Blue": str(cue_rgb[2]),
            },
            {
                "Name": "Loop 8",
                "Type": "4",
                "Start": loop_start,
                "End": loop_end,
                "Num": "1",
                "Red": str(loop_rgb[0]),
                "Green": str(loop_rgb[1]),
                "Blue": str(loop_rgb[2]),
            },
        ]
        if extra_mark:
            marks.append({"Name": "RB only", "Type": "0", "Start": "30.0", "Num": "7"})
        return {
            "track_id": "1",
            "absolute_path": str(self.audio),
            "attributes": {"TrackID": "1", "Location": location(self.audio)},
            "marks": marks,
            "tempos": [
                {"Inizio": position, "Bpm": bpm, "Metro": "4/4", "Battito": "1"}
                for position, bpm in tempos
            ],
        }

    def test_preserved_cue_loop_and_beatgrid_pass_and_audio_is_unchanged(self):
        self.write_serato_audio()
        before = self.audio.read_bytes()
        before_stat = self.audio.stat()

        report = verifier.verify_tracks([self.track(extra_mark=True)])

        after_stat = self.audio.stat()
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["summary"]["source_items_compared"], 4)
        self.assertEqual(report["summary"]["fail_findings"], 0)
        self.assertEqual(self.audio.read_bytes(), before)
        self.assertEqual(after_stat.st_mtime_ns, before_stat.st_mtime_ns)
        unsupported = report["tracks"][0]["unsupported_serato_fields"]
        self.assertTrue(any(item["field"] == "track_color" for item in unsupported))
        self.assertTrue(any(item["field"] == "bpm_lock" for item in unsupported))
        self.assertTrue(any(item["field"] == "loop_locked" for item in unsupported))

    def test_cue_position_name_and_color_drift_fail(self):
        self.write_serato_audio(grid=False)
        report = verifier.verify_tracks([
            self.track(cue_start="12.400", cue_name="Wrong", cue_rgb=(1, 2, 3), tempos=[])
        ])
        kinds = {item["kind"] for item in report["tracks"][0]["findings"]}
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("SERATO_CUE_POSITION_MISMATCH", kinds)
        self.assertIn("SERATO_CUE_NAME_MISMATCH", kinds)
        self.assertIn("SERATO_CUE_COLOR_MISMATCH", kinds)

    def test_loop_end_drift_fails(self):
        self.write_serato_audio(grid=False)
        report = verifier.verify_tracks([self.track(loop_end="24.250", tempos=[])])
        kinds = {item["kind"] for item in report["tracks"][0]["findings"]}
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("SERATO_LOOP_END_MISMATCH", kinds)

    def test_missing_hotcue_number_fails_instead_of_fuzzy_matching(self):
        self.write_serato_audio(grid=False)
        track = self.track(tempos=[])
        track["marks"][0]["Num"] = "6"
        report = verifier.verify_tracks([track])
        kinds = {item["kind"] for item in report["tracks"][0]["findings"]}
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("SERATO_CUE_MISSING", kinds)

    def test_beatgrid_position_and_bpm_drift_fail(self):
        self.write_serato_audio(markers=False)
        report = verifier.verify_tracks([
            self.track(
                tempos=[("1.050", "121.00"), ("3.000", "121.00")]
            )
        ])
        kinds = {item["kind"] for item in report["tracks"][0]["findings"]}
        self.assertEqual(report["verdict"], "FAIL")
        self.assertIn("SERATO_BEATGRID_ANCHOR_MISSING", kinds)
        self.assertIn("SERATO_BEATGRID_BPM_MISMATCH", kinds)

    def test_no_embedded_serato_performance_data_is_no_data_not_failure(self):
        self.write_serato_audio(markers=False, grid=False)
        report = verifier.verify_tracks([self.track(tempos=[])])
        self.assertEqual(report["verdict"], "NO_DATA")
        self.assertEqual(report["tracks"][0]["status"], "NO_DATA")
        self.assertEqual(report["summary"]["source_items_compared"], 0)

    def test_malformed_serato_payload_fails_closed(self):
        self.write_serato_audio(markers=True, grid=False, malformed=True)
        report = verifier.verify_tracks([self.track(tempos=[])])
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(
            report["tracks"][0]["findings"][0]["kind"],
            "SERATO_SOURCE_READ_ERROR",
        )

    def test_xml_entrypoint_uses_exact_location_and_is_read_only(self):
        self.write_serato_audio(markers=True, grid=False)
        root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
        collection = ET.SubElement(root, "COLLECTION", {"Entries": "1"})
        track = ET.SubElement(
            collection,
            "TRACK",
            {"TrackID": "1", "Name": "Anything", "Location": location(self.audio)},
        )
        for mark in self.track(tempos=[])["marks"]:
            ET.SubElement(track, "POSITION_MARK", mark)
        xml = self.tmp / "fresh.xml"
        xml.write_bytes(b'<?xml version="1.0" encoding="UTF-8" ?>\n' + ET.tostring(root))
        rekordbox_index._parse_export(xml.read_bytes())
        before_audio = self.audio.read_bytes()
        before_xml = xml.read_bytes()

        report = verifier.verify_xml(xml)

        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["tracks"][0]["path"], str(self.audio))
        self.assertEqual(self.audio.read_bytes(), before_audio)
        self.assertEqual(xml.read_bytes(), before_xml)


if __name__ == "__main__":
    unittest.main()
