"""TRUE multi-process concurrency regression tests for lock ownership.

These are not sequential unit tests with a mocked clock. Each one starts real
OS processes, synchronizes them on a filesystem barrier so their critical
sections genuinely overlap, and then asserts on the on-disk state that
survives. A sequential test cannot prove the property under test here, because
the bug being regressed is an interleaving: it only exists when two processes
are inside the read-check-write window at the same time.

## The bug

`lock_guard.require_lock_or_exit()` used to do:

    existing = folder_lock.read_lock(folder)      # (1) read
    ...checks against `existing`...               # (2) decide
    existing["last_renewed_at"] = now
    folder_lock.write_lock(folder, existing)      # (3) write

with no mutex around (1)-(3), while folder_lock's own acquire/release ran
inside a per-folder flock(). So this interleaving was reachable:

    owner A                          reclaimer B
    ---------------------------------------------------------
    (1) reads lock: "session A"
                                     acquires the folder as "session B"
                                     (inside the mutex, correctly)
    (2) decides: "I own this"
    (3) writes "session A" back  <-- B's ownership is silently erased

After which A and B are both mutating a live music library they each believe
they hold exclusively. `verify_and_renew()` closes it by running (1)-(3) inside
the same mutex, and by pinning `run_id` so a lock that changed hands cannot be
adopted even under a matching session name.
"""

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import folder_lock  # noqa: E402

FOLDER = "RACE-FIXTURE-folder"


def child_source(locks_dir: str, body: str) -> str:
    """A standalone child process that points folder_lock at a private
    .locks/ directory, so these tests never touch the real one."""
    return textwrap.dedent(f"""
        import json, os, sys, time
        sys.path.insert(0, {str(SCRIPTS_DIR)!r})
        import folder_lock, lock_guard
        from pathlib import Path
        folder_lock.LOCKS_DIR = Path({locks_dir!r})
        FOLDER = {FOLDER!r}

        def wait_for_barrier(path, timeout=20.0):
            deadline = time.time() + timeout
            while time.time() < deadline:
                if os.path.exists(path):
                    return True
                time.sleep(0.002)
            raise SystemExit("barrier timeout")

        def report(**payload):
            print(json.dumps(payload))

    """) + textwrap.dedent(body)


def run_child(locks_dir: Path, body: str, wait: bool = True):
    proc = subprocess.Popen(
        [sys.executable, "-c", child_source(str(locks_dir), body)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    if not wait:
        return proc
    out, err = proc.communicate(timeout=60)
    return proc.returncode, out, err


class LockOwnershipRaceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.locks_dir = Path(self._tmp.name) / "locks"
        self.locks_dir.mkdir(parents=True)
        self._orig = folder_lock.LOCKS_DIR
        folder_lock.LOCKS_DIR = self.locks_dir

    def tearDown(self):
        folder_lock.LOCKS_DIR = self._orig
        self._tmp.cleanup()

    def _acquire(self, session: str, stale_hours: float = 6.0) -> dict:
        from types import SimpleNamespace
        import contextlib, io

        args = SimpleNamespace(folder=FOLDER, session=session, agent="test", note=None,
                               run_id=None, repo_sha="test", stale_hours=stale_hours)
        with contextlib.redirect_stdout(io.StringIO()):
            folder_lock.cmd_acquire(args)
        return folder_lock.read_lock(FOLDER)

    # ------------------------------------------------------------------
    def test_simultaneous_acquisition_has_exactly_one_winner(self):
        """Eight real processes race to acquire a free folder at once."""
        barrier = Path(self._tmp.name) / "go"
        body = f"""
            session = sys.argv[1] if len(sys.argv) > 1 else os.environ["SESSION"]
            wait_for_barrier({str(barrier)!r})
            from types import SimpleNamespace
            import contextlib, io
            args = SimpleNamespace(folder=FOLDER, session=session, agent="test",
                                   note=None, run_id=None, repo_sha="t", stale_hours=6.0)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                code = folder_lock.cmd_acquire(args)
            report(session=session, acquired=(code == 0))
        """
        procs = []
        for index in range(8):
            env = dict(os.environ, SESSION=f"racer-{index}")
            procs.append(subprocess.Popen(
                [sys.executable, "-c", child_source(str(self.locks_dir), body)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
            ))
        time.sleep(0.3)          # let every child reach the barrier
        barrier.write_text("go")  # release them together

        winners = []
        for proc in procs:
            out, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)
            payload = json.loads(out.strip().splitlines()[-1])
            if payload["acquired"]:
                winners.append(payload["session"])

        self.assertEqual(len(winners), 1, f"expected exactly one winner, got {winners}")
        self.assertEqual(folder_lock.read_lock(FOLDER)["session"], winners[0])

    def test_different_folders_may_be_acquired_concurrently(self):
        """Locking is per folder: two sessions on two folders must both win."""
        barrier = Path(self._tmp.name) / "go2"
        body = f"""
            folder = os.environ["FOLDER"]
            wait_for_barrier({str(barrier)!r})
            from types import SimpleNamespace
            import contextlib, io
            args = SimpleNamespace(folder=folder, session=os.environ["SESSION"],
                                   agent="test", note=None, run_id=None,
                                   repo_sha="t", stale_hours=6.0)
            with contextlib.redirect_stdout(io.StringIO()):
                code = folder_lock.cmd_acquire(args)
            report(folder=folder, acquired=(code == 0))
        """
        procs = []
        for folder, session in (("Hip Hop", "s-hh"), ("Events", "s-ev")):
            env = dict(os.environ, FOLDER=folder, SESSION=session)
            procs.append(subprocess.Popen(
                [sys.executable, "-c", child_source(str(self.locks_dir), body)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
            ))
        time.sleep(0.3)
        barrier.write_text("go")
        for proc in procs:
            out, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)
            self.assertTrue(json.loads(out.strip().splitlines()[-1])["acquired"])

        self.assertEqual(folder_lock.read_lock("Hip Hop")["session"], "s-hh")
        self.assertEqual(folder_lock.read_lock("Events")["session"], "s-ev")

    # ------------------------------------------------------------------
    def test_stale_owner_cannot_reclaim_ownership_through_its_heartbeat(self):
        """THE regression test for the audited race.

        Process A holds a lock that has just aged past the stale window and is
        about to heartbeat. Process B reclaims the folder. Both are released
        from a barrier simultaneously and each hammers its operation in a loop,
        so the read-check-write windows overlap many times over -- if the
        heartbeat could resurrect stale ownership from a value it read before B
        committed, this would catch it.

        Correct outcome: A's heartbeat fails closed every time, and the lock on
        disk belongs to B when the dust settles.
        """
        # A's lock, already stale (last renewed 9 hours ago, window is 6).
        lock = self._acquire("session-A")
        from datetime import datetime, timedelta
        lock["last_renewed_at"] = (datetime.now().astimezone() - timedelta(hours=9)).isoformat(timespec="seconds")
        lock["started_at"] = lock["last_renewed_at"]
        folder_lock.write_lock(FOLDER, lock)
        stale_run_id = lock["run_id"]

        barrier = Path(self._tmp.name) / "go3"

        heartbeat_body = f"""
            wait_for_barrier({str(barrier)!r})
            failures, successes = 0, 0
            for _ in range(400):
                try:
                    lock_guard.require_lock_or_exit(FOLDER, "session-A", 6.0,
                                                    expect_run_id={stale_run_id!r})
                    successes += 1
                except SystemExit:
                    failures += 1
            report(role="A", successes=successes, failures=failures)
        """
        reclaim_body = f"""
            from types import SimpleNamespace
            import contextlib, io
            wait_for_barrier({str(barrier)!r})
            args = SimpleNamespace(folder=FOLDER, session="session-B", agent="test",
                                   note=None, run_id=None, repo_sha="t", stale_hours=6.0)
            acquired = False
            for _ in range(50):
                with contextlib.redirect_stdout(io.StringIO()):
                    if folder_lock.cmd_acquire(args) == 0:
                        acquired = True
                        break
            report(role="B", acquired=acquired)
        """

        proc_a = run_child(self.locks_dir, heartbeat_body, wait=False)
        proc_b = run_child(self.locks_dir, reclaim_body, wait=False)
        time.sleep(0.3)
        barrier.write_text("go")

        out_a, err_a = proc_a.communicate(timeout=90)
        out_b, err_b = proc_b.communicate(timeout=90)
        self.assertEqual(proc_a.returncode, 0, err_a)
        self.assertEqual(proc_b.returncode, 0, err_b)

        result_a = json.loads(out_a.strip().splitlines()[-1])
        result_b = json.loads(out_b.strip().splitlines()[-1])

        self.assertTrue(result_b["acquired"], "the reclaimer never got the folder")
        self.assertEqual(
            result_a["successes"], 0,
            "a stale owner's heartbeat succeeded -- it can resurrect ownership",
        )

        final = folder_lock.read_lock(FOLDER)
        self.assertEqual(final["session"], "session-B",
                         "the stale owner rewrote itself back over the new owner")
        self.assertNotEqual(final["run_id"], stale_run_id)

    def test_replaced_owner_cannot_rewrite_itself_back_in(self):
        """Ownership transfer under live contention, with A NOT stale.

        A holds a fresh lock and heartbeats in a tight loop. B force-releases
        and reacquires mid-flight. From the moment B owns the folder, every one
        of A's beats must fail, and the final on-disk owner must be B -- never
        A's value written back on top.
        """
        lock = self._acquire("session-A")
        a_run_id = lock["run_id"]
        barrier = Path(self._tmp.name) / "go4"
        handoff = Path(self._tmp.name) / "handoff"

        heartbeat_body = f"""
            wait_for_barrier({str(barrier)!r})
            before_handoff_ok, after_handoff_ok, after_handoff_fail = 0, 0, 0
            deadline = time.time() + 8.0
            while time.time() < deadline:
                handed_off = os.path.exists({str(handoff)!r})
                try:
                    lock_guard.require_lock_or_exit(FOLDER, "session-A", 6.0,
                                                    expect_run_id={a_run_id!r})
                    if handed_off:
                        after_handoff_ok += 1
                    else:
                        before_handoff_ok += 1
                except SystemExit:
                    if handed_off:
                        after_handoff_fail += 1
                        if after_handoff_fail > 20:
                            break
            report(role="A", before_handoff_ok=before_handoff_ok,
                   after_handoff_ok=after_handoff_ok,
                   after_handoff_fail=after_handoff_fail)
        """
        reclaim_body = f"""
            from types import SimpleNamespace
            import contextlib, io
            wait_for_barrier({str(barrier)!r})
            time.sleep(0.4)   # let A prove it is beating successfully first
            rel = SimpleNamespace(folder=FOLDER, session="session-B", force=True,
                                  reason="test handoff", agent="test", stale_hours=6.0,
                                  expect_run_id=None)
            acq = SimpleNamespace(folder=FOLDER, session="session-B", agent="test",
                                  note=None, run_id=None, repo_sha="t", stale_hours=6.0)
            with contextlib.redirect_stdout(io.StringIO()):
                folder_lock.cmd_release(rel)
                code = folder_lock.cmd_acquire(acq)
            open({str(handoff)!r}, "w").write("done")
            report(role="B", acquired=(code == 0))
        """

        proc_a = run_child(self.locks_dir, heartbeat_body, wait=False)
        proc_b = run_child(self.locks_dir, reclaim_body, wait=False)
        time.sleep(0.3)
        barrier.write_text("go")

        out_a, err_a = proc_a.communicate(timeout=90)
        out_b, err_b = proc_b.communicate(timeout=90)
        self.assertEqual(proc_a.returncode, 0, err_a)
        self.assertEqual(proc_b.returncode, 0, err_b)

        result_a = json.loads(out_a.strip().splitlines()[-1])
        result_b = json.loads(out_b.strip().splitlines()[-1])

        self.assertTrue(result_b["acquired"])
        self.assertGreater(result_a["before_handoff_ok"], 0,
                           "A never successfully beat while it legitimately owned the lock")
        self.assertEqual(result_a["after_handoff_ok"], 0,
                         "A kept beating successfully after B took ownership")

        final = folder_lock.read_lock(FOLDER)
        self.assertEqual(final["session"], "session-B")
        self.assertNotEqual(final["run_id"], a_run_id)

    def test_concurrent_heartbeats_never_observe_a_torn_lock_file(self):
        """Readers racing writers must never see a partially-written lock.

        write_lock() goes temp-file -> fsync -> os.replace -> fsync(dir), so a
        reader outside the mutex sees either the old file or the new one, never
        a half-written one. A torn read would surface here as a CorruptLock,
        which is counted rather than swallowed.
        """
        self._acquire("session-A")
        barrier = Path(self._tmp.name) / "go5"

        writer_body = f"""
            wait_for_barrier({str(barrier)!r})
            for _ in range(300):
                lock_guard.require_lock_or_exit(FOLDER, "session-A", 6.0)
            report(role="writer", done=True)
        """
        reader_body = f"""
            wait_for_barrier({str(barrier)!r})
            corrupt, reads = 0, 0
            deadline = time.time() + 5.0
            while time.time() < deadline:
                try:
                    lock = folder_lock.read_lock(FOLDER)
                    if lock is not None:
                        reads += 1
                except folder_lock.CorruptLock:
                    corrupt += 1
            report(role="reader", reads=reads, corrupt=corrupt)
        """

        writer = run_child(self.locks_dir, writer_body, wait=False)
        readers = [run_child(self.locks_dir, reader_body, wait=False) for _ in range(3)]
        time.sleep(0.3)
        barrier.write_text("go")

        out_w, err_w = writer.communicate(timeout=90)
        self.assertEqual(writer.returncode, 0, err_w)
        for reader in readers:
            out, err = reader.communicate(timeout=90)
            self.assertEqual(reader.returncode, 0, err)
            payload = json.loads(out.strip().splitlines()[-1])
            self.assertGreater(payload["reads"], 0, "reader never saw the lock at all")
            self.assertEqual(payload["corrupt"], 0,
                             "a reader observed a torn lock file mid-write")


if __name__ == "__main__":
    unittest.main()
