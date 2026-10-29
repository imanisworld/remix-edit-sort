#!/usr/bin/env python3
"""PLAN ONLY: propose restoring Serato "date added" from older evidence.

Imani (2026-10-29): Serato shows 2026 for tracks added years ago. Investigation
showed no Serato copy holds a date before 2025-09-13 (library rebuild), and our
moves/renames re-added some tracks with a fresh date. This tool builds a
preview of what an older, evidence-backed date would be. It NEVER writes
anything except its own output files: master.sqlite is opened read-only and
immutable, and there is no apply mode.

Evidence, per live (non-missing) asset that resolves to a file on disk:
  * file creation (birth) time on disk -- survives same-volume moves/renames;
  * the exact date-added of the same path in older Serato DB copies
    (--old-db, e.g. the Sep 6 and Oct 18 master.sqlite snapshots).
A change is proposed only when the earliest evidence is more than
--min-gap-days (default 30) EARLIER than Serato's current date. A later date
is never proposed.

Path resolution (the earlier 13.5k "unresolved"): portable_id is relative to
its location's volume. Apple Music streams (streaming://), Serato effects
(":/..."), AudioUnits and Serato Studio content are not library files and are
skipped with a reason; location ids on external volumes are mapped with
--volume LOCATION_ID=/Volumes/NAME; NFC/NFD spellings are both tried.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "preview-only"

import argparse
import collections
import csv
import json
import os
import sqlite3
import unicodedata
import urllib.parse
from datetime import datetime
from pathlib import Path

DAY = 86400
BACKUPISH = ("/backup/", "/backups/", "/handoff/", "-backups/", "/_Serato_Backup/")


def ts(value) -> float:
    value = float(value or 0)
    return value / 1e6 if value > 1e12 else value


def iso(value: float | None) -> str:
    return datetime.fromtimestamp(value).isoformat(timespec="seconds") if value else ""


def open_ro(path: Path) -> sqlite3.Connection:
    uri = "file:" + urllib.parse.quote(str(path)) + "?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True)


def skip_reason(portable_id: str) -> str | None:
    if "://" in portable_id:
        return "streaming (no file)"
    if portable_id.startswith(":/") or portable_id.startswith("AudioUnit:"):
        return "Serato effect/plugin"
    if "/Music/Serato Studio/Content/" in portable_id:
        return "Serato Studio content"
    return None


def resolve(location_id: int, portable_id: str, volumes: dict[int, str]) -> Path | None:
    base = volumes.get(location_id, "/")
    for form in (portable_id, unicodedata.normalize("NFC", portable_id), unicodedata.normalize("NFD", portable_id)):
        candidate = Path(base) / form
        if candidate.is_file():
            return candidate
    return None


def propose(current: float, birth: float | None, old_dates: list[tuple[float, str]], min_gap_days: int = 30):
    """Return (proposed_ts, source) or (None, reason). Never later than current."""
    evidence = [(d, src) for d, src in old_dates if d]
    if birth:
        evidence.append((birth, "file creation date"))
    if not evidence:
        return None, "no evidence"
    best, source = min(evidence)
    if best >= current - min_gap_days * DAY:
        return None, "evidence not >%d days earlier" % min_gap_days
    return best, source


def caveats(path: Path, birth: float | None, mtime: float | None) -> list[str]:
    notes = []
    text = str(path)
    if any(marker in text for marker in BACKUPISH):
        notes.append("path is a backup/handoff copy")
    if birth and mtime and mtime < birth - DAY:
        notes.append("file was copied (modified before created): creation date is the copy date; true add date may be earlier")
    return notes


def load_old(paths: list[Path]) -> dict[str, list[tuple[float, str]]]:
    out: dict[str, list[tuple[float, str]]] = collections.defaultdict(list)
    for path in paths:
        con = open_ro(path)
        for pid, added in con.execute("select portable_id, time_added from asset"):
            if pid and added:
                out[pid].append((ts(added), f"Serato DB copy {path.parent.name or path}"))
    return out


def build(master: Path, old_dbs: list[Path], volumes: dict[int, str], min_gap_days: int):
    old = load_old(old_dbs)
    con = open_ro(master)
    rows, skipped = [], collections.Counter()
    for location_id, pid, added in con.execute(
        "select location_id, portable_id, time_added from asset where is_missing = 0"
    ):
        reason = skip_reason(pid or "")
        if reason:
            skipped[reason] += 1
            continue
        path = resolve(location_id, pid, volumes)
        if path is None:
            skipped["file not found on disk"] += 1
            continue
        stat = path.stat()
        birth = getattr(stat, "st_birthtime", None)
        current = ts(added)
        proposed, source = propose(current, birth, old.get(pid, []), min_gap_days)
        if proposed is None:
            skipped["no change: " + source] += 1
            continue
        rows.append({
            "path": str(path),
            "folder": "/".join(Path(pid).parts[5:7]) if pid.startswith("Users/") else str(Path(pid).parent),
            "current_date_added": iso(current),
            "proposed_date_added": iso(proposed),
            "source": source,
            "file_created": iso(birth),
            "file_modified": iso(stat.st_mtime),
            "days_earlier": int((current - proposed) // DAY),
            "caveats": "; ".join(caveats(path, birth, stat.st_mtime)),
        })
    return rows, skipped


def summarize(rows, skipped, min_gap_days) -> str:
    years_now = collections.Counter(r["current_date_added"][:4] for r in rows)
    years_new = collections.Counter(r["proposed_date_added"][:4] for r in rows)
    folders = collections.Counter(r["folder"] for r in rows).most_common(25)
    sources = collections.Counter(r["source"].split(" ")[0] + (" DB copy" if "DB" in r["source"] else "") for r in rows)
    cav = collections.Counter(c for r in rows for c in r["caveats"].split("; ") if c)
    lines = [
        "# Date-added restore PREVIEW (plan only, nothing applied)", "",
        f"Tracks with a proposed earlier date (>{min_gap_days} days earlier): **{len(rows)}**", "",
        "Sources: " + ", ".join(f"{k}: {v}" for k, v in sources.items()), "",
        "Current year -> proposed year:", "",
        "| year | current | proposed |", "|---|---|---|",
    ]
    for y in sorted(set(years_now) | set(years_new)):
        lines.append(f"| {y} | {years_now.get(y, 0)} | {years_new.get(y, 0)} |")
    if rows:
        lines += ["", f"Proposed range: {min(r['proposed_date_added'] for r in rows)[:10]} .. "
                      f"{max(r['proposed_date_added'] for r in rows)[:10]}"]
    lines += ["", "## Per folder", "", "| folder | tracks |", "|---|---|"]
    lines += [f"| {f} | {n} |" for f, n in folders]
    lines += ["", "## Not proposed", ""] + [f"- {k}: {v}" for k, v in skipped.most_common()]
    lines += ["", "## Caveats", ""] + [f"- {k}: {v}" for k, v in cav.most_common()]
    step = max(1, len(rows) // 10)
    lines += ["", "## 10 samples", "", "| track | current | proposed | source |", "|---|---|---|---|"]
    lines += [f"| {Path(r['path']).name} | {r['current_date_added'][:10]} | {r['proposed_date_added'][:10]} | {r['source']} |"
              for r in rows[::step][:10]]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--master", type=Path, required=True, help="live master.sqlite (opened read-only)")
    parser.add_argument("--old-db", type=Path, action="append", default=[], help="older master.sqlite copy (repeatable)")
    parser.add_argument("--volume", action="append", default=[], help="LOCATION_ID=/Volumes/NAME")
    parser.add_argument("--min-gap-days", type=int, default=30)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    volumes = {int(k): v for k, v in (item.split("=", 1) for item in args.volume)}
    rows, skipped = build(args.master, args.old_db, volumes, args.min_gap_days)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) if rows else ["path"]
    with (args.out_dir / "date-added-preview.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (args.out_dir / "date-added-summary.md").write_text(summarize(rows, skipped, args.min_gap_days), encoding="utf-8")
    (args.out_dir / "date-added-plan.json").write_text(json.dumps(
        {"mode": "preview-only", "apply_supported": False, "min_gap_days": args.min_gap_days,
         "items": rows, "skipped": dict(skipped)}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"proposed": len(rows), "skipped": dict(skipped), "out_dir": str(args.out_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
