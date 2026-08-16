#!/usr/bin/env python3
"""Apply the approved genre-folder and genre-tag plan with verification and undo."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TCON


HERE = Path(__file__).resolve().parent
ROOT = Path("/Users/djb.a.e/Music/DJ-Music/Music/Events").resolve()
PLAN = HERE / "events-version-genre-plan.json"
BACKUP = Path("/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/backups/events-pre-sort-2026-08-15/Events")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def audio_payload(path: Path) -> bytes:
    data = path.read_bytes()
    suffix = path.suffix.casefold()
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
            frame.HashKey,
            str(getattr(frame, "desc", "")),
            str(getattr(frame, "owner", "")),
            str(getattr(frame, "filename", "")),
        )).casefold()
        if "serato" in haystack:
            value = {
                "key": frame.HashKey,
                "frame": frame.FrameID,
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
    return sha256_bytes(b"[]")


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
    raise RuntimeError(f"genre write not supported for {path.suffix}: {path}")


def write_genre(path: Path, value: str) -> None:
    suffix = path.suffix.casefold()
    if suffix == ".mp3":
        try:
            tags = ID3(path)
            original_v2_version = tags.version[1]
        except ID3NoHeaderError:
            tags = ID3()
            original_v2_version = 4
        text_encoding = 3 if original_v2_version == 4 else 1
        tags.delall("TCON")
        tags.add(TCON(encoding=text_encoding, text=[value]))
        tags.save(path, v2_version=original_v2_version)
        return
    if suffix == ".flac":
        audio = FLAC(path)
        audio["genre"] = [value]
        audio.save()
        return
    raise RuntimeError(f"genre write not supported for {path.suffix}: {path}")


def tag_skip_reason(path: Path, item: dict) -> str | None:
    """Preserve obsolete ID3v2.2 tags instead of silently upgrading them."""
    if not item["tag_change"] or path.suffix.casefold() != ".mp3":
        return None
    try:
        if ID3(path).version[1] == 2:
            return "ID3v2.2 preserved; Genre metadata write skipped"
    except ID3NoHeaderError:
        pass
    return None


def load_and_validate() -> tuple[dict, list[dict]]:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    if plan.get("mode") != "preview-only" or not plan.get("rules", {}).get("approval_required"):
        raise RuntimeError("unexpected plan mode")
    items = plan["items"]
    if len(items) != 3046:
        raise RuntimeError(f"expected 3046 plan items, found {len(items)}")
    if not BACKUP.is_dir():
        raise RuntimeError(f"verified pre-genre backup is missing: {BACKUP}")

    errors = []
    destinations: dict[str, str] = {}
    source_paths = {item["source"] for item in items}
    for item in items:
        source = Path(item["source"]).resolve()
        destination = Path(item["destination"]).resolve()
        if ROOT not in source.parents or ROOT not in destination.parents:
            errors.append(f"outside target root: {source} -> {destination}")
            continue
        if not source.is_file():
            errors.append(f"missing source: {source}")
            continue
        actual_hash = sha256_file(source)
        if actual_hash != item["sha256"]:
            errors.append(f"source hash changed since preview: {source}")
        destination_key = str(destination).casefold()
        if destination_key in destinations:
            errors.append(f"destination collision: {destination}")
        destinations[destination_key] = str(source)
        if item["move"] and destination.exists() and str(destination) not in source_paths:
            errors.append(f"destination already exists: {destination}")
        if item["tag_change"]:
            if source.suffix.casefold() not in {".mp3", ".flac"}:
                errors.append(f"unsupported tag target: {source}")
            current = read_genre(source)
            if current != item["current_genre"]:
                errors.append(f"genre changed since preview: {source}: {current!r} != {item['current_genre']!r}")
    if errors:
        raise RuntimeError("Genre plan validation failed:\n" + "\n".join(errors))
    return plan, items


def apply() -> None:
    plan, items = load_and_validate()
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    tag_backup_root = HERE / "events-tag-file-backups" / stamp
    undo_path = HERE / f"events-undo-{stamp}.jsonl"
    result_path = HERE / f"events-result-{stamp}.json"
    completed = []

    for index, item in enumerate(items, start=1):
        source = Path(item["source"])
        destination = Path(item["destination"])
        before_hash = sha256_file(source)
        before_audio = sha256_bytes(audio_payload(source))
        before_serato = serato_digest(source)
        tag_backup = None
        tag_was_changed = False
        skip_reason = tag_skip_reason(source, item)

        if item["tag_change"] and not skip_reason:
            tag_backup = tag_backup_root / item["source_relative"]
            tag_backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, tag_backup)
            try:
                write_genre(source, item["proposed_genre"])
                if read_genre(source) != item["proposed_genre"]:
                    raise RuntimeError("Genre tag verification failed")
                if sha256_bytes(audio_payload(source)) != before_audio:
                    raise RuntimeError("audio payload changed")
                if serato_digest(source) != before_serato:
                    raise RuntimeError("Serato frames changed")
                tag_was_changed = True
            except Exception:
                shutil.copy2(tag_backup, source)
                raise

        try:
            if item["move"]:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.rename(source, destination)
            else:
                destination = source
        except Exception:
            if tag_was_changed and tag_backup is not None:
                shutil.copy2(tag_backup, source)
            raise

        after_hash = sha256_file(destination)
        if sha256_bytes(audio_payload(destination)) != before_audio:
            raise RuntimeError(f"post-move audio verification failed: {destination}")
        if serato_digest(destination) != before_serato:
            raise RuntimeError(f"post-move Serato verification failed: {destination}")
        if item["proposed_genre"] and not skip_reason and read_genre(destination) != item["proposed_genre"]:
            raise RuntimeError(f"post-move Genre verification failed: {destination}")

        record = {
            "index": index,
            "source": str(source),
            "destination": str(destination),
            "moved": bool(item["move"]),
            "tag_changed": tag_was_changed,
            "tag_skipped": bool(skip_reason),
            "tag_skip_reason": skip_reason,
            "old_genre": item["current_genre"],
            "new_genre": item["proposed_genre"],
            "tag_backup": str(tag_backup) if tag_backup else None,
            "before_sha256": before_hash,
            "after_sha256": after_hash,
            "audio_payload_sha256": before_audio,
            "serato_frames_sha256": before_serato,
        }
        with undo_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        completed.append(record)

    missing_destinations = [r["destination"] for r in completed if not Path(r["destination"]).is_file()]
    surviving_sources = [r["source"] for r in completed if r["moved"] and Path(r["source"]).exists()]
    result = {
        "mode": "events-apply",
        "plan": str(PLAN),
        "verified_backup": str(BACKUP.parent),
        "tracks_processed": len(completed),
        "files_moved": sum(r["moved"] for r in completed),
        "genre_tags_changed": sum(r["tag_changed"] for r in completed),
        "genre_tags_skipped": sum(r["tag_skipped"] for r in completed),
        "audio_payloads_changed": 0,
        "serato_frames_changed": 0,
        "missing_destinations": missing_destinations,
        "surviving_moved_sources": surviving_sources,
        "undo_log": str(undo_path),
        "tag_backup_root": str(tag_backup_root),
        "genre_counts": plan["counts"]["genre_folders"],
    }
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({**result, "result_path": str(result_path)}, indent=2))


def undo(undo_path: Path) -> None:
    records = [json.loads(line) for line in undo_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    restored = 0
    for record in reversed(records):
        source = Path(record["source"])
        destination = Path(record["destination"])
        if record["moved"]:
            if source.exists():
                raise RuntimeError(f"undo source already exists: {source}")
            source.parent.mkdir(parents=True, exist_ok=True)
            os.rename(destination, source)
        if record["tag_changed"]:
            backup = Path(record["tag_backup"])
            if sha256_file(backup) != record["before_sha256"]:
                raise RuntimeError(f"tag backup mismatch: {backup}")
            shutil.copy2(backup, source)
        if sha256_file(source) != record["before_sha256"]:
            raise RuntimeError(f"undo verification failed: {source}")
        restored += 1
    print(json.dumps({"mode": "events-undo", "restored": restored}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--undo", type=Path)
    args = parser.parse_args()
    if args.undo:
        undo(args.undo.resolve())
    elif args.apply:
        apply()
    else:
        plan, items = load_and_validate()
        skipped = sum(bool(tag_skip_reason(Path(item["source"]), item)) for item in items)
        print(json.dumps({
            "mode": "dry-run",
            "tracks": len(items),
            "moves": sum(item["move"] for item in items),
            "genre_tag_changes_requested": sum(item["tag_change"] for item in items),
            "genre_tag_changes_to_apply": sum(item["tag_change"] for item in items) - skipped,
            "id3v22_tag_writes_skipped": skipped,
            "unresolved": plan["counts"]["unresolved_genre"],
            "validation": "passed",
            "changes_made": 0,
        }, indent=2))


if __name__ == "__main__":
    main()
