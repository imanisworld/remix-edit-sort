#!/usr/bin/env python3
"""Generate a reviewed Serato-crate playlist XML for manual Rekordbox import.

The immutable crosswalk is re-built from the selected trusted snapshots before
any output is authorized. A separate human review packet, SHA-bound to that
crosswalk, decides which crates may become new Rekordbox playlists. The tool
never opens or writes Rekordbox ``master.db`` and never writes Serato, audio,
or the SQLite index. It creates one new XML file at an unused path.
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

import library_index
import rekordbox_bridge_xml
import rekordbox_index
import rekordbox_relocation_preview
import serato_rekordbox_crosswalk
import serato_rekordbox_transfer_review

TOOL_VERSION = "1.0.0"
PLAYLIST_XML_SCHEMA_VERSION = 1
FULL_CREATE = "CREATE_NEW_REKORDBOX_PLAYLIST"
PARTIAL_CREATE = "CREATE_PARTIAL_REKORDBOX_PLAYLIST"
CREATE_DECISIONS = {FULL_CREATE, PARTIAL_CREATE}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be a JSON object")
    return resolved, raw, value


def _normalize_name(value: str) -> str:
    return unicodedata.normalize("NFC", " ".join(value.strip().split())).casefold()


def _valid_output_name(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise RuntimeError(f"{label} must be text")
    normalized = unicodedata.normalize("NFC", value.strip())
    if not normalized:
        raise RuntimeError(f"{label} must not be blank")
    if any(ord(char) < 32 for char in normalized):
        raise RuntimeError(f"{label} contains a control character")
    return normalized


def _validate_review(
    review: dict[str, Any],
    crosswalk: dict[str, Any],
    crosswalk_sha: str,
) -> list[dict[str, Any]]:
    if review.get("tool") != "serato_rekordbox_transfer_review":
        raise RuntimeError("review input is not a Serato → Rekordbox transfer review")
    if review.get("review_schema_version") != 1:
        raise RuntimeError("unsupported transfer review schema")
    safety = review.get("safety")
    if not isinstance(safety, dict) or safety.get("automatic_decision") is not False:
        raise RuntimeError("review packet allows automatic decisions")
    for key in ("writes_serato", "writes_rekordbox", "writes_audio", "writes_library_index", "writes_xml"):
        if safety.get(key) is not False:
            raise RuntimeError(f"unsafe review flag {key!r}")

    source = review.get("source")
    if not isinstance(source, dict):
        raise RuntimeError("review.source must be an object")
    expected_source = {
        "crosswalk_sha256": crosswalk_sha,
        "library_scan_id": crosswalk["library"]["scan_id"],
        "serato_import_id": crosswalk["serato"]["import_id"],
        "rekordbox_import_id": crosswalk["rekordbox"]["import_id"],
    }
    for key, expected in expected_source.items():
        if source.get(key) != expected:
            raise RuntimeError(f"review source binding mismatch for {key}")

    plans = review.get("plans")
    if not isinstance(plans, list):
        raise RuntimeError("review.plans must be an array")
    crates = {crate["crate_name"]: crate for crate in crosswalk["crates"]}
    seen: set[str] = set()
    for plan in plans:
        if not isinstance(plan, dict):
            raise RuntimeError("review plan must be an object")
        name = plan.get("crate_name")
        if name not in crates:
            raise RuntimeError(f"review plan references unknown crate {name!r}")
        if name in seen:
            raise RuntimeError(f"duplicate review plan for crate {name!r}")
        seen.add(name)
        crate = crates[name]
        for field in ("entry_count", "resolved_entry_count", "unresolved_count", "mapping_state"):
            if plan.get(field) != crate.get(field):
                raise RuntimeError(f"review plan drift for crate {name!r}: {field}")
        if plan.get("automatic_decision") is not False:
            raise RuntimeError(f"automatic review decision cannot authorize crate {name!r}")
        decision = plan.get("decision")
        allowed = plan.get("allowed_decisions")
        if not isinstance(allowed, list):
            raise RuntimeError(f"review plan for {name!r} lacks allowed_decisions")
        if decision is not None and decision not in allowed:
            raise RuntimeError(f"invalid decision for crate {name!r}: {decision!r}")
        if decision in CREATE_DECISIONS:
            evidence = plan.get("decision_evidence")
            if not isinstance(evidence, list) or not evidence or not all(
                isinstance(item, str) and item.strip() for item in evidence
            ):
                raise RuntimeError(f"playlist creation for crate {name!r} needs human decision_evidence")
            _valid_output_name(plan.get("output_playlist_name"), label=f"crate {name!r} output_playlist_name")
            unresolved_count = int(crate["unresolved_count"])
            if decision == FULL_CREATE and unresolved_count:
                raise RuntimeError(
                    f"crate {name!r} has unresolved entries; full playlist creation is refused"
                )
            if decision == PARTIAL_CREATE and not unresolved_count:
                raise RuntimeError(
                    f"crate {name!r} has no unresolved entries; partial creation is invalid"
                )
        if decision == "MAP_TO_EXISTING_PLAYLIST":
            node_key = plan.get("selected_rekordbox_node_key")
            node_path = plan.get("selected_rekordbox_node_path")
            candidates = crate.get("candidate_playlists", [])
            if not any(
                item.get("node_key") == node_key and item.get("node_path") == node_path
                for item in candidates
                if isinstance(item, dict)
            ):
                raise RuntimeError(
                    f"existing-playlist selection for crate {name!r} is not a crosswalk candidate"
                )
    if seen != set(crates):
        raise RuntimeError("review packet does not cover every crosswalk crate")
    return plans


def _fingerprint_matches_import(
    source_xml: Path,
    import_row: Any,
) -> tuple[Any, bytes]:
    parsed, fingerprint = rekordbox_index._load_stable_export(source_xml)
    try:
        stored = json.loads(import_row["source_fingerprint_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("indexed Rekordbox source fingerprint is invalid") from exc
    stored_path = stored.get("path")
    if not isinstance(stored_path, str):
        raise RuntimeError("indexed Rekordbox source fingerprint has no path")
    stored_identity = library_index.mac_path_identity(
        library_index.canonicalize_path(Path(stored_path))
    )
    current_identity = library_index.mac_path_identity(
        library_index.canonicalize_path(source_xml)
    )
    if stored_identity != current_identity:
        raise RuntimeError("source Rekordbox XML path does not match the selected import")
    for key, current in (
        ("size_bytes", fingerprint.size_bytes),
        ("mtime_ns", fingerprint.mtime_ns),
        ("sha256", fingerprint.sha256),
    ):
        if stored.get(key) != current:
            raise RuntimeError(
                f"source Rekordbox XML {key} does not match the selected import"
            )
    raw, _ = rekordbox_index._read_stable(source_xml)
    return parsed, raw


def _selected_file_ids(
    crosswalk: dict[str, Any], plans: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[int]]:
    crates = {crate["crate_name"]: crate for crate in crosswalk["crates"]}
    selected: list[dict[str, Any]] = []
    first_seen: list[int] = []
    seen: set[int] = set()
    output_names: set[str] = set()
    for plan in plans:
        decision = plan.get("decision")
        if decision not in CREATE_DECISIONS:
            continue
        crate = crates[plan["crate_name"]]
        output_name = _valid_output_name(
            plan["output_playlist_name"],
            label=f"crate {plan['crate_name']!r} output_playlist_name",
        )
        identity = _normalize_name(output_name)
        if identity in output_names:
            raise RuntimeError(f"two selected crates produce the same playlist name {output_name!r}")
        output_names.add(identity)
        order: list[int] = []
        excluded_unresolved: list[dict[str, Any]] = []
        for entry in crate["ordered_entries"]:
            if entry.get("state") == "RESOLVED":
                file_id = entry.get("file_id")
                if not isinstance(file_id, int):
                    raise RuntimeError(f"resolved entry in crate {crate['crate_name']!r} lacks file_id")
                order.append(file_id)
                if file_id not in seen:
                    seen.add(file_id)
                    first_seen.append(file_id)
            else:
                excluded_unresolved.append(entry)
        if decision == FULL_CREATE and excluded_unresolved:
            raise RuntimeError(f"full playlist {output_name!r} would silently exclude unresolved entries")
        if not order:
            raise RuntimeError(f"selected crate {crate['crate_name']!r} has no resolved tracks")
        selected.append(
            {
                "crate_name": crate["crate_name"],
                "playlist_name": output_name,
                "decision": decision,
                "ordered_file_ids": order,
                "excluded_unresolved": excluded_unresolved,
            }
        )
    if not selected:
        raise RuntimeError("no crates are approved for new Rekordbox playlist XML")
    return selected, first_seen


def _current_file_rows(conn: Any, file_ids: list[int]) -> dict[int, Any]:
    if not file_ids:
        return {}
    placeholders = ",".join("?" for _ in file_ids)
    rows = list(
        conn.execute(
            f"SELECT * FROM files WHERE file_id IN ({placeholders})",
            tuple(file_ids),
        )
    )
    result = {int(row["file_id"]): row for row in rows}
    if set(result) != set(file_ids):
        raise RuntimeError("one or more reviewed file_ids are no longer in the current library index")
    return result


def _verify_current_file(row: Any) -> None:
    path = Path(row["display_path"])
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"selected audio file is missing or symlinked: {path}")
    before = path.stat(follow_symlinks=False)
    if before.st_size != int(row["size_bytes"]):
        raise RuntimeError(f"selected audio file byte size changed since library scan: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat(follow_symlinks=False)
    before_id = (before.st_size, before.st_mtime_ns, before.st_dev, before.st_ino)
    after_id = (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino)
    if before_id != after_id:
        raise RuntimeError(f"selected audio file changed while being verified: {path}")
    if digest.hexdigest() != row["sha256"]:
        raise RuntimeError(f"selected audio file SHA-256 changed since library scan: {path}")


def _source_track_elements(source_raw: bytes) -> dict[str, ET.Element]:
    root = ET.fromstring(source_raw)
    collection = root.find("COLLECTION")
    if collection is None:
        raise RuntimeError("source Rekordbox XML has no COLLECTION")
    result: dict[str, ET.Element] = {}
    for element in collection:
        track_id = element.attrib.get("TrackID")
        if not track_id or track_id in result:
            raise RuntimeError("source Rekordbox XML has missing or duplicate TrackID")
        result[track_id] = element
    return result


def _semantic_source_signature(track: dict[str, Any]) -> tuple[Any, ...]:
    attrs = dict(track["attributes"])
    attrs.pop("TrackID", None)
    return (
        tuple(sorted(attrs.items())),
        tuple(tuple(sorted(item.items())) for item in track["marks"]),
        tuple(tuple(sorted(item.items())) for item in track["tempos"]),
    )


def _source_track_for_file(
    conn: Any,
    parsed_source: Any,
    source_elements: dict[str, ET.Element],
    rekordbox_import_id: int,
    file_id: int,
) -> ET.Element | None:
    ids = [
        str(row["rekordbox_track_id"])
        for row in conn.execute(
            "SELECT rekordbox_track_id FROM rekordbox_tracks "
            "WHERE import_id=? AND file_id=? AND match_state='EXACT_PATH' "
            "ORDER BY rekordbox_track_id",
            (rekordbox_import_id, file_id),
        )
    ]
    if not ids:
        return None
    parsed_by_id = {str(track["track_id"]): track for track in parsed_source.tracks}
    missing = [track_id for track_id in ids if track_id not in parsed_by_id or track_id not in source_elements]
    if missing:
        raise RuntimeError(f"indexed Rekordbox TrackID(s) are absent from source XML: {missing}")
    signatures = {_semantic_source_signature(parsed_by_id[track_id]) for track_id in ids}
    if len(signatures) != 1:
        raise RuntimeError(
            f"multiple Rekordbox records for file_id {file_id} disagree on metadata/cues/grid"
        )
    return source_elements[ids[0]]


def _minimal_track(row: Any, track_id: str) -> ET.Element:
    path = str(row["display_path"])
    attrs: dict[str, str] = {
        "TrackID": track_id,
        "Name": str(row["title"] or Path(path).stem),
        "Location": rekordbox_bridge_xml._path_to_location(path),
    }
    for xml_name, db_name in (
        ("Artist", "artist"),
        ("Album", "album"),
        ("Genre", "genre"),
    ):
        value = row[db_name]
        if value not in (None, ""):
            attrs[xml_name] = str(value)
    return ET.Element("TRACK", attrs)


def _build_xml(
    source_parsed: Any,
    source_raw: bytes,
    conn: Any,
    rekordbox_import_id: int,
    file_rows: dict[int, Any],
    unique_file_ids: list[int],
    selected_playlists: list[dict[str, Any]],
    folder_name: str,
) -> tuple[bytes, dict[int, str], int]:
    source_elements = _source_track_elements(source_raw)
    root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    if source_parsed.product:
        ET.SubElement(root, "PRODUCT", dict(source_parsed.product))
    collection = ET.SubElement(root, "COLLECTION", {"Entries": str(len(unique_file_ids))})
    track_id_by_file: dict[int, str] = {}
    copied_source_tracks = 0

    for ordinal, file_id in enumerate(unique_file_ids, start=1):
        output_track_id = str(ordinal)
        track_id_by_file[file_id] = output_track_id
        row = file_rows[file_id]
        source_element = _source_track_for_file(
            conn,
            source_parsed,
            source_elements,
            rekordbox_import_id,
            file_id,
        )
        if source_element is None:
            element = _minimal_track(row, output_track_id)
        else:
            copied_source_tracks += 1
            element = copy.deepcopy(source_element)
            element.set("TrackID", output_track_id)
            element.set(
                "Location",
                rekordbox_bridge_xml._path_to_location(str(row["display_path"])),
            )
        collection.append(element)

    playlists = ET.SubElement(root, "PLAYLISTS")
    folder = ET.SubElement(
        playlists,
        "NODE",
        {
            "Name": folder_name,
            "Type": "0",
            "Count": str(len(selected_playlists)),
        },
    )
    for playlist in selected_playlists:
        node = ET.SubElement(
            folder,
            "NODE",
            {
                "Name": playlist["playlist_name"],
                "Type": "1",
                "KeyType": "0",
                "Entries": str(len(playlist["ordered_file_ids"])),
            },
        )
        for file_id in playlist["ordered_file_ids"]:
            ET.SubElement(node, "TRACK", {"Key": track_id_by_file[file_id]})

    body = ET.tostring(root, encoding="utf-8", short_empty_elements=True)
    raw = b'<?xml version="1.0" encoding="UTF-8" ?>\n' + body
    return raw, track_id_by_file, copied_source_tracks


def _verify_output(
    raw: bytes,
    selected_playlists: list[dict[str, Any]],
    track_id_by_file: dict[int, str],
) -> None:
    parsed = rekordbox_index._parse_export(raw)
    if parsed.declared_collection_count != len(track_id_by_file):
        raise RuntimeError("generated playlist XML collection count verification failed")
    id_to_file = {track_id: file_id for file_id, track_id in track_id_by_file.items()}
    nodes = [node for node in parsed.playlist_nodes if node["node_type"] == 1]
    if len(nodes) != len(selected_playlists):
        raise RuntimeError("generated playlist XML playlist count verification failed")
    node_by_key = {node["node_key"]: node for node in nodes}
    memberships: dict[str, list[int]] = {node["node_key"]: [] for node in nodes}
    for item in sorted(parsed.memberships, key=lambda value: (value["node_key"], value["ordinal"])):
        if item["node_key"] not in node_by_key:
            continue
        file_id = id_to_file.get(str(item["raw_key"]))
        if file_id is None:
            raise RuntimeError("generated playlist membership references an unknown output TrackID")
        memberships[item["node_key"]].append(file_id)
    generated_by_name = {node["name"]: memberships[node["node_key"]] for node in nodes}
    expected_by_name = {
        item["playlist_name"]: item["ordered_file_ids"] for item in selected_playlists
    }
    if generated_by_name != expected_by_name:
        raise RuntimeError("generated playlist order verification failed")


def generate_playlist_xml(
    db_path: Path,
    source_xml: Path,
    crosswalk_path: Path,
    review_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    crosswalk_resolved, crosswalk_raw, crosswalk = _load_json(
        crosswalk_path, label="crosswalk"
    )
    serato_rekordbox_transfer_review.validate_crosswalk(crosswalk)
    review_resolved, review_raw, review = _load_json(review_path, label="transfer review")
    crosswalk_sha = _sha256(crosswalk_raw)
    plans = _validate_review(review, crosswalk, crosswalk_sha)
    folder_name = _valid_output_name(
        review.get("output_folder_name", "Serato Transfer"),
        label="review.output_folder_name",
    )
    selected_playlists, unique_file_ids = _selected_file_ids(crosswalk, plans)

    conn = rekordbox_relocation_preview._readonly_connect(db_path)
    try:
        library_state = rekordbox_relocation_preview._validate_index_state(conn)
        if int(library_state["scan_id"]) != int(crosswalk["library"]["scan_id"]):
            raise RuntimeError("library scan changed since crosswalk; playlist XML generation blocked")
        serato_import_id = int(crosswalk["serato"]["import_id"])
        rekordbox_import_id = int(crosswalk["rekordbox"]["import_id"])
        rebuilt = serato_rekordbox_crosswalk.build_crosswalk(
            db_path,
            serato_import_id=serato_import_id,
            rekordbox_import_id=rekordbox_import_id,
        )
        if rebuilt != crosswalk:
            raise RuntimeError(
                "live trusted snapshots no longer reproduce the reviewed crosswalk"
            )

        rekordbox_import = serato_rekordbox_crosswalk._select_completed_import(
            conn,
            table="rekordbox_imports",
            import_id=rekordbox_import_id,
            label="Rekordbox",
        )
        source_resolved = source_xml.expanduser().absolute().resolve()
        source_parsed, source_raw = _fingerprint_matches_import(
            source_resolved, rekordbox_import
        )

        file_rows = _current_file_rows(conn, unique_file_ids)
        for file_id in unique_file_ids:
            catalog_entry = crosswalk["track_catalog"].get(str(file_id))
            if not isinstance(catalog_entry, dict):
                raise RuntimeError(f"crosswalk track_catalog lacks selected file_id {file_id}")
            if catalog_entry.get("path") != file_rows[file_id]["display_path"]:
                raise RuntimeError(f"crosswalk path drift for selected file_id {file_id}")
            _verify_current_file(file_rows[file_id])

        output_raw, track_id_by_file, copied_source_tracks = _build_xml(
            source_parsed,
            source_raw,
            conn,
            rekordbox_import_id,
            file_rows,
            unique_file_ids,
            selected_playlists,
            folder_name,
        )
        _verify_output(output_raw, selected_playlists, track_id_by_file)
    finally:
        conn.close()

    output_requested = output_path.expanduser().absolute()
    if output_requested.suffix.casefold() != ".xml":
        raise RuntimeError("playlist output path must end in .xml")
    if output_requested.is_symlink():
        raise RuntimeError(f"playlist output must not be a symlink: {output_requested}")
    if output_requested.exists():
        raise RuntimeError(f"playlist output already exists; refusing overwrite: {output_requested}")
    if not output_requested.parent.is_dir():
        raise RuntimeError("playlist output parent directory must already exist")
    if output_requested.resolve(strict=False) == source_resolved:
        raise RuntimeError("playlist output must not overwrite the source Rekordbox XML")

    source_before_write = source_resolved.read_bytes()
    if _sha256(source_before_write) != _sha256(source_raw):
        raise RuntimeError("source Rekordbox XML changed before playlist XML write")
    with output_requested.open("xb") as handle:
        handle.write(output_raw)
        handle.flush()
        os.fsync(handle.fileno())
    if output_requested.read_bytes() != output_raw:
        raise RuntimeError("playlist XML write verification failed")
    if source_resolved.read_bytes() != source_before_write:
        raise RuntimeError("source Rekordbox XML changed during playlist XML generation")

    partial_count = sum(
        item["decision"] == PARTIAL_CREATE for item in selected_playlists
    )
    excluded_unresolved = sum(
        len(item["excluded_unresolved"]) for item in selected_playlists
    )
    return {
        "tool": "serato_rekordbox_playlist_xml",
        "tool_version": TOOL_VERSION,
        "playlist_xml_schema_version": PLAYLIST_XML_SCHEMA_VERSION,
        "safety": {
            "classification": SAFETY_CLASSIFICATION,
            "writes_rekordbox_master_db": False,
            "overwrites_source_xml": False,
            "writes_serato": False,
            "writes_audio": False,
            "writes_library_index": False,
            "output_is_new_xml_only": True,
            "automatic_playlist_creation": False,
        },
        "source": {
            "crosswalk_path": str(crosswalk_resolved),
            "crosswalk_sha256": crosswalk_sha,
            "review_path": str(review_resolved),
            "review_sha256": _sha256(review_raw),
            "rekordbox_xml_path": str(source_resolved),
            "rekordbox_xml_sha256": _sha256(source_raw),
        },
        "output": {
            "xml_path": str(output_requested),
            "xml_sha256": _sha256(output_raw),
            "bytes": len(output_raw),
            "folder_name": folder_name,
        },
        "summary": {
            "playlists_created": len(selected_playlists),
            "partial_playlists": partial_count,
            "unique_tracks": len(unique_file_ids),
            "playlist_entries": sum(len(item["ordered_file_ids"]) for item in selected_playlists),
            "unresolved_entries_explicitly_excluded": excluded_unresolved,
            "tracks_preserving_rekordbox_metadata_cues_grid": copied_source_tracks,
            "serato_only_tracks_added_minimally": len(unique_file_ids) - copied_source_tracks,
        },
        "playlists": selected_playlists,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--source-xml", type=Path, required=True)
    parser.add_argument("--crosswalk", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = generate_playlist_xml(
            args.db,
            args.source_xml,
            args.crosswalk,
            args.review,
            args.output,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
