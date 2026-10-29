#!/usr/bin/env python3
"""Mandatory coordination lock so concurrent agent sessions don't collide
on the same DJ-library folder.

This session discovered, the hard way, that multiple agents (this Claude
session, a Codex session, and another Claude session) were writing to the
same repo and the same Music library at the same time with zero
coordination -- including one folder (NewFiles) getting merged into another
(music to move) mid-run by a different session, silently, while this session
still had stale plans referencing its old paths. A second incident followed:
a session ("claude-metadata-pass") acquired locks on 11 folders and then
stopped running without releasing them.

A third bug was caught by review before it ever reached production: the
first "hardened" version of this tool still acquired locks via plain
read-then-write (`existing = read_lock(folder); ...; write_lock(folder,
lock)`), which is a classic TOCTOU race -- two processes can both read "no
lock" and both write, and the last write wins while both believe they
acquired it. Every acquire/touch/release now runs inside a per-folder
`flock()`-based mutex (see `_folder_mutex()` below), which makes the whole
read-evaluate-write sequence a real cross-process critical section, not
just careful-looking Python. `flock()` is a standard BSD/POSIX primitive,
available via the stdlib `fcntl` module on macOS with no external service.

Locks live in .locks/ at the repo root (gitignored -- they're ephemeral,
session-local state, not history) as one JSON file per folder:

    {
      "folder": "Hip Hop",
      "session": "claude-hiphop-metadata",
      "agent": "claude",
      "started_at": "2026-08-15T19:10:00-04:00",
      "last_renewed_at": "2026-08-15T19:42:00-04:00",
      "pid": 12345,
      "hostname": "brookes-mbp",
      "repo_sha": "f4d4cb0...",
      "run_id": "b3f1...",
      "note": "Grouping backfill"
    }

## Why staleness is time-based, not pid-liveness-based

An earlier version of this tool tried to treat "the recorded pid is no
longer running" as proof a lock was abandoned. That's wrong for how this
tool is actually used: `acquire`/`require`/`release` are one-shot CLI
invocations, so the pid they record is the CLI process's own pid -- which
has already exited by the time ANYONE checks it, including the session that
just acquired the lock. Tested live: a lock acquired seconds earlier was
immediately reported as owned by a dead pid. There is no persistent OS
process backing an agent "session" here, so pid-liveness is not meaningful
evidence one way or the other. `pid`/`hostname`/`repo_sha`/`run_id` are kept
as forensic metadata only (useful for a human to manually cross-check with
`ps` before deciding to force-release something), never as an automated
staleness signal.

Staleness is instead time-based, measured from `last_renewed_at` (falling
back to `started_at` for locks that predate renewal tracking), with a
default 6-hour window. Every successful `require` call -- the gate the
mutation scripts call before each apply, and periodically during a long
apply via `lock_guard.LockHeartbeat` -- renews `last_renewed_at`. So in
practice the window is "6 hours since this folder was last actually worked
on," a self-maintaining heartbeat, not a fixed deadline from initial
acquire. A session that genuinely goes quiet for the full window is fair
game for takeover; one that's actively running apply after apply never goes
stale mid-run.

Takeovers are never silent: the new lock's note records who it was
reclaimed from and why. Force-releasing another session's lock additionally
requires a non-blank `--reason` and is written to a local, fsync'd,
gitignored audit log (`.locks/coordination-audit.jsonl`) BEFORE the lock
file is removed -- if the audit write fails, the release is refused rather
than silently dropping the evidence trail.

A lock file that exists but fails to parse as JSON is never treated as "no
lock" -- that would make filesystem corruption or a torn write look exactly
like a free folder. It fails closed instead: `acquire`/`touch` refuse to
proceed, `check`/`require` report the folder as not available, and `status`
surfaces it as a `corrupt` entry rather than silently omitting it. This
should be rare in practice since every write now goes through an atomic
temp-file-then-rename, but a human might have hand-edited or truncated a
lock file, and this tool should never guess in that situation.

Usage:
    folder_lock.py acquire --folder "Hip Hop" --session "claude-hiphop" [--agent claude] [--note "..."] [--run-id ...] [--repo-sha ...]
    folder_lock.py touch   --folder "Hip Hop" --session "claude-hiphop"      # renew without acquiring
    folder_lock.py release --folder "Hip Hop" --session "claude-hiphop" [--force --reason "..."]
    folder_lock.py check   --folder "Hip Hop"                                # is it free right now?
    folder_lock.py require --folder "Hip Hop" --session "claude-hiphop"      # do I actually hold it? (renews on success)
    folder_lock.py status  [--folder "Hip Hop"]
"""

from __future__ import annotations

# Infrastructure: mutates only files this module owns (locks / its own temp files).
SAFETY_CLASSIFICATION = "infrastructure"

import argparse
import contextlib
import fcntl
import json
import os
import re
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def resolve_shared_locks_dir(repo_root: Path = REPO_ROOT) -> Path:
    """Return the ONE .locks/ directory shared by every checkout of this repo.

    Linked worktrees each have their own working tree, so ``repo_root/.locks``
    would give every worktree a private lock list that other agents can't
    see. Resolve the git common dir instead (the main checkout's ``.git``)
    and use ``<main checkout>/.locks``. Falls back to ``repo_root/.locks``
    only when this is not a git checkout (e.g. an exported copy) or the
    common dir is a bare repository.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return repo_root / ".locks"
    if not out:
        return repo_root / ".locks"
    common = Path(out).resolve()
    if common.name != ".git":
        return repo_root / ".locks"
    return common.parent / ".locks"


LOCKS_DIR = resolve_shared_locks_dir()


class CorruptLock(Exception):
    """A lock file exists but could not be parsed as JSON."""

    def __init__(self, path: Path, cause: Exception):
        super().__init__(f"{path} exists but is not valid JSON: {cause}")
        self.path = path
        self.cause = cause


def slugify(folder: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", folder.casefold()).strip("-")


def lock_path(folder: str) -> Path:
    return LOCKS_DIR / f"{slugify(folder)}.lock.json"


def _mutex_path(folder: str) -> Path:
    return LOCKS_DIR / f"{slugify(folder)}.mutex"


def _audit_log_path() -> Path:
    return LOCKS_DIR / "coordination-audit.jsonl"


@contextlib.contextmanager
def _folder_mutex(folder: str):
    """Cross-process critical section for one folder's lock file.

    Every acquire/touch/release for `folder` runs inside this, turning
    "read the current lock, decide, write the new lock" into a real atomic
    operation instead of three separate steps another process can interleave
    with. flock() blocks until held, so a losing contender simply waits its
    turn rather than racing -- it will then correctly see the winner's lock
    already in place.
    """
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    mutex_path = _mutex_path(folder)
    fd = os.open(str(mutex_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def read_lock(folder: str) -> dict | None:
    """Return the lock dict, or None if no lock file exists.

    Raises CorruptLock if a lock file exists but isn't valid JSON -- callers
    must handle this explicitly (fail closed), never treat it as "no lock".
    """
    path = lock_path(folder)
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CorruptLock(path, exc) from exc


def write_lock(folder: str, lock: dict) -> None:
    """Durably and atomically replace the lock file's contents.

    Temp file + fsync(temp) + os.replace + fsync(directory), same filesystem,
    so (a) a reader outside the mutex -- e.g. `status` -- can never observe a
    partially-written file, and (b) a renewal that this process reported as
    successful is on stable storage before the caller proceeds to mutate the
    library. Losing a heartbeat renewal to the page cache after a crash would
    make an actively-owned lock look older than it is, which is exactly the
    condition that lets someone else reclaim a folder out from under a run.
    """
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    target = lock_path(folder)
    tmp = target.with_suffix(target.suffix + f".tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(lock, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, target)
    dir_fd = os.open(str(LOCKS_DIR), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


class LockOwnershipError(Exception):
    """Ownership of a folder lock could not be verified. Always fail closed.

    `reason` is a short machine-readable code so callers can distinguish the
    cases without string-matching a human message.
    """

    def __init__(self, reason: str, message: str, lock: dict | None = None):
        super().__init__(message)
        self.reason = reason
        self.lock = lock


def verify_and_renew(
    folder: str,
    session: str,
    stale_hours: float = 6.0,
    expect_run_id: str | None = None,
    renew: bool = True,
) -> dict:
    """Atomically prove `session` owns `folder` right now, then renew it.

    `renew=False` performs every ownership check identically but skips the
    durable rewrite. It exists for a heartbeat that has already renewed
    recently and only needs to re-prove ownership before the next mutation:
    the *check* must never be skipped, but an fsync per item is wasteful when
    the recorded timestamp is already fresh. Verification is unaffected --
    the on-disk lock is still read under the mutex either way.

    This is THE ownership primitive. Read-check-renew-write happens as one
    serialized critical section under the same per-folder `flock()` mutex that
    acquire/touch/release use. Before this existed, the heartbeat path in
    lock_guard did that sequence *outside* the mutex, which is a genuine
    ownership race: process A reads "I own this", process B (having waited out
    the stale window) reclaims the folder inside the mutex, then A -- still
    holding the dict it read a moment ago -- writes it back and silently
    resurrects its own ownership over B's. A then keeps mutating files in a
    folder B believes it owns exclusively.

    Fail-closed guarantees, in evaluation order:

    * a lock file that exists but does not parse is an ownership FAILURE, never
      "no lock" (`CorruptLock` is caught here and converted, so no caller can
      accidentally let a torn write look like a free folder);
    * a lock whose payload is not a JSON object, or is missing `session`, is
      malformed and fails;
    * absence of a lock file fails (the heartbeat's whole job is proving a lock
      is *still* there);
    * a stale lock fails and is NOT renewed -- staleness is evaluated on the
      value just read inside the mutex, so a lock that aged out cannot be
      revived by its original owner's next beat;
    * the recorded `session` must equal `session` exactly;
    * `expect_run_id`, when given, must equal the lock's `run_id` exactly. This
      is what stops the subtle resurrection case that a session-name check alone
      misses: if another process reclaims the folder and a *new* lock is later
      created under the same session name, the run_id differs, so the original
      run's heartbeat still fails instead of adopting a lock it never acquired.

    Only after every check passes is `last_renewed_at` bumped and written
    durably, still inside the mutex.
    """
    with _folder_mutex(folder):
        try:
            existing = read_lock(folder)
        except CorruptLock as exc:
            raise LockOwnershipError(
                "corrupt",
                f"lock file for folder {folder!r} exists but is not valid JSON: {exc}",
            ) from exc
        if existing is None:
            raise LockOwnershipError(
                "missing", f"no lock exists for folder {folder!r}"
            )
        if not isinstance(existing, dict) or not isinstance(existing.get("session"), str):
            raise LockOwnershipError(
                "malformed",
                f"lock file for folder {folder!r} is missing a usable 'session' field",
                existing if isinstance(existing, dict) else None,
            )
        staleness = evaluate_staleness(existing, stale_hours)
        if staleness["stale"]:
            raise LockOwnershipError(
                "stale",
                f"lock for folder {folder!r} is stale ({staleness['reason']})",
                existing,
            )
        if existing.get("session") != session:
            raise LockOwnershipError(
                "not_owner",
                f"folder {folder!r} is locked by a different session "
                f"({existing.get('session')!r}), not {session!r}",
                existing,
            )
        if expect_run_id is not None and existing.get("run_id") != expect_run_id:
            raise LockOwnershipError(
                "run_id_mismatch",
                f"folder {folder!r} lock run_id is {existing.get('run_id')!r}, "
                f"not the {expect_run_id!r} this run acquired -- ownership changed hands",
                existing,
            )
        if not renew:
            return existing
        renewed = dict(existing)
        renewed["last_renewed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        write_lock(folder, renewed)
        return renewed


def current_hostname() -> str:
    return socket.gethostname()


def current_repo_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def evaluate_staleness(lock: dict, stale_hours: float) -> dict:
    """Return {"stale": bool, "reason": str}, time-based off last_renewed_at."""
    timestamp = lock.get("last_renewed_at") or lock.get("started_at")
    try:
        started = datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return {"stale": True, "reason": "lock has no valid started_at/last_renewed_at timestamp"}
    now = started.__class__.now(started.tzinfo) if started.tzinfo else datetime.now()
    age = now - started
    if age > timedelta(hours=stale_hours):
        return {
            "stale": True,
            "reason": f"last renewed {age} ago, exceeds --stale-hours={stale_hours}",
        }
    return {
        "stale": False,
        "reason": f"last renewed {age} ago, within {stale_hours}h window",
    }


# Backward-compatible boolean wrapper.
def is_stale(lock: dict, stale_hours: float) -> bool:
    return evaluate_staleness(lock, stale_hours)["stale"]


def _append_audit_record(record: dict) -> bool:
    """Append one fsync'd JSONL record. Returns False (never raises) on any
    I/O failure so callers can refuse the operation that depended on it."""
    try:
        LOCKS_DIR.mkdir(parents=True, exist_ok=True)
        with open(_audit_log_path(), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return True
    except OSError:
        return False


def _new_lock_payload(args: argparse.Namespace, reclaimed_from: dict | None = None) -> dict:
    note = args.note or ""
    if reclaimed_from is not None:
        prior = reclaimed_from
        reason = evaluate_staleness(prior, args.stale_hours)["reason"]
        reclaim_note = (
            f"Reclaimed from session {prior.get('session')!r} "
            f"(pid {prior.get('pid')}, host {prior.get('hostname')}): {reason}."
        )
        note = f"{note} {reclaim_note}".strip()
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "folder": args.folder,
        "session": args.session,
        "agent": getattr(args, "agent", None) or "unknown",
        "started_at": now,
        "last_renewed_at": now,
        "pid": os.getpid(),
        "hostname": current_hostname(),
        "repo_sha": getattr(args, "repo_sha", None) or current_repo_sha(),
        "run_id": getattr(args, "run_id", None) or uuid.uuid4().hex,
        "note": note,
    }


def cmd_acquire(args: argparse.Namespace) -> int:
    with _folder_mutex(args.folder):
        try:
            existing = read_lock(args.folder)
        except CorruptLock as exc:
            print(json.dumps({
                "acquired": False,
                "reason": f"refusing to acquire: existing lock file is corrupt, not treating it as free: {exc}",
                "path": str(exc.path),
            }, indent=2))
            return 1

        reclaimed_from = None
        if existing and existing.get("session") != args.session:
            staleness = evaluate_staleness(existing, args.stale_hours)
            if not staleness["stale"]:
                print(
                    json.dumps(
                        {
                            "acquired": False,
                            "reason": "already locked by another session",
                            "held_by": existing,
                            "staleness_check": staleness,
                        },
                        indent=2,
                    )
                )
                return 1
            # Provably abandoned by the time-based rule. Never a silent
            # takeover -- the new lock records exactly who it replaced and
            # why. Safe to decide and act on inside the same mutex section
            # no other acquire attempt for this folder can be mid-flight.
            reclaimed_from = existing

        lock = _new_lock_payload(args, reclaimed_from=reclaimed_from)
        write_lock(args.folder, lock)
        result = {"acquired": True, "lock": lock}
        if reclaimed_from is not None:
            result["reclaimed_from"] = reclaimed_from
        print(json.dumps(result, indent=2))
        return 0


def cmd_touch(args: argparse.Namespace) -> int:
    """Renew last_renewed_at on a lock we already own, without re-acquiring."""
    with _folder_mutex(args.folder):
        try:
            existing = read_lock(args.folder)
        except CorruptLock as exc:
            print(json.dumps({"touched": False, "reason": f"lock file is corrupt: {exc}"}, indent=2))
            return 1
        if not existing:
            print(json.dumps({"touched": False, "reason": "no lock exists"}, indent=2))
            return 1
        if existing.get("session") != args.session:
            print(json.dumps({
                "touched": False,
                "reason": f"lock is held by a different session ({existing.get('session')!r})",
                "lock": existing,
            }, indent=2))
            return 1
        existing["last_renewed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        write_lock(args.folder, existing)
        print(json.dumps({"touched": True, "lock": existing}, indent=2))
        return 0


def cmd_release(args: argparse.Namespace) -> int:
    with _folder_mutex(args.folder):
        try:
            existing = read_lock(args.folder)
        except CorruptLock as exc:
            if not args.force or not (args.reason and args.reason.strip()):
                print(json.dumps({
                    "released": False,
                    "reason": (
                        f"lock file is corrupt, cannot verify an owning session: {exc}. "
                        "Pass --force with a non-blank --reason to remove it anyway."
                    ),
                }, indent=2))
                return 1
            audit_ok = _append_audit_record({
                "type": "force_release",
                "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                "folder": args.folder,
                "prior_lock": None,
                "prior_lock_corrupt_path": str(exc.path),
                "forcing_session": args.session,
                "forcing_agent": getattr(args, "agent", None),
                "reason": args.reason,
                "repo_sha": current_repo_sha(),
            })
            if not audit_ok:
                print(json.dumps({
                    "released": False,
                    "reason": "could not write the coordination audit record; refusing to force-release",
                }, indent=2))
                return 1
            lock_path(args.folder).unlink(missing_ok=True)
            print(json.dumps({"released": True, "forced": True, "audit_log": str(_audit_log_path())}, indent=2))
            return 0

        if not existing:
            print(json.dumps({"released": False, "reason": "no lock exists"}, indent=2))
            return 0

        if args.expect_run_id and existing.get("run_id") != args.expect_run_id:
            # Session labels are deterministic and can repeat across processes;
            # only the acquire-time run_id proves this caller still owns the
            # lock. A mismatch means ownership changed hands (e.g. a stale lock
            # was reclaimed under the same session label) and this caller must
            # never remove the current owner's lock. --force does not bypass
            # this: passing --expect-run-id declares strict ownership binding.
            print(json.dumps({
                "released": False,
                "reason": (
                    f"lock run_id is {existing.get('run_id')!r}, not the expected "
                    f"{args.expect_run_id!r}; ownership changed hands, refusing to release"
                ),
                "held_by": existing,
            }, indent=2))
            return 1

        same_session = existing.get("session") == args.session
        if not same_session:
            if not args.force:
                print(
                    json.dumps(
                        {
                            "released": False,
                            "reason": "lock is held by a different session; pass --force (and --reason) to override",
                            "held_by": existing,
                        },
                        indent=2,
                    )
                )
                return 1
            if not (args.reason and args.reason.strip()):
                print(json.dumps({
                    "released": False,
                    "reason": "force-releasing another session's lock requires a non-blank --reason",
                    "held_by": existing,
                }, indent=2))
                return 1
            audit_ok = _append_audit_record({
                "type": "force_release",
                "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                "folder": args.folder,
                "prior_lock": existing,
                "forcing_session": args.session,
                "forcing_agent": getattr(args, "agent", None),
                "reason": args.reason,
                "repo_sha": current_repo_sha(),
            })
            if not audit_ok:
                print(json.dumps({
                    "released": False,
                    "reason": "could not write the coordination audit record; refusing to force-release",
                    "held_by": existing,
                }, indent=2))
                return 1

        lock_path(args.folder).unlink(missing_ok=True)
        result = {"released": True}
        if not same_session:
            result["forced"] = True
            result["audit_log"] = str(_audit_log_path())
        print(json.dumps(result, indent=2))
        return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Is this folder free to acquire right now? (Does not require a lock to exist.)"""
    try:
        existing = read_lock(args.folder)
    except CorruptLock as exc:
        print(json.dumps({"locked": True, "reason": f"lock file is corrupt, treating as locked: {exc}"}, indent=2))
        return 1
    if not existing:
        print(json.dumps({"locked": False}, indent=2))
        return 0
    staleness = evaluate_staleness(existing, args.stale_hours)
    print(json.dumps({"locked": not staleness["stale"], "staleness_check": staleness, "lock": existing}, indent=2))
    return 1 if (not staleness["stale"] and existing.get("session") != args.session) else 0


def cmd_require(args: argparse.Namespace) -> int:
    """Do I (this exact session) actually hold a live lock on this folder right now?

    Unlike `check`, this FAILS when no lock exists at all -- it's the gate
    mutation scripts call before touching files (and periodically during a
    long apply, via lock_guard.LockHeartbeat), so the absence of a lock is
    itself a failure, not an implicit green light. On success it renews the
    lock (see module docstring: this is what keeps an actively-working
    session's lock from going stale mid-run).
    """
    try:
        lock = verify_and_renew(
            args.folder, args.session, args.stale_hours,
            expect_run_id=getattr(args, "expect_run_id", None),
        )
    except LockOwnershipError as exc:
        print(json.dumps({"held": False, "reason": str(exc), "code": exc.reason, "lock": exc.lock}, indent=2))
        return 1
    print(json.dumps({"held": True, "lock": lock}, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    if not LOCKS_DIR.is_dir():
        print(json.dumps({"locks": []}, indent=2))
        return 0
    locks = []
    for path in sorted(LOCKS_DIR.glob("*.lock.json")):
        try:
            lock = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            locks.append({"path": str(path), "corrupt": True, "error": str(exc)})
            continue
        if args.folder and slugify(args.folder) != slugify(lock.get("folder", "")):
            continue
        staleness = evaluate_staleness(lock, args.stale_hours)
        lock["stale"] = staleness["stale"]
        lock["staleness_reason"] = staleness["reason"]
        locks.append(lock)
    print(json.dumps({"locks": locks}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stale-hours", type=float, default=6.0)
    sub = parser.add_subparsers(dest="command", required=True)

    p_acquire = sub.add_parser("acquire")
    p_acquire.add_argument("--folder", required=True)
    p_acquire.add_argument("--session", required=True)
    p_acquire.add_argument("--agent", help="e.g. claude, codex, chatgpt")
    p_acquire.add_argument("--note")
    p_acquire.add_argument("--run-id", help="defaults to a fresh uuid4")
    p_acquire.add_argument("--repo-sha", help="defaults to current HEAD")
    p_acquire.set_defaults(func=cmd_acquire)

    p_touch = sub.add_parser("touch")
    p_touch.add_argument("--folder", required=True)
    p_touch.add_argument("--session", required=True)
    p_touch.set_defaults(func=cmd_touch)

    p_release = sub.add_parser("release")
    p_release.add_argument("--folder", required=True)
    p_release.add_argument("--session", required=True)
    p_release.add_argument("--force", action="store_true")
    p_release.add_argument("--reason", help="required when force-releasing a different session's lock")
    p_release.add_argument(
        "--expect-run-id",
        help="refuse unless the lock's run_id matches exactly (strongest ownership proof; "
             "not bypassed by --force)",
    )
    p_release.add_argument("--agent", help="e.g. claude, codex, chatgpt (recorded in the audit log)")
    p_release.set_defaults(func=cmd_release)

    p_check = sub.add_parser("check")
    p_check.add_argument("--folder", required=True)
    p_check.add_argument("--session", default="")
    p_check.set_defaults(func=cmd_check)

    p_require = sub.add_parser("require")
    p_require.add_argument("--folder", required=True)
    p_require.add_argument("--session", required=True)
    p_require.add_argument(
        "--expect-run-id",
        help="also require the lock's run_id to match exactly (strongest ownership proof)",
    )
    p_require.set_defaults(func=cmd_require)

    p_status = sub.add_parser("status")
    p_status.add_argument("--folder")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
