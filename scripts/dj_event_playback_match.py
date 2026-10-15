#!/usr/bin/env python3
"""Offline candidate join of verified DJ sets to exact indexed tracks.

Reuses ``dj_event_time`` for set windows, IANA zones, and DST. A candidate
also needs a verified source artifact and one whole-file SHA-256 that matches
exactly one library ``file_id``. Titles, filenames, and a claimed identity
status never select a track. Ambiguous rows stay unresolved. This does not
parse Serato or Rekordbox exports and it does not prove a performance.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import argparse
import json
from pathlib import Path

import dj_event_time


class MatchEvidenceError(dj_event_time.ClockEvidenceError):
    """The synthetic library or provenance fixture cannot be used."""


def _sha256(value: object) -> str | None:
    if not isinstance(value, str) or len(value) != 64:
        return None
    if any(character not in "0123456789abcdefABCDEF" for character in value):
        return None
    return value.lower()


def _library_index(library: object) -> tuple[dict[int, dict], dict[str, list[int]]]:
    if not isinstance(library, dict) or not isinstance(library.get("files"), list):
        raise MatchEvidenceError("library fixture must be an object with a files array")
    by_id: dict[int, dict] = {}
    by_hash: dict[str, list[int]] = {}
    for item in library["files"]:
        if not isinstance(item, dict):
            raise MatchEvidenceError("library file must be an object")
        file_id = item.get("file_id")
        digest = _sha256(item.get("sha256"))
        if type(file_id) is not int or file_id < 1 or digest is None:
            raise MatchEvidenceError("library file needs a positive file_id and sha256")
        if file_id in by_id:
            raise MatchEvidenceError(f"duplicate library file_id: {file_id}")
        by_id[file_id] = item
        by_hash.setdefault(digest, []).append(file_id)
    return by_id, by_hash


def _provenance(provenance: object) -> list[dict]:
    if not isinstance(provenance, dict) or not isinstance(provenance.get("artifacts"), list):
        raise MatchEvidenceError("provenance fixture must be an object with an artifacts array")
    artifacts = []
    for item in provenance["artifacts"]:
        if not isinstance(item, dict):
            raise MatchEvidenceError("provenance artifact must be an object")
        digest = _sha256(item.get("sha256"))
        session_id = item.get("session_id")
        source = item.get("source")
        if digest is None or not isinstance(session_id, str) or not session_id.strip():
            raise MatchEvidenceError("provenance artifact needs sha256 and session_id")
        if not isinstance(source, str):
            raise MatchEvidenceError("provenance artifact needs a source string")
        if type(item.get("verified")) is not bool:
            raise MatchEvidenceError("provenance verified must be a boolean")
        artifacts.append({
            "sha256": digest,
            "session_id": session_id,
            "source": source.strip(),
            "verified": item["verified"],
        })
    return artifacts


def _identity_reason(entry: dict, by_id: dict[int, dict], by_hash: dict[str, list[int]], artifacts: list[dict]) -> str | None:
    digest = _sha256(entry.get("track_sha256"))
    if digest is None:
        return "exact whole-file track sha256 required"
    matches = by_hash.get(digest, [])
    if len(matches) > 1:
        return "sha256 matches multiple indexed files"
    if len(matches) != 1:
        return "track sha256 is not an indexed file"
    file_id = matches[0]
    if entry.get("file_id") != file_id or file_id not in by_id:
        return "indexed file_id does not match track sha256"
    source_digest = _sha256(entry.get("source_artifact_sha256"))
    session_id = entry.get("session_id")
    agreed = [
        artifact for artifact in artifacts
        if artifact["sha256"] == source_digest and artifact["session_id"] == session_id
    ]
    if len(agreed) != 1:
        return "source artifact is missing or ambiguous"
    artifact = agreed[0]
    if artifact["verified"] is not True or not artifact["source"]:
        return "source artifact is not a verified export"
    return None


def preview(events: list[dict], plays: list[dict], library: dict, provenance: dict) -> dict:
    """Return candidate associations. Anything uncertain stays ``REVIEW``."""
    by_id, by_hash = _library_index(library)
    artifacts = _provenance(provenance)
    temporal = dj_event_time.preview(events, plays)
    rows = []
    for entry, temporal_row in zip(plays, temporal["plays"], strict=True):
        row = dict(temporal_row)
        row["track_sha256"] = None
        if temporal_row["state"] != "CANDIDATE_ONLY":
            row["file_id"] = None
            rows.append(row)
            continue
        reason = _identity_reason(entry, by_id, by_hash, artifacts)
        if reason is not None:
            row.update({
                "state": "REVIEW",
                "file_id": None,
                "reason": reason,
            })
        else:
            row.update({
                "state": "CANDIDATE_ONLY",
                "file_id": entry["file_id"],
                "track_sha256": _sha256(entry.get("track_sha256")),
                "reason": (
                    "temporal window plus exact sha256 and verified source artifact; "
                    "independent review required"
                ),
            })
        rows.append(row)
    return {
        "mode": "preview-only",
        "proof_of_play": False,
        "identity": "sha256-whole-file",
        "parser": "none",
        "events_requiring_review": temporal["events_requiring_review"],
        "verified_set_count": temporal["verified_set_count"],
        "plays": rows,
    }


def _load(path: Path, label: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MatchEvidenceError(f"{label} fixture is not readable JSON: {path}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--plays", type=Path, required=True)
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        outcome = preview(
            _load(args.events, "events"),
            _load(args.plays, "plays"),
            _load(args.library, "library"),
            _load(args.provenance, "provenance"),
        )
    except (MatchEvidenceError, dj_event_time.ClockEvidenceError, ValueError) as exc:
        parser.exit(2, f"REFUSED: {exc}\n")
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
