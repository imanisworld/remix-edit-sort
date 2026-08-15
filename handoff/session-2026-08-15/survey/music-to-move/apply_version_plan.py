#!/usr/bin/env python3
"""Apply or undo the reviewed `music to move` version-folder plan.

Moves only. Does not rename files, alter tags, delete files, or prune folders.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path


HERE = Path(__file__).resolve().parent
PLAN_PATH = HERE / "version-folder-plan.json"
ROOT = Path("/Users/djb.a.e/Music/DJ-Music/Music/music to move").resolve()
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aif", ".aiff", ".flac", ".ogg"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def inside_root(path: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(ROOT)
        return True
    except ValueError:
        return False


def audio_count() -> int:
    return sum(
        1
        for path in ROOT.rglob("*")
        if path.is_file() and path.suffix.lower() in AUDIO_EXTS
    )


def validate(items: list[dict]) -> list[tuple[Path, Path, str]]:
    prepared = []
    target_keys = set()
    errors = []
    for item in items:
        source = Path(item["source"]).resolve()
        target = Path(item["proposed_destination"]).resolve(strict=False)
        expected_hash = item["current"]["sha256"]
        if not inside_root(source) or not inside_root(target):
            errors.append(f"outside scope: {source} -> {target}")
            continue
        if not source.is_file():
            errors.append(f"missing source: {source}")
            continue
        target_key = os.path.normcase(str(target))
        if target_key in target_keys:
            errors.append(f"duplicate destination: {target}")
            continue
        target_keys.add(target_key)
        if target.exists() and os.path.normcase(str(source)) != target_key:
            errors.append(f"destination exists: {target}")
            continue
        actual_hash = sha256(source)
        if actual_hash != expected_hash:
            errors.append(f"hash changed: {source}")
            continue
        prepared.append((source, target, expected_hash))
    if errors:
        raise RuntimeError("Plan validation failed:\n" + "\n".join(errors))
    return prepared


def apply_plan() -> None:
    plan = json.loads(PLAN_PATH.read_text())
    prepared = validate(plan["items"])
    starting_count = audio_count()
    if starting_count != len(prepared):
        raise RuntimeError(f"Expected {len(prepared)} scoped audio files, found {starting_count}")

    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    undo_path = HERE / f"undo-{stamp}.jsonl"
    moved = 0
    with undo_path.open("a", encoding="utf-8") as undo:
        for source, target, expected_hash in prepared:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            undo.write(json.dumps({
                "from": str(source),
                "to": str(target),
                "sha256": expected_hash,
            }, ensure_ascii=False) + "\n")
            undo.flush()
            os.fsync(undo.fileno())
            moved += 1

    errors = []
    for _source, target, expected_hash in prepared:
        if not target.is_file():
            errors.append(f"missing destination: {target}")
        elif sha256(target) != expected_hash:
            errors.append(f"post-move hash mismatch: {target}")
    ending_count = audio_count()
    if ending_count != starting_count:
        errors.append(f"file count changed: {starting_count} -> {ending_count}")

    result = {
        "mode": "apply",
        "moved": moved,
        "starting_audio_count": starting_count,
        "ending_audio_count": ending_count,
        "undo_log": str(undo_path),
        "verification_errors": errors,
        "tags_changed": 0,
        "files_deleted": 0,
    }
    (HERE / f"apply-result-{stamp}.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if errors:
        sys.exit(2)


def undo(log_path: Path) -> None:
    entries = [json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]
    restored = 0
    for entry in reversed(entries):
        source = Path(entry["from"])
        target = Path(entry["to"])
        if source.exists():
            raise RuntimeError(f"Cannot undo; original path already exists: {source}")
        if not target.is_file():
            raise RuntimeError(f"Cannot undo; moved file missing: {target}")
        if sha256(target) != entry["sha256"]:
            raise RuntimeError(f"Cannot undo; moved file hash changed: {target}")
        source.parent.mkdir(parents=True, exist_ok=True)
        target.rename(source)
        restored += 1
    print(json.dumps({"mode": "undo", "restored": restored, "log": str(log_path)}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--undo", type=Path)
    args = parser.parse_args()
    if args.undo:
        undo(args.undo.resolve())
    elif args.apply:
        apply_plan()
    else:
        plan = json.loads(PLAN_PATH.read_text())
        prepared = validate(plan["items"])
        print(f"DRY RUN: {len(prepared)} validated moves; no changes made")


if __name__ == "__main__":
    main()
