#!/usr/bin/env python3
"""Build a dry-run, evidence-based version + genre plan for one source folder.

Generalizes the two-stage music-to-move builder (build_version_plan.py +
build_genre_plan.py) into the single-pass shape every later folder's plan.json
actually used (see e.g. handoff/session-2026-08-15/survey/dub/dub-plan.json):
one item list carrying both version_category and proposed_genre, destination
= <root>/<version_category>/<genre_folder>/[preserved-subpath/]filename.

Writes reports only. Never moves, renames, or retags a file.
"""

from __future__ import annotations

# Preview-only: builds a plan for human review. Never mutates library media.
SAFETY_CLASSIFICATION = "preview-only"

import argparse
import collections
import csv
import json
import os
import re
from pathlib import Path

import mutation_engine

NEEDS_VERSION = "_NEEDS CHECK"
NEEDS_GENRE = "_GENRE NEEDS CHECK"
VERSION_CATEGORIES = {"CLEAN", "DIRTY", "INSTRUMENTALS", "ACAPELLAS", "REMIXES & EDITS", NEEDS_VERSION}
# apply_version_genre_plan.py's Genre reader/writer only supports these formats.
WRITABLE_GENRE_FORMATS = {".mp3", ".flac", ".m4a"}

# Version labels must be syntactically explicit. A plain word match is not
# enough: "Talking Dirty" is a title, not proof of a dirty DJ version.
_BRACKETED = lambda word: re.compile(r"[\[(][^\])]*\b(?:" + word + r")\b[^\])]*[\])]", re.I)
INSTRUMENTAL_RE = _BRACKETED(r"instrumental")
ACAPELLA_RE = _BRACKETED(r"acapella|a\s*cappella")
CLEAN_RE = _BRACKETED(r"clean")
DIRTY_RE = _BRACKETED(r"dirty")
EXPLICIT_EDIT_RE = re.compile(r"\bexplicit\s+(?:dj\s+)?edit\b", re.I)
REMIX_EDIT_RE = re.compile(
    r"(^|[^a-z0-9])(?:remix|re[- ]?mix|rmx|edit|re[- ]?edit|refix|flip|bootleg|mashup|blend|re[- ]?grid|kingmix)([^a-z0-9]|$)",
    re.I,
)


def classify_version(stem: str, title: str) -> tuple[str, str]:
    text = " ".join((stem, title)).casefold()
    if INSTRUMENTAL_RE.search(text):
        return "INSTRUMENTALS", "explicit instrumental label"
    if ACAPELLA_RE.search(text):
        return "ACAPELLAS", "explicit acapella label"
    if CLEAN_RE.search(text):
        return "CLEAN", "explicit clean label"
    if DIRTY_RE.search(text) or EXPLICIT_EDIT_RE.search(text):
        return "DIRTY", "explicit dirty/explicit label"
    if REMIX_EDIT_RE.search(text):
        return "REMIXES & EDITS", "explicit remix/edit label"
    return NEEDS_VERSION, "no proven version label"


def load_genre_rules(path: Path | None) -> dict:
    default_path = Path(__file__).resolve().parent.parent / "references" / "version-genre-rules.json"
    target = path or default_path
    if not target.is_file():
        return {"tag_aliases": {}, "filename_keywords": []}
    return json.loads(target.read_text(encoding="utf-8"))


def classify_genre(row: dict, rules: dict, assume_genre: str | None) -> tuple[str | None, str, str]:
    if assume_genre:
        return assume_genre, f"source folder is a confirmed single genre: {assume_genre}", "assumed"

    lower_name = row["filename"].casefold()
    for rule in rules.get("filename_keywords", []):
        if rule["contains"] in lower_name:
            return rule["genre"], rule["evidence"], "high"

    genre = row["genre"].strip()
    canonical = rules.get("tag_aliases", {}).get(genre.casefold())
    if canonical:
        return canonical, f"existing genre tag: {genre}", "tag"

    if not genre:
        return None, "no genre tag and no explicit genre evidence", "review"
    if genre.casefold() == "other":
        return None, "generic Other tag is not genre evidence", "review"
    return None, f"ambiguous or compound genre tag: {genre}", "review"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="Source folder these paths are relative to")
    parser.add_argument("--inventory", required=True, type=Path, help="inventory.csv from inventory_library.py")
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--assume-genre",
        help="Use this exact genre for every track (only for a folder you've confirmed is a single genre, "
        "e.g. --assume-genre Dub for a folder named Dub). Omit to classify per-track from tags/filenames.",
    )
    parser.add_argument(
        "--assume-version-fallback",
        choices=["CLEAN", "DIRTY", "INSTRUMENTALS", "ACAPELLAS"],
        help="Only for a folder you've confirmed is dedicated to one version type (e.g. an Acapella crate). "
        "Unlike --assume-genre, this is a FALLBACK, not an override: it only fills in items classify_version() "
        "left at _NEEDS CHECK (no explicit bracketed marker found), and never touches an item that already "
        "resolved to CLEAN/DIRTY/INSTRUMENTALS/ACAPELLAS/REMIXES & EDITS from real filename evidence.",
    )
    parser.add_argument(
        "--assume-genre-fallback",
        help="Only for a folder you've confirmed is dedicated to one genre overall, but whose tracks are "
        "individually tagged well enough that per-track classification should still win where it succeeds "
        "(e.g. a folder named 'Afrobeat' that already contains some tracks correctly tagged as the distinct "
        "'Afrobeats' genre). Unlike --assume-genre, this is a FALLBACK, not an override: it only fills in "
        "items classify_genre() left at _GENRE NEEDS CHECK, and never touches an item that already resolved "
        "from a real tag or filename match.",
    )
    parser.add_argument(
        "--preserve-top-level-subfolder",
        action="store_true",
        help="Treat the first path component under root as context to keep (e.g. an event/crate name) and "
        "insert version/genre inside it, matching the Events plan shape, instead of the flat "
        "<root>/<version>/<genre>/filename layout used for single-genre dumps.",
    )
    parser.add_argument(
        "--preserve-context-names",
        help="Only with --preserve-top-level-subfolder: comma-separated exact top-level folder names to "
        "preserve as context (e.g. 'Rap'). Any other non-version top-level folder is treated as if its "
        "files were loose at root -- flattened into the plain version/genre structure with no context kept. "
        "Omit to preserve every non-version top-level folder (the original, unrestricted behavior).",
    )
    parser.add_argument("--genre-rules", type=Path, help="Override path to a version-genre-rules.json")
    parser.add_argument(
        "--on-collision",
        choices=["report", "suffix"],
        default="report",
        help="'report' (default) leaves colliding items alone and lists them in validation for manual "
        "resolution before apply. 'suffix' keeps every file — nothing is ever dropped or overwritten — "
        "and appends ' (2)', ' (3)', ... to the destination filename for the 2nd+ item in each collision "
        "group (ordered by source path), consistent with this project's existing terminal-(N) convention. "
        "Sources are never modified by this, only the proposed destination.",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    rules = load_genre_rules(args.genre_rules)

    with args.inventory.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    items = []
    destinations: collections.defaultdict[str, list[str]] = collections.defaultdict(list)
    version_counts: collections.Counter = collections.Counter()
    genre_counts: collections.Counter = collections.Counter()
    tag_change_counts: collections.Counter = collections.Counter()
    source_missing = []
    preserve_names = (
        {name.strip() for name in args.preserve_context_names.split(",")}
        if args.preserve_context_names
        else None
    )

    for row in rows:
        source = Path(row["full_path"])
        try:
            rel = source.relative_to(root)
        except ValueError as exc:
            raise SystemExit(f"Inventory path escaped target root: {source}") from exc
        if not source.exists():
            source_missing.append(str(source))

        version_bucket, version_evidence = classify_version(source.stem, row.get("title", ""))
        if version_bucket == NEEDS_VERSION and args.assume_version_fallback:
            version_bucket = args.assume_version_fallback
            version_evidence = f"no explicit filename marker; folder is a confirmed {args.assume_version_fallback.title()} crate"
        genre, genre_evidence, genre_confidence = classify_genre(row, rules, args.assume_genre)
        if genre is None and args.assume_genre_fallback:
            genre = args.assume_genre_fallback
            genre_evidence = f"no tag/filename match; folder is a confirmed {args.assume_genre_fallback} crate"
            genre_confidence = "fallback"
        genre_folder = genre or NEEDS_GENRE

        # Idempotency: preserve-top-level-subfolder mode must recognize a file
        # that's already been sorted, in TWO shapes, or a second run corrupts
        # every already-correct path (caught live on R&b, both variants):
        #  - had real context, already sorted: <context>/<version>/<genre>/name
        #    -> without stripping, a re-run nests a second version/genre pair
        #       inside the first: 2025/CLEAN/R&B/CLEAN/R&B/track.mp3
        #  - had NO context (was loose at root), already sorted:
        #    <version>/<genre>/name -- rel.parts[0] is itself a version
        #    category, not a real context folder to preserve
        #    -> without this check, "CLEAN" gets treated as the context and
        #       a second version/genre pair gets nested inside it:
        #       CLEAN/CLEAN/R&B/R&B/track.mp3
        is_preservable_context = (
            args.preserve_top_level_subfolder
            and len(rel.parts) > 1
            and rel.parts[0] not in VERSION_CATEGORIES
            and (preserve_names is None or rel.parts[0].strip() in preserve_names)
        )
        if is_preservable_context:
            context, rest = rel.parts[0], list(rel.parts[1:])
            if len(rest) >= 2 and rest[0] in VERSION_CATEGORIES:
                rest = rest[2:]
            dest_rel = Path(context, version_bucket, genre_folder, *rest)
        else:
            dest_rel = Path(version_bucket, genre_folder, rel.name)
        dest = root / dest_rel

        current_genre = row["genre"]
        genre_writable = source.suffix.casefold() in WRITABLE_GENRE_FORMATS
        tag_change = bool(genre and genre != current_genre and genre_writable)
        move = source != dest

        version_counts[version_bucket] += 1
        genre_counts[genre_folder] += 1
        if tag_change:
            tag_change_counts[f"{current_genre or '(blank)'} -> {genre}"] += 1
        destinations[mutation_engine.path_collision_key(dest)].append(str(source))

        items.append(
            {
                "source": str(source),
                "source_relative": str(rel),
                "destination": str(dest),
                "destination_relative": str(dest_rel),
                "sha256": row["sha256"],
                "version_category": version_bucket,
                "version_evidence": version_evidence,
                "current_genre": current_genre,
                "proposed_genre": genre,
                "genre_evidence": genre_evidence,
                "genre_confidence": genre_confidence,
                "move": move,
                "tag_change": tag_change,
                "genre_write_skipped_reason": (
                    None if genre_writable or not genre or genre == current_genre
                    else f"Genre write not supported for {source.suffix} files; existing tag preserved"
                ),
            }
        )

    collisions = {k: v for k, v in destinations.items() if len(v) > 1}
    collisions_resolved = []
    if args.on_collision == "suffix" and collisions:
        by_dest_key: collections.defaultdict[str, list[dict]] = collections.defaultdict(list)
        for item in items:
            by_dest_key[item["destination"].casefold()].append(item)
        for dest_key, group in by_dest_key.items():
            if len(group) <= 1:
                continue
            group.sort(key=lambda i: i["source"])
            for order, item in enumerate(group[1:], start=2):
                dest = Path(item["destination"])
                new_dest = dest.with_name(f"{dest.stem} ({order}){dest.suffix}")
                item["destination"] = str(new_dest)
                item["destination_relative"] = str(Path(item["destination_relative"]).with_name(new_dest.name))
                item["collision_suffix_applied"] = True
                item["collision_evidence"] = (
                    f"destination collided with {len(group) - 1} other file(s) of the same name "
                    f"(different SHA-256 each time); kept both, never deduplicated by filename alone"
                )
            collisions_resolved.append({"original_destination": dest_key, "sources": [i["source"] for i in group]})
        destinations = collections.defaultdict(list)
        for item in items:
            destinations[mutation_engine.path_collision_key(Path(item["destination"]))].append(item["source"])
        collisions = {k: v for k, v in destinations.items() if len(v) > 1}

    source_paths = [Path(item["source"]) for item in items]
    existing_conflicts = []
    for item in items:
        dest = Path(item["destination"])
        source = Path(item["source"])
        if item["move"] and mutation_engine.destination_conflicts(source, dest):
            occupied_by_plan_source = any(
                candidate.exists() and os.path.samefile(dest, candidate)
                for candidate in source_paths
            )
            if not occupied_by_plan_source:
                existing_conflicts.append({"destination": item["destination"], "source": item["source"]})

    plan = {
        "mode": "preview-only",
        "library_root": str(root),
        "inventory": str(args.inventory),
        "track_count": len(items),
        "rules": {
            "approval_required": True,
            "no_delete": True,
            "no_audio_change": True,
            "unknowns": "Never guess. Route to _NEEDS CHECK / _GENRE NEEDS CHECK.",
        },
        "validation": {
            "missing_sources": source_missing,
            "destination_collisions": collisions,
            "existing_destination_conflicts": existing_conflicts,
            "collisions_resolved_by_suffix": collisions_resolved,
        },
        "counts": {
            "version_categories": dict(sorted(version_counts.items())),
            "genres": dict(sorted(genre_counts.items())),
            "moves": sum(i["move"] for i in items),
            "genre_tag_changes": sum(i["tag_change"] for i in items),
        },
        "tag_change_summary": dict(sorted(tag_change_counts.items())),
        "items": items,
    }
    plan_path = out_dir / "plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    needs_review = [i for i in items if i["version_category"] == NEEDS_VERSION or i["proposed_genre"] is None]
    lines = [
        f"# `{root.name}` — version + genre preview",
        "",
        "**Preview only — nothing has been moved, renamed, or retagged.**",
        "",
        f"Target: `{root}`",
        "",
        "## Counts",
        "",
        f"- Tracks checked: **{len(items)}**",
        f"- Proposed moves: **{plan['counts']['moves']}**",
        f"- Proposed Genre tag changes: **{plan['counts']['genre_tag_changes']}**",
        f"- Missing sources: **{len(source_missing)}**",
        f"- Destination collisions: **{len(collisions)}**",
        f"- Existing destination conflicts: **{len(existing_conflicts)}**",
        "",
        "## Version categories",
        "",
    ]
    for bucket, count in version_counts.most_common():
        lines.append(f"- `{bucket}`: **{count}**")
    lines.extend(["", "## Genres", ""])
    for genre, count in genre_counts.most_common():
        lines.append(f"- `{genre}`: **{count}**")
    lines.extend(["", "## Needs review", "", "| File | Version | Genre | Why |", "|---|---|---|---|"])
    for item in needs_review:
        why = item["version_evidence"] if item["version_category"] == NEEDS_VERSION else item["genre_evidence"]
        lines.append(
            f"| `{item['source_relative']}` | {item['version_category']} | "
            f"{item['proposed_genre'] or '—'} | {why} |"
        )
    lines.extend(
        [
            "",
            "## Apply safeguards (after approval only)",
            "",
            "- Create a fresh pre-apply backup and verify it.",
            "- Preserve each file's audio payload and Serato frames.",
            "- Preserve the original ID3 version while changing only the Genre field.",
            "- Re-check SHA/audio hashes and tag values after every write.",
            "- Write a complete move/tag undo log.",
            "- Delete nothing.",
            "",
        ]
    )
    (out_dir / "PREVIEW.md").write_text("\n".join(lines), encoding="utf-8")

    print(
        json.dumps(
            {
                "plan": str(plan_path),
                "preview": str(out_dir / "PREVIEW.md"),
                "counts": plan["counts"],
                "validation": {k: len(v) for k, v in plan["validation"].items()},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
