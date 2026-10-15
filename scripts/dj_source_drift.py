#!/usr/bin/env python3
"""Read-only source drift report for trusted Serato and Rekordbox imports.

Compares the bytes of Serato's database/crates and an exported Rekordbox XML
against their latest successful imports in library_index.py's SQLite database.
Does not read Rekordbox master.db, mutate files, refresh the index, or infer
track identities/play history. Re-import is a separate, deliberate operation.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import sys
from pathlib import Path
from urllib.parse import quote


def _hash_stable(path: Path) -> str:
    """Hash a regular file without following symlink swaps during the read."""
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"not a regular non-symlink source: {path}")
    before = path.stat(follow_symlinks=False)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        identity = lambda row: (
            row.st_size, row.st_mtime_ns, row.st_dev, row.st_ino
        )
        if not stat.S_ISREG(opened.st_mode) or identity(before) != identity(opened):
            raise ValueError(f"source changed before hashing: {path}")
        digest = hashlib.sha256()
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            while chunk := handle.read(4 * 1024 * 1024):
                digest.update(chunk)
            after_open = os.fstat(handle.fileno())
        after = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(after.st_mode) or (
            identity(opened) != identity(after_open)
            or identity(opened) != identity(after)
        ):
            raise ValueError(f"source changed during hashing: {path}")
        return digest.hexdigest()
    finally:
        if fd >= 0:
            os.close(fd)


def _valid_sha(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdefABCDEF" for char in value)
    )


def _validate_index(conn: sqlite3.Connection) -> dict:
    """Reject arbitrary or unbound SQLite files; never initialize a database."""
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta"))
        row = conn.execute(
            "SELECT scan_id, completed_at FROM scans "
            "WHERE completed_successfully=1 ORDER BY scan_id DESC LIMIT 1"
        ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError("not a trusted root-bound DJ library index") from exc
    if meta.get("schema_version") != "3":
        raise ValueError("unsupported DJ library index schema")
    root = meta.get("root_canonical")
    if not isinstance(root, str) or not Path(root).is_absolute():
        raise ValueError("DJ library index has no trusted absolute root")
    if row is None or not row["completed_at"]:
        raise ValueError("DJ library index has no completed source scan")
    return {
        "library_root": root, "completed_scan_id": int(row["scan_id"]),
        "completed_scan_at": row["completed_at"]
    }


def _latest(conn: sqlite3.Connection, table: str) -> sqlite3.Row | None:
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is None:
        return None
    return conn.execute(
        f"SELECT * FROM {table} WHERE completed_successfully=1 "
        "ORDER BY import_id DESC LIMIT 1"
    ).fetchone()


def _serato(root: Path | None, row: sqlite3.Row | None) -> dict:
    if row is None:
        return {
            "state": "NO_BASELINE", "reimport_required": False,
            "baseline_path": None,
            "inspected_path": None if root is None else str(root),
        }
    original_root = Path(row["serato_dir"])
    if not original_root.is_absolute() or not original_root.name:
        raise ValueError("invalid Serato baseline root")
    source_root = root or original_root
    if source_root.is_symlink() or not source_root.is_dir():
        return {
            "state": "SOURCE_UNAVAILABLE", "reimport_required": False,
            "baseline_path": str(original_root), "inspected_path": str(source_root),
        }
    crates_dir = source_root / "Subcrates"
    if crates_dir.is_symlink():
        raise ValueError("Serato Subcrates directory may not be a symlink")

    expected: dict[str, str] = {}
    manifest = json.loads(row["source_manifest_json"])
    if not isinstance(manifest, list) or not manifest:
        raise ValueError("invalid Serato source manifest")
    for item in manifest:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or not _valid_sha(item.get("sha256"))
            or type(item.get("size_bytes")) is not int
            or item["size_bytes"] < 0
        ):
            raise ValueError("invalid Serato source manifest entry")
        try:
            relative = Path(item["path"]).relative_to(original_root)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Serato manifest contains path outside imported root") from exc
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe Serato manifest relative path")
        name = relative.as_posix()
        if name != "database V2" and not (
            len(relative.parts) == 2 and relative.parts[0] == "Subcrates"
            and relative.suffix == ".crate" and not relative.name.startswith("._")
        ):
            raise ValueError("unexpected Serato baseline source path")
        if name in expected:
            raise ValueError("duplicate Serato source path in baseline")
        expected[name] = item["sha256"]
    if "database V2" not in expected:
        raise ValueError("Serato baseline has no database V2")

    present: dict[str, Path] = {}
    db_file = source_root / "database V2"
    if db_file.exists() or db_file.is_symlink():
        present["database V2"] = db_file
    if crates_dir.is_dir():
        for path in sorted(crates_dir.glob("*.crate")):
            if not path.name.startswith("._"):
                present[path.relative_to(source_root).as_posix()] = path

    modified, added, removed = [], [], []
    for name in sorted(set(expected) | set(present)):
        if name not in present:
            removed.append(name)
        elif name not in expected:
            # Newly added crates are a meaningful Serato change.
            _hash_stable(present[name])
            added.append(name)
        elif _hash_stable(present[name]) != expected[name]:
            modified.append(name)
    changed = bool(modified or added or removed)
    return {
        "state": "CHANGED" if changed else "UNCHANGED",
        "baseline_import_id": row["import_id"],
        "baseline_path": str(original_root),
        "inspected_path": str(source_root),
        "modified": modified, "added": added, "removed": removed,
        "reimport_required": changed,
    }


def _rekordbox(xml: Path | None, row: sqlite3.Row | None) -> dict:
    if row is None:
        return {
            "state": "NO_BASELINE", "reimport_required": False,
            "baseline_path": None,
            "inspected_path": None if xml is None else str(xml),
        }
    fingerprint = json.loads(row["source_fingerprint_json"])
    if (
        not isinstance(fingerprint, dict)
        or not _valid_sha(fingerprint.get("sha256"))
        or type(fingerprint.get("size_bytes")) is not int
        or fingerprint["size_bytes"] < 0
        or not isinstance(row["xml_path"], str)
        or not Path(row["xml_path"]).is_absolute()
    ):
        raise ValueError("invalid Rekordbox source fingerprint")
    source = xml or Path(row["xml_path"])
    if not source.exists() and not source.is_symlink():
        return {
            "state": "EXPORT_UNAVAILABLE", "baseline_import_id": row["import_id"],
            "baseline_path": row["xml_path"], "inspected_path": str(source),
            "reimport_required": False,
        }
    changed = _hash_stable(source) != fingerprint["sha256"]
    return {
        "state": "CHANGED" if changed else "UNCHANGED",
        "baseline_import_id": row["import_id"],
        "baseline_path": row["xml_path"],
        "inspected_path": str(source),
        "reimport_required": changed,
    }


def report(db: Path, *, serato_dir: Path | None = None, rekordbox_xml: Path | None = None) -> dict:
    if db.is_symlink() or not db.is_file():
        raise ValueError(f"index does not exist or is a symlink: {db}")
    # Open SQLite strictly read-only; do not create a new index or migrate schemas.
    uri = "file:" + quote(os.fspath(db.absolute()), safe="/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        index = _validate_index(conn)
        serato = _serato(serato_dir, _latest(conn, "serato_imports"))
        rekordbox = _rekordbox(rekordbox_xml, _latest(conn, "rekordbox_imports"))
    finally:
        conn.close()
    return {
        "schema_version": 1,
        "safety": "read-only",
        "index": index,
        "serato": serato,
        "rekordbox": rekordbox,
        "requires_attention": any(
            item["state"] != "UNCHANGED" for item in (serato, rekordbox)
        ),
        "scope": {
            "source_fingerprints": "sha256 of database V2, Subcrates/*.crate, and one exported XML",
            "per_track_metadata_deltas": "UNSUPPORTED",
            "live_rekordbox_collection": "NOT_OBSERVED",
            "playback_history": "NOT_OBSERVED",
        },
        "note": (
            "A changed export/source requires a new trusted import for per-track comparisons. "
            "Rekordbox XML is a point-in-time export, not the live collection. "
            "An UNCHANGED export cannot prove the current app is unchanged. "
            "No playback, folder move, or tag change is inferred."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--serato-dir", type=Path)
    parser.add_argument("--rekordbox-xml", type=Path)
    args = parser.parse_args(argv)
    try:
        result = report(
            args.db, serato_dir=args.serato_dir, rekordbox_xml=args.rekordbox_xml
        )
    except (OSError, ValueError, sqlite3.Error, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
