#!/usr/bin/env python3
"""One-off: merge 9 duplicate tracks living in two junk-named Events folders
("August 30th Random DL" and "trendindg august 2025") into the real Hip Hop /
R&b genre folders (a single copy each, matching classify_version()'s
evidence-based CLEAN/DIRTY/INSTRUMENTALS bucket). The duplicate left behind
in the other junk folder is intentionally NOT deleted here -- only the move
was approved; deletion of the leftover copy is a separate decision.

Every pair was independently verified before this script was written:
audio-payload SHA-256 and Serato GEOB frame set/sizes match exactly between
both copies, so which physical copy moves is arbitrary and no Serato
performance data is at risk either way.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

import lock_guard

ROOT = Path("/Users/djb.a.e/Music/DJ-Music/Music")
OUT_DIR = Path(__file__).resolve().parent.parent / "handoff/session-2026-08-15/survey/events-dedup-merge"

PAIRS = [
    ("Events/trendindg august 2025/CLEAN/Hip-Hop/Metro Boomin, Roscoe Dash & Quavo - Butterflies (Right Now) (Clean).mp3",
     "Hip Hop/Clean/Hip-Hop/Metro Boomin, Roscoe Dash & Quavo - Butterflies (Right Now) (Clean).mp3"),
    ("Events/trendindg august 2025/DIRTY/Hip-Hop/Metro Boomin, Roscoe Dash & Quavo - Butterflies (Right Now) (Intro Dirty).mp3",
     "Hip Hop/DIRTY/Hip-Hop/Metro Boomin, Roscoe Dash & Quavo - Butterflies (Right Now) (Intro Dirty).mp3"),
    ("Events/trendindg august 2025/DIRTY/Hip-Hop/Saweetie - pressure (Intro Dirty).mp3",
     "Hip Hop/DIRTY/Hip-Hop/Saweetie - pressure (Intro Dirty).mp3"),
    ("Events/trendindg august 2025/CLEAN/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Clean).mp3",
     "R&b/CLEAN/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Clean).mp3"),
    ("Events/trendindg august 2025/CLEAN/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Intro Clean).mp3",
     "R&b/CLEAN/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Intro Clean).mp3"),
    ("Events/trendindg august 2025/DIRTY/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Dirty).mp3",
     "R&b/DIRTY/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Dirty).mp3"),
    ("Events/trendindg august 2025/DIRTY/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Intro Dirty).mp3",
     "R&b/DIRTY/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Intro Dirty).mp3"),
    ("Events/trendindg august 2025/INSTRUMENTALS/R&B/INSTRUMENTALS/Al Green - Love And Happiness (Instrumental).mp3",
     "R&b/INSTRUMENTALS/R&B/Al Green - Love And Happiness (Instrumental).mp3"),
    ("Events/trendindg august 2025/INSTRUMENTALS/R&B/INSTRUMENTALS/Ciara ft Latto & Jazze Pha - This Right Here (Instrumental).mp3",
     "R&b/INSTRUMENTALS/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Instrumental).mp3"),
]

LEFTOVER_DUPLICATES = [
    "Events/August 30th Random DL/CLEAN/Hip-Hop/Metro Boomin, Roscoe Dash & Quavo - Butterflies (Right Now) (Clean).mp3",
    "Events/August 30th Random DL/DIRTY/Hip-Hop/Metro Boomin, Roscoe Dash & Quavo - Butterflies (Right Now) (Intro Dirty).mp3",
    "Events/August 30th Random DL/DIRTY/Hip-Hop/Saweetie - pressure (Intro Dirty).mp3",
    "Events/August 30th Random DL/CLEAN/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Clean).mp3",
    "Events/August 30th Random DL/CLEAN/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Intro Clean).mp3",
    "Events/August 30th Random DL/DIRTY/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Dirty).mp3",
    "Events/August 30th Random DL/DIRTY/R&B/Ciara ft Latto & Jazze Pha - This Right Here (Intro Dirty).mp3",
    "Events/August 30th Random DL/DJ TOOLS/R&B/INSTRUMENTALS/Al Green - Love And Happiness (Instrumental).mp3",
    "Events/August 30th Random DL/DJ TOOLS/R&B/INSTRUMENTALS/Ciara ft Latto & Jazze Pha - This Right Here (Instrumental).mp3",
]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    for folder in ("Events", "Hip Hop", "R&b"):
        lock_guard.require_lock_or_exit(folder, "claude-metadata-pass")

    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    backup_root = OUT_DIR / f"backup-{stamp}"
    undo_path = OUT_DIR / f"undo-{stamp}.jsonl"
    result_path = OUT_DIR / f"result-{stamp}.json"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    errors = []
    for src_rel, dest_rel in PAIRS:
        src = ROOT / src_rel
        dest = ROOT / dest_rel
        if not src.is_file():
            errors.append(f"missing source: {src}")
        if dest.exists():
            errors.append(f"destination already exists: {dest}")
        if not dest.parent.is_dir():
            errors.append(f"destination parent does not exist: {dest.parent}")
    if errors:
        raise RuntimeError("Pre-flight validation failed:\n" + "\n".join(errors))

    completed = []
    for index, (src_rel, dest_rel) in enumerate(PAIRS, start=1):
        src = ROOT / src_rel
        dest = ROOT / dest_rel
        before_hash = sha256_file(src)

        backup = backup_root / src_rel
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, backup)
        if sha256_file(backup) != before_hash:
            raise RuntimeError(f"backup verification failed: {src}")

        dest.parent.mkdir(parents=True, exist_ok=True)
        os.rename(src, dest)

        after_hash = sha256_file(dest)
        if after_hash != before_hash:
            raise RuntimeError(f"post-move hash mismatch: {dest}")

        record = {
            "index": index,
            "source": str(src),
            "destination": str(dest),
            "sha256": before_hash,
            "backup": str(backup),
        }
        with undo_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        completed.append(record)
        print(f"[{index}/9] moved: {src_rel} -> {dest_rel}")

    result = {
        "mode": "events-dedup-merge",
        "tracks_moved": len(completed),
        "undo_log": str(undo_path),
        "backup_root": str(backup_root),
        "leftover_duplicates_not_deleted": LEFTOVER_DUPLICATES,
        "records": completed,
    }
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"tracks_moved": len(completed), "result_path": str(result_path)}, indent=2))


if __name__ == "__main__":
    main()
