#!/usr/bin/env python3
"""Read-only survey of a folder's _GENRE NEEDS CHECK backlogs (across
CLEAN/DIRTY/INSTRUMENTALS/REMIXES & EDITS/_NEEDS CHECK) using the same
classify_genre() alias-table logic as build_version_genre_plan.py. Prints a
confidence/genre breakdown and writes the full per-file classification to
--out for a follow-up apply pass to consume.

Built during the "music to move" backlog session (2026-08-16): 535 files
started in _GENRE NEEDS CHECK, 452 resolved across 11 iterative rounds of
alias-table matches + Brooke-confirmed artist/title-evidence batches. 83
remain -- no tag/filename evidence left to mine (blank tags with
unidentifiable artists, non-genre labels like "Spoken Word", junk strings
like "Noir22"/"TOKYO FLIPZ"/"GEOTHEORY"). Left in _GENRE NEEDS CHECK for a
future session; would need actual listening, not metadata work.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_version_genre_plan import classify_genre, load_genre_rules  # noqa: E402

from mutagen.easyid3 import EasyID3
from mutagen.flac import FLAC
from mutagen.id3 import ID3NoHeaderError
from mutagen.mp4 import MP4


def read_genre_artist(path: Path) -> tuple[str, str]:
    suffix = path.suffix.casefold()
    try:
        if suffix == ".mp3":
            try:
                tags = EasyID3(path)
            except ID3NoHeaderError:
                return "", ""
            return (tags.get("genre", [""])[0], tags.get("artist", [""])[0])
        if suffix == ".flac":
            tags = FLAC(path)
            return (tags.get("genre", [""])[0], tags.get("artist", [""])[0])
        if suffix == ".m4a":
            tags = MP4(path)
            genre = tags.get("\xa9gen", [""])
            artist = tags.get("\xa9ART", [""])
            return (genre[0] if genre else "", artist[0] if artist else "")
    except Exception as exc:  # noqa: BLE001
        return f"ERROR:{exc}", ""
    return "", ""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path, help="Folder to survey (e.g. 'music to move')")
    parser.add_argument("--out", required=True, type=Path, help="Where to write the full per-file JSON classification")
    parser.add_argument("--genre-rules", type=Path, help="Override path to a version-genre-rules.json")
    args = parser.parse_args()

    rules = load_genre_rules(args.genre_rules)
    sections = [
        ("CLEAN", args.root / "CLEAN" / "_GENRE NEEDS CHECK"),
        ("DIRTY", args.root / "DIRTY" / "_GENRE NEEDS CHECK"),
        ("INSTRUMENTALS", args.root / "INSTRUMENTALS" / "_GENRE NEEDS CHECK"),
        ("REMIXES & EDITS", args.root / "REMIXES & EDITS" / "_GENRE NEEDS CHECK"),
        ("_NEEDS CHECK", args.root / "_NEEDS CHECK" / "_GENRE NEEDS CHECK"),
    ]

    results = []
    for version, folder in sections:
        if not folder.is_dir():
            continue
        for file_path in sorted(folder.iterdir()):
            if file_path.suffix.casefold() not in (".mp3", ".flac", ".m4a"):
                continue
            genre, artist = read_genre_artist(file_path)
            proposed, evidence, confidence = classify_genre({"filename": file_path.name, "genre": genre}, rules, None)
            results.append(
                {
                    "path": str(file_path),
                    "version": version,
                    "current_genre": genre,
                    "artist": artist,
                    "proposed_genre": proposed,
                    "evidence": evidence,
                    "confidence": confidence,
                }
            )

    print("total files scanned:", len(results))
    print("confidence breakdown:", dict(Counter(r["confidence"] for r in results)))

    resolved = [r for r in results if r["proposed_genre"]]
    review = [r for r in results if not r["proposed_genre"]]

    print("\n=== resolved genre distribution ===")
    for genre, count in Counter(r["proposed_genre"] for r in resolved).most_common():
        print(f"  {count:4} | {genre}")

    print("\n=== review reasons breakdown ===")
    for reason, count in Counter(r["evidence"] for r in review).most_common(40):
        print(f"  {count:4} | {reason}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
