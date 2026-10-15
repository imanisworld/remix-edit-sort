"""apply_folder_rename_plan.py and the full rename -> Serato rewrite pipeline.

Synthetic temp fixtures only: a fake library, a fake ``_Serato_`` directory
(database V2 + crates) and fake SQLite libraries.  The final test runs the
whole approved sequence end to end and then undoes it.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = TESTS_DIR.parent / "scripts"
for path in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import apply_folder_rename_plan as tool  # noqa: E402
import backup_manifest  # noqa: E402
import check_serato_crate_paths as checker  # noqa: E402
import folder_lock  # noqa: E402
import mutation_engine  # noqa: E402
import rewrite_serato_paths as serato_rewrite  # noqa: E402
import rewrite_serato_sqlite_paths as sqlite_rewrite  # noqa: E402
import serato_sqlite_fixtures as fx  # noqa: E402

NO_SERATO = lambda: ["/bin/zsh"]  # noqa: E731
RUNNING_SERATO = lambda: ["/Applications/Serato DJ Pro.app/Contents/MacOS/Serato DJ Pro"]  # noqa: E731


class FakeHeartbeat:
    def __init__(self):
        self.beats = 0

    def beat(self, force=False):
        self.beats += 1
        return {}


def stored(path: Path) -> str:
    return str(path).lstrip("/")


def names(directory: Path) -> set[str]:
    return set(os.listdir(directory))


def acquire(folder: str, session: str) -> None:
    args = SimpleNamespace(
        folder=folder, session=session, agent="test", note=None, run_id=None,
        repo_sha="test", stale_hours=6.0, reason=None, force=False,
    )
    with contextlib.redirect_stdout(io.StringIO()):
        folder_lock.cmd_acquire(args)


class PipelineCase(unittest.TestCase):
    FOLDER_MAP = {"R&b": "R&B", "Afrobeat": "Afrobeats"}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.music = self.tmp / "Music"
        self.tracks = []
        for relative in (
            "R&b/CLEAN/one.mp3", "R&b/DIRTY/two.mp3", "R&b/loose.mp3",
            "Afrobeat/a.mp3", "Afrobeat/b.mp3", "Hip Hop/keep.mp3",
        ):
            target = self.music / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(relative.encode() * 7)
            self.tracks.append(target)
        self.serato = self.tmp / "_Serato_"
        paths = [stored(track) for track in self.tracks]
        fx.make_serato_dir(self.serato, paths, {
            "Set": paths[:3], "Afro": paths[3:5], "Other": paths[5:],
        })
        self.library_dir = self.tmp / "SeratoLibrary"
        self.library_dir.mkdir()
        self.master = fx.make_master_sqlite(self.library_dir / "master.sqlite", paths, paths[:1])
        self.root_db = fx.make_root_sqlite(self.library_dir / "root.sqlite", paths)

        self.serato_backup = self.tmp / "serato-backup"
        shutil.copytree(self.serato, self.serato_backup)
        self.serato_manifest = backup_manifest.build_manifest(self.serato, self.serato_backup)
        self.assertEqual(self.serato_manifest["status"], "PASS", self.serato_manifest["problems"])
        self.sqlite_backup = self.tmp / "sqlite-backup"
        self.sqlite_backup.mkdir()
        for database in (self.master, self.root_db):
            shutil.copy2(database, self.sqlite_backup / database.name)
        self.sqlite_manifest = backup_manifest.build_scoped_manifest(
            self.library_dir, self.sqlite_backup, ["master.sqlite", "root.sqlite"]
        )
        self.assertEqual(self.sqlite_manifest["status"], "PASS", self.sqlite_manifest["problems"])

    def entries(self):
        return tool.normalize_folder_map(self.FOLDER_MAP)

    def preview(self, **kwargs):
        return tool.build_preview(self.music, self.entries(), **kwargs)

    def write_plan(self, plan=None):
        path = self.tmp / "folder-plan.json"
        serato_rewrite.write_json(path, plan or self.preview())
        return path

    def apply_folders(self, plan=None, **kwargs):
        plan = plan or tool.load_and_validate_plan(self.write_plan(), self.music)
        kwargs.setdefault("process_lister", NO_SERATO)
        kwargs.setdefault("journal_path", self.tmp / "rename-journal.jsonl")
        return tool.apply_folder_renames(
            plan=plan, serato_dir=self.serato, manifest=self.serato_manifest,
            heartbeat=FakeHeartbeat(), result_path=self.tmp / "rename-result.json",
            rename_map_path=self.tmp / "serato-map.json", **kwargs,
        )


class FolderMapTests(unittest.TestCase):
    def test_valid_map_is_normalized_with_case_only_flag(self):
        entries = tool.normalize_folder_map({"R&b": "R&B", "Afrobeat": "Afrobeats"})
        self.assertEqual([(e["from"], e["to"], e["case_only"]) for e in entries],
                         [("R&b", "R&B", True), ("Afrobeat", "Afrobeats", False)])

    def test_unsafe_maps_are_refused(self):
        for label, payload, pattern in (
            ("absolute", {"/R&b": "R&B"}, "relative"),
            ("dotdot", {"../R&b": "../R&B"}, "safe relative"),
            ("new parent", {"A/x": "B/x"}, "same parent"),
            ("no-op", {"R&b": "R&b"}, "does not change"),
            ("chain", {"A": "B", "B": "C"}, "another rename's source"),
            ("swap", {"A": "B", "B": "A"}, "another rename's source"),
            ("same destination", {"A": "C", "B": "c"}, "share the destination"),
            ("nested", {"A": "A2", "A/B": "A/C"}, "inside rename source"),
            ("empty", {}, "no entries"),
        ):
            with self.subTest(label):
                with self.assertRaisesRegex(tool.FolderRenameError, pattern):
                    tool.normalize_folder_map(payload)


class PreviewTests(PipelineCase):
    def test_preview_describes_each_rename_and_derives_the_serato_map(self):
        plan = self.preview()
        self.assertEqual(plan["mode"], "preview-only")
        self.assertEqual(plan["summary"], {
            "folders": 2, "files": 5,
            "bytes": plan["summary"]["bytes"], "case_only_renames": 1,
        })
        by_from = {item["from"]: item for item in plan["renames"]}
        self.assertTrue(by_from["R&b"]["case_only"])
        self.assertEqual(by_from["R&b"]["inventory"]["file_count"], 3)
        self.assertEqual(by_from["R&b"]["serato_from"], stored(self.music / "R&b"))
        self.assertEqual(by_from["R&b"]["serato_to"], stored(self.music / "R&B"))
        self.assertEqual(
            {rule["from"]: rule["to"] for rule in plan["serato_rename_map"]},
            {stored(self.music / "R&b"): stored(self.music / "R&B"),
             stored(self.music / "Afrobeat"): stored(self.music / "Afrobeats")},
        )
        self.assertEqual(plan["required_lock_folders"], ["Afrobeat", "Afrobeats", "R&b", "Serato"])
        # The derived map is directly consumable by the Serato rewriters.
        self.assertEqual(serato_rewrite.normalize_rename_map(plan["serato_rename_map"]), plan["serato_rename_map"])

    def test_preview_changes_nothing_on_disk(self):
        def snapshot(root):
            return sorted(
                (str(p.relative_to(root)), p.stat().st_mtime_ns, p.stat().st_size if p.is_file() else -1)
                for p in root.rglob("*")
            )
        before = (snapshot(self.music), snapshot(self.serato), snapshot(self.library_dir))
        self.preview(serato_dir=self.serato, sqlite_dbs=[self.master, self.root_db])
        self.assertEqual(before, (snapshot(self.music), snapshot(self.serato), snapshot(self.library_dir)))

    def test_preview_reports_serato_impact_without_touching_it(self):
        plan = self.preview(serato_dir=self.serato, sqlite_dbs=[self.master, self.root_db])
        impact = plan["serato_impact"]
        self.assertEqual(impact["serato_binaries"]["database_references_changed"], 5)
        self.assertEqual(impact["serato_binaries"]["crate_references_changed"], 5)
        self.assertEqual(impact["serato_sqlite"]["rows_changed"], 10)
        self.assertEqual(impact["serato_sqlite"]["history_entry_references_left_unchanged"], 1)

    def test_wrong_spelling_is_refused_even_though_apfs_would_resolve_it(self):
        with self.assertRaisesRegex(tool.FolderRenameError, "spelled exactly|not|missing"):
            tool.build_preview(self.music, tool.normalize_folder_map({"r&b": "R&B"}))

    def test_existing_destination_is_never_a_merge(self):
        (self.music / "Afrobeats").mkdir()
        with self.assertRaisesRegex(tool.FolderRenameError, "destination already exists"):
            self.preview()

    def test_library_root_must_be_spelled_exactly(self):
        probe = self.tmp / "Probe"
        probe.mkdir()
        if not (self.tmp / "probe").exists():
            self.skipTest("case-sensitive volume")
        wrong = self.tmp / "music"
        with self.assertRaisesRegex(tool.FolderRenameError, "not spelled exactly"):
            tool.build_preview(wrong, self.entries())

    def test_tampered_or_stale_plan_is_refused(self):
        plan = self.preview()
        path = self.write_plan(plan)
        (self.music / "R&b" / "CLEAN" / "late.mp3").write_bytes(b"x")
        with self.assertRaisesRegex(tool.FolderRenameError, "no longer matches"):
            tool.load_and_validate_plan(path, self.music)
        (self.music / "R&b" / "CLEAN" / "late.mp3").unlink()
        tool.load_and_validate_plan(path, self.music)
        plan["renames"][0]["to"] = "Elsewhere"
        serato_rewrite.write_json(path, plan)
        with self.assertRaises(tool.FolderRenameError):
            tool.load_and_validate_plan(path, self.music)


class ApplyTests(PipelineCase):
    def test_apply_renames_both_folders_with_literal_spellings_and_untouched_contents(self):
        before = {
            folder: mutation_engine.directory_inventory(self.music / folder)["sha256"]
            for folder in ("R&b", "Afrobeat")
        }
        contents = {str(p.relative_to(self.music)): p.read_bytes() for p in self.tracks}
        result = self.apply_folders()
        self.assertEqual(names(self.music), {"R&B", "Afrobeats", "Hip Hop"})
        self.assertEqual(result["folders_renamed"], 2)
        self.assertEqual(result["files_in_renamed_folders"], 5)
        after = {
            "R&b": mutation_engine.directory_inventory(self.music / "R&B")["sha256"],
            "Afrobeat": mutation_engine.directory_inventory(self.music / "Afrobeats")["sha256"],
        }
        self.assertEqual(before, after)
        for relative, payload in contents.items():
            moved = relative.replace("R&b/", "R&B/", 1).replace("Afrobeat/", "Afrobeats/", 1)
            self.assertEqual((self.music / moved).read_bytes(), payload)
        self.assertTrue(mutation_engine.recover(self.tmp / "rename-journal.jsonl")["clean"])
        rename_map = json.loads((self.tmp / "serato-map.json").read_text())
        self.assertEqual(
            {rule["from"]: rule["to"] for rule in rename_map},
            {stored(self.music / "R&b"): stored(self.music / "R&B"),
             stored(self.music / "Afrobeat"): stored(self.music / "Afrobeats")},
        )
        # Serato's own files were not touched by the folder step.
        for path in self.serato.rglob("*"):
            if path.is_file():
                self.assertEqual(path.read_bytes(), (self.serato_backup / path.relative_to(self.serato)).read_bytes())

    def test_serato_running_fails_closed_before_anything_moves(self):
        before = names(self.music)
        with self.assertRaisesRegex(serato_rewrite.SeratoRewriteError, "Serato is running"):
            self.apply_folders(process_lister=RUNNING_SERATO)
        self.assertEqual(names(self.music), before)
        self.assertFalse((self.tmp / "rename-journal.jsonl").exists())

    def test_serato_changed_since_its_backup_blocks_the_rename(self):
        (self.serato / "database V2").write_bytes((self.serato / "database V2").read_bytes() + b"\x00")
        before = names(self.music)
        with self.assertRaises(mutation_engine.MutationRefused):
            self.apply_folders()
        self.assertEqual(names(self.music), before)

    def test_new_crate_without_backup_blocks_the_rename(self):
        shutil.copy2(self.serato / "Subcrates" / "Set.crate", self.serato / "Subcrates" / "Fresh.crate")
        before = names(self.music)
        with self.assertRaises(backup_manifest.BackupGateError):
            self.apply_folders()
        self.assertEqual(names(self.music), before)

    def test_sqlite_libraries_can_be_required_backed_up_too(self):
        guards = [
            {"path": self.master, "manifest": self.sqlite_manifest, "source_root": self.library_dir},
            {"path": self.root_db, "manifest": self.sqlite_manifest, "source_root": self.library_dir},
        ]
        self.apply_folders(extra_guards=guards)
        intents = [r for r in mutation_engine.RecoveryJournal(self.tmp / "rename-journal.jsonl").read()
                   if r["type"] == "intent"]
        # database V2 + three crates + master.sqlite + root.sqlite, for each of the two folders
        self.assertEqual([len(r["guard_backups"]) for r in intents], [6, 6])

    def test_unbacked_sqlite_blocks_the_rename(self):
        shutil.copy2(self.master, self.library_dir / "extra.sqlite")
        guards = [{"path": self.library_dir / "extra.sqlite", "manifest": self.sqlite_manifest,
                   "source_root": self.library_dir}]
        before = names(self.music)
        with self.assertRaises(backup_manifest.BackupGateError):
            self.apply_folders(extra_guards=guards)
        self.assertEqual(names(self.music), before)

    def test_content_added_after_the_preview_blocks_that_folder_only_at_apply(self):
        plan = tool.load_and_validate_plan(self.write_plan(), self.music)
        (self.music / "Afrobeat" / "late.mp3").write_bytes(b"late")
        with self.assertRaisesRegex(mutation_engine.MutationRefused, "contents changed since preview"):
            self.apply_folders(plan=plan)
        # The first folder was completed and journaled; the batch is not atomic.
        self.assertIn("R&B", names(self.music))
        self.assertTrue(mutation_engine.recover(self.tmp / "rename-journal.jsonl")["clean"])

    def test_second_run_over_renamed_folders_is_refused(self):
        plan = tool.load_and_validate_plan(self.write_plan(), self.music)
        self.apply_folders(plan=plan)
        with self.assertRaises(tool.FolderRenameError):
            tool.load_and_validate_plan(self.write_plan(plan), self.music)


class LockTests(PipelineCase):
    def setUp(self):
        super().setUp()
        self._locks = tempfile.TemporaryDirectory()
        self.addCleanup(self._locks.cleanup)
        original = folder_lock.LOCKS_DIR
        folder_lock.LOCKS_DIR = Path(self._locks.name)
        self.addCleanup(setattr, folder_lock, "LOCKS_DIR", original)

    def test_every_listed_folder_needs_a_lock_argument(self):
        with self.assertRaisesRegex(tool.FolderRenameError, "missing: 'Afrobeats'"):
            tool.open_heartbeat(["R&b", "Afrobeat", "Serato"], "s1", ["Afrobeat", "Afrobeats", "R&b", "Serato"])

    def test_lock_spellings_share_a_slug_so_one_argument_covers_both(self):
        for folder in ("R&b", "Afrobeat", "Afrobeats", "Serato"):
            acquire(folder, "s1")
        heartbeat = tool.open_heartbeat(
            ["R&b", "Afrobeat", "Afrobeats", "Serato"], "s1", ["Afrobeat", "Afrobeats", "R&B", "Serato"]
        )
        heartbeat.beat()

    def test_a_lock_owned_by_someone_else_stops_the_run(self):
        for folder in ("R&b", "Afrobeat", "Afrobeats"):
            acquire(folder, "s1")
        acquire("Serato", "other-session")
        with self.assertRaises(SystemExit):
            tool.open_heartbeat(["R&b", "Afrobeat", "Afrobeats", "Serato"], "s1",
                                ["Afrobeat", "Afrobeats", "R&b", "Serato"])


class FullPipelineTests(PipelineCase):
    """preview -> apply folders -> rewrite Serato binaries -> rewrite SQLite -> undo."""

    def test_full_sequence_then_undo(self):
        # 1. Read-only previews, built BEFORE anything is renamed.
        folder_plan_path = self.write_plan(
            self.preview(serato_dir=self.serato, sqlite_dbs=[self.master, self.root_db])
        )
        folder_plan = json.loads(folder_plan_path.read_text())
        rules = folder_plan["serato_rename_map"]
        binary_plan = serato_rewrite.build_preview(self.serato, rules)
        binary_plan_path = self.tmp / "binary-plan.json"
        serato_rewrite.write_json(binary_plan_path, binary_plan)
        sqlite_plan = sqlite_rewrite.build_preview([self.master, self.root_db], rules)
        sqlite_plan_path = self.tmp / "sqlite-plan.json"
        serato_rewrite.write_json(sqlite_plan_path, sqlite_plan)
        self.assertEqual(binary_plan["summary"]["references_changed"], 10)
        self.assertEqual(sqlite_plan["summary"]["rows_changed"], 10)

        # 2. Rename the folders (both Serato stores backed up).
        guards = [{"path": db, "manifest": self.sqlite_manifest, "source_root": self.library_dir}
                  for db in (self.master, self.root_db)]
        folders_journal = self.tmp / "folders.jsonl"
        self.apply_folders(
            plan=tool.load_and_validate_plan(folder_plan_path, self.music),
            journal_path=folders_journal, extra_guards=guards,
        )
        self.assertEqual(names(self.music), {"R&B", "Afrobeats", "Hip Hop"})

        # 3. Rewrite database V2 + crates, then the SQLite libraries.
        validated = serato_rewrite.load_and_validate_plan(binary_plan_path, self.serato, require_targets=True)
        serato_rewrite.apply_rewrite_plan(
            plan=validated, serato_dir=self.serato, library_root=self.music,
            manifest=self.serato_manifest, heartbeat=FakeHeartbeat(),
            journal_path=self.tmp / "binary.jsonl", post_check_path=self.tmp / "post.json",
            result_path=self.tmp / "binary-result.json",
        )
        post = json.loads((self.tmp / "post.json").read_text())
        self.assertEqual(post["rewrite_verification"]["status"], "PASS")
        self.assertEqual(post["rewrite_verification"]["nonliteral_target_directories"], [])
        validated_sqlite = sqlite_rewrite.load_and_validate_plan(
            sqlite_plan_path, [self.master, self.root_db], require_targets=True
        )
        sqlite_rewrite.apply_sqlite_plan(
            plan=validated_sqlite, databases=[self.master, self.root_db],
            manifest=backup_manifest.require_pass(
                self._write_manifest(self.sqlite_manifest), self.library_dir),
            heartbeat=FakeHeartbeat(), journal_path=self.tmp / "sqlite.jsonl",
            result_path=self.tmp / "sqlite-result.json", process_lister=NO_SERATO,
        )

        # 4. Every stored path everywhere now exists under its literal spelling.
        stored_paths = serato_rewrite._all_stored_paths(self.serato)
        self.assertTrue(stored_paths)
        for path in stored_paths:
            self.assertTrue(mutation_engine.literal_path_components_exist(Path("/" + path)), path)
            self.assertNotIn("/R&b/", path)
            self.assertNotIn("/Afrobeat/", path)
        import sqlite3
        for database in (self.master, self.root_db):
            connection = sqlite3.connect(f"file:{database}?immutable=1", uri=True)
            for (portable_id,) in connection.execute("SELECT portable_id FROM asset"):
                self.assertTrue(mutation_engine.literal_path_components_exist(Path("/" + portable_id)), portable_id)
            connection.close()

        # 5. Undo is refused while Serato still points at the new names ...
        undo_kwargs = dict(
            journal_path=folders_journal, serato_dir=self.serato, sqlite_dbs=[self.master, self.root_db],
            heartbeat=FakeHeartbeat(), result_path=self.tmp / "undo-result.json", process_lister=NO_SERATO,
        )
        with self.assertRaisesRegex(tool.FolderRenameError, "Serato still references"):
            tool.undo_folder_renames(undo_journal_path=self.tmp / "undo-1.jsonl", **undo_kwargs)
        self.assertEqual(names(self.music), {"R&B", "Afrobeats", "Hip Hop"})

        # ... and succeeds once Serato's files are restored from the verified backups.
        for path in self.serato_backup.rglob("*"):
            if path.is_file():
                shutil.copy2(path, self.serato / path.relative_to(self.serato_backup))
        for database in (self.master, self.root_db):
            shutil.copy2(self.sqlite_backup / database.name, database)
        result = tool.undo_folder_renames(undo_journal_path=self.tmp / "undo-2.jsonl", **undo_kwargs)
        self.assertEqual(result["folders_restored"], 2)
        self.assertEqual(names(self.music), {"R&b", "Afrobeat", "Hip Hop"})
        self.assertTrue(mutation_engine.recover(self.tmp / "undo-2.jsonl")["clean"])
        for track in self.tracks:
            self.assertTrue(mutation_engine.literal_path_components_exist(track), track)

    def _write_manifest(self, manifest):
        path = self.tmp / "sqlite-manifest.json"
        backup_manifest.write_manifest(manifest, path)
        return path


class CliTests(PipelineCase):
    def setUp(self):
        super().setUp()
        self._locks = tempfile.TemporaryDirectory()
        self.addCleanup(self._locks.cleanup)
        original = folder_lock.LOCKS_DIR
        folder_lock.LOCKS_DIR = Path(self._locks.name)
        self.addCleanup(setattr, folder_lock, "LOCKS_DIR", original)

    def run_main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["apply_folder_rename_plan.py", *argv]), \
                mock.patch.object(serato_rewrite, "list_process_commands", NO_SERATO):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = tool.main()
        return code, out.getvalue(), err.getvalue()

    def test_preview_apply_undo_through_the_cli(self):
        folder_map = self.tmp / "map.json"
        folder_map.write_text(json.dumps(self.FOLDER_MAP), encoding="utf-8")
        plan = self.tmp / "plan.json"
        code, stdout, _ = self.run_main(
            "preview", "--library-root", str(self.music), "--folder-map", str(folder_map),
            "--out", str(plan), "--serato-dir", str(self.serato),
            "--sqlite-db", str(self.master), "--sqlite-db", str(self.root_db),
        )
        self.assertEqual(code, 0, stdout)
        preview = json.loads(stdout)
        self.assertEqual(preview["required_lock_folders"], ["Afrobeat", "Afrobeats", "R&b", "Serato"])

        manifest_path = self.tmp / "serato-manifest.json"
        backup_manifest.write_manifest(self.serato_manifest, manifest_path)
        common = ["--session", "cli-test", "--confirm-serato-closed"]
        locks = [arg for name in preview["required_lock_folders"] for arg in ("--lock-folder", name)]
        apply_args = [
            "apply", "--library-root", str(self.music), "--plan", str(plan), "--serato-dir", str(self.serato),
            "--backup-manifest", str(manifest_path), "--journal", str(self.tmp / "j.jsonl"),
            "--out", str(self.tmp / "r.json"), "--serato-rename-map-out", str(self.tmp / "map-out.json"),
            *locks, *common,
        ]
        # No lock held yet: refuses, nothing moves.
        with self.assertRaises(SystemExit):
            self.run_main(*apply_args)
        self.assertEqual(names(self.music), {"R&b", "Afrobeat", "Hip Hop"})

        for name in preview["required_lock_folders"]:
            acquire(name, "cli-test")
        code, stdout, stderr = self.run_main(*apply_args)
        self.assertEqual(code, 0, stderr)
        self.assertEqual(names(self.music), {"R&B", "Afrobeats", "Hip Hop"})
        self.assertTrue((self.tmp / "map-out.json").is_file())

        # Without the acknowledgement flag it refuses.
        code, _, stderr = self.run_main(
            "undo", "--journal", str(self.tmp / "j.jsonl"), "--serato-dir", str(self.serato),
            "--undo-journal", str(self.tmp / "u.jsonl"), "--out", str(self.tmp / "u.json"),
            "--session", "cli-test", *locks,
        )
        self.assertEqual(code, 2)
        self.assertIn("--confirm-serato-closed", stderr)

        code, stdout, stderr = self.run_main(
            "undo", "--journal", str(self.tmp / "j.jsonl"), "--serato-dir", str(self.serato),
            "--undo-journal", str(self.tmp / "u.jsonl"), "--out", str(self.tmp / "u.json"),
            *locks, *common,
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(names(self.music), {"R&b", "Afrobeat", "Hip Hop"})

    def test_recover_dry_run_needs_no_session_or_lock(self):
        journal = self.tmp / "empty.jsonl"
        mutation_engine.RecoveryJournal(journal).append({"type": "run_start", "run_id": "x"})
        code, stdout, _ = self.run_main("recover", "--journal", str(journal))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout)["status"], "DRY_RUN")


if __name__ == "__main__":
    unittest.main()
