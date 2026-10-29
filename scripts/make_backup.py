#!/usr/bin/env python3
"""Make the smallest backup a change needs -- no overlapping copies.

Imani (2026-10-28): "make it so we do not make overlapping copies unless
needed". Earlier runs rsync'd whole folders and a fresh 1-2 GB _Serato_ copy
every time, so T5 filled up with near-identical snapshots. This tool:

* ``files``: copies ONLY the files the change touches (relative to the source
  root, given with --file or read from a plan's items[].path) to
  ``<dest>/files/<relative path>``, then writes a scoped (explicit-files)
  manifest with backup_manifest.build_scoped_manifest. Nothing else in the
  folder is copied.
* Serato: takes a new full _Serato_ snapshot ONLY when _Serato_ changed since
  the last verified snapshot. It fingerprints _Serato_ (sha256 of every file,
  ignoring Logs/ and Reports/, which Serato rewrites on every launch and which
  a restore never needs) and compares it with the newest entry in
  ``<snapshot-root>/serato-snapshots.json``. If they match and that snapshot
  still re-hashes to the same fingerprint, the manifest REFERENCES it
  (serato.snapshot.reused = true) instead of copying again. Otherwise a full
  copy is made under ``<dest>/_Serato_``, verified against the source, and
  registered.
* ``serato-snapshot``: the Serato half on its own.

Smart-crate edits keep using ``edit_smart_crate.py --smartcrates-only-backup``
(SmartCrates folder only); this tool doesn't change that.

Copies use shutil.copy2 (no hardlinks: T5 is exFAT). This tool only writes
inside --dest and the registry file; it never touches the library or _Serato_.
"""

from __future__ import annotations

SAFETY_CLASSIFICATION = "infrastructure"

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup_manifest  # noqa: E402

REGISTRY_NAME = "serato-snapshots.json"
FINGERPRINT_IGNORE = ("Logs", "Reports")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(8 << 20):
            h.update(chunk)
    return h.hexdigest()


def serato_fingerprint(serato_dir: Path) -> dict:
    """{fingerprint, file_count, bytes} over every file except Logs/ and Reports/."""
    serato_dir = Path(serato_dir)
    entries = []
    total = 0
    for dirpath, dirnames, filenames in os.walk(serato_dir):
        rel_dir = Path(dirpath).relative_to(serato_dir)
        if rel_dir == Path("."):
            dirnames[:] = [d for d in dirnames if d not in FINGERPRINT_IGNORE]
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file():
                continue
            rel = (rel_dir / name).as_posix()
            entries.append(f"{rel}\0{_sha256(path)}")
            total += path.stat().st_size
    digest = hashlib.sha256("\n".join(sorted(entries)).encode("utf-8")).hexdigest()
    return {"fingerprint": digest, "file_count": len(entries), "bytes": total}


def load_registry(snapshot_root: Path) -> list[dict]:
    path = Path(snapshot_root) / REGISTRY_NAME
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _save_registry(snapshot_root: Path, entries: list[dict]) -> None:
    path = Path(snapshot_root) / REGISTRY_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with open(tmp, "rb+") as fh:
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def ensure_serato_snapshot(serato_dir: Path, snapshot_root: Path, dest: Path,
                           force_new: bool = False) -> dict:
    """Reuse the last verified snapshot if _Serato_ is unchanged, else copy+verify+register."""
    current = serato_fingerprint(serato_dir)
    registry = load_registry(snapshot_root)
    if not force_new:
        for entry in reversed(registry):
            snap = Path(entry["path"])
            if not snap.is_dir():
                continue  # deleted snapshot: fall back to an older one or copy anew
            if entry["fingerprint"] != current["fingerprint"]:
                break  # newest surviving snapshot differs: _Serato_ changed
            recheck = serato_fingerprint(snap)
            if recheck["fingerprint"] != entry["fingerprint"]:
                break  # snapshot no longer intact: don't trust it, copy anew
            return {**entry, "reused": True, "checked_at": _now()}
    target = Path(dest) / "_Serato_"
    if target.exists():
        raise RuntimeError(f"refusing to overwrite existing snapshot dir: {target}")
    shutil.copytree(serato_dir, target, copy_function=shutil.copy2, symlinks=True)
    copied = serato_fingerprint(target)
    if copied["fingerprint"] != current["fingerprint"]:
        raise RuntimeError(
            "Serato snapshot verification failed (copy differs from source; was Serato running?): "
            f"{target}"
        )
    entry = {"path": str(target.resolve()), **current, "created_at": _now()}
    registry.append(entry)
    _save_registry(snapshot_root, registry)
    return {**entry, "reused": False}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def copy_touched_files(source_root: Path, dest: Path, relative_paths: list[str]) -> Path:
    """Copy only the touched files to <dest>/files/<rel>. Returns the files root."""
    source_root = Path(source_root)
    files_root = Path(dest) / "files"
    for rel in relative_paths:
        rel_path = Path(rel)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            raise RuntimeError(f"unsafe relative path: {rel}")
        src = source_root / rel_path
        if not src.is_file() or src.is_symlink():
            raise RuntimeError(f"not a regular file: {src}")
        out = files_root / rel_path
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists():
            if _sha256(out) == _sha256(src):
                continue
            raise RuntimeError(f"backup copy exists and differs; use a new --dest: {out}")
        shutil.copy2(src, out)
    return files_root


def relative_paths_from_plan(plan_path: Path, source_root: Path) -> list[str]:
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    root = Path(source_root).resolve()
    rels = []
    for item in plan.get("items", []):
        p = Path(item["path"]).resolve()
        if root not in p.parents:
            raise RuntimeError(f"plan item outside source root: {p}")
        rels.append(p.relative_to(root).as_posix())
    return sorted(set(rels))


def backup_files(source_root: Path, dest: Path, relative_paths: list[str], out: Path, *,
                 serato_dir: Path | None, snapshot_root: Path | None, backup_id: str | None = None,
                 force_new_snapshot: bool = False) -> dict:
    if not relative_paths:
        raise RuntimeError("no files to back up")
    Path(dest).mkdir(parents=True, exist_ok=True)
    files_root = copy_touched_files(source_root, dest, relative_paths)
    snapshot = None
    if serato_dir is not None:
        snapshot = ensure_serato_snapshot(serato_dir, snapshot_root or Path(dest).parent, dest,
                                          force_new=force_new_snapshot)
    manifest = backup_manifest.build_scoped_manifest(
        source_root, files_root, relative_paths, backup_id=backup_id,
        serato_backup=Path(snapshot["path"]) if snapshot else None,
        serato_required=snapshot is not None,
        serato_snapshot=snapshot,
    )
    backup_manifest.write_manifest(manifest, out)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("files", help="copy only the touched files (+ Serato snapshot only if changed)")
    f.add_argument("--source-root", required=True, type=Path)
    f.add_argument("--dest", required=True, type=Path, help="new backup folder, e.g. /Volumes/T5 EVO/DJ-backups/2026-10-29-x")
    f.add_argument("--file", action="append", default=[], dest="files", help="path relative to --source-root (repeatable)")
    f.add_argument("--plan", type=Path, help="read items[].path from this plan instead of / as well as --file")
    f.add_argument("--serato-dir", type=Path, help="live _Serato_ folder; omit for no Serato evidence")
    f.add_argument("--snapshot-root", type=Path, help=f"folder holding {REGISTRY_NAME} (default: parent of --dest)")
    f.add_argument("--force-new-snapshot", action="store_true")
    f.add_argument("--backup-id")
    f.add_argument("--out", required=True, type=Path)
    s = sub.add_parser("serato-snapshot", help="reuse or take a verified full _Serato_ snapshot")
    s.add_argument("--serato-dir", required=True, type=Path)
    s.add_argument("--dest", required=True, type=Path)
    s.add_argument("--snapshot-root", type=Path)
    s.add_argument("--force-new-snapshot", action="store_true")
    args = ap.parse_args()

    if args.cmd == "serato-snapshot":
        args.dest.mkdir(parents=True, exist_ok=True)
        snap = ensure_serato_snapshot(args.serato_dir, args.snapshot_root or args.dest.parent, args.dest,
                                      force_new=args.force_new_snapshot)
        print(json.dumps(snap, indent=2))
        return 0
    rels = list(args.files)
    if args.plan:
        rels += relative_paths_from_plan(args.plan, args.source_root)
    rels = sorted(set(rels))
    m = backup_files(args.source_root, args.dest, rels, args.out, serato_dir=args.serato_dir,
                     snapshot_root=args.snapshot_root, backup_id=args.backup_id,
                     force_new_snapshot=args.force_new_snapshot)
    print(json.dumps({"status": m["status"], "file_count": m["file_count"],
                      "serato": {k: m["serato"].get(k) for k in ("result", "path")},
                      "serato_reused": (m["serato"].get("snapshot") or {}).get("reused"),
                      "problems": m["problems"][:10], "manifest": str(args.out)}, indent=2))
    return 0 if m["status"] == backup_manifest.STATUS_PASS else 1


if __name__ == "__main__":
    sys.exit(main())
