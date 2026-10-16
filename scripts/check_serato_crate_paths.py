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
from collections import defaultdict
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


def parse_database(path: Path) -> list[dict]:
    """Every `otrk` record in database V2, as {path, title, artist, genre}."""
    data = path.read_bytes()
    tracks = []
    for tag, value in parse_tlv(data):
        if tag != "otrk":
            continue
        fields = {t: v for t, v in parse_tlv(value)}
        pfil = fields.get("pfil")
        if pfil is None:
            continue
        tracks.append({
            "path": decode_text(pfil),
            "title": decode_text(fields["tsng"]) if "tsng" in fields else None,
            "artist": decode_text(fields["tart"]) if "tart" in fields else None,
            "genre": decode_text(fields["tgen"]) if "tgen" in fields else None,
        })
    return tracks


def parse_crate(path: Path) -> list[str]:
    """Every `ptrk` path referenced by one .crate file."""
    data = path.read_bytes()
    paths = []
    for tag, value in parse_tlv(data):
        if tag != "otrk":
            continue
        for inner_tag, inner_value in parse_tlv(value):
            if inner_tag == "ptrk":
                paths.append(decode_text(inner_value))
    return paths


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
    for folder, pattern, prefix in (("Subcrates", "*.crate", ""), ("SmartCrates", "*.scrate", "smart:")):
        crates_dir = serato_dir / folder
        if not crates_dir.is_dir():
            continue
        for crate_file in sorted(crates_dir.rglob(pattern)):
            name = str(crate_file.relative_to(crates_dir).with_suffix(""))
            crate_paths[prefix + name] = parse_crate(crate_file)

    filename_index = build_filename_index(library_root)

    # One record per unique referenced path, noting every crate (if any)
    # that also references it, so a broken crate entry is traceable.
    by_path: dict[str, dict] = {}
    for track in db_tracks:
        by_path[track["path"]] = {
            "path": track["path"],
            "title": track["title"],
            "artist": track["artist"],
            "genre": track["genre"],
            "in_database": True,
            "crates": [],
        }
    for crate_name, paths in crate_paths.items():
        for path_str in paths:
            entry = by_path.setdefault(path_str, {
                "path": path_str, "title": None, "artist": None, "genre": None,
                "in_database": False, "crates": [],
            })
            entry["crates"].append(crate_name)

    resolved = []
    moved_likely = []
    moved_ambiguous = []
    missing = []

    for path_str, entry in by_path.items():
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

    return {
        "serato_dir": str(serato_dir),
        "library_root": str(library_root),
        "database_track_count": len(db_tracks),
        "crate_count": len(crate_paths),
        "crate_reference_count": sum(len(v) for v in crate_paths.values()),
        "unique_paths_checked": len(by_path),
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
    print(f"crates: {report['crate_count']} ({report['crate_reference_count']} track references)")
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
