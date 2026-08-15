"""Tests for lock_guard.LockHeartbeat -- the mechanism that stops a long
apply from mutating files after its lock has gone stale or been reclaimed
mid-run. Imports folder_lock and lock_guard the normal way (via sys.path,
not importlib-by-path) so both modules share the exact same folder_lock
module object -- lock_guard.py does its own `import folder_lock` internally,
and monkeypatching LOCKS_DIR only has effect if that's the same object this
test patches.
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import folder_lock  # noqa: E402
import lock_guard  # noqa: E402


def _run(func, **kwargs):
    defaults = {"agent": None, "note": None, "run_id": None, "repo_sha": "testsha",
                "stale_hours": 6.0, "reason": None, "force": False}
    defaults.update(kwargs)
    args = SimpleNamespace(**defaults)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        func(args)


class LockHeartbeatTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_locks_dir = folder_lock.LOCKS_DIR
        folder_lock.LOCKS_DIR = Path(self._tmp.name)

    def tearDown(self):
        folder_lock.LOCKS_DIR = self._orig_locks_dir
        self._tmp.cleanup()

    def test_heartbeat_renews_the_lock_across_a_simulated_long_apply(self):
        _run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1")
        heartbeat = lock_guard.LockHeartbeat("Hip Hop", "s1", interval_seconds=0)

        seen_timestamps = set()
        for _ in range(5):  # stand-in for a long per-item apply loop
            heartbeat.beat()
            seen_timestamps.add(folder_lock.read_lock("Hip Hop")["last_renewed_at"])
        # Lock must still exist and still belong to s1 at the end.
        final = folder_lock.read_lock("Hip Hop")
        self.assertEqual(final["session"], "s1")
        self.assertTrue(seen_timestamps)

    def test_heartbeat_raises_when_ownership_is_lost_mid_run(self):
        _run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1")
        heartbeat = lock_guard.LockHeartbeat("Hip Hop", "s1", interval_seconds=0)
        heartbeat.beat()  # fine -- s1 still owns it

        # Simulate a second session force-reclaiming the folder mid-run,
        # the way a human/agent would after independently verifying s1 is
        # actually gone.
        _run(folder_lock.cmd_release, folder="Hip Hop", session="s2", force=True,
             reason="simulated reclaim mid-run", agent="codex")
        _run(folder_lock.cmd_acquire, folder="Hip Hop", session="s2", agent="codex")

        with self.assertRaises(SystemExit):
            heartbeat.beat()  # s1's next check must fail closed, not continue silently

        # And the mutation loop that owns this heartbeat should stop BEFORE
        # its next filesystem write -- i.e. the caller sees the exception
        # before doing anything else. There's nothing further to assert
        # about file state here (that's the calling script's job), but the
        # lock on disk must still belong to s2, untouched by s1's failed beat.
        self.assertEqual(folder_lock.read_lock("Hip Hop")["session"], "s2")

    def test_heartbeat_does_not_renew_faster_than_its_interval(self):
        _run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1")
        heartbeat = lock_guard.LockHeartbeat("Hip Hop", "s1", interval_seconds=999)
        heartbeat.beat()

        # Roll the on-disk timestamp back to prove a second beat() inside
        # the interval window is a genuine no-op, not a disguised renewal.
        lock = folder_lock.read_lock("Hip Hop")
        lock["last_renewed_at"] = "2000-01-01T00:00:00-04:00"
        folder_lock.write_lock("Hip Hop", lock)

        heartbeat.beat()
        self.assertEqual(folder_lock.read_lock("Hip Hop")["last_renewed_at"], "2000-01-01T00:00:00-04:00")

    def test_forced_beat_bypasses_the_interval(self):
        _run(folder_lock.cmd_acquire, folder="Hip Hop", session="s1")
        heartbeat = lock_guard.LockHeartbeat("Hip Hop", "s1", interval_seconds=999)
        heartbeat.beat()

        # Backdate a few minutes -- distinguishable from "now" but still well
        # within the 6h staleness window, so this isolates "does force=True
        # skip the internal rate-limiter" from staleness rules entirely.
        from datetime import datetime, timedelta
        backdated = (datetime.now().astimezone() - timedelta(minutes=3)).isoformat(timespec="seconds")
        lock = folder_lock.read_lock("Hip Hop")
        lock["last_renewed_at"] = backdated
        folder_lock.write_lock("Hip Hop", lock)

        heartbeat.beat(force=True)
        self.assertNotEqual(folder_lock.read_lock("Hip Hop")["last_renewed_at"], backdated)


if __name__ == "__main__":
    unittest.main()
