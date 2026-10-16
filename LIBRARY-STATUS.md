# DJ Library — live status (any AI: read this first)

Short handoff. Details live in `GROK-DJ-LIBRARY-LOG.md`, layout rules in `LIBRARY-LAYOUT.md`, Claude's Oct 15 wrap-up in `CLAUDE-HANDOFF-2026-10-15.md`.

## Last updated

2026-10-16 ~12:45 EDT — Claude (cloud, repo only): status file cleaned up: one "Current state" section; stale "waiting/running" items that later entries show as done were removed; facts from Grok's open status PRs #74 and #79 folded in (marked). No library files touched.
2026-10-16 12:35 EDT — Claude (Mac): NOLA Bounce + Ghettotech energy written; NEW MUSIC DOWNLOADS jobs 1-2 written (97 exact dupes deleted, 32 Genre + 14 Year), job 3 Clean/Dirty preview running (audio 5/12 batches), job 4 not started. See "NEW MUSIC DOWNLOADS — Claude" below.
2026-10-16 12:04 EDT — #83 merged: genre refinement apply no longer crashes at the end (missing result JSON / run_complete).
2026-10-16 ~12:00 EDT — Claude (cloud, repo only): code review of #63/#65/#73/#75; fixes merged as #78 and #81 (see "Checks before more cleanup" below). No library files touched.
2026-10-16 11:40 EDT — Grok (reported in open PR #79, not on main before this): Pop version-label dupes p5: 8 lower-bitrate copies removed (Imani OK 11:07); crates/smart crates repointed to keepers; database V2 clean; 5 stale SQLite rows held. Backup T5 `2026-10-16-pop-dupes-p5`. Metadata cleanup v2 preview ready.
2026-10-16 10:58 EDT — Grok (DJ B.A.E. Library): high-confidence Energy v2 (10-value scale) written: R&B 506, Pop 895, Hip Hop 950 Grouping appends (29 ID3v2.2 skipped, 0 Energy conflicts). Med/low/NEEDS YOU/Reset not written. Backup T5 2026-10-16-energy-v2-high; Imani to rescan ID3 tags in Serato. See GROK-DJ-LIBRARY-LOG.md.
2026-10-16 10:01 EDT — Grok (reported in open PR #74): Serato ghost cleanup: 882 crate/smart-crate refs repointed (crates-only); 397 database V2 ghosts + 377 rows per SQLite file pruned (with the prune tool as it was BEFORE #78).
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

## Current state (2026-10-16 ~12:45 EDT)

This section is the single list of what is open. Older sections below are history.

### Working now
- **Grok (Mac):** R&B / Pop / Hip Hop energy — high-confidence written 10:58; medium / low / NEEDS YOU not written (preview for Imani). Metadata cleanup v2 preview ready (reported in PR #79).
- **Claude (Mac), folder `NEW MUSIC DOWNLOADS - MUSIC TO MOVE`** (Imani 2026-10-16; Grok stays out): job 1 duplicates DONE (97 exact copies deleted), job 2 missing tags DONE (Genre 32, Year 14), job 3 Clean/Dirty PREVIEW running (nothing moved), job 4 energy not started. Also wrote NOLA Bounce + Ghettotech energy. Details and its open items: "NEW MUSIC DOWNLOADS — Claude" at the end of this file.
- **Serato:** stays CLOSED while anyone writes. Ask Grok before opening it.

### Checks before more cleanup (code review, Claude 2026-10-16)
The review fixes are merged (#78 prune tool + audio-match deleter; #81 crate repoint tool). Plans built before them are refused; rebuild them. Audio-match pairs files now need `keep_sha256`. Three things the OLD tools may have done wrong still need a read-only check by Grok:
1. **Pop duplicates (202 deleted 2026-10-15):** which mode, exact or `--mode audio-match`? If audio-match, list any pair whose deleted and kept files were in different CLEAN/DIRTY folders, had different Clean/Dirty Grouping, or different bracketed edit names. **Note: the T5 pop-dupes backups were already deleted** after Imani's Serato check (see Done), so a wrongly deleted Clean copy can only come back from its original source, not T5. The deletion undo logs/journals still list every deleted path and hash.
2. **Serato ghost cleanup (10:01, 397 database V2 / 377 per SQLite):** ran with the old prune tool. Dry-run the fixed tool against that run's backup copy (never the live files) and list records it would now HOLD. Keep that backup.
3. **Afrobeat prune (01:20, database V2 −48 vs SQLite −44):** do the Afrobeats copies of Ayotemi "Closer", Machel Montano "Slow Wine", Pheelz "Finesse", Joeboy "Focus" still have their database V2 record and crates? Keep T5 `2026-10-16-prune-afrobeat-stale/`.

### T5 backups waiting on Imani's Serato check (then delete)
- `2026-10-15-pop-tags/` and `2026-10-15-junk-grouping/` — rescan Pop, Cat Calls Inspo, Y2K.
- `2026-10-15-rnb-genre-conflicts/`, `2026-10-16-rnb-hand-years/` — rescan R&b.
- `2026-10-15-afrobeats-rename/` (inner, top, top2, top3), `2026-10-16-afrobeats-cleandirty/`, `2026-10-16-afrobeats-energy/` — check Afrobeats.
- `2026-10-15-afrobeat-genre-baile-energy/` — check Baile + the 19 genre-fix folders.
- `2026-10-16-drake-titles/` — check a few Drake titles (Marvins Room, July).
- `2026-10-16-energy-v2-high/` — rescan R&B, Pop, Hip Hop.
- `2026-10-16-pop-dupes-p5/` (reported in PR #79).
- `2026-10-16-nola-bounce-energy/`, `2026-10-16-ghettotech-energy/`, `2026-10-16-newmusic-dupes/`, `2026-10-16-newmusic-tags/` (Claude Mac) — rescan New Orleans Bounce, Ghettotech, NEW MUSIC DOWNLOADS.
- **Keep for now (checks above):** `2026-10-16-prune-afrobeat-stale/` and the 10:01 ghost-cleanup backup.

### Waiting on Imani
1. Serato check (when Grok says it is safe), then the backup deletes above.
2. Energy v2: medium / low / NEEDS YOU rows for R&B, Pop, Hip Hop.
3. Metadata cleanup v2 preview (reported in PR #79).
4. Pop: 10 "(NC)" pairs (same bitrate, kept until NC is known), 125 refused + 589 uncertain duplicate pairs, Clean/Dirty for ~1,190 unmarked new Pop tracks, 23 tracks with no Year, 4 wav skipped, Tyla 128 vs 320 kbps pairs; junk Grouping left in GRP1 frames (16 "November") and record-pool watermarks (9) — the 2026-10-15 removal covered TIT1/FLAC/M4A only.
5. R&B: 95 medium + 55 unsure hand-entry Years (`rnb-year-hand08-proposals-20261016.csv`), 4 m4a/FLAC, 4 ID3v2.2 (07 Got Her Own, 08 Drown In It, Return Of The Mac (Topper-Intro) Extended, Shut It Down — tag upgrade first), 2 missing files; deferred rows: 575 reported vs 142 in deferred.csv (reconcile).
6. Afrobeats: 32 held (29 need a Genre call, 2 Dirty ID3v2.2: Free Fall, Unfortunate; 1 backslash name: Kupe Dance), 161 in `_NEEDS CHECK`, 5 afro-ish tags that are not exactly "Afrobeat", Davido "Five" (no BPM).
7. Baile: ear-check 7 tracks. `Baile/baile_plex` is a keep-whole pack.
8. The 4 same-name pairs (different songs, both kept) and their held Serato rows.
9. Smart crates: tidy `afro world ≫ Afrobeat` (3 Genre-contains rules); `Clean R&B` / `r&b edits` need `"r&b "` with a trailing space; `Clean R&B` should match ALL not ANY; `90s`/`2000s` use Grouping, not Year.
10. 34 ID3v2.2 files need a tag-upgrade approval before Grouping can be written.
11. Folder moves (one pass, after go): `R&b` → `R&B` rename (needs Imani's go, Serato fully quit; tools fixed in #81); 12 non-R&B tracks out of `R&b` (Beyoncé Formation Intro Clean + Dirty, bLAck pARty Flame + KEMET, Col3trane Take Notes, Smino Curtains + Garden Lady + HOOPTI, Ludacris Saturday → Hip-Hop; James Brown I FEEL GOOD → Funk & Soul; Son Of Mongo → House; Alkaline Mamacita → Latin); 5 Pop keepers (Everything is romantic → Lounge, ROCKET → Groovy Bounce, Ick + Na Na Na → Jersey Club, SAD GIRLZ LUV MONEY Remix → Afrobeats); misfiled tracks from Claude's Oct 15 sheets (Reggae 16, Dancehall 21 marked SKIP).
12. Later: unproven old backups (~120G, check per file before any delete); Childish Gambino *We Are God* (backup may be the only copy); old T5 repo copy (215G, unpushed rule commits, ~3,655 tracks not in the live library — leave for later).
13. NEW MUSIC DOWNLOADS, NOLA Bounce, Ghettotech, Baile/Afrobeats energy blanks, Davido "Five" (delete asked, NOT done — needs an explicit go): see "NEW MUSIC DOWNLOADS — Claude" at the end.
14. `_GENRE NEEDS CHECK`: 179 files (2026-10-15 count, see totals) plus ~105 in Claude's per-folder `_NEEDS CHECK`.

### Agents to do
- `rewrite_serato_paths.py` still does not repoint History/session files (SmartCrates and nested crates are done, #73).
- Grok's status PRs #74 and #79 are folded into this file; close them as superseded once Imani agrees.

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
| `2026-10-15-junk-grouping/` (211 files, 18 manifests · `_Serato_` 664) | 211 + `_Serato_` | all PASS; delete after Imani's Serato rescan | verified |
| `2026-10-15-pop-tags/` (pre-genre 1,221 · pre-year 282 · pre-grouping 1,193 · `_Serato_` 664) | 3,360 | all PASS; delete after Imani's Serato rescan of Pop, Cat Calls Inspo, Y2K | verified |
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
- (verified) **Pop version-label dupes p5 (2026-10-16 11:40, reported in PR #79):** 8 lower-bitrate copies removed (Imani OK 11:07); crates/smart crates repointed; 5 stale SQLite rows held.
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
- (verified) **R&B Years done:** **230** Years written today from `rnb-year-review-v2-20261015.csv` (YEAR-01 25, -02 25, -03 24, -04..10 156), Imani-approved incl. overrides; read back OK, no audio/Serato frame changes. **9** skipped by Imani (no Year): Dru Hill "I Should Be...", The-Dream "You", 7 no-artist rows (moved to hand-entry). R&B Genre batch: 110 written (4 ID3v2.2 skipped). All T5 R&B backups deleted after Imani's rescans.
- (verified) **R&B conflicting-genre rows (20) done 21:31 ET:** Genre → R&B on 7 (Get Away was already R&B), `Alt R&B` appended to Grouping on Computer Luv / Closer (ODE 2 U) / Lovely, Year on all 20 (I FEEL GOOD = 1965). Rescan R&b in Serato and confirm, then delete T5 `2026-10-15-rnb-genre-conflicts/`.
- (reported) Old `genre-cleanup-20260906` proven backups (~52G / 6,711 files) deleted after hash check. Keep manifests/logs.

## Log (old notes, superseded by Current state)

- 2026-10-15 22:15 ET: Prune tool PR #65 is open and NOT merged. Dry run with Afrobeat/: database V2 would remove 48. The 48+48 SQLite rows are held because they're still in containers. Next: Imani decides what to do about the container hold, then apply the prune, then run top3 rename.
- 2026-10-16 00:55 ET: PR #65 updated (not merged): --drop-stale-memberships + --add-live-memberships (normal crates only; smart crates reported, never inserted). Dry run Afrobeat/: database V2 removes 48; master/root.sqlite 0 adds, 0 removes, 48+48 HELD — every container involved is a SMART crate the live copy isn't (yet) in, plus 4 tracks with 2 live copies. Next: Imani decides how to treat smart-crate memberships (they're rule-generated; Serato rebuilds them), then apply, then top3 rename.
- 2026-10-16 00:50 ET: PR #65 updated (779bd7c, not merged) with --drop-stale-smart-memberships. Dry run Afrobeat/ (all flags): DB V2 remove 48; master.sqlite remove 44 / 0 adds / 118 memberships dropped; root.sqlite remove 44 / 0 adds / 143 dropped; 4 held each (Closer, Slow Wine also in Pop/CLEAN; Finesse, Focus also in R&B folders = cross-genre duplicates). Next: Imani merges PR #65 + gives go, then apply (Serato closed), then top3 rename.
- 2026-10-16 01:30 ET: Prune APPLIED (PR #65 merged): DB V2 −48, master/root.sqlite −44 each, post-check PASS, 4 held (Closer/Slow Wine also in Pop/CLEAN; Finesse/Focus also in R&B folders) — not a blocker for the top rename (previews pass). Backup T5 2026-10-16-prune-afrobeat-stale/ (delete after Imani confirms Serato). Status PR #66 open. Keep Serato CLOSED. Next: top3 Afrobeat → Afrobeats rename (needs go).
- 2026-10-16 01:40 ET (Grok): Afrobeats top rename DONE (top3); 4 "dupes" are different songs, kept; lyrics check running; 16 Drake titles done; Pop version-label preview ready; energy previews R&B/Pop/Hip Hop running. Full summary in "Tonight (Oct 16)". Status PR supersedes #64/#66.

## Serato ghosts — done (2026-10-16 10:14 ET)
- Last 22 old Music/Afrobeat/ paths cleared with the #75 tools (Kupe Dance repointed in 2 smart crates and its V2 record pruned; 21 multi-copy rows per SQLite DB pruned via the move map). Final check: 0 old paths in database V2, crates, smart crates, master.sqlite, root.sqlite.

## NEW MUSIC DOWNLOADS — Claude (current as of 2026-10-16 12:35 ET)

Folder `NEW MUSIC DOWNLOADS - MUSIC TO MOVE` is Claude's (Imani 2026-10-16). Work files: `new-music-move-20261016/` (job1-dupes, job2-tags, job3-cleandirty). No lock held right now.

| Job | State |
|---|---|
| 1 Duplicates | DONE 10:28. 97 exact-SHA-256 extra copies deleted (38 keepers verified). 1,124 -> 1,027 files. 0 exact copies elsewhere in the library. |
| 2 Missing tags | DONE 11:18. Genre 32 + Year 14 written (blank only, 2+ sources or repo filename keyword). 288 Genre / 146 Year left blank. Genre tool end-of-run crash fixed in PR #83 (merged). Source: nothing written; 105 subfolders listed for Imani (`job2-tags/job2-source-subfolders-20261016.csv`). |
| 3 Clean/Dirty | PREVIEW running, nothing moved. Audio check 5/12 batches (ETA ~13:20), lyrics check done. Interim: DIRTY 160, CLEAN 49, _NEEDS CHECK 603. Imani OK'd emptying uploader/album subfolders into `<bucket>/<Genre>/` (empty folders left in place). Moves only after Imani's go on the final preview, Serato closed. |
| 4 Energy | Not started. |

Not touched: DJ TOOLS, ALBUMS & PROJECTS (ask Imani first).

Waiting on Imani (Claude items):
- Job 3 go after the final preview; Source subfolder sheet (or skip).
- Blanks: NOLA Bounce 46, Ghettotech 22 (preview CSVs in `nola-bounce-energy-20261016/`, `ghettotech-energy-20261016/`).
- Baile/Afrobeats 11 untagged (`energy-blanks-20261016/`): Situationship Edit = Warmup (not written); Waste My Time / CRU LOVE / Se Dansa need a value; 3 Tems ID3v2.2 need conversion OK; Davido "Five" delete requested, NOT done (needs explicit go).
- Grok's `energy-written-change-suggestions-20261016.csv` suggests 8 NOLA Bounce Twerk Dutty -> Shake (not applied).
- Maybe misfiled: Bounce (Wassup Wassup x2, River Dance Bounce, no luv (+)); Ghettotech ~12 (see its preview).
- Serato: Rescan ID3 Tags on New Orleans Bounce, Ghettotech, NEW MUSIC DOWNLOADS.

T5 backups from Claude today (delete after Serato check): `2026-10-16-nola-bounce-energy`, `2026-10-16-ghettotech-energy`, `2026-10-16-newmusic-dupes`, `2026-10-16-newmusic-tags`.

Energy values: Imani's 10 (Warmup, Starter, Sing Along, Dance Flex, Shake, Twerk Dutty, Slow Wine, Peak, Cooldown, Reset) plus Bashment, which stays for Jamaican dancehall (already written in Dancehall/Reggae/RIDDIM). No Banger.
