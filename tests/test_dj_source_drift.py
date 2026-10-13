"""Synthetic read-only source drift tests; no real DJ library is accessed."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dj_source_drift as drift  # noqa: E402


class SourceDriftTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.db = root / "index.sqlite3"
        self.serato = root / "_Serato_"
        (self.serato / "Subcrates").mkdir(parents=True)
        self.database = self.serato / "database V2"
        self.crate = self.serato / "Subcrates" / "one.crate"
        self.xml = root / "collection.xml"
        for path, payload in ((self.database, b"serato"), (self.crate, b"crate"), (self.xml, b"<xml/>")):
            path.write_bytes(payload)
        fp = lambda path: {
            "path": str(path), "size_bytes": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
        }
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE scans(scan_id INTEGER PRIMARY KEY,
                    completed_at TEXT,completed_successfully INTEGER);
                CREATE TABLE serato_imports(import_id INTEGER PRIMARY KEY,
                    completed_successfully INTEGER,serato_dir TEXT,source_manifest_json TEXT);
                CREATE TABLE rekordbox_imports(import_id INTEGER PRIMARY KEY,
                    completed_successfully INTEGER,xml_path TEXT,source_fingerprint_json TEXT);
            """)
            conn.executemany("INSERT INTO meta VALUES (?,?)", [
                ("schema_version", "3"),
                ("root_canonical", str(self.db.parent / "Music"))
            ])
            conn.execute("INSERT INTO scans VALUES (1, '2026-10-13T14:00:00Z', 1)")
            conn.execute("INSERT INTO serato_imports VALUES(1,1,?,?)",
                         (str(self.serato), json.dumps([fp(self.database), fp(self.crate)])))
            conn.execute("INSERT INTO rekordbox_imports VALUES(1,1,?,?)",
                         (str(self.xml), json.dumps(fp(self.xml))))

    def test_clean_and_read_only(self):
        original = self.db.read_bytes()
        result = drift.report(self.db)
        self.assertFalse(result["requires_attention"])
        self.assertEqual(result["serato"]["state"], "UNCHANGED")
        self.assertEqual(result["rekordbox"]["state"], "UNCHANGED")
        self.assertEqual(result["index"]["completed_scan_id"], 1)
        self.assertIn("current app", result["note"])
        self.assertEqual(self.db.read_bytes(), original)

    def test_modified_crate_and_xml(self):
        self.crate.write_bytes(b"changed")
        self.xml.write_bytes(b"<updated/>")
        result = drift.report(self.db)
        self.assertEqual(result["serato"]["modified"], ["Subcrates/one.crate"])
        self.assertEqual(result["rekordbox"]["state"], "CHANGED")
        self.assertTrue(result["requires_attention"])

    def test_added_and_removed(self):
        self.crate.unlink()
        (self.serato / "Subcrates" / "two.crate").write_bytes(b"new")
        result = drift.report(self.db)["serato"]
        self.assertEqual(result["removed"], ["Subcrates/one.crate"])
        self.assertEqual(result["added"], ["Subcrates/two.crate"])

    def test_missing_sources_are_unknown_not_unchanged(self):
        self.xml.unlink()
        self.assertEqual(drift.report(self.db)["rekordbox"]["state"], "EXPORT_UNAVAILABLE")
        self.assertEqual(
            drift.report(self.db, serato_dir=self.serato.parent / "missing")["serato"]["state"],
            "SOURCE_UNAVAILABLE",
        )

    def test_absent_baselines(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DELETE FROM serato_imports")
            conn.execute("DELETE FROM rekordbox_imports")
        result = drift.report(self.db)
        self.assertEqual(result["serato"]["state"], "NO_BASELINE")
        self.assertEqual(result["rekordbox"]["state"], "NO_BASELINE")

    def test_missing_index_not_created(self):
        missing = self.db.parent / "missing.sqlite3"
        with self.assertRaises(ValueError):
            drift.report(missing)
        self.assertFalse(missing.exists())

    def test_symlink_rejected(self):
        self.crate.unlink()
        self.crate.symlink_to(self.database)
        with self.assertRaisesRegex(ValueError, "non-symlink"):
            drift.report(self.db)

    def test_malformed_baselines_are_rejected_not_silently_unchanged(self):
        cases = (
            ("serato_imports", "source_manifest_json", [{"path": str(self.database), "sha256": "garbage", "size_bytes": 6}]),
            ("serato_imports", "source_manifest_json", [{"path": str(self.database), "sha256": "a" * 64}]),
            ("serato_imports", "source_manifest_json", [{"path": str(self.serato / "unknown.bin"), "sha256": "a" * 64, "size_bytes": 6}]),
            ("rekordbox_imports", "source_fingerprint_json", {"path": str(self.xml), "sha256": "broken", "size_bytes": 6}),
            ("rekordbox_imports", "source_fingerprint_json", {"path": str(self.xml), "sha256": "a" * 64}),
        )
        with sqlite3.connect(self.db) as conn:
            original_s = conn.execute(
                "SELECT source_manifest_json FROM serato_imports WHERE import_id=1"
            ).fetchone()[0]
            original_r = conn.execute(
                "SELECT source_fingerprint_json FROM rekordbox_imports WHERE import_id=1"
            ).fetchone()[0]
        for table, column, replacement in cases:
            with self.subTest(table=table, replacement=replacement):
                with sqlite3.connect(self.db) as conn:
                    conn.execute(f"UPDATE {table} SET {column}=? WHERE import_id=1",
                                 (json.dumps(replacement),))
                with self.assertRaisesRegex(ValueError, "invalid|unexpected|baseline"):
                    drift.report(self.db)
                with sqlite3.connect(self.db) as conn:
                    conn.execute(f"UPDATE {table} SET {column}=? WHERE import_id=1",
                                 (original_s if table == "serato_imports" else original_r,))

    def test_reject_unbound_or_incomplete_index(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
        with self.assertRaisesRegex(ValueError, "schema"):
            drift.report(self.db)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
            conn.execute("DELETE FROM scans")
        with self.assertRaisesRegex(ValueError, "completed"):
            drift.report(self.db)

    def test_latest_completed_import_over_failed_attempt(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO serato_imports VALUES(2,0,?,?)",
                         (str(self.serato), "{}"))
            conn.execute("INSERT INTO rekordbox_imports VALUES(2,0,?,?)",
                         (str(self.xml), "{}"))
        result = drift.report(self.db)
        self.assertEqual(result["serato"]["baseline_import_id"], 1)
        self.assertEqual(result["rekordbox"]["baseline_import_id"], 1)
        self.assertFalse(result["requires_attention"])

    def test_symlinked_source_root_and_rekordbox_export_are_never_unchanged(self):
        alias = self.serato.parent / "serato-alias"
        alias.symlink_to(self.serato, target_is_directory=True)
        self.assertEqual(
            drift.report(self.db, serato_dir=alias)["serato"]["state"],
            "SOURCE_UNAVAILABLE",
        )
        self.xml.unlink()
        self.xml.symlink_to(self.database)
        with self.assertRaisesRegex(ValueError, "non-symlink"):
            drift.report(self.db)



if __name__ == "__main__":
    unittest.main()
