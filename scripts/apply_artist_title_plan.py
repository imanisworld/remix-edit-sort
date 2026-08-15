#!/usr/bin/env python3
"""Apply an approved Artist/Title backfill plan, with verification and undo.

Same verify/rollback/undo discipline as apply_grouping_plan.py and
apply_version_genre_plan.py: audio payload and Serato frames are hashed
before and after every write, any mismatch rolls the file back immediately,
and a verified-benign Serato encoding-only difference (see
apply_version_genre_plan.py's SeratoEncodingOnlyChange for why that can
legitimately happen) is treated as a skip rather than a hard failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError, TPE1, TIT2


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


def serato_payload_digest(tags: ID3) -> str:
    values = []
    for frame in tags.values():
        haystack = " ".join((
            frame.HashKey,
            str(getattr(frame, "desc", "")),
            str(getattr(frame, "owner", "")),
        )).casefold()
        if "serato" not in haystack:
            continue
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


class SeratoEncodingOnlyChange(Exception):
    pass


def read_artist_title(path: Path) -> tuple[str, str, int]:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return "", "", 4
    artist_frames = tags.getall("TPE1")
    title_frames = tags.getall("TIT2")
    artist = str(artist_frames[0]).strip() if artist_frames else ""
    title = str(title_frames[0]).strip() if title_frames else ""
    return artist, title, tags.version[1]


def write_artist_title(path: Path, artist: str, title: str, version: int) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    encoding = 3 if version == 4 else 1
    tags.delall("TPE1")
    tags.add(TPE1(encoding=encoding, text=[artist]))
    tags.delall("TIT2")
    tags.add(TIT2(encoding=encoding, text=[title]))
    tags.save(path, v2_version=version)


def load_and_validate(plan_path: Path, backup: Path) -> tuple[dict, list[dict]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("mode") != "preview-only" or not plan.get("rules", {}).get("approval_required"):
        raise RuntimeError("unexpected plan mode")
    items = plan["items"]
    if not backup.is_dir():
        raise RuntimeError(f"verified pre-apply backup is missing: {backup}")

    errors = []
    for item in items:
        path = Path(item["path"])
        if not path.is_file():
            errors.append(f"missing file: {path}")
            continue
        if path.suffix.casefold() == ".mp3" and item.get("id3_version") is None:
            try:
                tags = ID3(path)
                item["id3_version"] = tags.version[1]
            except ID3NoHeaderError:
                item["id3_version"] = 4
        current_artist, current_title, _ = read_artist_title(path)
        if current_artist != item["current_artist"] or current_title != item["current_title"]:
            errors.append(
                f"Artist/Title changed since preview: {path}: "
                f"({current_artist!r}, {current_title!r}) != ({item['current_artist']!r}, {item['current_title']!r})"
            )
    if errors:
        raise RuntimeError("Artist/Title plan validation failed:\n" + "\n".join(errors))
    return plan, items


def apply(plan_path: Path, backup: Path, backup_relative_to: Path, out_dir: Path, label: str) -> None:
    plan, items = load_and_validate(plan_path, backup)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    undo_path = out_dir / f"{label}-artist-title-undo-{stamp}.jsonl"
    result_path = out_dir / f"{label}-artist-title-result-{stamp}.json"
    completed = []
    skipped_encoding_only = []

    for index, item in enumerate(items, start=1):
        path = Path(item["path"])
        relative = path.relative_to(backup_relative_to)
        backup_copy = backup / relative
        if not backup_copy.is_file():
            raise RuntimeError(f"backup file missing: {backup_copy}")
        before_hash = sha256_file(path)
        if sha256_file(backup_copy) != before_hash:
            raise RuntimeError(f"backup mismatch: {path}")
        before_audio = sha256_bytes(audio_payload(path))
        before_tags = ID3(path)
        before_serato = serato_digest(before_tags)
        before_serato_payload = serato_payload_digest(before_tags)
        version = before_tags.version[1]

        try:
            write_artist_title(path, item["proposed_artist"], item["proposed_title"], version)
            after_artist, after_title, after_version = read_artist_title(path)
            if after_artist != item["proposed_artist"] or after_title != item["proposed_title"]:
                raise RuntimeError("Artist/Title verification failed")
            if after_version != version:
                raise RuntimeError("ID3 version changed")
            if sha256_bytes(audio_payload(path)) != before_audio:
                raise RuntimeError("audio payload changed")
            after_tags = ID3(path)
            if serato_digest(after_tags) != before_serato:
                if serato_payload_digest(after_tags) == before_serato_payload:
                    raise SeratoEncodingOnlyChange(
                        "Artist/Title write forced an ID3-version-compliance re-encoding of a Serato "
                        "frame's encoding byte (payload verified byte-identical); write rolled back"
                    )
                raise RuntimeError("Serato frames changed")
        except SeratoEncodingOnlyChange as exc:
            shutil.copy2(backup_copy, path)
            skipped_encoding_only.append({"path": str(path), "reason": str(exc)})
            continue
        except Exception:
            shutil.copy2(backup_copy, path)
            raise

        record = {
            "index": index,
            "path": str(path),
            "backup": str(backup_copy),
            "old_artist": item["current_artist"],
            "old_title": item["current_title"],
            "new_artist": item["proposed_artist"],
            "new_title": item["proposed_title"],
            "before_sha256": before_hash,
            "after_sha256": sha256_file(path),
            "audio_payload_sha256": before_audio,
            "serato_frames_sha256": before_serato,
        }
        with undo_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        completed.append(record)

    result = {
        "mode": f"{label}-artist-title-apply",
        "plan": str(plan_path),
        "verified_backup": str(backup),
        "tracks_in_plan": len(items),
        "tags_changed": len(completed),
        "skipped_serato_encoding_only": skipped_encoding_only,
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
        path = Path(record["path"])
        backup = Path(record["backup"])
        if sha256_file(backup) != record["before_sha256"]:
            raise RuntimeError(f"backup mismatch during undo: {backup}")
        shutil.copy2(backup, path)
        if sha256_file(path) != record["before_sha256"]:
            raise RuntimeError(f"undo verification failed: {path}")
        restored += 1
    print(json.dumps({"mode": "artist-title-undo", "restored": restored}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--backup", type=Path, help="verified pre-apply backup directory")
    parser.add_argument("--backup-relative-to", type=Path, help="root the backup mirrors (paths in plan are relative to this)")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--label", default="artist-title")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--undo", type=Path)
    args = parser.parse_args()

    if args.undo:
        undo(args.undo.resolve())
        return

    if not args.plan:
        raise SystemExit("--plan is required")
    out_dir = args.out_dir or args.plan.resolve().parent

    if args.apply:
        if not args.backup or not args.backup_relative_to:
            raise SystemExit("--backup and --backup-relative-to are required with --apply")
        apply(args.plan, args.backup.resolve(), args.backup_relative_to.resolve(), out_dir, args.label)
    else:
        plan, items = load_and_validate(args.plan, args.backup.resolve()) if args.backup else (
            json.loads(args.plan.read_text(encoding="utf-8")), None
        )
        items = items if items is not None else plan["items"]
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "tracks": len(items),
                    "validation": "passed" if args.backup else "backup not checked (pass --backup to validate fully)",
                    "changes_made": 0,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
