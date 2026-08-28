#!/usr/bin/env python3
"""Build a human-review packet for Serato → Rekordbox playlist transfer.

The input crosswalk stays immutable. This tool validates it, binds the review
packet to the exact input bytes with SHA-256, and leaves every decision null.
It never writes Serato, Rekordbox, audio, SQLite, or XML files. Output is JSON
on stdout only.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

TOOL_VERSION = "1.0.0"
REVIEW_SCHEMA_VERSION = 1

BASE_DECISIONS = [
    "CREATE_NEW_REKORDBOX_PLAYLIST",
    "MAP_TO_EXISTING_PLAYLIST",
    "KEEP_UNRESOLVED",
    "SKIP",
]
PARTIAL_DECISION = "CREATE_PARTIAL_REKORDBOX_PLAYLIST"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _read_json(path: Path) -> tuple[Path, bytes, dict[str, Any]]:
    requested = path.expanduser().absolute()
    if requested.is_symlink():
        raise RuntimeError(f"crosswalk must not be a symlink: {requested}")
    resolved = requested.resolve()
    if not resolved.is_file():
        raise RuntimeError(f"crosswalk must be a regular file: {resolved}")
    before = resolved.stat(follow_symlinks=False)
    raw = resolved.read_bytes()
    after = resolved.stat(follow_symlinks=False)
    before_id = (before.st_size, before.st_mtime_ns, before.st_dev, before.st_ino)
    after_id = (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino)
    if before_id != after_id or len(raw) != after.st_size:
        raise RuntimeError("crosswalk changed while being read")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("crosswalk is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("crosswalk must be a JSON object")
    return resolved, raw, value


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be an object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RuntimeError(f"{label} must be an array")
    return value


def validate_crosswalk(crosswalk: dict[str, Any]) -> list[dict[str, Any]]:
    if crosswalk.get("tool") != "serato_rekordbox_crosswalk":
        raise RuntimeError("input is not a Serato ↔ Rekordbox crosswalk")
    if crosswalk.get("crosswalk_schema_version") != 1:
        raise RuntimeError("unsupported crosswalk schema")

    safety = _require_dict(crosswalk.get("safety"), "crosswalk.safety")
    required_false = (
        "writes_serato",
        "writes_rekordbox",
        "writes_audio",
        "writes_library_index",
        "automatic_mapping",
        "automatic_playlist_creation",
    )
    for key in required_false:
        if safety.get(key) is not False:
            raise RuntimeError(f"unsafe crosswalk flag {key!r}")

    serato = _require_dict(crosswalk.get("serato"), "crosswalk.serato")
    rekordbox = _require_dict(crosswalk.get("rekordbox"), "crosswalk.rekordbox")
    library = _require_dict(crosswalk.get("library"), "crosswalk.library")
    for container, key in (
        (serato, "import_id"),
        (rekordbox, "import_id"),
        (library, "scan_id"),
    ):
        if not isinstance(container.get(key), int):
            raise RuntimeError(f"crosswalk is missing integer {key!r}")

    crates = _require_list(crosswalk.get("crates"), "crosswalk.crates")
    names: set[str] = set()
    for raw in crates:
        crate = _require_dict(raw, "crosswalk crate")
        name = crate.get("crate_name")
        if not isinstance(name, str) or not name.strip():
            raise RuntimeError("crosswalk crate is missing crate_name")
        if name in names:
            raise RuntimeError(f"duplicate crate name in crosswalk: {name!r}")
        names.add(name)
        entries = _require_list(crate.get("ordered_entries"), f"crate {name} ordered_entries")
        if crate.get("entry_count") != len(entries):
            raise RuntimeError(f"crate {name!r} entry count mismatch")
        if [entry.get("ordinal") for entry in entries if isinstance(entry, dict)] != list(
            range(len(entries))
        ):
            raise RuntimeError(f"crate {name!r} order is malformed")
        unresolved = [
            entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("state") != "RESOLVED"
        ]
        if crate.get("unresolved_count") != len(unresolved):
            raise RuntimeError(f"crate {name!r} unresolved count mismatch")
        plan = _require_dict(crate.get("transfer_plan"), f"crate {name} transfer_plan")
        if plan.get("decision") is not None or plan.get("automatic_decision") is not False:
            raise RuntimeError(f"crosswalk crate {name!r} already contains a decision")

    summary = _require_dict(crosswalk.get("summary"), "crosswalk.summary")
    if summary.get("serato_crates") != len(crates):
        raise RuntimeError("crosswalk summary crate count mismatch")
    if summary.get("automatic_mappings") != 0:
        raise RuntimeError("crosswalk claims automatic mappings")
    return crates


def build_review_packet(
    crosswalk: dict[str, Any], *, source_crosswalk_sha256: str
) -> dict[str, Any]:
    crates = validate_crosswalk(crosswalk)
    plans: list[dict[str, Any]] = []
    unresolved_crates = 0
    for crate in crates:
        unresolved_count = int(crate["unresolved_count"])
        if unresolved_count:
            unresolved_crates += 1
        decisions = list(BASE_DECISIONS)
        if unresolved_count:
            decisions.insert(1, PARTIAL_DECISION)
        plans.append(
            {
                "crate_name": crate["crate_name"],
                "mapping_state": crate["mapping_state"],
                "entry_count": int(crate["entry_count"]),
                "resolved_entry_count": int(crate["resolved_entry_count"]),
                "unresolved_count": unresolved_count,
                "candidate_playlists": crate["candidate_playlists"],
                "decision": None,
                "automatic_decision": False,
                "allowed_decisions": decisions,
                "output_playlist_name": crate["crate_name"],
                "selected_rekordbox_node_key": None,
                "selected_rekordbox_node_path": None,
                "decision_evidence": [],
            }
        )

    return {
        "tool": "serato_rekordbox_transfer_review",
        "tool_version": TOOL_VERSION,
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "safety": {
            "classification": SAFETY_CLASSIFICATION,
            "writes_serato": False,
            "writes_rekordbox": False,
            "writes_audio": False,
            "writes_library_index": False,
            "writes_xml": False,
            "automatic_decision": False,
        },
        "source": {
            "crosswalk_sha256": source_crosswalk_sha256,
            "library_scan_id": crosswalk["library"]["scan_id"],
            "serato_import_id": crosswalk["serato"]["import_id"],
            "rekordbox_import_id": crosswalk["rekordbox"]["import_id"],
        },
        "summary": {
            "crates": len(plans),
            "crates_with_unresolved_entries": unresolved_crates,
            "decisions_made": 0,
            "automatic_decisions": 0,
        },
        "plans": plans,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crosswalk", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        _path, raw, crosswalk = _read_json(args.crosswalk)
        result = build_review_packet(
            crosswalk, source_crosswalk_sha256=_sha256(raw)
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
