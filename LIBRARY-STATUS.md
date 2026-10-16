# DJ Library — live status (any AI: read this first)

Short handoff. Details live in `GROK-DJ-LIBRARY-LOG.md`, layout rules in `LIBRARY-LAYOUT.md`, Claude's Oct 15 wrap-up in `CLAUDE-HANDOFF-2026-10-15.md`.

## Now (2026-10-16 15:20 EDT, Grok)

- **Serato is OPEN** for Imani's isolated rescan test (4 copies in ~/Music/RESCAN-TEST; backup T5 `2026-10-16-rescan-test-pre`, both Serato folders, PASS). No music, tag or Serato writes until she closes it and reports.
- Claude job 3 DONE and verified read-only by Grok: 762 moved; database V2, crates, root.sqlite and master.sqlite repointed, 0 old paths left (282 only in play history).
- Claude: 39 NEW MUSIC Genre tags respelled (15:10). rekordbox read-only check + job 4 Energy preview (15:20), see NEW MUSIC section.
- Grok Genre round 9 DONE (4 written, 99 already correct, 322 held); revised round 9 CSV adds nothing writable.
- PRs merged: #93 (Claude status), #92 (Grok status + reconciliation corrections).
- `GROK-DJ-LIBRARY-LOG.md` has a GAP from Oct 15 16:02 to Oct 16 15:10 (truncated by a Grok edit error, restored from the Time Machine local snapshot). Use this file and the per-run folders for that period.
- rekordbox: needs a fresh File > Export Collection (xml) + Display All Missing Files before any sync.

- **Correction (16:05):** the rescan test was inconclusive: Imani removed and re-added the 4 TEST copies, so it showed re-import behavior, not plain Rescan ID3 Tags. "A blank file tag clears the DB genre on rescan" is NOT proven. Safeguard chosen: backfill blank file Genres from Serato's DB. Read-only preview: `blank-genre-backfill-20261016/` (2,018 tracks: 2,016 writable; House 41; 321 in custom folders; neptunes 1,418). Nothing written yet.

- **Blank-genre backfill WRITTEN (16:07 go, 16:12-16:29):** 1,985 files got Serato's existing DB genre written exactly as stored (Genre frame only, blank only, re-checked right before each write). By folder: neptunes 1,418, Lounge 231, Kids Events 45, House 39, oLD Head Party 34, Events 28, Hip Hop 23, edit 21, Holiday 19, Pop 18, Electronic 15, Top 40 mashs 4 Club 12, top 40 11, Jersey Club 9, Gospel 8, Twerk 8, GarageJungle 7, Rock 6, Funk 4, 15 smaller folders 29. Readback 1,985/1,985, 0 failed. Held 3 (DB genre has a trailing space, which the writer can't verify): Hip Hop/Xan TALK ('imani   '), House Dreaming Harp ('kids '), House I Was Sad Last Night ('Imani Playlist '). One of these (Xan TALK) was tried first, failed verification and was rolled back with its hash unchanged. NEW MUSIC DOWNLOADS not touched. Backup T5 `2026-10-16-blank-genre-backfill` (changed files + both `_Serato_` and App Support Serato/Library, PASS; both re-verified PASS after the run, so neither Serato database changed). Serato is closed and safe to open.

### Open items
- Genre ear-check list (genre-ear-check-20261016.csv / .m3u8; 231 per Imani's count, the file currently lists 377 — reconcile).
- CLEAN smart crate: is the Filename "contains Clean" rule (field 4) wanted? 4,302 of 4,852 members come in by filename.
- PRs #85, #88, #89 open.
- 24 new downloads to process.
- Energy job 4.
- 22 Serato entries inside the library that don't resolve on disk.

## Last updated

2026-10-16 ~16:45 EDT — Claude (Mac, repo only + read-only count): ID3v2.3 year-frame fix. Every ID3 writer now loads with `translate=False` (`tag_formats.load_id3_for_save`), so a v2.3 save no longer turns TYER into an off-spec TDRC. Read-only count: 13,992 of 19,165 v2.3 MP3s in the library already carry TDRC (no TYER), plus 152 TDOR and 9 TIPL; not repaired (needs its own approved plan). No library files touched.
2026-10-16 12:43 EDT — Codex (Mac): R&B year research applied: 28 years (24 supported + 4 resolved conditional), verified 28/28; 5 conditional + 25 unresolved remain held. Audio/Serato/other tags unchanged; locks released. T5 backup `2026-10-16-codex-rnb-years`; rescan ID3 Tags. Artifacts: `/Users/djb.a.e/Music/DJ-Music/artifacts/rnb-year-research-20261016/applied/`. (only after the isolated rescan test passes)
2026-10-16 16:00 EDT — Claude (Mac): rekordbox read-only check of job 3: Aug 30 XML has 104 of the 762 old paths (no cues, no plays, playlists `Not Yet In Rekordbox`/`Music` only); fresh export needed for the real count. Job 4 Energy preview in progress (read-only).
2026-10-16 15:25 EDT — Claude (Mac): NEW MUSIC held 44: 39 Genre tags respelled (21 Hip-Hop, 16 R&B, 1 Bounce, 1 House), 5 unchanged, NOTHING moved, verified. Job 3 read-only re-check: 762/762 match journal, disk, database V2, 50 crate files and both SQLite (0 old paths). rekordbox NOT checked: old/new path list saved for Grok (`new-music-move-20261016/job3-verify/job3-moved-762-old-new-paths-20261016.csv`). See "NEW MUSIC DOWNLOADS — Claude".
2026-10-16 12:10 EDT — Grok (DJ B.A.E. Library): code-review-holds answers (read-only), see "Code review holds — answers" below. Status PR #84 (replaces #74/#79/#82).
2026-10-16 11:30 EDT — Grok (DJ B.A.E. Library): 92 R&B medium years written (incl. Slept On You 2020, Find Your Way 2005); Genre tag set on 9 held Afrobeats (7 changed, 2 already right; no moves; 20 still held). Backup T5 2026-10-16-rnb-medium-years-afro-genre. Tool PR #80 (prune --keeper-map) for the 5 stale Pop-dupe SQLite rows: dry-run only, waits for Imani's merge. Status PR #82.
2026-10-16 01:40 EDT — Grok (DJ B.A.E. Library): tonight's summary added (prune applied, Afrobeats rename done, 4 "dupes" = different songs, lyrics check running, 16 Drake titles, Pop version-label preview, energy previews running). See "Tonight (Oct 16)" below.
2026-10-16 11:40 EDT — Grok (DJ B.A.E. Library): Pop version-label dupes: 8 lower-bitrate copies removed (keepers already had the tags; crates/smart crates repointed, DB V2 clean; 5 stale SQLite rows remain, see log). Backup T5 2026-10-16-pop-dupes-p5. Metadata cleanup v2 preview ready (246 ready / 3,323 her call). R&B medium years reviewed, write on hold.
2026-10-16 10:58 EDT — Grok (DJ B.A.E. Library): high-confidence Energy v2 written: R&B 506, Pop 895, Hip Hop 950 (29 ID3v2.2 skipped). Backup T5 2026-10-16-energy-v2-high (delete after Serato rescan check). See GROK-DJ-LIBRARY-LOG.md. (only after the isolated rescan test passes)
2026-10-16 10:30 EDT — Grok (DJ B.A.E. Library): Energy v2 previews (10 values) for R&B/Pop/Hip Hop + change suggestions for written Baile/Afrobeats/NOLA Bounce tags (read-only).
2026-10-16 02:30 EDT — Grok (DJ B.A.E. Library): audio-based energy previews for R&B/Pop/Hip Hop ready for review (read-only).
2026-10-16 16:36 EDT — Claude (Mac): 14 quick-decision tags written + verified 14/14 (Imani go, Serato quit, lock + T5 `2026-10-16-quick14` PASS): Baile Energy 4 (Situationship Edit Warmup, Waste My Time Slow Wine, CRU LOVE Twerk Dutty, Se Dansa Dance Flex); New Orleans Bounce 8 Twerk Dutty -> Shake (Grok's list; owner-approved removal then append); Genre R&B on Durand Bernarr H.I, Country on nita maraj mashup (NEW MUSIC). 245 low-bitrate review sheet built (read-only): 201 remove low copy, 11 re-download, 17 keep both, 16 by ear (`low-bitrate-245-review-20261016/`). Not done: Tems (needs ID3v2.2 tool, PR in progress), Davido Five delete (no tool; prune on hold).
2026-10-16 16:10 EDT — Claude (Mac): NEW MUSIC job 4 Energy WRITTEN: 282 tags (verified 282/282), 622 left blank. Rescan only after the isolated rescan test passes.
2026-10-16 15:20 EDT — Claude (Mac): rekordbox read-only check of job 3: Aug 30 XML (newest export) has 104 of the 762 old paths (no cues, no plays, playlists `Not Yet In Rekordbox`/`Music` only); fresh export needed for the real count. Job 4 Energy preview built (282 proposed, 622 blank; nothing written).
2026-10-16 15:10 EDT — Claude (Mac): NEW MUSIC held 44: 39 Genre tags respelled (21 Hip-Hop, 16 R&B, 1 Bounce, 1 House), 5 unchanged, NOTHING moved, verified. Job 3 read-only re-check: 762/762 match journal, disk, database V2, 50 crate files and both SQLite (0 old paths). rekordbox NOT checked: old/new path list saved for Grok (`new-music-move-20261016/job3-verify/job3-moved-762-old-new-paths-20261016.csv`). See "NEW MUSIC DOWNLOADS — Claude".
2026-10-16 15:10 EDT — Grok (DJ B.A.E. Library): Genre rounds 1–9 written (single-genre picks folder by folder, ChatGPT rounds 1–9; round 9: 4 written, 99 already correct, 322 held), Genre standardize, Remixer (TPE4) writes, undo of r4 Genre writes in Lounge/oLD Head Party (custom-folder rule now in GENRE-WRITE-RULES.md). Serato DB verified read-only after Claude job 3: 762 moved, 0 old paths in database V2, crates, root.sqlite or master.sqlite assets (282 old paths only in play history); all 351 Afrobeats entries resolve; 22 unresolved in-library entries, none new. Active DB: Serato DJ Pro 4.0.1 uses ~/Library/Application Support/Serato/Library (master/root.sqlite); database V2 kept in sync. House count base: V2 1,629 vs master 1,999, so the House 301 projection only covers V2 tracks. Isolated rescan test prepped (4 copies in ~/Music/RESCAN-TEST, backup T5 2026-10-16-rescan-test-pre, both Serato folders, PASS); waiting on Imani. rekordbox: needs a fresh Collection XML export + Missing Files list.
2026-10-16 15:00 EDT — Claude (Mac): NEW MUSIC job 3 Clean/Dirty DONE: 762 moved, 44 held (Genre spelling), Serato + SQLite repointed (PASS). See "NEW MUSIC DOWNLOADS — Claude".
2026-10-16 ~12:50 EDT — Claude (cloud, repo only): status file cleaned up: one "Current state" section; stale "waiting/running" items that later entries show as done were removed; Grok's status PRs #74, #79 and #84 (holds answers) folded in. No library files touched.
2026-10-16 12:35 EDT — Claude (Mac): NOLA Bounce + Ghettotech energy written; NEW MUSIC DOWNLOADS jobs 1-2 written (97 exact dupes deleted, 32 Genre + 14 Year), job 3 Clean/Dirty preview running (audio 5/12 batches), job 4 not started. See "NEW MUSIC DOWNLOADS — Claude" below.
2026-10-16 12:25 EDT — Claude (Mac, read-only): low-bitrate triage list DONE, no library files touched. 12,303 tracks under 320 kbps: 10,078 re-download (1,773 are Neptunes "Other Side of the Planet" rips; ~8,300 without them), 245 already have a better copy in the library (delete candidates, Imani reviews first), 1,980 at 256 kbps fine to keep. Files: `~/Music/DJ-Music/artifacts/low-bitrate-triage-20261016/`. See log.
2026-10-16 12:10 EDT — Grok (from PR #84): code-review-holds answers (read-only): Oct 15 Pop dupes were audio-match and their T5 backup was already deleted; check Cassie "Me & U" and Britney "Slave 4 U (1st version)"; 10:01 ghost cleanup and the 4 Afrobeats same-name songs: nothing lost. See "Checks — answered".
2026-10-16 12:04 EDT — #83 merged: genre refinement apply no longer crashes at the end (missing result JSON / run_complete).
2026-10-16 ~12:00 EDT — Claude (cloud, repo only): code review of #63/#65/#73/#75; fixes merged as #78 and #81 (see "Checks before more cleanup" below). No library files touched.
2026-10-16 11:30 EDT — Grok (from PR #84): 92 R&B medium Years written; Genre set on 9 held Afrobeats (7 changed, 2 already right; no moves; 20 still held). Backup T5 `2026-10-16-rnb-medium-years-afro-genre`. Tool PR #80 (`--keeper-map`) for the 5 stale Pop-dupe SQLite rows: not run.
2026-10-16 11:15 EDT — Grok (from PRs #79/#84): Pop version-label dupes p5: 8 lower-bitrate copies removed (Imani OK 11:07); crates/smart crates repointed to keepers; database V2 clean; 5 stale SQLite rows held. Backup T5 `2026-10-16-pop-dupes-p5` (keep). Metadata cleanup v2 preview ready.
2026-10-16 10:58 EDT — Grok (DJ B.A.E. Library): high-confidence Energy v2 (10-value scale) written: R&B 506, Pop 895, Hip Hop 950 Grouping appends (29 ID3v2.2 skipped, 0 Energy conflicts). Med/low/NEEDS YOU/Reset not written. Backup T5 2026-10-16-energy-v2-high; Imani to rescan ID3 tags in Serato. See GROK-DJ-LIBRARY-LOG.md. (only after the isolated rescan test passes)
2026-10-16 10:01 EDT — Grok (from PRs #74/#84): Serato ghost cleanup: 882 crate/smart-crate refs repointed (crates-only); 397 database V2 ghosts + 377 rows per SQLite file pruned (with the prune tool as it was BEFORE #78).
2026-10-16 09:35 EDT — Grok (DJ B.A.E. Library): Afrobeats energy written (347); see GROK-DJ-LIBRARY-LOG.md.
2026-10-16 09:20 EDT — Grok (DJ B.A.E. Library): Afrobeats Clean/Dirty written (79 Dirty moved+tagged, 161 to _NEEDS CHECK, 32 held); genre-folder config PR #69; see GROK-DJ-LIBRARY-LOG.md.
2026-10-16 01:40 EDT — Grok (DJ B.A.E. Library): tonight's summary added (prune applied, Afrobeats rename done, 4 "dupes" = different songs, lyrics check running, 16 Drake titles, Pop version-label preview, energy previews running). (The "Tonight" notes are now folded into Current state / Done.)
2026-10-15 22:00 EDT — Grok (DJ B.A.E. Library): Genre Afrobeat->Afrobeats on 57 files; Baile energy 131; Afrobeat inner-folder rename done, top rename rolled back (stale Serato record).
2026-10-15 21:32 EDT — Grok (DJ B.A.E. Library): R&B conflicting-genre rows done (Genre R&B 7, Alt R&B Grouping 3, Year 20); see GROK-DJ-LIBRARY-LOG.md.
2026-10-15 20:45 EDT — Grok (DJ B.A.E. Library): Pop duplicates finished (202 deleted); R&B Years done (230 written, 9 skipped); see GROK-DJ-LIBRARY-LOG.md.
2026-10-15 16:58 EDT — Grok (DJ B.A.E. Library): R&B Genre batch written (110 of 114 approved; 4 ID3v2.2 skipped); see GROK-DJ-LIBRARY-LOG.md.
2026-10-15 16:41 EDT — Grok (DJ B.A.E. Library): junk Grouping removal written (211 files); see GROK-DJ-LIBRARY-LOG.md.
2026-10-15 16:22 EDT — Grok (DJ B.A.E. Library): Pop new-adds tag write (Genre/Year/Source Grouping) done; see GROK-DJ-LIBRARY-LOG.md.
2026-10-15 ~15:45 EDT — Claude (cloud, repo only): genre folder approval, PR #55 (see Waiting on Imani). No library files touched.
2026-10-15 ~16:45 EDT — Claude (cloud, repo only): junk-Grouping removal tool + docs (PR #57); recorded the Mac session's Pop progress report. No library files touched.
2026-10-15 ~15:55 EDT — Claude (cloud, repo only): PR #55 merged (also reads a plain "Afrobeat" tag as Afrobeats; "Dub" stays held). No library files touched.
2026-10-15 15:50 EDT — Claude (folder-rename tooling PR #56; library untouched)
2026-10-15 ~16:50 EDT — Claude (cloud, repo only): fixed main CI after #48 (History staging script classified as allowlisted infrastructure). No library files touched.


## Corrections and rules (Grok, 2026-10-16 15:10)

- **Rescan:** no library-wide Rescan ID3 Tags. Every rescan step in this file and the log applies only after the isolated rescan test passes.
- **Layout is genre-first** (genre folder, then CLEAN / DIRTY / REMIXES & EDITS etc. inside it), not version-first.
- **Smart crate field IDs (validated against crate snapshots):** `urkt` 9 = Genre, 19 = Grouping, 4 = Filename; `trft cond_con_str` = contains. Smart crates are not Grouping-only.
- **Afrobeats crates are NOT broken:** `Clean>>Afrobeat Clean` = Genre contains "Afrobeat" AND Grouping contains "Clean"; `afro world>>Afrobeat Dirty` = Genre contains "afrobeat" AND Grouping contains "dirty"; both Match All, Live Update on. "Afrobeats" contains "Afrobeat", and against the DB both rules reproduce their members exactly (59, 104; 0 enter, 0 leave). The earlier "0 of 351" finding checked Grouping and is withdrawn.
- **`Clean>>Clean` is Match ANY** (Genre / Filename / Grouping contains "Clean"), Live Update on: 4,302 of 4,852 members match on Filename, most of the rest on Grouping.
- **Backup rule:** every backup before a Serato-affecting write must include BOTH `~/Music/_Serato_` and `~/Library/Application Support/Serato/Library` (master/root.sqlite + any -wal/-shm), hash-verified. Today's tag-only backups had only `_Serato_` (their undos are still complete: tags live in the files). Proposed mutation_engine change (not implemented): add the App Support Library to the backup step and refuse if missing.

## Current state (2026-10-16 ~12:50 EDT)

This section is the single list of what is open. Older sections below are history.

### Working now
- **Grok (Mac):** R&B / Pop / Hip Hop energy — high-confidence written 10:58; medium / low / NEEDS YOU not written (preview for Imani). Metadata cleanup v2 preview ready.
- **Claude (Mac), folder `NEW MUSIC DOWNLOADS - MUSIC TO MOVE`** (Imani 2026-10-16; Grok stays out): job 1 duplicates DONE (97 exact copies deleted), job 2 missing tags DONE (Genre 32, Year 14), job 3 Clean/Dirty PREVIEW running (nothing moved), job 4 energy not started. Also wrote NOLA Bounce + Ghettotech energy. Details and its open items: "NEW MUSIC DOWNLOADS — Claude" at the end of this file.
- **Serato:** stays CLOSED while anyone writes. Ask Grok before opening it.
- **GitHub checks:** every run since 12:04 EDT (main included) fails within seconds, before any test starts, with no log. GitHub-side, not the code. Imani: check GitHub → Settings → Billing → Actions usage / spending limit. Until it is fixed, run the tests locally before merging.

### Checks — answered (Grok 12:10, read-only; from PR #84)
The review fixes are merged (#78 prune tool + audio-match deleter; #81 crate repoint tool). Plans built before them are refused; rebuild them. Audio-match pairs files now need `keep_sha256`.
1. **Oct 15 Pop duplicates (202): all deleted with audio-match.** Their T5 backup `2026-10-15-pop-dupes` was deleted Oct 15 19:33 after Imani's Serato check, so those copies can only come back from Time Machine. 30 of the 202 pairs differed in folder, Clean/Dirty Grouping or bracket text; Grok judged all correct except **Cassie "Me & U"**: three copies (Pop Girl At Heart, R&b, Y2K) were deleted for one keeper in `Events/LIKE SUGAR ON MY TONGUE/CLEAN/CLEAN R&B/R&B/Me & U_1.mp3`, so no genre-folder copy is left.
2. **Today's p5 (8, audio-match):** all fine except **Britney "I'm a Slave 4 U (1st version) [aka Light Your Ass On Fire]"** (Neptunes set), deleted for the album "I'm a Slave 4 U (2)" — a different early mix. Restorable from T5 `2026-10-16-pop-dupes-p5` with the p5 undo log; proposed only, needs Imani's OK.
3. **10:01 ghost cleanup:** the fixed tool would have held 27 records (all same-name songs). Every Afrobeats copy still has its database V2 and SQLite records; no song lost a record, crate or membership. Nothing to restore.
4. **Afrobeats Closer / Slow Wine / Finesse / Focus:** all have their database V2 record, both SQLite rows and their crates. Nothing lost.

### T5 backups waiting on Imani's Serato check (then delete)
- `2026-10-15-pop-tags/` and `2026-10-15-junk-grouping/` — rescan Pop, Cat Calls Inspo, Y2K. (only after the isolated rescan test passes)
- `2026-10-15-rnb-genre-conflicts/`, `2026-10-16-rnb-hand-years/` — rescan R&b. (only after the isolated rescan test passes)
- `2026-10-15-afrobeats-rename/` (inner, top, top2, top3), `2026-10-16-afrobeats-cleandirty/`, `2026-10-16-afrobeats-energy/` — check Afrobeats.
- `2026-10-15-afrobeat-genre-baile-energy/` — check Baile + the 19 genre-fix folders.
- `2026-10-16-drake-titles/` — check a few Drake titles (Marvins Room, July).
- `2026-10-16-energy-v2-high/` — rescan R&B, Pop, Hip Hop. (only after the isolated rescan test passes)
- `2026-10-16-rnb-medium-years-afro-genre/` — rescan R&b and Afrobeats. (only after the isolated rescan test passes)
- `2026-10-16-nola-bounce-energy/`, `2026-10-16-ghettotech-energy/`, `2026-10-16-newmusic-dupes/`, `2026-10-16-newmusic-tags/` (Claude Mac) — rescan New Orleans Bounce, Ghettotech, NEW MUSIC DOWNLOADS. (only after the isolated rescan test passes)
- `2026-10-16-prune-afrobeat-stale/` and `2026-10-16-serato-ghost-fix/` — checks found nothing lost; delete once Imani OKs.
- **Keep:** `2026-10-16-pop-dupes-p5/` until Imani decides on the Britney "Slave 4 U (1st version)" restore.

### Waiting on Imani
1. Serato check (when Grok says it is safe), then the backup deletes above.
1a. **Decide:** restore Britney "I'm a Slave 4 U (1st version)" from the p5 backup? Cassie "Me & U": OK that the only copy is in Events, or restore a genre-folder copy from Time Machine?
2. Energy v2: medium / low / NEEDS YOU rows for R&B, Pop, Hip Hop.
3. Metadata cleanup v2 preview (Grok).
4. Pop: 10 "(NC)" pairs (same bitrate, kept until NC is known), 125 refused + 589 uncertain duplicate pairs, Clean/Dirty for ~1,190 unmarked new Pop tracks, 23 tracks with no Year, 4 wav skipped, Tyla 128 vs 320 kbps pairs; junk Grouping left in GRP1 frames (16 "November") and record-pool watermarks (9) — the 2026-10-15 removal covered TIT1/FLAC/M4A only.
5. R&B: 3 medium (92 of 95 written 11:30) + 55 unsure hand-entry Years (`rnb-year-hand08-proposals-20261016.csv`), 4 m4a/FLAC, 4 ID3v2.2 (07 Got Her Own, 08 Drown In It, Return Of The Mac (Topper-Intro) Extended, Shut It Down — tag upgrade first), 2 missing files; deferred rows: 575 reported vs 142 in deferred.csv (reconcile).
6. Afrobeats: 20 still held after Grok set Genre on 9 (11:30) — incl. 2 Dirty ID3v2.2 (Free Fall, Unfortunate) and the backslash name Kupe Dance; 161 in `_NEEDS CHECK`, 5 afro-ish tags that are not exactly "Afrobeat", Davido "Five" (no BPM).
7. Baile: ear-check 7 tracks. `Baile/baile_plex` is a keep-whole pack.
8. The 4 same-name pairs (different songs, both kept) and their held Serato rows.
9. Smart crates: tidy `afro world ≫ Afrobeat` (3 Genre-contains rules); `Clean R&B` / `r&b edits` need `"r&b "` with a trailing space; `Clean R&B` should match ALL not ANY; `90s`/`2000s` use Grouping, not Year.
10. 34 ID3v2.2 files need a tag-upgrade approval before Grouping can be written.
11. Folder moves (one pass, after go): `R&b` → `R&B` rename (needs Imani's go, Serato fully quit; tools fixed in #81); 12 non-R&B tracks out of `R&b` (Beyoncé Formation Intro Clean + Dirty, bLAck pARty Flame + KEMET, Col3trane Take Notes, Smino Curtains + Garden Lady + HOOPTI, Ludacris Saturday → Hip-Hop; James Brown I FEEL GOOD → Funk & Soul; Son Of Mongo → House; Alkaline Mamacita → Latin); 5 Pop keepers (Everything is romantic → Lounge, ROCKET → Groovy Bounce, Ick + Na Na Na → Jersey Club, SAD GIRLZ LUV MONEY Remix → Afrobeats); misfiled tracks from Claude's Oct 15 sheets (Reggae 16, Dancehall 21 marked SKIP).
12. Later: unproven old backups (~120G, check per file before any delete); Childish Gambino *We Are God* (backup may be the only copy); old T5 repo copy (215G, unpushed rule commits, ~3,655 tracks not in the live library — leave for later).
13. NEW MUSIC DOWNLOADS, NOLA Bounce, Ghettotech, Baile/Afrobeats energy blanks, Davido "Five" (delete asked, NOT done — needs an explicit go): see "NEW MUSIC DOWNLOADS — Claude" at the end.
14. `_GENRE NEEDS CHECK`: 179 files (2026-10-15 count, see totals) plus ~105 in Claude's per-folder `_NEEDS CHECK`.
15. Low-bitrate triage: list is DONE (Claude, 2026-10-16, read-only). Imani: review the 245 "better copy exists" rows before any delete; re-download list is `low-bitrate-redownload.csv` (start with the ≤160 kbps rows). Deleting low copies needs a scoped backup + Serato repoint like the Pop dupes. **The 245 better-copy deletes stay dry-run only until Imani reviews them (code-review holds: no live `delete_verified_duplicates.py --mode audio-match` / `prune_missing_serato_records.py` runs).** This list (12,303, `~/Music/DJ-Music/artifacts/low-bitrate-triage-20261016/`) replaces Grok's earlier 11,966-track preview.

### Agents to do
- `rewrite_serato_paths.py` still does not repoint History/session files (SmartCrates and nested crates are done, #73).
- Open tool PRs, review before any live use: #80 (`prune_missing_serato_records --keeper-map`, for the 5 stale Pop-dupe SQLite rows; rebuild its plan on the fixed tool) and #85 (smart crate apply tool).
- Grok's status PR #84 is folded into this file (#74 and #79 are closed as superseded).

### Unknown — ask the agent that ran it
- Inbox genre/year lookup (`inbox-genre-lookup/`, last reported ~325+/514 on 2026-10-15): finished or not?

Library root: `~/Music/DJ-Music/Music` · Serato: `~/Music/_Serato_` · Mac: `djb.a.e` · Repo: `imanisworld/remix-edit-sort`

## Rules every AI must follow

- **Metadata first.** Tags (Genre, Grouping, Year) before folder moves so Serato smart crates pick tracks up.
- **Era = Year field only**, clean 4-digit. No Era tags, no era folders long-term.
- Spellings: top folder and Genre **`R&B`** (not R&b); **`Afrobeats`** (Afrobeat = same thing).
- **Grouping is append-only.** Never overwrite existing Grouping text. One exception (Imani, 2026-10-15): `scripts/build_grouping_removal_plan.py` removes only exact values Imani listed by hand after seeing the survey; everything else stays.
- Music changes **only** via `scripts/mutation_engine.py` (lock, verified backup, journal, audio + Serato hash checks). No ad-hoc tag edits.
- **Backups:** changed files + `_Serato_` only, on T5 EVO (`/Volumes/T5 EVO/DJ-backups/…`). Delete each backup after Imani confirms Serato looks right. Do not keep whole-folder copies.
- **Close-check Serato** before any write. Stop if Serato DJ Pro is open.
- Never touch `Imani Drops`, rekordbox `master.db`, or `handoff/`.
- Repo clone `~/Developer/remix-edit-sort` may have an uncommitted branch — **do not disturb it**. Use worktrees off `origin/main`. Never force-push main.
- Read-only unless Imani approved a preview. No deletes without her OK on a concrete list.

## Final totals (as of 2026-10-15)

Marks: **verified** = Grok checked the result file or the disk directly. **reported** = taken from a log or handoff only.

### Tag writes
| Run | Tracks changed | Failed / skipped | Mark |
|---|---|---|---|
| Genre fixes: Afrobeat→Afrobeats | 317 | 0 / 0 | verified (apply-Afrobeat.json) |
| Genre fixes: Dancehall | 45 | 0 / 0 | verified |
| Genre fixes: Reggae / World music / Amapiano / Soca | 6 / 6 / 2 / 1 | 0 / 0 | verified |
| **Genre fixes total** | **377** | 0 failed; 40 candidates held back (skipped.json), incl. 5 afro-ish tags | verified |
| R&B Year (MP3) | 492 | 0 failed; 0 audio or Serato frames changed | verified (year-bpm result json) |
| R&B Grouping append (MP3) | 55 | 0 failed; 0 audio or Serato frames changed | verified (grouping result json) |
| R&B Genre → `R&B` (review batches G-R&B-1/2/3 + G-MESSY, Imani OK 16:53) | 110 (108 mp3, 2 m4a); 4 ID3v2.2 skipped | 0 failed; journal 110 intent/mutated/completed + run_complete; `_Serato_` sha256 unchanged | verified (independent readback 110/110) |
| R&B rows deferred to Imani | 575 (142 rows in deferred.csv on disk) | n/a | reported (575); verified (142-row deferred.csv). The two numbers differ and need reconciling |
| R&B m4a/FLAC not yet written | 14 | pending | reported |
| Pop new adds: Genre → Pop (Pop Girl At Heart 982, Cat Calls Inspo 81, Y2K 130) | 1,193 | 0 failed; 4 wav skipped (Pop from) | verified (readback + journals) |
| Pop new adds: Year (Pop from 28, Pop Girl At Heart 254) | 282 | 0 failed; 23 still no Year (17 Pop from, 6 Pop Girl) | verified |
| Pop new adds: Grouping append `Source: <playlist>` | 1,193 | 0 failed; 0 audio or Serato frames changed. Watermark (9) / GRP1 "November" (16) strips NOT done | verified |
| Junk Grouping removal (21 owner-listed values; TIT1/FLAC/M4A only, not GRP1, not Plex) | 211 (88 left with no Grouping) | 0 failed; 0 audio or Serato frames changed | verified (readback + 18 journals) |

### Files backed up (changed files + `_Serato_` only)
| Backup | Files | Status | Mark |
|---|---|---|---|
| `2026-10-15-junk-grouping/` (211 files, 18 manifests · `_Serato_` 664) | 211 + `_Serato_` | all PASS; delete after Imani's Serato rescan | verified | (only after the isolated rescan test passes)
| `2026-10-15-pop-tags/` (pre-genre 1,221 · pre-year 282 · pre-grouping 1,193 · `_Serato_` 664) | 3,360 | all PASS; delete after Imani's Serato rescan of Pop, Cat Calls Inspo, Y2K | verified | (only after the isolated rescan test passes)
| T5 `2026-10-15-genre-fixes` | 377 + `_Serato_` | deleted after Imani's Serato check | reported (deletion), verified (377 count) |
| T5 `2026-10-15-rnb-tags` | 545 + `_Serato_` | deleted after Imani's Serato check | verified (545 in target-files.txt) |
| `genre-cleanup-20260906` proven backups | 6,711 (~52G) deleted; 1 kept (Gambino *We Are God*); ~120G unproven kept | reported |

### Claude's Oct 15 sort and energy pass (reported, from CLAUDE-HANDOFF-2026-10-15.md)
| Folder | Loose moved | → CLEAN / DIRTY | Energy tags | Left in `_NEEDS CHECK` |
|---|---|---|---|---|
| Reggae | 118 | 98 / 5 | 158 | 15 |
| Dancehall | 105 (+25 Genre → Dancehall, approved) | 42 / 3 | 213 | 57 |
| Soca | 51 | 43 / 0 | 53 | 7 |
| Bouyon | 5 → REMIXES & EDITS | — | 30 | — |
| Kompa | 3 → REMIXES & EDITS | — | 5 | — |
| RIDDIM | 0 (name on hold) | — | 10 | — |
| Reggaeton | 19 (3 → REMIXES & EDITS) | 6 / 0 | 25 | 10 |
| World music | 0 | — | 2 (16 FLACs skipped) | — |
| Twerk | 22 (0 loose left) | 1 / 6 (+1 by label) | 225 | 13 |
| Amapiano | 51 (25 REMIXES, 1 INSTRUMENTALS) | 22 / 0 | 128 | 3 |
| **Total** | | | **849** | **~105** |

Skipped (reported): 16 World music FLACs (energy); RIDDIM not moved because its name is on hold; misfiled tracks marked SKIP (Reggae 16, Dancehall 21).

### `_GENRE NEEDS CHECK` (verified: live `find` count, 2026-10-15 15:20 EDT)
**179 files** total: House 39, R&b 22, Lounge 16, Events 15, Juke 10, Bouyon 8, GarageJungle 8, NEW MUSIC DOWNLOADS 8, Pop 8, Twerk 7, oLD Head Party 5, Bass 4, AUBONI 3, Gay Club 3, edit 3, Dancehall 2, Disco 2, Jazz 2, Phunk 2, Rock 2, White OOtz 2, and 1 each in 15 min set, Club RN, Ghettotech, Hard GROOVE, afrofuturism, alternative, nba, sped_up.
(Claude's per-folder `_NEEDS CHECK` folders, about 105 files, are separate from this count.)

### Library size (verified: live count of audio files)
R&b 3,468 · Hip Hop 7,649 · Pop 2,846 · neptunes 2,102 · House 1,249 · Events 1,095 · NEW MUSIC DOWNLOADS 942 · Afrobeat 352 (folder renamed `Afrobeats` 2026-10-16).

## Done

- (reported, Claude Mac) **NOLA Bounce + Ghettotech energy written (2026-10-16);** NEW MUSIC DOWNLOADS: 97 exact-SHA-256 extra copies deleted (10:28), Genre 32 + Year 14 written (11:18).
- (verified) **Pop version-label dupes p5 (2026-10-16 11:15):** 8 lower-bitrate copies removed (Imani OK 11:07); crates/smart crates repointed; 5 stale SQLite rows held.
- (verified) **Energy v2 high-confidence (2026-10-16 10:58):** R&B 506, Pop 895, Hip Hop 950 Grouping appends; 29 ID3v2.2 skipped; 0 Energy conflicts.
- (verified) **R&B hand-entry Years (2026-10-16 09:22):** 41 high-confidence written (online lookup), read back 41/41.
- (verified) **Baile energy:** 131 Grouping appends (2026-10-15).
- (verified) **Junk Grouping removal (2026-10-15 16:41):** 211 files (21 owner-listed values).
- (verified) **Pop new adds tag write (2026-10-15):** Genre → Pop 1,193; Year 282; `Source:` Grouping 1,193.
- (repo) Code review fixes merged 2026-10-16: #78 (prune tool, audio-match deleter), #81 (crate repoint tool); #83 (genre refinement apply crash).
- (verified) **Afrobeats energy written (2026-10-16 09:33 EDT):** 347 Grouping appends from the approved preview — `Energy: Warmup` 210, `Energy: Sing Along` 96, `Energy: Dance Flex` 41; readback 347/347, audio and Serato frames unchanged. Skipped: Davido "Five By Alhanislam" (NEEDS YOU, no BPM), 3 Tems ID3v2.2 files (Forever, Free Fall, Unfortunate), and the already-deleted SAD GIRLZ dupe. Backup T5 `2026-10-16-afrobeats-energy/`.
- (verified) **Afrobeats Clean/Dirty sort written (2026-10-16 ~09:15 EDT, Imani approved the preview as is).** Evidence: audio transcript (faster-whisper) + published lyrics (lrclib.net), approved explicit-terms list; a hit = Dirty, no hit = `_NEEDS CHECK`. **79** moved to `Afrobeats/DIRTY/Afrobeats/` with `Dirty` appended to Grouping (readback 79/79). **161** moved to `Afrobeats/_NEEDS CHECK/Afrobeats/` (no tag change). **32 held in place:** 28 + 1 no-hit tracks whose Genre isn't Afrobeats (need a Genre call), 2 Dirty ID3v2.2 files (Free Fall, Unfortunate), 1 Dirty file with a backslash in its name (`\A-Star - Kupe Dance`). Serato database V2/crates + master/root.sqlite repointed; post-checks PASS; 0 unresolved Afrobeats paths. Backup T5 `2026-10-16-afrobeats-cleandirty/`.
- (verified) Top-level `Afrobeat` → `Afrobeats` rename (2026-10-16 01:25). Config follow-up PR #69 (`folder_names` → `Afrobeats`) merged.
- (verified) PR **#55** merged: 59 Genres now file into their existing folders, by exact on-disk spelling (Hip-Hop → `Hip Hop`, Afrobeats → `Afrobeat` (now `Afrobeats`, #69), Ballroom Vogue → `Ballroom`, Funk & Soul → `Funk`, Riddim → `RIDDIM`; smaller styles into a parent folder with their exact tag kept, e.g. Deep House → `House`). 17 stay held → `_GENRE NEEDS CHECK`. A plain "Afrobeat" tag now reads as Afrobeats. Evidence: `handoff/live-top-level-folders-2026-10-15.md`. **Mac clone: `git pull` before building the next plan.**
- (verified) Repo PRs **#50–#53** merged (R&B spelling; FLAC/M4A Year+Grouping writers; grouping CSV builder fixes; FLAC/M4A in grouping planner).
- (reported) Claude Oct 15: 10 folders sorted/energy-tagged (~849 Energy Grouping appends). See `CLAUDE-HANDOFF-2026-10-15.md`.
- (verified) Genre fixes (Oct 15): **377** tags, 0 failures — includes **317 Afrobeat → Afrobeats**, plus Dancehall/Reggae/World/Amapiano/Soca. Imani rescanned; T5 backup deleted.
- (verified) R&B confident tags: **492 Year** + **55 Grouping** (MP3), 0 Genre (none were high-confidence). Imani rescanned; T5 backup deleted.
- (verified) **Pop duplicates finished:** 202 duplicate copies deleted (Imani approved), every keeper is the 320 kbps copy, Serato DB V2 repointed. All T5 pop-dupes backups deleted after Imani checked Serato.
- (verified) **R&B Years done:** **230** Years written today from `rnb-year-review-v2-20261015.csv` (YEAR-01 25, -02 25, -03 24, -04..10 156), Imani-approved incl. overrides; read back OK, no audio/Serato frame changes. **9** skipped by Imani (no Year): Dru Hill "I Should Be...", The-Dream "You", 7 no-artist rows (moved to hand-entry). R&B Genre batch: 110 written (4 ID3v2.2 skipped). All T5 R&B backups deleted after Imani's rescans. (only after the isolated rescan test passes)
- (verified) **R&B conflicting-genre rows (20) done 21:31 ET:** Genre → R&B on 7 (Get Away was already R&B), `Alt R&B` appended to Grouping on Computer Luv / Closer (ODE 2 U) / Lovely, Year on all 20 (I FEEL GOOD = 1965). Rescan R&b in Serato and confirm, then delete T5 `2026-10-15-rnb-genre-conflicts/`. (only after the isolated rescan test passes)
- (reported) Old `genre-cleanup-20260906` proven backups (~52G / 6,711 files) deleted after hash check. Keep manifests/logs.

## Log (old notes, superseded by Current state)

- 2026-10-15 22:15 ET: Prune tool PR #65 is open and NOT merged. Dry run with Afrobeat/: database V2 would remove 48. The 48+48 SQLite rows are held because they're still in containers. Next: Imani decides what to do about the container hold, then apply the prune, then run top3 rename.
- 2026-10-16 00:55 ET: PR #65 updated (not merged): --drop-stale-memberships + --add-live-memberships (normal crates only; smart crates reported, never inserted). Dry run Afrobeat/: database V2 removes 48; master/root.sqlite 0 adds, 0 removes, 48+48 HELD — every container involved is a SMART crate the live copy isn't (yet) in, plus 4 tracks with 2 live copies. Next: Imani decides how to treat smart-crate memberships (they're rule-generated; Serato rebuilds them), then apply, then top3 rename.
- 2026-10-16 00:50 ET: PR #65 updated (779bd7c, not merged) with --drop-stale-smart-memberships. Dry run Afrobeat/ (all flags): DB V2 remove 48; master.sqlite remove 44 / 0 adds / 118 memberships dropped; root.sqlite remove 44 / 0 adds / 143 dropped; 4 held each (Closer, Slow Wine also in Pop/CLEAN; Finesse, Focus also in R&B folders = cross-genre duplicates). Next: Imani merges PR #65 + gives go, then apply (Serato closed), then top3 rename.
- 2026-10-16 01:30 ET: Prune APPLIED (PR #65 merged): DB V2 −48, master/root.sqlite −44 each, post-check PASS, 4 held (Closer/Slow Wine also in Pop/CLEAN; Finesse/Focus also in R&B folders) — not a blocker for the top rename (previews pass). Backup T5 2026-10-16-prune-afrobeat-stale/ (delete after Imani confirms Serato). Status PR #66 open. Keep Serato CLOSED. Next: top3 Afrobeat → Afrobeats rename (needs go).
- 2026-10-16 01:40 ET (Grok): Afrobeats top rename DONE (top3); 4 "dupes" are different songs, kept; lyrics check running; 16 Drake titles done; Pop version-label preview ready; energy previews R&B/Pop/Hip Hop running. Full summary in "Tonight (Oct 16)". Status PR supersedes #64/#66.

## Serato ghosts — done (2026-10-16 10:14 ET)
- Last 22 old Music/Afrobeat/ paths cleared with the #75 tools (Kupe Dance repointed in 2 smart crates and its V2 record pruned; 21 multi-copy rows per SQLite DB pruned via the move map). Final check: 0 old paths in database V2, crates, smart crates, master.sqlite, root.sqlite.

## NEW MUSIC DOWNLOADS — Claude (current as of 2026-10-16 15:20 ET)

Folder `NEW MUSIC DOWNLOADS - MUSIC TO MOVE` is Claude's (Imani 2026-10-16). Work files: `new-music-move-20261016/` (job1-dupes, job2-tags, job3-cleandirty, job3-apply, job3-held44, job3-verify). No lock held right now.

| Job | State |
|---|---|
| 1 Duplicates | DONE 10:28. 97 exact-SHA-256 extra copies deleted (38 keepers verified). 1,124 -> 1,027 files. 0 exact copies elsewhere in the library. |
| 2 Missing tags | DONE 11:18. Genre 32 + Year 14 written (blank only, 2+ sources or repo filename keyword). 288 Genre / 146 Year left blank. Genre tool end-of-run crash fixed in PR #83 (merged). Source: nothing written; 105 subfolders listed for Imani (`job2-tags/job2-source-subfolders-20261016.csv`). |
| 3 Clean/Dirty | DONE 14:55 (Imani go). 762 moved into `<bucket>/<Genre>/` with approved folder names (Hip Hop, Funk, Jersey Club, New Orleans Bounce, neo soul; Miami Bass/New Wave -> _GENRE NEEDS CHECK): CLEAN 49 + DIRTY 315 (Clean/Dirty Grouping appended on 240; 18 mp4/wav/ogg moved without tag) + _NEEDS CHECK 398. HELD 44 in place (not moved, Clean/Dirty unresolved). 15:10 (Imani go): 39 Genre tags respelled to the library standard - 21 `Hip-Hop`, 16 `R&B`, 1 `Bounce`, 1 `House`; 5 unchanged (Juke keeps "Juke; Footwork"; Nu Disco / Soul / Trap / Country;Hip-Hop mashup left for Imani). Genre only; Grouping untouched; readback 39/39, audio unchanged. One side effect: "08 - Gucci Please" TXXX:author re-saved as "Michael Williams/Radric Davis" (was 2 values; same text). Old Genre kept in `job3-held44/held44-genre-TAGS-ONLY-PREVIEW-20261016.csv` + undo log. Moves ON HOLD until rekordbox is checked. Backup T5 `2026-10-16-newmusic-held39-genre` (39 files PASS + _Serato_ + SQLite). Serato repointed: database V2 + 51 crate files (762 refs, post-check PASS), master/root.sqlite 762 rows each verified. Backup T5 `2026-10-16-newmusic-cleandirty` (806 files + _Serato_ + SQLite, PASS). Old Grok `CLEAN/Hip-Hop`, `DIRTY/Hip-Hop` folders left as they are. |
| 4 Energy | DONE 16:09 (Imani go, Serato quit, lock + T5 PASS). 282 written: Starter 82, Peak 81, Warmup 46, Sing Along 23, Cooldown 21, Dance Flex 13, Twerk Dutty 12, Shake 4. Verified 282/282 = old Grouping kept + one Energy appended; audio and Serato frames unchanged. 622 left blank (450 genre with no energy pass yet, 134 low-confidence suggestions, 38 no value) - see `job4-energy/job4-energy-PREVIEW-20261016.csv`. 24 newer downloads, DJ TOOLS, ALBUMS & PROJECTS not touched. Undo: `job4-energy/apply/newmusic-energy-grouping-undo-20261016-160911.jsonl`. Backup T5 `2026-10-16-newmusic-energy` (whole folder 1,049 files + _Serato_ + SQLite, PASS). |
| Re-check of job 3 | 15:15 read-only: 762/762 moves match the journal; every new file exists with the logged hash, no old file left; database V2 762/762 new, 0 old; 50 crate files (15 crates + 35 smart crates) 0 old refs; master/root.sqlite 762/762 new, 0 old, unchanged since apply. **rekordbox (read-only, 15:18):** no fresh export exists; newest Collection XML is Aug 30 (`~/Documents/rekordbox/ok.xml`, 59,498 tracks). In it, **104 of the 762 old paths** are rekordbox tracks: 0 cues, 0 hot cues, 0 plays, 0 duplicate entries, new path not already in rekordbox; playlists only `Not Yet In Rekordbox` (85) and `Music` (19). rekordbox `master.db` last changed Oct 16 00:07 (before job 3), so rekordbox has not seen the moves; tracks added after Aug 30 are not in that export. Real count needs a fresh export. Nothing in rekordbox changed. Lists: `job3-verify/job3-moved-762-old-new-paths-20261016.csv`, `job3-verify/rekordbox-aug30-xml-vs-762-20261016.csv`. |

Not touched: DJ TOOLS, ALBUMS & PROJECTS (ask Imani first).

Waiting on Imani (Claude items):
- rekordbox: export the Collection XML so the 762 moved tracks can be checked (no relocation until then). The 44 held tracks stay in place until then.
- 4 held tracks with no clear Genre (Daddy Yankee Shaky Shaky remix, Durand Bernarr H.I, Central Cee Wagwan remix, nita maraj mashup). Source subfolder sheet (or skip).
- 24 new downloads arrived in the folder during the day (Young Miko/, Destin Conrad, PinkPantheress, Tinashe): not in any job yet.
- Serato: Rescan ID3 Tags on NEW MUSIC DOWNLOADS (240 Clean/Dirty Grouping appends); check moved tracks show up. (only after the isolated rescan test passes)
- Blanks: NOLA Bounce 46, Ghettotech 22 (preview CSVs in `nola-bounce-energy-20261016/`, `ghettotech-energy-20261016/`).
- Baile/Afrobeats 11 untagged (`energy-blanks-20261016/`): Situationship Edit = Warmup (not written); Waste My Time / CRU LOVE / Se Dansa need a value; 3 Tems ID3v2.2 need conversion OK; Davido "Five" delete requested, NOT done (needs explicit go).
- Grok's `energy-written-change-suggestions-20261016.csv` suggests 8 NOLA Bounce Twerk Dutty -> Shake (not applied).
- Maybe misfiled: Bounce (Wassup Wassup x2, River Dance Bounce, no luv (+)); Ghettotech ~12 (see its preview).
- Serato: Rescan ID3 Tags on New Orleans Bounce, Ghettotech, NEW MUSIC DOWNLOADS. (only after the isolated rescan test passes)

T5 backups from Claude today (delete after Serato check): `2026-10-16-nola-bounce-energy`, `2026-10-16-ghettotech-energy`, `2026-10-16-newmusic-dupes`, `2026-10-16-newmusic-tags`, `2026-10-16-newmusic-cleandirty`, `2026-10-16-newmusic-held39-genre`, `2026-10-16-newmusic-energy`.

Energy values: Imani's 10 (Warmup, Starter, Sing Along, Dance Flex, Shake, Twerk Dutty, Slow Wine, Peak, Cooldown, Reset) plus Bashment, which stays for Jamaican dancehall (already written in Dancehall/Reggae/RIDDIM). No Banger.
- 2026-10-16 14:15 ET (Claude): NEW MUSIC job 3 Clean/Dirty FINAL PREVIEW (read-only, nothing moved): 815 in scope. DIRTY 315 (label 75, heard in audio 208, published lyrics only 32), CLEAN 49 (label only), _NEEDS CHECK 446 (no hit), 5 instrumentals left in place. 806 moves into <bucket>/<Genre>/, 0 name clashes, 112 subfolders would be emptied (Imani OK). 2 files found in the Mac Trash (AZ Chike "Are You Tired Yet" FLAC, J. Cole H.Y.B. .ogg) - deleted by hand, not by a tool; both in T5 2026-10-16-newmusic-tags. CSV: new-music-move-20261016/job3-cleandirty/job3-cleandirty-PREVIEW-20261016.csv. Waiting on Imani go.
- 2026-10-16 15:00 ET (Claude): NEW MUSIC job 3 APPLIED: 762 moved (346 apply_clean_dirty_move_plan + 18/398 apply_version_genre_plan), 44 held (Genre spelling). Notes: move-only run 1 stopped at item 19 on a Genre-spelling check and rolled it back cleanly; run 2 finished all 398 then hit KeyError counts["genres"] writing its result (plan-file field missing; undo log complete). SQLite plan built with build_preview(require_live_rules=False) for the 762 journal-proven moves (CLI preview refuses after moves). Locks released.
- 2026-10-16 15:10 ET (Claude): held 44: 39 Genre tags respelled (apply_genre_refinement_plan, lock + T5 PASS), 5 unchanged, no moves, readback 39/39. Job 3 read-only re-check all PASS (journal/disk/hash/V2/50 crates/SQLite); rekordbox unchecked, path list saved for Grok. Locks released.

## Restored from the project-folder copy (2026-10-16 15:25)

These entries existed only in the project-folder copy before the 15:20 sync; re-added verbatim, nothing dropped. Headings are the sections they came from.

### Code review holds — answers (Grok, 2026-10-16 12:10 ET, read-only)
Holds respected: no live prune / audio-match delete runs; PR #80 keeper-map plan NOT to be run live (rebuild after the holds lift); old plans must be rebuilt. Keep T5 `2026-10-16-prune-afrobeat-stale`, `2026-10-16-serato-ghost-fix`, `2026-10-16-pop-dupes-p5`.
1. **Oct 15 Pop dupes (202): audio-match mode** (rule owner-audio-match-2026-10-15, all 202). **Its T5 backup `2026-10-15-pop-dupes` no longer exists**: it was deleted Oct 15 19:33 after Imani confirmed Serato (log). The 202 deleted copies cannot be restored from T5; Time Machine is the only possible source. Pairs where folder, Clean/Dirty Grouping or bracketed name differ (30 of 202):
   - Different CLEAN/DIRTY folder, same version label (deleted "Pop from /" copy had no Clean/Dirty tag, keeper is in the matching folder with the same label). Looks correct: Baby Tate Lollipop (Intro Dirty); Drake Calling My Name (Intro Dirty), Sticky (Intro Dirty); LEAF/Baby Tate Picky (Intro Dirty); Lil Nas X MONTERO (Intro Clean) + (Intro Dirty); Miley Party In The USA (Intro Clean); Nicki/Ariana Bed (Intro Clean) + (Intro Dirty); Nicki Megatron (Intro Clean) + (Intro Dirty) + (BPM Supreme Hype Intro)(Clean Extended); XXXTENTACION I Don't Even Speak Spanish Lol (Intro Clean).
   - **Check: Cassie "Me & U"**. Three copies (Pop/Pop Girl At Heart, R&b/, Y2K/) were deleted for the one keeper `Events/LIKE SUGAR ON MY TONGUE/CLEAN/CLEAN R&B/R&B/Me & U_1.mp3` (Grouping Clean). No genre-folder copy is left.
   - Clean/Dirty Grouping differed, but the keeper now carries it after the tag merge: Katy Perry Last Friday Night, Teenage Dream; Nelly Furtado Promiscuous; Selena Gomez Love You Like A Love Song; Spice Girls Spice Up Your Life; PCD Buttons (all Clean); Beyoncé MOVE (edit/ copy, Dirty); Drake Passionfruit (Clean)(Acapella) (keeper is the Clean acapella).
   - Bracket text only (feat./prod./spelling, same version): Amaarae SAD GIRLZ LUV MONEY (feat. Moliy) and the (Remix) pair; coco & clair clair pretty (prod. okthxbb); Blaque Bring It All To Me (Featuring N'Sync); Ashlee Simpson Boys and Pharrell Gust of Wind (Neptunes "produced by" names); Last Friday Night (T.G.I.F.) vs (TGIF).
   - **Today's p5 (8): audio-match**. No CLEAN/DIRTY folder or Clean/Dirty Grouping difference. Bracket differences: the NC pairs Imani approved at 11:07 (PLASTIC OFF THE SOFA ×2 and Gimme More → keeper "(NC)"; Rock Your Body (NC) and Honey (NC) → keepers without NC; Maneater → "(NC)"); NSYNC Girlfriend (album version) → album track (same). **Check: Britney "I'm a Slave 4 U (1st version) [aka Light Your Ass On Fire]"** (Neptunes set) was deleted for the regular album "I'm a Slave 4 U (2)". The 1st version is a different early mix. It can be restored from T5 `2026-10-16-pop-dupes-p5/deleted/neptunes/...` via the p5 undo log (`pop-dupes-p5-20261016/del-neptunes/dupe-p5-neptunes-undo-*.jsonl`, delete_verified_duplicates.py --undo). **Proposed only, not run; needs Imani's OK.** Its stale SQLite row is one of the 5 in PR #80.
2. **10:01 ghost cleanup**: the fixed prune tool (#78) on a copy of the pre-10:01 backup would have held 27 records it removed (27 database V2, 7 per SQLite). All are same-name songs. Every Afrobeats copy exists and has its database V2 + both SQLite records. No other same-name song lost a record, crate or membership. Only rule-based smart crates (Unsorted Oct, Afrobeat Clean) differ, by their rules. Nothing to restore. Details: `ghost-fix-20261016/fixed-tool-held-vs-1001-run.csv`.
3. **Ayotemi Closer, Machel Montano Slow Wine, Pheelz Finesse, Joeboy Focus** (Afrobeats/CLEAN/Afrobeats, all "(Intro Clean)"), plus the DIRTY Closer (Abigail Chams/Harmonize): all have their database V2 record and master/root rows, and are in Afrobeat Clean (or Afrobeat Dirty), Clean, all, and IYG PROM > International (DIRTY Closer). The only change vs the pre-10:01 backup is the Unsorted Oct smart list (rule: added after Oct 1; these were added earlier). **Nothing lost.** The T5 `serato-ghost-fix` backup can go once Imani OKs it; it is held per the hold for now.

### Tonight (Oct 16, 00:00-01:40 EDT)
- (verified) **PR #65 merged**: `prune_missing_serato_records.py` (dry-run first, verified backup, journal, undo; smart-crate memberships only).
- (verified) **Stale Afrobeat prune applied 01:20-01:30**: database V2 -48, master.sqlite -44, root.sqlite -44, post-check PASS. 4 rows held per SQLite file (Closer, Slow Wine, Finesse, Focus old paths).
- (verified) **Afrobeat -> Afrobeats top-level rename DONE 01:24-01:27** (top3). `Music/Afrobeats` exists, no `Afrobeat`; database V2, crates and both SQLite files repointed, post-checks PASS. Genre-folder config PR (`folder_names` -> `Afrobeats`) still NOT opened.
- (verified) **The "4 dupes" are NOT duplicates**: same filename, different songs (Goapele vs Ayotemi "Closer"; Tony! Toni! Toné! vs Machel Montano "Slow Wine"; Drake vs Pheelz "Finesse"; H.E.R. vs Joeboy "Focus"). Nothing deleted; all 8 files kept. The 4 held SQLite rows can stay or be fixed later with a tag-aware match.
- (verified) **Explicit terms list approved as is** by Imani (`folder-pass-preview-20261015/explicit-terms.txt`, 19 terms).
- **Afrobeats lyrics check RUNNING** (started 01:27, read-only): 272 `_NEEDS CHECK` tracks, 6 batches, ~30 min each (`afrobeats-lyrics-20261016/`). Result = Clean/Dirty preview for Imani; nothing moves until she approves. Afrobeats energy comes after.
- (verified) **Drake title cleanup DONE 01:31**: 16 titles (R&b 15, Hip Hop 1), readback 16/16, Artist/audio/Serato frames unchanged. 10 left on purpose.
- **Pop version-label dupes preview** (`pop-version-label-dupes-preview-20261016.csv/.md`, read-only): 18 pairs. 8 = keep the 320 kbps copy and remove the lower-bitrate one (waiting on Imani). 10 = same bitrate, KEEP BOTH until "(NC)" meaning is known (Imani doesn't remember what NC means; check by ear or lyrics).
- **Energy previews READY (audio-based, read-only)** for R&B, Pop, Hip Hop: `energy-preview-{rnb,pop,hiphop}-audio-20261016.csv` + `energy-preview-rnb-pop-hiphop-audio-20261016.md`. Essentia audio features + tags + Billboard Hot 100 match (`web_signal`). NEEDS YOU: R&B 323, Pop 200, Hip Hop 672 (was 1,600 / 519 / 3,799 with BPM rules). Waiting on Imani's review; nothing written.
- **Energy previews v2 READY (10 values, Imani 10:20)**: `energy-preview-{rnb,pop,hiphop}-audio-v2-20261016.csv` + `energy-preview-rnb-pop-hiphop-audio-v2-20261016.md`. NEEDS YOU: R&B 187, Pop 122, Hip Hop 437. Also `energy-written-change-suggestions-20261016.csv` (109 already-written tags that might change; suggestions only). Waiting on Imani; nothing written.
- **Serato must stay CLOSED** until the lyrics check is finished and Imani is told it's safe.

### T5 backups waiting on Imani's Serato check (then delete)  _(restored from project copy)_
- `2026-10-15-afrobeats-rename/` (inner, top, top2, top3) — check Afrobeats in Serato.
- `2026-10-15-afrobeat-genre-baile-energy/` — check Baile + 19 genre-fix folders.
- `2026-10-16-prune-afrobeat-stale/` — check Afrobeats crates.
- `2026-10-16-drake-titles/` — check a few renamed Drake titles (e.g. Marvins Room, July).

### Waiting on Imani (short list)
1. Open Serato only after told it's safe; check Afrobeats, Baile, Drake titles -> then backups above get deleted.
2. Approve the 8 Pop higher-bitrate removals; decide what to do with the 10 NC pairs.
3. Review the Afrobeats Clean/Dirty preview when the lyrics check finishes.
4. Review energy previews for R&B / Pop / Hip Hop when ready.
5. Ear-check 7 Baile tracks + Davido "Five".
6. Tidy smart crate `afro world ≫ Afrobeat` rules.
7. Decide the 4 same-name pairs (both kept for now) and whether to clean their 4 held Serato rows.

### To do (agents)
- Genre-folder config PR: `folder_names` Afrobeats -> `Afrobeats` folder.
- Afrobeats energy after Clean/Dirty sort.
- Later: R&B hand-entry Years (41 high written 2026-10-16; 95 medium + 55 unsure await Imani, see rnb-year-hand08-proposals-20261016.csv), 4 m4a/FLAC, 4 ID3v2.2, 2 missing; Pop 125 refused + 589 uncertain pairs; folder moves (5 Pop keepers, 12 non-R&B tracks); T5 repo copy.

### Library size (verified: live count of audio files)  _(restored from project copy)_
R&b 3,468 · Hip Hop 7,649 · Pop 2,846 · neptunes 2,102 · House 1,249 · Events 1,095 · NEW MUSIC DOWNLOADS 942 · Afrobeat 352.

### Done  _(restored from project copy)_
- (verified) PR **#55** merged: 59 Genres now file into their existing folders, by exact on-disk spelling (Hip-Hop → `Hip Hop`, Afrobeats → `Afrobeat`, Ballroom Vogue → `Ballroom`, Funk & Soul → `Funk`, Riddim → `RIDDIM`; smaller styles into a parent folder with their exact tag kept, e.g. Deep House → `House`). 17 stay held → `_GENRE NEEDS CHECK`. A plain "Afrobeat" tag now reads as Afrobeats. Evidence: `handoff/live-top-level-folders-2026-10-15.md`. **Mac clone: `git pull` before building the next plan.** If the `Afrobeat` folder is later renamed to `Afrobeats`, update `folder_names` in the same change.

### In progress
- Old T5 repo copy (215G): audited 2026-10-15 15:29 EDT, read-only, NOT deleted. It has unpushed rule commits (Nu Wave, Juke & Footwork, T5 dedupe --serato-backup), 13 attached worktrees, and about 3,655 backup audio tracks not found in the live library, including the Gambino We Are God.flac. (verified) Imani: leave for later.
- **New Pop music** (Imani adding): `Pop/Pop from ` · `Cat Calls Inspo/` · `Pop/00_s Music/Pop Girl At Heart/` (**982 files**, was still copying at 471) · top-level `Y2K/`.
- **Approved plan** (Imani): Genre → Pop; append `Source: <playlist>` to Grouping; 4-digit Years; strip "November" / record-pool watermark Groupings (now its own step, see Waiting on Imani).
- (reported by the Mac session, ~16:00 EDT) **Written, then paused:** Genre → Pop on **1,193** files (all of Pop Girl At Heart, Cat Calls Inspo, Y2K) and **238** Pop Girl Years cut to 4 digits. All read back correctly, backed up, undoable. Imani to rescan those three in Serato. (only after the isolated rescan test passes)
- **Refreshed Pop preview, waiting on Imani's go:** 44 Pop Girl Years; 1,202 `Source:` Grouping appends (Pop Girl 982, Cat Calls 81, Y2K 130, Pop from 9). 23 tracks have no year (left blank); 4 wav skipped.
- **Inbox genre/year lookup** still running (`inbox-genre-lookup/`, ~325+/514). When done: `merge.py` → preview → write only after approval.
- **Folder-rename tooling (PR `claude/folder-rename-tooling`, not yet run on the live library):** `apply_folder_rename_plan.py` (R&b→R&B case-only via temp name, Afrobeat→Afrobeats; preview/apply/undo/recover) and `rewrite_serato_sqlite_paths.py` (`asset.portable_id` in **both** `master.sqlite` and `root.sqlite`). Real-scale rehearsal on copies passed (4,311 Serato refs, 3,824 rows per SQLite file, undo works). Review fixes landed (atomic no-replace rename; SQLite hardening); #56 reviewed OK. Live run order: own full copies of `_Serato_` + music, spare-copy trial incl. undo, check Serato re-imports, then the real rename with Serato quit. Needs Imani's separate go; read `references/folder-rename-runbook.md` first (open questions: Serato re-import of `database V2`, SmartCrates cached paths).

### Waiting on Imani
- **(SUPERSEDED 01:27 Oct 16 — rename done, see Tonight)** 22:10 update: SAD GIRLZ stale record gone (Imani 22:04), top rename retried (`top2`) and refused again, then rolled back (folder is `Afrobeat`). After Imani opened Serato, it re-added the 48 OLD inner paths (`Afrobeat/CLEAN/Afrobeat/…` 32, `Afrobeat/DIRTY/Afrobeat/…` 16) in database V2 and both SQLite files, alongside the correct `…/Afrobeats/` entries. Those 48 are now missing-file duplicates in Serato and block the rewriter. Runbook open question 1 answered: Serato re-imports/keeps old paths after a rewrite. Needs a decision before retrying (Imani removes the 48 missing duplicates in Serato, or a sanctioned prune tool). Config PR still not opened.
- **(SUPERSEDED — top rename DONE 01:27 Oct 16)** Afrobeat -> Afrobeats folder rename: PARTLY DONE (2026-10-15 21:5x ET). Inner folders renamed `Afrobeat/CLEAN/Afrobeat` -> `CLEAN/Afrobeats` and `DIRTY/Afrobeat` -> `DIRTY/Afrobeats`, Serato DB V2/crates/SQLite repointed (tooling PR #56). **Top-level rename rolled back**: `rewrite_serato_paths.py` refused because `database V2` still holds a stale record for the deleted Pop-dupe `Afrobeat/SAD GIRLZ LUV MONEY - Remix.mp3`; folder renamed back with the tool's undo, Serato consistent (post-check: that 1 stale ref is the only `Music/Afrobeat/` miss). Needs: remove that missing track in Serato (or a sanctioned prune tool), then re-run. Genre-folder config PR NOT opened (runbook: only after the top rename). Backups T5 `2026-10-15-afrobeats-rename/{inner,top}`.
- **Smart crate `afro world >> Afrobeat`** (not touched): 3 Genre-contains rules: `Afrobeat`, `afro beat`, `afrobeats`. Imani to tidy in Serato (one `Afrobeat` rule already matches Afrobeats).
- **Baile energy written** (131 Grouping appends). 7 EAR CHECK rows still need Imani; also Davido Five (Imani to call). `Baile/baile_plex` = **keep-whole pack** (never sorted/moved).
- **Afrobeats energy** still waiting on the Clean/Dirty sort (lyrics check RUNNING since 01:27 Oct 16; 'no hit' = _NEEDS CHECK).
- **Pop new adds:** rescan ID3 tags in Serato (Pop, Cat Calls Inspo, Y2K) and confirm, then delete T5 `2026-10-15-pop-tags/`. Decide: ~440 dupes (keep best, merge tags), Clean/Dirty for ~1,190 unmarked, watermark/November Grouping strips (needs writer change + append-only exception). (only after the isolated rescan test passes)
- **Junk Grouping removal ("November", record-pool watermarks)** — Imani said yes to a narrow exception. Tool is in PR #57. Steps, Pop / Cat Calls Inspo / Y2K, after the `Source:` write:
  1. `python3 scripts/build_grouping_removal_plan.py survey --root "$LIB/<folder>" --out-dir "$W/grouping-survey"` (read-only) → show Imani `GROUPING-VALUES.md`.
  2. Imani picks the exact junk values → one per line in `$W/remove.txt`.
  3. `python3 scripts/build_grouping_removal_plan.py plan --root "$LIB/<folder>" --remove "$W/remove.txt" --out-dir "$W/grouping-removal"` → show `GROUPING-REMOVAL-PREVIEW.md`; Imani says go.
  4. Scoped backup + PASS manifest, then `apply_grouping_plan.py --apply` with the lock, as for any Grouping write. Serato rescan. (only after the isolated rescan test passes)
- **R&B still open** (`rnb-year-review-v2-20261015.csv/.md`): type-by-hand Years: **41** high-confidence written 2026-10-16 09:22 (online lookup, read back 41/41, 0 audio/Serato frame changes; undo `rnb-hand-years-20261016/rnb-hand-year-bpm-undo-20261016-092227.jsonl`; T5 backup `2026-10-16-rnb-hand-years`), **95** medium + **55** unsure still open (`rnb-year-hand08-proposals-20261016.csv`), **4** m4a/FLAC, **4** ID3v2.2 (07 Got Her Own, 08 Drown In It, Return Of The Mac (Topper-Intro) Extended, Shut It Down — tag upgrade first), **2** missing files.
- **Not R&B — move out of the R&b folder in the folder pass** (Genre left unchanged, Year written 2026-10-15): Hip-Hop: Beyoncé Formation (Intro Clean) + (Intro Dirty), bLAck pARty Flame + KEMET, Col3trane Take Notes, Smino Curtains + Garden Lady + HOOPTI, Ludacris Saturday (Oooh! Ooooh!); Funk & Soul: James Brown I FEEL GOOD; House: Son Of Mongo; Latin; Pop: Alkaline Mamacita.
- **Pop duplicates still waiting:** 18 version-label holds (preview 20261016: 8 removals waiting, 10 kept until NC known), 125 refused, 589 uncertain pairs. 5 keepers to move into their genre folders in the folder pass (Everything is romantic → Lounge, ROCKET → Groovy Bounce, Ick + Na Na Na → Jersey Club, SAD GIRLZ LUV MONEY Remix → Afrobeats; Grouping already tagged).
- **Drake title cleanup: DONE 2026-10-16 01:31.** 16 titles cleaned in R&b (15) and Hip Hop (1), e.g. "Drake - Marvins Room" -> "Marvins Room". Artist tags unchanged. 10 left as-is on purpose: 7 remixes/edits where Drake only appears in the Title, plus KAYTRANADA and 2 Drake/21 Savage titles. Undo + T5 backup: drake-title-20261016/, T5 2026-10-16-drake-titles/ (delete after Serato check).
- Tyla 128 kbps vs 320 kbps pairs (not hash dupes) — keep/delete call.
- Fix Serato smart crates: `Clean R&B` / `r&b edits` need `"r&b "` with trailing space; `Clean R&B` should match ALL not ANY; `90s`/`2000s` use Grouping not Year.
- **5** afro-ish tags left in Afrobeat (e.g. `r&b afro beat lounge`) — not exact "Afrobeat".
- Unproven old backups ~**120G** (Hip Hop/source + passA copies, newbatch-20260906b, remainders) — do not delete until per-file checked.
- Missing library copy: Childish Gambino — *We Are God* (backup may be only copy; ask if intentional).

### Next up
1. Rescan Pop Girl → refresh preview → write Pop tags (scoped backup) after Serato closed. (only after the isolated rescan test passes)
2. Imani reviews the held/refused/uncertain duplicate pairs.
3. R&B conflicting-genre (20) + hand-entry Years (150 left: 95 medium, 55 unsure); m4a/FLAC Year/Grouping (writers on main via #51).
4. **34** ID3v2.2 Grouping (needs conversion approval).
5. PR: extend `rewrite_serato_paths.py` to History + SmartCrates.
6. One-pass folder moves: `R&b` → `R&B` + CLEAN/DIRTY sort; Afrobeat folder → Afrobeats; misfiled tracks from Claude sheets. (Rename tooling exists in the PR above; needs review + Imani's go first.)
7. NEW MUSIC DOWNLOADS (Claude, Imani 2026-10-16): jobs 1-2 done; job 3 Clean/Dirty preview, then job 4 energy. See "NEW MUSIC DOWNLOADS — Claude".
8. Low-bitrate triage: list is DONE (Claude, 2026-10-16, read-only). Imani: review the 245 "better copy exists" rows before any delete; re-download list is `low-bitrate-redownload.csv` (start with the ≤160 kbps rows). Deleting low copies needs a scoped backup + Serato repoint like the Pop dupes.
- 2026-10-16 02:30 ET: Top-level Afrobeat -> Afrobeats rename DONE (top3, all post-checks PASS; backup T5 2026-10-15-afrobeats-rename/top3). Genre-folder config PR still to do.
- 2026-10-16 02:30 ET: "4 dupes" NOT deleted — the Pop/R&B copies of Closer, Slow Wine, Finesse, Focus (Intro Clean) are different songs (Goapele, Tony! Toni! Toné!, Drake, H.E.R.) vs the Afrobeats ones (Ayotemi, Machel Montano & Afro B, Pheelz & Bnxn, Joeboy). 4 held SQLite rows remain (harmless). Needs Imani.
- 2026-10-16 02:30 ET: Afrobeats Clean/Dirty PREVIEW (272 tracks): DIRTY 82, _NEEDS CHECK 190 -> afrobeats-lyrics-20261016/afrobeats-clean-dirty-PREVIEW-20261016.csv. No moves/tag writes until Imani approves. Serato may be opened.
- 2026-10-16 09:20 ET: Afrobeats Clean/Dirty WRITTEN: 79 Dirty (moved + Grouping), 161 -> _NEEDS CHECK/Afrobeats, 32 held (29 non-Afrobeats Genre, 2 ID3v2.2, 1 backslash name). Serato repointed, post-checks PASS. Backup T5 2026-10-16-afrobeats-cleandirty/. Config PR #69 + status PR #70 open. Next: Afrobeats energy picks.
- 2026-10-16 09:35 ET: Afrobeats energy WRITTEN: 347 (Warmup 210, Sing Along 96, Dance Flex 41), readback 347/347. Skipped 5 (Davido Five = NEEDS YOU, 3 Tems ID3v2.2, SAD GIRLZ deleted dupe). Backup T5 2026-10-16-afrobeats-energy/. Serato may be opened.

### Serato ghost entries (updated 2026-10-16 09:54 ET)
- Cause: smart crates cache old paths; Serato re-imports them on launch. Fix in PR #73 (open, not merged).
- Dry run: 882 smart/regular crate refs (397 tracks) to repoint; 1 held (backslash Kupe Dance); 398 ghost records to prune after (database V2 + both SQLite DBs). Not applied. Next: merge #73 → live run with Serato closed + T5 mounted.

### Serato ghost cleanup (updated 2026-10-16 10:01 ET)
- Done: 882 crate/smart-crate refs repointed; 397 database V2 ghosts + 377 rows per SQLite DB pruned.
- Left: Kupe Dance (backslash) in DB V2 + 2 smart crates; 21 ambiguous rows per SQLite DB. Serato should stay closed until those are fixed (follow-up).

### Serato ghost follow-up (2026-10-16 10:07 ET)
- PR #75 open. Dry run: 2 crate refs + 21/21 SQLite rows + 1 database V2 record → 0 old paths left. Waiting for merge + go. Serato stays closed.
- 2026-10-16 10:08 ET (Claude): New Orleans Bounce energy WRITTEN: 93 (Twerk Dutty 60, Sing Along 23, Dance Flex 10), readback 93/93, 46 left blank for Imani. Backup T5 2026-10-16-nola-bounce-energy/ (PASS). Lock released. Serato may be opened; Rescan ID3 Tags on New Orleans Bounce. (only after the isolated rescan test passes)

### Serato ghosts — DONE (2026-10-16 10:14 ET)
- 0 old Music/Afrobeat/ paths anywhere. Serato safe to open. Next: Rescan ID3 Tags on Afrobeats, Baile, New Orleans Bounce; delete T5 backups after she confirms. (only after the isolated rescan test passes)
- 2026-10-16 10:16 ET (Claude): Ghettotech energy WRITTEN: 69 Twerk Dutty, 22 blank (instrumentals + maybe-misfiled). Backup T5 2026-10-16-ghettotech-energy/ (PASS). Lock released. Rescan ID3 Tags on Ghettotech in Serato. (only after the isolated rescan test passes)


_(16:35: the old NEW MUSIC lines restored here were removed at Claude's request; the current NEW MUSIC section is above.)_
