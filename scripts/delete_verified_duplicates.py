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

``--mode audio-match`` (owner rule approved 2026-10-15, see
duplicate_semantics.py) accepts pairs whose bytes differ but whose audio is the
same recording: identical audio payload, or Chromaprint >= 0.85 + durations
within 3 s + same version markers + the removed copy lower quality. The rule is
re-derived from fresh probes of BOTH files inside the engine transaction,
immediately before each delete; the caller's claims are never trusted. The
kept copy must exist. Backup, journal, rollback and undo are identical to the
exact mode.
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

import backup_manifest
import audio_fingerprint
import duplicate_semantics
import lock_guard
import mutation_engine

MODES = ("exact", "audio-match")
OUT_OF_SCOPE_FOLDERS = {"imani drops"}


def scope_errors(*paths: Path) -> list[str]:
    """Imani Drops is never touched, kept or deleted."""
    return [f"out of scope (Imani Drops is never touched): {p}" for p in paths
            if any(part.strip().casefold() in OUT_OF_SCOPE_FOLDERS for part in Path(p).parts)]


def same_file(a: Path, b: Path) -> bool:
    """Same file on disk, even when spelled differently (APFS ignores case/accents)."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return Path(a).resolve() == Path(b).resolve()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_and_validate(pairs_path: Path) -> list[dict]:
    """Every pair must be a proven EXACT_DUPLICATE before it is even eligible.

    The repository-wide truth model (scripts/duplicate_semantics.py) allows
    exactly one basis for calling two files duplicates: an identical SHA-256
    over the whole file. Same filename, same title, same duration -- alone or
    together -- can only ever produce REVIEW. This gate re-derives the verdict
    here rather than trusting whatever the caller's JSON claims, so a pairs
    file built by an older or sloppier tool cannot smuggle a filename-based
    "duplicate" into a deletion run.
    """
    pairs = json.loads(pairs_path.read_text(encoding="utf-8"))

    errors = []
    for pair in pairs:
        delete_path = Path(pair["delete"])
        keep_path = Path(pair["keep"])
        if scope_errors(delete_path, keep_path):
            errors.extend(scope_errors(delete_path, keep_path))
            continue
        if not delete_path.is_file():
            errors.append(f"missing delete-target: {delete_path}")
            continue
        if not keep_path.is_file():
            errors.append(f"missing keep-target: {keep_path}")
            continue
        if same_file(delete_path, keep_path):
            errors.append(f"delete and keep are the same file: {delete_path} / {keep_path}")
            continue
        delete_hash = sha256_file(delete_path)
        keep_hash = sha256_file(keep_path)
        if delete_hash != keep_hash:
            errors.append(f"hash mismatch, not deleting: {delete_path} ({delete_hash}) != {keep_path} ({keep_hash})")
            continue
        if delete_hash != pair["sha256"]:
            errors.append(f"hash drifted from recorded value: {delete_path}")
            continue
        verdict = duplicate_semantics.classify(
            sha256_a=delete_hash, sha256_b=keep_hash,
            name_a=delete_path.name, name_b=keep_path.name,
        )
        if not duplicate_semantics.may_authorize_deletion(verdict["verdict"]):
            errors.append(
                f"refusing to delete: {delete_path} vs {keep_path} classifies as "
                f"{verdict['verdict']} ({verdict['reason']}), not EXACT_DUPLICATE"
            )
            continue
    if errors:
        raise RuntimeError("Validation failed:\n" + "\n".join(errors))
    return pairs


def audio_match_verdict(delete_path: Path, keep_path: Path, probe) -> dict:
    """Probe both files now and apply the owner audio-match rule."""
    if scope_errors(delete_path, keep_path):
        raise RuntimeError("; ".join(scope_errors(delete_path, keep_path)))
    if not keep_path.is_file():
        raise RuntimeError(f"keep-target missing, refusing: {keep_path}")
    if same_file(delete_path, keep_path):
        raise RuntimeError(f"delete and keep are the same file: {delete_path}")
    d, k = probe(delete_path), probe(keep_path)
    if d["payload_sha256"] == k["payload_sha256"]:
        detail = {"similarity": 1.0, "coverage": None, "min_window": None}
    else:
        detail = audio_fingerprint.match_detail(d["fingerprint"], k["fingerprint"])
    verdict = duplicate_semantics.classify_audio_match(
        delete=d, keep=k, similarity=detail["similarity"],
        coverage=detail["coverage"], min_window=detail["min_window"])
    verdict.update(detail)
    verdict["delete_bitrate"], verdict["keep_bitrate"] = d["bitrate"], k["bitrate"]
    verdict["markers"] = d["markers"]
    verdict["version"] = d.get("version", {})
    return verdict


def load_and_validate_audio_match(pairs_path: Path, probe) -> list[dict]:
    pairs = json.loads(pairs_path.read_text(encoding="utf-8"))
    errors = []
    deletes = set()
    for pair in pairs:
        delete_path = Path(pair["delete"])
        keep_path = Path(pair["keep"])
        if str(delete_path.resolve()) in deletes:
            errors.append(f"duplicate delete-target in pairs: {delete_path}")
            continue
        deletes.add(str(delete_path.resolve()))
        if not delete_path.is_file():
            errors.append(f"missing delete-target: {delete_path}")
            continue
        if sha256_file(delete_path) != pair["sha256"]:
            errors.append(f"hash drifted from recorded value: {delete_path}")
            continue
        # The kept file is pinned too: the human approved THAT file being kept.
        if not pair.get("keep_sha256"):
            errors.append(f"pair has no keep_sha256 (the kept file must be pinned): {keep_path}")
            continue
        if keep_path.is_file() and sha256_file(keep_path) != pair["keep_sha256"]:
            errors.append(f"keep-target changed since approval: {keep_path}")
            continue
        try:
            verdict = audio_match_verdict(delete_path, keep_path, probe)
        except Exception as exc:  # noqa: BLE001 - every refusal is reported
            errors.append(str(exc))
            continue
        if not duplicate_semantics.may_authorize_audio_match_deletion(verdict):
            errors.append(f"refusing: {delete_path} vs {keep_path}: {verdict['verdict']} ({verdict['reason']})")
    # A file being kept may not also be deleted in the same run.
    for pair in pairs:
        keep_path = Path(pair["keep"])
        if str(keep_path.resolve()) in deletes or any(
                same_file(keep_path, Path(other["delete"])) for other in pairs if Path(other["delete"]).exists()):
            errors.append(f"keep-target is also a delete-target: {pair['keep']}")
    if errors:
        raise RuntimeError("Validation failed:\n" + "\n".join(errors))
    return pairs


def audio_match_report(pairs_path: Path, probe) -> list[dict]:
    """Read-only per-pair evidence for the approver: verdict, similarity,
    weakest stretch, bitrates and version labels. Never raises on a refusal."""
    rows = []
    for pair in json.loads(pairs_path.read_text(encoding="utf-8")):
        row = {"delete": pair["delete"], "keep": pair["keep"]}
        try:
            verdict = audio_match_verdict(Path(pair["delete"]), Path(pair["keep"]), probe)
            row.update({k: verdict.get(k) for k in ("verdict", "reason", "similarity", "coverage", "min_window",
                                                     "delete_bitrate", "keep_bitrate", "markers", "version")})
            row["removable"] = duplicate_semantics.may_authorize_audio_match_deletion(verdict)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            row.update({"verdict": "REFUSED", "reason": str(exc), "removable": False})
        rows.append(row)
    return rows


def apply(pairs_path: Path, manifest: dict, source_root: Path, backup_root: Path,
          out_dir: Path, label: str, heartbeat: lock_guard.LockHeartbeat,
          mode: str = "exact", probe=None) -> None:
    if mode not in MODES:
        raise RuntimeError(f"unknown mode {mode!r}")
    probe = probe or audio_fingerprint.probe
    pairs = load_and_validate(pairs_path) if mode == "exact" else load_and_validate_audio_match(pairs_path, probe)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    undo_path = out_dir / f"{label}-undo-{stamp}.jsonl"
    result_path = out_dir / f"{label}-result-{stamp}.json"
    journal_path = out_dir / f"{label}-recovery-{stamp}.jsonl"
    engine = mutation_engine.MutationEngine(
        journal_path, heartbeat, manifest, source_root, label,
    )
    engine.start({"pairs_file": str(pairs_path), "pairs": len(pairs)})
    completed = []

    for index, pair in enumerate(pairs, start=1):
        delete_path = Path(pair["delete"])
        keep_path = Path(pair["keep"])
        backup_rel = pair.get("backup_relative")
        if not backup_rel:
            # No default convention: a deletion whose restore path has to be
            # guessed is not a deletion this tool will perform.
            raise RuntimeError(f"pair missing backup_relative: {pair}")
        backup_path = backup_root / backup_rel

        # The engine performs the heartbeat, re-hashes the source immediately
        # before the delete (not just at validate time -- this is a live,
        # multi-session library), proves this exact file is in the verified
        # backup, and writes the recovery intent BEFORE the unlink. That
        # ordering is what makes a crash mid-delete recoverable: the journal
        # already names the file, its hash, and the backup copy to restore from.
        with engine.item(
            index,
            kind=mutation_engine.DELETE,
            source=delete_path,
            expected_sha256=pair["sha256"],
            backup_path=backup_path,
            extra_intent={"kept": str(keep_path), "mode": mode,
                          "verdict": duplicate_semantics.EXACT_DUPLICATE if mode == "exact"
                          else duplicate_semantics.AUDIO_EQUIVALENT},
        ) as txn:
            delete_hash = sha256_file(delete_path)
            if mode == "exact":
                keep_hash = sha256_file(keep_path)
                if delete_hash != keep_hash or delete_hash != pair["sha256"]:
                    raise RuntimeError(
                        f"hash changed since validation, aborting before delete: {delete_path}"
                    )
                evidence = {}
            else:
                if delete_hash != pair["sha256"]:
                    raise RuntimeError(f"hash changed since validation, aborting before delete: {delete_path}")
                if sha256_file(keep_path) != pair["keep_sha256"]:
                    raise RuntimeError(f"kept file changed since approval, aborting before delete: {keep_path}")
                verdict = audio_match_verdict(delete_path, keep_path, probe)
                if not duplicate_semantics.may_authorize_audio_match_deletion(verdict):
                    raise RuntimeError(
                        f"audio-match rule no longer holds, aborting before delete: {delete_path}: {verdict['reason']}")
                evidence = {"rule": duplicate_semantics.OWNER_AUDIO_MATCH_RULE,
                            "similarity": verdict["similarity"], "reason": verdict["reason"]}
            txn.on_rollback(
                lambda p=delete_path, b=backup_path, h=delete_hash:
                mutation_engine.restore_from_backup(b, p, h)
            )

            delete_path.unlink()
            txn.mark_mutated()

            if delete_path.exists():
                raise RuntimeError(f"delete did not take effect: {delete_path}")
            if not keep_path.is_file() or same_file(delete_path, keep_path):
                raise RuntimeError(f"the copy being kept vanished during the run: {keep_path}")

            record = {
                "index": index,
                "deleted": str(delete_path),
                "kept": str(keep_path),
                "sha256": delete_hash,
                "backup": str(backup_path),
                **evidence,
            }
            txn.record(kept=str(keep_path), sha256=delete_hash, backup=str(backup_path))
            with undo_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            completed.append(record)

    surviving = [r["deleted"] for r in completed if Path(r["deleted"]).exists()]
    kept_missing = [r["kept"] for r in completed if not Path(r["kept"]).is_file()]
    result = {
        "mode": f"{label}-apply",
        "dedupe_mode": mode,
        "pairs_file": str(pairs_path),
        "backup_root": str(backup_root),
        "deleted_count": len(completed),
        "surviving_delete_targets": surviving,
        "kept_missing": kept_missing,
        "undo_log": str(undo_path),
        "recovery_journal": str(journal_path),
        "verified_backup_manifest": {
            "backup_id": manifest["backup_id"],
            "status": manifest["status"],
        },
    }
    engine.finish(result)
    result_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({**result, "result_path": str(result_path)}, indent=2))


def undo(undo_path: Path, label: str, heartbeat: lock_guard.LockHeartbeat) -> None:
    """Restore deleted files.

    An undo is a mutation, so it runs through the same engine as the delete:
    ownership re-verified before every restore, and a write-ahead intent
    record on disk before each file is written back.

    The engine's `source` here is the BACKUP copy, not the deleted path -- the
    deleted file does not exist any more, so it cannot be the thing whose
    preconditions get checked. Pointing source at the backup makes the
    precondition "the backup we are about to restore from is present and
    hashes as recorded", which is exactly what must hold before writing.
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
        deleted = Path(record["deleted"])
        backup = Path(record["backup"])
        with engine.item(
            position,
            kind=mutation_engine.DELETE,
            source=backup,
            destination=deleted,
            expected_sha256=record["sha256"],
            require_backup=False,
            extra_intent={"undoing_index": record.get("index"),
                          "restoring_to": str(deleted),
                          "kept": record.get("kept")},
        ) as txn:
            if deleted.exists():
                raise RuntimeError(f"restore target already exists: {deleted}")
            deleted.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, deleted)
            txn.mark_mutated()
            if sha256_file(deleted) != record["sha256"]:
                raise RuntimeError(f"restore verification failed: {deleted}")
            txn.record(restored_to=str(deleted), sha256=record["sha256"])
            restored += 1

    result = {"mode": f"{label}-undo", "restored": restored,
              "recovery_journal": str(journal_path)}
    engine.finish(result)
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", required=True, type=Path, help="JSON list of {sha256, delete, keep} pairs")
    parser.add_argument("--backup-root", type=Path,
                        help="Backup dir the per-pair copies live in (required with --apply)")
    parser.add_argument("--source-root", type=Path,
                        help="Library root the backup manifest covers (required with --apply)")
    parser.add_argument("--backup-manifest", type=Path,
                        help="PASS manifest from backup_manifest.py verify (required with --apply)")
    parser.add_argument("--require-serato-backup", action="store_true",
                        help="also require the manifest to carry PASS Serato backup evidence")
    parser.add_argument("--out-dir", type=Path, help="Where undo/result files land (defaults to pairs file's dir)")
    parser.add_argument("--label", default="dedup-delete", help="Prefix for undo/result files")
    parser.add_argument("--mode", choices=MODES, default="exact",
                        help="exact (identical SHA-256, default) or audio-match (owner rule 2026-10-15)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--undo", type=Path, help="Path to an undo .jsonl log to reverse")
    parser.add_argument("--lock-folder", help="Folder locked via folder_lock.py (required with --apply/--undo)")
    parser.add_argument("--session", help="Session id that holds the lock (required with --apply/--undo)")
    args = parser.parse_args()

    out_dir = args.out_dir or args.pairs.resolve().parent

    if args.undo:
        if not args.lock_folder or not args.session:
            raise SystemExit("--lock-folder and --session are required with --undo")
        lock = lock_guard.require_lock_or_exit(args.lock_folder, args.session)
        heartbeat = lock_guard.LockHeartbeat(args.lock_folder, args.session)
        heartbeat.pin_run_id(lock)
        undo(args.undo.resolve(), args.label, heartbeat)
    elif args.apply:
        if not args.backup_root or not args.source_root or not args.backup_manifest:
            raise SystemExit(
                "--backup-root, --source-root and --backup-manifest are required with --apply.\n"
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
                source_root=args.source_root.resolve(),
                serato_required=args.require_serato_backup,
            )
        except backup_manifest.BackupGateError as exc:
            raise SystemExit(f"REFUSING TO MUTATE: {exc}") from exc
        apply(args.pairs, manifest, args.source_root.resolve(), args.backup_root.resolve(),
              out_dir, args.label, heartbeat, mode=args.mode)
    else:
        if args.mode == "audio-match":
            # Show the human every pair's evidence before they approve anything.
            report = audio_match_report(args.pairs, audio_fingerprint.probe)
            print(json.dumps({"mode": "dry-run", "dedupe_mode": args.mode, "pairs": len(report),
                              "removable": sum(r["removable"] for r in report), "changes_made": 0,
                              "evidence": report}, indent=2, ensure_ascii=False))
        else:
            pairs = json.loads(args.pairs.read_text(encoding="utf-8"))
            print(json.dumps({"mode": "dry-run", "dedupe_mode": args.mode, "pairs": len(pairs),
                              "validated": False, "changes_made": 0}, indent=2))


if __name__ == "__main__":
    main()
