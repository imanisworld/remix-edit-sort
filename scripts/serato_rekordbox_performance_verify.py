#!/usr/bin/env python3
"""Read-only Serato -> Rekordbox performance-metadata verifier.

For each Rekordbox XML TRACK, this tool opens the exact audio file named by the
TRACK Location, reads any embedded Serato Markers2 / BeatGrid metadata, and
checks that every representable Serato cue, loop, and beat-grid anchor exists
in the Rekordbox XML representation.

Identity is exact current file location only. Filenames, duration, artist/title
metadata, and fuzzy matching never link tracks. Extra Rekordbox cues/loops/grid
anchors are allowed; the requirement is that Serato source data survives.

Unsupported Serato-only concepts (track colour, BPM lock, loop lock state, and
unknown Markers2 entries) are reported but never silently treated as verified.
No audio, Serato, Rekordbox, SQLite, or other files are written.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Iterable

import rekordbox_index
import serato_performance_metadata as spm

TOOL_VERSION = "1.0.0"
VERIFY_SCHEMA_VERSION = 1
POSITION_TOLERANCE_MS = 2.0
BPM_TOLERANCE = 0.02


class PerformanceVerifyError(RuntimeError):
    pass


def _finite_float(value: Any, *, context: str) -> float:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError) as exc:
        raise PerformanceVerifyError(f"invalid numeric value in {context}: {value!r}") from exc
    if not math.isfinite(parsed):
        raise PerformanceVerifyError(f"non-finite numeric value in {context}: {value!r}")
    return parsed


def _int_value(value: Any, *, context: str) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise PerformanceVerifyError(f"invalid integer in {context}: {value!r}") from exc


def _rgb(mark: dict[str, str], *, context: str) -> list[int] | None:
    keys = ("Red", "Green", "Blue")
    present = [key in mark and mark.get(key) not in (None, "") for key in keys]
    if not any(present):
        return None
    if not all(present):
        raise PerformanceVerifyError(f"partial RGB colour in {context}")
    values = [_int_value(mark[key], context=f"{context}.{key}") for key in keys]
    if any(value < 0 or value > 255 for value in values):
        raise PerformanceVerifyError(f"RGB colour outside 0..255 in {context}")
    return values


def _mark_projection(mark: dict[str, str], *, ordinal: int) -> dict[str, Any]:
    context = f"POSITION_MARK[{ordinal}]"
    mark_type = str(mark.get("Type", ""))
    if mark_type not in {"0", "4"}:
        return {"type": mark_type, "ordinal": ordinal, "raw": mark}
    start_ms = _finite_float(mark.get("Start"), context=f"{context}.Start") * 1000.0
    end_ms = None
    if mark_type == "4":
        end_raw = mark.get("End")
        if end_raw in (None, ""):
            raise PerformanceVerifyError(f"loop {context} has no End")
        end_ms = _finite_float(end_raw, context=f"{context}.End") * 1000.0
    num = _int_value(mark.get("Num"), context=f"{context}.Num")
    return {
        "type": mark_type,
        "ordinal": ordinal,
        "name": str(mark.get("Name") or ""),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "num": num,
        "color": _rgb(mark, context=context),
    }


def _tempo_projection(tempo: dict[str, str], *, ordinal: int) -> dict[str, Any]:
    context = f"TEMPO[{ordinal}]"
    return {
        "ordinal": ordinal,
        "position_ms": _finite_float(tempo.get("Inizio"), context=f"{context}.Inizio") * 1000.0,
        "bpm": _finite_float(tempo.get("Bpm"), context=f"{context}.Bpm"),
    }


def _close(left: float, right: float, tolerance: float) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def _compare_mark(
    source: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    kind: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    expected_type = "0" if kind == "cue" else "4"
    source_index = int(source["index"])
    matching = [
        mark for mark in candidates
        if mark.get("type") == expected_type and mark.get("num") == source_index
    ]
    if not matching:
        return None, [{
            "kind": f"SERATO_{kind.upper()}_MISSING",
            "index": source_index,
            "source": source,
        }]
    if len(matching) > 1:
        return None, [{
            "kind": f"SERATO_{kind.upper()}_AMBIGUOUS",
            "index": source_index,
            "candidate_ordinals": [mark["ordinal"] for mark in matching],
        }]

    target = matching[0]
    findings: list[dict[str, Any]] = []
    source_start = float(source["position_ms"] if kind == "cue" else source["start_ms"])
    if not _close(source_start, target["start_ms"], POSITION_TOLERANCE_MS):
        findings.append({
            "kind": f"SERATO_{kind.upper()}_POSITION_MISMATCH",
            "index": source_index,
            "source_ms": source_start,
            "rekordbox_ms": target["start_ms"],
        })
    if kind == "loop":
        source_end = float(source["end_ms"])
        if target["end_ms"] is None or not _close(
            source_end, target["end_ms"], POSITION_TOLERANCE_MS
        ):
            findings.append({
                "kind": "SERATO_LOOP_END_MISMATCH",
                "index": source_index,
                "source_ms": source_end,
                "rekordbox_ms": target["end_ms"],
            })
    if str(source.get("name") or "") != target["name"]:
        findings.append({
            "kind": f"SERATO_{kind.upper()}_NAME_MISMATCH",
            "index": source_index,
            "source": str(source.get("name") or ""),
            "rekordbox": target["name"],
        })
    source_color = source.get("color")
    if source_color is not None and list(source_color) != target.get("color"):
        findings.append({
            "kind": f"SERATO_{kind.upper()}_COLOR_MISMATCH",
            "index": source_index,
            "source": list(source_color),
            "rekordbox": target.get("color"),
        })
    return target, findings


def _compare_grid(
    source_anchors: list[dict[str, Any]],
    target_tempos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    unused = set(range(len(target_tempos)))
    for source_ordinal, anchor in enumerate(source_anchors):
        source_position = float(anchor["position_ms"])
        position_matches = [
            index for index in unused
            if _close(source_position, target_tempos[index]["position_ms"], POSITION_TOLERANCE_MS)
        ]
        if not position_matches:
            findings.append({
                "kind": "SERATO_BEATGRID_ANCHOR_MISSING",
                "source_ordinal": source_ordinal,
                "source_position_ms": source_position,
                "source_bpm": float(anchor["bpm"]),
            })
            continue
        best = min(
            position_matches,
            key=lambda index: abs(target_tempos[index]["position_ms"] - source_position),
        )
        unused = unused - {best}
        target = target_tempos[best]
        if not _close(float(anchor["bpm"]), target["bpm"], BPM_TOLERANCE):
            findings.append({
                "kind": "SERATO_BEATGRID_BPM_MISMATCH",
                "source_ordinal": source_ordinal,
                "source_position_ms": source_position,
                "source_bpm": float(anchor["bpm"]),
                "rekordbox_ordinal": target["ordinal"],
                "rekordbox_bpm": target["bpm"],
            })
    return findings


def verify_track(track: dict[str, Any]) -> dict[str, Any]:
    path = Path(track["absolute_path"])
    base = {
        "path": str(path),
        "rekordbox_track_id": str(track.get("track_id") or ""),
    }
    try:
        source = spm.inspect_file(path)
    except Exception as exc:  # mutagen formats expose several exception classes
        return {
            **base,
            "status": "FAIL",
            "compared_items": 0,
            "findings": [{"kind": "SERATO_SOURCE_READ_ERROR", "error": str(exc)}],
            "unsupported_serato_fields": [],
        }

    markers2 = source.get("markers2")
    beatgrid = source.get("beatgrid")
    if markers2 is None and beatgrid is None:
        return {
            **base,
            "status": "NO_DATA",
            "compared_items": 0,
            "findings": [],
            "unsupported_serato_fields": [],
        }

    findings: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    try:
        marks = [_mark_projection(mark, ordinal=index) for index, mark in enumerate(track["marks"])]
        tempos = [_tempo_projection(tempo, ordinal=index) for index, tempo in enumerate(track["tempos"])]
    except PerformanceVerifyError as exc:
        return {
            **base,
            "status": "FAIL",
            "compared_items": 0,
            "findings": [{"kind": "REKORDBOX_PERFORMANCE_PARSE_ERROR", "error": str(exc)}],
            "unsupported_serato_fields": [],
        }

    compared = 0
    if markers2 is not None:
        if markers2.get("track_color") is not None:
            unsupported.append({"field": "track_color", "value": markers2["track_color"]})
        if markers2.get("bpm_lock"):
            unsupported.append({"field": "bpm_lock", "value": True})
        for unknown in markers2.get("unknown_entries", []):
            unsupported.append({"field": "unknown_markers2_entry", "value": unknown})

        for cue in markers2.get("cues", []):
            compared += 1
            _target, item_findings = _compare_mark(cue, marks, kind="cue")
            findings.extend(item_findings)
        for loop in markers2.get("loops", []):
            compared += 1
            _target, item_findings = _compare_mark(loop, marks, kind="loop")
            findings.extend(item_findings)
            if loop.get("locked"):
                unsupported.append({
                    "field": "loop_locked",
                    "value": True,
                    "loop_index": int(loop["index"]),
                })

    if beatgrid is not None:
        anchors = list(beatgrid.get("anchors") or [])
        compared += len(anchors)
        findings.extend(_compare_grid(anchors, tempos))

    status = "FAIL" if findings else ("PASS" if compared else "NO_DATA")
    return {
        **base,
        "status": status,
        "compared_items": compared,
        "source_counts": {
            "cues": len(markers2.get("cues", [])) if markers2 else 0,
            "loops": len(markers2.get("loops", [])) if markers2 else 0,
            "beatgrid_anchors": len(beatgrid.get("anchors", [])) if beatgrid else 0,
        },
        "rekordbox_counts": {
            "position_marks": len(track["marks"]),
            "tempos": len(track["tempos"]),
        },
        "findings": findings,
        "unsupported_serato_fields": unsupported,
    }


def verify_tracks(tracks: Iterable[dict[str, Any]]) -> dict[str, Any]:
    results = [verify_track(track) for track in tracks]
    fail_tracks = sum(result["status"] == "FAIL" for result in results)
    pass_tracks = sum(result["status"] == "PASS" for result in results)
    no_data_tracks = sum(result["status"] == "NO_DATA" for result in results)
    compared_items = sum(int(result["compared_items"]) for result in results)
    findings = sum(len(result["findings"]) for result in results)
    unsupported = sum(len(result["unsupported_serato_fields"]) for result in results)
    if fail_tracks:
        verdict = "FAIL"
    elif compared_items:
        verdict = "PASS"
    else:
        verdict = "NO_DATA"
    return {
        "tool": "serato_rekordbox_performance_verify",
        "tool_version": TOOL_VERSION,
        "verify_schema_version": VERIFY_SCHEMA_VERSION,
        "safety": {
            "classification": SAFETY_CLASSIFICATION,
            "writes_audio": False,
            "writes_serato": False,
            "writes_rekordbox": False,
            "writes_library_index": False,
            "writes_files": False,
            "track_identity": "exact Rekordbox Location -> same physical audio file",
            "fuzzy_matching": False,
        },
        "tolerances": {
            "position_ms": POSITION_TOLERANCE_MS,
            "bpm": BPM_TOLERANCE,
        },
        "verdict": verdict,
        "summary": {
            "tracks_checked": len(results),
            "tracks_pass": pass_tracks,
            "tracks_fail": fail_tracks,
            "tracks_no_serato_performance_data": no_data_tracks,
            "source_items_compared": compared_items,
            "fail_findings": findings,
            "unsupported_serato_fields_reported": unsupported,
        },
        "tracks": results,
    }


def _read_rekordbox_xml(path: Path) -> tuple[Path, Any]:
    requested = path.expanduser().absolute()
    if requested.is_symlink():
        raise PerformanceVerifyError(f"Rekordbox XML must not be a symlink: {requested}")
    resolved = requested.resolve()
    if not resolved.is_file():
        raise PerformanceVerifyError(f"Rekordbox XML must be a regular file: {resolved}")
    before = resolved.stat(follow_symlinks=False)
    payload = resolved.read_bytes()
    after = resolved.stat(follow_symlinks=False)
    before_id = (before.st_size, before.st_mtime_ns, before.st_dev, before.st_ino)
    after_id = (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino)
    if before_id != after_id or len(payload) != after.st_size:
        raise PerformanceVerifyError(f"Rekordbox XML changed while being read: {resolved}")
    return resolved, rekordbox_index._parse_export(payload)


def verify_xml(path: Path) -> dict[str, Any]:
    resolved, parsed = _read_rekordbox_xml(path)
    report = verify_tracks(parsed.tracks)
    report["input"] = {"rekordbox_xml": str(resolved)}
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rekordbox-xml", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = verify_xml(args.rekordbox_xml)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 2 if report["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
