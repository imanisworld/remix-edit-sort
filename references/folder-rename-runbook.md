# ADR-003: Whole-folder renames and the Serato stores that follow them

**Status:** Accepted (tooling only; the first live run needs the library owner's separate go)
**Date:** 2026-10-15
**Builds on:** ADR-002 (`serato-path-rewrite-architecture.md`)

## Context

Renaming a top-level library folder (`R&b` -> `R&B`, `Afrobeat` -> `Afrobeats`)
moves thousands of tracks in one operation and orphans every Serato path that
points into it. Three things made the existing tooling insufficient:

1. **No folder-rename tool.** `mutation_engine` only knew single *files*.
2. **Case-only renames lie on APFS.** `R&B` "exists" whenever `R&b` does, so
   `Path.exists()`/`is_dir()` cannot tell a collision from the folder itself, and
   `os.rename('R&b', 'R&B')` is a silent no-op. The same alias makes
   `Path('.../R&B/x.mp3').is_file()` true while the folder is still `R&b`, so
   ADR-002's per-file target check could not notice an un-done rename.
3. **Serato keeps paths in more than `database V2` and `Subcrates/`.** Read from
   *copies* of the live files on 2026-10-15:

   | Store | Holds old-prefix paths | Covered by |
   | --- | --- | --- |
   | `database V2` + `Subcrates/*.crate` | 4,311 refs / 35 files | `rewrite_serato_paths.py` (ADR-002) |
   | `Library/master.sqlite` `asset.portable_id` | 3,473 `R&b` + 351 `Afrobeat` | **`rewrite_serato_sqlite_paths.py`** |
   | `Library/root.sqlite` `asset.portable_id` | 3,473 `R&b` + 351 `Afrobeat` | **`rewrite_serato_sqlite_paths.py`** |
   | `master.sqlite` `history_entry.portable_id` (play history) | 257 rows match the rename map | left alone, counted in the preview |
   | `_Serato_/SmartCrates/*.scrate` cached `ptrk` | 8,178 `Music/R&b/` + 902 `Music/Afrobeat/` byte-string hits | **not covered** (see Open questions) |
   | `_Serato_/History/*.session`, `Logs/` | 453 + 29 / 237 + 12 byte-string hits | not covered; history, by design |

## Decision

* Add one engine primitive for folders, not a second mutation path:
  `MutationEngine.folder_item()` + `_ItemTransaction.rename_dir_no_overwrite()`
  (mutation class `folder_rename`). It does the engine's usual per-item sequence
  (heartbeat, preconditions, backup proof, durable intent, verify, rollback,
  completion) for a directory.
  * Existence and collisions are decided from **literal directory entries**
    (`literal_path_is_dir`, `literal_entry_exists`, `literal_path_components_exist`),
    never from alias-following `exists()`.
  * A case- or normalization-only change always goes through a unique temporary
    name: two journaled physical renames, restored if the second fails, on Linux
    and macOS alike.
  * The folder is fingerprinted (`directory_inventory`: every entry's relative
    path, type, size, `mtime_ns`; SHA-256 too with `--hash-contents`; independent of
    the folder's own name; `.DS_Store` ignored). The engine refuses on drift since
    the preview and re-checks the fingerprint, the literal destination spelling and
    the absence of the old spelling after the rename, renaming back otherwise.
  * "Backup" for a folder rename is the evidence for what the rename can orphan:
    every Serato database and crate (and optionally the SQLite libraries) must have a
    PASS manifest entry that still matches the live bytes and whose concrete backup
    copy still hashes correctly. The folder's own audio is not copied; the rename is
    reversible and the fingerprint proves nothing inside changed.
* `scripts/apply_folder_rename_plan.py` (`engine-user`): `preview` (read-only,
  derives the exact Serato rename map and can report Serato impact), `apply`,
  `undo` (the apply run's recovery journal *is* its undo log; refuses while Serato
  still references the new names) and `recover` (restores a folder stranded by a
  crash from its source, temporary or destination name; dry-run by default).
* `scripts/rewrite_serato_sqlite_paths.py` (`engine-user`): rewrites only
  `asset.portable_id`, for `master.sqlite` and `root.sqlite`, with the same
  reviewed rename map. Read-only preview opens the database `immutable` (no `-wal`
  or `-shm` is created next to Serato's files), refuses an un-checkpointed WAL or
  hot journal, proves no unique index (`NOCASE`) collision, and binds the plan to the
  file hash plus a digest of every other cell. Apply is one SQLite transaction per
  database inside one engine item; verification is `integrity_check`, "all other
  cells unchanged", every row equals the plan, journal-mode header unchanged and
  every moved file present under its literal spelling. Failure restores the
  verified backup and moves any `-wal`/`-shm`/`-journal` aside (as
  `*.discarded-<id>`, never deleted) so a WAL is never replayed over a restore.
* `rewrite_serato_paths.py` gains `require_serato_closed()` (fails closed on a
  running `Serato*.app`, or an unreadable process table) and a literal check on
  destination *folders* in both the apply gate and the post-check.

## Approved sequence for the live run

Nothing here has been run on the live library. Needs the owner's explicit go.

```bash
# 0. Quit Serato completely. Work in ONE checkout: locks live in <checkout>/.locks.
# 1. Read-only previews. Build ALL of them first; the SQLite preview must be built
#    BEFORE the folders are renamed (it records which rows were already stale).
python3 scripts/apply_folder_rename_plan.py preview --library-root ~/Music/DJ-Music/Music \
    --folder-map folder-map.json --out folder-plan.json --serato-rename-map-out serato-map.json \
    --serato-dir ~/Music/_Serato_ --sqlite-db "$LIB/master.sqlite" --sqlite-db "$LIB/root.sqlite"
python3 scripts/rewrite_serato_paths.py preview --serato-dir ~/Music/_Serato_ \
    --rename-map serato-map.json --out binary-plan.json
python3 scripts/rewrite_serato_sqlite_paths.py preview --sqlite-db "$LIB/master.sqlite" \
    --sqlite-db "$LIB/root.sqlite" --rename-map serato-map.json --out sqlite-plan.json

# 2. Backups, then prove them (Serato dir, and the two SQLite files, scoped).
python3 scripts/backup_manifest.py verify --source-root ~/Music/_Serato_ --backup "$B/_Serato_" --out serato-manifest.json
python3 scripts/backup_manifest.py verify-scoped --source-root "$LIB" --backup "$B/Library" \
    --file master.sqlite --file root.sqlite --out sqlite-manifest.json

# 3. Locks: every name in required_lock_folders ("R&b", "Afrobeat", "Afrobeats", "Serato").
python3 scripts/folder_lock.py acquire --folder "R&b" --session S --agent claude   # ... x4

# 4. Rename the folders, then repoint Serato (each step is its own engine run).
python3 scripts/apply_folder_rename_plan.py apply --library-root ... --plan folder-plan.json \
    --serato-dir ~/Music/_Serato_ --backup-manifest serato-manifest.json \
    --sqlite-db "$LIB/master.sqlite" --sqlite-db "$LIB/root.sqlite" --sqlite-backup-manifest sqlite-manifest.json \
    --journal folders.jsonl --out folders-result.json --serato-rename-map-out serato-map-applied.json \
    --lock-folder "R&b" --lock-folder Afrobeat --lock-folder Afrobeats --lock-folder Serato \
    --session S --confirm-serato-closed
python3 scripts/rewrite_serato_paths.py apply ...        # ADR-002
python3 scripts/rewrite_serato_sqlite_paths.py apply --sqlite-db ... --plan sqlite-plan.json \
    --backup-manifest sqlite-manifest.json --session S --journal sqlite.jsonl --out sqlite-result.json \
    --confirm-serato-closed

# 5. python3 scripts/check_serato_crate_paths.py, release locks, open Serato, spot-check crates.
```

Back out: restore database V2, crates and both SQLite files from the verified
backups, then `apply_folder_rename_plan.py undo --journal folders.jsonl ...`.
After a crash: `apply_folder_rename_plan.py recover --journal <journal>` (dry run), then
`--apply --session S --lock-folder ... --confirm-serato-closed`.

## Open questions (need a human or a Serato experiment, not more code)

1. **Does Serato re-import `database V2` when it changes?** `root.sqlite` keeps
   `last_seen_dbv2_library(filename, size, md5_hash)` for `database V2` and every
   crate (the md5 of the live `database V2` matches it today) and `dbv2_status`
   with import/export revisions. After the rewrite those md5s no longer match, so
   Serato will likely re-import the binaries. The SQLite rows are rewritten to the
   same paths, so the import should find the same `portable_id`s and be a no-op, but
   this is inferred, not observed. The tool deliberately does not edit
   `last_seen_dbv2_library`. Try one small folder first, or take a spare copy of the
   Serato directories and open it.
2. **SmartCrates cache `ptrk` paths** (8,178 + 902 byte-string hits here). Whether Serato
   regenerates them from the rules on launch is unknown. The backlog item "extend
   `rewrite_serato_paths.py` to History + SmartCrates" would cover it; not done here.
3. **Five stale `master.sqlite`/`root.sqlite` rows** under `R&b/_NEEDS CHECK/R&B/`
   point at files that do not exist today. They are rewritten like any other row and
   reported as `rows_already_missing`; the rename can neither create nor fix them.
4. Locks are per checkout (`<checkout>/.locks`). A worktree does not share the main
   clone's locks; run the live sequence from the checkout every other session uses.
