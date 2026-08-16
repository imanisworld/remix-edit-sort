#!/usr/bin/env python3
"""Recurring housekeeping audit: backup cruft + library duplicates.

Read-only. Never deletes, moves, or writes anything -- it only reports, so a
human (or a follow-up tool with its own safety pattern) can act on the
findings. Meant to be cheap enough to re-run anytime instead of spinning up
a research agent to redo this by hand (that's what this replaces -- see
handoff/session-2026-08-15/survey/backup-audit-20260816 for the one-off
version this automates).

Two independent checks:

--backups   Scans for backup-ish directories under this repo's
            handoff/session-*/survey/ tree AND ~/.codex/visualizations/,
            since both accumulate them and only the repo's are git-tracked.
            For each, looks for sibling result/undo-log files in the same
            parent directory and checks whether those are committed to git
            (repo) or just present on disk (.codex, which isn't a git repo --
            treated as evidence-of-completion only when a HANDOFF.md
            "Completed: <folder>" section references it). Anything matching
            a known-in-progress name pattern (e.g. "*-backlog") is always
            KEEP regardless of git state, since multi-step work in progress
            legitimately looks "uncommitted" the whole time it's running.

--duplicates
            Whole-file SHA-256 sweep across the Music library (the ONLY
            verdict this reports is EXACT_DUPLICATE per
            scripts/duplicate_semantics.py's vocabulary -- never guesses at
            AUDIO_EQUIVALENT/RELATED_VERSION). Diffs against the last saved
            snapshot (--baseline) and reports only NEW groups since then, so
            re-running this doesn't dump the same already-triaged 122 groups
            every time. Pass --full to ignore the baseline and report
            everything found this run.

--tags      Metadata-consistency sweep over Genre/BPM/Key/Grouping across
            --library-root (default the whole Music library). The point is
            Smart Crates: Serato matches tag values, not fuzzy/contains
            logic, so "House" vs "house" vs "House " silently splits a track
            out of a rule that should have caught it. Checks:
              * Genre values against references/genre-rules.json's
                canonical_genres list -- reports case/whitespace variants of
                a known canonical genre separately from genuinely unknown
                values (never auto-merges either; a human decides what's a
                typo vs a new genre).
              * Empty Genre / empty BPM / empty Key counts.
              * Key notation split (Camelot e.g. "8A" vs standard e.g. "Abm")
                -- mixed notation breaks any key-based Smart Crate rule.
              * Distinct Grouping values in use and their counts, so tagging
                a new field doesn't silently collide with the existing
                Clean/Dirty convention (see apply_clean_dirty_grouping.py).
            Non-canonical genre values are diffed against --tags-baseline
            the same way --duplicates diffs against its baseline: already-
            seen values stay quiet after a human has reviewed them. Nothing
            is added to the baseline automatically -- pass --update-baseline
            once you've reviewed a run's non-canonical values and decided
            they're acceptable, and only the values seen in *that* run are
            recorded as known-going-forward.

None of the three flags write, move, or delete anything under the library.
Categorization is deliberately conservative: an audit tool that guesses
"safe" is worse than useless.
"""

from __future__ import annotations

# Read by scripts/mutation_surface.py. Read-only: never deletes, moves, or
# writes anything under Music/ -- only reports. See the module docstring.
SAFETY_CLASSIFICATION = "read-only"

import argparse
import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.mp4 import MP4

REPO = Path(__file__).resolve().parent.parent
MUSIC_ROOT = Path("/Users/djb.a.e/Music/DJ-Music/Music").resolve()
CODEX_ROOT = Path("/Users/djb.a.e/.codex/visualizations").resolve()
HANDOFF_DOC = REPO / "handoff" / "HANDOFF-DJ-MUSIC-SORT.md"
GENRE_RULES_PATH = REPO / "references" / "genre-rules.json"
AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".wav", ".aiff"}

BACKUP_NAME_RE = re.compile(r"backup|tag-file-backups", re.IGNORECASE)
ALWAYS_KEEP_RE = re.compile(r"-backlog|needs-check-recheck", re.IGNORECASE)
CAMELOT_KEY_RE = re.compile(r"^(1[0-2]|[1-9])[ab]$", re.IGNORECASE)
STANDARD_KEY_RE = re.compile(r"^[a-g](#|b)?\s*(maj|major|min|minor|m)?$", re.IGNORECASE)
TAG_SAMPLE_CAP = 10


def du_sh(path: Path) -> int:
    """Total size in bytes via os.walk (avoids shelling out to du per dir)."""
    total = 0
    for p in path.rglob("*"):
        if p.is_file() and not p.is_symlink():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}PB"


def find_backup_dirs(root: Path) -> list[Path]:
    found = []
    for p in root.rglob("*"):
        if p.is_dir() and BACKUP_NAME_RE.search(p.name):
            found.append(p)
    return found


def git_tracks_any(paths: list[Path]) -> str | None:
    """Returns the commit hash+message that most recently touched any of
    these paths, or None if none are tracked by git."""
    if not paths:
        return None
    result = subprocess.run(
        ["git", "log", "-1", "--format=%h %s", "--"] + [str(p) for p in paths],
        cwd=REPO, capture_output=True, text=True,
    )
    line = result.stdout.strip()
    return line or None


def sibling_evidence_files(backup_dir: Path) -> list[Path]:
    """result-*.json / undo-*.jsonl / *-result-*.json siblings in the same
    parent directory as the backup dir -- these are what prove a pass ran
    and (if git-tracked) that it was committed."""
    parent = backup_dir.parent
    siblings = []
    for pattern in ("*result*.json", "*undo*.jsonl", "*result*.jsonl"):
        siblings.extend(parent.glob(pattern))
    return siblings


def audit_repo_backups() -> list[dict]:
    survey_root = REPO / "handoff"
    entries = []
    for backup_dir in find_backup_dirs(survey_root):
        rel = backup_dir.relative_to(REPO)
        if ALWAYS_KEEP_RE.search(str(rel)):
            entries.append({
                "path": str(backup_dir), "location": "repo",
                "size_bytes": du_sh(backup_dir),
                "status": "KEEP", "evidence": "matches known-in-progress pattern",
            })
            continue
        siblings = sibling_evidence_files(backup_dir)
        commit = git_tracks_any(siblings)
        if commit:
            entries.append({
                "path": str(backup_dir), "location": "repo",
                "size_bytes": du_sh(backup_dir),
                "status": "SAFE_TO_DELETE", "evidence": commit,
            })
        else:
            entries.append({
                "path": str(backup_dir), "location": "repo",
                "size_bytes": du_sh(backup_dir),
                "status": "UNCERTAIN", "evidence": "no git-tracked sibling result/undo file found",
            })
    return entries


def audit_codex_backups() -> list[dict]:
    if not CODEX_ROOT.is_dir():
        return []
    handoff_text = HANDOFF_DOC.read_text(encoding="utf-8") if HANDOFF_DOC.is_file() else ""
    entries = []
    for backup_dir in find_backup_dirs(CODEX_ROOT):
        # folder name two levels up from a .../survey/<folder>/<backup-dir> layout
        try:
            folder_name = backup_dir.parent.name
        except IndexError:
            folder_name = backup_dir.parent.name
        completed_marker = f"Completed: {folder_name}".casefold()
        alt_marker = f'completed: "{folder_name}"'.casefold()
        if completed_marker in handoff_text.casefold() or alt_marker in handoff_text.casefold():
            entries.append({
                "path": str(backup_dir), "location": "codex",
                "size_bytes": du_sh(backup_dir),
                "status": "SAFE_TO_DELETE",
                "evidence": f'HANDOFF.md "Completed: {folder_name}" section',
            })
        else:
            entries.append({
                "path": str(backup_dir), "location": "codex",
                "size_bytes": du_sh(backup_dir),
                "status": "UNCERTAIN",
                "evidence": f'no HANDOFF.md "Completed: {folder_name}" section found',
            })
    return entries


def run_backups_audit(out_path: Path) -> dict:
    entries = audit_repo_backups() + audit_codex_backups()
    by_status: dict[str, list[dict]] = {"SAFE_TO_DELETE": [], "KEEP": [], "UNCERTAIN": []}
    for e in entries:
        by_status[e["status"]].append(e)

    summary = {
        "mode": "backup-audit",
        "generated_at_note": "pass --stamp if you need this recorded; timestamps are supplied by the caller, not this script",
        "counts": {k: len(v) for k, v in by_status.items()},
        "total_bytes": {k: sum(e["size_bytes"] for e in v) for k, v in by_status.items()},
        "entries": by_status,
    }
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"SAFE_TO_DELETE: {len(by_status['SAFE_TO_DELETE'])} dirs, "
          f"{human_size(summary['total_bytes']['SAFE_TO_DELETE'])}")
    print(f"KEEP:           {len(by_status['KEEP'])} dirs, "
          f"{human_size(summary['total_bytes']['KEEP'])}")
    print(f"UNCERTAIN:      {len(by_status['UNCERTAIN'])} dirs, "
          f"{human_size(summary['total_bytes']['UNCERTAIN'])} (needs a human look, not auto-deletable)")
    print(f"\nfull report: {out_path}")
    return summary


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run_duplicates_audit(out_path: Path, baseline_path: Path | None, full: bool) -> dict:
    known_hashes: set[str] = set()
    if baseline_path and baseline_path.is_file() and not full:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        for group in baseline.get("cross_folder_groups", []) + baseline.get("same_folder_groups", []):
            known_hashes.add(group["sha256"])

    by_hash: dict[str, list[Path]] = {}
    scanned = 0
    for p in MUSIC_ROOT.rglob("*"):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS and not p.is_symlink():
            scanned += 1
            h = sha256_file(p)
            by_hash.setdefault(h, []).append(p)

    new_groups = []
    for h, paths in by_hash.items():
        if len(paths) > 1 and h not in known_hashes:
            new_groups.append({
                "sha256": h,
                "count": len(paths),
                "paths": [str(p) for p in paths],
                "folders": sorted({p.relative_to(MUSIC_ROOT).parts[0] for p in paths}),
            })

    summary = {
        "mode": "duplicate-audit",
        "files_scanned": scanned,
        "baseline_used": str(baseline_path) if (baseline_path and not full) else None,
        "new_duplicate_groups": len(new_groups),
        "groups": new_groups,
    }
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"files scanned: {scanned}")
    print(f"NEW duplicate groups since baseline: {len(new_groups)}"
          + ("" if not full else " (--full: baseline ignored, this is everything found)"))
    print(f"\nfull report: {out_path}")
    return summary


def load_canonical_genres() -> list[str]:
    data = json.loads(GENRE_RULES_PATH.read_text(encoding="utf-8"))
    return data["canonical_genres"]


def read_genre_bpm_key(path: Path) -> tuple[str, str, str]:
    """Genre/BPM/Key via ID3 first, falling back to mutagen's generic "easy"
    tags for non-ID3 formats -- the same extraction path inventory_library.py
    uses, already proven across this library's MP3/M4A/FLAC mix."""
    try:
        tags = ID3(path)
        genre = str(tags.getall("TCON")[0]).strip() if tags.getall("TCON") else ""
        bpm = str(tags.getall("TBPM")[0]).strip() if tags.getall("TBPM") else ""
        key = str(tags.getall("TKEY")[0]).strip() if tags.getall("TKEY") else ""
        return genre, bpm, key
    except Exception:
        pass
    try:
        easy = MutagenFile(path, easy=True)
        tags = getattr(easy, "tags", {}) or {}
        take = lambda name: str((tags.get(name) or [""])[0]).strip()
        return take("genre"), take("bpm"), take("initialkey")
    except Exception:
        return "", "", ""


def _split_grouping(raw_values: list[str]) -> list[str]:
    parts = []
    for raw in raw_values:
        parts.extend(p.strip() for p in raw.split(";") if p.strip())
    return parts


def read_grouping_values(path: Path) -> list[str]:
    suffix = path.suffix.casefold()
    try:
        if suffix == ".mp3":
            tags = ID3(path)
            raw = [
                str(v).strip()
                for frame in tags.getall("TIT1")
                for v in getattr(frame, "text", [])
                if str(v).strip()
            ]
            return _split_grouping(raw)
        if suffix == ".m4a":
            raw = [str(v).strip() for v in MP4(path).get("\xa9grp", []) if str(v).strip()]
            return _split_grouping(raw)
        if suffix == ".flac":
            raw = [str(v).strip() for v in FLAC(path).get("grouping", []) if str(v).strip()]
            return _split_grouping(raw)
    except Exception:
        pass
    return []


def classify_key(value: str) -> str:
    v = value.strip()
    if not v:
        return "empty"
    if CAMELOT_KEY_RE.match(v):
        return "camelot"
    if STANDARD_KEY_RE.match(v):
        return "standard"
    return "unrecognized"


def run_tags_audit(
    out_path: Path, library_root: Path, baseline_path: Path | None, full: bool, update_baseline: bool,
) -> dict:
    canonical = load_canonical_genres()
    canonical_by_fold = {g.casefold(): g for g in canonical}

    known_variants: set[str] = set()
    if baseline_path and baseline_path.is_file() and not full:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        known_variants = set(baseline.get("known_non_canonical_genre_values", []))

    genre_groups: dict[str, dict] = {}
    empty_genre_count = 0
    empty_genre_sample: list[str] = []
    empty_bpm_count = 0
    empty_bpm_sample: list[str] = []
    key_counts = {"camelot": 0, "standard": 0, "unrecognized": 0, "empty": 0}
    key_samples: dict[str, list[str]] = {"camelot": [], "standard": [], "unrecognized": [], "empty": []}
    grouping_counts: dict[str, int] = {}
    scanned = 0

    for p in sorted(library_root.rglob("*")):
        if not (p.is_file() and p.suffix.lower() in AUDIO_EXTS and not p.is_symlink()):
            continue
        scanned += 1
        genre, bpm, key = read_genre_bpm_key(p)

        if genre:
            fold = genre.casefold().strip()
            group = genre_groups.setdefault(fold, {"raw_values": set(), "count": 0, "sample_paths": []})
            group["raw_values"].add(genre)
            group["count"] += 1
            if len(group["sample_paths"]) < TAG_SAMPLE_CAP:
                group["sample_paths"].append(str(p))
        else:
            empty_genre_count += 1
            if len(empty_genre_sample) < TAG_SAMPLE_CAP:
                empty_genre_sample.append(str(p))

        if not bpm.strip():
            empty_bpm_count += 1
            if len(empty_bpm_sample) < TAG_SAMPLE_CAP:
                empty_bpm_sample.append(str(p))

        key_class = classify_key(key)
        key_counts[key_class] += 1
        if len(key_samples[key_class]) < TAG_SAMPLE_CAP:
            key_samples[key_class].append(str(p))

        for value in read_grouping_values(p):
            grouping_counts[value] = grouping_counts.get(value, 0) + 1

    canonical_exact: set[str] = set()
    case_or_whitespace_variants = []
    non_canonical = []
    for fold, group in genre_groups.items():
        canonical_name = canonical_by_fold.get(fold)
        if canonical_name is None:
            non_canonical.append({
                "value_seen": sorted(group["raw_values"]),
                "count": group["count"],
                "sample_paths": group["sample_paths"],
                "is_new_since_baseline": fold not in known_variants,
            })
        elif group["raw_values"] != {canonical_name}:
            case_or_whitespace_variants.append({
                "canonical": canonical_name,
                "value_seen": sorted(group["raw_values"]),
                "count": group["count"],
                "sample_paths": group["sample_paths"],
            })
        else:
            canonical_exact.add(canonical_name)

    non_canonical.sort(key=lambda e: -e["count"])
    shown_non_canonical = non_canonical if full else [e for e in non_canonical if e["is_new_since_baseline"]]
    suppressed_count = len(non_canonical) - len(shown_non_canonical)

    summary = {
        "mode": "tag-audit",
        "library_root": str(library_root),
        "files_scanned": scanned,
        "canonical_genres_matched_exactly": sorted(canonical_exact),
        "genre_case_or_whitespace_variants": sorted(case_or_whitespace_variants, key=lambda e: -e["count"]),
        "genre_non_canonical": shown_non_canonical,
        "genre_non_canonical_suppressed_by_baseline": suppressed_count,
        "empty_genre": {"count": empty_genre_count, "sample_paths": empty_genre_sample},
        "empty_bpm": {"count": empty_bpm_count, "sample_paths": empty_bpm_sample},
        "key_notation": {
            "counts": key_counts,
            "mixed_camelot_and_standard": key_counts["camelot"] > 0 and key_counts["standard"] > 0,
            "samples": key_samples,
        },
        "grouping_values_in_use": dict(sorted(grouping_counts.items(), key=lambda kv: -kv[1])),
        "baseline_used": str(baseline_path) if (baseline_path and not full) else None,
        "baseline_updated": False,
    }

    if update_baseline and baseline_path:
        all_non_canonical_folds = sorted(
            fold for fold, group in genre_groups.items() if canonical_by_fold.get(fold) is None
        )
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps({"known_non_canonical_genre_values": all_non_canonical_folds}, indent=2) + "\n",
            encoding="utf-8",
        )
        summary["baseline_updated"] = True

    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"files scanned: {scanned}")
    print(
        f"genre: {len(canonical_exact)} exact-canonical, "
        f"{len(case_or_whitespace_variants)} case/whitespace variant groups, "
        f"{len(shown_non_canonical)} non-canonical groups shown"
        + (f" ({suppressed_count} already-known, suppressed -- pass --full to see them)" if suppressed_count else "")
    )
    print(f"empty genre: {empty_genre_count}   empty bpm: {empty_bpm_count}   empty key: {key_counts['empty']}")
    print(
        f"key notation: {key_counts['camelot']} camelot, {key_counts['standard']} standard, "
        f"{key_counts['unrecognized']} unrecognized"
        + ("  <!> mixed camelot+standard in use" if key_counts["camelot"] and key_counts["standard"] else "")
    )
    print(f"grouping values in use: {summary['grouping_values_in_use']}")
    if update_baseline:
        print(f"\nbaseline updated: {baseline_path}")
    print(f"\nfull report: {out_path}")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backups", action="store_true")
    parser.add_argument("--duplicates", action="store_true")
    parser.add_argument("--tags", action="store_true")
    parser.add_argument(
        "--full", action="store_true",
        help="duplicates/tags: ignore baseline, report everything found this run",
    )
    parser.add_argument(
        "--baseline", type=Path,
        default=REPO / "handoff/session-2026-08-15/survey/library-wide-dedup/duplicate-groups.json",
        help="duplicates: known-duplicate-hash snapshot to diff against",
    )
    parser.add_argument(
        "--library-root", type=Path, default=MUSIC_ROOT,
        help="tags: root to scan (defaults to the whole Music library)",
    )
    parser.add_argument(
        "--tags-baseline", type=Path,
        default=REPO / "handoff/maintenance-audits/tag-audit-baseline.json",
        help="tags: known non-canonical-genre-value snapshot to diff against",
    )
    parser.add_argument(
        "--update-baseline", action="store_true",
        help="tags: after reviewing a run's non-canonical genre values, record them as accepted so future runs only surface newly-introduced drift",
    )
    parser.add_argument("--out-dir", type=Path, default=REPO / "handoff/maintenance-audits")
    args = parser.parse_args()

    if not args.backups and not args.duplicates and not args.tags:
        args.backups = args.duplicates = args.tags = True

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")

    if args.backups:
        print("=== Backup audit ===")
        run_backups_audit(args.out_dir / f"backup-audit-{stamp}.json")
    if args.duplicates:
        print("\n=== Duplicate audit ===")
        run_duplicates_audit(args.out_dir / f"duplicate-audit-{stamp}.json", args.baseline, args.full)
    if args.tags:
        print("\n=== Tag audit ===")
        run_tags_audit(
            args.out_dir / f"tag-audit-{stamp}.json", args.library_root, args.tags_baseline,
            args.full, args.update_baseline,
        )


if __name__ == "__main__":
    main()
