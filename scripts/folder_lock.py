#!/usr/bin/env python3
"""Lightweight coordination lock so concurrent agent sessions don't collide
on the same DJ-library folder.

This session discovered, the hard way, that multiple agents (this Claude
session, a Codex session, and another Claude session) were writing to the
same repo and the same Music library at the same time with zero
coordination -- including one folder (NewFiles) getting merged into another
(music to move) mid-run by a different session, silently, while this session
still had stale plans referencing its old paths. This tool doesn't prevent
that on its own (nothing forces another agent to check it), but it gives any
cooperating session -- and any human glancing at .locks/ -- a fast, honest
answer to "is someone already working on this folder?" before it starts.

Locks live in .locks/ at the repo root (gitignored -- they're ephemeral,
session-local state, not history) as one JSON file per folder:

    {
      "folder": "Hip Hop",
      "session": "claude-hiphop-metadata",
      "started_at": "2026-08-15T19:10:00-04:00",
      "pid": 12345,
      "note": "Grouping backfill"
    }

A lock older than --stale-hours (default 6) is treated as abandoned and can
be taken over -- crashed sessions shouldn't permanently block a folder.

Usage:
    folder_lock.py acquire --folder "Hip Hop" --session "claude-hiphop" [--note "..."]
    folder_lock.py release --folder "Hip Hop" --session "claude-hiphop" [--force]
    folder_lock.py check   --folder "Hip Hop"
    folder_lock.py status  [--folder "Hip Hop"]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

LOCKS_DIR = Path(__file__).resolve().parent.parent / ".locks"


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


def is_stale(lock: dict, stale_hours: float) -> bool:
    try:
        started = datetime.fromisoformat(lock["started_at"])
    except (KeyError, ValueError):
        return True
    now = started.__class__.now(started.tzinfo) if started.tzinfo else datetime.now()
    return now - started > timedelta(hours=stale_hours)


def cmd_acquire(args: argparse.Namespace) -> int:
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)
    existing = read_lock(args.folder)
    if existing and existing.get("session") != args.session and not is_stale(existing, args.stale_hours):
        print(
            json.dumps(
                {
                    "acquired": False,
                    "reason": "already locked by another session",
                    "held_by": existing,
                },
                indent=2,
            )
        )
        return 1
    lock = {
        "folder": args.folder,
        "session": args.session,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "pid": os.getpid(),
        "note": args.note or "",
    }
    lock_path(args.folder).write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"acquired": True, "lock": lock}, indent=2))
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
    existing = read_lock(args.folder)
    if not existing:
        print(json.dumps({"locked": False}, indent=2))
        return 0
    stale = is_stale(existing, args.stale_hours)
    print(json.dumps({"locked": not stale, "stale": stale, "lock": existing}, indent=2))
    return 1 if (not stale and existing.get("session") != args.session) else 0


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
        lock["stale"] = is_stale(lock, args.stale_hours)
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
    p_acquire.add_argument("--note")
    p_acquire.set_defaults(func=cmd_acquire)

    p_release = sub.add_parser("release")
    p_release.add_argument("--folder", required=True)
    p_release.add_argument("--session", required=True)
    p_release.add_argument("--force", action="store_true")
    p_release.set_defaults(func=cmd_release)

    p_check = sub.add_parser("check")
    p_check.add_argument("--folder", required=True)
    p_check.add_argument("--session", default="")
    p_check.set_defaults(func=cmd_check)

    p_status = sub.add_parser("status")
    p_status.add_argument("--folder")
    p_status.set_defaults(func=cmd_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
