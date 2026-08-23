#!/usr/bin/env python3
"""Survey new/unsorted music (default root: "music to move") for the
mechanical, deterministic fixes established during the 2026-08-16 library
cleanup, so they can run automatically on future downloads instead of by
hand each time:

  - deterministic transformation/version detection from explicit filename
    wording -> plain Grouping terms (Remix, Edit, Bootleg, Extended,
    Instrumental, Acapella, Clean, Dirty, and approved edit forms)
  - Radio Edit, Flip, and ReWork remain reviewable rather than being silently
    collapsed into Edit
  - Clean/Dirty detection from an explicit "(Clean)"/"(Dirty)" filename
    label -> Grouping tag (never inferred from absence of a label)
  - Genre-from-folder: ONLY when every other already-tagged file in the
    same immediate parent folder unanimously agrees on one genre value
    (min 2 agreeing siblings, zero disagreement) -- otherwise left for
    manual review, exactly like every genre write done by hand today
  - exact-duplicate detection against the rest of the live library by
    SHA256 (report only -- this script never deletes anything)

Read-only. Writes a plan JSON for apply_new_music_intake_plan.py to consume.
Anything not high-confidence goes to "needs_review", never guessed.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "preview-only"

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.mp4 import MP4

from build_version_genre_plan import classify_grouping, load_genre_rules

AUDIO_EXTS = {".mp3", ".flac", ".m4a"}
PLACEHOLDER_GENRES = {"", "other", "unknown", "n/a"}
BACKLOG_MARKERS = ("needs check", "genre needs check")

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def read_genre(path: Path) -> str:
    suffix = path.suffix.casefold()
    try:
        if suffix == ".mp3":
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                return ""
            frames = tags.getall("TCON")
            return str(frames[0]).strip() if frames else ""
        if suffix == ".flac":
            values = FLAC(path).get("genre", [])
            return str(values[0]).strip() if values else ""
        if suffix == ".m4a":
            values = MP4(path).get("\xa9gen", [])
            return str(values[0]).strip() if values else ""
    except Exception:
        return ""
    return ""


def read_grouping(path: Path) -> list[str]:
    suffix = path.suffix.casefold()
    raw: list[str] = []
    try:
        if suffix == ".mp3":
            try:
                tags = ID3(path)
            except ID3NoHeaderError:
                return []
            for frame in tags.getall("TIT1"):
                text = getattr(frame, "text", [])
                raw.extend(str(v).strip() for v in text if str(v).strip())
        elif suffix == ".flac":
            raw = [str(v).strip() for v in FLAC(path).get("grouping", []) if str(v).strip()]
        elif suffix == ".m4a":
            raw = [str(v).strip() for v in MP4(path).get("\xa9grp", []) if str(v).strip()]
    except Exception:
        return []
    parts = []
    for value in raw:
        parts.extend(p.strip() for p in value.split(";") if p.strip())
    return parts


def in_backlog_path(path_str: str) -> bool:
    lower = path_str.lower()
    return any(marker in lower for marker in BACKLOG_MARKERS)


def build_live_library_index(library_root: Path, exclude_root: Path) -> dict[str, list[str]]:
    """filename -> [full paths], across the whole live library EXCLUDING
    exclude_root itself (so intake files don't just match themselves)."""
    index: dict[str, list[str]] = defaultdict(list)
    exclude_resolved = exclude_root.resolve()
    for dirpath, dirnames, filenames in os.walk(library_root):
        dp = Path(dirpath).resolve()
        try:
            dp.relative_to(exclude_resolved)
            continue
        except ValueError:
            pass
        for fn in filenames:
            if fn.lower().endswith(tuple(AUDIO_EXTS)) and not fn.startswith("._"):
                index[fn].append(os.path.join(dirpath, fn))
    return index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", required=True, type=Path,
                         help="Where new/unsorted downloads land")
    parser.add_argument("--library-root", required=True, type=Path,
                         help="Library root used only for report-only exact-duplicate checking")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--skip-duplicate-check", action="store_true",
                         help="Skip the whole-library hash scan (faster, no duplicate info)")
    args = parser.parse_args()

    root = args.root.resolve()
    library_root = args.library_root.resolve()
    metadata_rules = load_genre_rules(None)
    try:
        root.relative_to(library_root)
    except ValueError as exc:
        raise SystemExit(f"--root must be inside --library-root: {root} vs {library_root}") from exc

    print("indexing genre siblings within the intake root...", flush=True)
    siblings_by_dir: dict[str, Counter] = defaultdict(Counter)
    all_files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        if in_backlog_path(dirpath):
            continue
        for fn in filenames:
            if not fn.lower().endswith(tuple(AUDIO_EXTS)) or fn.startswith("._"):
                continue
            full = Path(dirpath) / fn
            all_files.append(full)
            genre = read_genre(full)
            if genre.strip().lower() not in PLACEHOLDER_GENRES:
                siblings_by_dir[dirpath][genre.strip()] += 1

    live_index = None
    if not args.skip_duplicate_check:
        print("indexing the rest of the live library for duplicate detection...", flush=True)
        live_index = build_live_library_index(library_root, root)

    items = []
    needs_review = []
    print(f"scanning {len(all_files)} intake files...", flush=True)

    for i, path in enumerate(all_files, start=1):
        rel = str(path.relative_to(root))
        genre = read_genre(path)
        grouping = read_grouping(path)

        fix = {"path": str(path), "relative_path": rel, "sha256": sha256_file(path)}
        review_reasons = []

        # --- genre-from-sibling-consensus (high confidence only) ---
        if genre.strip().lower() in PLACEHOLDER_GENRES:
            counts = siblings_by_dir.get(str(path.parent), Counter())
            if len(counts) == 1 and sum(counts.values()) >= 2:
                proposed = next(iter(counts))
                fix["genre_fix"] = {"from": genre, "to": proposed, "basis": f"{counts[proposed]} sibling(s) agree"}
            elif counts:
                review_reasons.append(f"genre placeholder, siblings disagree: {dict(counts)}")
            else:
                review_reasons.append("genre placeholder, no tagged siblings in this folder to infer from")

        # --- remix/edit type + clean/dirty grouping terms ---
        grouping_analysis = classify_grouping(path.name, "", grouping, metadata_rules)
        terms_to_add = grouping_analysis["add_terms"]
        review_reasons.extend(grouping_analysis["review_reasons"])
        if terms_to_add:
            fix["grouping_fix"] = {"add_terms": terms_to_add, "existing": grouping}

        # --- duplicate detection (info only, never auto-deleted) ---
        if live_index is not None:
            candidates = live_index.get(path.name, [])
            if candidates:
                try:
                    this_hash = sha256_file(path)
                    matches = []
                    for cand in candidates:
                        try:
                            if sha256_file(Path(cand)) == this_hash:
                                matches.append(cand)
                        except OSError:
                            pass
                    if matches:
                        fix["duplicate_of"] = matches
                except OSError:
                    pass

        if "genre_fix" in fix or "grouping_fix" in fix or "duplicate_of" in fix:
            items.append(fix)
        if review_reasons:
            needs_review.append({"path": str(path), "relative_path": rel, "reasons": review_reasons})

        if i % 500 == 0:
            print(f"  scanned {i}/{len(all_files)}...", flush=True)

    plan = {
        "mode": "preview-only",
        "root": str(root),
        "library_root": str(library_root),
        "rules": {
            "approval_required": True,
            "genre_requires_unanimous_sibling_consensus": True,
            "clean_dirty_only_from_explicit_filename_label": True,
            "duplicates_never_auto_deleted": True,
        },
        "counts": {
            "scanned": len(all_files),
            "with_a_fix": len(items),
            "genre_fixes": sum(1 for i in items if "genre_fix" in i),
            "grouping_fixes": sum(1 for i in items if "grouping_fix" in i),
            "duplicate_flags": sum(1 for i in items if "duplicate_of" in i),
            "needs_review": len(needs_review),
        },
        "items": items,
        "needs_review": needs_review,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(plan["counts"], indent=2))
    print(f"written to {args.out}")


if __name__ == "__main__":
    main()
