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

Neither flag deletes anything. Categorization is deliberately conservative:
an audit tool that guesses "safe" is worse than useless.
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

REPO = Path(__file__).resolve().parent.parent
MUSIC_ROOT = Path("/Users/djb.a.e/Music/DJ-Music/Music").resolve()
CODEX_ROOT = Path("/Users/djb.a.e/.codex/visualizations").resolve()
HANDOFF_DOC = REPO / "handoff" / "HANDOFF-DJ-MUSIC-SORT.md"
AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".wav", ".aiff"}

BACKUP_NAME_RE = re.compile(r"backup|tag-file-backups", re.IGNORECASE)
ALWAYS_KEEP_RE = re.compile(r"-backlog|needs-check-recheck", re.IGNORECASE)


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backups", action="store_true")
    parser.add_argument("--duplicates", action="store_true")
    parser.add_argument("--full", action="store_true", help="duplicates: ignore baseline, report every group found")
    parser.add_argument(
        "--baseline", type=Path,
        default=REPO / "handoff/session-2026-08-15/survey/library-wide-dedup/duplicate-groups.json",
    )
    parser.add_argument("--out-dir", type=Path, default=REPO / "handoff/maintenance-audits")
    args = parser.parse_args()

    if not args.backups and not args.duplicates:
        args.backups = args.duplicates = True

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")

    if args.backups:
        print("=== Backup audit ===")
        run_backups_audit(args.out_dir / f"backup-audit-{stamp}.json")
    if args.duplicates:
        print("\n=== Duplicate audit ===")
        run_duplicates_audit(args.out_dir / f"duplicate-audit-{stamp}.json", args.baseline, args.full)


if __name__ == "__main__":
    main()
