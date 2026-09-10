"""Synthetic fixtures for the safety tests.

Everything here is generated. No copyrighted production audio is committed to
this repository, and no test ever reads or writes the real music library.

The "MP3" files are real enough for the code paths under test: they carry
valid MPEG-1 Layer III frame headers so mutagen will attach and read ID3 tags,
and the frame bytes are deterministic silence so audio-payload digests are
stable and comparable before/after a tag write.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import backup_manifest  # noqa: E402

# MPEG-1 Layer III, 128kbps, 44.1kHz, no padding, stereo. 417-byte frames.
_FRAME_HEADER = b"\xff\xfb\x90\x00"
_FRAME_SIZE = 417


def mp3_bytes(seed: int = 0, frames: int = 8) -> bytes:
    """Deterministic synthetic MP3 payload. Different seeds -> different bytes."""
    out = bytearray()
    for index in range(frames):
        out += _FRAME_HEADER
        body = bytes(((seed * 31 + index * 7 + position) % 251) for position in range(_FRAME_SIZE - 4))
        out += body
    return bytes(out)


def write_mp3(path: Path, seed: int = 0, frames: int = 8) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(mp3_bytes(seed, frames))
    return path


def tag_mp3(path: Path, *, genre=None, title=None, artist=None, grouping=None,
            year=None, bpm=None, key=None, v2_version: int = 4) -> Path:
    """Attach ID3 tags at the requested version. v2_version=2 makes an ID3v2.2
    file, which the engines must preserve rather than silently upgrade."""
    from mutagen.id3 import ID3, ID3NoHeaderError, TCON, TIT2, TPE1, TIT1, TDRC, TYER, TBPM, TKEY

    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    if genre is not None:
        tags.setall("TCON", [TCON(encoding=1, text=[genre])])
    if title is not None:
        tags.setall("TIT2", [TIT2(encoding=1, text=[title])])
    if artist is not None:
        tags.setall("TPE1", [TPE1(encoding=1, text=[artist])])
    if grouping is not None:
        tags.setall("TIT1", [TIT1(encoding=1, text=[grouping])])
    if year is not None:
        if v2_version == 4:
            tags.setall("TDRC", [TDRC(encoding=1, text=[year])])
        else:
            tags.setall("TYER", [TYER(encoding=1, text=[year])])
    if bpm is not None:
        tags.setall("TBPM", [TBPM(encoding=1, text=[bpm])])
    if key is not None:
        tags.setall("TKEY", [TKEY(encoding=1, text=[key])])
    if v2_version == 2:
        raise ValueError("use write_id3v22_mp3(); mutagen can only write v2.3/v2.4")
    tags.save(path, v2_version=v2_version)
    return Path(path)


def _synchsafe(value: int) -> bytes:
    return bytes(((value >> 21) & 0x7F, (value >> 14) & 0x7F,
                  (value >> 7) & 0x7F, value & 0x7F))


def write_id3v22_mp3(
    path: Path,
    seed: int = 0,
    genre: str = "",
    title: str = "",
    artist: str = "",
    grouping: str = "",
) -> Path:
    """Write a real ID3v2.2 file by hand.

    mutagen refuses to SAVE v2.2 ("Only 3 or 4 allowed for v2_version"), which
    is exactly why the production code must skip rather than rewrite these:
    any tag write to a v2.2 file necessarily upgrades it and destroys the
    original block. Building the bytes directly is the only way to test that
    the engines really do leave them alone.

    v2.2 frames are 3-character IDs (TCO = genre, TT2 = title, TT1 = grouping)
    with a 3-byte big-endian size, unlike v2.3/v2.4's 4-character IDs and
    4-byte sizes.
    """
    frames = bytearray()
    for frame_id, text in (
        ("TCO", genre), ("TT2", title), ("TP1", artist), ("TT1", grouping),
    ):
        if not text:
            continue
        body = b"\x00" + text.encode("latin-1", "replace")
        frames += frame_id.encode("ascii") + len(body).to_bytes(3, "big") + body
    padding = b"\x00" * 32
    body = bytes(frames) + padding
    header = b"ID3" + bytes((2, 0, 0)) + _synchsafe(len(body))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + body + mp3_bytes(seed))
    return path


def write_unserializable_rva2_mp3(
    path: Path,
    seed: int = 0,
    genre: str = "",
    grouping: str = "",
) -> Path:
    """Write ID3v2.4 with a real RVA2 frame Mutagen cannot re-serialize.

    A malformed peak-width byte of 35 makes Mutagen's RVA2 reader scale the
    four stored peak bytes to 40.0. The frame is therefore readable, but its
    parsed peak exceeds the writer's allowed range and save() fails with a
    wrapped SpecError before touching the file. This reproduces the production
    a&t failure without mocking Mutagen or opening a library file.
    """
    def frame(frame_id: str, body: bytes) -> bytes:
        return frame_id.encode("ascii") + _synchsafe(len(body)) + b"\x00\x00" + body

    frames = bytearray()
    for frame_id, text in (("TCON", genre), ("TIT1", grouping)):
        if text:
            frames += frame(frame_id, b"\x03" + text.encode("utf-8"))

    # description="\x0c\x10", master channel, zero gain, 35-bit width,
    # 0xa0000000 peak. Mutagen reads the resulting peak as 40.0000000186.
    rva2 = b"\x0c\x10\x00\x01\x00\x00\x23\xa0\x00\x00\x00"
    frames += frame("RVA2", rva2)
    body = bytes(frames) + b"\x00" * 32
    header = b"ID3" + bytes((4, 0, 0)) + _synchsafe(len(body))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + body + mp3_bytes(seed))
    return path


def add_serato_frame(path: Path, payload: bytes = b"SERATO-TEST-PAYLOAD") -> Path:
    """Attach a GEOB frame that looks like Serato performance data.

    The engines must prove these survive a tag write byte-for-byte; a test
    that never puts one on the file cannot prove that.
    """
    from mutagen.id3 import ID3, ID3NoHeaderError, GEOB

    try:
        tags = ID3(path)
        original_v2_version = tags.version[1]
    except ID3NoHeaderError:
        tags = ID3()
        original_v2_version = 4
    if original_v2_version not in {3, 4}:
        raise ValueError(
            f"add_serato_frame refuses to upgrade ID3v2.{original_v2_version}: {path}"
        )
    tags.add(GEOB(encoding=0, mime="application/octet-stream",
                  filename="", desc="Serato Markers2", data=payload))
    tags.save(path, v2_version=original_v2_version)
    return Path(path)


def mirror_backup(source_root: Path, backup_root: Path) -> Path:
    """Byte-for-byte copy of source_root into backup_root."""
    import shutil

    source_root, backup_root = Path(source_root), Path(backup_root)
    if backup_root.exists():
        shutil.rmtree(backup_root)
    shutil.copytree(source_root, backup_root)
    return backup_root


def passing_manifest(source_root: Path, backup_root: Path, out_path: Path,
                     serato_backup: Path | None = None,
                     serato_required: bool = False) -> Path:
    """Build a genuine PASS manifest by actually hashing both trees.

    Note this does NOT fabricate a PASS: it calls the same build_manifest the
    production verifier uses, so if the backup is incomplete the fixture
    manifest correctly says FAIL and the test sees a refusal.
    """
    manifest = backup_manifest.build_manifest(
        Path(source_root), Path(backup_root), serato_backup,
        serato_required=serato_required,
    )
    backup_manifest.write_manifest(manifest, Path(out_path))
    return Path(out_path)


def make_library(root: Path, tracks) -> dict:
    """Create a small synthetic library. `tracks` is a list of dicts:
    {relative, seed?, genre?, title?, artist?, grouping?, year?, bpm?, key?,
     v2_version?, serato?}
    Returns {relative: Path}.
    """
    made = {}
    for index, spec in enumerate(tracks):
        if spec.get("v2_version") == 2:
            path = write_id3v22_mp3(
                Path(root) / spec["relative"], seed=spec.get("seed", index),
                genre=spec.get("genre", ""), title=spec.get("title", ""),
            )
            made[spec["relative"]] = path
            continue
        path = write_mp3(Path(root) / spec["relative"], seed=spec.get("seed", index))
        tag_kwargs = {field: spec[field] for field in
                      ("genre", "title", "artist", "grouping", "year", "bpm", "key")
                      if field in spec}
        if tag_kwargs or "v2_version" in spec:
            tag_mp3(path, v2_version=spec.get("v2_version", 4), **tag_kwargs)
        if spec.get("serato"):
            add_serato_frame(path, spec["serato"] if isinstance(spec["serato"], bytes)
                             else b"SERATO-TEST-PAYLOAD")
        made[spec["relative"]] = path
    return made
