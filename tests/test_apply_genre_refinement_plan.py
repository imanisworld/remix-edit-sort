"""End-to-end coverage for apply_genre_refinement_plan.apply().

Regression: apply() wrote every item, then crashed with NameError while
building the result dict (`backup`, then `genre_counts`, were never defined),
so no result JSON was written and engine.finish() never journaled
run_complete. This runs the whole apply() path on a synthetic library with a
real lock and a genuinely verified backup manifest.
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

import apply_genre_refinement_plan as refine  # noqa: E402
import backup_manifest  # noqa: E402
import fixtures  # noqa: E402
import mutation_engine  # noqa: E402


FOLDER_LOCK = SCRIPTS_DIR / "folder_lock.py"


def run(*args):
    return subprocess.run(
        [sys.executable, *[str(arg) for arg in args]],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )


class ApplyGenreRefinementEndToEndTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "library"
        self.root.mkdir()
        self.lock_folder = f"TEST-FIXTURE-genre-refine-{uuid.uuid4().hex}"
        self.session = "genre-refine-test"
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

    def test_apply_writes_result_and_journals_run_complete(self):
        tracks = fixtures.make_library(self.root, [
            {"relative": "NEW MUSIC/a.mp3", "genre": "Other", "serato": True},
            {"relative": "NEW MUSIC/b.mp3", "genre": "Other"},
        ])
        before = {
            rel: (refine.audio_payload(p), refine.serato_digest(p))
            for rel, p in tracks.items()
        }
        backup = fixtures.mirror_backup(self.root, self.tmp / "backup")
        manifest_path = fixtures.passing_manifest(self.root, backup, self.tmp / "manifest.json")

        plan = {
            "mode": "preview-only",
            "rules": {
                "approval_required": True, "genre_metadata_only": True,
                "no_move": True, "no_delete": True,
            },
            "items": [
                {
                    "path": str(path), "folder": "NEW MUSIC",
                    "combined_status": "A_ONLY", "current_genre": "Other",
                    "proposed_genre": "House",
                    "provenance": {"current_sha256": backup_manifest.sha256_file(path)},
                }
                for path in tracks.values()
            ],
        }
        plan_path = self.tmp / "plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")
        out_dir = self.tmp / "out"
        out_dir.mkdir()

        with mock.patch.object(refine, "LIBRARY_ROOT", self.root):
            heartbeat, manifest = mutation_engine.run_preflight(
                lock_folder=self.lock_folder, session=self.session,
                manifest_path=manifest_path, source_root=self.root,
            )
            refine.apply(plan_path, manifest, out_dir, "genre-refinement", heartbeat)

        for rel, path in tracks.items():
            self.assertEqual(refine.read_genre(path), "House")
            self.assertEqual((refine.audio_payload(path), refine.serato_digest(path)), before[rel])

        results = list(out_dir.glob("genre-refinement-result-*.json"))
        self.assertEqual(len(results), 1)
        result = json.loads(results[0].read_text(encoding="utf-8"))
        self.assertEqual(result["tracks_processed"], 2)
        self.assertEqual(result["genre_tags_changed"], 2)
        self.assertEqual(result["genre_counts"], {"House": 2})
        self.assertEqual(result["backup_id"], manifest["backup_id"])
        self.assertEqual(result["verified_backup"], manifest["backup_root"])

        journal = next(out_dir.glob("genre-refinement-recovery-*.jsonl"))
        records = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(records[-1]["type"], "run_complete")
        self.assertEqual(records[-1]["items_completed"], 2)
        self.assertTrue(mutation_engine.recover(journal)["clean"])


if __name__ == "__main__":
    unittest.main()
