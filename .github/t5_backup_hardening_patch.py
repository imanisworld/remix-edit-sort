#!/usr/bin/env python3
"""One-shot hardening patch for T5 backup independence and index locking."""
from pathlib import Path


def patch_manifest() -> None:
    path = Path("scripts/backup_manifest.py")
    source = path.read_text(encoding="utf-8")

    old = '''            backup_path = backup_path.resolve()
            backup_hash = sha256_file(backup_path)
'''
    new = '''            try:
                same_file = __import__("os").path.samefile(source_path, backup_path)
            except OSError as exc:
                problems.append(
                    f"could not prove backup independence for {raw_relative}: {exc}"
                )
                continue
            if same_file:
                problems.append(
                    f"explicit backup is the source file itself, not an independent copy: {raw_relative}"
                )
                continue

            backup_path = backup_path.resolve()
            backup_hash = sha256_file(backup_path)
'''
    if source.count(old) != 1:
        raise SystemExit(f"expected one explicit backup hash block, found {source.count(old)}")
    source = source.replace(old, new, 1)

    old = '    if manifest.get("scope_mode") == "explicit-backup-paths" or "backup_path" in entry:\n'
    new = '    if manifest.get("scope_mode") == "explicit-backup-paths":\n'
    if source.count(old) != 1:
        raise SystemExit("explicit resolver scope-mode guard not found")
    source = source.replace(old, new, 1)
    path.write_text(source, encoding="utf-8")


def patch_helper() -> None:
    path = Path("scripts/t5_backup_dedupe.py")
    source = path.read_text(encoding="utf-8")

    old = '''import argparse
import hashlib
'''
    new = '''import argparse
from contextlib import contextmanager
import fcntl
import hashlib
'''
    if source.count(old) != 1:
        raise SystemExit("helper import insertion point not found")
    source = source.replace(old, new, 1)

    marker = '''def sha256_file(path: Path) -> str:
'''
    lock_helper = '''@contextmanager
def _index_lock(index_path: Path):
    # Serialize index refresh/copy/publication across concurrent backup lanes.
    # flock is released automatically if the process exits or crashes.
    index_path = Path(index_path).resolve()
    lock_path = index_path.with_name(index_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError as exc:
            raise BackupDedupeError(
                f"could not acquire T5 index lock {lock_path}: {exc}"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


'''
    if source.count(marker) != 1:
        raise SystemExit("sha256 helper insertion point not found")
    source = source.replace(marker, lock_helper + marker, 1)

    old = 'def _verified_reuse(index: dict, digest: str) -> Path | None:\n'
    new = 'def _verified_reuse(index: dict, digest: str, source_path: Path | None = None) -> Path | None:\n'
    if source.count(old) != 1:
        raise SystemExit("verified reuse signature not found")
    source = source.replace(old, new, 1)

    old = '''            if not path.is_file():
                raise BackupDedupeError(f"indexed T5 candidate is missing: {path}")
            actual = sha256_file(path)
'''
    new = '''            if not path.is_file():
                raise BackupDedupeError(f"indexed T5 candidate is missing: {path}")
            if source_path is not None:
                try:
                    if os.path.samefile(path, source_path):
                        # The live source cannot serve as its own recovery copy.
                        continue
                except OSError as exc:
                    raise BackupDedupeError(
                        f"could not prove backup independence for {path}: {exc}"
                    ) from exc
            actual = sha256_file(path)
'''
    if source.count(old) != 1:
        raise SystemExit("verified reuse candidate block not found")
    source = source.replace(old, new, 1)

    old = '''    verified.sort(key=lambda pair: (_path_key(pair[0]), pair[0]))
    return verified[0][1]
'''
    new = '''    verified.sort(key=lambda pair: (_path_key(pair[0]), pair[0]))
    return verified[0][1] if verified else None
'''
    if source.count(old) != 1:
        raise SystemExit("verified reuse return block not found")
    source = source.replace(old, new, 1)

    old = 'def prepare(\n'
    new = 'def _prepare_unlocked(\n'
    if source.count(old) != 1:
        raise SystemExit(f"expected one prepare definition, found {source.count(old)}")
    source = source.replace(old, new, 1)

    old = '        backup_path = _verified_reuse(index, source_hash)\n'
    new = '        backup_path = _verified_reuse(index, source_hash, source_resolved)\n'
    if source.count(old) != 1:
        raise SystemExit("prepare reuse call not found")
    source = source.replace(old, new, 1)

    marker = '''def _require_index_outside_root(index_path: Path, root: Path) -> None:
'''
    wrapper = '''def prepare(
    *,
    index_path: Path,
    source_root: Path,
    lane_backup_root: Path,
    manifest_out: Path,
    paths_json: Path | None = None,
    backup_id: str | None = None,
) -> dict:
    # Validate placement before creating the adjacent lock file, then reload
    # the index under the lock inside _prepare_unlocked.
    probe = load_index(index_path)
    _require_index_outside_root(index_path, Path(probe["root"]))
    with _index_lock(index_path):
        return _prepare_unlocked(
            index_path=index_path,
            source_root=source_root,
            lane_backup_root=lane_backup_root,
            manifest_out=manifest_out,
            paths_json=paths_json,
            backup_id=backup_id,
        )


'''
    if source.count(marker) != 1:
        raise SystemExit("prepare wrapper insertion point not found")
    source = source.replace(marker, wrapper + marker, 1)

    # Serialize explicit index/refresh CLI operations with prepare() as well.
    old = '''        if args.command == "index":
            _require_index_outside_root(args.out, args.root)
            index = build_index(args.root)
            write_index(index, args.out)
            print(
'''
    new = '''        if args.command == "index":
            _require_index_outside_root(args.out, args.root)
            with _index_lock(args.out):
                index = build_index(args.root)
                write_index(index, args.out)
            print(
'''
    if source.count(old) != 1:
        raise SystemExit("index CLI block not found")
    source = source.replace(old, new, 1)

    old = '''        if args.command == "refresh":
            index = load_index(args.index)
            _require_index_outside_root(args.index, Path(index["root"]))
            index = refresh_index(index)
            write_index(index, args.index)
            print(
'''
    new = '''        if args.command == "refresh":
            probe = load_index(args.index)
            _require_index_outside_root(args.index, Path(probe["root"]))
            with _index_lock(args.index):
                index = load_index(args.index)
                _require_index_outside_root(args.index, Path(index["root"]))
                index = refresh_index(index)
                write_index(index, args.index)
            print(
'''
    if source.count(old) != 1:
        raise SystemExit("refresh CLI block not found")
    source = source.replace(old, new, 1)

    path.write_text(source, encoding="utf-8")


def patch_tests() -> None:
    path = Path("tests/test_t5_backup_dedupe.py")
    source = path.read_text(encoding="utf-8")
    marker = '''    def test_duplicate_same_hash_candidates_choose_deterministically(self):
'''
    tests = '''    def test_source_file_itself_is_never_accepted_as_backup(self):
        self.source = self.t5 / "live-library"
        self.source.mkdir()
        source = self._write_source("song.mp3", b"source-on-t5")
        self._build_index()

        report = self._prepare()

        copied = self.lane / "song.mp3"
        self.assertEqual(report["reused_index"], 0)
        self.assertEqual(report["copied"], 1)
        self.assertTrue(copied.is_file())
        self.assertFalse(os.path.samefile(source, copied))
        manifest = backup_manifest.require_pass(self.manifest_path, self.source)
        self.assertEqual(
            backup_manifest.backup_path_for_item(manifest, self.source, source),
            copied.resolve(),
        )

    def test_explicit_manifest_rejects_source_as_its_own_backup(self):
        source = self._write_source("song.mp3", b"payload")
        self.lane.mkdir()
        manifest = backup_manifest.build_explicit_paths_manifest(
            self.source,
            self.lane,
            [{
                "path": "song.mp3",
                "backup_path": str(source.resolve()),
                "backup_mode": "bad-self-reference",
            }],
        )
        self.assertEqual(manifest["status"], "FAIL")
        self.assertTrue(
            any("source file itself" in problem for problem in manifest["problems"])
        )

    def test_explicit_backup_symlink_substitution_is_refused(self):
        source = self._write_source("song.mp3", b"payload")
        backup = self._write_t5("archive/song.mp3", source.read_bytes())
        self.lane.mkdir()
        manifest = backup_manifest.build_explicit_paths_manifest(
            self.source,
            self.lane,
            [{
                "path": "song.mp3",
                "backup_path": str(backup.resolve()),
                "backup_mode": "reused-index",
            }],
        )
        self.assertEqual(manifest["status"], "PASS")
        backup.unlink()
        backup.symlink_to(source)
        with self.assertRaises(backup_manifest.BackupGateError):
            backup_manifest.backup_path_for_item(manifest, self.source, source)

'''
    if source.count(marker) != 1:
        raise SystemExit("test insertion point not found")
    source = source.replace(marker, tests + marker, 1)
    path.write_text(source, encoding="utf-8")


def patch_handoff() -> None:
    path = Path("handoff/CURRENT-STATUS.md")
    source = path.read_text(encoding="utf-8")
    old = "A missing or changed indexed path, a symlink, a hash mismatch, an index-integrity failure, or source drift fails closed."
    new = "A missing or changed indexed path, a symlink, a hash mismatch, an index-integrity failure, source drift, or any attempt to use the live source itself as its backup fails closed. Shared-index operations are serialized with an advisory file lock so parallel lanes cannot overwrite each other's index updates."
    if source.count(old) != 1:
        raise SystemExit("handoff T5 failure-policy sentence not found")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


if __name__ == "__main__":
    patch_manifest()
    patch_helper()
    patch_tests()
    patch_handoff()
