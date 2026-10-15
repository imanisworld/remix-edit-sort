import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import apply_new_music_intake_plan as intake_apply  # noqa: E402
import backup_manifest  # noqa: E402
import fixtures  # noqa: E402
import mutation_engine  # noqa: E402
from mutagen.id3._specs import SpecError  # noqa: E402


class Rva2SerializerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.tmp = Path(self.temp.name)
        self.root = self.tmp / "library"
        self.path = fixtures.make_library(self.root, [{
            "relative": "a&t (Intro Clean).mp3",
            "genre": "Other",
            "grouping": "Existing",
        }])["a&t (Intro Clean).mp3"]
        self.before = self.path.read_bytes()

    def engine(self, journal):
        engine = mutation_engine.MutationEngine(
            journal,
            heartbeat=None,
            manifest=None,
            source_root=None,
            label="rva2-safety-test",
            allow_unbacked=True,
        )
        engine.start({})
        return engine

    def apply_one(self, engine, side_effect):
        undo = self.tmp / "undo.jsonl"
        with mock.patch.object(intake_apply, "write_metadata", side_effect=side_effect):
            with engine.item(
                1,
                kind=mutation_engine.METADATA,
                source=self.path,
                expected_sha256=backup_manifest.sha256_file(self.path),
                require_backup=False,
            ) as txn:
                return intake_apply._apply_one_metadata_write(
                    txn,
                    self.path,
                    proposed_genre="Hip-Hop",
                    proposed_grouping="Existing; Clean",
                    proposed_year=None,
                    original_genre="Other",
                    original_grouping=["Existing"],
                    original_year="",
                    year_basis=None,
                    year_grade=None,
                    version=4,
                    id3_upgrade=None,
                    tag_backup_root=self.tmp / "tag-backups",
                    index=1,
                    undo_path=undo,
                )

    def test_unserializable_id3_is_skipped_only_when_file_is_unchanged(self):
        journal = self.tmp / "unchanged.jsonl"
        engine = self.engine(journal)
        record = self.apply_one(engine, SpecError("not in range"))
        engine.finish({"transactions": 1})
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertFalse(record["tag_changed"])
        self.assertTrue(record["tag_skipped"])
        self.assertIn("Unserializable ID3 frames preserved", record["tag_skip_reason"])
        self.assertTrue(mutation_engine.recover(journal)["clean"])

    def test_serializer_error_after_byte_change_is_not_treated_as_skip(self):
        journal = self.tmp / "changed.jsonl"
        engine = self.engine(journal)

        def corrupt_then_fail(path, **_kwargs):
            with Path(path).open("ab") as handle:
                handle.write(b"corrupt")
            raise SpecError("not in range")

        with self.assertRaisesRegex(RuntimeError, "serialization failed after changing"):
            self.apply_one(engine, corrupt_then_fail)
        engine.finish({"transactions": 0})
        self.assertEqual(self.path.read_bytes(), self.before)
        report = mutation_engine.recover(journal)
        self.assertTrue(report["clean"], report)
        self.assertEqual(report["rollback_failures"], 0)


if __name__ == "__main__":
    unittest.main()
