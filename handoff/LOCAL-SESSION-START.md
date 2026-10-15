# Local session: start here (genre moves, tags, folder renames)

For an assistant running **on the owner's Mac** (Cursor or local Claude Code). Read `CLAUDE.md`, `SAFETY-MODEL.md` and `handoff/DJ-LIBRARY-HANDOFF-2026-10-15.md` first.

## The backup question, settled

The owner already has a backup on the **T5 EVO** drive. Only files added since then (new downloads) are not in it.

`scripts/t5_backup_dedupe.py` is built for exactly this. It fingerprints the T5 drive once. For each change it **reuses every file that is already backed up** (after re-checking its fingerprint) and **copies only the new ones**. Then it writes the PASS backup report that the apply tools require.

So there is no full re-backup. The new files get backed up in the same quick step, and the report is generated automatically. There is no flag that skips the report, and the code must not be edited to get around it.

## Before anything (once)

1. **The genre PRs are merged into `main`.** Merge top-down: #49 → #47 → #41 → #37. Then `git pull` on the Mac clone.
   - Clone path: `/Volumes/T5 EVO/DJ/Tools/remix-edit-sort` as of Aug 2026. **Confirm it's still current.**
2. **Index the T5 drive** (read-only; slow the first time):
   ```bash
   python3 scripts/t5_backup_dedupe.py index --root "/Volumes/T5 EVO" \
     --out ~/Documents/DJ-library-project-20260825/t5-backup-sha256-index.json
   ```
   Later, update it with `python3 scripts/t5_backup_dedupe.py refresh --index <same file>`.
3. **Mac test run (`MAC-CANARY.md`)** on a throwaway copy, never the live library. Required before the first real apply.

## Per folder: genre moves and tags (one folder at a time)

`LIB=/Users/djb.a.e/Music/DJ-Music/Music`. Confirm this path first.
`F` = the top-level folder, e.g. `Events`.
`W` = a work folder **outside** the music library, e.g. `~/Documents/DJ-library-project-20260825/runs/<folder>-<date>`.

```bash
# 1. Lock the folder (other sessions work this library too)
python3 scripts/folder_lock.py check   --folder "$F"
python3 scripts/folder_lock.py acquire --folder "$F" --session "$SESSION" --agent cursor

# 2. Read-only inventory, then a preview plan (moves nothing)
python3 scripts/inventory_library.py --root "$LIB/$F" --out-dir "$W/inventory"
python3 scripts/build_version_genre_plan.py --root "$LIB/$F" \
  --inventory "$W/inventory/inventory.csv" --out-dir "$W/plan"
#    -> SHOW THE OWNER THE PREVIEW. Stop here until they say go.

# 3. Backup: reuse existing T5 copies, copy only new files, write the PASS report
python3 scripts/t5_backup_dedupe.py prepare \
  --index ~/Documents/DJ-library-project-20260825/t5-backup-sha256-index.json \
  --source-root "$LIB/$F" \
  --lane-backup-root "/Volumes/T5 EVO/DJ/Backups/remix-edit-sort/<run-id>" \
  --manifest-out "$W/backup-manifest.json" --report-out "$W/backup-report.json"

# 4. Apply only after the owner approves the preview
python3 scripts/apply_version_genre_plan.py --root "$LIB/$F" --plan "$W/plan/plan.json" \
  --backup-manifest "$W/backup-manifest.json" --lock-folder "$F" --session "$SESSION" --apply

# 5. Owner spot-checks in Serato; then release the lock
python3 scripts/folder_lock.py release --folder "$F" --session "$SESSION"
```

Undo: `apply_version_genre_plan.py --undo <undo .jsonl from step 4>` (same `--root/--plan/--lock-folder/--session`).

**Approved folders (2026-10-15):** 59 Genres now have a destination, using the live folder spellings (e.g. Hip-Hop → `Hip Hop`, Afrobeats → `Afrobeat`, Deep House → `House`). The 17 still held (Dub, Breaks, Electro, Dubstep, Miami/Atlanta Bass, Afro Tech, Gqom, Kuduro, Afro Baile, New Wave, Drill styles) go to `_GENRE NEEDS CHECK` with no tag write. The full list is `folder_names` in `references/version-genre-rules.json`. `git pull` before the next plan.

To approve more later: add the entry to `folder_names` in **both** `references/genre-rules.json` and `references/version-genre-rules.json`, **and** to `APPROVED_FOLDER_NAMES` in `scripts/check_genre_naming.py`, then run the tests. Use the exact on-disk spelling.

A plain "Afrobeat" tag is read as Afrobeats (owner: "same thing"). "Dub" stays held.

**Junk Grouping removal** (the one exception to append-only): `scripts/build_grouping_removal_plan.py survey` lists every Grouping value with counts (read-only); the owner picks exact junk values into a text file; `... plan --remove <file>` builds the preview; apply with `apply_grouping_plan.py` like any Grouping write (lock, PASS backup, `--apply`). Steps are in `LIBRARY-STATUS.md`.

**Serato:** after tags are written, Serato shows the new Genre only once it re-reads the files. Select the tracks in Serato and use **Rescan ID3 Tags** (Files panel).

## Folder renames (e.g. `Afrobeat/` → `Afrobeats/`)

A rename changes file paths, so Serato's database and crates must be updated too, or tracks drop out of crates.

1. Decide the exact old → new folder pairs **with the owner**. Not decided yet: Afrobeat→Afrobeats, Ghetto Tech→Ghettotech, DNB→Drum & Bass, Hip Hop→Hip-Hop.
   - Do Hip Hop last; it has ~5,900 tracks.
   - Owner confirmed 2026-10-15: the `Afrobeat` folder is Afrobeats ("same thing").
2. Move the files with the per-folder steps above (approved destination, PASS backup).
3. **With Serato closed**, update Serato's paths:
   ```bash
   python3 scripts/rewrite_serato_paths.py preview --serato-dir /Users/djb.a.e/Music/_Serato_ \
     --rename-map "$W/rename-map.json" --out "$W/serato-preview.json"
   #   -> owner reviews the match counts
   python3 scripts/rewrite_serato_paths.py apply --serato-dir /Users/djb.a.e/Music/_Serato_ \
     --library-root "$LIB" --plan "$W/serato-preview.json" \
     --backup-manifest <PASS report covering the _Serato_ files> --session "$SESSION" \
     --journal "$W/serato-journal.jsonl" --post-check-out "$W/serato-post-check.json" --out "$W/serato-result.json" \
     --confirm-serato-closed
   ```

## Never
- Touch `Imani Drops`.
- Read or write rekordbox `master.db`.
- Delete anything automatically.
- Apply without the owner's OK on the preview.
- Edit the safety code to skip the backup report.
