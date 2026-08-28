from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import library_index  # noqa: E402
import post_export_usb_verify  # noqa: E402
import rekordbox_bridge_xml  # noqa: E402
import rekordbox_index  # noqa: E402


class PostExportUsbVerifyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = self.tmp / "library.sqlite3"
        self.volume = self.tmp / "USB"
        self.volume.mkdir()
        self._init_db()

    def tearDown(self):
        self._tmp.cleanup()

    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db)
        conn.executescript(library_index.SCHEMA_SQL)
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)",
            (str(library_index.SCHEMA_VERSION),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES('rekordbox_schema_version',?)",
            (str(rekordbox_index.REKORDBOX_SCHEMA_VERSION),),
        )
        conn.execute(
            "INSERT OR REPLACE INTO meta(key,value) VALUES('root_canonical',?)",
            (library_index.canonicalize_path(self.tmp),),
        )
        conn.execute(
            "INSERT INTO scans(scan_id,started_at,completed_at,root_display,root_canonical,config_json,file_count,total_bytes,new_count,changed_count,moved_count,removed_count,unchanged_count,error_count,completed_successfully,tool_version,schema_version) "
            "VALUES(1,'x','x',?,?, '{}',0,0,0,0,0,0,0,0,1,'test',?)",
            (
                str(self.tmp),
                library_index.canonicalize_path(self.tmp),
                library_index.SCHEMA_VERSION,
            ),
        )
        conn.commit()
        conn.close()

    def _add_library_file(self, name: str, payload: bytes = b"audio") -> tuple[int, Path, str]:
        source = self.tmp / "library" / f"{name}.mp3"
        source.parent.mkdir(exist_ok=True)
        source.write_bytes(payload)
        canonical = library_index.canonicalize_path(source)
        digest = hashlib.sha256(payload).hexdigest()
        stat = source.stat()
        conn = sqlite3.connect(self.db)
        cur = conn.execute(
            "INSERT INTO files(canonical_path,display_path,path_identity,extension,size_bytes,mtime_ns,device,inode,sha256,artist,title,album,album_artist,genre,grouping,year,bpm,musical_key,duration_seconds,bitrate_bps,sample_rate_hz,channels,bits_per_sample,metadata_json,parse_error,first_seen_scan_id,last_seen_scan_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                canonical,
                canonical,
                library_index.mac_path_identity(canonical),
                ".mp3",
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_dev,
                stat.st_ino,
                digest,
                "Artist",
                name,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                10.0,
                320000,
                44100,
                2,
                16,
                "{}",
                None,
                1,
                1,
            ),
        )
        file_id = int(cur.lastrowid)
        count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        total = conn.execute("SELECT COALESCE(SUM(size_bytes),0) FROM files").fetchone()[0]
        conn.execute("UPDATE scans SET file_count=?,total_bytes=? WHERE scan_id=1", (count, total))
        conn.commit()
        conn.close()
        return file_id, Path(canonical), digest

    def _xml(
        self,
        source_paths: list[Path],
        *,
        tempos: bool = False,
        cues: bool = False,
        order: list[int] | None = None,
    ) -> Path:
        tracks: list[str] = []
        for index, source in enumerate(source_paths, start=1):
            children = ""
            if tempos:
                children += '<TEMPO Inizio="0.000" Bpm="120.00" Metro="4/4" Battito="1" />'
            if cues:
                children += '<POSITION_MARK Name="Cue 1" Type="0" Start="1.000" Num="0" />'
            tracks.append(
                f'<TRACK TrackID="{index}" Name="T{index}" '
                f'Location="{rekordbox_bridge_xml._path_to_location(str(source))}">'
                f"{children}</TRACK>"
            )
        ids = order or list(range(1, len(source_paths) + 1))
        memberships = "".join(f'<TRACK Key="{track_id}" />' for track_id in ids)
        raw = (
            '<?xml version="1.0" encoding="UTF-8" ?>'
            f'<DJ_PLAYLISTS Version="1.0.0"><COLLECTION Entries="{len(tracks)}">'
            + "".join(tracks)
            + '</COLLECTION><PLAYLISTS><NODE Name="Gig" Type="0" Count="1">'
            f'<NODE Name="Set" Type="1" KeyType="0" Entries="{len(ids)}">'
            + memberships
            + '</NODE></NODE></PLAYLISTS></DJ_PLAYLISTS>'
        )
        xml = self.tmp / "expected.xml"
        xml.write_text(raw, encoding="utf-8")
        return xml

    def _usb_inventory(self) -> dict:
        return {
            "volume": {
                "path": str(self.volume),
                "capacity_after": {"free_bytes": 10_000_000},
                "diskutil": {"filesystem_type": "exfat"},
            },
            "health": {"status": "PASS", "reasons": []},
        }

    def _profile(self, library_format: str) -> Path:
        profile = self.tmp / f"profile-{library_format}.json"
        profile.write_text(
            json.dumps(
                {
                    "tool": "dj_hardware_profile",
                    "model": "TEST-PLAYER",
                    "verification": {"verified": True, "source": "test fixture"},
                    "device_library_format": library_format,
                }
            ),
            encoding="utf-8",
        )
        return profile

    def _copy_to_usb(self, name: str, payload: bytes) -> Path:
        path = self.volume / "Contents" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def _normalized(self, tracks: list[tuple[int, str]], playlist_ids: list[int]) -> dict:
        return {
            "format": "DEVICE_LIBRARY",
            "tracks": {
                track_id: {
                    "id": track_id,
                    "device_path": path,
                    "analysis_path": "PIONEER/USBANLZ/P001/ABC/ANLZ0000.DAT",
                    "title": f"T{track_id}",
                }
                for track_id, path in tracks
            },
            "playlists": {"Gig / Set": playlist_ids},
            "parser": {"fixture": True},
        }

    def _run(
        self,
        xml: Path,
        profile: Path | None = None,
        *,
        legacy_normalized: dict | None = None,
        onelibrary_normalized: dict | None = None,
    ) -> dict:
        patches = [
            mock.patch(
                "post_export_usb_verify.usb_readonly_inventory._explicit_mountpoint",
                return_value=self.volume,
            ),
            mock.patch(
                "post_export_usb_verify.usb_readonly_inventory.inventory",
                return_value=self._usb_inventory(),
            ),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        if legacy_normalized is not None:
            db = self.volume / "PIONEER" / "rekordbox" / "export.pdb"
            db.parent.mkdir(parents=True, exist_ok=True)
            db.write_bytes(b"legacy-db")
            patcher = mock.patch(
                "post_export_usb_verify._legacy_normalized",
                return_value=legacy_normalized,
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        if onelibrary_normalized is not None:
            db = self.volume / "PIONEER" / "rekordbox" / "exportLibrary.db"
            db.parent.mkdir(parents=True, exist_ok=True)
            db.write_bytes(b"one-db")
            patcher = mock.patch(
                "post_export_usb_verify._onelibrary_normalized",
                return_value=onelibrary_normalized,
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        return post_export_usb_verify.verify_export(self.db, xml, self.volume, profile)

    def test_unknown_hardware_is_blocked_even_when_expected_audio_is_present(self):
        _, source, _ = self._add_library_file("one", b"same")
        self._copy_to_usb("one.mp3", b"same")
        report = self._run(self._xml([source]))
        self.assertEqual(report["overall"]["status"], "BLOCKED")
        self.assertFalse(report["overall"]["verified_for_gig"])
        self.assertEqual(report["checks"]["audio_fidelity"]["status"], "PASS")

    def test_missing_expected_usb_audio_is_hard_failure(self):
        _, source, _ = self._add_library_file("one", b"same")
        report = self._run(self._xml([source]))
        self.assertEqual(report["overall"]["status"], "FAIL")
        self.assertEqual(len(report["checks"]["audio_fidelity"]["missing"]), 1)

    def test_duplicate_usb_audio_is_warning_not_deletion_authority(self):
        _, source, _ = self._add_library_file("one", b"same")
        self._copy_to_usb("one.mp3", b"same")
        self._copy_to_usb("copy.mp3", b"same")
        report = self._run(self._xml([source]))
        audio = report["checks"]["audio_fidelity"]
        self.assertEqual(report["overall"]["status"], "BLOCKED")
        self.assertEqual(audio["multiple_copy_status"], "WARN")
        self.assertEqual(len(audio["multiple_usb_copies"]), 1)
        self.assertFalse(audio["deletion_authority"])

    def test_playlist_order_is_compared_by_sha_not_device_track_id(self):
        _, one, _ = self._add_library_file("one", b"one")
        _, two, _ = self._add_library_file("two", b"two")
        self._copy_to_usb("one.mp3", b"one")
        self._copy_to_usb("two.mp3", b"two")
        xml = self._xml([one, two])
        expected = post_export_usb_verify._expected_model(
            rekordbox_index._parse_export(xml.read_bytes()),
            post_export_usb_verify._current_library(self.db)[1],
        )
        normalized = self._normalized(
            [(900, "Contents/one.mp3"), (700, "Contents/two.mp3")],
            [900, 700],
        )
        with mock.patch(
            "post_export_usb_verify._analysis_for_track",
            return_value={"status": "PASS", "has_grid": False, "has_cue_data": False},
        ):
            result = post_export_usb_verify._verify_normalized_library(
                normalized, expected, self.volume
            )
        self.assertEqual(result["status"], "PASS")

        normalized["playlists"]["Gig / Set"] = [700, 900]
        with mock.patch(
            "post_export_usb_verify._analysis_for_track",
            return_value={"status": "PASS", "has_grid": False, "has_cue_data": False},
        ):
            result = post_export_usb_verify._verify_normalized_library(
                normalized, expected, self.volume
            )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["playlist_errors"][0]["reason"], "ordered_membership_mismatch")

    def test_unreadable_unrelated_device_track_is_warning_not_false_failure(self):
        _, source, _ = self._add_library_file("one", b"one")
        self._copy_to_usb("one.mp3", b"one")
        xml = self._xml([source])
        expected = post_export_usb_verify._expected_model(
            rekordbox_index._parse_export(xml.read_bytes()),
            post_export_usb_verify._current_library(self.db)[1],
        )
        normalized = self._normalized(
            [(10, "Contents/one.mp3"), (99, "Contents/missing-extra.mp3")],
            [10],
        )
        with mock.patch(
            "post_export_usb_verify._analysis_for_track",
            return_value={"status": "PASS", "has_grid": False, "has_cue_data": False},
        ):
            result = post_export_usb_verify._verify_normalized_library(
                normalized, expected, self.volume
            )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(result["unrelated_or_unreadable_device_track_warnings"]), 1)

    def test_device_path_escape_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "unsafe device-relative path"):
            post_export_usb_verify._resolve_device_path(self.volume, "../../outside.mp3")

    def test_symlink_component_in_device_path_is_refused(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        link = self.volume / "Contents"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("host does not permit symlinks")
        with self.assertRaisesRegex(RuntimeError, "contains a symlink"):
            post_export_usb_verify._resolve_device_path(self.volume, "Contents/song.mp3")

    def test_competing_hidden_and_visible_device_databases_are_refused(self):
        for root in ("PIONEER", ".PIONEER"):
            path = self.volume / root / "rekordbox" / "export.pdb"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"db")
        with self.assertRaisesRegex(RuntimeError, "multiple competing"):
            post_export_usb_verify._select_existing(
                self.volume, post_export_usb_verify.LEGACY_CANDIDATES
            )

    def test_anlz_parser_requires_well_formed_lengths(self):
        with self.assertRaisesRegex(RuntimeError, "truncated"):
            post_export_usb_verify._anlz_evidence(
                b"PMAI" + struct.pack(">II", 12, 100) + b"x"
            )

    def _anlz(self, *, grid: bool, cues: bool) -> bytes:
        sections: list[bytes] = []
        if grid:
            sections.append(
                b"PQTZ" + struct.pack(">II", 24, 24) + b"\x00" * 8 + struct.pack(">I", 2)
            )
        if cues:
            sections.append(b"PCOB" + struct.pack(">II", 12, 12))
        total = 12 + sum(len(section) for section in sections)
        return b"PMAI" + struct.pack(">II", 12, total) + b"".join(sections)

    def test_anlz_grid_and_cue_evidence_is_explicit(self):
        grid = post_export_usb_verify._anlz_evidence(self._anlz(grid=True, cues=False))
        cue = post_export_usb_verify._anlz_evidence(self._anlz(grid=False, cues=True))
        self.assertTrue(grid["has_grid"])
        self.assertFalse(grid["has_cue_data"])
        self.assertFalse(cue["has_grid"])
        self.assertTrue(cue["has_cue_data"])

    def test_expected_grid_and_cues_without_matching_analysis_evidence_fail(self):
        _, source, _ = self._add_library_file("one", b"one")
        self._copy_to_usb("one.mp3", b"one")
        xml = self._xml([source], tempos=True, cues=True)
        expected = post_export_usb_verify._expected_model(
            rekordbox_index._parse_export(xml.read_bytes()),
            post_export_usb_verify._current_library(self.db)[1],
        )
        normalized = self._normalized([(1, "Contents/one.mp3")], [1])
        with mock.patch(
            "post_export_usb_verify._analysis_for_track",
            return_value={"status": "PASS", "has_grid": False, "has_cue_data": False},
        ):
            result = post_export_usb_verify._verify_normalized_library(
                normalized, expected, self.volume
            )
        reasons = {item["reason"] for item in result["analysis"]["errors"]}
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("expected_grid_has_no_ANLZ_evidence", reasons)
        self.assertIn("expected_cues_have_no_ANLZ_evidence", reasons)

    def test_onelibrary_parser_receives_only_verified_temporary_copy(self):
        source = self.volume / "PIONEER" / "rekordbox" / "exportLibrary.db"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"encrypted-one-library")
        before = (source.read_bytes(), source.stat().st_mtime_ns)
        captured: dict[str, object] = {}

        class FakeQuery(list):
            pass

        class FakeDeviceLibraryPlus:
            def __init__(self, path):
                path = Path(path)
                captured["path"] = path
                captured["bytes"] = path.read_bytes()
                captured["under_usb"] = str(path).startswith(str(self_volume))

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def get_content(self):
                return FakeQuery()

            def get_playlist(self):
                return FakeQuery()

            def get_playlist_content(self):
                return FakeQuery()

        self_volume = self.volume
        fake_module = types.ModuleType("pyrekordbox")
        fake_module.DeviceLibraryPlus = FakeDeviceLibraryPlus
        with mock.patch.dict(sys.modules, {"pyrekordbox": fake_module}):
            normalized = post_export_usb_verify._onelibrary_normalized(source.read_bytes())

        self.assertEqual(normalized["format"], "ONELIBRARY")
        self.assertEqual(captured["bytes"], before[0])
        self.assertFalse(captured["under_usb"])
        self.assertFalse(Path(captured["path"]).exists())
        self.assertEqual(source.read_bytes(), before[0])
        self.assertEqual(source.stat().st_mtime_ns, before[1])

    def test_legacy_parser_normalizes_playlist_order_from_parser_objects(self):
        fake_module = types.ModuleType("rekordbox_pdb")

        class FakeDatabase:
            @classmethod
            def from_bytes(cls, raw):
                self.assertEqual(raw, b"legacy")
                return SimpleNamespace(
                    tracks=[
                        SimpleNamespace(
                            id=17,
                            file_path="Contents/one.mp3",
                            analyze_path="PIONEER/USBANLZ/P001/X/ANLZ0000.DAT",
                            title="One",
                        )
                    ],
                    playlist_tree=[
                        SimpleNamespace(id=1, parent_id=0, name="Gig", is_folder=True),
                        SimpleNamespace(id=2, parent_id=1, name="Set", is_folder=False),
                    ],
                    playlist_entries=[
                        SimpleNamespace(playlist_id=2, entry_index=1, track_id=17),
                    ],
                )

        fake_module.Database = FakeDatabase
        with mock.patch.dict(sys.modules, {"rekordbox_pdb": fake_module}):
            normalized = post_export_usb_verify._legacy_normalized(b"legacy")
        self.assertEqual(normalized["playlists"], {"Gig / Set": [17]})
        self.assertEqual(normalized["tracks"][17]["device_path"], "Contents/one.mp3")

    def test_verified_device_library_profile_can_pass_with_different_track_ids(self):
        _, source, _ = self._add_library_file("one", b"one")
        self._copy_to_usb("one.mp3", b"one")
        normalized = self._normalized([(999, "Contents/one.mp3")], [999])
        with mock.patch(
            "post_export_usb_verify._analysis_for_track",
            return_value={"status": "PASS", "has_grid": False, "has_cue_data": False},
        ):
            report = self._run(
                self._xml([source]),
                self._profile("DEVICE_LIBRARY"),
                legacy_normalized=normalized,
            )
        self.assertEqual(report["overall"]["status"], "PASS")
        self.assertTrue(report["overall"]["verified_for_gig"])

    def test_profile_requiring_both_libraries_requires_both_to_pass(self):
        _, source, _ = self._add_library_file("one", b"one")
        self._copy_to_usb("one.mp3", b"one")
        legacy = self._normalized([(5, "Contents/one.mp3")], [5])
        one = dict(legacy)
        one["format"] = "ONELIBRARY"
        one["tracks"] = {8: dict(next(iter(legacy["tracks"].values())), id=8)}
        one["playlists"] = {"Gig / Set": [8]}
        with mock.patch(
            "post_export_usb_verify._analysis_for_track",
            return_value={"status": "PASS", "has_grid": False, "has_cue_data": False},
        ):
            passing = self._run(
                self._xml([source]),
                self._profile("BOTH"),
                legacy_normalized=legacy,
                onelibrary_normalized=one,
            )
        self.assertEqual(passing["overall"]["status"], "PASS")

        # Rebuild a clean volume with only Device Library: BOTH must fail.
        other_volume = self.tmp / "USB2"
        other_volume.mkdir()
        self.volume = other_volume
        self._copy_to_usb("one.mp3", b"one")
        with mock.patch(
            "post_export_usb_verify._analysis_for_track",
            return_value={"status": "PASS", "has_grid": False, "has_cue_data": False},
        ):
            missing_one = self._run(
                self._xml([source]),
                self._profile("BOTH"),
                legacy_normalized=legacy,
            )
        self.assertEqual(missing_one["overall"]["status"], "FAIL")
        statuses = {
            item["format"]: item["status"]
            for item in missing_one["overall"]["required_library_results"]
        }
        self.assertEqual(statuses["ONELIBRARY"], "MISSING")


if __name__ == "__main__":
    unittest.main()
