#!/usr/bin/env python3
"""Apply the approved NIGER -> Source: Nigeria plan (nigeria-plan.json).

Two tracks, move-only + Grouping (TIT1) write — no Genre change. Uses the
same verify-before/after-every-write and fsync'd undo log pattern as
scripts/apply_version_genre_plan.py and the historical
apply_events_grouping.py, sized to this specific plan rather than folded
into either generalized pipeline (a move+Grouping combination hasn't
recurred elsewhere yet).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError, TIT1

HERE = Path(__file__).resolve().parent
ROOT = Path("/Users/djb.a.e/Music/DJ-Music/Music/NIGER").resolve()
PLAN = HERE / "nigeria-plan.json"
BACKUP = (HERE / "../../backups/niger-pre-sort-2026-08-15/NIGER").resolve()


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
    start, end = 0, len(data)
    if data[:3] == b"ID3" and len(data) >= 10:
        size = ((data[6] & 127) << 21) | ((data[7] & 127) << 14) | ((data[8] & 127) << 7) | (data[9] & 127)
        start = 10 + size + (10 if data[5] & 16 else 0)
    if len(data) >= 128 and data[-128:-125] == b"TAG":
        end -= 128
    return data[start:end]


def serato_digest(tags: ID3) -> str:
    values = []
    for frame in tags.values():
        haystack = " ".join((
            frame.HashKey,
            str(getattr(frame, "desc", "")),
            str(getattr(frame, "owner", "")),
            str(getattr(frame, "filename", "")),
        )).casefold()
        if "serato" not in haystack:
            continue
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


def read_grouping(path: Path) -> tuple[list[str], int]:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return [], 4
    values = []
    for frame in tags.getall("TIT1"):
        text = getattr(frame, "text", [])
        values.extend(str(value).strip() for value in text if str(value).strip())
    return values, tags.version[1]


def combined_grouping(existing: list[str], proposed: str) -> str:
    values, seen = [], set()
    for value in [*existing, proposed]:
        normalized = value.strip().casefold()
        if normalized and normalized not in seen:
            values.append(value.strip())
            seen.add(normalized)
    return "; ".join(values)


def write_grouping(path: Path, value: str, version: int) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    encoding = 3 if version == 4 else 1
    tags.delall("TIT1")
    tags.add(TIT1(encoding=encoding, text=[value]))
    tags.save(path, v2_version=version)


def load_and_validate() -> list[dict]:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    if plan.get("mode") != "preview-only" or not plan.get("rules", {}).get("approval_required"):
        raise RuntimeError("unexpected plan mode")
    items = plan["items"]
    if len(items) != plan["track_count"]:
        raise RuntimeError("plan item count does not match its own declared track_count")
    if not BACKUP.is_dir():
        raise RuntimeError(f"verified pre-apply backup is missing: {BACKUP}")

    errors = []
    for item in items:
        source = Path(item["source"]).resolve()
        destination = Path(item["destination"]).resolve()
        if ROOT not in source.parents or ROOT not in destination.parents:
            errors.append(f"outside target root: {source} -> {destination}")
            continue
        if not source.is_file():
            errors.append(f"missing source: {source}")
            continue
        if sha256_file(source) != item["sha256"]:
            errors.append(f"source hash changed since preview: {source}")
        backup_copy = BACKUP / source.name
        if not backup_copy.is_file() or sha256_file(backup_copy) != item["sha256"]:
            errors.append(f"backup copy missing or mismatched: {backup_copy}")
    if errors:
        raise RuntimeError("Nigeria plan validation failed:\n" + "\n".join(errors))
    return items


def apply() -> None:
    items = load_and_validate()
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    undo_path = HERE / f"nigeria-undo-{stamp}.jsonl"
    result_path = HERE / f"nigeria-result-{stamp}.json"
    completed = []

    for index, item in enumerate(items, start=1):
        source = Path(item["source"])
        destination = Path(item["destination"])
        before_hash = sha256_file(source)
        before_audio = sha256_bytes(audio_payload(source))
        tags = ID3(source)
        before_serato = serato_digest(tags)
        current_grouping, id3_version = read_grouping(source)
        new_grouping = combined_grouping(current_grouping, item["proposed_grouping"])

        try:
            write_grouping(source, new_grouping, id3_version)
            after_values, after_version = read_grouping(source)
            if after_values != [new_grouping] or after_version != id3_version:
                raise RuntimeError("Grouping verification failed")
            if sha256_bytes(audio_payload(source)) != before_audio:
                raise RuntimeError("audio payload changed")
            if serato_digest(ID3(source)) != before_serato:
                raise RuntimeError("Serato frames changed")
        except Exception:
            shutil.copy2(BACKUP / source.name, source)
            raise

        destination.parent.mkdir(parents=True, exist_ok=True)
        os.rename(source, destination)

        if sha256_bytes(audio_payload(destination)) != before_audio:
            raise RuntimeError(f"post-move audio verification failed: {destination}")
        if serato_digest(ID3(destination)) != before_serato:
            raise RuntimeError(f"post-move Serato verification failed: {destination}")

        record = {
            "index": index,
            "source": str(source),
            "destination": str(destination),
            "old_grouping": current_grouping,
            "new_grouping": new_grouping,
            "id3_version": id3_version,
            "before_sha256": before_hash,
            "after_sha256": sha256_file(destination),
            "audio_payload_sha256": before_audio,
            "serato_frames_sha256": before_serato,
        }
        with undo_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        completed.append(record)

    result = {
        "mode": "nigeria-apply",
        "plan": str(PLAN),
        "verified_backup": str(BACKUP),
        "tracks_processed": len(completed),
        "files_moved": len(completed),
        "grouping_tags_changed": len(completed),
        "genre_tags_changed": 0,
        "audio_payloads_changed": 0,
        "serato_frames_changed": 0,
        "undo_log": str(undo_path),
    }
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({**result, "result_path": str(result_path)}, indent=2))


def undo(undo_path: Path) -> None:
    records = [json.loads(line) for line in undo_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    restored = 0
    for record in reversed(records):
        source = Path(record["source"])
        destination = Path(record["destination"])
        if source.exists():
            raise RuntimeError(f"undo source already exists: {source}")
        source.parent.mkdir(parents=True, exist_ok=True)
        os.rename(destination, source)
        backup_copy = BACKUP / source.name
        shutil.copy2(backup_copy, source)
        if sha256_file(source) != record["before_sha256"]:
            raise RuntimeError(f"undo verification failed: {source}")
        restored += 1
    print(json.dumps({"mode": "nigeria-undo", "restored": restored}, indent=2))


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
        items = load_and_validate()
        print(json.dumps({
            "mode": "dry-run",
            "tracks": len(items),
            "moves": len(items),
            "grouping_tag_changes": len(items),
            "validation": "passed",
            "changes_made": 0,
        }, indent=2))


if __name__ == "__main__":
    main()
