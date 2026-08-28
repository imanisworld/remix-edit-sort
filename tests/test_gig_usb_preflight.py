from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import gig_usb_preflight  # noqa: E402
import library_index  # noqa: E402
import rekordbox_bridge_xml  # noqa: E402
import rekordbox_index  # noqa: E402


class GigUsbPreflightTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = self.tmp / "library.sqlite3"
        self.volume = self.tmp / "USB"
        self.volume.mkdir()
        self._init_db()

    def tearDown(self):
        self._tmp.cleanup()

    def _init_db(self):
        conn = sqlite3.connect(self.db)
        conn.executescript(library_index.SCHEMA_SQL)
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (str(library_index.SCHEMA_VERSION),))
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('rekordbox_schema_version',?)", (str(rekordbox_index.REKORDBOX_SCHEMA_VERSION),))
        conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('root_canonical',?)", (library_index.canonicalize_path(self.tmp),))
        conn.execute(
            "INSERT INTO scans(scan_id,started_at,completed_at,root_display,root_canonical,config_json,file_count,total_bytes,new_count,changed_count,moved_count,removed_count,unchanged_count,error_count,completed_successfully,tool_version,schema_version) "
            "VALUES(1,'x','x',?,?, '{}',0,0,0,0,0,0,0,0,1,'test',?)",
            (str(self.tmp), library_index.canonicalize_path(self.tmp), library_index.SCHEMA_VERSION),
        )
        conn.commit()
        conn.close()

    def _add_file(self, name: str, payload: bytes = b"audio", extension: str = ".mp3", *, sha_override: str | None = None):
        path = self.tmp / f"{name}{extension}"
        path.write_bytes(payload)
        canonical = library_index.canonicalize_path(path)
        sha = sha_override or hashlib.sha256(payload).hexdigest()
        conn = sqlite3.connect(self.db)
        cur = conn.execute(
            "INSERT INTO files(canonical_path,display_path,path_identity,extension,size_bytes,mtime_ns,device,inode,sha256,artist,title,album,album_artist,genre,grouping,year,bpm,musical_key,duration_seconds,bitrate_bps,sample_rate_hz,channels,bits_per_sample,metadata_json,parse_error,first_seen_scan_id,last_seen_scan_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                canonical, canonical, library_index.mac_path_identity(canonical), extension,
                path.stat().st_size, path.stat().st_mtime_ns, path.stat().st_dev, path.stat().st_ino,
                sha, 'Artist', name, None, None, None, None, None, None, None, 10.0,
                320000, 44100, 2, 16, '{}', None, 1, 1,
            ),
        )
        file_id = cur.lastrowid
        count = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        total = conn.execute("SELECT COALESCE(SUM(size_bytes),0) FROM files").fetchone()[0]
        conn.execute("UPDATE scans SET file_count=?, total_bytes=? WHERE scan_id=1", (count, total))
        conn.commit()
        conn.close()
        return file_id, Path(canonical)

    def _xml(self, paths: list[Path], *, bad_reference: bool = False, tempos: bool = True) -> Path:
        tracks = []
        memberships = []
        for index, path in enumerate(paths, start=1):
            children = '<TEMPO Inizio="0.000" Bpm="120.00" Metro="4/4" Battito="1" />' if tempos else ''
            tracks.append(
                f'<TRACK TrackID="{index}" Name="T{index}" Location="{rekordbox_bridge_xml._path_to_location(str(path))}">{children}</TRACK>'
            )
            key = "999" if bad_reference and index == 1 else str(index)
            memberships.append(f'<TRACK Key="{key}" />')
        raw = (
            '<?xml version="1.0" encoding="UTF-8" ?>'
            f'<DJ_PLAYLISTS Version="1.0.0"><COLLECTION Entries="{len(tracks)}">'
            + ''.join(tracks)
            + '</COLLECTION><PLAYLISTS><NODE Name="Gig" Type="0" Count="1">'
            f'<NODE Name="Set" Type="1" KeyType="0" Entries="{len(memberships)}">'
            + ''.join(memberships)
            + '</NODE></NODE></PLAYLISTS></DJ_PLAYLISTS>'
        )
        path = self.tmp / "selected.xml"
        path.write_text(raw, encoding="utf-8")
        return path

    def _usb(self, free_bytes: int = 10_000_000, filesystem: str = "exfat") -> dict:
        return {
            "volume": {
                "path": str(self.volume),
                "capacity_after": {"free_bytes": free_bytes},
                "diskutil": {"filesystem_type": filesystem},
            },
            "health": {"status": "PASS", "reasons": []},
        }

    def _profile(self, extension_state: str = "SUPPORTED", filesystem: str = "exfat") -> Path:
        path = self.tmp / "hardware.json"
        path.write_text(
            json.dumps(
                {
                    "tool": "dj_hardware_profile",
                    "profile_schema_version": 1,
                    "model": "TEST-PLAYER",
                    "verification": {"verified": True, "source": "test fixture"},
                    "compatibility": {
                        "extension_rules": {".mp3": extension_state},
                        "supported_filesystems": [filesystem],
                        "max_sample_rate_hz": 48000,
                        "max_bits_per_sample": 24,
                        "max_channels": 2,
                    },
                }
            ),
            encoding="utf-8",
        )
        return path

    def _run(self, xml: Path, profile: Path | None = None, usb: dict | None = None) -> dict:
        with mock.patch("gig_usb_preflight.usb_readonly_inventory.inventory", return_value=usb or self._usb()):
            return gig_usb_preflight.build_preflight(self.db, xml, self.volume, profile)

    def test_without_hardware_profile_is_blocked_not_passed(self):
        _, path = self._add_file("one")
        report = self._run(self._xml([path]))
        self.assertEqual(report["overall"]["status"], "BLOCKED")
        self.assertFalse(report["overall"]["ready_for_rekordbox_export"])
        self.assertEqual(report["checks"]["track_existence"]["status"], "PASS")
        self.assertEqual(report["checks"]["device_audio_compatibility"]["status"], "BLOCKED_DEVICE_PROFILE")

    def test_verified_supported_profile_can_pass(self):
        _, path = self._add_file("one")
        report = self._run(self._xml([path]), self._profile())
        self.assertEqual(report["overall"]["status"], "PASS")
        self.assertTrue(report["overall"]["ready_for_rekordbox_export"])
        self.assertEqual(report["checks"]["filesystem_compatibility"]["status"], "PASS")

    def test_codec_evidence_requirement_blocks_instead_of_guessing(self):
        _, path = self._add_file("one")
        report = self._run(self._xml([path]), self._profile("REQUIRES_CODEC_EVIDENCE"))
        self.assertEqual(report["overall"]["status"], "BLOCKED")
        self.assertEqual(report["checks"]["device_audio_compatibility"]["status"], "BLOCKED_CODEC_EVIDENCE")

    def test_missing_current_audio_is_hard_failure(self):
        _, path = self._add_file("one")
        xml = self._xml([path])
        path.unlink()
        report = self._run(xml, self._profile())
        self.assertEqual(report["overall"]["status"], "FAIL")
        self.assertEqual(report["checks"]["track_existence"]["status"], "FAIL")

    def test_insufficient_space_is_hard_failure(self):
        _, path = self._add_file("one", b"x" * 100)
        report = self._run(self._xml([path]), self._profile(), self._usb(free_bytes=10))
        self.assertEqual(report["overall"]["status"], "FAIL")
        self.assertEqual(report["checks"]["space"]["status"], "FAIL")

    def test_bad_playlist_reference_is_hard_failure(self):
        _, path = self._add_file("one")
        report = self._run(self._xml([path], bad_reference=True), self._profile())
        self.assertEqual(report["overall"]["status"], "FAIL")
        self.assertEqual(report["checks"]["playlist_integrity"]["status"], "FAIL")

    def test_exact_sha_duplicate_is_warning_not_deletion_authority(self):
        _, first = self._add_file("one", b"same")
        _, second = self._add_file("two", b"same")
        report = self._run(self._xml([first, second]), self._profile())
        self.assertEqual(report["overall"]["status"], "PASS")
        self.assertEqual(report["checks"]["duplicates"]["status"], "WARN")
        self.assertEqual(report["checks"]["duplicates"]["exact_sha256_duplicate_groups"], 1)
        self.assertFalse(report["checks"]["duplicates"]["deletion_authority"])

    def test_missing_tempo_grid_is_reported_without_inventing_analysis(self):
        _, path = self._add_file("one")
        report = self._run(self._xml([path], tempos=False), self._profile())
        self.assertEqual(report["checks"]["analysis"]["tempo_grid_missing_count"], 1)
        self.assertEqual(report["overall"]["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
