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
3. Select the next source folder, inventory it read-only, and create a new preview.
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

## Next pass: Grouping metadata backfill

Do this as a tags-only, preview-first pass. Do not reorganize the completed folders again.

- `Genre` remains the musical style; `Grouping` records event/crate/source context.
- Start with `Events`: reconstruct each track's original event folder from the successful plan or undo logs and propose `Event: <original event name>`.
- Use `Event: Wedding` for the completed Wedding track.
- For useful source collections, propose values such as `Source: RAP RN`, `Source: Nigeria`, and `Source: Rave`.
- `NIGER` is a mistaken label for **Nigeria**. Its pending preview proposes `Grouping: Source: Nigeria`; Genre and Artist remain uninferred.
- Do not add generic labels such as `Source: music to move`.
- Preserve existing Grouping values. Append and deduplicate using one consistent delimiter; never overwrite blindly.
- A track may have multiple event/source contexts, so preserve all evidenced values.
- Make and verify a fresh scoped backup, generate a preview, and obtain explicit approval before writing tags.
- Keep `Imani Drops` completely excluded.

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
