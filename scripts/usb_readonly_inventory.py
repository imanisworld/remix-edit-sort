#!/usr/bin/env python3
"""Inventory one explicitly selected mounted DJ USB volume without writing it.

The caller must provide the mount point.  The tool never auto-selects a volume,
never writes a report to the target, never follows symlinks, and never edits
Rekordbox/Pioneer databases.  On macOS it reads ``diskutil info -plist`` for
volume metadata and independently traverses the mounted filesystem read-only.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import argparse
import hashlib
import json
import os
import platform
import plistlib
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

TOOL_VERSION = "1.0.0"
USB_INVENTORY_SCHEMA_VERSION = 1

AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".aac", ".wav", ".aif", ".aiff", ".flac", ".ogg", ".opus"
}
REKORDBOX_PATH_MARKERS = {"pioneer", ".pioneer", "rekordbox", "pioneerdj", "onelibrary"}
REKORDBOX_FILE_MARKERS = {
    "export.pdb",
    "master.db",
    "exportlibrary.db",
}
MAX_REPORTED_MARKERS = 200
MAX_REPORTED_ERRORS = 100
MAX_TOP_LEVEL = 200


def _root_identity(path: Path) -> dict[str, int]:
    stat = path.stat(follow_symlinks=False)
    return {
        "st_dev": int(stat.st_dev),
        "st_ino": int(stat.st_ino),
        "st_mode": int(stat.st_mode),
        "st_size": int(stat.st_size),
        "st_mtime_ns": int(stat.st_mtime_ns),
    }


def _explicit_mountpoint(path: Path) -> Path:
    requested = path.expanduser().absolute()
    if requested.is_symlink():
        raise RuntimeError(f"volume path must not be a symlink: {requested}")
    resolved = requested.resolve()
    if not resolved.is_dir():
        raise RuntimeError(f"volume path must be a directory: {resolved}")
    if resolved == Path("/"):
        raise RuntimeError("refusing to inventory the system root as a DJ USB")
    if not os.path.ismount(resolved):
        raise RuntimeError(
            "volume path must be the explicit mounted-volume root; "
            f"not a mount point: {resolved}"
        )
    return resolved


def _diskutil_info(volume: Path) -> dict[str, Any]:
    if platform.system() != "Darwin":
        return {
            "available": False,
            "reason": "diskutil metadata is only available on macOS",
        }
    diskutil = Path("/usr/sbin/diskutil")
    if not diskutil.is_file():
        raise RuntimeError("macOS diskutil was not found at /usr/sbin/diskutil")
    result = subprocess.run(
        [str(diskutil), "info", "-plist", str(volume)],
        capture_output=True,
        check=False,
        timeout=20,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"diskutil info failed for {volume}: {stderr or result.returncode}")
    try:
        payload = plistlib.loads(result.stdout)
    except Exception as exc:
        raise RuntimeError("diskutil returned an invalid property list") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("diskutil returned an unexpected non-dictionary result")

    def pick(*names: str) -> Any:
        for name in names:
            if name in payload:
                return payload[name]
        return None

    mount_point = pick("MountPoint")
    if isinstance(mount_point, str):
        mount_identity = os.path.normcase(os.path.realpath(mount_point))
        volume_identity = os.path.normcase(os.path.realpath(volume))
        if mount_identity != volume_identity:
            raise RuntimeError(
                f"diskutil mount point {mount_point!r} does not match requested volume {str(volume)!r}"
            )

    return {
        "available": True,
        "device_identifier": pick("DeviceIdentifier"),
        "volume_name": pick("VolumeName"),
        "mount_point": mount_point,
        "filesystem_type": pick("FilesystemType", "FileSystemType"),
        "filesystem_name": pick("FilesystemName", "FileSystemPersonality"),
        "volume_size_bytes": pick("VolumeSize", "TotalSize"),
        "volume_free_bytes": pick("VolumeFreeSpace", "FreeSpace"),
        "writable": pick("Writable"),
        "internal": pick("Internal"),
        "ejectable": pick("Ejectable"),
        "removable_media": pick("RemovableMedia", "Removable"),
        "solid_state": pick("SolidState"),
        "protocol": pick("BusProtocol", "Protocol"),
    }


def _capacity(volume: Path) -> dict[str, int | float | None]:
    stat = os.statvfs(volume)
    total = int(stat.f_frsize * stat.f_blocks)
    free = int(stat.f_frsize * stat.f_bavail)
    used = max(0, total - free)
    return {
        "total_bytes": total,
        "free_bytes": free,
        "used_bytes": used,
        "free_percent": (free / total * 100.0) if total else None,
    }


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_rekordbox_marker(relative: str) -> bool:
    path = Path(relative)
    parts = {part.casefold() for part in path.parts}
    name = path.name.casefold()
    return bool(parts & REKORDBOX_PATH_MARKERS) or name in REKORDBOX_FILE_MARKERS


def _sha256_regular(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _walk(volume: Path) -> dict[str, Any]:
    counts = Counter()
    audio_by_extension: Counter[str] = Counter()
    top_level: list[dict[str, Any]] = []
    marker_paths: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    total_file_bytes = 0
    largest_file_bytes = 0
    largest_file_path: str | None = None

    stack: list[Path] = [volume]
    while stack:
        directory = stack.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(list(iterator), key=lambda item: item.name.casefold())
        except OSError as exc:
            counts["traversal_errors"] += 1
            if len(errors) < MAX_REPORTED_ERRORS:
                errors.append({"path": _relative(directory, volume) or ".", "error": str(exc)})
            continue

        for entry in reversed(entries):
            path = Path(entry.path)
            relative = _relative(path, volume)
            try:
                if entry.is_symlink():
                    counts["symlinks_skipped"] += 1
                    if path.parent == volume and len(top_level) < MAX_TOP_LEVEL:
                        top_level.append({"name": entry.name, "type": "symlink_skipped"})
                    continue
                if entry.is_dir(follow_symlinks=False):
                    counts["directories"] += 1
                    if path.parent == volume and len(top_level) < MAX_TOP_LEVEL:
                        top_level.append({"name": entry.name, "type": "directory"})
                    if _is_rekordbox_marker(relative) and len(marker_paths) < MAX_REPORTED_MARKERS:
                        marker_paths.append({"path": relative, "type": "directory"})
                    stack.append(path)
                    continue
                if entry.is_file(follow_symlinks=False):
                    stat = entry.stat(follow_symlinks=False)
                    size = int(stat.st_size)
                    counts["files"] += 1
                    total_file_bytes += size
                    suffix = path.suffix.casefold()
                    if suffix in AUDIO_EXTENSIONS:
                        counts["audio_files"] += 1
                        audio_by_extension[suffix] += 1
                    if size > largest_file_bytes:
                        largest_file_bytes = size
                        largest_file_path = relative
                    if path.parent == volume and len(top_level) < MAX_TOP_LEVEL:
                        top_level.append({"name": entry.name, "type": "file", "size_bytes": size})
                    if _is_rekordbox_marker(relative) and len(marker_paths) < MAX_REPORTED_MARKERS:
                        marker: dict[str, Any] = {
                            "path": relative,
                            "type": "file",
                            "size_bytes": size,
                        }
                        if path.name.casefold() in REKORDBOX_FILE_MARKERS:
                            before = entry.stat(follow_symlinks=False)
                            marker["sha256"] = _sha256_regular(path)
                            after = entry.stat(follow_symlinks=False)
                            before_id = (before.st_size, before.st_mtime_ns, before.st_dev, before.st_ino)
                            after_id = (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino)
                            if before_id != after_id:
                                raise RuntimeError(f"Rekordbox marker changed while being hashed: {path}")
                        marker_paths.append(marker)
                    continue
                counts["other_entries"] += 1
            except OSError as exc:
                counts["entry_errors"] += 1
                if len(errors) < MAX_REPORTED_ERRORS:
                    errors.append({"path": relative, "error": str(exc)})

    return {
        "file_count": int(counts["files"]),
        "directory_count": int(counts["directories"]),
        "audio_file_count": int(counts["audio_files"]),
        "audio_by_extension": dict(sorted(audio_by_extension.items())),
        "total_file_bytes": total_file_bytes,
        "largest_file": {
            "path": largest_file_path,
            "size_bytes": largest_file_bytes,
        } if largest_file_path is not None else None,
        "symlinks_skipped": int(counts["symlinks_skipped"]),
        "other_entries": int(counts["other_entries"]),
        "traversal_errors": int(counts["traversal_errors"]),
        "entry_errors": int(counts["entry_errors"]),
        "errors": errors,
        "errors_truncated": (int(counts["traversal_errors"]) + int(counts["entry_errors"])) > len(errors),
        "top_level": sorted(top_level, key=lambda item: item["name"].casefold()),
        "top_level_truncated": len(top_level) >= MAX_TOP_LEVEL,
        "rekordbox_markers": marker_paths,
        "rekordbox_markers_truncated": len(marker_paths) >= MAX_REPORTED_MARKERS,
    }


def inventory(volume_path: Path) -> dict[str, Any]:
    volume = _explicit_mountpoint(volume_path)
    before = _root_identity(volume)
    disk = _diskutil_info(volume)
    capacity_before = _capacity(volume)
    tree = _walk(volume)
    capacity_after = _capacity(volume)
    after = _root_identity(volume)

    same_root_object = (
        before["st_dev"] == after["st_dev"]
        and before["st_ino"] == after["st_ino"]
    )
    capacity_valid = (
        capacity_after["total_bytes"] >= 0
        and capacity_after["free_bytes"] >= 0
        and capacity_after["free_bytes"] <= capacity_after["total_bytes"]
    )
    error_count = int(tree["traversal_errors"]) + int(tree["entry_errors"])
    health = "PASS"
    health_reasons: list[str] = []
    if not same_root_object:
        health = "FAIL"
        health_reasons.append("mounted volume identity changed during inventory")
    if not capacity_valid:
        health = "FAIL"
        health_reasons.append("filesystem capacity values are inconsistent")
    if error_count:
        health = "FAIL"
        health_reasons.append(f"{error_count} filesystem entries could not be inventoried")
    if tree["symlinks_skipped"]:
        health_reasons.append(f"{tree['symlinks_skipped']} symlink entries were safely skipped")

    return {
        "tool": "usb_readonly_inventory",
        "tool_version": TOOL_VERSION,
        "inventory_schema_version": USB_INVENTORY_SCHEMA_VERSION,
        "safety": {
            "classification": SAFETY_CLASSIFICATION,
            "explicit_volume_required": True,
            "auto_selects_volume": False,
            "follows_symlinks": False,
            "writes_target_volume": False,
            "writes_rekordbox_database": False,
            "writes_audio": False,
            "writes_report_file": False,
        },
        "volume": {
            "path": str(volume),
            "root_identity_before": before,
            "root_identity_after": after,
            "diskutil": disk,
            "capacity_before": capacity_before,
            "capacity_after": capacity_after,
        },
        "inventory": tree,
        "rekordbox_presence": {
            "marker_count": len(tree["rekordbox_markers"]),
            "markers_found": bool(tree["rekordbox_markers"]),
            "interpretation": (
                "Rekordbox/Pioneer path or database markers were found; this does not by itself prove a complete or valid device export."
                if tree["rekordbox_markers"]
                else "No recognized Rekordbox/Pioneer markers were found; this does not prove the volume is empty or incompatible."
            ),
        },
        "health": {
            "status": health,
            "reasons": health_reasons,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--volume",
        required=True,
        type=Path,
        help="Explicit mounted-volume root to inventory; no volume is auto-selected.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = inventory(args.volume)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["health"]["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
