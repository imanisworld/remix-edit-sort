#!/usr/bin/env python3
"""Build and maintain a SHA-256 index of T5 backups, then reuse verified content.

This is backup infrastructure only. It never edits, moves, or deletes source
library media. ``prepare`` resolves every source item to a concrete verified
backup path: an existing T5 file with the same SHA-256, or a newly copied file
under the caller-provided lane backup root.

The index is refreshed before every prepare. Unchanged files reuse their
recorded digest using size + mtime_ns as the change detector; new/changed files
are re-hashed. Any file selected for backup reuse is always re-hashed in full
immediately before it is trusted.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "infrastructure"

import argparse
import hashlib
import json
import os
import shutil
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup_manifest  # noqa: E402

INDEX_VERSION = 1
ALGORITHM = "sha256"
CHUNK_SIZE = 4 * 1024 * 1024


class BackupDedupeError(RuntimeError):
    """Index or backup evidence is insufficient; do not continue the lane."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def _path_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _safe_relative(raw: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise BackupDedupeError(f"invalid relative path: {raw!r}")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts or str(relative) in {"", "."}:
        raise BackupDedupeError(f"unsafe relative path: {raw!r}")
    return relative


def _under(root: Path, path: Path) -> Path:
    root = Path(root).resolve()
    resolved = Path(path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise BackupDedupeError(f"path escapes required root {root}: {path}") from exc
    return resolved


def _canonical_index_digest(payload: dict) -> str:
    body = {key: value for key, value in payload.items() if key != "index_sha256"}
    encoded = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _scan_tree(root: Path) -> list[dict]:
    root = Path(root).resolve()
    if not root.is_dir():
        raise BackupDedupeError(f"T5 root is not a directory: {root}")

    rows: list[dict] = []
    seen: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(dirpath)
        dirnames[:] = sorted(
            name for name in dirnames
            if not (base / name).is_symlink()
        )
        for name in sorted(filenames):
            if name in backup_manifest.IGNORED_NAMES or name.startswith("._"):
                continue
            path = base / name
            if path.is_symlink() or not path.is_file():
                continue
            relative = str(path.relative_to(root))
            key = _path_key(relative)
            previous = seen.get(key)
            if previous is not None and previous != relative:
                raise BackupDedupeError(
                    "canonical path collision on T5: "
                    f"{previous!r} and {relative!r}"
                )
            seen[key] = relative
            stat = path.stat()
            rows.append(
                {
                    "path": relative,
                    "bytes": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "_absolute": path,
                }
            )
    rows.sort(key=lambda row: (_path_key(row["path"]), row["path"]))
    return rows


def _rebuild_lookup(index: dict) -> None:
    by_sha: dict[str, list[str]] = {}
    for entry in index["files"]:
        by_sha.setdefault(entry["sha256"], []).append(entry["path"])
    for digest in by_sha:
        by_sha[digest].sort(key=lambda value: (_path_key(value), value))
    index["by_sha256"] = dict(sorted(by_sha.items()))
    index["file_count"] = len(index["files"])
    index["total_bytes"] = sum(int(entry["bytes"]) for entry in index["files"])


def _finalize_index(index: dict) -> dict:
    index["index_sha256"] = _canonical_index_digest(index)
    return index


def build_index(root: Path) -> dict:
    root = Path(root).resolve()
    rows = _scan_tree(root)
    files = []
    for row in rows:
        path = row.pop("_absolute")
        row["sha256"] = sha256_file(path)
        files.append(row)
    index = {
        "schema_version": INDEX_VERSION,
        "algorithm": ALGORITHM,
        "root": str(root),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": files,
    }
    _rebuild_lookup(index)
    return _finalize_index(index)


def validate_index(index: dict) -> dict:
    if not isinstance(index, dict):
        raise BackupDedupeError("T5 index is not a JSON object")
    if index.get("schema_version") != INDEX_VERSION:
        raise BackupDedupeError(
            f"unsupported T5 index version: {index.get('schema_version')!r}"
        )
    if index.get("algorithm") != ALGORITHM:
        raise BackupDedupeError(
            f"unsupported T5 index algorithm: {index.get('algorithm')!r}"
        )
    if not isinstance(index.get("root"), str) or not Path(index["root"]).is_absolute():
        raise BackupDedupeError("T5 index root must be an absolute path")
    recorded = index.get("index_sha256")
    if recorded != _canonical_index_digest(index):
        raise BackupDedupeError("T5 index digest mismatch; rebuild the index")
    files = index.get("files")
    if not isinstance(files, list):
        raise BackupDedupeError("T5 index files must be a list")

    seen: dict[str, str] = {}
    rebuilt: dict[str, list[str]] = {}
    for entry in files:
        if not isinstance(entry, dict):
            raise BackupDedupeError("T5 index contains a non-object file entry")
        relative = _safe_relative(entry.get("path"))
        raw = str(relative)
        key = _path_key(raw)
        previous = seen.get(key)
        if previous is not None and previous != raw:
            raise BackupDedupeError(
                f"canonical path collision in T5 index: {previous!r} and {raw!r}"
            )
        seen[key] = raw
        digest = entry.get("sha256")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise BackupDedupeError(f"invalid SHA-256 in T5 index for {raw!r}")
        if not isinstance(entry.get("bytes"), int) or entry["bytes"] < 0:
            raise BackupDedupeError(f"invalid byte count in T5 index for {raw!r}")
        if not isinstance(entry.get("mtime_ns"), int) or entry["mtime_ns"] < 0:
            raise BackupDedupeError(f"invalid mtime_ns in T5 index for {raw!r}")
        rebuilt.setdefault(digest, []).append(raw)

    for digest in rebuilt:
        rebuilt[digest].sort(key=lambda value: (_path_key(value), value))
    rebuilt = dict(sorted(rebuilt.items()))
    if index.get("by_sha256") != rebuilt:
        raise BackupDedupeError("T5 index by_sha256 lookup is inconsistent with files")
    if index.get("file_count") != len(files):
        raise BackupDedupeError("T5 index file_count does not match files")
    if index.get("total_bytes") != sum(entry["bytes"] for entry in files):
        raise BackupDedupeError("T5 index total_bytes does not match files")
    return index


def load_index(path: Path) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BackupDedupeError(f"T5 index not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BackupDedupeError(f"T5 index is not valid JSON: {path}: {exc}") from exc
    return validate_index(payload)


def write_index(index: dict, out_path: Path) -> None:
    out_path = Path(out_path)
    index = dict(index)
    index["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    _rebuild_lookup(index)
    _finalize_index(index)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = out_path.with_name(
        f".{out_path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp"
    )
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(index, indent=2, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, out_path)
    try:
        descriptor = os.open(out_path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def refresh_index(index: dict) -> dict:
    """Add new T5 files, but fail closed if indexed backup content disappeared/changed.

    Size + mtime_ns decide whether an existing indexed file needs a fresh hash.
    When either changes, the full SHA-256 must still equal the recorded digest;
    otherwise the index is stale/corrupt and must be rebuilt deliberately.
    """
    validate_index(index)
    root = Path(index["root"]).resolve()
    prior = {_path_key(entry["path"]): entry for entry in index["files"]}
    current = _scan_tree(root)
    current_keys = {_path_key(row["path"]) for row in current}
    missing = [
        entry["path"] for key, entry in prior.items()
        if key not in current_keys
    ]
    if missing:
        raise BackupDedupeError(
            "indexed T5 backup path is missing; rebuild required: "
            + ", ".join(sorted(missing)[:5])
        )

    files: list[dict] = []
    for row in current:
        absolute = row.pop("_absolute")
        key = _path_key(row["path"])
        old = prior.get(key)
        if old is None:
            digest = sha256_file(absolute)
        else:
            if old["path"] != row["path"]:
                raise BackupDedupeError(
                    "indexed T5 path spelling/normalization changed; rebuild required: "
                    f"{old['path']!r} -> {row['path']!r}"
                )
            if old["bytes"] == row["bytes"] and old["mtime_ns"] == row["mtime_ns"]:
                digest = old["sha256"]
            else:
                digest = sha256_file(absolute)
                if digest != old["sha256"]:
                    raise BackupDedupeError(
                        "indexed T5 backup content changed; rebuild required: "
                        f"{absolute}"
                    )
        row["sha256"] = digest
        files.append(row)

    refreshed = {
        "schema_version": INDEX_VERSION,
        "algorithm": ALGORITHM,
        "root": str(root),
        "generated_at": index.get("generated_at")
        or datetime.now().astimezone().isoformat(timespec="seconds"),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": files,
    }
    _rebuild_lookup(refreshed)
    return _finalize_index(refreshed)


def _index_entry_by_path(index: dict, relative: str) -> dict | None:
    key = _path_key(relative)
    found = [entry for entry in index["files"] if _path_key(entry["path"]) == key]
    if len(found) > 1:
        raise BackupDedupeError(f"ambiguous T5 index path: {relative!r}")
    return found[0] if found else None


def _candidate_path(root: Path, relative: str) -> Path:
    safe = _safe_relative(relative)
    candidate = root / safe
    resolved = _under(root, candidate)
    if candidate.is_symlink():
        raise BackupDedupeError(f"indexed T5 candidate is a symlink: {candidate}")
    return resolved


def _verified_reuse(index: dict, digest: str) -> Path | None:
    candidates = list(index["by_sha256"].get(digest, []))
    if not candidates:
        return None
    root = Path(index["root"]).resolve()
    verified: list[tuple[str, Path]] = []
    problems: list[str] = []
    for relative in candidates:
        try:
            path = _candidate_path(root, relative)
            if not path.is_file():
                raise BackupDedupeError(f"indexed T5 candidate is missing: {path}")
            actual = sha256_file(path)
            if actual != digest:
                raise BackupDedupeError(
                    f"indexed T5 candidate hash changed: {path} ({actual} != {digest})"
                )
            verified.append((relative, path))
        except BackupDedupeError as exc:
            problems.append(str(exc))
    if problems:
        raise BackupDedupeError(
            "T5 index is stale/corrupt for a requested hash; refusing reuse or copy: "
            + "; ".join(problems[:5])
        )
    verified.sort(key=lambda pair: (_path_key(pair[0]), pair[0]))
    return verified[0][1]


def _add_verified_path(index: dict, path: Path, digest: str) -> None:
    root = Path(index["root"]).resolve()
    path = _under(root, path)
    if path.is_symlink() or not path.is_file():
        raise BackupDedupeError(f"cannot index non-regular backup path: {path}")
    actual = sha256_file(path)
    if actual != digest:
        raise BackupDedupeError(
            f"cannot index backup whose content differs: {path} ({actual} != {digest})"
        )
    relative = str(path.relative_to(root))
    existing = _index_entry_by_path(index, relative)
    stat = path.stat()
    row = {
        "path": relative,
        "bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": digest,
    }
    if existing is not None:
        if existing["sha256"] != digest:
            raise BackupDedupeError(
                f"T5 index path changed identity; rebuild required: {relative}"
            )
        existing.update(row)
    else:
        index["files"].append(row)
        index["files"].sort(key=lambda item: (_path_key(item["path"]), item["path"]))
    _rebuild_lookup(index)
    _finalize_index(index)


def _requested_paths(source_root: Path, paths_json: Path | None) -> list[str]:
    if paths_json is None:
        return sorted(
            backup_manifest._walk_audio(Path(source_root).resolve()).keys(),
            key=lambda value: (_path_key(value), value),
        )
    try:
        payload = json.loads(Path(paths_json).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupDedupeError(f"cannot read requested paths JSON: {paths_json}: {exc}") from exc
    if isinstance(payload, dict):
        payload = payload.get("paths")
    if not isinstance(payload, list) or not payload:
        raise BackupDedupeError("requested paths JSON must be a non-empty list or {'paths': [...]}" )
    paths: list[str] = []
    seen: dict[str, str] = {}
    for raw in payload:
        relative = str(_safe_relative(raw))
        key = _path_key(relative)
        if key in seen:
            raise BackupDedupeError(
                f"duplicate/colliding requested paths: {seen[key]!r} and {relative!r}"
            )
        seen[key] = relative
        paths.append(relative)
    return paths


def prepare(
    *,
    index_path: Path,
    source_root: Path,
    lane_backup_root: Path,
    manifest_out: Path,
    paths_json: Path | None = None,
    backup_id: str | None = None,
) -> dict:
    source_root = Path(source_root).resolve()
    lane_backup_root = Path(lane_backup_root).resolve()
    if not source_root.is_dir():
        raise BackupDedupeError(f"source root is not a directory: {source_root}")

    index = refresh_index(load_index(index_path))
    t5_root = Path(index["root"]).resolve()
    _require_index_outside_root(index_path, t5_root)
    _under(t5_root, lane_backup_root)
    lane_backup_root.mkdir(parents=True, exist_ok=True)

    requested = _requested_paths(source_root, paths_json)
    if not requested:
        raise BackupDedupeError("source scope contains no files to back up")

    records: list[dict] = []
    reused_index = 0
    reused_lane = 0
    copied = 0

    for raw_relative in requested:
        relative = _safe_relative(raw_relative)
        source = source_root / relative
        source_resolved = _under(source_root, source)
        if source.is_symlink() or not source_resolved.is_file():
            raise BackupDedupeError(
                f"source item is missing, not a regular file, or a symlink: {source}"
            )
        source_hash = sha256_file(source_resolved)

        backup_path = _verified_reuse(index, source_hash)
        mode = "reused-index"
        if backup_path is not None:
            reused_index += 1
        else:
            destination = lane_backup_root / relative
            destination_resolved = _under(lane_backup_root, destination)
            t5_relative = str(destination_resolved.relative_to(t5_root))
            indexed_destination = _index_entry_by_path(index, t5_relative)
            if indexed_destination is not None:
                indexed_path = _candidate_path(t5_root, indexed_destination["path"])
                if not indexed_path.is_file():
                    raise BackupDedupeError(
                        f"lane destination is stale in T5 index: {indexed_path}"
                    )
                actual_indexed = sha256_file(indexed_path)
                if actual_indexed != indexed_destination["sha256"]:
                    raise BackupDedupeError(
                        f"lane destination changed since T5 index: {indexed_path}"
                    )
                raise BackupDedupeError(
                    f"lane destination is already indexed with different content: {indexed_path}"
                )

            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_symlink():
                raise BackupDedupeError(f"lane backup destination is a symlink: {destination}")
            if destination.exists():
                if not destination.is_file():
                    raise BackupDedupeError(
                        f"lane backup destination is not a regular file: {destination}"
                    )
                if sha256_file(destination) != source_hash:
                    raise BackupDedupeError(
                        f"lane backup destination exists with different content: {destination}"
                    )
                backup_path = destination.resolve()
                mode = "reused-lane"
                reused_lane += 1
            else:
                shutil.copy2(source_resolved, destination)
                if destination.is_symlink() or not destination.is_file():
                    raise BackupDedupeError(
                        f"new lane backup is not a regular file: {destination}"
                    )
                copied_hash = sha256_file(destination)
                if copied_hash != source_hash:
                    raise BackupDedupeError(
                        f"new lane backup hash mismatch: {destination}"
                    )
                if sha256_file(source_resolved) != source_hash:
                    raise BackupDedupeError(
                        f"source changed while backup was being copied: {source_resolved}"
                    )
                backup_path = destination.resolve()
                mode = "copied"
                copied += 1

            _add_verified_path(index, backup_path, source_hash)

        if sha256_file(source_resolved) != source_hash:
            raise BackupDedupeError(
                f"source changed while backup evidence was being prepared: {source_resolved}"
            )
        if backup_path is None or backup_path.is_symlink() or not backup_path.is_file():
            raise BackupDedupeError(f"no valid backup path for {source_resolved}")
        if sha256_file(backup_path) != source_hash:
            raise BackupDedupeError(f"backup path changed before manifest write: {backup_path}")

        records.append(
            {
                "path": str(relative),
                "backup_path": str(backup_path),
                "backup_mode": mode,
            }
        )

    manifest = backup_manifest.build_explicit_paths_manifest(
        source_root,
        lane_backup_root,
        records,
        backup_id=backup_id,
    )
    if manifest.get("status") != backup_manifest.STATUS_PASS:
        raise BackupDedupeError(
            "explicit backup manifest failed verification: "
            + "; ".join((manifest.get("problems") or [])[:5])
        )

    write_index(index, index_path)
    backup_manifest.write_manifest(manifest, manifest_out)

    return {
        "status": "PASS",
        "source_root": str(source_root),
        "t5_root": str(t5_root),
        "lane_backup_root": str(lane_backup_root),
        "manifest": str(Path(manifest_out).resolve()),
        "files": len(records),
        "reused_index": reused_index,
        "reused_lane": reused_lane,
        "copied": copied,
    }


def _require_index_outside_root(index_path: Path, root: Path) -> None:
    root = Path(root).resolve()
    resolved = Path(index_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return
    raise BackupDedupeError(
        f"T5 index must live outside the indexed T5 root to avoid self-indexing: {resolved}"
    )


def _write_json(payload: dict, path: Path | None) -> None:
    if path is None:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    index_parser = sub.add_parser("index", help="Build a full SHA-256 index of the T5 root")
    index_parser.add_argument("--root", type=Path, required=True)
    index_parser.add_argument("--out", type=Path, required=True)

    refresh_parser = sub.add_parser("refresh", help="Refresh a prior T5 index")
    refresh_parser.add_argument("--index", type=Path, required=True)

    prepare_parser = sub.add_parser(
        "prepare",
        help="Reuse identical T5 content or copy only missing source content",
    )
    prepare_parser.add_argument("--index", type=Path, required=True)
    prepare_parser.add_argument("--source-root", type=Path, required=True)
    prepare_parser.add_argument("--lane-backup-root", type=Path, required=True)
    prepare_parser.add_argument("--manifest-out", type=Path, required=True)
    prepare_parser.add_argument("--paths-json", type=Path)
    prepare_parser.add_argument("--backup-id")
    prepare_parser.add_argument("--report-out", type=Path)

    args = parser.parse_args()
    try:
        if args.command == "index":
            _require_index_outside_root(args.out, args.root)
            index = build_index(args.root)
            write_index(index, args.out)
            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "root": index["root"],
                        "files": index["file_count"],
                        "total_bytes": index["total_bytes"],
                        "index": str(args.out.resolve()),
                    },
                    indent=2,
                )
            )
            return 0
        if args.command == "refresh":
            index = load_index(args.index)
            _require_index_outside_root(args.index, Path(index["root"]))
            index = refresh_index(index)
            write_index(index, args.index)
            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "root": index["root"],
                        "files": index["file_count"],
                        "total_bytes": index["total_bytes"],
                        "index": str(args.index.resolve()),
                    },
                    indent=2,
                )
            )
            return 0

        report = prepare(
            index_path=args.index,
            source_root=args.source_root,
            lane_backup_root=args.lane_backup_root,
            manifest_out=args.manifest_out,
            paths_json=args.paths_json,
            backup_id=args.backup_id,
        )
        _write_json(report, args.report_out)
        return 0
    except (BackupDedupeError, backup_manifest.BackupGateError, OSError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
