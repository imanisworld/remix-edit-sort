#!/usr/bin/env python3
"""Run the DJ library audit with pre/post source verification.

This wrapper is read-only with respect to source audio. It fingerprints each audio
file before and after the audit and fails closed if any source path, size, mtime,
or SHA-256 changes. It also writes an immutable run manifest with report hashes.
"""

from __future__ import annotations

# Read-only: inspects the library and writes reports/derived data only.
SAFETY_CLASSIFICATION = "read-only"

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".aif", ".aiff", ".flac", ".m4a", ".aac", ".ogg", ".wma"
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a cryptographically verified read-only library audit.")
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--out-root", type=Path, default=Path("output/verified-audits"))
    parser.add_argument("--audit-script", type=Path, default=Path(__file__).with_name("audit_library.py"))
    parser.add_argument("--repo-sha", default="", help="Optional repository commit SHA recorded in the manifest.")
    parser.add_argument("--low-bitrate-kbps", type=int, default=192)
    parser.add_argument("--follow-symlinks", action="store_true")
    return parser.parse_args()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_audio_files(root: Path, follow_symlinks: bool = False) -> Iterable[Path]:
    stack = [root]
    while stack:
        current = stack.pop()
        for child in sorted(current.iterdir(), key=lambda p: p.name.casefold()):
            if child.is_symlink() and not follow_symlinks:
                continue
            if child.is_dir():
                stack.append(child)
            elif child.suffix.casefold() in AUDIO_EXTENSIONS:
                yield child


def fingerprint_roots(roots: list[Path], follow_symlinks: bool = False) -> dict[str, dict[str, Any]]:
    manifest: dict[str, dict[str, Any]] = {}
    seen: set[Path] = set()
    for root in roots:
        for path in iter_audio_files(root, follow_symlinks=follow_symlinks):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            stat = path.stat()
            manifest[str(resolved)] = {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sha256": sha256_file(path),
            }
    return dict(sorted(manifest.items()))


def compare_manifests(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    all_paths = sorted(set(before) | set(after))
    for path in all_paths:
        if path not in before:
            changes.append({"path": path, "change": "added", "before": None, "after": after[path]})
        elif path not in after:
            changes.append({"path": path, "change": "removed", "before": before[path], "after": None})
        elif before[path] != after[path]:
            changes.append({"path": path, "change": "modified", "before": before[path], "after": after[path]})
    return changes


def hash_reports(run_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in sorted(run_dir.iterdir(), key=lambda p: p.name.casefold()):
        if path.is_file() and path.name not in {"run-manifest.json", "source-before.json", "source-after.json"}:
            hashes[path.name] = sha256_file(path)
    return hashes


def make_run_id(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    return current.strftime("%Y%m%dT%H%M%S.%fZ")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
        handle.write("\n")


def run_verified_audit(
    roots: list[Path],
    out_root: Path,
    audit_script: Path,
    repo_sha: str = "",
    low_bitrate_kbps: int = 192,
    follow_symlinks: bool = False,
    runner=subprocess.run,
) -> tuple[int, Path, dict[str, Any]]:
    roots = [root.expanduser().resolve() for root in roots]
    for root in roots:
        if not root.is_dir():
            raise ValueError(f"Source is not a directory: {root}")
    audit_script = audit_script.expanduser().resolve()
    if not audit_script.is_file():
        raise ValueError(f"Audit script not found: {audit_script}")

    run_id = make_run_id()
    run_dir = out_root.expanduser().resolve() / run_id
    report_dir = run_dir / "reports"
    run_dir.mkdir(parents=True, exist_ok=False)

    started = datetime.now(timezone.utc)
    before = fingerprint_roots(roots, follow_symlinks=follow_symlinks)
    write_json(run_dir / "source-before.json", before)

    command = [
        sys.executable,
        str(audit_script),
        *[str(root) for root in roots],
        "--out-dir",
        str(report_dir),
        "--low-bitrate-kbps",
        str(low_bitrate_kbps),
    ]
    if follow_symlinks:
        command.append("--follow-symlinks")

    completed = runner(command, check=False)

    after = fingerprint_roots(roots, follow_symlinks=follow_symlinks)
    write_json(run_dir / "source-after.json", after)
    changes = compare_manifests(before, after)
    source_verified_unchanged = not changes
    completed_at = datetime.now(timezone.utc)

    manifest = {
        "run_id": run_id,
        "started_utc": started.isoformat(),
        "completed_utc": completed_at.isoformat(),
        "repo_commit_sha": repo_sha or None,
        "audit_script": str(audit_script),
        "roots": [str(root) for root in roots],
        "source_file_count_before": len(before),
        "source_file_count_after": len(after),
        "source_total_bytes_before": sum(item["size"] for item in before.values()),
        "source_total_bytes_after": sum(item["size"] for item in after.values()),
        "source_verified_unchanged": source_verified_unchanged,
        "source_changes": changes,
        "audit_exit_code": completed.returncode,
        "report_sha256": hash_reports(report_dir) if report_dir.exists() else {},
        "result": "PASS" if completed.returncode == 0 and source_verified_unchanged else "FAIL",
    }
    write_json(run_dir / "run-manifest.json", manifest)

    if not source_verified_unchanged:
        print("AUDIT FAILED: source mutation detected", file=sys.stderr)
        return 2, run_dir, manifest
    if completed.returncode != 0:
        print(f"AUDIT FAILED: audit script exited {completed.returncode}", file=sys.stderr)
        return completed.returncode or 1, run_dir, manifest
    return 0, run_dir, manifest


def main() -> int:
    args = parse_args()
    code, run_dir, manifest = run_verified_audit(
        roots=args.roots,
        out_root=args.out_root,
        audit_script=args.audit_script,
        repo_sha=args.repo_sha,
        low_bitrate_kbps=args.low_bitrate_kbps,
        follow_symlinks=args.follow_symlinks,
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    print(f"\nVerified run directory: {run_dir}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
