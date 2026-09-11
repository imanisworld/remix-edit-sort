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
who can write the file can recompute the digest. That is acceptable
here because the threat model is "an agent or human takes a shortcut", not
"an attacker with write access to the repo". Do not describe it as
tamper-proof.

## Producing a manifest

    python3 scripts/backup_manifest.py verify \
        --source-root "/path/to/Music/Hip Hop" \
        --backup "/path/to/backups/hip-hop-20260816" \
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
import unicodedata
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


def _comparison_key(value: str) -> str:
    """Canonical filesystem-comparison key used throughout the project."""
    return unicodedata.normalize("NFC", value).casefold()


def _identity(path: Path) -> str:
    """Normalized absolute path used to compare roots.

    Two normalizations, both deliberate:

    * `casefold()`, NOT `os.path.normcase()`. normcase is the identity
      function on every POSIX platform including macOS -- it only lowercases
      on Windows -- so using it here would silently do nothing on the machine
      that actually matters. The production library lives on case-insensitive
      APFS, where "/Music/Hip Hop" and "/music/hip hop" are the same
      directory, and treating them as different roots would reject a
      perfectly valid backup.
    * Unicode NFC, because macOS has historically stored filenames decomposed.
      A manifest written with "Beyoncé" must still match a run targeting
      "Beyoncé" -- same directory, different bytes.
    """
    resolved = str(Path(path).resolve())
    return _comparison_key(resolved)


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


def _canonical_file_index(
    files: dict[str, Path],
    tree_name: str,
) -> tuple[dict[str, tuple[str, Path]], list[str]]:
    """Index raw relative paths by the project's collision semantics.

    A backup filesystem may serialize the same filename with different case or
    Unicode normalization.  Matching through this index handles that safely,
    while retaining both raw names so an ambiguous many-to-one mapping is a
    hard failure rather than a silent overwrite.
    """
    index: dict[str, tuple[str, Path]] = {}
    problems: list[str] = []
    for relative in sorted(files):
        key = _comparison_key(relative)
        previous = index.get(key)
        if previous is not None:
            problems.append(
                f"canonical path collision in {tree_name}: "
                f"{previous[0]!r} and {relative!r}"
            )
            continue
        index[key] = (relative, files[relative])
    return index, problems


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
        source_files_raw = _walk_audio(source_root)
        backup_files_raw = _walk_audio(backup_root)
        source_files, source_collisions = _canonical_file_index(
            source_files_raw, "source tree"
        )
        backup_files, backup_collisions = _canonical_file_index(
            backup_files_raw, "backup tree"
        )
        problems.extend(source_collisions)
        problems.extend(backup_collisions)
        for key in sorted(source_files):
            relative, source_path = source_files[key]
            backup_entry = backup_files.get(key)
            backup_path = backup_entry[1] if backup_entry is not None else None
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
        serato_root = Path(serato_backup)
        serato_files = _walk_audio(serato_root)
        serato["file_count"] = len(serato_files)
        serato["total_bytes"] = sum(p.stat().st_size for p in serato_files.values())
        # A non-empty directory is not evidence of a Serato backup. Prove the
        # actual Serato inventory: a non-empty 'database V2' hashed at record
        # time, plus every crate/Smart Crate hashed so a restore has a bound
        # byte-for-byte target set.
        database = serato_root / "database V2"
        crates = sorted(serato_root.glob("Subcrates/*.crate"))
        smart_crates = sorted(serato_root.glob("SmartCrates/*.scrate"))
        serato["database_v2_bytes"] = database.stat().st_size if database.is_file() else 0
        serato["database_v2_sha256"] = (
            sha256_file(database) if serato["database_v2_bytes"] else None
        )
        serato["crate_count"] = len(crates)
        serato["smart_crate_count"] = len(smart_crates)
        serato["crate_sha256"] = {p.name: sha256_file(p) for p in crates}
        serato["smart_crate_sha256"] = {p.name: sha256_file(p) for p in smart_crates}
        if not serato_files:
            serato["result"] = STATUS_FAIL
            problems.append("Serato backup directory exists but is empty")
        elif not serato["database_v2_sha256"]:
            serato["result"] = STATUS_FAIL
            problems.append(
                "Serato backup directory has no non-empty 'database V2'; a directory "
                "without one is not a restorable Serato backup"
            )
        else:
            serato["result"] = STATUS_PASS
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


def build_scoped_manifest(
    source_root: Path,
    backup_root: Path,
    relative_paths: list[str],
    *,
    backup_id: str | None = None,
) -> dict:
    """Hash an explicit subset of one source root and compute PASS/FAIL.

    This mode exists for tag-only plans where restoring the files that can be
    written is sufficient and copying a multi-gigabyte parent tree is wasteful.
    It does not authorize any file outside ``relative_paths``; the mutation
    engine still requires per-item evidence and re-hashes the concrete backup
    copy immediately before every write.
    """
    source_root = Path(source_root).resolve()
    backup_root = Path(backup_root).resolve()
    problems: list[str] = []
    files: list[dict] = []
    total_bytes = 0

    if not source_root.is_dir():
        problems.append(f"source root is not a directory: {source_root}")
    if not backup_root.is_dir():
        problems.append(f"backup root is not a directory: {backup_root}")
    if not relative_paths:
        problems.append("scoped backup contains no requested files")

    seen: dict[str, str] = {}
    if not problems:
        for raw_relative in relative_paths:
            if not isinstance(raw_relative, str) or not raw_relative:
                problems.append(f"invalid scoped relative path: {raw_relative!r}")
                continue
            relative = Path(raw_relative)
            if relative.is_absolute() or ".." in relative.parts:
                problems.append(f"unsafe scoped relative path: {raw_relative!r}")
                continue
            key = _comparison_key(str(relative))
            if key in seen:
                problems.append(
                    f"canonical path collision in scoped manifest: {seen[key]!r} and {raw_relative!r}"
                )
                continue
            seen[key] = raw_relative

            source_path = source_root / relative
            backup_path = backup_root / relative
            if source_path.is_symlink() or not source_path.is_file():
                problems.append(f"scoped source is missing, not a file, or a symlink: {raw_relative}")
                continue
            if backup_path.is_symlink() or not backup_path.is_file():
                source_hash = sha256_file(source_path)
                size = source_path.stat().st_size
                total_bytes += size
                problems.append(f"missing from scoped backup: {raw_relative}")
                files.append({
                    "path": str(relative),
                    "sha256": source_hash,
                    "bytes": size,
                    "backed_up": False,
                })
                continue

            source_hash = sha256_file(source_path)
            backup_hash = sha256_file(backup_path)
            size = source_path.stat().st_size
            total_bytes += size
            matched = source_hash == backup_hash
            if not matched:
                problems.append(f"scoped backup copy differs from source: {raw_relative}")
            files.append({
                "path": str(relative),
                "sha256": source_hash,
                "bytes": size,
                "backed_up": True,
                "backup_sha256": backup_hash,
                "matched": matched,
            })

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "backup_id": backup_id or f"{source_root.name}-scoped-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}",
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_root": str(source_root),
        "source_root_identity": _identity(source_root),
        "backup_root": str(backup_root),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
        "serato": {
            "required": False,
            "present": False,
            "path": None,
            "result": "NOT_REQUIRED",
        },
        "verifier": VERIFIER_NAME,
        "verifier_version": MANIFEST_VERSION,
        "repo_sha": _repo_sha(),
        "scope_mode": "explicit-files",
        "problems": problems,
        "status": STATUS_FAIL if problems or not files else STATUS_PASS,
    }
    manifest["manifest_sha256"] = canonical_digest(manifest)
    return manifest



def build_explicit_paths_manifest(
    source_root: Path,
    backup_root: Path,
    backup_records: list[dict],
    *,
    backup_id: str | None = None,
) -> dict:
    # Verify an explicit source-relative -> concrete backup-path mapping.
    #
    # Unlike build_scoped_manifest(), the verified backup for an item may be
    # anywhere on the backup volume. This is what permits SHA-256 dedupe to
    # reuse an older T5 copy without manufacturing another duplicate solely to
    # satisfy the historical backup_root/<relative> layout convention.
    source_root = Path(source_root).resolve()
    backup_root = Path(backup_root).resolve()
    problems: list[str] = []
    files: list[dict] = []
    total_bytes = 0

    if not source_root.is_dir():
        problems.append(f"source root is not a directory: {source_root}")
    if not backup_root.is_dir():
        problems.append(f"backup root is not a directory: {backup_root}")
    if not isinstance(backup_records, list) or not backup_records:
        problems.append("explicit backup manifest contains no requested files")

    seen: dict[str, str] = {}
    if not problems:
        for record in backup_records:
            if not isinstance(record, dict):
                problems.append(f"invalid explicit backup record: {record!r}")
                continue
            raw_relative = record.get("path")
            if not isinstance(raw_relative, str) or not raw_relative:
                problems.append(f"invalid explicit relative path: {raw_relative!r}")
                continue
            relative = Path(raw_relative)
            if relative.is_absolute() or ".." in relative.parts:
                problems.append(f"unsafe explicit relative path: {raw_relative!r}")
                continue
            key = _comparison_key(str(relative))
            if key in seen:
                problems.append(
                    f"canonical path collision in explicit manifest: "
                    f"{seen[key]!r} and {raw_relative!r}"
                )
                continue
            seen[key] = raw_relative

            source_path = source_root / relative
            raw_backup_path = record.get("backup_path")
            if not isinstance(raw_backup_path, str) or not raw_backup_path:
                problems.append(f"missing explicit backup_path for: {raw_relative}")
                continue
            backup_path = Path(raw_backup_path)
            if not backup_path.is_absolute():
                problems.append(
                    f"explicit backup_path must be absolute for {raw_relative}: {raw_backup_path!r}"
                )
                continue

            if source_path.is_symlink() or not source_path.is_file():
                problems.append(
                    f"explicit source is missing, not a file, or a symlink: {raw_relative}"
                )
                continue
            source_hash = sha256_file(source_path)
            size = source_path.stat().st_size
            total_bytes += size

            if backup_path.is_symlink() or not backup_path.is_file():
                problems.append(
                    f"explicit backup is missing, not a regular file, or a symlink: {backup_path}"
                )
                files.append({
                    "path": str(relative),
                    "sha256": source_hash,
                    "bytes": size,
                    "backed_up": False,
                    "backup_path": str(backup_path),
                    "backup_mode": record.get("backup_mode"),
                })
                continue

            backup_path = backup_path.resolve()
            backup_hash = sha256_file(backup_path)
            matched = source_hash == backup_hash
            if not matched:
                problems.append(
                    f"explicit backup copy differs from source: {raw_relative} -> {backup_path}"
                )
            files.append({
                "path": str(relative),
                "sha256": source_hash,
                "bytes": size,
                "backed_up": True,
                "backup_sha256": backup_hash,
                "matched": matched,
                "backup_path": str(backup_path),
                "backup_mode": record.get("backup_mode"),
            })

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "backup_id": backup_id or f"{source_root.name}-explicit-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}",
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_root": str(source_root),
        "source_root_identity": _identity(source_root),
        "backup_root": str(backup_root),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
        "serato": {
            "required": False,
            "present": False,
            "path": None,
            "result": "NOT_REQUIRED",
        },
        "verifier": VERIFIER_NAME,
        "verifier_version": MANIFEST_VERSION,
        "repo_sha": _repo_sha(),
        "scope_mode": "explicit-backup-paths",
        "problems": problems,
        "status": STATUS_FAIL if problems or not files else STATUS_PASS,
    }
    manifest["manifest_sha256"] = canonical_digest(manifest)
    return manifest


def backup_path_for_item(manifest: dict, source_root: Path, path: Path) -> Path:
    # Resolve the concrete verified restore path for one manifest item.
    # Legacy manifests retain their original backup_root/<source-relative>
    # behavior. Explicit-path manifests return the exact path that was hashed
    # when their PASS evidence was built.
    try:
        relative = str(Path(path).resolve().relative_to(Path(source_root).resolve()))
    except ValueError as exc:
        raise BackupGateError(
            f"{path} is not under the backed-up source root {source_root}"
        ) from exc

    key = _relative_key(relative)
    matches = [
        entry for entry in manifest.get("files", [])
        if isinstance(entry, dict)
        and isinstance(entry.get("path"), str)
        and _relative_key(entry["path"]) == key
    ]
    if len(matches) != 1:
        if not matches:
            raise BackupGateError(
                f"no verified backup evidence for {relative!r} in manifest "
                f"{manifest.get('backup_id')!r}"
            )
        raise BackupGateError(
            f"backup manifest contains ambiguous evidence for {relative!r}"
        )

    entry = matches[0]
    if not entry.get("backed_up") or not entry.get("matched", True):
        raise BackupGateError(
            f"backup manifest does not prove a matching copy for {relative!r}"
        )

    if manifest.get("scope_mode") == "explicit-backup-paths" or "backup_path" in entry:
        raw_backup = entry.get("backup_path")
        if not isinstance(raw_backup, str) or not raw_backup:
            raise BackupGateError(
                f"explicit backup manifest has no concrete path for {relative!r}"
            )
        candidate = Path(raw_backup)
        if not candidate.is_absolute():
            raise BackupGateError(
                f"explicit backup path is not absolute for {relative!r}: {raw_backup!r}"
            )
        if candidate.is_symlink():
            raise BackupGateError(
                f"explicit backup path became a symlink; refusing it: {candidate}"
            )
        return candidate.resolve()

    # Exact legacy behavior: concrete backup is source-relative beneath the
    # manifest's backup_root.
    return (
        Path(manifest["backup_root"])
        / Path(path).resolve().relative_to(Path(source_root).resolve())
    ).resolve()

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
    # A canonical collision would make per-item evidence ambiguous even if a
    # manifest body had otherwise been made internally consistent.
    backed_up_hashes(manifest)
    return manifest


def _relative_key(relative: str) -> str:
    """Lookup key for a per-file manifest entry.

    Uses the same NFC + casefold collision semantics as production-path
    validation. Distinct raw entries that collapse to this key are refused by
    `backed_up_hashes()` rather than silently merged.
    """
    return _comparison_key(relative)


def backed_up_hashes(manifest: dict) -> dict[str, str]:
    """{relative path -> sha256} for every file the manifest proves is backed up."""
    hashes: dict[str, str] = {}
    raw_paths: dict[str, str] = {}
    for entry in manifest.get("files", []):
        if not entry.get("backed_up") or not entry.get("matched", True):
            continue
        raw_path = entry["path"]
        key = _relative_key(raw_path)
        if key in hashes:
            raise BackupGateError(
                "backup manifest contains a canonical path collision: "
                f"{raw_paths[key]!r} and {raw_path!r}"
            )
        hashes[key] = entry["sha256"]
        raw_paths[key] = raw_path
    return hashes


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
    relative = _relative_key(relative)
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
