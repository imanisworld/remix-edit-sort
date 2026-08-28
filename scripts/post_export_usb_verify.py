#!/usr/bin/env python3
"""Verify a Rekordbox USB export without mutating the USB or DJ library.

The verifier compares the reviewed source XML to the exported USB using strong
identity rather than filenames or Rekordbox TrackIDs:

* every expected collection track must be present on the USB with the same
  SHA-256;
* playlist membership/order is compared by those SHA-256 identities;
* the traditional Device Library (``export.pdb``) and OneLibrary
  (``exportLibrary.db``) are parsed independently when present;
* expected beat-grid/cue data must have corresponding ANLZ evidence;
* all source reads are stability-checked and symlinks are refused/skipped.

``export.pdb`` is parsed directly from bytes. OneLibrary is encrypted and its
reader requires a filesystem path, so only the already-read database bytes are
written to a temporary local workspace; the USB source database is never opened
by that third-party reader and is never written.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "infrastructure"

import argparse
import hashlib
import json
import os
import struct
import sys
import tempfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

import library_index
import rekordbox_index
import rekordbox_relocation_preview
import usb_readonly_inventory

TOOL_VERSION = "1.0.0"
VERIFY_SCHEMA_VERSION = 1

LEGACY_READER_REPO = "https://github.com/fragmede/rekordbox-pdb"
LEGACY_READER_COMMIT = "ee3bac2f22ca11a5ce61eea35f8cb951c246eaef"
ONELIBRARY_READER_REPO = "https://github.com/dylanljones/pyrekordbox"
ONELIBRARY_READER_COMMIT = "f695541827cc488af267d6ca8a8e0052598d85a0"

LEGACY_CANDIDATES = (
    "PIONEER/rekordbox/export.pdb",
    ".PIONEER/rekordbox/export.pdb",
)
ONELIBRARY_CANDIDATES = (
    "PIONEER/rekordbox/exportLibrary.db",
    ".PIONEER/rekordbox/exportLibrary.db",
)
AUDIO_EXTENSIONS = frozenset(library_index.SUPPORTED_EXTENSIONS)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_stable_regular(path: Path, *, label: str) -> tuple[Path, bytes]:
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


def _hash_stable_regular(path: Path, *, label: str) -> tuple[str, int]:
    requested = path.expanduser().absolute()
    if requested.is_symlink() or not requested.is_file():
        raise RuntimeError(f"{label} is missing, not regular, or symlinked: {requested}")
    before = requested.stat(follow_symlinks=False)
    digest = hashlib.sha256()
    with requested.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    after = requested.stat(follow_symlinks=False)
    before_id = (before.st_size, before.st_mtime_ns, before.st_dev, before.st_ino)
    after_id = (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino)
    if before_id != after_id:
        raise RuntimeError(f"{label} changed while being hashed: {requested}")
    return digest.hexdigest(), int(after.st_size)


def _norm_playlist_path(value: str) -> str:
    parts = [unicodedata.normalize("NFC", part.strip()) for part in value.split("/")]
    parts = [part for part in parts if part]
    return " / ".join(parts)


def _norm_device_path(value: str) -> str:
    text = unicodedata.normalize("NFC", str(value).replace("\\", "/").strip())
    pure = PurePosixPath(text)
    parts = [part for part in pure.parts if part not in ("", "/", ".")]
    if not parts or any(part == ".." for part in parts):
        raise RuntimeError(f"unsafe device-relative path: {value!r}")
    return "/".join(parts)


def _resolve_device_path(volume: Path, raw: str) -> Path:
    relative = _norm_device_path(raw)
    candidate = volume.joinpath(*PurePosixPath(relative).parts)
    resolved = candidate.resolve(strict=False)
    volume_real = volume.resolve()
    try:
        resolved.relative_to(volume_real)
    except ValueError as exc:
        raise RuntimeError(f"device path escapes selected volume: {raw!r}") from exc
    return candidate


def _select_existing(volume: Path, candidates: tuple[str, ...]) -> Path | None:
    found: list[Path] = []
    for relative in candidates:
        path = volume.joinpath(*PurePosixPath(relative).parts)
        if path.is_symlink():
            raise RuntimeError(f"device database path must not be a symlink: {path}")
        if path.is_file():
            found.append(path)
    if len(found) > 1:
        raise RuntimeError(f"multiple competing device databases found: {[str(p) for p in found]}")
    return found[0] if found else None


def _current_library(db_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    conn = rekordbox_relocation_preview._readonly_connect(db_path)
    try:
        state = rekordbox_relocation_preview._validate_index_state(conn)
        rows = list(conn.execute("SELECT * FROM files ORDER BY file_id"))
    finally:
        conn.close()
    by_identity = {str(row["path_identity"]): row for row in rows}
    return state, by_identity


def _expected_model(parsed: Any, by_identity: dict[str, Any]) -> dict[str, Any]:
    by_track_id = {str(track["track_id"]): track for track in parsed.tracks}
    by_location_uri: dict[str, list[dict[str, Any]]] = defaultdict(list)
    expected_tracks: dict[str, dict[str, Any]] = {}
    for track in parsed.tracks:
        canonical = library_index.canonicalize_path(Path(track["absolute_path"]))
        identity = library_index.mac_path_identity(canonical)
        row = by_identity.get(identity)
        if row is None:
            raise RuntimeError(
                f"expected XML track is not in current trusted library index: {track['absolute_path']}"
            )
        digest, size = _hash_stable_regular(Path(row["display_path"]), label="expected audio")
        if digest != row["sha256"] or size != int(row["size_bytes"]):
            raise RuntimeError(
                f"current audio no longer matches trusted library index: {row['display_path']}"
            )
        track_id = str(track["track_id"])
        expected_tracks[track_id] = {
            "track_id": track_id,
            "file_id": int(row["file_id"]),
            "path": str(row["display_path"]),
            "sha256": digest,
            "size_bytes": size,
            "requires_grid": bool(track["tempos"]),
            "requires_cues": bool(track["marks"]),
            "xml_mark_count": len(track["marks"]),
            "xml_tempo_count": len(track["tempos"]),
        }
        by_location_uri[str(track["location_uri"])].append(track)

    nodes = {node["node_key"]: node for node in parsed.playlist_nodes}
    ordered: dict[str, list[str]] = defaultdict(list)
    errors: list[dict[str, Any]] = []
    for item in sorted(parsed.memberships, key=lambda x: (x["node_key"], x["ordinal"])):
        node = nodes.get(item["node_key"])
        if node is None or node["node_type"] != 1:
            errors.append({"node_key": item["node_key"], "reason": "membership_without_playlist"})
            continue
        candidates: list[dict[str, Any]]
        if item["key_type"] == 0:
            track = by_track_id.get(str(item["raw_key"]))
            candidates = [track] if track is not None else []
        else:
            candidates = by_location_uri.get(str(item["raw_key"]), [])
        if len(candidates) != 1:
            errors.append(
                {
                    "node_path": node["node_path"],
                    "ordinal": item["ordinal"],
                    "reason": "unresolved_playlist_reference",
                    "raw_key": item["raw_key"],
                }
            )
            continue
        ordered[item["node_key"]].append(str(candidates[0]["track_id"]))
    if errors:
        raise RuntimeError(f"expected XML playlist references are not fully resolvable: {errors[:5]}")

    playlists: dict[str, list[str]] = {}
    playlist_track_ids: dict[str, list[str]] = {}
    for node in parsed.playlist_nodes:
        if node["node_type"] != 1:
            continue
        path = _norm_playlist_path(str(node["node_path"]))
        if path in playlists:
            raise RuntimeError(f"duplicate normalized expected playlist path: {path!r}")
        ids = ordered.get(node["node_key"], [])
        playlist_track_ids[path] = ids
        playlists[path] = [expected_tracks[track_id]["sha256"] for track_id in ids]
    return {
        "tracks": expected_tracks,
        "playlists": playlists,
        "playlist_track_ids": playlist_track_ids,
    }


def _scan_usb_audio(volume: Path) -> dict[str, Any]:
    by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_relative_identity: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    symlinks = 0
    stack = [volume]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            errors.append({"path": str(directory), "error": str(exc)})
            continue
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.is_symlink():
                    symlinks += 1
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                if path.suffix.casefold() not in AUDIO_EXTENSIONS:
                    continue
                digest, size = _hash_stable_regular(path, label="USB audio")
                relative = path.relative_to(volume).as_posix()
                identity = unicodedata.normalize("NFC", relative).casefold()
                item = {"path": relative, "sha256": digest, "size_bytes": size}
                if identity in by_relative_identity:
                    raise RuntimeError(f"case/Unicode-colliding USB paths: {relative!r}")
                by_relative_identity[identity] = item
                by_sha[digest].append(item)
            except OSError as exc:
                errors.append({"path": str(path), "error": str(exc)})
    return {
        "by_sha": by_sha,
        "by_relative_identity": by_relative_identity,
        "errors": errors,
        "symlinks_skipped": symlinks,
        "audio_file_count": sum(len(items) for items in by_sha.values()),
    }


def _legacy_normalized(raw: bytes) -> dict[str, Any]:
    try:
        from rekordbox_pdb import Database
    except ImportError as exc:  # pragma: no cover - dependency gate handles this in CI
        raise RuntimeError("legacy Device Library reader is unavailable") from exc
    db = Database.from_bytes(raw)
    tracks = {
        int(track.id): {
            "id": int(track.id),
            "device_path": str(track.file_path),
            "analysis_path": str(track.analyze_path),
            "title": str(track.title),
        }
        for track in db.tracks
    }
    nodes = {int(node.id): node for node in db.playlist_tree}

    def node_path(node_id: int, visiting: set[int] | None = None) -> str:
        visiting = set() if visiting is None else set(visiting)
        if node_id in visiting:
            raise RuntimeError("cycle in legacy Device Library playlist tree")
        visiting.add(node_id)
        node = nodes.get(node_id)
        if node is None:
            raise RuntimeError(f"legacy playlist tree references missing node {node_id}")
        name = unicodedata.normalize("NFC", str(node.name).strip())
        if int(node.parent_id) == 0:
            return name
        parent = node_path(int(node.parent_id), visiting)
        return f"{parent} / {name}" if parent else name

    entries: dict[int, list[Any]] = defaultdict(list)
    for item in db.playlist_entries:
        entries[int(item.playlist_id)].append(item)
    playlists: dict[str, list[int]] = {}
    for node_id, node in nodes.items():
        if bool(node.is_folder):
            continue
        path = _norm_playlist_path(node_path(node_id))
        if path in playlists:
            raise RuntimeError(f"duplicate legacy playlist path: {path!r}")
        ordered = sorted(entries.get(node_id, []), key=lambda x: int(x.entry_index))
        playlists[path] = [int(item.track_id) for item in ordered]
    return {
        "format": "DEVICE_LIBRARY",
        "tracks": tracks,
        "playlists": playlists,
        "parser": {
            "repository": LEGACY_READER_REPO,
            "pinned_commit": LEGACY_READER_COMMIT,
        },
    }


def _onelibrary_normalized(raw: bytes) -> dict[str, Any]:
    try:
        from pyrekordbox import DeviceLibraryPlus
    except ImportError as exc:  # pragma: no cover - dependency gate handles this in CI
        raise RuntimeError("OneLibrary reader is unavailable") from exc

    # The third-party reader requires a path and may create SQLite sidecars.
    # It sees only this disposable local copy, never the USB source database.
    with tempfile.TemporaryDirectory(prefix="rekordbox-onelibrary-verify-") as tmpdir:
        temp_db = Path(tmpdir) / "exportLibrary.db"
        temp_db.write_bytes(raw)
        with DeviceLibraryPlus(temp_db) as db:
            content_rows = list(db.get_content())
            playlist_rows = list(db.get_playlist())
            membership_rows = list(db.get_playlist_content())

    tracks = {
        int(row.content_id): {
            "id": int(row.content_id),
            "device_path": str(row.path),
            "analysis_path": str(row.analysisDataFilePath or ""),
            "title": str(row.title or ""),
        }
        for row in content_rows
    }
    nodes = {int(row.playlist_id): row for row in playlist_rows}

    def node_path(node_id: int, visiting: set[int] | None = None) -> str:
        visiting = set() if visiting is None else set(visiting)
        if node_id in visiting:
            raise RuntimeError("cycle in OneLibrary playlist tree")
        visiting.add(node_id)
        node = nodes.get(node_id)
        if node is None:
            raise RuntimeError(f"OneLibrary playlist tree references missing node {node_id}")
        name = unicodedata.normalize("NFC", str(node.name).strip())
        parent_id = int(node.playlist_id_parent or 0)
        if parent_id == 0:
            return name
        parent = node_path(parent_id, visiting)
        return f"{parent} / {name}" if parent else name

    memberships: dict[int, list[Any]] = defaultdict(list)
    for row in membership_rows:
        memberships[int(row.playlist_id)].append(row)
    playlists: dict[str, list[int]] = {}
    for node_id, node in nodes.items():
        # pyrekordbox uses attribute=1 for folder, 0 for playlist.
        if int(node.attribute or 0) != 0:
            continue
        path = _norm_playlist_path(node_path(node_id))
        if path in playlists:
            raise RuntimeError(f"duplicate OneLibrary playlist path: {path!r}")
        ordered = sorted(memberships.get(node_id, []), key=lambda x: int(x.sequenceNo))
        playlists[path] = [int(item.content_id) for item in ordered]
    return {
        "format": "ONELIBRARY",
        "tracks": tracks,
        "playlists": playlists,
        "parser": {
            "repository": ONELIBRARY_READER_REPO,
            "pinned_commit": ONELIBRARY_READER_COMMIT,
            "source_usb_opened_by_parser": False,
            "temporary_local_copy_only": True,
        },
    }


def _anlz_evidence(raw: bytes) -> dict[str, Any]:
    if len(raw) < 12 or raw[:4] != b"PMAI":
        raise RuntimeError("invalid ANLZ file header")
    header_len = struct.unpack_from(">I", raw, 4)[0]
    declared_len = struct.unpack_from(">I", raw, 8)[0]
    if header_len < 12 or header_len > len(raw):
        raise RuntimeError("invalid ANLZ header length")
    if declared_len not in (0, len(raw)) and declared_len > len(raw):
        raise RuntimeError("truncated ANLZ file")
    pos = header_len
    tags: list[str] = []
    beat_entries = 0
    cue_container_count = 0
    while pos + 12 <= len(raw):
        fourcc_raw = raw[pos : pos + 4]
        try:
            fourcc = fourcc_raw.decode("ascii")
        except UnicodeDecodeError as exc:
            raise RuntimeError("invalid ANLZ section FourCC") from exc
        len_header = struct.unpack_from(">I", raw, pos + 4)[0]
        len_tag = struct.unpack_from(">I", raw, pos + 8)[0]
        if len_header < 12 or len_tag < len_header or pos + len_tag > len(raw):
            raise RuntimeError(f"invalid ANLZ section length for {fourcc!r}")
        tags.append(fourcc)
        if fourcc == "PQTZ" and len_header >= 24:
            count = struct.unpack_from(">I", raw, pos + 20)[0]
            beat_entries = max(beat_entries, int(count))
        if fourcc in {"PCOB", "PCO2"}:
            cue_container_count += 1
        pos += len_tag
    if pos != len(raw):
        # A short trailing region is suspicious; do not silently call it valid.
        raise RuntimeError("ANLZ file contains trailing/unparsed bytes")
    return {
        "tags": sorted(set(tags)),
        "beat_grid_entries": beat_entries,
        "cue_containers": cue_container_count,
        "has_grid": beat_entries > 0,
        "has_cue_data": cue_container_count > 0,
    }


def _analysis_for_track(volume: Path, analysis_path: str) -> dict[str, Any]:
    if not analysis_path.strip():
        return {"status": "FAIL", "reason": "device_database_has_no_analysis_path"}
    primary = _resolve_device_path(volume, analysis_path)
    if primary.is_symlink() or not primary.is_file():
        return {"status": "FAIL", "reason": "analysis_file_missing", "path": str(primary)}
    directory = primary.parent
    candidates = sorted(
        path for path in directory.iterdir()
        if path.is_file() and not path.is_symlink() and path.name.upper().startswith("ANLZ")
        and path.suffix.upper() in {".DAT", ".EXT", ".2EX"}
    )
    if not candidates:
        return {"status": "FAIL", "reason": "analysis_directory_has_no_ANLZ_files"}
    combined = {"has_grid": False, "has_cue_data": False, "files": []}
    for path in candidates:
        _, raw = _read_stable_regular(path, label="USB ANLZ")
        evidence = _anlz_evidence(raw)
        combined["has_grid"] = bool(combined["has_grid"] or evidence["has_grid"])
        combined["has_cue_data"] = bool(combined["has_cue_data"] or evidence["has_cue_data"])
        combined["files"].append(
            {
                "path": path.relative_to(volume).as_posix(),
                "sha256": _sha256(raw),
                **evidence,
            }
        )
    combined["status"] = "PASS"
    return combined


def _verify_normalized_library(
    normalized: dict[str, Any],
    expected: dict[str, Any],
    volume: Path,
) -> dict[str, Any]:
    tracks = normalized["tracks"]
    db_track_sha: dict[int, str] = {}
    db_track_analysis: dict[int, dict[str, Any]] = {}
    track_errors: list[dict[str, Any]] = []
    for track_id, item in tracks.items():
        try:
            audio_path = _resolve_device_path(volume, item["device_path"])
            digest, _ = _hash_stable_regular(audio_path, label="device-database audio")
            db_track_sha[int(track_id)] = digest
            db_track_analysis[int(track_id)] = _analysis_for_track(volume, str(item["analysis_path"]))
        except Exception as exc:
            track_errors.append({"device_track_id": int(track_id), "error": str(exc)})

    playlist_errors: list[dict[str, Any]] = []
    for path, expected_shas in expected["playlists"].items():
        actual_ids = normalized["playlists"].get(path)
        if actual_ids is None:
            playlist_errors.append({"playlist": path, "reason": "missing_playlist"})
            continue
        actual_shas: list[str] = []
        unresolved = False
        for track_id in actual_ids:
            digest = db_track_sha.get(int(track_id))
            if digest is None:
                unresolved = True
                playlist_errors.append(
                    {
                        "playlist": path,
                        "device_track_id": int(track_id),
                        "reason": "playlist_track_missing_or_unreadable",
                    }
                )
                break
            actual_shas.append(digest)
        if not unresolved and actual_shas != expected_shas:
            playlist_errors.append(
                {
                    "playlist": path,
                    "reason": "ordered_membership_mismatch",
                    "expected_entries": len(expected_shas),
                    "actual_entries": len(actual_shas),
                }
            )

    expected_sha_requirements: dict[str, dict[str, bool]] = defaultdict(
        lambda: {"requires_grid": False, "requires_cues": False}
    )
    for item in expected["tracks"].values():
        req = expected_sha_requirements[item["sha256"]]
        req["requires_grid"] = bool(req["requires_grid"] or item["requires_grid"])
        req["requires_cues"] = bool(req["requires_cues"] or item["requires_cues"])

    sha_to_track_ids: dict[str, list[int]] = defaultdict(list)
    for track_id, digest in db_track_sha.items():
        sha_to_track_ids[digest].append(track_id)
    analysis_errors: list[dict[str, Any]] = []
    analysis_verified = 0
    for digest, requirements in expected_sha_requirements.items():
        ids = sha_to_track_ids.get(digest, [])
        if not ids:
            analysis_errors.append({"sha256": digest, "reason": "expected_audio_absent_from_device_database"})
            continue
        evidence_options = [db_track_analysis.get(track_id, {}) for track_id in ids]
        has_grid = any(option.get("status") == "PASS" and option.get("has_grid") for option in evidence_options)
        has_cues = any(option.get("status") == "PASS" and option.get("has_cue_data") for option in evidence_options)
        if requirements["requires_grid"] and not has_grid:
            analysis_errors.append({"sha256": digest, "reason": "expected_grid_has_no_ANLZ_evidence"})
        if requirements["requires_cues"] and not has_cues:
            analysis_errors.append({"sha256": digest, "reason": "expected_cues_have_no_ANLZ_evidence"})
        if (not requirements["requires_grid"] or has_grid) and (not requirements["requires_cues"] or has_cues):
            analysis_verified += 1

    status = "PASS" if not track_errors and not playlist_errors and not analysis_errors else "FAIL"
    return {
        "status": status,
        "format": normalized["format"],
        "parser": normalized["parser"],
        "device_track_count": len(tracks),
        "device_playlist_count": len(normalized["playlists"]),
        "track_errors": track_errors,
        "playlist_errors": playlist_errors,
        "analysis": {
            "status": "PASS" if not analysis_errors else "FAIL",
            "expected_unique_audio_verified": analysis_verified,
            "errors": analysis_errors,
        },
    }


def _profile_library_requirement(profile_path: Path | None) -> dict[str, Any]:
    if profile_path is None:
        return {
            "status": "BLOCKED_DEVICE_PROFILE",
            "required_formats": [],
            "reason": "no verified hardware profile supplied",
        }
    resolved, raw = _read_stable_regular(profile_path, label="hardware profile")
    try:
        profile = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("hardware profile is not valid UTF-8 JSON") from exc
    if not isinstance(profile, dict) or profile.get("tool") != "dj_hardware_profile":
        raise RuntimeError("hardware profile has unexpected tool identifier")
    verification = profile.get("verification")
    if not isinstance(verification, dict) or verification.get("verified") is not True:
        raise RuntimeError("hardware profile is not verified")
    format_value = profile.get("device_library_format")
    mapping = {
        "DEVICE_LIBRARY": ["DEVICE_LIBRARY"],
        "ONELIBRARY": ["ONELIBRARY"],
        "BOTH": ["DEVICE_LIBRARY", "ONELIBRARY"],
    }
    if format_value not in mapping:
        return {
            "status": "BLOCKED_LIBRARY_FORMAT",
            "required_formats": [],
            "path": str(resolved),
            "sha256": _sha256(raw),
            "reason": "verified hardware profile does not declare device_library_format",
        }
    return {
        "status": "PASS",
        "required_formats": mapping[format_value],
        "declared": format_value,
        "path": str(resolved),
        "sha256": _sha256(raw),
    }


def verify_export(
    db_path: Path,
    expected_xml: Path,
    volume_path: Path,
    hardware_profile: Path | None = None,
) -> dict[str, Any]:
    volume = usb_readonly_inventory._explicit_mountpoint(volume_path)
    volume_inventory = usb_readonly_inventory.inventory(volume)
    xml_resolved, xml_raw = _read_stable_regular(expected_xml, label="expected Rekordbox XML")
    parsed = rekordbox_index._parse_export(xml_raw)
    library_state, by_identity = _current_library(db_path)
    expected = _expected_model(parsed, by_identity)
    usb_audio = _scan_usb_audio(volume)
    profile = _profile_library_requirement(hardware_profile)

    audio_missing: list[dict[str, Any]] = []
    audio_multiple: list[dict[str, Any]] = []
    for item in expected["tracks"].values():
        matches = usb_audio["by_sha"].get(item["sha256"], [])
        if not matches:
            audio_missing.append(
                {"track_id": item["track_id"], "file_id": item["file_id"], "sha256": item["sha256"]}
            )
        elif len(matches) > 1:
            audio_multiple.append(
                {"sha256": item["sha256"], "paths": [match["path"] for match in matches]}
            )
    audio_status = "PASS" if not audio_missing and not usb_audio["errors"] else "FAIL"

    legacy_path = _select_existing(volume, LEGACY_CANDIDATES)
    one_path = _select_existing(volume, ONELIBRARY_CANDIDATES)
    library_results: dict[str, Any] = {}
    if legacy_path is not None:
        _, raw = _read_stable_regular(legacy_path, label="Device Library export.pdb")
        try:
            normalized = _legacy_normalized(raw)
            library_results["DEVICE_LIBRARY"] = {
                **_verify_normalized_library(normalized, expected, volume),
                "path": legacy_path.relative_to(volume).as_posix(),
                "sha256": _sha256(raw),
            }
        except Exception as exc:
            library_results["DEVICE_LIBRARY"] = {
                "status": "FAIL",
                "path": legacy_path.relative_to(volume).as_posix(),
                "sha256": _sha256(raw),
                "error": str(exc),
            }
    else:
        library_results["DEVICE_LIBRARY"] = {"status": "MISSING"}

    if one_path is not None:
        _, raw = _read_stable_regular(one_path, label="OneLibrary exportLibrary.db")
        try:
            normalized = _onelibrary_normalized(raw)
            library_results["ONELIBRARY"] = {
                **_verify_normalized_library(normalized, expected, volume),
                "path": one_path.relative_to(volume).as_posix(),
                "sha256": _sha256(raw),
            }
        except Exception as exc:
            library_results["ONELIBRARY"] = {
                "status": "FAIL",
                "path": one_path.relative_to(volume).as_posix(),
                "sha256": _sha256(raw),
                "error": str(exc),
            }
    else:
        library_results["ONELIBRARY"] = {"status": "MISSING"}

    hard_fail = (
        volume_inventory["health"]["status"] != "PASS"
        or audio_status == "FAIL"
        or bool(audio_missing)
    )
    blocked = profile["status"].startswith("BLOCKED_")
    required_results: list[dict[str, Any]] = []
    for format_name in profile.get("required_formats", []):
        result = library_results.get(format_name, {"status": "MISSING"})
        required_results.append({"format": format_name, "status": result.get("status")})
        if result.get("status") in {"FAIL", "MISSING"}:
            hard_fail = True
        elif result.get("status") != "PASS":
            blocked = True

    if not profile.get("required_formats"):
        # Still surface concrete database corruption even when hardware is unknown,
        # but absence of one format is not itself a failure until a model requires it.
        if any(result.get("status") == "FAIL" for result in library_results.values()):
            hard_fail = True

    overall = "FAIL" if hard_fail else ("BLOCKED" if blocked else "PASS")
    return {
        "tool": "post_export_usb_verify",
        "tool_version": TOOL_VERSION,
        "verify_schema_version": VERIFY_SCHEMA_VERSION,
        "safety": {
            "classification": SAFETY_CLASSIFICATION,
            "writes_usb": False,
            "writes_audio": False,
            "writes_rekordbox_source_database": False,
            "writes_serato": False,
            "writes_library_index": False,
            "temporary_local_copy_for_onelibrary_only": True,
            "automatic_repair": False,
        },
        "source": {
            "expected_xml_path": str(xml_resolved),
            "expected_xml_sha256": _sha256(xml_raw),
            "library_scan_id": int(library_state["scan_id"]),
            "volume_path": str(volume),
            "hardware_profile": profile,
        },
        "overall": {
            "status": overall,
            "verified_for_gig": overall == "PASS",
            "required_library_results": required_results,
        },
        "checks": {
            "usb_inventory_health": volume_inventory["health"],
            "audio_fidelity": {
                "status": audio_status,
                "expected_collection_tracks": len(expected["tracks"]),
                "expected_unique_sha256": len({item["sha256"] for item in expected["tracks"].values()}),
                "usb_audio_files_hashed": usb_audio["audio_file_count"],
                "missing": audio_missing,
                "multiple_usb_copies": audio_multiple,
                "multiple_copy_status": "WARN" if audio_multiple else "PASS",
                "symlinks_skipped": usb_audio["symlinks_skipped"],
                "scan_errors": usb_audio["errors"],
            },
            "device_libraries": library_results,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--expected-xml", required=True, type=Path)
    parser.add_argument("--volume", required=True, type=Path)
    parser.add_argument("--hardware-profile", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = verify_export(args.db, args.expected_xml, args.volume, args.hardware_profile)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["overall"]["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
