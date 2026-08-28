#!/usr/bin/env python3
"""Read-only preflight for a reviewed Rekordbox XML before USB export.

The tool verifies the selected XML against the current trusted library index and
an explicitly selected mounted USB volume.  It does not export, copy, move, or
write anything.  Device compatibility is fail-closed: without a verified
hardware profile the overall readiness result is BLOCKED.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import library_index
import rekordbox_index
import rekordbox_relocation_preview
import usb_readonly_inventory

TOOL_VERSION = "1.0.0"
PREFLIGHT_SCHEMA_VERSION = 1
HARDWARE_PROFILE_SCHEMA_VERSION = 1


def _read_regular(path: Path, *, label: str) -> tuple[Path, bytes]:
    requested = path.expanduser().absolute()
    if requested.is_symlink():
        raise RuntimeError(f"{label} must not be a symlink: {requested}")
    resolved = requested.resolve()
    if not resolved.is_file():
        raise RuntimeError(f"{label} must be a regular file: {resolved}")
    before = resolved.stat(follow_symlinks=False)
    raw = resolved.read_bytes()
    after = resolved.stat(follow_symlinks=False)
    before_id = (before.st_size, before.st_mtime_ns, before.st_dev, before.st_ino)
    after_id = (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino)
    if before_id != after_id or len(raw) != after.st_size:
        raise RuntimeError(f"{label} changed while being read: {resolved}")
    return resolved, raw


def _load_json(path: Path, *, label: str) -> tuple[Path, bytes, dict[str, Any]]:
    resolved, raw = _read_regular(path, label=label)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return resolved, raw, payload


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _verify_current_file(row: Any) -> dict[str, Any]:
    path = Path(row["display_path"])
    if path.is_symlink() or not path.is_file():
        return {"ok": False, "reason": "missing_or_symlink", "path": str(path)}
    before = path.stat(follow_symlinks=False)
    if before.st_size != int(row["size_bytes"]):
        return {"ok": False, "reason": "size_changed", "path": str(path)}
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat(follow_symlinks=False)
    before_id = (before.st_size, before.st_mtime_ns, before.st_dev, before.st_ino)
    after_id = (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino)
    if before_id != after_id:
        return {"ok": False, "reason": "changed_while_hashed", "path": str(path)}
    if digest.hexdigest() != row["sha256"]:
        return {"ok": False, "reason": "sha256_changed", "path": str(path)}
    return {"ok": True, "path": str(path)}


def _load_profile(path: Path | None) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if path is None:
        return None, {
            "status": "BLOCKED_DEVICE_PROFILE",
            "reason": "no verified hardware profile supplied",
        }
    resolved, raw, profile = _load_json(path, label="hardware profile")
    if profile.get("tool") != "dj_hardware_profile":
        raise RuntimeError("hardware profile has an unexpected tool identifier")
    if profile.get("profile_schema_version") != HARDWARE_PROFILE_SCHEMA_VERSION:
        raise RuntimeError("unsupported hardware profile schema")
    model = profile.get("model")
    verification = profile.get("verification")
    compatibility = profile.get("compatibility")
    if not isinstance(model, str) or not model.strip():
        raise RuntimeError("hardware profile model is missing")
    if not isinstance(verification, dict) or verification.get("verified") is not True:
        raise RuntimeError("hardware profile is not explicitly verified")
    source = verification.get("source")
    if not isinstance(source, str) or not source.strip():
        raise RuntimeError("verified hardware profile requires a source")
    if not isinstance(compatibility, dict):
        raise RuntimeError("hardware profile compatibility section is missing")
    rules = compatibility.get("extension_rules")
    filesystems = compatibility.get("supported_filesystems")
    if not isinstance(rules, dict) or not rules:
        raise RuntimeError("hardware profile requires extension_rules")
    if not isinstance(filesystems, list) or not filesystems:
        raise RuntimeError("hardware profile requires supported_filesystems")
    for ext, state in rules.items():
        if not isinstance(ext, str) or not ext.startswith("."):
            raise RuntimeError("hardware profile extension rule keys must start with '.'")
        if state not in {"SUPPORTED", "UNSUPPORTED", "REQUIRES_CODEC_EVIDENCE"}:
            raise RuntimeError(f"invalid extension compatibility state for {ext!r}")
    return profile, {
        "status": "PROFILE_LOADED",
        "path": str(resolved),
        "sha256": _sha256_bytes(raw),
        "model": model,
    }


def _filesystem_identity(usb: dict[str, Any]) -> str | None:
    disk = usb.get("volume", {}).get("diskutil", {})
    for key in ("filesystem_type", "filesystem_name"):
        value = disk.get(key) if isinstance(disk, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip().casefold()
    return None


def _playlist_integrity(parsed: Any) -> dict[str, Any]:
    tracks_by_id = {str(track["track_id"]): track for track in parsed.tracks}
    tracks_by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for track in parsed.tracks:
        tracks_by_path[track["absolute_path"]].append(track)
    duplicate_locations = sorted(path for path, items in tracks_by_path.items() if len(items) > 1)

    nodes = {node["node_key"]: node for node in parsed.playlist_nodes}
    errors: list[dict[str, Any]] = []
    playlist_count = 0
    ordered_memberships: dict[str, list[str]] = defaultdict(list)
    for membership in sorted(parsed.memberships, key=lambda item: (item["node_key"], item["ordinal"])):
        node = nodes.get(membership["node_key"])
        if node is None or node["node_type"] != 1:
            errors.append({"node_key": membership["node_key"], "reason": "membership_without_playlist"})
            continue
        raw_key = str(membership["raw_key"])
        key_type = membership["key_type"]
        resolved_id: str | None = None
        if key_type == 0:
            if raw_key in tracks_by_id:
                resolved_id = raw_key
        elif key_type == 1:
            try:
                path = rekordbox_index._location_to_path(raw_key, context="playlist preflight")
            except RuntimeError:
                path = raw_key
            matches = tracks_by_path.get(path, [])
            if len(matches) == 1:
                resolved_id = str(matches[0]["track_id"])
        if resolved_id is None:
            errors.append(
                {
                    "node_key": membership["node_key"],
                    "ordinal": membership["ordinal"],
                    "raw_key": raw_key,
                    "reason": "unresolved_playlist_reference",
                }
            )
        else:
            ordered_memberships[membership["node_key"]].append(resolved_id)

    for node in parsed.playlist_nodes:
        if node["node_type"] == 1:
            playlist_count += 1
            expected = node.get("declared_count")
            actual = len(ordered_memberships.get(node["node_key"], []))
            if expected is not None and int(expected) != actual:
                errors.append(
                    {
                        "node_key": node["node_key"],
                        "node_path": node["node_path"],
                        "reason": "playlist_entry_count_mismatch",
                        "declared": int(expected),
                        "resolved": actual,
                    }
                )

    return {
        "status": "PASS" if not errors and not duplicate_locations else "FAIL",
        "playlist_count": playlist_count,
        "playlist_reference_count": len(parsed.memberships),
        "duplicate_collection_locations": duplicate_locations,
        "errors": errors,
    }


def _compatibility_for_track(row: Any, profile: dict[str, Any] | None) -> dict[str, Any]:
    extension = str(row["extension"]).casefold()
    if profile is None:
        return {"status": "BLOCKED_DEVICE_PROFILE", "extension": extension}
    compatibility = profile["compatibility"]
    rules = {str(k).casefold(): v for k, v in compatibility["extension_rules"].items()}
    state = rules.get(extension, "UNSUPPORTED")
    reasons: list[str] = []
    status = "PASS"
    if state == "UNSUPPORTED":
        status = "FAIL"
        reasons.append(f"extension {extension} is not supported by the verified profile")
    elif state == "REQUIRES_CODEC_EVIDENCE":
        status = "BLOCKED_CODEC_EVIDENCE"
        reasons.append(
            f"extension {extension} requires normalized codec evidence that the current library index does not provide"
        )

    for profile_key, row_key, label in (
        ("max_sample_rate_hz", "sample_rate_hz", "sample rate"),
        ("max_bits_per_sample", "bits_per_sample", "bit depth"),
        ("max_channels", "channels", "channel count"),
        ("max_file_size_bytes", "size_bytes", "file size"),
        ("max_bitrate_bps", "bitrate_bps", "bitrate"),
    ):
        limit = compatibility.get(profile_key)
        value = row[row_key]
        if limit is not None and value is not None and int(value) > int(limit):
            status = "FAIL"
            reasons.append(f"{label} {value} exceeds verified limit {limit}")
    return {"status": status, "extension": extension, "reasons": reasons}


def build_preflight(
    db_path: Path,
    playlist_xml: Path,
    volume_path: Path,
    hardware_profile: Path | None = None,
) -> dict[str, Any]:
    xml_resolved, xml_raw = _read_regular(playlist_xml, label="playlist XML")
    parsed = rekordbox_index._parse_export(xml_raw)
    playlist_check = _playlist_integrity(parsed)
    usb = usb_readonly_inventory.inventory(volume_path)
    profile, profile_state = _load_profile(hardware_profile)

    conn = rekordbox_relocation_preview._readonly_connect(db_path)
    try:
        library_state = rekordbox_relocation_preview._validate_index_state(conn)
        rows = list(conn.execute("SELECT * FROM files ORDER BY file_id"))
    finally:
        conn.close()
    by_identity = {row["path_identity"]: row for row in rows}

    selected: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    sha_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    total_bytes = 0
    grid_missing: list[dict[str, Any]] = []
    cue_missing: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []
    compatibility_counts: Counter[str] = Counter()

    for track in parsed.tracks:
        canonical = library_index.canonicalize_path(Path(track["absolute_path"]))
        identity = library_index.mac_path_identity(canonical)
        row = by_identity.get(identity)
        item: dict[str, Any] = {
            "track_id": str(track["track_id"]),
            "xml_path": track["absolute_path"],
            "mark_count": len(track["marks"]),
            "tempo_count": len(track["tempos"]),
        }
        if row is None:
            item["existence"] = {"status": "FAIL", "reason": "not_in_current_library_index"}
            failures.append({"track_id": item["track_id"], "reason": "missing_current_file"})
            selected.append(item)
            continue

        file_id = int(row["file_id"])
        verification = _verify_current_file(row)
        item.update(
            {
                "file_id": file_id,
                "current_path": row["display_path"],
                "sha256": row["sha256"],
                "size_bytes": int(row["size_bytes"]),
                "existence": {
                    "status": "PASS" if verification["ok"] else "FAIL",
                    "reason": verification.get("reason"),
                },
            }
        )
        if not verification["ok"]:
            failures.append({"track_id": item["track_id"], "file_id": file_id, "reason": verification["reason"]})
        else:
            total_bytes += int(row["size_bytes"])
            sha_groups[str(row["sha256"])].append(
                {"file_id": file_id, "path": row["display_path"], "track_id": item["track_id"]}
            )

        if row["parse_error"]:
            parse_errors.append({"file_id": file_id, "path": row["display_path"], "error": row["parse_error"]})
        if not track["tempos"]:
            grid_missing.append({"file_id": file_id, "path": row["display_path"]})
        if not track["marks"]:
            cue_missing.append({"file_id": file_id, "path": row["display_path"]})
        compatibility = _compatibility_for_track(row, profile)
        compatibility_counts[compatibility["status"]] += 1
        item["compatibility"] = compatibility
        selected.append(item)

    duplicates = [
        {"sha256": sha, "files": items}
        for sha, items in sorted(sha_groups.items())
        if len({item["file_id"] for item in items}) > 1
    ]

    free_bytes = int(usb["volume"]["capacity_after"]["free_bytes"])
    space_status = "PASS" if total_bytes <= free_bytes else "FAIL"
    filesystem_status = "BLOCKED_DEVICE_PROFILE"
    filesystem_reason = "no verified hardware profile supplied"
    if profile is not None:
        fs_identity = _filesystem_identity(usb)
        allowed = {str(value).strip().casefold() for value in profile["compatibility"]["supported_filesystems"]}
        if fs_identity is None:
            filesystem_status = "BLOCKED_FILESYSTEM_EVIDENCE"
            filesystem_reason = "USB filesystem identity could not be established"
        elif fs_identity in allowed:
            filesystem_status = "PASS"
            filesystem_reason = None
        else:
            filesystem_status = "FAIL"
            filesystem_reason = f"USB filesystem {fs_identity!r} is not in verified supported filesystems"

    hard_fail = bool(failures) or playlist_check["status"] == "FAIL" or usb["health"]["status"] != "PASS" or space_status == "FAIL"
    if profile is not None:
        hard_fail = hard_fail or compatibility_counts["FAIL"] > 0 or filesystem_status == "FAIL"
    blocked = (
        profile is None
        or compatibility_counts["BLOCKED_CODEC_EVIDENCE"] > 0
        or filesystem_status.startswith("BLOCKED_")
    )
    overall = "FAIL" if hard_fail else ("BLOCKED" if blocked else "PASS")

    return {
        "tool": "gig_usb_preflight",
        "tool_version": TOOL_VERSION,
        "preflight_schema_version": PREFLIGHT_SCHEMA_VERSION,
        "safety": {
            "classification": SAFETY_CLASSIFICATION,
            "writes_usb": False,
            "writes_audio": False,
            "writes_rekordbox": False,
            "writes_serato": False,
            "writes_library_index": False,
            "exports_to_device": False,
        },
        "source": {
            "playlist_xml_path": str(xml_resolved),
            "playlist_xml_sha256": _sha256_bytes(xml_raw),
            "library_scan_id": int(library_state["scan_id"]),
            "volume_path": usb["volume"]["path"],
            "hardware_profile": profile_state,
        },
        "overall": {
            "status": overall,
            "ready_for_rekordbox_export": overall == "PASS",
        },
        "checks": {
            "usb_inventory_health": usb["health"],
            "playlist_integrity": playlist_check,
            "track_existence": {
                "status": "PASS" if not failures else "FAIL",
                "selected_tracks": len(parsed.tracks),
                "failures": failures,
            },
            "space": {
                "status": space_status,
                "required_audio_bytes": total_bytes,
                "usb_free_bytes": free_bytes,
                "headroom_bytes": free_bytes - total_bytes,
                "note": "required_audio_bytes is a minimum estimate and does not include Rekordbox database/artwork overhead",
            },
            "filesystem_compatibility": {
                "status": filesystem_status,
                "reason": filesystem_reason,
                "detected": _filesystem_identity(usb),
            },
            "device_audio_compatibility": {
                "status": (
                    "BLOCKED_DEVICE_PROFILE"
                    if profile is None
                    else (
                        "FAIL" if compatibility_counts["FAIL"] else (
                            "BLOCKED_CODEC_EVIDENCE"
                            if compatibility_counts["BLOCKED_CODEC_EVIDENCE"]
                            else "PASS"
                        )
                    )
                ),
                "counts": dict(compatibility_counts),
            },
            "analysis": {
                "tempo_grid_missing_count": len(grid_missing),
                "tempo_grid_missing": grid_missing,
                "parse_error_count": len(parse_errors),
                "parse_errors": parse_errors,
            },
            "cues": {
                "tracks_without_position_marks": len(cue_missing),
                "tracks": cue_missing,
                "status": "INFO",
            },
            "duplicates": {
                "exact_sha256_duplicate_groups": len(duplicates),
                "groups": duplicates,
                "status": "WARN" if duplicates else "PASS",
                "deletion_authority": False,
            },
        },
        "tracks": selected,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--playlist-xml", required=True, type=Path)
    parser.add_argument("--volume", required=True, type=Path)
    parser.add_argument("--hardware-profile", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = build_preflight(args.db, args.playlist_xml, args.volume, args.hardware_profile)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["overall"]["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
