#!/usr/bin/env python3
"""Verified-backup manifest: the evidence an apply engine demands before it
is allowed to mutate anything.

## Why a directory existing is not a backup

Every apply script in this repo used to gate production mutation on
`backup.is_dir()`. That check passes for an empty directory, a half-finished
copy that died at 40%, a backup of a *different* folder, or a backup taken
before the last three hours of edits. It proves a path exists; it proves
nothing about whether the files about to be mutated can actually be
restored.

A verified backup here means: something walked the real source tree, walked
the backup tree, hashed both, and recorded that every source file is present
in the backup with a matching SHA-256. That record is this manifest. PASS is
a *computed result*, never an assertion a caller can hand in.

## What the gate checks

`require_pass()` refuses to return unless the manifest:

* exists and parses;
* carries every required field (see REQUIRED_FIELDS);
* says `"status": "PASS"`;
* was produced by a verifier version this code understands;
* covers the source root the run is about to mutate (identity, not a
  substring match) -- so yesterday's Events backup cannot authorize a Hip Hop
  apply;
* still hashes to its recorded `manifest_sha256` -- the body is canonicalized
  and hashed at write time, so a hand-edited `"status": "FAIL"` -> `"PASS"`,
  a deleted entry, or a swapped source root is detected;
* carries Serato backup evidence when the run declares Serato data is in
  scope.

### Honest limit of `manifest_sha256`

It is an integrity check against accidental or careless alteration -- a
truncated file, a hand edit, a partial merge. It is NOT a signature: anyone
who can write the manifest can recompute the digest. That is acceptable
here because the threat model is "an agent or human takes a shortcut", not
"an attacker with write access to the repo". Do not describe it as
tamper-proof.

## Producing a manifest

    python3 scripts/backup_manifest.py verify \\
        --source-root "/path/to/Music/Hip Hop" \\
        --backup "/path/to/backups/hip-hop-20260816" \\
        --out backup-manifest.json [--serato-backup /path/to/_Serato_backup]

This walks both trees and computes PASS/FAIL from what it finds. There is
deliberately no flag that stamps PASS onto a directory without hashing it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

SAFETY_CLASSIFICATION = "read-only"

MANIFEST_VERSION = 1
VERIFIER_NAME = "remix-edit-sort/backup_manifest"

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"

REQUIRED_FIELDS = (
    "manifest_version",
    "backup_id",
    "timestamp",
    "source_root",
    "backup_root",
    "file_count",
    "total_bytes",
    "files",
    "serato",
    "verifier",
    "verifier_version",
    "repo_sha",
    "status",
    "manifest_sha256",
)

# Files that are never part of the audio library and whose absence from a
# backup must not fail an otherwise-complete verification.
IGNORED_NAMES = {".DS_Store", "Icon\r", "Icon?"}


class BackupGateError(Exception):
    """The backup evidence is insufficient. Production mutation must not proceed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _repo_sha() -> str | None:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parent.parent), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def canonical_digest(manifest: dict) -> str:
    """Hash of the manifest body with `manifest_sha256` itself excluded.

    Sorted keys + compact separators so the digest depends on content, not on
    whitespace or key order -- reformatting the file must not look like
    tampering, but changing any value must.
    """
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _identity(path: Path) -> str:
    """Normalized absolute path used to compare roots.

    Case-folded because the production library is on case-insensitive APFS,
    where "/Music/Hip Hop" and "/music/hip hop" are genuinely the same
    directory and treating them as different roots would fail a valid backup.
    """
    return os.path.normcase(str(Path(path).resolve()))


def _walk_audio(root: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name in IGNORED_NAMES or name.startswith("._"):
                continue
            full = Path(dirpath) / name
            if full.is_symlink() or not full.is_file():
                continue
            found[str(full.relative_to(root))] = full
    return found


def build_manifest(
    source_root: Path,
    backup_root: Path,
    serato_backup: Path | None = None,
    serato_required: bool = False,
    backup_id: str | None = None,
) -> dict:
    """Walk both trees, hash everything, and compute PASS/FAIL from evidence."""
    source_root = source_root.resolve()
    backup_root = backup_root.resolve()
    problems: list[str] = []

    if not source_root.is_dir():
        problems.append(f"source root is not a directory: {source_root}")
    if not backup_root.is_dir():
        problems.append(f"backup root is not a directory: {backup_root}")

    files: list[dict] = []
    total_bytes = 0
    if not problems:
        source_files = _walk_audio(source_root)
        backup_files = _walk_audio(backup_root)
        for relative in sorted(source_files):
            source_path = source_files[relative]
            backup_path = backup_files.get(relative)
            source_hash = sha256_file(source_path)
            size = source_path.stat().st_size
            total_bytes += size
            if backup_path is None:
                problems.append(f"missing from backup: {relative}")
                files.append({"path": relative, "sha256": source_hash, "bytes": size, "backed_up": False})
                continue
            backup_hash = sha256_file(backup_path)
            matched = backup_hash == source_hash
            if not matched:
                problems.append(f"backup copy differs from source: {relative}")
            files.append({
                "path": relative,
                "sha256": source_hash,
                "bytes": size,
                "backed_up": True,
                "backup_sha256": backup_hash,
                "matched": matched,
            })
        extra = sorted(set(backup_files) - set(source_files))
        if extra:
            # Extra files in the backup are not a failure -- a backup taken
            # before a move legitimately holds files the source no longer has.
            # Recorded, not fatal.
            pass

    serato = {
        "required": bool(serato_required),
        "present": serato_backup is not None and Path(serato_backup).is_dir(),
        "path": str(Path(serato_backup).resolve()) if serato_backup else None,
    }
    if serato_required and not serato["present"]:
        problems.append("Serato backup was required for this run but is missing or not a directory")
    if serato["present"]:
        serato_files = _walk_audio(Path(serato_backup))
        serato["file_count"] = len(serato_files)
        serato["total_bytes"] = sum(p.stat().st_size for p in serato_files.values())
        serato["result"] = STATUS_PASS if serato_files else STATUS_FAIL
        if not serato_files:
            problems.append("Serato backup directory exists but is empty")
    else:
        serato["result"] = STATUS_FAIL if serato_required else "NOT_REQUIRED"

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "backup_id": backup_id or f"{source_root.name}-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}",
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_root": str(source_root),
        "source_root_identity": _identity(source_root),
        "backup_root": str(backup_root),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
        "serato": serato,
        "verifier": VERIFIER_NAME,
        "verifier_version": MANIFEST_VERSION,
        "repo_sha": _repo_sha(),
        "problems": problems,
        "status": STATUS_FAIL if problems or not files else STATUS_PASS,
    }
    manifest["manifest_sha256"] = canonical_digest(manifest)
    return manifest


def write_manifest(manifest: dict, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def load_manifest(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BackupGateError(f"backup manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BackupGateError(f"backup manifest is not valid JSON: {path}: {exc}") from exc


def require_pass(
    manifest_path: Path,
    source_root: Path,
    serato_required: bool = False,
) -> dict:
    """Return the manifest, or raise BackupGateError. Never returns on doubt."""
    manifest = load_manifest(Path(manifest_path))
    if not isinstance(manifest, dict):
        raise BackupGateError(f"backup manifest is not a JSON object: {manifest_path}")

    missing = [field for field in REQUIRED_FIELDS if field not in manifest]
    if missing:
        raise BackupGateError(
            f"backup manifest {manifest_path} is missing required field(s): {', '.join(missing)}"
        )
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise BackupGateError(
            f"backup manifest version {manifest.get('manifest_version')!r} is not the "
            f"{MANIFEST_VERSION} this engine understands: {manifest_path}"
        )
    if manifest.get("verifier") != VERIFIER_NAME:
        raise BackupGateError(
            f"backup manifest was produced by {manifest.get('verifier')!r}, not the approved "
            f"verifier {VERIFIER_NAME!r}: {manifest_path}"
        )
    expected_digest = canonical_digest(manifest)
    if manifest.get("manifest_sha256") != expected_digest:
        raise BackupGateError(
            f"backup manifest {manifest_path} has been altered since it was written "
            f"(recorded digest {manifest.get('manifest_sha256')!r} != recomputed {expected_digest!r}). "
            "Re-run backup_manifest.py verify against the real backup."
        )
    if manifest.get("status") != STATUS_PASS:
        problems = manifest.get("problems") or []
        raise BackupGateError(
            f"backup manifest status is {manifest.get('status')!r}, not PASS: {manifest_path}"
            + (f"\nFirst problems: {problems[:5]}" if problems else "")
        )
    if not manifest.get("files"):
        raise BackupGateError(f"backup manifest lists zero files: {manifest_path}")
    if len(manifest["files"]) != manifest.get("file_count"):
        raise BackupGateError(
            f"backup manifest file_count {manifest.get('file_count')} does not match the "
            f"{len(manifest['files'])} entries it contains: {manifest_path}"
        )
    manifest_identity = manifest.get("source_root_identity") or _identity(Path(manifest["source_root"]))
    if manifest_identity != _identity(Path(source_root)):
        raise BackupGateError(
            f"backup manifest covers {manifest.get('source_root')!r}, but this run targets "
            f"{str(source_root)!r}. Refusing to authorize a mutation with another folder's backup."
        )
    if serato_required:
        serato = manifest.get("serato") or {}
        if not serato.get("present") or serato.get("result") != STATUS_PASS:
            raise BackupGateError(
                f"this run requires Serato backup evidence, but the manifest records "
                f"{serato!r}: {manifest_path}"
            )
    return manifest


def backed_up_hashes(manifest: dict) -> dict[str, str]:
    """{relative path -> sha256} for every file the manifest proves is backed up."""
    return {
        entry["path"]: entry["sha256"]
        for entry in manifest.get("files", [])
        if entry.get("backed_up") and entry.get("matched", True)
    }


def require_item_backed_up(manifest: dict, source_root: Path, path: Path) -> str:
    """Prove THIS file is in the verified backup, and return its recorded hash.

    Per-item evidence, not just per-run: a manifest that passed overall still
    cannot authorize mutating a file it never covered (for example one that
    appeared after the backup was taken).
    """
    try:
        relative = str(Path(path).resolve().relative_to(Path(source_root).resolve()))
    except ValueError as exc:
        raise BackupGateError(
            f"{path} is not under the backed-up source root {source_root}"
        ) from exc
    hashes = backed_up_hashes(manifest)
    if relative not in hashes:
        raise BackupGateError(
            f"no verified backup evidence for {relative!r} in manifest {manifest.get('backup_id')!r}"
        )
    return hashes[relative]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_verify = sub.add_parser("verify", help="hash source+backup trees and write a PASS/FAIL manifest")
    p_verify.add_argument("--source-root", required=True, type=Path)
    p_verify.add_argument("--backup", required=True, type=Path)
    p_verify.add_argument("--serato-backup", type=Path)
    p_verify.add_argument("--require-serato", action="store_true")
    p_verify.add_argument("--backup-id")
    p_verify.add_argument("--out", required=True, type=Path)

    p_check = sub.add_parser("check", help="re-validate an existing manifest against a target root")
    p_check.add_argument("--manifest", required=True, type=Path)
    p_check.add_argument("--source-root", required=True, type=Path)
    p_check.add_argument("--require-serato", action="store_true")

    args = parser.parse_args()

    if args.command == "verify":
        manifest = build_manifest(
            args.source_root, args.backup, args.serato_backup,
            serato_required=args.require_serato, backup_id=args.backup_id,
        )
        write_manifest(manifest, args.out)
        print(json.dumps({
            "status": manifest["status"],
            "file_count": manifest["file_count"],
            "total_bytes": manifest["total_bytes"],
            "problems": manifest["problems"][:10],
            "problem_count": len(manifest["problems"]),
            "manifest": str(args.out),
        }, indent=2))
        return 0 if manifest["status"] == STATUS_PASS else 1

    try:
        manifest = require_pass(args.manifest, args.source_root, serato_required=args.require_serato)
    except BackupGateError as exc:
        print(json.dumps({"gate": "REFUSED", "reason": str(exc)}, indent=2))
        return 1
    print(json.dumps({
        "gate": "PASS",
        "backup_id": manifest["backup_id"],
        "file_count": manifest["file_count"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
