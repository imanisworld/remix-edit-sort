from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import rekordbox_bridge_verify  # noqa: E402
import rekordbox_bridge_xml  # noqa: E402
import rekordbox_index  # noqa: E402


def location(path: Path) -> str:
    return "file://localhost" + quote(str(path), safe="/")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RekordboxBridgeVerifyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.music = self.tmp / "Music"
        self.music.mkdir()
        self.one = self._audio("One.mp3", b"one")
        self.two = self._audio("Moved/Two.mp3", b"two")
        self.source = self.tmp / "source.xml"
        self.preview_path = self.tmp / "preview.json"
        self.bridge = self.tmp / "bridge.xml"
        self.fresh = self.tmp / "fresh.xml"
        self._write_source()
        self.preview = self._build_preview()
        self.preview_path.write_text(
            json.dumps(self.preview, indent=2), encoding="utf-8"
        )
        rekordbox_bridge_xml.build_bridge(
            self.source, self.preview_path, self.bridge
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _audio(self, relative: str, payload: bytes) -> Path:
        path = self.music / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def _write_source(self) -> None:
        root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
        ET.SubElement(
            root,
            "PRODUCT",
            {"Name": "rekordbox", "Version": "7.2.17", "Company": "AlphaTheta"},
        )
        collection = ET.SubElement(root, "COLLECTION", {"Entries": "3"})
        old_two = self.tmp / "Old" / "Two.mp3"
        missing = self.tmp / "Old" / "Missing.mp3"
        specs = [
            ("1", "One", self.one),
            ("2", "Two", old_two),
            ("3", "Missing", missing),
        ]
        for track_id, name, path in specs:
            track = ET.SubElement(
                collection,
                "TRACK",
                {
                    "TrackID": track_id,
                    "Name": name,
                    "Artist": "Artist",
                    "Album": "Album",
                    "Genre": "House",
                    "AverageBpm": "120.00",
                    "DateAdded": "2026-01-01",
                    "Comments": f"comment-{track_id}",
                    "Location": location(path),
                },
            )
            ET.SubElement(
                track,
                "TEMPO",
                {"Inizio": "0.000", "Bpm": "120.00", "Metro": "4/4", "Battito": "1"},
            )
            ET.SubElement(
                track,
                "POSITION_MARK",
                {"Name": f"Cue {track_id}", "Type": "0", "Start": "12.500", "Num": "-1"},
            )

        playlists = ET.SubElement(root, "PLAYLISTS")
        folder = ET.SubElement(
            playlists, "NODE", {"Type": "0", "Name": "ROOT", "Count": "2"}
        )
        by_id = ET.SubElement(
            folder,
            "NODE",
            {"Type": "1", "Name": "By ID", "Entries": "3", "KeyType": "0"},
        )
        by_location = ET.SubElement(
            folder,
            "NODE",
            {"Type": "1", "Name": "By Location", "Entries": "3", "KeyType": "1"},
        )
        for track_id, _name, path in specs:
            ET.SubElement(by_id, "TRACK", {"Key": track_id})
            ET.SubElement(by_location, "TRACK", {"Key": location(path)})

        raw = b'<?xml version="1.0" encoding="UTF-8" ?>\n' + ET.tostring(
            root, encoding="utf-8"
        )
        self.source.write_bytes(raw)
        rekordbox_index._parse_export(raw)

    def _candidate(self, file_id: int, path: Path, score: int) -> dict[str, object]:
        return {
            "file_id": file_id,
            "path": str(path),
            "canonical_path": str(path.resolve()),
            "filename": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": digest(path),
            "duration_seconds": 180.0,
            "artist": "Artist",
            "title": path.stem,
            "album": "Album",
            "genre": "House",
            "extension": ".mp3",
            "parse_error": None,
            "score": score,
            "evidence": ["exact_current_path"] if file_id == 1 else ["sha256_move_lineage"],
            "conflicts": [],
            "differences": [],
            "material_change": False,
        }

    def _build_preview(self) -> dict[str, object]:
        c1 = self._candidate(1, self.one, 250)
        c2 = self._candidate(2, self.two, 150)
        tracks = [
            {
                "rekordbox_track_id": "1",
                "old_path": str(self.one),
                "name": "One",
                "artist": "Artist",
                "status": "UNCHANGED",
                "proposed_file_id": 1,
                "proposed_path": str(self.one),
                "bridge_eligible": True,
                "review_required": False,
                "top_candidates": [c1],
            },
            {
                "rekordbox_track_id": "2",
                "old_path": str(self.tmp / "Old" / "Two.mp3"),
                "name": "Two",
                "artist": "Artist",
                "status": "STRONG_CANDIDATE",
                "proposed_file_id": 2,
                "proposed_path": str(self.two),
                "bridge_eligible": True,
                "review_required": False,
                "top_candidates": [c2],
            },
            {
                "rekordbox_track_id": "3",
                "old_path": str(self.tmp / "Old" / "Missing.mp3"),
                "name": "Missing",
                "artist": "Artist",
                "status": "MISSING",
                "proposed_file_id": None,
                "proposed_path": None,
                "bridge_eligible": False,
                "review_required": True,
                "top_candidates": [],
            },
        ]
        raw = self.source.read_bytes()
        return {
            "tool": "rekordbox_relocation_preview",
            "tool_version": "1.0.1",
            "preview_schema_version": 1,
            "safety": {
                "classification": "read-only",
                "sqlite_mode": "read-only + query_only",
                "output": "stdout only",
                "writes_rekordbox": False,
                "writes_serato": False,
                "writes_audio": False,
                "writes_library_index": False,
                "automatic_approval": False,
            },
            "library": {"scan_id": 1, "file_count": 2},
            "rekordbox": {
                "import_id": 1,
                "collection_tracks": 3,
                "source_fingerprint": {
                    "path": str(self.source),
                    "size_bytes": len(raw),
                    "mtime_ns": self.source.stat().st_mtime_ns,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                },
            },
            "serato": {"available": True, "import_id": 1},
            "summary": {
                "total": 3,
                "unchanged": 1,
                "strong_candidate": 1,
                "ambiguous": 0,
                "changed": 0,
                "missing": 1,
                "bridge_eligible": 2,
                "review_required": 1,
            },
            "tracks": tracks,
        }

    def _fresh_root(self) -> ET.Element:
        return ET.fromstring(self.bridge.read_bytes())

    def _write_fresh(self, root: ET.Element) -> None:
        raw = b'<?xml version="1.0" encoding="UTF-8" ?>\n' + ET.tostring(
            root, encoding="utf-8"
        )
        self.fresh.write_bytes(raw)
        rekordbox_index._parse_export(raw)

    def _remap_track_ids(self, root: ET.Element) -> None:
        mapping = {"1": "101", "2": "202"}
        collection = root.find("COLLECTION")
        for track in collection:
            track.set("TrackID", mapping[track.attrib["TrackID"]])
            track.set("DateAdded", "2026-08-28")
        folder = root.find("PLAYLISTS").find("NODE")
        by_id = list(folder)[0]
        for entry in by_id:
            entry.set("Key", mapping[entry.attrib["Key"]])

    def test_passes_with_new_track_ids_dynamic_metadata_and_extra_fresh_content(self):
        root = self._fresh_root()
        self._remap_track_ids(root)
        collection = root.find("COLLECTION")
        extra_path = self._audio("Extra.mp3", b"extra")
        extra = ET.SubElement(
            collection,
            "TRACK",
            {
                "TrackID": "999",
                "Name": "Extra",
                "Artist": "Other",
                "Location": location(extra_path),
            },
        )
        ET.SubElement(extra, "TEMPO", {"Inizio": "0", "Bpm": "100", "Metro": "4/4", "Battito": "1"})
        collection.set("Entries", "3")
        root_playlists = root.find("PLAYLISTS")
        ET.SubElement(
            root_playlists,
            "NODE",
            {"Type": "1", "Name": "Extra Playlist", "Entries": "0", "KeyType": "0"},
        )
        self._write_fresh(root)

        report = rekordbox_bridge_verify.verify(
            self.bridge, self.fresh, self.preview_path
        )
        self.assertEqual(report["verdict"], "PASS")
        self.assertEqual(report["summary"]["required_bridge_tracks"], 2)
        self.assertEqual(report["summary"]["extra_fresh_tracks_allowed"], 1)
        self.assertEqual(report["summary"]["extra_fresh_playlist_nodes_allowed"], 1)
        self.assertEqual(report["summary"]["preview_missing_excluded"], 1)

    def test_missing_bridge_track_fails(self):
        root = self._fresh_root()
        collection = root.find("COLLECTION")
        collection[:] = [x for x in list(collection) if x.attrib["TrackID"] != "2"]
        collection.set("Entries", "1")
        folder = root.find("PLAYLISTS").find("NODE")
        for playlist in list(folder):
            if playlist.attrib["KeyType"] == "0":
                playlist[:] = [x for x in list(playlist) if x.attrib["Key"] != "2"]
            else:
                playlist[:] = [x for x in list(playlist) if x.attrib["Key"] != location(self.two)]
            playlist.set("Entries", "1")
        self._write_fresh(root)
        report = rekordbox_bridge_verify.verify(
            self.bridge, self.fresh, self.preview_path
        )
        self.assertEqual(report["verdict"], "FAIL")
        self.assertTrue(
            any(x["kind"] == "TRACK_MISSING_FROM_FRESH_EXPORT" for x in report["track_findings"])
        )

    def test_duplicate_expected_location_in_fresh_fails(self):
        root = self._fresh_root()
        collection = root.find("COLLECTION")
        duplicate = ET.fromstring(ET.tostring(list(collection)[0]))
        duplicate.set("TrackID", "888")
        collection.append(duplicate)
        collection.set("Entries", "3")
        self._write_fresh(root)
        report = rekordbox_bridge_verify.verify(
            self.bridge, self.fresh, self.preview_path
        )
        self.assertEqual(report["verdict"], "FAIL")
        self.assertTrue(
            any(x["kind"] == "TRACK_LOCATION_AMBIGUOUS_IN_FRESH_EXPORT" for x in report["track_findings"])
        )

    def test_static_metadata_drift_fails(self):
        root = self._fresh_root()
        root.find("COLLECTION")[0].set("Comments", "changed")
        self._write_fresh(root)
        report = rekordbox_bridge_verify.verify(
            self.bridge, self.fresh, self.preview_path
        )
        self.assertEqual(report["verdict"], "FAIL")
        self.assertTrue(
            any(x["kind"] == "STATIC_METADATA_MISMATCH" for x in report["track_findings"])
        )

    def test_cue_drift_fails(self):
        root = self._fresh_root()
        mark = root.find("COLLECTION")[0].find("POSITION_MARK")
        mark.set("Start", "13.000")
        self._write_fresh(root)
        report = rekordbox_bridge_verify.verify(
            self.bridge, self.fresh, self.preview_path
        )
        self.assertEqual(report["verdict"], "FAIL")
        self.assertTrue(any(x["kind"] == "CUE_LOOP_MISMATCH" for x in report["track_findings"]))

    def test_tempo_grid_drift_fails(self):
        root = self._fresh_root()
        tempo = root.find("COLLECTION")[0].find("TEMPO")
        tempo.set("Bpm", "121.000")
        self._write_fresh(root)
        report = rekordbox_bridge_verify.verify(
            self.bridge, self.fresh, self.preview_path
        )
        self.assertEqual(report["verdict"], "FAIL")
        self.assertTrue(any(x["kind"] == "TEMPO_GRID_MISMATCH" for x in report["track_findings"]))

    def test_playlist_order_drift_fails(self):
        root = self._fresh_root()
        folder = root.find("PLAYLISTS").find("NODE")
        playlist = list(folder)[0]
        playlist[:] = list(reversed(list(playlist)))
        self._write_fresh(root)
        report = rekordbox_bridge_verify.verify(
            self.bridge, self.fresh, self.preview_path
        )
        self.assertEqual(report["verdict"], "FAIL")
        self.assertTrue(
            any(x["kind"] == "PLAYLIST_MEMBERSHIP_ORDER_MISMATCH" for x in report["playlist_findings"])
        )

    def test_expected_audio_file_missing_on_disk_fails(self):
        self.fresh.write_bytes(self.bridge.read_bytes())
        self.two.unlink()
        report = rekordbox_bridge_verify.verify(
            self.bridge, self.fresh, self.preview_path
        )
        self.assertEqual(report["verdict"], "FAIL")
        self.assertEqual(report["summary"]["expected_files_missing_on_disk"], 1)

    def test_bridge_and_fresh_must_be_different_files(self):
        with self.assertRaisesRegex(RuntimeError, "must be different files"):
            rekordbox_bridge_verify.verify(
                self.bridge, self.bridge, self.preview_path
            )

    def test_bridge_must_equal_preview_approved_set(self):
        tampered = ET.fromstring(self.bridge.read_bytes())
        collection = tampered.find("COLLECTION")
        collection[:] = list(collection)[:1]
        collection.set("Entries", "1")
        folder = tampered.find("PLAYLISTS").find("NODE")
        for playlist in list(folder):
            playlist[:] = list(playlist)[:1]
            playlist.set("Entries", "1")
        tampered_path = self.tmp / "tampered-bridge.xml"
        tampered_path.write_bytes(
            b'<?xml version="1.0" encoding="UTF-8" ?>\n'
            + ET.tostring(tampered, encoding="utf-8")
        )
        self.fresh.write_bytes(tampered_path.read_bytes())
        with self.assertRaisesRegex(RuntimeError, "does not equal the approved relocation set"):
            rekordbox_bridge_verify.verify(
                tampered_path, self.fresh, self.preview_path
            )


if __name__ == "__main__":
    unittest.main()
