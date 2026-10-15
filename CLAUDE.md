# Claude continuation instructions

Read LIBRARY-STATUS.md first. Any assistant that changes the library or this repo updates it before stopping, and commits it.

This repository contains the reusable remix/edit sorting skill plus the local handoff from the 2026-08-15 DJ-library cleanup.

Before changing anything:

1. Read `SAFETY-MODEL.md` completely. It is the authoritative one-page statement
   of how mutations work here; if any other doc contradicts it, it wins.
2. Read `SKILL.md` completely.
3. Read `handoff/HANDOFF-DJ-MUSIC-SORT.md` completely.
4. Treat completed folders as complete; do not rerun their old plans over sorted paths.
5. Survey one new source folder read-only and show a preview before any move or metadata write.
6. Require explicit user approval and a fresh **verified** music + `_Serato_` backup before
   applying — verified meaning a `backup_manifest.py` manifest that says PASS, not a
   directory that exists.

## The mutation safety model (enforced in code, not convention)

- **ALL mutations go through one approved engine**, `scripts/mutation_engine.py`.
  No apply script performs its own rename, tag write, or unlink.
- **The lock is mandatory** for `--apply` and `--undo`. Ownership is verified
  atomically inside the per-folder mutex (`folder_lock.verify_and_renew`).
- **A heartbeat runs before EVERY mutation**, not once at startup. Losing the
  lock stops the run before its next write.
- **A PASS backup manifest is mandatory.** `backup_manifest.py` computes PASS by
  hashing the source and backup trees; there is no flag that stamps PASS on a
  directory. The manifest must cover the exact source root, be unaltered, and
  carry per-file evidence for each file mutated.
- **Write-ahead recovery exists.** A durable intent record is fsync'd before each
  mutation and a completion record after it, so a crash mid-item is recoverable
  via `python3 scripts/mutation_engine.py recover --journal <path>`.
- **No automatic deletion.** Nothing deletes on its own.
  `delete_verified_duplicates.py` acts only on a human-approved pair list.
- **EXACT_DUPLICATE means identical SHA-256 only.** Filename/title/duration
  similarity is REVIEW at most. See `scripts/duplicate_semantics.py`.
- **`handoff/` is historical evidence and is never executable.** All its Python
  is archived as `*.py.archived`; CI fails if a `.py` reappears there. See
  `handoff/ARCHIVED-CODE.md`.
- **Read-only tooling requires no mutation lock** — audits, inventories,
  evidence checks, and every plan/preview builder.
- **A Mac canary run is required before the first real production apply**, because
  CI runs Linux and the library is on macOS/APFS. See `MAC-CANARY.md`.

Non-negotiable safety rules:

- Never delete or deduplicate from filenames alone. `EXACT_DUPLICATE` = identical SHA-256 over
  the whole file, and nothing else. Filename/title/duration similarity is REVIEW only.
- Preserve Clean, Dirty, Intro, Remix, Edit, Instrumental, Acapella, and other meaningful versions.
- Preserve meaningful numbers such as `21 Savage`, `112`, years, BPM, and part numbers.
- Do not infer uncertain genres, artists, titles, event names, or versions. Use needs-check classifications.
- Preserve audio payloads and embedded Serato frames; verify both after metadata writes.
- Preserve ID3v2.2 blocks unless tag conversion is separately previewed and approved.
- `Genre` means musical style. `Grouping` stores evidenced event/crate/source context and must append to—not overwrite—existing values.
- `Imani Drops` is explicitly out of scope and must remain untouched.

Current important state:

- Events version/genre organization is complete for 3,046 tracks.
- Events Grouping backfill is complete for 3,010 MP3s; 36 unsupported/old-tag files were safely skipped.
- The folder label `NIGER` is a mistake and means **Nigeria**. Its two-track plan is applied: Grouping is `Source: Nigeria` on both; Genre and Artist remain unknown (not guessed). See `handoff/pending/nigeria-plan.json`, the applied result, and undo log in `handoff/pending/`.
- The nine one-off per-folder apply scripts are superseded by a reusable pipeline: `scripts/inventory_library.py`, `scripts/build_version_genre_plan.py`, `scripts/apply_version_genre_plan.py`. Use it for the next source folder instead of writing a new one-off script.
- **The mutation engine is now unified, and the old ungated mutators are retired.** (2026-08-16 hardening pass — see `SAFETY-MODEL.md`.) Seven scripts perform library mutations and all seven share `scripts/mutation_engine.py`: `apply_version_genre_plan.py` (relocation + Genre), `apply_grouping_plan.py`, `apply_artist_title_plan.py`, `apply_year_bpm_plan.py`, `apply_genre_refinement_plan.py`, `apply_needs_check_recheck_genre_plan.py`, and `delete_verified_duplicates.py`. Each requires `--lock-folder`/`--session` **and** `--backup-manifest`. The P1 follow-up noted here previously (each script duplicating its own hash/backup/verify logic) is **done**: the per-item verify-write-verify-or-rollback loop now lives in the engine, with each script keeping only its plan-shape-specific logic on top. Four scripts that used to mutate no longer can: `standardize_names.py` and `reclassify.py` are preview/plan builders (they had been renaming files and rewriting Artist/Title/Genre with no lock, no backup, no verification, a forced ID3v2.3 downgrade, and an undo that never restored metadata); `apply_plan.py` and `merge_events_duplicates.py` are hard-disabled — `--legacy-confirm` is gone, and `merge_events_duplicates.py` had been hardcoding *another session's* lock name. See `handoff/session-2026-08-15/RECONCILIATION-REPORT.md` for the earlier hardening round, including a live-tested dead end (pid-liveness staleness doesn't work for one-shot CLI locks; staleness is renewal-based instead, see `folder_lock.py`'s own docstring).
- **The lock heartbeat had a real ownership race, now fixed.** `lock_guard.require_lock_or_exit()` used to read/check/renew/write *outside* the per-folder mutex that acquire/release run inside, so an old owner could rewrite itself back on top of a new owner's lock and both would then mutate the library believing they held it exclusively. All ownership checks now go through `folder_lock.verify_and_renew()`, which does the whole sequence inside the mutex, fails closed on corrupt/malformed/missing/stale locks, and pins the lock's `run_id` so a lock that changed hands cannot be adopted even under a matching session name. `tests/test_lock_ownership_race.py` reproduces the interleaving with real processes and a start barrier; it was confirmed to fail against the old implementation.
- Hip Hop (5,888 tracks, 44GB) is sorted — see the Completed section in `handoff/HANDOFF-DJ-MUSIC-SORT.md` for the flatten-everything decision and three real bugs found/fixed live during that run (unwritable-format tag proposals, an unconditional-read crash on unsupported formats, and a verified-benign Serato encoding-normalization false positive). `build_version_genre_plan.py` now supports `--on-collision suffix` to safely keep same-named, different-content files (never delete/overwrite) when flattening previously-separated folders creates a destination collision.
- A library-wide metadata completeness pass is planned as a later phase (not started): fill in missing artist/title/genre/BPM/key from evidence, backfill Grouping across every sorted folder, and check Serato crate-path integrity. Scope to be confirmed with Brooke before starting.
- **Genre atlas (2026-10-13):** `references/genre-atlas/` links every master Genre (`genre-learner-rules.json`) to the third-party World Music Atlas (origin, era, roots, parent genres). Read its `README.md` before using it. It is a genre reference only: never evidence for a track's tag, never learner training data, and links are hand-curated (no name matching). `python3 scripts/genre_atlas.py check` must pass. The learner list is the single master Genre list (76 genres after the 2026-10-13 Genre Master Review: Afrobeat → Afrobeats, Ballroom → Ballroom Vogue, eight genres added; Twerk/Dance/Club are Grouping; Garage/Garage Jungle/Bass/Drum need a check; see `tests/test_learner_taxonomy_20261013.py`). Tag names and folder names are separate: `folder_names` in both rule files maps each Genre to the exact live folder it files into. **Folder approval (2026-10-15):** from a read-only listing of the live library (`handoff/live-top-level-folders-2026-10-15.md`), the owner approved 59 Genres: 36 file into their own folder (e.g. Hip-Hop → `Hip Hop`, Afrobeats → `Afrobeat`, Ballroom Vogue → `Ballroom`, Funk & Soul → `Funk`) and 23 smaller styles file into a parent folder and keep their exact tag (e.g. Deep House → `House`, UK Garage → `GarageJungle`). The other 17 (Dub, Breaks, Electro, Dubstep, Miami/Atlanta Bass, Afro Tech, Gqom, Kuduro, Afro Baile, New Wave, the Drill styles) still go to `_GENRE NEEDS CHECK`. An approved destination is **not** Genre evidence: only `folder_genre_hints` (still the original three) lets a folder name label an untagged track. `scripts/check_genre_naming.py` pins both maps.
- **Folder renames (2026-10-15):** renaming a whole library folder (`R&b` → `R&B`, `Afrobeat` → `Afrobeats`) goes only through `scripts/apply_folder_rename_plan.py` (engine `folder_item`, atomic no-replace rename, case-only via a temp name) followed by `rewrite_serato_paths.py` and `rewrite_serato_sqlite_paths.py` (both `master.sqlite` and `root.sqlite`). Read `references/folder-rename-runbook.md` first. Not yet run on the live library; it needs the owner's separate go, a spare-copy trial with undo, and Serato fully quit.
- Text-only `.codex` working artifacts are mirrored under `handoff/session-2026-08-15/survey/`: plans, inventories, CSV reviews, result JSON, and undo manifests. Audio/tag-file backups and `_Serato_` data remain outside Git.
- `VDBL` version/genre sort is complete for 150 tracks (done by Claude, before the reusable pipeline above existed — see the `VDBL` section in the handoff for the one-off script used). It is NOT a real event despite its pre-existing `Event: VDBL` Grouping tag — the user says it's just a source download folder. `Country` was added to `canonical_genres` in `references/genre-rules.json` for this run. **Its 141 classified tracks have since been merged into the main genre library** (`Hip Hop`, `R&b`, `Amapiano `, new `Country/`) — see the second `VDBL` section in the handoff for destinations, dedup results, and locking. Only the 9 still-unresolved `_GENRE NEEDS CHECK`/`_NEEDS CHECK` tracks remain under `Events/VDBL`, awaiting the user's manual review.
- **Multiple agent sessions have been working this library concurrently with zero coordination** — this session, a Codex session, and at least one other Claude session, all writing to this same repo and the same Music folder at once. That already caused a real incident: `NewFiles` got merged into `music to move` mid-run by another session, silently, while this session still held plans referencing its old paths (recovered by hash-verifying every file at its new location before touching anything). A second incident followed: a session (`claude-metadata-pass`) acquired locks on 11 folders including `Events` and `Hip Hop` and stopped running without releasing them. Before starting work on any source folder, run `python3 scripts/folder_lock.py check --folder "<name>"` and `acquire` it if free; `status` lists everyone's current locks. **This is now enforced, not just convention:** every mutation script's `--apply`/`--undo` calls `scripts/lock_guard.py`, which refuses to run without an owned, non-stale lock for `--lock-folder`, and re-verifies that ownership before every single item — see the mutation-engine bullets above for what "non-stale" means now (renewal-based, not pid-based) and for the ownership race that was fixed. Also: before editing `CLAUDE.md` or `handoff/HANDOFF-DJ-MUSIC-SORT.md`, re-read them fresh and use a small targeted edit — another session's uncommitted, in-progress content may already be sitting there.

Useful session implementations and reports are under `handoff/session-2026-08-15/`. They contain local absolute paths and are historical/audit material; generalize paths before promoting code into `scripts/`.

Read `handoff/ARTIFACTS.md` before syncing more `.codex` files. Never copy a directory named `backups`, `tag-file-backups`, or `*-tag-file-backups`, because those contain real audio payloads.

This repository contains private local workflow information. Do not publish it or add a public remote without explicit approval.
