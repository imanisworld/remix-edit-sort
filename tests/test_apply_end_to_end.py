"""End-to-end apply/undo through a real apply script, on synthetic audio.

These drive `apply_version_genre_plan.py` as a subprocess exactly the way an
agent session would -- real lock, real backup manifest, real plan -- and then
assert on what is actually on disk. Everything is generated; no production
audio, no real `_Serato_` data, no real library path.

The properties pinned here are the ones a DJ library cannot afford to get
wrong: the audio payload never changes, embedded Serato frames never change,
ID3v2.2 blocks are preserved rather than upgraded, Clean/Dirty pairs stay
distinct, and undo restores byte-for-byte.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
for path in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import backup_manifest  # noqa: E402
import fixtures  # noqa: E402
from mutagen.id3 import ID3  # noqa: E402

APPLY = SCRIPTS_DIR / "apply_version_genre_plan.py"
FOLDER_LOCK = SCRIPTS_DIR / "folder_lock.py"
LOCK_FOLDER = "E2E-FIXTURE-folder"
SESSION = "e2e-session"


def run(*args):
    return subprocess.run([sys.executable, *[str(a) for a in args]],
                          capture_output=True, text=True, cwd=REPO_ROOT)


class ApplyEndToEndTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "Hip Hop"
        self.paths = fixtures.make_library(self.root, [
            {"relative": "Song (Clean).mp3", "genre": "Unsorted", "serato": True},
            {"relative": "Song (Dirty).mp3", "genre": "Unsorted", "serato": True},
            {"relative": "21 Savage - 112 (2016).mp3", "genre": "Unsorted"},
            {"relative": "old.mp3", "genre": "Unsorted", "title": "Old", "v2_version": 2},
            {"relative": "bt005.mp3", "genre": "Unsorted"},
            {"relative": "mix — 日本語.mp3", "genre": "Unsorted"},
        ])
        self.backup = fixtures.mirror_backup(self.root, self.tmp / "backup")
        self.manifest_path = fixtures.passing_manifest(
            self.root, self.backup, self.tmp / "manifest.json")
        acquire = run(FOLDER_LOCK, "acquire", "--folder", LOCK_FOLDER,
                      "--session", SESSION, "--agent", "test")
        self.assertEqual(acquire.returncode, 0, acquire.stdout + acquire.stderr)

    def tearDown(self):
        run(FOLDER_LOCK, "release", "--folder", LOCK_FOLDER, "--session", SESSION, "--force",
            "--reason", "e2e teardown")
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    def build_plan(self, items) -> Path:
        plan = {
            "mode": "preview-only",
            "rules": {"approval_required": True},
            "items": items,
            "counts": {"genres": {}, "version_categories": {}},
        }
        path = self.tmp / "plan.json"
        path.write_text(json.dumps(plan), encoding="utf-8")
        return path

    def item(self, relative, genre, move_to=None, tag_change=True):
        source = self.paths[relative]
        return {
            "source": str(source),
            "source_relative": relative,
            "destination": str(self.root / move_to) if move_to else str(source),
            "move": bool(move_to),
            "tag_change": tag_change,
            "current_genre": "Unsorted",
            "proposed_genre": genre,
            "destination_relative": move_to or relative,
            "version_category": ("DIRTY" if move_to and move_to.startswith("DIRTY/") else "CLEAN"),
            "sha256": backup_manifest.sha256_file(source),
        }

    def apply_plan(self, plan_path, extra=()):
        return run(APPLY, "--root", self.root, "--plan", plan_path,
                   "--backup-manifest", self.manifest_path, "--apply",
                   "--lock-folder", LOCK_FOLDER, "--session", SESSION, *extra)

    # ------------------------------------------------------------------
    def test_genre_write_preserves_audio_payload_and_serato_frames(self):
        source = self.paths["Song (Clean).mp3"]
        import apply_version_genre_plan as engine_script
        before_audio = engine_script.audio_payload(source)
        before_serato = engine_script.serato_digest(source)

        result = self.apply_plan(self.build_plan([self.item("Song (Clean).mp3", "R&B")]))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["genre_tags_changed"], 1)
        self.assertEqual(payload["audio_payloads_changed"], 0)
        self.assertEqual(payload["serato_frames_changed"], 0)

        self.assertEqual(engine_script.read_genre(source), "R&B")
        self.assertEqual(engine_script.audio_payload(source), before_audio,
                         "the audio payload changed during a tag write")
        self.assertEqual(engine_script.serato_digest(source), before_serato,
                         "embedded Serato frames changed during a tag write")

    def test_id3v22_files_are_preserved_not_upgraded(self):
        old = self.paths["old.mp3"]
        self.assertEqual(ID3(old).version, (2, 2, 0))
        result = self.apply_plan(self.build_plan([self.item("old.mp3", "R&B")]))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["genre_tags_skipped"], 1)
        self.assertEqual(payload["genre_tags_changed"], 0)
        self.assertEqual(ID3(old).version, (2, 2, 0),
                         "an ID3v2.2 tag was silently upgraded")

    def test_clean_and_dirty_versions_both_survive_a_run(self):
        plan = self.build_plan([
            self.item("Song (Clean).mp3", "R&B", move_to="CLEAN/R&b/Song (Clean).mp3"),
            self.item("Song (Dirty).mp3", "R&B", move_to="DIRTY/R&b/Song (Dirty).mp3"),
        ])
        result = self.apply_plan(plan)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / "CLEAN/R&b/Song (Clean).mp3").is_file())
        self.assertTrue((self.root / "DIRTY/R&b/Song (Dirty).mp3").is_file())

    def test_number_heavy_names_survive_a_move(self):
        """21 Savage / 112 / a year in the title must come through intact."""
        relative = "21 Savage - 112 (2016).mp3"
        before = backup_manifest.sha256_file(self.paths[relative])
        plan = self.build_plan([self.item(relative, "R&B", move_to=f"CLEAN/R&b/{relative}")])
        result = self.apply_plan(plan)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        moved = self.root / "CLEAN" / "R&b" / relative
        self.assertTrue(moved.is_file())
        self.assertNotEqual(backup_manifest.sha256_file(moved), before)  # genre was written
        self.assertIn("21 Savage", moved.name)
        self.assertIn("112", moved.name)
        self.assertIn("2016", moved.name)

    def test_case_only_rename_applies_with_literal_casing_and_undo_restores_it(self):
        source_name = "bt005.mp3"
        destination_name = "BT005.mp3"
        original_hash = backup_manifest.sha256_file(self.paths[source_name])
        plan = self.build_plan([
            self.item(source_name, "R&B", move_to=destination_name, tag_change=False),
        ])

        result = self.apply_plan(plan)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        literal_names = {entry.name for entry in self.root.iterdir()}
        self.assertIn(destination_name, literal_names)
        self.assertNotIn(source_name, literal_names)
        self.assertEqual(
            backup_manifest.sha256_file(self.root / destination_name), original_hash
        )

        undone = run(
            APPLY, "--root", self.root, "--plan", plan,
            "--undo", payload["undo_log"], "--lock-folder", LOCK_FOLDER,
            "--session", SESSION,
        )
        self.assertEqual(undone.returncode, 0, undone.stdout + undone.stderr)
        literal_names = {entry.name for entry in self.root.iterdir()}
        self.assertIn(source_name, literal_names)
        self.assertNotIn(destination_name, literal_names)
        self.assertEqual(backup_manifest.sha256_file(self.root / source_name), original_hash)

    def test_case_only_rename_preserves_unicode_filename_content(self):
        source_name = "mix — 日本語.mp3"
        destination_name = "MIX — 日本語.mp3"
        original_hash = backup_manifest.sha256_file(self.paths[source_name])
        result = self.apply_plan(self.build_plan([
            self.item(source_name, "R&B", move_to=destination_name, tag_change=False),
        ]))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        literal_names = {entry.name for entry in self.root.iterdir()}
        self.assertIn(destination_name, literal_names)
        self.assertNotIn(source_name, literal_names)
        self.assertEqual(
            backup_manifest.sha256_file(self.root / destination_name), original_hash
        )

    # ------------------------------------------------------------------
    def test_a_write_ahead_recovery_journal_is_produced(self):
        result = self.apply_plan(self.build_plan([self.item("Song (Clean).mp3", "R&B")]))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        journal = Path(json.loads(result.stdout)["recovery_journal"])
        self.assertTrue(journal.is_file())
        types = [json.loads(line)["type"] for line in
                 journal.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(types[0], "run_start")
        self.assertIn("intent", types)
        self.assertIn("completed", types)
        self.assertLess(types.index("intent"), types.index("completed"))

        import mutation_engine
        self.assertTrue(mutation_engine.recover(journal)["clean"])

    def test_undo_restores_every_file_byte_for_byte(self):
        originals = {relative: backup_manifest.sha256_file(path)
                     for relative, path in self.paths.items()}
        plan = self.build_plan([
            self.item("Song (Clean).mp3", "R&B", move_to="CLEAN/R&b/Song (Clean).mp3"),
            self.item("Song (Dirty).mp3", "R&B"),
        ])
        result = self.apply_plan(plan)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        undo_log = json.loads(result.stdout)["undo_log"]

        undone = run(APPLY, "--root", self.root, "--plan", self.tmp / "plan.json",
                     "--undo", undo_log, "--lock-folder", LOCK_FOLDER, "--session", SESSION)
        self.assertEqual(undone.returncode, 0, undone.stdout + undone.stderr)
        self.assertEqual(json.loads(undone.stdout)["restored"], 2)

        for relative in ("Song (Clean).mp3", "Song (Dirty).mp3"):
            path = self.root / relative
            self.assertTrue(path.is_file(), f"{relative} was not restored to its original path")
            self.assertEqual(backup_manifest.sha256_file(path), originals[relative],
                             f"{relative} was not restored byte-for-byte")

    # ------------------------------------------------------------------
    def test_apply_is_refused_without_a_backup_manifest(self):
        plan = self.build_plan([self.item("Song (Clean).mp3", "R&B")])
        result = run(APPLY, "--root", self.root, "--plan", plan, "--apply",
                     "--lock-folder", LOCK_FOLDER, "--session", SESSION)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--backup-manifest is required", result.stdout + result.stderr)

    def test_apply_is_refused_with_a_failing_backup_manifest(self):
        (self.tmp / "empty").mkdir()
        bad = fixtures.passing_manifest(self.root, self.tmp / "empty", self.tmp / "bad.json")
        plan = self.build_plan([self.item("Song (Clean).mp3", "R&B")])
        result = run(APPLY, "--root", self.root, "--plan", plan,
                     "--backup-manifest", bad, "--apply",
                     "--lock-folder", LOCK_FOLDER, "--session", SESSION)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("REFUSING TO MUTATE", result.stdout + result.stderr)

    def test_apply_is_refused_when_a_source_changed_since_the_preview(self):
        item = self.item("Song (Clean).mp3", "R&B")
        item["sha256"] = "0" * 64
        result = self.apply_plan(self.build_plan([item]))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hash changed since preview", result.stdout + result.stderr)

    def test_apply_is_refused_on_a_destination_collision_within_the_plan(self):
        plan = self.build_plan([
            self.item("Song (Clean).mp3", "R&B", move_to="CLEAN/R&b/Same.mp3"),
            self.item("Song (Dirty).mp3", "R&B", move_to="CLEAN/R&b/Same.mp3"),
        ])
        result = self.apply_plan(plan)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("destination collision", result.stdout + result.stderr)

    def test_apply_is_refused_when_the_destination_already_exists(self):
        existing = self.root / "CLEAN" / "R&b" / "Taken.mp3"
        fixtures.write_mp3(existing, seed=200)
        plan = self.build_plan([
            self.item("Song (Clean).mp3", "R&B", move_to="CLEAN/R&b/Taken.mp3"),
        ])
        result = self.apply_plan(plan)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("destination already exists", result.stdout + result.stderr)

    def test_apply_is_refused_when_the_source_is_missing(self):
        item = self.item("Song (Clean).mp3", "R&B")
        self.paths["Song (Clean).mp3"].unlink()
        result = self.apply_plan(self.build_plan([item]))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing source", result.stdout + result.stderr)

    def test_apply_is_refused_for_an_unsupported_format(self):
        weird = self.root / "track.wav"
        weird.write_bytes(b"RIFF....WAVEfmt ")
        fixtures.mirror_backup(self.root, self.backup)
        manifest = fixtures.passing_manifest(self.root, self.backup, self.tmp / "m2.json")
        plan = self.build_plan([{
            "source": str(weird), "source_relative": "track.wav",
            "destination": str(weird), "move": False, "tag_change": True,
            "current_genre": "", "proposed_genre": "R&B",
            "sha256": backup_manifest.sha256_file(weird),
        }])
        result = run(APPLY, "--root", self.root, "--plan", plan,
                     "--backup-manifest", manifest, "--apply",
                     "--lock-folder", LOCK_FOLDER, "--session", SESSION)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported tag target", result.stdout + result.stderr)

    def test_apply_is_refused_without_an_owned_lock(self):
        plan = self.build_plan([self.item("Song (Clean).mp3", "R&B")])
        result = run(APPLY, "--root", self.root, "--plan", plan,
                     "--backup-manifest", self.manifest_path, "--apply",
                     "--lock-folder", LOCK_FOLDER, "--session", "someone-else")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("REFUSING TO MUTATE", result.stdout + result.stderr)

    def test_undo_is_refused_without_an_owned_lock(self):
        result = self.apply_plan(self.build_plan([self.item("Song (Clean).mp3", "R&B")]))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        undo_log = json.loads(result.stdout)["undo_log"]
        undone = run(APPLY, "--root", self.root, "--plan", self.tmp / "plan.json",
                     "--undo", undo_log, "--lock-folder", LOCK_FOLDER,
                     "--session", "someone-else")
        self.assertNotEqual(undone.returncode, 0)
        self.assertIn("REFUSING TO MUTATE", undone.stdout + undone.stderr)

    def test_dry_run_mutates_nothing_and_reports_the_backup_gate(self):
        before = {r: backup_manifest.sha256_file(p) for r, p in self.paths.items()}
        plan = self.build_plan([self.item("Song (Clean).mp3", "R&B")])
        result = run(APPLY, "--root", self.root, "--plan", plan,
                     "--backup-manifest", self.manifest_path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "dry-run")
        self.assertEqual(payload["changes_made"], 0)
        self.assertIn("PASS", payload["backup_gate"])
        for relative, digest in before.items():
            self.assertEqual(backup_manifest.sha256_file(self.paths[relative]), digest)


if __name__ == "__main__":
    unittest.main()
