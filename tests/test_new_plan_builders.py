"""Read-only plan/survey coverage for the newly promoted apply inputs."""

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

import backup_manifest  # noqa: E402
import fixtures  # noqa: E402


BUILD_CLEAN = SCRIPTS_DIR / "build_clean_dirty_move_plan.py"
SURVEY_INTAKE = SCRIPTS_DIR / "survey_new_music_intake.py"
SURVEY_GENRE = SCRIPTS_DIR / "survey_genre_needs_check_backlog.py"


def run(*args):
    return subprocess.run(
        [sys.executable, *[str(arg) for arg in args]],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


class NewPlanBuilderTests(unittest.TestCase):
    def test_clean_dirty_builder_emits_hashed_reviewed_move_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            root = tmp / "library"
            source = fixtures.make_library(root, [{
                "relative": "_NEEDS CHECK/track.mp3", "grouping": "Existing",
            }])["_NEEDS CHECK/track.mp3"]
            before = source.read_bytes()
            evidence = tmp / "version-evidence.csv"
            with evidence.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=["full_path", "clean_dirty_status", "reason"]
                )
                writer.writeheader()
                writer.writerow({
                    "full_path": str(source),
                    "clean_dirty_status": "CONFIRMED_CLEAN",
                    "reason": "explicit filename label",
                })
            out = tmp / "plan.json"
            result = run(
                BUILD_CLEAN, "--root", root, "--version-evidence", evidence,
                "--out", out,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            plan = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(plan["mode"], "preview-only")
            self.assertTrue(plan["rules"]["approval_required"])
            self.assertEqual(len(plan["items"]), 1)
            item = plan["items"][0]
            self.assertEqual(item["sha256"], backup_manifest.sha256_file(source))
            self.assertEqual(Path(item["destination"]), root / "CLEAN" / "track.mp3")
            self.assertEqual(item["proposed_grouping"], "Existing; Clean")
            self.assertEqual(source.read_bytes(), before)

    def test_intake_survey_records_hash_and_only_uses_unanimous_siblings(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            library = tmp / "library"
            intake = library / "music to move" / "batch"
            paths = fixtures.make_library(intake, [
                {"relative": "target (Clean Remix).mp3", "genre": "Other", "grouping": "Existing"},
                {"relative": "sibling-one.mp3", "genre": "House"},
                {"relative": "sibling-two.mp3", "genre": "House"},
            ])
            target = paths["target (Clean Remix).mp3"]
            before = target.read_bytes()
            out = tmp / "intake-plan.json"
            result = run(
                SURVEY_INTAKE, "--root", library / "music to move",
                "--library-root", library, "--skip-duplicate-check", "--out", out,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            plan = json.loads(out.read_text(encoding="utf-8"))
            row = next(
                item for item in plan["items"]
                if Path(item["path"]).resolve() == target.resolve()
            )
            self.assertEqual(row["sha256"], backup_manifest.sha256_file(target))
            self.assertEqual(row["genre_fix"]["to"], "House")
            self.assertEqual(
                row["grouping_fix"]["add_terms"], ["Remix", "Edit", "Clean"]
            )
            self.assertEqual(target.read_bytes(), before)

    def test_genre_needs_check_survey_records_source_hash_without_mutation(self):
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            root = tmp / "library"
            source = fixtures.make_library(root, [{
                "relative": "CLEAN/_GENRE NEEDS CHECK/track.mp3", "genre": "Other",
            }])["CLEAN/_GENRE NEEDS CHECK/track.mp3"]
            before = source.read_bytes()
            out = tmp / "genre-survey.json"
            result = run(SURVEY_GENRE, "--root", root, "--out", out)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            rows = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["sha256"], backup_manifest.sha256_file(source))
            self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
