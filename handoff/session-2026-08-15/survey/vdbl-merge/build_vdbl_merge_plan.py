#!/usr/bin/env python3
"""Build (not apply) the plan for merging VDBL's sorted tracks into the main
genre library. Read-only: computes hashes and destinations, writes plan.json
and a human-readable preview. No files moved, no tags written.

Destination rules (confirmed with the user after checking actual folder
contents -- Hip Hop's "2024"/"2025" folders turned out to hold only
Serato Stems, not regular tracks; R&b has no Clean/DIRTY split at all):

  Hip-Hop  -> Hip Hop/<Clean|DIRTY|INSTRUMENTALS|ACAPELLAS>/Hip-Hop/<file>
              (mirrors VDBL's own bucket structure exactly)
  R&B      -> R&b/<file>                     (flat, version stays in filename)
  Amapiano -> Amapiano /<file>                (flat; note trailing space)
  Country  -> Country/<file>                  (flat; new folder)
  _GENRE NEEDS CHECK -> excluded entirely, left in VDBL for manual review

TDRC year is written only for Hip-Hop/R&B tracks with confirmed evidence in
release-year-evidence.json, matched via the original apply's undo log (which
still has each file's pre-sort release-folder path).
"""
import hashlib
import json
import re
from pathlib import Path

REPO = Path("/Users/djb.a.e/Music/DJ-Music/Tools/remix-edit-sort")
LIBRARY = Path("/Users/djb.a.e/Music/DJ-Music/Music")
VDBL = LIBRARY / "Events" / "VDBL"
UNDO_LOG = REPO / "handoff/session-2026-08-15/vdbl-undo-20260815-184344.jsonl"
EVIDENCE = REPO / "references/release-year-evidence.json"
OUT_DIR = REPO / "handoff/session-2026-08-15/survey/vdbl-merge"

HIPHOP_BUCKET_MAP = {
    "CLEAN": "Clean",
    "DIRTY": "DIRTY",
    "INSTRUMENTALS": "INSTRUMENTALS",
    "ACAPELLAS": "ACAPELLAS",
}

DEST_ROOTS = {
    "Hip-Hop": LIBRARY / "Hip Hop",
    "R&B": LIBRARY / "R&b",
    "Amapiano": LIBRARY / "Amapiano ",
    "Country": LIBRARY / "Country",
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_undo_log() -> dict:
    """dest path (str, resolved) -> release folder name (the path segment
    right after VDBL/ in the ORIGINAL pre-sort path)."""
    mapping = {}
    with UNDO_LOG.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            src = Path(rec["src"])
            dest = Path(rec["dest"])
            try:
                rel = src.relative_to(VDBL)
            except ValueError:
                continue
            release_folder = rel.parts[0] if rel.parts else None
            mapping[str(dest.resolve())] = release_folder
    return mapping


def load_evidence() -> dict:
    data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    return data.get("releases", {})


STOPWORDS = {"feat", "featuring", "ft", "with", "clean", "dirty", "intro", "outro",
             "remix", "edit", "flip", "version", "the", "a", "an", "and"}


def title_tokens(filename: str) -> set:
    """Coarse normalized-title token set: strip extension/brackets/leading
    punctuation, lowercase, drop stopwords and single-char tokens. Used only
    as an advisory "these might be the same song" signal for human review --
    never as an automated duplicate/delete decision (never dedupe by
    filename alone)."""
    stem = Path(filename).stem
    stem = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", stem)  # drop bracketed content
    stem = re.sub(r"[^a-z0-9]+", " ", stem.lower())
    return {t for t in stem.split() if t not in STOPWORDS and len(t) > 1}


def find_title_overlaps(filename: str, existing_files: list, min_jaccard: float = 0.6) -> list:
    src_tokens = title_tokens(filename)
    if not src_tokens:
        return []
    hits = []
    for existing in existing_files:
        other_tokens = title_tokens(existing.name)
        if not other_tokens:
            continue
        union = src_tokens | other_tokens
        jaccard = len(src_tokens & other_tokens) / len(union) if union else 0
        if jaccard >= min_jaccard:
            hits.append({"existing_path": str(existing), "title_token_jaccard": round(jaccard, 2)})
    return hits


def existing_dest_index(root: Path) -> dict:
    """filename (casefolded) -> list of existing file Paths under root,
    recursive. Used for collision/duplicate detection."""
    index = {}
    if not root.is_dir():
        return index
    for p in root.rglob("*"):
        if p.is_file() and not p.name.startswith("."):
            index.setdefault(p.name.casefold(), []).append(p)
    return index


def all_existing_files(root: Path) -> list:
    if not root.is_dir():
        return []
    return [p for p in root.rglob("*") if p.is_file() and not p.name.startswith(".")]


def main():
    undo_map = load_undo_log()
    evidence = load_evidence()

    items = []
    counts = {"by_genre": {}, "by_bucket": {}, "year_written": 0, "year_unresolved": 0,
              "exact_duplicate_at_dest": 0, "name_collision_diff_content": 0, "clean_moves": 0,
              "possible_title_overlap": 0}

    dest_indexes = {genre: existing_dest_index(root) for genre, root in DEST_ROOTS.items()}
    dest_all_files = {genre: all_existing_files(root) for genre, root in DEST_ROOTS.items()}

    for bucket_dir_name in ("CLEAN", "DIRTY", "INSTRUMENTALS", "ACAPELLAS"):
        bucket_dir = VDBL / bucket_dir_name
        if not bucket_dir.is_dir():
            continue
        for genre_dir in sorted(p for p in bucket_dir.iterdir() if p.is_dir()):
            genre = genre_dir.name
            if genre == "_GENRE NEEDS CHECK":
                continue
            if genre not in DEST_ROOTS:
                items.append({"source": str(genre_dir), "error": f"unrecognized genre folder {genre!r}, skipping"})
                continue
            for f in sorted(genre_dir.iterdir()):
                if not f.is_file() or f.name in (".DS_Store",) or f.name.startswith("._"):
                    continue

                if genre == "Hip-Hop":
                    hh_bucket = HIPHOP_BUCKET_MAP[bucket_dir_name]
                    destination = DEST_ROOTS["Hip-Hop"] / hh_bucket / "Hip-Hop" / f.name
                else:
                    destination = DEST_ROOTS[genre] / f.name

                release_folder = undo_map.get(str(f.resolve()))
                year_info = evidence.get(release_folder) if release_folder else None

                write_year = None
                if genre in ("Hip-Hop", "R&B"):
                    if year_info:
                        write_year = year_info["year"]
                        counts["year_written"] += 1
                    else:
                        counts["year_unresolved"] += 1

                src_hash = sha256_file(f)
                existing_matches = dest_indexes[genre].get(f.name.casefold(), [])
                collision = None
                if existing_matches:
                    for m in existing_matches:
                        m_hash = sha256_file(m)
                        if m_hash == src_hash:
                            collision = {"type": "EXACT_DUPLICATE", "existing_path": str(m)}
                            counts["exact_duplicate_at_dest"] += 1
                            break
                    else:
                        collision = {"type": "NAME_COLLISION_DIFFERENT_CONTENT",
                                     "existing_paths": [str(m) for m in existing_matches]}
                        counts["name_collision_diff_content"] += 1
                else:
                    counts["clean_moves"] += 1

                title_overlaps = []
                if collision is None:  # exact-name collision already covers the exact-match case
                    title_overlaps = find_title_overlaps(f.name, dest_all_files[genre])
                    if title_overlaps:
                        counts["possible_title_overlap"] += 1

                counts["by_genre"][genre] = counts["by_genre"].get(genre, 0) + 1
                counts["by_bucket"][bucket_dir_name] = counts["by_bucket"].get(bucket_dir_name, 0) + 1

                items.append({
                    "source": str(f),
                    "destination": str(destination),
                    "sha256": src_hash,
                    "genre": genre,
                    "version_bucket": bucket_dir_name,
                    "release_folder": release_folder,
                    "year_evidence": year_info,
                    "write_tdrc_year": write_year,
                    "collision": collision,
                    "possible_title_overlaps": title_overlaps,
                })

    plan = {
        "mode": "preview-only",
        "rules": {"approval_required": True},
        "source_root": str(VDBL),
        "items": items,
        "counts": counts,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "plan.json").write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({"mode": "dry-run", "tracks": len(items), **counts}, indent=2))


if __name__ == "__main__":
    main()
