#!/usr/bin/env python3
"""One-time, file-bound fix for the 8 tracks held across
metadata-mp3-cleandirty-20260823-181741's S016/S017/S018 because the shared
mutation engine detected a Serato GEOB encoding-byte-only difference on
write and, correctly, refused to trust it and rolled back.

Root cause (already documented at apply_new_music_intake_plan.py's
serato_payload_digest()): mutagen legitimately normalizes each GEOB frame's
encoding byte on save even when the frame's actual binary payload is
untouched. The strict digest (serato_digest()) treats that as a change; the
payload digest (serato_payload_digest()) -- the module's own documented
"ONLY correct digest to compare across a resave" -- does not.

This script does NOT change apply_new_music_intake_plan.py's mutation
engine or its default cautious rollback-and-skip behavior for the general
case -- that stays correct for any file this project hasn't individually
reviewed. It is a narrow, hardcoded, file-bound exception for exactly these
8 already-investigated tracks: the same Genre-preserving, single
Clean/Dirty-term Grouping addition every other file in that batch got, but
accepting the write when -- and only when -- the sole drift after resave is
that encoding-byte normalization. Audio payload and the actual Serato
payload bytes must still be provably byte-identical; anything else (audio
payload changed, Serato payload bytes actually changed, Grouping/Genre
verification failed) rolls back and refuses exactly like the shared engine
would.

Usage:
    python3 scripts/fix_held_serato_encoding_grouping_20260824.py --apply
    python3 scripts/fix_held_serato_encoding_grouping_20260824.py --dry-run
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "engine-user"
ENGINE_MUTATION_HELPERS = ("_write_one",)

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import apply_new_music_intake_plan as intake
import backup_manifest
import lock_guard
import metadata_batch
import mutation_engine

FOLDER_LOCK = Path(__file__).resolve().parent / "folder_lock.py"


def _lock_command(command: str, folder: str, session: str) -> dict:
    cmd = [sys.executable, str(FOLDER_LOCK), command, "--folder", folder, "--session", session]
    if command == "acquire":
        cmd += ["--agent", "fix-held-serato", "--note", "one-time fix for 8 held Serato-encoding-skip files"]
    result = subprocess.run(cmd, text=True, capture_output=True)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise metadata_batch.BatchRefused(
            f"lock {command} returned non-JSON output: {result.stdout} {result.stderr}"
        ) from exc
    if result.returncode != 0:
        raise metadata_batch.BatchRefused(f"lock {command} refused for {folder}: {payload}")
    expected_flag = "acquired" if command == "acquire" else "released"
    if not payload.get(expected_flag):
        raise metadata_batch.BatchRefused(f"lock {command} did not confirm {expected_flag}: {payload}")
    return payload

LOCK_FOLDER = "Hip Hop"
T5_VOLUME = metadata_batch.T5_VOLUME
OUT_ROOT = T5_VOLUME / "DJ/Backups/remix-edit-sort/fix-held-serato-20260824"

# (leaf source folder, [(path, expected_sha256, add_terms), ...])
GROUPS: list[tuple[str, list[tuple[str, str, list[str]]]]] = [
    (
        "/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/ACAPELLAS/Hip-Hop",
        [
            (
                "/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/ACAPELLAS/Hip-Hop/"
                "Common vs Tag Team - Universal Mind Control (UMC) (DJ Nasa Acapella Out) (Clean Extended).mp3",
                "0503e8fef58dee191dfbb1a29c3b31695432bb97467cffe31bf70c8bb36171fa", ["Clean"],
            ),
            (
                "/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/ACAPELLAS/Hip-Hop/"
                "Common vs Tag Team - Universal Mind Control (UMC) (DJ Nasa Acapella Out) (Dirty Extended).mp3",
                "de7aec5cce7690ba60ddcee1fa88466441d72b330df41eee1df0193c420ebd14", ["Dirty"],
            ),
        ],
    ),
    (
        "/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/Clean/Hip-Hop",
        [
            (
                "/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/Clean/Hip-Hop/"
                "Offset & Cardi B - FREAKY (Cesar Castilla Hype Intro) (Clean Extended).mp3",
                "0461055f45c62c71756cadbe7b72052da23554c3fb963135c77217fe40166b82", ["Clean"],
            ),
        ],
    ),
    (
        "/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/DIRTY/Hip-Hop",
        [
            (
                "/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/DIRTY/Hip-Hop/"
                "Bad Bunny - MONACO (Cesar Castilla Hype Intro) (Dirty Extended).mp3",
                "126adbc093b545c3dfd3ef0595f74e4ff7a52a8d8e1552265cf89f1d1f392941", ["Dirty"],
            ),
            (
                "/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/DIRTY/Hip-Hop/"
                "City Girls - Work For It (Cesar Castilla Slam Intro) (Dirty Extended).mp3",
                "74eb72d20a16bc206d9392be3ba754aad3ceb1a0e832fa58504563583903d80b", ["Dirty"],
            ),
            (
                "/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/DIRTY/Hip-Hop/"
                "Drake - What Would Pluto Do (Dennis Blaze ReFunk) (Dirty Extended).mp3",
                "aab096ab69e46a8c3827a1b693c4a3f932dbeca4d5975023f049ab40eda38795", ["Dirty"],
            ),
            (
                "/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/DIRTY/Hip-Hop/"
                "Offset & Cardi B - FREAKY (Cesar Castilla Hype Intro) (Dirty Extended).mp3",
                "7dcbf13fd32f1aeadbd57473517d7c84b93b3c57926caa28c625d6bf12b1378e", ["Dirty"],
            ),
            (
                "/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/DIRTY/Hip-Hop/"
                "Snoop Dogg ft Pharrell vs City Girls - Drop It Like It's Hot vs Work For It "
                "(DJ Nasa Transition 92-104) (Dirty Extended).mp3",
                "0576248cb6baee553a1bc0a84d954b3f85a4ed54e8c80dea4b8e708df61f2eba", ["Dirty"],
            ),
        ],
    ),
]


def _write_one(txn, path: Path, *, add_terms: list[str], tag_backup_root: Path,
               index: int, undo_path: Path) -> dict:
    before_hash = intake.sha256_file(path)
    before_audio = intake.sha256_bytes(intake.audio_payload(path))
    before_serato_strict = intake.serato_digest(path)
    before_serato_payload = intake.serato_payload_digest(path)
    existing_grouping, version = intake.read_grouping(path)
    original_genre = intake.read_genre(path)

    proposed_grouping = intake.combined_grouping(existing_grouping, add_terms)

    tag_backup = tag_backup_root / f"{index:05d}-fix-{path.name}"
    tag_backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, tag_backup)
    if intake.sha256_file(tag_backup) != before_hash:
        raise RuntimeError(f"per-item tag backup verification failed: {tag_backup}")
    txn.on_rollback(lambda: mutation_engine.restore_from_backup(tag_backup, path, before_hash))

    intake.write_metadata(path, genre_value=None, grouping_value=proposed_grouping, version=version)
    txn.mark_mutated()

    actual_grouping, _ = intake.read_grouping(path)
    expected_grouping_list = [part.strip() for part in proposed_grouping.split(";") if part.strip()]
    if actual_grouping != expected_grouping_list:
        raise RuntimeError(f"Grouping tag verification failed: {path}")
    if intake.read_genre(path) != original_genre:
        raise RuntimeError(f"Genre unexpectedly changed: {path}")
    if intake.sha256_bytes(intake.audio_payload(path)) != before_audio:
        raise RuntimeError(f"audio payload changed: {path}")

    after_serato_strict = intake.serato_digest(path)
    encoding_only_diff = after_serato_strict != before_serato_strict
    if encoding_only_diff:
        after_serato_payload = intake.serato_payload_digest(path)
        if after_serato_payload != before_serato_payload:
            raise RuntimeError(
                f"Serato PAYLOAD changed (a real change, not encoding-only -- refusing): {path}"
            )

    after_hash = intake.sha256_file(path)
    record = {
        "index": index, "kind": "metadata", "path": str(path),
        "tag_changed": True,
        "serato_encoding_only_diff_accepted": encoding_only_diff,
        "genre_requested": False, "grouping_requested": True,
        "old_genre": original_genre, "new_genre": None,
        "old_grouping": existing_grouping, "new_grouping": proposed_grouping,
        "tag_backup": str(tag_backup),
        "before_sha256": before_hash, "after_sha256": after_hash,
    }
    txn.record(**{k: v for k, v in record.items() if k not in {"index", "path"}})
    with undo_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return record


def run(*, apply_writes: bool) -> dict:
    for source_root, items in GROUPS:
        for path, expected_sha, _terms in items:
            live = intake.sha256_file(Path(path))
            if live != expected_sha:
                raise RuntimeError(f"live sha256 no longer matches the held expectation, refusing: {path}")
            grouping, _major = intake.read_grouping(Path(path))
            if grouping:
                raise RuntimeError(f"expected empty Grouping (untouched hold), found {grouping}: {path}")

    if not apply_writes:
        return {
            "mode": "dry-run", "groups": len(GROUPS),
            "items": sum(len(items) for _, items in GROUPS),
            "all_live_state_matches_held_expectation": True,
        }

    session = f"fix-held-serato-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}"
    acquired = _lock_command("acquire", LOCK_FOLDER, session)
    heartbeat = lock_guard.LockHeartbeat(LOCK_FOLDER, session)
    heartbeat.pin_run_id(acquired["lock"])

    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    out_dir = metadata_batch.require_t5_path(OUT_ROOT / stamp, T5_VOLUME)
    all_completed = []
    try:
        for group_index, (source_root, items) in enumerate(GROUPS, start=1):
            group_dir = out_dir / f"group-{group_index:02d}"
            backup_root = group_dir / "backup"
            manifest_path = group_dir / "backup-manifest.json"
            manifest = metadata_batch.create_verified_backup(
                Path(source_root), backup_root, manifest_path,
                t5_volume=T5_VOLUME, heartbeat=heartbeat,
            )
            tag_backup_root = group_dir / "tag-file-backups"
            undo_path = group_dir / "undo.jsonl"
            journal_path = group_dir / "recovery.jsonl"
            label = f"fix-held-serato-group{group_index:02d}"
            engine = mutation_engine.MutationEngine(journal_path, heartbeat, manifest, Path(source_root), label)
            engine.start({"group": group_index, "source_root": source_root, "items": len(items)})
            for item_index, (path, expected_sha, add_terms) in enumerate(items, start=1):
                with engine.item(
                    item_index, kind=mutation_engine.METADATA, source=Path(path),
                    expected_sha256=expected_sha,
                    extra_intent={"add_grouping_terms": add_terms},
                ) as txn:
                    all_completed.append(_write_one(
                        txn, Path(path), add_terms=add_terms,
                        tag_backup_root=tag_backup_root, index=item_index, undo_path=undo_path,
                    ))
            group_result = {
                "group": group_index, "source_root": source_root, "items": len(items),
                "recovery_journal": str(journal_path), "undo_log": str(undo_path),
            }
            engine.finish(group_result)
    finally:
        _lock_command("release", LOCK_FOLDER, session)

    return {
        "mode": "apply", "groups": len(GROUPS), "items": len(all_completed),
        "encoding_only_accepted": sum(1 for r in all_completed if r["serato_encoding_only_diff_accepted"]),
        "out_dir": str(out_dir),
        "completed": all_completed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.apply == args.dry_run:
        raise SystemExit("pass exactly one of --apply or --dry-run")
    try:
        result = run(apply_writes=args.apply)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (metadata_batch.BatchRefused, backup_manifest.BackupGateError, RuntimeError) as exc:
        print(f"REFUSED: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
