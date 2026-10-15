# Landing-folder promotion — source-only plan

**Goal:** `music to move` is an inbox, not a permanent genre-library branch.
After a track's metadata and musical style are verified, a **reviewed batch**
should physically promote it to the existing canonical destination in the
main Music tree. No per-track manual dragging. Anything genuinely unresolved
stays in the landing folder with a **NEEDS_REVIEW** status. No new physical
holding folder is needed. An upcoming-event hold is temporary and intentional.

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
  is uncertain, hold the track in the existing landing folder and list it
  in a review report. Do not create a second permanent holding area.
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
destinations; blocks occupied destinations or within-plan collisions,
including names that differ only in case or Unicode normalization. It
also checks explicit version-vs-folder conflicts and stale approvals.
It produces only **candidate** moves into verified Music folders; unresolved
tracks are held in place. The optional Serato candidate rename map is for
`rewrite_serato_paths.py preview` only. No Serato, metadata, file, folder
or library writes.

Example routing map shape (ILLUSTRATIVE; verify actual folders first):

```json
{
  "schema_version": 1,
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

A track with no confirmed version route receives `NEEDS_REVIEW` in the
landing folder; no new physical folder or version is invented. A verified
empty landing folder produces a zero-item report.

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

Optional **Serato path-impact preview**, after verifying the real mounted
volume root (e.g. `/` for an internal-volume library). The second file is
only a candidate map, **not** a path-rewrite approval:

```bash
python3 scripts/preview_landing_promotion.py \
  --library-root "$HOME/Music/DJ-Music/Music" \
  --landing-root "$HOME/Music/DJ-Music/Music/music to move" \
  --inventory "<private report directory>/inventory.csv" \
  --destinations "<private approved destination map.json>" \
  --approvals "<private post-tag approvals.json>" \
  --event-holds "<private upcoming-event holds.json>" \
  --serato-volume-root "<verified mounted root of this Music volume>" \
  --rename-map-out "<private report directory>/candidate-serato-renames.json" \
  --out "<private report directory>/landing-promotion-preview.json"

python3 scripts/rewrite_serato_paths.py preview \
  --serato-dir "<verified read-only _Serato_ directory>" \
  --rename-map "<private report directory>/candidate-serato-renames.json" \
  --out "<private report directory>/serato-path-impact-preview.json"
```

The Serato preview reports `pfil` and `ptrk` reference impacts. Any
candidate with zero matches remains subject to an explicit missing/ambiguous
reference review; a no-match result is not proof the track is unused.
Rekordbox's read-only XML relocation preview must also be checked wherever
that library references the track. Neither preview modifies Serato or
Rekordbox. No apply stage is added by this PR.

Statuses:

- `ROUTE_CANDIDATE`: approved canonical Genre + content SHA + a real
  confirmed destination, with no collision.
- `NEEDS_REVIEW`: Genre/version/destination approval is missing or
  contradictory; keep the track in the landing folder without guessing.
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
5. Promote a whole approved batch of ready tracks into genre folders;
   unresolved tracks remain in the landing folder and the review report.
   Report exactly what remains staged and why; do not silently delete empty
   directories.
6. Preserve actual played-history evidence from a verified source, distinct
   from event-prep crate membership. Do not date or invent a play from a folder
   name alone.

## Proposed operator parameters for the first live test

These are **release gates for a future apply implementation**. They are
documented defaults, **not active auto-move controls**, because no
cross-folder apply command exists in this PR.

| Gate | First live run |
|---|---|
| Authorization | Default **OFF**; an explicit reviewed plan and per-batch approval |
| First batch size | **Up to 5 tracks**, each individually approved; no silent expansion |
| Classification | Canonical Genre + reviewed evidence; conflict or uncertainty = HOLD |
| Physical destinations | Existing, exact, verified folder paths; no new folders |
| Collisions | **0** allowed; no replacement, suffix auto-rename or deletion |
| DJ apps | Both closed before path changes; Serato and Rekordbox path impact reviewed |
| Backups | Full affected source/metadata and DJ-reference backups; manifest **PASS** |
| Path protection | Exclusive source **and** destination folder locks and undo journal |
| Integrity | For move-only tracks, exact full-file SHA-256 after relocation |
| DJ metadata | Artwork, cue points, beatgrids, custom Serato frames and playlist links preserved |
| Stop rule | First failed verification = stop and recover; never skip a failed item |
| Event exceptions | Upcoming holds honored; a crate association does not claim a recorded play |
| Automatic unattended mode | **DISABLED** until a real supervised canary succeeds |

After the first supervised pass, review **all five results** and test a real
undo/restore before approving any larger batch. Only then consider a
25-track supervised batch; no scheduled watcher or unattended filesystem
moves are part of this milestone. A read-only report may cover the entire
library, while the apply budget remains limited.

`input_fingerprints` in each preview records SHA-256 evidence for the exact
inventory CSV, approved route map, approvals, event holds and master genre
rules. These are comparison fingerprints, **not an operator signature**.
An apply implementation must revalidate current source hashes, rules,
destinations and DJ reference snapshots immediately before each move.

**Completion criterion:** one verified physical copy per intended track,
the landing area free of processed tracks, only explicit temporary event
holds remaining there, Serato/Rekordbox references intact, and a complete
move/undo/history report.

**Current limitation:** the DJ Mac was offline during source preparation.
Current folder contents, exact destinations, current Serato references,
and true already-misclassified track counts have not been verified. No
production move is approved until those observations are available.
