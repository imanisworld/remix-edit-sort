from __future__ import annotations

import copy
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

import rekordbox_bridge_xml  # noqa: E402
import rekordbox_index  # noqa: E402
import rekordbox_unresolved_review  # noqa: E402


def location(path: Path) -> str:
    return "file://localhost" + quote(str(path), safe="/")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RekordboxBridgeXmlTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.current = self.tmp / "Current"
        self.current.mkdir()
        self.source = self.tmp / "source.xml"
        self.preview_path = self.tmp / "preview.json"
        self.review_path = self.tmp / "review.json"
        self.output = self.tmp / "bridge.xml"

        self.files = {
            1: self._audio("One.mp3", b"one-current"),
            2: self._audio("Moved/Two.mp3", b"two-current"),
            31: self._audio("Ambiguous/A #1?.mp3", b"three-a-current"),
            32: self._audio("Ambiguous/B.mp3", b"three-b-current"),
            4: self._audio("Changed/Four.mp3", b"four-current-changed"),
        }
        self.old_paths = {
            "1": self.files[1],
            "2": self.tmp / "Old" / "Two.mp3",
            "3": self.tmp / "Old" / "Three.mp3",
            "4": self.tmp / "Old" / "Four.mp3",
            "5": self.tmp / "Old" / "Five.mp3",
        }
        self._write_source()
        self.preview = self._build_preview()
        self._write_preview(self.preview)
        self.review = self._build_review(self.preview)
        self._write_review(self.review)

    def tearDown(self):
        self._tmp.cleanup()

    def _audio(self, relative: str, payload: bytes) -> Path:
        path = self.current / relative
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
        collection = ET.SubElement(root, "COLLECTION", {"Entries": "5"})
        for track_id in ("1", "2", "3", "4", "5"):
            track = ET.SubElement(
                collection,
                "TRACK",
                {
                    "TrackID": track_id,
                    "Name": f"Song {track_id}",
                    "Artist": "Artist",
                    "Album": "Album",
                    "Genre": "House",
                    "AverageBpm": "120.00",
                    "Comments": f"comment-{track_id}",
                    "Location": location(self.old_paths[track_id]),
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
        root_node = ET.SubElement(
            playlists, "NODE", {"Type": "0", "Name": "ROOT", "Count": "2"}
        )
        by_id = ET.SubElement(
            root_node,
            "NODE",
            {"Type": "1", "Name": "By ID", "Entries": "5", "KeyType": "0"},
        )
        by_location = ET.SubElement(
            root_node,
            "NODE",
            {"Type": "1", "Name": "By Location", "Entries": "5", "KeyType": "1"},
        )
        for track_id in ("1", "2", "3", "4", "5"):
            ET.SubElement(by_id, "TRACK", {"Key": track_id})
            ET.SubElement(
                by_location,
                "TRACK",
                {"Key": location(self.old_paths[track_id])},
            )

        raw = b'<?xml version="1.0" encoding="UTF-8" ?>\n' + ET.tostring(
            root, encoding="utf-8"
        )
        self.source.write_bytes(raw)
        rekordbox_index._parse_export(raw)

    def _candidate(
        self,
        file_id: int,
        path: Path,
        *,
        score: int,
        evidence: list[str] | None = None,
        conflicts: list[str] | None = None,
        material_change: bool = False,
    ) -> dict[str, object]:
        return {
            "file_id": file_id,
            "path": str(path),
            "canonical_path": str(path.resolve()),
            "filename": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": sha(path),
            "duration_seconds": 180.0,
            "artist": "Artist",
            "title": f"Song {file_id}",
            "album": "Album",
            "genre": "House",
            "extension": ".mp3",
            "parse_error": None,
            "score": score,
            "evidence": list(evidence or []),
            "conflicts": list(conflicts or []),
            "differences": [],
            "material_change": material_change,
        }

    def _track(
        self,
        track_id: str,
        status: str,
        candidates: list[dict[str, object]],
        proposed_file_id: int | None,
        proposed_path: Path | None,
    ) -> dict[str, object]:
        resolved = status in {"UNCHANGED", "STRONG_CANDIDATE"}
        return {
            "rekordbox_track_id": track_id,
            "old_path": str(self.old_paths[track_id]),
            "name": f"Song {track_id}",
            "artist": "Artist",
            "status": status,
            "proposed_file_id": proposed_file_id,
            "proposed_path": str(proposed_path) if proposed_path else None,
            "bridge_eligible": resolved,
            "review_required": not resolved,
            "top_candidates": candidates,
        }

    def _build_preview(self) -> dict[str, object]:
        c1 = self._candidate(1, self.files[1], score=250, evidence=["exact_current_path"])
        c2 = self._candidate(2, self.files[2], score=150, evidence=["sha256_move_lineage"])
        c31 = self._candidate(31, self.files[31], score=78, evidence=["filename_exact"])
        c32 = self._candidate(32, self.files[32], score=74, evidence=["current_file_has_exact_serato_reference"])
        c4 = self._candidate(
            4,
            self.files[4],
            score=130,
            evidence=["historical_sha256_move_lineage"],
            conflicts=["changed_after_sha256_move"],
            material_change=True,
        )
        tracks = [
            self._track("1", "UNCHANGED", [c1], 1, self.files[1]),
            self._track("2", "STRONG_CANDIDATE", [c2], 2, self.files[2]),
            self._track("3", "AMBIGUOUS", [c31, c32], 31, self.files[31]),
            self._track("4", "CHANGED", [c4], 4, self.files[4]),
            self._track("5", "MISSING", [], None, None),
        ]
        source_raw = self.source.read_bytes()
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
            "library": {"scan_id": 7, "file_count": 5},
            "rekordbox": {
                "import_id": 2,
                "collection_tracks": 5,
                "source_fingerprint": {
                    "path": str(self.source),
                    "size_bytes": len(source_raw),
                    "mtime_ns": self.source.stat().st_mtime_ns,
                    "sha256": hashlib.sha256(source_raw).hexdigest(),
                },
            },
            "serato": {"available": True, "import_id": 3},
            "summary": {
                "total": 5,
                "unchanged": 1,
                "strong_candidate": 1,
                "ambiguous": 1,
                "changed": 1,
                "missing": 1,
                "bridge_eligible": 2,
                "review_required": 3,
            },
            "tracks": tracks,
        }

    def _write_preview(self, preview: dict[str, object]) -> bytes:
        raw = json.dumps(preview, indent=2, ensure_ascii=False).encode("utf-8")
        self.preview_path.write_bytes(raw)
        return raw

    def _build_review(self, preview: dict[str, object]) -> dict[str, object]:
        preview_raw = json.dumps(preview, indent=2, ensure_ascii=False).encode("utf-8")
        return rekordbox_unresolved_review.build_review_packet(
            preview,
            source_preview_sha256=hashlib.sha256(preview_raw).hexdigest(),
        )

    def _write_review(self, review: dict[str, object]) -> None:
        self.review_path.write_text(
            json.dumps(review, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _approve_default_ambiguous_and_changed(self) -> None:
        ambiguous = self.review["queues"]["ambiguous"][0]
        ambiguous["decision"] = "APPROVE_CURRENT_FILE"
        ambiguous["decision_evidence"] = ["human reviewed candidates"]
        changed = self.review["queues"]["changed"][0]
        changed["decision"] = "APPROVE_CHANGED_CURRENT_FILE"
        changed["decision_evidence"] = ["human confirmed intended current version"]
        missing = self.review["queues"]["missing"][0]
        missing["decision"] = "MARK_GENUINELY_ABSENT"
        missing["decision_evidence"] = ["human confirmed absent"]
        self._write_review(self.review)

    def test_builds_separate_bridge_and_preserves_metadata_cues_tempo_playlists(self):
        self._approve_default_ambiguous_and_changed()
        source_before = self.source.read_bytes()

        result = rekordbox_bridge_xml.build_bridge(
            self.source,
            self.preview_path,
            self.output,
            review_path=self.review_path,
        )

        self.assertEqual(self.source.read_bytes(), source_before)
        self.assertTrue(self.output.is_file())
        bridge_raw = self.output.read_bytes()
        parsed = rekordbox_index._parse_export(bridge_raw)
        self.assertEqual(parsed.declared_collection_count, 4)
        self.assertEqual(result["summary"]["bridge_collection_tracks"], 4)
        self.assertEqual(result["summary"]["excluded_tracks"], 1)

        root = ET.fromstring(bridge_raw)
        tracks = {
            element.attrib["TrackID"]: element
            for element in root.find("COLLECTION")
        }
        self.assertEqual(set(tracks), {"1", "2", "3", "4"})
        self.assertEqual(tracks["2"].attrib["Location"], location(self.files[2]))
        self.assertEqual(
            tracks["3"].attrib["Location"],
            rekordbox_bridge_xml._path_to_location(str(self.files[31])),
        )
        self.assertEqual(tracks["4"].attrib["Comments"], "comment-4")
        self.assertEqual(tracks["4"].find("TEMPO").attrib["Bpm"], "120.00")
        self.assertEqual(tracks["4"].find("POSITION_MARK").attrib["Name"], "Cue 4")

        root_node = root.find("PLAYLISTS").find("NODE")
        by_id, by_location = list(root_node)
        self.assertEqual(by_id.attrib["Entries"], "4")
        self.assertEqual([x.attrib["Key"] for x in by_id], ["1", "2", "3", "4"])
        self.assertEqual(by_location.attrib["Entries"], "4")
        self.assertEqual(
            [x.attrib["Key"] for x in by_location],
            [
                rekordbox_bridge_xml._path_to_location(str(self.files[1])),
                rekordbox_bridge_xml._path_to_location(str(self.files[2])),
                rekordbox_bridge_xml._path_to_location(str(self.files[31])),
                rekordbox_bridge_xml._path_to_location(str(self.files[4])),
            ],
        )
        self.assertIn("%23", tracks["3"].attrib["Location"])
        self.assertIn("%3F", tracks["3"].attrib["Location"])

    def test_without_review_only_resolved_bridge_eligible_tracks_are_included(self):
        result = rekordbox_bridge_xml.build_bridge(
            self.source, self.preview_path, self.output
        )
        parsed = rekordbox_index._parse_export(self.output.read_bytes())
        self.assertEqual(parsed.declared_collection_count, 2)
        self.assertEqual(result["summary"]["bridge_collection_tracks"], 2)
        ids = {track["track_id"] for track in parsed.tracks}
        self.assertEqual(ids, {"1", "2"})

    def test_human_can_select_a_non_top_ambiguous_candidate(self):
        ambiguous = self.review["queues"]["ambiguous"][0]
        ambiguous["decision"] = "APPROVE_CURRENT_FILE"
        ambiguous["decision_evidence"] = ["human selected second candidate"]
        ambiguous["selected_file_id"] = 32
        ambiguous["selected_path"] = str(self.files[32])
        self._write_review(self.review)

        rekordbox_bridge_xml.build_bridge(
            self.source,
            self.preview_path,
            self.output,
            review_path=self.review_path,
        )
        root = ET.fromstring(self.output.read_bytes())
        tracks = {x.attrib["TrackID"]: x for x in root.find("COLLECTION")}
        self.assertEqual(
            tracks["3"].attrib["Location"],
            rekordbox_bridge_xml._path_to_location(str(self.files[32])),
        )

    def test_approved_unresolved_track_requires_human_evidence(self):
        ambiguous = self.review["queues"]["ambiguous"][0]
        ambiguous["decision"] = "APPROVE_CURRENT_FILE"
        ambiguous["decision_evidence"] = []
        self._write_review(self.review)

        with self.assertRaisesRegex(RuntimeError, "needs decision_evidence"):
            rekordbox_bridge_xml.build_bridge(
                self.source,
                self.preview_path,
                self.output,
                review_path=self.review_path,
            )
        self.assertFalse(self.output.exists())

    def test_review_must_be_bound_to_exact_preview_bytes(self):
        self.review["source"]["preview_sha256"] = "0" * 64
        self._write_review(self.review)
        with self.assertRaisesRegex(RuntimeError, "not bound to the supplied preview"):
            rekordbox_bridge_xml.build_bridge(
                self.source,
                self.preview_path,
                self.output,
                review_path=self.review_path,
            )
        self.assertFalse(self.output.exists())

    def test_selected_target_must_be_one_of_the_preview_candidates(self):
        ambiguous = self.review["queues"]["ambiguous"][0]
        ambiguous["decision"] = "APPROVE_CURRENT_FILE"
        ambiguous["decision_evidence"] = ["human selection"]
        ambiguous["selected_file_id"] = 999
        ambiguous["selected_path"] = "/not/a/candidate.mp3"
        self._write_review(self.review)
        with self.assertRaisesRegex(RuntimeError, "not a relocation-preview candidate"):
            rekordbox_bridge_xml.build_bridge(
                self.source,
                self.preview_path,
                self.output,
                review_path=self.review_path,
            )
        self.assertFalse(self.output.exists())

    def test_target_file_drift_after_preview_is_refused(self):
        self.files[2].write_bytes(b"changed-after-preview-and-longer")
        with self.assertRaisesRegex(RuntimeError, "changed since preview"):
            rekordbox_bridge_xml.build_bridge(
                self.source, self.preview_path, self.output
            )
        self.assertFalse(self.output.exists())

    def test_source_xml_must_match_the_preview_fingerprint(self):
        self.source.write_bytes(self.source.read_bytes() + b"\n")
        with self.assertRaisesRegex(RuntimeError, "does not match the XML bound"):
            rekordbox_bridge_xml.build_bridge(
                self.source, self.preview_path, self.output
            )
        self.assertFalse(self.output.exists())

    def test_source_xml_can_never_be_the_output(self):
        source_before = self.source.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "must not overwrite"):
            rekordbox_bridge_xml.build_bridge(
                self.source, self.preview_path, self.source
            )
        self.assertEqual(self.source.read_bytes(), source_before)

    def test_existing_output_is_never_overwritten(self):
        self.output.write_bytes(b"existing")
        with self.assertRaisesRegex(RuntimeError, "refusing overwrite"):
            rekordbox_bridge_xml.build_bridge(
                self.source, self.preview_path, self.output
            )
        self.assertEqual(self.output.read_bytes(), b"existing")

    def test_automatic_review_decision_can_never_authorize_bridge(self):
        ambiguous = self.review["queues"]["ambiguous"][0]
        ambiguous["decision"] = "APPROVE_CURRENT_FILE"
        ambiguous["decision_evidence"] = ["not actually human"]
        ambiguous["automatic_decision"] = True
        self._write_review(self.review)
        with self.assertRaisesRegex(RuntimeError, "automatic decision cannot authorize"):
            rekordbox_bridge_xml.build_bridge(
                self.source,
                self.preview_path,
                self.output,
                review_path=self.review_path,
            )
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
