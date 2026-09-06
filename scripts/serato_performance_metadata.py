#!/usr/bin/env python3
"""Read-only Serato cue/loop/beatgrid inspector.

This module reads Serato performance metadata embedded in supported audio files
without changing the files. It handles the formats already used by this project:
MP3/WAV/AIFF ID3 GEOB frames, M4A/MP4 freeform atoms, and FLAC Vorbis comments.

The binary layouts were independently cross-checked against the MIT-licensed
`tmahanjana/dj-sync` and `scanmydata/Dj-Library-migration-tool` implementations.
This implementation is intentionally fail-closed: malformed or truncated Serato
payloads raise instead of being silently repaired.

It never writes audio metadata, Serato data, Rekordbox data, or the library index.
The CLI prints JSON to stdout only.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import argparse
import base64
import json
import math
import os
import re
import struct
import sys
from pathlib import Path
from typing import Any

try:
    from mutagen.aiff import AIFF
    from mutagen.flac import FLAC
    from mutagen.id3 import ID3, ID3NoHeaderError
    from mutagen.mp4 import MP4
    from mutagen.wave import WAVE
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "mutagen is required. Install with: python3 -m pip install --user mutagen"
    ) from exc


TOOL_VERSION = "1.0.0"
MARKERS2_DESC = "Serato Markers2"
BEATGRID_DESC = "Serato BeatGrid"
SERATO_DESCRIPTIONS = (MARKERS2_DESC, BEATGRID_DESC)

ID3_EXTENSIONS = {".mp3", ".wav", ".aif", ".aiff"}
MP4_EXTENSIONS = {".m4a", ".mp4", ".m4v"}
FLAC_EXTENSIONS = {".flac"}
SUPPORTED_EXTENSIONS = ID3_EXTENSIONS | MP4_EXTENSIONS | FLAC_EXTENSIONS

MP4_ATOMS = {
    MARKERS2_DESC: "----:com.serato.dj:markersv2",
    BEATGRID_DESC: "----:com.serato.dj:beatgrid",
}
FLAC_FIELDS = {
    MARKERS2_DESC: "SERATO_MARKERS_V2",
    BEATGRID_DESC: "SERATO_BEATGRID",
}
NON_ID3_WRAPPER_PREFIX = b"application/octet-stream\x00\x00"

MARKERS2_HEADER = b"\x01\x01"
BEATGRID_HEADER = b"\x01\x00"


class SeratoPerformanceError(ValueError):
    """Raised when Serato performance metadata is structurally invalid."""


def _decode_utf8_null_terminated(data: bytes, *, context: str) -> str:
    terminator = data.find(b"\x00")
    if terminator < 0:
        raise SeratoPerformanceError(f"missing NUL terminator in {context}")
    try:
        return data[:terminator].decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SeratoPerformanceError(f"invalid UTF-8 in {context}") from exc


def _clean_base64(value: bytes, *, context: str) -> bytes:
    cleaned = re.sub(rb"\s", b"", value)
    if not cleaned:
        raise SeratoPerformanceError(f"empty base64 payload in {context}")
    # A valid unpadded base64 string cannot have length % 4 == 1. Do not
    # synthesize data to repair that state: it is indistinguishable from
    # truncation and this project intentionally fails closed.
    if len(cleaned) % 4 == 1:
        raise SeratoPerformanceError(f"invalid base64 length in {context}")
    return cleaned + b"=" * (-len(cleaned) % 4)


def _decode_base64(value: bytes, *, context: str) -> bytes:
    padded = _clean_base64(value, context=context)
    try:
        return base64.b64decode(padded, validate=True)
    except Exception as exc:
        raise SeratoPerformanceError(f"invalid base64 in {context}") from exc


def unwrap_non_id3_field(value: bytes, *, expected_description: str) -> bytes:
    """Decode Serato's MP4/FLAC wrapper and return the inner GEOB body."""

    raw = _decode_base64(value, context=f"{expected_description} wrapper")
    if not raw.startswith(NON_ID3_WRAPPER_PREFIX):
        raise SeratoPerformanceError(
            f"{expected_description} wrapper is missing application/octet-stream prefix"
        )
    remainder = raw[len(NON_ID3_WRAPPER_PREFIX) :]
    separator = remainder.find(b"\x00")
    if separator < 0:
        raise SeratoPerformanceError(
            f"{expected_description} wrapper is missing field-name terminator"
        )
    try:
        actual = remainder[:separator].decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        raise SeratoPerformanceError(
            f"{expected_description} wrapper has non-ASCII field name"
        ) from exc
    if actual != expected_description:
        raise SeratoPerformanceError(
            f"Serato wrapper field mismatch: expected {expected_description!r}, got {actual!r}"
        )
    return remainder[separator + 1 :]


def _read_markers2_entries(payload: bytes) -> list[tuple[str, bytes]]:
    if not payload.startswith(MARKERS2_HEADER):
        raise SeratoPerformanceError("Markers2 inner payload is missing 01 01 header")

    entries: list[tuple[str, bytes]] = []
    offset = len(MARKERS2_HEADER)
    while offset < len(payload):
        name_end = payload.find(b"\x00", offset)
        if name_end < 0:
            raise SeratoPerformanceError("Markers2 entry name is not NUL-terminated")
        raw_name = payload[offset:name_end]
        offset = name_end + 1
        if not raw_name:
            # Empty name is the format terminator. Only NUL padding may follow.
            if any(payload[offset:]):
                raise SeratoPerformanceError("Markers2 has nonzero bytes after terminator")
            return entries
        try:
            name = raw_name.decode("ascii", errors="strict")
        except UnicodeDecodeError as exc:
            raise SeratoPerformanceError("Markers2 entry name is not ASCII") from exc
        if offset + 4 > len(payload):
            raise SeratoPerformanceError(f"Markers2 {name} entry is missing length")
        length = struct.unpack(">I", payload[offset : offset + 4])[0]
        offset += 4
        end = offset + length
        if end > len(payload):
            raise SeratoPerformanceError(
                f"Markers2 {name} entry declares {length} bytes beyond payload"
            )
        entries.append((name, payload[offset:end]))
        offset = end

    raise SeratoPerformanceError("Markers2 payload is missing terminator")


def decode_markers2(body: bytes) -> dict[str, Any]:
    """Decode a complete `Serato Markers2` GEOB body."""

    if not body.startswith(MARKERS2_HEADER):
        raise SeratoPerformanceError("Markers2 body is missing 01 01 header")

    encoded = body[len(MARKERS2_HEADER) :].split(b"\x00", 1)[0]
    payload = _decode_base64(encoded, context="Markers2 body")
    entries = _read_markers2_entries(payload)

    track_color: list[int] | None = None
    cues: list[dict[str, Any]] = []
    loops: list[dict[str, Any]] = []
    bpm_lock = False
    unknown_entries: list[dict[str, Any]] = []

    for name, data in entries:
        if name == "COLOR":
            if len(data) < 4:
                raise SeratoPerformanceError("Markers2 COLOR entry is truncated")
            track_color = [data[1], data[2], data[3]]
            continue

        if name == "CUE":
            if len(data) < 13:
                raise SeratoPerformanceError("Markers2 CUE entry is truncated")
            position_ms = struct.unpack(">I", data[2:6])[0]
            cue_name = _decode_utf8_null_terminated(data[12:], context="Markers2 CUE name")
            cues.append(
                {
                    "index": int(data[1]),
                    "position_ms": int(position_ms),
                    "color": [data[7], data[8], data[9]],
                    "name": cue_name,
                }
            )
            continue

        if name == "LOOP":
            if len(data) < 21:
                raise SeratoPerformanceError("Markers2 LOOP entry is truncated")
            start_ms, end_ms = struct.unpack(">II", data[2:10])
            if end_ms < start_ms:
                raise SeratoPerformanceError("Markers2 LOOP ends before it starts")
            loop_name = _decode_utf8_null_terminated(data[20:], context="Markers2 LOOP name")
            loops.append(
                {
                    "index": int(data[1]),
                    "start_ms": int(start_ms),
                    "end_ms": int(end_ms),
                    "color": [data[15], data[16], data[17]],
                    "locked": bool(data[19]),
                    "name": loop_name,
                }
            )
            continue

        if name == "BPMLOCK":
            if len(data) < 1:
                raise SeratoPerformanceError("Markers2 BPMLOCK entry is truncated")
            bpm_lock = bool(data[0])
            continue

        unknown_entries.append({"name": name, "length": len(data)})

    return {
        "track_color": track_color,
        "cues": cues,
        "loops": loops,
        "bpm_lock": bpm_lock,
        "unknown_entries": unknown_entries,
    }


def decode_beatgrid(body: bytes) -> list[dict[str, Any]]:
    """Decode a complete `Serato BeatGrid` GEOB body."""

    if len(body) < 7 or not body.startswith(BEATGRID_HEADER):
        raise SeratoPerformanceError("BeatGrid body is missing 01 00 header")
    marker_count = struct.unpack(">I", body[2:6])[0]
    expected_length = 6 + (marker_count * 8) + 1
    if len(body) != expected_length:
        raise SeratoPerformanceError(
            f"BeatGrid length mismatch: expected {expected_length}, got {len(body)}"
        )

    markers: list[dict[str, Any]] = []
    offset = 6
    for index in range(marker_count):
        position_s = struct.unpack(">f", body[offset : offset + 4])[0]
        if not math.isfinite(position_s) or position_s < 0:
            raise SeratoPerformanceError(f"BeatGrid marker {index} has invalid position")
        terminal = index == marker_count - 1
        if terminal:
            bpm = struct.unpack(">f", body[offset + 4 : offset + 8])[0]
            if not math.isfinite(bpm) or bpm <= 0:
                raise SeratoPerformanceError("BeatGrid terminal marker has invalid BPM")
            markers.append(
                {
                    "position_s": float(position_s),
                    "terminal": True,
                    "bpm": float(bpm),
                }
            )
        else:
            beats_till_next = struct.unpack(">I", body[offset + 4 : offset + 8])[0]
            markers.append(
                {
                    "position_s": float(position_s),
                    "terminal": False,
                    "beats_till_next": int(beats_till_next),
                }
            )
        offset += 8
    return markers


def beatgrid_to_anchors(markers: list[dict[str, Any]]) -> list[dict[str, float]]:
    """Convert Serato beatgrid markers to Rekordbox-style position/BPM anchors."""

    anchors: list[dict[str, float]] = []
    for index, marker in enumerate(markers):
        position_s = float(marker["position_s"])
        if marker.get("terminal"):
            bpm = float(marker["bpm"])
        else:
            if index + 1 >= len(markers):
                raise SeratoPerformanceError("non-terminal BeatGrid marker has no successor")
            next_position = float(markers[index + 1]["position_s"])
            delta = next_position - position_s
            beats = int(marker["beats_till_next"])
            if delta <= 0 or beats <= 0:
                raise SeratoPerformanceError(
                    f"BeatGrid marker {index} has invalid interval or beat count"
                )
            bpm = beats * 60.0 / delta
        anchors.append({"position_ms": position_s * 1000.0, "bpm": bpm})
    return anchors


def _id3_tags(path: Path):
    ext = path.suffix.casefold()
    if ext == ".mp3":
        try:
            return ID3(path)
        except ID3NoHeaderError:
            return None
    if ext == ".wav":
        return WAVE(path).tags
    if ext in {".aif", ".aiff"}:
        return AIFF(path).tags
    raise AssertionError(f"unexpected ID3 extension: {ext}")


def _read_id3_frames(path: Path) -> dict[str, bytes]:
    tags = _id3_tags(path)
    if tags is None:
        return {}
    result: dict[str, bytes] = {}
    for description in SERATO_DESCRIPTIONS:
        frame = tags.get(f"GEOB:{description}")
        if frame is not None:
            result[description] = bytes(frame.data)
    return result


def _read_mp4_frames(path: Path) -> dict[str, bytes]:
    audio = MP4(path)
    result: dict[str, bytes] = {}
    for description, atom in MP4_ATOMS.items():
        values = audio.get(atom)
        if values:
            result[description] = unwrap_non_id3_field(
                bytes(values[0]), expected_description=description
            )
    return result


def _read_flac_frames(path: Path) -> dict[str, bytes]:
    audio = FLAC(path)
    result: dict[str, bytes] = {}
    for description, field in FLAC_FIELDS.items():
        values = audio.get(field)
        if values:
            try:
                wrapped = values[0].encode("ascii", errors="strict")
            except UnicodeEncodeError as exc:
                raise SeratoPerformanceError(
                    f"FLAC {field} value is not ASCII base64"
                ) from exc
            result[description] = unwrap_non_id3_field(
                wrapped, expected_description=description
            )
    return result


def read_serato_frames(path: Path) -> dict[str, bytes]:
    """Read embedded Serato performance frames without writing the file."""

    requested = path.expanduser().absolute()
    if requested.is_symlink():
        raise SeratoPerformanceError(f"audio path must not be a symlink: {requested}")
    resolved = requested.resolve()
    if not resolved.is_file():
        raise SeratoPerformanceError(f"audio path is not a regular file: {resolved}")
    ext = resolved.suffix.casefold()
    if ext not in SUPPORTED_EXTENSIONS:
        raise SeratoPerformanceError(f"unsupported audio extension {ext or '(none)'}")

    before = resolved.stat(follow_symlinks=False)
    if ext in ID3_EXTENSIONS:
        frames = _read_id3_frames(resolved)
    elif ext in MP4_EXTENSIONS:
        frames = _read_mp4_frames(resolved)
    else:
        frames = _read_flac_frames(resolved)
    after = resolved.stat(follow_symlinks=False)
    before_identity = (before.st_size, before.st_mtime_ns, before.st_dev, before.st_ino)
    after_identity = (after.st_size, after.st_mtime_ns, after.st_dev, after.st_ino)
    if before_identity != after_identity:
        raise SeratoPerformanceError(f"audio file changed while being read: {resolved}")
    return frames


def inspect_file(path: Path) -> dict[str, Any]:
    """Return decoded Serato performance metadata for one audio file."""

    frames = read_serato_frames(path)
    markers2 = decode_markers2(frames[MARKERS2_DESC]) if MARKERS2_DESC in frames else None
    beatgrid_markers = (
        decode_beatgrid(frames[BEATGRID_DESC]) if BEATGRID_DESC in frames else None
    )
    return {
        "tool": "serato_performance_metadata",
        "tool_version": TOOL_VERSION,
        "file": os.fspath(path.expanduser().absolute().resolve()),
        "frames_present": sorted(frames),
        "markers2": markers2,
        "beatgrid": (
            {
                "markers": beatgrid_markers,
                "anchors": beatgrid_to_anchors(beatgrid_markers),
            }
            if beatgrid_markers is not None
            else None
        ),
        "writes_audio": False,
        "writes_serato": False,
        "writes_rekordbox": False,
        "writes_library_index": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio_file", type=Path)
    args = parser.parse_args()
    try:
        result = inspect_file(args.audio_file)
    except (OSError, SeratoPerformanceError, Exception) as exc:
        # Mutagen format errors do not share one stable base class across every
        # supported container, so report them uniformly without mutating state.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
