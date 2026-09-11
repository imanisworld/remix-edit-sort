#!/usr/bin/env python3
"""One-shot repository patcher for the T5 dedupe integration branch."""
from pathlib import Path


EXPLICIT_HELPERS = r'''

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
'''


HANDOFF_REPLACEMENT = '''## T5 SHA-256 backup deduplication

Repository support is implemented and tested. `scripts/t5_backup_dedupe.py` can build/refresh a full-file SHA-256 index, reuse an existing identical T5 backup only after re-hashing the concrete file, copy only unmatched content into the lane backup, and emit a verified manifest with an explicit concrete backup path for every source item. The mutation engine accepts that explicit path while remaining backward-compatible with legacy `backup_root/<relative>` manifests, and still re-hashes the selected backup immediately before any mutation.

The actual chain shell workflow is Mac-local/untracked; there is no chain shell script in this repository to patch. It must invoke the helper locally. The initial real T5 index and a real matched/unmatched dry proof are therefore still **Mac-local execution prerequisites**:

```bash
python3 scripts/t5_backup_dedupe.py index \\
  --root /Volumes/T5 \\
  --out ~/Documents/DJ-library-project-20260825/t5-backup-sha256-index.json

python3 scripts/t5_backup_dedupe.py prepare \\
  --index ~/Documents/DJ-library-project-20260825/t5-backup-sha256-index.json \\
  --source-root "<lane source root>" \\
  --lane-backup-root "/Volumes/T5/<lane backup root>" \\
  --manifest-out "<lane backup manifest.json>" \\
  --report-out "<lane backup report.json>"
```

For a scoped plan, pass `--paths-json <json>` containing either a non-empty JSON list of source-relative paths or `{"paths": [...]}`. Matching is SHA-256 only; filenames and durations never authorize reuse. A missing or changed indexed path, a symlink, a hash mismatch, an index-integrity failure, or source drift fails closed.

No real music files, Serato database, crates, or local library metadata were changed while implementing or testing this repository support.

'''


def patch_backup_manifest() -> None:
    path = Path("scripts/backup_manifest.py")
    source = path.read_text(encoding="utf-8")
    marker = "\ndef write_manifest(manifest: dict, out_path: Path) -> None:\n"
    if marker not in source:
        raise SystemExit("backup_manifest insertion point not found")
    if "def build_explicit_paths_manifest(" in source:
        raise SystemExit("explicit manifest helper already exists unexpectedly")
    path.write_text(source.replace(marker, EXPLICIT_HELPERS + marker, 1), encoding="utf-8")


def patch_mutation_engine() -> None:
    path = Path("scripts/mutation_engine.py")
    source = path.read_text(encoding="utf-8")
    old = '''                manifest_backup = (
                    Path(self.manifest["backup_root"])
                    / source.resolve().relative_to(self.source_root.resolve())
                )
'''
    new = '''                manifest_backup = backup_manifest.backup_path_for_item(
                    self.manifest, self.source_root, source
                )
'''
    if source.count(old) != 1:
        raise SystemExit(
            f"expected one mutation-engine backup resolver, found {source.count(old)}"
        )
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def patch_mutation_surface() -> None:
    path = Path("scripts/mutation_surface.py")
    source = path.read_text(encoding="utf-8")
    old = '''    "scripts/metadata_batch.py":
        "atomically writes only repo handoff/T5 evidence and copies full scopes only to confined T5 backups",
}'''
    new = '''    "scripts/metadata_batch.py":
        "atomically writes only repo handoff/T5 evidence and copies full scopes only to confined T5 backups",
    "scripts/t5_backup_dedupe.py":
        "copies only source bytes into a caller-confined T5 lane backup root and atomically replaces only its external SHA-256 index; never mutates library source media",
}'''
    if source.count(old) != 1:
        raise SystemExit("mutation_surface infrastructure allowlist insertion point not found")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def update_handoff() -> None:
    path = Path("handoff/CURRENT-STATUS.md")
    source = path.read_text(encoding="utf-8")
    start = source.index("## Remaining prerequisite\n")
    end = source.index("## Production safety boundary\n", start)
    path.write_text(
        source[:start] + HANDOFF_REPLACEMENT + source[end:],
        encoding="utf-8",
    )


if __name__ == "__main__":
    patch_backup_manifest()
    patch_mutation_engine()
    patch_mutation_surface()
