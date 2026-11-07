#!/usr/bin/env python3
"""Read-only occurrence-level parity audit for Serato and file metadata.

The audit relates every crate ``ptrk`` occurrence to every exact and logical
alias match in ``database V2``, both Serato SQLite ``asset`` tables, and the
audio file's Artist/Title/BPM/bitrate. Duplicate records are reported as
separate ordered rows; this module never selects a winner or mutates a source.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import argparse
import hashlib
import json
import re
import sqlite3
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

try:
    from mutagen import File as MutagenFile
except ImportError:  # pragma: no cover - reported when the real reader is used
    MutagenFile = None

import check_serato_crate_paths


FILE_FIELDS = ("artist", "title", "bpm", "bitrate_bps")
SQLITE_FIELD_COLUMNS = {
    "artist": "artist",
    "title": "name",
    "bpm": "bpm",
    "bitrate_bps": "file_bit_rate",
}
DATABASE_FIELD_NAMES = {
    "artist": "artist",
    "title": "title",
    "bpm": "bpm",
    "bitrate_bps": "bitrate",
}


def _first(tags: Any, key: str) -> str | None:
    values = tags.get(key, []) if tags is not None else []
    if not isinstance(values, (list, tuple)):
        values = [values]
    for value in values:
        text = str(value).strip()
        if text:
            return text
    return None


def read_file_metadata(path: Path) -> dict[str, Any]:
    """Read only the parity fields; no fallback writes or bitrate synthesis."""
    if MutagenFile is None:
        raise RuntimeError("mutagen is required to read file metadata")
    audio = MutagenFile(path, easy=True)
    if audio is None:
        raise ValueError("mutagen could not identify audio format")
    info = getattr(audio, "info", None)
    bitrate = getattr(info, "bitrate", None) if info is not None else None
    return {
        "artist": _first(audio.tags, "artist"),
        "title": _first(audio.tags, "title"),
        "bpm": _first(audio.tags, "bpm"),
        "bitrate_bps": int(round(float(bitrate))) if bitrate else None,
    }


def _sqlite_uri(path: Path) -> str:
    return f"file:{quote(str(Path(path).resolve()), safe='/')}?mode=ro&immutable=1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _audit_source_state(
    serato_dir: Path,
    sqlite_paths: list[Path],
) -> dict[str, dict[str, Any] | None]:
    paths = [serato_dir / "database V2"]
    for folder, pattern in (
        ("Subcrates", "*.crate"),
        ("SmartCrates", "*.scrate"),
    ):
        root = serato_dir / folder
        if root.is_dir():
            paths.extend(sorted(root.rglob(pattern)))
    for sqlite_path in sqlite_paths:
        sqlite_path = Path(sqlite_path).resolve()
        paths.append(sqlite_path)
        paths.extend(
            sqlite_path.with_name(sqlite_path.name + suffix)
            for suffix in ("-wal", "-shm", "-journal")
        )
    state = {}
    for path in paths:
        key = str(path)
        state[key] = (
            {"bytes": path.stat().st_size, "sha256": _sha256(path)}
            if path.is_file()
            else None
        )
    return state


def read_sqlite_assets(path: Path) -> list[dict[str, Any]]:
    """Read ordered asset rows via an immutable, query-only connection."""
    path = Path(path).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    for suffix, label in (
        ("-wal", "WAL"),
        ("-shm", "shared-memory"),
        ("-journal", "rollback-journal"),
    ):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.is_file() and sidecar.stat().st_size:
            raise RuntimeError(
                f"{path}: nonempty {label} sidecar prevents an immutable audit: "
                f"{sidecar}"
            )
    connection = sqlite3.connect(_sqlite_uri(path), uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(asset)").fetchall()
        }
        if "portable_id" not in columns:
            raise RuntimeError(f"{path}: asset.portable_id is missing")
        selected = ["rowid AS _rowid"]
        for column in ("id", "portable_id", *SQLITE_FIELD_COLUMNS.values()):
            if column in columns:
                selected.append(f'"{column}"')
        rows = connection.execute(
            f"SELECT {', '.join(selected)} FROM asset ORDER BY rowid"
        ).fetchall()
        available_fields = {
            field
            for field, column in SQLITE_FIELD_COLUMNS.items()
            if column in columns
        }
        output = []
        for row in rows:
            values = dict(row)
            portable_id = values["portable_id"]
            valid_portable_id = isinstance(portable_id, str) and bool(portable_id)
            logical = (
                check_serato_crate_paths.logical_path(portable_id)
                if valid_portable_id
                else None
            )
            output.append(
                {
                    "id": values.get("id", values["_rowid"]),
                    "rowid": values["_rowid"],
                    "path": portable_id if valid_portable_id else None,
                    "raw_portable_id": repr(portable_id),
                    "path_error": (
                        None
                        if valid_portable_id
                        else "portable_id is null, empty, or not text"
                    ),
                    "logical_path": logical,
                    "is_canonical_path": (
                        valid_portable_id and portable_id == logical
                    ),
                    "artist": values.get(SQLITE_FIELD_COLUMNS["artist"]),
                    "title": values.get(SQLITE_FIELD_COLUMNS["title"]),
                    "bpm": values.get(SQLITE_FIELD_COLUMNS["bpm"]),
                    "bitrate_bps": values.get(
                        SQLITE_FIELD_COLUMNS["bitrate_bps"]
                    ),
                    "available_metadata_fields": sorted(available_fields),
                }
            )
        return output
    finally:
        connection.close()


def _parse_tlv_strict(data: bytes, context: str) -> list[tuple[str, bytes]]:
    records = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < 8:
            raise RuntimeError(
                f"{context}: truncated TLV header at offset {offset}"
            )
        raw_tag = data[offset:offset + 4]
        try:
            tag = raw_tag.decode("ascii")
        except UnicodeDecodeError as exc:
            raise RuntimeError(
                f"{context}: non-ASCII TLV tag at offset {offset}"
            ) from exc
        length = struct.unpack(">I", data[offset + 4:offset + 8])[0]
        end = offset + 8 + length
        if end > len(data):
            raise RuntimeError(
                f"{context}: truncated TLV value for {tag!r} at offset "
                f"{offset}"
            )
        records.append((tag, data[offset + 8:end]))
        offset = end
    return records


def _validate_serato_source(path: Path) -> None:
    data = Path(path).read_bytes()
    for outer_index, (tag, value) in enumerate(
        _parse_tlv_strict(data, str(path))
    ):
        if tag == "otrk":
            _parse_tlv_strict(value, f"{path}:otrk[{outer_index}]")


def _normalized_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def _normalized_value(field: str, value: Any) -> str | float | None:
    if value in (None, ""):
        return None
    if field == "bpm":
        return _normalized_number(value)
    if field == "bitrate_bps":
        number = _normalized_number(value)
        if number is None:
            return None
        if "kbps" in str(value).casefold() or number < 10_000:
            number *= 1000
        return number
    return str(value).strip()


def _values_match(field: str, expected: Any, actual: Any) -> bool:
    left = _normalized_value(field, expected)
    right = _normalized_value(field, actual)
    if isinstance(left, float) and isinstance(right, float):
        return abs(left - right) < 0.01
    return left == right


def _comparison_issues(
    *,
    source: str,
    identity: dict[str, Any],
    record: dict[str, Any],
    available_fields: set[str],
    file_metadata: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if file_metadata is None:
        return []
    issues = []
    for field in FILE_FIELDS:
        if field not in available_fields:
            continue
        expected = file_metadata.get(field)
        if _normalized_value(field, expected) is None:
            continue
        actual = record.get(field)
        if _normalized_value(field, actual) is None:
            issues.append(
                {
                    "code": f"{source}_{field.removesuffix('_bps')}_missing",
                    "field": field,
                    "source": source,
                    "identity": identity,
                    "file_value": expected,
                    "record_value": actual,
                }
            )
        elif not _values_match(field, expected, actual):
            issues.append(
                {
                    "code": f"{source}_{field.removesuffix('_bps')}_mismatch",
                    "field": field,
                    "source": source,
                    "identity": identity,
                    "file_value": expected,
                    "record_value": actual,
                }
            )
    return issues


def _crate_occurrence_issues(
    occurrence: dict[str, Any],
    database_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    issues = []
    if occurrence["is_canonical_path"]:
        return issues
    identity = {
        "crate": occurrence["crate"],
        "record_index": occurrence["record_index"],
        "outer_record_index": occurrence["outer_record_index"],
        "ptrk_field_index": occurrence["ptrk_field_index"],
    }
    issues.append(
        {
            "code": "noncanonical_crate_path",
            "source": "crate",
            "identity": identity,
            "record_value": occurrence["path"],
            "canonical_value": occurrence["logical_path"],
        }
    )
    exact = [
        record for record in database_records if record["path"] == occurrence["path"]
    ]
    canonical = [
        record
        for record in database_records
        if record["path"] == occurrence["logical_path"]
    ]
    exact_score = max(
        (record["metadata_populated_count"] for record in exact),
        default=-1,
    )
    canonical_score = max(
        (record["metadata_populated_count"] for record in canonical),
        default=-1,
    )
    if canonical and exact_score < canonical_score:
        issues.append(
            {
                "code": "crate_points_to_sparse_alias",
                "source": "crate",
                "identity": identity,
                "record_value": occurrence["path"],
                "canonical_value": occurrence["logical_path"],
                "alias_metadata_populated_count": exact_score,
                "canonical_metadata_populated_count": canonical_score,
            }
        )
    return issues


def audit_metadata_parity(
    serato_dir: Path,
    sqlite_paths: list[Path],
    *,
    metadata_reader: Callable[[Path], dict[str, Any]] = read_file_metadata,
) -> dict[str, Any]:
    """Return full occurrence-level parity evidence without changing inputs."""
    serato_dir = Path(serato_dir).resolve()
    sqlite_paths = [Path(path).resolve() for path in sqlite_paths]
    source_state_before = _audit_source_state(serato_dir, sqlite_paths)
    _validate_serato_source(serato_dir / "database V2")
    database_records = check_serato_crate_paths.parse_database(
        serato_dir / "database V2"
    )
    crate_occurrences = []
    for folder, pattern, prefix in (
        ("Subcrates", "*.crate", ""),
        ("SmartCrates", "*.scrate", "smart:"),
    ):
        root = serato_dir / folder
        if not root.is_dir():
            continue
        for crate_file in sorted(root.rglob(pattern)):
            _validate_serato_source(crate_file)
            crate_name = prefix + str(
                crate_file.relative_to(root).with_suffix("")
            )
            for occurrence in check_serato_crate_paths.parse_crate_occurrences(
                crate_file
            ):
                crate_occurrences.append(
                    {
                        **occurrence,
                        "crate": crate_name,
                        "crate_file": str(crate_file),
                    }
                )

    sqlite_records: dict[str, list[dict[str, Any]]] = {}
    for sqlite_path in sqlite_paths:
        if sqlite_path.name in sqlite_records:
            raise ValueError(f"duplicate SQLite source name: {sqlite_path.name}")
        sqlite_records[sqlite_path.name] = read_sqlite_assets(sqlite_path)

    source_issues = []
    for database_name, records in sqlite_records.items():
        for record in records:
            if record["logical_path"] is None:
                source_issues.append(
                    {
                        "code": "invalid_sqlite_portable_id",
                        "source": "sqlite",
                        "identity": {
                            "database": database_name,
                            "id": record["id"],
                            "rowid": record["rowid"],
                        },
                        "raw_portable_id": record["raw_portable_id"],
                        "reason": record["path_error"],
                    }
                )

    logical_paths = {
        item["logical_path"]
        for item in database_records + crate_occurrences
    }
    for records in sqlite_records.values():
        logical_paths.update(
            item["logical_path"]
            for item in records
            if item["logical_path"] is not None
        )

    database_by_logical: dict[str, list[dict[str, Any]]] = defaultdict(list)
    crate_by_logical: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sqlite_by_logical: dict[str, dict[str, list[dict[str, Any]]]] = {
        name: defaultdict(list) for name in sqlite_records
    }
    for record in database_records:
        database_by_logical[record["logical_path"]].append(record)
    for occurrence in crate_occurrences:
        crate_by_logical[occurrence["logical_path"]].append(occurrence)
    for name, records in sqlite_records.items():
        for record in records:
            if record["logical_path"] is None:
                continue
            sqlite_by_logical[name][record["logical_path"]].append(record)

    logical_tracks = []
    all_issues = []
    for logical in sorted(logical_paths):
        file_path = check_serato_crate_paths.resolve(logical)
        file_error = None
        file_metadata = None
        if file_path.is_file():
            try:
                file_metadata = metadata_reader(file_path)
            except Exception as exc:  # noqa: BLE001 - evidence belongs in report
                file_error = f"{type(exc).__name__}: {exc}"
        records = database_by_logical[logical]
        occurrences = crate_by_logical[logical]
        rows_by_database = {
            name: sqlite_by_logical[name][logical]
            for name in sqlite_records
        }
        issues = []
        if not file_path.is_file():
            issues.append(
                {
                    "code": "file_missing",
                    "source": "file",
                    "path": str(file_path),
                }
            )
        elif file_error:
            issues.append(
                {
                    "code": "file_metadata_unreadable",
                    "source": "file",
                    "path": str(file_path),
                    "error": file_error,
                }
            )
        for occurrence in occurrences:
            occurrence["matching_database_occurrences"] = [
                {
                    "record_index": record["record_index"],
                    "outer_record_index": record["outer_record_index"],
                    "pfil_field_index": record["pfil_field_index"],
                }
                for record in records
                if record["path"] == occurrence["path"]
            ]
            occurrence["logical_database_occurrences"] = [
                {
                    "record_index": record["record_index"],
                    "outer_record_index": record["outer_record_index"],
                    "pfil_field_index": record["pfil_field_index"],
                }
                for record in records
            ]
            occurrence["matching_sqlite_ids"] = {
                database_name: [
                    row["id"]
                    for row in rows
                    if row["path"] == occurrence["path"]
                ]
                for database_name, rows in rows_by_database.items()
            }
            occurrence["logical_sqlite_ids"] = {
                database_name: [row["id"] for row in rows]
                for database_name, rows in rows_by_database.items()
            }
            if not occurrence["matching_database_occurrences"]:
                issues.append(
                    {
                        "code": "crate_without_database_record",
                        "source": "crate",
                        "identity": {
                            "crate": occurrence["crate"],
                            "record_index": occurrence["record_index"],
                            "outer_record_index": occurrence[
                                "outer_record_index"
                            ],
                            "ptrk_field_index": occurrence[
                                "ptrk_field_index"
                            ],
                        },
                        "record_value": occurrence["path"],
                    }
                )
            issues.extend(_crate_occurrence_issues(occurrence, records))
        for record in records:
            issues.extend(
                _comparison_issues(
                    source="database",
                    identity={
                        "record_index": record["record_index"],
                        "outer_record_index": record["outer_record_index"],
                        "pfil_field_index": record["pfil_field_index"],
                    },
                    record={
                        field: record[DATABASE_FIELD_NAMES[field]]
                        for field in FILE_FIELDS
                    },
                    available_fields=set(FILE_FIELDS),
                    file_metadata=file_metadata,
                )
            )
        for database_name, rows in rows_by_database.items():
            if not rows:
                issues.append(
                    {
                        "code": "sqlite_record_missing",
                        "source": "sqlite",
                        "identity": {"database": database_name},
                        "logical_path": logical,
                    }
                )
            for row in rows:
                issues.extend(
                    _comparison_issues(
                        source="sqlite",
                        identity={
                            "database": database_name,
                            "id": row["id"],
                            "rowid": row["rowid"],
                        },
                        record=row,
                        available_fields=set(row["available_metadata_fields"]),
                        file_metadata=file_metadata,
                    )
                )
        duplicate_cardinality = {
            "database_v2": len(records),
            "crate_ptrk": len(occurrences),
            **{
                database_name: len(rows)
                for database_name, rows in rows_by_database.items()
            },
        }
        exact_paths = sorted(
            {
                item["path"]
                for item in records
                + occurrences
                + [
                    row
                    for rows in rows_by_database.values()
                    for row in rows
                ]
            }
        )
        logical_tracks.append(
            {
                "logical_path": logical,
                "canonical_path": logical,
                "exact_paths": exact_paths,
                "file": {
                    "path": str(file_path),
                    "exists": file_path.is_file(),
                    "metadata": file_metadata,
                    "error": file_error,
                },
                "database_records": records,
                "crate_occurrences": occurrences,
                "sqlite_records": rows_by_database,
                "duplicate_cardinality": duplicate_cardinality,
                "issues": issues,
            }
        )
        all_issues.extend(issues)

    source_state_after = _audit_source_state(serato_dir, sqlite_paths)
    if source_state_after != source_state_before:
        changed = sorted(
            path
            for path in set(source_state_before) | set(source_state_after)
            if source_state_before.get(path) != source_state_after.get(path)
        )
        raise RuntimeError(
            "Serato or SQLite sources changed during audit: "
            + ", ".join(changed[:10])
        )

    return {
        "status": "PASS" if not source_issues and not all_issues else "FAIL",
        "serato_dir": str(serato_dir),
        "sqlite_sources": [str(Path(path).resolve()) for path in sqlite_paths],
        "summary": {
            "logical_track_count": len(logical_tracks),
            "database_record_count": len(database_records),
            "crate_reference_count": len(crate_occurrences),
            "sqlite_record_counts": {
                name: len(records) for name, records in sqlite_records.items()
            },
            "issue_count": len(source_issues) + len(all_issues),
        },
        "source_issues": source_issues,
        "logical_tracks": logical_tracks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serato-dir", required=True, type=Path)
    parser.add_argument("--master-sqlite", required=True, type=Path)
    parser.add_argument("--root-sqlite", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    report = audit_metadata_parity(
        args.serato_dir,
        [args.master_sqlite, args.root_sqlite],
    )
    rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": report["status"],
                    **report["summary"],
                    "report": str(args.out),
                },
                indent=2,
            )
        )
    else:
        print(rendered, end="")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
