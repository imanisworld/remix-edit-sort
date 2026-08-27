#!/usr/bin/env python3
"""Import read-only Serato relationships into the persistent library index.

Reads ``_Serato_/database V2`` and ``_Serato_/Subcrates/*.crate`` plus the
existing SQLite library index.  It writes only additive relationship tables in
that SQLite database.  It never writes Serato, Rekordbox, or audio files.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import argparse
import hashlib
import json
import os
import sqlite3
import struct
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import library_index


TOOL_VERSION = "1.0.0"
SERATO_SCHEMA_VERSION = 1

SERATO_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS serato_imports (
    import_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    serato_dir TEXT NOT NULL,
    source_manifest_json TEXT NOT NULL,
    database_track_count INTEGER NOT NULL,
    crate_count INTEGER NOT NULL,
    crate_reference_count INTEGER NOT NULL,
    unique_path_count INTEGER NOT NULL,
    exact_path_count INTEGER NOT NULL,
    moved_likely_count INTEGER NOT NULL,
    ambiguous_count INTEGER NOT NULL,
    missing_count INTEGER NOT NULL,
    completed_successfully INTEGER NOT NULL,
    tool_version TEXT NOT NULL,
    schema_version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS serato_tracks (
    import_id INTEGER NOT NULL,
    serato_path TEXT NOT NULL,
    absolute_path TEXT NOT NULL,
    match_state TEXT NOT NULL CHECK(match_state IN ('EXACT_PATH','MOVED_LIKELY','AMBIGUOUS','MISSING')),
    file_id INTEGER,
    matched_display_path TEXT,
    in_database INTEGER NOT NULL,
    title TEXT,
    artist TEXT,
    genre TEXT,
    candidates_json TEXT NOT NULL,
    PRIMARY KEY(import_id, serato_path),
    FOREIGN KEY(import_id) REFERENCES serato_imports(import_id)
);
CREATE INDEX IF NOT EXISTS idx_serato_tracks_import_state
    ON serato_tracks(import_id, match_state);
CREATE INDEX IF NOT EXISTS idx_serato_tracks_file
    ON serato_tracks(file_id);
CREATE TABLE IF NOT EXISTS serato_crate_memberships (
    import_id INTEGER NOT NULL,
    crate_name TEXT NOT NULL,
    serato_path TEXT NOT NULL,
    PRIMARY KEY(import_id, crate_name, serato_path),
    FOREIGN KEY(import_id) REFERENCES serato_imports(import_id)
);
CREATE INDEX IF NOT EXISTS idx_serato_crates_path
    ON serato_crate_memberships(import_id, serato_path);
"""


@dataclass(frozen=True)
class SourceFingerprint:
    path: str
    size_bytes: int
    mtime_ns: int
    sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path: Path) -> SourceFingerprint:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError(f"Serato source must be a regular, non-symlink file: {path}")
    before = path.stat(follow_symlinks=False)
    digest = _sha256(path)
    after = path.stat(follow_symlinks=False)
    before_identity = (before.st_size, before.st_mtime_ns, before.st_dev, before.st_ino)
    after_identity = (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino)
    if before_identity != after_identity:
        raise RuntimeError(f"Serato source changed while being fingerprinted: {path}")
    return SourceFingerprint(
        path=os.fspath(path),
        size_bytes=after.st_size,
        mtime_ns=after.st_mtime_ns,
        sha256=digest,
    )


def _source_paths(serato_dir: Path) -> list[Path]:
    database = serato_dir / "database V2"
    if not database.is_file():
        raise RuntimeError(f"no database V2 found at {database}")
    paths = [database]
    crates_dir = serato_dir / "Subcrates"
    if crates_dir.is_dir():
        paths.extend(
            path
            for path in sorted(crates_dir.glob("*.crate"))
            if not path.name.startswith("._")
        )
    return paths


def _source_manifest(serato_dir: Path) -> list[SourceFingerprint]:
    return [_fingerprint(path) for path in _source_paths(serato_dir)]


def _parse_tlv_strict(data: bytes, *, context: str) -> list[tuple[str, bytes]]:
    records: list[tuple[str, bytes]] = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < 8:
            raise RuntimeError(f"truncated TLV header in {context} at byte {offset}")
        raw_tag = data[offset:offset + 4]
        try:
            tag = raw_tag.decode("ascii")
        except UnicodeDecodeError as exc:
            raise RuntimeError(f"non-ASCII TLV tag in {context} at byte {offset}") from exc
        length = struct.unpack(">I", data[offset + 4:offset + 8])[0]
        end = offset + 8 + length
        if end > len(data):
            raise RuntimeError(
                f"truncated TLV value in {context} at byte {offset}: "
                f"declared {length}, only {len(data) - offset - 8} available"
            )
        records.append((tag, data[offset + 8:end]))
        offset = end
    return records


def _decode_path(value: bytes, *, context: str) -> str:
    try:
        decoded = value.decode("utf-16-be")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"invalid UTF-16BE path in {context}") from exc
    if not decoded:
        raise RuntimeError(f"empty Serato path in {context}")
    return decoded


def _decode_optional(value: bytes | None) -> str | None:
    if value is None:
        return None
    return value.decode("utf-16-be", errors="replace")


def _parse_database(path: Path) -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    for index, (tag, value) in enumerate(
        _parse_tlv_strict(path.read_bytes(), context=os.fspath(path))
    ):
        if tag != "otrk":
            continue
        fields = dict(_parse_tlv_strict(value, context=f"{path}:otrk[{index}]"))
        raw_path = fields.get("pfil")
        if raw_path is None:
            continue
        tracks.append(
            {
                "path": _decode_path(raw_path, context=f"{path}:otrk[{index}].pfil"),
                "title": _decode_optional(fields.get("tsng")),
                "artist": _decode_optional(fields.get("tart")),
                "genre": _decode_optional(fields.get("tgen")),
            }
        )
    return tracks


def _parse_crate(path: Path) -> list[str]:
    paths: list[str] = []
    for index, (tag, value) in enumerate(
        _parse_tlv_strict(path.read_bytes(), context=os.fspath(path))
    ):
        if tag != "otrk":
            continue
        for inner_tag, inner_value in _parse_tlv_strict(
            value, context=f"{path}:otrk[{index}]"
        ):
            if inner_tag == "ptrk":
                paths.append(
                    _decode_path(inner_value, context=f"{path}:otrk[{index}].ptrk")
                )
    return paths


def _manifest_json(manifest: list[SourceFingerprint]) -> str:
    return json.dumps(
        [fingerprint.__dict__ for fingerprint in manifest],
        ensure_ascii=False,
        sort_keys=True,
    )


def _ensure_serato_schema(conn: sqlite3.Connection) -> None:
    existing = conn.execute(
        "SELECT value FROM meta WHERE key='serato_schema_version'"
    ).fetchone()
    serato_tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'serato_%'"
        )
    }
    if existing is not None:
        if int(existing[0]) != SERATO_SCHEMA_VERSION:
            raise RuntimeError(
                f"unsupported Serato index schema {existing[0]}; "
                f"expected {SERATO_SCHEMA_VERSION}"
            )
    elif serato_tables:
        raise RuntimeError(
            "Serato relationship tables exist without a recognized schema version"
        )
    conn.executescript(SERATO_SCHEMA_SQL)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key,value) VALUES('serato_schema_version',?)",
        (str(SERATO_SCHEMA_VERSION),),
    )
    conn.commit()


def _absolute_serato_path(serato_path: str) -> str:
    return "/" + serato_path.lstrip("/")


def _filename_identity(path: str) -> str:
    return unicodedata.normalize("NFC", Path(path).name).casefold()


def _assert_db_outside_serato(db_path: Path, serato_dir: Path) -> None:
    db_real = library_index.canonicalize_path(db_path.expanduser().absolute())
    serato_real = library_index.canonicalize_path(serato_dir.resolve())
    if db_real == serato_real or db_real.startswith(serato_real + os.sep):
        raise ValueError("database must be stored outside the Serato directory")


def import_serato(serato_dir: Path, db_path: Path) -> dict[str, Any]:
    started_at = library_index.utc_now()
    serato_dir = serato_dir.expanduser().resolve()
    if not serato_dir.is_dir():
        raise ValueError(f"not a directory: {serato_dir}")
    _assert_db_outside_serato(db_path, serato_dir)

    conn = library_index.connect_database(db_path)
    try:
        bound_root = conn.execute(
            "SELECT value FROM meta WHERE key='root_canonical'"
        ).fetchone()
        if bound_root is None:
            raise RuntimeError("library index has no completed root-bound scan")
        _ensure_serato_schema(conn)

        conn.execute("BEGIN IMMEDIATE")
        try:
            before_manifest = _source_manifest(serato_dir)
            database_path = serato_dir / "database V2"
            db_tracks = _parse_database(database_path)
            crate_files = [Path(item.path) for item in before_manifest[1:]]
            crate_paths = {path.stem: _parse_crate(path) for path in crate_files}
            after_manifest = _source_manifest(serato_dir)
            if before_manifest != after_manifest:
                raise RuntimeError(
                    "Serato database or crates changed during import; prior trusted state was preserved"
                )

            indexed_rows = list(conn.execute("SELECT * FROM files"))
            by_identity = {row["path_identity"]: row for row in indexed_rows}
            by_filename: dict[str, list[sqlite3.Row]] = {}
            for row in indexed_rows:
                by_filename.setdefault(
                    _filename_identity(row["canonical_path"]), []
                ).append(row)

            by_path: dict[str, dict[str, Any]] = {}
            for track in db_tracks:
                by_path[track["path"]] = {
                    **track,
                    "in_database": True,
                    "crates": [],
                }
            memberships: set[tuple[str, str]] = set()
            for crate_name, paths in crate_paths.items():
                for serato_path in paths:
                    memberships.add((crate_name, serato_path))
                    entry = by_path.setdefault(
                        serato_path,
                        {
                            "path": serato_path,
                            "title": None,
                            "artist": None,
                            "genre": None,
                            "in_database": False,
                            "crates": [],
                        },
                    )
                    entry["crates"].append(crate_name)

            prepared: list[dict[str, Any]] = []
            counts = {state: 0 for state in ("EXACT_PATH", "MOVED_LIKELY", "AMBIGUOUS", "MISSING")}
            for serato_path, entry in sorted(by_path.items()):
                absolute_path = _absolute_serato_path(serato_path)
                canonical = library_index.canonicalize_path(Path(absolute_path))
                exact = by_identity.get(library_index.mac_path_identity(canonical))
                candidates: list[sqlite3.Row] = []
                matched = None
                if exact is not None:
                    state = "EXACT_PATH"
                    matched = exact
                else:
                    candidates = by_filename.get(_filename_identity(absolute_path), [])
                    if len(candidates) == 1:
                        state = "MOVED_LIKELY"
                    elif len(candidates) > 1:
                        state = "AMBIGUOUS"
                    else:
                        state = "MISSING"
                counts[state] += 1
                candidate_payload = [
                    {"file_id": int(row["file_id"]), "display_path": row["display_path"]}
                    for row in candidates
                ]
                prepared.append(
                    {
                        "serato_path": serato_path,
                        "absolute_path": absolute_path,
                        "match_state": state,
                        "file_id": int(matched["file_id"]) if matched is not None else None,
                        "matched_display_path": matched["display_path"] if matched is not None else None,
                        "in_database": int(entry["in_database"]),
                        "title": entry["title"],
                        "artist": entry["artist"],
                        "genre": entry["genre"],
                        "candidates_json": json.dumps(candidate_payload, ensure_ascii=False, sort_keys=True),
                    }
                )

            cursor = conn.execute(
                """INSERT INTO serato_imports(
                       started_at,completed_at,serato_dir,source_manifest_json,
                       database_track_count,crate_count,crate_reference_count,unique_path_count,
                       exact_path_count,moved_likely_count,ambiguous_count,missing_count,
                       completed_successfully,tool_version,schema_version
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
                (
                    started_at, library_index.utc_now(), os.fspath(serato_dir),
                    _manifest_json(after_manifest), len(db_tracks), len(crate_paths),
                    sum(len(paths) for paths in crate_paths.values()), len(by_path),
                    counts["EXACT_PATH"], counts["MOVED_LIKELY"], counts["AMBIGUOUS"],
                    counts["MISSING"], TOOL_VERSION, SERATO_SCHEMA_VERSION,
                ),
            )
            import_id = int(cursor.lastrowid)
            conn.executemany(
                """INSERT INTO serato_tracks(
                       import_id,serato_path,absolute_path,match_state,file_id,
                       matched_display_path,in_database,title,artist,genre,candidates_json
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        import_id, item["serato_path"], item["absolute_path"],
                        item["match_state"], item["file_id"], item["matched_display_path"],
                        item["in_database"], item["title"], item["artist"], item["genre"],
                        item["candidates_json"],
                    )
                    for item in prepared
                ],
            )
            conn.executemany(
                "INSERT INTO serato_crate_memberships(import_id,crate_name,serato_path) VALUES(?,?,?)",
                [(import_id, crate_name, serato_path) for crate_name, serato_path in sorted(memberships)],
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        return {
            "import_id": import_id,
            "serato_dir": os.fspath(serato_dir),
            "library_root": bound_root[0],
            "database_tracks": len(db_tracks),
            "crates": len(crate_paths),
            "crate_references": sum(len(paths) for paths in crate_paths.values()),
            "unique_paths": len(by_path),
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
        _ensure_serato_schema(conn)
        latest = conn.execute(
            "SELECT * FROM serato_imports WHERE completed_successfully=1 ORDER BY import_id DESC LIMIT 1"
        ).fetchone()
        if latest is None:
            return {
                "initialized": True,
                "serato_schema_version": SERATO_SCHEMA_VERSION,
                "latest_completed_import": None,
            }
        return {
            "initialized": True,
            "serato_schema_version": SERATO_SCHEMA_VERSION,
            "latest_completed_import": dict(latest),
        }
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    import_parser = subparsers.add_parser(
        "import", help="import read-only Serato paths and crate relationships"
    )
    import_parser.add_argument("--serato-dir", type=Path, required=True)
    import_parser.add_argument("--db", type=Path, required=True)
    status_parser = subparsers.add_parser("status", help="show latest trusted Serato import")
    status_parser.add_argument("--db", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "import":
            result = import_serato(args.serato_dir, args.db)
        else:
            result = status(args.db)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
