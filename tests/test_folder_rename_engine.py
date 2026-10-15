"""Whole-folder rename transactions in mutation_engine.

Fixtures are synthetic temp trees; nothing here touches the real library or
Serato.  The case-only tests run on both case-sensitive (Linux CI) and
case-insensitive (APFS) volumes: the engine takes the temp-name two-step on
both, and every assertion reads literal directory entries.
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
for path in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import backup_manifest  # noqa: E402
import mutation_engine  # noqa: E402


class FakeHeartbeat:
    def __init__(self, fail_after=None):
        self.beats = 0
        self.fail_after = fail_after

    def beat(self, force=False):
        self.beats += 1
        if self.fail_after is not None and self.beats > self.fail_after:
            raise SystemExit("REFUSING TO MUTATE: lock lost mid-run (simulated)")
        return {"session": "test", "run_id": "test-run"}


def names(directory: Path) -> set[str]:
    return set(os.listdir(directory))


class FolderTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.library = self.tmp / "Music"
        for relative, payload in {
            "R&b/CLEAN/one.mp3": b"one",
            "R&b/DIRTY/two.mp3": b"two-two",
            "R&b/loose.flac": b"loose",
            "Afrobeat/a.mp3": b"afro",
        }.items():
            target = self.library / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        self.serato = self.tmp / "_Serato_"
        (self.serato / "Subcrates").mkdir(parents=True)
        (self.serato / "database V2").write_bytes(b"db-bytes")
        (self.serato / "Subcrates" / "Set.crate").write_bytes(b"crate-bytes")
        self.backup = self.tmp / "serato-backup"
        import shutil
        shutil.copytree(self.serato, self.backup)
        self.manifest = backup_manifest.build_manifest(self.serato, self.backup)
        self.assertEqual(self.manifest["status"], "PASS", self.manifest["problems"])
        self.manifest_path = self.tmp / "manifest.json"
        backup_manifest.write_manifest(self.manifest, self.manifest_path)
        self.journal = self.tmp / "journal.jsonl"
        self.guards = [
            {"path": self.serato / "database V2"},
            {"path": self.serato / "Subcrates" / "Set.crate"},
        ]

    def engine(self, heartbeat=None, **kwargs):
        engine = mutation_engine.MutationEngine(
            self.journal, heartbeat or FakeHeartbeat(), self.manifest, self.serato, "test", **kwargs
        )
        engine.start({"test": True})
        return engine

    def inventory(self, folder: str):
        return mutation_engine.directory_inventory(self.library / folder)

    def records(self):
        return mutation_engine.RecoveryJournal(self.journal).read()

    def rename(self, engine, source: str, destination: str, **kwargs):
        expected = kwargs.pop("expected_inventory", None)
        if expected is None:
            expected = self.inventory(source)
        with engine.folder_item(
            1, self.library / source, self.library / destination,
            expected_inventory=expected,
            guard_files=kwargs.pop("guard_files", self.guards), **kwargs,
        ) as txn:
            txn.rename_dir_no_overwrite(self.library / source, self.library / destination)


class InventoryTests(FolderTestCase):
    def test_digest_ignores_the_folder_name_and_finder_litter(self):
        before = self.inventory("R&b")
        (self.library / "R&b" / ".DS_Store").write_bytes(b"finder")
        self.assertEqual(self.inventory("R&b")["sha256"], before["sha256"])
        os.rename(self.library / "R&b", self.library / "Renamed")
        self.assertEqual(self.inventory("Renamed")["sha256"], before["sha256"])
        self.assertEqual((before["file_count"], before["directory_count"]), (3, 2))

    def test_digest_changes_when_a_file_is_added_resized_or_removed(self):
        base = self.inventory("R&b")["sha256"]
        extra = self.library / "R&b" / "CLEAN" / "new.mp3"
        extra.write_bytes(b"x")
        added = self.inventory("R&b")["sha256"]
        extra.write_bytes(b"xy")
        resized = self.inventory("R&b")["sha256"]
        extra.unlink()
        os.remove(self.library / "R&b" / "loose.flac")
        removed = self.inventory("R&b")["sha256"]
        self.assertEqual(len({base, added, resized, removed}), 4)

    def test_hashing_mode_detects_same_size_content_change(self):
        target = self.library / "R&b" / "loose.flac"
        plain = mutation_engine.directory_inventory(self.library / "R&b")
        hashed = mutation_engine.directory_inventory(self.library / "R&b", hash_contents=True)
        stat = target.stat()
        target.write_bytes(b"LOOSE")  # same length
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertEqual(mutation_engine.directory_inventory(self.library / "R&b")["sha256"], plain["sha256"])
        self.assertNotEqual(
            mutation_engine.directory_inventory(self.library / "R&b", hash_contents=True)["sha256"],
            hashed["sha256"],
        )

    def test_symlinks_are_recorded_not_followed(self):
        link = self.library / "R&b" / "alias"
        link.symlink_to(self.library / "Afrobeat")
        inventory = mutation_engine.directory_inventory(self.library / "R&b")
        self.assertEqual(inventory["symlink_count"], 1)
        self.assertEqual(inventory["file_count"], 3)


class LiteralHelperTests(FolderTestCase):
    def test_literal_helpers_compare_stored_spelling(self):
        self.assertTrue(mutation_engine.literal_path_is_dir(self.library / "R&b"))
        self.assertFalse(mutation_engine.literal_path_is_dir(self.library / "R&B"))
        self.assertFalse(mutation_engine.literal_path_is_dir(self.library / "R&b" / "loose.flac"))
        self.assertTrue(mutation_engine.literal_path_components_exist(self.library / "R&b" / "CLEAN"))
        self.assertFalse(mutation_engine.literal_path_components_exist(self.library / "r&b" / "CLEAN"))
        self.assertFalse(mutation_engine.literal_path_components_exist(self.library / "Missing"))


class FolderRenameTests(FolderTestCase):
    def test_plain_rename_journals_intent_then_completion(self):
        engine = self.engine()
        self.rename(engine, "Afrobeat", "Afrobeats")
        self.assertEqual(names(self.library), {"R&b", "Afrobeats"})
        types = [record["type"] for record in self.records()]
        self.assertEqual(
            types,
            ["run_start", "intent", "rename_step_intent", "mutated", "rename_step_completed", "completed"],
        )
        intent = self.records()[1]
        self.assertEqual(intent["kind"], mutation_engine.FOLDER_RENAME)
        self.assertEqual(len(intent["guard_backups"]), 2)
        self.assertTrue(all(Path(g["backup_path"]).is_file() for g in intent["guard_backups"]))
        self.assertTrue(mutation_engine.recover(self.journal)["clean"])
        self.assertGreaterEqual(engine.heartbeat.beats, 2)

    def test_case_only_rename_uses_a_temporary_name_and_lands_on_the_literal_spelling(self):
        before = self.inventory("R&b")
        engine = self.engine()
        self.rename(engine, "R&b", "R&B")
        self.assertEqual(names(self.library), {"R&B", "Afrobeat"})  # literal, no temp left behind
        self.assertEqual(mutation_engine.directory_inventory(self.library / "R&B")["sha256"], before["sha256"])
        purposes = [r["purpose"] for r in self.records() if r["type"] == "rename_step_completed"]
        self.assertEqual(purposes, ["folder_rename:case-to-temp", "folder_rename:temp-to-case"])
        case_intent = next(r for r in self.records() if r["type"] == "case_rename_intent")
        self.assertTrue(Path(case_intent["temporary"]).name.startswith(".R&b.mutation-"))
        self.assertTrue(mutation_engine.recover(self.journal)["clean"])

    def test_every_physical_step_is_heartbeat_checked(self):
        heartbeat = FakeHeartbeat()
        engine = self.engine(heartbeat)
        self.rename(engine, "R&b", "R&B")
        # item start + one beat per physical rename step
        self.assertGreaterEqual(heartbeat.beats, 3)

    def test_second_step_failure_restores_the_original_spelling(self):
        engine = self.engine()
        real_rename = mutation_engine.rename_noreplace
        calls = []

        def flaky(source, destination):
            calls.append((os.fspath(source), os.fspath(destination)))
            if len(calls) == 2:  # the temp -> final step
                raise OSError("simulated rename failure")
            return real_rename(source, destination)

        with mock.patch.object(mutation_engine, "rename_noreplace", side_effect=flaky):
            with self.assertRaises(OSError):
                self.rename(engine, "R&b", "R&B")
        self.assertEqual(names(self.library), {"R&b", "Afrobeat"})
        rolled = [r for r in self.records() if r["type"] == "rolled_back"]
        self.assertTrue(rolled and rolled[-1]["restored"])
        self.assertTrue(mutation_engine.recover(self.journal)["clean"])

    def test_post_rename_content_mismatch_renames_back(self):
        engine = self.engine()
        expected = self.inventory("R&b")
        real_inventory = mutation_engine.directory_inventory
        seen = []

        def lying_inventory(root, **kwargs):
            seen.append(Path(root).name)
            result = real_inventory(root, **kwargs)
            if len(seen) >= 2:  # call 1 is the precondition, call 2 the post-rename check
                result = {**result, "sha256": "0" * 64}
            return result

        with mock.patch.object(mutation_engine, "directory_inventory", side_effect=lying_inventory):
            with self.assertRaisesRegex(mutation_engine.MutationVerificationError, "differ after rename"):
                self.rename(engine, "R&b", "R&B", expected_inventory=expected)
        self.assertEqual(names(self.library), {"R&b", "Afrobeat"})
        self.assertTrue(mutation_engine.recover(self.journal)["clean"])

    def test_body_that_does_not_rename_is_a_failure_not_a_pass(self):
        engine = self.engine()
        with self.assertRaisesRegex(mutation_engine.MutationVerificationError, "did not happen"):
            with engine.folder_item(
                1, self.library / "R&b", self.library / "R&B",
                expected_inventory=self.inventory("R&b"), guard_files=self.guards,
            ):
                pass
        self.assertEqual(names(self.library), {"R&b", "Afrobeat"})


class AtomicNoReplaceTests(FolderTestCase):
    """The destination check and the rename are one kernel call, not check-then-rename."""

    def appear_before_rename(self, name, content=None):
        real = mutation_engine.rename_noreplace
        created = self.library / name

        def racing(source, destination):
            if Path(destination).name == name:
                created.mkdir()  # another process creates the name in the window
                if content is not None:
                    (created / "theirs.mp3").write_bytes(content)
            return real(source, destination)

        return mock.patch.object(mutation_engine, "rename_noreplace", side_effect=racing)

    def test_an_empty_destination_dir_appearing_before_the_rename_is_not_replaced(self):
        # os.rename would silently replace an empty directory; the kernel must refuse.
        engine = self.engine()
        before = self.inventory("Afrobeat")
        with self.appear_before_rename("Afrobeats"):
            with self.assertRaisesRegex(mutation_engine.MutationRefused, "destination already exists"):
                self.rename(engine, "Afrobeat", "Afrobeats")
        self.assertEqual(names(self.library), {"R&b", "Afrobeat", "Afrobeats"})
        self.assertEqual(os.listdir(self.library / "Afrobeats"), [])  # theirs, still there
        self.assertEqual(self.inventory("Afrobeat")["sha256"], before["sha256"])
        (item,) = mutation_engine.recover(self.journal)["in_flight"]
        self.assertEqual(item["state"], "ABORTED_BEFORE_MUTATION")  # visible, but nothing to restore

    def test_a_case_only_destination_appearing_between_the_steps_loses_nothing(self):
        engine = self.engine()
        before = self.inventory("R&b")
        with self.appear_before_rename("R&B", content=b"theirs"):
            with self.assertRaises(mutation_engine.MutationRefused):
                self.rename(engine, "R&b", "R&B")
        # Their folder is untouched and ours is intact (stranded under its temp name,
        # because the original name is now occupied) -- recoverable, nothing lost.
        self.assertEqual((self.library / "R&B" / "theirs.mp3").read_bytes(), b"theirs")
        stranded = [n for n in names(self.library) if n.startswith(".R&b.mutation-")]
        if stranded:
            self.assertEqual(
                mutation_engine.directory_inventory(self.library / stranded[0])["sha256"], before["sha256"]
            )
        else:  # case-sensitive volume: original name restored
            self.assertEqual(self.inventory("R&b")["sha256"], before["sha256"])

    def test_primitive_refuses_any_existing_destination_and_allows_a_free_name(self):
        (self.library / "Taken").mkdir()
        with self.assertRaises(mutation_engine.MutationRefused):
            mutation_engine.rename_noreplace(self.library / "Afrobeat", self.library / "Taken")
        (self.library / "TakenFile").write_bytes(b"x")
        with self.assertRaises(mutation_engine.MutationRefused):
            mutation_engine.rename_noreplace(self.library / "Afrobeat", self.library / "TakenFile")
        mutation_engine.rename_noreplace(self.library / "Afrobeat", self.library / "Free")
        self.assertIn("Free", names(self.library))
        self.assertNotIn("Afrobeat", names(self.library))

    def test_it_fails_closed_when_no_atomic_primitive_exists(self):
        engine = self.engine()
        with mock.patch.object(mutation_engine.sys, "platform", "win32"):
            with self.assertRaisesRegex(mutation_engine.MutationRefused, "no atomic no-replace"):
                self.rename(engine, "Afrobeat", "Afrobeats")
        self.assertEqual(names(self.library), {"R&b", "Afrobeat"})

    def test_an_unsupporting_filesystem_fails_closed(self):
        import ctypes
        import errno

        engine = self.engine()
        with mock.patch.object(ctypes, "get_errno", return_value=errno.ENOTSUP), \
                mock.patch.object(ctypes, "CDLL") as cdll:
            cdll.return_value.renamex_np.return_value = -1
            cdll.return_value.renameat2.return_value = -1
            with self.assertRaisesRegex(mutation_engine.MutationRefused, "does not support"):
                self.rename(engine, "Afrobeat", "Afrobeats")
        self.assertEqual(names(self.library), {"R&b", "Afrobeat"})


class FolderRenameRefusalTests(FolderTestCase):
    def assertRefused(self, pattern, source="R&b", destination="R&B", **kwargs):
        engine = kwargs.pop("engine", None) or self.engine()
        before = names(self.library)
        with self.assertRaisesRegex(mutation_engine.MutationRefused, pattern):
            self.rename(engine, source, destination, **kwargs)
        self.assertEqual(names(self.library), before)
        return engine

    def test_destination_that_exists_is_never_overwritten_or_merged(self):
        (self.library / "Afrobeats").mkdir()
        self.assertRefused("destination already exists", "Afrobeat", "Afrobeats")
        self.assertEqual((self.library / "Afrobeat" / "a.mp3").read_bytes(), b"afro")

    def test_missing_source_and_wrong_spelling_are_refused(self):
        self.assertRefused("missing rename source", "Nope", "Nope2", expected_inventory={"sha256": "x"})
        # 'r&b' resolves on APFS but is not the literal entry.
        self.assertRefused("missing rename source", "r&b", "R&B2", expected_inventory={"sha256": "x"})

    def test_rename_must_stay_in_one_parent(self):
        (self.library / "Sub").mkdir()
        self.assertRefused("one parent", "R&b", "Sub/R&b", expected_inventory={"sha256": "x"})

    def test_symlinked_source_is_refused(self):
        (self.library / "Link").symlink_to(self.library / "Afrobeat")
        self.assertRefused("missing rename source", "Link", "Link2", expected_inventory={"sha256": "x"})

    def test_content_drift_since_preview_is_refused(self):
        expected = self.inventory("R&b")
        (self.library / "R&b" / "CLEAN" / "late-arrival.mp3").write_bytes(b"new")
        self.assertRefused("contents changed since preview", expected_inventory=expected)

    def test_missing_inventory_is_refused(self):
        self.assertRefused("expected inventory", expected_inventory={})

    def test_unbacked_rename_is_refused(self):
        self.assertRefused("verified backup evidence", guard_files=[])

    def test_guard_file_changed_after_backup_is_refused(self):
        (self.serato / "database V2").write_bytes(b"db-bytes-AFTER-BACKUP")
        self.assertRefused("changed after the backup was taken")

    def test_guard_file_without_backup_evidence_is_refused(self):
        late = self.serato / "Subcrates" / "Late.crate"
        late.write_bytes(b"new crate")
        with self.assertRaisesRegex(backup_manifest.BackupGateError, "no verified backup evidence"):
            self.rename(self.engine(), "R&b", "R&B", guard_files=[*self.guards, {"path": late}])
        self.assertEqual(names(self.library), {"R&b", "Afrobeat"})

    def test_damaged_backup_copy_is_refused(self):
        (self.backup / "database V2").write_bytes(b"damaged")
        self.assertRefused("no longer matches manifest evidence")

    def test_lost_lock_stops_the_run_before_the_rename(self):
        engine = self.engine(FakeHeartbeat(fail_after=0))
        before = names(self.library)
        with self.assertRaises(SystemExit):
            self.rename(engine, "R&b", "R&B")
        self.assertEqual(names(self.library), before)

    def test_lock_lost_between_the_two_case_steps_leaves_a_detectable_recoverable_state(self):
        # Policy: no write without a verified lock -- not even the restoring one.
        # beats: item start (1), case-to-temp (2), temp-to-case (3 -> lost)
        before = self.inventory("R&b")
        engine = self.engine(FakeHeartbeat(fail_after=2))
        with self.assertRaises(SystemExit):
            self.rename(engine, "R&b", "R&B")
        stranded = [n for n in names(self.library) if n.startswith(".R&b.mutation-")]
        self.assertEqual(len(stranded), 1)
        report = mutation_engine.recover(self.journal)
        self.assertFalse(report["clean"])
        self.assertEqual(report["in_flight"][0]["case_rename_temporary_path"].rsplit("/", 1)[1], stranded[0])
        import apply_folder_rename_plan as tool
        tool.recover_folder_renames(
            journal_path=self.journal, heartbeat=FakeHeartbeat(),
            recovery_journal_path=self.tmp / "r.jsonl", result_path=self.tmp / "r.json",
            apply=True, process_lister=lambda: [],
        )
        self.assertEqual(names(self.library), {"R&b", "Afrobeat"})
        self.assertEqual(self.inventory("R&b")["sha256"], before["sha256"])

    def test_engine_must_be_started(self):
        engine = mutation_engine.MutationEngine(self.journal, FakeHeartbeat(), self.manifest, self.serato, "t")
        with self.assertRaisesRegex(mutation_engine.MutationRefused, "start"):
            self.rename(engine, "R&b", "R&B")


class CrashRecoveryTests(FolderTestCase):
    CRASH_SCRIPT = textwrap.dedent('''
        import os, sys
        from pathlib import Path
        sys.path.insert(0, {scripts!r})
        import backup_manifest, mutation_engine

        class Beat:
            def beat(self, force=False):
                return {{}}

        library, serato = Path({library!r}), Path({serato!r})
        manifest = backup_manifest.require_pass(Path({manifest!r}), serato)
        crash_after = {crash_after!r}
        original = mutation_engine._ItemTransaction._rename_step

        def dying(self, source, destination, purpose, **kwargs):
            if crash_after == "before-first-rename":
                os._exit(137)
            original(self, source, destination, purpose, **kwargs)
            if purpose.endswith(crash_after):
                os._exit(137)  # the process dies; no rollback runs

        mutation_engine._ItemTransaction._rename_step = dying
        engine = mutation_engine.MutationEngine(Path({journal!r}), Beat(), manifest, serato, "crash")
        engine.start({{}})
        inventory = mutation_engine.directory_inventory(library / "R&b")
        guards = [{{"path": serato / "database V2"}}]
        with engine.folder_item(1, library / "R&b", library / "R&B", expected_inventory=inventory,
                                guard_files=guards) as txn:
            txn.rename_dir_no_overwrite(library / "R&b", library / "R&B")
    ''')

    def crash(self, crash_after):
        script = self.CRASH_SCRIPT.format(
            scripts=str(SCRIPTS_DIR), library=str(self.library), serato=str(self.serato),
            manifest=str(self.manifest_path), journal=str(self.journal), crash_after=crash_after,
        )
        completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 137, completed.stderr)

    def test_crash_between_the_two_case_steps_is_detected_and_recoverable(self):
        before = self.inventory("R&b")
        self.crash("case-to-temp")

        # The folder is stranded under the temporary name; neither spelling exists.
        stranded = [name for name in names(self.library) if name.startswith(".R&b.mutation-")]
        self.assertEqual(len(stranded), 1)
        self.assertNotIn("R&b", names(self.library))
        self.assertNotIn("R&B", names(self.library))

        report = mutation_engine.recover(self.journal)
        self.assertFalse(report["clean"])
        (item,) = report["in_flight"]
        self.assertEqual(item["state"], "INTERRUPTED")
        self.assertTrue(item["mutation_had_started"])
        self.assertTrue(item["restorable"])
        self.assertEqual(Path(item["case_rename_temporary_path"]).name, stranded[0])
        self.assertEqual(item["before_sha256"], before["sha256"])

        import apply_folder_rename_plan as tool
        actions = tool.plan_recovery(self.journal)
        self.assertEqual([a["state"] for a in actions], ["AT_TEMPORARY"])
        self.assertEqual(tool.recover_folder_renames(
            journal_path=self.journal, heartbeat=None, recovery_journal_path=self.tmp / "r.jsonl",
            result_path=self.tmp / "r.json", apply=False)["status"], "DRY_RUN")
        self.assertIn(stranded[0], names(self.library))  # dry run changed nothing

        result = tool.recover_folder_renames(
            journal_path=self.journal, heartbeat=FakeHeartbeat(),
            recovery_journal_path=self.tmp / "r.jsonl", result_path=self.tmp / "r.json",
            apply=True, process_lister=lambda: [],
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(names(self.library), {"R&b", "Afrobeat"})
        self.assertEqual(self.inventory("R&b")["sha256"], before["sha256"])
        self.assertTrue(mutation_engine.recover(self.journal)["clean"])
        self.assertTrue(mutation_engine.recover(self.tmp / "r.jsonl")["clean"])

    def test_crash_after_the_final_step_but_before_completion_rolls_back(self):
        before = self.inventory("R&b")
        self.crash("temp-to-case")
        self.assertEqual(names(self.library), {"R&B", "Afrobeat"})
        import apply_folder_rename_plan as tool
        (action,) = tool.plan_recovery(self.journal)
        self.assertEqual(action["state"], "AT_DESTINATION")
        tool.recover_folder_renames(
            journal_path=self.journal, heartbeat=FakeHeartbeat(),
            recovery_journal_path=self.tmp / "r.jsonl", result_path=self.tmp / "r.json",
            apply=True, process_lister=lambda: [],
        )
        self.assertEqual(names(self.library), {"R&b", "Afrobeat"})
        self.assertEqual(self.inventory("R&b")["sha256"], before["sha256"])
        self.assertTrue(mutation_engine.recover(self.journal)["clean"])

    def test_crash_before_any_rename_needs_no_restore(self):
        self.crash("before-first-rename")
        self.assertEqual(names(self.library), {"R&b", "Afrobeat"})
        import apply_folder_rename_plan as tool
        (action,) = tool.plan_recovery(self.journal)
        self.assertEqual(action["state"], "AT_SOURCE")
        result = tool.recover_folder_renames(
            journal_path=self.journal, heartbeat=FakeHeartbeat(),
            recovery_journal_path=self.tmp / "r.jsonl", result_path=self.tmp / "r.json",
            apply=True, process_lister=lambda: [],
        )
        self.assertEqual(result["restored"], [])
        self.assertTrue(mutation_engine.recover(self.journal)["clean"])

    def test_recovery_refuses_to_guess_when_the_folder_is_nowhere(self):
        self.crash("case-to-temp")
        stranded = next(n for n in names(self.library) if n.startswith(".R&b.mutation-"))
        os.rename(self.library / stranded, self.library / "Somewhere Else")
        import apply_folder_rename_plan as tool
        with self.assertRaisesRegex(tool.FolderRenameError, "needs a human"):
            tool.recover_folder_renames(
                journal_path=self.journal, heartbeat=FakeHeartbeat(),
                recovery_journal_path=self.tmp / "r.jsonl", result_path=self.tmp / "r.json",
                apply=True, process_lister=lambda: [],
            )


if __name__ == "__main__":
    unittest.main()
