#!/usr/bin/env python3
"""Build a dry-run version-folder and metadata plan for `music to move`.

This script writes reports only. It never edits or moves music files.
"""

from __future__ import annotations

import collections
import csv
import json
import re
from pathlib import Path


ROOT = Path("/Users/djb.a.e/Music/DJ-Music/Music/music to move")
HERE = Path(__file__).resolve().parent
PROJECT_FOLDERS = {
    "CRACK - A Lost Juke-Footwork Compilation",
    "Destin_Conrad-wHIMSY-WEB-2025-ENRiCH",
    "Spend Dat",
}

TRACK_PREFIX_RE = re.compile(r"^\s*\d{1,3}(?:[-_.]|\s)+")
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
BPM_RE = re.compile(r"(?<!\d)(\d{2,3}(?:\.\d+)?)\s*bpm\b", re.I)
COPY_SUFFIX_RE = re.compile(r"\s\((\d+)\)$")


def first_component(path: Path) -> str:
    rel = path.relative_to(ROOT)
    return rel.parts[0] if len(rel.parts) > 1 else "."


def has_word(text: str, pattern: str) -> bool:
    return bool(re.search(r"(^|[^a-z0-9])(?:" + pattern + r")([^a-z0-9]|$)", text, re.I))


def choose_bucket(row: dict[str, str]) -> tuple[str, str]:
    path = Path(row["full_path"])
    source = first_component(path)
    if source in PROJECT_FOLDERS:
        rel_parent = path.parent.relative_to(ROOT / source)
        base = Path("ALBUMS & PROJECTS") / source
        return str(base / rel_parent), f"preserve project folder `{source}`"
    if source == "Scratch Beats":
        rel_parent = path.parent.relative_to(ROOT / source)
        return str(Path("DJ TOOLS") / "Scratch Beats" / rel_parent), "existing Scratch Beats folder"

    # Version labels must be syntactically explicit. A plain word match is not
    # enough: `Talking Dirty` is a title, not proof of a dirty DJ version.
    text = " ".join((path.stem, row.get("title", ""))).casefold()
    instrumental = re.search(r"[\[(][^\])]*\binstrumental\b[^\])]*[\])]", text, re.I)
    acapella = re.search(r"[\[(][^\])]*\b(?:acapella|a\s*cappella)\b[^\])]*[\])]", text, re.I)
    clean = re.search(r"[\[(][^\])]*\bclean\b[^\])]*[\])]", text, re.I)
    dirty = re.search(r"[\[(][^\])]*\bdirty\b[^\])]*[\])]", text, re.I)
    explicit_edit = re.search(r"\bexplicit\s+(?:dj\s+)?edit\b", text, re.I)
    if instrumental:
        return "INSTRUMENTALS", "explicit instrumental label"
    if acapella:
        return "ACAPELLAS", "explicit acapella label"
    if clean:
        return "CLEAN", "explicit clean label"
    if dirty or explicit_edit:
        return "DIRTY", "explicit dirty/explicit label"
    if has_word(text, r"remix|re[- ]?mix|rmx|edit|re[- ]?edit|refix|flip|bootleg|mashup|blend|re[- ]?grid|kingmix"):
        return "REMIXES & EDITS", "explicit remix/edit label"
    return "_NEEDS CHECK", "no proven version label"


def safe_filename_metadata(row: dict[str, str]) -> dict[str, object]:
    path = Path(row["full_path"])
    stem = COPY_SUFFIX_RE.sub("", path.stem).strip()
    stem_without_track = TRACK_PREFIX_RE.sub("", stem).strip()
    proposals: dict[str, object] = {}

    if (not row["artist"].strip() or not row["title"].strip()) and " - " in stem_without_track:
        left, right = stem_without_track.split(" - ", 1)
        if left.strip() and right.strip():
            if not row["artist"].strip():
                proposals["artist"] = {
                    "value": left.strip(),
                    "evidence": "strict `Artist - Title` filename split",
                    "confidence": "review",
                }
            if not row["title"].strip():
                proposals["title"] = {
                    "value": right.strip(),
                    "evidence": "strict `Artist - Title` filename split",
                    "confidence": "review",
                }

    if not row["year"].strip():
        candidates = []
        for part in path.relative_to(ROOT).parts:
            candidates.extend(YEAR_RE.findall(part))
        if len(set(candidates)) == 1:
            proposals["year"] = {
                "value": candidates[0],
                "evidence": "single explicit four-digit year in path/filename",
                "confidence": "review",
            }

    if not row["bpm"].strip():
        bpm = BPM_RE.search(f"{path.stem} {row.get('title', '')}")
        if bpm:
            proposals["bpm"] = {
                "value": bpm.group(1),
                "evidence": "explicit `BPM` token in filename/title",
                "confidence": "review",
            }
    return proposals


def main() -> None:
    with (HERE / "inventory.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    items = []
    target_keys: dict[str, list[str]] = collections.defaultdict(list)
    bucket_counts = collections.Counter()
    for row in rows:
        source = Path(row["full_path"])
        bucket, reason = choose_bucket(row)
        target = ROOT / bucket / source.name
        item = {
            "source": str(source),
            "proposed_destination": str(target),
            "bucket": bucket,
            "bucket_evidence": reason,
            "proposed_metadata": safe_filename_metadata(row),
            "current": {
                "artist": row["artist"],
                "title": row["title"],
                "album": row["album"],
                "genre": row["genre"],
                "year": row["year"],
                "bpm": row["bpm"],
                "key": row["key"],
                "sha256": row["sha256"],
            },
            "flags": [],
        }
        if bucket == "_NEEDS CHECK":
            item["flags"].append("unknown_version")
        if row["trailing_copy_suffix"] == "yes":
            item["flags"].append("terminal_number_suffix_review")
        missing = [field for field in ("artist", "title", "genre", "year", "bpm", "key") if not row[field].strip()]
        if missing:
            item["flags"].append("missing:" + ",".join(missing))
        items.append(item)
        bucket_counts[bucket.split("/")[0]] += 1
        target_keys[str(target).casefold()].append(str(source))

    collisions = [
        {"destination": destination, "sources": sources}
        for destination, sources in target_keys.items()
        if len(sources) > 1
    ]
    plan = {
        "scope": str(ROOT),
        "mode": "dry-run preview only",
        "rules": {
            "priority": [
                "preserve existing album/project sets",
                "preserve Scratch Beats as DJ tools",
                "Instrumental",
                "Acapella",
                "Clean",
                "Dirty/Explicit",
                "Remix/Edit/Flip",
                "_NEEDS CHECK",
            ],
            "unknowns": "Never guess. Route to `_NEEDS CHECK`.",
            "numbers": "Preserve meaningful numbers. A terminal `(N)` is review-only and never duplicate proof.",
            "metadata": "All proposed missing values are review-only; none are approved for writing.",
        },
        "bucket_counts": dict(bucket_counts),
        "destination_collisions": collisions,
        "items": items,
    }
    (HERE / "version-folder-plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n")

    needs_check = [item for item in items if item["bucket"] == "_NEEDS CHECK"]
    metadata = [item for item in items if item["proposed_metadata"]]
    flagged = [item for item in items if item["flags"]]
    lines = [
        "# `music to move` — version-folder preview",
        "",
        "> DRY RUN ONLY. Nothing has been moved, renamed, or retagged.",
        "",
        "## Proposed top-level folders",
        "",
    ]
    for bucket, count in bucket_counts.most_common():
        lines.append(f"- `{bucket}`: **{count}** tracks")
    lines.extend([
        "",
        f"Destination collisions: **{len(collisions)}**",
        f"Metadata proposals requiring review: **{len(metadata)}**",
        f"Tracks carrying any review flag: **{len(flagged)}**",
        "",
        "## `_NEEDS CHECK`",
        "",
    ])
    for item in needs_check:
        lines.append(f"- `{Path(item['source']).relative_to(ROOT)}`")
    lines.extend(["", "## Proposed metadata additions", ""])
    for item in metadata:
        changes = "; ".join(
            f"{field} → `{detail['value']}` ({detail['evidence']})"
            for field, detail in item["proposed_metadata"].items()
        )
        lines.append(f"- `{Path(item['source']).relative_to(ROOT)}`: {changes}")
    lines.extend(["", "## Destination collisions", ""])
    if collisions:
        for collision in collisions:
            lines.append(f"- `{collision['destination']}`")
            for source in collision["sources"]:
                lines.append(f"  - from `{source}`")
    else:
        lines.append("None.")
    lines.extend([
        "",
        "## Approval boundary",
        "",
        "This preview does not authorize moving files or writing metadata. Review `_NEEDS CHECK`, metadata proposals, and collisions first.",
        "",
    ])
    (HERE / "VERSION-FOLDER-PREVIEW.md").write_text("\n".join(lines))
    print(json.dumps({
        "tracks": len(items),
        "bucket_counts": dict(bucket_counts),
        "needs_check": len(needs_check),
        "metadata_proposals": len(metadata),
        "collisions": len(collisions),
    }, indent=2))


if __name__ == "__main__":
    main()
