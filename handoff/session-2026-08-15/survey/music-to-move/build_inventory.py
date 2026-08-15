#!/usr/bin/env python3
"""Build a read-only per-file inventory for one DJ-library folder."""

from __future__ import annotations

import collections
import csv
import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.id3 import ID3


ROOT = Path("/Users/djb.a.e/Music/DJ-Music/Music/music to move")
OUT = Path(__file__).resolve().parent
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aif", ".aiff", ".flac", ".ogg"}

# Only this exact terminal pattern is a copy-suffix hint. Numbers elsewhere
# remain meaningful content: 21 Savage, 112, Part 1, 1999, 123 BPM, etc.
COPY_SUFFIX_RE = re.compile(r"\s\((\d+)\)$")
VERSION_RE = re.compile(
    r"\b(clean|dirty|explicit|radio|intro|outro|extended|acapella|a cappella|"
    r"instrumental|remix|re[- ]?mix|edit|re[- ]?edit|flip|bootleg|mashup|blend|dub|version)\b",
    re.IGNORECASE,
)

FIELDS = [
    "full_path",
    "filename",
    "artist",
    "title",
    "album",
    "genre",
    "year",
    "bpm",
    "key",
    "file_type",
    "bitrate_kbps",
    "sample_rate_hz",
    "file_size_bytes",
    "duration_seconds",
    "date_modified",
    "sha256",
    "version_words",
    "trailing_copy_suffix",
    "copy_suffix_number",
    "exact_duplicate_count_in_folder",
]


def first_id3(tags: ID3, frame: str) -> str:
    values = tags.getall(frame)
    return str(values[0]).strip() if values else ""


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_row(path: Path) -> dict[str, object]:
    stat = path.stat()
    audio = None
    try:
        audio = MutagenFile(path)
    except Exception:
        pass

    info = getattr(audio, "info", None)
    bitrate = getattr(info, "bitrate", 0) or 0
    sample_rate = getattr(info, "sample_rate", 0) or 0
    duration = getattr(info, "length", 0) or 0

    artist = title = album = genre = year = bpm = key = ""
    try:
        tags = ID3(path)
        artist = first_id3(tags, "TPE1")
        title = first_id3(tags, "TIT2")
        album = first_id3(tags, "TALB")
        genre = first_id3(tags, "TCON")
        year = first_id3(tags, "TDRC") or first_id3(tags, "TYER")
        bpm = first_id3(tags, "TBPM")
        key = first_id3(tags, "TKEY")
    except Exception:
        # Generic fallback for non-ID3 formats.
        try:
            easy = MutagenFile(path, easy=True)
            tags = getattr(easy, "tags", {}) or {}
            take = lambda name: str((tags.get(name) or [""])[0]).strip()
            artist, title, album = take("artist"), take("title"), take("album")
            genre, year = take("genre"), take("date")
            bpm, key = take("bpm"), take("initialkey")
        except Exception:
            pass

    copy_match = COPY_SUFFIX_RE.search(path.stem)
    version_words = []
    seen = set()
    for match in VERSION_RE.finditer(f"{path.stem} {title}"):
        value = match.group(1).lower()
        if value not in seen:
            seen.add(value)
            version_words.append(value)

    return {
        "full_path": str(path),
        "filename": path.name,
        "artist": artist,
        "title": title,
        "album": album,
        "genre": genre,
        "year": year[:4] if year else "",
        "bpm": bpm,
        "key": key,
        "file_type": path.suffix.lower().lstrip("."),
        "bitrate_kbps": round(bitrate / 1000) if bitrate else "",
        "sample_rate_hz": sample_rate or "",
        "file_size_bytes": stat.st_size,
        "duration_seconds": round(duration, 3) if duration else "",
        "date_modified": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds"),
        "sha256": file_hash(path),
        "version_words": "; ".join(version_words),
        "trailing_copy_suffix": "yes" if copy_match else "no",
        "copy_suffix_number": copy_match.group(1) if copy_match else "",
        "exact_duplicate_count_in_folder": 0,
    }


def main() -> None:
    before = sorted(
        path for path in ROOT.rglob("*")
        if path.is_file() and path.suffix.lower() in AUDIO_EXTS
    )
    before_state = {str(path): (path.stat().st_size, path.stat().st_mtime_ns) for path in before}
    rows = [read_row(path) for path in before]

    hash_counts = collections.Counter(str(row["sha256"]) for row in rows)
    for row in rows:
        row["exact_duplicate_count_in_folder"] = hash_counts[str(row["sha256"])] - 1

    after = sorted(
        path for path in ROOT.rglob("*")
        if path.is_file() and path.suffix.lower() in AUDIO_EXTS
    )
    after_state = {str(path): (path.stat().st_size, path.stat().st_mtime_ns) for path in after}
    stable = before_state == after_state

    with (OUT / "inventory.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    (OUT / "inventory.json").write_text(
        json.dumps(
            {
                "scope": str(ROOT),
                "read_only": True,
                "stable_during_inventory": stable,
                "number_rule": (
                    "Only a terminal (N) immediately before the extension is flagged as a copy hint. "
                    "Meaningful numbers are preserved. A suffix is not duplicate proof; SHA-256 is."
                ),
                "rows": rows,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )

    exact_groups = sum(1 for count in hash_counts.values() if count > 1)
    exact_extra = sum(count - 1 for count in hash_counts.values() if count > 1)
    suffix_rows = [row for row in rows if row["trailing_copy_suffix"] == "yes"]
    missing = {
        field: sum(not str(row[field]).strip() for row in rows)
        for field in ("artist", "title", "album", "genre", "year", "bpm", "key")
    }
    formats = collections.Counter(str(row["file_type"]) for row in rows)
    sample_rates = collections.Counter(str(row["sample_rate_hz"]) or "unknown" for row in rows)
    bitrates = [int(row["bitrate_kbps"]) for row in rows if row["bitrate_kbps"] != ""]

    report = [
        "# `music to move` folder — read-only inventory",
        "",
        f"Scope: `{ROOT}`",
        "",
        "> No music file, tag, filename, path, or Serato data was changed.",
        "",
        "## Result",
        "",
        f"- Files inventoried: **{len(rows):,}**",
        f"- Snapshot remained stable during hashing: **{'yes' if stable else 'no — rerun required'}**",
        f"- Unique SHA-256 hashes within this folder: **{len(hash_counts):,}**",
        f"- Exact-duplicate groups within this folder: **{exact_groups:,}**",
        f"- Redundant instances within those groups: **{exact_extra:,}**",
        f"- Terminal `(N)` filename hints: **{len(suffix_rows):,}**",
        f"- Formats: {', '.join(f'{name}: {count:,}' for name, count in formats.most_common())}",
        f"- Sample rates: {', '.join(f'{name} Hz: {count:,}' for name, count in sample_rates.most_common())}",
        f"- Bitrate range: **{min(bitrates) if bitrates else 'unknown'}–{max(bitrates) if bitrates else 'unknown'} kbps**",
        "",
        "## Missing metadata",
        "",
    ]
    for field, count in missing.items():
        report.append(f"- Missing {field}: **{count:,}**")

    report.extend(["", "## Terminal `(N)` hints", ""])
    if suffix_rows:
        for row in suffix_rows:
            report.append(f"- `{row['filename']}` — suffix `{row['copy_suffix_number']}`; SHA-256 recorded, not classified as a duplicate by name")
    else:
        report.append("None in this folder.")

    report.extend([
        "",
        "## Number handling rule",
        "",
        "Preserve numbers inside artists, titles, years, BPM labels, and version names. Only a final `(N)` is a copy-suffix hint, and it still requires hash evidence before exact-duplicate classification.",
        "",
        "## Outputs",
        "",
        "- `inventory.csv` — one row per audio file",
        "- `inventory.json` — the same data plus audit metadata",
        "- `cross-library-exact-copies.json` — earlier targeted comparison against the rest of the library",
        "",
    ])
    (OUT / "INVENTORY-SUMMARY.md").write_text("\n".join(report))

    print(f"files={len(rows)} stable={stable} hashes={len(hash_counts)} exact_groups={exact_groups}")
    print(f"copy_suffix_hints={len(suffix_rows)}")


if __name__ == "__main__":
    main()
