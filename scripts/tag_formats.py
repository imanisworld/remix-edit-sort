#!/usr/bin/env python3
"""Read-only, format-aware helpers shared by the Grouping and Year/BPM writers.

Covers the non-ID3 containers the writers now support:

  * FLAC  -- Grouping is the ``GROUPING`` Vorbis comment, Year is ``DATE``,
             BPM is ``BPM``. Serato keeps its data in ``SERATO_*`` Vorbis
             comments; APPLICATION metadata blocks are also treated as
             protected opaque data.
  * M4A   -- Grouping is ``\xa9grp``, Year is ``\xa9day``, BPM is ``tmpo``.
             Serato keeps its data in ``----:com.serato.dj:*`` freeform atoms.

It also holds ``load_id3_for_save()``, the one way the ID3 writers load a tag
they are about to save (see its docstring for the ID3v2.3 year-frame bug it
prevents).

Nothing in this module writes to a file. The writes live inside each apply
script's declared ENGINE_MUTATION_HELPERS so mutation_surface.py keeps
governing them.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import hashlib
import json
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.mp4 import MP4

FLAC_SUFFIXES = {".flac"}
MP4_SUFFIXES = {".m4a"}
NON_ID3_SUFFIXES = FLAC_SUFFIXES | MP4_SUFFIXES
V22_IMAGE_MIMES = {"PNG": "image/png", "JPG": "image/jpeg"}


def kind(path: Path) -> str:
    suffix = Path(path).suffix.casefold()
    if suffix == ".mp3":
        return "mp3"
    if suffix in FLAC_SUFFIXES:
        return "flac"
    if suffix in MP4_SUFFIXES:
        return "m4a"
    return "unsupported"


def fit_id3_to_save_version(tags, v2_version: int) -> None:
    """Bring a tag loaded with ``translate=False`` into ``v2_version``'s frame
    model, in memory only.

    mutagen's default load converts every tag to its v2.4 model (TYER/TDAT/
    TIME -> TDRC, TORY -> TDOR, IPLS -> TIPL), and ``save(v2_version=3)`` does
    NOT convert back, so a v2.3 file came out with off-spec v2.4 frames. For a
    v2.4 save this does exactly what the default load did. For a v2.3 save of
    a v2.3 (or v2.2) tag it leaves the frames as read, bar a v2.2 picture's
    image format (below). Unlike ``update_to_v23()``, that keeps v2.4-only
    frames iTunes puts in v2.3 tags (TSOP, TSOA, ...) and raw TCON values
    such as "(17)".
    """
    if v2_version == 4:
        tags.update_to_v24()
    elif tags.version[1] == 4:
        tags.update_to_v23()
    elif tags.version[1] == 2:
        # Untranslated v2.2 PIC frames load as APIC but keep v2.2's 3-letter
        # image format ("PNG") where v2.3 needs a MIME type. Same mapping
        # mutagen's own translation applies.
        for pic in tags.getall("APIC"):
            pic.mime = V22_IMAGE_MIMES.get(pic.mime, pic.mime)


def load_id3_for_save(path: Path, v2_version: int | None = None) -> ID3:
    """Load an MP3's ID3 tag for a write that will ``save(v2_version=...)``.
    ``v2_version=None`` means the tag's own version. Raises
    ``ID3NoHeaderError`` like ``ID3(path)``."""
    tags = ID3(path, translate=False)
    fit_id3_to_save_version(tags, tags.version[1] if v2_version is None else v2_version)
    return tags


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _synchsafe_id3_end(data: bytes) -> int:
    if data[:3] == b"ID3" and len(data) >= 10:
        size = ((data[6] & 127) << 21) | ((data[7] & 127) << 14) | ((data[8] & 127) << 7) | (data[9] & 127)
        return 10 + size + (10 if data[5] & 16 else 0)
    return 0


def _mp4_top_level_boxes(data: bytes):
    pos, end = 0, len(data)
    while pos + 8 <= end:
        size = int.from_bytes(data[pos:pos + 4], "big")
        btype = data[pos + 4:pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > end:
                raise RuntimeError(f"truncated 64-bit MP4 box header at offset {pos}")
            size = int.from_bytes(data[pos + 8:pos + 16], "big")
            header = 16
        elif size == 0:
            size = end - pos
        if size < header:
            raise RuntimeError(f"invalid MP4 box size at offset {pos}")
        yield btype, pos, size, header
        pos += size


def _flac_blocks(data: bytes, path: Path):
    """Yield (block_type, raw_body) for each FLAC metadata block; return audio offset via StopIteration value."""
    start = _synchsafe_id3_end(data)
    if data[start:start + 4] != b"fLaC":
        raise RuntimeError(f"missing FLAC marker: {path}")
    pos = start + 4
    blocks = []
    while True:
        header = data[pos:pos + 4]
        if len(header) != 4:
            raise RuntimeError(f"invalid FLAC metadata header: {path}")
        last = bool(header[0] & 0x80)
        btype = header[0] & 0x7F
        length = int.from_bytes(header[1:4], "big")
        body = data[pos + 4:pos + 4 + length]
        if len(body) != length:
            raise RuntimeError(f"truncated FLAC metadata block: {path}")
        blocks.append((btype, body))
        pos += 4 + length
        if last:
            return blocks, pos


def audio_payload(path: Path) -> bytes:
    """Bytes of the encoded audio only, so tag rewrites can be proven harmless."""
    path = Path(path)
    data = path.read_bytes()
    k = kind(path)
    if k == "mp3":
        start, end = _synchsafe_id3_end(data), len(data)
        if len(data) >= 128 and data[-128:-125] == b"TAG":
            end -= 128
        return data[start:end]
    if k == "flac":
        _, offset = _flac_blocks(data, path)
        return data[offset:]
    if k == "m4a":
        payload = bytearray()
        for btype, start, size, header in _mp4_top_level_boxes(data):
            if btype == b"mdat":
                payload += data[start + header:start + size]
        if not payload:
            raise RuntimeError(f"no mdat box found: {path}")
        return bytes(payload)
    raise RuntimeError(f"unsupported format: {path}")


def serato_digest(path: Path) -> str:
    """Digest of Serato's own data in a FLAC or M4A file (must be unchanged by a tag write)."""
    path = Path(path)
    k = kind(path)
    if k == "flac":
        audio = FLAC(path)
        comments = {}
        if audio.tags is not None:
            for key, value in audio.tags:
                if "serato" in key.casefold():
                    comments.setdefault(key.upper(), []).append(value)
        blocks, _ = _flac_blocks(path.read_bytes(), path)
        application = [_sha(body) for btype, body in blocks if btype == 2]
        payload = {"comments": comments, "application_blocks": application}
        return _sha(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode())
    if k == "m4a":
        audio = MP4(path)
        values = []
        for key, value in (audio.tags or {}).items():
            if "serato" not in key.casefold():
                continue
            parts = value if isinstance(value, list) else [value]
            values.append({"key": key, "parts_sha256": [_sha(bytes(p)) for p in parts]})
        return _sha(json.dumps(sorted(values, key=lambda v: v["key"]), ensure_ascii=False).encode())
    raise RuntimeError(f"serato_digest: unsupported format: {path}")


def _flac_values(audio: FLAC, key: str) -> list[str]:
    if audio.tags is None:
        return []
    return [str(v).strip() for v in audio.tags.get(key, []) if str(v).strip()]


def read_grouping(path: Path) -> list[str]:
    path = Path(path)
    k = kind(path)
    if k == "flac":
        return _flac_values(FLAC(path), "GROUPING")
    if k == "m4a":
        tags = MP4(path).tags or {}
        return [str(v).strip() for v in tags.get("\xa9grp", []) if str(v).strip()]
    raise RuntimeError(f"read_grouping: unsupported format: {path}")


def read_year_bpm(path: Path) -> tuple[str, str]:
    path = Path(path)
    k = kind(path)
    if k == "flac":
        audio = FLAC(path)
        dates = _flac_values(audio, "DATE")
        bpms = _flac_values(audio, "BPM")
        return (dates[0][:4] if dates else ""), (bpms[0] if bpms else "")
    if k == "m4a":
        tags = MP4(path).tags or {}
        days = [str(v).strip() for v in tags.get("\xa9day", []) if str(v).strip()]
        tmpo = tags.get("tmpo", [])
        return (days[0][:4] if days else ""), (str(tmpo[0]) if tmpo else "")
    raise RuntimeError(f"read_year_bpm: unsupported format: {path}")
