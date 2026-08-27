from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock
from urllib.parse import quote

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import library_index  # noqa: E402
import rekordbox_index  # noqa: E402


class RekordboxIndexTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.library = self.tmp / "Music"
        self.library.mkdir()
        self.xml = self.tmp / "rekordbox.xml"
        self.db = self.tmp / "app" / "library.sqlite3"

    def tearDown(self):
        self._tmp.cleanup()

    def add_audio(self, relative: str, payload: bytes) -> Path:
        path = self.library / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def location(self, path: Path) -> str:
        return "file://localhost" + quote(str(path), safe="/")

    def track(
        self,
        parent: ET.Element,
        track_id: str,
        path: Path,
        *,
        name: str,
        artist: str = "Artist",
        mark: bool = False,
        tempo: bool = False,
    ) -> ET.Element:
        element = ET.SubElement(
            parent,
            "TRACK",
            {
                "TrackID": track_id,
                "Name": name,
                "Artist": artist,
                "Album": "Album",
                "Genre": "House",
                "Remixer": "Editor",
                "Tonality": "8A",
                "Comments": "Keep",
                "DateAdded": "2026-08-27",
                "BitRate": "320",
                "SampleRate": "44100",
                "PlayCount": "3",
                "LastPlayed": "2026-08-26",
                "Rating": "255",
                "Location": self.location(path),
            },
        )
        if tempo:
            ET.SubElement(
                element,
                "TEMPO",
                {"Inizio": "0.441", "Bpm": "128.00", "Metro": "4/4", "Battito": "1"},
            )
        if mark:
            ET.SubElement(
                element,
                "POSITION_MARK",
                {
                    "Name": "Drop",
                    "Type": "0",
                    "Start": "32.000",
                    "Num": "0",
                    "Red": "255",
                    "Green": "0",
                    "Blue": "0",
                },
            )
        return element

    def write_export(
        self,
        tracks: list[dict[str, object]],
        *,
        include_playlists: bool = True,
        missing_reference: bool = False,
    ) -> None:
        root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
        ET.SubElement(
            root,
            "PRODUCT",
            {"Name": "rekordbox", "Version": "7.2.17", "Company": "AlphaTheta"},
        )
        collection = ET.SubElement(root, "COLLECTION", {"Entries": str(len(tracks))})
        for item in tracks:
            self.track(collection, **item)
        if include_playlists:
            playlists = ET.SubElement(root, "PLAYLISTS")
            folder = ET.SubElement(playlists, "NODE", {"Name": "ROOT", "Type": "0", "Count": "2"})
            by_id = ET.SubElement(
                folder,
                "NODE",
                {"Name": "Gig", "Type": "1", "KeyType": "0", "Entries": "2"},
            )
            ET.SubElement(by_id, "TRACK", {"Key": str(tracks[0]["track_id"])})
            ET.SubElement(by_id, "TRACK", {"Key": "not-in-collection" if missing_reference else str(tracks[-1]["track_id"])})
            by_location = ET.SubElement(
                folder,
                "NODE",
                {"Name": "Warmup", "Type": "1", "KeyType": "1", "Entries": "1"},
            )
            ET.SubElement(by_location, "TRACK", {"Key": self.location(Path(tracks[0]["path"]))})
        ET.ElementTree(root).write(self.xml, encoding="utf-8", xml_declaration=True)

    def build_index(self) -> None:
        library_index.scan(self.library, self.db)

    def test_import_preserves_relationships_and_uncertain_match_states(self):
        exact = self.add_audio("House/Exact.mp3", b"exact")
        moved = self.add_audio("Pop/Moved.mp3", b"moved")
        self.add_audio("A/Shared.mp3", b"first")
        self.add_audio("B/Shared.mp3", b"second")
        self.build_index()

        old_moved = self.library / "Old" / moved.name
        old_shared = self.library / "Old" / "Shared.mp3"
        missing = self.library / "Old" / "Gone.mp3"
        self.write_export(
            [
                {"track_id": "1", "path": exact, "name": "Exact", "mark": True, "tempo": True},
                {"track_id": "2", "path": old_moved, "name": "Moved"},
                {"track_id": "3", "path": old_shared, "name": "Shared"},
                {"track_id": "4", "path": missing, "name": "Gone"},
            ],
            missing_reference=True,
        )
        source_before = (self.xml.read_bytes(), self.xml.stat().st_mtime_ns)

        report = rekordbox_index.import_rekordbox(self.xml, self.db)

        self.assertEqual(report["collection_tracks"], 4)
        self.assertEqual(report["playlist_nodes"], 3)
        self.assertEqual(report["playlists"], 2)
        self.assertEqual(report["playlist_references"], 3)
        self.assertEqual(report["unresolved_playlist_references"], 1)
        self.assertEqual(report["position_marks"], 1)
        self.assertEqual(report["tempos"], 1)
        self.assertEqual(report["exact_path"], 1)
        self.assertEqual(report["moved_likely"], 1)
        self.assertEqual(report["ambiguous"], 1)
        self.assertEqual(report["missing"], 1)

        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            rows = {
                row["rekordbox_track_id"]: row
                for row in conn.execute(
                    "SELECT * FROM rekordbox_tracks WHERE import_id=?", (report["import_id"],)
                )
            }
            memberships = list(
                conn.execute(
                    "SELECT * FROM rekordbox_playlist_memberships WHERE import_id=? ORDER BY node_key,ordinal",
                    (report["import_id"],),
                )
            )
            mark = conn.execute(
                "SELECT * FROM rekordbox_position_marks WHERE import_id=?", (report["import_id"],)
            ).fetchone()
            tempo = conn.execute(
                "SELECT * FROM rekordbox_tempos WHERE import_id=?", (report["import_id"],)
            ).fetchone()
            node_paths = [
                row[0]
                for row in conn.execute(
                    "SELECT node_path FROM rekordbox_playlist_nodes WHERE import_id=? ORDER BY node_key",
                    (report["import_id"],),
                )
            ]
            base_schema = conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()[0]
            adapter_schema = conn.execute(
                "SELECT value FROM meta WHERE key='rekordbox_schema_version'"
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(rows["1"]["match_state"], "EXACT_PATH")
        self.assertIsNotNone(rows["1"]["file_id"])
        self.assertEqual(rows["1"]["name"], "Exact")
        self.assertEqual(rows["1"]["remixer"], "Editor")
        self.assertEqual(rows["2"]["match_state"], "MOVED_LIKELY")
        self.assertIsNone(rows["2"]["file_id"])
        self.assertIsNone(rows["2"]["matched_display_path"])
        self.assertEqual(len(json.loads(rows["2"]["candidates_json"])), 1)
        self.assertEqual(rows["3"]["match_state"], "AMBIGUOUS")
        self.assertEqual(len(json.loads(rows["3"]["candidates_json"])), 2)
        self.assertEqual(rows["4"]["match_state"], "MISSING")
        self.assertEqual([row["reference_state"] for row in memberships].count("MISSING"), 1)
        self.assertEqual(mark["name"], "Drop")
        self.assertEqual(tempo["bpm"], "128.00")
        self.assertEqual(node_paths, ["ROOT", "ROOT / Gig", "ROOT / Warmup"])
        self.assertEqual(base_schema, str(library_index.SCHEMA_VERSION))
        self.assertEqual(adapter_schema, str(rekordbox_index.REKORDBOX_SCHEMA_VERSION))
        self.assertEqual(self.xml.read_bytes(), source_before[0])
        self.assertEqual(self.xml.stat().st_mtime_ns, source_before[1])

    def test_inspect_validates_without_creating_database(self):
        exact = self.add_audio("Exact.mp3", b"exact")
        self.write_export([{"track_id": "1", "path": exact, "name": "Exact"}])

        report = rekordbox_index.inspect_xml(self.xml)

        self.assertEqual(report["collection_tracks"], 1)
        self.assertEqual(report["product"]["Version"], "7.2.17")
        self.assertFalse(self.db.exists())

    def test_doctype_input_fails_without_replacing_trusted_import(self):
        exact = self.add_audio("Exact.mp3", b"exact")
        self.build_index()
        self.write_export([{"track_id": "1", "path": exact, "name": "Exact"}])
        first = rekordbox_index.import_rekordbox(self.xml, self.db)
        self.xml.write_text(
            '<!DOCTYPE x [<!ENTITY injected "value">]><DJ_PLAYLISTS><COLLECTION Entries="0"/></DJ_PLAYLISTS>',
            encoding="utf-8",
        )

        with self.assertRaises(RuntimeError):
            rekordbox_index.import_rekordbox(self.xml, self.db)

        conn = sqlite3.connect(self.db)
        try:
            import_ids = [row[0] for row in conn.execute("SELECT import_id FROM rekordbox_imports")]
        finally:
            conn.close()
        self.assertEqual(import_ids, [first["import_id"]])

    def test_source_change_during_import_preserves_prior_trusted_state(self):
        exact = self.add_audio("Exact.mp3", b"exact")
        self.build_index()
        self.write_export([{"track_id": "1", "path": exact, "name": "Exact"}])
        first = rekordbox_index.import_rekordbox(self.xml, self.db)
        payload = self.xml.read_bytes()
        stable = rekordbox_index.SourceFingerprint(
            path=str(self.xml), size_bytes=len(payload), mtime_ns=1, sha256="a"
        )
        changed = rekordbox_index.SourceFingerprint(
            path=str(self.xml), size_bytes=len(payload), mtime_ns=2, sha256="a"
        )

        with mock.patch.object(
            rekordbox_index,
            "_read_stable",
            side_effect=[(payload, stable), (payload, changed)],
        ):
            with self.assertRaises(RuntimeError):
                rekordbox_index.import_rekordbox(self.xml, self.db)

        conn = sqlite3.connect(self.db)
        try:
            import_ids = [row[0] for row in conn.execute("SELECT import_id FROM rekordbox_imports")]
        finally:
            conn.close()
        self.assertEqual(import_ids, [first["import_id"]])

    def test_collection_count_mismatch_is_refused(self):
        exact = self.add_audio("Exact.mp3", b"exact")
        self.write_export([{"track_id": "1", "path": exact, "name": "Exact"}])
        payload = self.xml.read_text(encoding="utf-8").replace('COLLECTION Entries="1"', 'COLLECTION Entries="2"')
        self.xml.write_text(payload, encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "declares 2 entries"):
            rekordbox_index.inspect_xml(self.xml)

    def test_playlist_only_xml_is_not_mistaken_for_collection_export(self):
        self.xml.write_text(
            '<?xml version="1.0"?><DJ_PLAYLISTS Version="1.0.0"><PRODUCT Name="rekordbox"/>'
            '<PLAYLISTS><NODE Name="ROOT" Type="0" Count="0"/></PLAYLISTS></DJ_PLAYLISTS>',
            encoding="utf-8",
        )

        with self.assertRaisesRegex(RuntimeError, "exactly one COLLECTION"):
            rekordbox_index.inspect_xml(self.xml)

    def test_unversioned_rekordbox_tables_are_refused_without_changes(self):
        self.add_audio("Exact.mp3", b"exact")
        self.build_index()
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("CREATE TABLE rekordbox_unknown(value TEXT)")
            conn.execute("INSERT INTO rekordbox_unknown(value) VALUES('keep-me')")
            conn.commit()
        finally:
            conn.close()
        self.write_export([], include_playlists=False)

        with self.assertRaises(RuntimeError):
            rekordbox_index.import_rekordbox(self.xml, self.db)

        conn = sqlite3.connect(self.db)
        try:
            value = conn.execute("SELECT value FROM rekordbox_unknown").fetchone()[0]
            known = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='rekordbox_imports'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(value, "keep-me")
        self.assertIsNone(known)

    def test_incompatible_rekordbox_schema_is_refused_without_changes(self):
        self.add_audio("Exact.mp3", b"exact")
        self.build_index()
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "INSERT INTO meta(key,value) VALUES('rekordbox_schema_version','999')"
            )
            conn.commit()
        finally:
            conn.close()
        self.write_export([], include_playlists=False)

        with self.assertRaisesRegex(RuntimeError, "unsupported Rekordbox index schema 999"):
            rekordbox_index.import_rekordbox(self.xml, self.db)

        conn = sqlite3.connect(self.db)
        try:
            version = conn.execute(
                "SELECT value FROM meta WHERE key='rekordbox_schema_version'"
            ).fetchone()[0]
            table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='rekordbox_imports'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(version, "999")
        self.assertIsNone(table)

    def test_database_cannot_be_the_xml_source(self):
        self.xml.write_text("<DJ_PLAYLISTS/>", encoding="utf-8")
        with self.assertRaises(ValueError):
            rekordbox_index.import_rekordbox(self.xml, self.xml)

    def test_symlink_source_is_rejected(self):
        real = self.tmp / "real.xml"
        real.write_text('<DJ_PLAYLISTS><COLLECTION Entries="0"/></DJ_PLAYLISTS>', encoding="utf-8")
        self.xml.symlink_to(real)

        with self.assertRaisesRegex(RuntimeError, "non-symlink"):
            rekordbox_index.inspect_xml(self.xml)


if __name__ == "__main__":
    unittest.main()
