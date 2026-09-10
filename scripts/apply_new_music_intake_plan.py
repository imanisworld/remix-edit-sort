#!/usr/bin/env python3
"""Apply the auto-fixable items from survey_new_music_intake.py's plan:
Genre (only where the survey found unanimous sibling consensus), Year (with
reviewed evidence), and/or Grouping (remix/edit type + "Edit" umbrella,
and/or an explicit Clean/Dirty filename label). Duplicate flags in the plan
are report-only and are never acted on here -- deleting anything stays a
manual, per-file decision.

ID3v2.2 MP3s remain byte-for-byte skips unless that exact actionable item
carries ``"id3_upgrade": {"from": "2.2", "to": "2.3"}``. The upgrade is
validated against the live tag version, performed in the same one-save
metadata transaction, and verifies every non-target frame plus audio
and Serato payloads before it is journaled as complete.

Scoped to ONE top-level library folder at a time (the --lock-folder unit),
same discipline as every other apply_*.py script in this repo. Genre and
Genre and Grouping are written in one container save and one journaled
transaction per file. This matters for backup safety: after a first tag save,
the live file no longer matches the pre-apply backup manifest, so a second
transaction would correctly be refused by the shared engine.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "engine-user"

# Read by scripts/mutation_surface.py -- see apply_genre_needs_check_folder_plan.py
# for the contract this list has to satisfy. Both are called only from inside
# `with engine.item(...)`.
ENGINE_MUTATION_HELPERS = ("_apply_one_metadata_write", "write_metadata")

import argparse
from collections import Counter
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TCON, TIT1, TDRC, TYER
from mutagen.mp4 import MP4

import backup_manifest
import lock_guard
import mutation_engine

SUPPORTED_EXTS = {".mp3", ".flac", ".m4a"}
ID3_V22_TO_V23 = {"from": "2.2", "to": "2.3"}
YEAR_GRADES = {
    "confirmed_primary", "confirmed_secondary", "local_evidence",
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _mp4_top_level_boxes(data: bytes):
    """Yield (type: bytes, start, size, header_size) for each top-level MP4 box."""
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


def audio_payload(path: Path) -> bytes:
    data = path.read_bytes()
    suffix = path.suffix.casefold()
    if suffix == ".m4a":
        payload = bytearray()
        for btype, start, size, header in _mp4_top_level_boxes(data):
            if btype == b"mdat":
                payload += data[start + header:start + size]
        if not payload:
            raise RuntimeError(f"no mdat box found: {path}")
        return bytes(payload)
    if suffix == ".mp3":
        start, end = 0, len(data)
        if data[:3] == b"ID3" and len(data) >= 10:
            size = ((data[6] & 127) << 21) | ((data[7] & 127) << 14) | ((data[8] & 127) << 7) | (data[9] & 127)
            start = 10 + size + (10 if data[5] & 16 else 0)
        if data[-128:-125] == b"TAG":
            end -= 128
        return data[start:end]
    if suffix == ".flac":
        flac_start = 0
        if data[:3] == b"ID3" and len(data) >= 10:
            id3_size = ((data[6] & 127) << 21) | ((data[7] & 127) << 14) | ((data[8] & 127) << 7) | (data[9] & 127)
            flac_start = 10 + id3_size + (10 if data[5] & 16 else 0)
        if data[flac_start:flac_start + 4] != b"fLaC":
            raise RuntimeError(f"missing FLAC marker: {path}")
        pos = flac_start + 4
        while True:
            header = data[pos:pos + 4]
            if len(header) != 4:
                raise RuntimeError(f"invalid FLAC metadata header: {path}")
            last = bool(header[0] & 0x80)
            length = int.from_bytes(header[1:4], "big")
            pos += 4 + length
            if last:
                return data[pos:]
    return data


def serato_digest_mp3(tags: ID3) -> str:
    values = []
    for frame in tags.values():
        haystack = " ".join((
            frame.HashKey, str(getattr(frame, "desc", "")),
            str(getattr(frame, "owner", "")), str(getattr(frame, "filename", "")),
        )).casefold()
        if "serato" in haystack:
            value = {
                "key": frame.HashKey, "frame": frame.FrameID,
                "encoding": str(getattr(frame, "encoding", None)),
                "desc": str(getattr(frame, "desc", "")),
                "owner": str(getattr(frame, "owner", "")),
                "filename": str(getattr(frame, "filename", "")),
            }
            data = getattr(frame, "data", None)
            if isinstance(data, bytes):
                value["data_sha256"] = sha256_bytes(data)
                value["data_length"] = len(data)
            text = getattr(frame, "text", None)
            if text is not None:
                value["text"] = [str(item) for item in text]
            values.append(value)
    payload = json.dumps(sorted(values, key=lambda item: item["key"]), ensure_ascii=False).encode()
    return sha256_bytes(payload)


def serato_digest(path: Path) -> str:
    if path.suffix.casefold() == ".mp3":
        try:
            return serato_digest_mp3(ID3(path))
        except ID3NoHeaderError:
            return sha256_bytes(b"[]")
    if path.suffix.casefold() == ".flac":
        audio = FLAC(path)
        values = {key: list(value) for key, value in audio.tags.items() if "serato" in key.casefold()}
        return sha256_bytes(json.dumps(values, sort_keys=True, ensure_ascii=False).encode())
    if path.suffix.casefold() == ".m4a":
        audio = MP4(path)
        values = []
        for key, value in audio.items():
            if "serato" not in key.casefold():
                continue
            parts = value if isinstance(value, list) else [value]
            values.append({"key": key, "parts_sha256": [sha256_bytes(bytes(p)) for p in parts]})
        return sha256_bytes(json.dumps(sorted(values, key=lambda i: i["key"]), ensure_ascii=False).encode())
    return sha256_bytes(b"[]")


def serato_payload_digest_mp3(tags: ID3) -> str:
    values = []
    for frame in tags.values():
        haystack = " ".join((
            frame.HashKey, str(getattr(frame, "desc", "")),
            str(getattr(frame, "owner", "")), str(getattr(frame, "filename", "")),
        )).casefold()
        if "serato" in haystack:
            data = getattr(frame, "data", None)
            value = {"key": frame.HashKey}
            if isinstance(data, bytes):
                value["data_sha256"] = sha256_bytes(data)
                value["data_length"] = len(data)
            else:
                text = getattr(frame, "text", None)
                if text is not None:
                    value["text"] = [str(item) for item in text]
            values.append(value)
    payload = json.dumps(sorted(values, key=lambda item: item["key"]), ensure_ascii=False).encode()
    return sha256_bytes(payload)


def serato_payload_digest(path: Path) -> str:
    # This is the ONLY correct digest to compare across a resave for the
    # "is this a real change" question -- see the 2026-08-16 remix/edit
    # grouping batch postmortem: the strict serato_digest() includes each
    # GEOB frame's encoding byte, which mutagen legitimately normalizes on
    # save even when the frame's actual binary payload is untouched. Using
    # the strict digest as the safety gate produced 17 false-positive
    # "corruption" alarms that turned out to be exactly this.
    if path.suffix.casefold() == ".mp3":
        try:
            return serato_payload_digest_mp3(ID3(path))
        except ID3NoHeaderError:
            return sha256_bytes(b"[]")
    return serato_digest(path)


class SeratoEncodingOnlyChange(Exception):
    pass


def read_genre(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            return ""
        frames = tags.getall("TCON")
        return str(frames[0]).strip() if frames else ""
    if suffix == ".flac":
        values = FLAC(path).get("genre", [])
        return str(values[0]).strip() if values else ""
    if suffix == ".m4a":
        values = MP4(path).get("\xa9gen", [])
        return str(values[0]).strip() if values else ""
    raise RuntimeError(f"genre read not supported for {path.suffix}: {path}")


def read_grouping(path: Path) -> tuple[list[str], int]:
    suffix = path.suffix.casefold()
    if suffix == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            return [], 4
        raw = []
        for frame in tags.getall("TIT1"):
            text = getattr(frame, "text", [])
            raw.extend(str(v).strip() for v in text if str(v).strip())
        parts = []
        for value in raw:
            parts.extend(p.strip() for p in value.split(";") if p.strip())
        return parts, tags.version[1]
    if suffix == ".flac":
        raw = [str(v).strip() for v in FLAC(path).get("grouping", []) if str(v).strip()]
        parts = []
        for value in raw:
            parts.extend(p.strip() for p in value.split(";") if p.strip())
        return parts, 4
    if suffix == ".m4a":
        raw = [str(v).strip() for v in MP4(path).get("\xa9grp", []) if str(v).strip()]
        parts = []
        for value in raw:
            parts.extend(p.strip() for p in value.split(";") if p.strip())
        return parts, 4
    raise RuntimeError(f"grouping read not supported for {path.suffix}: {path}")


def read_year(path: Path) -> str:
    """Return the stored four-digit release year, independent of container."""
    suffix = path.suffix.casefold()
    if suffix == ".mp3":
        try:
            tags = ID3(path)
        except ID3NoHeaderError:
            return ""
        frames = tags.getall("TDRC") or tags.getall("TYER")
        value = str(frames[0]).strip() if frames else ""
    elif suffix == ".flac":
        values = FLAC(path).get("date", [])
        value = str(values[0]).strip() if values else ""
    elif suffix == ".m4a":
        values = MP4(path).get("\xa9day", [])
        value = str(values[0]).strip() if values else ""
    else:
        raise RuntimeError(f"year read not supported for {path.suffix}: {path}")
    return value[:4] if len(value) >= 4 and value[:4].isdigit() else value


def verify_year_field(path: Path, expected: str, version: int) -> None:
    """Verify both the value and the physical field required by the format."""
    if read_year(path) != expected:
        raise RuntimeError(f"Year tag verification failed: {path}")
    suffix = path.suffix.casefold()
    if suffix == ".mp3":
        raw = ID3(path, translate=False)
        expected_frame = "TDRC" if version == 4 else "TYER"
        forbidden_frame = "TYER" if version == 4 else "TDRC"
        frames = raw.getall(expected_frame)
        if len(frames) != 1 or str(frames[0]).strip()[:4] != expected:
            raise RuntimeError(
                f"Year was not written to {expected_frame} for ID3v2.{version}: {path}"
            )
        if raw.getall(forbidden_frame):
            raise RuntimeError(f"conflicting {forbidden_frame} year field remains: {path}")
    elif suffix == ".flac":
        if [str(value) for value in FLAC(path).get("date", [])] != [expected]:
            raise RuntimeError(f"Year was not written to FLAC date: {path}")
    elif suffix == ".m4a":
        if [str(value) for value in MP4(path).get("\xa9day", [])] != [expected]:
            raise RuntimeError(f"Year was not written to MP4 ©day: {path}")


def combined_grouping(existing: list[str], new_terms: list[str], remove_terms: list[str] = ()) -> str:
    remove_normalized = {term.strip().casefold() for term in remove_terms if term.strip()}
    values, seen = [], set()
    for value in [*existing, *new_terms]:
        normalized = value.strip().casefold()
        if not normalized or normalized in seen or normalized in remove_normalized:
            continue
        values.append(value.strip())
        seen.add(normalized)
    return "; ".join(values)


def approved_id3_upgrade(value: object) -> bool:
    return isinstance(value, dict) and value == ID3_V22_TO_V23


def _semantic_value(value):
    """Stable JSON value for verifying non-target ID3 frame preservation."""
    if isinstance(value, bytes):
        return {"bytes": len(value), "sha256": sha256_bytes(value)}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_semantic_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _semantic_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    # Mutagen enums (Encoding, PictureType, Channel, ...) are int-like in
    # current releases.  String fallback keeps unusual frame attributes
    # comparable without depending on private serialization APIs.
    try:
        return int(value)
    except (TypeError, ValueError):
        return str(value)


def id3_preservation_snapshot(
    path: Path, excluded_frame_ids: set[str] | None = None,
) -> dict:
    """Semantic inventory of every ID3 frame except fields this plan edits.

    ID3v2.2 frames are already mapped by mutagen on load (PIC -> APIC,
    GEO -> GEOB, COM -> COMM, and so on), so this compares the mapped v2.3
    identity, multiplicity, and payload-bearing attributes on both sides of
    the upgrade. Encoding is intentionally omitted: v2.2 -> v2.3 may need a
    representation change while the text/data stays the same.
    """
    tags = ID3(path)
    excluded = {"TCON", "TIT1"} if excluded_frame_ids is None else excluded_frame_ids
    frames = []
    counts: Counter[str] = Counter()
    for frame in tags.values():
        if frame.FrameID in excluded:
            continue
        counts[frame.FrameID] += 1
        attributes = {
            key: _semantic_value(value)
            for key, value in sorted(getattr(frame, "__dict__", {}).items())
            if key != "encoding"
        }
        frames.append({
            "frame_id": frame.FrameID,
            "hash_key": frame.HashKey,
            "attributes": attributes,
        })
    frames.sort(key=lambda item: (
        item["frame_id"], item["hash_key"],
        json.dumps(item["attributes"], sort_keys=True, ensure_ascii=False),
    ))
    return {
        "frame_id_counts": dict(sorted(counts.items())),
        "frames": frames,
        "unknown_frames": [
            {"bytes": len(value), "sha256": sha256_bytes(value)}
            for value in tags.unknown_frames
        ],
    }


def write_metadata(
    path: Path,
    *,
    genre_value: str | None,
    grouping_value: str | None,
    version: int,
    year_value: str | None = None,
    id3_upgrade: dict | None = None,
) -> None:
    """Write all requested tag fields with exactly one container save."""
    if version not in {3, 4}:
        raise RuntimeError(
            f"ID3v2.{version} is not writable without an upgrade; refusing to change {path}"
        )
    suffix = path.suffix.casefold()
    if suffix == ".mp3":
        try:
            tags = ID3(path)
            source_version = tags.version[1]
        except ID3NoHeaderError:
            tags = ID3()
            source_version = 4
        if source_version == 2:
            if not approved_id3_upgrade(id3_upgrade) or version != 3:
                raise RuntimeError(
                    f"ID3v2.2 upgrade was not explicitly approved by the plan: {path}"
                )
        elif id3_upgrade is not None:
            raise RuntimeError(
                f"plan requests an ID3v2.2 upgrade, but source is ID3v2.{source_version}: {path}"
            )
        encoding = 3 if version == 4 else 1
        if genre_value is not None:
            tags.delall("TCON")
            tags.add(TCON(encoding=encoding, text=[genre_value]))
        if grouping_value is not None:
            tags.delall("TIT1")
            tags.add(TIT1(encoding=encoding, text=[grouping_value]))
        if year_value is not None:
            tags.delall("TYER")
            tags.delall("TDRC")
            if version == 4:
                tags.add(TDRC(encoding=encoding, text=[year_value]))
            else:
                tags.add(TYER(encoding=encoding, text=[year_value]))
        tags.save(path, v2_version=version)
        return
    if id3_upgrade is not None:
        raise RuntimeError(f"ID3 upgrade is only valid for MP3 files: {path}")
    if suffix == ".flac":
        audio = FLAC(path)
        if genre_value is not None:
            audio["genre"] = [genre_value]
        if grouping_value is not None:
            audio["grouping"] = [grouping_value]
        if year_value is not None:
            audio["date"] = [year_value]
        audio.save()
        return
    if suffix == ".m4a":
        audio = MP4(path)
        if genre_value is not None:
            audio["\xa9gen"] = [genre_value]
        if grouping_value is not None:
            audio["\xa9grp"] = [grouping_value]
        if year_value is not None:
            audio["\xa9day"] = [year_value]
        audio.save()
        return
    raise RuntimeError(f"metadata write not supported for {path.suffix}: {path}")


def tag_skip_reason(path: Path, id3_upgrade: dict | None = None) -> str | None:
    if path.suffix.casefold() != ".mp3":
        return None
    try:
        if ID3(path).version[1] == 2:
            if approved_id3_upgrade(id3_upgrade):
                return None
            return "ID3v2.2 preserved; tag write skipped"
    except ID3NoHeaderError:
        pass
    return None


def year_fix_error(value: object) -> str | None:
    if not isinstance(value, dict):
        return "must be an object"
    required = {"from", "to", "basis", "grade"}
    if set(value) != required:
        return f"must contain exactly {sorted(required)}"
    old, new = value["from"], value["to"]
    if not isinstance(old, str) or (old and (len(old) != 4 or not old.isdigit())):
        return "from must be empty or a four-digit year string"
    if not isinstance(new, str) or len(new) != 4 or not new.isdigit():
        return "to must be a four-digit year string"
    if not 1900 <= int(new) <= 2100:
        return "to must be in the range 1900..2100"
    if old == new:
        return "from and to must differ"
    if not isinstance(value["basis"], str) or not value["basis"].strip():
        return "basis must be a non-empty string"
    if value["grade"] not in YEAR_GRADES:
        return f"grade must authorize a write: {sorted(YEAR_GRADES)}"
    return None


def load_and_validate(plan_path: Path, root: Path) -> tuple[list[dict], list[str]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("mode") != "preview-only" or not plan.get("rules", {}).get("approval_required"):
        raise RuntimeError("unexpected plan mode")

    if Path(plan.get("root", "")).resolve() != root.resolve():
        raise RuntimeError(
            f"plan root {plan.get('root')!r} does not match requested root {str(root)!r}"
        )

    items = plan["items"]
    errors = []
    validated = []
    seen_paths = set()
    for row in items:
        if not row.get("sha256"):
            errors.append(f"plan item has no source SHA-256: {row.get('path')}")
            continue
        path = Path(row["path"]).resolve()
        if path in seen_paths:
            errors.append(f"duplicate path in plan: {path}")
            continue
        seen_paths.add(path)
        if root not in path.parents:
            errors.append(f"outside folder root {root}: {path}")
            continue
        if not path.is_file():
            errors.append(f"missing file: {path}")
            continue
        if path.suffix.casefold() not in SUPPORTED_EXTS:
            if any(key in row for key in ("genre_fix", "grouping_fix", "year_fix", "id3_upgrade")):
                errors.append(f"unsupported metadata format in actionable item: {path}")
            continue
        actual_hash = sha256_file(path)
        if actual_hash != row["sha256"]:
            errors.append(f"file hash changed since survey: {path}")
            continue
        if "id3_upgrade" in row:
            upgrade = row["id3_upgrade"]
            if not approved_id3_upgrade(upgrade):
                errors.append(f"unsupported ID3 upgrade request for {path}: {upgrade!r}")
                continue
            if path.suffix.casefold() != ".mp3":
                errors.append(f"ID3 upgrade requested for non-MP3 file: {path}")
                continue
            if not any(key in row for key in ("genre_fix", "grouping_fix", "year_fix")):
                errors.append(f"standalone ID3 upgrade without a metadata fix is not allowed: {path}")
                continue
            try:
                source_version = ID3(path).version[1]
            except ID3NoHeaderError:
                source_version = None
            if source_version != 2:
                errors.append(
                    f"ID3 upgrade requested, but file is not ID3v2.2: {path} "
                    f"(found {source_version!r})"
                )
                continue
        if "genre_fix" in row:
            current = read_genre(path)
            if current.strip() != (row["genre_fix"]["from"] or "").strip():
                errors.append(f"genre changed since survey: {path}")
                continue
        if "grouping_fix" in row:
            current_grouping, _ = read_grouping(path)
            if current_grouping != row["grouping_fix"].get("existing", []):
                errors.append(f"grouping changed since survey: {path}")
                continue
        if "year_fix" in row:
            problem = year_fix_error(row["year_fix"])
            if problem:
                errors.append(f"invalid year_fix for {path}: {problem}")
                continue
            if read_year(path) != row["year_fix"]["from"]:
                errors.append(f"year changed since survey: {path}")
                continue
        validated.append({**row, "path": str(path), "before_sha256": actual_hash})
    if errors:
        raise RuntimeError("Validation failed:\n" + "\n".join(errors))
    return validated, []


def _apply_one_metadata_write(
    txn,
    path,
    *,
    proposed_genre,
    proposed_grouping,
    proposed_year,
    original_genre,
    original_grouping,
    original_year,
    year_basis,
    year_grade,
    version,
    id3_upgrade,
    tag_backup_root,
    index,
    undo_path,
):
    before_hash = sha256_file(path)
    before_audio = sha256_bytes(audio_payload(path))
    before_serato = serato_digest(path)
    before_serato_payload = serato_payload_digest(path)
    skip_reason = tag_skip_reason(path, id3_upgrade)
    tag_backup = None
    tag_was_changed = False
    id3_upgraded = False
    excluded_frames = {"TCON", "TIT1"}
    if proposed_year is not None:
        excluded_frames.update({"TDRC", "TYER"})
    preserved_id3 = (
        id3_preservation_snapshot(path, excluded_frames) if id3_upgrade else None
    )

    if not skip_reason:
        tag_backup = tag_backup_root / f"{index:05d}-metadata-{path.name}"
        tag_backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, tag_backup)
        if sha256_file(tag_backup) != before_hash:
            raise RuntimeError(f"per-item tag backup verification failed: {tag_backup}")
        txn.on_rollback(lambda: mutation_engine.restore_from_backup(tag_backup, path, before_hash))
        try:
            write_metadata(
                path,
                genre_value=proposed_genre,
                grouping_value=proposed_grouping,
                year_value=proposed_year,
                version=3 if id3_upgrade else version,
                id3_upgrade=id3_upgrade,
            )
            tag_was_changed = True
            id3_upgraded = bool(id3_upgrade)
            txn.mark_mutated()
            if proposed_genre is not None and read_genre(path) != proposed_genre:
                raise RuntimeError("Genre tag verification failed")
            if proposed_grouping is not None:
                actual_grouping, _ = read_grouping(path)
                expected_grouping = [
                    part.strip() for part in proposed_grouping.split(";") if part.strip()
                ]
                if actual_grouping != expected_grouping:
                    raise RuntimeError("Grouping tag verification failed")
            if proposed_year is not None:
                verify_year_field(path, proposed_year, 3 if id3_upgrade else version)
            if sha256_bytes(audio_payload(path)) != before_audio:
                raise RuntimeError("audio payload changed")
            if id3_upgrade:
                if ID3(path).version[1] != 3:
                    raise RuntimeError("ID3v2.2 -> v2.3 upgrade verification failed")
                if id3_preservation_snapshot(path, excluded_frames) != preserved_id3:
                    raise RuntimeError("non-target ID3 frame preservation verification failed")
                if serato_payload_digest(path) != before_serato_payload:
                    raise RuntimeError("Serato frame payload changed during ID3 upgrade")
            elif serato_digest(path) != before_serato:
                if serato_payload_digest(path) == before_serato_payload:
                    raise SeratoEncodingOnlyChange("encoding-byte-only Serato change; rolled back")
                raise RuntimeError("Serato frames changed")
        except SeratoEncodingOnlyChange:
            mutation_engine.restore_from_backup(tag_backup, path, before_hash)
            tag_was_changed = False
            id3_upgraded = False
            skip_reason = "Serato encoding-only change on write; skipped out of caution"

    after_hash = sha256_file(path)
    if sha256_bytes(audio_payload(path)) != before_audio:
        raise RuntimeError(f"post-write audio verification failed: {path}")
    if id3_upgrade and tag_was_changed:
        if ID3(path).version[1] != 3:
            raise RuntimeError(f"post-write ID3 version verification failed: {path}")
        if id3_preservation_snapshot(path, excluded_frames) != preserved_id3:
            raise RuntimeError(f"post-write non-target ID3 preservation failed: {path}")
        if serato_payload_digest(path) != before_serato_payload:
            raise RuntimeError(f"post-write Serato payload verification failed: {path}")
    elif serato_digest(path) != before_serato:
        raise RuntimeError(f"post-write Serato verification failed: {path}")
    if not skip_reason and proposed_genre is not None and read_genre(path) != proposed_genre:
        raise RuntimeError(f"post-write Genre verification failed: {path}")
    if not skip_reason and proposed_grouping is not None:
        actual_grouping, _ = read_grouping(path)
        expected_grouping = [part.strip() for part in proposed_grouping.split(";") if part.strip()]
        if actual_grouping != expected_grouping:
            raise RuntimeError(f"post-write Grouping verification failed: {path}")
    if not skip_reason and proposed_year is not None:
        verify_year_field(path, proposed_year, 3 if id3_upgrade else version)
    record = {
        "index": index, "kind": "metadata", "path": str(path),
        "tag_changed": tag_was_changed, "tag_skipped": bool(skip_reason),
        "tag_skip_reason": skip_reason,
        "id3_upgraded": "2.2->2.3" if id3_upgraded else None,
        "genre_requested": proposed_genre is not None,
        "grouping_requested": proposed_grouping is not None,
        "year_requested": proposed_year is not None,
        "old_genre": original_genre,
        "new_genre": proposed_genre,
        "old_grouping": original_grouping,
        "new_grouping": proposed_grouping,
        "old_year": original_year,
        "new_year": proposed_year,
        "year_basis": year_basis,
        "year_grade": year_grade,
        "tag_backup": str(tag_backup) if tag_backup else None,
        "before_sha256": before_hash, "after_sha256": after_hash,
    }
    txn.record(**{k: v for k, v in record.items() if k not in {"index", "path"}})
    with undo_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record


def apply(plan_path: Path, root: Path, manifest: dict, out_dir: Path, label: str, heartbeat) -> None:
    items, _ = load_and_validate(plan_path, root)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    tag_backup_root = out_dir / f"{label}-tag-file-backups" / stamp
    undo_path = out_dir / f"{label}-undo-{stamp}.jsonl"
    result_path = out_dir / f"{label}-result-{stamp}.json"
    journal_path = out_dir / f"{label}-recovery-{stamp}.jsonl"
    engine = mutation_engine.MutationEngine(journal_path, heartbeat, manifest, root, label)
    engine.start({"plan": str(plan_path), "items": len(items)})

    completed = []
    actionable = [
        row for row in items
        if any(key in row for key in ("genre_fix", "grouping_fix", "year_fix"))
    ]
    for index, row in enumerate(actionable, start=1):
        path = Path(row["path"])
        existing_grouping, version = read_grouping(path)
        original_genre = read_genre(path)
        original_year = read_year(path)
        proposed_genre = row.get("genre_fix", {}).get("to")
        proposed_year = row.get("year_fix", {}).get("to")
        proposed_grouping = None
        if "grouping_fix" in row:
            proposed_grouping = combined_grouping(
                existing_grouping, row["grouping_fix"]["add_terms"],
                row["grouping_fix"].get("remove_terms", []),
            )
        with engine.item(
            index, kind=mutation_engine.METADATA, source=path,
            expected_sha256=row["before_sha256"],
            extra_intent={
                "proposed_genre": proposed_genre,
                "add_grouping_terms": row.get("grouping_fix", {}).get("add_terms", []),
                "remove_grouping_terms": row.get("grouping_fix", {}).get("remove_terms", []),
                "year_fix": row.get("year_fix"),
                "id3_upgrade": row.get("id3_upgrade"),
            },
        ) as txn:
            completed.append(_apply_one_metadata_write(
                txn, path,
                proposed_genre=proposed_genre,
                proposed_grouping=proposed_grouping,
                proposed_year=proposed_year,
                original_genre=original_genre,
                original_grouping=existing_grouping,
                original_year=original_year,
                year_basis=row.get("year_fix", {}).get("basis"),
                year_grade=row.get("year_fix", {}).get("grade"),
                version=version,
                id3_upgrade=row.get("id3_upgrade"),
                tag_backup_root=tag_backup_root,
                index=index,
                undo_path=undo_path,
            ))

    result = {
        "mode": f"{label}-apply",
        "plan": str(plan_path),
        "root": str(root),
        "verified_backup": manifest.get("backup_id"),
        "transactions": len(completed),
        "genre_changed": sum(1 for r in completed if r["genre_requested"] and r["tag_changed"]),
        "grouping_changed": sum(1 for r in completed if r["grouping_requested"] and r["tag_changed"]),
        "year_changed": sum(1 for r in completed if r["year_requested"] and r["tag_changed"]),
        "skipped_id3v22": sum(
            1 for r in completed if (r["tag_skip_reason"] or "").startswith("ID3v2.2")
        ),
        "id3_upgraded": sum(1 for r in completed if r["id3_upgraded"] == "2.2->2.3"),
        "duplicate_flags_in_plan_not_acted_on": sum(1 for row in items if "duplicate_of" in row),
        "undo_log": str(undo_path),
        "tag_backup_root": str(tag_backup_root),
        "recovery_journal": str(journal_path),
    }
    engine.finish(result)
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({**result, "result_path": str(result_path)}, indent=2))


def undo(undo_path: Path, root: Path, label: str, heartbeat) -> None:
    records = [json.loads(line) for line in undo_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    journal_path = undo_path.with_name(undo_path.stem + "-undo-recovery.jsonl")
    engine = mutation_engine.MutationEngine(
        journal_path, heartbeat, manifest=None, source_root=None,
        label=f"{label}-undo", allow_unbacked=True,
    )
    engine.start({"undo_log": str(undo_path), "records": len(records)})

    restored = 0
    for position, record in enumerate(reversed(records), start=1):
        path = Path(record["path"])
        with engine.item(
            position, kind=mutation_engine.METADATA, source=path, require_backup=False,
            extra_intent={"undoing_index": record.get("index"), "restore_from": record.get("tag_backup")},
        ) as txn:
            if record["tag_changed"]:
                backup = Path(record["tag_backup"])
                if sha256_file(backup) != record["before_sha256"]:
                    raise RuntimeError(f"tag backup mismatch: {backup}")
                shutil.copy2(backup, path)
                txn.mark_mutated()
            if sha256_file(path) != record["before_sha256"]:
                raise RuntimeError(f"undo verification failed: {path}")
            txn.record(restored_to=str(path), sha256=record["before_sha256"])
            restored += 1

    result = {"mode": f"{label}-undo", "restored": restored, "recovery_journal": str(journal_path)}
    engine.finish(result)
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--plan", type=Path, help="plan JSON from survey_new_music_intake.py (required unless --undo)")
    parser.add_argument("--backup-manifest", type=Path)
    parser.add_argument("--require-serato-backup", action="store_true")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--label", default="new-music-intake")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--undo", type=Path)
    parser.add_argument("--lock-folder")
    parser.add_argument("--session")
    args = parser.parse_args()

    root = args.root.resolve()
    out_dir = args.out_dir or (
        args.plan.resolve().parent if args.plan else args.undo.resolve().parent
        if args.undo else Path(".")
    )

    if args.undo:
        if not args.lock_folder or not args.session:
            raise SystemExit("--lock-folder and --session are required with --undo")
        lock = lock_guard.require_lock_or_exit(args.lock_folder, args.session)
        heartbeat = lock_guard.LockHeartbeat(args.lock_folder, args.session)
        heartbeat.pin_run_id(lock)
        undo(args.undo.resolve(), root, args.label, heartbeat)
    elif args.apply:
        if not args.plan:
            raise SystemExit("--plan is required with --apply")
        if not args.backup_manifest:
            raise SystemExit(
                "--backup-manifest is required with --apply.\n"
                "  mkdir -p <backup-dir>\n"
                "  python3 -c \"import shutil; shutil.copytree('<root>', '<backup-dir>', dirs_exist_ok=True)\"\n"
                "  python3 scripts/backup_manifest.py verify --source-root <root> --backup <backup-dir> --out manifest.json"
            )
        if not args.lock_folder or not args.session:
            raise SystemExit("--lock-folder and --session are required with --apply")
        try:
            heartbeat, manifest = mutation_engine.run_preflight(
                lock_folder=args.lock_folder, session=args.session,
                manifest_path=args.backup_manifest, source_root=root,
                serato_required=args.require_serato_backup,
            )
        except backup_manifest.BackupGateError as exc:
            raise SystemExit(f"REFUSING TO MUTATE: {exc}") from exc
        apply(args.plan, root, manifest, out_dir, args.label, heartbeat)
    else:
        if not args.plan:
            raise SystemExit("--plan is required for a dry run")
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        print(json.dumps({"mode": "dry-run", **plan["counts"]}, indent=2))


if __name__ == "__main__":
    main()
