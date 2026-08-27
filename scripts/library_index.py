#!/usr/bin/env python3
"""Persistent read-only DJ library index with incremental SQLite scanning.

Reads supported audio files and writes only the local SQLite index. It never
writes metadata, moves library files, or touches Serato/Rekordbox data.
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
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from mutagen import File as MutagenFile
    from mutagen.id3 import ID3
except ImportError as exc:  # pragma: no cover
    raise SystemExit("mutagen is required") from exc

TOOL_VERSION = "1.0.4"
SCHEMA_VERSION = 3
SUPPORTED_EXTENSIONS = {".mp3", ".flac", ".aiff", ".aif", ".m4a", ".aac", ".wav"}

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scans (
    scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    root_display TEXT NOT NULL,
    root_canonical TEXT NOT NULL,
    config_json TEXT NOT NULL,
    file_count INTEGER NOT NULL DEFAULT 0,
    total_bytes INTEGER NOT NULL DEFAULT 0,
    new_count INTEGER NOT NULL DEFAULT 0,
    changed_count INTEGER NOT NULL DEFAULT 0,
    moved_count INTEGER NOT NULL DEFAULT 0,
    removed_count INTEGER NOT NULL DEFAULT 0,
    unchanged_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    completed_successfully INTEGER NOT NULL DEFAULT 0,
    tool_version TEXT NOT NULL,
    schema_version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS scan_errors (
    error_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id INTEGER NOT NULL,
    stage TEXT NOT NULL,
    path TEXT,
    error TEXT NOT NULL,
    FOREIGN KEY(scan_id) REFERENCES scans(scan_id)
);
CREATE INDEX IF NOT EXISTS idx_scan_errors_scan ON scan_errors(scan_id);
CREATE TABLE IF NOT EXISTS files (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_path TEXT NOT NULL UNIQUE,
    display_path TEXT NOT NULL,
    path_identity TEXT NOT NULL UNIQUE,
    extension TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    device INTEGER,
    inode INTEGER,
    sha256 TEXT NOT NULL,
    artist TEXT,
    title TEXT,
    album TEXT,
    album_artist TEXT,
    genre TEXT,
    grouping TEXT,
    year TEXT,
    bpm TEXT,
    musical_key TEXT,
    duration_seconds REAL,
    bitrate_bps INTEGER,
    sample_rate_hz INTEGER,
    channels INTEGER,
    bits_per_sample INTEGER,
    metadata_json TEXT NOT NULL,
    parse_error TEXT,
    first_seen_scan_id INTEGER NOT NULL,
    last_seen_scan_id INTEGER NOT NULL,
    FOREIGN KEY(first_seen_scan_id) REFERENCES scans(scan_id),
    FOREIGN KEY(last_seen_scan_id) REFERENCES scans(scan_id)
);
CREATE INDEX IF NOT EXISTS idx_files_identity ON files(device, inode, size_bytes, mtime_ns);
CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256);
CREATE TABLE IF NOT EXISTS scan_files (
    scan_id INTEGER NOT NULL,
    file_id INTEGER,
    canonical_path TEXT NOT NULL,
    display_path TEXT NOT NULL,
    state TEXT NOT NULL,
    prior_canonical_path TEXT,
    move_ambiguous INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    PRIMARY KEY(scan_id, canonical_path),
    FOREIGN KEY(scan_id) REFERENCES scans(scan_id)
);
"""

_METADATA_FIELDS = (
    "artist", "title", "album", "album_artist", "genre", "grouping", "year",
    "bpm", "musical_key", "duration_seconds", "bitrate_bps", "sample_rate_hz",
    "channels", "bits_per_sample",
)


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    display_path: str
    canonical_path: str
    path_identity: str
    extension: str
    size_bytes: int
    mtime_ns: int
    device: int | None
    inode: int | None


@dataclass
class ParsedMetadata:
    values: dict[str, Any]
    raw: dict[str, Any]
    error: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonicalize_path(path: Path) -> str:
    return unicodedata.normalize("NFC", os.path.realpath(os.fspath(path)))


def mac_path_identity(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def audio_extension(path: Path) -> str:
    name = path.name.casefold()
    for ext in SUPPORTED_EXTENSIONS:
        if name.endswith(ext):
            return ext
    return path.suffix.casefold()


def is_supported_audio(path: Path) -> bool:
    return audio_extension(path) in SUPPORTED_EXTENSIONS


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _scalar(value: Any) -> str | None:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _easy_take(tags: Any, *keys: str) -> str | None:
    if not tags:
        return None
    for key in keys:
        try:
            text = _scalar(tags.get(key))
        except Exception:
            continue
        if text is not None:
            return text
    return None


def _id3_take(tags: ID3, frame: str) -> str | None:
    try:
        frames = tags.getall(frame)
    except Exception:
        return None
    return _scalar(frames[0]) if frames else None


def parse_metadata(path: Path) -> ParsedMetadata:
    raw: dict[str, Any] = {}
    values = {key: None for key in _METADATA_FIELDS}
    errors: list[str] = []

    audio = None
    try:
        audio = MutagenFile(path)
        if audio is None:
            errors.append("mutagen could not identify file")
    except Exception as exc:
        errors.append(f"mutagen parse failed: {exc}")

    info = getattr(audio, "info", None)
    if info is not None:
        for source_name, target_name in (
            ("length", "duration_seconds"),
            ("bitrate", "bitrate_bps"),
            ("sample_rate", "sample_rate_hz"),
            ("channels", "channels"),
            ("bits_per_sample", "bits_per_sample"),
        ):
            value = getattr(info, source_name, None)
            if value not in (None, 0):
                values[target_name] = value

    try:
        id3 = ID3(path)
        values.update(
            artist=_id3_take(id3, "TPE1"),
            title=_id3_take(id3, "TIT2"),
            album=_id3_take(id3, "TALB"),
            album_artist=_id3_take(id3, "TPE2"),
            genre=_id3_take(id3, "TCON"),
            grouping=_id3_take(id3, "TIT1"),
            year=_id3_take(id3, "TDRC") or _id3_take(id3, "TYER"),
            bpm=_id3_take(id3, "TBPM"),
            musical_key=_id3_take(id3, "TKEY"),
        )
        raw["id3_version"] = list(id3.version)
    except Exception:
        try:
            easy = MutagenFile(path, easy=True)
            tags = getattr(easy, "tags", None)
            values["artist"] = values["artist"] or _easy_take(tags, "artist")
            values["title"] = values["title"] or _easy_take(tags, "title")
            values["album"] = values["album"] or _easy_take(tags, "album")
            values["album_artist"] = values["album_artist"] or _easy_take(tags, "albumartist", "album artist")
            values["genre"] = values["genre"] or _easy_take(tags, "genre")
            values["grouping"] = values["grouping"] or _easy_take(tags, "grouping", "contentgroup")
            values["year"] = values["year"] or _easy_take(tags, "date", "year")
            values["bpm"] = values["bpm"] or _easy_take(tags, "bpm")
            values["musical_key"] = values["musical_key"] or _easy_take(tags, "initialkey", "key")
        except Exception as exc:
            errors.append(f"metadata fallback failed: {exc}")

    return ParsedMetadata(values=values, raw=raw, error="; ".join(errors) or None)


def snapshot_path(path: Path) -> FileSnapshot:
    stat = path.stat(follow_symlinks=False)
    canonical = canonicalize_path(path)
    return FileSnapshot(
        path=path,
        display_path=os.fspath(path),
        canonical_path=canonical,
        path_identity=mac_path_identity(canonical),
        extension=audio_extension(path),
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        device=getattr(stat, "st_dev", None),
        inode=getattr(stat, "st_ino", None),
    )


def _snapshot_stable(before: FileSnapshot, after: FileSnapshot) -> bool:
    return (
        before.canonical_path == after.canonical_path
        and before.path_identity == after.path_identity
        and before.extension == after.extension
        and before.size_bytes == after.size_bytes
        and before.mtime_ns == after.mtime_ns
        and before.device == after.device
        and before.inode == after.inode
    )


def discover_audio(root: Path) -> tuple[list[FileSnapshot], list[str]]:
    root = root.resolve()
    root_real = canonicalize_path(root)
    found: list[FileSnapshot] = []
    errors: list[str] = []
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except Exception as exc:
            errors.append(f"{directory}: {exc}")
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    child_real = canonicalize_path(path)
                    if child_real == root_real or child_real.startswith(root_real + os.sep):
                        stack.append(path)
                elif entry.is_file(follow_symlinks=False) and is_supported_audio(path):
                    found.append(snapshot_path(path))
            except Exception as exc:
                errors.append(f"{entry.path}: {exc}")
    found.sort(key=lambda item: (item.path_identity, item.canonical_path))
    return found, errors


def connect_database(db_path: Path) -> sqlite3.Connection:
    db_path = db_path.expanduser().resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # Reject an existing incompatible index before executing any schema DDL.
    meta_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if meta_exists is not None:
        existing = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if existing is None or int(existing[0]) != SCHEMA_VERSION:
            value = existing[0] if existing is not None else "missing"
            conn.close()
            raise RuntimeError(
                f"unsupported library-index schema {value}; expected {SCHEMA_VERSION}. "
                "This milestone does not migrate pre-release databases."
            )

    conn.executescript(SCHEMA_SQL)
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
    conn.commit()
    return conn


def assert_db_outside_root(db_path: Path, root: Path) -> None:
    db_real = canonicalize_path(db_path.expanduser().absolute())
    root_real = canonicalize_path(root.resolve())
    if db_real == root_real or db_real.startswith(root_real + os.sep):
        raise ValueError("database must be stored outside the music root")


def _root_binding(root: Path) -> tuple[str, str]:
    canonical = canonicalize_path(root.resolve())
    return canonical, mac_path_identity(canonical)


def _validate_bound_root(conn: sqlite3.Connection, root: Path) -> tuple[str, str]:
    canonical, identity = _root_binding(root)
    existing = conn.execute("SELECT value FROM meta WHERE key='root_identity'").fetchone()
    if existing is not None and existing[0] != identity:
        stored = conn.execute("SELECT value FROM meta WHERE key='root_canonical'").fetchone()
        stored_root = stored[0] if stored is not None else "<unknown>"
        raise ValueError(
            f"database is bound to a different music root: {stored_root!r}; "
            f"requested {canonical!r}"
        )
    return canonical, identity


def _bind_root_on_success(conn: sqlite3.Connection, root: Path) -> None:
    canonical, identity = _validate_bound_root(conn, root)
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('root_identity', ?)", (identity,))
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('root_canonical', ?)", (canonical,))
    _validate_bound_root(conn, root)


def _row_identity_matches(row: sqlite3.Row, snap: FileSnapshot) -> bool:
    return (
        row["size_bytes"] == snap.size_bytes
        and row["mtime_ns"] == snap.mtime_ns
        and row["device"] == snap.device
        and row["inode"] == snap.inode
        and row["path_identity"] == snap.path_identity
    )


def _insert_scan(conn: sqlite3.Connection, root: Path) -> int:
    config = {"supported_extensions": sorted(SUPPORTED_EXTENSIONS), "follow_symlinks": False}
    cur = conn.execute(
        """INSERT INTO scans(started_at, root_display, root_canonical, config_json, tool_version, schema_version)
           VALUES(?,?,?,?,?,?)""",
        (utc_now(), os.fspath(root), canonicalize_path(root), json.dumps(config, sort_keys=True), TOOL_VERSION, SCHEMA_VERSION),
    )
    return int(cur.lastrowid)


def _record_scan_errors(conn: sqlite3.Connection, scan_id: int, stage: str, errors: list[str]) -> None:
    for message in errors:
        path, sep, detail = message.partition(": ")
        conn.execute(
            "INSERT INTO scan_errors(scan_id,stage,path,error) VALUES(?,?,?,?)",
            (scan_id, stage, path if sep else None, detail if sep else message),
        )
    conn.execute("UPDATE scans SET error_count=? WHERE scan_id=?", (len(errors), scan_id))
    conn.commit()


def _existing_files(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {row["path_identity"]: row for row in conn.execute("SELECT * FROM files")}


def _metadata_from_row(row: sqlite3.Row) -> ParsedMetadata:
    return ParsedMetadata(
        values={key: row[key] for key in _METADATA_FIELDS},
        raw=json.loads(row["metadata_json"] or "{}"),
        error=row["parse_error"],
    )


def _write_file(
    conn: sqlite3.Connection,
    scan_id: int,
    snap: FileSnapshot,
    sha256: str,
    meta: ParsedMetadata,
    prior: sqlite3.Row | None,
) -> int:
    vals = meta.values
    payload = (
        snap.canonical_path, snap.display_path, snap.path_identity, snap.extension,
        snap.size_bytes, snap.mtime_ns, snap.device, snap.inode, sha256,
        vals["artist"], vals["title"], vals["album"], vals["album_artist"],
        vals["genre"], vals["grouping"], vals["year"], vals["bpm"], vals["musical_key"],
        vals["duration_seconds"], vals["bitrate_bps"], vals["sample_rate_hz"],
        vals["channels"], vals["bits_per_sample"],
        json.dumps(meta.raw, ensure_ascii=False, sort_keys=True), meta.error,
    )
    if prior is None:
        cur = conn.execute(
            """INSERT INTO files(canonical_path,display_path,path_identity,extension,size_bytes,mtime_ns,device,inode,
               sha256,artist,title,album,album_artist,genre,grouping,year,bpm,musical_key,duration_seconds,bitrate_bps,
               sample_rate_hz,channels,bits_per_sample,metadata_json,parse_error,first_seen_scan_id,last_seen_scan_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            payload + (scan_id, scan_id),
        )
        return int(cur.lastrowid)
    conn.execute(
        """UPDATE files SET canonical_path=?,display_path=?,path_identity=?,extension=?,size_bytes=?,mtime_ns=?,device=?,inode=?,
           sha256=?,artist=?,title=?,album=?,album_artist=?,genre=?,grouping=?,year=?,bpm=?,musical_key=?,duration_seconds=?,
           bitrate_bps=?,sample_rate_hz=?,channels=?,bits_per_sample=?,metadata_json=?,parse_error=?,last_seen_scan_id=?
           WHERE file_id=?""",
        payload + (scan_id, prior["file_id"]),
    )
    return int(prior["file_id"])


def _assert_unique_path_identities(discovered: list[FileSnapshot]) -> None:
    seen: dict[str, str] = {}
    for snap in discovered:
        prior = seen.get(snap.path_identity)
        if prior is not None and prior != snap.canonical_path:
            raise RuntimeError(
                "case/Unicode path identity collision: "
                f"{prior!r} and {snap.canonical_path!r} resolve to one macOS identity"
            )
        seen[snap.path_identity] = snap.canonical_path


def scan(root: Path, db_path: Path) -> dict[str, Any]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    assert_db_outside_root(db_path, root)
    conn = connect_database(db_path)
    try:
        _validate_bound_root(conn, root)
        scan_id = _insert_scan(conn, root)
        conn.commit()
        previous = _existing_files(conn)
        discovered, discovery_errors = discover_audio(root)
        if discovery_errors:
            _record_scan_errors(conn, scan_id, "discovery", discovery_errors)
            raise RuntimeError(
                f"filesystem discovery incomplete ({len(discovery_errors)} error(s)); "
                "trusted index state was preserved"
            )
        _assert_unique_path_identities(discovered)

        current_identities = {snap.path_identity for snap in discovered}
        removed_rows = [row for identity, row in previous.items() if identity not in current_identities]
        removed_by_hash: dict[str, list[sqlite3.Row]] = {}
        for row in removed_rows:
            removed_by_hash.setdefault(row["sha256"], []).append(row)

        prepared: dict[str, tuple[FileSnapshot, sqlite3.Row | None, str, ParsedMetadata, bool]] = {}
        new_by_hash: dict[str, list[FileSnapshot]] = {}
        error_count = 0
        for snap in discovered:
            prior = previous.get(snap.path_identity)
            unchanged = prior is not None and _row_identity_matches(prior, snap)
            if unchanged:
                sha = prior["sha256"]
                meta = _metadata_from_row(prior)
            else:
                sha = sha256_file(snap.path)
                meta = parse_metadata(snap.path)
                if meta.error:
                    error_count += 1
            prepared[snap.path_identity] = (snap, prior, sha, meta, unchanged)
            if prior is None:
                new_by_hash.setdefault(sha, []).append(snap)

        for snap in discovered:
            try:
                after = snapshot_path(snap.path)
            except Exception as exc:
                _record_scan_errors(conn, scan_id, "stability", [f"{snap.display_path}: {exc}"])
                raise RuntimeError("file disappeared or became unreadable during scan; trusted index state was preserved") from exc
            if not _snapshot_stable(snap, after):
                _record_scan_errors(
                    conn,
                    scan_id,
                    "stability",
                    [f"{snap.display_path}: file changed during scan"],
                )
                raise RuntimeError("file changed during scan; trusted index state was preserved")

        counts = {name: 0 for name in ("NEW", "CHANGED", "MOVED", "REMOVED", "UNCHANGED")}
        total_bytes = sum(snap.size_bytes for snap in discovered)
        consumed_removed_ids: set[int] = set()

        conn.execute("BEGIN IMMEDIATE")
        try:
            for snap in discovered:
                snap, prior, sha, meta, unchanged = prepared[snap.path_identity]
                prior_path = None
                ambiguous = 0
                if unchanged:
                    state = "UNCHANGED"
                    file_id = int(prior["file_id"])
                    conn.execute(
                        "UPDATE files SET last_seen_scan_id=?,display_path=?,canonical_path=? WHERE file_id=?",
                        (scan_id, snap.display_path, snap.canonical_path, file_id),
                    )
                elif prior is not None:
                    state = "CHANGED"
                    file_id = _write_file(conn, scan_id, snap, sha, meta, prior)
                else:
                    old_candidates = removed_by_hash.get(sha, [])
                    new_candidates = new_by_hash.get(sha, [])
                    if len(old_candidates) == 1 and len(new_candidates) == 1:
                        moved_from = old_candidates[0]
                        state = "MOVED"
                        prior_path = moved_from["canonical_path"]
                        file_id = _write_file(conn, scan_id, snap, sha, meta, moved_from)
                        consumed_removed_ids.add(int(moved_from["file_id"]))
                    else:
                        state = "NEW"
                        ambiguous = int(bool(old_candidates) and (len(old_candidates) != 1 or len(new_candidates) != 1))
                        file_id = _write_file(conn, scan_id, snap, sha, meta, None)

                counts[state] += 1
                conn.execute(
                    """INSERT INTO scan_files(scan_id,file_id,canonical_path,display_path,state,prior_canonical_path,move_ambiguous,error)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (scan_id, file_id, snap.canonical_path, snap.display_path, state, prior_path, ambiguous, meta.error),
                )

            for row in removed_rows:
                if int(row["file_id"]) in consumed_removed_ids:
                    continue
                counts["REMOVED"] += 1
                conn.execute(
                    "INSERT INTO scan_files(scan_id,file_id,canonical_path,display_path,state,error) VALUES(?,?,?,?,?,NULL)",
                    (scan_id, row["file_id"], row["canonical_path"], row["display_path"], "REMOVED"),
                )
                conn.execute("DELETE FROM files WHERE file_id=?", (row["file_id"],))

            _bind_root_on_success(conn, root)
            conn.execute(
                """UPDATE scans SET completed_at=?,file_count=?,total_bytes=?,new_count=?,changed_count=?,moved_count=?,
                   removed_count=?,unchanged_count=?,error_count=?,completed_successfully=1 WHERE scan_id=?""",
                (
                    utc_now(), len(discovered), total_bytes, counts["NEW"], counts["CHANGED"], counts["MOVED"],
                    counts["REMOVED"], counts["UNCHANGED"], error_count, scan_id,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        return {
            "scan_id": scan_id,
            "root": os.fspath(root),
            "files": len(discovered),
            "bytes": total_bytes,
            "new": counts["NEW"],
            "changed": counts["CHANGED"],
            "moved": counts["MOVED"],
            "removed": counts["REMOVED"],
            "unchanged": counts["UNCHANGED"],
            "errors": error_count,
            "completed_successfully": True,
        }
    finally:
        conn.close()


def status(db_path: Path) -> dict[str, Any]:
    conn = connect_database(db_path)
    try:
        bound = conn.execute("SELECT value FROM meta WHERE key='root_canonical'").fetchone()
        bound_root = bound[0] if bound is not None else None
        latest = conn.execute(
            "SELECT * FROM scans WHERE completed_successfully=1 ORDER BY scan_id DESC LIMIT 1"
        ).fetchone()
        if latest is None:
            return {
                "initialized": True,
                "schema_version": SCHEMA_VERSION,
                "tool_version": TOOL_VERSION,
                "bound_root": bound_root,
                "latest_completed_scan": None,
                "current_files": 0,
            }
        return {
            "initialized": True,
            "schema_version": SCHEMA_VERSION,
            "tool_version": TOOL_VERSION,
            "bound_root": bound_root,
            "current_files": conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            "latest_completed_scan": dict(latest),
        }
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scan_p = sub.add_parser("scan", help="scan a music root into the persistent index")
    scan_p.add_argument("--root", type=Path, required=True)
    scan_p.add_argument("--db", type=Path, required=True)
    status_p = sub.add_parser("status", help="show latest trusted completed scan")
    status_p.add_argument("--db", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = scan(args.root, args.db) if args.command == "scan" else status(args.db)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
