#!/usr/bin/env python3
"""Apply the VDBL-into-main-library merge plan (build_vdbl_merge_plan.py's
output), with the same verify/rollback/undo discipline as the reusable
apply engines: per-item audio-payload + Serato-frame hash verification
before and after every write, TDRC year writes skipped+logged (not
force-upgraded) on ID3v2.2 files, mandatory lock via lock_guard.py across
every folder this touches (Events, Hip Hop, R&b, Amapiano, Country), and a
heartbeat-renewed lock throughout the run.

Session-specific one-off script (hardcoded local paths) -- matches the
project's existing convention for handoff/session-2026-08-15/ scripts;
generalize before reuse on another merge.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError, TDRC

SCRIPTS_DIR = Path("/Users/djb.a.e/Music/DJ-Music/Tools/remix-edit-sort/scripts")
sys.path.insert(0, str(SCRIPTS_DIR))
import lock_guard  # noqa: E402
from apply_version_genre_plan import (  # noqa: E402
    sha256_bytes,
    sha256_file,
    audio_payload,
    serato_digest,
    serato_payload_digest,
)

LOCK_FOLDERS = ["Events", "Hip Hop", "R&b", "Amapiano ", "Country"]
SESSION = "claude-vdbl-merge"


def read_year(path: Path) -> str:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return ""
    frames = tags.getall("TDRC")
    return str(frames[0]).strip() if frames else ""


def write_year(path: Path, value: str) -> None:
    try:
        tags = ID3(path)
        original_v2_version = tags.version[1]
    except ID3NoHeaderError:
        tags = ID3()
        original_v2_version = 4
    text_encoding = 3 if original_v2_version == 4 else 1
    tags.delall("TDRC")
    tags.add(TDRC(encoding=text_encoding, text=[value]))
    tags.save(path, v2_version=original_v2_version)


def year_skip_reason(path: Path, item: dict) -> str | None:
    if not item.get("write_tdrc_year") or path.suffix.casefold() != ".mp3":
        return None
    try:
        if ID3(path).version[1] == 2:
            return "ID3v2.2 preserved; TDRC year write skipped"
    except ID3NoHeaderError:
        pass
    return None


def apply(plan_path: Path, backup: Path, out_dir: Path, label: str, heartbeats: list) -> None:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("mode") != "preview-only" or not plan.get("rules", {}).get("approval_required"):
        raise RuntimeError("unexpected plan mode")
    items = plan["items"]
    if not backup.is_dir():
        raise RuntimeError(f"verified pre-apply backup is missing: {backup}")

    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    undo_path = out_dir / f"{label}-undo-{stamp}.jsonl"
    result_path = out_dir / f"{label}-result-{stamp}.json"
    tag_backup_root = out_dir / f"{label}-tag-file-backups" / stamp

    completed = []
    skipped_exact_dup = []
    skipped_needs_review = []
    errors = []

    for index, item in enumerate(items, start=1):
        for hb in heartbeats:
            hb.beat()

        source = Path(item["source"])
        destination = Path(item["destination"])

        collision = item.get("collision")
        if collision and collision["type"] == "EXACT_DUPLICATE":
            skipped_exact_dup.append(item)
            continue
        if collision and collision["type"] == "NAME_COLLISION_DIFFERENT_CONTENT":
            skipped_needs_review.append(item)
            continue

        if not source.is_file():
            errors.append({"source": str(source), "error": "source missing"})
            continue

        actual_hash = sha256_file(source)
        if actual_hash != item["sha256"]:
            errors.append({"source": str(source), "error": "source hash changed since plan build"})
            continue

        if destination.exists():
            errors.append({
                "source": str(source), "destination": str(destination),
                "error": "destination now exists (created after plan build) -- skipping, needs manual review",
            })
            continue

        before_hash = actual_hash
        before_audio = sha256_bytes(audio_payload(source))
        before_serato = serato_digest(source)
        before_serato_payload = serato_payload_digest(source)

        destination.parent.mkdir(parents=True, exist_ok=True)
        # Same volume (one Music library), so this is a real atomic rename,
        # not copy+delete.
        os.rename(source, destination)

        tag_backup = None
        tag_was_changed = False
        skip_reason = year_skip_reason(destination, item)

        if item.get("write_tdrc_year") and not skip_reason:
            tag_backup = tag_backup_root / item["genre"] / source.name
            tag_backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, tag_backup)
            try:
                write_year(destination, item["write_tdrc_year"])
                if read_year(destination) != item["write_tdrc_year"]:
                    raise RuntimeError("year tag verification failed")
                if sha256_bytes(audio_payload(destination)) != before_audio:
                    raise RuntimeError("audio payload changed")
                if serato_digest(destination) != before_serato:
                    if serato_payload_digest(destination) == before_serato_payload:
                        skip_reason = (
                            "Serato encoding-only re-encode; write kept "
                            "(payload verified byte-identical), not treated as a hard failure"
                        )
                    else:
                        raise RuntimeError("Serato frames changed")
                else:
                    tag_was_changed = True
            except RuntimeError as exc:
                # Roll back the tag write only -- the move itself already
                # succeeded and is not undone here, undo.py handles that.
                shutil.copy2(tag_backup, destination)
                skip_reason = f"tag write rolled back: {exc}"

        after_hash = sha256_file(destination)

        record = {
            "source": str(source),
            "destination": str(destination),
            "moved": True,
            "genre": item["genre"],
            "tag_changed": tag_was_changed,
            "tag_skip_reason": skip_reason,
            "written_year": item.get("write_tdrc_year") if tag_was_changed else None,
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

        if index % 25 == 0:
            print(f"...{index}/{len(items)}", file=sys.stderr)

    result = {
        "mode": f"{label}-apply",
        "plan": str(plan_path),
        "verified_backup": str(backup),
        "tracks_processed": len(completed),
        "files_moved": sum(r["moved"] for r in completed),
        "year_tags_changed": sum(r["tag_changed"] for r in completed),
        "year_tags_skipped": sum(1 for r in completed if r["tag_skip_reason"]),
        "skipped_exact_duplicates": len(skipped_exact_dup),
        "skipped_needs_review_collisions": len(skipped_needs_review),
        "errors": errors,
        "undo_log": str(undo_path),
        "tag_backup_root": str(tag_backup_root),
    }
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({**result, "result_path": str(result_path)}, indent=2))


def undo(undo_path: Path) -> None:
    records = [json.loads(line) for line in undo_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    restored = 0
    for record in reversed(records):
        source = Path(record["source"])
        destination = Path(record["destination"])
        if record["moved"]:
            if source.exists():
                raise RuntimeError(f"undo source already exists: {source}")
            source.parent.mkdir(parents=True, exist_ok=True)
            os.rename(destination, source)
        if record["tag_changed"] and record["tag_backup"]:
            backup = Path(record["tag_backup"])
            if sha256_file(backup) != record["before_sha256"]:
                raise RuntimeError(f"tag backup mismatch: {backup}")
            shutil.copy2(backup, source)
        if sha256_file(source) != record["before_sha256"]:
            raise RuntimeError(f"undo verification failed: {source}")
        restored += 1
    print(json.dumps({"mode": "vdbl-merge-undo", "restored": restored}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--backup", type=Path, help="verified pre-apply backup of VDBL (required with --apply)")
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--label", default="vdbl-merge")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--undo", type=Path)
    args = parser.parse_args()

    out_dir = args.out_dir or args.plan.resolve().parent

    if args.undo:
        for folder in LOCK_FOLDERS:
            lock_guard.require_lock_or_exit(folder, SESSION)
        undo(args.undo.resolve())
        return

    if args.apply:
        if not args.backup:
            raise SystemExit("--backup is required with --apply")
        for folder in LOCK_FOLDERS:
            lock_guard.require_lock_or_exit(folder, SESSION)
        heartbeats = [lock_guard.LockHeartbeat(f, SESSION) for f in LOCK_FOLDERS]
        apply(args.plan, args.backup.resolve(), out_dir, args.label, heartbeats)
    else:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        items = plan["items"]
        print(json.dumps({"mode": "dry-run", "tracks": len(items)}, indent=2))


if __name__ == "__main__":
    main()
