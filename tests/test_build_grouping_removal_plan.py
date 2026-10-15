"""build_grouping_removal_plan.py: the owner-approved exception to append-only
Grouping. Removes only listed exact values. Synthetic fixtures only."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
for entry in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import fixtures  # noqa: E402
import media_fixtures  # noqa: E402
import apply_grouping_plan  # noqa: E402

BUILD = SCRIPTS_DIR / "build_grouping_removal_plan.py"
APPLY = SCRIPTS_DIR / "apply_grouping_plan.py"
FOLDER_LOCK = SCRIPTS_DIR / "folder_lock.py"
LOCK_FOLDER = "test-fixture-grouping-removal"
SESSION = "grouping-removal-test"


def run(*args):
    return subprocess.run([sys.executable, *[str(a) for a in args]],
                          capture_output=True, text=True, cwd=REPO_ROOT)


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "Pop"
        self.out = self.tmp / "out"

    def tearDown(self):
        run(FOLDER_LOCK, "release", "--folder", LOCK_FOLDER, "--session", SESSION, "--force",
            "--reason", "test teardown")
        self._tmp.cleanup()

    def remove_file(self, *values) -> Path:
        path = self.tmp / "remove.txt"
        path.write_text("# junk the owner approved\n" + "\n".join(values) + "\n", encoding="utf-8")
        return path

    def plan(self, *values):
        result = run(BUILD, "plan", "--root", self.root, "--remove", self.remove_file(*values),
                     "--out-dir", self.out)
        plan_path = self.out / "grouping-plan.json"
        return result, json.loads(plan_path.read_text(encoding="utf-8")), plan_path


class SurveyAndPlan(Base):
    def test_survey_lists_values_with_counts_and_changes_nothing(self):
        made = fixtures.make_library(self.root, [
            {"relative": "a.mp3", "grouping": "Source: Pop Girl; November"},
            {"relative": "b.mp3", "grouping": "november"},
            {"relative": "Imani Drops/c.mp3", "grouping": "November"},
        ])
        before = {p: p.read_bytes() for p in made.values()}
        result = run(BUILD, "survey", "--root", self.root, "--out-dir", self.out)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        text = (self.out / "grouping-values.csv").read_text(encoding="utf-8")
        self.assertIn("Source: Pop Girl,1,a.mp3", text)
        self.assertNotIn("Imani Drops", text)
        self.assertEqual({p: p.read_bytes() for p in made.values()}, before)

    def test_removes_only_whole_listed_values_and_keeps_the_rest_in_order(self):
        made = fixtures.make_library(self.root, [
            {"relative": "a.mp3", "grouping": "Clean; November; Source: Pop Girl"},
            {"relative": "b.mp3", "grouping": "November Sets; Event: November"},
            {"relative": "c.mp3", "grouping": "BPM Supreme"},
            {"relative": "Imani Drops/d.mp3", "grouping": "November"},
        ])
        before = {p: p.read_bytes() for p in made.values()}
        result, plan, _ = self.plan("november", "BPM Supreme", "Typo Pool")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        by_name = {Path(i["path"]).name: i for i in plan["items"]}
        self.assertEqual(set(by_name), {"a.mp3", "c.mp3"})
        self.assertEqual(by_name["a.mp3"]["combined_grouping"], "Clean; Source: Pop Girl")
        self.assertEqual(by_name["a.mp3"]["removed_values"], ["November"])
        self.assertEqual(by_name["c.mp3"]["combined_grouping"], "")
        self.assertEqual(plan["counts"]["left_empty"], 1)
        self.assertEqual(plan["counts"]["matched_nothing"], ["Typo Pool"])
        self.assertFalse(plan["rules"]["append_only"])
        self.assertTrue(plan["rules"]["approval_required"])
        self.assertTrue((self.out / "GROUPING-REMOVAL-PREVIEW.md").is_file())
        self.assertEqual({p: p.read_bytes() for p in made.values()}, before)

    def test_bad_remove_list_fails_closed(self):
        fixtures.make_library(self.root, [{"relative": "a.mp3", "grouping": "November"}])
        result, plan, _ = self.plan("November; Clean")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(plan["items"], [])

    def test_id3v22_is_skipped(self):
        fixtures.make_library(self.root, [{"relative": "old.mp3", "v2_version": 2, "grouping": "November"}])
        _, plan, _ = self.plan("November")
        self.assertEqual(plan["items"], [])
        self.assertEqual(plan["counts"]["skipped"], {"ID3v2.2 preserved (not checked)": 1})


class WriterGuard(Base):
    """apply_grouping_plan.py refuses a plan that drops Grouping values unless
    they are on the plan's approved removal list."""

    def test_unapproved_removal_is_refused(self):
        made = fixtures.make_library(self.root, [{"relative": "a.mp3", "grouping": "Clean; November"}])
        _, plan, plan_path = self.plan("November")
        plan["rules"]["grouping_removal"]["approved_values"] = []
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "approved removal list"):
            apply_grouping_plan.load_and_validate(plan_path, self.root.resolve())

        plan["rules"]["grouping_removal"]["approved_values"] = ["November"]
        plan["items"][0]["combined_grouping"] = ""  # would also drop "Clean"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            apply_grouping_plan.load_and_validate(plan_path, self.root.resolve())
        self.assertTrue(made["a.mp3"].is_file())

    def test_append_plans_still_validate(self):
        made = fixtures.make_library(self.root, [{"relative": "a.mp3", "grouping": "Clean"}])
        plan_path = self.tmp / "append.json"
        plan_path.write_text(json.dumps({
            "mode": "preview-only", "rules": {"approval_required": True},
            "items": [{"path": str(made["a.mp3"].resolve()), "current_grouping": ["Clean"],
                       "combined_grouping": "Clean; Source: Pop Girl", "id3_version": 4}],
        }), encoding="utf-8")
        _, items = apply_grouping_plan.load_and_validate(plan_path, self.root.resolve())
        self.assertEqual(len(items), 1)


class EndToEnd(Base):
    def test_apply_removes_listed_values_preserves_payloads_and_undoes(self):
        made = fixtures.make_library(self.root, [
            {"relative": "a.mp3", "grouping": "Clean; November", "serato": True},
            {"relative": "b.mp3", "grouping": "November", "serato": True},
        ])
        flac = media_fixtures.write_tiny(self.root / "c.flac", "flac")
        m4a = media_fixtures.write_tiny(self.root / "d.m4a", "m4a")
        apply_grouping_plan.write_grouping(flac, "Source: Y2K; November", None)
        apply_grouping_plan.write_grouping(m4a, "November", None)
        a, b = made["a.mp3"], made["b.mp3"]
        files = (a, b, flac, m4a)
        before = {p: p.read_bytes() for p in files}
        before_audio = {p: apply_grouping_plan.audio_payload(p) for p in files}
        from mutagen.id3 import ID3
        before_serato = {p: apply_grouping_plan.serato_digest(ID3(p)) for p in (a, b)}

        result, _, plan_path = self.plan("November")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        backup = fixtures.mirror_backup(self.root, self.tmp / "backup")
        manifest = fixtures.passing_manifest(self.root, backup, self.tmp / "manifest.json")
        acquire = run(FOLDER_LOCK, "acquire", "--folder", LOCK_FOLDER, "--session", SESSION, "--agent", "test")
        self.assertEqual(acquire.returncode, 0, acquire.stdout + acquire.stderr)

        applied = run(APPLY, "--root", self.root, "--plan", plan_path, "--backup", backup,
                      "--backup-manifest", manifest, "--apply",
                      "--lock-folder", LOCK_FOLDER, "--session", SESSION, "--label", "t")
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        payload = json.loads(applied.stdout)
        self.assertEqual(payload["grouping_tags_changed"], 4)

        self.assertEqual(apply_grouping_plan.read_grouping(a)[0], ["Clean"])
        self.assertEqual(apply_grouping_plan.read_grouping(b)[0], [])
        self.assertEqual(apply_grouping_plan.read_grouping(flac), (["Source: Y2K"], None))
        self.assertEqual(apply_grouping_plan.read_grouping(m4a), ([], None))
        for p in files:
            self.assertEqual(apply_grouping_plan.audio_payload(p), before_audio[p])
        for p in (a, b):
            self.assertEqual(apply_grouping_plan.serato_digest(ID3(p)), before_serato[p])

        undone = run(APPLY, "--undo", payload["undo_log"], "--lock-folder", LOCK_FOLDER, "--session", SESSION)
        self.assertEqual(undone.returncode, 0, undone.stdout + undone.stderr)
        self.assertEqual({p: p.read_bytes() for p in files}, before)


if __name__ == "__main__":
    unittest.main()
