#!/usr/bin/env python3
"""Apply the auto-fixable items from survey_new_music_intake.py's plan:
Genre (only where the survey found unanimous sibling consensus) and/or
Grouping (remix/edit type + "Edit" umbrella, and/or an explicit Clean/Dirty
filename label). Duplicate flags in the plan are report-only and are never
acted on here -- deleting anything stays a manual, per-file decision.

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
import hashlib
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TCON, TIT1
from mutagen.mp4 import MP4

import backup_manifest
import lock_guard
import mutation_engine

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


def write_metadata(
    path: Path,
    *,
    genre_value: str | None,
    grouping_value: str | None,
    version: int,
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
        except ID3NoHeaderError:
            tags = ID3()
        encoding = 3 if version == 4 else 1
        if genre_value is not None:
            tags.delall("TCON")
            tags.add(TCON(encoding=encoding, text=[genre_value]))
        if grouping_value is not None:
            tags.delall("TIT1")
            tags.add(TIT1(encoding=encoding, text=[grouping_value]))
        tags.save(path, v2_version=version)
        return
    if suffix == ".flac":
        audio = FLAC(path)
        if genre_value is not None:
            audio["genre"] = [genre_value]
        if grouping_value is not None:
            audio["grouping"] = [grouping_value]
        audio.save()
        return
    if suffix == ".m4a":
        audio = MP4(path)
        if genre_value is not None:
            audio["\xa9gen"] = [genre_value]
        if grouping_value is not None:
            audio["\xa9grp"] = [grouping_value]
        audio.save()
        return
    raise RuntimeError(f"metadata write not supported for {path.suffix}: {path}")


def tag_skip_reason(path: Path) -> str | None:
    if path.suffix.casefold() != ".mp3":
        return None
    try:
        if ID3(path).version[1] == 2:
            return "ID3v2.2 preserved; tag write skipped"
    except ID3NoHeaderError:
        pass
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
            if "genre_fix" in row or "grouping_fix" in row:
                errors.append(f"unsupported metadata format in actionable item: {path}")
            continue
        actual_hash = sha256_file(path)
        if actual_hash != row["sha256"]:
            errors.append(f"file hash changed since survey: {path}")
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
    original_genre,
    original_grouping,
    version,
    tag_backup_root,
    index,
    undo_path,
):
    before_hash = sha256_file(path)
    before_audio = sha256_bytes(audio_payload(path))
    before_serato = serato_digest(path)
    before_serato_payload = serato_payload_digest(path)
    skip_reason = tag_skip_reason(path)
    tag_backup = None
    tag_was_changed = False

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
                version=version,
            )
            tag_was_changed = True
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
            if sha256_bytes(audio_payload(path)) != before_audio:
                raise RuntimeError("audio payload changed")
            if serato_digest(path) != before_serato:
                if serato_payload_digest(path) == before_serato_payload:
                    raise SeratoEncodingOnlyChange("encoding-byte-only Serato change; rolled back")
                raise RuntimeError("Serato frames changed")
        except SeratoEncodingOnlyChange:
            mutation_engine.restore_from_backup(tag_backup, path, before_hash)
            tag_was_changed = False
            skip_reason = "Serato encoding-only change on write; skipped out of caution"

    after_hash = sha256_file(path)
    if sha256_bytes(audio_payload(path)) != before_audio:
        raise RuntimeError(f"post-write audio verification failed: {path}")
    if serato_digest(path) != before_serato:
        raise RuntimeError(f"post-write Serato verification failed: {path}")
    if not skip_reason and proposed_genre is not None and read_genre(path) != proposed_genre:
        raise RuntimeError(f"post-write Genre verification failed: {path}")
    if not skip_reason and proposed_grouping is not None:
        actual_grouping, _ = read_grouping(path)
        expected_grouping = [part.strip() for part in proposed_grouping.split(";") if part.strip()]
        if actual_grouping != expected_grouping:
            raise RuntimeError(f"post-write Grouping verification failed: {path}")
    record = {
        "index": index, "kind": "metadata", "path": str(path),
        "tag_changed": tag_was_changed, "tag_skipped": bool(skip_reason),
        "tag_skip_reason": skip_reason,
        "genre_requested": proposed_genre is not None,
        "grouping_requested": proposed_grouping is not None,
        "old_genre": original_genre,
        "new_genre": proposed_genre,
        "old_grouping": original_grouping,
        "new_grouping": proposed_grouping,
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
    actionable = [row for row in items if "genre_fix" in row or "grouping_fix" in row]
    for index, row in enumerate(actionable, start=1):
        path = Path(row["path"])
        existing_grouping, version = read_grouping(path)
        original_genre = read_genre(path)
        proposed_genre = row.get("genre_fix", {}).get("to")
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
            },
        ) as txn:
            completed.append(_apply_one_metadata_write(
                txn, path,
                proposed_genre=proposed_genre,
                proposed_grouping=proposed_grouping,
                original_genre=original_genre,
                original_grouping=existing_grouping,
                version=version,
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
        "skipped_id3v22": sum(
            1 for r in completed if (r["tag_skip_reason"] or "").startswith("ID3v2.2")
        ),
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
