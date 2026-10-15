# DJ music sorting handoff

Date: 2026-08-15

## Safety rules agreed with the user

- Work one source folder at a time.
- Survey and show a preview before moving or retagging anything.
- Never delete or identify duplicates from filenames alone.
- Preserve meaningful numbers such as `21 Savage`, `112`, years, BPM, and part numbers.
- Clean, Dirty, Intro, Instrumental, Acapella, Remix, and Edit versions remain separate.
- Unknown classifications go to a needs-check folder; do not guess.
- Preserve Serato cues, beatgrids, loops, and custom frames.
- Make and verify a fresh backup before each apply.
- Every apply needs a complete undo log and audio/Serato verification.

## Completed: `music to move`

Source:

`/Users/djb.a.e/Music/DJ-Music/Music/music to move`

Fully completed and verified:

- 152 tracks processed.
- Version folders created: `CLEAN`, `DIRTY`, `REMIXES & EDITS`, `DJ TOOLS`, `ALBUMS & PROJECTS`, `_NEEDS CHECK`.
- Genre subfolders added and matching Genre metadata written where evidence was clear.
- 126 genre-folder moves and 99 Genre metadata updates.
- 31 tracks left in `_GENRE NEEDS CHECK` rather than guessed.
- Zero audio-payload changes, zero Serato-frame changes, zero deletions.
- Fresh final inventory found 152 tracks and no internal exact-duplicate groups.

Completion report:

`/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/music-to-move/GENRE-COMPLETION-REPORT.md`

Pre-genre backup:

`/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/backups/music-to-move-pre-genre-2026-08-15`

Final genre undo log:

`/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/music-to-move/genre-undo-20260815-160858.jsonl`

## Completed: `Events`

Source:

`/Users/djb.a.e/Music/DJ-Music/Music/Events`

Current state: **fully applied and independently verified**.

- 3,046 files moved into the approved version/genre structure.
- 2,191 Genre metadata fields safely updated.
- 15 ID3v2.2 Genre writes skipped and logged; those old tag blocks were not silently converted.
- Zero audio-payload changes and zero Serato-frame changes.
- Zero missing destinations, surviving old source paths, or deletions.
- The post-run inventory found all 3,046 tracks and remained stable during hashing.
- Format-aware verification checked all 2,560 resolved/skipped Genre values with zero errors.

Completion report:

`/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/events/EVENTS-COMPLETION-REPORT.md`

Final undo log:

`/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/events/events-undo-20260815-163215.jsonl`

Inventory and approved plan:

- 3,046 audio files, approximately 27 GB.
- 2,984 unique full-file hashes.
- 56 exact-hash groups / 62 redundant instances. Do not delete them; copies across event crates may be intentional.
- Proposed: 3,046 moves and 2,206 Genre metadata updates.
- Version results: 979 Clean, 203 Dirty, 41 Remixes & Edits, 19 DJ Tools, 1,804 `_NEEDS CHECK`.
- Genre results: 486 `_GENRE NEEDS CHECK`; clear genres use canonical folder names mirrored into metadata.
- Every top-level event and existing album/project/crate path is preserved. The plan inserts version and genre inside that context rather than flattening Events.
- Plan validation: zero missing sources, zero destination collisions, zero existing-destination conflicts.

Preview:

`/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/events/EVENTS-PREVIEW.md`

Full plan:

`/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/events/events-version-genre-plan.json`

Genre review list:

`/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/events/EVENTS-NEEDS-REVIEW.csv`

Verified Events + Serato backup:

`/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/backups/events-pre-sort-2026-08-15`

Backup is 27 GB and contains both `Events` and `_Serato_`. `diff -qr -x .DS_Store` was silent for both sources.

Apply tool:

`/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/events/apply_events_plan.py`

## Resolved Events compatibility notes

The apply tool now correctly handles FLAC files that contain a leading ID3 header. A copy-only test on `Judas Priest - 04 - Hell Bent For Leather.flac` proved that the true FLAC audio stream and Serato data remain unchanged after a Genre write.

Fifteen MP3 files use ID3v2.2. Their Genre writes were skipped and logged, their existing tags were retained, and the files were still moved to their approved folders. The other 2,191 Genre updates were applied successfully. Do not convert the 15 old tag blocks unless separately requested.

ID3 version counts among proposed MP3 Genre writes:

- ID3v2.3: 1,958
- ID3v2.4: 228
- ID3v2.2: 15

The exact 15 paths were printed during the audit and can be regenerated read-only from the plan with `mutagen.id3.ID3(path).version[1] == 2`.

## Safe continuation checklist for another folder

1. Read the local skill completely:
   `/Users/djb.a.e/Music/DJ-Music/Tools/remix-edit-sort/SKILL.md`
2. Do not rerun the full Events plan over the already-sorted destination paths.
3. Use the reusable pipeline in `scripts/` — do not hand-write another one-off
   `apply_<folder>_plan.py`. The nine per-folder apply scripts under
   `handoff/session-2026-08-15/survey/*/` were byte-identical apart from four
   hardcoded values; that duplication is now retired in favor of:
   - `scripts/inventory_library.py --root <folder> --out-dir <dir>`
   - `scripts/build_version_genre_plan.py --root <folder> --inventory <dir>/inventory.csv --out-dir <dir> [--assume-genre "Name"]`
     (`--assume-genre` only for a folder you've visually confirmed is a single
     genre, same judgment call already made for Dub/Rave/Metro/Wedding/Ambient/
     belarusian_electronic; omit it to classify per-track from tags/filenames
     via `references/version-genre-rules.json`, falling back to
     `_GENRE NEEDS CHECK` rather than guessing)
   - Review `<dir>/PREVIEW.md` with Brooke; get explicit approval.
   - `scripts/apply_version_genre_plan.py --root <folder> --plan <dir>/plan.json --backup <verified-backup> --apply`
   - Undo: `scripts/apply_version_genre_plan.py --undo <dir>/<label>-undo-<stamp>.jsonl --root <folder>`
   Regression-tested against the archived Dub and Rave inventories: the
   generalized builder reproduces the historical plans item-for-item.
4. Make and verify a new scoped backup before applying that folder's plan.
5. Preserve the ID3v2.2 skip behavior unless tag conversion is separately approved.

## Historical partial-run logs

These runs were fully undone and should not be treated as successful applies:

- `events-undo-20260815-162354.jsonl` — 289 entries, fully restored.
- `events-undo-20260815-162639.jsonl` — 1,030 entries, fully restored.

Before the final successful apply, the post-rollback dry run passed for all 3,046 tracks. The later final apply and verification are recorded in the completed Events section above.

## Completed: `RAP RN`

Source: `/Users/djb.a.e/Music/DJ-Music/Music/RAP RN`

- 21 tracks moved: 10 Clean, 8 Dirty, and 3 `_NEEDS CHECK`.
- 17 Genre metadata fields normalized.
- Final genres: 16 Hip-Hop, 4 R&B, and 1 Afrobeats.
- Zero audio-payload changes, Serato-frame changes, deletions, duplicates, missing destinations, or tag mismatches.
- Verified backup: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/backups/rap-rn-pre-sort-2026-08-15`
- Undo log: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/rap-rn/rap-rn-undo-20260815-164503.jsonl`
- Completion report: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/rap-rn/RAP-RN-COMPLETION-REPORT.md`

## Completed: `90S HIPHOP R&B CLEAN DIRTY`

- 10 tracks moved: 6 Clean, 3 Dirty, and 1 `_NEEDS CHECK`.
- Nine Genre metadata fields normalized to Hip-Hop.
- `Money Ain't A Thang` remains `_GENRE NEEDS CHECK` with its existing `Other` tag; no guess was made.
- Zero audio, Serato, deletion, duplicate, path, or tag-verification errors.
- Backup: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/backups/90s-hiphop-rnb-pre-sort-2026-08-15`
- Undo log: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/90s-hiphop-rnb/90s-undo-20260815-164752.jsonl`

## Completed: `Ambient`

- One track moved to `_NEEDS CHECK/Ambient` with its existing Genre metadata preserved.
- Full-file hash verified after the move; zero audio, Serato, deletion, or path errors.
- Backup: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/backups/ambient-pre-sort-2026-08-15`
- Undo log: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/ambient/ambient-undo-20260815-165036.jsonl`

## Completed: `Wedding`

- One Latin Orchestral Edit moved to `REMIXES & EDITS/Latin` and tagged Genre `Latin`.
- Zero audio, Serato, deletion, collision, or verification errors.
- Backup: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/backups/wedding-pre-sort-2026-08-15`
- Undo log: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/wedding/wedding-undo-20260815-165156.jsonl`

## Completed: `belarusian_electronic`

- One ambiguous track moved to `_NEEDS CHECK/_GENRE NEEDS CHECK` with all conflicting metadata preserved.
- Full-file hash verified; zero audio, Serato, deletion, or path errors.
- Backup: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/backups/belarusian-electronic-pre-sort-2026-08-15`
- Undo log: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/belarusian-electronic/belarusian-undo-20260815-165326.jsonl`

## Completed: `metro`

- One remix moved to `REMIXES & EDITS/Jungle`; Genre normalized from `jungle` to `Jungle`.
- Zero audio, Serato, deletion, collision, or verification errors.
- Backup: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/backups/metro-pre-sort-2026-08-15`
- Undo log: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/metro/metro-undo-20260815-165507.jsonl`

## Completed: `Dub`

- `Spooky Dub.mp3` moved to `_NEEDS CHECK/Dub` with Genre unchanged.
- Full-file hash verified; zero audio, Serato, deletion, or path errors.
- Backup: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/backups/dub-pre-sort-2026-08-15`
- Undo log: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/dub/dub-undo-20260815-165621.jsonl`

## Explicitly skipped: `Imani Drops`

The user said this folder is something different. It remains completely untouched. Do not include it in this sorting workflow unless the user later gives specific instructions for it.

## Completed: `Rave`

- Two tracks moved to `_NEEDS CHECK/Rave`; Genre set to `Rave` using the source folder as evidence.
- Zero audio, Serato, deletion, duplicate, collision, or verification errors.
- Backup: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/backups/rave-pre-sort-2026-08-15`
- Undo log: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/rave/rave-undo-20260815-165945.jsonl`

## Completed: `NIGER` (Nigeria)

- Two tracks moved to `_NEEDS CHECK/_GENRE NEEDS CHECK/`; Grouping set to `Source: Nigeria` on both.
- Genre and Artist metadata left unchanged (unknown, not guessed).
- Three unrelated `.mp4` files in the same folder were out of scope and left untouched.
- Zero audio-payload changes, zero Serato-frame changes, zero deletions.
- Backup: `backups/niger-pre-sort-2026-08-15/NIGER` (repo-root, gitignored).
- Undo log: `handoff/pending/nigeria-undo-20260815-180809.jsonl`
- Applied via `handoff/pending/apply_nigeria_plan.py` (a small dedicated script — this plan's shape, move + Grouping write with no Genre change, hasn't recurred elsewhere yet, so it wasn't folded into either generalized pipeline).

## Completed: `NewFiles`

Source: `/Users/djb.a.e/Music/DJ-Music/Music/NewFiles` (461 flat files + `Music`, `sort`, `Michael`, `SoundCloud`, `NEW downloads`, `Spoken Word` subfolders)

Sorted using the new `scripts/` pipeline (first real run on a large, previously-untouched folder):

- 829 tracks inventoried; 827 eligible for move, 2 deliberately excluded (see below).
- Version: 23 Clean, 20 Dirty, 4 Instrumental, 48 Remix/Edit, 734 `_NEEDS CHECK`.
- Genre: 168 resolved from existing tags/filename evidence across Hip-Hop, R&B, Pop, Electronic, and five smaller genres; 504 routed to `_GENRE NEEDS CHECK` rather than guessed.
- Zero audio-payload changes, zero Serato-frame changes, zero deletions.
- Post-apply inventory confirms all 829 tracks still present, stable, zero new exact-duplicate groups.
- Backup: `backups/newfiles-pre-sort-2026-08-15/NewFiles` (repo-root, gitignored, verified byte-identical with `diff -qr`).

**Two `1-01. The Way.flac` files** (different SHA-256, same size/duration — different masters, not duplicates) collided on destination path. Per Brooke's instruction, both were left completely untouched in their original locations (`Music/TMHoarder/1-01. The Way.flac` and `Music/TMHoarder/DRAM - Shake Tape (EP) (2026)/1-01. The Way.flac`) for manual placement later.

**Two real bugs found and fixed in `scripts/apply_version_genre_plan.py` during this run** (both inherited unchanged from the original nine per-folder scripts, never previously exercised):

1. The post-move Genre verification call was unconditional on file format. It crashed on the first non-mp3/flac file carrying a `proposed_genre` (an `.m4a`), even though Genre reads/writes are only implemented for mp3/flac. Fixed: the check now only runs when the destination format is verifiable.
2. The pre-apply validation's unsupported-tag-target check appended an error but then still called `read_genre()` unconditionally on the same item, which would have crashed instead of failing cleanly. Fixed: the read is now skipped when the format is unsupported.

One file (`Music/thepathforward/[2018-11-28] CHANCES/01 ''KKAAYYTTRRAA'' - CHANCES.m4a`) was moved for real by the first crash before bug #1 was hit, but the crash happened before its undo record was written. Recovered by hash-verifying the file at its destination against the plan's recorded SHA-256 (match confirmed, no tag write had been attempted) and manually appending a reconstructed undo record: `handoff/session-2026-08-15/survey/newfiles/newfiles-orphan-recovered-20260815.jsonl`.

Full run, in order (undo logs under `handoff/session-2026-08-15/survey/newfiles/`): `newfiles-undo-20260815-181211.jsonl` (337, then crashed on bug #1) → `newfiles-remainder-undo-20260815-181341.jsonl` (113, crashed again on bug #1 via a different file) → bugs fixed → `newfiles-remainder3-undo-20260815-181610.jsonl` (378, completed cleanly) → orphan recovery (1). 337+113+378+1+2(excluded) = 829.

## Completed: `Hip Hop`

Source: `/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop` — 5,888 tracks, 44GB. By far the largest folder yet (larger than Events + NewFiles combined). Unlike every prior folder, most of it was already curated: only 2,511 files sat loose at the top level; the rest lived inside subfolders that were real DJ crates — regional scenes (`chicago_drill`, `atl_trap`, `houston_rap`, `nyc_rap`, `bronx_drill`, `london_drill`, etc.), decade sets (`Hip Hop '80-'00`, `Hip Hop '00-'19`), a pre-existing `Clean` folder, and DJ energy-tier folders (`2026 BEGIN`/`MID`/`HIGH`). Brooke explicitly chose to flatten everything into the same CLEAN/DIRTY/genre scheme used elsewhere, accepting that the existing regional/decade/energy-tier organization would be erased. Genre assumed as `Hip-Hop` for the whole tree (the root folder's own name), matching how Dub/Rave/Metro/etc. were handled.

- Version: 1,590 Clean, 1,752 Dirty, 134 Remix/Edit, 51 Instrumental, 19 Acapella, 2,342 `_NEEDS CHECK`.
- Genre: `Hip-Hop` for all 5,888 tracks (assumed, per folder name).
- 3,829 Genre tag writes attempted; 5,543 applied, 171 skipped for ID3v2.2 (preserved, not converted), 12 skipped for a verified-benign Serato encoding difference (see below).
- 41 destination collisions from merging previously-separated crates (same track title, different bytes — different rips, confirmed by differing SHA-256, not exact duplicates). Both files in every pair were kept; the second file's destination filename got a ` (2)` suffix, following this project's existing terminal-`(N)` convention. Nothing was deleted, renamed at the source, or silently picked as a "winner."
- Zero audio-payload changes, zero Serato-frame changes, zero deletions.
- Post-apply inventory confirms all 5,888 tracks present, stable, zero new exact-duplicate groups; 41 terminal-`(N)` hints match the 41 resolved collisions exactly.
- Backup: `backups/hip-hop-pre-sort-2026-08-15/Hip Hop` (repo-root, gitignored, 44GB, verified byte-identical with `diff -qr`).
- Undo logs: `handoff/session-2026-08-15/survey/hip-hop/hip-hop-undo-20260815-183117.jsonl` (1,817 items, first run) + `hip-hop-remainder-undo-20260815-183812.jsonl` (4,071 items, after fixes below) = 5,888.

**Three real bugs found and fixed live during this run, all now permanent fixes in `scripts/`:**

1. `build_version_genre_plan.py` proposed a Genre tag write (`tag_change: true`) for `.m4a`/`.aif` files even though the writer only supports mp3/flac. Caught cleanly by pre-apply validation (zero files touched) but blocked the whole plan. Fixed: the builder now never proposes an unwritable tag change — those files still move, just keep their existing Genre tag, with the reason recorded in `genre_write_skipped_reason`.
2. `apply_version_genre_plan.py`'s pre-apply validation appended an "unsupported tag target" error but then still called the mp3/flac-only `read_genre()` unconditionally on the same item, which would have crashed instead of failing cleanly. This bug was inherited unchanged from all nine original per-folder scripts and never previously exercised. Fixed: the read is now skipped once the format is already flagged unsupported.
3. One file's Serato GEOB frames (cues, beatgrid, markers, analysis, overview) used an ID3 encoding byte that's technically invalid for its ID3v2.3 tag. Writing the Genre tag made mutagen silently normalize that byte for spec compliance, changing `serato_digest()` even though the real Serato data was untouched — confirmed by a byte-level payload comparison in isolation before touching any real file. Rather than weakening the safety check, added `serato_payload_digest()` (hashes only each Serato frame's actual binary payload, ignoring encoding/desc/owner) and a narrow `SeratoEncodingOnlyChange` skip path: if the strict digest changes but the payload digest doesn't, the write is rolled back and skipped — logged exactly like the ID3v2.2 case — instead of hard-aborting the entire run. If the payload digest had also differed, it still hard-aborts. 12 files hit this path in the real run; each was verified payload-identical before being classified as a skip, not assumed safe.

## Next pass: Grouping metadata backfill

Do this as a tags-only, preview-first pass. Do not reorganize the completed folders again.

- `Genre` remains the musical style; `Grouping` records event/crate/source context.
- Start with `Events`: reconstruct each track's original event folder from the successful plan or undo logs and propose `Event: <original event name>`.
- Use `Event: Wedding` for the completed Wedding track.
- For useful source collections, propose values such as `Source: RAP RN`, `Source: Nigeria`, and `Source: Rave`.
- `NIGER` is a mistaken label for **Nigeria**. Its pending preview proposes `Grouping: Source: Nigeria`; Genre and Artist remain uninferred.
- Do not add generic labels such as `Source: music to move`.
- Preserve existing Grouping values. Append and deduplicate using one consistent delimiter; never overwrite blindly. Only exception: exact junk values the owner listed by hand, removed with `scripts/build_grouping_removal_plan.py` (approved 2026-10-15).
- A track may have multiple event/source contexts, so preserve all evidenced values.
- Make and verify a fresh scoped backup, generate a preview, and obtain explicit approval before writing tags.
- Keep `Imani Drops` completely excluded.

## Completed: `VDBL` (version + genre sort, done by Claude)

Source: `/Users/djb.a.e/Music/DJ-Music/Music/Events/VDBL`

Note: despite the pre-existing `Event: VDBL` Grouping tag from the Events backfill
below, the user clarified VDBL is a source/download folder, not a real event ("where
i got them from"). No Grouping changes were made here; where these tracks ultimately
belong is an open question, deferred by the user to a later pass.

- 150 tracks scanned, all pre-existing DJ-pool `MP3/CLEAN`/`MP3/DIRTY` folder labels
  trusted as version evidence (stronger than filename parsing) before falling back to
  bracketed-label filename detection.
- Version results: 79 Clean, 62 Dirty, 3 Instrumentals, 1 Acapella, 5 `_NEEDS CHECK`
  (all in `Dead Silence Pack`, left unlabeled by the source itself).
- Genre results: 127 Hip-Hop, 10 Amapiano, 8 R&B, 1 Country, 4 `_GENRE NEEDS CHECK`
  (Tyla — user chose not to force a call; source folder itself flagged these ambiguous).
- Added `Country` to `canonical_genres` and a matching keyword rule in
  `references/genre-rules.json` (user request, prompted by the Shaboozey track).
- 150/150 files moved, 0 errors, 0 collisions, 0 duplicates skipped.
- 139/146 classified tracks got a `TCON` write; 7 were skipped as ID3v2.2 (preserved,
  not converted), matching the same rule used for the Events pass.
- Every moved file verified present at its destination; every written genre tag
  re-read from disk and confirmed to match what was recorded in the undo log.
- 31 now-empty leftover folders pruned; non-empty artwork/PDF folders left in place.
- Backup: `Tools/remix-edit-sort/handoff/session-2026-08-15/backups/vdbl-pre-sort-2026-08-15`
- Undo log: `Tools/remix-edit-sort/handoff/session-2026-08-15/vdbl-undo-20260815-184344.jsonl`
- Script used: adapted from `music-to-move/build_version_plan.py`'s explicit-label-only
  version rule + `remix-edit-sort/scripts/scan_library.py`'s genre classifier; not yet
  promoted into `scripts/` — still has VDBL-specific path constants.

## Completed: `VDBL` merged into the main genre library (done by Claude)

Follow-up to the version+genre sort above. The 141 classified tracks (everything except
the 9 still sitting in `_GENRE NEEDS CHECK`/`_NEEDS CHECK`) were moved out of
`Events/VDBL` into the real top-level genre folders, closing the "where do these
ultimately belong" question deferred earlier.

- Destination structure was checked against the real folders before merging, not
  assumed: `Hip Hop/2024`/`2025` turned out to hold only Serato Stems files, not
  regular tracks, so the earlier "merge by year" idea was dropped. Real Hip Hop tracks
  live in `Clean/Hip-Hop`, `DIRTY/Hip-Hop`, `INSTRUMENTALS/Hip-Hop`, `ACAPELLAS/Hip-Hop`
  (mirrors VDBL's own structure exactly). `R&b` has no Clean/DIRTY split at all — flat,
  version kept in the filename — confirmed with the user before merging that way.
- 127 Hip-Hop → `Hip Hop/<bucket>/Hip-Hop/`, 10 Amapiano → `Amapiano /`, 3 R&B → `R&b/`,
  1 Country → new `Country/` folder.
- `TDRC` year written for 127 tracks using `references/release-year-evidence.json`
  (the evidence-provenance file from the version+genre pass); left blank for 3 Dead
  Silence Pack tracks with no resolvable evidence.
- Dedup check against existing destination content before moving anything: 0 exact-hash
  duplicates, 0 filename collisions. A normalized-title heuristic additionally flagged 9
  tracks with a likely-existing "Intro Clean/Dirty" counterpart already in the
  destination folder (Travis Scott FE!N, Veeze - Not a Drill, 3 Tyla tracks, Sexyy Red &
  Drake, Hitkidd & Juicy J) — not treated as duplicates (Intro/Clean/Dirty edits are
  distinct, intentional DJ assets per this project's own rules), just surfaced for
  awareness; all 9 were merged in like everything else.
- 141/141 files moved, 0 errors. Every move + tag write verified: audio payload hash,
  Serato-frame hash, and the written TDRC value all re-checked against the undo log
  after the fact (0 mismatches across all 141 records).
- Locking: this was the first real production run under the atomic `folder_lock.py`
  (flock-based, fixes the TOCTOU race an earlier version had) — locks acquired on all
  five touched folders (`Events`, `Hip Hop`, `R&b`, `Amapiano `, `Country`) before
  applying, heartbeat-renewed throughout, released cleanly after.
- Backup: `Tools/remix-edit-sort/handoff/session-2026-08-15/backups/vdbl-pre-merge-20260815-201005`
  (spot-verified against source by hash before applying).
- Plan/preview/undo/result: `Tools/remix-edit-sort/handoff/session-2026-08-15/survey/vdbl-merge/`
- Remaining in VDBL, untouched: 4 tracks in `CLEAN/_GENRE NEEDS CHECK`, 5 in
  `_NEEDS CHECK/R&B` (the Dead Silence Pack tracks whose version couldn't be
  determined) — still need the user's own manual review, unrelated to this merge.

## Completed: Events Grouping metadata backfill

- 3,010 MP3 Grouping fields updated from the original top-level Events folder names.
- 586 pre-existing Grouping values were preserved and the event value appended.
- 36 files were skipped safely: 23 ID3v2.2 MP3s, 10 unreadable FLAC tag records, 2 AIFFs, and 1 M4A.
- No files moved, renamed, or deleted; no Genre tags changed.
- Every changed MP3 passed audio-payload and embedded Serato-frame verification.
- Verified backup: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/backups/events-pre-grouping-2026-08-15`
- Undo log: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/events/events-grouping-undo-20260815-171044.jsonl`
- Completion report: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/events/EVENTS-GROUPING-COMPLETION-REPORT.md`
- One new live Serato crate (`womens international -  Jamie.crate`) appeared during the pass. It was the only `_Serato_` delta, was not written by the script, and was separately backed up and verified.

## Completed: 8 small `_NEEDS CHECK`-only folders (done by Claude, via the reusable pipeline)

Source: `Halloween Party`, `karaoke`, `tpom `, `women`, `white dance party`, `NYE`, `xmas`, `Gay Women Party` — all under `/Users/djb.a.e/Music/DJ-Music/Music/Events/`.

First use of `inventory_library.py` -> `build_version_genre_plan.py` -> `apply_version_genre_plan.py` by Claude (the reusable pipeline referenced in `CLAUDE.md`, built during this same session). One folder at a time, each with its own inventory, plan, preview, verified backup, and apply.

- 89 tracks total across the 8 folders; 0 exact-duplicate groups in any of them.
- 51 files moved, 5 Genre tag changes (all in `xmas`: 9 Pop, 16 R&B already-evidenced, plus 5 new writes).
- Everything else landed `_NEEDS CHECK` / `_GENRE NEEDS CHECK` — none of these folders had explicit version labels or evidenced genres in their filenames/tags, so nothing was guessed.
- 0 audio-payload changes, 0 Serato-frame changes, 0 errors, 0 destination collisions across all 8 applies.
- Post-apply audio file counts matched each folder's original inventory count exactly; empty leftover folders pruned.
- Backups: `handoff/session-2026-08-15/backups/<slug>-pre-sort-2026-08-15/` for each folder.
- Undo logs, plans, and previews: `handoff/session-2026-08-15/survey/<slug>/`.

This closes out every folder under `Events` that existed before this session — `VDBL` (above) and these 8 were the last ones without a crate or a completed sort.
