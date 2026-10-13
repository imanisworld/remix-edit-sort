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
        fp = lambda path: {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE serato_imports(import_id INTEGER PRIMARY KEY,
                    completed_successfully INTEGER,serato_dir TEXT,source_manifest_json TEXT);
                CREATE TABLE rekordbox_imports(import_id INTEGER PRIMARY KEY,
                    completed_successfully INTEGER,xml_path TEXT,source_fingerprint_json TEXT);
            """)
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


if __name__ == "__main__":
    unittest.main()
