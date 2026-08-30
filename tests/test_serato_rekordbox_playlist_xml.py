from __future__ import annotations

import hashlib
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
import serato_rekordbox_playlist_xml  # noqa: E402
import serato_rekordbox_transfer_review  # noqa: E402


def tlv(tag: str, value: bytes) -> bytes:
    return tag.encode("ascii") + struct.pack(">I", len(value)) + value


def text(value: str) -> bytes:
    return value.encode("utf-16-be")


def otrk(fields: bytes) -> bytes:
    return tlv("otrk", fields)


class SeratoRekordboxPlaylistXmlTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.music = self.tmp / "Music"
        self.music.mkdir()
        self.serato = self.tmp / "_Serato_"
        (self.serato / "Subcrates").mkdir(parents=True)
        self.source_xml = self.tmp / "rekordbox.xml"
        self.db = self.tmp / "app" / "library.sqlite3"
        self.crosswalk_path = self.tmp / "crosswalk.json"
        self.review_path = self.tmp / "review.json"
        self.output = self.tmp / "serato-playlists.xml"

        self.a = self._audio("House/A.mp3", b"audio-a")
        self.b = self._audio("House/B.mp3", b"audio-b")
        self.c = self._audio("House/C.mp3", b"audio-c")
        library_index.scan(self.music, self.db)
        self._write_serato()
        self.serato_report = serato_index.import_serato(self.serato, self.db)
        self._write_rekordbox()
        self.rekordbox_report = rekordbox_index.import_rekordbox(
            self.source_xml, self.db
        )
        self.crosswalk = serato_rekordbox_crosswalk.build_crosswalk(self.db)
        self.crosswalk_raw = json.dumps(
            self.crosswalk, indent=2, ensure_ascii=False
        ).encode("utf-8")
        self.crosswalk_path.write_bytes(self.crosswalk_raw)
        self.review = serato_rekordbox_transfer_review.build_review_packet(
            self.crosswalk,
            source_crosswalk_sha256=hashlib.sha256(self.crosswalk_raw).hexdigest(),
        )
        self._write_review()

    def tearDown(self):
        self._tmp.cleanup()

    def _audio(self, relative: str, payload: bytes) -> Path:
        path = self.music / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def _serato_path(self, path: Path) -> str:
        return str(path).lstrip("/")

    def _write_serato(self) -> None:
        database = tlv("vrsn", text("2.0"))
        for path in (self.a, self.b, self.c):
            database += otrk(
                tlv("pfil", text(self._serato_path(path)))
                + tlv("tsng", text(path.stem))
                + tlv("tart", text("Artist"))
            )
        (self.serato / "database V2").write_bytes(database)

        # Non-alphabetic order must survive into the generated playlist XML.
        self._write_crate("Full", [self.b, self.a])
        missing_path = self.music / "Gone" / "Missing.mp3"
        self._write_crate("Partial", [self.a, str(missing_path).lstrip("/")])
        self._write_crate("Skip Me", [self.c])

    def _write_crate(self, name: str, paths: list[Path | str]) -> None:
        body = tlv("vrsn", text("1.0"))
        for value in paths:
            path = value if isinstance(value, str) else self._serato_path(value)
            body += otrk(tlv("ptrk", text(path)))
        (self.serato / "Subcrates" / f"{name}.crate").write_bytes(body)

    def _location(self, path: Path) -> str:
        return "file://localhost" + quote(str(path), safe="/")

    def _write_rekordbox(self) -> None:
        root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
        ET.SubElement(
            root,
            "PRODUCT",
            {"Name": "rekordbox", "Version": "7.2.17", "Company": "AlphaTheta"},
        )
        collection = ET.SubElement(root, "COLLECTION", {"Entries": "2"})
        for track_id, path in (("11", self.a), ("33", self.c)):
            track = ET.SubElement(
                collection,
                "TRACK",
                {
                    "TrackID": track_id,
                    "Name": path.stem,
                    "Artist": "Artist",
                    "Album": "Album",
                    "Genre": "House",
                    "Comments": f"keep-{path.stem}",
                    "Location": self._location(path),
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
                {"Name": f"Cue {path.stem}", "Type": "0", "Start": "12.500", "Num": "-1"},
            )
        playlists = ET.SubElement(root, "PLAYLISTS")
        folder = ET.SubElement(
            playlists,
            "NODE",
            {"Name": "ROOT", "Type": "0", "Count": "1"},
        )
        node = ET.SubElement(
            folder,
            "NODE",
            {"Name": "Existing", "Type": "1", "KeyType": "0", "Entries": "1"},
        )
        ET.SubElement(node, "TRACK", {"Key": "11"})
        ET.ElementTree(root).write(
            self.source_xml, encoding="utf-8", xml_declaration=True
        )

    def _write_review(self) -> None:
        self.review_path.write_text(
            json.dumps(self.review, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _plan(self, crate_name: str) -> dict:
        return next(
            plan for plan in self.review["plans"] if plan["crate_name"] == crate_name
        )

    def _approve_full_and_partial(self) -> None:
        full = self._plan("Full")
        full["decision"] = "CREATE_NEW_REKORDBOX_PLAYLIST"
        full["decision_evidence"] = ["human selected full crate"]
        partial = self._plan("Partial")
        partial["decision"] = "CREATE_PARTIAL_REKORDBOX_PLAYLIST"
        partial["decision_evidence"] = ["human approved excluding unresolved entry"]
        skip = self._plan("Skip Me")
        skip["decision"] = "SKIP"
        self.review["output_folder_name"] = "Serato Transfer Review"
        self._write_review()

    def test_review_packet_is_sha_bound_and_leaves_decisions_null(self):
        self.assertEqual(
            self.review["source"]["crosswalk_sha256"],
            hashlib.sha256(self.crosswalk_raw).hexdigest(),
        )
        self.assertEqual(self.review["summary"]["decisions_made"], 0)
        self.assertTrue(all(plan["decision"] is None for plan in self.review["plans"]))
        partial = self._plan("Partial")
        full = self._plan("Full")
        self.assertIn(
            "CREATE_PARTIAL_REKORDBOX_PLAYLIST", partial["allowed_decisions"]
        )
        self.assertNotIn(
            "CREATE_PARTIAL_REKORDBOX_PLAYLIST", full["allowed_decisions"]
        )

    def test_generates_reviewed_playlists_in_serato_order_without_touching_sources(self):
        self._approve_full_and_partial()
        source_before = self.source_xml.read_bytes()
        db_before = self.db.read_bytes()
        serato_before = {
            path: path.read_bytes()
            for path in [
                self.serato / "database V2",
                *sorted((self.serato / "Subcrates").glob("*.crate")),
            ]
        }

        report = serato_rekordbox_playlist_xml.generate_playlist_xml(
            self.db,
            self.source_xml,
            self.crosswalk_path,
            self.review_path,
            self.output,
        )

        self.assertEqual(self.source_xml.read_bytes(), source_before)
        self.assertEqual(self.db.read_bytes(), db_before)
        for path, payload in serato_before.items():
            self.assertEqual(path.read_bytes(), payload)
        self.assertEqual(report["summary"]["playlists_created"], 2)
        self.assertEqual(report["summary"]["partial_playlists"], 1)
        self.assertEqual(report["summary"]["unique_tracks"], 2)
        self.assertEqual(
            report["summary"]["tracks_preserving_rekordbox_metadata_cues_grid"], 1
        )
        self.assertEqual(report["summary"]["serato_only_tracks_added_minimally"], 1)
        self.assertEqual(
            report["summary"]["unresolved_entries_explicitly_excluded"], 1
        )

        output_root = ET.fromstring(self.output.read_bytes())
        playlists_element = output_root.find("PLAYLISTS")
        self.assertIsNotNone(playlists_element)
        top_children = list(playlists_element)
        self.assertEqual(len(top_children), 1)
        self.assertEqual(top_children[0].tag, "NODE")
        self.assertEqual(top_children[0].attrib.get("Type"), "0")
        self.assertEqual(top_children[0].attrib.get("Name"), "ROOT")

        parsed = rekordbox_index._parse_export(self.output.read_bytes())
        self.assertEqual(parsed.declared_collection_count, 2)
        tracks_by_path = {track["absolute_path"]: track for track in parsed.tracks}
        canonical_a = library_index.canonicalize_path(self.a)
        canonical_b = library_index.canonicalize_path(self.b)
        self.assertIn(canonical_a, tracks_by_path)
        self.assertIn(canonical_b, tracks_by_path)
        copied = tracks_by_path[canonical_a]
        minimal = tracks_by_path[canonical_b]
        self.assertEqual(copied["attributes"]["Comments"], "keep-A")
        self.assertEqual(copied["tempos"][0]["Bpm"], "120.00")
        self.assertEqual(copied["marks"][0]["Name"], "Cue A")
        self.assertEqual(minimal["marks"], [])
        self.assertEqual(minimal["tempos"], [])

        playlist_nodes = [
            node for node in parsed.playlist_nodes if node["node_type"] == 1
        ]
        self.assertEqual([node["name"] for node in playlist_nodes], ["Full", "Partial"])
        id_to_path = {
            str(track["track_id"]): track["absolute_path"] for track in parsed.tracks
        }
        membership_by_node = {node["node_key"]: [] for node in playlist_nodes}
        for membership in sorted(
            parsed.memberships,
            key=lambda item: (item["node_key"], item["ordinal"]),
        ):
            if membership["node_key"] in membership_by_node:
                membership_by_node[membership["node_key"]].append(
                    id_to_path[membership["raw_key"]]
                )
        full_node = next(node for node in playlist_nodes if node["name"] == "Full")
        partial_node = next(node for node in playlist_nodes if node["name"] == "Partial")
        self.assertEqual(
            membership_by_node[full_node["node_key"]],
            [canonical_b, canonical_a],
        )
        self.assertEqual(
            membership_by_node[partial_node["node_key"]], [canonical_a]
        )

    def test_full_creation_refuses_a_crate_with_unresolved_entries(self):
        partial = self._plan("Partial")
        partial["decision"] = "CREATE_NEW_REKORDBOX_PLAYLIST"
        partial["decision_evidence"] = ["human tried full create"]
        # Tamper allowed_decisions too; the generator still uses the crate facts.
        partial["allowed_decisions"].append("CREATE_NEW_REKORDBOX_PLAYLIST")
        self._write_review()
        with self.assertRaisesRegex(RuntimeError, "full playlist creation is refused"):
            serato_rekordbox_playlist_xml.generate_playlist_xml(
                self.db,
                self.source_xml,
                self.crosswalk_path,
                self.review_path,
                self.output,
            )
        self.assertFalse(self.output.exists())

    def test_partial_creation_is_invalid_when_nothing_is_unresolved(self):
        full = self._plan("Full")
        full["decision"] = "CREATE_PARTIAL_REKORDBOX_PLAYLIST"
        full["decision_evidence"] = ["human tried partial"]
        full["allowed_decisions"].append("CREATE_PARTIAL_REKORDBOX_PLAYLIST")
        self._write_review()
        with self.assertRaisesRegex(RuntimeError, "partial creation is invalid"):
            serato_rekordbox_playlist_xml.generate_playlist_xml(
                self.db,
                self.source_xml,
                self.crosswalk_path,
                self.review_path,
                self.output,
            )

    def test_playlist_creation_requires_human_evidence(self):
        full = self._plan("Full")
        full["decision"] = "CREATE_NEW_REKORDBOX_PLAYLIST"
        full["decision_evidence"] = []
        self._write_review()
        with self.assertRaisesRegex(RuntimeError, "needs human decision_evidence"):
            serato_rekordbox_playlist_xml.generate_playlist_xml(
                self.db,
                self.source_xml,
                self.crosswalk_path,
                self.review_path,
                self.output,
            )

    def test_review_must_bind_to_exact_crosswalk_bytes(self):
        self.review["source"]["crosswalk_sha256"] = "0" * 64
        self._write_review()
        with self.assertRaisesRegex(RuntimeError, "source binding mismatch"):
            serato_rekordbox_playlist_xml.generate_playlist_xml(
                self.db,
                self.source_xml,
                self.crosswalk_path,
                self.review_path,
                self.output,
            )

    def test_serato_source_drift_blocks_old_review(self):
        self._approve_full_and_partial()
        crate = self.serato / "Subcrates" / "Full.crate"
        crate.write_bytes(crate.read_bytes() + b"x")
        with self.assertRaisesRegex(RuntimeError, "changed since the selected import"):
            serato_rekordbox_playlist_xml.generate_playlist_xml(
                self.db,
                self.source_xml,
                self.crosswalk_path,
                self.review_path,
                self.output,
            )

    def test_new_library_scan_blocks_old_review(self):
        self._approve_full_and_partial()
        self._audio("House/New.mp3", b"new")
        library_index.scan(self.music, self.db)
        with self.assertRaisesRegex(RuntimeError, "library scan changed since crosswalk"):
            serato_rekordbox_playlist_xml.generate_playlist_xml(
                self.db,
                self.source_xml,
                self.crosswalk_path,
                self.review_path,
                self.output,
            )

    def test_audio_content_drift_after_scan_blocks_generation(self):
        self._approve_full_and_partial()
        self.b.write_bytes(b"audio-b-changed")
        with self.assertRaisesRegex(RuntimeError, "changed since library scan"):
            serato_rekordbox_playlist_xml.generate_playlist_xml(
                self.db,
                self.source_xml,
                self.crosswalk_path,
                self.review_path,
                self.output,
            )

    def test_source_rekordbox_xml_drift_blocks_generation(self):
        self._approve_full_and_partial()
        self.source_xml.write_bytes(self.source_xml.read_bytes() + b"\n")
        with self.assertRaisesRegex(RuntimeError, "does not match the selected import"):
            serato_rekordbox_playlist_xml.generate_playlist_xml(
                self.db,
                self.source_xml,
                self.crosswalk_path,
                self.review_path,
                self.output,
            )

    def test_existing_output_is_never_overwritten(self):
        self._approve_full_and_partial()
        self.output.write_bytes(b"existing")
        with self.assertRaisesRegex(RuntimeError, "refusing overwrite"):
            serato_rekordbox_playlist_xml.generate_playlist_xml(
                self.db,
                self.source_xml,
                self.crosswalk_path,
                self.review_path,
                self.output,
            )
        self.assertEqual(self.output.read_bytes(), b"existing")

    def test_map_to_existing_requires_a_real_crosswalk_candidate(self):
        plan = self._plan("Full")
        plan["decision"] = "MAP_TO_EXISTING_PLAYLIST"
        plan["selected_rekordbox_node_key"] = "not-real"
        plan["selected_rekordbox_node_path"] = "ROOT / not-real"
        self._write_review()
        with self.assertRaisesRegex(RuntimeError, "is not a crosswalk candidate"):
            serato_rekordbox_playlist_xml.generate_playlist_xml(
                self.db,
                self.source_xml,
                self.crosswalk_path,
                self.review_path,
                self.output,
            )

    def test_no_creation_decisions_produces_no_xml(self):
        for plan in self.review["plans"]:
            plan["decision"] = "SKIP"
        self._write_review()
        with self.assertRaisesRegex(RuntimeError, "no crates are approved"):
            serato_rekordbox_playlist_xml.generate_playlist_xml(
                self.db,
                self.source_xml,
                self.crosswalk_path,
                self.review_path,
                self.output,
            )
        self.assertFalse(self.output.exists())

    def test_crosswalk_validation_rejects_automatic_mapping_flag(self):
        tampered = json.loads(json.dumps(self.crosswalk))
        tampered["safety"]["automatic_mapping"] = True
        with self.assertRaisesRegex(RuntimeError, "unsafe crosswalk flag"):
            serato_rekordbox_transfer_review.validate_crosswalk(tampered)


if __name__ == "__main__":
    unittest.main()
