#!/usr/bin/env python3
"""Generate a Rekordbox Collection-add XML for library files Rekordbox has never seen.

Compares the current library index against the selected trusted Rekordbox
relationship snapshot. Any library file with no ``EXACT_PATH`` match in that
snapshot has never been imported into Rekordbox at all (as opposed to having
moved, which the relocation/bridge tools handle separately). This tool adds
those files as brand-new minimal Collection entries, grouped in a single
reviewable playlist, so importing them can never create duplicate entries for
tracks Rekordbox already has. It never opens or writes Rekordbox's encrypted
``master.db`` and never writes Serato, audio, or the SQLite index. It creates
one new XML file at an unused path.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "preview-only"

import argparse
import hashlib
import json
import os
import sys
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import rekordbox_bridge_xml
import rekordbox_index
import rekordbox_relocation_preview
import serato_rekordbox_crosswalk

TOOL_VERSION = "1.0.0"
MISSING_COLLECTION_XML_SCHEMA_VERSION = 1


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _valid_output_name(value: str, *, label: str) -> str:
    normalized = unicodedata.normalize("NFC", value.strip())
    if not normalized:
        raise RuntimeError(f"{label} must not be blank")
    if any(ord(char) < 32 for char in normalized):
        raise RuntimeError(f"{label} contains a control character")
    return normalized


def _missing_file_rows(
    conn: Any, rekordbox_import_id: int
) -> list[Any]:
    resolved = {
        int(row["file_id"])
        for row in conn.execute(
            "SELECT DISTINCT file_id FROM rekordbox_tracks "
            "WHERE import_id=? AND match_state='EXACT_PATH' AND file_id IS NOT NULL",
            (rekordbox_import_id,),
        )
    }
    rows = list(conn.execute("SELECT * FROM files ORDER BY file_id"))
    return [row for row in rows if int(row["file_id"]) not in resolved]


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


def _build_xml(rows: list[Any], playlist_name: str, folder_name: str) -> bytes:
    root = ET.Element("DJ_PLAYLISTS", {"Version": "1.0.0"})
    collection = ET.SubElement(root, "COLLECTION", {"Entries": str(len(rows))})
    track_ids: list[str] = []
    for ordinal, row in enumerate(rows, start=1):
        track_id = str(ordinal)
        track_ids.append(track_id)
        collection.append(_minimal_track(row, track_id))

    playlists = ET.SubElement(root, "PLAYLISTS")
    playlist_root = ET.SubElement(
        playlists, "NODE", {"Type": "0", "Name": "ROOT", "Count": "1"}
    )
    folder = ET.SubElement(
        playlist_root, "NODE", {"Name": folder_name, "Type": "0", "Count": "1"}
    )
    playlist_node = ET.SubElement(
        folder,
        "NODE",
        {"Name": playlist_name, "Type": "1", "KeyType": "0", "Entries": str(len(rows))},
    )
    for track_id in track_ids:
        ET.SubElement(playlist_node, "TRACK", {"Key": track_id})

    body = ET.tostring(root, encoding="utf-8", short_empty_elements=True)
    return b'<?xml version="1.0" encoding="UTF-8" ?>\n' + body


def _verify_output(raw: bytes, rows: list[Any], playlist_name: str) -> None:
    parsed = rekordbox_index._parse_export(raw)
    if parsed.declared_collection_count != len(rows):
        raise RuntimeError("generated collection XML entry count verification failed")
    nodes = [node for node in parsed.playlist_nodes if node["node_type"] == 1]
    if len(nodes) != 1 or nodes[0]["name"] != playlist_name:
        raise RuntimeError("generated collection XML playlist verification failed")
    memberships = [m for m in parsed.memberships if m["node_key"] == nodes[0]["node_key"]]
    if len(memberships) != len(rows):
        raise RuntimeError("generated collection XML membership count verification failed")
    expected_paths = {
        rekordbox_bridge_xml._path_to_location(str(row["display_path"])) for row in rows
    }
    got_paths = {track["attributes"]["Location"] for track in parsed.tracks}
    if got_paths != expected_paths:
        raise RuntimeError("generated collection XML location set verification failed")


def generate_missing_collection_xml(
    db_path: Path,
    output_path: Path,
    *,
    rekordbox_import_id: int | None,
    playlist_name: str,
    folder_name: str,
) -> dict[str, Any]:
    playlist_name = _valid_output_name(playlist_name, label="playlist_name")
    folder_name = _valid_output_name(folder_name, label="folder_name")

    conn = rekordbox_relocation_preview._readonly_connect(db_path)
    try:
        library_state = rekordbox_relocation_preview._validate_index_state(conn)
        rekordbox_import = serato_rekordbox_crosswalk._select_completed_import(
            conn, table="rekordbox_imports", import_id=rekordbox_import_id, label="Rekordbox"
        )
        rows = _missing_file_rows(conn, int(rekordbox_import["import_id"]))
        if not rows:
            raise RuntimeError("no library files are missing from the selected Rekordbox import")
        for row in rows:
            _verify_current_file(row)
        raw = _build_xml(rows, playlist_name, folder_name)
        _verify_output(raw, rows, playlist_name)
    finally:
        conn.close()

    output_requested = output_path.expanduser().absolute()
    if output_requested.suffix.casefold() != ".xml":
        raise RuntimeError("output path must end in .xml")
    if output_requested.is_symlink():
        raise RuntimeError(f"output must not be a symlink: {output_requested}")
    if output_requested.exists():
        raise RuntimeError(f"output already exists; refusing overwrite: {output_requested}")
    if not output_requested.parent.is_dir():
        raise RuntimeError("output parent directory must already exist")

    with output_requested.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    if output_requested.read_bytes() != raw:
        raise RuntimeError("collection XML write verification failed")

    return {
        "tool": "rekordbox_missing_collection_xml",
        "tool_version": TOOL_VERSION,
        "missing_collection_xml_schema_version": MISSING_COLLECTION_XML_SCHEMA_VERSION,
        "safety": {
            "classification": SAFETY_CLASSIFICATION,
            "writes_rekordbox_master_db": False,
            "writes_serato": False,
            "writes_audio": False,
            "writes_library_index": False,
            "output_is_new_xml_only": True,
        },
        "library": library_state,
        "rekordbox": {
            "import_id": int(rekordbox_import["import_id"]),
            "completed_at": rekordbox_import["completed_at"],
            "xml_path": rekordbox_import["xml_path"],
        },
        "output": {
            "xml_path": str(output_requested),
            "xml_sha256": _sha256(raw),
            "bytes": len(raw),
            "folder_name": folder_name,
            "playlist_name": playlist_name,
        },
        "summary": {
            "tracks_added": len(rows),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rekordbox-import-id", type=int)
    parser.add_argument("--playlist-name", default="Not Yet In Rekordbox")
    parser.add_argument("--folder-name", default="Library Completion")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = generate_missing_collection_xml(
            args.db,
            args.output,
            rekordbox_import_id=args.rekordbox_import_id,
            playlist_name=args.playlist_name,
            folder_name=args.folder_name,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
