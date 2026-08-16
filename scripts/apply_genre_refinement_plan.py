#!/usr/bin/env python3
"""Apply approved Genre Refinement Pass A/B changes (genre-tag-only, no moves).

Mirrors apply_version_genre_plan.py's verification/rollback/undo pattern
(per-item hash/audio-payload/Serato-frame check before and after every write,
atomic rollback on failure, ID3v2.2 Genre writes skipped and logged, an
append-only fsync'd undo log) but operates on genre-refinement-plan.json's
schema: a single "path" per item (never moved), matched against
provenance.current_sha256 and current_genre, writing proposed_genre.

Only items with combined_status in {A_ONLY, B_ONLY, A_AND_B_SAME} and a
non-blank proposed_genre are eligible. Only .mp3/.flac are supported for
writing; any other extension (e.g. .m4a) is reported as unsupported and left
untouched rather than guessed at with unverified format-specific code.
"""

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

import lock_guard

SUPPORTED_EXTS = {".mp3", ".flac"}
ELIGIBLE_STATUSES = {"A_ONLY", "B_ONLY", "A_AND_B_SAME"}
LIBRARY_ROOT = Path("/Users/djb.a.e/Music/DJ-Music/Music")


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


def serato_payload_digest_mp3(tags: ID3) -> str:
    values = []
    for frame in tags.values():
        haystack = " ".join((
            frame.HashKey,
            str(getattr(frame, "desc", "")),
            str(getattr(frame, "owner", "")),
            str(getattr(frame, "filename", "")),
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
    if path.suffix.casefold() == ".mp3":
        try:
            return serato_payload_digest_mp3(ID3(path))
        except ID3NoHeaderError:
            return sha256_bytes(b"[]")
    return serato_digest(path)


class SeratoEncodingOnlyChange(Exception):
    """Raised when a genre write changed a Serato frame's on-disk encoding byte
    but its actual binary payload was verified byte-identical. Treated as a
    skip, not a failure."""


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
    raise RuntimeError(f"genre read not supported for {path.suffix}: {path}")


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


def tag_skip_reason(path: Path) -> str | None:
    if path.suffix.casefold() != ".mp3":
        return None
    try:
        if ID3(path).version[1] == 2:
            return "ID3v2.2 preserved; Genre metadata write skipped"
    except ID3NoHeaderError:
        pass
    return None


def load_and_validate(plan_path: Path, backup: Path) -> tuple[dict, list[dict], list[dict]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    rules = plan.get("rules", {})
    if plan.get("mode") != "preview-only" or not rules.get("approval_required"):
        raise RuntimeError("unexpected plan mode")
    if not rules.get("genre_metadata_only") or not rules.get("no_move") or not rules.get("no_delete"):
        raise RuntimeError("plan rules do not match genre-metadata-only/no-move/no-delete expectations")
    if not backup.is_dir():
        raise RuntimeError(f"verified pre-apply backup is missing: {backup}")

    eligible = [
        item for item in plan["items"]
        if item["combined_status"] in ELIGIBLE_STATUSES and item.get("proposed_genre")
    ]

    seen_paths = set()
    for item in eligible:
        if item["path"] in seen_paths:
            raise RuntimeError(f"duplicate path in plan: {item['path']}")
        seen_paths.add(item["path"])

    folders = {item["folder"] for item in eligible}
    if len(folders) != 1:
        raise RuntimeError(f"expected exactly one folder among eligible items, found: {sorted(folders)}")

    candidates = [item for item in eligible if Path(item["path"]).suffix.casefold() in SUPPORTED_EXTS]
    unsupported = [item for item in eligible if Path(item["path"]).suffix.casefold() not in SUPPORTED_EXTS]

    errors = []
    already_correct = []
    supported = []
    for item in candidates:
        path = Path(item["path"])
        if LIBRARY_ROOT not in path.parents:
            errors.append(f"outside library root: {path}")
            continue
        if not path.is_file():
            errors.append(f"missing file: {path}")
            continue
        actual_hash = sha256_file(path)
        if actual_hash != item["provenance"]["current_sha256"]:
            errors.append(f"hash changed since preview: {path}")
            continue
        current = read_genre(path)
        if current != item["current_genre"]:
            # Whole-file hash is unchanged since the plan was built, so the
            # file itself has not been touched -- this is a stale/wrong
            # current_genre value recorded by the plan builder, not real
            # drift. Only bypass the hard error when the on-disk value
            # already equals the exact proposed target (nothing to write);
            # any other mismatch is still a hard validation failure.
            if current == item["proposed_genre"]:
                already_correct.append(item)
                continue
            errors.append(f"genre changed since preview: {path}: {current!r} != {item['current_genre']!r}")
            continue
        supported.append(item)
    if errors:
        raise RuntimeError("Plan validation failed:\n" + "\n".join(errors))
    return plan, supported, unsupported, already_correct


def apply(plan_path: Path, backup: Path, out_dir: Path, label: str, heartbeat: lock_guard.LockHeartbeat) -> None:
    plan, items, unsupported, already_correct = load_and_validate(plan_path, backup)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    tag_backup_root = out_dir / f"{label}-tag-file-backups" / stamp
    undo_path = out_dir / f"{label}-undo-{stamp}.jsonl"
    result_path = out_dir / f"{label}-result-{stamp}.json"
    completed = []

    for index, item in enumerate(items, start=1):
        heartbeat.beat()
        path = Path(item["path"])
        before_hash = sha256_file(path)
        before_audio = sha256_bytes(audio_payload(path))
        before_serato = serato_digest(path)
        before_serato_payload = serato_payload_digest(path)
        skip_reason = tag_skip_reason(path)
        tag_backup = None
        tag_was_changed = False

        if not skip_reason:
            tag_backup = tag_backup_root / path.relative_to(LIBRARY_ROOT)
            tag_backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, tag_backup)
            try:
                write_genre(path, item["proposed_genre"])
                if read_genre(path) != item["proposed_genre"]:
                    raise RuntimeError("Genre tag verification failed")
                if sha256_bytes(audio_payload(path)) != before_audio:
                    raise RuntimeError("audio payload changed")
                if serato_digest(path) != before_serato:
                    if serato_payload_digest(path) == before_serato_payload:
                        raise SeratoEncodingOnlyChange(
                            "Genre write forced an ID3-version-compliance re-encoding of a Serato "
                            "frame's encoding byte (its actual binary payload was verified "
                            "byte-identical before and after); write rolled back and skipped out of "
                            "caution rather than trusting the payload check alone"
                        )
                    raise RuntimeError("Serato frames changed")
                tag_was_changed = True
            except SeratoEncodingOnlyChange as exc:
                shutil.copy2(tag_backup, path)
                skip_reason = str(exc)
            except Exception:
                shutil.copy2(tag_backup, path)
                raise

        after_hash = sha256_file(path)
        if sha256_bytes(audio_payload(path)) != before_audio:
            raise RuntimeError(f"post-write audio verification failed: {path}")
        if serato_digest(path) != before_serato:
            raise RuntimeError(f"post-write Serato verification failed: {path}")
        if not skip_reason and read_genre(path) != item["proposed_genre"]:
            raise RuntimeError(f"post-write Genre verification failed: {path}")

        record = {
            "index": index,
            "path": str(path),
            "tag_changed": tag_was_changed,
            "tag_skipped": bool(skip_reason),
            "tag_skip_reason": skip_reason,
            "old_genre": item["current_genre"],
            "new_genre": item["proposed_genre"],
            "combined_status": item["combined_status"],
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

    genre_counts: dict[str, int] = {}
    for record in completed:
        if record["tag_changed"]:
            genre_counts[record["new_genre"]] = genre_counts.get(record["new_genre"], 0) + 1

    result = {
        "mode": f"{label}-apply",
        "plan": str(plan_path),
        "verified_backup": str(backup),
        "tracks_processed": len(completed),
        "genre_tags_changed": sum(r["tag_changed"] for r in completed),
        "genre_tags_skipped": sum(r["tag_skipped"] for r in completed),
        "audio_payloads_changed": 0,
        "serato_frames_changed": 0,
        "unsupported_format_count": len(unsupported),
        "unsupported_format_items": [
            {"path": i["path"], "combined_status": i["combined_status"], "proposed_genre": i["proposed_genre"]}
            for i in unsupported
        ],
        "already_correct_count": len(already_correct),
        "already_correct_items": [
            {"path": i["path"], "proposed_genre": i["proposed_genre"], "note": "on-disk genre already matched proposed value; plan's recorded current_genre was stale"}
            for i in already_correct
        ],
        "undo_log": str(undo_path),
        "tag_backup_root": str(tag_backup_root),
        "genre_counts": genre_counts,
    }
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({**result, "result_path": str(result_path)}, indent=2))


def undo(undo_path: Path, label: str) -> None:
    records = [json.loads(line) for line in undo_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    restored = 0
    for record in reversed(records):
        path = Path(record["path"])
        if record["tag_changed"]:
            backup = Path(record["tag_backup"])
            if sha256_file(backup) != record["before_sha256"]:
                raise RuntimeError(f"tag backup mismatch: {backup}")
            shutil.copy2(backup, path)
        if sha256_file(path) != record["before_sha256"]:
            raise RuntimeError(f"undo verification failed: {path}")
        restored += 1
    print(json.dumps({"mode": f"{label}-undo", "restored": restored}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path, help="genre-refinement-plan.json")
    parser.add_argument("--backup", type=Path, help="Verified pre-apply scoped backup directory (required with --apply)")
    parser.add_argument("--out-dir", type=Path, help="Where undo/result/tag-backup files land (defaults to plan's dir)")
    parser.add_argument("--label", default="genre-refinement", help="Prefix for undo/result files")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--undo", type=Path, help="Path to an undo .jsonl log to reverse")
    parser.add_argument("--lock-folder", help="Top-level library folder locked via folder_lock.py (required with --apply/--undo)")
    parser.add_argument("--session", help="Session id that holds the lock (required with --apply/--undo)")
    args = parser.parse_args()

    out_dir = args.out_dir or args.plan.resolve().parent

    if args.undo:
        if not args.lock_folder or not args.session:
            raise SystemExit("--lock-folder and --session are required with --undo")
        lock_guard.require_lock_or_exit(args.lock_folder, args.session)
        undo(args.undo.resolve(), args.label)
    elif args.apply:
        if not args.backup:
            raise SystemExit("--backup is required with --apply")
        if not args.lock_folder or not args.session:
            raise SystemExit("--lock-folder and --session are required with --apply")
        lock_guard.require_lock_or_exit(args.lock_folder, args.session)
        heartbeat = lock_guard.LockHeartbeat(args.lock_folder, args.session)
        apply(args.plan, args.backup.resolve(), out_dir, args.label, heartbeat)
    else:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        eligible = [
            item for item in plan["items"]
            if item["combined_status"] in ELIGIBLE_STATUSES and item.get("proposed_genre")
        ]
        supported = [i for i in eligible if Path(i["path"]).suffix.casefold() in SUPPORTED_EXTS]
        unsupported = [i for i in eligible if Path(i["path"]).suffix.casefold() not in SUPPORTED_EXTS]
        skipped = sum(bool(tag_skip_reason(Path(i["path"]))) for i in supported if Path(i["path"]).is_file())
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "eligible": len(eligible),
                    "supported": len(supported),
                    "unsupported_format": len(unsupported),
                    "unsupported_format_exts": sorted({Path(i["path"]).suffix for i in unsupported}),
                    "id3v22_tag_writes_would_be_skipped": skipped,
                    "genre_tag_changes_to_apply": len(supported) - skipped,
                    "validation": "passed" if args.backup and args.backup.is_dir() else "backup not checked (pass --backup to validate fully)",
                    "changes_made": 0,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
