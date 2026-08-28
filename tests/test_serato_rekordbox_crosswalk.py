from __future__ import annotations

import json
import struct
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
import serato_index  # noqa: E402
import serato_rekordbox_crosswalk  # noqa: E402


def tlv(tag: str, value: bytes) -> bytes:
    return tag.encode("ascii") + struct.pack(">I", len(value)) + value


def text(value: str) -> bytes:
    return value.encode("utf-16-be")


def otrk(fields: bytes) -> bytes:
    return tlv("otrk", fields)


class SeratoRekordboxCrosswalkTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.library = self.tmp / "Music"
        self.library.mkdir()
        self.serato = self.tmp / "_Serato_"
        (self.serato / "Subcrates").mkdir(parents=True)
        self.xml = self.tmp / "rekordbox.xml"
        self.db = self.tmp / "app" / "library.sqlite3"

        self.a = self._audio("House/A.mp3", b"a")
        self.b = self._audio("House/B.mp3", b"b")
        self.c = self._audio("House/C.mp3", b"c")
        self.d = self._audio("House/D.mp3", b"d")
        library_index.scan(self.library, self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def _audio(self, relative: str, payload: bytes) -> Path:
        path = self.library / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def _serato_path(self, path: Path) -> str:
        return str(path).lstrip("/")

    def _write_database(self, paths: list[Path]) -> None:
        body = tlv("vrsn", text("2.0"))
        for path in paths:
            body += otrk(
                tlv("pfil", text(self._serato_path(path)))
                + tlv("tsng", text(path.stem))
                + tlv("tart", text("Artist"))
            )
        (self.serato / "database V2").write_bytes(body)

    def _write_crate(self, name: str, paths: list[Path | str]) -> None:
        body = tlv("vrsn", text("1.0"))
        for value in paths:
            path = value if isinstance(value, str) else self._serato_path(value)
            body += otrk(tlv("ptrk", text(path)))
        (self.serato / "Subcrates" / f"{name}.crate").write_bytes(body)

    def _location(self, path: Path) -> str:
        return "file://localhost" + quote(str(path), safe="/")

    def _write_rekordbox(
        self,
        playlists: list[tuple[list[str], str, list[str]]],
        *,
        old_b_path: Path | None = None,
    ) -> None:
        root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
        ET.SubElement(
            root,
            "PRODUCT",
            {"Name": "rekordbox", "Version": "7.2.17", "Company": "AlphaTheta"},
        )
        collection = ET.SubElement(root, "COLLECTION", {"Entries": "4"})
        paths = {"1": self.a, "2": old_b_path or self.b, "3": self.c, "4": self.d}
        for track_id, path in paths.items():
            ET.SubElement(
                collection,
                "TRACK",
                {
                    "TrackID": track_id,
                    "Name": path.stem,
                    "Artist": "Artist",
                    "Genre": "House",
                    "Location": self._location(path),
                },
            )

        root_playlists = ET.SubElement(root, "PLAYLISTS")
        root_node = ET.SubElement(
            root_playlists, "NODE", {"Name": "ROOT", "Type": "0", "Count": str(len(playlists))}
        )
        next_folder = 0
        for folders, name, track_ids in playlists:
            parent = root_node
            for folder_name in folders:
                next_folder += 1
                parent = ET.SubElement(
                    parent,
                    "NODE",
                    {"Name": folder_name, "Type": "0", "Count": "1"},
                )
            playlist = ET.SubElement(
                parent,
                "NODE",
                {
                    "Name": name,
                    "Type": "1",
                    "KeyType": "0",
                    "Entries": str(len(track_ids)),
                },
            )
            for track_id in track_ids:
                ET.SubElement(playlist, "TRACK", {"Key": track_id})
        ET.ElementTree(root).write(self.xml, encoding="utf-8", xml_declaration=True)

    def _import_default(self) -> tuple[dict, dict]:
        self._write_database([self.a, self.b, self.c, self.d])
        # Deliberately non-alphabetic order proves we recover order from the crate source.
        self._write_crate("Gig", [self.b, self.a])
        self._write_crate("Discovery", [self.c])
        self._write_crate("No Candidate", [self.d])
        serato_report = serato_index.import_serato(self.serato, self.db)

        self._write_rekordbox(
            [
                ([], "Gig", ["1", "3"]),
                ([], "Other", ["3"]),
            ]
        )
        rekordbox_report = rekordbox_index.import_rekordbox(self.xml, self.db)
        return serato_report, rekordbox_report

    def _crate(self, report: dict, name: str) -> dict:
        return next(item for item in report["crates"] if item["crate_name"] == name)

    def test_crosswalk_uses_file_ids_recovers_crate_order_and_writes_nothing(self):
        self._import_default()
        db_before = self.db.read_bytes()
        serato_before = {
            path: path.read_bytes()
            for path in [
                self.serato / "database V2",
                *sorted((self.serato / "Subcrates").glob("*.crate")),
            ]
        }

        report = serato_rekordbox_crosswalk.build_crosswalk(self.db)

        self.assertEqual(self.db.read_bytes(), db_before)
        for path, payload in serato_before.items():
            self.assertEqual(path.read_bytes(), payload)
        self.assertTrue(report["serato"]["source_manifest_verified"])
        self.assertTrue(report["serato"]["crate_order_recovered_from_verified_sources"])
        self.assertEqual(report["summary"]["serato_crates"], 3)
        self.assertEqual(report["summary"]["rekordbox_playlists"], 2)
        self.assertEqual(report["summary"]["automatic_mappings"], 0)

        gig = self._crate(report, "Gig")
        self.assertEqual(gig["mapping_state"], "UNIQUE_NAME_CANDIDATE")
        self.assertEqual(gig["transfer_plan"]["decision"], None)
        self.assertFalse(gig["transfer_plan"]["automatic_decision"])
        ordered_paths = [entry["serato_path"] for entry in gig["ordered_entries"]]
        self.assertEqual(ordered_paths, [self._serato_path(self.b), self._serato_path(self.a)])

        candidate = gig["candidate_playlists"][0]
        self.assertTrue(candidate["exact_name"])
        self.assertEqual(candidate["shared_count"], 1)
        self.assertEqual(candidate["serato_only_count"], 1)
        self.assertEqual(candidate["rekordbox_only_count"], 1)
        catalog_paths = {item["path"] for item in report["track_catalog"].values()}
        self.assertIn(library_index.canonicalize_path(self.a), catalog_paths)
        self.assertIn(library_index.canonicalize_path(self.b), catalog_paths)
        self.assertIn(library_index.canonicalize_path(self.c), catalog_paths)

        discovery = self._crate(report, "Discovery")
        self.assertEqual(discovery["mapping_state"], "OVERLAP_CANDIDATES")
        self.assertGreaterEqual(discovery["candidate_playlists"][0]["shared_count"], 1)

        no_candidate = self._crate(report, "No Candidate")
        self.assertEqual(no_candidate["mapping_state"], "NO_CANDIDATE")
        self.assertEqual(no_candidate["candidate_playlists"], [])

    def test_serato_source_drift_blocks_crosswalk(self):
        self._import_default()
        crate = self.serato / "Subcrates" / "Gig.crate"
        crate.write_bytes(crate.read_bytes() + b"x")
        with self.assertRaisesRegex(RuntimeError, "changed since the selected import"):
            serato_rekordbox_crosswalk.build_crosswalk(self.db)

    def test_moved_likely_serato_reference_stays_unresolved_and_cannot_create_overlap(self):
        old_c = self.library / "Old" / "C.mp3"
        self._write_database([self.a])
        self._write_crate("Moved", [str(old_c).lstrip("/")])
        serato_index.import_serato(self.serato, self.db)
        self._write_rekordbox([([], "Different Name", ["3"])])
        rekordbox_index.import_rekordbox(self.xml, self.db)

        report = serato_rekordbox_crosswalk.build_crosswalk(self.db)
        crate = self._crate(report, "Moved")
        self.assertEqual(crate["resolved_entry_count"], 0)
        self.assertEqual(crate["unresolved_count"], 1)
        self.assertEqual(crate["unresolved"][0]["reason"], "MOVED_LIKELY")
        self.assertEqual(crate["mapping_state"], "NO_CANDIDATE")

    def test_rekordbox_track_without_exact_library_link_is_playlist_unresolved(self):
        self._write_database([self.b])
        self._write_crate("Gig", [self.b])
        serato_index.import_serato(self.serato, self.db)
        old_b = self.library / "Old" / "B.mp3"
        self._write_rekordbox([([], "Gig", ["2"])], old_b_path=old_b)
        rekordbox_index.import_rekordbox(self.xml, self.db)

        report = serato_rekordbox_crosswalk.build_crosswalk(self.db)
        crate = self._crate(report, "Gig")
        candidate = crate["candidate_playlists"][0]
        self.assertTrue(candidate["exact_name"])
        self.assertEqual(candidate["shared_count"], 0)
        self.assertEqual(candidate["rekordbox_unresolved_count"], 1)
        self.assertEqual(candidate["rekordbox_unresolved"][0]["reason"], "TRACK_MOVED_LIKELY")

    def test_duplicate_leaf_playlist_names_are_ambiguous_not_auto_mapped(self):
        self._write_database([self.a])
        self._write_crate("Gig", [self.a])
        serato_index.import_serato(self.serato, self.db)
        self._write_rekordbox(
            [
                (["Folder A"], "Gig", ["1"]),
                (["Folder B"], "Gig", ["1"]),
            ]
        )
        rekordbox_index.import_rekordbox(self.xml, self.db)

        report = serato_rekordbox_crosswalk.build_crosswalk(self.db)
        crate = self._crate(report, "Gig")
        self.assertEqual(crate["mapping_state"], "AMBIGUOUS_NAME_CANDIDATES")
        self.assertEqual(len(crate["candidate_playlists"]), 2)
        self.assertIsNone(crate["transfer_plan"]["decision"])
        self.assertIsNone(crate["transfer_plan"]["selected_rekordbox_node_key"])

    def test_explicit_import_ids_are_honored(self):
        first_serato, first_rekordbox = self._import_default()
        report = serato_rekordbox_crosswalk.build_crosswalk(
            self.db,
            serato_import_id=first_serato["import_id"],
            rekordbox_import_id=first_rekordbox["import_id"],
        )
        self.assertEqual(report["serato"]["import_id"], first_serato["import_id"])
        self.assertEqual(report["rekordbox"]["import_id"], first_rekordbox["import_id"])


if __name__ == "__main__":
    unittest.main()
