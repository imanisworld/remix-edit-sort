"""Every checkout and linked worktree must resolve the same .locks/ dir."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import folder_lock  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


class SharedLocksDirTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.main = self.root / "main"
        self.main.mkdir()
        git(self.main, "init", "-q")
        git(self.main, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "i")

    def test_main_checkout_uses_its_own_locks(self):
        self.assertEqual(folder_lock.resolve_shared_locks_dir(self.main), self.main / ".locks")

    def test_linked_worktree_shares_main_locks(self):
        wt = self.root / "wt"
        git(self.main, "worktree", "add", "-q", "--detach", str(wt))
        self.assertEqual(folder_lock.resolve_shared_locks_dir(wt), self.main / ".locks")

    def test_subdirectory_resolves_to_main(self):
        sub = self.main / "scripts"
        sub.mkdir()
        self.assertEqual(folder_lock.resolve_shared_locks_dir(sub), self.main / ".locks")

    def test_non_git_dir_falls_back(self):
        plain = self.root / "plain"
        plain.mkdir()
        self.assertEqual(folder_lock.resolve_shared_locks_dir(plain), plain / ".locks")

    def test_lock_taken_in_worktree_visible_from_main(self):
        wt = self.root / "wt2"
        git(self.main, "worktree", "add", "-q", "--detach", str(wt))
        orig = folder_lock.LOCKS_DIR
        self.addCleanup(setattr, folder_lock, "LOCKS_DIR", orig)
        folder_lock.LOCKS_DIR = folder_lock.resolve_shared_locks_dir(wt)
        folder_lock.LOCKS_DIR.mkdir(parents=True, exist_ok=True)
        (folder_lock.LOCKS_DIR / "x.lock.json").write_text("{}")
        self.assertTrue((folder_lock.resolve_shared_locks_dir(self.main) / "x.lock.json").exists())


if __name__ == "__main__":
    unittest.main()
