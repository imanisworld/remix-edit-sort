#!/usr/bin/env python3
"""Build a fail-closed review packet for unresolved Rekordbox relocation records.

Consumes JSON emitted by ``rekordbox_relocation_preview.py`` and emits a
read-only review packet on stdout. It does not approve, apply, move, delete,
retag, or rewrite anything.
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
EXPECTED_PREVIEW_TOOL = "rekordbox_relocation_preview"
EXPECTED_PREVIEW_SCHEMA_VERSION = 1

STATUS_UNCHANGED = "UNCHANGED"
STATUS_STRONG = "STRONG_CANDIDATE"
STATUS_AMBIGUOUS = "AMBIGUOUS"
STATUS_CHANGED = "CHANGED"
STATUS_MISSING = "MISSING"
ALL_STATUSES = (
    STATUS_UNCHANGED,
    STATUS_STRONG,
    STATUS_AMBIGUOUS,
    STATUS_CHANGED,
    STATUS_MISSING,
)
UNRESOLVED_STATUSES = (
    STATUS_AMBIGUOUS,
    STATUS_CHANGED,
    STATUS_MISSING,
)

ALLOWED_DECISIONS: dict[str, tuple[str, ...]] = {
    STATUS_AMBIGUOUS: (
        "APPROVE_CURRENT_FILE",
        "KEEP_UNRESOLVED",
        "MARK_STALE_REKORDBOX_RECORD",
        "MARK_INTENTIONAL_VERSION_CONFLICT",
    ),
    STATUS_CHANGED: (
        "APPROVE_CHANGED_CURRENT_FILE",
        "KEEP_UNRESOLVED",
        "MARK_STALE_REKORDBOX_RECORD",
        "MARK_INTENTIONAL_VERSION_CHANGE",
    ),
    STATUS_MISSING: (
        "KEEP_UNRESOLVED",
        "MARK_STALE_REKORDBOX_RECORD",
        "MARK_GENUINELY_ABSENT",
        "REQUIRES_EXTERNAL_SEARCH",
    ),
}


def _require_dict(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be an object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RuntimeError(f"{label} must be an array")
    return value


def _require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise RuntimeError(f"{label} must be boolean")
    return value


def _load_preview(path: Path) -> tuple[bytes, dict[str, Any]]:
    requested = path.expanduser().absolute()
    if requested.is_symlink():
        raise RuntimeError(f"preview input must not be a symlink: {requested}")
    resolved = requested.resolve()
    if not resolved.is_file():
        raise RuntimeError(f"preview input must be a regular file: {resolved}")
    raw = resolved.read_bytes()
    try:
        preview = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("preview input is not valid UTF-8 JSON") from exc
    return raw, _require_dict(preview, "preview")


def _validate_preview_identity(preview: dict[str, Any]) -> None:
    if preview.get("tool") != EXPECTED_PREVIEW_TOOL:
        raise RuntimeError(
            f"expected tool {EXPECTED_PREVIEW_TOOL!r}, got {preview.get('tool')!r}"
        )
    if preview.get("preview_schema_version") != EXPECTED_PREVIEW_SCHEMA_VERSION:
        raise RuntimeError(
            "unsupported Rekordbox relocation preview schema version"
        )

    safety = _require_dict(preview.get("safety"), "preview.safety")
    if safety.get("classification") != "read-only":
        raise RuntimeError("preview safety classification is not read-only")
    for key in (
        "writes_rekordbox",
        "writes_serato",
        "writes_audio",
        "writes_library_index",
        "automatic_approval",
    ):
        value = _require_bool(safety.get(key), f"preview.safety.{key}")
        if value:
            raise RuntimeError(f"unsafe preview flag set: {key}")


def _validate_track(track: dict[str, Any], index: int) -> None:
    prefix = f"preview.tracks[{index}]"
    track_id = track.get("rekordbox_track_id")
    if track_id in (None, ""):
        raise RuntimeError(f"{prefix}.rekordbox_track_id is required")
    status = track.get("status")
    if status not in ALL_STATUSES:
        raise RuntimeError(f"{prefix}.status is invalid: {status!r}")

    bridge_eligible = _require_bool(
        track.get("bridge_eligible"), f"{prefix}.bridge_eligible"
    )
    review_required = _require_bool(
        track.get("review_required"), f"{prefix}.review_required"
    )
    _require_list(track.get("top_candidates"), f"{prefix}.top_candidates")

    if status in UNRESOLVED_STATUSES:
        if bridge_eligible:
            raise RuntimeError(
                f"{prefix} is unresolved but bridge_eligible=true"
            )
        if not review_required:
            raise RuntimeError(
                f"{prefix} is unresolved but review_required=false"
            )
    else:
        if review_required:
            raise RuntimeError(
                f"{prefix} is resolved but review_required=true"
            )
        if not bridge_eligible:
            raise RuntimeError(
                f"{prefix} is resolved but bridge_eligible=false"
            )

    proposed_file_id = track.get("proposed_file_id")
    proposed_path = track.get("proposed_path")
    if status == STATUS_MISSING:
        if proposed_file_id is not None or proposed_path is not None:
            raise RuntimeError(
                f"{prefix} is MISSING but still has a proposed target"
            )


def _recomputed_summary(tracks: list[dict[str, Any]]) -> dict[str, int]:
    counts = {status: 0 for status in ALL_STATUSES}
    for track in tracks:
        counts[track["status"]] += 1
    return {
        "total": len(tracks),
        "unchanged": counts[STATUS_UNCHANGED],
        "strong_candidate": counts[STATUS_STRONG],
        "ambiguous": counts[STATUS_AMBIGUOUS],
        "changed": counts[STATUS_CHANGED],
        "missing": counts[STATUS_MISSING],
        "bridge_eligible": sum(bool(t["bridge_eligible"]) for t in tracks),
        "review_required": sum(bool(t["review_required"]) for t in tracks),
    }


def _validate_summary(
    preview: dict[str, Any], tracks: list[dict[str, Any]]
) -> dict[str, int]:
    summary = _require_dict(preview.get("summary"), "preview.summary")
    expected = _recomputed_summary(tracks)
    for key, value in expected.items():
        if summary.get(key) != value:
            raise RuntimeError(
                f"preview summary mismatch for {key}: "
                f"declared={summary.get(key)!r} recomputed={value!r}"
            )
    return expected


def validate_preview(preview: dict[str, Any]) -> list[dict[str, Any]]:
    _validate_preview_identity(preview)
    raw_tracks = _require_list(preview.get("tracks"), "preview.tracks")
    tracks: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for index, value in enumerate(raw_tracks):
        track = _require_dict(value, f"preview.tracks[{index}]")
        _validate_track(track, index)
        track_id = str(track["rekordbox_track_id"])
        if track_id in seen_ids:
            raise RuntimeError(
                f"duplicate Rekordbox track id in preview: {track_id}"
            )
        seen_ids.add(track_id)
        tracks.append(track)

    _validate_summary(preview, tracks)
    return tracks


def _candidate_signal_summary(candidates: list[Any]) -> dict[str, Any]:
    candidate_dicts = [
        candidate for candidate in candidates if isinstance(candidate, dict)
    ]
    scores = [
        candidate.get("score")
        for candidate in candidate_dicts
        if isinstance(candidate.get("score"), (int, float))
    ]
    top_score_gap: float | int | None = None
    if len(scores) >= 2:
        top_score_gap = scores[0] - scores[1]

    evidence: set[str] = set()
    conflicts: set[str] = set()
    differences: set[str] = set()
    material_change = False
    for candidate in candidate_dicts:
        for item in candidate.get("evidence", []):
            if isinstance(item, str):
                evidence.add(item)
        for item in candidate.get("conflicts", []):
            if isinstance(item, str):
                conflicts.add(item)
        for item in candidate.get("differences", []):
            if isinstance(item, str):
                differences.add(item)
        if candidate.get("material_change") is True:
            material_change = True

    return {
        "candidate_count": len(candidate_dicts),
        "top_score_gap": top_score_gap,
        "has_sha256_move_lineage": bool(
            {"sha256_move_lineage", "historical_sha256_move_lineage"} & evidence
        ),
        "has_exact_serato_path_link": "exact_serato_path_link" in evidence,
        "has_current_exact_serato_reference": (
            "current_file_has_exact_serato_reference" in evidence
        ),
        "material_change_present": material_change,
        "evidence_terms": sorted(evidence),
        "conflict_terms": sorted(conflicts),
        "difference_terms": sorted(differences),
    }


def _build_review_item(track: dict[str, Any]) -> dict[str, Any]:
    status = track["status"]
    candidates = track["top_candidates"]
    return {
        "review_id": f"rekordbox:{track['rekordbox_track_id']}",
        "rekordbox_track_id": track["rekordbox_track_id"],
        "status": status,
        "old_path": track.get("old_path"),
        "name": track.get("name"),
        "artist": track.get("artist"),
        "proposed_file_id": track.get("proposed_file_id"),
        "proposed_path": track.get("proposed_path"),
        "candidate_signals": _candidate_signal_summary(candidates),
        "top_candidates": candidates,
        "allowed_decisions": list(ALLOWED_DECISIONS[status]),
        "decision": None,
        "decision_evidence": [],
        "automatic_decision": False,
    }


def build_review_packet(
    preview: dict[str, Any], *, source_preview_sha256: str
) -> dict[str, Any]:
    tracks = validate_preview(preview)

    queues: dict[str, list[dict[str, Any]]] = {
        "ambiguous": [],
        "changed": [],
        "missing": [],
    }
    key_for_status = {
        STATUS_AMBIGUOUS: "ambiguous",
        STATUS_CHANGED: "changed",
        STATUS_MISSING: "missing",
    }

    for track in tracks:
        status = track["status"]
        if status not in UNRESOLVED_STATUSES:
            continue
        queues[key_for_status[status]].append(_build_review_item(track))

    unresolved_total = sum(len(queue) for queue in queues.values())
    declared_review_required = int(preview["summary"]["review_required"])
    if unresolved_total != declared_review_required:
        raise RuntimeError(
            "review queue count does not equal preview review_required count"
        )

    return {
        "tool": "rekordbox_unresolved_review",
        "tool_version": TOOL_VERSION,
        "review_schema_version": REVIEW_SCHEMA_VERSION,
        "safety": {
            "classification": SAFETY_CLASSIFICATION,
            "input": "existing relocation preview JSON",
            "output": "stdout only",
            "writes_rekordbox": False,
            "writes_serato": False,
            "writes_audio": False,
            "writes_library_index": False,
            "automatic_approval": False,
            "automatic_resolution": False,
        },
        "source": {
            "preview_tool": preview["tool"],
            "preview_tool_version": preview.get("tool_version"),
            "preview_schema_version": preview["preview_schema_version"],
            "preview_sha256": source_preview_sha256,
            "library": preview.get("library"),
            "rekordbox": preview.get("rekordbox"),
            "serato": preview.get("serato"),
        },
        "summary": {
            "total_preview_tracks": int(preview["summary"]["total"]),
            "already_resolved": (
                int(preview["summary"]["unchanged"])
                + int(preview["summary"]["strong_candidate"])
            ),
            "unresolved_total": unresolved_total,
            "ambiguous": len(queues["ambiguous"]),
            "changed": len(queues["changed"]),
            "missing": len(queues["missing"]),
            "automatic_decisions": 0,
        },
        "queues": queues,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preview",
        type=Path,
        required=True,
        help="JSON emitted by rekordbox_relocation_preview.py",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        raw, preview = _load_preview(args.preview)
        result = build_review_packet(
            preview,
            source_preview_sha256=hashlib.sha256(raw).hexdigest(),
        )
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
