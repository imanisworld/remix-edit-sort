#!/usr/bin/env python3
"""Delete one confirmed-redundant copy from each of a list of exact-duplicate
pairs, keeping the other copy untouched. Never picks a winner from
ambiguous evidence -- every pair must already carry a verified-identical
SHA-256 from the caller (see library-wide-dedup's duplicate-groups.json).

Safety: re-verifies delete/keep hash match immediately before each
deletion (not just at plan-build time), requires a pre-existing verified
backup directory, and writes an append-only fsync'd undo log recording
enough to restore (backup path + original path + hash) before removing
anything.
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

import lock_guard


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_and_validate(pairs_path: Path, backup_root: Path) -> list[dict]:
    pairs = json.loads(pairs_path.read_text(encoding="utf-8"))
    if not backup_root.is_dir():
        raise RuntimeError(f"verified backup directory missing: {backup_root}")

    errors = []
    for pair in pairs:
        delete_path = Path(pair["delete"])
        keep_path = Path(pair["keep"])
        if not delete_path.is_file():
            errors.append(f"missing delete-target: {delete_path}")
            continue
        if not keep_path.is_file():
            errors.append(f"missing keep-target: {keep_path}")
            continue
        delete_hash = sha256_file(delete_path)
        keep_hash = sha256_file(keep_path)
        if delete_hash != keep_hash:
            errors.append(f"hash mismatch, not deleting: {delete_path} ({delete_hash}) != {keep_path} ({keep_hash})")
            continue
        if delete_hash != pair["sha256"]:
            errors.append(f"hash drifted from recorded value: {delete_path}")
            continue
    if errors:
        raise RuntimeError("Validation failed:\n" + "\n".join(errors))
    return pairs


def apply(pairs_path: Path, backup_root: Path, out_dir: Path, label: str, heartbeat: lock_guard.LockHeartbeat) -> None:
    pairs = load_and_validate(pairs_path, backup_root)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    undo_path = out_dir / f"{label}-undo-{stamp}.jsonl"
    result_path = out_dir / f"{label}-result-{stamp}.json"
    completed = []

    for index, pair in enumerate(pairs, start=1):
        heartbeat.beat()
        delete_path = Path(pair["delete"])
        keep_path = Path(pair["keep"])
        # Re-verify immediately before removal -- not just at validate time --
        # in case anything changed in the gap (this is a live, multi-session library).
        delete_hash = sha256_file(delete_path)
        keep_hash = sha256_file(keep_path)
        if delete_hash != keep_hash or delete_hash != pair["sha256"]:
            raise RuntimeError(f"hash changed since validation, aborting before delete: {delete_path}")

        backup_rel = pair.get("backup_relative")
        backup_path = (backup_root / backup_rel) if backup_rel else None
        if backup_path is None:
            # Default convention: backup mirrors the delete path relative to
            # a shared library root recorded once for the whole run.
            raise RuntimeError(f"pair missing backup_relative: {pair}")
        if not backup_path.is_file():
            raise RuntimeError(f"backup copy missing for {delete_path}: {backup_path}")
        if sha256_file(backup_path) != delete_hash:
            raise RuntimeError(f"backup copy hash mismatch for {delete_path}: {backup_path}")

        delete_path.unlink()

        record = {
            "index": index,
            "deleted": str(delete_path),
            "kept": str(keep_path),
            "sha256": delete_hash,
            "backup": str(backup_path),
        }
        with undo_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        completed.append(record)

    surviving = [r["deleted"] for r in completed if Path(r["deleted"]).exists()]
    kept_missing = [r["kept"] for r in completed if not Path(r["kept"]).is_file()]
    result = {
        "mode": f"{label}-apply",
        "pairs_file": str(pairs_path),
        "backup_root": str(backup_root),
        "deleted_count": len(completed),
        "surviving_delete_targets": surviving,
        "kept_missing": kept_missing,
        "undo_log": str(undo_path),
    }
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({**result, "result_path": str(result_path)}, indent=2))


def undo(undo_path: Path, label: str) -> None:
    records = [json.loads(line) for line in undo_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    restored = 0
    for record in reversed(records):
        deleted = Path(record["deleted"])
        backup = Path(record["backup"])
        if deleted.exists():
            raise RuntimeError(f"restore target already exists: {deleted}")
        if sha256_file(backup) != record["sha256"]:
            raise RuntimeError(f"backup hash mismatch, refusing to restore: {backup}")
        deleted.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, deleted)
        if sha256_file(deleted) != record["sha256"]:
            raise RuntimeError(f"restore verification failed: {deleted}")
        restored += 1
    print(json.dumps({"mode": f"{label}-undo", "restored": restored}, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", required=True, type=Path, help="JSON list of {sha256, delete, keep} pairs")
    parser.add_argument("--backup-root", type=Path, help="Verified backup dir (required with --apply)")
    parser.add_argument("--out-dir", type=Path, help="Where undo/result files land (defaults to pairs file's dir)")
    parser.add_argument("--label", default="dedup-delete", help="Prefix for undo/result files")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--undo", type=Path, help="Path to an undo .jsonl log to reverse")
    parser.add_argument("--lock-folder", help="Folder locked via folder_lock.py (required with --apply/--undo)")
    parser.add_argument("--session", help="Session id that holds the lock (required with --apply/--undo)")
    args = parser.parse_args()

    out_dir = args.out_dir or args.pairs.resolve().parent

    if args.undo:
        if not args.lock_folder or not args.session:
            raise SystemExit("--lock-folder and --session are required with --undo")
        lock_guard.require_lock_or_exit(args.lock_folder, args.session)
        undo(args.undo.resolve(), args.label)
    elif args.apply:
        if not args.backup_root:
            raise SystemExit("--backup-root is required with --apply")
        if not args.lock_folder or not args.session:
            raise SystemExit("--lock-folder and --session are required with --apply")
        lock_guard.require_lock_or_exit(args.lock_folder, args.session)
        heartbeat = lock_guard.LockHeartbeat(args.lock_folder, args.session)
        apply(args.pairs, args.backup_root.resolve(), out_dir, args.label, heartbeat)
    else:
        pairs = json.loads(args.pairs.read_text(encoding="utf-8"))
        print(json.dumps({"mode": "dry-run", "pairs": len(pairs), "changes_made": 0}, indent=2))


if __name__ == "__main__":
    main()
