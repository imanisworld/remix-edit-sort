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
mutation scripts call before each apply -- renews `last_renewed_at`. So in
practice the window is "6 hours since this folder was last actually worked
on," a self-maintaining heartbeat, not a fixed deadline from initial
acquire. A session that genuinely goes quiet for the full window is fair
game for takeover; one that's actively running apply after apply never goes
stale mid-run. `touch` renews it directly for long stretches between apply
calls (e.g. while building a plan or researching evidence).

Takeovers are never silent: the new lock's note records who it was
reclaimed from and why.

Usage:
    folder_lock.py acquire --folder "Hip Hop" --session "claude-hiphop" [--agent claude] [--note "..."] [--run-id ...] [--repo-sha ...]
    folder_lock.py touch   --folder "Hip Hop" --session "claude-hiphop"      # renew without acquiring
    folder_lock.py release --folder "Hip Hop" --session "claude-hiphop" [--force]
    folder_lock.py check   --folder "Hip Hop"                                # is it free right now?
    folder_lock.py require --folder "Hip Hop" --session "claude-hiphop"      # do I actually hold it? (renews on success)
    folder_lock.py status  [--folder "Hip Hop"]
"""

from __future__ import annotations

import argparse
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
LOCKS_DIR = REPO_ROOT / ".locks"


def slugify(folder: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", folder.casefold()).strip("-")


def lock_path(folder: str) -> Path:
    return LOCKS_DIR / f"{slugify(folder)}.lock.json"


def read_lock(folder: str) -> dict | None:
    path = lock_path(folder)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_lock(folder: str, lock: dict) -> None:
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    lock_path(folder).write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")


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
    existing = read_lock(args.folder)
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
        # Provably abandoned by the time-based rule. Never a silent takeover
        # -- the new lock records exactly who it replaced and why.
        reclaimed_from = existing
    elif existing and existing.get("session") == args.session:
        # Re-acquiring our own lock (e.g. resuming work) -- treat as a renewal.
        pass

    lock = _new_lock_payload(args, reclaimed_from=reclaimed_from)
    write_lock(args.folder, lock)
    result = {"acquired": True, "lock": lock}
    if reclaimed_from is not None:
        result["reclaimed_from"] = reclaimed_from
    print(json.dumps(result, indent=2))
    return 0


def cmd_touch(args: argparse.Namespace) -> int:
    """Renew last_renewed_at on a lock we already own, without re-acquiring."""
    existing = read_lock(args.folder)
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
    existing = read_lock(args.folder)
    if not existing:
        print(json.dumps({"released": False, "reason": "no lock exists"}, indent=2))
        return 0
    if existing.get("session") != args.session and not args.force:
        print(
            json.dumps(
                {
                    "released": False,
                    "reason": "lock is held by a different session; pass --force to override",
                    "held_by": existing,
                },
                indent=2,
            )
        )
        return 1
    lock_path(args.folder).unlink(missing_ok=True)
    print(json.dumps({"released": True}, indent=2))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Is this folder free to acquire right now? (Does not require a lock to exist.)"""
    existing = read_lock(args.folder)
    if not existing:
        print(json.dumps({"locked": False}, indent=2))
        return 0
    staleness = evaluate_staleness(existing, args.stale_hours)
    print(json.dumps({"locked": not staleness["stale"], "staleness_check": staleness, "lock": existing}, indent=2))
    return 1 if (not staleness["stale"] and existing.get("session") != args.session) else 0


def cmd_require(args: argparse.Namespace) -> int:
    """Do I (this exact session) actually hold a live lock on this folder right now?

    Unlike `check`, this FAILS when no lock exists at all -- it's the gate
    mutation scripts call before touching files, so the absence of a lock is
    itself a failure, not an implicit green light. On success it renews the
    lock (see module docstring: this is what keeps an actively-working
    session's lock from going stale mid-run).
    """
    existing = read_lock(args.folder)
    if not existing:
        print(json.dumps({
            "held": False,
            "reason": "no lock exists for this folder -- run 'acquire' before applying",
        }, indent=2))
        return 1
    staleness = evaluate_staleness(existing, args.stale_hours)
    if staleness["stale"]:
        print(json.dumps({
            "held": False,
            "reason": f"lock exists but is stale: {staleness['reason']}",
            "lock": existing,
        }, indent=2))
        return 1
    if existing.get("session") != args.session:
        print(json.dumps({
            "held": False,
            "reason": f"lock is held by a different session ({existing.get('session')!r}), not {args.session!r}",
            "lock": existing,
        }, indent=2))
        return 1
    existing["last_renewed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    write_lock(args.folder, existing)
    print(json.dumps({"held": True, "lock": existing}, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    if not LOCKS_DIR.is_dir():
        print(json.dumps({"locks": []}, indent=2))
        return 0
    locks = []
    for path in sorted(LOCKS_DIR.glob("*.lock.json")):
        try:
            lock = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
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
    p_release.set_defaults(func=cmd_release)

    p_check = sub.add_parser("check")
    p_check.add_argument("--folder", required=True)
    p_check.add_argument("--session", default="")
    p_check.set_defaults(func=cmd_check)

    p_require = sub.add_parser("require")
    p_require.add_argument("--folder", required=True)
    p_require.add_argument("--session", required=True)
    p_require.set_defaults(func=cmd_require)

    p_status = sub.add_parser("status")
    p_status.add_argument("--folder")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
