#!/usr/bin/env python3
"""Stage official Serato/Rekordbox History exports for Grok set building.

Read-only toward DJ app data. Copies only official export files
(csv / txt / m3u / m3u8) into a private staging directory outside Music and
outside Git. Never opens .session files, history.database, master.sqlite,
diagnostic logs, or collection XML as play logs.

Purpose for the set bot: once official exports are staged, Grok can see
what actually played in a room, how tracks were sequenced, and which titles
have already been overplayed — without guessing from crates or PlayCount.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
from datetime import datetime, timezone
from pathlib import Path

EXPORT_SUFFIXES = {".csv", ".txt", ".m3u", ".m3u8"}
BLOCKED_NAMES = {
    "history.database",
    "master.sqlite",
    "master.db",
}
BLOCKED_SUFFIXES = {".session", ".sqlite", ".sqlite3", ".db", ".database"}


class StageRefused(ValueError):
    """Fail closed when paths or inputs are unsafe."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_regular_file(path: Path) -> bool:
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False


def _hash_stable(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise StageRefused(f"not a regular non-symlink file: {path}")
    before = path.stat(follow_symlinks=False)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        identity = lambda row: (
            row.st_size, row.st_mtime_ns, row.st_dev, row.st_ino
        )
        if not stat.S_ISREG(opened.st_mode) or identity(before) != identity(opened):
            raise StageRefused(f"source changed before hashing: {path}")
        digest = hashlib.sha256()
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            while chunk := handle.read(4 * 1024 * 1024):
                digest.update(chunk)
            after_open = os.fstat(handle.fileno())
        after = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(after.st_mode) or (
            identity(opened) != identity(after_open)
            or identity(opened) != identity(after)
        ):
            raise StageRefused(f"source changed during hashing: {path}")
        return digest.hexdigest()
    finally:
        if fd >= 0:
            os.close(fd)


def _classify(path: Path) -> str:
    name = path.name
    lower = name.casefold()
    if name in BLOCKED_NAMES or any(lower.endswith(suf) for suf in BLOCKED_SUFFIXES):
        return "BLOCKED_BINARY_OR_DB"
    if path.suffix.casefold() in EXPORT_SUFFIXES:
        return "OFFICIAL_EXPORT_CANDIDATE"
    return "IGNORED"


def _scan_tree(root: Path | None) -> dict:
    if root is None:
        return {
            "path": None,
            "exists": False,
            "official_export_candidates": [],
            "blocked_binary_or_db": [],
            "session_file_count": 0,
            "note": "path not supplied",
        }
    root = root.expanduser().resolve()
    if not root.exists():
        return {
            "path": str(root),
            "exists": False,
            "official_export_candidates": [],
            "blocked_binary_or_db": [],
            "session_file_count": 0,
            "note": "path missing",
        }
    if root.is_symlink() or not root.is_dir():
        raise StageRefused(f"scan root must be a real directory: {root}")

    official: list[dict] = []
    blocked: list[dict] = []
    session_count = 0
    for path in sorted(root.rglob("*")):
        if not _is_regular_file(path):
            continue
        kind = _classify(path)
        rel = str(path.relative_to(root))
        entry = {
            "relative_path": rel,
            "absolute_path": str(path),
            "size_bytes": path.stat(follow_symlinks=False).st_size,
            "suffix": path.suffix.casefold(),
        }
        if kind == "OFFICIAL_EXPORT_CANDIDATE":
            official.append(entry)
        elif kind == "BLOCKED_BINARY_OR_DB":
            blocked.append(entry)
            if path.suffix.casefold() == ".session":
                session_count += 1
    return {
        "path": str(root),
        "exists": True,
        "official_export_candidates": official,
        "blocked_binary_or_db": blocked,
        "session_file_count": session_count,
        "note": (
            "Binary .session / history.database / sqlite files are listed only "
            "as evidence that History exists; they are not staged or parsed."
        ),
    }


def _default_serato_history() -> Path:
    return Path.home() / "Music" / "_Serato_" / "History"


def _default_serato_history_export() -> Path:
    return Path.home() / "Music" / "_Serato_" / "History Export"


def _assert_staging_safe(staging: Path, music_roots: list[Path]) -> Path:
    staging = staging.expanduser().resolve()
    if staging.exists() and (staging.is_symlink() or not staging.is_dir()):
        raise StageRefused(f"staging path must be a real directory: {staging}")
    for music in music_roots:
        try:
            music_resolved = music.expanduser().resolve()
        except OSError:
            continue
        if not music_resolved.exists():
            continue
        try:
            staging.relative_to(music_resolved)
        except ValueError:
            continue
        raise StageRefused(
            f"staging directory must be outside music/DJ roots: {staging} "
            f"is under {music_resolved}"
        )
    # Refuse staging inside this git checkout.
    repo_root = Path(__file__).resolve().parents[1]
    try:
        staging.relative_to(repo_root)
    except ValueError:
        return staging
    raise StageRefused(
        f"staging directory must be outside the git checkout: {staging}"
    )


def inventory(
    *,
    serato_history: Path | None = None,
    serato_history_export: Path | None = None,
    rekordbox_export_dirs: list[Path] | None = None,
    extra_search: list[Path] | None = None,
) -> dict:
    """Read-only inventory of official export candidates and blocked binaries."""
    serato_history = serato_history or _default_serato_history()
    serato_history_export = serato_history_export or _default_serato_history_export()
    rekordbox_export_dirs = rekordbox_export_dirs or []
    extra_search = extra_search or []

    scans = {
        "serato_history": _scan_tree(serato_history),
        "serato_history_export": _scan_tree(serato_history_export),
        "rekordbox_export_dirs": [_scan_tree(path) for path in rekordbox_export_dirs],
        "extra_search": [_scan_tree(path) for path in extra_search],
    }

    official: list[dict] = []
    for key in ("serato_history", "serato_history_export"):
        for item in scans[key]["official_export_candidates"]:
            official.append({**item, "source_bucket": key})
    for bucket_name in ("rekordbox_export_dirs", "extra_search"):
        for scan in scans[bucket_name]:
            for item in scan["official_export_candidates"]:
                official.append({**item, "source_bucket": bucket_name})

    session_total = scans["serato_history"]["session_file_count"]
    readiness = (
        "READY_FOR_GROK_STAGING"
        if official
        else "WAITING_FOR_OFFICIAL_EXPORTS"
    )
    return {
        "tool": "dj_history_export_stage",
        "version": "1.0.0",
        "mode": "inventory",
        "created_at_utc": _utc_now(),
        "safety": {
            "classification": SAFETY_CLASSIFICATION,
            "parses_vendor_rows": False,
            "opens_session_or_sqlite": False,
            "mutates_music_or_dj_databases": False,
        },
        "set_bot_purpose": [
            "what actually worked in a room (event-linked later)",
            "how you like to sequence tracks",
            "what you have already played too much",
        ],
        "readiness": readiness,
        "official_export_candidate_count": len(official),
        "official_export_candidates": official,
        "serato_session_file_count": session_total,
        "blocked_binary_present": session_total > 0 or any(
            scans["serato_history"]["blocked_binary_or_db"]
        ),
        "scans": scans,
        "export_required_next": [
            "Serato: open History, show Start Time / End Time / Playtime / Deck / Location, export CSV and TXT into History Export (or a private folder).",
            "rekordbox: open Histories, copy or export one History as .txt and .m3u8 into a private folder.",
            "Prefer a midnight-crossing set and one inserted or mark-played track.",
            "Never commit the exports. Stage them with this tool for Grok.",
        ],
        "do_not": [
            "Do not parse .session files or history.database.",
            "Do not treat collection XML, crates, PlayCount, or LastPlayed as a set history.",
            "Do not put exports inside the git checkout or inside the live music tree staging path.",
        ],
    }


def _unique_staged_name(dest_dir: Path, source: Path, used: set[str]) -> str:
    base = source.name
    if base not in used and not (dest_dir / base).exists():
        used.add(base)
        return base
    stem = source.stem
    suffix = source.suffix
    digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:8]
    candidate = f"{stem}.{digest}{suffix}"
    n = 2
    while candidate in used or (dest_dir / candidate).exists():
        candidate = f"{stem}.{digest}.{n}{suffix}"
        n += 1
    used.add(candidate)
    return candidate


def stage(
    *,
    staging_dir: Path,
    serato_history: Path | None = None,
    serato_history_export: Path | None = None,
    rekordbox_export_dirs: list[Path] | None = None,
    extra_search: list[Path] | None = None,
    music_roots: list[Path] | None = None,
) -> dict:
    """Copy official export candidates into a private Grok staging directory."""
    music_roots = music_roots or [
        Path.home() / "Music",
        Path("/Volumes"),
    ]
    staging = _assert_staging_safe(staging_dir, music_roots)
    staging.mkdir(parents=True, exist_ok=True)
    exports_dir = staging / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    report = inventory(
        serato_history=serato_history,
        serato_history_export=serato_history_export,
        rekordbox_export_dirs=rekordbox_export_dirs,
        extra_search=extra_search,
    )
    staged: list[dict] = []
    used_names: set[str] = set()
    for item in report["official_export_candidates"]:
        source = Path(item["absolute_path"])
        if not _is_regular_file(source):
            continue
        if _classify(source) != "OFFICIAL_EXPORT_CANDIDATE":
            raise StageRefused(f"refusing to stage blocked or unknown file: {source}")
        sha = _hash_stable(source)
        name = _unique_staged_name(exports_dir, source, used_names)
        dest = exports_dir / name
        shutil.copy2(source, dest, follow_symlinks=False)
        if dest.is_symlink():
            dest.unlink()
            raise StageRefused(f"staged path became a symlink: {dest}")
        staged_sha = _hash_stable(dest)
        if staged_sha != sha:
            dest.unlink(missing_ok=True)
            raise StageRefused(f"staged copy hash mismatch for {source}")
        staged.append({
            "staged_name": name,
            "staged_path": str(dest),
            "source_path": str(source),
            "source_bucket": item["source_bucket"],
            "size_bytes": dest.stat(follow_symlinks=False).st_size,
            "sha256": staged_sha,
            "suffix": dest.suffix.casefold(),
            "parsed": False,
            "set_bot_ready": "RAW_EXPORT_STAGED_UNPARSED",
        })

    readiness = (
        "RAW_EXPORTS_STAGED_FOR_GROK"
        if staged
        else "WAITING_FOR_OFFICIAL_EXPORTS"
    )
    manifest = {
        **report,
        "mode": "stage",
        "readiness": readiness,
        "staging_dir": str(staging),
        "exports_dir": str(exports_dir),
        "staged_exports": staged,
        "staged_count": len(staged),
        "grok_instructions": {
            "read_first": str(staging / "GROK-SET-HISTORY.md"),
            "manifest": str(staging / "manifest.json"),
            "exports_glob": str(exports_dir / "*"),
            "allowed_now": [
                "Inspect staged CSV/TXT/M3U/M3U8 bytes and column layouts.",
                "Summarize sequencing and repeat plays only from staged official exports.",
                "Flag overplayed titles across staged sessions once rows are readable.",
            ],
            "forbidden": [
                "Do not open or parse .session, history.database, or sqlite under Music/_Serato_.",
                "Do not invent play times from event start times or PlayCount.",
                "Do not modify music files, Serato, or rekordbox.",
            ],
        },
    }

    grok_md = _grok_readme(manifest)
    (staging / "GROK-SET-HISTORY.md").write_text(grok_md, encoding="utf-8")
    (staging / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def _grok_readme(manifest: dict) -> str:
    staged = manifest.get("staged_exports") or []
    lines = [
        "# Grok set-history pack (private)",
        "",
        "Source-only staging. No music mutation. No DJ database rewrite.",
        "",
        "## Why this exists",
        "",
        "For building sets, Grok needs your **official Serato / rekordbox History",
        "exports** — not crates, not collection XML, not PlayCount.",
        "",
        "With those exports, the set bot can see:",
        "",
        "1. What actually worked in a room (once joined to confirmed set windows)",
        "2. How you like to sequence tracks",
        "3. What you have already played too much",
        "",
        f"## Readiness: `{manifest['readiness']}`",
        "",
        f"- Official export candidates found: **{manifest['official_export_candidate_count']}**",
        f"- Staged for Grok: **{manifest.get('staged_count', 0)}**",
        f"- Serato `.session` files observed (blocked, not parsed): **{manifest['serato_session_file_count']}**",
        "",
        "## Staged files",
        "",
    ]
    if not staged:
        lines.extend([
            "_None yet._ Export CSV/TXT (Serato) and History `.txt` / `.m3u8`",
            "(rekordbox), then re-run staging.",
            "",
        ])
    else:
        for item in staged:
            lines.append(
                f"- `{item['staged_name']}` — sha256 `{item['sha256'][:12]}…` "
                f"from `{item['source_path']}` — **unparsed raw export**"
            )
        lines.append("")
    lines.extend([
        "## Rules for Grok",
        "",
        "- Read `manifest.json` in this folder.",
        "- Use only files under `exports/`.",
        "- Do **not** parse `.session`, `history.database`, or sqlite.",
        "- Do **not** treat collection XML as a play log.",
        "- Until a reviewed parser exists, treat layouts as unknown and prefer",
        "  human-visible column inspection over assumptions.",
        "- Never commit these files to Git.",
        "",
        "## Operator export checklist",
        "",
    ])
    for step in manifest["export_required_next"]:
        lines.append(f"- {step}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inventory or stage official Serato/Rekordbox History exports "
            "for Grok set building (read-only toward DJ app data)."
        )
    )
    parser.add_argument(
        "command",
        choices=("inventory", "stage"),
        help="inventory = report only; stage = copy official exports to a private folder",
    )
    parser.add_argument(
        "--staging-dir",
        type=Path,
        help="private directory outside Music and outside this git checkout",
    )
    parser.add_argument(
        "--serato-history",
        type=Path,
        default=None,
        help="Serato History folder (default: ~/Music/_Serato_/History)",
    )
    parser.add_argument(
        "--serato-history-export",
        type=Path,
        default=None,
        help="Serato History Export folder (default: ~/Music/_Serato_/History Export)",
    )
    parser.add_argument(
        "--rekordbox-export-dir",
        type=Path,
        action="append",
        default=[],
        help="directory containing rekordbox History .txt/.m3u8 exports; repeatable",
    )
    parser.add_argument(
        "--search",
        type=Path,
        action="append",
        default=[],
        help="extra private folder to scan for official exports; repeatable",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        help="optional path to write the machine-readable report",
    )
    args = parser.parse_args(argv)

    try:
        if args.command == "inventory":
            result = inventory(
                serato_history=args.serato_history,
                serato_history_export=args.serato_history_export,
                rekordbox_export_dirs=args.rekordbox_export_dir,
                extra_search=args.search,
            )
        else:
            if args.staging_dir is None:
                raise StageRefused("--staging-dir is required for stage")
            result = stage(
                staging_dir=args.staging_dir,
                serato_history=args.serato_history,
                serato_history_export=args.serato_history_export,
                rekordbox_export_dirs=args.rekordbox_export_dir,
                extra_search=args.search,
            )
    except StageRefused as exc:
        print(json.dumps({"error": str(exc), "refused": True}, indent=2))
        return 2

    text = json.dumps(result, indent=2) + "\n"
    if args.json_out is not None:
        out = args.json_out.expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
