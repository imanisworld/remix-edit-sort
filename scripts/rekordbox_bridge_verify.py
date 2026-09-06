#!/usr/bin/env python3
"""Verify a fresh Rekordbox XML export against a reviewed bridge XML.

This is a read-only post-import verifier. The fresh Rekordbox export may contain
additional collection tracks or playlists; the bridge is treated as the
required subset. Every bridge track must resolve at its approved current file
location, preserve static metadata, cues/loops and tempo-grid data, and every
bridge playlist must preserve its ordered membership after resolving playlist
keys to file locations.

TrackID values are deliberately not used as cross-export identity because
Rekordbox may assign different IDs after import. No files are written.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import argparse
import hashlib
import json
import math
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any

import library_index
import rekordbox_bridge_xml
import rekordbox_index
import rekordbox_unresolved_review
import serato_rekordbox_performance_verify

TOOL_VERSION = "1.0.0"
VERIFY_SCHEMA_VERSION = 1

# Fields that can legitimately change as Rekordbox imports/exports a track.
# Everything else in TRACK attributes is treated as static bridge metadata.
DYNAMIC_TRACK_ATTRIBUTES = frozenset(
    {
        "TrackID",
        "Location",
        "DateAdded",
        "DateModified",
        "PlayCount",
        "LastPlayed",
    }
)

# Floating-point values in XML are semantic, not formatting-sensitive.
FLOAT_TOLERANCE = 0.001


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_stable(path: Path, *, label: str) -> tuple[Path, bytes]:
    requested = path.expanduser().absolute()
    if requested.is_symlink():
        raise RuntimeError(f"{label} must not be a symlink: {requested}")
    resolved = requested.resolve()
    if not resolved.is_file():
        raise RuntimeError(f"{label} must be a regular file: {resolved}")
    before = resolved.stat(follow_symlinks=False)
    payload = resolved.read_bytes()
    after = resolved.stat(follow_symlinks=False)
    before_id = (before.st_size, before.st_mtime_ns, before.st_dev, before.st_ino)
    after_id = (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino)
    if before_id != after_id or len(payload) != after.st_size:
        raise RuntimeError(f"{label} changed while being read: {resolved}")
    return resolved, payload


def _load_json(path: Path, *, label: str) -> tuple[Path, bytes, dict[str, Any]]:
    resolved, raw = _read_stable(path, label=label)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return resolved, raw, value


def _path_identity(path: str) -> str:
    return library_index.mac_path_identity(
        library_index.canonicalize_path(Path(unicodedata.normalize("NFC", path)))
    )


def _track_index(parsed: Any, *, label: str) -> dict[str, list[dict[str, Any]]]:
    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for track in parsed.tracks:
        by_path[_path_identity(track["absolute_path"])].append(track)
    return dict(by_path)


def _static_attributes(track: dict[str, Any]) -> dict[str, str]:
    attrs = track["attributes"]
    return {
        key: value
        for key, value in attrs.items()
        if key not in DYNAMIC_TRACK_ATTRIBUTES
    }


def _parse_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _semantic_child(
    child: dict[str, str], *, numeric_fields: frozenset[str]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in child.items():
        if key in numeric_fields:
            parsed = _parse_float(value)
            result[key] = parsed if parsed is not None else value
        else:
            result[key] = value
    return result


def _value_equal(left: Any, right: Any) -> bool:
    if isinstance(left, float) and isinstance(right, float):
        return abs(left - right) <= FLOAT_TOLERANCE
    return left == right


def _dict_equal(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if set(left) != set(right):
        return False
    return all(_value_equal(left[key], right[key]) for key in left)


def _sequence_equal(
    left: list[dict[str, Any]], right: list[dict[str, Any]]
) -> bool:
    return len(left) == len(right) and all(
        _dict_equal(a, b) for a, b in zip(left, right)
    )


def _semantic_tempos(track: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _semantic_child(
            item,
            numeric_fields=frozenset({"Inizio", "Bpm", "Battito"}),
        )
        for item in track["tempos"]
    ]


def _semantic_marks(track: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _semantic_child(
            item,
            numeric_fields=frozenset({"Start", "End", "Num", "Red", "Green", "Blue"}),
        )
        for item in track["marks"]
    ]


def _resolve_playlist_memberships(parsed: Any) -> dict[str, list[str]]:
    tracks_by_id = {str(track["track_id"]): track for track in parsed.tracks}
    node_by_key = {node["node_key"]: node for node in parsed.playlist_nodes}
    memberships: dict[str, list[str]] = defaultdict(list)

    for membership in sorted(
        parsed.memberships,
        key=lambda item: (item["node_key"], int(item["ordinal"])),
    ):
        node = node_by_key.get(membership["node_key"])
        if node is None:
            raise RuntimeError(
                f"playlist membership references unknown node {membership['node_key']!r}"
            )
        key_type = node["key_type"]
        raw_key = membership["raw_key"]
        if key_type == 0:
            track = tracks_by_id.get(str(raw_key))
            if track is None:
                raise RuntimeError(
                    f"playlist {node['node_path']!r} references absent TrackID {raw_key!r}"
                )
            path = track["absolute_path"]
        elif key_type == 1:
            path = rekordbox_index._location_to_path(
                raw_key,
                context=f"PLAYLISTS/{node['node_path']}",
            )
        else:
            raise RuntimeError(
                f"playlist {node['node_path']!r} has unsupported KeyType {key_type!r}"
            )
        memberships[node["node_path"]].append(_path_identity(path))
    return dict(memberships)


def _playlist_index(parsed: Any) -> dict[str, list[dict[str, Any]]]:
    by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in parsed.playlist_nodes:
        by_path[node["node_path"]].append(node)
    return dict(by_path)


def _validate_bridge_authority(
    bridge_parsed: Any,
    preview: dict[str, Any],
    preview_sha: str,
    review: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    rekordbox_unresolved_review.validate_preview(preview)
    approved, _excluded = rekordbox_bridge_xml._approved_targets(
        preview,
        preview_sha,
        review,
    )
    bridge_by_path = _track_index(bridge_parsed, label="bridge XML")
    if any(len(items) != 1 for items in bridge_by_path.values()):
        raise RuntimeError("bridge XML contains duplicate current file locations")

    expected_by_path: dict[str, dict[str, Any]] = {}
    for candidate in approved.values():
        identity = _path_identity(candidate["path"])
        if identity in expected_by_path:
            raise RuntimeError("approved bridge targets collapse to one file location")
        expected_by_path[identity] = candidate

    if set(bridge_by_path) != set(expected_by_path):
        missing = sorted(set(expected_by_path) - set(bridge_by_path))
        extra = sorted(set(bridge_by_path) - set(expected_by_path))
        raise RuntimeError(
            "bridge XML does not equal the approved relocation set; "
            f"missing={len(missing)} extra={len(extra)}"
        )
    return expected_by_path


def _file_presence(expected_by_path: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for candidate in expected_by_path.values():
        path = Path(candidate["path"])
        if path.is_symlink() or not path.is_file():
            missing.append(
                {
                    "path": candidate["path"],
                    "reason": "expected_current_file_missing",
                }
            )
    return missing


def verify(
    bridge_xml: Path,
    fresh_xml: Path,
    preview_path: Path,
    *,
    review_path: Path | None = None,
    verify_serato_performance: bool = False,
) -> dict[str, Any]:
    bridge_resolved, bridge_raw = _read_stable(bridge_xml, label="bridge XML")
    fresh_resolved, fresh_raw = _read_stable(fresh_xml, label="fresh Rekordbox XML")
    if bridge_resolved == fresh_resolved:
        raise RuntimeError("bridge XML and fresh Rekordbox XML must be different files")

    preview_resolved, preview_raw, preview = _load_json(
        preview_path, label="relocation preview"
    )
    review = None
    review_resolved = None
    review_raw = None
    if review_path is not None:
        review_resolved, review_raw, review = _load_json(
            review_path, label="review packet"
        )

    bridge_parsed = rekordbox_index._parse_export(bridge_raw)
    fresh_parsed = rekordbox_index._parse_export(fresh_raw)
    preview_sha = _sha256(preview_raw)
    expected_by_path = _validate_bridge_authority(
        bridge_parsed, preview, preview_sha, review
    )

    bridge_tracks = _track_index(bridge_parsed, label="bridge XML")
    fresh_tracks = _track_index(fresh_parsed, label="fresh Rekordbox XML")

    track_findings: list[dict[str, Any]] = []
    matched_expected = 0
    matched_fresh_tracks: list[dict[str, Any]] = []
    for identity, bridge_list in sorted(bridge_tracks.items()):
        bridge_track = bridge_list[0]
        fresh_list = fresh_tracks.get(identity, [])
        if not fresh_list:
            track_findings.append(
                {
                    "path": bridge_track["absolute_path"],
                    "kind": "TRACK_MISSING_FROM_FRESH_EXPORT",
                }
            )
            continue
        if len(fresh_list) > 1:
            track_findings.append(
                {
                    "path": bridge_track["absolute_path"],
                    "kind": "TRACK_LOCATION_AMBIGUOUS_IN_FRESH_EXPORT",
                    "fresh_count": len(fresh_list),
                }
            )
            continue
        matched_expected += 1
        fresh_track = fresh_list[0]
        matched_fresh_tracks.append(fresh_track)
        if _static_attributes(bridge_track) != _static_attributes(fresh_track):
            track_findings.append(
                {
                    "path": bridge_track["absolute_path"],
                    "kind": "STATIC_METADATA_MISMATCH",
                    "bridge": _static_attributes(bridge_track),
                    "fresh": _static_attributes(fresh_track),
                }
            )
        bridge_tempos = _semantic_tempos(bridge_track)
        fresh_tempos = _semantic_tempos(fresh_track)
        if not _sequence_equal(bridge_tempos, fresh_tempos):
            track_findings.append(
                {
                    "path": bridge_track["absolute_path"],
                    "kind": "TEMPO_GRID_MISMATCH",
                    "bridge_count": len(bridge_tempos),
                    "fresh_count": len(fresh_tempos),
                }
            )
        bridge_marks = _semantic_marks(bridge_track)
        fresh_marks = _semantic_marks(fresh_track)
        if not _sequence_equal(bridge_marks, fresh_marks):
            track_findings.append(
                {
                    "path": bridge_track["absolute_path"],
                    "kind": "CUE_LOOP_MISMATCH",
                    "bridge_count": len(bridge_marks),
                    "fresh_count": len(fresh_marks),
                }
            )

    bridge_nodes = _playlist_index(bridge_parsed)
    fresh_nodes = _playlist_index(fresh_parsed)
    bridge_members = _resolve_playlist_memberships(bridge_parsed)
    fresh_members = _resolve_playlist_memberships(fresh_parsed)
    playlist_findings: list[dict[str, Any]] = []
    matched_playlists = 0

    for node_path, bridge_node_list in sorted(bridge_nodes.items()):
        if len(bridge_node_list) != 1:
            raise RuntimeError(
                f"bridge XML has duplicate playlist/folder path {node_path!r}"
            )
        bridge_node = bridge_node_list[0]
        fresh_node_list = fresh_nodes.get(node_path, [])
        if not fresh_node_list:
            playlist_findings.append(
                {"node_path": node_path, "kind": "PLAYLIST_NODE_MISSING"}
            )
            continue
        if len(fresh_node_list) > 1:
            playlist_findings.append(
                {
                    "node_path": node_path,
                    "kind": "PLAYLIST_NODE_AMBIGUOUS",
                    "fresh_count": len(fresh_node_list),
                }
            )
            continue
        matched_playlists += 1
        fresh_node = fresh_node_list[0]
        if bridge_node["node_type"] != fresh_node["node_type"]:
            playlist_findings.append(
                {
                    "node_path": node_path,
                    "kind": "PLAYLIST_NODE_TYPE_MISMATCH",
                }
            )
            continue
        if bridge_node["node_type"] == 1:
            expected_members = bridge_members.get(node_path, [])
            actual_members = fresh_members.get(node_path, [])
            if expected_members != actual_members:
                playlist_findings.append(
                    {
                        "node_path": node_path,
                        "kind": "PLAYLIST_MEMBERSHIP_ORDER_MISMATCH",
                        "expected_count": len(expected_members),
                        "fresh_count": len(actual_members),
                    }
                )

    missing_files = _file_presence(expected_by_path)
    bridge_path_set = set(bridge_tracks)
    fresh_path_set = set(fresh_tracks)
    extra_fresh_tracks = len(fresh_path_set - bridge_path_set)
    extra_fresh_playlist_nodes = len(set(fresh_nodes) - set(bridge_nodes))

    serato_performance_report = None
    serato_performance_failures = 0
    if verify_serato_performance:
        serato_performance_report = serato_rekordbox_performance_verify.verify_tracks(
            matched_fresh_tracks
        )
        if serato_performance_report["verdict"] == "FAIL":
            serato_performance_failures = int(
                serato_performance_report["summary"]["fail_findings"]
            )

    preview_summary = preview["summary"]
    fail_count = (
        len(track_findings)
        + len(playlist_findings)
        + len(missing_files)
        + serato_performance_failures
    )
    verdict = "PASS" if fail_count == 0 else "FAIL"

    report = {
        "tool": "rekordbox_bridge_verify",
        "tool_version": TOOL_VERSION,
        "verify_schema_version": VERIFY_SCHEMA_VERSION,
        "safety": {
            "classification": SAFETY_CLASSIFICATION,
            "writes_rekordbox": False,
            "writes_serato": False,
            "writes_audio": False,
            "writes_library_index": False,
            "writes_files": False,
        },
        "verdict": verdict,
        "inputs": {
            "bridge_xml": str(bridge_resolved),
            "bridge_sha256": _sha256(bridge_raw),
            "fresh_xml": str(fresh_resolved),
            "fresh_sha256": _sha256(fresh_raw),
            "preview": str(preview_resolved),
            "preview_sha256": preview_sha,
            "review": str(review_resolved) if review_resolved else None,
            "review_sha256": _sha256(review_raw) if review_raw is not None else None,
        },
        "summary": {
            "required_bridge_tracks": len(bridge_tracks),
            "required_tracks_found_once": matched_expected,
            "fresh_collection_tracks": len(fresh_parsed.tracks),
            "extra_fresh_tracks_allowed": extra_fresh_tracks,
            "required_playlist_nodes": len(bridge_nodes),
            "required_playlist_nodes_found_once": matched_playlists,
            "extra_fresh_playlist_nodes_allowed": extra_fresh_playlist_nodes,
            "track_findings": len(track_findings),
            "playlist_findings": len(playlist_findings),
            "expected_files_missing_on_disk": len(missing_files),
            "preview_ambiguous_excluded_or_reviewed": int(preview_summary["ambiguous"]),
            "preview_changed_excluded_or_reviewed": int(preview_summary["changed"]),
            "preview_missing_excluded": int(preview_summary["missing"]),
            "fail_findings": fail_count,
        },
        "track_findings": track_findings,
        "playlist_findings": playlist_findings,
        "missing_files": missing_files,
    }
    if serato_performance_report is not None:
        report["summary"]["serato_performance_fail_findings"] = serato_performance_failures
        report["serato_performance"] = serato_performance_report
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-xml", type=Path, required=True)
    parser.add_argument("--fresh-xml", type=Path, required=True)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--review", type=Path)
    parser.add_argument(
        "--verify-serato-performance",
        action="store_true",
        help="also compare embedded Serato cues/loops/BeatGrid against the fresh Rekordbox XML",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = verify(
            args.bridge_xml,
            args.fresh_xml,
            args.preview,
            review_path=args.review,
            verify_serato_performance=args.verify_serato_performance,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
