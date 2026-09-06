#!/usr/bin/env python3
"""Shared, fail-closed coordination for durable metadata batches.

This module owns only repository handoff state and T5 backup/evidence files.
It never edits library media or Serato data.  The one production-capable
operation here is a byte-for-byte folder copy to the fixed T5 backup root;
the existing mutation engine remains the only code allowed to edit media.
"""
from __future__ import annotations

SAFETY_CLASSIFICATION = "infrastructure"

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

import backup_manifest

REPO_ROOT = Path(__file__).resolve().parent.parent
HANDOFF_ROOT = REPO_ROOT / "handoff"
CURRENT_BATCH = HANDOFF_ROOT / "current-batch.json"
T5_VOLUME = Path("/Volumes/T5 EVO")
T5_BACKUP_ROOT = T5_VOLUME / "DJ/Backups/remix-edit-sort"

STATES = (
    "DISCOVERED", "PREVIEWED", "HUMAN_APPROVED", "PLANNED",
    "PREFLIGHT_PASS", "BACKED_UP", "APPLIED", "VERIFIED",
    "SERATO_SPOTCHECK", "COMPLETE", "BLOCKED", "ROLLBACK_FAILED",
)
FORWARD = {
    "DISCOVERED": "PREVIEWED",
    "PREVIEWED": "HUMAN_APPROVED",
    "HUMAN_APPROVED": "PLANNED",
    "PLANNED": "PREFLIGHT_PASS",
    "PREFLIGHT_PASS": "BACKED_UP",
    "BACKED_UP": "APPLIED",
    "APPLIED": "VERIFIED",
    "VERIFIED": "SERATO_SPOTCHECK",
    "SERATO_SPOTCHECK": "COMPLETE",
}


class BatchRefused(RuntimeError):
    """A safety gate refused the requested operation."""


class RollbackFailed(BatchRefused):
    """A mutation began and its automatic restore also failed."""


GATED_SAFETY_FILES = (
    "scripts/apply_approved_batch.py",
    "scripts/apply_new_music_intake_plan.py",
    "scripts/backup_manifest.py",
    "scripts/build_version_genre_plan.py",
    "scripts/folder_lock.py",
    "scripts/lock_guard.py",
    "scripts/metadata_batch.py",
    "scripts/mutation_engine.py",
    "scripts/mutation_surface.py",
    "scripts/prepare_metadata_batch.py",
    "references/version-genre-rules.json",
)


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(document: dict, excluded=("master_sha",)) -> str:
    body = {key: value for key, value in document.items() if key not in set(excluded)}
    payload = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def repo_head() -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        text=True, capture_output=True, check=True, timeout=10,
    )
    return result.stdout.strip()


def safety_file_hashes(repo_root: Path = REPO_ROOT) -> dict[str, str]:
    """Content binding for code and rules that affect approval or execution."""
    repo_root = Path(repo_root).resolve()
    result = {}
    for relative in GATED_SAFETY_FILES:
        path = repo_root / relative
        if not path.is_file():
            raise BatchRefused(f"gated safety file is missing: {path}")
        result[relative] = sha256_file(path)
    return result


def _is_within(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def require_handoff_path(path: Path, handoff_root: Path = HANDOFF_ROOT) -> Path:
    resolved = Path(path).resolve()
    if not _is_within(resolved, handoff_root):
        raise BatchRefused(f"durable artifact must resolve under {handoff_root}: {resolved}")
    return resolved


def require_t5_path(path: Path, t5_volume: Path = T5_VOLUME) -> Path:
    resolved = Path(path).resolve()
    if not _is_within(resolved, t5_volume):
        raise BatchRefused(f"production backup/evidence must resolve under {t5_volume}: {resolved}")
    return resolved


def atomic_write_json(path: Path, document: dict, *, handoff_root: Path | None = None,
                      t5_volume: Path | None = None) -> None:
    """Durably replace owned JSON only after its location is explicitly confined."""
    path = Path(path)
    if handoff_root is not None:
        path = require_handoff_path(path, handoff_root)
    elif t5_volume is not None:
        path = require_t5_path(path, t5_volume)
    else:
        raise BatchRefused("atomic_write_json requires an explicit confinement root")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp.exists():
            temp.unlink()


def read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchRefused(f"cannot read valid durable JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BatchRefused(f"durable JSON is not an object: {path}")
    return value


def initial_state() -> dict:
    return {
        "schema_version": 1,
        "batch_id": None,
        "state": "COMPLETE",
        "reason": "No active batch",
        "updated_at": now(),
        "history": [],
    }


def start_batch(state_path: Path, batch_id: str, batch_dir: Path,
                *, handoff_root: Path = HANDOFF_ROOT) -> dict:
    state_path = require_handoff_path(state_path, handoff_root)
    if state_path.exists():
        prior = read_json(state_path)
        if prior.get("state") != "COMPLETE":
            raise BatchRefused(
                f"current batch {prior.get('batch_id')} is {prior.get('state')}; "
                "finish or explicitly resolve it before preparing another"
            )
    stamp = now()
    state = {
        "schema_version": 1,
        "batch_id": batch_id,
        "batch_dir": str(require_handoff_path(batch_dir, handoff_root)),
        "state": "DISCOVERED",
        "reason": None,
        "updated_at": stamp,
        "history": [{"state": "DISCOVERED", "at": stamp}],
        "scopes": {},
    }
    atomic_write_json(state_path, state, handoff_root=handoff_root)
    return state


def transition(state_path: Path, new_state: str, *, reason: str | None = None,
               updates: dict | None = None, handoff_root: Path = HANDOFF_ROOT) -> dict:
    if new_state not in STATES:
        raise BatchRefused(f"unknown batch state: {new_state}")
    state_path = require_handoff_path(state_path, handoff_root)
    state = read_json(state_path)
    old = state.get("state")
    if new_state not in {"BLOCKED", "ROLLBACK_FAILED"} and FORWARD.get(old) != new_state:
        raise BatchRefused(f"invalid batch transition {old} -> {new_state}")
    if old == "COMPLETE" and new_state in {"BLOCKED", "ROLLBACK_FAILED"}:
        raise BatchRefused("a completed batch cannot be retroactively blocked")
    stamp = now()
    state["state"] = new_state
    state["reason"] = reason
    state["updated_at"] = stamp
    state.setdefault("history", []).append({"state": new_state, "at": stamp, "reason": reason})
    if updates:
        state.update(updates)
    atomic_write_json(state_path, state, handoff_root=handoff_root)
    return state


def update_scope(state_path: Path, scope_id: str, scope_state: str, *, details: dict | None = None,
                 handoff_root: Path = HANDOFF_ROOT) -> dict:
    if scope_state not in STATES:
        raise BatchRefused(f"unknown scope state: {scope_state}")
    state_path = require_handoff_path(state_path, handoff_root)
    state = read_json(state_path)
    scopes = state.setdefault("scopes", {})
    prior = scopes.get(scope_id, {}).get("state", "PREFLIGHT_PASS")
    if scope_state not in {"BLOCKED", "ROLLBACK_FAILED"} and FORWARD.get(prior) != scope_state:
        raise BatchRefused(f"invalid scope transition {scope_id}: {prior} -> {scope_state}")
    entry = scopes.setdefault(scope_id, {"history": []})
    entry["state"] = scope_state
    entry["updated_at"] = now()
    entry.setdefault("history", []).append({"state": scope_state, "at": entry["updated_at"]})
    if details:
        entry.update(details)
    atomic_write_json(state_path, state, handoff_root=handoff_root)
    return state


def verify_master(master_path: Path, expected_sha: str) -> dict:
    master = read_json(master_path)
    embedded = master.get("master_sha")
    actual = canonical_sha(master)
    if not expected_sha or embedded != expected_sha or actual != expected_sha:
        raise BatchRefused(
            f"master SHA mismatch: requested={expected_sha!r} embedded={embedded!r} actual={actual!r}"
        )
    return master


def create_verified_backup(source_root: Path, destination: Path, manifest_path: Path,
                           *, t5_volume: Path = T5_VOLUME, heartbeat=None) -> dict:
    """Copy one full scope to a new T5 directory and require computed PASS."""
    source_root = Path(source_root).resolve()
    destination = require_t5_path(destination, t5_volume)
    manifest_path = require_t5_path(manifest_path, t5_volume)
    if not source_root.is_dir():
        raise BatchRefused(f"backup source is not a directory: {source_root}")
    symlinks = [str(path) for path in source_root.rglob("*") if path.is_symlink()]
    if symlinks:
        raise BatchRefused(
            f"backup scope contains symlinks and cannot be proven as a literal full-folder copy: {symlinks[:10]}"
        )
    if destination.exists():
        raise BatchRefused(f"backup destination already exists; refusing overwrite: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if heartbeat is not None:
        heartbeat.beat(force=True)

    def copy_with_heartbeat(source, target):
        if heartbeat is not None:
            heartbeat.beat()
        return shutil.copy2(source, target)

    shutil.copytree(source_root, destination, copy_function=copy_with_heartbeat)
    if heartbeat is not None:
        heartbeat.beat(force=True)
    manifest = backup_manifest.build_manifest(
        source_root, destination, backup_id=f"metadata-batch-{destination.parent.name}"
    )
    backup_manifest.write_manifest(manifest, manifest_path)
    backup_manifest.require_pass(manifest_path, source_root)
    if heartbeat is not None:
        heartbeat.beat(force=True)
    return manifest


def create_verified_tag_only_backup(
    source_root: Path,
    plan_path: Path,
    destination: Path,
    manifest_path: Path,
    *,
    t5_volume: Path = T5_VOLUME,
    heartbeat=None,
) -> dict:
    """Back up only tag-write candidates from a tag-only plan, then prove them.

    This is deliberately narrower than ``create_verified_backup`` and refuses
    any plan containing a move.  It preserves each source-relative path under
    the backup root because ``mutation_engine`` resolves and re-hashes that
    exact copy immediately before each mutation.
    """
    source_root = Path(source_root).resolve()
    destination = require_t5_path(destination, t5_volume)
    manifest_path = require_t5_path(manifest_path, t5_volume)
    plan = read_json(plan_path)
    items = plan.get("items")

    if not source_root.is_dir():
        raise BatchRefused(f"backup source is not a directory: {source_root}")
    if destination.exists():
        raise BatchRefused(f"backup destination already exists; refusing overwrite: {destination}")
    if not isinstance(items, list):
        raise BatchRefused(f"plan has no items list: {plan_path}")
    if any(bool(item.get("move")) for item in items if isinstance(item, dict)):
        raise BatchRefused("scoped tag-only backup refuses any plan containing moves")

    selected = []
    seen = set()
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise BatchRefused(f"plan item {index} is not an object")
        if not item.get("tag_change"):
            continue
        relative_text = item.get("source_relative")
        if not isinstance(relative_text, str) or not relative_text.strip():
            raise BatchRefused(f"plan item {index} is missing source_relative")
        relative = Path(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            raise BatchRefused(f"unsafe source_relative in plan item {index}: {relative_text!r}")
        expected_source = (source_root / relative).resolve()
        source_text = item.get("source")
        if not isinstance(source_text, str) or not source_text:
            raise BatchRefused(f"plan item {index} is missing source")
        source = Path(source_text).resolve()
        if source != expected_source:
            raise BatchRefused(
                f"source/source_relative mismatch in plan item {index}: {source} != {expected_source}"
            )
        try:
            source.relative_to(source_root)
        except ValueError as exc:
            raise BatchRefused(f"plan source escapes source root: {source}") from exc
        if not source.is_file() or source.is_symlink():
            raise BatchRefused(f"planned tag source is not a literal file: {source}")
        expected_hash = item.get("sha256")
        actual_hash = sha256_file(source)
        if not isinstance(expected_hash, str) or actual_hash != expected_hash:
            raise BatchRefused(f"source hash changed since plan: {source}")
        key = backup_manifest._relative_key(str(relative))
        if key in seen:
            raise BatchRefused(f"duplicate/colliding source_relative in tag-only plan: {relative}")
        seen.add(key)
        selected.append((relative, source, actual_hash, source.stat().st_size))

    if not selected:
        raise BatchRefused("tag-only plan contains no tag_change items to back up")

    # Finish every read-only precondition before creating the destination.
    destination.mkdir(parents=True, exist_ok=False)
    files = []
    total_bytes = 0
    for relative, source, source_hash, size in selected:
        if heartbeat is not None:
            heartbeat.beat()
        backup_path = destination / relative
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup_path)
        backup_hash = sha256_file(backup_path)
        if backup_hash != source_hash:
            raise BatchRefused(f"scoped backup copy hash mismatch: {backup_path}")
        total_bytes += size
        files.append({
            "path": str(relative),
            "sha256": source_hash,
            "bytes": size,
            "backed_up": True,
            "backup_sha256": backup_hash,
            "matched": True,
        })

    manifest = {
        "manifest_version": backup_manifest.MANIFEST_VERSION,
        "backup_id": f"tag-only-{destination.parent.name}",
        "timestamp": now(),
        "source_root": str(source_root),
        "source_root_identity": backup_manifest._identity(source_root),
        "backup_root": str(destination),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
        "serato": {
            "required": False,
            "present": False,
            "path": None,
            "result": "NOT_REQUIRED",
        },
        "verifier": backup_manifest.VERIFIER_NAME,
        "verifier_version": backup_manifest.MANIFEST_VERSION,
        "repo_sha": backup_manifest._repo_sha(),
        "scope_mode": "tag-only-plan",
        "scope_plan": str(Path(plan_path).resolve()),
        "problems": [],
        "status": backup_manifest.STATUS_PASS,
    }
    manifest["manifest_sha256"] = backup_manifest.canonical_digest(manifest)
    backup_manifest.write_manifest(manifest, manifest_path)
    verified = backup_manifest.require_pass(manifest_path, source_root)
    for _, source, source_hash, _ in selected:
        recorded = backup_manifest.require_item_backed_up(verified, source_root, source)
        if recorded != source_hash:
            raise BatchRefused(f"scoped manifest evidence mismatch after write: {source}")
    if heartbeat is not None:
        heartbeat.beat(force=True)
    return verified
