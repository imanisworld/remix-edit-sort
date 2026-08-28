#!/usr/bin/env python3
"""Build a fail-closed, read-only Serato ↔ Rekordbox playlist crosswalk.

The tool compares the latest (or explicitly selected) trusted Serato and
Rekordbox relationship snapshots in the persistent library index. Track
identity is the shared current ``file_id`` only; filenames, durations, and
metadata never create a resolved cross-application link.

Serato crate order is recovered by rereading the exact source ``.crate`` files
only after proving their current fingerprints still equal the indexed source
manifest. No mapping or playlist-transfer decision is automatic. Output is JSON
on stdout only.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import argparse
import json
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import library_index
import rekordbox_index
import rekordbox_relocation_preview
import serato_index

TOOL_VERSION = "1.0.0"
CROSSWALK_SCHEMA_VERSION = 1
MAX_CANDIDATES_PER_CRATE = 5


def _normalize_name(value: str) -> str:
    return unicodedata.normalize("NFC", " ".join(value.strip().split())).casefold()


def _select_completed_import(
    conn: sqlite3.Connection,
    *,
    table: str,
    import_id: int | None,
    label: str,
) -> sqlite3.Row:
    if import_id is None:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE completed_successfully=1 "
            "ORDER BY import_id DESC LIMIT 1"
        ).fetchone()
    else:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE import_id=? AND completed_successfully=1",
            (import_id,),
        ).fetchone()
    if row is None:
        requested = "latest completed" if import_id is None else str(import_id)
        raise RuntimeError(f"no {label} import found for {requested}")
    return row


def _validate_schema(conn: sqlite3.Connection) -> None:
    if rekordbox_relocation_preview._required_meta(
        conn, "serato_schema_version"
    ) != str(serato_index.SERATO_SCHEMA_VERSION):
        raise RuntimeError("unsupported Serato relationship schema")
    if rekordbox_relocation_preview._required_meta(
        conn, "rekordbox_schema_version"
    ) != str(rekordbox_index.REKORDBOX_SCHEMA_VERSION):
        raise RuntimeError("unsupported Rekordbox relationship schema")


def _validate_serato_source(row: sqlite3.Row) -> tuple[Path, dict[str, list[str]]]:
    serato_dir = Path(row["serato_dir"])
    if serato_dir.is_symlink() or not serato_dir.is_dir():
        raise RuntimeError(f"indexed Serato directory is unavailable: {serato_dir}")
    try:
        stored = json.loads(row["source_manifest_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("indexed Serato source manifest is invalid") from exc
    current_manifest = serato_index._source_manifest(serato_dir)
    current = [item.__dict__ for item in current_manifest]
    if stored != current:
        raise RuntimeError(
            "Serato database/crates changed since the selected import; "
            "crosswalk blocked until Serato relationships are refreshed"
        )

    crate_files = [Path(item.path) for item in current_manifest[1:]]
    if len(crate_files) != int(row["crate_count"]):
        raise RuntimeError("Serato import crate count no longer matches its source manifest")
    ordered = {path.stem: serato_index._parse_crate(path) for path in crate_files}
    if len(ordered) != len(crate_files):
        raise RuntimeError("Serato crate filenames collapse to duplicate crate names")
    if sum(len(paths) for paths in ordered.values()) != int(row["crate_reference_count"]):
        raise RuntimeError("Serato crate reference count is internally inconsistent")
    return serato_dir, ordered


def _load_file_catalog(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    catalog: dict[int, dict[str, Any]] = {}
    for row in conn.execute(
        "SELECT file_id,display_path,artist,title,album,genre FROM files ORDER BY file_id"
    ):
        catalog[int(row["file_id"])] = {
            "file_id": int(row["file_id"]),
            "path": row["display_path"],
            "artist": row["artist"],
            "title": row["title"],
            "album": row["album"],
            "genre": row["genre"],
        }
    return catalog


def _serato_tracks(
    conn: sqlite3.Connection, import_id: int
) -> dict[str, sqlite3.Row]:
    rows = list(
        conn.execute(
            "SELECT * FROM serato_tracks WHERE import_id=? ORDER BY serato_path",
            (import_id,),
        )
    )
    result = {row["serato_path"]: row for row in rows}
    if len(result) != len(rows):
        raise RuntimeError("duplicate Serato paths in relationship snapshot")
    return result


def _serato_crates(
    ordered_sources: dict[str, list[str]],
    track_rows: dict[str, sqlite3.Row],
    current_file_ids: set[int],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for crate_name, ordered_paths in ordered_sources.items():
        entries: list[dict[str, Any]] = []
        resolved_order: list[int] = []
        unresolved: list[dict[str, Any]] = []
        for ordinal, serato_path in enumerate(ordered_paths):
            row = track_rows.get(serato_path)
            if row is None:
                raise RuntimeError(
                    f"Serato crate {crate_name!r} references a path absent from its import"
                )
            file_id = int(row["file_id"]) if row["file_id"] is not None else None
            if (
                row["match_state"] == "EXACT_PATH"
                and file_id is not None
                and file_id in current_file_ids
            ):
                entry = {
                    "ordinal": ordinal,
                    "serato_path": serato_path,
                    "state": "RESOLVED",
                    "file_id": file_id,
                }
                resolved_order.append(file_id)
            else:
                reason = row["match_state"]
                if file_id is not None and file_id not in current_file_ids:
                    reason = "STALE_FILE_ID"
                try:
                    candidates = json.loads(row["candidates_json"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"invalid Serato candidates for {serato_path!r}"
                    ) from exc
                entry = {
                    "ordinal": ordinal,
                    "serato_path": serato_path,
                    "state": "UNRESOLVED",
                    "reason": reason,
                    "candidates": candidates,
                }
                unresolved.append(entry)
            entries.append(entry)

        counts = Counter(resolved_order)
        duplicates = sorted(file_id for file_id, count in counts.items() if count > 1)
        result[crate_name] = {
            "crate_name": crate_name,
            "entry_count": len(entries),
            "resolved_entry_count": len(resolved_order),
            "resolved_distinct_file_ids": sorted(set(resolved_order)),
            "resolved_order": resolved_order,
            "duplicate_resolved_file_ids": duplicates,
            "unresolved_count": len(unresolved),
            "unresolved": unresolved,
            "ordered_entries": entries,
        }
    return result


def _rekordbox_playlists(
    conn: sqlite3.Connection,
    import_id: int,
    current_file_ids: set[int],
) -> dict[str, dict[str, Any]]:
    track_rows = {
        row["rekordbox_track_id"]: row
        for row in conn.execute(
            "SELECT * FROM rekordbox_tracks WHERE import_id=?",
            (import_id,),
        )
    }
    nodes = list(
        conn.execute(
            "SELECT * FROM rekordbox_playlist_nodes "
            "WHERE import_id=? AND node_type=1 ORDER BY node_path,node_key",
            (import_id,),
        )
    )
    by_path: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for node in nodes:
        by_path[node["node_path"]].append(node)
    duplicate_paths = sorted(path for path, items in by_path.items() if len(items) > 1)
    if duplicate_paths:
        raise RuntimeError(
            f"Rekordbox snapshot has duplicate playlist paths: {duplicate_paths[:5]}"
        )

    result: dict[str, dict[str, Any]] = {}
    for node in nodes:
        memberships = list(
            conn.execute(
                "SELECT * FROM rekordbox_playlist_memberships "
                "WHERE import_id=? AND node_key=? ORDER BY ordinal",
                (import_id, node["node_key"]),
            )
        )
        resolved_order: list[int] = []
        unresolved: list[dict[str, Any]] = []
        ordered_entries: list[dict[str, Any]] = []
        for membership in memberships:
            rb_track_id = membership["rekordbox_track_id"]
            track = track_rows.get(rb_track_id) if rb_track_id is not None else None
            file_id = (
                int(track["file_id"])
                if track is not None and track["file_id"] is not None
                else None
            )
            if (
                membership["reference_state"] == "RESOLVED"
                and track is not None
                and track["match_state"] == "EXACT_PATH"
                and file_id is not None
                and file_id in current_file_ids
            ):
                entry = {
                    "ordinal": int(membership["ordinal"]),
                    "state": "RESOLVED",
                    "file_id": file_id,
                    "rekordbox_track_id": rb_track_id,
                }
                resolved_order.append(file_id)
            else:
                reason = membership["reference_state"]
                if membership["reference_state"] == "RESOLVED":
                    if track is None:
                        reason = "TRACK_RECORD_MISSING"
                    elif track["match_state"] != "EXACT_PATH":
                        reason = f"TRACK_{track['match_state']}"
                    elif file_id is None or file_id not in current_file_ids:
                        reason = "STALE_FILE_ID"
                try:
                    candidates = json.loads(membership["candidates_json"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"invalid Rekordbox playlist candidates in {node['node_path']!r}"
                    ) from exc
                entry = {
                    "ordinal": int(membership["ordinal"]),
                    "state": "UNRESOLVED",
                    "reason": reason,
                    "raw_key": membership["raw_key"],
                    "rekordbox_track_id": rb_track_id,
                    "candidates": candidates,
                }
                unresolved.append(entry)
            ordered_entries.append(entry)

        counts = Counter(resolved_order)
        duplicates = sorted(file_id for file_id, count in counts.items() if count > 1)
        result[node["node_key"]] = {
            "node_key": node["node_key"],
            "node_path": node["node_path"],
            "name": node["name"],
            "key_type": node["key_type"],
            "entry_count": len(memberships),
            "resolved_entry_count": len(resolved_order),
            "resolved_distinct_file_ids": sorted(set(resolved_order)),
            "resolved_order": resolved_order,
            "duplicate_resolved_file_ids": duplicates,
            "unresolved_count": len(unresolved),
            "unresolved": unresolved,
            "ordered_entries": ordered_entries,
        }
    return result


def _candidate(
    crate: dict[str, Any], playlist: dict[str, Any]
) -> dict[str, Any] | None:
    serato_ids = set(crate["resolved_distinct_file_ids"])
    rekordbox_ids = set(playlist["resolved_distinct_file_ids"])
    shared = sorted(serato_ids & rekordbox_ids)
    exact_name = _normalize_name(crate["crate_name"]) == _normalize_name(
        playlist["name"]
    )
    if not exact_name and not shared:
        return None
    serato_only = sorted(serato_ids - rekordbox_ids)
    rekordbox_only = sorted(rekordbox_ids - serato_ids)
    union = serato_ids | rekordbox_ids
    return {
        "node_key": playlist["node_key"],
        "node_path": playlist["node_path"],
        "name": playlist["name"],
        "exact_name": exact_name,
        "shared_count": len(shared),
        "serato_only_count": len(serato_only),
        "rekordbox_only_count": len(rekordbox_only),
        "shared_file_ids": shared,
        "serato_only_file_ids": serato_only,
        "rekordbox_only_file_ids": rekordbox_only,
        "serato_coverage": (len(shared) / len(serato_ids)) if serato_ids else 0.0,
        "jaccard": (len(shared) / len(union)) if union else 0.0,
        "rekordbox_unresolved_count": playlist["unresolved_count"],
        "rekordbox_unresolved": playlist["unresolved"],
    }


def _rank_candidates(
    crate: dict[str, Any], playlists: dict[str, dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    candidates = [
        candidate
        for playlist in playlists.values()
        if (candidate := _candidate(crate, playlist)) is not None
    ]
    candidates.sort(
        key=lambda item: (
            not item["exact_name"],
            -item["shared_count"],
            -item["serato_coverage"],
            -item["jaccard"],
            item["node_path"].casefold(),
        )
    )
    exact_count = sum(bool(item["exact_name"]) for item in candidates)
    if exact_count == 1:
        state = "UNIQUE_NAME_CANDIDATE"
    elif exact_count > 1:
        state = "AMBIGUOUS_NAME_CANDIDATES"
    elif candidates:
        state = "OVERLAP_CANDIDATES"
    else:
        state = "NO_CANDIDATE"
    return state, candidates[:MAX_CANDIDATES_PER_CRATE]


def build_crosswalk(
    db_path: Path,
    *,
    serato_import_id: int | None = None,
    rekordbox_import_id: int | None = None,
) -> dict[str, Any]:
    conn = rekordbox_relocation_preview._readonly_connect(db_path)
    try:
        library_state = rekordbox_relocation_preview._validate_index_state(conn)
        _validate_schema(conn)
        serato_import = _select_completed_import(
            conn,
            table="serato_imports",
            import_id=serato_import_id,
            label="Serato",
        )
        rekordbox_import = _select_completed_import(
            conn,
            table="rekordbox_imports",
            import_id=rekordbox_import_id,
            label="Rekordbox",
        )
        serato_dir, ordered_sources = _validate_serato_source(serato_import)
        catalog = _load_file_catalog(conn)
        current_file_ids = set(catalog)
        serato_track_rows = _serato_tracks(conn, int(serato_import["import_id"]))
        serato_crates = _serato_crates(
            ordered_sources, serato_track_rows, current_file_ids
        )
        rekordbox_playlists = _rekordbox_playlists(
            conn, int(rekordbox_import["import_id"]), current_file_ids
        )

        crate_payloads: list[dict[str, Any]] = []
        all_referenced_file_ids: set[int] = set()
        mapping_state_counts: Counter[str] = Counter()
        serato_unresolved_total = 0
        rekordbox_unresolved_total = sum(
            int(playlist["unresolved_count"])
            for playlist in rekordbox_playlists.values()
        )

        for crate_name in sorted(serato_crates, key=str.casefold):
            crate = serato_crates[crate_name]
            state, candidates = _rank_candidates(crate, rekordbox_playlists)
            mapping_state_counts[state] += 1
            serato_unresolved_total += int(crate["unresolved_count"])
            all_referenced_file_ids.update(crate["resolved_distinct_file_ids"])
            for candidate in candidates:
                all_referenced_file_ids.update(candidate["shared_file_ids"])
                all_referenced_file_ids.update(candidate["serato_only_file_ids"])
                all_referenced_file_ids.update(candidate["rekordbox_only_file_ids"])

            crate_payloads.append(
                {
                    **crate,
                    "mapping_state": state,
                    "candidate_playlists": candidates,
                    "transfer_plan": {
                        "decision": None,
                        "automatic_decision": False,
                        "allowed_decisions": [
                            "MAP_TO_EXISTING_PLAYLIST",
                            "CREATE_NEW_REKORDBOX_PLAYLIST",
                            "KEEP_UNRESOLVED",
                            "SKIP",
                        ],
                        "selected_rekordbox_node_key": None,
                        "selected_rekordbox_node_path": None,
                        "proposed_new_playlist_name": crate_name,
                        "decision_evidence": [],
                        "ordered_serato_entries": crate["ordered_entries"],
                    },
                }
            )

        return {
            "tool": "serato_rekordbox_crosswalk",
            "tool_version": TOOL_VERSION,
            "crosswalk_schema_version": CROSSWALK_SCHEMA_VERSION,
            "safety": {
                "classification": SAFETY_CLASSIFICATION,
                "sqlite_mode": "read-only + query_only",
                "serato_source_read": "fingerprint verified, read-only",
                "writes_serato": False,
                "writes_rekordbox": False,
                "writes_audio": False,
                "writes_library_index": False,
                "automatic_mapping": False,
                "automatic_playlist_creation": False,
            },
            "library": library_state,
            "serato": {
                "import_id": int(serato_import["import_id"]),
                "completed_at": serato_import["completed_at"],
                "serato_dir": str(serato_dir),
                "crate_count": len(serato_crates),
                "source_manifest_verified": True,
                "crate_order_recovered_from_verified_sources": True,
            },
            "rekordbox": {
                "import_id": int(rekordbox_import["import_id"]),
                "completed_at": rekordbox_import["completed_at"],
                "xml_path": rekordbox_import["xml_path"],
                "playlist_count": len(rekordbox_playlists),
            },
            "summary": {
                "serato_crates": len(serato_crates),
                "rekordbox_playlists": len(rekordbox_playlists),
                "unique_name_candidates": mapping_state_counts["UNIQUE_NAME_CANDIDATE"],
                "ambiguous_name_candidates": mapping_state_counts[
                    "AMBIGUOUS_NAME_CANDIDATES"
                ],
                "overlap_candidates": mapping_state_counts["OVERLAP_CANDIDATES"],
                "no_candidate": mapping_state_counts["NO_CANDIDATE"],
                "serato_unresolved_entries": serato_unresolved_total,
                "rekordbox_unresolved_entries": rekordbox_unresolved_total,
                "automatic_mappings": 0,
                "review_plans": len(crate_payloads),
            },
            "track_catalog": {
                str(file_id): catalog[file_id]
                for file_id in sorted(all_referenced_file_ids)
                if file_id in catalog
            },
            "rekordbox_playlists": [
                rekordbox_playlists[key]
                for key in sorted(
                    rekordbox_playlists,
                    key=lambda item: rekordbox_playlists[item]["node_path"].casefold(),
                )
            ],
            "crates": crate_payloads,
        }
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--serato-import-id", type=int)
    parser.add_argument("--rekordbox-import-id", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = build_crosswalk(
            args.db,
            serato_import_id=args.serato_import_id,
            rekordbox_import_id=args.rekordbox_import_id,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
