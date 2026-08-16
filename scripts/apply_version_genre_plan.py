#!/usr/bin/env python3
"""Apply an approved version-folder + genre-tag plan, with verification and undo.

Generalizes the nine near-identical apply_<folder>_plan.py scripts under
handoff/session-2026-08-15/survey/*/ (dub, rave, metro, wedding, belarusian,
ambient, 90s-hiphop-rnb, rap-rn, events were byte-identical apart from ROOT,
PLAN, BACKUP, and the hardcoded expected-item-count check). All safety
behavior is preserved: per-item hash/audio-payload/Serato-frame verification
before and after every write, atomic rollback on any failure, ID3v2.2 Genre
writes skipped and logged rather than silently upgraded, and an append-only
fsync'd undo log.

.m4a genre writes are supported via MP4's ©gen atom (see _mp4_mdat_payload
and the MP4 branches of audio_payload/serato_digest/read_genre/write_genre).
Verified on real files: mutagen's MP4 save reuses existing "free" box
padding rather than relocating mdat, but this code never assumes that -- it
re-parses the box tree after every write and hashes mdat wherever it landed.
"""

from __future__ import annotations

# Every library mutation in this module goes through scripts/mutation_engine.py.
SAFETY_CLASSIFICATION = "engine-user"

# Read by scripts/mutation_surface.py. The small, explicitly-reviewed set of
# functions allowed to contain a mutation primitive without being lexically
# inside a `with engine.item(...)` block. The scanner additionally proves each
# one IS called from inside such a block. Keep this list tiny: it is exactly
# what a human reviewer has to check by hand.
#
# write_genre() is the only function here that touches a tag outside a
# transaction; apply() and undo() call it from inside `with engine.item(...)`.
ENGINE_MUTATION_HELPERS = ("write_genre",)

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
    """Hash only each Serato GEOB frame's actual binary payload (cues, beatgrid,
    markers, analysis, overview) — not its encoding/desc/owner metadata. Some
    files carry Serato frames with an encoding byte that's technically invalid
    for their ID3 version (e.g. UTF8 inside a v2.3 tag); saving with mutagen
    then silently normalizes that byte for spec compliance, which changes
    serato_digest() even though the real Serato data is untouched. This lets
    that specific, verified-benign case be told apart from an actual change."""
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
    # FLAC and MP4 Serato values are stored as plain tag bytes with no separate
    # encoding metadata the way ID3 GEOB frames have (verified for MP4 by
    # comparing MP4FreeForm.dataformat before/after a genre write on sample
    # files -- identical), so the strict digest is already payload-only.
    return serato_digest(path)


class SeratoEncodingOnlyChange(Exception):
    """Raised when a genre write changed a Serato frame's on-disk encoding byte
    (forced by ID3-version-compliance re-serialization) but its actual binary
    payload was verified byte-identical. Caught and treated as a skip, not a
    failure — never silently swallowed for a real payload difference."""


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
    if suffix == ".m4a":
        audio = MP4(path)
        audio["\xa9gen"] = [value]
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


def load_and_validate(root: Path, plan_path: Path) -> tuple[dict, list[dict]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("mode") != "preview-only" or not plan.get("rules", {}).get("approval_required"):
        raise RuntimeError("unexpected plan mode")
    items = plan["items"]

    errors = []
    destinations: dict[str, str] = {}
    source_paths = {item["source"] for item in items}
    for item in items:
        source = Path(item["source"]).resolve()
        destination = Path(item["destination"]).resolve()
        if root not in source.parents or root not in destination.parents:
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
            if source.suffix.casefold() not in {".mp3", ".flac", ".m4a"}:
                errors.append(f"unsupported tag target: {source}")
            else:
                current = read_genre(source)
                if current != item["current_genre"]:
                    errors.append(f"genre changed since preview: {source}: {current!r} != {item['current_genre']!r}")
    if errors:
        raise RuntimeError("Plan validation failed:\n" + "\n".join(errors))
    return plan, items


def apply(
    root: Path,
    plan_path: Path,
    manifest: dict,
    out_dir: Path,
    label: str,
    heartbeat: lock_guard.LockHeartbeat,
) -> None:
    """Apply the plan through the shared mutation engine.

    Every per-item safety step -- heartbeat, source-hash precondition, backup
    evidence, write-ahead intent record, rollback, completion record -- lives
    in mutation_engine.MutationEngine.item(). What stays here is only what is
    specific to this mutation class: which tag to write, and what "verified"
    means for a Genre write plus an optional relocation.
    """
    plan, items = load_and_validate(root, plan_path)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    tag_backup_root = out_dir / f"{label}-tag-file-backups" / stamp
    undo_path = out_dir / f"{label}-undo-{stamp}.jsonl"
    result_path = out_dir / f"{label}-result-{stamp}.json"
    journal_path = out_dir / f"{label}-recovery-{stamp}.jsonl"

    engine = mutation_engine.MutationEngine(
        journal_path, heartbeat, manifest, root, label,
    )
    engine.start({
        "plan": str(plan_path),
        "items": len(items),
        "moves": sum(bool(item["move"]) for item in items),
        "tag_changes": sum(bool(item["tag_change"]) for item in items),
    })

    completed = []
    for index, item in enumerate(items, start=1):
        source = Path(item["source"])
        destination = Path(item["destination"])
        kind = mutation_engine.RELOCATE if item["move"] else mutation_engine.GENRE

        with engine.item(
            index,
            kind=kind,
            source=source,
            expected_sha256=item["sha256"],
            destination=destination if item["move"] else None,
            extra_intent={
                "current_genre": item["current_genre"],
                "proposed_genre": item["proposed_genre"],
                "tag_change": bool(item["tag_change"]),
            },
        ) as txn:
            before_hash = sha256_file(source)
            before_audio = sha256_bytes(audio_payload(source))
            before_serato = serato_digest(source)
            before_serato_payload = serato_payload_digest(source)
            tag_backup = None
            tag_was_changed = False
            moved = False
            skip_reason = tag_skip_reason(source, item)

            def rollback():
                # Ordered to undo the mutations in reverse: put the file back
                # where it was first, then restore its pre-write bytes.
                if moved and destination.exists():
                    os.rename(destination, source)
                if tag_was_changed and tag_backup is not None:
                    mutation_engine.restore_from_backup(tag_backup, source, before_hash)

            txn.on_rollback(rollback)

            if item["tag_change"] and not skip_reason:
                tag_backup = tag_backup_root / item["source_relative"]
                tag_backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, tag_backup)
                if sha256_file(tag_backup) != before_hash:
                    raise RuntimeError(f"per-item tag backup verification failed: {tag_backup}")
                try:
                    write_genre(source, item["proposed_genre"])
                    tag_was_changed = True
                    txn.mark_mutated()
                    if read_genre(source) != item["proposed_genre"]:
                        raise RuntimeError("Genre tag verification failed")
                    if sha256_bytes(audio_payload(source)) != before_audio:
                        raise RuntimeError("audio payload changed")
                    if serato_digest(source) != before_serato:
                        if serato_payload_digest(source) == before_serato_payload:
                            raise SeratoEncodingOnlyChange(
                                "Genre write forced an ID3-version-compliance re-encoding of a Serato "
                                "frame's encoding byte (its actual binary payload was verified "
                                "byte-identical before and after); write rolled back and skipped out of "
                                "caution rather than trusting the payload check alone"
                            )
                        raise RuntimeError("Serato frames changed")
                except SeratoEncodingOnlyChange as exc:
                    # Verified benign: restore and skip this file's tag write.
                    # Not an engine-level failure, so it is handled here rather
                    # than propagating out of the transaction.
                    mutation_engine.restore_from_backup(tag_backup, source, before_hash)
                    tag_was_changed = False
                    skip_reason = str(exc)

            if item["move"]:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.rename(source, destination)
                moved = True
                txn.mark_mutated()
            else:
                destination = source

            after_hash = sha256_file(destination)
            if sha256_bytes(audio_payload(destination)) != before_audio:
                raise RuntimeError(f"post-move audio verification failed: {destination}")
            if serato_digest(destination) != before_serato:
                raise RuntimeError(f"post-move Serato verification failed: {destination}")
            genre_verifiable = destination.suffix.casefold() in {".mp3", ".flac", ".m4a"}
            if item["proposed_genre"] and not skip_reason and genre_verifiable and read_genre(destination) != item["proposed_genre"]:
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
            txn.record(**{k: v for k, v in record.items() if k not in {"index", "source", "destination"}})
            with undo_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            completed.append(record)

    missing_destinations = [r["destination"] for r in completed if not Path(r["destination"]).is_file()]
    surviving_sources = [r["source"] for r in completed if r["moved"] and Path(r["source"]).exists()]
    result = {
        "mode": f"{label}-apply",
        "plan": str(plan_path),
        "verified_backup_manifest": {
            "backup_id": manifest["backup_id"],
            "status": manifest["status"],
            "file_count": manifest["file_count"],
        },
        "tracks_processed": len(completed),
        "files_moved": sum(r["moved"] for r in completed),
        "genre_tags_changed": sum(r["tag_changed"] for r in completed),
        "genre_tags_skipped": sum(r["tag_skipped"] for r in completed),
        "audio_payloads_changed": 0,
        "serato_frames_changed": 0,
        "missing_destinations": missing_destinations,
        "surviving_moved_sources": surviving_sources,
        "undo_log": str(undo_path),
        "recovery_journal": str(journal_path),
        "tag_backup_root": str(tag_backup_root),
        "genre_counts": plan["counts"]["genres"],
    }
    engine.finish(result)
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({**result, "result_path": str(result_path)}, indent=2))


def undo(undo_path: Path, label: str, heartbeat: lock_guard.LockHeartbeat) -> None:
    """Reverse an apply run.

    An undo is a mutation. It moves files and rewrites tags, so it gets the
    same treatment as an apply: ownership is re-verified before EVERY restore
    (not just once at startup), and each restore is journaled write-ahead so a
    crash mid-undo is recoverable too. A half-finished undo that nobody can
    reconstruct is exactly as damaging as a half-finished apply.
    """
    records = [json.loads(line) for line in undo_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    journal_path = undo_path.with_name(undo_path.stem + "-undo-recovery.jsonl")
    engine = mutation_engine.MutationEngine(
        journal_path, heartbeat, manifest=None, source_root=None,
        label=f"{label}-undo", allow_unbacked=True,
    )
    engine.start({"undo_log": str(undo_path), "records": len(records)})

    restored = 0
    for position, record in enumerate(reversed(records), start=1):
        source = Path(record["source"])
        destination = Path(record["destination"])
        # The file to verify preconditions on is wherever it currently lives.
        current = destination if record["moved"] else source
        with engine.item(
            position,
            kind=mutation_engine.RELOCATE if record["moved"] else mutation_engine.GENRE,
            source=current,
            destination=source,
            require_backup=False,
            extra_intent={
                "undoing_index": record.get("index"),
                "restore_to_sha256": record["before_sha256"],
                "tag_backup": record.get("tag_backup"),
            },
        ) as txn:
            if record["moved"]:
                if source.exists():
                    raise RuntimeError(f"undo source already exists: {source}")
                source.parent.mkdir(parents=True, exist_ok=True)
                os.rename(destination, source)
                txn.mark_mutated()
            if record["tag_changed"]:
                backup = Path(record["tag_backup"])
                if sha256_file(backup) != record["before_sha256"]:
                    raise RuntimeError(f"tag backup mismatch: {backup}")
                shutil.copy2(backup, source)
                txn.mark_mutated()
            if sha256_file(source) != record["before_sha256"]:
                raise RuntimeError(f"undo verification failed: {source}")
            txn.record(restored_to=str(source), sha256=record["before_sha256"])
            restored += 1

    result = {"mode": f"{label}-undo", "restored": restored, "recovery_journal": str(journal_path)}
    engine.finish(result)
    print(json.dumps(result, indent=2))


def _dry_run_backup_gate(manifest_path: Path | None, root: Path) -> str:
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
    parser.add_argument("--root", required=True, type=Path, help="Source folder the plan's paths live under")
    parser.add_argument("--plan", required=True, type=Path, help="plan.json from build_version_genre_plan.py")
    parser.add_argument(
        "--backup-manifest", type=Path,
        help="PASS manifest from backup_manifest.py verify (required with --apply). "
             "A directory existing is not proof of a backup; this is.",
    )
    parser.add_argument(
        "--require-serato-backup", action="store_true",
        help="also require the manifest to carry PASS Serato backup evidence",
    )
    parser.add_argument("--out-dir", type=Path, help="Where undo/result/tag-backup files land (defaults to plan's dir)")
    parser.add_argument("--label", help="Prefix for undo/result files, e.g. 'dub' (defaults to root folder name)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--undo", type=Path, help="Path to an undo .jsonl log to reverse")
    parser.add_argument("--lock-folder", help="Top-level library folder locked via folder_lock.py (required with --apply/--undo)")
    parser.add_argument("--session", help="Session id that holds the lock (required with --apply/--undo)")
    args = parser.parse_args()

    root = args.root.resolve()
    label = args.label or root.name.casefold().replace(" ", "-")
    out_dir = args.out_dir or args.plan.resolve().parent

    if args.undo:
        if not args.lock_folder or not args.session:
            raise SystemExit("--lock-folder and --session are required with --undo")
        lock = lock_guard.require_lock_or_exit(args.lock_folder, args.session)
        heartbeat = lock_guard.LockHeartbeat(args.lock_folder, args.session)
        heartbeat.pin_run_id(lock)
        undo(args.undo.resolve(), label, heartbeat)
    elif args.apply:
        if not args.backup_manifest:
            raise SystemExit(
                "--backup-manifest is required with --apply.\n"
                "Produce one with:\n"
                "  python3 scripts/backup_manifest.py verify --source-root <root> "
                "--backup <backup dir> --out manifest.json"
            )
        if not args.lock_folder or not args.session:
            raise SystemExit("--lock-folder and --session are required with --apply")
        try:
            heartbeat, manifest = mutation_engine.run_preflight(
                lock_folder=args.lock_folder,
                session=args.session,
                manifest_path=args.backup_manifest,
                source_root=root,
                serato_required=args.require_serato_backup,
            )
        except backup_manifest.BackupGateError as exc:
            raise SystemExit(f"REFUSING TO MUTATE: {exc}") from exc
        apply(root, args.plan, manifest, out_dir, label, heartbeat)
    else:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        items = plan["items"]
        skipped = sum(bool(tag_skip_reason(Path(item["source"]), item)) for item in items)
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "tracks": len(items),
                    "moves": sum(item["move"] for item in items),
                    "genre_tag_changes_requested": sum(item["tag_change"] for item in items),
                    "genre_tag_changes_to_apply": sum(item["tag_change"] for item in items) - skipped,
                    "id3v22_tag_writes_skipped": skipped,
                    "unresolved": plan["counts"]["version_categories"].get("_NEEDS CHECK", 0),
                    "backup_gate": _dry_run_backup_gate(args.backup_manifest, root),
                    "changes_made": 0,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
