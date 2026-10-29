"""make_backup.py: touched-files-only copies; Serato snapshot only when changed."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import backup_manifest  # noqa: E402
import make_backup as MB  # noqa: E402


class MakeBackupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        t = Path(self._tmp.name).resolve()
        self.lib = t / "Music"
        for rel in ("Hip Hop/a.mp3", "Hip Hop/b.mp3", "Pop/c.mp3"):
            (self.lib / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.lib / rel).write_bytes(rel.encode() * 50)
        self.serato = t / "_Serato_"
        (self.serato / "Subcrates").mkdir(parents=True)
        (self.serato / "SmartCrates").mkdir()
        (self.serato / "Logs").mkdir()
        (self.serato / "database V2").write_bytes(b"db" * 100)
        (self.serato / "Subcrates/x.crate").write_bytes(b"crate")
        (self.serato / "SmartCrates/y.scrate").write_bytes(b"smart")
        (self.serato / "Logs/run.log").write_text("1")
        self.t5 = t / "DJ-backups"
        self.t5.mkdir()

    def run_backup(self, name, rels):
        dest = self.t5 / name
        return MB.backup_files(self.lib, dest, rels, dest / "manifest.json",
                               serato_dir=self.serato, snapshot_root=self.t5), dest

    def test_copies_only_touched_files(self):
        m, dest = self.run_backup("one", ["Hip Hop/a.mp3"])
        self.assertEqual(m["status"], "PASS")
        copied = sorted(p.relative_to(dest / "files").as_posix() for p in (dest / "files").rglob("*") if p.is_file())
        self.assertEqual(copied, ["Hip Hop/a.mp3"])
        gate = backup_manifest.require_pass(dest / "manifest.json", self.lib, serato_required=True)
        self.assertEqual(gate["serato"]["result"], "PASS")

    def test_unchanged_serato_reuses_last_snapshot(self):
        m1, d1 = self.run_backup("one", ["Hip Hop/a.mp3"])
        (self.serato / "Logs/run.log").write_text("2")  # ignored churn
        m2, d2 = self.run_backup("two", ["Pop/c.mp3"])
        self.assertFalse(m1["serato"]["snapshot"]["reused"])
        self.assertTrue(m2["serato"]["snapshot"]["reused"])
        self.assertFalse((d2 / "_Serato_").exists())
        self.assertEqual(m2["serato"]["path"], str((d1 / "_Serato_").resolve()))
        self.assertEqual(m2["status"], "PASS")
        self.assertEqual(len(MB.load_registry(self.t5)), 1)

    def test_changed_serato_takes_new_verified_snapshot(self):
        self.run_backup("one", ["Hip Hop/a.mp3"])
        (self.serato / "database V2").write_bytes(b"changed")
        m2, d2 = self.run_backup("two", ["Hip Hop/b.mp3"])
        self.assertFalse(m2["serato"]["snapshot"]["reused"])
        self.assertTrue((d2 / "_Serato_/database V2").is_file())
        self.assertEqual(len(MB.load_registry(self.t5)), 2)

    def test_tampered_or_deleted_snapshot_is_not_reused(self):
        _, d1 = self.run_backup("one", ["Hip Hop/a.mp3"])
        (d1 / "_Serato_/Subcrates/x.crate").write_bytes(b"corrupt")
        m2, d2 = self.run_backup("two", ["Pop/c.mp3"])
        self.assertFalse(m2["serato"]["snapshot"]["reused"])
        import shutil
        shutil.rmtree(d2 / "_Serato_")
        shutil.rmtree(d1 / "_Serato_")
        m3, d3 = self.run_backup("three", ["Pop/c.mp3"])
        self.assertFalse(m3["serato"]["snapshot"]["reused"])
        self.assertTrue((d3 / "_Serato_").is_dir())

    def test_plan_paths_and_unsafe_paths(self):
        plan = self.t5 / "plan.json"
        plan.write_text(json.dumps({"items": [{"path": str(self.lib / "Pop/c.mp3")}]}))
        self.assertEqual(MB.relative_paths_from_plan(plan, self.lib), ["Pop/c.mp3"])
        with self.assertRaises(RuntimeError):
            MB.copy_touched_files(self.lib, self.t5 / "x", ["../etc/passwd"])
        plan.write_text(json.dumps({"items": [{"path": "/elsewhere/z.mp3"}]}))
        with self.assertRaises(RuntimeError):
            MB.relative_paths_from_plan(plan, self.lib)

    def test_no_serato_dir_means_no_snapshot(self):
        dest = self.t5 / "plain"
        m = MB.backup_files(self.lib, dest, ["Pop/c.mp3"], dest / "m.json", serato_dir=None, snapshot_root=None)
        self.assertEqual(m["serato"]["result"], "NOT_REQUIRED")
        self.assertFalse((dest / "_Serato_").exists())

    def test_smartcrates_only_option_still_exists(self):
        src = (Path(__file__).resolve().parent.parent / "scripts/edit_smart_crate.py").read_text()
        self.assertIn("--smartcrates-only-backup", src)


if __name__ == "__main__":
    unittest.main()
