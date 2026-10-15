"""build_grouping_csv_plan.py: per-track reviewed Grouping values (e.g. DJ
energy crates) -> a plan apply_grouping_plan.py accepts. Synthetic fixtures only."""

import csv
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
import apply_grouping_plan  # noqa: E402

BUILD = SCRIPTS_DIR / "build_grouping_csv_plan.py"
APPLY = SCRIPTS_DIR / "apply_grouping_plan.py"
FOLDER_LOCK = SCRIPTS_DIR / "folder_lock.py"
LOCK_FOLDER = "test-fixture-grouping-csv"
SESSION = "grouping-csv-test"


def run(*args):
    return subprocess.run([sys.executable, *[str(a) for a in args]],
                          capture_output=True, text=True, cwd=REPO_ROOT)


class BuildGroupingCsvPlanTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "Reggae"
        self.out = self.tmp / "out"

    def tearDown(self):
        self._tmp.cleanup()

    def decisions(self, rows) -> Path:
        path = self.tmp / "decisions.csv"
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["full_path", "grouping_value"])
            writer.writeheader()
            for full_path, value in rows:
                writer.writerow({"full_path": str(full_path), "grouping_value": value})
        return path

    def build(self, rows, *extra):
        result = run(BUILD, "--root", self.root, "--decisions", self.decisions(rows),
                     "--out-dir", self.out, *extra)
        plan_path = self.out / "grouping-plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else None
        return result, plan

    def test_appends_to_existing_grouping_and_matches_apply_shape(self):
        made = fixtures.make_library(self.root, [
            {"relative": "CLEAN/Reggae/a.mp3", "grouping": "Clean; Remix; Edit"},
            {"relative": "CLEAN/Reggae/b.mp3"},
        ])
        result, plan = self.build([
            (made["CLEAN/Reggae/a.mp3"], "Energy: Warmup"),
            (made["CLEAN/Reggae/b.mp3"], "Energy: Peak"),
        ], "--require-prefix", "Energy: ")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(plan["mode"], "preview-only")
        self.assertTrue(plan["rules"]["approval_required"])
        by_name = {Path(i["path"]).name: i for i in plan["items"]}
        self.assertEqual(by_name["a.mp3"]["current_grouping"], ["Clean; Remix; Edit"])
        self.assertEqual(by_name["a.mp3"]["combined_grouping"], "Clean; Remix; Edit; Energy: Warmup")
        self.assertEqual(by_name["b.mp3"]["combined_grouping"], "Energy: Peak")
        # The exact keys apply_grouping_plan.load_and_validate reads.
        for item in plan["items"]:
            self.assertTrue({"path", "current_grouping", "combined_grouping", "id3_version"} <= set(item))
        self.assertTrue((self.out / "GROUPING-PREVIEW.md").is_file())

    def test_never_modifies_library_files(self):
        made = fixtures.make_library(self.root, [{"relative": "a.mp3", "grouping": "Clean"}])
        before = made["a.mp3"].read_bytes()
        self.build([(made["a.mp3"], "Energy: Bashment")])
        self.assertEqual(made["a.mp3"].read_bytes(), before)

    def test_blank_value_is_skipped_not_guessed(self):
        made = fixtures.make_library(self.root, [{"relative": "a.mp3"}])
        result, plan = self.build([(made["a.mp3"], "")])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(plan["items"], [])
        self.assertEqual(plan["counts"]["skipped"], {"no grouping_value (undecided)": 1})

    def test_existing_same_value_is_skipped(self):
        made = fixtures.make_library(self.root, [{"relative": "a.mp3", "grouping": "Clean; energy: peak"}])
        _, plan = self.build([(made["a.mp3"], "Energy: Peak")], "--require-prefix", "Energy: ")
        self.assertEqual(plan["items"], [])
        self.assertIn("already has this exact Grouping value", plan["counts"]["skipped"])

    def test_conflicting_value_with_same_prefix_is_skipped_not_stacked(self):
        made = fixtures.make_library(self.root, [{"relative": "a.mp3", "grouping": "Energy: Warmup"}])
        _, plan = self.build([(made["a.mp3"], "Energy: Peak")], "--require-prefix", "Energy: ")
        self.assertEqual(plan["items"], [])
        reasons = list(plan["counts"]["skipped"])
        self.assertEqual(len(reasons), 1)
        self.assertIn("Energy: Warmup", reasons[0])

    def test_id3v22_is_skipped(self):
        made = fixtures.make_library(self.root, [{"relative": "old.mp3", "v2_version": 2}])
        _, plan = self.build([(made["old.mp3"], "Energy: Peak")])
        self.assertEqual(plan["items"], [])
        self.assertIn("ID3v2.2 preserved", plan["counts"]["skipped"])

    def test_errors_fail_closed(self):
        made = fixtures.make_library(self.root, [{"relative": "a.mp3"}, {"relative": "b.mp3"}])
        outside = fixtures.write_mp3(self.tmp / "elsewhere" / "c.mp3")
        result, plan = self.build([
            (made["a.mp3"], "Energy: Peak; Clean"),      # delimiter in value
            (made["b.mp3"], "Mood: Peak"),               # wrong prefix
            (outside, "Energy: Peak"),                   # outside root
            (self.root / "missing.mp3", "Energy: Peak"),  # missing file
        ], "--require-prefix", "Energy: ")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(plan["validation"]["errors"]), 4)
        self.assertEqual(plan["items"], [])

    def test_duplicate_path_is_an_error(self):
        made = fixtures.make_library(self.root, [{"relative": "a.mp3"}])
        result, plan = self.build([(made["a.mp3"], "Energy: Peak"), (made["a.mp3"], "Energy: Warmup")])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(plan["items"]), 1)
        self.assertIn("more than once", plan["validation"]["errors"][0])


class EndToEndThroughApplyGroupingPlan(unittest.TestCase):
    """The built plan must actually apply through the engine, keep audio and
    Serato frames byte-identical, and undo cleanly."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "Reggae"
        self.made = fixtures.make_library(self.root, [
            {"relative": "CLEAN/Reggae/a.mp3", "grouping": "Caribbean; Clean", "serato": True},
            {"relative": "DIRTY/Reggae/b.mp3", "v2_version": 3},
        ])
        self.backup = fixtures.mirror_backup(self.root, self.tmp / "backup")
        self.manifest = fixtures.passing_manifest(self.root, self.backup, self.tmp / "manifest.json")
        acquire = run(FOLDER_LOCK, "acquire", "--folder", LOCK_FOLDER, "--session", SESSION, "--agent", "test")
        self.assertEqual(acquire.returncode, 0, acquire.stdout + acquire.stderr)

    def tearDown(self):
        run(FOLDER_LOCK, "release", "--folder", LOCK_FOLDER, "--session", SESSION, "--force",
            "--reason", "test teardown")
        self._tmp.cleanup()

    def test_apply_writes_appended_grouping_preserves_payloads_and_undoes(self):
        a = self.made["CLEAN/Reggae/a.mp3"]
        b = self.made["DIRTY/Reggae/b.mp3"]
        before_bytes = {a: a.read_bytes(), b: b.read_bytes()}
        before_audio = apply_grouping_plan.audio_payload(a)
        from mutagen.id3 import ID3
        before_serato = apply_grouping_plan.serato_digest(ID3(a))

        decisions = self.tmp / "d.csv"
        decisions.write_text(f"full_path,grouping_value\n{a},Energy: Slow Wine\n{b},Energy: Twerk Dutty\n",
                             encoding="utf-8")
        out = self.tmp / "out"
        built = run(BUILD, "--root", self.root, "--decisions", decisions, "--out-dir", out,
                    "--require-prefix", "Energy: ")
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        plan = out / "grouping-plan.json"

        dry = run(APPLY, "--root", self.root, "--plan", plan, "--backup", self.backup,
                  "--backup-manifest", self.manifest)
        self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
        self.assertEqual(json.loads(dry.stdout)["changes_made"], 0)
        self.assertEqual(a.read_bytes(), before_bytes[a])

        applied = run(APPLY, "--root", self.root, "--plan", plan, "--backup", self.backup,
                      "--backup-manifest", self.manifest, "--apply",
                      "--lock-folder", LOCK_FOLDER, "--session", SESSION, "--label", "t")
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        payload = json.loads(applied.stdout)
        self.assertEqual(payload["grouping_tags_changed"], 2)

        self.assertEqual(apply_grouping_plan.read_grouping(a)[0], ["Caribbean; Clean; Energy: Slow Wine"])
        self.assertEqual(apply_grouping_plan.read_grouping(b), (["Energy: Twerk Dutty"], 3))
        self.assertEqual(apply_grouping_plan.audio_payload(a), before_audio)
        self.assertEqual(apply_grouping_plan.serato_digest(ID3(a)), before_serato)

        journal = next(out.glob("t-recovery-*.jsonl"))
        records = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(records[-1]["type"], "run_complete")

        undone = run(APPLY, "--undo", payload["undo_log"], "--lock-folder", LOCK_FOLDER, "--session", SESSION)
        self.assertEqual(undone.returncode, 0, undone.stdout + undone.stderr)
        self.assertEqual(a.read_bytes(), before_bytes[a])
        self.assertEqual(b.read_bytes(), before_bytes[b])


if __name__ == "__main__":
    unittest.main()
