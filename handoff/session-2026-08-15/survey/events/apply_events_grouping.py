#!/usr/bin/env python3
"""Apply the approved Events Grouping metadata preview, with verification and undo."""

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
ROOT = Path("/Users/djb.a.e/Music/DJ-Music/Music/Events").resolve()
SERATO = Path("/Users/djb.a.e/Music/_Serato_").resolve()
PREVIEW = HERE / "events-grouping-preview.json"
BACKUP_ROOT = Path("/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/backups/events-pre-grouping-2026-08-15")
BACKUP_EVENTS = BACKUP_ROOT / "Events"
BACKUP_SERATO = BACKUP_ROOT / "_Serato_"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def mp3_audio_payload_digest(path: Path) -> str:
    data = path.read_bytes()
    start, end = 0, len(data)
    if data[:3] == b"ID3" and len(data) >= 10:
        size = ((data[6] & 127) << 21) | ((data[7] & 127) << 14) | ((data[8] & 127) << 7) | (data[9] & 127)
        start = 10 + size + (10 if data[5] & 16 else 0)
    if len(data) >= 128 and data[-128:-125] == b"TAG":
        end -= 128
    return sha256_bytes(data[start:end])


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
    values = []
    seen = set()
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


def load_items() -> list[dict]:
    preview = json.loads(PREVIEW.read_text(encoding="utf-8"))
    if preview.get("mode") != "preview-only" or not preview.get("rules", {}).get("approval_required"):
        raise RuntimeError("unexpected preview mode")
    if not BACKUP_EVENTS.is_dir() or not BACKUP_SERATO.is_dir():
        raise RuntimeError("fresh verified Events/Serato backup is missing")
    return preview["items"]


def classify(items: list[dict]) -> tuple[list[dict], list[dict]]:
    applicable, skipped = [], []
    for item in items:
        path = Path(item["path"]).resolve()
        reason = None
        if ROOT not in path.parents:
            raise RuntimeError(f"outside Events root: {path}")
        if not path.is_file():
            raise RuntimeError(f"missing file: {path}")
        if item["action"] == "skip_tag_read_error":
            reason = "tag read error in preview"
        elif path.suffix.casefold() != ".mp3":
            reason = f"unsupported safe Grouping writer: {path.suffix}"
        else:
            current, version = read_grouping(path)
            if version == 2:
                reason = "ID3v2.2 preserved"
            else:
                expected = item["current_grouping"]
                actual = " ; ".join(current)
                if actual != expected.strip():
                    raise RuntimeError(f"Grouping changed since preview: {path}: {actual!r} != {expected!r}")
                item = {**item, "current_values": current, "id3_version": version}
        if reason:
            skipped.append({"path": str(path), "reason": reason})
        else:
            applicable.append(item)
    return applicable, skipped


def apply() -> None:
    items = load_items()
    applicable, skipped = classify(items)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    undo_path = HERE / f"events-grouping-undo-{stamp}.jsonl"
    result_path = HERE / f"events-grouping-result-{stamp}.json"
    changed = []

    for index, item in enumerate(applicable, start=1):
        path = Path(item["path"])
        relative = path.relative_to(ROOT)
        backup = BACKUP_EVENTS / relative
        if not backup.is_file():
            raise RuntimeError(f"backup file missing: {backup}")
        before_hash = sha256_file(path)
        if sha256_file(backup) != before_hash:
            raise RuntimeError(f"backup mismatch: {path}")
        before_audio = mp3_audio_payload_digest(path)
        tags = ID3(path)
        before_serato = serato_digest(tags)
        old_values = item["current_values"]
        new_value = combined_grouping(old_values, item["proposed_grouping"])

        try:
            write_grouping(path, new_value, item["id3_version"])
            after_values, after_version = read_grouping(path)
            if after_values != [new_value] or after_version != item["id3_version"]:
                raise RuntimeError("Grouping verification failed")
            if mp3_audio_payload_digest(path) != before_audio:
                raise RuntimeError("audio payload changed")
            if serato_digest(ID3(path)) != before_serato:
                raise RuntimeError("Serato frames changed")
        except Exception:
            shutil.copy2(backup, path)
            raise

        record = {
            "index": index,
            "path": str(path),
            "backup": str(backup),
            "old_grouping": old_values,
            "new_grouping": new_value,
            "before_sha256": before_hash,
            "after_sha256": sha256_file(path),
            "audio_payload_sha256": before_audio,
            "serato_frames_sha256": before_serato,
        }
        with undo_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        changed.append(record)

    result = {
        "mode": "events-grouping-apply",
        "preview": str(PREVIEW),
        "verified_backup": str(BACKUP_ROOT),
        "tracks_in_preview": len(items),
        "grouping_tags_changed": len(changed),
        "tags_skipped": len(skipped),
        "skipped": skipped,
        "files_moved": 0,
        "files_deleted": 0,
        "genre_tags_changed": 0,
        "audio_payloads_changed": 0,
        "serato_frames_changed": 0,
        "undo_log": str(undo_path),
    }
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({**result, "result_path": str(result_path)}, indent=2))


def dry_run() -> None:
    items = load_items()
    applicable, skipped = classify(items)
    reasons = {}
    for item in skipped:
        reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
    print(json.dumps({
        "mode": "dry-run",
        "tracks": len(items),
        "grouping_tags_to_change": len(applicable),
        "tags_to_skip": len(skipped),
        "skip_reasons": reasons,
        "changes_made": 0,
        "validation": "passed",
    }, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    apply() if args.apply else dry_run()


if __name__ == "__main__":
    main()
