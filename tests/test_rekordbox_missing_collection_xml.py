from __future__ import annotations

import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import library_index  # noqa: E402
import rekordbox_index  # noqa: E402
import rekordbox_missing_collection_xml  # noqa: E402


class RekordboxMissingCollectionXmlTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.music = self.tmp / "Music"
        self.music.mkdir()
        self.source_xml = self.tmp / "rekordbox.xml"
        self.db = self.tmp / "app" / "library.sqlite3"
        self.output = self.tmp / "missing.xml"

        self.a = self._audio("House/A.mp3", b"audio-a")
        self.b = self._audio("House/B.mp3", b"audio-b")
        self.c = self._audio("House/C.mp3", b"audio-c")
        library_index.scan(self.music, self.db)
        self._write_rekordbox()
        self.rekordbox_report = rekordbox_index.import_rekordbox(
            self.source_xml, self.db
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _audio(self, relative: str, payload: bytes) -> Path:
        path = self.music / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def _location(self, path: Path) -> str:
        return "file://localhost" + quote(str(path), safe="/")

    def _write_rekordbox(self, extra_ids: dict | None = None) -> None:
        # Only A and C exist in Rekordbox's own collection; B has never been imported.
        root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
        collection = ET.SubElement(root, "COLLECTION", {"Entries": "2"})
        for track_id, path in (("11", self.a), ("33", self.c)):
            ET.SubElement(
                collection,
                "TRACK",
                {
                    "TrackID": track_id,
                    "Name": path.stem,
                    "Location": self._location(path),
                },
            )
        playlists = ET.SubElement(root, "PLAYLISTS")
        rootnode = ET.SubElement(playlists, "NODE", {"Name": "ROOT", "Type": "0", "Count": "0"})
        ET.ElementTree(root).write(self.source_xml, encoding="utf-8", xml_declaration=True)

    def test_generates_only_missing_files_as_minimal_playlist(self):
        source_before = self.source_xml.read_bytes()
        db_before = self.db.read_bytes()

        result = rekordbox_missing_collection_xml.generate_missing_collection_xml(
            self.db,
            self.output,
            rekordbox_import_id=None,
            playlist_name="Not Yet In Rekordbox",
            folder_name="Library Completion",
        )

        self.assertEqual(self.source_xml.read_bytes(), source_before)
        self.assertEqual(self.db.read_bytes(), db_before)
        self.assertEqual(result["summary"]["tracks_added"], 1)
        self.assertFalse(result["safety"]["writes_rekordbox_master_db"])
        self.assertFalse(result["safety"]["writes_library_index"])

        raw = self.output.read_bytes()
        parsed_root = ET.fromstring(raw)
        playlists_element = parsed_root.find("PLAYLISTS")
        top_children = list(playlists_element)
        self.assertEqual(len(top_children), 1)
        self.assertEqual(top_children[0].attrib.get("Type"), "0")
        self.assertEqual(top_children[0].attrib.get("Name"), "ROOT")

        parsed = rekordbox_index._parse_export(raw)
        self.assertEqual(parsed.declared_collection_count, 1)
        self.assertEqual(parsed.tracks[0]["absolute_path"], library_index.canonicalize_path(self.b))
        playlist_nodes = [n for n in parsed.playlist_nodes if n["node_type"] == 1]
        self.assertEqual(len(playlist_nodes), 1)
        self.assertEqual(playlist_nodes[0]["name"], "Not Yet In Rekordbox")

    def test_refuses_to_overwrite_existing_output(self):
        self.output.write_bytes(b"placeholder")
        with self.assertRaises(RuntimeError):
            rekordbox_missing_collection_xml.generate_missing_collection_xml(
                self.db,
                self.output,
                rekordbox_import_id=None,
                playlist_name="Not Yet In Rekordbox",
                folder_name="Library Completion",
            )

    def test_refuses_when_nothing_is_missing(self):
        # Reimport a Rekordbox snapshot that already covers every library file.
        root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
        collection = ET.SubElement(root, "COLLECTION", {"Entries": "3"})
        for track_id, path in (("11", self.a), ("22", self.b), ("33", self.c)):
            ET.SubElement(
                collection,
                "TRACK",
                {"TrackID": track_id, "Name": path.stem, "Location": self._location(path)},
            )
        playlists = ET.SubElement(root, "PLAYLISTS")
        ET.SubElement(playlists, "NODE", {"Name": "ROOT", "Type": "0", "Count": "0"})
        full_xml = self.tmp / "rekordbox-full.xml"
        ET.ElementTree(root).write(full_xml, encoding="utf-8", xml_declaration=True)
        rekordbox_index.import_rekordbox(full_xml, self.db)

        with self.assertRaises(RuntimeError):
            rekordbox_missing_collection_xml.generate_missing_collection_xml(
                self.db,
                self.output,
                rekordbox_import_id=None,
                playlist_name="Not Yet In Rekordbox",
                folder_name="Library Completion",
            )

    def test_refuses_when_selected_file_changed_since_scan(self):
        self.b.write_bytes(b"audio-b-modified")
        with self.assertRaises(RuntimeError):
            rekordbox_missing_collection_xml.generate_missing_collection_xml(
                self.db,
                self.output,
                rekordbox_import_id=None,
                playlist_name="Not Yet In Rekordbox",
                folder_name="Library Completion",
            )
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
