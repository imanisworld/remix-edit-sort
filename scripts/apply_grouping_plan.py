#!/usr/bin/env python3
"""Apply an approved Grouping (TIT1) backfill plan, with verification and undo.

Generalizes the write/verify/rollback logic proven in the historical
apply_events_grouping.py and handoff/pending/apply_nigeria_plan.py: every
write is verified against the audio payload and Serato frames before being
recorded, and rolled back immediately on any mismatch. No file is ever moved
or renamed by this script — it only writes the TIT1 frame.
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

import lock_guard


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
    """Payload-only counterpart to serato_digest, see apply_version_genre_plan.py
    for why: an ID3-version-compliance re-encoding can change a Serato frame's
    encoding byte without touching its actual binary cue/beatgrid/analysis data."""
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


def write_grouping(path: Path, value: str, version: int) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    encoding = 3 if version == 4 else 1
    tags.delall("TIT1")
    tags.add(TIT1(encoding=encoding, text=[value]))
    tags.save(path, v2_version=version)


def load_and_validate(plan_path: Path, backup: Path, root: Path) -> tuple[dict, list[dict]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("mode") != "preview-only" or not plan.get("rules", {}).get("approval_required"):
        raise RuntimeError("unexpected plan mode")
    items = plan["items"]
    if not backup.is_dir():
        raise RuntimeError(f"verified pre-apply backup is missing: {backup}")

    errors = []
    for item in items:
        path = Path(item["path"]).resolve()
        if root not in path.parents and path.parent != root:
            errors.append(f"outside target root: {path}")
            continue
        if not path.is_file():
            errors.append(f"missing file: {path}")
            continue
        current_values, current_version = read_grouping(path)
        if current_values != item["current_grouping"] or current_version != item["id3_version"]:
            errors.append(
                f"Grouping changed since preview: {path}: {current_values!r} != {item['current_grouping']!r}"
            )
    if errors:
        raise RuntimeError("Grouping plan validation failed:\n" + "\n".join(errors))
    return plan, items


def apply(plan_path: Path, backup: Path, root: Path, out_dir: Path, label: str) -> None:
    plan, items = load_and_validate(plan_path, backup, root)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    undo_path = out_dir / f"{label}-grouping-undo-{stamp}.jsonl"
    result_path = out_dir / f"{label}-grouping-result-{stamp}.json"
    completed = []
    skipped_encoding_only = []

    for index, item in enumerate(items, start=1):
        path = Path(item["path"])
        relative = path.relative_to(root) if root in path.parents else path.name
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

        try:
            write_grouping(path, item["combined_grouping"], item["id3_version"])
            after_values, after_version = read_grouping(path)
            if after_values != [item["combined_grouping"]] or after_version != item["id3_version"]:
                raise RuntimeError("Grouping verification failed")
            if sha256_bytes(audio_payload(path)) != before_audio:
                raise RuntimeError("audio payload changed")
            after_tags = ID3(path)
            if serato_digest(after_tags) != before_serato:
                if serato_payload_digest(after_tags) == before_serato_payload:
                    raise SeratoEncodingOnlyChange(
                        "Grouping write forced an ID3-version-compliance re-encoding of a Serato "
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
            "old_grouping": item["current_grouping"],
            "new_grouping": item["combined_grouping"],
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
        "mode": f"{label}-grouping-apply",
        "plan": str(plan_path),
        "verified_backup": str(backup),
        "tracks_in_plan": len(items),
        "grouping_tags_changed": len(completed),
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
    print(json.dumps({"mode": "grouping-undo", "restored": restored}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, help="folder root the plan's paths live under (required unless --undo)")
    parser.add_argument("--plan", type=Path, help="grouping-plan.json from build_grouping_plan.py")
    parser.add_argument("--backup", type=Path, help="verified pre-apply backup directory")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--label", help="prefix for undo/result files")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--undo", type=Path)
    parser.add_argument("--lock-folder", help="Top-level library folder locked via folder_lock.py (required with --apply/--undo)")
    parser.add_argument("--session", help="Session id that holds the lock (required with --apply/--undo)")
    args = parser.parse_args()

    if args.undo:
        if not args.lock_folder or not args.session:
            raise SystemExit("--lock-folder and --session are required with --undo")
        lock_guard.require_lock_or_exit(args.lock_folder, args.session)
        undo(args.undo.resolve())
        return

    if not args.plan or not args.root:
        raise SystemExit("--plan and --root are required")
    label = args.label or args.root.name.casefold().replace(" ", "-")
    out_dir = args.out_dir or args.plan.resolve().parent
    root = args.root.resolve()

    if args.apply:
        if not args.backup:
            raise SystemExit("--backup is required with --apply")
        if not args.lock_folder or not args.session:
            raise SystemExit("--lock-folder and --session are required with --apply")
        lock_guard.require_lock_or_exit(args.lock_folder, args.session)
        apply(args.plan, args.backup.resolve(), root, out_dir, label)
    else:
        plan, items = load_and_validate(args.plan, args.backup.resolve(), root) if args.backup else (
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
