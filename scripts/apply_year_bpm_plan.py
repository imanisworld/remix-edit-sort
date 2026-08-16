#!/usr/bin/env python3
"""Apply an approved Year/BPM backfill plan, with verification and undo.

Same verify/rollback/undo discipline as apply_grouping_plan.py and
apply_artist_title_plan.py: audio payload and Serato frames are hashed
before and after every write, any mismatch rolls the file back immediately,
and a verified-benign Serato encoding-only difference is treated as a skip
rather than a hard failure (see apply_version_genre_plan.py's
SeratoEncodingOnlyChange for why that can legitimately happen).

Year is written as TDRC on ID3v2.4 files and TYER on ID3v2.3 files, matching
each file's existing tag version -- never converting version. BPM is written
as TBPM, which is version-independent. A plan item may propose only one of
the two fields; only the proposed field(s) are written.

Requires an owned, non-stale folder_lock.py lock via lock_guard.py for both
--apply and --undo, matching the other three mutation-capable scripts.
"""

from __future__ import annotations

# Every library mutation in this module goes through scripts/mutation_engine.py.
SAFETY_CLASSIFICATION = "engine-user"

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError, TYER, TDRC, TBPM

sys.path.insert(0, str(Path(__file__).resolve().parent))
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


def read_year_bpm(path: Path) -> tuple[str, str, int]:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return "", "", 4
    year_frames = tags.getall("TDRC") or tags.getall("TYER")
    bpm_frames = tags.getall("TBPM")
    year = str(year_frames[0]).strip()[:4] if year_frames else ""
    bpm = str(bpm_frames[0]).strip() if bpm_frames else ""
    return year, bpm, tags.version[1]


def write_year_bpm(path: Path, year: str | None, bpm: str | None, version: int) -> None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()
    encoding = 3 if version == 4 else 1
    if year:
        tags.delall("TYER")
        tags.delall("TDRC")
        if version == 4:
            tags.add(TDRC(encoding=encoding, text=[year]))
        else:
            tags.add(TYER(encoding=encoding, text=[year]))
    if bpm:
        tags.delall("TBPM")
        tags.add(TBPM(encoding=encoding, text=[bpm]))
    tags.save(path, v2_version=version)


def load_and_validate(plan_path: Path) -> tuple[dict, list[dict]]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("mode") != "preview-only" or not plan.get("rules", {}).get("approval_required"):
        raise RuntimeError("unexpected plan mode")
    items = plan["items"]

    errors = []
    for item in items:
        path = Path(item["path"])
        if not path.is_file():
            errors.append(f"missing file: {path}")
            continue
        current_year, current_bpm, current_version = read_year_bpm(path)
        if current_version != item["id3_version"]:
            errors.append(f"ID3 version changed since preview: {path}: {current_version} != {item['id3_version']}")
            continue
        if current_year != item["current_year"][:4]:
            errors.append(f"Year changed since preview: {path}: {current_year!r} != {item['current_year']!r}")
        if current_bpm != item["current_bpm"]:
            errors.append(f"BPM changed since preview: {path}: {current_bpm!r} != {item['current_bpm']!r}")
    if errors:
        raise RuntimeError("Year/BPM plan validation failed:\n" + "\n".join(errors))
    return plan, items


def apply(plan_path: Path, manifest: dict, backup: Path, backup_relative_to: Path, out_dir: Path, label: str, heartbeat: lock_guard.LockHeartbeat) -> None:
    plan, items = load_and_validate(plan_path)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    undo_path = out_dir / f"{label}-year-bpm-undo-{stamp}.jsonl"
    result_path = out_dir / f"{label}-year-bpm-result-{stamp}.json"
    journal_path = out_dir / f"{label}-recovery-{stamp}.jsonl"
    engine = mutation_engine.MutationEngine(
        journal_path, heartbeat, manifest, backup_relative_to, label,
    )
    engine.start({"plan": str(plan_path), "items": len(items)})
    completed = []
    skipped_encoding_only = []

    for index, item in enumerate(items, start=1):
        path = Path(item["path"])
        relative = path.relative_to(backup_relative_to)
        backup_copy = backup / relative

        # The engine performs the heartbeat, the source-exists and
        # backup-evidence checks (both the manifest entry for this exact file
        # and this per-item backup copy's hash), the write-ahead intent
        # record, the rollback on failure, and the completion record.
        with engine.item(
            index,
            kind=mutation_engine.YEAR_BPM,
            source=path,
            backup_path=backup_copy,
            extra_intent={
                "current_year": item["current_year"], "current_bpm": item["current_bpm"],
                "proposed_year": item["proposed_year"], "proposed_bpm": item["proposed_bpm"],
            },
        ) as txn:
            before_hash = sha256_file(path)
            txn.on_rollback(
                lambda p=path, b=backup_copy, h=before_hash:
                mutation_engine.restore_from_backup(b, p, h)
            )
            before_audio = sha256_bytes(audio_payload(path))
            before_tags = ID3(path)
            before_serato = serato_digest(before_tags)
            before_serato_payload = serato_payload_digest(before_tags)
            version = before_tags.version[1]

            try:
                write_year_bpm(path, item["proposed_year"], item["proposed_bpm"], version)
                txn.mark_mutated()
                after_year, after_bpm, after_version = read_year_bpm(path)
                if item["proposed_year"] and after_year != item["proposed_year"]:
                    raise RuntimeError("Year verification failed")
                if item["proposed_bpm"] and after_bpm != item["proposed_bpm"]:
                    raise RuntimeError("BPM verification failed")
                if after_version != version:
                    raise RuntimeError("ID3 version changed")
                if sha256_bytes(audio_payload(path)) != before_audio:
                    raise RuntimeError("audio payload changed")
                after_tags = ID3(path)
                if serato_digest(after_tags) != before_serato:
                    if serato_payload_digest(after_tags) == before_serato_payload:
                        raise SeratoEncodingOnlyChange(
                            "Year/BPM write forced an ID3-version-compliance re-encoding of a "
                            "Serato frame's encoding byte (payload verified byte-identical); "
                            "write rolled back"
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
                "old_year": item["current_year"],
                "old_bpm": item["current_bpm"],
                "new_year": item["proposed_year"],
                "new_bpm": item["proposed_bpm"],
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
        "mode": f"{label}-year-bpm-apply",
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
        label="year-bpm-undo", allow_unbacked=True,
    )
    engine.start({"undo_log": str(undo_path), "records": len(records)})

    restored = 0
    for position, record in enumerate(reversed(records), start=1):
        path = Path(record["path"])
        backup = Path(record["backup"])
        with engine.item(
            position,
            kind=mutation_engine.YEAR_BPM,
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

    result = {"mode": "year-bpm-undo", "restored": restored, "recovery_journal": str(journal_path)}
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
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--backup", type=Path,
                        help="pre-apply backup directory the per-item copies live in "
                             "(required with --apply, alongside --backup-manifest)")
    parser.add_argument("--backup-manifest", type=Path,
                        help="PASS manifest from backup_manifest.py verify (required with --apply). "
                             "A directory existing is not proof of a backup; this is.")
    parser.add_argument("--require-serato-backup", action="store_true",
                        help="also require the manifest to carry PASS Serato backup evidence")
    parser.add_argument("--backup-relative-to", type=Path, help="root the backup mirrors (paths in plan are relative to this)")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--label", default="year-bpm")
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

    if not args.plan:
        raise SystemExit("--plan is required")
    out_dir = args.out_dir or args.plan.resolve().parent

    if args.apply:
        if not args.backup or not args.backup_relative_to or not args.backup_manifest:
            raise SystemExit(
                "--backup, --backup-manifest and --backup-relative-to are required with --apply.\n"
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
                source_root=args.backup_relative_to.resolve(),
                serato_required=args.require_serato_backup,
            )
        except backup_manifest.BackupGateError as exc:
            raise SystemExit(f"REFUSING TO MUTATE: {exc}") from exc
        apply(args.plan, manifest, args.backup.resolve(), args.backup_relative_to.resolve(), out_dir, args.label, heartbeat)
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
                    "backup_gate": _dry_run_backup_gate(args.backup_manifest, args.backup_relative_to.resolve() if args.backup_relative_to else None),
                    "changes_made": 0,
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
