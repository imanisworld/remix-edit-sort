#!/usr/bin/env python3
"""Backfill Clean/Dirty version status into the Grouping (TIT1/(c)grp) tag.

For every audio file sitting under a literal `CLEAN/` or `DIRTY/` folder within
--root, appends "Clean"/"Dirty" to that file's existing Grouping value (never
overwrites it) using the append/dedupe/"; "-join convention shared with
scripts/apply_grouping_plan.py. No file is ever moved or renamed.

Migrated onto scripts/mutation_engine.py (see SAFETY-MODEL.md): every write
goes through MutationEngine.item(), which re-verifies lock ownership, proves
verified backup evidence for the exact file, journals write-ahead intent
before mutating, and rolls back on any verification failure. This script does
NOT create the backup itself -- `--backup`/`--backup-manifest` must already
exist and PASS, exactly like apply_grouping_plan.py, because bulk directory
copying is a mutation-surface primitive (`shutil.copytree`/`copy2`) and doing
it inside this module would either need to live inside a transaction (wrong
shape for a whole-tree backup taken once, up front) or be exempted from the
scanner, neither of which this repo's safety model allows. Produce it with:

    mkdir -p <backup-dir>
    python3 -c "import shutil; shutil.copytree('<root>', '<backup-dir>', dirs_exist_ok=True)"
    python3 scripts/backup_manifest.py verify \\
        --source-root <root> --backup <backup-dir> --out <manifest.json>

--root is one CLEAN or DIRTY leaf directory (not a whole top-level genre
folder) -- backup_manifest requires every file under --root to be present in
the backup, and a CLEAN/DIRTY leaf directory's contents ARE exactly what this
script touches, so that's the natural, non-wasteful unit. A top-level genre
folder (the --lock-folder unit) can contain several such leaf directories;
run this once per leaf directory, reusing the same --lock-folder/--session.

MP3 (ID3 TIT1) and M4A (MP4 (c)grp atom) are supported, reusing the MP4-box
audio_payload/serato_digest approach from apply_version_genre_plan.py's ".m4a
genre write support". FLAC is included for consistency though no FLAC targets
exist under CLEAN/DIRTY today. Any other format is skipped and reported.

ID3v2.2 files are detected before attempting a write and skipped (not
force-upgraded), matching the rest of this repo's tag-writing scripts.
"""

from __future__ import annotations

# Every library mutation in this module goes through scripts/mutation_engine.py.
SAFETY_CLASSIFICATION = "engine-user"

# Read by scripts/mutation_surface.py: the only functions allowed to contain a
# mutation primitive outside a `with engine.item(...)` block, each proven to
# be called only from inside one. Keep this list exactly as small as needed.
ENGINE_MUTATION_HELPERS = ("write_grouping",)

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError, TIT1
from mutagen.mp4 import MP4

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup_manifest  # noqa: E402
import lock_guard  # noqa: E402
import mutation_engine  # noqa: E402
import tag_formats  # noqa: E402

SUPPORTED_EXTS = {".mp3", ".m4a", ".flac"}


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
        if len(data) >= 128 and data[-128:-125] == b"TAG":
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
    suffix = path.suffix.casefold()
    if suffix == ".mp3":
        try:
            return serato_digest_mp3(ID3(path))
        except ID3NoHeaderError:
            return sha256_bytes(b"[]")
    if suffix == ".flac":
        audio = FLAC(path)
        values = {key: list(value) for key, value in audio.tags.items() if "serato" in key.casefold()}
        return sha256_bytes(json.dumps(values, sort_keys=True, ensure_ascii=False).encode())
    if suffix == ".m4a":
        return serato_digest_mp4(MP4(path))
    return sha256_bytes(b"[]")


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


def combined_grouping(existing: list[str], proposed: str) -> str:
    values, seen = [], set()
    for value in [*existing, proposed]:
        normalized = value.strip().casefold()
        if normalized and normalized not in seen:
            values.append(value.strip())
            seen.add(normalized)
    return "; ".join(values)


def is_id3v22(path: Path) -> bool:
    if path.suffix.casefold() != ".mp3":
        return False
    try:
        return ID3(path).version[1] == 2
    except ID3NoHeaderError:
        return False


def write_grouping(path: Path, value: str, version: int) -> None:
    """The one function allowed to contain a Grouping-write primitive outside
    a `with engine.item(...)` block -- see ENGINE_MUTATION_HELPERS above.
    Every call site is inside such a block (apply() and repair())."""
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


def status_for(root: Path) -> str | None:
    """--root is always exactly one CLEAN or DIRTY leaf directory (see the
    module docstring), so the status is a property of the root, not of each
    individual file underneath it."""
    name = root.name.casefold()
    if name == "clean":
        return "Clean"
    if name == "dirty":
        return "Dirty"
    return None


def discover_files(root: Path) -> list[Path]:
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix.lower() in SUPPORTED_EXTS:
                found.append(p)
    return found


def apply_leaf(
    root: Path, manifest: dict, backup: Path, out_dir: Path, label: str,
    heartbeat: lock_guard.LockHeartbeat,
) -> dict:
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    undo_path = out_dir / f"{label}-grouping-undo-{stamp}.jsonl"
    result_path = out_dir / f"{label}-grouping-result-{stamp}.json"
    journal_path = out_dir / f"{label}-recovery-{stamp}.jsonl"
    engine = mutation_engine.MutationEngine(journal_path, heartbeat, manifest, root, label)

    status = status_for(root)
    if status is None:
        raise SystemExit(f"--root must be a CLEAN or DIRTY leaf directory, got: {root}")

    files = discover_files(root)
    engine.start({"root": str(root), "status": status, "files": len(files)})

    written = already_present = skipped_id3v22 = unsupported = failed = 0
    failed_items = []

    for index, path in enumerate(files, start=1):
        if path.suffix.casefold() not in SUPPORTED_EXTS:
            unsupported += 1
            continue

        existing, id3_version = read_grouping(path)
        if any(v.strip().casefold() == status.casefold() for v in existing):
            already_present += 1
            continue
        if is_id3v22(path):
            skipped_id3v22 += 1
            continue

        relative = path.relative_to(root)
        backup_copy = backup / relative
        new_grouping = combined_grouping(existing, status)

        try:
            with engine.item(
                index, kind=mutation_engine.GROUPING, source=path,
                backup_path=backup_copy,
                extra_intent={"current_grouping": existing, "combined_grouping": new_grouping,
                              "id3_version": id3_version, "status_added": status},
            ) as txn:
                before_hash = sha256_file(path)
                txn.on_rollback(
                    lambda p=path, b=backup_copy, h=before_hash:
                    mutation_engine.restore_from_backup(b, p, h)
                )
                before_audio = sha256_bytes(audio_payload(path))
                before_serato = serato_digest(path)

                write_grouping(path, new_grouping, id3_version)
                txn.mark_mutated()

                after_values, after_version = read_grouping(path)
                if after_values != [new_grouping] or (path.suffix.casefold() == ".mp3" and after_version != id3_version):
                    raise RuntimeError("Grouping verification failed")
                if sha256_bytes(audio_payload(path)) != before_audio:
                    raise RuntimeError("audio payload changed")
                if serato_digest(path) != before_serato:
                    raise RuntimeError("Serato frames changed")

                txn.record(
                    old_grouping=existing, new_grouping=new_grouping,
                    before_sha256=before_hash, after_sha256=sha256_file(path),
                    audio_payload_sha256=before_audio, serato_frames_sha256=before_serato,
                )
            written += 1
        except (mutation_engine.MutationRefused, mutation_engine.MutationVerificationError) as exc:
            failed += 1
            failed_items.append({"path": str(path), "error": str(exc)})

    result = {
        "mode": f"{label}-clean-dirty-grouping-apply",
        "root": str(root),
        "verified_backup": str(backup),
        "written": written,
        "already_present": already_present,
        "skipped_id3v22": skipped_id3v22,
        "unsupported_format": unsupported,
        "failed": failed,
        "failed_items": failed_items,
        "undo_log": str(undo_path),
        "journal": str(journal_path),
    }
    engine.finish(result)

    undo_path.parent.mkdir(parents=True, exist_ok=True)
    with undo_path.open("a", encoding="utf-8") as handle:
        for completion in engine.completed:
            if completion.get("mutated"):
                handle.write(json.dumps({
                    "path": completion["source"], "backup_path": completion["backup_path"],
                    **completion,
                }, ensure_ascii=False) + "\n")
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({**result, "result_path": str(result_path)}, indent=2))
    return result


def repair_leaf(
    root: Path, manifest: dict, backup: Path, out_dir: Path, label: str,
    heartbeat: lock_guard.LockHeartbeat,
) -> dict:
    """Collapse a duplicated "; "-joined Grouping value (e.g. "X; Clean; Clean")
    back to its deduped form. Same engine wiring as apply_leaf."""
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    undo_path = out_dir / f"{label}-repair-undo-{stamp}.jsonl"
    result_path = out_dir / f"{label}-repair-result-{stamp}.json"
    journal_path = out_dir / f"{label}-repair-recovery-{stamp}.jsonl"
    engine = mutation_engine.MutationEngine(journal_path, heartbeat, manifest, root, label)

    files = discover_files(root)
    engine.start({"root": str(root), "files": len(files)})

    repaired = no_duplication = failed = 0
    failed_items = []

    for index, path in enumerate(files, start=1):
        raw_values, id3_version = [], 4
        suffix = path.suffix.casefold()
        if suffix == ".mp3":
            try:
                tags = ID3(path)
                id3_version = tags.version[1]
            except ID3NoHeaderError:
                continue
            raw_values = [str(f) for f in tags.getall("TIT1")]
        elif suffix == ".m4a":
            raw_values = [str(v) for v in MP4(path).get("\xa9grp", [])]
        elif suffix == ".flac":
            raw_values = [str(v) for v in FLAC(path).get("grouping", [])]
        else:
            continue

        combined_before = "; ".join(v.strip() for v in raw_values if v.strip())
        parts, seen = [], set()
        for raw in combined_before.split(";"):
            value = raw.strip()
            normalized = value.casefold()
            if value and normalized not in seen:
                parts.append(value)
                seen.add(normalized)
        combined_after = "; ".join(parts)
        if combined_after == combined_before:
            no_duplication += 1
            continue

        relative = path.relative_to(root)
        backup_copy = backup / relative

        try:
            with engine.item(
                index, kind=mutation_engine.GROUPING, source=path,
                backup_path=backup_copy,
                extra_intent={"before_grouping": combined_before, "after_grouping": combined_after},
            ) as txn:
                before_hash = sha256_file(path)
                txn.on_rollback(
                    lambda p=path, b=backup_copy, h=before_hash:
                    mutation_engine.restore_from_backup(b, p, h)
                )
                before_audio = sha256_bytes(audio_payload(path))
                before_serato = serato_digest(path)

                write_grouping(path, combined_after, id3_version)
                txn.mark_mutated()

                after_values, after_version = read_grouping(path)
                if "; ".join(after_values) != combined_after or (suffix == ".mp3" and after_version != id3_version):
                    raise RuntimeError("repair verification failed")
                if sha256_bytes(audio_payload(path)) != before_audio:
                    raise RuntimeError("audio payload changed")
                if serato_digest(path) != before_serato:
                    raise RuntimeError("Serato frames changed")

                txn.record(
                    before_grouping=combined_before, after_grouping=combined_after,
                    before_sha256=before_hash, after_sha256=sha256_file(path),
                )
            repaired += 1
        except (mutation_engine.MutationRefused, mutation_engine.MutationVerificationError) as exc:
            failed += 1
            failed_items.append({"path": str(path), "error": str(exc)})

    result = {
        "mode": f"{label}-clean-dirty-grouping-repair",
        "root": str(root),
        "repaired": repaired,
        "no_duplication": no_duplication,
        "failed": failed,
        "failed_items": failed_items,
        "undo_log": str(undo_path),
    }
    engine.finish(result)
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({**result, "result_path": str(result_path)}, indent=2))
    return result


def undo(undo_path: Path, lock_folder: str, session: str) -> None:
    """Reverse an apply run. An undo mutates the filesystem too, so it gets
    the same lock + journal treatment as apply -- see apply_grouping_plan.py's
    undo() for why a half-finished undo is exactly as damaging as a
    half-finished apply."""
    records = [json.loads(line) for line in undo_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    journal_path = undo_path.with_name(undo_path.stem + "-undo-recovery.jsonl")
    heartbeat = lock_guard.LockHeartbeat(lock_folder, session)
    heartbeat.pin_run_id(lock_guard.require_lock_or_exit(lock_folder, session))
    engine = mutation_engine.MutationEngine(
        journal_path, heartbeat, manifest=None, source_root=None,
        label="clean-dirty-grouping-undo", allow_unbacked=True,
    )
    engine.start({"undo_log": str(undo_path), "records": len(records)})

    restored = 0
    for position, record in enumerate(reversed(records), start=1):
        path = Path(record.get("path") or record.get("source"))
        backup = Path(record["backup_path"])
        with engine.item(
            position, kind=mutation_engine.GROUPING, source=path,
            backup_path=None, require_backup=False,
            extra_intent={"restore_from": str(backup), "restore_to_sha256": record["before_sha256"]},
        ) as txn:
            if sha256_file(backup) != record["before_sha256"]:
                raise RuntimeError(f"backup mismatch during undo: {backup}")
            shutil.copy2(backup, path)
            txn.mark_mutated()
            if sha256_file(path) != record["before_sha256"]:
                raise RuntimeError(f"undo verification failed: {path}")
            txn.record(restored_to=str(path), sha256=record["before_sha256"])
            restored += 1

    result = {"mode": "clean-dirty-grouping-undo", "restored": restored, "recovery_journal": str(journal_path)}
    engine.finish(result)
    print(json.dumps(result, indent=2))


def dry_run(root: Path) -> None:
    status = status_for(root)
    files = discover_files(root) if status else []
    print(json.dumps({
        "mode": "dry-run", "root": str(root), "status": status,
        "files": len(files) if status else 0,
        "error": None if status else "--root must be a CLEAN or DIRTY leaf directory",
    }, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, help="one CLEAN or DIRTY leaf directory (required unless --undo)")
    parser.add_argument("--backup", type=Path, help="pre-verified backup of --root (required with --apply/--repair)")
    parser.add_argument("--backup-manifest", type=Path, help="PASS manifest from backup_manifest.py verify")
    parser.add_argument("--require-serato-backup", action="store_true")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--label")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--repair", action="store_true", help="collapse duplicated Grouping tokens under --root")
    parser.add_argument("--undo", type=Path)
    parser.add_argument("--lock-folder", help="top-level Music folder locked via folder_lock.py")
    parser.add_argument("--session", help="session id that holds the lock")
    args = parser.parse_args()

    if args.undo:
        if not args.lock_folder or not args.session:
            raise SystemExit("--lock-folder and --session are required with --undo")
        undo(args.undo.resolve(), args.lock_folder, args.session)
        return

    if not args.root:
        raise SystemExit("--root is required")
    root = args.root.resolve()
    # e.g. "hip-hop-_needs-check-clean" -- unique per leaf dir so parallel
    # runs across different CLEAN/DIRTY leaves never collide on output names.
    default_label = "-".join(p.casefold().replace(" ", "-") for p in root.parts[-3:])
    label = args.label or default_label
    out_dir = args.out_dir or (Path(__file__).resolve().parent.parent
                                / "handoff/session-2026-08-15/survey/clean-dirty-grouping")

    if args.apply or args.repair:
        if not args.backup or not args.backup_manifest:
            raise SystemExit(
                "--backup and --backup-manifest are required with --apply/--repair.\n"
                "Produce them with:\n"
                "  mkdir -p <backup-dir> && python3 -c \"import shutil; "
                "shutil.copytree('<root>', '<backup-dir>', dirs_exist_ok=True)\"\n"
                "  python3 scripts/backup_manifest.py verify --source-root <root> "
                "--backup <backup-dir> --out <manifest.json>"
            )
        if not args.lock_folder or not args.session:
            raise SystemExit("--lock-folder and --session are required with --apply/--repair")
        try:
            heartbeat, manifest = mutation_engine.run_preflight(
                lock_folder=args.lock_folder, session=args.session,
                manifest_path=args.backup_manifest, source_root=root,
                serato_required=args.require_serato_backup,
            )
        except backup_manifest.BackupGateError as exc:
            raise SystemExit(f"REFUSING TO MUTATE: {exc}") from exc
        if args.repair:
            repair_leaf(root, manifest, args.backup.resolve(), out_dir, label, heartbeat)
        else:
            apply_leaf(root, manifest, args.backup.resolve(), out_dir, label, heartbeat)
    else:
        dry_run(root)


if __name__ == "__main__":
    main()
