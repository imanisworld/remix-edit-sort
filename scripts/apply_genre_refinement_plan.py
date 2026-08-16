#!/usr/bin/env python3
"""Apply approved Genre Refinement Pass A/B changes (genre-tag-only, no moves).

Mirrors apply_version_genre_plan.py's verification/rollback/undo pattern
(per-item hash/audio-payload/Serato-frame check before and after every write,
atomic rollback on failure, ID3v2.2 Genre writes skipped and logged, an
append-only fsync'd undo log) but operates on genre-refinement-plan.json's
schema: a single "path" per item (never moved), matched against
provenance.current_sha256 and current_genre, writing proposed_genre.

Only items with combined_status in {A_ONLY, B_ONLY, A_AND_B_SAME} and a
non-blank proposed_genre are eligible. .mp3/.flac/.m4a are supported for
writing; any other extension is reported as unsupported and left untouched.

.m4a support was added and verified separately (see _mp4_mdat_payload and
the MP4 branches of audio_payload/serato_digest/read_genre/write_genre):
mutagen's MP4 tag save on the sampled files reuses the existing top-level
"free" padding box rather than moving mdat, but this code never assumes
that -- it re-parses the box tree after every write and hashes the mdat
payload wherever it actually landed, so a relocation would still verify
correctly (only re-encoded audio bytes would fail it).
"""

from __future__ import annotations

# Every library mutation in this module goes through scripts/mutation_engine.py.
SAFETY_CLASSIFICATION = "engine-user"

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TCON
from mutagen.mp4 import MP4

import backup_manifest
import lock_guard
import mutation_engine

SUPPORTED_EXTS = {".mp3", ".flac", ".m4a"}
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


def serato_digest_mp4(audio: MP4) -> str:
    values = []
    for key, value in audio.items():
        if "serato" not in key.casefold():
            continue
        parts = value if isinstance(value, list) else [value]
        values.append({"key": key, "parts_sha256": [sha256_bytes(bytes(p)) for p in parts]})
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
        return serato_digest_mp4(MP4(path))
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
    # FLAC and MP4 Serato values are stored as plain tag bytes with no
    # separate encoding metadata the way ID3 GEOB frames have (verified for
    # MP4 by comparing MP4FreeForm.dataformat before/after a genre write on
    # sample files -- identical), so the strict digest is already payload-only.
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
    if suffix == ".m4a":
        values = MP4(path).get("\xa9gen", [])
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
    if suffix == ".m4a":
        audio = MP4(path)
        audio["\xa9gen"] = [value]
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


def load_and_validate(plan_path: Path) -> tuple[dict, list[dict], list[dict]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    rules = plan.get("rules", {})
    if plan.get("mode") != "preview-only" or not rules.get("approval_required"):
        raise RuntimeError("unexpected plan mode")
    if not rules.get("genre_metadata_only") or not rules.get("no_move") or not rules.get("no_delete"):
        raise RuntimeError("plan rules do not match genre-metadata-only/no-move/no-delete expectations")

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
        # Check "already at the target value" BEFORE the hash check. A
        # successful genre write on a prior run legitimately changes the
        # whole-file hash -- that's not drift, it's this exact plan already
        # having been applied to this item, and re-running must recognize
        # that and skip it rather than hash-fail on its own prior success.
        current = read_genre(path)
        if current == item["proposed_genre"]:
            already_correct.append(item)
            continue
        actual_hash = sha256_file(path)
        if actual_hash != item["provenance"]["current_sha256"]:
            errors.append(f"hash changed since preview: {path}")
            continue
        if current != item["current_genre"]:
            # File is verified untouched since the plan was built (hash
            # matches) but the plan's recorded current_genre doesn't match
            # what's actually on disk -- a plan-builder data bug (confirmed
            # for every .m4a item: the inventory read never populated
            # current_genre for that format). Trust the verified-unchanged
            # file's real genre only when it matches pre_overwrite_genre --
            # the actual evidence pass_a_alias_key used to compute
            # proposed_genre -- so this isn't guessing, it's re-confirming
            # the same evidence chain the proposal was already built from.
            if current == item.get("pre_overwrite_genre"):
                supported.append(item)
                continue
            errors.append(f"genre changed since preview: {path}: {current!r} != {item['current_genre']!r}")
            continue
        supported.append(item)
    if errors:
        raise RuntimeError("Plan validation failed:\n" + "\n".join(errors))
    return plan, supported, unsupported, already_correct


def _apply_one_genre_write(txn, engine, path, proposed_genre, extra_record,
                           tag_backup_root, index, undo_path, completed):
    """One journaled Genre write: back up, write, verify, record.

    Shared by this script's per-item loop so the write/verify/rollback
    sequence exists once. Rollback restoration and the write-ahead intent and
    completion records are the engine's job (see scripts/mutation_engine.py);
    what lives here is only what "verified" means for a Genre tag write:
    the tag reads back as requested, the audio payload is byte-identical, and
    the Serato frames are byte-identical.
    """
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
        if sha256_file(tag_backup) != before_hash:
            raise RuntimeError(f"per-item tag backup verification failed: {tag_backup}")
        txn.on_rollback(
            lambda: mutation_engine.restore_from_backup(tag_backup, path, before_hash)
        )
        try:
            write_genre(path, proposed_genre)
            tag_was_changed = True
            txn.mark_mutated()
            if read_genre(path) != proposed_genre:
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
        except SeratoEncodingOnlyChange as exc:
            # Verified benign, not a safety failure: restore and skip this file
            # rather than aborting the whole run.
            mutation_engine.restore_from_backup(tag_backup, path, before_hash)
            tag_was_changed = False
            skip_reason = str(exc)

    after_hash = sha256_file(path)
    if sha256_bytes(audio_payload(path)) != before_audio:
        raise RuntimeError(f"post-write audio verification failed: {path}")
    if serato_digest(path) != before_serato:
        raise RuntimeError(f"post-write Serato verification failed: {path}")
    if not skip_reason and read_genre(path) != proposed_genre:
        raise RuntimeError(f"post-write Genre verification failed: {path}")

    record = {
        "index": index,
        "path": str(path),
        "tag_changed": tag_was_changed,
        "tag_skipped": bool(skip_reason),
        "tag_skip_reason": skip_reason,
        "new_genre": proposed_genre,
        "tag_backup": str(tag_backup) if tag_backup else None,
        "before_sha256": before_hash,
        "after_sha256": after_hash,
        "audio_payload_sha256": before_audio,
        "serato_frames_sha256": before_serato,
        **extra_record,
    }
    txn.record(**{k: v for k, v in record.items() if k not in {"index", "path"}})
    with undo_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    completed.append(record)
    return record


def apply(plan_path: Path, manifest: dict, out_dir: Path, label: str, heartbeat: lock_guard.LockHeartbeat) -> None:
    plan, items, unsupported, already_correct = load_and_validate(plan_path)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    tag_backup_root = out_dir / f"{label}-tag-file-backups" / stamp
    undo_path = out_dir / f"{label}-undo-{stamp}.jsonl"
    result_path = out_dir / f"{label}-result-{stamp}.json"
    journal_path = out_dir / f"{label}-recovery-{stamp}.jsonl"
    engine = mutation_engine.MutationEngine(
        journal_path, heartbeat, manifest, LIBRARY_ROOT, label,
    )
    engine.start({"plan": str(plan_path), "items": len(items)})
    completed = []

    for index, item in enumerate(items, start=1):
        path = Path(item["path"])
        with engine.item(
            index,
            kind=mutation_engine.GENRE,
            source=path,
            extra_intent={"proposed_genre": item["proposed_genre"]},
        ) as txn:
            _apply_one_genre_write(
                txn, engine, path, item["proposed_genre"], {"old_genre": item["current_genre"], "combined_status": item["combined_status"]},
                tag_backup_root, index, undo_path, completed,
            )


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


def undo(undo_path: Path, label: str, heartbeat: lock_guard.LockHeartbeat) -> None:
    """An undo is a mutation: ownership is re-verified before every restore,
    and each restore is journaled write-ahead so a crash mid-undo is
    recoverable too."""
    records = [json.loads(line) for line in undo_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    restored = 0
    for record in reversed(records):
        heartbeat.beat()
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


def _dry_run_backup_gate(manifest_path, root) -> str:
    """Report, without mutating, whether the backup gate would let an apply run."""
    if not manifest_path:
        return "not checked -- pass --backup-manifest to validate the backup gate"
    try:
        manifest = backup_manifest.require_pass(manifest_path, root)
    except backup_manifest.BackupGateError as exc:
        return f"WOULD REFUSE: {exc}"
    return f"PASS ({manifest['backup_id']}, {manifest['file_count']} files)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path, help="genre-refinement-plan.json")
    parser.add_argument("--backup-manifest", type=Path,
                        help="PASS manifest from backup_manifest.py verify (required with --apply). "
                             "A directory existing is not proof of a backup; this is.")
    parser.add_argument("--require-serato-backup", action="store_true",
                        help="also require the manifest to carry PASS Serato backup evidence")
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
        lock = lock_guard.require_lock_or_exit(args.lock_folder, args.session)
        heartbeat = lock_guard.LockHeartbeat(args.lock_folder, args.session)
        heartbeat.pin_run_id(lock)
        undo(args.undo.resolve(), args.label, heartbeat)
    elif args.apply:
        if not args.backup_manifest:
            raise SystemExit(
                "--backup-manifest is required with --apply.\n"
                "Produce one with:\n"
                "  python3 scripts/backup_manifest.py verify "
                "--source-root <root> --backup <dir> --out manifest.json"
            )
        if not args.lock_folder or not args.session:
            raise SystemExit("--lock-folder and --session are required with --apply")
        try:
            heartbeat, manifest = mutation_engine.run_preflight(
                lock_folder=args.lock_folder,
                session=args.session,
                manifest_path=args.backup_manifest,
                source_root=LIBRARY_ROOT,
                serato_required=args.require_serato_backup,
            )
        except backup_manifest.BackupGateError as exc:
            raise SystemExit(f"REFUSING TO MUTATE: {exc}") from exc
        apply(args.plan, manifest, out_dir, args.label, heartbeat)
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
                    "backup_gate": _dry_run_backup_gate(args.backup_manifest, LIBRARY_ROOT),
                    "changes_made": 0,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
