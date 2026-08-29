#!/usr/bin/env python3
"""Read-only audio-payload extraction shared by evidence tools.

The payload excludes mutable metadata containers while retaining encoded audio
bytes. Callers must still compare the result with trusted historical evidence;
a current payload hash alone cannot prove identity with an old catalog row.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "read-only"

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PayloadDigest:
    size_bytes: int
    sha256: str


def _synchsafe_size(header: bytes) -> int:
    if len(header) < 10 or header[:3] != b"ID3":
        return 0
    if any(value & 0x80 for value in header[6:10]):
        raise RuntimeError("invalid ID3 synchsafe size")
    size = (
        (header[6] << 21)
        | (header[7] << 14)
        | (header[8] << 7)
        | header[9]
    )
    return 10 + size + (10 if header[5] & 0x10 else 0)


def _mp3_or_aac_payload(data: bytes) -> bytes:
    start = _synchsafe_size(data[:10])
    end = len(data)
    if end - start >= 128 and data[end - 128 : end - 125] == b"TAG":
        end -= 128
    # APEv2 is also metadata and is commonly stored immediately before ID3v1
    # or at EOF. Its footer carries the complete tag size.
    if end - start >= 32 and data[end - 32 : end - 24] == b"APETAGEX":
        ape_size = int.from_bytes(data[end - 20 : end - 16], "little")
        if ape_size < 32 or ape_size > end - start:
            raise RuntimeError("invalid trailing APEv2 tag size")
        end -= ape_size
    if start > end:
        raise RuntimeError("metadata regions overlap audio payload")
    return data[start:end]


def _mp4_boxes(data: bytes):
    pos, end = 0, len(data)
    while pos + 8 <= end:
        size = int.from_bytes(data[pos : pos + 4], "big")
        box_type = data[pos + 4 : pos + 8]
        header_size = 8
        if size == 1:
            if pos + 16 > end:
                raise RuntimeError("truncated 64-bit MP4 box header")
            size = int.from_bytes(data[pos + 8 : pos + 16], "big")
            header_size = 16
        elif size == 0:
            size = end - pos
        if size < header_size or pos + size > end:
            raise RuntimeError("invalid MP4 box size")
        yield box_type, pos + header_size, pos + size
        pos += size
    if pos != end:
        raise RuntimeError("trailing bytes after MP4 boxes")


def _m4a_payload(data: bytes) -> bytes:
    chunks = [data[start:end] for kind, start, end in _mp4_boxes(data) if kind == b"mdat"]
    if not chunks:
        raise RuntimeError("no mdat box found")
    return b"".join(chunks)


def _flac_payload(data: bytes) -> bytes:
    start = _synchsafe_size(data[:10])
    if data[start : start + 4] != b"fLaC":
        raise RuntimeError("missing FLAC marker")
    pos = start + 4
    while True:
        header = data[pos : pos + 4]
        if len(header) != 4:
            raise RuntimeError("invalid FLAC metadata header")
        last = bool(header[0] & 0x80)
        length = int.from_bytes(header[1:4], "big")
        pos += 4 + length
        if pos > len(data):
            raise RuntimeError("truncated FLAC metadata block")
        if last:
            return data[pos:]


def _riff_wave_payload(data: bytes) -> bytes:
    if len(data) < 12 or data[:4] not in {b"RIFF", b"RIFX"} or data[8:12] != b"WAVE":
        raise RuntimeError("invalid RIFF/WAVE header")
    byteorder = "little" if data[:4] == b"RIFF" else "big"
    pos = 12
    chunks: list[bytes] = []
    while pos + 8 <= len(data):
        kind = data[pos : pos + 4]
        size = int.from_bytes(data[pos + 4 : pos + 8], byteorder)
        start = pos + 8
        end = start + size
        if end > len(data):
            raise RuntimeError("truncated RIFF chunk")
        if kind == b"data":
            chunks.append(data[start:end])
        pos = end + (size & 1)
    if not chunks:
        raise RuntimeError("WAVE file has no data chunk")
    return b"".join(chunks)


def _aiff_payload(data: bytes) -> bytes:
    if len(data) < 12 or data[:4] != b"FORM" or data[8:12] not in {b"AIFF", b"AIFC"}:
        raise RuntimeError("invalid AIFF header")
    pos = 12
    chunks: list[bytes] = []
    while pos + 8 <= len(data):
        kind = data[pos : pos + 4]
        size = int.from_bytes(data[pos + 4 : pos + 8], "big")
        start = pos + 8
        end = start + size
        if end > len(data):
            raise RuntimeError("truncated AIFF chunk")
        if kind == b"SSND":
            if size < 8:
                raise RuntimeError("invalid AIFF SSND chunk")
            offset = int.from_bytes(data[start : start + 4], "big")
            audio_start = start + 8 + offset
            if audio_start > end:
                raise RuntimeError("invalid AIFF SSND offset")
            chunks.append(data[audio_start:end])
        pos = end + (size & 1)
    if not chunks:
        raise RuntimeError("AIFF file has no SSND chunk")
    return b"".join(chunks)


def payload_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    suffix = path.suffix.casefold()
    if suffix in {".mp3", ".aac"}:
        return _mp3_or_aac_payload(data)
    if suffix == ".m4a":
        return _m4a_payload(data)
    if suffix == ".flac":
        return _flac_payload(data)
    if suffix == ".wav":
        return _riff_wave_payload(data)
    if suffix in {".aif", ".aiff"}:
        return _aiff_payload(data)
    raise RuntimeError(f"unsupported audio payload format: {suffix or '<none>'}")


def payload_digest(path: Path) -> PayloadDigest:
    payload = payload_bytes(path)
    return PayloadDigest(
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
