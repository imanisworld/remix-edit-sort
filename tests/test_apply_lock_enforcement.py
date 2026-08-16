"""End-to-end check that a real mutation script (not just folder_lock.py in
isolation) actually refuses to run without an owned lock, and actually runs
once one is held. Exercises the real .locks/ directory via subprocess calls
to folder_lock.py and apply_version_genre_plan.py, the same way an agent
session invokes them -- a uniquely-named test folder keeps this isolated
from any real lock, and tearDown always releases/removes it.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixtures  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
FOLDER_LOCK = REPO_ROOT / "scripts" / "folder_lock.py"
APPLY_VERSION_GENRE = REPO_ROOT / "scripts" / "apply_version_genre_plan.py"
TEST_FOLDER = "TEST-FIXTURE-lock-enforcement"


def run_cli(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args], capture_output=True, text=True, cwd=REPO_ROOT
    )


def empty_plan() -> dict:
    # A minimal, structurally valid plan.json with zero items -- exercises
    # the real apply() code path (load_and_validate, result-writing) without
    # needing fabricated audio files, since the lock gate runs before any
    # per-item work regardless of item count.
    return {
        "mode": "preview-only",
        "rules": {"approval_required": True},
        "items": [],
        "counts": {"genres": {}, "version_categories": {}},
    }


def library_with_manifest(tmp: Path):
    """A real one-track library plus a genuinely-verified PASS manifest.

    The manifest is built by hashing both trees with the production verifier,
    not hand-written -- a fixture that fabricated PASS would prove nothing
    about the gate.
    """
    root = tmp / "library"
    fixtures.make_library(root, [{"relative": "track.mp3", "genre": "Hip Hop"}])
    fixtures.mirror_backup(root, tmp / "backup")
    manifest = fixtures.passing_manifest(root, tmp / "backup", tmp / "manifest.json")
    plan_path = root / "plan.json"
    plan_path.write_text(json.dumps(empty_plan()), encoding="utf-8")
    return root, plan_path, manifest


class ApplyScriptLockEnforcementTests(unittest.TestCase):
    def tearDown(self):
        run_cli(str(FOLDER_LOCK), "release", "--folder", TEST_FOLDER,
                 "--session", "test-session-a", "--force")

    def test_apply_refuses_without_any_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, plan_path, manifest = library_with_manifest(Path(tmp))

            result = run_cli(
                str(APPLY_VERSION_GENRE), "--root", str(root), "--plan", str(plan_path),
                "--backup-manifest", str(manifest), "--apply",
                "--lock-folder", TEST_FOLDER, "--session", "test-session-a",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("REFUSING TO MUTATE", result.stdout + result.stderr)
            # No result file should have been produced -- the refusal happened
            # before any mutation logic ran.
            self.assertEqual(list(root.glob("*-result-*.json")), [])

    def test_apply_succeeds_once_the_matching_session_holds_the_lock(self):
        acquire = run_cli(str(FOLDER_LOCK), "acquire", "--folder", TEST_FOLDER,
                           "--session", "test-session-a", "--agent", "claude")
        self.assertEqual(acquire.returncode, 0, acquire.stdout + acquire.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            root, plan_path, manifest = library_with_manifest(Path(tmp))

            result = run_cli(
                str(APPLY_VERSION_GENRE), "--root", str(root), "--plan", str(plan_path),
                "--backup-manifest", str(manifest), "--apply",
                "--lock-folder", TEST_FOLDER, "--session", "test-session-a",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["tracks_processed"], 0)

    def test_apply_refuses_when_a_different_session_holds_the_lock(self):
        acquire = run_cli(str(FOLDER_LOCK), "acquire", "--folder", TEST_FOLDER,
                           "--session", "test-session-a", "--agent", "claude")
        self.assertEqual(acquire.returncode, 0, acquire.stdout + acquire.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            root, plan_path, manifest = library_with_manifest(Path(tmp))

            result = run_cli(
                str(APPLY_VERSION_GENRE), "--root", str(root), "--plan", str(plan_path),
                "--backup-manifest", str(manifest), "--apply",
                "--lock-folder", TEST_FOLDER, "--session", "test-session-b",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("REFUSING TO MUTATE", result.stdout + result.stderr)

    def test_dry_run_is_unaffected_by_locking(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, plan_path, manifest = library_with_manifest(Path(tmp))

            result = run_cli(
                str(APPLY_VERSION_GENRE), "--root", str(root), "--plan", str(plan_path),
                "--backup-manifest", str(manifest),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["mode"], "dry-run")


if __name__ == "__main__":
    unittest.main()
