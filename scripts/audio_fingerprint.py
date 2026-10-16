#!/usr/bin/env python3
"""Read-only audio probing for the owner-approved audio-match duplicate rule.

Produces the evidence `duplicate_semantics.classify_audio_match` needs for one
file: Chromaprint fingerprint (via the `fpcalc` CLI), duration, bitrate,
lossless flag, audio-payload SHA-256 (tags ignored), version markers taken
from the filename and the Title tag, plus the version evidence that is not in
the name: whole version labels, CLEAN/DIRTY-style folders and Clean/Dirty
Grouping values, and whether the file carries Serato cue data. The whole song
is fingerprinted, not just its first two minutes. Never writes anything.
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
WINDOW_FRAMES = 80      # ~10 s windows for the per-stretch check


def fingerprint(path: Path, seconds: int = FINGERPRINT_SECONDS) -> list[int]:
    out = subprocess.run(
        ["fpcalc", "-raw", "-length", str(seconds), str(path)],
        capture_output=True, text=True, timeout=max(120, seconds), check=False,
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


def match_detail(a: list[int], b: list[int], max_offset: int = MAX_OFFSET_FRAMES,
                 window: int = WINDOW_FRAMES) -> dict:
    """Best alignment's overall similarity, how much of the shorter fingerprint
    it covers, and its worst ~10 s window (a changed bridge or outro shows up
    there even when the overall score stays high)."""
    best = {"similarity": 0.0, "coverage": 0.0, "min_window": 0.0}
    if not a or not b:
        return best
    shorter = min(len(a), len(b))
    for offset in range(-max_offset, max_offset + 1):
        pa = a[max(0, offset):]
        pb = b[max(0, -offset):]
        n = min(len(pa), len(pb))
        if n < 50:
            continue
        bits = [bin((pa[i] ^ pb[i]) & 0xFFFFFFFF).count("1") for i in range(n)]
        score = 1.0 - sum(bits) / (32.0 * n)
        if score > best["similarity"]:
            windows = [bits[i:i + window] for i in range(0, n, window)]
            windows = [w for w in windows if len(w) >= window // 2] or [bits]
            best = {"similarity": round(score, 4), "coverage": round(n / shorter, 4),
                    "min_window": round(min(1.0 - sum(w) / (32.0 * len(w)) for w in windows), 4)}
    return best


VERSION_FOLDER_NAMES = {"clean", "dirty", "explicit", "instrumental", "instrumentals", "acapella",
                        "acapellas", "intro", "intros", "remixes & edits", "remixes", "edits"}


def version_folders(path: Path) -> list[str]:
    """Folder names on the path that say which version a file is (CLEAN, DIRTY, ...)."""
    out = []
    for part in Path(path).parent.parts:
        folded = part.strip().casefold()
        if folded in VERSION_FOLDER_NAMES or duplicate_semantics.version_markers(part):
            out.append(folded)
    return sorted(out)


def version_grouping(path: Path) -> list[str]:
    """Grouping values that name a version (e.g. "Clean", "Version: Dirty"); not Energy/Source."""
    values: list[str] = []
    try:
        from mutagen import File
        audio = File(path)
        tags = getattr(audio, "tags", None) or {}
        for key in list(tags.keys()):
            k = str(key).casefold()
            if k.startswith(("tit1", "grp1")) or k in {"grouping", "©grp"}:
                raw = tags[key]
                raw = getattr(raw, "text", raw)
                raw = raw if isinstance(raw, list) else [raw]
                for value in raw:
                    values.extend(v.strip() for v in str(value).split(";") if v.strip())
    except Exception:  # noqa: BLE001 - unreadable tags are compared as "none"
        return []
    return sorted({" ".join(v.casefold().split()) for v in values if duplicate_semantics.version_markers(v)})


def has_serato_cues(path: Path) -> bool:
    try:
        from mutagen import File
        tags = getattr(File(path), "tags", None) or {}
        return any("serato" in str(k).casefold() and "marker" in str(k).casefold() for k in tags.keys())
    except Exception:  # noqa: BLE001
        return False


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
    duration = float(audio.info.length)
    title = _title(path)
    codec = str(getattr(audio.info, "codec", "") or "").casefold()
    return {
        "duration": duration,
        "bitrate": int(getattr(audio.info, "bitrate", 0) or 0),
        "lossless": path.suffix.casefold() in LOSSLESS_SUFFIXES or codec == "alac",
        "payload_sha256": audio_payload.payload_digest(path).sha256,
        "markers": sorted(duplicate_semantics.version_markers(path.stem + " " + title)),
        "version": {
            "labels": duplicate_semantics.version_labels(path.stem) + [
                "title:" + label for label in duplicate_semantics.version_labels(title)],
            "folders": version_folders(path),
            "grouping": version_grouping(path),
        },
        "serato_cues": has_serato_cues(path),
        "fingerprint": fingerprint(path, int(duration) + 10),
    }
