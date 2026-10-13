# DJ event history and folder-renaming integration boundaries

Status: source-only review, 2026-10-13. No music or software data changes approved.

## Verified source facts

- The website event model stores stable event ID, event date, timezone,
  title, venue, city and visibility. Event date is NOT proof of the DJ's
  actual set start, end, or any track play.
- A connected read-only check on 2026-10-13 found 14 event records
  (10 public), some with legacy or placeholder times; 5 booking records
  had no populated end_time or event_end_time.
- The library index already detects NEW/CHANGED/MOVED/REMOVED audio files.
- Serato imports are snapshots of database V2 and Subcrates; Rekordbox
  imports are snapshots of a user-exported collection XML. They are not
  live observations of the applications.
- scripts/dj_source_drift.py compares current source bytes to the latest
  successful imports. It reports changed/new/removed Serato sources and
  changed Rekordbox XML, without reimporting, moving or retagging anything.

## Vendor evidence and limits (checked 2026-10-13)

- Serato's official [History guide](https://support.serato.com/hc/en-us/articles/223455687-History) documents session and track **Start Time**, **End Time**, **Playtime**, **Deck**, manual *played/unplayed* marks, inserted tracks and CSV/TXT/M3U exports. Since history can be edited and the exact exported CSV/TXT columns still need a real sample, **do not automatically equate an exported row with an authenticated performance**. Serato's separate [technical session logs](https://support.serato.com/hc/en-us/articles/5583242327951-Locating-providing-session-logs-for-Serato-DJ) are troubleshooting records, **not** interchangeable with History.
- The [Rekordbox 7 manual](https://cdn.rekordbox.com/files/20260409151936/rekordbox7.214_manual_EN.pdf) documents a **Histories** view listing eligible played tracks; a normal *collection XML* export is not a timestamped playback-session log. Record which specific history export format and timestamp granularity can actually be verified from a sample before implementing a Rekordbox history parser.
- Neither app's crate/playlist membership, LastPlayed/PlayCount, or genre metadata proves **where, when and for whom** a track was played. A match is a candidate requiring corroborated source and set time.

The current active work list, agent division, review gates, and done criteria are in [dj-integration-work-plan.md](dj-integration-work-plan.md).

## Event-to-playback association: required evidence

An internal event export needs: event ID, event timezone, confirmed *DJ set*
start and end timestamps (or null), title/venue, and source provenance.

A playback export needs: source/session ID, actual played_at with timezone,
and a verified current file identity or exact path. Rekordbox LastPlayed and
PlayCount do not constitute a history of individual plays; crates and
playlists establish membership only.

For a future join, convert explicitly valid times to UTC. Match a logged
play to an event only if it falls within a *confirmed* DJ set window.
Keep overlapped windows, absent set ends, timezone uncertainty, ambiguous
file identities, and unknown timestamps flagged for review. A temporal match
is a candidate association, never independent proof of a track's Genre
or authorization to retag it. Keep internal bookings and performance
history out of public events endpoints.

The Mac is required to obtain and validate real Serato or Rekordbox playback
session samples. Until that evidence exists, do not fabricate playback
history from event start times, playlist membership, or historical LastPlayed.

## Read-only source-drift operation

After a trusted library scan and Serato/Rekordbox imports:

    python3 scripts/dj_source_drift.py --db /path/to/library.sqlite3

Optionally pass --serato-dir or --rekordbox-xml when explicitly selecting
different source locations. CHANGED means the persisted snapshot may be
stale and should be *reimported separately after review*. If no new
Rekordbox XML is exported, unchanged old XML cannot establish that the
live application has not changed. Missing sources and missing baselines
must remain unknown rather than falsely reporting unchanged.

## Safe genre-folder rename sequence

1. Obtain a fresh read-only Mac listing of exact genre-folder names and
   track counts. The PR #37 Genre-to-folder maps are deliberately empty.
   Never derive physical paths from historical handoff notes.
2. Keep canonical Genre tags separate from physical directory names.
   Reconcile aliases first, retaining Miami Bass and Atlanta Bass as
   distinct genres. Do not assume all ATL Bass records belong to one.
3. Generate an evidence-bound per-track move preview, checking existing
   targets, hashes, APFS case/Unicode conflicts, same-name different
   versions, Serato crate paths, and Rekordbox collection references.
4. Run an on-volume synthetic Mac canary, obtain verified music and Serato
   backups, locks, and explicit user approval before any physical changes.
   Use the shared mutation engine; no raw bulk shell moves.
5. After approved audio relocations, use the existing
   scripts/rewrite_serato_paths.py exact-prefix preview and gated
   Serato path rewrite, with Serato closed, separate verified backup,
   post-check, recovery and undo. This tool does NOT move audio itself.
6. For Rekordbox, use a fresh user XML export, the existing relocation
   preview and bridge-XML review flow; never modify encrypted master.db.
7. Verify every moved file, audio payload, embedded cues, crate references,
   Rekordbox imports, and undo. Preserve old paths as local history.

## Required evidence before claiming the integration works

- One confirmed event with actual DJ start AND end times.
- One timestamped DJ playback-session export with verified track identity.
- Synthetic overlap and ambiguity tests plus one real read-only join.
- Actual folder listing and trusted Serato/Rekordbox snapshots.
- No production deploy, music edits, or folder renames from this PR.
