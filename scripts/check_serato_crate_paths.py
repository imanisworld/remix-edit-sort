#!/usr/bin/env python3
"""Read-only integrity check: do Serato's database and crate files still
point at real files on disk?

Serato stores every track it knows about in `_Serato_/database V2`, and
every crate (playlist) in `_Serato_/Subcrates/*.crate`, as a binary TLV
format (4-byte ASCII tag, 4-byte big-endian length, then the value --
UTF-16BE text for string fields, nested TLV for container fields). This is
a stable, long-documented format; there is no ambiguity being guessed at
here, and every assumption below was checked against this machine's real
`_Serato_` data before being written:

* `database V2` holds one `otrk` record per track, each containing a
  `pfil` field with that track's path.
* Each `.crate` file holds one `otrk` record per track reference, each
  containing a `ptrk` field with that track's path (crates reference the
  master database by path; they don't repeat other metadata).
* Both store the path relative to the volume root, with no leading `/`
  (e.g. `Users/you/Music/...`) -- prepend `/` to get an absolute path.
  Verified against ~25,800 real entries: every one of the ~21,700 that
  resolved did so only with the leading `/` prepended, none resolved
  as-is.
* `SmartCrates/*.scrate` are rule-based filters, BUT they also cache their
  current track list as `otrk`/`ptrk` records (corrected 2026-10-16: 75,063
  cached refs on this machine). Serato re-imports any cached path missing from
  `database V2`, so they are checked too and reported as `smart:<name>`.
  Nested crates (e.g. `Subcrates/Serato Stems/Stems.crate`) are included.

This script only reads `_Serato_/database V2`, `_Serato_/Subcrates/**` and
`_Serato_/SmartCrates/**`,
and only reads the live library to check whether paths exist and, for
missing ones, to look for a same-named file elsewhere as a candidate
"probably moved here" hint. It never writes to Serato's data and never
writes to the library. Nothing here requires a folder lock.
"""

from __future__ import annotations

# Read-only: parses Serato's binary files and the library tree; writes
# only its own JSON report.
SAFETY_CLASSIFICATION = "read-only"

import argparse
import json
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT_SERATO_DIR = Path("/Users/djb.a.e/Music/_Serato_")
DEFAULT_LIBRARY_ROOT = Path("/Users/djb.a.e/Music/DJ-Music/Music")
AUDIO_SUFFIXES = {".mp3", ".flac", ".m4a", ".aiff", ".aif", ".wav"}


def parse_tlv(data: bytes) -> list[tuple[str, bytes]]:
    """Split one TLV byte blob into (tag, value) records.

    Malformed trailing bytes (fewer than 8 left for a header) are simply
    not yielded -- this format has no explicit end marker, callers rely on
    the enclosing length to bound each container, and a torn read here
    means the loop just stops, not that it misreads garbage as a tag.
    """
    records = []
    i, n = 0, len(data)
    while i + 8 <= n:
        tag = data[i:i + 4].decode("ascii", errors="replace")
        length = struct.unpack(">I", data[i + 4:i + 8])[0]
        value = data[i + 8:i + 8 + length]
        if i + 8 + length > n:
            break
        records.append((tag, value))
        i += 8 + length
    return records


def decode_text(value: bytes) -> str:
    return value.decode("utf-16-be", errors="replace")


def decode_stored_path(value: bytes) -> tuple[str, int]:
    decoded = decode_text(value)
    terminator_length = len(decoded) - len(decoded.rstrip("\x00"))
    path = decoded[:-terminator_length] if terminator_length else decoded
    return path, terminator_length


DATABASE_METADATA_FIELDS = {
    "tsng": "title",
    "tart": "artist",
    "tgen": "genre",
    "tbpm": "bpm",
    "tbit": "bitrate",
}


def logical_path(serato_path: str) -> str:
    """Identity used only to relate leading-slash aliases to canonical paths."""
    return serato_path.lstrip("/")


def parse_database(path: Path) -> list[dict]:
    """Every ordered `otrk`/`pfil` occurrence in database V2."""
    data = path.read_bytes()
    tracks = []
    record_index = 0
    for outer_record_index, (tag, value) in enumerate(parse_tlv(data)):
        if tag != "otrk":
            continue
        record_index += 1
        fields = parse_tlv(value)
        path_fields = [
            (field_index, field_value)
            for field_index, (field_tag, field_value) in enumerate(fields)
            if field_tag == "pfil"
        ]
        if not path_fields:
            continue
        decoded_fields = {
            DATABASE_METADATA_FIELDS[field_tag]: decode_text(field_value)
            for field_tag, field_value in fields
            if field_tag in DATABASE_METADATA_FIELDS
        }
        metadata = {
            name: decoded_fields.get(name)
            for name in DATABASE_METADATA_FIELDS.values()
        }
        for field_index, field_value in path_fields:
            stored_path, terminator_length = decode_stored_path(field_value)
            tracks.append(
                {
                    "record_index": record_index,
                    "outer_record_index": outer_record_index,
                    "pfil_field_index": field_index,
                    "path_terminator_length": terminator_length,
                    "path": stored_path,
                    "logical_path": logical_path(stored_path),
                    "is_canonical_path": stored_path == logical_path(stored_path),
                    **metadata,
                    "metadata_populated_count": sum(
                        value not in (None, "") for value in metadata.values()
                    ),
                }
            )
    return tracks


def parse_crate_occurrences(path: Path) -> list[dict]:
    """Every ordered `otrk`/`ptrk` occurrence in one crate file."""
    data = path.read_bytes()
    occurrences = []
    record_index = 0
    for outer_record_index, (tag, value) in enumerate(parse_tlv(data)):
        if tag != "otrk":
            continue
        record_index += 1
        for field_index, (inner_tag, inner_value) in enumerate(parse_tlv(value)):
            if inner_tag == "ptrk":
                stored_path, terminator_length = decode_stored_path(inner_value)
                occurrences.append(
                    {
                        "record_index": record_index,
                        "outer_record_index": outer_record_index,
                        "ptrk_field_index": field_index,
                        "path_terminator_length": terminator_length,
                        "path": stored_path,
                        "logical_path": logical_path(stored_path),
                        "is_canonical_path": stored_path == logical_path(stored_path),
                    }
                )
    return occurrences


def parse_crate(path: Path) -> list[str]:
    """Every `ptrk` path referenced by one .crate file."""
    return [item["path"] for item in parse_crate_occurrences(path)]


def resolve(serato_path: str) -> Path:
    """Serato's stored path -> the absolute path it refers to."""
    return Path("/" + serato_path)


def build_filename_index(library_root: Path) -> dict[str, list[Path]]:
    """filename -> every path under library_root with that exact filename.

    Used only to suggest where a missing file might have moved to -- a
    hint for a human, never treated as authoritative. A file could have
    been renamed as well as moved, in which case this finds nothing and
    the entry is correctly reported as unmapped, not silently guessed at.
    """
    index: dict[str, list[Path]] = defaultdict(list)
    for entry in library_root.rglob("*"):
        if entry.is_file() and entry.suffix.casefold() in AUDIO_SUFFIXES:
            index[entry.name].append(entry)
    return index


def audit(serato_dir: Path, library_root: Path) -> dict:
    db_path = serato_dir / "database V2"
    if not db_path.is_file():
        raise SystemExit(f"no database V2 found at {db_path}")

    db_tracks = parse_database(db_path)
    crate_paths: dict[str, list[str]] = {}
    crate_occurrences = []
    for folder, pattern, prefix in (("Subcrates", "*.crate", ""), ("SmartCrates", "*.scrate", "smart:")):
        crates_dir = serato_dir / folder
        if not crates_dir.is_dir():
            continue
        for crate_file in sorted(crates_dir.rglob(pattern)):
            name = str(crate_file.relative_to(crates_dir).with_suffix(""))
            crate_name = prefix + name
            occurrences = parse_crate_occurrences(crate_file)
            for occurrence in occurrences:
                crate_occurrences.append(
                    {
                        **occurrence,
                        "crate": crate_name,
                        "crate_file": str(crate_file),
                    }
                )
            crate_paths[crate_name] = [item["path"] for item in occurrences]

    filename_index = build_filename_index(library_root)

    database_by_path: dict[str, list[dict]] = defaultdict(list)
    for track in db_tracks:
        database_by_path[track["path"]].append(track)
    crates_by_path: dict[str, list[dict]] = defaultdict(list)
    for occurrence in crate_occurrences:
        crates_by_path[occurrence["path"]].append(occurrence)

    exact_paths = sorted(set(database_by_path) | set(crates_by_path))
    exact_path_groups = []
    for path_str in exact_paths:
        records = database_by_path[path_str]
        occurrences = crates_by_path[path_str]
        first = records[0] if records else {}
        exact_path_groups.append(
            {
                "path": path_str,
                "logical_path": logical_path(path_str),
                "title": first.get("title"),
                "artist": first.get("artist"),
                "genre": first.get("genre"),
                "in_database": bool(records),
                "crates": [item["crate"] for item in occurrences],
                "database_record_count": len(records),
                "crate_reference_count": len(occurrences),
                "database_records": records,
                "crate_occurrences": occurrences,
            }
        )

    logical_groups: dict[str, list[dict]] = defaultdict(list)
    for group in exact_path_groups:
        logical_groups[group["logical_path"]].append(group)
    logical_alias_groups = []
    for path_str, groups in sorted(logical_groups.items()):
        if len(groups) < 2:
            continue
        logical_alias_groups.append(
            {
                "logical_path": path_str,
                "exact_paths": sorted(group["path"] for group in groups),
                "database_record_count": sum(
                    group["database_record_count"] for group in groups
                ),
                "crate_reference_count": sum(
                    group["crate_reference_count"] for group in groups
                ),
                "groups": groups,
            }
        )

    resolved = []
    moved_likely = []
    moved_ambiguous = []
    missing = []

    for entry in exact_path_groups:
        path_str = entry["path"]
        absolute = resolve(path_str)
        if absolute.is_file():
            resolved.append(entry)
            continue
        candidates = filename_index.get(absolute.name, [])
        if len(candidates) == 1:
            entry["candidate_current_path"] = str(candidates[0])
            moved_likely.append(entry)
        elif len(candidates) > 1:
            entry["candidates"] = [str(c) for c in candidates]
            moved_ambiguous.append(entry)
        else:
            missing.append(entry)

    noncanonical_crate_references = [
        item for item in crate_occurrences if not item["is_canonical_path"]
    ]
    sparse_alias_references = []
    for occurrence in noncanonical_crate_references:
        exact_records = database_by_path[occurrence["path"]]
        canonical_records = database_by_path[occurrence["logical_path"]]
        exact_score = max(
            (record["metadata_populated_count"] for record in exact_records),
            default=-1,
        )
        canonical_score = max(
            (record["metadata_populated_count"] for record in canonical_records),
            default=-1,
        )
        if canonical_records and exact_score < canonical_score:
            sparse_alias_references.append(
                {
                    **occurrence,
                    "exact_database_record_count": len(exact_records),
                    "canonical_database_record_count": len(canonical_records),
                    "exact_metadata_populated_count": exact_score,
                    "canonical_metadata_populated_count": canonical_score,
                }
            )

    database_counts = Counter(record["path"] for record in db_tracks)
    logical_database_counts = Counter(record["logical_path"] for record in db_tracks)
    crate_counts = Counter(
        (item["crate"], item["path"]) for item in crate_occurrences
    )
    logical_crate_counts = Counter(
        (item["crate"], item["logical_path"]) for item in crate_occurrences
    )

    return {
        "serato_dir": str(serato_dir),
        "library_root": str(library_root),
        "database_track_count": len(db_tracks),
        "crate_count": len(crate_paths),
        "crate_reference_count": sum(len(v) for v in crate_paths.values()),
        "unique_paths_checked": len(exact_path_groups),
        "database_record_count": len(db_tracks),
        "database_exact_duplicate_record_count": sum(
            count - 1 for count in database_counts.values() if count > 1
        ),
        "database_duplicate_record_count": sum(
            count - 1 for count in logical_database_counts.values() if count > 1
        ),
        "crate_duplicate_reference_count": sum(
            count - 1 for count in crate_counts.values() if count > 1
        ),
        "crate_logical_duplicate_reference_count": sum(
            count - 1 for count in logical_crate_counts.values() if count > 1
        ),
        "database_records": db_tracks,
        "crate_occurrences": crate_occurrences,
        "exact_path_groups": exact_path_groups,
        "logical_alias_groups": logical_alias_groups,
        "noncanonical_crate_references": noncanonical_crate_references,
        "sparse_alias_references": sparse_alias_references,
        "resolved_count": len(resolved),
        "moved_likely_count": len(moved_likely),
        "moved_ambiguous_count": len(moved_ambiguous),
        "missing_count": len(missing),
        "moved_likely": moved_likely,
        "moved_ambiguous": moved_ambiguous,
        "missing": missing,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--serato-dir", type=Path, default=DEFAULT_SERATO_DIR)
    parser.add_argument("--library-root", type=Path, default=DEFAULT_LIBRARY_ROOT)
    parser.add_argument("--out", type=Path, help="write the full JSON report here")
    args = parser.parse_args()

    report = audit(args.serato_dir, args.library_root)

    print(f"database V2: {report['database_track_count']} tracks")
    print(
        "  duplicate logical records: "
        f"{report['database_duplicate_record_count']} "
        f"({report['database_exact_duplicate_record_count']} exact-path)"
    )
    print(f"crates: {report['crate_count']} ({report['crate_reference_count']} track references)")
    print(
        "  duplicate references: "
        f"{report['crate_duplicate_reference_count']} exact / "
        f"{report['crate_logical_duplicate_reference_count']} logical "
        "(within one crate); "
        f"noncanonical: {len(report['noncanonical_crate_references'])}; "
        f"sparse aliases: {len(report['sparse_alias_references'])}"
    )
    print(f"unique paths checked: {report['unique_paths_checked']}")
    print(f"  resolved:         {report['resolved_count']}")
    print(f"  probably moved:   {report['moved_likely_count']}  (exactly one same-named file found elsewhere)")
    print(f"  ambiguous:        {report['moved_ambiguous_count']}  (multiple same-named files found)")
    print(f"  missing:          {report['missing_count']}  (no same-named file found anywhere in the library)")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nfull report written to {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
