#!/usr/bin/env python3
"""The one approved mutation engine. Every production mutation goes through it.

Before this existed, five apply scripts each carried their own hand-rolled
copy of "hash it, back it up, write it, verify it, roll back on failure,
append an undo line". They had drifted: some heartbeat the lock per item and
some don't, some prove a per-file backup and some check only that a directory
exists, none of them write recovery intent *before* mutating. Duplicated
safety code is not safety; it is five chances to get it wrong once.

## The invariant this enforces, per item, in this order

 1. reverify lock ownership and renew the heartbeat        (abort if lost)
 2. validate source preconditions -- exists, is a file,
    SHA-256 still matches what the preview recorded        (abort if drifted)
 3. prove verified backup evidence for THIS file           (abort if absent)
 4. write a durable write-ahead INTENT record and fsync it (before any write)
 5. perform the logical mutation (a case-only rename is two journaled physical
    renames through a unique temporary name)
 6. verify: expected metadata, audio payload unchanged,
    Serato payload/frames unchanged where applicable
 7. on any verification failure: restore immediately from backup, journal the
    rollback, and stop the run
 8. commit a durable COMPLETED record and fsync it
 9. only then move to the next item

Steps 4 and 8 are what make a crash recoverable. If the process dies at any
point after step 4 and before step 8, the journal still contains the intent
record -- source path, destination path, pre-mutation SHA-256, backup path
and its hash, and the mutation class -- which is everything needed to
deterministically restore or inspect that one item. `recover()` and the
`--recover` CLI report exactly those in-flight items.

## What "transactional" does and does not mean here

Each ITEM is transactional: it either completes and is verified, or it is
rolled back to its pre-mutation bytes. A BATCH is not atomic -- there is no
way to make N independent filesystem renames commit as one unit on APFS -- so
a failed run leaves earlier items applied and journaled. That is deliberate
and is why the undo log exists. Do not describe a batch as all-or-nothing.

## Mutation classes

The engine knows about exactly the currently approved classes (see
MUTATION_CLASSES). A caller asking for anything else is refused, so a new
kind of mutation cannot quietly ride in on the existing machinery without
someone extending this list on purpose.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import errno
import hashlib
import json
import os
import shutil
import sys
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup_manifest  # noqa: E402
import lock_guard  # noqa: E402

SAFETY_CLASSIFICATION = "mutation-engine"

# The mutation classes this engine is approved to perform. Anything else is
# refused at begin_item() time.
RELOCATE = "relocate"
GENRE = "genre"
GROUPING = "grouping"
ARTIST_TITLE = "artist_title"
YEAR_BPM = "year_bpm"
DELETE = "delete"
METADATA = "metadata"
SERATO_PATHS = "serato_paths"
FOLDER_RENAME = "folder_rename"

MUTATION_CLASSES = frozenset({
    RELOCATE, GENRE, GROUPING, ARTIST_TITLE, YEAR_BPM, DELETE, METADATA,
    SERATO_PATHS, FOLDER_RENAME,
})

# Names that carry no library content and that Finder may create at any time.
INVENTORY_IGNORED_NAMES = frozenset({".DS_Store"})

JOURNAL_VERSION = 2


class MutationRefused(Exception):
    """A precondition failed. Nothing was mutated for this item."""


class MutationVerificationError(Exception):
    """The mutation happened but did not verify. The item was rolled back."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _comparison_path(path: Path) -> str:
    """Model a case-insensitive, normalization-insensitive path comparison.

    ``os.path.normcase`` is intentionally not used: it is the identity on
    macOS. NFC + casefold models the aliases APFS can expose while the
    subsequent ``samefile`` check proves identity from the filesystem.
    """
    return unicodedata.normalize("NFC", os.path.abspath(os.fspath(path))).casefold()


def path_collision_key(path: Path) -> str:
    """Return the APFS-safe key used to find case/Unicode path aliases."""
    return _comparison_path(Path(path))


def is_case_or_normalization_only_rename(source: Path, destination: Path) -> bool:
    source_text = os.path.abspath(os.fspath(source))
    destination_text = os.path.abspath(os.fspath(destination))
    return source_text != destination_text and _comparison_path(source) == _comparison_path(destination)


def destination_is_source_alias(source: Path, destination: Path) -> bool:
    """True only when destination names this source with different spelling."""
    source, destination = Path(source), Path(destination)
    if not is_case_or_normalization_only_rename(source, destination):
        return False
    try:
        return destination.exists() and os.path.samefile(source, destination)
    except OSError:
        return False


def destination_conflicts(source: Path, destination: Path) -> bool:
    """Refuse a real occupant, but not this source's APFS case alias."""
    destination = Path(destination)
    return destination.exists() and not destination_is_source_alias(source, destination)


def literal_path_is_file(path: Path) -> bool:
    """Check the directory entry's literal spelling, not APFS alias lookup."""
    path = Path(path)
    try:
        with os.scandir(path.parent) as entries:
            return any(entry.name == path.name and entry.is_file(follow_symlinks=False) for entry in entries)
    except OSError:
        return False


def literal_path_is_dir(path: Path) -> bool:
    """Directory-entry check for a real (non-symlink) directory.

    ``Path.is_dir()`` on a case-insensitive APFS volume answers True for ANY
    spelling of the name, so it cannot prove that ``R&B`` rather than ``R&b``
    is the entry on disk. This scans the parent and compares the stored name.
    """
    path = Path(path)
    try:
        with os.scandir(path.parent) as entries:
            return any(
                entry.name == path.name and entry.is_dir(follow_symlinks=False)
                for entry in entries
            )
    except OSError:
        return False


def literal_entry_exists(path: Path) -> bool:
    """True if the parent directory has an entry named exactly ``path.name``."""
    path = Path(path)
    try:
        with os.scandir(path.parent) as entries:
            return any(entry.name == path.name for entry in entries)
    except OSError:
        return False


def literal_entry_names(directory: Path) -> set[str]:
    """Exact on-disk entry names of ``directory`` (empty set if unreadable)."""
    try:
        with os.scandir(directory) as entries:
            return {entry.name for entry in entries}
    except OSError:
        return set()


def literal_path_components_exist(path: Path) -> bool:
    """Every component of an absolute path is spelled exactly as on disk.

    A path typed or stored with the wrong case still resolves on APFS, so a
    plan built from it would silently carry that spelling into Serato's
    stored paths. This walks the chain from the root and compares each name.
    """
    path = Path(os.path.abspath(os.fspath(path)))
    current = Path(path.anchor)
    for part in path.parts[1:]:
        names = literal_entry_names(current)
        if part not in names:
            return False
        current = current / part
    return True


def directory_inventory(root: Path, *, hash_contents: bool = False) -> dict:
    """Content fingerprint of a directory tree, independent of its own name.

    Records every entry's relative path, type, and for files size and
    ``mtime_ns`` (plus SHA-256 when ``hash_contents``). A whole-folder rename
    must leave this digest unchanged; any file added, removed, resized, or
    retagged between preview and apply -- or lost by the rename itself --
    changes it. Symlinks are recorded, never followed.
    """
    import stat as stat_module

    root = Path(root)
    entries: list[list] = []
    counts = {"file_count": 0, "directory_count": 0, "symlink_count": 0, "total_bytes": 0}

    def fail(error: OSError):
        raise error

    for current, dirnames, filenames in os.walk(root, followlinks=False, onerror=fail):
        dirnames.sort()
        for name in dirnames:
            full = os.path.join(current, name)
            relative = os.path.relpath(full, root)
            info = os.lstat(full)
            if stat_module.S_ISLNK(info.st_mode):
                counts["symlink_count"] += 1
                entries.append([relative, "l", os.readlink(full)])
            else:
                counts["directory_count"] += 1
                entries.append([relative, "d"])
        for name in sorted(filenames):
            if name in INVENTORY_IGNORED_NAMES:
                continue
            full = os.path.join(current, name)
            relative = os.path.relpath(full, root)
            info = os.lstat(full)
            if stat_module.S_ISLNK(info.st_mode):
                counts["symlink_count"] += 1
                entries.append([relative, "l", os.readlink(full)])
                continue
            counts["file_count"] += 1
            counts["total_bytes"] += info.st_size
            entry = [relative, "f", info.st_size, info.st_mtime_ns]
            if hash_contents:
                entry.append(sha256_file(Path(full)))
            entries.append(entry)
    entries.sort(key=lambda entry: entry[0])
    payload = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return {**counts, "hash_contents": bool(hash_contents), "sha256": sha256_bytes(payload)}


def rename_noreplace(source: Path, destination: Path) -> None:
    """Rename that the kernel refuses if ``destination`` exists (any type).

    ``os.rename`` silently replaces an empty destination directory, so a
    check-then-rename leaves a window in which a folder created by another
    process is lost. macOS ``renamex_np(RENAME_EXCL)`` and Linux
    ``renameat2(RENAME_NOREPLACE)`` make the check and the rename one atomic
    step. If neither is available (or the filesystem does not support the
    flag) this fails closed rather than falling back to ``os.rename``.
    """
    src, dst = os.fsencode(source), os.fsencode(destination)
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if sys.platform == "darwin":
            call = libc.renamex_np
            call.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            call.restype = ctypes.c_int
            result = call(src, dst, 0x4)  # RENAME_EXCL
        elif sys.platform.startswith("linux"):
            call = libc.renameat2
            call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            call.restype = ctypes.c_int
            result = call(-100, src, -100, dst, 1)  # AT_FDCWD, RENAME_NOREPLACE
        else:
            raise AttributeError(sys.platform)
    except (AttributeError, OSError) as exc:
        raise MutationRefused(
            f"no atomic no-replace rename is available on this platform ({exc}); refusing "
            f"to rename a folder with a check-then-rename"
        ) from exc
    if result != 0:
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise MutationRefused(f"destination already exists: {destination}")
        if code in (errno.ENOTSUP, errno.EINVAL, errno.ENOSYS):
            raise MutationRefused(
                f"this filesystem does not support an atomic no-replace rename ({os.strerror(code)}); "
                f"refusing to rename a folder here: {source}"
            )
        raise OSError(code, os.strerror(code), os.fspath(source), None, os.fspath(destination))


def check_folder_rename(source: Path, destination: Path) -> bool:
    """Refuse anything but a safe in-place folder rename; return case_only.

    Pure read-only precondition check (literal directory entries, no aliases).
    """
    if os.path.abspath(os.fspath(source.parent)) != os.path.abspath(os.fspath(destination.parent)):
        raise MutationRefused(
            f"folder rename must stay inside one parent directory: {source} -> {destination}"
        )
    if os.path.abspath(os.fspath(source)) == os.path.abspath(os.fspath(destination)):
        raise MutationRefused(f"source and destination are identical: {source}")
    if source.is_symlink() or not literal_path_is_dir(source):
        raise MutationRefused(f"missing rename source folder (literal directory entry): {source}")
    case_only = is_case_or_normalization_only_rename(source, destination)
    if case_only:
        # On a case-insensitive volume the destination spelling resolves to
        # the source itself, which is not a collision; a literal entry with
        # the destination spelling, or any other occupant, is.
        if literal_entry_exists(destination) or destination_conflicts(source, destination):
            raise MutationRefused(f"destination already exists: {destination}")
    elif os.path.lexists(destination):
        raise MutationRefused(f"destination already exists: {destination}")
    return case_only


def _prove_manifest_backup(manifest: dict, source_root: Path, source: Path, actual: str) -> tuple[str, Path]:
    """Prove ``source`` (currently hashing to ``actual``) has a verified backup.

    Returns ``(manifest_sha256, concrete_backup_path)``. Raises MutationRefused
    unless the manifest's evidence for this file matches the live bytes and the
    concrete backup copy still exists and still hashes to the same value.
    """
    backup_sha = backup_manifest.require_item_backed_up(manifest, source_root, source)
    if backup_sha != actual:
        raise MutationRefused(
            f"verified backup for {source} records {backup_sha}, but the file on disk "
            f"is now {actual} -- it changed after the backup was taken"
        )
    # A PASS manifest proves that this copy existed and matched when
    # verification ran. Re-check the real copy immediately before this
    # mutation as well: the backup may have been moved, removed, or damaged
    # after the manifest was written. Recording the concrete path also makes
    # crash recovery actionable instead of merely naming a backup id/hash.
    manifest_backup = backup_manifest.backup_path_for_item(manifest, source_root, source)
    if not manifest_backup.is_file():
        raise MutationRefused(f"per-item backup copy missing: {manifest_backup}")
    if sha256_file(manifest_backup) != actual:
        raise MutationRefused(
            f"per-item backup copy no longer matches manifest evidence: {manifest_backup}"
        )
    return backup_sha, manifest_backup


class RecoveryJournal:
    """Append-only, fsync-per-record write-ahead log.

    Every record is one JSON line flushed and fsync'd before the call
    returns, so an intent record is on stable storage before the mutation it
    describes is attempted. Buffered writes would defeat the entire point:
    the crash window this closes is precisely the one where the process dies
    with the record still in a userspace buffer.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict) -> dict:
        record = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"), **record}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def read(self) -> list[dict]:
        if not self.path.is_file():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                # A torn final line means the process died mid-append. That is
                # itself recovery-relevant information, not something to hide.
                records.append({"type": "torn_record", "raw": line})
        return records


class _ItemTransaction:
    """One journaled mutation. Created by MutationEngine.item()."""

    def __init__(self, engine: "MutationEngine", index: int, intent: dict):
        self.engine = engine
        self.index = index
        self.intent = intent
        self.mutated = False
        self._rollbacks = []
        self.outcome: dict = {}

    def on_rollback(self, callback) -> None:
        """Register how to restore this item if verification fails."""
        self._rollbacks.append(callback)

    def mark_mutated(self) -> None:
        """Call immediately after each physical mutation succeeds.

        This is what tells the engine that a failure from here on needs a
        rollback rather than a plain abort, and it is recorded in the journal
        so recovery can tell "died before touching anything" apart from
        "died with the file already changed".
        """
        self.mutated = True
        self.engine.journal.append({
            "type": "mutated",
            "run_id": self.engine.run_id,
            "index": self.index,
            "source": self.intent.get("source"),
        })

    def record(self, **fields) -> None:
        """Attach result fields to the completion record."""
        self.outcome.update(fields)

    def _rename_step(
        self, source: Path, destination: Path, purpose: str, *, exclusive: bool = False
    ) -> None:
        """Journal and heartbeat one physical rename before performing it.

        ``exclusive`` (used for every folder rename) makes the kernel refuse an
        existing destination atomically instead of trusting an earlier check.
        """
        self.engine.journal.append({
            "type": "rename_step_intent",
            "run_id": self.engine.run_id,
            "index": self.index,
            "source": str(source),
            "destination": str(destination),
            "purpose": purpose,
        })
        if self.engine.heartbeat is not None:
            self.engine.heartbeat.beat()
        if exclusive:
            rename_noreplace(source, destination)
        else:
            os.rename(source, destination)
        self.mark_mutated()
        self.engine.journal.append({
            "type": "rename_step_completed",
            "run_id": self.engine.run_id,
            "index": self.index,
            "source": str(source),
            "destination": str(destination),
            "purpose": purpose,
        })

    def rename_no_overwrite(self, source: Path, destination: Path, *, purpose: str = "relocate") -> None:
        """Rename without mistaking an APFS case alias for a collision.

        Case/normalization-only changes always go through a unique temporary
        directory entry, on both Linux and macOS. If the second step fails,
        the helper restores the source spelling before propagating the error.
        A genuinely different destination is never overwritten.
        """
        source, destination = Path(source), Path(destination)
        if os.path.abspath(os.fspath(source)) == os.path.abspath(os.fspath(destination)):
            raise MutationRefused(f"source and destination are identical: {source}")
        if not source.is_file():
            raise MutationRefused(f"missing rename source: {source}")
        if destination_conflicts(source, destination):
            raise MutationRefused(f"destination already exists: {destination}")

        destination.parent.mkdir(parents=True, exist_ok=True)
        if not is_case_or_normalization_only_rename(source, destination):
            # Repeat immediately before os.rename so a preflight performed
            # earlier in the run is not the only collision guard.
            if destination.exists():
                raise MutationRefused(f"destination already exists: {destination}")
            self._rename_step(source, destination, purpose)
            return

        temporary = source.with_name(f".{source.name}.mutation-{uuid.uuid4().hex}.tmp")
        while temporary.exists():
            temporary = source.with_name(f".{source.name}.mutation-{uuid.uuid4().hex}.tmp")
        self.engine.journal.append({
            "type": "case_rename_intent",
            "run_id": self.engine.run_id,
            "index": self.index,
            "source": str(source),
            "temporary": str(temporary),
            "destination": str(destination),
            "purpose": purpose,
        })

        def restore_interrupted_case_step() -> None:
            if not literal_path_is_file(temporary):
                return
            if destination_conflicts(temporary, source):
                raise MutationVerificationError(
                    f"cannot restore interrupted case-only rename; original path is occupied: {source}"
                )
            self._rename_step(temporary, source, f"{purpose}:rollback-temp-to-source")

        # If the process remains alive but step two fails, the transaction's
        # normal rollback must know about the temporary entry as well as the
        # caller's tag/move rollback. Callbacks run in reverse registration
        # order so this repair happens before the caller restores metadata.
        self.on_rollback(restore_interrupted_case_step)
        self._rename_step(source, temporary, f"{purpose}:case-to-temp")
        try:
            if destination.exists():
                raise MutationRefused(f"destination became occupied during case-only rename: {destination}")
            self._rename_step(temporary, destination, f"{purpose}:temp-to-case")
        except BaseException:
            restore_interrupted_case_step()
            raise

    def _folder_restorer(self, source: Path, destination: Path, temporary: Path | None, purpose: str):
        """Return a callback that puts the folder back under its original name.

        It decides from the literal directory entries actually on disk, so it
        is correct after a failed second step, after a verified-then-rejected
        rename, and when called twice.
        """
        def restore() -> None:
            names = literal_entry_names(source.parent)
            if source.name in names:
                return
            if temporary is not None and temporary.name in names:
                self._rename_step(temporary, source, f"{purpose}:rollback-temp-to-source", exclusive=True)
                return
            if destination.name in names:
                if temporary is not None:
                    self._rename_step(destination, temporary, f"{purpose}:rollback-case-to-temp", exclusive=True)
                    self._rename_step(temporary, source, f"{purpose}:rollback-temp-to-source", exclusive=True)
                else:
                    self._rename_step(destination, source, f"{purpose}:rollback-destination-to-source", exclusive=True)
                return
            raise MutationVerificationError(
                f"cannot restore folder {source}: neither the original, temporary nor "
                f"destination entry exists"
            )
        return restore

    def rename_dir_no_overwrite(self, source: Path, destination: Path, *, purpose: str = "folder_rename") -> None:
        """Rename one directory in place without overwriting or trusting aliases.

        The source and destination must share a parent. Existence is decided
        from literal directory entries, never ``Path.exists()``, because on
        APFS ``R&B`` "exists" whenever ``R&b`` does. A case- or normalization-
        only change always goes through a unique temporary name (two journaled
        physical renames), on Linux and macOS alike. Registers its own
        rollback so a failure at any point puts the original name back.
        """
        source, destination = Path(source), Path(destination)
        case_only = check_folder_rename(source, destination)
        temporary = None
        if case_only:
            temporary = source.with_name(f".{source.name}.mutation-{uuid.uuid4().hex}.tmp")
            while os.path.lexists(temporary):
                temporary = source.with_name(f".{source.name}.mutation-{uuid.uuid4().hex}.tmp")
            self.engine.journal.append({
                "type": "case_rename_intent",
                "run_id": self.engine.run_id,
                "index": self.index,
                "source": str(source),
                "temporary": str(temporary),
                "destination": str(destination),
                "purpose": purpose,
            })
        restore = self._folder_restorer(source, destination, temporary, purpose)
        self.on_rollback(restore)
        if temporary is None:
            # Repeat immediately before os.rename so the preflight is not the
            # only collision guard.
            if os.path.lexists(destination):
                raise MutationRefused(f"destination already exists: {destination}")
            self._rename_step(source, destination, purpose, exclusive=True)
            return
        self._rename_step(source, temporary, f"{purpose}:case-to-temp", exclusive=True)
        try:
            if os.path.lexists(destination):
                raise MutationRefused(f"destination became occupied during case-only rename: {destination}")
            self._rename_step(temporary, destination, f"{purpose}:temp-to-case", exclusive=True)
        except BaseException:
            restore()
            raise

    def do_rollback(self, reason: str) -> None:
        restored = False
        error = None
        if self._rollbacks and self.mutated:
            restored = True
            errors = []
            for callback in reversed(self._rollbacks):
                try:
                    callback()
                except Exception as exc:  # noqa: BLE001 - all failures recorded
                    restored = False
                    errors.append(f"{type(exc).__name__}: {exc}")
            error = "; ".join(errors) or None
        self.engine.journal.append({
            "type": "rolled_back",
            "run_id": self.engine.run_id,
            "index": self.index,
            "source": self.intent.get("source"),
            "reason": reason,
            "restored": restored,
            "rollback_error": error,
        })


class MutationEngine:
    """Drives a batch of mutations under lock, backup evidence and a WAL.

    Usage from an apply script::

        engine = MutationEngine(journal_path, heartbeat, manifest, source_root, label)
        for index, item in enumerate(items, start=1):
            with engine.item(index, kind=GENRE, source=path, expected_sha256=item["sha256"]) as txn:
                txn.on_rollback(lambda: shutil.copy2(backup, path))
                write_genre(path, new_genre)
                txn.mark_mutated()
                verify(...)            # raise on any mismatch
                txn.record(old_genre=..., new_genre=...)

    The context manager does the heartbeat, the precondition checks, the
    backup proof, the intent record, the rollback-on-exception and the
    completion record. The caller supplies only what is specific to its
    mutation class.
    """

    def __init__(
        self,
        journal_path: Path,
        heartbeat,
        manifest: dict | None,
        source_root: Path | None,
        label: str,
        run_id: str | None = None,
        allow_unbacked: bool = False,
    ):
        self.journal = RecoveryJournal(journal_path)
        self.heartbeat = heartbeat
        self.manifest = manifest
        self.source_root = Path(source_root) if source_root else None
        self.label = label
        self.run_id = run_id or datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-") + os.urandom(4).hex()
        # allow_unbacked exists only for undo/recovery paths that independently
        # re-verify the apply run's recorded backup hash, plus synthetic engine
        # tests. Production APPLY callers must pass a manifest; run_preflight()
        # enforces that gate.
        self.allow_unbacked = allow_unbacked
        self.completed: list[dict] = []
        self._started = False

    def start(self, plan_summary: dict) -> None:
        """Journal the run header. Call once, before the first item."""
        if self.manifest is None and not self.allow_unbacked:
            raise MutationRefused(
                "MutationEngine requires a verified backup manifest for production mutation"
            )
        self.journal.append({
            "type": "run_start",
            "journal_version": JOURNAL_VERSION,
            "run_id": self.run_id,
            "label": self.label,
            "source_root": str(self.source_root) if self.source_root else None,
            "backup_id": (self.manifest or {}).get("backup_id"),
            "backup_manifest_sha256": (self.manifest or {}).get("manifest_sha256"),
            "plan": plan_summary,
        })
        self._started = True

    def finish(self, result: dict) -> None:
        self.journal.append({
            "type": "run_complete",
            "run_id": self.run_id,
            "items_completed": len(self.completed),
            "result": result,
        })

    @contextlib.contextmanager
    def item(
        self,
        index: int,
        kind: str,
        source: Path,
        expected_sha256: str | None = None,
        destination: Path | None = None,
        backup_path: Path | None = None,
        require_backup: bool = True,
        extra_intent: dict | None = None,
    ):
        if kind not in MUTATION_CLASSES:
            raise MutationRefused(
                f"{kind!r} is not an approved mutation class "
                f"(approved: {sorted(MUTATION_CLASSES)})"
            )
        if not self._started:
            raise MutationRefused("MutationEngine.start() must be called before the first item")

        source = Path(source)

        # 1. Ownership, before anything else. Raises SystemExit if lost.
        if self.heartbeat is not None:
            self.heartbeat.beat()

        # 2. Source preconditions.
        if not source.is_file():
            raise MutationRefused(f"missing source: {source}")
        actual = sha256_file(source)
        if expected_sha256 is not None and actual != expected_sha256:
            raise MutationRefused(
                f"source hash changed since preview, refusing to mutate: {source} "
                f"({actual} != {expected_sha256})"
            )

        # 3. Backup evidence for this exact file.
        backup_sha = None
        if require_backup:
            if self.manifest is not None and self.source_root is not None:
                backup_sha, manifest_backup = _prove_manifest_backup(
                    self.manifest, self.source_root, source, actual
                )
                if backup_path is None:
                    backup_path = manifest_backup
            elif not self.allow_unbacked:
                raise MutationRefused(
                    f"no backup manifest available to authorize mutating {source}"
                )
        if backup_path is not None:
            backup_path = Path(backup_path)
            if not backup_path.is_file():
                raise MutationRefused(f"per-item backup copy missing: {backup_path}")
            backup_path_sha = sha256_file(backup_path)
            if backup_path_sha != actual:
                raise MutationRefused(f"per-item backup copy hash mismatch: {backup_path}")
            if backup_sha is not None and backup_path_sha != backup_sha:
                raise MutationRefused(
                    f"per-item backup copy no longer matches manifest evidence: {backup_path}"
                )

        intent = {
            "type": "intent",
            "run_id": self.run_id,
            "index": index,
            "kind": kind,
            "source": str(source),
            "destination": str(destination) if destination else None,
            "before_sha256": actual,
            "backup_path": str(backup_path) if backup_path else None,
            "backup_sha256": backup_sha,
            "backup_id": (self.manifest or {}).get("backup_id"),
            **(extra_intent or {}),
        }
        # 4. Durable intent BEFORE the mutation.
        self.journal.append(intent)

        txn = _ItemTransaction(self, index, intent)
        try:
            yield txn
        except BaseException as exc:  # noqa: BLE001 - journal, restore, re-raise
            txn.do_rollback(f"{type(exc).__name__}: {exc}")
            raise
        # 8. Durable completion record.
        completion = {
            "type": "completed",
            "run_id": self.run_id,
            "index": index,
            "kind": kind,
            "source": str(source),
            "destination": str(destination) if destination else None,
            "before_sha256": actual,
            "backup_path": str(backup_path) if backup_path else None,
            "mutated": txn.mutated,
            **txn.outcome,
        }
        self.journal.append(completion)
        self.completed.append(completion)


    @contextlib.contextmanager
    def folder_item(
        self,
        index: int,
        source: Path,
        destination: Path,
        *,
        expected_inventory: dict,
        guard_files=(),
        extra_intent: dict | None = None,
    ):
        """One journaled whole-folder rename.

        ``expected_inventory`` is ``{"sha256": ..., "hash_contents": bool}`` from
        ``directory_inventory()`` at preview time: the folder is refused if its
        contents drifted. ``guard_files`` are the data this rename can orphan
        (for the library: Serato's database and crates); each is a dict with
        ``path`` and optionally ``manifest``/``source_root`` (defaulting to the
        engine's) and must have verified backup evidence that still matches the
        live bytes. After the body runs the engine itself re-checks that the
        destination spelling is the literal entry, the old spelling is gone, and
        the inventory is unchanged -- otherwise the folder is renamed back.
        """
        if not self._started:
            raise MutationRefused("MutationEngine.start() must be called before the first item")
        source, destination = Path(source), Path(destination)
        if self.heartbeat is not None:
            self.heartbeat.beat()

        expected_sha = (expected_inventory or {}).get("sha256")
        hash_contents = bool((expected_inventory or {}).get("hash_contents"))
        if not expected_sha:
            raise MutationRefused("folder rename requires the preview's expected inventory digest")
        check_folder_rename(source, destination)
        inventory = directory_inventory(source, hash_contents=hash_contents)
        if inventory["sha256"] != expected_sha:
            raise MutationRefused(
                f"folder contents changed since preview, refusing to rename: {source} "
                f"({inventory['sha256']} != {expected_sha})"
            )

        guards = []
        for guard in guard_files:
            guard_path = Path(guard["path"])
            manifest = guard.get("manifest") or self.manifest
            root = guard.get("source_root") or self.source_root
            if manifest is None or root is None:
                raise MutationRefused(f"no backup manifest available to authorize renaming around {guard_path}")
            if not guard_path.is_file():
                raise MutationRefused(f"missing guarded file: {guard_path}")
            actual = sha256_file(guard_path)
            backup_sha, backup_copy = _prove_manifest_backup(manifest, Path(root), guard_path, actual)
            guards.append({
                "path": str(guard_path),
                "sha256": actual,
                "backup_path": str(backup_copy),
                "backup_sha256": backup_sha,
                "backup_id": manifest.get("backup_id"),
            })
        if not guards and not self.allow_unbacked:
            raise MutationRefused(
                "folder rename requires verified backup evidence of the data it can orphan "
                "(no guard files were supplied)"
            )

        intent = {
            "type": "intent",
            "run_id": self.run_id,
            "index": index,
            "kind": FOLDER_RENAME,
            "source": str(source),
            "destination": str(destination),
            "before_sha256": inventory["sha256"],
            "inventory": inventory,
            "backup_path": None,
            "backup_sha256": None,
            "backup_id": (self.manifest or {}).get("backup_id"),
            "guard_backups": guards,
            **(extra_intent or {}),
        }
        self.journal.append(intent)

        txn = _ItemTransaction(self, index, intent)
        try:
            yield txn
            if not txn.mutated:
                raise MutationVerificationError(f"planned folder rename did not happen: {source}")
            if not literal_path_is_dir(destination):
                raise MutationVerificationError(f"renamed folder is not a literal directory entry: {destination}")
            if source.name in literal_entry_names(source.parent):
                raise MutationVerificationError(f"old folder spelling still present after rename: {source}")
            after = directory_inventory(destination, hash_contents=hash_contents)
            if after["sha256"] != expected_sha:
                raise MutationVerificationError(
                    f"folder contents differ after rename: {destination} "
                    f"({after['file_count']} files vs {inventory['file_count']} before)"
                )
            txn.record(after_inventory=after)
        except BaseException as exc:  # noqa: BLE001 - journal, restore, re-raise
            txn.do_rollback(f"{type(exc).__name__}: {exc}")
            raise
        completion = {
            "type": "completed",
            "run_id": self.run_id,
            "index": index,
            "kind": FOLDER_RENAME,
            "source": str(source),
            "destination": str(destination),
            "before_sha256": inventory["sha256"],
            "backup_path": None,
            "mutated": txn.mutated,
            **txn.outcome,
        }
        self.journal.append(completion)
        self.completed.append(completion)


def restore_from_backup(backup: Path, target: Path, expected_sha256: str) -> None:
    """Standard rollback: copy the verified backup back and prove it landed."""
    backup, target = Path(backup), Path(target)
    if sha256_file(backup) != expected_sha256:
        raise MutationVerificationError(
            f"refusing to roll back from a backup whose hash does not match the pre-mutation "
            f"state: {backup}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, target)
    if sha256_file(target) != expected_sha256:
        raise MutationVerificationError(f"rollback verification failed: {target}")


def recover(journal_path: Path) -> dict:
    """Report what a crashed run left behind.

    An item is IN-FLIGHT if the journal has its intent record but no
    completed/rolled_back record. Those are the items a human must inspect or
    restore. `mutated` says whether the physical write had already happened
    when the process died, which is the difference between "nothing to do" and
    "restore this from its backup".

    An item is settled by a `completed` record, or by a `rolled_back` record
    that reports `restored: true`. A `rolled_back` record with
    `restored: false` after a `mutated` marker means the engine mutated the
    file, verification failed, and the automatic restore then failed too (a
    corrupted backup, a permissions error, a backup copy that vanished). The
    file is on disk in its broken mutated state and needs a human. Those items
    are reported with `state: "ROLLBACK_FAILED"` and the original
    `rollback_error`. If no mutation marker and no rollback error exist, the
    item is instead reported as `ABORTED_BEFORE_MUTATION`: still visible for
    hash confirmation, but never mislabeled as a failed restore.
    """
    journal = RecoveryJournal(Path(journal_path))
    records = journal.read()
    intents: dict[tuple, dict] = {}
    mutated: set[tuple] = set()
    settled: set[tuple] = set()
    failed_rollbacks: dict[tuple, dict] = {}
    prewrite_aborts: dict[tuple, dict] = {}
    case_renames: dict[tuple, dict] = {}
    rename_steps: dict[tuple, list] = {}
    torn = 0
    runs: dict[str, dict] = {}

    for record in records:
        kind = record.get("type")
        if kind == "torn_record":
            torn += 1
            continue
        if kind == "run_start":
            runs[record.get("run_id")] = {"started": record.get("ts"), "complete": False}
            continue
        if kind == "run_complete":
            runs.setdefault(record.get("run_id"), {})["complete"] = True
            continue
        key = (record.get("run_id"), record.get("index"))
        if kind == "intent":
            intents[key] = record
        elif kind == "case_rename_intent":
            case_renames[key] = record
        elif kind == "rename_step_completed":
            rename_steps.setdefault(key, []).append(
                [record.get("source"), record.get("destination"), record.get("purpose")]
            )
        elif kind == "mutated":
            mutated.add(key)
        elif kind == "completed":
            settled.add(key)
        elif kind == "rolled_back":
            # A rollback record only settles an item if the rollback actually
            # RESTORED it. do_rollback() also uses this record shape for an
            # exception before mark_mutated(), when no restore is attempted;
            # that must stay visible without being called ROLLBACK_FAILED.
            # A real failed restore remains the single most urgent thing a
            # human needs to see, so it stays in-flight under that state.
            if record.get("restored"):
                settled.add(key)
            elif key not in mutated and not record.get("rollback_error"):
                # The transaction raised after its durable intent but before
                # mark_mutated(), so no restore was attempted or needed. Keep
                # it visible for hash verification, but do not call it a
                # rollback failure: no rollback took place and none failed.
                prewrite_aborts[key] = record
            else:
                failed_rollbacks[key] = record

    in_flight = []
    for key, intent in intents.items():
        if key in settled:
            continue
        failed = failed_rollbacks.get(key)
        prewrite_abort = prewrite_aborts.get(key)
        entry = {
            "run_id": intent.get("run_id"),
            "index": intent.get("index"),
            "kind": intent.get("kind"),
            "source": intent.get("source"),
            "destination": intent.get("destination"),
            "before_sha256": intent.get("before_sha256"),
            "backup_path": intent.get("backup_path"),
            "backup_sha256": intent.get("backup_sha256"),
            "backup_id": intent.get("backup_id"),
            "mutation_had_started": key in mutated,
            # A whole-folder rename has no file backup: renaming it back, checked
            # against the inventory digest in before_sha256, is its restore.
            "restorable": bool(
                intent.get("backup_path") or intent.get("backup_sha256")
                or intent.get("kind") == FOLDER_RENAME
            ),
            "rollback_attempted": failed is not None,
            "rollback_error": failed.get("rollback_error") if failed else None,
            "failure_reason": (
                failed.get("reason") if failed else
                prewrite_abort.get("reason") if prewrite_abort else None
            ),
        }
        case_rename = case_renames.get(key)
        if case_rename is not None:
            # A crash between the two physical renames can leave the file at
            # this unique temporary path. Surface it explicitly so recovery
            # never asks a human to guess where the bytes went.
            entry["case_rename_temporary_path"] = case_rename.get("temporary")
            entry["case_rename_final_path"] = case_rename.get("destination")
        if intent.get("kind") == FOLDER_RENAME:
            entry["rename_steps_completed"] = rename_steps.get(key, [])
        if failed is not None:
            entry["state"] = "ROLLBACK_FAILED"
            entry["action_required"] = (
                "The mutation was performed, verification failed, and the automatic "
                "restore ALSO failed. This file is on disk in its mutated state. "
                "Restore it manually from backup_path and confirm it hashes to "
                "before_sha256 before doing anything else with this folder."
            )
        elif prewrite_abort is not None:
            entry["state"] = "ABORTED_BEFORE_MUTATION"
            entry["action_required"] = (
                "The item failed after its intent was journaled, but no mutation marker "
                "was written and no rollback was attempted. Confirm the source still "
                "hashes to before_sha256; no restore is needed if it does."
            )
        else:
            entry["state"] = "INTERRUPTED"
            entry["action_required"] = (
                "The run stopped between the intent record and the completion record. "
                "Compare the file against before_sha256 and restore from backup_path "
                "if it was already mutated."
            )
        in_flight.append(entry)
    in_flight.sort(key=lambda entry: (str(entry["run_id"]), entry["index"] or 0))

    rollback_failures = [entry for entry in in_flight if entry["state"] == "ROLLBACK_FAILED"]
    return {
        "journal": str(journal_path),
        "records": len(records),
        "torn_records": torn,
        "runs": runs,
        "items_intended": len(intents),
        "items_settled": len(settled),
        "in_flight": in_flight,
        "rollback_failures": len(rollback_failures),
        "clean": not in_flight and torn == 0,
    }


def run_preflight(
    *,
    lock_folder: str,
    session: str,
    manifest_path: Path,
    source_root: Path,
    serato_required: bool = False,
    stale_hours: float = 6.0,
) -> tuple[lock_guard.LockHeartbeat, dict]:
    """The single entry point every production apply script calls first.

    Proves, in order: the lock is owned right now, and the backup manifest
    PASSes for this exact source root. Returns a heartbeat pinned to the lock
    instance that was just proven, plus the verified manifest.

    Either failure aborts before a single file is touched -- SystemExit for
    the lock (so the CLI exit is unambiguous), BackupGateError for the backup.
    """
    lock = lock_guard.require_lock_or_exit(lock_folder, session, stale_hours)
    heartbeat = lock_guard.LockHeartbeat(lock_folder, session, stale_hours)
    heartbeat.pin_run_id(lock)
    manifest = backup_manifest.require_pass(
        Path(manifest_path), Path(source_root), serato_required=serato_required
    )
    return heartbeat, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p_recover = sub.add_parser("recover", help="report in-flight items from a crashed run")
    p_recover.add_argument("--journal", required=True, type=Path)
    args = parser.parse_args()

    report = recover(args.journal)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["clean"] else 2


if __name__ == "__main__":
    sys.exit(main())
