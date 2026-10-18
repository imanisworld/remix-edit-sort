"""apply_year_bpm_plan.py: exact-value pinning, duplicate/ID3v2.2 refusal,
run_complete journal record, read-back and undo. Synthetic fixtures only."""

import json
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
for entry in (str(TESTS_DIR), str(SCRIPTS_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import fixtures  # noqa: E402
import backup_manifest  # noqa: E402
import apply_year_bpm_plan as Y  # noqa: E402

APPLY = SCRIPTS_DIR / "apply_year_bpm_plan.py"
FOLDER_LOCK = SCRIPTS_DIR / "folder_lock.py"


def run(*args):
    return subprocess.run([sys.executable, *[str(a) for a in args]], capture_output=True, text=True, cwd=REPO_ROOT)


class YearHardeningTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.root = self.tmp / "Hip Hop"
        self.made = fixtures.make_library(self.root, [
            {"relative": "a.mp3", "year": "2018", "serato": True},
            {"relative": "b.mp3", "v2_version": 3},
            {"relative": "c.mp3", "v2_version": 2, "title": "x"},
        ])
        self.backup = fixtures.mirror_backup(self.root, self.tmp / "backup")
        self.manifest = fixtures.passing_manifest(self.root, self.backup, self.tmp / "manifest.json")
        self.lock = f"TEST-FIXTURE-year-{uuid.uuid4().hex}"
        self.session = "year-test"
        r = run(FOLDER_LOCK, "acquire", "--folder", self.lock, "--session", self.session, "--agent", "test")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def tearDown(self):
        run(FOLDER_LOCK, "release", "--folder", self.lock, "--session", self.session, "--force", "--reason", "teardown")
        self._tmp.cleanup()

    def item(self, path, year):
        cy, cb, v = Y.read_year_bpm(path)
        return {"path": str(path), "sha256": backup_manifest.sha256_file(path), "id3_version": v,
                "current_year": cy, "current_bpm": cb, "proposed_year": year, "proposed_bpm": None}

    def apply(self, items, *extra):
        plan = self.tmp / "plan.json"
        plan.write_text(json.dumps({"mode": "preview-only", "rules": {"approval_required": True}, "items": items}))
        return run(APPLY, "--plan", plan, "--backup", self.backup, "--backup-manifest", self.manifest,
                   "--backup-relative-to", self.root, "--out-dir", self.tmp / "out", "--label", "t", "--apply",
                   "--lock-folder", self.lock, "--session", self.session, *extra)

    def test_writes_exact_year_reads_back_keeps_audio_journals_and_undoes(self):
        a, b = self.made["a.mp3"], self.made["b.mp3"]
        before = {p: p.read_bytes() for p in (a, b)}
        audio = Y.audio_payload(a)
        res = self.apply([self.item(a, "2017"), self.item(b, "1999")])
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        out = json.loads(res.stdout)
        self.assertEqual(out["tags_changed"], 2)
        self.assertEqual(Y.read_year_bpm(a)[::2], ("2017", 4))
        self.assertEqual(Y.read_year_bpm(b)[::2], ("1999", 3))
        self.assertEqual(Y.audio_payload(a), audio)
        journal = next((self.tmp / "out").glob("t-recovery-*.jsonl"))
        self.assertEqual(json.loads(journal.read_text().splitlines()[-1])["type"], "run_complete")
        undone = run(APPLY, "--undo", out["undo_log"], "--lock-folder", self.lock, "--session", self.session)
        self.assertEqual(undone.returncode, 0, undone.stdout + undone.stderr)
        for p, data in before.items():
            self.assertEqual(p.read_bytes(), data)

    def test_hash_drift_refused_before_any_write(self):
        a = self.made["a.mp3"]
        it = self.item(a, "2017")
        it["sha256"] = "0" * 64
        before = a.read_bytes()
        res = self.apply([it])
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("hash changed", res.stderr)
        self.assertEqual(a.read_bytes(), before)

    def test_duplicate_path_refused(self):
        a = self.made["a.mp3"]
        res = self.apply([self.item(a, "2017"), self.item(a, "2016")])
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("duplicate path", res.stderr)

    def test_id3v22_refused_at_validation(self):
        c = self.made["c.mp3"]
        before = c.read_bytes()
        res = self.apply([self.item(c, "2001")])
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("ID3v2.2", res.stderr)
        self.assertEqual(c.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
