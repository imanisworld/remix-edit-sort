#!/usr/bin/env python3
"""Build a dry-run Artist/Title backfill plan from an inventory.csv.

Reuses the exact evidence rule from the original music-to-move
build_version_plan.py's safe_filename_metadata(): only propose Artist/Title
when BOTH are currently blank and the filename (after stripping a trailing
copy-suffix and a leading track-number prefix) splits cleanly on a single
" - " into two non-empty halves. Never propose a value when only one of
Artist/Title is blank and the other already has content, and never propose
anything from a filename with zero or multiple " - " separators (ambiguous).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError, TPE1, TIT2

TRACK_PREFIX_RE = re.compile(r"^\s*\d{1,3}(?:[-_.]|\s)+")
COPY_SUFFIX_RE = re.compile(r"\s\((\d+)\)$")


def propose(row: dict) -> dict | None:
    if row["file_type"] != "mp3":
        return None
    artist, title = row["artist"].strip(), row["title"].strip()
    if artist or title:
        return None  # only fill when BOTH are blank -- never guess one alongside a trusted other
    stem = COPY_SUFFIX_RE.sub("", Path(row["filename"]).stem).strip()
    stem_without_track = TRACK_PREFIX_RE.sub("", stem).strip()
    if stem_without_track.count(" - ") != 1:
        return None  # zero or ambiguous multiple separators
    left, right = stem_without_track.split(" - ", 1)
    left, right = left.strip(), right.strip()
    if not left or not right:
        return None
    return {"artist": left, "title": right, "evidence": "strict `Artist - Title` filename split"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    with args.inventory.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    items = []
    for row in rows:
        proposal = propose(row)
        if not proposal:
            continue
        path = Path(row["full_path"])
        items.append(
            {
                "path": str(path),
                "sha256": row["sha256"],
                "current_artist": row["artist"],
                "current_title": row["title"],
                "proposed_artist": proposal["artist"],
                "proposed_title": proposal["title"],
                "evidence": proposal["evidence"],
            }
        )

    plan = {
        "mode": "preview-only",
        "inventory": str(args.inventory),
        "track_count": len(items),
        "rules": {
            "approval_required": True,
            "no_delete": True,
            "no_move": True,
            "only_when_both_blank": True,
            "unknowns": "Never guess. Only a strict, unambiguous `Artist - Title` filename split qualifies.",
        },
        "items": items,
    }
    plan_path = out_dir / "artist-title-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# Artist/Title backfill preview",
        "",
        "**Preview only — no tags have been written.**",
        "",
        f"Tracks proposed: **{len(items)}**",
        "",
        "| File | Proposed Artist | Proposed Title |",
        "|---|---|---|",
    ]
    for item in items:
        lines.append(f"| `{Path(item['path']).name}` | {item['proposed_artist']} | {item['proposed_title']} |")
    (out_dir / "ARTIST-TITLE-PREVIEW.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps({"plan": str(plan_path), "tracks_proposed": len(items)}, indent=2))


if __name__ == "__main__":
    main()
