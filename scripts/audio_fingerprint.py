#!/usr/bin/env python3
"""Read-only audio probing for the owner-approved audio-match duplicate rule.

Produces the evidence `duplicate_semantics.classify_audio_match` needs for one
file: Chromaprint fingerprint (via the `fpcalc` CLI), duration, bitrate,
lossless flag, audio-payload SHA-256 (tags ignored) and version markers taken
from the filename and the Title tag. Never writes anything.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import re
import subprocess
from pathlib import Path

import audio_payload
import duplicate_semantics

LOSSLESS_SUFFIXES = {".flac", ".wav", ".aif", ".aiff"}
FINGERPRINT_SECONDS = 120
MAX_OFFSET_FRAMES = 80  # ~10 s of Chromaprint frames, covers different lead-in silence


def fingerprint(path: Path, seconds: int = FINGERPRINT_SECONDS) -> list[int]:
    out = subprocess.run(
        ["fpcalc", "-raw", "-length", str(seconds), str(path)],
        capture_output=True, text=True, timeout=120, check=False,
    ).stdout
    match = re.search(r"FINGERPRINT=(.*)", out)
    if not match or not match.group(1).strip():
        raise RuntimeError(f"fpcalc produced no fingerprint for {path}")
    return [int(v) for v in match.group(1).split(",")]


def similarity(a: list[int], b: list[int], max_offset: int = MAX_OFFSET_FRAMES) -> float:
    """Best 1 - bit-error-rate over small alignment offsets. 1.0 = identical."""
    if not a or not b:
        return 0.0
    best = 0.0
    for offset in range(-max_offset, max_offset + 1):
        pa = a[max(0, offset):]
        pb = b[max(0, -offset):]
        n = min(len(pa), len(pb))
        if n < 50:
            continue
        errors = sum(bin((pa[i] ^ pb[i]) & 0xFFFFFFFF).count("1") for i in range(n))
        best = max(best, 1.0 - errors / (32.0 * n))
    return round(best, 4)


def _title(path: Path) -> str:
    try:
        from mutagen import File
        audio = File(path, easy=True)
        values = (audio.tags or {}).get("title") if audio is not None else None
        return str(values[0]) if values else ""
    except Exception:  # noqa: BLE001 - a missing title is not evidence either way
        return ""


def probe(path: Path) -> dict:
    from mutagen import File
    path = Path(path)
    audio = File(path)
    if audio is None or getattr(audio, "info", None) is None:
        raise RuntimeError(f"cannot read audio info: {path}")
    return {
        "duration": float(audio.info.length),
        "bitrate": int(getattr(audio.info, "bitrate", 0) or 0),
        "lossless": path.suffix.casefold() in LOSSLESS_SUFFIXES,
        "payload_sha256": audio_payload.payload_digest(path).sha256,
        "markers": sorted(duplicate_semantics.version_markers(path.stem + " " + _title(path))),
        "fingerprint": fingerprint(path),
    }
