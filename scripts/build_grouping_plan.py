#!/usr/bin/env python3
"""Build a dry-run Grouping (TIT1) backfill plan from a folder's move history.

Reads the undo log(s) written by scripts/apply_version_genre_plan.py (or an
older one-off apply_<folder>_plan.py) for a folder that's already been sorted,
and reconstructs each track's Grouping evidence from where it originally
lived — before its version/genre move — without re-touching that move.

Two evidence modes:
  --mode flat       every moved track gets the same fixed label, for a folder
                     that IS one meaningful collection (e.g. RAP RN, Rave).
  --mode subfolder  the label comes from each track's own original top-level
                     subfolder name (e.g. Hip Hop's chicago_drill, NewFiles'
                     SoundCloud). A track that sat loose directly under a
                     generic parent (no subfolder) gets no Grouping proposed
                     — never invent evidence for a file that didn't carry any.

Only mp3 is supported (TIT1/Grouping write path), matching the precedent set
by apply_events_grouping.py and apply_nigeria_plan.py. ID3v2.2 files are
skipped, matching the Genre-write precedent elsewhere in this project.
Existing Grouping values are always preserved and appended to, never
overwritten — same rule as everywhere else in this project.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError


def read_grouping(path: Path) -> tuple[list[str], int]:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return [], 4
    values = []
    for frame in tags.getall("TIT1"):
        text = getattr(frame, "text", [])
        values.extend(str(value).strip() for value in text if str(value).strip())
    return values, tags.version[1]


def combined_grouping(existing: list[str], proposed: str) -> str:
    values, seen = [], set()
    for value in [*existing, proposed]:
        normalized = value.strip().casefold()
        if normalized and normalized not in seen:
            values.append(value.strip())
            seen.add(normalized)
    return "; ".join(values)


def load_moves(undo_logs: list[Path]) -> dict[str, str]:
    """original source path -> current (post-move) destination path, across one or more undo logs."""
    moves: dict[str, str] = {}
    for log in undo_logs:
        for line in log.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if record.get("moved"):
                moves[record["source"]] = record["destination"]
    return moves


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--undo-log", action="append", required=True, type=Path, dest="undo_logs")
    parser.add_argument("--original-root", required=True, type=Path, help="the folder's root BEFORE its version/genre sort")
    parser.add_argument("--mode", choices=["flat", "subfolder"], required=True)
    parser.add_argument("--label", help="required for --mode flat: the fixed label, e.g. 'RAP RN' or 'Wedding'")
    parser.add_argument("--label-prefix", default="Source", choices=["Source", "Event"])
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()

    if args.mode == "flat" and not args.label:
        raise SystemExit("--label is required for --mode flat")

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    original_root = args.original_root.resolve()

    moves = load_moves(args.undo_logs)
    items = []
    skipped_counts: collections.Counter = collections.Counter()

    for source, destination in sorted(moves.items()):
        dest_path = Path(destination)
        if not dest_path.is_file():
            skipped_counts["destination no longer exists"] += 1
            continue
        if dest_path.suffix.casefold() != ".mp3":
            skipped_counts["non-mp3 (Grouping writer only supports mp3)"] += 1
            continue

        if args.mode == "flat":
            proposed_label = args.label
            evidence = f"whole folder is one source collection: {args.label}"
        else:
            try:
                rel = Path(source).resolve().relative_to(original_root)
            except ValueError:
                skipped_counts["source path outside --original-root"] += 1
                continue
            if len(rel.parts) <= 1:
                skipped_counts["sat loose at the generic root; no subfolder evidence"] += 1
                continue
            proposed_label = rel.parts[0]
            evidence = f"original subfolder: {proposed_label}"

        proposed_grouping = f"{args.label_prefix}: {proposed_label}"
        current_values, id3_version = read_grouping(dest_path)
        if id3_version == 2:
            skipped_counts["ID3v2.2 preserved"] += 1
            continue
        if any(v.strip().casefold() == proposed_grouping.casefold() for v in current_values):
            skipped_counts["already has this exact Grouping value"] += 1
            continue

        items.append(
            {
                "path": str(dest_path),
                "original_source": source,
                "current_grouping": current_values,
                "proposed_grouping": proposed_grouping,
                "combined_grouping": combined_grouping(current_values, proposed_grouping),
                "id3_version": id3_version,
                "evidence": evidence,
            }
        )

    plan = {
        "mode": "preview-only",
        "grouping_mode": args.mode,
        "original_root": str(original_root),
        "track_count": len(items),
        "rules": {
            "approval_required": True,
            "no_delete": True,
            "no_move": True,
            "preserve_existing_grouping": "append and dedupe, never overwrite",
        },
        "skipped": dict(sorted(skipped_counts.items())),
        "items": items,
    }
    plan_path = out_dir / "grouping-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# Grouping backfill preview",
        "",
        "**Preview only — no tags have been written.**",
        "",
        f"Mode: `{args.mode}`" + (f" (label: `{args.label}`)" if args.mode == "flat" else f" (original root: `{original_root}`)"),
        "",
        f"Tracks proposed for a Grouping change: **{len(items)}**",
        "",
        "## Skipped",
        "",
    ]
    for reason, count in sorted(skipped_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {reason}: **{count}**")
    lines.extend(["", "## Proposed changes", "", "| File | Current Grouping | New Grouping | Evidence |", "|---|---|---|---|"])
    for item in items:
        current = " ; ".join(item["current_grouping"]) or "—"
        lines.append(f"| `{Path(item['path']).name}` | {current} | {item['combined_grouping']} | {item['evidence']} |")
    (out_dir / "GROUPING-PREVIEW.md").write_text("\n".join(lines), encoding="utf-8")

    print(
        json.dumps(
            {
                "plan": str(plan_path),
                "preview": str(out_dir / "GROUPING-PREVIEW.md"),
                "tracks_proposed": len(items),
                "skipped": dict(skipped_counts),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
