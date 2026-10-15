"""SQLite (master.sqlite / root.sqlite) path rewrites. Synthetic temp fixtures only."""

import hashlib
import json
import os
import shutil
import sqlite3
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
import rewrite_serato_paths as serato_rewrite  # noqa: E402
import rewrite_serato_sqlite_paths as sqlite_rewrite  # noqa: E402
import serato_sqlite_fixtures as fx  # noqa: E402

NO_SERATO = lambda: ["/bin/zsh", "/usr/bin/python3"]  # noqa: E731
RUNNING_SERATO = lambda: ["/Applications/Serato DJ Pro.app/Contents/MacOS/Serato DJ Pro"]  # noqa: E731


class FakeHeartbeat:
    def __init__(self):
        self.beats = 0

    def beat(self, force=False):
        self.beats += 1
        return {}


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stored(path: Path) -> str:
    return str(path).lstrip("/")


def rename_directory(source: Path, destination: Path) -> None:
    """Stand-in for the (separately tested) folder rename, via a temp name."""
    temporary = source.with_name(source.name + ".swap")
    os.rename(source, temporary)
    os.rename(temporary, destination)


class SqliteFixtureCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.music = self.tmp / "Music"
        self.tracks = {}
        for relative in (
            "R&b/CLEAN/one.mp3",
            "R&b/DIRTY/two.mp3",
            "R&b/loose.mp3",
            "Afrobeat/a.mp3",
            "Hip Hop/keep.mp3",
            "White OOtz/REMIXES & EDITS/R&B/nested.mp3",
        ):
            target = self.music / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(relative.encode())
            self.tracks[relative] = target
        self.library = self.tmp / "Library"
        self.library.mkdir()
        assets = [stored(self.tracks[key]) for key in self.tracks]
        history = [stored(self.tracks["R&b/CLEAN/one.mp3"]), stored(self.tracks["Hip Hop/keep.mp3"])]
        self.master = fx.make_master_sqlite(self.library / "master.sqlite", assets, history)
        self.root_db = fx.make_root_sqlite(self.library / "root.sqlite", assets)
        self.rules = serato_rewrite.normalize_rename_map([
            {"from": stored(self.music / "R&b"), "to": stored(self.music / "R&B")},
            {"from": stored(self.music / "Afrobeat"), "to": stored(self.music / "Afrobeats")},
        ])

    def rows(self, database: Path, table="asset"):
        connection = sqlite3.connect(f"file:{database}?immutable=1", uri=True)
        try:
            return connection.execute(f"SELECT rowid, portable_id FROM {table} ORDER BY rowid").fetchall()
        finally:
            connection.close()

    def make_manifest(self, databases=None):
        databases = databases or [self.master, self.root_db]
        backup = self.tmp / "sqlite-backup"
        backup.mkdir(exist_ok=True)
        for database in databases:
            shutil.copy2(database, backup / database.name)
        manifest = backup_manifest.build_scoped_manifest(
            self.library, backup, [database.name for database in databases]
        )
        self.assertEqual(manifest["status"], "PASS", manifest["problems"])
        return manifest, backup

    def move_folders(self):
        rename_directory(self.music / "R&b", self.music / "R&B")
        os.rename(self.music / "Afrobeat", self.music / "Afrobeats")

    def apply(self, plan, manifest, *, journal=None, process_lister=NO_SERATO, heartbeat=None):
        return sqlite_rewrite.apply_sqlite_plan(
            plan=plan, databases=[self.master, self.root_db], manifest=manifest,
            heartbeat=heartbeat or FakeHeartbeat(), journal_path=journal or self.tmp / "journal.jsonl",
            result_path=self.tmp / "result.json", process_lister=process_lister,
        )

    def write_plan(self, **kwargs):
        plan = sqlite_rewrite.build_preview([self.master, self.root_db], self.rules, **kwargs)
        path = self.tmp / "plan.json"
        serato_rewrite.write_json(path, plan)
        return plan, path


class PreviewTests(SqliteFixtureCase):
    def test_preview_rewrites_only_exact_case_prefix_rows(self):
        plan = sqlite_rewrite.build_preview([self.master, self.root_db], self.rules)
        master = next(entry for entry in plan["databases"] if entry["name"] == "master.sqlite")
        self.assertEqual(master["rows_changed"], 4)  # 3 R&b + 1 Afrobeat
        changed = {row["from"] for row in master["rows"]}
        self.assertNotIn(stored(self.tracks["White OOtz/REMIXES & EDITS/R&B/nested.mp3"]), changed)
        self.assertNotIn(stored(self.tracks["Hip Hop/keep.mp3"]), changed)
        self.assertEqual(plan["summary"]["rows_changed"], 8)
        self.assertEqual(master["history_entry_references_left_unchanged"], 1)
        self.assertTrue(all(row["source_present"] for row in master["rows"]))
        self.assertEqual(plan["summary"]["unused_rule_source_indexes"], [])

    def test_preview_is_read_only_and_creates_no_sidecars(self):
        before = {path.name: (sha(path), path.stat().st_mtime_ns) for path in self.library.iterdir()}
        sqlite_rewrite.build_preview([self.master, self.root_db], self.rules)
        after = {path.name: (sha(path), path.stat().st_mtime_ns) for path in self.library.iterdir()}
        self.assertEqual(before, after)
        self.assertEqual(sorted(after), ["master.sqlite", "root.sqlite"])

    def test_root_style_schema_is_supported(self):
        plan = sqlite_rewrite.build_preview([self.root_db], self.rules)
        entry = plan["databases"][0]
        self.assertEqual(entry["schema"]["unique_indexes"][0]["keys"], [["portable_id", "NOCASE"]])
        self.assertEqual(entry["rows_changed"], 4)
        self.assertEqual(entry["history_entry_references_left_unchanged"], 0)

    def test_uncheckpointed_wal_is_refused(self):
        connection = sqlite3.connect(str(self.master), isolation_level=None)
        self.addCleanup(connection.close)
        connection.execute("PRAGMA wal_autocheckpoint = 0")
        connection.execute("INSERT INTO location (id) VALUES (99)")
        self.assertTrue(Path(f"{self.master}-wal").stat().st_size > 0)
        with self.assertRaisesRegex(sqlite_rewrite.SeratoSqliteError, "un-checkpointed WAL"):
            sqlite_rewrite.build_preview([self.master], self.rules)

    def test_destination_collision_is_refused_before_any_write(self):
        occupant = stored(self.music / "Afrobeats" / "a.mp3")
        connection = sqlite3.connect(str(self.master), isolation_level=None)
        connection.execute(
            "INSERT INTO asset (external_id, location_id, portable_id) VALUES (500, 1, ?)", (occupant,)
        )
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.close()
        with self.assertRaisesRegex(sqlite_rewrite.SeratoSqliteError, "unique index"):
            sqlite_rewrite.build_preview([self.master], self.rules)

    def test_ascii_case_collision_uses_sqlite_nocase_semantics(self):
        occupant = stored(self.music / "afrobeats" / "A.MP3")
        connection = sqlite3.connect(str(self.master), isolation_level=None)
        connection.execute(
            "INSERT INTO asset (external_id, location_id, portable_id) VALUES (500, 1, ?)", (occupant,)
        )
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.close()
        with self.assertRaisesRegex(sqlite_rewrite.SeratoSqliteError, "unique index"):
            sqlite_rewrite.build_preview([self.master], self.rules)

    def test_preview_built_after_the_rename_is_refused(self):
        self.move_folders()
        with self.assertRaisesRegex(sqlite_rewrite.SeratoSqliteError, "BEFORE renaming"):
            sqlite_rewrite.build_preview([self.master], self.rules)

    def test_stale_rows_are_flagged_not_hidden(self):
        os.remove(self.tracks["R&b/loose.mp3"])
        plan = sqlite_rewrite.build_preview([self.master], self.rules)
        self.assertEqual(plan["databases"][0]["rows_already_missing"], 1)

    def test_databases_must_share_one_directory(self):
        other = self.tmp / "elsewhere"
        other.mkdir()
        stray = fx.make_root_sqlite(other / "root.sqlite", [])
        with self.assertRaisesRegex(sqlite_rewrite.SeratoSqliteError, "one directory"):
            sqlite_rewrite.build_preview([self.master, stray], self.rules)


class ApplyTests(SqliteFixtureCase):
    def test_apply_rewrites_only_asset_portable_id_and_verifies(self):
        plan, plan_path = self.write_plan()
        manifest, backup = self.make_manifest()
        originals = {db.name: sha(db) for db in (self.master, self.root_db)}
        history_before = self.rows(self.master, "history_entry")
        self.move_folders()

        validated = sqlite_rewrite.load_and_validate_plan(
            plan_path, [self.master, self.root_db], require_targets=True
        )
        journal = self.tmp / "journal.jsonl"
        result = self.apply(validated, manifest, journal=journal)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["rows_rewritten"], 8)
        for database in (self.master, self.root_db):
            paths = [portable for _, portable in self.rows(database)]
            self.assertIn(stored(self.music / "R&B" / "CLEAN" / "one.mp3"), paths)
            self.assertIn(stored(self.music / "Afrobeats" / "a.mp3"), paths)
            self.assertIn(stored(self.music / "Hip Hop" / "keep.mp3"), paths)
            # the nested folder that merely contains a segment spelled R&B is not a match
            self.assertIn(stored(self.tracks["White OOtz/REMIXES & EDITS/R&B/nested.mp3"]), paths)
            self.assertFalse([p for p in paths if "/R&b/" in p or "/Afrobeat/" in p])
        self.assertEqual(self.rows(self.master, "history_entry"), history_before)
        self.assertEqual(sorted(path.name for path in self.library.iterdir()), ["master.sqlite", "root.sqlite"])
        for name, digest in originals.items():
            self.assertEqual(sha(backup / name), digest)  # backups untouched
            self.assertNotEqual(sha(self.library / name), digest)
        self.assertTrue(mutation_engine.recover(journal)["clean"])
        connection = sqlite3.connect(f"file:{self.master}?immutable=1", uri=True)
        self.assertEqual(connection.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
        connection.close()

    def test_wal_mode_is_preserved(self):
        plan, _ = self.write_plan()
        manifest, _ = self.make_manifest()
        self.move_folders()
        self.apply(plan, manifest)
        # immutable handles always report "delete"; the persistent mode is in the header.
        self.assertEqual(sqlite_rewrite.journal_mode_header(self.master), "0202")
        self.assertEqual(sqlite_rewrite.journal_mode_header(self.root_db), "0101")

    def test_apply_before_the_folders_are_renamed_is_refused(self):
        _, plan_path = self.write_plan()
        with self.assertRaisesRegex(sqlite_rewrite.SeratoSqliteError, "do not exist|not spelled exactly"):
            sqlite_rewrite.load_and_validate_plan(
                plan_path, [self.master, self.root_db], require_targets=True
            )

    def test_plain_case_alias_does_not_satisfy_the_target_check(self):
        """APFS answers True for `R&B` while the folder is still `R&b`; that must not pass."""
        probe = self.tmp / "CaseProbe"
        probe.mkdir()
        if not (self.tmp / "caseprobe").exists():
            self.skipTest("case-sensitive volume: the alias cannot exist")
        # Only Afrobeat is really renamed; R&b -> R&B is NOT done.
        os.rename(self.music / "Afrobeat", self.music / "Afrobeats")
        _, plan_path = self.write_plan(require_live_rules=False)
        self.assertTrue((self.music / "R&B" / "CLEAN" / "one.mp3").is_file())  # alias resolves
        with self.assertRaisesRegex(sqlite_rewrite.SeratoSqliteError, "do not exist|not spelled exactly"):
            sqlite_rewrite.load_and_validate_plan(
                plan_path, [self.master, self.root_db], require_targets=True
            )

    def test_rows_already_missing_before_the_rename_are_tolerated_and_reported(self):
        os.remove(self.tracks["R&b/loose.mp3"])
        plan, plan_path = self.write_plan()
        manifest, _ = self.make_manifest()
        self.move_folders()
        validated = sqlite_rewrite.load_and_validate_plan(
            plan_path, [self.master, self.root_db], require_targets=True
        )
        result = self.apply(validated, manifest)
        self.assertEqual(result["completed"][0]["rows_already_missing_before"], 1)
        paths = [portable for _, portable in self.rows(self.master)]
        self.assertIn(stored(self.music / "R&B" / "loose.mp3"), paths)

    def test_a_file_that_existed_but_is_missing_after_the_rename_blocks_apply(self):
        _, plan_path = self.write_plan()
        self.move_folders()
        os.remove(self.music / "R&B" / "DIRTY" / "two.mp3")
        with self.assertRaisesRegex(sqlite_rewrite.SeratoSqliteError, "do not exist"):
            sqlite_rewrite.load_and_validate_plan(
                plan_path, [self.master, self.root_db], require_targets=True
            )

    def test_plan_is_bound_to_database_bytes_and_every_other_cell(self):
        _, plan_path = self.write_plan()
        connection = sqlite3.connect(str(self.master), isolation_level=None)
        connection.execute("UPDATE asset SET external_id = external_id + 1000 WHERE rowid = 1")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.close()
        with self.assertRaisesRegex(sqlite_rewrite.SeratoSqliteError, "no longer matches"):
            sqlite_rewrite.load_and_validate_plan(plan_path, [self.master, self.root_db])

    def test_edited_plan_rows_are_refused(self):
        plan, plan_path = self.write_plan()
        plan["databases"][0]["rows"][0]["to"] = "Users/evil/elsewhere.mp3"
        serato_rewrite.write_json(plan_path, plan)
        with self.assertRaisesRegex(sqlite_rewrite.SeratoSqliteError, "no longer matches"):
            sqlite_rewrite.load_and_validate_plan(plan_path, [self.master, self.root_db])

    def test_running_serato_fails_closed_without_touching_anything(self):
        plan, _ = self.write_plan()
        manifest, _ = self.make_manifest()
        self.move_folders()
        before = {db.name: sha(db) for db in (self.master, self.root_db)}
        with self.assertRaisesRegex(serato_rewrite.SeratoRewriteError, "Serato is running"):
            self.apply(plan, manifest, process_lister=RUNNING_SERATO)
        self.assertEqual(before, {db.name: sha(db) for db in (self.master, self.root_db)})
        self.assertFalse((self.tmp / "journal.jsonl").exists())

    def test_unreadable_process_list_fails_closed(self):
        def broken():
            raise serato_rewrite.SeratoRewriteError("cannot verify that Serato is closed")

        plan, _ = self.write_plan()
        manifest, _ = self.make_manifest()
        with self.assertRaisesRegex(serato_rewrite.SeratoRewriteError, "cannot verify"):
            self.apply(plan, manifest, process_lister=broken)

    def test_backup_must_cover_every_database(self):
        plan, _ = self.write_plan()
        manifest, _ = self.make_manifest(databases=[self.master])  # root.sqlite not backed up
        self.move_folders()
        before = sha(self.root_db)
        with self.assertRaises(backup_manifest.BackupGateError):
            self.apply(plan, manifest)
        self.assertEqual(sha(self.root_db), before)

    def test_stale_backup_is_refused(self):
        plan, _ = self.write_plan()
        manifest, _ = self.make_manifest()
        connection = sqlite3.connect(str(self.root_db), isolation_level=None)
        connection.execute("UPDATE asset SET genre = 'changed after backup' WHERE id = 1")
        connection.close()
        self.move_folders()
        with self.assertRaises(mutation_engine.MutationRefused):
            self.apply(plan, manifest)

    def test_verification_failure_restores_the_verified_backup(self):
        plan, _ = self.write_plan()
        manifest, _ = self.make_manifest()
        self.move_folders()
        original = sha(self.master)
        journal = self.tmp / "journal.jsonl"
        with mock.patch.object(
            sqlite_rewrite, "verify_database",
            side_effect=mutation_engine.MutationVerificationError("simulated verification failure"),
        ):
            with self.assertRaises(mutation_engine.MutationVerificationError):
                self.apply(plan, manifest, journal=journal)
        self.assertEqual(sha(self.master), original)
        self.assertEqual(sorted(p.name for p in self.library.iterdir()), ["master.sqlite", "root.sqlite"])
        report = mutation_engine.recover(journal)
        self.assertTrue(report["clean"], report)
        rolled = [r for r in mutation_engine.RecoveryJournal(journal).read() if r["type"] == "rolled_back"]
        self.assertTrue(rolled[-1]["restored"])

    def test_rollback_discards_sidecars_instead_of_replaying_them_over_the_restore(self):
        plan, _ = self.write_plan()
        manifest, _ = self.make_manifest()
        self.move_folders()
        original = sha(self.master)
        real_update = sqlite_rewrite._apply_sqlite_update

        def update_then_leave_a_wal(txn, database, entry):
            real_update(txn, database, entry)
            Path(f"{database}-wal").write_bytes(b"junk that would be replayed")
            Path(f"{database}-shm").write_bytes(b"junk")

        with mock.patch.object(sqlite_rewrite, "_apply_sqlite_update", side_effect=update_then_leave_a_wal):
            with self.assertRaisesRegex(mutation_engine.MutationVerificationError, "WAL not checkpointed"):
                self.apply(plan, manifest)
        self.assertEqual(sha(self.master), original)
        leftovers = sorted(p.name for p in self.library.iterdir())
        # Moved aside, not deleted; nothing named like a live sidecar remains.
        self.assertNotIn("master.sqlite-wal", leftovers)
        self.assertNotIn("master.sqlite-shm", leftovers)
        self.assertEqual(sum(1 for name in leftovers if ".discarded-" in name), 2)
        self.assertEqual(
            sorted(p.name for p in self.library.iterdir() if ".discarded-" not in p.name),
            ["master.sqlite", "root.sqlite"],
        )
        connection = sqlite3.connect(str(self.master))
        self.assertEqual(connection.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
        connection.close()

    def test_incomplete_wal_checkpoint_is_a_failure_and_restores_the_backup(self):
        plan, _ = self.write_plan()
        manifest, _ = self.make_manifest()
        self.move_folders()
        original = sha(self.master)
        reader = sqlite3.connect(str(self.master), isolation_level=None)
        self.addCleanup(reader.close)
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM asset").fetchone()  # holds a WAL read snapshot
        with self.assertRaisesRegex(mutation_engine.MutationVerificationError, "checkpoint did not complete"):
            self.apply(plan, manifest)
        # Checked while the (unrealistic: Serato is closed) reader is still attached;
        # closing it afterwards would let SQLite write its orphaned handles back.
        self.assertEqual(sha(self.master), original)
        reader.close()

    def test_rollback_restores_from_the_exact_backup_path_the_engine_verified(self):
        plan, _ = self.write_plan()
        manifest, backup = self.make_manifest()
        self.move_folders()
        used = []
        real_restore = mutation_engine.restore_from_backup

        def spy(backup_path, target, expected):
            used.append(Path(backup_path))
            return real_restore(backup_path, target, expected)

        with mock.patch.object(sqlite_rewrite, "verify_database",
                               side_effect=mutation_engine.MutationVerificationError("boom")), \
                mock.patch.object(mutation_engine, "restore_from_backup", side_effect=spy):
            with self.assertRaises(mutation_engine.MutationVerificationError):
                self.apply(plan, manifest)
        journal_paths = [Path(r["backup_path"]) for r in mutation_engine.RecoveryJournal(
            self.tmp / "journal.jsonl").read() if r["type"] == "intent"]
        self.assertEqual(used, journal_paths[:1])

    def test_a_writer_holding_the_database_blocks_apply_without_corruption(self):
        plan, _ = self.write_plan()
        manifest, _ = self.make_manifest()
        self.move_folders()
        original = sha(self.master)
        other = sqlite3.connect(str(self.master), isolation_level=None)
        self.addCleanup(other.close)
        other.execute("BEGIN IMMEDIATE")
        with self.assertRaises(sqlite3.OperationalError):
            self.apply(plan, manifest)
        other.execute("ROLLBACK")
        other.close()
        self.assertEqual(sha(self.master), original)

    def test_a_row_that_changed_under_the_plan_aborts_the_transaction(self):
        plan, _ = self.write_plan()
        manifest, _ = self.make_manifest()
        self.move_folders()
        plan["databases"][0]["rows"][0]["from"] = "Users/x/not-what-is-stored.mp3"
        original = sha(self.master)
        with self.assertRaises(mutation_engine.MutationVerificationError):
            self.apply(plan, manifest)
        self.assertEqual(sha(self.master), original)

    def test_second_apply_is_refused_not_repeated(self):
        plan, plan_path = self.write_plan()
        manifest, _ = self.make_manifest()
        self.move_folders()
        self.apply(plan, manifest)
        with self.assertRaises(sqlite_rewrite.SeratoSqliteError):
            sqlite_rewrite.load_and_validate_plan(plan_path, [self.master, self.root_db], require_targets=True)

    def test_crash_after_commit_is_journaled_and_restorable_from_backup(self):
        plan, plan_path = self.write_plan()
        manifest, backup = self.make_manifest()
        manifest_path = self.tmp / "manifest.json"
        backup_manifest.write_manifest(manifest, manifest_path)
        self.move_folders()
        original = {db.name: sha(db) for db in (self.master, self.root_db)}
        journal = self.tmp / "crash-journal.jsonl"
        script = textwrap.dedent(f'''
            import os, sys
            from pathlib import Path
            sys.path.insert(0, {str(SCRIPTS_DIR)!r})
            import backup_manifest, rewrite_serato_sqlite_paths as r

            class Beat:
                def beat(self, force=False):
                    return {{}}

            library = Path({str(self.library)!r})
            dbs = [library / "master.sqlite", library / "root.sqlite"]
            manifest = backup_manifest.require_pass(Path({str(manifest_path)!r}), library)
            plan = r.load_and_validate_plan(Path({str(plan_path)!r}), dbs, require_targets=True)
            r.verify_database = lambda *a, **k: os._exit(137)   # die right after the commit
            r.apply_sqlite_plan(plan=plan, databases=dbs, manifest=manifest, heartbeat=Beat(),
                                journal_path=Path({str(journal)!r}), result_path=Path({str(self.tmp / "x.json")!r}),
                                process_lister=lambda: [])
        ''')
        completed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 137, completed.stderr)

        self.assertNotEqual(sha(self.master), original["master.sqlite"])  # the commit landed
        report = mutation_engine.recover(journal)
        self.assertFalse(report["clean"])
        (item,) = report["in_flight"]
        self.assertEqual(item["state"], "INTERRUPTED")
        self.assertTrue(item["mutation_had_started"])
        self.assertEqual(item["before_sha256"], original["master.sqlite"])
        self.assertEqual(Path(item["backup_path"]), (backup / "master.sqlite").resolve())
        # The documented recovery: restore from the recorded, hash-checked backup.
        mutation_engine.restore_from_backup(Path(item["backup_path"]), self.master, item["before_sha256"])
        self.assertEqual(sha(self.master), original["master.sqlite"])


class CliTests(SqliteFixtureCase):
    def run_main(self, *argv):
        import io
        import contextlib
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["rewrite_serato_sqlite_paths.py", *argv]):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = sqlite_rewrite.main()
        return code, out.getvalue(), err.getvalue()

    def test_preview_command_writes_a_plan(self):
        rename_map = self.tmp / "map.json"
        rename_map.write_text(json.dumps({
            stored(self.music / "R&b"): stored(self.music / "R&B"),
        }), encoding="utf-8")
        out = self.tmp / "plan.json"
        code, stdout, _ = self.run_main(
            "preview", "--sqlite-db", str(self.master), "--sqlite-db", str(self.root_db),
            "--rename-map", str(rename_map), "--out", str(out),
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["rows_changed"], 6)
        self.assertEqual(json.loads(out.read_text())["mode"], "preview-only")

    def test_apply_requires_the_serato_closed_acknowledgement(self):
        code, _, stderr = self.run_main(
            "apply", "--sqlite-db", str(self.master), "--plan", "x", "--backup-manifest", "y",
            "--session", "s", "--journal", "j", "--out", "o",
        )
        self.assertEqual(code, 2)
        self.assertIn("--confirm-serato-closed", stderr)


if __name__ == "__main__":
    unittest.main()
