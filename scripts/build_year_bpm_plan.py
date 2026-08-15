#!/usr/bin/env python3
"""Build a dry-run Year/BPM backfill plan from an existing inventory.csv.

Reuses the exact evidence rules from the original music-to-move
build_version_plan.py's safe_filename_metadata():
  - Year: only when the current Year tag is blank AND exactly one distinct
    four-digit 19xx/20xx year appears across the file's path components
    (relative to --root) and filename. Two different candidate years is
    ambiguous and is skipped, not guessed between.
  - BPM: only when the current BPM tag is blank AND an explicit `<number>
    bpm` token appears in the filename stem or Title tag.

mp3-only (TYER/TDRC/TBPM is an ID3 write), matching the write-path
precedent set by build_grouping_plan.py and build_artist_title_plan.py.
ID3v2.2 files are skipped, same precedent as Grouping.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError

YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
BPM_RE = re.compile(r"(?<!\d)(\d{2,3}(?:\.\d+)?)\s*bpm\b", re.I)


def id3_version(path: Path) -> int:
    try:
        return ID3(path).version[1]
    except ID3NoHeaderError:
        return 4


def propose_year(row: dict, root: Path) -> dict | None:
    if row["year"].strip():
        return None
    path = Path(row["full_path"])
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = Path(path.name)
    candidates = []
    for part in rel.parts:
        candidates.extend(YEAR_RE.findall(part))
    distinct = set(candidates)
    if len(distinct) != 1:
        return None
    return {"value": next(iter(distinct)), "evidence": "single explicit four-digit year in path/filename"}


def propose_bpm(row: dict) -> dict | None:
    if row["bpm"].strip():
        return None
    path = Path(row["full_path"])
    match = BPM_RE.search(f"{path.stem} {row.get('title', '')}")
    if not match:
        return None
    return {"value": match.group(1), "evidence": "explicit `BPM` token in filename/title"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path, help="folder root the paths are evaluated relative to")
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    root = args.root.resolve()

    with args.inventory.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    items = []
    skipped_not_mp3 = 0
    skipped_id3v22 = 0
    for row in rows:
        if row["file_type"] != "mp3":
            skipped_not_mp3 += 1
            continue
        year_proposal = propose_year(row, root)
        bpm_proposal = propose_bpm(row)
        if not year_proposal and not bpm_proposal:
            continue
        path = Path(row["full_path"])
        version = id3_version(path)
        if version == 2:
            skipped_id3v22 += 1
            continue
        items.append(
            {
                "path": str(path),
                "sha256": row["sha256"],
                "id3_version": version,
                "current_year": row["year"],
                "proposed_year": year_proposal["value"] if year_proposal else None,
                "year_evidence": year_proposal["evidence"] if year_proposal else None,
                "current_bpm": row["bpm"],
                "proposed_bpm": bpm_proposal["value"] if bpm_proposal else None,
                "bpm_evidence": bpm_proposal["evidence"] if bpm_proposal else None,
            }
        )

    plan = {
        "mode": "preview-only",
        "inventory": str(args.inventory),
        "root": str(root),
        "track_count": len(items),
        "rules": {
            "approval_required": True,
            "no_delete": True,
            "no_move": True,
            "unknowns": (
                "Never guess. Year requires exactly one distinct explicit four-digit "
                "19xx/20xx year across the path/filename; BPM requires an explicit "
                "`<number> bpm` token in the filename/title."
            ),
        },
        "skipped": {
            "not_mp3": skipped_not_mp3,
            "id3v2.2_preserved": skipped_id3v22,
        },
        "items": items,
    }
    plan_path = out_dir / "year-bpm-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# Year/BPM backfill preview",
        "",
        "**Preview only — no tags have been written.**",
        "",
        f"Tracks proposed: **{len(items)}**",
        f"Skipped (not mp3): **{skipped_not_mp3}**",
        f"Skipped (ID3v2.2 preserved): **{skipped_id3v22}**",
        "",
        "| File | Proposed Year | Proposed BPM |",
        "|---|---|---|",
    ]
    for item in items:
        lines.append(
            f"| `{Path(item['path']).name}` | {item['proposed_year'] or '—'} | {item['proposed_bpm'] or '—'} |"
        )
    (out_dir / "YEAR-BPM-PREVIEW.md").write_text("\n".join(lines), encoding="utf-8")

    print(
        json.dumps(
            {
                "plan": str(plan_path),
                "tracks_proposed": len(items),
                "skipped_not_mp3": skipped_not_mp3,
                "skipped_id3v22": skipped_id3v22,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
