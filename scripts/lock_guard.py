#!/usr/bin/env python3
"""Shared mandatory-lock enforcement for every mutation-capable script.

Every script that can move files or write metadata (apply_plan.py,
apply_version_genre_plan.py, apply_grouping_plan.py,
apply_artist_title_plan.py, and any future apply engine) must call
`require_lock_or_exit()` before it touches a single file -- for --apply AND
for --undo, since undo mutates the filesystem too. This is what turns
folder_lock.py from a convention someone might remember to run into an
actual gate: a script with this guard wired in physically refuses to run
without an active, owned lock.

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
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import folder_lock  # noqa: E402


def require_lock_or_exit(folder: str, session: str, stale_hours: float = 6.0) -> dict:
    """Verify `session` currently holds a non-stale lock on `folder`, or exit.

    Renews the lock on success -- see folder_lock.py's module docstring for
    why renewal-on-use, not pid-liveness, is what keeps an actively-working
    session's lock from going stale mid-run.
    """
    existing = folder_lock.read_lock(folder)
    if not existing:
        raise SystemExit(
            f"REFUSING TO APPLY: no lock held for folder {folder!r}.\n"
            f"Run first: python3 scripts/folder_lock.py acquire "
            f"--folder {folder!r} --session {session!r} --agent claude"
        )
    staleness = folder_lock.evaluate_staleness(existing, stale_hours)
    if staleness["stale"]:
        raise SystemExit(
            f"REFUSING TO APPLY: lock for folder {folder!r} is stale ({staleness['reason']}).\n"
            f"Re-acquire it first: python3 scripts/folder_lock.py acquire "
            f"--folder {folder!r} --session {session!r} --agent claude"
        )
    if existing.get("session") != session:
        raise SystemExit(
            f"REFUSING TO APPLY: folder {folder!r} is locked by a different session "
            f"({existing.get('session')!r}), not {session!r}.\n"
            f"Another agent may be actively working here. Do not force this -- "
            f"investigate with: python3 scripts/folder_lock.py status --folder {folder!r}"
        )
    existing["last_renewed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    folder_lock.write_lock(folder, existing)
    return existing


class LockHeartbeat:
    """Call .beat() at the top of every iteration of a long mutation loop.

    A single require_lock_or_exit() call before the loop starts isn't
    enough: a run long enough to cross the stale window could keep mutating
    files after another session has reclaimed the lock, believing it's
    still exclusive. .beat() re-verifies ownership and renews the lock at
    most once per `interval_seconds` (so it's cheap to call every
    iteration), and calls SystemExit -- via require_lock_or_exit, the same
    gate used before the loop starts -- the moment ownership is lost or
    replaced, so the caller stops BEFORE its next filesystem mutation
    instead of continuing blindly.

    One shared class so every apply script gets this by calling .beat()
    once per item, rather than each reimplementing its own renewal timer.
    """

    def __init__(self, folder: str, session: str, stale_hours: float = 6.0, interval_seconds: float = 30.0):
        self.folder = folder
        self.session = session
        self.stale_hours = stale_hours
        self.interval_seconds = interval_seconds
        self._last_beat = 0.0

    def beat(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._last_beat) < self.interval_seconds:
            return
        self._last_beat = now
        # Raises SystemExit (via require_lock_or_exit) if the lock is gone,
        # stale, or held by someone else -- exactly the fail-closed behavior
        # a mid-loop ownership check needs.
        require_lock_or_exit(self.folder, self.session, self.stale_hours)
