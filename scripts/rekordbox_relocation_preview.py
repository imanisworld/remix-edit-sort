#!/usr/bin/env python3
"""Build a fail-closed, read-only Rekordbox relocation preview.

Consumes the trusted local SQLite library index plus a completed Rekordbox XML
import. It never opens or writes Rekordbox ``master.db``, never writes audio or
Serato data, and opens SQLite in read-only/query-only mode. Output is JSON on
stdout only.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import argparse
import json
import os
import sqlite3
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import library_index
import rekordbox_index


TOOL_VERSION = "1.0.1"
PREVIEW_SCHEMA_VERSION = 1

STATUS_UNCHANGED = "UNCHANGED"
STATUS_STRONG = "STRONG_CANDIDATE"
STATUS_AMBIGUOUS = "AMBIGUOUS"
STATUS_CHANGED = "CHANGED"
STATUS_MISSING = "MISSING"
STATUSES = (
    STATUS_UNCHANGED,
    STATUS_STRONG,
    STATUS_AMBIGUOUS,
    STATUS_CHANGED,
    STATUS_MISSING,
)

DURATION_MATCH_TOLERANCE_SECONDS = 2.0
DURATION_CONFLICT_TOLERANCE_SECONDS = 5.0
STRONG_SCORE = 55
AMBIGUITY_MARGIN = 15
MIN_PLAUSIBLE_SCORE = 20


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    if not text:
        return None
    return unicodedata.normalize("NFC", text).casefold()


def _filename_identity(path: str) -> str:
    return unicodedata.normalize("NFC", Path(path).name).casefold()


def _path_identity(path: str) -> str:
    # Use the exact same realpath + NFC + case-fold identity as the persistent
    # library index. realpath also normalizes existing symlinked path prefixes
    # such as macOS /var -> /private/var even when the final old file is gone.
    return library_index.mac_path_identity(
        library_index.canonicalize_path(Path(path))
    )


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _readonly_connect(db_path: Path) -> sqlite3.Connection:
    requested = db_path.expanduser().absolute()
    if requested.is_symlink():
        raise RuntimeError(f"library index must not be a symlink: {requested}")
    path = requested.resolve()
    if not path.is_file():
        raise RuntimeError(f"library index must be a regular file: {path}")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA foreign_keys = ON")
    if conn.execute("PRAGMA query_only").fetchone()[0] != 1:
        conn.close()
        raise RuntimeError("failed to enforce SQLite query_only mode")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _required_meta(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if row is None:
        raise RuntimeError(f"library index is missing required meta key {key!r}")
    return str(row[0])


def _validate_index_state(conn: sqlite3.Connection) -> dict[str, Any]:
    if _required_meta(conn, "schema_version") != str(library_index.SCHEMA_VERSION):
        raise RuntimeError("library index schema changed after open")
    if _required_meta(conn, "rekordbox_schema_version") != str(
        rekordbox_index.REKORDBOX_SCHEMA_VERSION
    ):
        raise RuntimeError("unsupported Rekordbox relationship schema")

    latest_any = conn.execute("SELECT * FROM scans ORDER BY scan_id DESC LIMIT 1").fetchone()
    latest = conn.execute(
        "SELECT * FROM scans WHERE completed_successfully=1 ORDER BY scan_id DESC LIMIT 1"
    ).fetchone()
    if latest is None:
        raise RuntimeError("no completed library scan is available")
    if latest_any is None or int(latest_any["scan_id"]) != int(latest["scan_id"]):
        raise RuntimeError("a newer incomplete library scan exists; relocation preview blocked")

    current_files = int(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0])
    if current_files != int(latest["file_count"]):
        raise RuntimeError(
            "files table does not match latest completed scan count; relocation preview blocked"
        )
    stale_files = int(
        conn.execute(
            "SELECT COUNT(*) FROM files WHERE last_seen_scan_id != ?", (latest["scan_id"],)
        ).fetchone()[0]
    )
    if stale_files:
        raise RuntimeError(
            f"{stale_files} file row(s) are not anchored to the latest completed scan"
        )
    return {
        "scan_id": int(latest["scan_id"]),
        "completed_at": latest["completed_at"],
        "root": _required_meta(conn, "root_canonical"),
        "file_count": current_files,
        # Successful scans fail before replacing trusted state on
        # discovery/stability errors. This count is metadata parse coverage.
        "metadata_parse_error_count": int(latest["error_count"]),
    }


def _select_rekordbox_import(
    conn: sqlite3.Connection, import_id: int | None
) -> sqlite3.Row:
    if import_id is None:
        row = conn.execute(
            "SELECT * FROM rekordbox_imports WHERE completed_successfully=1 "
            "ORDER BY import_id DESC LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM rekordbox_imports "
            "WHERE import_id=? AND completed_successfully=1",
            (import_id,),
        ).fetchone()
    if row is None:
        label = "latest completed" if import_id is None else str(import_id)
        raise RuntimeError(f"no completed Rekordbox import found for {label}")

    count = int(
        conn.execute(
            "SELECT COUNT(*) FROM rekordbox_tracks WHERE import_id=?",
            (row["import_id"],),
        ).fetchone()[0]
    )
    if count != int(row["collection_track_count"]):
        raise RuntimeError("Rekordbox relationship tables are internally inconsistent")
    return row


def _latest_serato_import(conn: sqlite3.Connection) -> sqlite3.Row | None:
    if not _table_exists(conn, "serato_imports") or not _table_exists(
        conn, "serato_tracks"
    ):
        return None
    return conn.execute(
        "SELECT * FROM serato_imports WHERE completed_successfully=1 "
        "ORDER BY import_id DESC LIMIT 1"
    ).fetchone()


def _attributes(row: sqlite3.Row) -> dict[str, Any]:
    try:
        parsed = json.loads(row["attributes_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"invalid attributes_json for Rekordbox track {row['rekordbox_track_id']}"
        ) from exc
    if not isinstance(parsed, dict):
        raise RuntimeError(
            f"non-object attributes_json for Rekordbox track {row['rekordbox_track_id']}"
        )
    return parsed


def _source_snapshot_boundary(rb_import: sqlite3.Row) -> str:
    """Conservative time boundary for evidence that a file changed after XML."""
    imported = str(rb_import["completed_at"])
    try:
        fingerprint = json.loads(rb_import["source_fingerprint_json"])
        mtime_ns = int(fingerprint["mtime_ns"])
        source_mtime = datetime.fromtimestamp(
            mtime_ns / 1_000_000_000, tz=timezone.utc
        ).isoformat()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError):
        return imported
    # If the XML is old, use its mtime. If a copy operation made its mtime look
    # newer than the import, never move the boundary past the import itself.
    return min(imported, source_mtime)


def _snapshot_baseline_scan_id(
    conn: sqlite3.Connection, rb_import: sqlite3.Row
) -> int | None:
    row = conn.execute(
        "SELECT scan_id FROM scans "
        "WHERE completed_successfully=1 AND completed_at IS NOT NULL AND completed_at <= ? "
        "ORDER BY scan_id DESC LIMIT 1",
        (_source_snapshot_boundary(rb_import),),
    ).fetchone()
    return int(row["scan_id"]) if row is not None else None


def _changed_since_scan(
    conn: sqlite3.Connection, baseline_scan_id: int | None
) -> set[int]:
    if baseline_scan_id is None:
        return set()
    return {
        int(row["file_id"])
        for row in conn.execute(
            "SELECT DISTINCT file_id FROM scan_files "
            "WHERE scan_id > ? AND state='CHANGED' AND file_id IS NOT NULL",
            (baseline_scan_id,),
        )
    }


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "file_id": int(row["file_id"]),
        "path": row["display_path"],
        "canonical_path": row["canonical_path"],
        "filename": Path(row["display_path"]).name,
        "size_bytes": int(row["size_bytes"]),
        "sha256": row["sha256"],
        "duration_seconds": row["duration_seconds"],
        "artist": row["artist"],
        "title": row["title"],
        "album": row["album"],
        "genre": row["genre"],
        "extension": row["extension"],
        "parse_error": row["parse_error"],
    }


def _candidate_indexes(
    rows: Iterable[sqlite3.Row],
) -> tuple[
    dict[str, sqlite3.Row],
    dict[str, list[sqlite3.Row]],
    dict[int, list[sqlite3.Row]],
    dict[tuple[str, str], list[sqlite3.Row]],
]:
    by_path: dict[str, sqlite3.Row] = {}
    by_filename: dict[str, list[sqlite3.Row]] = defaultdict(list)
    by_size: dict[int, list[sqlite3.Row]] = defaultdict(list)
    by_artist_title: dict[tuple[str, str], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_path[row["path_identity"]] = row
        by_filename[_filename_identity(row["canonical_path"])].append(row)
        by_size[int(row["size_bytes"])].append(row)
        artist = _normalize_text(row["artist"])
        title = _normalize_text(row["title"])
        if artist and title:
            by_artist_title[(artist, title)].append(row)
    return by_path, by_filename, by_size, by_artist_title


def _move_history(
    conn: sqlite3.Connection,
) -> tuple[dict[str, set[int]], dict[str, set[int]]]:
    """Return current-valid and later-changed SHA move lineage by prior path."""
    trusted: dict[str, set[int]] = defaultdict(set)
    invalidated: dict[str, set[int]] = defaultdict(set)
    moves = list(
        conn.execute(
            "SELECT scan_id,file_id,prior_canonical_path FROM scan_files "
            "WHERE state='MOVED' AND prior_canonical_path IS NOT NULL "
            "AND file_id IS NOT NULL ORDER BY scan_id"
        )
    )
    for row in moves:
        file_id = int(row["file_id"])
        changed_later = conn.execute(
            "SELECT 1 FROM scan_files "
            "WHERE file_id=? AND scan_id>? AND state='CHANGED' LIMIT 1",
            (file_id, row["scan_id"]),
        ).fetchone()
        target = invalidated if changed_later is not None else trusted
        target[_path_identity(row["prior_canonical_path"])].add(file_id)
    return trusted, invalidated


def _serato_evidence(
    conn: sqlite3.Connection, serato_import: sqlite3.Row | None
) -> tuple[dict[str, set[int]], set[int]]:
    by_path: dict[str, set[int]] = defaultdict(set)
    exact_file_ids: set[int] = set()
    if serato_import is None:
        return by_path, exact_file_ids
    for row in conn.execute(
        "SELECT absolute_path,match_state,file_id FROM serato_tracks WHERE import_id=?",
        (serato_import["import_id"],),
    ):
        if row["match_state"] == "EXACT_PATH" and row["file_id"] is not None:
            file_id = int(row["file_id"])
            by_path[_path_identity(row["absolute_path"])].add(file_id)
            exact_file_ids.add(file_id)
    return by_path, exact_file_ids


def _metadata_pair(
    attributes: dict[str, Any],
) -> tuple[str | None, str | None]:
    return (
        _normalize_text(attributes.get("Artist")),
        _normalize_text(attributes.get("Name")),
    )


def _candidate_set(
    rb_row: sqlite3.Row,
    attributes: dict[str, Any],
    by_file_id: dict[int, sqlite3.Row],
    by_filename: dict[str, list[sqlite3.Row]],
    by_size: dict[int, list[sqlite3.Row]],
    by_artist_title: dict[tuple[str, str], list[sqlite3.Row]],
    move_history: dict[str, set[int]],
    invalidated_move_history: dict[str, set[int]],
    serato_by_path: dict[str, set[int]],
) -> tuple[list[sqlite3.Row], set[int], set[int], set[int]]:
    ids: set[int] = set()
    old_path_id = _path_identity(rb_row["absolute_path"])
    history_ids = set(move_history.get(old_path_id, set()))
    invalidated_history_ids = set(invalidated_move_history.get(old_path_id, set()))
    serato_ids = set(serato_by_path.get(old_path_id, set()))
    ids.update(history_ids)
    ids.update(invalidated_history_ids)
    ids.update(serato_ids)
    ids.update(
        int(row["file_id"])
        for row in by_filename.get(_filename_identity(rb_row["absolute_path"]), [])
    )

    rb_size = _safe_int(attributes.get("Size"))
    if rb_size is not None:
        size_rows = by_size.get(rb_size, [])
        # Byte size is useful but not unique enough to explode a candidate set.
        if len(size_rows) <= 20:
            ids.update(int(row["file_id"]) for row in size_rows)

    artist, title = _metadata_pair(attributes)
    if artist and title:
        ids.update(
            int(row["file_id"])
            for row in by_artist_title.get((artist, title), [])
        )

    return (
        [by_file_id[file_id] for file_id in ids if file_id in by_file_id],
        history_ids,
        invalidated_history_ids,
        serato_ids,
    )


def _compare_candidate(
    rb_row: sqlite3.Row,
    attributes: dict[str, Any],
    candidate: sqlite3.Row,
    *,
    history_ids: set[int],
    invalidated_history_ids: set[int],
    serato_old_path_ids: set[int],
    serato_exact_file_ids: set[int],
    changed_since_snapshot_ids: set[int],
) -> dict[str, Any]:
    file_id = int(candidate["file_id"])
    score = 0
    evidence: list[str] = []
    conflicts: list[str] = []
    differences: list[str] = []

    if file_id in history_ids:
        score += 100
        evidence.append("sha256_move_lineage")
    elif file_id in invalidated_history_ids:
        # The move itself was proven by whole-file SHA-256, but a later scan
        # proved those bytes changed. Keep it as identity evidence, never as
        # automatic relocation authority.
        score += 70
        evidence.append("historical_sha256_move_lineage")
        conflicts.append("changed_after_sha256_move")

    if file_id in serato_old_path_ids:
        score += 70
        evidence.append("exact_serato_path_link")

    if _filename_identity(candidate["canonical_path"]) == _filename_identity(
        rb_row["absolute_path"]
    ):
        score += 25
        evidence.append("filename_exact")

    rb_size = _safe_int(attributes.get("Size"))
    if rb_size is not None:
        if int(candidate["size_bytes"]) == rb_size:
            score += 25
            evidence.append("byte_size_exact")
        else:
            conflicts.append("byte_size_changed")
            score -= 25

    rb_duration = _safe_float(attributes.get("TotalTime"))
    current_duration = _safe_float(candidate["duration_seconds"])
    if rb_duration is not None and current_duration is not None:
        delta = abs(rb_duration - current_duration)
        if delta <= DURATION_MATCH_TOLERANCE_SECONDS:
            score += 20
            evidence.append("duration_match")
        elif delta > DURATION_CONFLICT_TOLERANCE_SECONDS:
            conflicts.append("duration_changed")
            score -= 25

    for rb_name, current_name, points in (
        ("Name", "title", 15),
        ("Artist", "artist", 15),
        ("Album", "album", 5),
    ):
        rb_value = _normalize_text(attributes.get(rb_name))
        current_value = _normalize_text(candidate[current_name])
        if rb_value and current_value:
            if rb_value == current_value:
                score += points
                evidence.append(f"{current_name}_exact")
            else:
                differences.append(f"{current_name}_changed")

    rb_genre = _normalize_text(attributes.get("Genre"))
    current_genre = _normalize_text(candidate["genre"])
    if rb_genre and current_genre:
        if rb_genre == current_genre:
            evidence.append("genre_exact")
        else:
            differences.append("genre_changed")

    rb_ext = Path(rb_row["absolute_path"]).suffix.casefold()
    if rb_ext and rb_ext == str(candidate["extension"]).casefold():
        score += 2
        evidence.append("extension_exact")

    if file_id in serato_exact_file_ids:
        score += 5
        evidence.append("current_file_has_exact_serato_reference")

    if file_id in changed_since_snapshot_ids:
        conflicts.append("changed_since_rekordbox_snapshot")

    # Keep deterministic, non-duplicated evidence when two independent history
    # rules report the same material change.
    conflicts = list(dict.fromkeys(conflicts))
    return {
        **_row_payload(candidate),
        "score": score,
        "evidence": evidence,
        "conflicts": conflicts,
        "differences": differences,
        "material_change": bool(conflicts),
    }


def _classify_ranked(
    ranked: list[dict[str, Any]],
) -> tuple[str, dict[str, Any] | None]:
    if not ranked or ranked[0]["score"] < MIN_PLAUSIBLE_SCORE:
        return STATUS_MISSING, None
    top = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    separated = (
        second is None or top["score"] - second["score"] >= AMBIGUITY_MARGIN
    )
    if top["material_change"] and top["score"] >= STRONG_SCORE and separated:
        return STATUS_CHANGED, top
    if top["score"] >= STRONG_SCORE and separated:
        return STATUS_STRONG, top
    return STATUS_AMBIGUOUS, top


def _build_track_preview(
    rb_row: sqlite3.Row,
    *,
    by_path: dict[str, sqlite3.Row],
    by_file_id: dict[int, sqlite3.Row],
    by_filename: dict[str, list[sqlite3.Row]],
    by_size: dict[int, list[sqlite3.Row]],
    by_artist_title: dict[tuple[str, str], list[sqlite3.Row]],
    move_history: dict[str, set[int]],
    invalidated_move_history: dict[str, set[int]],
    serato_by_path: dict[str, set[int]],
    serato_exact_file_ids: set[int],
    changed_since_snapshot_ids: set[int],
) -> dict[str, Any]:
    attrs = _attributes(rb_row)
    old_path = rb_row["absolute_path"]
    exact = by_path.get(_path_identity(old_path))

    if exact is not None:
        compared = _compare_candidate(
            rb_row,
            attrs,
            exact,
            history_ids=set(),
            invalidated_history_ids=set(),
            serato_old_path_ids=set(),
            serato_exact_file_ids=serato_exact_file_ids,
            changed_since_snapshot_ids=changed_since_snapshot_ids,
        )
        compared["evidence"].append("exact_current_path")
        compared["score"] += 200
        status = STATUS_CHANGED if compared["material_change"] else STATUS_UNCHANGED
        return {
            "rekordbox_track_id": rb_row["rekordbox_track_id"],
            "old_path": old_path,
            "name": attrs.get("Name"),
            "artist": attrs.get("Artist"),
            "status": status,
            "proposed_file_id": int(exact["file_id"]),
            "proposed_path": exact["display_path"],
            "bridge_eligible": status == STATUS_UNCHANGED,
            "review_required": status != STATUS_UNCHANGED,
            "top_candidates": [compared],
        }

    (
        candidates,
        history_ids,
        invalidated_history_ids,
        serato_old_path_ids,
    ) = _candidate_set(
        rb_row,
        attrs,
        by_file_id,
        by_filename,
        by_size,
        by_artist_title,
        move_history,
        invalidated_move_history,
        serato_by_path,
    )
    ranked = sorted(
        (
            _compare_candidate(
                rb_row,
                attrs,
                candidate,
                history_ids=history_ids,
                invalidated_history_ids=invalidated_history_ids,
                serato_old_path_ids=serato_old_path_ids,
                serato_exact_file_ids=serato_exact_file_ids,
                changed_since_snapshot_ids=changed_since_snapshot_ids,
            )
            for candidate in candidates
        ),
        key=lambda item: (-item["score"], item["path"].casefold()),
    )
    status, top = _classify_ranked(ranked)
    return {
        "rekordbox_track_id": rb_row["rekordbox_track_id"],
        "old_path": old_path,
        "name": attrs.get("Name"),
        "artist": attrs.get("Artist"),
        "status": status,
        "proposed_file_id": top["file_id"] if top else None,
        "proposed_path": top["path"] if top else None,
        "bridge_eligible": status == STATUS_STRONG,
        "review_required": status != STATUS_STRONG,
        "top_candidates": ranked[:10],
    }


def build_preview(
    db_path: Path, *, import_id: int | None = None
) -> dict[str, Any]:
    conn = _readonly_connect(db_path)
    try:
        library_state = _validate_index_state(conn)
        rb_import = _select_rekordbox_import(conn, import_id)
        serato_import = _latest_serato_import(conn)
        current_rows = list(conn.execute("SELECT * FROM files ORDER BY file_id"))
        by_file_id = {int(row["file_id"]): row for row in current_rows}
        (
            by_path,
            by_filename,
            by_size,
            by_artist_title,
        ) = _candidate_indexes(current_rows)
        move_history, invalidated_move_history = _move_history(conn)
        serato_by_path, serato_exact_file_ids = _serato_evidence(
            conn, serato_import
        )
        baseline_scan_id = _snapshot_baseline_scan_id(conn, rb_import)
        changed_since_snapshot_ids = _changed_since_scan(
            conn, baseline_scan_id
        )
        rb_rows = list(
            conn.execute(
                "SELECT * FROM rekordbox_tracks "
                "WHERE import_id=? ORDER BY rekordbox_track_id",
                (rb_import["import_id"],),
            )
        )

        tracks = [
            _build_track_preview(
                row,
                by_path=by_path,
                by_file_id=by_file_id,
                by_filename=by_filename,
                by_size=by_size,
                by_artist_title=by_artist_title,
                move_history=move_history,
                invalidated_move_history=invalidated_move_history,
                serato_by_path=serato_by_path,
                serato_exact_file_ids=serato_exact_file_ids,
                changed_since_snapshot_ids=changed_since_snapshot_ids,
            )
            for row in rb_rows
        ]
        counts = {status: 0 for status in STATUSES}
        for track in tracks:
            counts[track["status"]] += 1

        return {
            "tool": "rekordbox_relocation_preview",
            "tool_version": TOOL_VERSION,
            "preview_schema_version": PREVIEW_SCHEMA_VERSION,
            "safety": {
                "classification": SAFETY_CLASSIFICATION,
                "sqlite_mode": "read-only + query_only",
                "output": "stdout only",
                "writes_rekordbox": False,
                "writes_serato": False,
                "writes_audio": False,
                "writes_library_index": False,
                "automatic_approval": False,
            },
            "library": library_state,
            "rekordbox": {
                "import_id": int(rb_import["import_id"]),
                "completed_at": rb_import["completed_at"],
                "xml_path": rb_import["xml_path"],
                "source_fingerprint": json.loads(
                    rb_import["source_fingerprint_json"]
                ),
                "collection_tracks": int(rb_import["collection_track_count"]),
                "evidence_baseline_scan_id": baseline_scan_id,
            },
            "serato": (
                {
                    "available": True,
                    "import_id": int(serato_import["import_id"]),
                    "completed_at": serato_import["completed_at"],
                    "serato_dir": serato_import["serato_dir"],
                }
                if serato_import is not None
                else {"available": False}
            ),
            "summary": {
                "total": len(tracks),
                "unchanged": counts[STATUS_UNCHANGED],
                "strong_candidate": counts[STATUS_STRONG],
                "ambiguous": counts[STATUS_AMBIGUOUS],
                "changed": counts[STATUS_CHANGED],
                "missing": counts[STATUS_MISSING],
                "bridge_eligible": sum(
                    bool(track["bridge_eligible"]) for track in tracks
                ),
                "review_required": sum(
                    bool(track["review_required"]) for track in tracks
                ),
            },
            "tracks": tracks,
        }
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument(
        "--import-id",
        type=int,
        help="specific completed Rekordbox import to preview; defaults to latest",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = build_preview(args.db, import_id=args.import_id)
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
