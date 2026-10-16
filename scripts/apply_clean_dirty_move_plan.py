#!/usr/bin/env python3
"""Apply an approved build_clean_dirty_move_plan.py plan: move each track from
_NEEDS CHECK into its decided CLEAN or DIRTY sibling folder, appending the
Clean/Dirty status to its Grouping tag (never overwriting existing Grouping
content -- reuses apply_clean_dirty_grouping.py's append/dedupe convention).

Structurally identical to apply_version_genre_plan.py (move + tag write, both
inside the shared mutation engine, single-folder-scoped backup manifest) --
the only difference is which tag gets written and how (Grouping is additive,
Genre is an overwrite).
"""

from __future__ import annotations

# Every library mutation in this module goes through scripts/mutation_engine.py.
SAFETY_CLASSIFICATION = "engine-user"

# Read by scripts/mutation_surface.py. write_grouping() is the only function
# here that touches a tag outside a transaction; apply()/undo() call it from
# inside `with engine.item(...)`.
ENGINE_MUTATION_HELPERS = ("write_grouping",)

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TIT1
from mutagen.mp4 import MP4

import backup_manifest
import lock_guard
import mutation_engine
import tag_formats

SUPPORTED_EXTS = {".mp3", ".flac", ".m4a"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _mp4_top_level_boxes(data: bytes):
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
    return serato_digest(path)


class SeratoEncodingOnlyChange(Exception):
    pass


def _split_grouping_parts(raw_values: list[str]) -> list[str]:
    parts = []
    for raw in raw_values:
        parts.extend(p.strip() for p in raw.split(";") if p.strip())
    return parts


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
            raw.extend(str(value).strip() for value in text if str(value).strip())
        return _split_grouping_parts(raw), tags.version[1]
    if suffix == ".m4a":
        raw = [str(v).strip() for v in MP4(path).get("\xa9grp", []) if str(v).strip()]
        return _split_grouping_parts(raw), 4
    if suffix == ".flac":
        raw = [str(v).strip() for v in FLAC(path).get("grouping", []) if str(v).strip()]
        return _split_grouping_parts(raw), 4
    raise RuntimeError(f"grouping read not supported for {path.suffix}: {path}")


def write_grouping(path: Path, value: str, version: int) -> None:
    suffix = path.suffix.casefold()
    if suffix == ".mp3":
        try:
            tags = tag_formats.load_id3_for_save(path, version)
        except ID3NoHeaderError:
            tags = ID3()
        encoding = 3 if version == 4 else 1
        tags.delall("TIT1")
        tags.add(TIT1(encoding=encoding, text=[value]))
        tags.save(path, v2_version=version)
        return
    if suffix == ".m4a":
        audio = MP4(path)
        audio["\xa9grp"] = [value]
        audio.save()
        return
    if suffix == ".flac":
        audio = FLAC(path)
        audio["grouping"] = [value]
        audio.save()
        return
    raise RuntimeError(f"grouping write not supported for {path.suffix}: {path}")


def is_id3v22(path: Path) -> bool:
    if path.suffix.casefold() != ".mp3":
        return False
    try:
        return ID3(path).version[1] == 2
    except ID3NoHeaderError:
        return False


def load_and_validate(root: Path, plan_path: Path) -> tuple[dict, list[dict]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("mode") != "preview-only" or not plan.get("rules", {}).get("approval_required"):
        raise RuntimeError("unexpected plan mode")
    items = plan["items"]

    errors = []
    destinations: dict[str, str] = {}
    root_resolved = root.resolve()
    for item in items:
        missing = [key for key in (
            "source", "destination", "sha256", "decision", "existing_grouping",
            "proposed_grouping", "tag_change", "move",
        ) if key not in item]
        if missing:
            errors.append(f"plan item missing required field(s): {', '.join(missing)}")
            continue
        source = Path(item["source"]).absolute()
        destination = Path(item["destination"]).absolute()
        try:
            source.resolve().relative_to(root_resolved)
            destination.parent.resolve().relative_to(root_resolved)
        except ValueError:
            errors.append(f"outside target root: {source} -> {destination}")
            continue
        if item["decision"] not in {"CLEAN", "DIRTY"}:
            errors.append(f"invalid clean/dirty decision for {source}: {item['decision']!r}")
            continue
        if not item["move"] or source == destination:
            errors.append(f"plan item is not a real move: {source} -> {destination}")
            continue
        if not source.is_file():
            errors.append(f"missing source: {source}")
            continue
        actual_hash = sha256_file(source)
        if actual_hash != item["sha256"]:
            errors.append(f"source hash changed since plan built: {source}")
            continue
        destination_key = mutation_engine.path_collision_key(destination)
        if destination_key in destinations:
            errors.append(f"destination collision: {destination}")
        destinations[destination_key] = str(source)
        if mutation_engine.destination_conflicts(source, destination):
            errors.append(f"destination already exists: {destination}")
            continue
        current_grouping, _ = read_grouping(source)
        if current_grouping != item["existing_grouping"]:
            errors.append(
                f"grouping changed since plan built: {source}: {current_grouping!r} != {item['existing_grouping']!r}"
            )
    if errors:
        raise RuntimeError("Plan validation failed:\n" + "\n".join(errors))
    return plan, items


def apply(root: Path, plan_path: Path, manifest: dict, out_dir: Path, label: str, heartbeat) -> None:
    plan, items = load_and_validate(root, plan_path)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    tag_backup_root = out_dir / f"{label}-tag-file-backups" / stamp
    undo_path = out_dir / f"{label}-undo-{stamp}.jsonl"
    result_path = out_dir / f"{label}-result-{stamp}.json"
    journal_path = out_dir / f"{label}-recovery-{stamp}.jsonl"

    engine = mutation_engine.MutationEngine(journal_path, heartbeat, manifest, root, label)
    engine.start({
        "plan": str(plan_path), "items": len(items),
        "tag_changes": sum(bool(item["tag_change"]) for item in items),
    })

    completed = []
    for index, item in enumerate(items, start=1):
        source = Path(item["source"])
        destination = Path(item["destination"])

        with engine.item(
            index,
            kind=mutation_engine.RELOCATE,
            source=source,
            expected_sha256=item["sha256"],
            destination=destination,
            extra_intent={"decision": item["decision"], "tag_change": bool(item["tag_change"])},
        ) as txn:
            before_hash = sha256_file(source)
            before_audio = sha256_bytes(audio_payload(source))
            before_serato = serato_digest(source)
            before_serato_payload = serato_payload_digest(source)
            tag_backup = None
            tag_was_changed = False
            moved = False
            skip_reason = "ID3v2.2 preserved; Grouping metadata write skipped" if item["tag_change"] and is_id3v22(source) else None

            def rollback():
                if moved and mutation_engine.literal_path_is_file(destination):
                    txn.rename_no_overwrite(destination, source, purpose="apply-rollback")
                if tag_was_changed and tag_backup is not None:
                    mutation_engine.restore_from_backup(tag_backup, source, before_hash)

            txn.on_rollback(rollback)

            if item["tag_change"] and not skip_reason:
                tag_backup = (
                    tag_backup_root
                    / Path(item["source"]).resolve().relative_to(root.resolve())
                )
                tag_backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, tag_backup)
                if sha256_file(tag_backup) != before_hash:
                    raise RuntimeError(f"per-item tag backup verification failed: {tag_backup}")
                _, id3_version = read_grouping(source)
                try:
                    write_grouping(source, item["proposed_grouping"], id3_version)
                    tag_was_changed = True
                    txn.mark_mutated()
                    new_grouping, _ = read_grouping(source)
                    if new_grouping != _split_grouping_parts([item["proposed_grouping"]]):
                        raise RuntimeError("Grouping tag verification failed")
                    if sha256_bytes(audio_payload(source)) != before_audio:
                        raise RuntimeError("audio payload changed")
                    if serato_digest(source) != before_serato:
                        if serato_payload_digest(source) == before_serato_payload:
                            raise SeratoEncodingOnlyChange(
                                "Grouping write forced an ID3-version-compliance re-encoding of a Serato "
                                "frame's encoding byte (payload verified byte-identical); write rolled back"
                            )
                        raise RuntimeError("Serato frames changed")
                except SeratoEncodingOnlyChange as exc:
                    mutation_engine.restore_from_backup(tag_backup, source, before_hash)
                    tag_was_changed = False
                    skip_reason = str(exc)

            destination.parent.mkdir(parents=True, exist_ok=True)
            # The shared rename helper journals and heartbeats every
            # physical rename, including both case-only rename steps.
            txn.rename_no_overwrite(source, destination)
            moved = True

            after_hash = sha256_file(destination)
            if sha256_bytes(audio_payload(destination)) != before_audio:
                raise RuntimeError(f"post-move audio verification failed: {destination}")
            if serato_digest(destination) != before_serato:
                raise RuntimeError(f"post-move Serato verification failed: {destination}")
            if item["tag_change"] and not skip_reason:
                final_grouping, _ = read_grouping(destination)
                if final_grouping != _split_grouping_parts([item["proposed_grouping"]]):
                    raise RuntimeError(f"post-move Grouping verification failed: {destination}")

            record = {
                "index": index,
                "source": str(source),
                "destination": str(destination),
                "decision": item["decision"],
                "moved": True,
                "tag_changed": tag_was_changed,
                "tag_skipped": bool(skip_reason),
                "tag_skip_reason": skip_reason,
                "existing_grouping": item["existing_grouping"],
                "proposed_grouping": item["proposed_grouping"],
                "tag_backup": str(tag_backup) if tag_backup else None,
                "before_sha256": before_hash,
                "after_sha256": after_hash,
                "audio_payload_sha256": before_audio,
                "serato_frames_sha256": before_serato,
            }
            txn.record(**{k: v for k, v in record.items() if k not in {"index", "source", "destination"}})
            with undo_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            completed.append(record)

    result = {
        "mode": f"{label}-apply",
        "plan": str(plan_path),
        "root": str(root),
        "verified_backup": manifest.get("backup_id"),
        "tracks_processed": len(completed),
        "files_moved": sum(r["moved"] for r in completed),
        "clean_count": sum(1 for r in completed if r["decision"] == "CLEAN"),
        "dirty_count": sum(1 for r in completed if r["decision"] == "DIRTY"),
        "tags_changed": sum(r["tag_changed"] for r in completed),
        "tags_skipped": sum(r["tag_skipped"] for r in completed),
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
        journal_path, heartbeat, manifest=None, source_root=None, label=f"{label}-undo", allow_unbacked=True,
    )
    engine.start({"undo_log": str(undo_path), "records": len(records)})

    restored = 0
    for position, record in enumerate(reversed(records), start=1):
        source = Path(record["source"])
        destination = Path(record["destination"])
        with engine.item(
            position,
            kind=mutation_engine.RELOCATE,
            source=destination,
            destination=source,
            require_backup=False,
            extra_intent={"undoing_index": record.get("index"), "restore_to_sha256": record["before_sha256"]},
        ) as txn:
            if mutation_engine.destination_conflicts(destination, source):
                raise RuntimeError(f"undo source already exists: {source}")
            source.parent.mkdir(parents=True, exist_ok=True)
            undo_moved = False

            def rollback_undo_move():
                if undo_moved and mutation_engine.literal_path_is_file(source):
                    txn.rename_no_overwrite(source, destination, purpose="undo-rollback")

            txn.on_rollback(rollback_undo_move)
            txn.rename_no_overwrite(destination, source, purpose="undo")
            undo_moved = True
            if record["tag_changed"]:
                backup = Path(record["tag_backup"])
                if sha256_file(backup) != record["before_sha256"]:
                    raise RuntimeError(f"tag backup mismatch: {backup}")
                heartbeat.beat()
                shutil.copy2(backup, source)
            if sha256_file(source) != record["before_sha256"]:
                raise RuntimeError(f"undo verification failed: {source}")
            txn.record(restored_to=str(source), sha256=record["before_sha256"])
            restored += 1

    result = {"mode": f"{label}-undo", "restored": restored, "recovery_journal": str(journal_path)}
    engine.finish(result)
    print(json.dumps(result, indent=2))


def _dry_run_backup_gate(manifest_path, root) -> str:
    if not manifest_path:
        return "not checked -- pass --backup-manifest to validate the backup gate"
    try:
        manifest = backup_manifest.require_pass(manifest_path, root)
    except backup_manifest.BackupGateError as exc:
        return f"WOULD REFUSE: {exc}"
    return f"PASS ({manifest['backup_id']}, {manifest['file_count']} files)"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", required=True, type=Path, help="One top-level library folder (e.g. 'Juke')")
    parser.add_argument("--plan", type=Path, help="plan.json from build_clean_dirty_move_plan.py (required unless --undo)")
    parser.add_argument("--backup-manifest", type=Path,
                         help="PASS manifest from backup_manifest.py verify (required with --apply)")
    parser.add_argument("--require-serato-backup", action="store_true")
    parser.add_argument("--out-dir", type=Path, help="Where undo/result/tag-backup files land (defaults to plan's dir)")
    parser.add_argument("--label", default="clean-dirty-move", help="Prefix for undo/result files")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--undo", type=Path, help="Path to an undo .jsonl log to reverse")
    parser.add_argument("--lock-folder", help="Top-level library folder locked via folder_lock.py (required with --apply/--undo)")
    parser.add_argument("--session", help="Session id that holds the lock (required with --apply/--undo)")
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
                "Produce one with:\n"
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
        apply(root, args.plan, manifest, out_dir, args.label, heartbeat)
    else:
        if not args.plan:
            raise SystemExit("--plan is required for a dry run")
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        items = plan["items"]
        skipped = sum(1 for i in items if is_id3v22(Path(i["source"])) and i["tag_change"])
        print(json.dumps({
            "mode": "dry-run",
            "root": str(root),
            "tracks": len(items),
            "clean": sum(1 for i in items if i["decision"] == "CLEAN"),
            "dirty": sum(1 for i in items if i["decision"] == "DIRTY"),
            "tag_changes_requested": sum(1 for i in items if i["tag_change"]),
            "id3v22_tag_writes_would_be_skipped": skipped,
            "backup_gate": _dry_run_backup_gate(args.backup_manifest, root),
            "changes_made": 0,
        }, indent=2))


if __name__ == "__main__":
    main()
