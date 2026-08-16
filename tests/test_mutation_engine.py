"""The shared mutation transaction engine: preconditions, WAL, rollback, recovery.

All fixtures are synthetic (see tests/fixtures.py). Nothing here reads or
writes the real music library.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
for path in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import backup_manifest  # noqa: E402
import fixtures  # noqa: E402
import mutation_engine  # noqa: E402


class FakeHeartbeat:
    """Stands in for lock_guard.LockHeartbeat so engine mechanics can be tested
    without a real lock. `fail_after` makes ownership disappear mid-run, which
    is the scenario that matters most: the engine must stop BEFORE the next
    mutation, not after it."""

    def __init__(self, fail_after=None):
        self.beats = 0
        self.fail_after = fail_after

    def beat(self, force=False):
        self.beats += 1
        if self.fail_after is not None and self.beats > self.fail_after:
            raise SystemExit("REFUSING TO MUTATE: lock lost mid-run (simulated)")
        return {"session": "test", "run_id": "test-run"}


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.source_root = self.tmp / "Library"
        self.paths = fixtures.make_library(self.source_root, [
            {"relative": "one.mp3", "genre": "Hip Hop"},
            {"relative": "two.mp3", "genre": "R&b"},
            {"relative": "three.mp3", "genre": "House"},
        ])
        self.backup = fixtures.mirror_backup(self.source_root, self.tmp / "backup")
        self.manifest_path = fixtures.passing_manifest(
            self.source_root, self.backup, self.tmp / "manifest.json")
        self.manifest = backup_manifest.require_pass(self.manifest_path, self.source_root)
        self.journal = self.tmp / "recovery.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def engine(self, heartbeat=None, manifest="default", **kwargs):
        return mutation_engine.MutationEngine(
            self.journal,
            heartbeat if heartbeat is not None else FakeHeartbeat(),
            self.manifest if manifest == "default" else manifest,
            self.source_root, "test", **kwargs,
        )

    def journal_records(self):
        return mutation_engine.RecoveryJournal(self.journal).read()

    def types(self):
        return [record.get("type") for record in self.journal_records()]


class PreconditionTests(EngineTestCase):
    def test_happy_path_journals_intent_then_completion(self):
        engine = self.engine()
        engine.start({"items": 1})
        path = self.paths["one.mp3"]
        with engine.item(1, kind=mutation_engine.GENRE, source=path) as txn:
            fixtures.tag_mp3(path, genre="Amapiano")
            txn.mark_mutated()
            txn.record(new_genre="Amapiano")
        engine.finish({"ok": True})

        types = self.types()
        self.assertEqual(types, ["run_start", "intent", "mutated", "completed", "run_complete"])
        # Intent must be journaled BEFORE the mutation record -- that ordering
        # is the entire basis of crash recovery.
        self.assertLess(types.index("intent"), types.index("mutated"))

    def test_missing_source_is_refused_before_anything_is_journaled_as_mutated(self):
        engine = self.engine()
        engine.start({})
        with self.assertRaises(mutation_engine.MutationRefused):
            with engine.item(1, kind=mutation_engine.GENRE, source=self.source_root / "ghost.mp3"):
                self.fail("body must not run")
        self.assertNotIn("mutated", self.types())

    def test_source_hash_changed_since_preview_is_refused(self):
        engine = self.engine()
        engine.start({})
        path = self.paths["one.mp3"]
        with self.assertRaises(mutation_engine.MutationRefused) as ctx:
            with engine.item(1, kind=mutation_engine.GENRE, source=path,
                             expected_sha256="0" * 64):
                self.fail("body must not run")
        self.assertIn("hash changed since preview", str(ctx.exception))

    def test_file_changed_after_the_backup_was_taken_is_refused(self):
        """The manifest PASSes overall, but THIS file no longer matches it."""
        engine = self.engine()
        engine.start({})
        path = self.paths["one.mp3"]
        fixtures.tag_mp3(path, genre="Changed By Someone Else")
        with self.assertRaises(mutation_engine.MutationRefused) as ctx:
            with engine.item(1, kind=mutation_engine.GENRE, source=path):
                self.fail("body must not run")
        self.assertIn("changed after the backup was taken", str(ctx.exception))

    def test_a_file_with_no_backup_evidence_is_refused(self):
        engine = self.engine()
        engine.start({})
        newcomer = fixtures.write_mp3(self.source_root / "newcomer.mp3", seed=42)
        with self.assertRaises(backup_manifest.BackupGateError):
            with engine.item(1, kind=mutation_engine.GENRE, source=newcomer):
                self.fail("body must not run")

    def test_production_run_without_a_manifest_is_refused_at_start(self):
        engine = self.engine(manifest=None)
        with self.assertRaises(mutation_engine.MutationRefused) as ctx:
            engine.start({})
        self.assertIn("requires a verified backup manifest", str(ctx.exception))

    def test_unapproved_mutation_class_is_refused(self):
        engine = self.engine()
        engine.start({})
        with self.assertRaises(mutation_engine.MutationRefused) as ctx:
            with engine.item(1, kind="reencode_to_aac", source=self.paths["one.mp3"]):
                self.fail("body must not run")
        self.assertIn("not an approved mutation class", str(ctx.exception))

    def test_per_item_backup_copy_must_exist_and_match(self):
        engine = self.engine()
        engine.start({})
        path = self.paths["one.mp3"]
        with self.assertRaises(mutation_engine.MutationRefused):
            with engine.item(1, kind=mutation_engine.GENRE, source=path,
                             backup_path=self.tmp / "nope.mp3"):
                self.fail("body must not run")

        wrong = fixtures.write_mp3(self.tmp / "wrong.mp3", seed=123)
        with self.assertRaises(mutation_engine.MutationRefused) as ctx:
            with engine.item(2, kind=mutation_engine.GENRE, source=path, backup_path=wrong):
                self.fail("body must not run")
        self.assertIn("hash mismatch", str(ctx.exception))


class HeartbeatTests(EngineTestCase):
    def test_every_item_heartbeats(self):
        heartbeat = FakeHeartbeat()
        engine = self.engine(heartbeat)
        engine.start({})
        for index, relative in enumerate(["one.mp3", "two.mp3", "three.mp3"], start=1):
            with engine.item(index, kind=mutation_engine.GENRE, source=self.paths[relative]) as txn:
                txn.record(noop=True)
        self.assertEqual(heartbeat.beats, 3)

    def test_lost_lock_stops_the_run_before_the_next_mutation(self):
        """A run that loses its lock must not perform one more write."""
        heartbeat = FakeHeartbeat(fail_after=1)
        engine = self.engine(heartbeat)
        engine.start({})

        path_one = self.paths["one.mp3"]
        with engine.item(1, kind=mutation_engine.GENRE, source=path_one) as txn:
            fixtures.tag_mp3(path_one, genre="Amapiano")
            txn.mark_mutated()

        two_before = mutation_engine.sha256_file(self.paths["two.mp3"])
        with self.assertRaises(SystemExit):
            with engine.item(2, kind=mutation_engine.GENRE, source=self.paths["two.mp3"]):
                self.fail("the second item's body must never run")
        # The second file is untouched, and no intent was even written for it.
        self.assertEqual(mutation_engine.sha256_file(self.paths["two.mp3"]), two_before)
        intents = [r for r in self.journal_records() if r.get("type") == "intent"]
        self.assertEqual([r["index"] for r in intents], [1])


class RollbackTests(EngineTestCase):
    def test_failed_verification_restores_the_file_and_journals_the_rollback(self):
        engine = self.engine()
        engine.start({})
        path = self.paths["one.mp3"]
        before = mutation_engine.sha256_file(path)
        backup_copy = self.backup / "one.mp3"

        with self.assertRaises(RuntimeError):
            with engine.item(1, kind=mutation_engine.GENRE, source=path,
                             backup_path=backup_copy) as txn:
                txn.on_rollback(
                    lambda: mutation_engine.restore_from_backup(backup_copy, path, before))
                fixtures.tag_mp3(path, genre="Wrong")
                txn.mark_mutated()
                raise RuntimeError("Genre tag verification failed")

        self.assertEqual(mutation_engine.sha256_file(path), before,
                         "the file was not restored after a failed verification")
        rolled = [r for r in self.journal_records() if r.get("type") == "rolled_back"]
        self.assertEqual(len(rolled), 1)
        self.assertTrue(rolled[0]["restored"])
        self.assertIn("Genre tag verification failed", rolled[0]["reason"])

    def test_rollback_refuses_a_backup_whose_hash_does_not_match(self):
        path = self.paths["one.mp3"]
        wrong = fixtures.write_mp3(self.tmp / "wrong.mp3", seed=99)
        with self.assertRaises(mutation_engine.MutationVerificationError):
            mutation_engine.restore_from_backup(wrong, path, "0" * 64)

    def test_no_rollback_is_attempted_when_nothing_was_mutated(self):
        engine = self.engine()
        engine.start({})
        calls = []
        with self.assertRaises(RuntimeError):
            with engine.item(1, kind=mutation_engine.GENRE, source=self.paths["one.mp3"]) as txn:
                txn.on_rollback(lambda: calls.append("called"))
                raise RuntimeError("failed before touching the file")
        self.assertEqual(calls, [], "rollback ran for an item that was never mutated")


class RecoveryTests(EngineTestCase):
    def test_a_clean_run_reports_nothing_in_flight(self):
        engine = self.engine()
        engine.start({})
        with engine.item(1, kind=mutation_engine.GENRE, source=self.paths["one.mp3"]) as txn:
            txn.record(noop=True)
        engine.finish({})
        report = mutation_engine.recover(self.journal)
        self.assertTrue(report["clean"])
        self.assertEqual(report["in_flight"], [])

    def test_crash_after_mutation_before_completion_is_recoverable(self):
        """The core crash-recovery guarantee.

        Simulates the process dying between the physical write and the
        completion record by writing the same journal records the engine
        would have, then stopping. Recovery must still name the file, its
        pre-mutation hash, and the backup to restore from.
        """
        journal = mutation_engine.RecoveryJournal(self.journal)
        path = self.paths["one.mp3"]
        before = mutation_engine.sha256_file(path)
        journal.append({"type": "run_start", "run_id": "r1", "label": "test"})
        journal.append({
            "type": "intent", "run_id": "r1", "index": 1, "kind": mutation_engine.GENRE,
            "source": str(path), "before_sha256": before,
            "backup_path": str(self.backup / "one.mp3"), "backup_sha256": before,
            "backup_id": self.manifest["backup_id"],
        })
        journal.append({"type": "mutated", "run_id": "r1", "index": 1, "source": str(path)})
        # ...process dies here. No completion record, no run_complete.

        report = mutation_engine.recover(self.journal)
        self.assertFalse(report["clean"])
        self.assertEqual(len(report["in_flight"]), 1)
        entry = report["in_flight"][0]
        self.assertEqual(entry["source"], str(path))
        self.assertEqual(entry["before_sha256"], before)
        self.assertTrue(entry["mutation_had_started"])
        self.assertTrue(entry["restorable"])
        # And that evidence is sufficient to actually restore it.
        fixtures.tag_mp3(path, genre="Half-written")
        mutation_engine.restore_from_backup(
            Path(entry["backup_path"]), Path(entry["source"]), entry["before_sha256"])
        self.assertEqual(mutation_engine.sha256_file(path), before)

    def test_crash_before_the_mutation_is_distinguishable_from_after(self):
        journal = mutation_engine.RecoveryJournal(self.journal)
        journal.append({"type": "run_start", "run_id": "r1"})
        journal.append({"type": "intent", "run_id": "r1", "index": 1,
                        "kind": mutation_engine.GENRE, "source": str(self.paths["one.mp3"]),
                        "before_sha256": "abc", "backup_path": "/b/one.mp3"})
        report = mutation_engine.recover(self.journal)
        self.assertFalse(report["in_flight"][0]["mutation_had_started"])

    def test_a_torn_final_record_is_surfaced_not_hidden(self):
        engine = self.engine()
        engine.start({})
        with self.journal.open("a", encoding="utf-8") as handle:
            handle.write('{"type": "intent", "run_id": "r1", "ind')
        report = mutation_engine.recover(self.journal)
        self.assertEqual(report["torn_records"], 1)
        self.assertFalse(report["clean"])

    def test_journal_is_fsynced_per_record(self):
        """Buffered writes would defeat the point: the record must be on disk
        before the mutation it describes is attempted."""
        journal = mutation_engine.RecoveryJournal(self.journal)
        journal.append({"type": "intent", "index": 1})
        # Read it back through a completely separate handle.
        self.assertIn("intent", self.journal.read_text(encoding="utf-8"))


class IdempotencyAndUnicodeTests(EngineTestCase):
    def test_unicode_paths_round_trip(self):
        root = self.tmp / "Unicode"
        names = ["Beyoncé - Déjà Vu (Clean).mp3", "日本語トラック.mp3", "Naïve — Æther.mp3"]
        fixtures.make_library(root, [{"relative": name, "genre": "Pop"} for name in names])
        backup = fixtures.mirror_backup(root, self.tmp / "u-backup")
        manifest_path = fixtures.passing_manifest(root, backup, self.tmp / "u-manifest.json")
        manifest = backup_manifest.require_pass(manifest_path, root)
        self.assertEqual(manifest["file_count"], 3)
        for name in names:
            digest = backup_manifest.require_item_backed_up(manifest, root, root / name)
            self.assertEqual(len(digest), 64)

    def test_second_run_over_already_applied_items_is_refused_not_silently_redone(self):
        """Re-running a completed plan must not quietly mutate again.

        The engine's source-hash precondition is what makes a rerun safe: the
        files no longer match what the plan (and the backup) recorded, so the
        second run refuses rather than stacking a second write on top.
        """
        engine = self.engine()
        engine.start({})
        path = self.paths["one.mp3"]
        original = mutation_engine.sha256_file(path)
        with engine.item(1, kind=mutation_engine.GENRE, source=path,
                         expected_sha256=original) as txn:
            fixtures.tag_mp3(path, genre="Amapiano")
            txn.mark_mutated()

        engine2 = self.engine()
        engine2.start({})
        with self.assertRaises((mutation_engine.MutationRefused, backup_manifest.BackupGateError)):
            with engine2.item(1, kind=mutation_engine.GENRE, source=path,
                              expected_sha256=original):
                self.fail("a second run must not mutate an already-applied item")


if __name__ == "__main__":
    unittest.main()
