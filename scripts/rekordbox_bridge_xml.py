#!/usr/bin/env python3
"""Generate a separate, reviewed Rekordbox bridge XML.

The bridge is derived from an existing Rekordbox XML export plus the exact
relocation-preview evidence produced for that export. Resolved tracks are
included only when the preview marked them bridge-eligible. AMBIGUOUS and
CHANGED tracks require an explicit human review decision; MISSING tracks can
never be included.

This tool never opens or writes Rekordbox ``master.db`` and never mutates the
source XML, Serato data, audio files, or the SQLite library index. It creates
one new XML file at a previously unused output path.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "preview-only"

import argparse
import copy
import hashlib
import json
import os
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import quote

import rekordbox_index
import rekordbox_unresolved_review

TOOL_VERSION = "1.0.0"
BRIDGE_SCHEMA_VERSION = 1
APPROVAL_DECISIONS = {
    "AMBIGUOUS": "APPROVE_CURRENT_FILE",
    "CHANGED": "APPROVE_CHANGED_CURRENT_FILE",
}


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be an object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RuntimeError(f"{label} must be an array")
    return value


def _read_regular(path: Path, *, label: str) -> tuple[Path, bytes]:
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
    resolved, raw = _read_regular(path, label=label)
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is not valid UTF-8 JSON") from exc
    return resolved, raw, _require_dict(parsed, label)


def _path_to_location(path: str) -> str:
    normalized = unicodedata.normalize("NFC", path)
    if not normalized.startswith("/"):
        raise RuntimeError(f"approved path is not absolute: {path!r}")
    return "file://localhost" + quote(normalized, safe="/")


def _candidate_by_target(
    track: dict[str, Any], file_id: int, path: str
) -> dict[str, Any]:
    for raw in _require_list(track.get("top_candidates"), "track.top_candidates"):
        if not isinstance(raw, dict):
            continue
        if raw.get("file_id") == file_id and raw.get("path") == path:
            return raw
    raise RuntimeError(
        f"approved target for Rekordbox track {track['rekordbox_track_id']} "
        "is not a relocation-preview candidate"
    )


def _validate_target_file(candidate: dict[str, Any]) -> None:
    path_value = candidate.get("path")
    if not isinstance(path_value, str) or not path_value:
        raise RuntimeError("approved candidate path is missing")
    path = Path(path_value)
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"approved candidate is not a regular file: {path}")
    expected_size = candidate.get("size_bytes")
    expected_sha = candidate.get("sha256")
    if not isinstance(expected_size, int) or expected_size < 0:
        raise RuntimeError(f"candidate lacks trusted byte size: {path}")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise RuntimeError(f"candidate lacks trusted SHA-256: {path}")

    before = path.stat(follow_symlinks=False)
    if before.st_size != expected_size:
        raise RuntimeError(f"candidate byte size changed since preview: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat(follow_symlinks=False)
    before_id = (before.st_size, before.st_mtime_ns, before.st_dev, before.st_ino)
    after_id = (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino)
    if before_id != after_id:
        raise RuntimeError(f"candidate changed while being verified: {path}")
    if digest.hexdigest() != expected_sha:
        raise RuntimeError(f"candidate SHA-256 changed since preview: {path}")


def _review_items(
    review: dict[str, Any], preview_sha256: str
) -> dict[str, dict[str, Any]]:
    if review.get("tool") != "rekordbox_unresolved_review":
        raise RuntimeError("review input is not a Rekordbox unresolved review packet")
    if review.get("review_schema_version") != 1:
        raise RuntimeError("unsupported Rekordbox unresolved review schema")
    safety = _require_dict(review.get("safety"), "review.safety")
    if safety.get("automatic_approval") is not False:
        raise RuntimeError("review packet allows automatic approval")
    if safety.get("automatic_resolution") is not False:
        raise RuntimeError("review packet allows automatic resolution")
    source = _require_dict(review.get("source"), "review.source")
    if source.get("preview_sha256") != preview_sha256:
        raise RuntimeError("review packet is not bound to the supplied preview")

    result: dict[str, dict[str, Any]] = {}
    queues = _require_dict(review.get("queues"), "review.queues")
    for queue_name in ("ambiguous", "changed", "missing"):
        for raw in _require_list(
            queues.get(queue_name), f"review.queues.{queue_name}"
        ):
            item = _require_dict(raw, f"review.queues.{queue_name} item")
            track_id = str(item.get("rekordbox_track_id", ""))
            if not track_id:
                raise RuntimeError("review item is missing rekordbox_track_id")
            if track_id in result:
                raise RuntimeError(f"duplicate Rekordbox track id in review: {track_id}")
            result[track_id] = item
    return result


def _approved_targets(
    preview: dict[str, Any],
    preview_sha256: str,
    review: dict[str, Any] | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    tracks = rekordbox_unresolved_review.validate_preview(preview)
    review_by_id = _review_items(review, preview_sha256) if review is not None else {}
    approved: dict[str, dict[str, Any]] = {}
    excluded: dict[str, str] = {}

    preview_ids = {str(track["rekordbox_track_id"]) for track in tracks}
    extra_review_ids = set(review_by_id) - preview_ids
    if extra_review_ids:
        raise RuntimeError(
            f"review contains track ids absent from preview: {sorted(extra_review_ids)}"
        )

    for track in tracks:
        track_id = str(track["rekordbox_track_id"])
        status = track["status"]
        if status in {"UNCHANGED", "STRONG_CANDIDATE"}:
            if track.get("bridge_eligible") is not True:
                raise RuntimeError(f"resolved track {track_id} is not bridge eligible")
            file_id = track.get("proposed_file_id")
            path = track.get("proposed_path")
            if not isinstance(file_id, int) or not isinstance(path, str):
                raise RuntimeError(f"resolved track {track_id} lacks a proposed target")
            approved[track_id] = _candidate_by_target(track, file_id, path)
            continue

        item = review_by_id.get(track_id)
        if item is None:
            excluded[track_id] = "unresolved_without_review"
            continue
        if item.get("status") != status:
            raise RuntimeError(f"review status drift for Rekordbox track {track_id}")
        decision = item.get("decision")
        allowed = item.get("allowed_decisions")
        if decision is None:
            excluded[track_id] = "review_decision_not_made"
            continue
        if not isinstance(allowed, list) or decision not in allowed:
            raise RuntimeError(
                f"invalid review decision for Rekordbox track {track_id}: {decision!r}"
            )
        required_approval = APPROVAL_DECISIONS.get(status)
        if decision != required_approval:
            excluded[track_id] = str(decision)
            continue
        if item.get("automatic_decision") is not False:
            raise RuntimeError(
                f"automatic decision cannot authorize Rekordbox track {track_id}"
            )
        evidence = item.get("decision_evidence")
        if not isinstance(evidence, list) or not evidence or not all(
            isinstance(value, str) and value.strip() for value in evidence
        ):
            raise RuntimeError(
                f"approved review decision for track {track_id} needs decision_evidence"
            )

        selected_file_id = item.get("selected_file_id", item.get("proposed_file_id"))
        selected_path = item.get("selected_path", item.get("proposed_path"))
        if not isinstance(selected_file_id, int) or not isinstance(selected_path, str):
            raise RuntimeError(
                f"approved review decision for track {track_id} needs an explicit candidate target"
            )
        approved[track_id] = _candidate_by_target(
            track, selected_file_id, selected_path
        )

    return approved, excluded


def _parse_source_xml(raw: bytes) -> tuple[ET.Element, Any]:
    parsed = rekordbox_index._parse_export(raw)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise RuntimeError(f"invalid Rekordbox XML: {exc}") from exc
    version = root.attrib.get("Version")
    if version not in (None, "1.0.0"):
        raise RuntimeError(f"unsupported DJ_PLAYLISTS Version: {version!r}")
    return root, parsed


def _track_elements(root: ET.Element) -> dict[str, ET.Element]:
    collection = root.find("COLLECTION")
    if collection is None:
        raise RuntimeError("Rekordbox XML has no COLLECTION")
    result: dict[str, ET.Element] = {}
    for element in list(collection):
        track_id = element.attrib.get("TrackID")
        if not track_id or track_id in result:
            raise RuntimeError("Rekordbox XML has missing or duplicate TrackID")
        result[track_id] = element
    return result


def _track_snapshot(element: ET.Element) -> dict[str, Any]:
    return {
        "attributes": dict(element.attrib),
        "children": [(child.tag, dict(child.attrib)) for child in list(element)],
    }


def _rewrite_collection(
    root: ET.Element,
    approved: dict[str, dict[str, Any]],
) -> tuple[dict[str, str], dict[str, str], set[str]]:
    collection = root.find("COLLECTION")
    if collection is None:
        raise RuntimeError("source XML has no COLLECTION")
    exact_uri_targets: dict[str, set[str]] = {}
    decoded_path_targets: dict[str, set[str]] = {}
    included_ids: set[str] = set()
    kept_elements: list[ET.Element] = []

    for element in list(collection):
        track_id = str(element.attrib.get("TrackID", ""))
        candidate = approved.get(track_id)
        if candidate is None:
            continue
        old_location = element.attrib.get("Location")
        if not old_location:
            raise RuntimeError(f"source track {track_id} has no Location")
        old_path = rekordbox_index._location_to_path(
            old_location, context=f"COLLECTION/TRACK[{track_id}]"
        )
        new_location = _path_to_location(candidate["path"])
        exact_uri_targets.setdefault(old_location, set()).add(new_location)
        decoded_path_targets.setdefault(old_path, set()).add(new_location)
        element.set("Location", new_location)
        included_ids.add(track_id)
        kept_elements.append(element)
    collection[:] = kept_elements
    collection.set("Entries", str(len(included_ids)))

    def flatten(values: dict[str, set[str]], label: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, targets in values.items():
            if len(targets) != 1:
                raise RuntimeError(
                    f"one source {label} maps to multiple approved targets: {key!r}"
                )
            result[key] = next(iter(targets))
        return result

    return (
        flatten(exact_uri_targets, "Location"),
        flatten(decoded_path_targets, "path"),
        included_ids,
    )


def _rewrite_playlists(
    root: ET.Element,
    included_ids: set[str],
    exact_uri_map: dict[str, str],
    decoded_path_map: dict[str, str],
) -> tuple[int, int]:
    playlists = root.find("PLAYLISTS")
    if playlists is None:
        return 0, 0
    kept = 0
    removed = 0

    def visit(node: ET.Element, node_key: str) -> None:
        nonlocal kept, removed
        if node.tag != "NODE":
            raise RuntimeError("PLAYLISTS contains a non-NODE element")
        node_type = node.attrib.get("Type")
        if node_type == "0":
            children = list(node)
            for position, child in enumerate(children):
                visit(child, f"{node_key}.{position}")
            if "Count" in node.attrib:
                node.set("Count", str(len(children)))
            return
        if node_type != "1":
            raise RuntimeError(f"unsupported playlist NODE Type: {node_type!r}")
        key_type = node.attrib.get("KeyType")
        if key_type not in {"0", "1"}:
            raise RuntimeError(f"unsupported playlist KeyType: {key_type!r}")

        kept_children: list[ET.Element] = []
        for child in list(node):
            key = child.attrib.get("Key")
            if key is None:
                raise RuntimeError(f"playlist {node_key} TRACK is missing Key")
            if key_type == "0":
                if key in included_ids:
                    kept += 1
                    kept_children.append(child)
                else:
                    removed += 1
                continue

            target = exact_uri_map.get(key)
            if target is None:
                try:
                    decoded = rekordbox_index._location_to_path(
                        key, context=f"PLAYLISTS/NODE[{node_key}]"
                    )
                except RuntimeError:
                    decoded = None
                if decoded is not None:
                    target = decoded_path_map.get(decoded)
            if target is None:
                removed += 1
            else:
                child.set("Key", target)
                kept += 1
                kept_children.append(child)
        node[:] = kept_children
        node.set("Entries", str(len(kept_children)))

    for position, node in enumerate(list(playlists)):
        visit(node, str(position))
    return kept, removed


def _playlist_snapshot(root: ET.Element) -> dict[str, dict[str, Any]]:
    playlists = root.find("PLAYLISTS")
    if playlists is None:
        return {}
    result: dict[str, dict[str, Any]] = {}

    def visit(node: ET.Element, key: str) -> None:
        attrs = dict(node.attrib)
        node_type = attrs.get("Type")
        attrs.pop("Entries", None)
        attrs.pop("Count", None)
        if node_type == "0":
            result[key] = {"attributes": attrs, "memberships": None}
            for position, child in enumerate(list(node)):
                visit(child, f"{key}.{position}")
        elif node_type == "1":
            result[key] = {
                "attributes": attrs,
                "memberships": [child.attrib.get("Key") for child in list(node)],
            }
        else:
            raise RuntimeError(f"unsupported playlist NODE Type: {node_type!r}")

    for position, node in enumerate(list(playlists)):
        visit(node, str(position))
    return result


def _verify_semantics(
    source_root: ET.Element,
    expected_root: ET.Element,
    bridge_raw: bytes,
    approved: dict[str, dict[str, Any]],
) -> None:
    bridge_parsed = rekordbox_index._parse_export(bridge_raw)
    bridge_root = ET.fromstring(bridge_raw)
    source_elements = _track_elements(source_root)
    bridge_elements = _track_elements(bridge_root)
    if set(bridge_elements) != set(approved):
        raise RuntimeError("bridge XML collection does not equal the approved TrackID set")

    for track_id, bridge_element in bridge_elements.items():
        source_snapshot = _track_snapshot(source_elements[track_id])
        bridge_snapshot = _track_snapshot(bridge_element)
        expected_location = _path_to_location(approved[track_id]["path"])
        if bridge_snapshot["attributes"].get("Location") != expected_location:
            raise RuntimeError(f"bridge Location verification failed for track {track_id}")
        source_attrs = dict(source_snapshot["attributes"])
        bridge_attrs = dict(bridge_snapshot["attributes"])
        source_attrs.pop("Location", None)
        bridge_attrs.pop("Location", None)
        if source_attrs != bridge_attrs:
            raise RuntimeError(f"metadata changed unexpectedly for track {track_id}")
        if source_snapshot["children"] != bridge_snapshot["children"]:
            raise RuntimeError(f"cue/tempo data changed unexpectedly for track {track_id}")

    if bridge_parsed.declared_collection_count != len(approved):
        raise RuntimeError("bridge COLLECTION count verification failed")

    source_nodes = _playlist_snapshot(source_root)
    expected_nodes = _playlist_snapshot(expected_root)
    bridge_nodes = _playlist_snapshot(bridge_root)
    if set(source_nodes) != set(bridge_nodes):
        raise RuntimeError("playlist folder/playlist structure changed unexpectedly")
    for key in source_nodes:
        if source_nodes[key]["attributes"] != bridge_nodes[key]["attributes"]:
            raise RuntimeError(f"playlist metadata changed unexpectedly at node {key}")
    if expected_nodes != bridge_nodes:
        raise RuntimeError("playlist membership/order verification failed")


def build_bridge(
    source_xml: Path,
    preview_path: Path,
    output_path: Path,
    *,
    review_path: Path | None = None,
) -> dict[str, Any]:
    source_resolved, source_raw = _read_regular(
        source_xml, label="source Rekordbox XML"
    )
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

    rekordbox_unresolved_review.validate_preview(preview)
    source_fingerprint = _require_dict(
        _require_dict(preview.get("rekordbox"), "preview.rekordbox").get(
            "source_fingerprint"
        ),
        "preview.rekordbox.source_fingerprint",
    )
    source_sha = _sha256_bytes(source_raw)
    if source_fingerprint.get("sha256") != source_sha:
        raise RuntimeError(
            "source Rekordbox XML does not match the XML bound to the relocation preview"
        )
    declared_size = source_fingerprint.get("size_bytes")
    if declared_size is not None and declared_size != len(source_raw):
        raise RuntimeError("source Rekordbox XML byte size does not match relocation preview")

    preview_sha = _sha256_bytes(preview_raw)
    approved, excluded = _approved_targets(preview, preview_sha, review)
    if not approved:
        raise RuntimeError("no tracks are approved for bridge XML")
    for candidate in approved.values():
        _validate_target_file(candidate)

    source_root, source_parsed = _parse_source_xml(source_raw)
    source_ids = {str(track["track_id"]) for track in source_parsed.tracks}
    missing_source_ids = set(approved) - source_ids
    if missing_source_ids:
        raise RuntimeError(
            f"approved TrackID(s) absent from source Rekordbox XML: {sorted(missing_source_ids)}"
        )

    bridge_root = copy.deepcopy(source_root)
    exact_uri_map, decoded_path_map, included_ids = _rewrite_collection(
        bridge_root, approved
    )
    playlist_kept, playlist_removed = _rewrite_playlists(
        bridge_root, included_ids, exact_uri_map, decoded_path_map
    )
    expected_root = copy.deepcopy(bridge_root)
    body = ET.tostring(bridge_root, encoding="utf-8", short_empty_elements=True)
    bridge_raw = b'<?xml version="1.0" encoding="UTF-8" ?>\n' + body
    _verify_semantics(source_root, expected_root, bridge_raw, approved)

    output_requested = output_path.expanduser().absolute()
    if output_requested.suffix.casefold() != ".xml":
        raise RuntimeError("bridge output path must end in .xml")
    if output_requested.is_symlink():
        raise RuntimeError(f"bridge output must not be a symlink: {output_requested}")
    output_resolved = output_requested.resolve(strict=False)
    if output_resolved == source_resolved:
        raise RuntimeError("bridge output must not overwrite the source Rekordbox XML")
    if output_requested.exists():
        raise RuntimeError(
            f"bridge output already exists; refusing overwrite: {output_requested}"
        )
    if not output_requested.parent.is_dir():
        raise RuntimeError("bridge output parent directory must already exist")

    if source_resolved.read_bytes() != source_raw:
        raise RuntimeError("source Rekordbox XML changed before bridge write")
    with output_requested.open("xb") as handle:
        handle.write(bridge_raw)
        handle.flush()
        os.fsync(handle.fileno())
    if output_requested.read_bytes() != bridge_raw:
        raise RuntimeError("bridge XML write verification failed")
    if source_resolved.read_bytes() != source_raw:
        raise RuntimeError("source Rekordbox XML changed during bridge generation")

    return {
        "tool": "rekordbox_bridge_xml",
        "tool_version": TOOL_VERSION,
        "bridge_schema_version": BRIDGE_SCHEMA_VERSION,
        "safety": {
            "classification": SAFETY_CLASSIFICATION,
            "writes_rekordbox_master_db": False,
            "overwrites_source_xml": False,
            "writes_serato": False,
            "writes_audio": False,
            "writes_library_index": False,
            "output_is_new_xml_only": True,
        },
        "source": {
            "xml_path": str(source_resolved),
            "xml_sha256": source_sha,
            "preview_path": str(preview_resolved),
            "preview_sha256": preview_sha,
            "review_path": str(review_resolved) if review_resolved else None,
            "review_sha256": (
                _sha256_bytes(review_raw) if review_raw is not None else None
            ),
        },
        "output": {
            "xml_path": str(output_requested),
            "xml_sha256": _sha256_bytes(bridge_raw),
            "bytes": len(bridge_raw),
        },
        "summary": {
            "source_collection_tracks": len(source_parsed.tracks),
            "bridge_collection_tracks": len(approved),
            "excluded_tracks": len(source_parsed.tracks) - len(approved),
            "playlist_entries_kept": playlist_kept,
            "playlist_entries_removed": playlist_removed,
            "explicitly_unresolved_or_excluded": len(excluded),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-xml", type=Path, required=True)
    parser.add_argument("--preview", type=Path, required=True)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = build_bridge(
            args.source_xml,
            args.preview,
            args.output,
            review_path=args.review,
        )
    except Exception as exc:
        print(
            json.dumps({"ok": False, "error": str(exc)}, indent=2),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
