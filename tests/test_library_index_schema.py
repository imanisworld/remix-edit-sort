from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import library_index  # noqa: E402


class LibraryIndexSchemaTests(unittest.TestCase):
    def test_incompatible_schema_is_rejected_before_any_new_ddl(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "library.sqlite3"
            conn = sqlite3.connect(db)
            try:
                conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                conn.execute("INSERT INTO meta(key, value) VALUES('schema_version', '2')")
                conn.commit()
                before = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
            finally:
                conn.close()

            with self.assertRaises(RuntimeError):
                library_index.connect_database(db)

            conn = sqlite3.connect(db)
            try:
                after = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                version = conn.execute(
                    "SELECT value FROM meta WHERE key='schema_version'"
                ).fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(after, before)
            self.assertEqual(version, "2")
            self.assertNotIn("scan_errors", after)


if __name__ == "__main__":
    unittest.main()
