#!/usr/bin/env python3
"""Read-only whole-library audit for a DJ music collection.

This script NEVER moves, renames, deletes, or writes metadata to source audio.
It only reads files and writes reports to the requested output directory.

Outputs:
- inventory.csv
- summary.json
- exact_duplicates.csv
- metadata_review_candidates.csv
- errors.csv
"""

from __future__ import annotations

# Read-only: inspects the library and writes reports/derived data only.
SAFETY_CLASSIFICATION = "read-only"

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from mutagen import File as MutagenFile
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "mutagen is required. Install with: python3 -m pip install --user mutagen"
    ) from exc


AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".aif",
    ".aiff",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".wma",
}
LOSSY_EXTENSIONS = {".mp3", ".m4a", ".aac", ".ogg", ".wma"}

PROMO_PATTERNS = (
    "free dl",
    "free download",
    "out now",
    "official audio",
    "official video",
    "available on all platforms",
    "premiere:",
    "www.",
    "http://",
    "https://",
)

INVENTORY_COLUMNS = [
    "root",
    "relative_path",
    "absolute_path",
    "filename",
    "extension",
    "file_size_bytes",
    "modified_utc",
    "sha256",
    "artist",
    "title",
    "album",
    "genre",
    "year",
    "bpm",
    "key",
    "grouping",
    "duration_seconds",
    "bitrate_kbps",
    "sample_rate_hz",
    "channels",
    "bits_per_sample",
    "metadata_identity",
    "exact_duplicate_group",
    "quality_flags",
    "metadata_flags",
    "read_error",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only DJ library inventory and exact-duplicate audit."
    )
    parser.add_argument(
        "roots",
        nargs="+",
        type=Path,
        help="One or more folders containing audio files.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("output/library-audit"),
        help="Report directory (default: output/library-audit).",
    )
    parser.add_argument(
        "--no-hash",
        action="store_true",
        help="Skip SHA-256 hashing. Exact-duplicate reports will be unavailable.",
    )
    parser.add_argument(
        "--follow-symlinks",
        action="store_true",
        help="Follow directory symlinks. Disabled by default to avoid accidental rescans.",
    )
    parser.add_argument(
        "--low-bitrate-kbps",
        type=int,
        default=192,
        help="Flag lossy files below this bitrate for review (default: 192).",
    )
    return parser.parse_args()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def first_tag(tags: Any, *names: str) -> str:
    if not tags:
        return ""
    for name in names:
        value = tags.get(name)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        text = str(value).strip()
        if text:
            return text
    return ""


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"[^\w\s]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def metadata_identity(artist: str, title: str) -> str:
    artist_norm = normalize_text(artist)
    title_norm = normalize_text(title)
    if not artist_norm or not title_norm:
        return ""
    return f"{artist_norm} :: {title_norm}"


def parse_year(raw: str) -> str:
    match = re.search(r"\b(19|20)\d{2}\b", raw or "")
    return match.group(0) if match else ""


def safe_number(value: Any, scale: float = 1.0, digits: int = 0) -> str:
    if value is None:
        return ""
    try:
        number = float(value) / scale
    except (TypeError, ValueError):
        return ""
    if digits == 0:
        return str(int(round(number)))
    return f"{number:.{digits}f}"


def read_audio_metadata(path: Path) -> dict[str, str]:
    audio = MutagenFile(path, easy=True)
    if audio is None:
        raise ValueError("mutagen could not identify audio format")

    tags = audio.tags or {}
    info = getattr(audio, "info", None)

    artist = first_tag(tags, "artist", "albumartist")
    title = first_tag(tags, "title")
    album = first_tag(tags, "album")
    genre = first_tag(tags, "genre")
    raw_date = first_tag(tags, "date", "originaldate")
    bpm = first_tag(tags, "bpm")
    initial_key = first_tag(tags, "initialkey", "key")
    grouping = first_tag(tags, "grouping")

    duration = getattr(info, "length", None) if info else None
    bitrate = getattr(info, "bitrate", None) if info else None
    sample_rate = getattr(info, "sample_rate", None) if info else None
    channels = getattr(info, "channels", None) if info else None
    bits_per_sample = getattr(info, "bits_per_sample", None) if info else None

    if not bitrate and duration:
        try:
            bitrate = (path.stat().st_size * 8) / float(duration)
        except (OSError, ZeroDivisionError, ValueError):
            bitrate = None

    return {
        "artist": artist,
        "title": title,
        "album": album,
        "genre": genre,
        "year": parse_year(raw_date),
        "bpm": bpm,
        "key": initial_key,
        "grouping": grouping,
        "duration_seconds": safe_number(duration, digits=3),
        "bitrate_kbps": safe_number(bitrate, scale=1000.0),
        "sample_rate_hz": safe_number(sample_rate),
        "channels": safe_number(channels),
        "bits_per_sample": safe_number(bits_per_sample),
    }


def metadata_flags(row: dict[str, str]) -> list[str]:
    flags: list[str] = []
    if not row["artist"]:
        flags.append("missing_artist")
    if not row["title"]:
        flags.append("missing_title")
    if not row["genre"]:
        flags.append("missing_genre")
    if not row["year"]:
        flags.append("missing_year")

    probe = " ".join(
        part for part in (row["filename"], row["artist"], row["title"]) if part
    ).casefold()
    for pattern in PROMO_PATTERNS:
        if pattern in probe:
            flags.append("promo_text_candidate")
            break

    return flags


def quality_flags(row: dict[str, str], low_bitrate_kbps: int) -> list[str]:
    flags: list[str] = []
    ext = row["extension"].casefold()

    bitrate_text = row.get("bitrate_kbps", "")
    if ext in LOSSY_EXTENSIONS and bitrate_text:
        try:
            if int(float(bitrate_text)) < low_bitrate_kbps:
                flags.append(f"lossy_below_{low_bitrate_kbps}kbps")
        except ValueError:
            pass

    sample_rate_text = row.get("sample_rate_hz", "")
    if sample_rate_text:
        try:
            if int(float(sample_rate_text)) < 44100:
                flags.append("sample_rate_below_44_1khz")
        except ValueError:
            pass

    duration_text = row.get("duration_seconds", "")
    if duration_text:
        try:
            if float(duration_text) <= 1.0:
                flags.append("duration_at_or_below_1s")
        except ValueError:
            pass

    return flags


def iter_audio_files(root: Path, follow_symlinks: bool) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        if not follow_symlinks:
            dirnames[:] = [
                name
                for name in dirnames
                if not (Path(dirpath) / name).is_symlink()
            ]
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.casefold() in AUDIO_EXTENSIONS:
                yield path


def scan_file(
    root: Path,
    path: Path,
    do_hash: bool,
    low_bitrate_kbps: int,
) -> dict[str, str]:
    stat = path.stat()
    row = {column: "" for column in INVENTORY_COLUMNS}
    row.update(
        {
            "root": str(root),
            "relative_path": str(path.relative_to(root)),
            "absolute_path": str(path.resolve()),
            "filename": path.name,
            "extension": path.suffix.casefold(),
            "file_size_bytes": str(stat.st_size),
            "modified_utc": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).isoformat(),
        }
    )

    if do_hash:
        row["sha256"] = sha256_file(path)

    try:
        row.update(read_audio_metadata(path))
    except Exception as exc:  # audit must continue across bad files
        row["read_error"] = f"{type(exc).__name__}: {exc}"

    row["metadata_identity"] = metadata_identity(row["artist"], row["title"])
    row["metadata_flags"] = ";".join(metadata_flags(row))
    row["quality_flags"] = ";".join(quality_flags(row, low_bitrate_kbps))
    return row


def assign_exact_duplicate_groups(rows: list[dict[str, str]]) -> dict[str, list[int]]:
    by_hash: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        if row["sha256"]:
            by_hash[row["sha256"]].append(index)

    duplicate_groups = {
        digest: indexes for digest, indexes in by_hash.items() if len(indexes) > 1
    }
    for group_number, (digest, indexes) in enumerate(
        sorted(duplicate_groups.items()), start=1
    ):
        group_id = f"EXACT-{group_number:05d}"
        for index in indexes:
            rows[index]["exact_duplicate_group"] = group_id
    return duplicate_groups


def metadata_review_groups(rows: list[dict[str, str]]) -> list[list[int]]:
    """Return metadata-only review groups.

    These are NOT duplicate verdicts. They only identify different files with the
    same normalized artist + title so a human can inspect versions/edits/mastering.
    """
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        identity = row["metadata_identity"]
        if identity:
            groups[identity].append(index)

    results: list[list[int]] = []
    for indexes in groups.values():
        hashes = {rows[index]["sha256"] for index in indexes if rows[index]["sha256"]}
        if len(indexes) > 1 and (not hashes or len(hashes) > 1):
            results.append(indexes)
    return results


def write_csv(path: Path, rows: Iterable[dict[str, str]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_summary(
    rows: list[dict[str, str]],
    duplicate_groups: dict[str, list[int]],
    metadata_groups: list[list[int]],
    hashing_enabled: bool,
) -> dict[str, Any]:
    total_bytes = sum(int(row["file_size_bytes"] or 0) for row in rows)
    extension_counts: dict[str, int] = defaultdict(int)
    for row in rows:
        extension_counts[row["extension"]] += 1

    exact_redundant_instances = sum(len(indexes) - 1 for indexes in duplicate_groups.values())
    exact_redundant_bytes = 0
    for indexes in duplicate_groups.values():
        sizes = [int(rows[index]["file_size_bytes"] or 0) for index in indexes]
        if sizes:
            exact_redundant_bytes += sum(sizes) - min(sizes)

    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source_files_modified": False,
        "hashing_enabled": hashing_enabled,
        "audio_files": len(rows),
        "total_bytes": total_bytes,
        "total_gib": round(total_bytes / (1024 ** 3), 3),
        "extensions": dict(sorted(extension_counts.items())),
        "read_errors": sum(bool(row["read_error"]) for row in rows),
        "missing_artist": sum("missing_artist" in row["metadata_flags"] for row in rows),
        "missing_title": sum("missing_title" in row["metadata_flags"] for row in rows),
        "missing_genre": sum("missing_genre" in row["metadata_flags"] for row in rows),
        "missing_year": sum("missing_year" in row["metadata_flags"] for row in rows),
        "quality_review_candidates": sum(bool(row["quality_flags"]) for row in rows),
        "exact_duplicate_groups": len(duplicate_groups) if hashing_enabled else None,
        "exact_redundant_instances": exact_redundant_instances if hashing_enabled else None,
        "exact_redundant_bytes": exact_redundant_bytes if hashing_enabled else None,
        "metadata_only_review_groups": len(metadata_groups),
        "safety_note": (
            "Exact hashes prove byte-identical files only. Metadata review groups are "
            "not duplicate verdicts and must never drive automatic deletion."
        ),
    }


def main() -> int:
    args = parse_args()
    roots = [root.expanduser().resolve() for root in args.roots]
    out_dir = args.out_dir.expanduser().resolve()

    for root in roots:
        if not root.exists():
            raise SystemExit(f"Source does not exist: {root}")
        if not root.is_dir():
            raise SystemExit(f"Source is not a directory: {root}")

    rows: list[dict[str, str]] = []
    seen_paths: set[Path] = set()

    for root in roots:
        for path in iter_audio_files(root, args.follow_symlinks):
            resolved = path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            try:
                rows.append(
                    scan_file(
                        root=root,
                        path=path,
                        do_hash=not args.no_hash,
                        low_bitrate_kbps=args.low_bitrate_kbps,
                    )
                )
            except Exception as exc:
                fallback = {column: "" for column in INVENTORY_COLUMNS}
                fallback.update(
                    {
                        "root": str(root),
                        "relative_path": str(path.relative_to(root)),
                        "absolute_path": str(path.resolve()),
                        "filename": path.name,
                        "extension": path.suffix.casefold(),
                        "read_error": f"{type(exc).__name__}: {exc}",
                    }
                )
                rows.append(fallback)

    rows.sort(key=lambda row: (row["root"], row["relative_path"].casefold()))

    duplicate_groups = assign_exact_duplicate_groups(rows) if not args.no_hash else {}
    metadata_groups = metadata_review_groups(rows)

    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "inventory.csv", rows, INVENTORY_COLUMNS)

    exact_rows: list[dict[str, str]] = []
    if not args.no_hash:
        for row in rows:
            if row["exact_duplicate_group"]:
                exact_rows.append(
                    {
                        "group": row["exact_duplicate_group"],
                        "sha256": row["sha256"],
                        "file_size_bytes": row["file_size_bytes"],
                        "absolute_path": row["absolute_path"],
                    }
                )
    write_csv(
        out_dir / "exact_duplicates.csv",
        exact_rows,
        ["group", "sha256", "file_size_bytes", "absolute_path"],
    )

    metadata_rows: list[dict[str, str]] = []
    for group_number, indexes in enumerate(metadata_groups, start=1):
        group_id = f"META-REVIEW-{group_number:05d}"
        for index in indexes:
            row = rows[index]
            metadata_rows.append(
                {
                    "group": group_id,
                    "evidence": "same_normalized_artist_and_title_only",
                    "artist": row["artist"],
                    "title": row["title"],
                    "duration_seconds": row["duration_seconds"],
                    "sha256": row["sha256"],
                    "absolute_path": row["absolute_path"],
                }
            )
    write_csv(
        out_dir / "metadata_review_candidates.csv",
        metadata_rows,
        [
            "group",
            "evidence",
            "artist",
            "title",
            "duration_seconds",
            "sha256",
            "absolute_path",
        ],
    )

    error_rows = [
        {
            "absolute_path": row["absolute_path"],
            "extension": row["extension"],
            "error": row["read_error"],
        }
        for row in rows
        if row["read_error"]
    ]
    write_csv(
        out_dir / "errors.csv",
        error_rows,
        ["absolute_path", "extension", "error"],
    )

    summary = build_summary(
        rows,
        duplicate_groups,
        metadata_groups,
        hashing_enabled=not args.no_hash,
    )
    with (out_dir / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)
        handle.write("\n")

    print(json.dumps(summary, indent=2))
    print(f"\nReports written to: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
