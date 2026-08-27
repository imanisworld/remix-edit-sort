from __future__ import annotations

import json
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

import library_index  # noqa: E402
import serato_index  # noqa: E402


def tlv(tag: str, value: bytes) -> bytes:
    return tag.encode("ascii") + struct.pack(">I", len(value)) + value


def text(value: str) -> bytes:
    return value.encode("utf-16-be")


def otrk(fields: bytes) -> bytes:
    return tlv("otrk", fields)


class SeratoIndexTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.library = self.tmp / "Music"
        self.library.mkdir()
        self.serato = self.tmp / "_Serato_"
        (self.serato / "Subcrates").mkdir(parents=True)
        self.db = self.tmp / "app" / "library.sqlite3"

    def tearDown(self):
        self._tmp.cleanup()

    def add_audio(self, relative: str, payload: bytes) -> Path:
        path = self.library / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def serato_path(self, absolute: Path) -> str:
        return str(absolute).lstrip("/")

    def write_database(self, records: list[tuple[str, str, str]]) -> None:
        body = tlv("vrsn", text("2.0"))
        for path, title, artist in records:
            body += otrk(
                tlv("pfil", text(path))
                + tlv("tsng", text(title))
                + tlv("tart", text(artist))
            )
        (self.serato / "database V2").write_bytes(body)

    def write_crate(self, name: str, paths: list[str]) -> None:
        body = tlv("vrsn", text("1.0"))
        for path in paths:
            body += otrk(tlv("ptrk", text(path)))
        (self.serato / "Subcrates" / f"{name}.crate").write_bytes(body)

    def build_index(self) -> None:
        library_index.scan(self.library, self.db)

    def test_import_links_exact_and_preserves_uncertain_states(self):
        exact = self.add_audio("House/Exact.mp3", b"exact")
        moved = self.add_audio("Pop/Moved.mp3", b"moved")
        self.add_audio("A/Shared.mp3", b"first")
        self.add_audio("B/Shared.mp3", b"second")
        crate_only = self.add_audio("Tools/CrateOnly.mp3", b"crate")
        self.build_index()

        old_moved = self.library / "Old" / moved.name
        old_shared = self.library / "Old" / "Shared.mp3"
        missing = self.library / "Old" / "Gone.mp3"
        self.write_database(
            [
                (self.serato_path(exact), "Exact", "Artist"),
                (self.serato_path(old_moved), "Moved", "Artist"),
                (self.serato_path(old_shared), "Shared", "Artist"),
                (self.serato_path(missing), "Gone", "Artist"),
            ]
        )
        self.write_crate(
            "Set",
            [self.serato_path(exact), self.serato_path(crate_only)],
        )
        source_before = {
            path: (path.read_bytes(), path.stat().st_mtime_ns)
            for path in [self.serato / "database V2", self.serato / "Subcrates" / "Set.crate"]
        }

        report = serato_index.import_serato(self.serato, self.db)

        self.assertEqual(report["database_tracks"], 4)
        self.assertEqual(report["crates"], 1)
        self.assertEqual(report["crate_references"], 2)
        self.assertEqual(report["unique_paths"], 5)
        self.assertEqual(report["exact_path"], 2)
        self.assertEqual(report["moved_likely"], 1)
        self.assertEqual(report["ambiguous"], 1)
        self.assertEqual(report["missing"], 1)

        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            rows = {
                Path(row["absolute_path"]).name: row
                for row in conn.execute(
                    "SELECT * FROM serato_tracks WHERE import_id=?",
                    (report["import_id"],),
                )
            }
            memberships = list(
                conn.execute(
                    "SELECT crate_name,serato_path FROM serato_crate_memberships WHERE import_id=?",
                    (report["import_id"],),
                )
            )
            base_schema = conn.execute(
                "SELECT value FROM meta WHERE key='schema_version'"
            ).fetchone()[0]
            serato_schema = conn.execute(
                "SELECT value FROM meta WHERE key='serato_schema_version'"
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(rows["Exact.mp3"]["match_state"], "EXACT_PATH")
        self.assertEqual(rows["Moved.mp3"]["match_state"], "MOVED_LIKELY")
        self.assertIsNone(rows["Moved.mp3"]["file_id"])
        self.assertIsNone(rows["Moved.mp3"]["matched_display_path"])
        self.assertEqual(len(json.loads(rows["Moved.mp3"]["candidates_json"])), 1)
        self.assertEqual(rows["Shared.mp3"]["match_state"], "AMBIGUOUS")
        self.assertIsNone(rows["Shared.mp3"]["file_id"])
        self.assertEqual(rows["Gone.mp3"]["match_state"], "MISSING")
        self.assertEqual(len(memberships), 2)
        self.assertEqual(base_schema, str(library_index.SCHEMA_VERSION))
        self.assertEqual(serato_schema, str(serato_index.SERATO_SCHEMA_VERSION))

        for path, (payload, mtime_ns) in source_before.items():
            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(path.stat().st_mtime_ns, mtime_ns)

    def test_truncated_serato_input_fails_without_replacing_trusted_import(self):
        exact = self.add_audio("House/Exact.mp3", b"exact")
        self.build_index()
        self.write_database([(self.serato_path(exact), "Exact", "Artist")])
        first = serato_index.import_serato(self.serato, self.db)

        database = self.serato / "database V2"
        database.write_bytes(database.read_bytes() + b"pfil\x00\x00\x00")
        with self.assertRaises(RuntimeError):
            serato_index.import_serato(self.serato, self.db)

        conn = sqlite3.connect(self.db)
        try:
            import_ids = [row[0] for row in conn.execute("SELECT import_id FROM serato_imports")]
        finally:
            conn.close()
        self.assertEqual(import_ids, [first["import_id"]])

    def test_source_change_during_import_preserves_prior_trusted_state(self):
        exact = self.add_audio("House/Exact.mp3", b"exact")
        self.build_index()
        self.write_database([(self.serato_path(exact), "Exact", "Artist")])
        first = serato_index.import_serato(self.serato, self.db)
        stable = serato_index._source_manifest(self.serato)
        changed = [
            serato_index.SourceFingerprint(
                path=item.path,
                size_bytes=item.size_bytes,
                mtime_ns=item.mtime_ns + 1,
                sha256=item.sha256,
            )
            for item in stable
        ]

        with mock.patch.object(
            serato_index,
            "_source_manifest",
            side_effect=[stable, changed],
        ):
            with self.assertRaises(RuntimeError):
                serato_index.import_serato(self.serato, self.db)

        conn = sqlite3.connect(self.db)
        try:
            import_ids = [row[0] for row in conn.execute("SELECT import_id FROM serato_imports")]
        finally:
            conn.close()
        self.assertEqual(import_ids, [first["import_id"]])

    def test_unversioned_serato_tables_are_refused_without_changes(self):
        self.add_audio("House/Exact.mp3", b"exact")
        self.build_index()
        conn = sqlite3.connect(self.db)
        try:
            conn.execute("CREATE TABLE serato_unknown(value TEXT)")
            conn.execute("INSERT INTO serato_unknown(value) VALUES('keep-me')")
            conn.commit()
        finally:
            conn.close()
        self.write_database([])

        with self.assertRaises(RuntimeError):
            serato_index.import_serato(self.serato, self.db)

        conn = sqlite3.connect(self.db)
        try:
            value = conn.execute("SELECT value FROM serato_unknown").fetchone()[0]
            known = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='serato_imports'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(value, "keep-me")
        self.assertIsNone(known)

    def test_database_inside_serato_directory_is_rejected(self):
        self.write_database([])
        with self.assertRaises(ValueError):
            serato_index.import_serato(
                self.serato,
                self.serato / "library.sqlite3",
            )


if __name__ == "__main__":
    unittest.main()
