# Landing-folder promotion — source-only plan

**Goal:** `music to move` is an inbox, not a permanent genre-library branch.
After a track's metadata and musical style are verified, a **reviewed batch**
should physically promote it to the existing canonical destination in the
main Music tree. No per-track manual dragging. Anything genuinely unresolved
goes to **one** explicit needs-review location. An upcoming-event hold is
temporary and intentional.

## Existing system (verified 2026-10-13)

- `survey_new_music_intake.py` and the `--lane intake` metadata batch
  classify or add Grouping, **but do not relocate the audio file**.
- `build_version_genre_plan.py` + `apply_version_genre_plan.py` already
  support safe version/genre sorting and moves **within a selected root**.
  An August 2026 run sorted 152 tracks **within** `music to move`.
  The inbox therefore did not automatically empty into the main library.
- The real library's destination layouts are **not uniform**:
  the historical verified VDBL merge used `Hip Hop/CLEAN/Hip-Hop/` etc.,
  `R&b/` flat, and `Amapiano /` with its actual trailing space.
  Do not manufacture new folder names from genre labels or change these
  structures without a current Mac inventory and a reviewed migration.
- Existing `Events` history is partly represented by Grouping:
  3,010 MP3s received additive `Event: ...` tags in August 2026.
  The underlying folder context was retained during that original sort.
  **Event-prep membership or an Event grouping tag does not prove a song
  was actually played.** Only a verified performance/play-history source
  can establish that fact.

## Recommended end state

- **Landing / `music to move`**: temporary unsorted downloads only.
- **Music**: single authoritative audio copy in the verified existing
  destination layout for each genre/version. If Genre, version or evidence
  is uncertain, use one central `_GENRE NEEDS CHECK` area.
- **Upcoming event**: Serato playlist/crate, plus a time-bound, explicitly
  approved hold if the track truly needs to remain physically staged.
- **Past event**: preserve the known event/source association in additive
  Grouping and Serato crate membership; store **actual played** history
  separately when proven by performance history (session/date/track ID).
  This gives repeat-avoidance insight without duplicating audio for each gig.

## Implemented this PR: read-only promotion preview

`scripts/preview_landing_promotion.py` accepts a current inventory,
a user-reviewed routing map, hash-bound post-tag genre approvals, and
explicit upcoming-event holds. It hashes every inventoried file and refuses
a partial/stale inventory; rejects missing/symlinked/outside-library
destinations; blocks occupied destinations or within-plan collisions.
It produces only **candidate** moves (to main Music or review), never an
apply plan. No Serato, metadata, file, folder or library writes.

Example routing map shape (ILLUSTRATIVE; verify actual folders first):

```json
{
  "schema_version": 1,
  "review_destination": "_GENRE NEEDS CHECK",
  "routes": {
    "R&B": {"default": "R&b"},
    "Hip-Hop": {
      "versions": {
        "CLEAN": "Hip Hop/CLEAN/Hip-Hop",
        "DIRTY": "Hip Hop/DIRTY/Hip-Hop"
      }
    }
  }
}
```

A candidate with no confirmed version route falls back to the central
review queue; it does not invent a version or folder.

Post-tag genre approval manifest (from a separately reviewed decision,
not a generic model guess):

```json
{
  "items": [
    {
      "source_relative": "song.mp3",
      "sha256": "<64-character hash of the current post-tag file>",
      "genre": "R&B",
      "approved_by": "operator",
      "evidence": "reviewed musical-style identification"
    }
  ]
}
```

Upcoming-event hold manifest (explicitly bound to current bytes):

```json
{"items": [
  {"source_relative": "event-prep/song.mp3", "sha256": "<64-character hash>"}
]}
```

An empty hold list is `{"items": []}`. A source under an `Events`
subfolder is held automatically until its active-vs-historical status
is reviewed.

**On the Mac**, with the actual library mounted, prepare a current read-only
inventory and verified destination/approval/hold manifests. Keep these
private artifacts **outside the repo** and outside the Music tree:

```bash
python3 scripts/inventory_library.py \
  --root "$HOME/Music/DJ-Music/Music/music to move" \
  --out-dir "<private report directory>"

python3 scripts/preview_landing_promotion.py \
  --library-root "$HOME/Music/DJ-Music/Music" \
  --landing-root "$HOME/Music/DJ-Music/Music/music to move" \
  --inventory "<private report directory>/inventory.csv" \
  --destinations "<private approved destination map.json>" \
  --approvals "<private post-tag approvals.json>" \
  --event-holds "<private upcoming-event holds.json>" \
  --out "<private report directory>/landing-promotion-preview.json"
```

Statuses:

- `ROUTE_CANDIDATE`: approved canonical Genre + content SHA + a real
  confirmed destination, with no collision.
- `REVIEW_CANDIDATE`: track has no approved Genre or matching
  destination; propose a safe physical review queue without guessing
  a musical style.
- `EVENT_HOLD`: explicit upcoming-event hold or unknown event-folder
  purpose; leaves the file staged.
- `BLOCKED`: collision or unsafe destination; requires resolution.

All proposed moves **still require** real-path and Serato-impact proofs.

## Required next implementation: approved promotion/apply

The current preview is **not an apply command**. Before building or running
a production cross-folder relocation lane:

1. Confirm the live Mac folders and the destination map; decide the final
   shape once, reusing existing directories where possible. Include previously
   sorted subfolders inside `music to move` in the candidate scan.
2. Reconcile Genre output and current tags (including already misclassified
   Atlanta Bass, Afro House, etc.), then produce post-tag hash-bound approvals.
   A mere existing tag cannot prove classification accuracy.
3. Inspect existing Serato library/crate and Rekordbox relationships and
   identify path-dependent references. Preserve selected playlists and
   performance metadata. Move/relink plans must be coordinated and verified,
   not a blind filesystem rename.
4. Require **all touched folder locks**, exact-source backups and PASS
   manifests, no-overwrite/no-delete semantics, mutation-engine journaling,
   rollback evidence, and post-move path/hash/metadata/Serato verification.
   The existing one-folder batch should **not** be generalized by merely
   passing the top-level Music root as a lock.
5. Promote a whole approved batch, including ready tracks into genre folders
   and unresolved tracks into the central review queue. Report exactly what
   remains staged and why; do not silently delete empty directories.
6. Preserve actual played-history evidence from a verified source, distinct
   from event-prep crate membership. Do not date or invent a play from a folder
   name alone.

**Completion criterion:** one verified physical copy per intended track,
the landing area free of processed tracks, only explicit temporary event
holds remaining there, Serato/Rekordbox references intact, and a complete
move/undo/history report.

**Current limitation:** the DJ Mac was offline during source preparation.
Current folder contents, exact destinations, current Serato references,
and true already-misclassified track counts have not been verified. No
production move is approved until those observations are available.
