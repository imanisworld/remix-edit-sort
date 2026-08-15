import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "folder_lock.py"
SPEC = importlib.util.spec_from_file_location("folder_lock", MODULE_PATH)
folder_lock = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(folder_lock)


def run(func, **kwargs):
    """Call a cmd_* function with sane argparse-Namespace-like defaults and
    capture its printed JSON, mirroring how main() dispatches subcommands."""
    defaults = {"stale_hours": 6.0, "agent": None, "note": None, "run_id": None, "repo_sha": "testsha", "reason": None}
    defaults.update(kwargs)
    args = SimpleNamespace(**defaults)
    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = func(args)
    return exit_code, json.loads(buf.getvalue())


class FolderLockTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_locks_dir = folder_lock.LOCKS_DIR
        folder_lock.LOCKS_DIR = Path(self._tmp.name)

    def tearDown(self):
        folder_lock.LOCKS_DIR = self._orig_locks_dir
        self._tmp.cleanup()

    def test_two_sessions_competing_for_same_folder(self):
        code1, out1 = run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1")
        self.assertEqual(code1, 0)
        self.assertTrue(out1["acquired"])

        code2, out2 = run(folder_lock.cmd_acquire, folder="Hip Hop", session="s2")
        self.assertEqual(code2, 1)
        self.assertFalse(out2["acquired"])
        self.assertEqual(out2["held_by"]["session"], "s1")

    def test_different_folders_allowed_concurrently(self):
        code1, out1 = run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1")
        code2, out2 = run(folder_lock.cmd_acquire, folder="R&b", session="s2")
        self.assertEqual((code1, code2), (0, 0))
        self.assertTrue(out1["acquired"] and out2["acquired"])

    def test_stale_lock_is_reclaimable_with_recorded_reason(self):
        run(folder_lock.cmd_acquire, folder="Events", session="s1")
        # Force it stale by rewriting last_renewed_at into the past instead of
        # sleeping in a test.
        lock = folder_lock.read_lock("Events")
        lock["last_renewed_at"] = (datetime.now().astimezone() - timedelta(hours=7)).isoformat(timespec="seconds")
        folder_lock.write_lock("Events", lock)

        code, out = run(folder_lock.cmd_acquire, folder="Events", session="s2")
        self.assertEqual(code, 0)
        self.assertTrue(out["acquired"])
        self.assertIn("reclaimed_from", out)
        self.assertEqual(out["reclaimed_from"]["session"], "s1")
        self.assertIn("s1", out["lock"]["note"])  # takeover is never silent

    def test_non_stale_lock_is_not_reclaimable(self):
        run(folder_lock.cmd_acquire, folder="Events", session="s1")
        code, out = run(folder_lock.cmd_acquire, folder="Events", session="s2")
        self.assertEqual(code, 1)
        self.assertFalse(out["acquired"])

    def test_require_fails_when_no_lock_exists(self):
        code, out = run(folder_lock.cmd_require, folder="Never Locked", session="s1")
        self.assertEqual(code, 1)
        self.assertFalse(out["held"])

    def test_require_fails_for_wrong_session(self):
        run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1")
        code, out = run(folder_lock.cmd_require, folder="Hip Hop", session="s2")
        self.assertEqual(code, 1)
        self.assertFalse(out["held"])

    def test_require_succeeds_and_renews_for_owning_session(self):
        run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1")
        # Backdate last_renewed_at so a successful renewal is observable.
        lock = folder_lock.read_lock("Hip Hop")
        backdated = (datetime.now().astimezone() - timedelta(minutes=5)).isoformat(timespec="seconds")
        lock["started_at"] = lock["last_renewed_at"] = backdated
        folder_lock.write_lock("Hip Hop", lock)

        code, out = run(folder_lock.cmd_require, folder="Hip Hop", session="s1")
        self.assertEqual(code, 0)
        self.assertTrue(out["held"])
        after = folder_lock.read_lock("Hip Hop")["last_renewed_at"]
        self.assertNotEqual(backdated, after)  # renewed, not left as the backdated value

    def test_require_fails_once_past_stale_window(self):
        run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1")
        lock = folder_lock.read_lock("Hip Hop")
        lock["last_renewed_at"] = (datetime.now().astimezone() - timedelta(hours=7)).isoformat(timespec="seconds")
        folder_lock.write_lock("Hip Hop", lock)

        code, out = run(folder_lock.cmd_require, folder="Hip Hop", session="s1")
        self.assertEqual(code, 1)
        self.assertFalse(out["held"])

    def test_lock_cleanup_after_release(self):
        run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1")
        self.assertIsNotNone(folder_lock.read_lock("Hip Hop"))
        code, out = run(folder_lock.cmd_release, folder="Hip Hop", session="s1", force=False)
        self.assertEqual(code, 0)
        self.assertTrue(out["released"])
        self.assertIsNone(folder_lock.read_lock("Hip Hop"))

    def test_release_refuses_for_wrong_session_without_force(self):
        run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1")
        code, out = run(folder_lock.cmd_release, folder="Hip Hop", session="s2", force=False)
        self.assertEqual(code, 1)
        self.assertFalse(out["released"])
        self.assertIsNotNone(folder_lock.read_lock("Hip Hop"))  # untouched

    def test_release_with_force_allows_takeover_cleanup(self):
        run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1")
        code, out = run(folder_lock.cmd_release, folder="Hip Hop", session="s2", force=True,
                         reason="verified s1's process is dead")
        self.assertEqual(code, 0)
        self.assertTrue(out["released"])
        self.assertIsNone(folder_lock.read_lock("Hip Hop"))

    def test_force_release_requires_non_blank_reason(self):
        run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1")
        code, out = run(folder_lock.cmd_release, folder="Hip Hop", session="s2", force=True, reason=None)
        self.assertEqual(code, 1)
        self.assertFalse(out["released"])
        self.assertIsNotNone(folder_lock.read_lock("Hip Hop"))  # untouched

        code, out = run(folder_lock.cmd_release, folder="Hip Hop", session="s2", force=True, reason="   ")
        self.assertEqual(code, 1)
        self.assertFalse(out["released"])
        self.assertIsNotNone(folder_lock.read_lock("Hip Hop"))

    def test_force_release_writes_audit_record_before_deleting_lock(self):
        run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1", agent="claude")
        code, out = run(folder_lock.cmd_release, folder="Hip Hop", session="s2", force=True,
                         reason="verified s1's process is dead", agent="codex")
        self.assertEqual(code, 0)

        audit_path = folder_lock._audit_log_path()
        self.assertTrue(audit_path.is_file())
        records = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["type"], "force_release")
        self.assertEqual(record["folder"], "Hip Hop")
        self.assertEqual(record["forcing_session"], "s2")
        self.assertEqual(record["forcing_agent"], "codex")
        self.assertEqual(record["reason"], "verified s1's process is dead")
        self.assertEqual(record["prior_lock"]["session"], "s1")
        self.assertIn("timestamp", record)
        self.assertIn("repo_sha", record)

    def test_force_release_refused_if_audit_write_fails(self):
        run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1")

        original = folder_lock._append_audit_record
        folder_lock._append_audit_record = lambda record: False
        try:
            code, out = run(folder_lock.cmd_release, folder="Hip Hop", session="s2", force=True,
                             reason="verified s1's process is dead")
        finally:
            folder_lock._append_audit_record = original

        self.assertEqual(code, 1)
        self.assertFalse(out["released"])
        # Lock must survive -- refusing to force-release means refusing, not
        # deleting the lock and hoping the audit trail catches up later.
        self.assertIsNotNone(folder_lock.read_lock("Hip Hop"))

    def test_same_session_release_does_not_require_reason(self):
        run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1")
        code, out = run(folder_lock.cmd_release, folder="Hip Hop", session="s1", force=False, reason=None)
        self.assertEqual(code, 0)
        self.assertTrue(out["released"])

    def test_malformed_lock_file_fails_closed_on_acquire(self):
        folder_lock.LOCKS_DIR.mkdir(parents=True, exist_ok=True)
        folder_lock.lock_path("Hip Hop").write_text("{not valid json", encoding="utf-8")

        code, out = run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1")
        self.assertEqual(code, 1)
        self.assertFalse(out["acquired"])
        # The corrupt file must survive untouched -- refusing means refusing.
        self.assertEqual(folder_lock.lock_path("Hip Hop").read_text(encoding="utf-8"), "{not valid json")

    def test_malformed_lock_file_fails_closed_on_check_and_require(self):
        folder_lock.LOCKS_DIR.mkdir(parents=True, exist_ok=True)
        folder_lock.lock_path("Hip Hop").write_text("{not valid json", encoding="utf-8")

        code, out = run(folder_lock.cmd_check, folder="Hip Hop", session="s1")
        self.assertEqual(code, 1)
        self.assertTrue(out["locked"])  # fails closed: corrupt => treated as locked, not free

        code, out = run(folder_lock.cmd_require, folder="Hip Hop", session="s1")
        self.assertEqual(code, 1)
        self.assertFalse(out["held"])

    def test_malformed_lock_file_surfaced_not_hidden_in_status(self):
        folder_lock.LOCKS_DIR.mkdir(parents=True, exist_ok=True)
        folder_lock.lock_path("Hip Hop").write_text("{not valid json", encoding="utf-8")

        code, out = run(folder_lock.cmd_status)
        self.assertEqual(code, 0)
        self.assertEqual(len(out["locks"]), 1)
        self.assertTrue(out["locks"][0]["corrupt"])

    def test_corrupt_lock_can_be_removed_with_force_and_reason(self):
        folder_lock.LOCKS_DIR.mkdir(parents=True, exist_ok=True)
        folder_lock.lock_path("Hip Hop").write_text("{not valid json", encoding="utf-8")

        code, out = run(folder_lock.cmd_release, folder="Hip Hop", session="s1", force=False, reason=None)
        self.assertEqual(code, 1)
        self.assertFalse(out["released"])

        code, out = run(folder_lock.cmd_release, folder="Hip Hop", session="s1", force=True,
                         reason="manually inspected, corrupted by a hand edit")
        self.assertEqual(code, 0)
        self.assertTrue(out["released"])
        self.assertFalse(folder_lock.lock_path("Hip Hop").exists())

    def test_lock_left_behind_after_interruption_is_still_stale_checkable(self):
        # Simulate a session that acquired and then never released (crash,
        # kill -9, network drop) -- the lock file just sits there. Nothing
        # special has to happen for status/require to still reason about it
        # correctly using ordinary staleness rules.
        run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1")
        code, out = run(folder_lock.cmd_status, folder="Hip Hop")
        self.assertEqual(code, 0)
        self.assertEqual(len(out["locks"]), 1)
        self.assertFalse(out["locks"][0]["stale"])

    def test_lock_metadata_has_required_fields(self):
        _, out = run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1", agent="claude", note="test")
        lock = out["lock"]
        for field in ("folder", "session", "agent", "started_at", "last_renewed_at",
                      "pid", "hostname", "repo_sha", "run_id", "note"):
            self.assertIn(field, lock)


if __name__ == "__main__":
    unittest.main()
