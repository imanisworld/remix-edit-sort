import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import audit_serato_metadata_parity as parity  # noqa: E402


def tlv(tag: str, value: bytes) -> bytes:
    return tag.encode("ascii") + struct.pack(">I", len(value)) + value


def text(value: str) -> bytes:
    return value.encode("utf-16-be")


def otrk(fields: bytes) -> bytes:
    return tlv("otrk", fields)


class SeratoMetadataParityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.serato = self.root / "_Serato_"
        (self.serato / "Subcrates").mkdir(parents=True)
        self.music = self.root / "Music"
        self.music.mkdir()
        self.track = self.music / "Song.mp3"
        self.track.write_bytes(b"synthetic")
        self.canonical = str(self.track).lstrip("/")
        self.alias = "/" + self.canonical
        self.master = self.root / "master.sqlite"
        self.root_db = self.root / "root.sqlite"

    def write_database(self, records):
        body = tlv("vrsn", text("2.0"))
        for record in records:
            fields = tlv("pfil", text(record["path"]))
            for tag, value in record.get("fields", {}).items():
                fields += tlv(tag, text(value))
            body += otrk(fields)
        (self.serato / "database V2").write_bytes(body)

    def write_crate(self, paths):
        body = tlv("vrsn", text("1.0"))
        for path in paths:
            body += otrk(tlv("ptrk", text(path)))
        (self.serato / "Subcrates" / "Set.crate").write_bytes(body)

    def write_sqlite(self, path, rows):
        connection = sqlite3.connect(path)
        connection.execute(
            "CREATE TABLE asset ("
            "id INTEGER PRIMARY KEY, portable_id TEXT, artist TEXT, name TEXT, "
            "bpm REAL, file_bit_rate REAL)"
        )
        connection.executemany(
            "INSERT INTO asset VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
        connection.close()

    @staticmethod
    def metadata(_path):
        return {
            "artist": "Artist",
            "title": "Song",
            "bpm": "100",
            "bitrate_bps": 320000,
        }

    def test_matching_file_v2_crate_and_sqlite_rows_pass(self):
        fields = {
            "tart": "Artist",
            "tsng": "Song",
            "tbpm": "100.00",
            "tbit": "320.0kbps",
        }
        self.write_database([{"path": self.canonical, "fields": fields}])
        self.write_crate([self.canonical])
        row = (1, self.canonical, "Artist", "Song", 100.0, 320000.0)
        self.write_sqlite(self.master, [row])
        self.write_sqlite(self.root_db, [row])

        report = parity.audit_metadata_parity(
            self.serato,
            [self.master, self.root_db],
            metadata_reader=self.metadata,
        )

        self.assertEqual(report["status"], "PASS")
        track = report["logical_tracks"][0]
        self.assertEqual(track["issues"], [])
        self.assertEqual(track["duplicate_cardinality"]["database_v2"], 1)
        self.assertEqual(track["duplicate_cardinality"]["master.sqlite"], 1)
        self.assertEqual(track["duplicate_cardinality"]["root.sqlite"], 1)
        occurrence = track["crate_occurrences"][0]
        self.assertEqual(
            occurrence["matching_database_occurrences"],
            [
                {
                    "record_index": 1,
                    "outer_record_index": 1,
                    "pfil_field_index": 0,
                }
            ],
        )
        self.assertEqual(occurrence["matching_sqlite_ids"]["master.sqlite"], [1])
        self.assertEqual(occurrence["logical_sqlite_ids"]["root.sqlite"], [1])

    def test_sparse_alias_and_missing_bitrates_are_occurrence_level_failures(self):
        populated = {
            "tart": "Artist",
            "tsng": "Song",
            "tbpm": "100.00",
        }
        self.write_database([
            {"path": self.alias},
            {"path": self.canonical, "fields": populated},
        ])
        self.write_crate([self.alias])
        self.write_sqlite(self.master, [
            (10, self.alias, None, None, None, None),
            (11, self.canonical, "Artist", "Song", 100.0, None),
        ])
        self.write_sqlite(self.root_db, [
            (20, self.alias, None, None, None, None),
            (21, self.canonical, "Artist", "Song", 100.0, None),
        ])

        report = parity.audit_metadata_parity(
            self.serato,
            [self.master, self.root_db],
            metadata_reader=self.metadata,
        )

        self.assertEqual(report["status"], "FAIL")
        track = report["logical_tracks"][0]
        issue_codes = {issue["code"] for issue in track["issues"]}
        self.assertIn("noncanonical_crate_path", issue_codes)
        self.assertIn("crate_points_to_sparse_alias", issue_codes)
        self.assertIn("database_bitrate_missing", issue_codes)
        self.assertIn("sqlite_bitrate_missing", issue_codes)
        self.assertEqual(track["duplicate_cardinality"]["database_v2"], 2)
        self.assertEqual(track["duplicate_cardinality"]["master.sqlite"], 2)
        self.assertEqual(track["duplicate_cardinality"]["root.sqlite"], 2)
        self.assertEqual(
            [record["record_index"] for record in track["database_records"]],
            [1, 2],
        )
        self.assertEqual(
            [record["id"] for record in track["sqlite_records"]["master.sqlite"]],
            [10, 11],
        )

    def test_sqlite_sources_are_opened_immutable_and_query_only(self):
        self.write_database([{"path": self.canonical}])
        self.write_crate([])
        row = (1, self.canonical, None, None, None, None)
        self.write_sqlite(self.master, [row])
        before = self.master.stat().st_mtime_ns

        rows = parity.read_sqlite_assets(self.master)

        self.assertEqual(rows[0]["id"], 1)
        self.assertEqual(self.master.stat().st_mtime_ns, before)
        self.assertFalse(self.master.with_name(self.master.name + "-wal").exists())
        self.assertFalse(self.master.with_name(self.master.name + "-shm").exists())

    def test_missing_v2_and_sqlite_counterparts_fail(self):
        self.write_database([])
        self.write_crate([self.canonical])
        self.write_sqlite(self.master, [])
        self.write_sqlite(self.root_db, [])

        report = parity.audit_metadata_parity(
            self.serato,
            [self.master, self.root_db],
            metadata_reader=self.metadata,
        )

        issue_codes = {
            issue["code"]
            for issue in report["logical_tracks"][0]["issues"]
        }
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("crate_without_database_record", issue_codes)
        self.assertIn("sqlite_record_missing", issue_codes)

    def test_nonempty_wal_sidecar_is_refused_before_immutable_read(self):
        row = (1, self.canonical, "Artist", "Song", 100.0, 320000.0)
        self.write_sqlite(self.master, [row])
        self.master.with_name(self.master.name + "-wal").write_bytes(b"pending")

        with self.assertRaisesRegex(RuntimeError, "WAL sidecar"):
            parity.read_sqlite_assets(self.master)

    def test_nonempty_rollback_journal_is_refused_before_immutable_read(self):
        row = (1, self.canonical, "Artist", "Song", 100.0, 320000.0)
        self.write_sqlite(self.master, [row])
        self.master.with_name(self.master.name + "-journal").write_bytes(b"hot")

        with self.assertRaisesRegex(RuntimeError, "rollback-journal"):
            parity.read_sqlite_assets(self.master)

    def test_malformed_crate_is_refused_instead_of_silently_dropped(self):
        self.write_database([{"path": self.canonical}])
        (self.serato / "Subcrates" / "Set.crate").write_bytes(
            b"otrk\x00\x00\x00\x10short"
        )
        self.write_sqlite(self.master, [])
        self.write_sqlite(self.root_db, [])

        with self.assertRaisesRegex(RuntimeError, "truncated TLV"):
            parity.audit_metadata_parity(
                self.serato,
                [self.master, self.root_db],
                metadata_reader=self.metadata,
            )

    def test_invalid_sqlite_portable_id_is_retained_as_source_issue(self):
        self.write_database([])
        self.write_crate([])
        self.write_sqlite(
            self.master,
            [(1, None, "Artist", "Song", 100.0, 320000.0)],
        )
        self.write_sqlite(self.root_db, [])

        report = parity.audit_metadata_parity(
            self.serato,
            [self.master, self.root_db],
            metadata_reader=self.metadata,
        )

        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["summary"]["sqlite_record_counts"]["master.sqlite"], 1)
        self.assertEqual(report["source_issues"][0]["code"], "invalid_sqlite_portable_id")
        self.assertEqual(report["source_issues"][0]["identity"]["id"], 1)

    def test_serato_source_drift_during_audit_is_refused(self):
        self.write_database([{"path": self.canonical}])
        self.write_crate([self.canonical])
        row = (1, self.canonical, "Artist", "Song", 100.0, 320000.0)
        self.write_sqlite(self.master, [row])
        self.write_sqlite(self.root_db, [row])
        original_reader = parity.read_sqlite_assets

        def drifting_reader(path):
            rows = original_reader(path)
            crate = self.serato / "Subcrates" / "Set.crate"
            crate.write_bytes(crate.read_bytes() + tlv("vrsn", text("drift")))
            return rows

        with mock.patch.object(
            parity, "read_sqlite_assets", side_effect=drifting_reader
        ):
            with self.assertRaisesRegex(RuntimeError, "changed during audit"):
                parity.audit_metadata_parity(
                    self.serato,
                    [self.master, self.root_db],
                    metadata_reader=self.metadata,
                )


if __name__ == "__main__":
    unittest.main()
