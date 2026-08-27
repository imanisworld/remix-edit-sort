#!/usr/bin/env python3
"""Import a supported Rekordbox XML export into the local library index.

The input must be the collection XML exported from Rekordbox's File > Library
menu.  This tool never opens or writes Rekordbox's encrypted ``master.db`` and
never writes audio files, Rekordbox data, Serato data, or performance devices.
It writes only additive relationship tables in the local SQLite index.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote_to_bytes

import library_index


TOOL_VERSION = "1.0.0"
REKORDBOX_SCHEMA_VERSION = 1

MATCH_STATES = ("EXACT_PATH", "MOVED_LIKELY", "AMBIGUOUS", "MISSING")

REKORDBOX_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS rekordbox_imports (
    import_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    xml_path TEXT NOT NULL,
    source_fingerprint_json TEXT NOT NULL,
    product_name TEXT,
    product_version TEXT,
    product_company TEXT,
    declared_collection_count INTEGER NOT NULL,
    collection_track_count INTEGER NOT NULL,
    playlist_node_count INTEGER NOT NULL,
    playlist_count INTEGER NOT NULL,
    playlist_reference_count INTEGER NOT NULL,
    unresolved_playlist_reference_count INTEGER NOT NULL,
    position_mark_count INTEGER NOT NULL,
    tempo_count INTEGER NOT NULL,
    exact_path_count INTEGER NOT NULL,
    moved_likely_count INTEGER NOT NULL,
    ambiguous_count INTEGER NOT NULL,
    missing_count INTEGER NOT NULL,
    completed_successfully INTEGER NOT NULL,
    tool_version TEXT NOT NULL,
    schema_version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS rekordbox_tracks (
    import_id INTEGER NOT NULL,
    rekordbox_track_id TEXT NOT NULL,
    location_uri TEXT NOT NULL,
    absolute_path TEXT NOT NULL,
    match_state TEXT NOT NULL CHECK(match_state IN ('EXACT_PATH','MOVED_LIKELY','AMBIGUOUS','MISSING')),
    file_id INTEGER,
    matched_display_path TEXT,
    name TEXT,
    artist TEXT,
    album TEXT,
    genre TEXT,
    remixer TEXT,
    tonality TEXT,
    comments TEXT,
    date_added TEXT,
    bitrate TEXT,
    sample_rate TEXT,
    play_count TEXT,
    last_played TEXT,
    rating TEXT,
    attributes_json TEXT NOT NULL,
    candidates_json TEXT NOT NULL,
    PRIMARY KEY(import_id, rekordbox_track_id),
    FOREIGN KEY(import_id) REFERENCES rekordbox_imports(import_id)
);
CREATE INDEX IF NOT EXISTS idx_rekordbox_tracks_import_state
    ON rekordbox_tracks(import_id, match_state);
CREATE INDEX IF NOT EXISTS idx_rekordbox_tracks_file
    ON rekordbox_tracks(file_id);
CREATE TABLE IF NOT EXISTS rekordbox_position_marks (
    import_id INTEGER NOT NULL,
    rekordbox_track_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    name TEXT,
    mark_type TEXT,
    start_seconds TEXT,
    end_seconds TEXT,
    hotcue_number TEXT,
    red TEXT,
    green TEXT,
    blue TEXT,
    attributes_json TEXT NOT NULL,
    PRIMARY KEY(import_id, rekordbox_track_id, ordinal),
    FOREIGN KEY(import_id) REFERENCES rekordbox_imports(import_id)
);
CREATE TABLE IF NOT EXISTS rekordbox_tempos (
    import_id INTEGER NOT NULL,
    rekordbox_track_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    start_seconds TEXT,
    bpm TEXT,
    meter TEXT,
    beat TEXT,
    attributes_json TEXT NOT NULL,
    PRIMARY KEY(import_id, rekordbox_track_id, ordinal),
    FOREIGN KEY(import_id) REFERENCES rekordbox_imports(import_id)
);
CREATE TABLE IF NOT EXISTS rekordbox_playlist_nodes (
    import_id INTEGER NOT NULL,
    node_key TEXT NOT NULL,
    parent_node_key TEXT,
    node_path TEXT NOT NULL,
    name TEXT NOT NULL,
    node_type INTEGER NOT NULL CHECK(node_type IN (0,1)),
    key_type INTEGER,
    declared_count INTEGER,
    position INTEGER NOT NULL,
    attributes_json TEXT NOT NULL,
    PRIMARY KEY(import_id, node_key),
    FOREIGN KEY(import_id) REFERENCES rekordbox_imports(import_id)
);
CREATE TABLE IF NOT EXISTS rekordbox_playlist_memberships (
    import_id INTEGER NOT NULL,
    node_key TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    raw_key TEXT NOT NULL,
    reference_state TEXT NOT NULL CHECK(reference_state IN ('RESOLVED','AMBIGUOUS','MISSING')),
    rekordbox_track_id TEXT,
    candidates_json TEXT NOT NULL,
    PRIMARY KEY(import_id, node_key, ordinal),
    FOREIGN KEY(import_id) REFERENCES rekordbox_imports(import_id)
);
CREATE INDEX IF NOT EXISTS idx_rekordbox_playlist_track
    ON rekordbox_playlist_memberships(import_id, rekordbox_track_id);
"""


@dataclass(frozen=True)
class SourceFingerprint:
    path: str
    size_bytes: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class ParsedExport:
    product: dict[str, str]
    declared_collection_count: int
    tracks: list[dict[str, Any]]
    playlist_nodes: list[dict[str, Any]]
    memberships: list[dict[str, Any]]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_stable(path: Path) -> tuple[bytes, SourceFingerprint]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"Rekordbox XML must be a regular, non-symlink file: {path}")
    before = path.stat(follow_symlinks=False)
    payload = path.read_bytes()
    after = path.stat(follow_symlinks=False)
    before_identity = (before.st_size, before.st_mtime_ns, before.st_dev, before.st_ino)
    after_identity = (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino)
    if before_identity != after_identity or len(payload) != after.st_size:
        raise RuntimeError(f"Rekordbox XML changed while being read: {path}")
    return payload, SourceFingerprint(
        path=os.fspath(path),
        size_bytes=after.st_size,
        mtime_ns=after.st_mtime_ns,
        sha256=_sha256_bytes(payload),
    )


def _parse_nonnegative_int(value: str | None, *, context: str, required: bool) -> int | None:
    if value is None:
        if required:
            raise RuntimeError(f"missing integer attribute in {context}")
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise RuntimeError(f"invalid integer {value!r} in {context}") from exc
    if parsed < 0:
        raise RuntimeError(f"negative integer {value!r} in {context}")
    return parsed


def _location_to_path(location: str, *, context: str) -> str:
    if location[:5].casefold() != "file:":
        raise RuntimeError(f"non-file Location URI in {context}: {location!r}")
    remainder = location[5:]
    if remainder.startswith("//"):
        authority_and_path = remainder[2:]
        slash = authority_and_path.find("/")
        if slash < 0:
            raise RuntimeError(f"file Location has no absolute path in {context}: {location!r}")
        authority = authority_and_path[:slash]
        raw_path = authority_and_path[slash:]
        if authority.casefold() not in ("", "localhost"):
            raise RuntimeError(f"remote file host in {context}: {location!r}")
    elif remainder.startswith("/"):
        raw_path = remainder
    else:
        raise RuntimeError(f"non-absolute file Location in {context}: {location!r}")
    # Rekordbox's own XML exporter leaves literal '#' and '?' characters in
    # filenames instead of percent-encoding them.  They are path bytes here,
    # not URI fragment/query delimiters, so preserve the complete remainder.
    try:
        decoded = unquote_to_bytes(raw_path).decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"invalid UTF-8 file Location in {context}") from exc
    if not decoded.startswith("/"):
        raise RuntimeError(f"non-absolute file Location in {context}: {location!r}")
    return unicodedata.normalize("NFC", decoded)


def _parse_track(element: ET.Element, *, index: int) -> dict[str, Any]:
    context = f"COLLECTION/TRACK[{index}]"
    attributes = dict(element.attrib)
    track_id = attributes.get("TrackID")
    location = attributes.get("Location")
    if not track_id:
        raise RuntimeError(f"missing TrackID in {context}")
    if not location:
        raise RuntimeError(f"missing Location in {context}")
    marks: list[dict[str, str]] = []
    tempos: list[dict[str, str]] = []
    for child in element:
        if child.tag == "POSITION_MARK":
            marks.append(dict(child.attrib))
        elif child.tag == "TEMPO":
            tempos.append(dict(child.attrib))
        else:
            raise RuntimeError(f"unsupported element {child.tag!r} in {context}")
    return {
        "track_id": track_id,
        "location_uri": location,
        "absolute_path": _location_to_path(location, context=context),
        "attributes": attributes,
        "marks": marks,
        "tempos": tempos,
    }


def _parse_playlists(playlists: ET.Element) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    memberships: list[dict[str, Any]] = []

    def visit(element: ET.Element, *, parent_key: str | None, parent_path: str, position: int) -> None:
        if element.tag != "NODE":
            raise RuntimeError(f"unsupported element {element.tag!r} under PLAYLISTS")
        key = str(position) if parent_key is None else f"{parent_key}.{position}"
        context = f"PLAYLISTS/NODE[{key}]"
        name = element.attrib.get("Name")
        if name is None:
            raise RuntimeError(f"missing Name in {context}")
        node_type = _parse_nonnegative_int(element.attrib.get("Type"), context=context, required=True)
        if node_type not in (0, 1):
            raise RuntimeError(f"unsupported node Type {node_type!r} in {context}")
        key_type = _parse_nonnegative_int(element.attrib.get("KeyType"), context=context, required=False)
        declared_count = _parse_nonnegative_int(
            element.attrib.get("Entries", element.attrib.get("Count")),
            context=context,
            required=False,
        )
        path = f"{parent_path} / {name}" if parent_path else name
        children = list(element)
        if node_type == 0:
            if key_type is not None:
                raise RuntimeError(f"folder node unexpectedly has KeyType in {context}")
            if any(child.tag != "NODE" for child in children):
                raise RuntimeError(f"folder node contains non-NODE child in {context}")
        else:
            if key_type not in (0, 1):
                raise RuntimeError(f"playlist node requires KeyType 0 or 1 in {context}")
            if any(child.tag != "TRACK" for child in children):
                raise RuntimeError(f"playlist node contains non-TRACK child in {context}")
        if declared_count is not None and declared_count != len(children):
            raise RuntimeError(
                f"declared count {declared_count} does not match {len(children)} children in {context}"
            )
        nodes.append(
            {
                "node_key": key,
                "parent_node_key": parent_key,
                "node_path": path,
                "name": name,
                "node_type": node_type,
                "key_type": key_type,
                "declared_count": declared_count,
                "position": position,
                "attributes": dict(element.attrib),
            }
        )
        if node_type == 0:
            for child_position, child in enumerate(children):
                visit(child, parent_key=key, parent_path=path, position=child_position)
        else:
            for ordinal, child in enumerate(children):
                raw_key = child.attrib.get("Key")
                if raw_key is None:
                    raise RuntimeError(f"missing TRACK Key in {context} at ordinal {ordinal}")
                memberships.append(
                    {
                        "node_key": key,
                        "ordinal": ordinal,
                        "raw_key": raw_key,
                        "key_type": key_type,
                    }
                )

    for position, child in enumerate(playlists):
        visit(child, parent_key=None, parent_path="", position=position)
    return nodes, memberships


def _parse_export(payload: bytes) -> ParsedExport:
    upper = payload.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise RuntimeError("DOCTYPE and ENTITY declarations are not allowed in Rekordbox XML")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise RuntimeError(f"invalid Rekordbox XML: {exc}") from exc
    if root.tag != "DJ_PLAYLISTS":
        raise RuntimeError(f"expected DJ_PLAYLISTS root, found {root.tag!r}")

    allowed = {"PRODUCT", "COLLECTION", "PLAYLISTS"}
    unexpected = [child.tag for child in root if child.tag not in allowed]
    if unexpected:
        raise RuntimeError(f"unsupported DJ_PLAYLISTS element(s): {unexpected}")
    products = root.findall("PRODUCT")
    collections = root.findall("COLLECTION")
    playlist_roots = root.findall("PLAYLISTS")
    if len(products) > 1 or len(collections) != 1 or len(playlist_roots) > 1:
        raise RuntimeError(
            "Rekordbox XML must contain exactly one COLLECTION and at most one PRODUCT and PLAYLISTS"
        )
    collection = collections[0]
    declared = _parse_nonnegative_int(
        collection.attrib.get("Entries"), context="COLLECTION", required=True
    )
    track_elements = list(collection)
    if any(element.tag != "TRACK" for element in track_elements):
        raise RuntimeError("COLLECTION contains an unsupported non-TRACK element")
    if declared != len(track_elements):
        raise RuntimeError(
            f"COLLECTION declares {declared} entries but contains {len(track_elements)} tracks"
        )
    tracks = [_parse_track(element, index=index) for index, element in enumerate(track_elements)]
    track_ids = [track["track_id"] for track in tracks]
    if len(track_ids) != len(set(track_ids)):
        raise RuntimeError("duplicate TrackID in Rekordbox COLLECTION")

    nodes: list[dict[str, Any]] = []
    memberships: list[dict[str, Any]] = []
    if playlist_roots:
        nodes, memberships = _parse_playlists(playlist_roots[0])
    return ParsedExport(
        product=dict(products[0].attrib) if products else {},
        declared_collection_count=int(declared),
        tracks=tracks,
        playlist_nodes=nodes,
        memberships=memberships,
    )


def _load_stable_export(xml_path: Path) -> tuple[ParsedExport, SourceFingerprint]:
    first_payload, before = _read_stable(xml_path)
    parsed = _parse_export(first_payload)
    second_payload, after = _read_stable(xml_path)
    if before != after or first_payload != second_payload:
        raise RuntimeError(
            "Rekordbox XML changed during import; prior trusted state was preserved"
        )
    return parsed, after


def _ensure_rekordbox_schema(conn: sqlite3.Connection) -> None:
    existing = conn.execute(
        "SELECT value FROM meta WHERE key='rekordbox_schema_version'"
    ).fetchone()
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'rekordbox_%'"
        )
    }
    if existing is not None:
        if int(existing[0]) != REKORDBOX_SCHEMA_VERSION:
            raise RuntimeError(
                f"unsupported Rekordbox index schema {existing[0]}; "
                f"expected {REKORDBOX_SCHEMA_VERSION}"
            )
    elif tables:
        raise RuntimeError(
            "Rekordbox relationship tables exist without a recognized schema version"
        )
    conn.executescript(REKORDBOX_SCHEMA_SQL)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('rekordbox_schema_version',?)",
        (str(REKORDBOX_SCHEMA_VERSION),),
    )
    conn.commit()


def _filename_identity(path: str) -> str:
    return unicodedata.normalize("NFC", Path(path).name).casefold()


def _assert_distinct_files(db_path: Path, xml_path: Path) -> None:
    db_real = library_index.canonicalize_path(db_path.expanduser().absolute())
    xml_real = library_index.canonicalize_path(xml_path.expanduser().absolute())
    if db_real == xml_real:
        raise ValueError("SQLite database and Rekordbox XML must be different files")


def _match_tracks(
    parsed: ParsedExport, indexed_rows: list[sqlite3.Row]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    by_identity = {row["path_identity"]: row for row in indexed_rows}
    by_filename: dict[str, list[sqlite3.Row]] = {}
    for row in indexed_rows:
        by_filename.setdefault(_filename_identity(row["canonical_path"]), []).append(row)
    counts = {state: 0 for state in MATCH_STATES}
    prepared: list[dict[str, Any]] = []
    for track in parsed.tracks:
        canonical = library_index.canonicalize_path(Path(track["absolute_path"]))
        exact = by_identity.get(library_index.mac_path_identity(canonical))
        candidates: list[sqlite3.Row] = []
        matched = None
        if exact is not None:
            state = "EXACT_PATH"
            matched = exact
        else:
            candidates = by_filename.get(_filename_identity(track["absolute_path"]), [])
            if len(candidates) == 1:
                state = "MOVED_LIKELY"
            elif len(candidates) > 1:
                state = "AMBIGUOUS"
            else:
                state = "MISSING"
        counts[state] += 1
        prepared.append(
            {
                **track,
                "match_state": state,
                "file_id": int(matched["file_id"]) if matched is not None else None,
                "matched_display_path": matched["display_path"] if matched is not None else None,
                "candidates": [
                    {"file_id": int(row["file_id"]), "display_path": row["display_path"]}
                    for row in candidates
                ],
            }
        )
    return prepared, counts


def _resolve_memberships(
    parsed: ParsedExport,
) -> tuple[list[dict[str, Any]], int]:
    by_id = {track["track_id"]: track for track in parsed.tracks}
    by_location: dict[str, list[dict[str, Any]]] = {}
    for track in parsed.tracks:
        by_location.setdefault(track["location_uri"], []).append(track)
    resolved: list[dict[str, Any]] = []
    unresolved = 0
    for membership in parsed.memberships:
        if membership["key_type"] == 0:
            candidates = [by_id[membership["raw_key"]]] if membership["raw_key"] in by_id else []
        else:
            candidates = by_location.get(membership["raw_key"], [])
        if len(candidates) == 1:
            state = "RESOLVED"
            track_id = candidates[0]["track_id"]
        elif len(candidates) > 1:
            state = "AMBIGUOUS"
            track_id = None
            unresolved += 1
        else:
            state = "MISSING"
            track_id = None
            unresolved += 1
        resolved.append(
            {
                **membership,
                "reference_state": state,
                "rekordbox_track_id": track_id,
                "candidates": [candidate["track_id"] for candidate in candidates],
            }
        )
    return resolved, unresolved


def inspect_xml(xml_path: Path) -> dict[str, Any]:
    xml_path = xml_path.expanduser().absolute()
    parsed, fingerprint = _load_stable_export(xml_path)
    resolved, unresolved = _resolve_memberships(parsed)
    return {
        "xml_path": os.fspath(xml_path),
        "source_fingerprint": fingerprint.__dict__,
        "product": parsed.product,
        "collection_tracks": len(parsed.tracks),
        "playlist_nodes": len(parsed.playlist_nodes),
        "playlists": sum(node["node_type"] == 1 for node in parsed.playlist_nodes),
        "playlist_references": len(resolved),
        "unresolved_playlist_references": unresolved,
        "position_marks": sum(len(track["marks"]) for track in parsed.tracks),
        "tempos": sum(len(track["tempos"]) for track in parsed.tracks),
    }


def import_rekordbox(xml_path: Path, db_path: Path) -> dict[str, Any]:
    started_at = library_index.utc_now()
    xml_path = xml_path.expanduser().absolute()
    _assert_distinct_files(db_path, xml_path)
    conn = library_index.connect_database(db_path)
    try:
        bound_root = conn.execute(
            "SELECT value FROM meta WHERE key='root_canonical'"
        ).fetchone()
        if bound_root is None:
            raise RuntimeError("library index has no completed root-bound scan")
        _ensure_rekordbox_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        try:
            parsed, fingerprint = _load_stable_export(xml_path)
            tracks, counts = _match_tracks(parsed, list(conn.execute("SELECT * FROM files")))
            memberships, unresolved = _resolve_memberships(parsed)
            mark_count = sum(len(track["marks"]) for track in tracks)
            tempo_count = sum(len(track["tempos"]) for track in tracks)
            playlist_count = sum(node["node_type"] == 1 for node in parsed.playlist_nodes)
            cursor = conn.execute(
                """INSERT INTO rekordbox_imports(
                       started_at,completed_at,xml_path,source_fingerprint_json,
                       product_name,product_version,product_company,
                       declared_collection_count,collection_track_count,
                       playlist_node_count,playlist_count,playlist_reference_count,
                       unresolved_playlist_reference_count,position_mark_count,tempo_count,
                       exact_path_count,moved_likely_count,ambiguous_count,missing_count,
                       completed_successfully,tool_version,schema_version
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                (
                    started_at,
                    library_index.utc_now(),
                    os.fspath(xml_path),
                    json.dumps(fingerprint.__dict__, ensure_ascii=False, sort_keys=True),
                    parsed.product.get("Name"),
                    parsed.product.get("Version"),
                    parsed.product.get("Company"),
                    parsed.declared_collection_count,
                    len(tracks),
                    len(parsed.playlist_nodes),
                    playlist_count,
                    len(memberships),
                    unresolved,
                    mark_count,
                    tempo_count,
                    counts["EXACT_PATH"],
                    counts["MOVED_LIKELY"],
                    counts["AMBIGUOUS"],
                    counts["MISSING"],
                    TOOL_VERSION,
                    REKORDBOX_SCHEMA_VERSION,
                ),
            )
            import_id = int(cursor.lastrowid)
            conn.executemany(
                """INSERT INTO rekordbox_tracks(
                       import_id,rekordbox_track_id,location_uri,absolute_path,
                       match_state,file_id,matched_display_path,name,artist,album,genre,
                       remixer,tonality,comments,date_added,bitrate,sample_rate,play_count,
                       last_played,rating,attributes_json,candidates_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        import_id,
                        track["track_id"],
                        track["location_uri"],
                        track["absolute_path"],
                        track["match_state"],
                        track["file_id"],
                        track["matched_display_path"],
                        track["attributes"].get("Name"),
                        track["attributes"].get("Artist"),
                        track["attributes"].get("Album"),
                        track["attributes"].get("Genre"),
                        track["attributes"].get("Remixer"),
                        track["attributes"].get("Tonality"),
                        track["attributes"].get("Comments"),
                        track["attributes"].get("DateAdded"),
                        track["attributes"].get("BitRate"),
                        track["attributes"].get("SampleRate"),
                        track["attributes"].get("PlayCount"),
                        track["attributes"].get("LastPlayed"),
                        track["attributes"].get("Rating"),
                        json.dumps(track["attributes"], ensure_ascii=False, sort_keys=True),
                        json.dumps(track["candidates"], ensure_ascii=False, sort_keys=True),
                    )
                    for track in tracks
                ],
            )
            conn.executemany(
                """INSERT INTO rekordbox_position_marks(
                       import_id,rekordbox_track_id,ordinal,name,mark_type,start_seconds,
                       end_seconds,hotcue_number,red,green,blue,attributes_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        import_id,
                        track["track_id"],
                        ordinal,
                        mark.get("Name"),
                        mark.get("Type"),
                        mark.get("Start"),
                        mark.get("End"),
                        mark.get("Num"),
                        mark.get("Red"),
                        mark.get("Green"),
                        mark.get("Blue"),
                        json.dumps(mark, ensure_ascii=False, sort_keys=True),
                    )
                    for track in tracks
                    for ordinal, mark in enumerate(track["marks"])
                ],
            )
            conn.executemany(
                """INSERT INTO rekordbox_tempos(
                       import_id,rekordbox_track_id,ordinal,start_seconds,bpm,meter,beat,attributes_json
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                [
                    (
                        import_id,
                        track["track_id"],
                        ordinal,
                        tempo.get("Inizio"),
                        tempo.get("Bpm"),
                        tempo.get("Metro"),
                        tempo.get("Battito"),
                        json.dumps(tempo, ensure_ascii=False, sort_keys=True),
                    )
                    for track in tracks
                    for ordinal, tempo in enumerate(track["tempos"])
                ],
            )
            conn.executemany(
                """INSERT INTO rekordbox_playlist_nodes(
                       import_id,node_key,parent_node_key,node_path,name,node_type,key_type,
                       declared_count,position,attributes_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        import_id,
                        node["node_key"],
                        node["parent_node_key"],
                        node["node_path"],
                        node["name"],
                        node["node_type"],
                        node["key_type"],
                        node["declared_count"],
                        node["position"],
                        json.dumps(node["attributes"], ensure_ascii=False, sort_keys=True),
                    )
                    for node in parsed.playlist_nodes
                ],
            )
            conn.executemany(
                """INSERT INTO rekordbox_playlist_memberships(
                       import_id,node_key,ordinal,raw_key,reference_state,
                       rekordbox_track_id,candidates_json
                   ) VALUES(?,?,?,?,?,?,?)""",
                [
                    (
                        import_id,
                        item["node_key"],
                        item["ordinal"],
                        item["raw_key"],
                        item["reference_state"],
                        item["rekordbox_track_id"],
                        json.dumps(item["candidates"], ensure_ascii=False, sort_keys=True),
                    )
                    for item in memberships
                ],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        return {
            "import_id": import_id,
            "xml_path": os.fspath(xml_path),
            "library_root": bound_root[0],
            "product": parsed.product,
            "collection_tracks": len(tracks),
            "playlist_nodes": len(parsed.playlist_nodes),
            "playlists": playlist_count,
            "playlist_references": len(memberships),
            "unresolved_playlist_references": unresolved,
            "position_marks": mark_count,
            "tempos": tempo_count,
            "exact_path": counts["EXACT_PATH"],
            "moved_likely": counts["MOVED_LIKELY"],
            "ambiguous": counts["AMBIGUOUS"],
            "missing": counts["MISSING"],
            "completed_successfully": True,
        }
    finally:
        conn.close()


def status(db_path: Path) -> dict[str, Any]:
    conn = library_index.connect_database(db_path)
    try:
        _ensure_rekordbox_schema(conn)
        latest = conn.execute(
            "SELECT * FROM rekordbox_imports WHERE completed_successfully=1 "
            "ORDER BY import_id DESC LIMIT 1"
        ).fetchone()
        if latest is None:
            return {
                "initialized": True,
                "rekordbox_schema_version": REKORDBOX_SCHEMA_VERSION,
                "latest_completed_import": None,
            }
        return {
            "initialized": True,
            "rekordbox_schema_version": REKORDBOX_SCHEMA_VERSION,
            "latest_completed_import": dict(latest),
        }
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser(
        "inspect", help="validate and summarize a Rekordbox collection XML without a database"
    )
    inspect_parser.add_argument("--xml", type=Path, required=True)
    import_parser = subparsers.add_parser(
        "import", help="import a read-only Rekordbox collection XML snapshot"
    )
    import_parser.add_argument("--xml", type=Path, required=True)
    import_parser.add_argument("--db", type=Path, required=True)
    status_parser = subparsers.add_parser(
        "status", help="show the latest trusted Rekordbox XML import"
    )
    status_parser.add_argument("--db", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect_xml(args.xml)
        elif args.command == "import":
            result = import_rekordbox(args.xml, args.db)
        else:
            result = status(args.db)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
