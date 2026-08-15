#!/usr/bin/env python3
"""Build a read-only, evidence-based genre folder/tag preview."""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


REPORT_DIR = Path(__file__).resolve().parent
INVENTORY = REPORT_DIR / "inventory.csv"
PLAN_PATH = REPORT_DIR / "genre-plan.json"
PREVIEW_PATH = REPORT_DIR / "GENRE-PREVIEW.md"
LIBRARY_ROOT = Path("/Users/djb.a.e/Music/DJ-Music/Music/music to move")
NEEDS = "_GENRE NEEDS CHECK"


SIMPLE_TAGS = {
    "hip-hop/rap": "Hip-Hop",
    "hip hop": "Hip-Hop",
    "rap/hip hop": "Hip-Hop",
    "hip-hop & rap": "Hip-Hop",
    "rap": "Hip-Hop",
    "hiphop": "Hip-Hop",
    "rap/hip-hop": "Hip-Hop",
    "gangsta rap, hip hop, southern hip hop, trap": "Hip-Hop",
    "r&b": "R&B",
    "r&b & soul": "R&B",
    "r&b/soul": "R&B",
    "pop": "Pop",
    "bounce": "Bounce",
    "dembow": "Dembow",
    "electronic": "Electronic",
    "miami bass": "Miami Bass",
    "miamibass": "Miami Bass",
    "juke": "Juke & Footwork",
    "classic funk-soul, old head party": "Funk & Soul",
}


def classify(row: dict[str, str], rel: Path) -> tuple[str | None, str, str]:
    """Return canonical genre, evidence, and confidence."""
    parts = rel.parts
    filename = row["filename"]
    genre = row["genre"].strip()
    lower_name = filename.casefold()

    # Named collections and functional folders are stronger than inconsistent tags.
    if len(parts) >= 3 and parts[0] == "ALBUMS & PROJECTS":
        project = parts[1]
        if project == "CRACK - A Lost Juke-Footwork Compilation":
            return "Juke & Footwork", "project name and album tag explicitly say Juke/Footwork", "high"
        if project == "Destin_Conrad-wHIMSY-WEB-2025-ENRiCH" and genre.casefold() == "r&b":
            return "R&B", "all project tracks carry the R&B tag", "high"
        if project == "Spend Dat" and genre.casefold() == "hip-hop/rap":
            return "Hip-Hop", "all project tracks carry the Hip-Hop/Rap tag", "high"

    if parts and parts[0] == "DJ TOOLS":
        return "DJ Tools", "existing DJ TOOLS/Scratch Beats placement", "high"

    # Explicit style words in filenames can correct generic or absent tags.
    if "ghettotech" in lower_name:
        return "Ghettotech", "filename explicitly says Ghettotech", "high"
    if "bmore" in lower_name:
        return "Baltimore Club", "filename explicitly says BMORE", "high"
    if "nola bounce" in lower_name or "bankhead bounce" in lower_name:
        return "Bounce", "filename explicitly names a Bounce style", "high"
    if "juke edit" in lower_name or "juke mix" in lower_name:
        return "Juke & Footwork", "filename explicitly says Juke", "high"
    if "atl bass" in lower_name:
        return "Miami Bass", "filename explicitly says ATL Bass; existing tag says Miami Bass", "high"

    canonical = SIMPLE_TAGS.get(genre.casefold())
    if canonical:
        return canonical, f"existing genre tag: {genre}", "tag"

    if not genre:
        return None, "no genre tag and no explicit genre evidence", "review"
    if genre.casefold() == "other":
        return None, "generic Other tag is not genre evidence", "review"
    return None, f"ambiguous or compound genre tag: {genre}", "review"


def destination(rel: Path, canonical: str | None) -> Path:
    parts = rel.parts
    folder = canonical or NEEDS
    if parts[0] == "ALBUMS & PROJECTS" and len(parts) >= 3:
        return Path(parts[0], folder, parts[1], *parts[2:])
    if parts[0] == "DJ TOOLS":
        # Scratch Beats is already the useful functional subdivision; avoid DJ TOOLS/DJ Tools.
        return rel
    return Path(parts[0], folder, *parts[1:])


def main() -> None:
    with INVENTORY.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    items = []
    source_missing = []
    destinations: defaultdict[str, list[str]] = defaultdict(list)
    genre_counts = Counter()
    folder_counts = Counter()
    tag_change_counts = Counter()

    for row in rows:
        source = Path(row["full_path"])
        try:
            rel = source.relative_to(LIBRARY_ROOT)
        except ValueError as exc:
            raise SystemExit(f"Inventory path escaped target root: {source}") from exc
        canonical, evidence, confidence = classify(row, rel)
        dest_rel = destination(rel, canonical)
        dest = LIBRARY_ROOT / dest_rel
        if not source.exists():
            source_missing.append(str(source))
        destinations[str(dest).casefold()].append(str(source))
        proposed_tag = canonical if canonical else None
        tag_change = bool(proposed_tag and proposed_tag != row["genre"])
        move = source != dest
        label = canonical or NEEDS
        genre_counts[label] += 1
        folder_counts[rel.parts[0]] += 1
        if tag_change:
            tag_change_counts[f"{row['genre'] or '(blank)'} -> {proposed_tag}"] += 1
        items.append({
            "source": str(source),
            "source_relative": str(rel),
            "destination": str(dest),
            "destination_relative": str(dest_rel),
            "current_genre": row["genre"],
            "proposed_genre": proposed_tag,
            "genre_folder": label,
            "evidence": evidence,
            "confidence": confidence,
            "move": move,
            "tag_change": tag_change,
            "sha256": row["sha256"],
        })

    collisions = {k: v for k, v in destinations.items() if len(v) > 1}
    existing_conflicts = []
    source_set = {item["source"] for item in items}
    for item in items:
        dest = Path(item["destination"])
        if item["move"] and dest.exists() and str(dest) not in source_set:
            existing_conflicts.append({"destination": str(dest), "source": item["source"]})

    plan = {
        "mode": "preview-only",
        "library_root": str(LIBRARY_ROOT),
        "inventory": str(INVENTORY),
        "track_count": len(items),
        "rules": {
            "resolved_tracks": "move under a canonical genre folder and write the identical canonical value to genre metadata",
            "unresolved_tracks": f"move under {NEEDS} and leave genre metadata unchanged",
            "dj_tools_exception": "retain DJ TOOLS/Scratch Beats paths and normalize their genre metadata to DJ Tools",
            "no_delete": True,
            "no_audio_change": True,
            "approval_required": True,
        },
        "validation": {
            "missing_sources": source_missing,
            "destination_collisions": collisions,
            "existing_destination_conflicts": existing_conflicts,
        },
        "counts": {
            "genres": dict(sorted(genre_counts.items())),
            "current_top_folders": dict(sorted(folder_counts.items())),
            "moves": sum(i["move"] for i in items),
            "genre_tag_changes": sum(i["tag_change"] for i in items),
            "genre_tags_unchanged": sum(not i["tag_change"] for i in items),
            "unresolved": genre_counts[NEEDS],
        },
        "tag_change_summary": dict(sorted(tag_change_counts.items())),
        "items": items,
    }
    PLAN_PATH.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    lines = [
        "# Genre folder + metadata preview",
        "",
        "**Preview only — no music files have been moved or retagged.**",
        "",
        f"Target: `{LIBRARY_ROOT}`",
        "",
        "## Proposed rule",
        "",
        "For resolved tracks, add a canonical genre folder beneath the existing version/category folder and write that exact same value into the file's Genre metadata. For unresolved tracks, use `_GENRE NEEDS CHECK` and leave the existing genre tag untouched.",
        "",
        "Examples:",
        "",
        "- `CLEAN/Pop/track.mp3` with Genre = `Pop`",
        "- `DIRTY/Hip-Hop/track.mp3` with Genre = `Hip-Hop`",
        "- `REMIXES & EDITS/Miami Bass/track.mp3` with Genre = `Miami Bass`",
        "- `ALBUMS & PROJECTS/Juke & Footwork/<project>/track.mp3` with Genre = `Juke & Footwork`",
        "- `_NEEDS CHECK/_GENRE NEEDS CHECK/track.mp3` with its genre tag unchanged",
        "",
        "`DJ TOOLS/Scratch Beats` stays structurally intact; its tracks get Genre = `DJ Tools` instead of a redundant `DJ TOOLS/DJ Tools` folder.",
        "",
        "## Counts",
        "",
        f"- Tracks checked: **{len(items)}**",
        f"- Proposed folder moves: **{plan['counts']['moves']}**",
        f"- Proposed Genre tag changes: **{plan['counts']['genre_tag_changes']}**",
        f"- Genre tag unchanged: **{plan['counts']['genre_tags_unchanged']}**",
        f"- Routed to `{NEEDS}`: **{plan['counts']['unresolved']}**",
        f"- Missing sources: **{len(source_missing)}**",
        f"- Destination collisions: **{len(collisions)}**",
        f"- Existing destination conflicts: **{len(existing_conflicts)}**",
        "",
        "## Proposed genre totals",
        "",
        "| Genre folder / tag | Tracks |",
        "|---|---:|",
    ]
    for genre, count in sorted(genre_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {genre} | {count} |")

    lines += ["", "## Metadata normalization", "", "| Current Genre | Proposed Genre | Tracks |", "|---|---|---:|"]
    for change, count in sorted(tag_change_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        old, new = change.split(" -> ", 1)
        lines.append(f"| {old} | {new} | {count} |")

    lines += [
        "",
        "## Needs genre review",
        "",
        "These are deliberately not guessed. Their Genre metadata will remain unchanged.",
        "",
        "| File | Current Genre | Why unresolved |",
        "|---|---|---|",
    ]
    for item in items:
        if item["proposed_genre"] is None:
            lines.append(f"| `{item['source_relative']}` | {item['current_genre'] or '—'} | {item['evidence']} |")

    lines += [
        "",
        "## Full proposed mapping",
        "",
        "| From | To | Genre metadata | Evidence |",
        "|---|---|---|---|",
    ]
    for item in items:
        proposed = item["proposed_genre"] or "unchanged"
        lines.append(
            f"| `{item['source_relative']}` | `{item['destination_relative']}` | {proposed} | {item['evidence']} |"
        )

    lines += [
        "",
        "## Apply safeguards (after approval only)",
        "",
        "- Create a fresh pre-genre backup and verify it.",
        "- Preserve each file's audio payload and Serato frames.",
        "- Preserve the original ID3 version while changing only the Genre field.",
        "- Re-check SHA/audio hashes and tag values after every write.",
        "- Write a complete move/tag undo log.",
        "- Delete nothing.",
        "",
    ]
    PREVIEW_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "preview": str(PREVIEW_PATH),
        "plan": str(PLAN_PATH),
        "counts": plan["counts"],
        "validation": {k: len(v) for k, v in plan["validation"].items()},
    }, indent=2))


if __name__ == "__main__":
    main()
