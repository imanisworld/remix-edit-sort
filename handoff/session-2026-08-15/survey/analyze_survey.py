#!/usr/bin/env python3
"""Create a read-only DJ-library audit from remix-edit-sort scan output.

Duplicate labels are deliberately conservative:
- exact duplicate: same file size and SHA-256 bytes
- same-name collision: case-insensitive basename match, regardless of content
- numbered-copy candidate: filename differs only by a trailing ``(N)``

No files in the music library are modified.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import re
from pathlib import Path


HERE = Path(__file__).resolve().parent
TRACKS_PATH = HERE / "tracks.json"
REPORT_PATH = HERE / "DJ-LIBRARY-AUDIT.md"
DETAILS_PATH = HERE / "duplicate-details.json"

AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aiff", ".aif", ".flac", ".ogg"}
NUMBERED_COPY_RX = re.compile(r"\s*\((\d+)\)$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def logical_copy_name(filename: str) -> str:
    path = Path(filename)
    stem = NUMBERED_COPY_RX.sub("", path.stem).strip()
    return f"{stem}{path.suffix}".casefold()


def display_paths(paths: list[str], limit: int = 8) -> list[str]:
    shown = [f"  - `{path}`" for path in paths[:limit]]
    if len(paths) > limit:
        shown.append(f"  - …and {len(paths) - limit} more")
    return shown


def main() -> None:
    payload = json.loads(TRACKS_PATH.read_text())
    root = Path(payload["root"])
    tracks = payload["tracks"]

    existing_tracks = [t for t in tracks if Path(t["path"]).is_file()]
    paths = [Path(t["path"]) for t in existing_tracks]
    by_path = {t["path"]: t for t in existing_tracks}

    ext_counts = collections.Counter(path.suffix.lower() for path in paths)
    genre_counts = collections.Counter(t["genre"] or "_Unclassified" for t in existing_tracks)
    top_folder_counts = collections.Counter(t["source_folder"] or "." for t in existing_tracks)

    by_basename: dict[str, list[Path]] = collections.defaultdict(list)
    by_copy_name: dict[str, list[Path]] = collections.defaultdict(list)
    by_size: dict[int, list[Path]] = collections.defaultdict(list)
    total_bytes = 0
    for path in paths:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        total_bytes += size
        by_basename[path.name.casefold()].append(path)
        by_copy_name[logical_copy_name(path.name)].append(path)
        by_size[size].append(path)

    # Hash only repeated-size files. Identical files must have identical sizes;
    # this finds exact copies without rereading every unique-size track.
    hashes: dict[str, list[Path]] = collections.defaultdict(list)
    hashed_files = 0
    hashed_bytes = 0
    for size, candidates in sorted(by_size.items()):
        if size <= 0 or len(candidates) < 2:
            continue
        for path in candidates:
            try:
                hashes[sha256(path)].append(path)
                hashed_files += 1
                hashed_bytes += size
            except OSError:
                pass

    exact_groups = [group for group in hashes.values() if len(group) > 1]
    exact_groups.sort(key=lambda g: (-sum(p.stat().st_size for p in g[1:]), str(g[0]).casefold()))
    exact_redundant_files = sum(len(group) - 1 for group in exact_groups)
    exact_reclaimable_bytes = sum(group[0].stat().st_size * (len(group) - 1) for group in exact_groups)

    same_name_groups = [group for group in by_basename.values() if len(group) > 1]
    same_name_groups.sort(key=lambda g: (-len(g), g[0].name.casefold()))
    numbered_copy_groups = [
        group for group in by_copy_name.values()
        if len(group) > 1 and len({p.name.casefold() for p in group}) > 1
    ]
    numbered_copy_groups.sort(key=lambda g: (-len(g), g[0].name.casefold()))

    exact_hash_by_path = {
        str(path): digest
        for digest, group in hashes.items()
        for path in group
        if len(group) > 1
    }

    same_name_details = []
    for group in same_name_groups:
        rows = []
        for path in group:
            track = by_path[str(path)]
            rows.append({
                "path": str(path),
                "bytes": path.stat().st_size,
                "sha256": exact_hash_by_path.get(str(path)),
                "duration_seconds": track.get("duration"),
                "title": track.get("title_raw"),
                "artist": track.get("artist_raw"),
                "remixer": track.get("remixer"),
            })
        same_name_details.append({"basename": group[0].name, "files": rows})

    details = {
        "root": str(root),
        "definitions": {
            "exact_duplicate": "Identical SHA-256 bytes; no deletion decision has been made.",
            "same_name_collision": "Case-insensitive basenames match; this does not imply duplicate audio.",
            "numbered_copy_candidate": "Names differ only by a trailing (N); this does not imply duplicate audio.",
        },
        "exact_duplicate_groups": [
            {
                "sha256": sha256(group[0]),
                "bytes_each": group[0].stat().st_size,
                "paths": [str(path) for path in group],
            }
            for group in exact_groups
        ],
        "same_name_collision_groups": same_name_details,
        "numbered_copy_candidate_groups": [[str(path) for path in group] for group in numbered_copy_groups],
    }
    DETAILS_PATH.write_text(json.dumps(details, indent=2, ensure_ascii=False) + "\n")

    remixes = [t for t in existing_tracks if t["is_remix"]]
    identifiable_remixes = [t for t in remixes if t["remixer"]]
    long_mixes = [t for t in existing_tracks if t["is_long_mix"]]
    missing_artist = [t for t in existing_tracks if not t["artist_raw"].strip()]
    missing_title = [t for t in existing_tracks if not t["title_raw"].strip()]
    missing_bpm = [t for t in existing_tracks if not t["existing_bpm"]]
    missing_key = [t for t in existing_tracks if not t["existing_key"].strip()]
    missing_genre_tag = [t for t in existing_tracks if not t["existing_genre"].strip()]
    unclassified = [t for t in existing_tracks if not t["genre"]]
    low_bitrate = [t for t in existing_tracks if 0 < t["bitrate"] < 192]
    zero_duration_mp3 = [
        t for t in existing_tracks
        if Path(t["path"]).suffix.lower() == ".mp3" and not t["duration"]
    ]

    report = [
        "# DJ Library Audit — read-only survey",
        "",
        f"Library: `{root}`",
        "",
        "> No audio, tags, Serato data, paths, or filenames were changed. No deletion or move plan was generated.",
        "",
        "## Scope",
        "",
        f"- Audio files scanned: **{len(existing_tracks):,}**",
        f"- Audio storage represented: **{total_bytes / (1024 ** 3):,.1f} GiB**",
        f"- File formats: {', '.join(f'{ext or "(none)"}: {count:,}' for ext, count in ext_counts.most_common())}",
        f"- Files hashed for exact-copy checking: **{hashed_files:,}** ({hashed_bytes / (1024 ** 3):,.1f} GiB read)",
        "",
        "## Remix/edit findings",
        "",
        f"- Remix/edit candidates: **{len(remixes):,}**",
        f"- With an identifiable remixer: **{len(identifiable_remixes):,}**",
        f"- Distinct parsed remixer spellings: **{len({t['remixer'] for t in identifiable_remixes}):,}**",
        f"- Long mixes/sets over 20 minutes: **{len(long_mixes):,}**",
        f"- Genre-classification unresolved: **{len(unclassified):,}**",
        "",
        "These are parser findings, not approved renames or classifications. `_Unclassified` remains preferable to guessing.",
        "",
        "## Duplicate evidence",
        "",
        f"- Exact byte-identical groups: **{len(exact_groups):,}**",
        f"- Redundant file instances inside those groups: **{exact_redundant_files:,}**",
        f"- Potentially reclaimable space if reviewed copies were eventually removed: **{exact_reclaimable_bytes / (1024 ** 3):,.2f} GiB**",
        f"- Same-name collision groups: **{len(same_name_groups):,}**",
        f"- Numbered-copy name groups (`Track` / `Track (1)`): **{len(numbered_copy_groups):,}**",
        "",
        "A same name is not considered a duplicate. Clean/dirty, intro/outro, radio/extended, remixes, edits, stems, and alternate masters remain distinct unless the bytes are actually identical—and even exact copies still require a Serato-reference decision before any removal.",
        "",
        "## Metadata gaps",
        "",
        f"- Missing artist tag: **{len(missing_artist):,}**",
        f"- Missing title tag: **{len(missing_title):,}**",
        f"- Missing BPM tag: **{len(missing_bpm):,}**",
        f"- Missing key tag: **{len(missing_key):,}**",
        f"- Missing genre tag: **{len(missing_genre_tag):,}**",
        f"- MP3s below 192 kbps: **{len(low_bitrate):,}** (review-only; bitrate alone does not make a file disposable)",
        f"- MP3s with no readable duration: **{len(zero_duration_mp3):,}**",
        "",
        "## Largest top-level music folders by track count",
        "",
    ]
    for folder, count in top_folder_counts.most_common(20):
        report.append(f"- `{folder}`: {count:,}")

    report.extend(["", "## Largest exact-copy groups", ""])
    if exact_groups:
        for index, group in enumerate(exact_groups[:25], 1):
            size = group[0].stat().st_size
            report.append(f"### {index}. {len(group)} files × {size / (1024 ** 2):,.1f} MiB")
            report.extend(display_paths([str(path) for path in group]))
            report.append("")
    else:
        report.append("No exact byte-identical groups found.")

    report.extend(["", "## Largest same-name collision groups", ""])
    if same_name_groups:
        for index, group in enumerate(same_name_groups[:25], 1):
            digests = {exact_hash_by_path.get(str(path)) for path in group}
            status = "all exact copies" if len(digests) == 1 and None not in digests else "mixed or distinct files — review"
            report.append(f"### {index}. `{group[0].name}` — {len(group)} files ({status})")
            report.extend(display_paths([str(path) for path in group]))
            report.append("")
    else:
        report.append("No case-insensitive same-name collisions found.")

    report.extend([
        "",
        "## Safe next step",
        "",
        "Review the detailed collision groups and Serato references before building any move or deletion plan. Do not run `apply_plan.py --apply` yet.",
        "",
        f"Machine-readable evidence: `{DETAILS_PATH}`",
        "",
    ])
    REPORT_PATH.write_text("\n".join(report))

    print(f"Wrote {REPORT_PATH}")
    print(f"Wrote {DETAILS_PATH}")
    print(f"Exact groups: {len(exact_groups):,}; same-name groups: {len(same_name_groups):,}")


if __name__ == "__main__":
    main()
