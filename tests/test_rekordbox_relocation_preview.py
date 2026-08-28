from __future__ import annotations

import sqlite3
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
import rekordbox_relocation_preview  # noqa: E402


class RekordboxRelocationPreviewTests(unittest.TestCase):
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

    def write_export(self, tracks: list[dict[str, object]]) -> None:
        root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
        ET.SubElement(
            root,
            "PRODUCT",
            {"Name": "rekordbox", "Version": "7.2.17", "Company": "AlphaTheta"},
        )
        collection = ET.SubElement(root, "COLLECTION", {"Entries": str(len(tracks))})
        for item in tracks:
            path = Path(item["path"])
            attrs = {
                "TrackID": str(item["track_id"]),
                "Name": str(item.get("name", path.stem)),
                "Artist": str(item.get("artist", "Artist")),
                "Album": str(item.get("album", "Album")),
                "Genre": str(item.get("genre", "House")),
                "Location": self.location(path),
            }
            if "size" in item:
                attrs["Size"] = str(item["size"])
            if "total_time" in item:
                attrs["TotalTime"] = str(item["total_time"])
            ET.SubElement(collection, "TRACK", attrs)
        ET.ElementTree(root).write(self.xml, encoding="utf-8", xml_declaration=True)

    def scan(self) -> dict[str, object]:
        return library_index.scan(self.library, self.db)

    def import_xml(self) -> dict[str, object]:
        return rekordbox_index.import_rekordbox(self.xml, self.db)

    def test_read_only_preview_classifies_unchanged_strong_ambiguous_and_missing(self):
        exact = self.add_audio("Exact.mp3", b"exact-current")
        moved = self.add_audio("Old/Moved.mp3", b"moved-payload")
        self.add_audio("A/Shared.mp3", b"shared-a")
        self.add_audio("B/Shared.mp3", b"shared-b")
        self.scan()

        missing = self.library / "Old" / "Gone.mp3"
        shared_old = self.library / "Old" / "Shared.mp3"
        self.write_export(
            [
                {"track_id": "1", "path": exact, "name": "Exact", "size": exact.stat().st_size},
                {"track_id": "2", "path": moved, "name": "Moved", "size": moved.stat().st_size},
                {"track_id": "3", "path": shared_old, "name": "Shared"},
                {"track_id": "4", "path": missing, "name": "Gone"},
            ]
        )
        rb = self.import_xml()

        destination = self.library / "New" / "Moved.mp3"
        destination.parent.mkdir(parents=True)
        moved.rename(destination)
        self.scan()

        before = self.db.read_bytes()
        report = rekordbox_relocation_preview.build_preview(
            self.db, import_id=int(rb["import_id"])
        )
        after = self.db.read_bytes()
        self.assertEqual(before, after)

        tracks = {item["rekordbox_track_id"]: item for item in report["tracks"]}
        self.assertEqual(tracks["1"]["status"], "UNCHANGED")
        self.assertTrue(tracks["1"]["bridge_eligible"])
        self.assertEqual(tracks["2"]["status"], "STRONG_CANDIDATE")
        self.assertEqual(
            tracks["2"]["proposed_path"],
            library_index.canonicalize_path(destination),
        )
        self.assertTrue(tracks["2"]["bridge_eligible"])
        self.assertIn("sha256_move_lineage", tracks["2"]["top_candidates"][0]["evidence"])
        self.assertEqual(tracks["3"]["status"], "AMBIGUOUS")
        self.assertEqual(len(tracks["3"]["top_candidates"]), 2)
        self.assertEqual(tracks["4"]["status"], "MISSING")
        self.assertIsNone(tracks["4"]["proposed_path"])
        self.assertEqual(report["summary"]["total"], 4)
        self.assertEqual(report["summary"]["strong_candidate"], 1)
        self.assertEqual(report["summary"]["ambiguous"], 1)
        self.assertEqual(report["summary"]["missing"], 1)

    def test_moved_file_with_material_change_is_review_only_changed(self):
        old = self.add_audio("Old/Track.mp3", b"original")
        self.scan()
        original_size = old.stat().st_size
        self.write_export(
            [{"track_id": "1", "path": old, "name": "Track", "size": original_size}]
        )
        rb = self.import_xml()

        new = self.library / "New" / "Track.mp3"
        new.parent.mkdir(parents=True)
        old.rename(new)
        self.scan()
        new.write_bytes(b"materially-changed-and-longer")
        self.scan()

        report = rekordbox_relocation_preview.build_preview(
            self.db, import_id=int(rb["import_id"])
        )
        track = report["tracks"][0]
        self.assertEqual(track["status"], "CHANGED")
        self.assertTrue(track["review_required"])
        self.assertFalse(track["bridge_eligible"])
        self.assertIn(
            "historical_sha256_move_lineage",
            track["top_candidates"][0]["evidence"],
        )
        self.assertIn("changed_after_sha256_move", track["top_candidates"][0]["conflicts"])
        self.assertIn("byte_size_changed", track["top_candidates"][0]["conflicts"])

    def test_later_change_blocks_sha_move_even_without_rekordbox_size(self):
        old = self.add_audio("Old/NoSize.mp3", b"original")
        self.scan()
        self.write_export([{"track_id": "1", "path": old, "name": "NoSize"}])
        rb = self.import_xml()

        new = self.library / "New" / "NoSize.mp3"
        new.parent.mkdir(parents=True)
        old.rename(new)
        self.scan()
        new.write_bytes(b"changed")
        self.scan()

        track = rekordbox_relocation_preview.build_preview(
            self.db, import_id=int(rb["import_id"])
        )["tracks"][0]
        self.assertEqual(track["status"], "CHANGED")
        self.assertFalse(track["bridge_eligible"])
        self.assertIn(
            "changed_after_sha256_move",
            track["top_candidates"][0]["conflicts"],
        )

    def test_exact_current_path_changed_after_snapshot_requires_review(self):
        exact = self.add_audio("Exact.mp3", b"first")
        self.scan()
        self.write_export([{"track_id": "1", "path": exact, "name": "Exact"}])
        rb = self.import_xml()

        exact.write_bytes(b"second-content")
        self.scan()

        track = rekordbox_relocation_preview.build_preview(
            self.db, import_id=int(rb["import_id"])
        )["tracks"][0]
        self.assertEqual(track["status"], "CHANGED")
        self.assertFalse(track["bridge_eligible"])
        self.assertIn(
            "changed_since_rekordbox_snapshot",
            track["top_candidates"][0]["conflicts"],
        )

    def test_newer_incomplete_scan_blocks_preview(self):
        exact = self.add_audio("Exact.mp3", b"exact")
        self.scan()
        self.write_export([{"track_id": "1", "path": exact, "name": "Exact"}])
        self.import_xml()

        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                """INSERT INTO scans(
                       started_at,root_display,root_canonical,config_json,tool_version,schema_version
                   ) VALUES('now',?,?, '{}','test',?)""",
                (str(self.library), str(self.library), library_index.SCHEMA_VERSION),
            )
            conn.commit()
        finally:
            conn.close()

        with self.assertRaisesRegex(RuntimeError, "newer incomplete library scan"):
            rekordbox_relocation_preview.build_preview(self.db)

    def test_symlink_database_is_refused(self):
        self.add_audio("Exact.mp3", b"exact")
        self.scan()
        alias = self.tmp / "library-alias.sqlite3"
        alias.symlink_to(self.db)

        with self.assertRaisesRegex(RuntimeError, "must not be a symlink"):
            rekordbox_relocation_preview.build_preview(alias)


if __name__ == "__main__":
    unittest.main()
