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

Official pages are [OFFICIAL]. Third-party pages are [UNOFFICIAL] and are not confirmed facts. No History export was parsed, and neither application's database was opened.

- [OFFICIAL] Serato's [History guide](https://support.serato.com/hc/en-us/articles/223455687-History) documents session and track **Start Time**, **End Time**, and **Playtime** (the duration example is `00:03:00`; Start/End clock precision is not stated), plus session start, end, and elapsed time. **Played** means the crossfader reached the track and the line fader was up. Manage History can start or end a session, insert a track, and mark played or unplayed. Exports are Serato Playlists, txt, csv, and m3u, written to the History Export folder inside the Serato folder. The m3u omits inserted tracks. The docs do not say whether a manual edit is flagged in the file. [OFFICIAL] [Reset played tracks](https://support.serato.com/hc/en-us/articles/202538290) confirms the same played rule, manual marks for vinyl or CD, hidden unplayed rows unless Show Unplayed is on, and decks 3 and 4 on 4-deck hardware. The export's exact columns, delimiter, encoding, deck field, unplayed rows, and inserted-track flag still need a real sample. Do not treat an exported row as an authenticated performance.
- [OFFICIAL] Serato's [Plays column](https://support.serato.com/hc/en-us/articles/360001031675) is a separate count from History, excludes older-version history, can be disabled from 2.5.5, and "what counts as a play varies between states." [OFFICIAL] [Serato Playlists](https://www.serato.com/playlists/how-to) confirms txt, m3u, CSV, and online Serato Playlists export, including several sessions at once. The online option needs internet. [OFFICIAL] [Library navigation](https://support.serato.com/hc/en-us/articles/14359483189135) puts the Serato folder in the Music folder and on each external drive. Moved or renamed files show yellow. Library columns include Filename, Location, Plays, and Added. Those columns are not a History export.
- [OFFICIAL] Serato's [technical session logs](https://support.serato.com/hc/en-us/articles/5583242327951-Locating-providing-session-logs-for-Serato-DJ) are support diagnostics. They are not a play record and must not be parsed as one.
- [OFFICIAL] The [rekordbox 7.0.4 manual](https://cdn.rekordbox.com/files/20241004105932/rekordbox7.0.4_manual_EN.pdf) says Histories list tracks played for 1 minute or more in PERFORMANCE mode (`HISTORY yyyy-mm-dd`) or LINK EXPORT (`LINK HISTORY yyyy-mm-dd`). A history can be copied to a playlist. Playlist export to a file is `.txt` or `.m3u8`. This file also cites the later [rekordbox 7.2.14 manual](https://cdn.rekordbox.com/files/20260409151936/rekordbox7.214_manual_EN.pdf) for the Histories view. The installed app on the 2026-10-13 Mac check was 7.2.18. Neither manual was re-read against that build in this pass, and neither documents a per-track play timestamp inside a History export.
- [OFFICIAL] The [rekordbox operation FAQ](https://rekordbox.com/en/support/faq/operation-hints-7/) says USB play histories import only in EXPORT mode. Automatic import skips a history with the same name and contents as one already imported. Manual Import History reads the device's Histories folder. [OFFICIAL] The [October 2025 USB Export Guide](https://cdn.rekordbox.com/files/20251021171528/USB_export_guide_en_251007.pdf) says history imports automatically by default, and OneLibrary histories are not shown on Device Library gear, or the reverse.
- [OFFICIAL] [rekordbox XML](https://rekordbox.com/en/support/developer/) is a collection and playlist interchange file, not a play log. This file already cites AlphaTheta's [XML field list](https://cdn.rekordbox.com/files/20200410160904/xml_format_list.pdf) for `TrackID`, `Location`, `PlayCount`, and date-only `LastPlayed`. [UNOFFICIAL] The pyrekordbox field page was not used to confirm that list. `PlayCount` and `LastPlayed` are not individual play times.
- [UNOFFICIAL] Claims that Serato 4 stores history in `master.sqlite`, that Serato 3.x stores binary `.session` files, that a CSV contains only columns visible in the session view, and that rekordbox History txt is UTF-16LE and tab-separated are unverified. Do not parse `.session` files or `master.sqlite`.
- Neither app's crate or playlist membership, Plays, PlayCount, LastPlayed, or genre metadata proves where, when, and for whom a track was played.

### Confirmed, unknown, and still needing a sample

| Item | Status |
| --- | --- |
| Serato panel times: track Start Time, End Time, Playtime; session start, end, elapsed | [OFFICIAL] Confirmed. Duration example is seconds (`00:03:00`). Start/End precision is not stated. |
| Serato played rule and manual Insert / Mark Played / Start Session | [OFFICIAL] Confirmed. Whether an edit is flagged in an export is unknown. |
| Serato CSV headers, order, delimiter, encoding, 12/24-hour times, date on track rows | Needs a sample. |
| Serato deck column, unplayed rows, and inserted-track flag in the file | Needs a sample. Deck display on 4-deck hardware is [OFFICIAL] for the panel, not for the file. |
| Serato txt layout | Needs a sample. |
| Serato `.session` / `master.sqlite` layout | [UNOFFICIAL] only. Do not parse. |
| rekordbox History inclusion and dated names | [OFFICIAL] Confirmed for the 7.0.4 manual: at least 1 minute, `HISTORY yyyy-mm-dd` or `LINK HISTORY yyyy-mm-dd`. |
| rekordbox History txt columns, encoding, delimiter, and any per-track clock time | Needs a sample. The manuals consulted do not document per-track play timestamps. |
| rekordbox `.m3u8` contents | Needs a sample. |
| rekordbox.xml `TRACK` fields | [OFFICIAL] field-list PDF, already cited above. Verify a sample before a parser trusts it. |
| Time zone, UTC versus local, DST, CDJ clock | Unknown. Do not assume local machine time or a CDJ clock. |
| A set that crosses midnight | Unknown for rekordbox's date-named History. Serato documents a dated session start, not how a CSV represents the next day. |
| Editing, deleting, or renaming a rekordbox History, and whether the edit is detectable | Unknown. The manual documents copy-to-playlist and export-to-file. |
| Deleting or renaming a Serato session, and whether the edit leaves a trace | Unknown. |
| rekordbox tracks under 1 minute | Presumably absent. Not confirmed from a sample. |
| Serato minimum play duration | No minimum is documented. The fader rule is the documented gate. |
| Streaming-service identity in either export | Undocumented. |
| Stable ID shared by a Serato History export and a rekordbox History txt | Not documented. rekordbox `TrackID` is a collection-XML id, not a History-txt id. |

On the 2026-10-13 read-only Mac check, Serato DJ Pro 4.0.1 and rekordbox 7.2.18 were installed and not running. No History Export folder was present. Older `.session` files and Serato's `master.sqlite` were present and were not opened. Their presence does not confirm the unofficial storage claims. The rekordbox collection XML on disk is the library interchange file, not a History export.

A future sample set is one Serato CSV and one Serato txt, plus one rekordbox History `.txt` and one `.m3u8`, saved outside Git. Include a set that crosses midnight and one inserted or manually marked-played track. Show Start Time, End Time, Playtime, Deck, and Location in the Serato session view before export. Pin any parser to those samples. Treat every clock time as local and unverified until the sample is checked. Title plus artist plus duration, and path when the export has one, can be a review key. A low-confidence match stays `REVIEW`. Path identity breaks when a file moves, is renamed, or lives on another drive.

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

### Drift review (2026-10-13, source only)

Reviewed `scripts/dj_source_drift.py` from draft PR #42, including the
fail-closed hardening already on that branch. No second detector was added.

What already holds:

- A missing, symlinked, wrong-schema, or never-completed index is refused.
  The tool does not create a database.
- Malformed manifests, unexpected Serato paths, and symlinked source files
  fail closed. Missing sources are `SOURCE_UNAVAILABLE` or
  `EXPORT_UNAVAILABLE`, not `UNCHANGED`.
- A newer failed import does not replace the last successful baseline.
- An uppercase stored fingerprint compares unequal to the lowercase digest
  and reports `CHANGED`.

What `UNCHANGED` means, and what this draft made explicit:

- `scope.per_track_metadata_deltas` is `UNSUPPORTED`.
- `scope.live_rekordbox_collection` is `NOT_OBSERVED`.
- `scope.playback_history` is `NOT_OBSERVED`.
- `requires_attention: false` means the named source bytes match the last
  successful import. It does not mean the live Rekordbox collection is
  unchanged.
- `baseline_path` and `inspected_path` are both reported. A relocated
  directory whose relative bytes match can still be `UNCHANGED`; the two
  paths show that the override is not the original import directory.
- Files outside `database V2` and one-level `Subcrates/*.crate` are outside
  this comparison. A change to SmartCrates, History, or any other Serato
  file is not a drift result. Unexpected paths inside a saved manifest
  still fail closed.

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

### Offline rename preview before any of those steps

`scripts/preview_folder_rename.py` checks a proposed plan only. It does not
read the music library, Serato, or Rekordbox. A row becomes `CANDIDATE`
only with a positive `file_id`, a whole-file SHA-256, and a from/to pair
that `rewrite_serato_paths.normalize_rename_map` accepts. Case-only and
Unicode-equivalent paths stay `REVIEW`. Exact destination collisions and
chained renames stay `BLOCKED` and suppress the Serato map. An unchanged
path is `NO_CHANGE`. The script embeds no live genre-folder names.

```bash
python3 scripts/preview_folder_rename.py --plan "<private rename-plan.json>"
```

Illustrative plan shape, not a library path:

```json
{
  "schema_version": 1,
  "items": [
    {
      "file_id": 1,
      "sha256": "<64 lowercase hex characters>",
      "from": "Shelf/Old/a.mp3",
      "to": "Shelf/New/a.mp3"
    }
  ]
}
```

When every non-noop row is a candidate, the preview's `serato_rename_map`
is the input for the existing read-only command:

```bash
python3 scripts/rewrite_serato_paths.py preview \
  --serato-dir "<verified _Serato_ directory>" \
  --rename-map "<private serato_rename_map.json>" \
  --out "<private serato-path-preview.json>"
```

Rekordbox reference impact stays on the existing read-only preview, after
a fresh collection XML import. This plan does not parse that XML:

```bash
python3 scripts/rekordbox_relocation_preview.py --db "<trusted library index>"
```

`apply_authorized` is false. Audio relocation remains the mutation engine,
after the Mac canary, PASS backups, locks, and an approved plan.

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

### Exact track identity on top of those windows

`scripts/dj_event_playback_match.py` calls `dj_event_time.preview` and then
applies identity. A `CANDIDATE_ONLY` row requires the time tool's unique
verified window **and** all of the following:

- `track_sha256` equals exactly one synthetic library file's whole-file SHA-256;
- that file's `file_id` is the play's `file_id`;
- `source_artifact_sha256` and `session_id` match one provenance artifact
  whose `verified` flag is true and whose `source` names the export.

The same title on two hashes stays unresolved. One hash on two `file_id`s
stays unresolved. A hash that disagrees with `file_id` stays unresolved.
Overlapping windows, unverified set times, and DST gaps stay unresolved
even when the hash is exact. `proof_of_play` is false. There is no Serato
or Rekordbox parser in this script.

```bash
python3 scripts/dj_event_playback_match.py \
  --events "<private reviewed-set-windows.json>" \
  --plays "<private normalized-playback-fixture.json>" \
  --library "<private synthetic-library-files.json>" \
  --provenance "<private verified-source-artifacts.json>"
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
