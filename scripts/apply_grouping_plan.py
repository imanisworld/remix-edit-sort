#!/usr/bin/env python3
"""Apply an approved Grouping (TIT1) backfill plan, with verification and undo.

Generalizes the write/verify/rollback logic proven in the historical
apply_events_grouping.py and handoff/pending/apply_nigeria_plan.py: every
write is verified against the audio payload and Serato frames before being
recorded, and rolled back immediately on any mismatch. No file is ever moved
or renamed by this script — it only writes the Grouping field.

Formats: MP3 (ID3 TIT1, existing behaviour), FLAC (``GROUPING`` Vorbis
comment) and M4A (``\xa9grp`` atom). For FLAC/M4A the plan item's
``id3_version`` must be null; Serato's own data (``SERATO_*`` Vorbis comments
and APPLICATION blocks in FLAC, ``----:com.serato.dj:*`` atoms in M4A) is
hashed before and after every write and must be byte-identical.
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
# write_grouping() is called only from inside `with engine.item(...)`.
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

import tag_formats

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


def audio_payload(path: Path) -> bytes:
    if tag_formats.kind(path) in {"flac", "m4a"}:
        return tag_formats.audio_payload(path)
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


def read_grouping(path: Path) -> tuple[list[str], int | None]:
    if tag_formats.kind(path) in {"flac", "m4a"}:
        return tag_formats.read_grouping(path), None
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return [], 4
    values = []
    for frame in tags.getall("TIT1"):
        text = getattr(frame, "text", [])
        values.extend(str(value).strip() for value in text if str(value).strip())
    return values, tags.version[1]


def write_grouping(path: Path, value: str, version: int | None) -> None:
    """Write Grouping as one value. An empty value (only reachable from an
    owner-approved removal plan) deletes the field instead of writing ""."""
    file_kind = tag_formats.kind(path)
    if file_kind == "flac":
        audio = FLAC(path)
        if audio.tags is None:
            audio.add_tags()
        if value:
            audio.tags["GROUPING"] = [value]
        elif "GROUPING" in audio.tags:
            del audio.tags["GROUPING"]
        audio.save()
        return
    if file_kind == "m4a":
        audio = MP4(path)
        if audio.tags is None:
            audio.add_tags()
        if value:
            audio.tags["\xa9grp"] = [value]
        elif "\xa9grp" in audio.tags:
            del audio.tags["\xa9grp"]
        audio.save()
        return
    if version is None:
        raise RuntimeError(f"ID3 write needs an id3_version: {path}")
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    encoding = 3 if version == 4 else 1
    tags.delall("TIT1")
    if value:
        tags.add(TIT1(encoding=encoding, text=[value]))
    tags.save(path, v2_version=version)


def removal_errors(plan: dict, item: dict) -> list[str]:
    """Grouping is append-only except through an owner-approved removal list
    (build_grouping_removal_plan.py). An item may drop only listed values,
    and must keep every other value in order."""
    if "removed_values" not in item and item["combined_grouping"]:
        return []
    approved = {v.casefold() for v in plan.get("rules", {}).get("grouping_removal", {}).get("approved_values", [])}
    if not approved:
        return [f"removes Grouping without an approved removal list: {item['path']}"]
    removed = item.get("removed_values", [])
    parts = [p.strip() for raw in item["current_grouping"] for p in raw.split(";") if p.strip()]
    kept = [p for p in parts if p.casefold() not in approved]
    errors = [f"value not on the approved removal list: {item['path']}: {v!r}"
              for v in removed if v.casefold() not in approved]
    if "; ".join(kept) != item["combined_grouping"] or not removed:
        errors.append(f"removal item does not match its approved list: {item['path']}")
    return errors


def load_and_validate(plan_path: Path, root: Path) -> tuple[dict, list[dict]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("mode") != "preview-only" or not plan.get("rules", {}).get("approval_required"):
        raise RuntimeError("unexpected plan mode")
    items = plan["items"]

    errors = []
    for item in items:
        errors.extend(removal_errors(plan, item))
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


def apply(plan_path: Path, manifest: dict, backup: Path, root: Path, out_dir: Path, label: str, heartbeat: lock_guard.LockHeartbeat) -> None:
    plan, items = load_and_validate(plan_path, root)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    undo_path = out_dir / f"{label}-grouping-undo-{stamp}.jsonl"
    result_path = out_dir / f"{label}-grouping-result-{stamp}.json"
    journal_path = out_dir / f"{label}-recovery-{stamp}.jsonl"
    engine = mutation_engine.MutationEngine(
        journal_path, heartbeat, manifest, root, label,
    )
    engine.start({"plan": str(plan_path), "items": len(items)})
    completed = []
    skipped_encoding_only = []

    for index, item in enumerate(items, start=1):
        path = Path(item["path"])
        relative = path.relative_to(root) if root in path.parents else path.name
        backup_copy = backup / relative

        # The engine performs the heartbeat, the source-exists and
        # backup-evidence checks (both the manifest entry for this exact file
        # and this per-item backup copy's hash), the write-ahead intent
        # record, the rollback on failure, and the completion record.
        with engine.item(
            index,
            kind=mutation_engine.GROUPING,
            source=path,
            backup_path=backup_copy,
            extra_intent={
                "current_grouping": item["current_grouping"],
                "combined_grouping": item["combined_grouping"],
                "id3_version": item["id3_version"],
            },
        ) as txn:
            before_hash = sha256_file(path)
            txn.on_rollback(
                lambda p=path, b=backup_copy, h=before_hash:
                mutation_engine.restore_from_backup(b, p, h)
            )
            before_audio = sha256_bytes(audio_payload(path))
            non_id3 = tag_formats.kind(path) in {"flac", "m4a"}
            if non_id3:
                before_serato = tag_formats.serato_digest(path)
                before_serato_payload = before_serato
            else:
                before_tags = ID3(path)
                before_serato = serato_digest(before_tags)
                before_serato_payload = serato_payload_digest(before_tags)

            try:
                write_grouping(path, item["combined_grouping"], item["id3_version"])
                txn.mark_mutated()
                after_values, after_version = read_grouping(path)
                expected = [item["combined_grouping"]] if item["combined_grouping"] else []
                if after_values != expected or after_version != item["id3_version"]:
                    raise RuntimeError("Grouping verification failed")
                if sha256_bytes(audio_payload(path)) != before_audio:
                    raise RuntimeError("audio payload changed")
                if non_id3:
                    if tag_formats.serato_digest(path) != before_serato:
                        raise RuntimeError("Serato data changed")
                elif serato_digest(after_tags := ID3(path)) != before_serato:
                    if serato_payload_digest(after_tags) == before_serato_payload:
                        raise SeratoEncodingOnlyChange(
                            "Grouping write forced an ID3-version-compliance re-encoding of a Serato "
                            "frame's encoding byte (payload verified byte-identical); write rolled back"
                        )
                    raise RuntimeError("Serato frames changed")
            except SeratoEncodingOnlyChange as exc:
                # Verified benign, not a safety failure: restore and skip this
                # file rather than aborting the whole run.
                mutation_engine.restore_from_backup(backup_copy, path, before_hash)
                skipped_encoding_only.append({"path": str(path), "reason": str(exc)})
                txn.record(skipped_serato_encoding_only=True, reason=str(exc))
                continue

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
            txn.record(**{k: v for k, v in record.items() if k not in {"index", "path"}})
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
    # Every other engine-user records run_complete; without it the recovery
    # journal can't distinguish a finished grouping run from an abandoned one.
    engine.finish(result)
    print(json.dumps({**result, "result_path": str(result_path)}, indent=2))


def undo(undo_path: Path, heartbeat: lock_guard.LockHeartbeat) -> None:
    """Reverse an apply run.

    An undo is a mutation. It rewrites tags, so it gets the same treatment as
    an apply: ownership is re-verified before EVERY restore, not once at
    startup, and each restore is journaled write-ahead so a crash mid-undo is
    recoverable too. A half-finished undo nobody can reconstruct is exactly as
    damaging as a half-finished apply.
    """
    records = [json.loads(line) for line in undo_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    journal_path = undo_path.with_name(undo_path.stem + "-undo-recovery.jsonl")
    engine = mutation_engine.MutationEngine(
        journal_path, heartbeat, manifest=None, source_root=None,
        label="grouping-undo", allow_unbacked=True,
    )
    engine.start({"undo_log": str(undo_path), "records": len(records)})

    restored = 0
    for position, record in enumerate(reversed(records), start=1):
        path = Path(record["path"])
        backup = Path(record["backup"])
        with engine.item(
            position,
            kind=mutation_engine.GROUPING,
            source=path,
            backup_path=None,
            require_backup=False,
            extra_intent={"undoing_index": record.get("index"),
                          "restore_from": str(backup),
                          "restore_to_sha256": record["before_sha256"]},
        ) as txn:
            if sha256_file(backup) != record["before_sha256"]:
                raise RuntimeError(f"backup mismatch during undo: {backup}")
            shutil.copy2(backup, path)
            txn.mark_mutated()
            if sha256_file(path) != record["before_sha256"]:
                raise RuntimeError(f"undo verification failed: {path}")
            txn.record(restored_to=str(path), sha256=record["before_sha256"])
            restored += 1

    result = {"mode": "grouping-undo", "restored": restored, "recovery_journal": str(journal_path)}
    engine.finish(result)
    print(json.dumps(result, indent=2))


def _dry_run_backup_gate(manifest_path, root) -> str:
    """Report, without mutating, whether the backup gate would let an apply run."""
    if not manifest_path or root is None:
        return "not checked -- pass --backup-manifest to validate the backup gate"
    try:
        manifest = backup_manifest.require_pass(manifest_path, root)
    except backup_manifest.BackupGateError as exc:
        return f"WOULD REFUSE: {exc}"
    return f"PASS ({manifest['backup_id']}, {manifest['file_count']} files)"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, help="folder root the plan's paths live under (required unless --undo)")
    parser.add_argument("--plan", type=Path, help="grouping-plan.json from build_grouping_plan.py or build_grouping_csv_plan.py")
    parser.add_argument("--backup", type=Path,
                        help="pre-apply backup directory the per-item copies live in "
                             "(required with --apply, alongside --backup-manifest)")
    parser.add_argument("--backup-manifest", type=Path,
                        help="PASS manifest from backup_manifest.py verify (required with --apply). "
                             "A directory existing is not proof of a backup; this is.")
    parser.add_argument("--require-serato-backup", action="store_true",
                        help="also require the manifest to carry PASS Serato backup evidence")
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
        lock = lock_guard.require_lock_or_exit(args.lock_folder, args.session)
        heartbeat = lock_guard.LockHeartbeat(args.lock_folder, args.session)
        heartbeat.pin_run_id(lock)
        undo(args.undo.resolve(), heartbeat)
        return

    if not args.plan or not args.root:
        raise SystemExit("--plan and --root are required")
    label = args.label or args.root.name.casefold().replace(" ", "-")
    out_dir = args.out_dir or args.plan.resolve().parent
    root = args.root.resolve()

    if args.apply:
        if not args.backup or not args.backup_manifest:
            raise SystemExit(
                "--backup, --backup-manifest are required with --apply.\n"
                "Produce a manifest with:\n"
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
                source_root=root,
                serato_required=args.require_serato_backup,
            )
        except backup_manifest.BackupGateError as exc:
            raise SystemExit(f"REFUSING TO MUTATE: {exc}") from exc
        apply(args.plan, manifest, args.backup.resolve(), root, out_dir, label, heartbeat)
    else:
        # load_and_validate takes (plan_path, root); passing the backup dir here
        # used to raise TypeError on every dry run that included --backup.
        plan, items = load_and_validate(args.plan, root) if args.backup else (
            json.loads(args.plan.read_text(encoding="utf-8")), None
        )
        items = items if items is not None else plan["items"]
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "tracks": len(items),
                    "backup_gate": _dry_run_backup_gate(args.backup_manifest, root),
                    "changes_made": 0,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
