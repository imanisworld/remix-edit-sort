"""Synthetic Serato-shaped fixtures: SQLite libraries and database V2 / crates.

Shaped after the real files (schema read from a COPY of the owner's
``master.sqlite`` / ``root.sqlite``, never from the live files): an ``asset``
table with a NOCASE unique index over ``portable_id``, a trigger that calls a
custom SQL function SQLite does not have (so any UPDATE that fires it fails),
play history, and a table that references assets by id.
"""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

MASTER_SCHEMA = """
CREATE TABLE location (id INTEGER PRIMARY KEY, path TEXT, uuid BLOB);
CREATE TABLE asset (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id INTEGER NOT NULL,
    location_id INTEGER NOT NULL,
    portable_id TEXT NOT NULL DEFAULT '',
    file_name TEXT DEFAULT NULL,
    name TEXT NOT NULL DEFAULT '',
    genre TEXT NOT NULL DEFAULT '',
    name_norm TEXT,
    FOREIGN KEY(location_id) REFERENCES location(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX asset__unique_database ON asset ( location_id, portable_id COLLATE NOCASE );
CREATE UNIQUE INDEX asset__unique_external_id ON asset ( location_id, external_id );
CREATE TRIGGER after_asset_update
AFTER UPDATE OF file_name, name, genre ON main.asset
BEGIN
    UPDATE asset SET name_norm = serato_str_norm( name ) WHERE asset.id = old.id;
END;
CREATE TABLE history_entry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    location_id INTEGER,
    portable_id TEXT NOT NULL DEFAULT ''
);
CREATE TABLE container_asset (id INTEGER PRIMARY KEY AUTOINCREMENT, asset_id INTEGER NOT NULL);
"""

ROOT_SCHEMA = """
CREATE TABLE asset (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    revision INTEGER NOT NULL,
    portable_id TEXT NOT NULL DEFAULT '',
    file_name TEXT DEFAULT NULL,
    genre TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX asset__unique_portable_id ON asset ( portable_id COLLATE NOCASE );
CREATE TABLE last_seen_dbv2_library (
    filename TEXT NOT NULL PRIMARY KEY, size INTEGER NOT NULL, md5_hash BLOB NOT NULL
);
"""


def make_master_sqlite(path: Path, assets: list[str], history: list[str] = (), *, wal: bool = True) -> Path:
    path = Path(path)
    connection = sqlite3.connect(str(path), isolation_level=None)
    if wal:
        connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript(MASTER_SCHEMA)
    connection.execute("INSERT INTO location (id, path) VALUES (1, NULL)")
    for number, portable_id in enumerate(assets, start=1):
        connection.execute(
            "INSERT INTO asset (external_id, location_id, portable_id, file_name, name, genre) "
            "VALUES (?, 1, ?, ?, ?, 'R&B')",
            (number, portable_id, portable_id.rsplit("/", 1)[-1], f"Track {number}"),
        )
        connection.execute("INSERT INTO container_asset (asset_id) VALUES (?)", (number,))
    for portable_id in history:
        connection.execute("INSERT INTO history_entry (location_id, portable_id) VALUES (1, ?)", (portable_id,))
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return path


def make_root_sqlite(path: Path, assets: list[str]) -> Path:
    path = Path(path)
    connection = sqlite3.connect(str(path), isolation_level=None)
    connection.executescript(ROOT_SCHEMA)
    for number, portable_id in enumerate(assets, start=1):
        connection.execute(
            "INSERT INTO asset (revision, portable_id, file_name, genre) VALUES (?, ?, ?, 'R&B')",
            (number, portable_id, portable_id.rsplit("/", 1)[-1]),
        )
    connection.execute("INSERT INTO last_seen_dbv2_library VALUES ('database V2', 1, x'00')")
    connection.close()
    return path


def tlv(tag: str, value: bytes) -> bytes:
    return tag.encode("ascii") + struct.pack(">I", len(value)) + value


def text(value: str) -> bytes:
    return value.encode("utf-16-be")


def otrk(fields: bytes) -> bytes:
    return tlv("otrk", fields)


def make_serato_dir(serato: Path, database_paths: list[str], crates: dict[str, list[str]]) -> None:
    (Path(serato) / "Subcrates").mkdir(parents=True, exist_ok=True)
    database = tlv("vrsn", text("2.0")) + b"".join(
        otrk(tlv("pfil", text(path)) + tlv("tsng", text("Song"))) for path in database_paths
    )
    (Path(serato) / "database V2").write_bytes(database)
    for name, paths in crates.items():
        body = tlv("vrsn", text("1.0")) + b"".join(otrk(tlv("ptrk", text(path))) for path in paths)
        (Path(serato) / "Subcrates" / f"{name}.crate").write_bytes(body)
