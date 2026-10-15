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


## Clock normalization and match policy (source-only implementation in PR #43)

`scripts/dj_event_time.py` normalizes **reviewed DJ set windows** and
**already normalized playback rows** with only Python's standard-library
`zoneinfo` (IANA rules). It operates on supplied private JSON fixtures and
prints candidate results. It does not parse actual Serato/Rekordbox histories,
index tracks, import private website bookings, or write any media or databases.
The official website's `event_date` is not a verified DJ set interval.

Required set evidence:

```json
[{
  "event_id": "<real website event ID>",
  "event_timezone": "America/Indiana/Indianapolis",
  "set_time_verified": true,
  "set_start_local": "2026-11-21T22:00:00",
  "set_end_local": "2026-11-22T02:00:00"
}]
```

This is an illustrative schema, not a real booking. **Both local calendar
dates must be explicit**. The first date is the `performance_date` (the night
the set belongs to); a track played after midnight retains its actual
`played_local_date` and offset-aware `played_at_utc`. Never subtract a day
from a playback record or silently assume an end before a start means "next
day." A future UI may offer *next-day* as an editable suggestion, confirmed
before writing the set window.

For spring-forward nonexistent times, refuse the window; for repeated
fall-back local times, require a reviewed `set_start_fold` /
`set_end_fold` (0 = earlier occurrence; 1 = later occurrence). UTC offset
abbreviations like EST and EDT are insufficient for the event time zone. Use
actual elapsed UTC time through DST shifts. A naive Serato CSV wall clock
without offset must be reviewed with confirmed venue/device clock context
before normalizing its playback timestamp.

Normalized playback records supplied by a future **trusted import** require
a unique `play_id`, offset-aware `played_at`, `played_status` of
`PLAYED`, `identity_status` of `EXACT_INDEXED_FILE`, positive
`file_id` from this repo's **same library index**, `session_id`, and
`source_artifact_sha256` referencing the original export. The script only
returns `CANDIDATE_ONLY` for exactly one confirmed set window with all
these fields; overlapping sets, unplayed/manual-uncertain records, missing
sources and ambiguous file matches remain `REVIEW`. These caller fields
are assertions and **not cryptographic proof of DJ playback**; verify them
in the upstream Serato/Rekordbox importer before operational use.

Command (private fixture paths, never commit real exports):

```bash
python3 scripts/dj_event_time.py \
  --events "<private reviewed-set-windows.json>" \
  --plays "<private normalized-playback-fixture.json>"
```

### Cross-application track identity

- Rekordbox exported collection XML's `TrackID` is its **own** integer
  identifying a track **inside its exported collection/playlist schema**.
  The XML also supplies `Location`, `PlayCount`, and date-only
  `LastPlayed`: https://cdn.rekordbox.com/files/20200410160904/xml_format_list.pdf
- The current Serato `database V2` parser uses `pfil` paths, and
  `ptrk` crate references. Serato History is a separate play-session
  source: https://support.serato.com/hc/en-us/articles/223455687-History
- There is **no proven native ID shared by both DJ applications**.
  The project already has a local SQLite `file_id` from
  `library_index.py`, resolved separately by `serato_index.py` and
  `rekordbox_index.py`. `serato_rekordbox_crosswalk.py` permits an
  exact cross-app relationship **only through that same resolved file_id**,
  tied to trusted import snapshots; duplicate titles/artist names do not
  establish identity. Do not assume the integer Rekordbox `TrackID` equals
  the local `file_id`, or that matching audio duration proves identity.
- A changed/moved audio file requires a fresh verified index resolution;
  cached file IDs are local index keys, not portable permanent recording IDs.
  Re-exports and provenance are necessary after library edits.

### Histories may be incomplete or changed

- Serato explicitly permits `Insert Track`, `Mark as Played / Unplayed`,
  notes and deletion of sessions: its History export can be a *manually
  corrected performance narrative*. An inserted record may not appear in
  M3U export. Preserve raw CSV/TXT bytes and whether a field's source/status
  is original, adjusted, or unknown where the app format exposes it.
  https://support.serato.com/hc/en-us/articles/223455687-History
- Rekordbox documentation describes date-named histories for tracks played
  about a minute or more in PERFORMANCE or LINK EXPORT, with device-history
  import, history-to-playlist copying, and device-history deletion. USB
  history imports may by default **delete the USB original**; change the
  setting on the Mac before importing if the original is to be preserved.
  https://rekordbox.com/en/support/faq/operation-hints-7/
  https://rekordbox.com/en/support/faq/rekordbox7/
- Individual-row editing of a Rekordbox original history (as distinct from
  editing a playlist copy or deleting a device history) is **unverified**.
  Do not claim it is editable or immutable without an installed-version
  UI test and a before/after export. Rekordbox collection XML and diagnostic
  logs remain separate from actual playback histories.

### Grok research still needed (vendor docs do not establish these details)

The repo assigns **one-off technical research** to Grok, separate from its
website DJ-manager scouting prompt. A completed Grok technical research
response was not available in the evidence reviewed here; these are
**questions, not asserted vendor behavior**.

1. On the *installed* Serato version, export one sample CSV/TXT and confirm
   exact headers, session identifiers, raw date/time representation, local
   offsets, daylight-saving handling, and presence/absence of manual/played
   status indicators. Determine if edits affect re-exported history.
2. On the *installed* Rekordbox version and device, identify the true
   history artifact/export path, original record editing capabilities (if
   any), when import duplicates/skips, what the history rows actually
   timestamp, and whether time/date changes on device/host affect imports.
3. Test a disposable session crossing midnight and the DST clock change:
   does each app report track **date plus time**, only the date of the
   session, a fixed local wall time, or a UTC-aware instant? Record
   actual version, OS, device model, mode and sample hashes.
4. Test mixed sources, multiple versions of the same song, renamed/moved
   tracks, two copies of equal-length audio, and whether history entries
   expose enough path/file provenance to bind to the existing local
   `file_id`. **No guessed global track GUID.**
5. Note vendor docs vs. experimentally observed vs. community-reported
   behavior separately, with links and anonymized evidence. Do not edit
   original playback data or ship a write-capable app-database parser.

**Stop condition:** final parsers and automatic played-at-event associations
wait for these real samples; synthetic time normalization can be tested now.
