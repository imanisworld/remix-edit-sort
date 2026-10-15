"""End-to-end coverage for the three newly governed apply scripts.

Every file is synthetic, every library is temporary, and every apply uses a
real lock plus a genuinely verified backup manifest.
"""

import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
for entry in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import apply_clean_dirty_move_plan as clean_apply  # noqa: E402
import apply_genre_needs_check_folder_plan as genre_apply  # noqa: E402
import apply_new_music_intake_plan as intake_apply  # noqa: E402
import backup_manifest  # noqa: E402
import fixtures  # noqa: E402
import mutation_engine  # noqa: E402
from mutagen.id3 import ID3  # noqa: E402


FOLDER_LOCK = SCRIPTS_DIR / "folder_lock.py"
CLEAN_APPLY = SCRIPTS_DIR / "apply_clean_dirty_move_plan.py"
GENRE_APPLY = SCRIPTS_DIR / "apply_genre_needs_check_folder_plan.py"
INTAKE_APPLY = SCRIPTS_DIR / "apply_new_music_intake_plan.py"


def run(*args):
    return subprocess.run(
        [sys.executable, *[str(arg) for arg in args]],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


class NewApplyScriptsEndToEndTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "library"
        self.root.mkdir()
        self.lock_folder = f"TEST-FIXTURE-new-applies-{uuid.uuid4().hex}"
        self.session = "new-apply-test"
        acquired = run(
            FOLDER_LOCK, "acquire", "--folder", self.lock_folder,
            "--session", self.session, "--agent", "test",
        )
        self.assertEqual(acquired.returncode, 0, acquired.stdout + acquired.stderr)

    def tearDown(self):
        run(
            FOLDER_LOCK, "release", "--folder", self.lock_folder,
            "--session", self.session, "--force", "--reason", "test teardown",
        )
        self._tmp.cleanup()

    def backup(self):
        backup = fixtures.mirror_backup(self.root, self.tmp / "backup")
        manifest = fixtures.passing_manifest(
            self.root, backup, self.tmp / "manifest.json"
        )
        return backup, manifest

    def locked_args(self):
        return ("--lock-folder", self.lock_folder, "--session", self.session)

    def test_folder_scoped_genre_apply_is_locked_backed_up_verified_and_recoverable(self):
        path = fixtures.make_library(self.root, [{
            "relative": "_GENRE NEEDS CHECK/track.mp3",
            "genre": "Other",
            "serato": True,
        }])["_GENRE NEEDS CHECK/track.mp3"]
        before_audio = genre_apply.audio_payload(path)
        before_serato = genre_apply.serato_digest(path)
        _, manifest = self.backup()
        survey = [{
            "path": str(path), "sha256": backup_manifest.sha256_file(path),
            "current_genre": "Other", "proposed_genre": "House",
            "evidence": "synthetic test", "confidence": "HIGH",
        }]
        survey_path = self.tmp / "survey.json"
        survey_path.write_text(json.dumps(survey), encoding="utf-8")

        result = run(
            GENRE_APPLY, "--root", self.root, "--survey", survey_path,
            "--backup-manifest", manifest, "--apply", *self.locked_args(),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["genre_tags_changed"], 1)
        self.assertEqual(genre_apply.read_genre(path), "House")
        self.assertEqual(genre_apply.audio_payload(path), before_audio)
        self.assertEqual(genre_apply.serato_digest(path), before_serato)
        self.assertTrue(mutation_engine.recover(Path(payload["recovery_journal"]))["clean"])

    def test_clean_dirty_apply_moves_and_appends_grouping_without_losing_bytes(self):
        path = fixtures.make_library(self.root, [{
            "relative": "_NEEDS CHECK/track.mp3",
            "grouping": "Existing", "serato": True,
        }])["_NEEDS CHECK/track.mp3"]
        destination = self.root / "CLEAN" / "track.mp3"
        before_audio = clean_apply.audio_payload(path)
        before_serato = clean_apply.serato_digest(path)
        _, manifest = self.backup()
        plan = {
            "mode": "preview-only", "rules": {"approval_required": True},
            "items": [{
                "source": str(path), "destination": str(destination),
                "sha256": backup_manifest.sha256_file(path), "decision": "CLEAN",
                "existing_grouping": ["Existing"],
                "proposed_grouping": "Existing; Clean",
                "tag_change": True, "move": True,
            }],
        }
        plan_path = self.tmp / "clean-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

        result = run(
            CLEAN_APPLY, "--root", self.root, "--plan", plan_path,
            "--backup-manifest", manifest, "--apply", *self.locked_args(),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(path.exists())
        self.assertTrue(destination.is_file())
        self.assertEqual(clean_apply.read_grouping(destination)[0], ["Existing", "Clean"])
        self.assertEqual(clean_apply.audio_payload(destination), before_audio)
        self.assertEqual(clean_apply.serato_digest(destination), before_serato)
        self.assertTrue(mutation_engine.recover(Path(payload["recovery_journal"]))["clean"])

    def test_clean_dirty_apply_supports_a_case_only_move_without_tag_write(self):
        source = fixtures.make_library(self.root, [{
            "relative": "bt005.mp3", "grouping": "Clean",
        }])["bt005.mp3"]
        destination = self.root / "BT005.mp3"
        original_hash = backup_manifest.sha256_file(source)
        _, manifest = self.backup()
        plan = {
            "mode": "preview-only", "rules": {"approval_required": True},
            "items": [{
                "source": str(source), "destination": str(destination),
                "sha256": original_hash, "decision": "CLEAN",
                "existing_grouping": ["Clean"], "proposed_grouping": "Clean",
                "tag_change": False, "move": True,
            }],
        }
        plan_path = self.tmp / "clean-case-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

        result = run(
            CLEAN_APPLY, "--root", self.root, "--plan", plan_path,
            "--backup-manifest", manifest, "--apply", *self.locked_args(),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        literal_names = {entry.name for entry in self.root.iterdir()}
        self.assertIn("BT005.mp3", literal_names)
        self.assertNotIn("bt005.mp3", literal_names)
        self.assertEqual(backup_manifest.sha256_file(destination), original_hash)

    def test_intake_writes_genre_and_grouping_in_one_transaction_then_undoes(self):
        path = fixtures.make_library(self.root, [{
            "relative": "track (Clean Remix).mp3", "genre": "Other",
            "grouping": "Existing", "serato": True,
        }])["track (Clean Remix).mp3"]
        original = path.read_bytes()
        before_audio = intake_apply.audio_payload(path)
        before_serato = intake_apply.serato_digest(path)
        _, manifest = self.backup()
        plan = {
            "mode": "preview-only", "root": str(self.root),
            "rules": {"approval_required": True},
            "counts": {},
            "items": [{
                "path": str(path), "sha256": backup_manifest.sha256_file(path),
                "genre_fix": {"from": "Other", "to": "House", "basis": "test"},
                "grouping_fix": {"existing": ["Existing"], "add_terms": ["Remix", "Edit", "Clean"]},
            }],
        }
        plan_path = self.tmp / "intake-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

        result = run(
            INTAKE_APPLY, "--root", self.root, "--plan", plan_path,
            "--backup-manifest", manifest, "--apply", *self.locked_args(),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["transactions"], 1)
        self.assertEqual(payload["genre_changed"], 1)
        self.assertEqual(payload["grouping_changed"], 1)
        self.assertEqual(intake_apply.read_genre(path), "House")
        self.assertEqual(
            intake_apply.read_grouping(path)[0], ["Existing", "Remix", "Edit", "Clean"]
        )
        self.assertEqual(intake_apply.audio_payload(path), before_audio)
        self.assertEqual(intake_apply.serato_digest(path), before_serato)
        recovery = mutation_engine.recover(Path(payload["recovery_journal"]))
        self.assertTrue(recovery["clean"])
        self.assertEqual(recovery["items_intended"], 1)

        undone = run(
            INTAKE_APPLY, "--root", self.root, "--undo", payload["undo_log"],
            *self.locked_args(),
        )
        self.assertEqual(undone.returncode, 0, undone.stdout + undone.stderr)
        self.assertEqual(path.read_bytes(), original)

    def test_intake_removes_one_grouping_term_and_preserves_the_rest(self):
        path = fixtures.make_library(self.root, [{
            "relative": "track.mp3", "genre": "Hip-Hop",
            "grouping": "Event: LIKE SUGAR ON MY TONGUE; Dirty; Remix", "serato": True,
        }])["track.mp3"]
        before_audio = intake_apply.audio_payload(path)
        before_serato = intake_apply.serato_digest(path)
        _, manifest = self.backup()
        plan = {
            "mode": "preview-only", "root": str(self.root),
            "rules": {"approval_required": True}, "counts": {},
            "items": [{
                "path": str(path), "sha256": backup_manifest.sha256_file(path),
                "grouping_fix": {
                    "existing": ["Event: LIKE SUGAR ON MY TONGUE", "Dirty", "Remix"],
                    "add_terms": [], "remove_terms": ["Event: LIKE SUGAR ON MY TONGUE"],
                },
            }],
        }
        plan_path = self.tmp / "remove-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

        result = run(
            INTAKE_APPLY, "--root", self.root, "--plan", plan_path,
            "--backup-manifest", manifest, "--apply", *self.locked_args(),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["grouping_changed"], 1)
        self.assertEqual(intake_apply.read_genre(path), "Hip-Hop")
        self.assertEqual(intake_apply.read_grouping(path)[0], ["Dirty", "Remix"])
        self.assertEqual(intake_apply.audio_payload(path), before_audio)
        self.assertEqual(intake_apply.serato_digest(path), before_serato)
        self.assertTrue(mutation_engine.recover(Path(payload["recovery_journal"]))["clean"])

    def test_intake_removal_leaving_no_terms_still_verifies_clean(self):
        """Removing the only Grouping term a file has must not be mistaken
        for a verification failure -- read_grouping() on an empty/absent
        TIT1 frame returns [] either way."""
        path = fixtures.make_library(self.root, [{
            "relative": "track.mp3", "genre": "Hip-Hop",
            "grouping": "Event: LIKE SUGAR ON MY TONGUE", "serato": True,
        }])["track.mp3"]
        _, manifest = self.backup()
        plan = {
            "mode": "preview-only", "root": str(self.root),
            "rules": {"approval_required": True}, "counts": {},
            "items": [{
                "path": str(path), "sha256": backup_manifest.sha256_file(path),
                "grouping_fix": {
                    "existing": ["Event: LIKE SUGAR ON MY TONGUE"],
                    "add_terms": [], "remove_terms": ["Event: LIKE SUGAR ON MY TONGUE"],
                },
            }],
        }
        plan_path = self.tmp / "remove-all-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

        result = run(
            INTAKE_APPLY, "--root", self.root, "--plan", plan_path,
            "--backup-manifest", manifest, "--apply", *self.locked_args(),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["grouping_changed"], 1)
        self.assertEqual(intake_apply.read_grouping(path)[0], [])
        self.assertTrue(mutation_engine.recover(Path(payload["recovery_journal"]))["clean"])

    def test_intake_preserves_id3v22_when_both_fields_were_requested(self):
        path = fixtures.write_id3v22_mp3(
            self.root / "old.mp3", genre="Other", grouping="Existing"
        )
        before = path.read_bytes()
        _, manifest = self.backup()
        plan = {
            "mode": "preview-only", "root": str(self.root),
            "rules": {"approval_required": True}, "counts": {},
            "items": [{
                "path": str(path), "sha256": backup_manifest.sha256_file(path),
                "genre_fix": {"from": "Other", "to": "House", "basis": "test"},
                "grouping_fix": {"existing": ["Existing"], "add_terms": ["Clean"]},
            }],
        }
        plan_path = self.tmp / "v22-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        result = run(
            INTAKE_APPLY, "--root", self.root, "--plan", plan_path,
            "--backup-manifest", manifest, "--apply", *self.locked_args(),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["skipped_id3v22"], 1)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(ID3(path).version, (2, 2, 0))

    def test_intake_explicitly_upgrades_id3v22_to_v23_and_preserves_other_frames(self):
        picture = b"synthetic-png-payload"
        geob = b"synthetic-serato-payload"
        path = fixtures.write_id3v22_mp3(
            self.root / "old-upgrade.mp3",
            genre="Other",
            title="Keep This Title",
            artist="Keep This Artist",
            grouping="Existing",
            comment="Keep This Comment",
            picture_data=picture,
            geob_data=geob,
        )
        original = path.read_bytes()
        before_audio = intake_apply.audio_payload(path)
        before_snapshot = intake_apply.id3_preservation_snapshot(path)
        before_serato_payload = intake_apply.serato_payload_digest(path)
        _, manifest = self.backup()
        plan = {
            "mode": "preview-only", "root": str(self.root),
            "rules": {"approval_required": True}, "counts": {},
            "items": [{
                "path": str(path), "sha256": backup_manifest.sha256_file(path),
                "genre_fix": {"from": "Other", "to": "House", "basis": "test"},
                "grouping_fix": {"existing": ["Existing"], "add_terms": ["Clean"]},
                "id3_upgrade": {"from": "2.2", "to": "2.3"},
            }],
        }
        plan_path = self.tmp / "v22-upgrade-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

        result = run(
            INTAKE_APPLY, "--root", self.root, "--plan", plan_path,
            "--backup-manifest", manifest, "--apply", *self.locked_args(),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["id3_upgraded"], 1)
        self.assertEqual(payload["skipped_id3v22"], 0)
        self.assertEqual(ID3(path).version, (2, 3, 0))
        self.assertEqual(intake_apply.read_genre(path), "House")
        self.assertEqual(intake_apply.read_grouping(path)[0], ["Existing", "Clean"])
        self.assertEqual(intake_apply.audio_payload(path), before_audio)
        self.assertEqual(intake_apply.serato_payload_digest(path), before_serato_payload)
        self.assertEqual(intake_apply.id3_preservation_snapshot(path), before_snapshot)
        tags = ID3(path)
        self.assertEqual(tags.getall("APIC")[0].data, picture)
        self.assertEqual(tags.getall("GEOB")[0].data, geob)
        self.assertEqual(str(tags.getall("COMM")[0]), "Keep This Comment")
        undo_record = json.loads(
            Path(payload["undo_log"]).read_text(encoding="utf-8").splitlines()[0]
        )
        self.assertEqual(undo_record["id3_upgraded"], "2.2->2.3")
        self.assertTrue(
            mutation_engine.recover(Path(payload["recovery_journal"]))["clean"]
        )

        undone = run(
            INTAKE_APPLY, "--root", self.root, "--undo", payload["undo_log"],
            *self.locked_args(),
        )
        self.assertEqual(undone.returncode, 0, undone.stdout + undone.stderr)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(ID3(path).version, (2, 2, 0))

    def test_intake_writes_year_to_version_specific_id3_fields_and_undoes(self):
        paths = fixtures.make_library(self.root, [
            {"relative": "v24.mp3", "year": "2020", "v2_version": 4, "serato": True},
            {"relative": "v23.mp3", "year": "2021", "v2_version": 3, "serato": True},
        ])
        originals = {name: path.read_bytes() for name, path in paths.items()}
        before_audio = {
            name: intake_apply.audio_payload(path) for name, path in paths.items()
        }
        _, manifest = self.backup()
        plan = {
            "mode": "preview-only", "root": str(self.root),
            "rules": {"approval_required": True}, "counts": {},
            "items": [
                {
                    "path": str(paths["v24.mp3"]),
                    "sha256": backup_manifest.sha256_file(paths["v24.mp3"]),
                    "year_fix": {
                        "from": "2020", "to": "1999", "basis": "official release",
                        "grade": "confirmed_primary",
                    },
                },
                {
                    "path": str(paths["v23.mp3"]),
                    "sha256": backup_manifest.sha256_file(paths["v23.mp3"]),
                    "year_fix": {
                        "from": "2021", "to": "2001", "basis": "label catalog",
                        "grade": "confirmed_secondary",
                    },
                },
            ],
        }
        plan_path = self.tmp / "year-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

        result = run(
            INTAKE_APPLY, "--root", self.root, "--plan", plan_path,
            "--backup-manifest", manifest, "--apply", *self.locked_args(),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["transactions"], 2)
        self.assertEqual(payload["year_changed"], 2)
        raw24 = ID3(paths["v24.mp3"], translate=False)
        raw23 = ID3(paths["v23.mp3"], translate=False)
        self.assertEqual(str(raw24.getall("TDRC")[0]), "1999")
        self.assertFalse(raw24.getall("TYER"))
        self.assertEqual(str(raw23.getall("TYER")[0]), "2001")
        self.assertFalse(raw23.getall("TDRC"))
        for name, path in paths.items():
            self.assertEqual(intake_apply.audio_payload(path), before_audio[name])
        records = [
            json.loads(line) for line in Path(payload["undo_log"]).read_text().splitlines()
        ]
        self.assertEqual(records[0]["year_grade"], "confirmed_primary")
        self.assertEqual(records[1]["year_basis"], "label catalog")

        undone = run(
            INTAKE_APPLY, "--root", self.root, "--undo", payload["undo_log"],
            *self.locked_args(),
        )
        self.assertEqual(undone.returncode, 0, undone.stdout + undone.stderr)
        for name, path in paths.items():
            self.assertEqual(path.read_bytes(), originals[name])

    def test_intake_year_fix_can_authorize_the_v22_to_v23_upgrade(self):
        path = fixtures.write_id3v22_mp3(
            self.root / "old-year.mp3", title="Preserved", year="2024",
            geob_data=b"serato-year-payload",
        )
        before_audio = intake_apply.audio_payload(path)
        before_serato = intake_apply.serato_payload_digest(path)
        _, manifest = self.backup()
        plan = {
            "mode": "preview-only", "root": str(self.root),
            "rules": {"approval_required": True}, "counts": {},
            "items": [{
                "path": str(path), "sha256": backup_manifest.sha256_file(path),
                "year_fix": {
                    "from": "2024", "to": "2013", "basis": "original release page",
                    "grade": "confirmed_primary",
                },
                "id3_upgrade": {"from": "2.2", "to": "2.3"},
            }],
        }
        plan_path = self.tmp / "v22-year-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        result = run(
            INTAKE_APPLY, "--root", self.root, "--plan", plan_path,
            "--backup-manifest", manifest, "--apply", *self.locked_args(),
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["year_changed"], 1)
        self.assertEqual(payload["id3_upgraded"], 1)
        raw = ID3(path, translate=False)
        self.assertEqual(raw.version, (2, 3, 0))
        self.assertEqual(str(raw.getall("TYER")[0]), "2013")
        self.assertFalse(raw.getall("TDRC"))
        self.assertEqual(str(ID3(path).getall("TIT2")[0]), "Preserved")
        self.assertEqual(intake_apply.audio_payload(path), before_audio)
        self.assertEqual(intake_apply.serato_payload_digest(path), before_serato)

    def test_intake_year_fix_schema_and_live_from_value_are_validated(self):
        path = fixtures.make_library(
            self.root, [{"relative": "year.mp3", "year": "2020"}],
        )["year.mp3"]
        base = {
            "path": str(path), "sha256": backup_manifest.sha256_file(path),
        }
        cases = (
            ("bad-grade", {"from": "2020", "to": "1999", "basis": "none", "grade": "unknown"}, "grade must authorize"),
            ("missing-basis", {"from": "2020", "to": "1999", "grade": "local_evidence"}, "exactly"),
            ("stale-from", {"from": "2021", "to": "1999", "basis": "test", "grade": "local_evidence"}, "year changed"),
        )
        for name, year_fix, message in cases:
            with self.subTest(name=name):
                plan_path = self.tmp / f"{name}.json"
                plan_path.write_text(json.dumps({
                    "mode": "preview-only", "root": str(self.root),
                    "rules": {"approval_required": True}, "counts": {},
                    "items": [{**base, "year_fix": year_fix}],
                }), encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, message):
                    intake_apply.load_and_validate(plan_path, self.root.resolve())

    def test_year_write_uses_flac_date_and_mp4_day_fields(self):
        class FakeTags(dict):
            def save(self):
                self.saved = True

        for suffix, constructor_name, field in (
            (".flac", "FLAC", "date"),
            (".m4a", "MP4", "\xa9day"),
        ):
            with self.subTest(suffix=suffix):
                tags = FakeTags()
                with mock.patch.object(intake_apply, constructor_name, return_value=tags):
                    intake_apply.write_metadata(
                        self.root / f"synthetic{suffix}", genre_value=None,
                        grouping_value=None, year_value="1997", version=4,
                    )
                    intake_apply.verify_year_field(
                        self.root / f"synthetic{suffix}", "1997", 4,
                    )
                self.assertEqual(tags[field], ["1997"])
                self.assertTrue(tags.saved)

    def test_direct_id3v22_upgrade_without_explicit_plan_item_is_refused(self):
        path = fixtures.write_id3v22_mp3(self.root / "direct-old.mp3", genre="Other")
        before = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "not explicitly approved"):
            intake_apply.write_metadata(
                path,
                genre_value="House",
                grouping_value=None,
                version=3,
            )
        self.assertEqual(path.read_bytes(), before)

    def test_id3_upgrade_plan_is_refused_when_source_is_already_v23(self):
        path = fixtures.make_library(self.root, [{
            "relative": "already-v23.mp3", "genre": "Other", "v2_version": 3,
        }])["already-v23.mp3"]
        plan = {
            "mode": "preview-only", "root": str(self.root),
            "rules": {"approval_required": True}, "counts": {},
            "items": [{
                "path": str(path), "sha256": backup_manifest.sha256_file(path),
                "genre_fix": {"from": "Other", "to": "House", "basis": "test"},
                "id3_upgrade": {"from": "2.2", "to": "2.3"},
            }],
        }
        plan_path = self.tmp / "already-v23-plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "not ID3v2.2"):
            intake_apply.load_and_validate(plan_path, self.root.resolve())

    def test_id3_upgrade_plan_refuses_other_version_pairs_and_upgrade_only_items(self):
        path = fixtures.write_id3v22_mp3(self.root / "invalid-upgrade.mp3", genre="Other")
        base = {
            "path": str(path), "sha256": backup_manifest.sha256_file(path),
            "genre_fix": {"from": "Other", "to": "House", "basis": "test"},
        }
        for name, item, message in (
            (
                "wrong-pair",
                {**base, "id3_upgrade": {"from": "2.2", "to": "2.4"}},
                "unsupported ID3 upgrade",
            ),
            (
                "upgrade-only",
                {
                    "path": str(path), "sha256": backup_manifest.sha256_file(path),
                    "id3_upgrade": {"from": "2.2", "to": "2.3"},
                },
                "standalone ID3 upgrade",
            ),
        ):
            with self.subTest(name=name):
                plan_path = self.tmp / f"{name}.json"
                plan_path.write_text(json.dumps({
                    "mode": "preview-only", "root": str(self.root),
                    "rules": {"approval_required": True}, "counts": {},
                    "items": [item],
                }), encoding="utf-8")
                with self.assertRaisesRegex(RuntimeError, message):
                    intake_apply.load_and_validate(plan_path, self.root.resolve())

    def test_all_three_apply_clis_refuse_without_lock_arguments(self):
        path = fixtures.make_library(self.root, [{"relative": "track.mp3"}])["track.mp3"]
        _, manifest = self.backup()
        sha = backup_manifest.sha256_file(path)
        clean_plan = self.tmp / "clean-empty.json"
        clean_plan.write_text(json.dumps({
            "mode": "preview-only", "rules": {"approval_required": True}, "items": [],
        }), encoding="utf-8")
        genre_survey = self.tmp / "genre-empty.json"
        genre_survey.write_text("[]", encoding="utf-8")
        intake_plan = self.tmp / "intake-empty.json"
        intake_plan.write_text(json.dumps({
            "mode": "preview-only", "root": str(self.root),
            "rules": {"approval_required": True}, "items": [], "counts": {},
        }), encoding="utf-8")
        commands = [
            (CLEAN_APPLY, "--root", self.root, "--plan", clean_plan),
            (GENRE_APPLY, "--root", self.root, "--survey", genre_survey),
            (INTAKE_APPLY, "--root", self.root, "--plan", intake_plan),
        ]
        self.assertTrue(sha)  # the PASS manifest covers at least one real file
        for command in commands:
            with self.subTest(script=Path(command[0]).name):
                result = run(*command, "--backup-manifest", manifest, "--apply")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("--lock-folder and --session are required", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
