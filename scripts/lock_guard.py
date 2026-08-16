#!/usr/bin/env python3
"""Shared mandatory-lock enforcement for every mutation-capable script.

Every script that can move files, delete files, or write metadata must call
`require_lock_or_exit()` before it touches a single file -- for --apply AND
for --undo, since undo mutates the filesystem too -- and must then call
`LockHeartbeat.beat()` before EACH subsequent mutation. This is what turns
folder_lock.py from a convention someone might remember to run into an
actual gate: a script with this guard wired in physically refuses to run
without an active, owned lock, and stops mid-run the moment ownership is
lost rather than continuing to write into a folder someone else now owns.

Locking is per named folder (--lock-folder), not derived from --root, by
design: every prior session in this repo has locked at the top-level Music
library folder ("Hip Hop", "Events", "Ambient", ...), not at whatever nested
path an individual apply run happens to target, so an apply run against
Events/VDBL is gated on the "Events" lock, matching what a human/agent
already has to acquire first. Deriving that name automatically from --root
would mean guessing which path segment is "the" library folder -- exactly
the kind of guess this project's rules say not to make. The caller states
it explicitly with --lock-folder, matching how --session is already
explicit.

## The race this module used to have

`require_lock_or_exit()` originally did its own read_lock -> evaluate ->
write_lock sequence in plain Python, *outside* the per-folder mutex that
folder_lock's acquire/touch/release run inside. That is a real ownership
race, not a theoretical one: the reader can be descheduled between the read
and the write while another process legitimately reclaims the folder inside
the mutex, and the original owner's write then resurrects its own ownership
on top of the new owner's lock -- after which two processes both believe
they hold exclusive access to a live music library.

Every ownership check now delegates to `folder_lock.verify_and_renew()`,
which performs check-and-renew as one serialized critical section under
that same mutex, and additionally pins the lock's `run_id` so a
same-session-name reacquisition by someone else cannot be adopted. See that
function's docstring for the full fail-closed ordering.
"""

from __future__ import annotations

# Infrastructure: mutates only files this module owns (locks / its own temp files).
SAFETY_CLASSIFICATION = "infrastructure"

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import folder_lock  # noqa: E402

# Re-exported so callers can catch ownership loss without importing
# folder_lock themselves.
LockOwnershipError = folder_lock.LockOwnershipError


def _acquire_hint(folder: str, session: str) -> str:
    return (
        f"Run first: python3 scripts/folder_lock.py acquire "
        f"--folder {folder!r} --session {session!r} --agent claude"
    )


def require_lock_or_exit(
    folder: str,
    session: str,
    stale_hours: float = 6.0,
    expect_run_id: str | None = None,
    renew: bool = True,
) -> dict:
    """Atomically verify `session` holds a non-stale lock on `folder`, or exit.

    Delegates the whole check-and-renew to folder_lock.verify_and_renew, so
    it runs inside the per-folder mutex as one operation. Renews on success
    -- see folder_lock.py's module docstring for why renewal-on-use, not
    pid-liveness, is what keeps an actively-working session's lock from going
    stale mid-run.

    Any ownership failure -- missing, corrupt, malformed, stale, owned by
    someone else, or run_id changed -- raises SystemExit. There is no code
    path that returns normally without a proven, freshly renewed lock.
    """
    try:
        return folder_lock.verify_and_renew(
            folder, session, stale_hours, expect_run_id=expect_run_id, renew=renew
        )
    except folder_lock.LockOwnershipError as exc:
        raise SystemExit(f"REFUSING TO MUTATE: {exc}\n{_acquire_hint(folder, session)}") from exc


class LockHeartbeat:
    """Call .beat() before every single mutation in a long mutation loop.

    A single require_lock_or_exit() call before the loop starts isn't
    enough: a run long enough to cross the stale window could keep mutating
    files after another session has reclaimed the lock, believing it's still
    exclusive. .beat() re-verifies ownership and renews the lock, and raises
    SystemExit -- via require_lock_or_exit, the same gate used before the
    loop starts -- the moment ownership is lost or replaced, so the caller
    stops BEFORE its next filesystem mutation instead of continuing blindly.

    ## Rate limiting is bounded, and never skips the ownership check

    An earlier version skipped the whole beat (returning immediately) if the
    last one was under `interval_seconds` ago, so most iterations of a fast
    loop did no ownership check at all. That reintroduced exactly the gap
    the heartbeat exists to close: hundreds of files could be mutated
    between two ownership checks. Now every beat verifies ownership; only
    the *durable renewal write* is rate-limited, and only when it is safe --
    which it is, because verify_and_renew reads the authoritative on-disk
    lock under the mutex on every call regardless of whether it rewrites it.

    `pin_run_id()` binds this heartbeat to the exact lock instance the run
    started with, so a lock that changed hands and came back under the same
    session name is still rejected.
    """

    def __init__(
        self,
        folder: str,
        session: str,
        stale_hours: float = 6.0,
        interval_seconds: float = 30.0,
        run_id: str | None = None,
    ):
        self.folder = folder
        self.session = session
        self.stale_hours = stale_hours
        self.interval_seconds = interval_seconds
        self.run_id = run_id
        self.beats = 0
        self._last_renewal = 0.0

    def pin_run_id(self, lock: dict) -> None:
        """Remember the run_id of the lock this run proved at startup."""
        run_id = lock.get("run_id") if isinstance(lock, dict) else None
        if isinstance(run_id, str) and run_id:
            self.run_id = run_id

    def beat(self, force: bool = False) -> dict:
        """Verify ownership now; renew durably at most once per interval.

        Returns the current lock dict. Raises SystemExit if ownership is
        gone, stale, corrupt, or held by anyone else -- callers must not
        catch this to keep going.
        """
        self.beats += 1
        now = time.monotonic()
        due = force or (now - self._last_renewal) >= self.interval_seconds
        lock = require_lock_or_exit(
            self.folder, self.session, self.stale_hours,
            expect_run_id=self.run_id, renew=due,
        )
        if due:
            self._last_renewal = now
        if self.run_id is None:
            self.pin_run_id(lock)
        return lock


class MultiFolderLockHeartbeat:
    """Heartbeat across several folders at once, for a run whose plan spans
    more than one locked top-level folder. Every folder is verified before
    each mutation; losing any one of them stops the run.
    """

    def __init__(self, folders, session: str, stale_hours: float = 6.0, interval_seconds: float = 30.0):
        self._beats = [
            LockHeartbeat(folder, session, stale_hours, interval_seconds)
            for folder in sorted(folders)
        ]
        self.session = session

    def pin_from(self, locks: dict) -> None:
        """locks: {folder: lock_dict} captured at startup."""
        for heartbeat in self._beats:
            lock = locks.get(heartbeat.folder)
            if lock:
                heartbeat.pin_run_id(lock)

    def beat(self, force: bool = False) -> None:
        for heartbeat in self._beats:
            heartbeat.beat(force=force)
