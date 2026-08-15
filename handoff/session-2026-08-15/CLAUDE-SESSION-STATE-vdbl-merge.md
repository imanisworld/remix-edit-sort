# In-progress: deciding where VDBL's tracks belong (Claude session)

Saved 2026-08-15, before a context compaction, so this is recoverable if the
session restarts. Not yet applied -- no files have moved as part of this task.

## Where this is going

VDBL (`/Users/djb.a.e/Music/DJ-Music/Music/Events/VDBL`) is a source/download
dump, not a real event (user's words: "vdbl is where i got them from"). It
was already sorted into CLEAN/DIRTY/genre internally (see the "Completed:
VDBL" section in `HANDOFF-DJ-MUSIC-SORT.md`). The current task is merging
those 150 tracks into the real top-level genre library
(`~/Music/DJ-Music/Music/<Genre>/`, sibling to `Events`), not leaving them
parked under Events.

## User decisions so far

- Hip-Hop/R&B tracks: merge **by release year** into the existing
  `Hip Hop/<year>` / `R&b/<year>`-style folders (those libraries are
  organized by sub-genre/era, not flat).
- No VDBL track has a TDRC/year tag, so year has to come from evidence
  outside the file. User's call: **use general knowledge, but mark it as
  inferred** rather than guess wildly, and prefer looking releases up online
  where memory alone isn't solid.
- Amapiano tracks (10): merge into `Amapiano ` (note trailing space in the
  real folder name) -- it's currently empty, so this is low-risk, not yet
  done.
- Shaboozey's 1 Country track: create a new top-level `Country` folder for
  it (none exists yet) -- not yet done.
- Process improvement requested by the user and implemented: record every
  confirmed year with its evidence/source in
  `references/release-year-evidence.json` (same pattern as
  `artist-genre-extra.json`) rather than just writing a tag with no trail.

## Year evidence -- DONE, see `references/release-year-evidence.json`

All 15 releases resolved: 13 have a confirmed year (6 from confident memory,
7 confirmed via web search), 1 is explicitly unresolved after a real search
attempt (Dead Silence Pack -- 8 R&B tracks, `Topper-Intro` DJ-tool pack
naming, not findable online, left unresolved rather than guessed).

Every Hip-Hop/R&B VDBL track now has either a confident year or an explicit
"no evidence" flag -- nothing in between. Year lookups are complete; nothing
left to research. Next real step is the move plan itself, once unblocked.

## BLOCKED: do not touch `Events` or `Hip Hop` right now

`python3 scripts/folder_lock.py status` showed **another live Claude
session** ("claude-metadata-pass") holding non-stale locks on both `Events`
(VDBL's parent) and `Hip Hop` (the main merge destination) as of
2026-08-15T19:08, running a "Grouping/Artist-Title/Year-BPM backfill pass".
Given the documented real incident (`NewFiles` silently merged into
`music to move` mid-run by another session), **do not move or write to any
file under `Events` or `Hip Hop` until both locks clear** -- re-check with
`folder_lock.py status` first. `R&b`, `Amapiano `, and a new `Country`
folder were NOT locked as of this check, but the VDBL source itself lives
under the locked `Events`, so nothing can move yet regardless of
destination.

## Next steps once locks clear

1. Re-check `folder_lock.py status` for `Events` and `Hip Hop`.
2. Finish the 2 remaining year lookups above.
3. Build the actual move plan: for each of the ~150 tracks, destination =
   `Hip Hop/<year>/...` or `R&b/<year>/...` (year-confirmed tracks),
   `Hip Hop/_NEEDS CHECK-YEAR/...` or similar for anything left unresolved
   (Dead Silence Pack, Young Nudy if still unconfirmed), `Amapiano /...`,
   `Country/...`. Write the year into TDRC for tracks getting a confirmed
   year, sourced from `release-year-evidence.json`.
4. Check for exact-hash duplicates against what's already in `Hip Hop`/`R&b`
   before moving anything in (per the project's "never delete/dedupe from
   filenames alone, but do check real hashes" rule).
5. Fresh verified backup of VDBL (and ideally the destination folders) before
   applying.
6. Apply with the same payload/Serato-frame verification and undo-log
   discipline as every other pass this session.
7. Update `HANDOFF-DJ-MUSIC-SORT.md` and `CLAUDE.md` when done.
