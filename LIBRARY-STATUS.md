# DJ Library — live status (any AI: read this first)

Short handoff. Details live in `GROK-DJ-LIBRARY-LOG.md`, layout rules in `LIBRARY-LAYOUT.md`, Claude's Oct 15 wrap-up in `CLAUDE-HANDOFF-2026-10-15.md`.

## Last updated

2026-10-16 ~12:00 EDT — Claude (cloud, repo only): code review of #63/#65/#73/#75. Prune tool fix PR opened; see "Code review holds" below. No library files touched.
2026-10-16 10:58 EDT — Grok (DJ B.A.E. Library): high-confidence Energy v2 (10-value scale) written: R&B 506, Pop 895, Hip Hop 950 Grouping appends (29 ID3v2.2 skipped, 0 Energy conflicts). Med/low/NEEDS YOU/Reset not written. Backup T5 2026-10-16-energy-v2-high; Imani to rescan ID3 tags in Serato. See GROK-DJ-LIBRARY-LOG.md.
2026-10-16 09:35 EDT — Grok (DJ B.A.E. Library): Afrobeats energy written (347); see GROK-DJ-LIBRARY-LOG.md.
2026-10-16 09:20 EDT — Grok (DJ B.A.E. Library): Afrobeats Clean/Dirty written (79 Dirty moved+tagged, 161 to _NEEDS CHECK, 32 held); genre-folder config PR #69; see GROK-DJ-LIBRARY-LOG.md.
2026-10-16 01:40 EDT — Grok (DJ B.A.E. Library): tonight's summary added (prune applied, Afrobeats rename done, 4 "dupes" = different songs, lyrics check running, 16 Drake titles, Pop version-label preview, energy previews running). See "Tonight (Oct 16)" below.
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

## Code review holds (Claude, 2026-10-16)

- **Do not run `prune_missing_serato_records.py` until the fix PR is merged.** It could prune the record of a file that still exists (accent or letter-case spelling differences), and could prune a same-name *different* song's record or hand its crates to the wrong song (database V2 had no twin check; SQLite skipped the move map when there was one twin). Undo could replay a stale WAL or silently skip SQLite.
- **Keep T5 `2026-10-16-prune-afrobeat-stale/`.** database V2 removed 48 records vs 44 per SQLite file; the 4-record gap matches the 4 same-name pairs (Closer, Slow Wine, Finesse, Focus). Check read-only that the Afrobeats copies of those 4 still have their record and crates before deleting this backup.
- **Do not run `delete_verified_duplicates.py --mode audio-match`** until the fix PR is merged (pairs files then also need `keep_sha256`). Version markers come only from filename + Title, so a CLEAN-folder copy and its DIRTY-folder twin with the same name can match (review test: similarity 0.956, Clean copy marked removable). Different edits/remixes share one marker (`DJ A Edit` = `DJ B Edit`); `Main`/`Amended`/`Censored`/`Club Mix` are not markers; only the first 120 s is fingerprinted; the keep file is not hash-pinned; nothing blocks `Imani Drops`. Exact mode (SHA-256) is unaffected.
- **Check:** were the 202 Pop duplicates deleted on 2026-10-15 removed with `--mode audio-match`? If yes, list any pair where the deleted file and the kept file sit in different CLEAN/DIRTY folders (or one says Clean/Dirty in Grouping), and keep that backup until checked.
- `rewrite_serato_paths.py` (crate repoint): no corruption found. Weak spots before the R&b → R&B rename: a stop mid-run leaves database V2 repointed but crates not (recover still says clean); case/accent spelling variants are skipped while the post-check passes; repointing can leave a crate with the same track twice.

## Tonight (Oct 16, 00:00-01:40 EDT)

- (verified) **PR #65 merged**: `prune_missing_serato_records.py` (dry-run first, verified backup, journal, undo; smart-crate memberships only).
- (verified) **Stale Afrobeat prune applied 01:20-01:30**: database V2 -48, master.sqlite -44, root.sqlite -44, post-check PASS. 4 rows held per SQLite file (Closer, Slow Wine, Finesse, Focus old paths).
- (verified) **Afrobeat -> Afrobeats top-level rename DONE 01:24-01:27** (top3). `Music/Afrobeats` exists, no `Afrobeat`; database V2, crates and both SQLite files repointed, post-checks PASS. Genre-folder config PR (`folder_names` -> `Afrobeats`) still NOT opened.
- (verified) **The "4 dupes" are NOT duplicates**: same filename, different songs (Goapele vs Ayotemi "Closer"; Tony! Toni! Toné! vs Machel Montano "Slow Wine"; Drake vs Pheelz "Finesse"; H.E.R. vs Joeboy "Focus"). Nothing deleted; all 8 files kept. The 4 held SQLite rows can stay or be fixed later with a tag-aware match.
- (verified) **Explicit terms list approved as is** by Imani (`folder-pass-preview-20261015/explicit-terms.txt`, 19 terms).
- ~~Afrobeats lyrics check running~~ done 02:27; see the Clean/Dirty entry below. Old note: (started 01:27, read-only): 272 `_NEEDS CHECK` tracks, 6 batches, ~30 min each (`afrobeats-lyrics-20261016/`). Result = Clean/Dirty preview for Imani; nothing moves until she approves. Afrobeats energy comes after.
- (verified) **Drake title cleanup DONE 01:31**: 16 titles (R&b 15, Hip Hop 1), readback 16/16, Artist/audio/Serato frames unchanged. 10 left on purpose.
- **Pop version-label dupes preview** (`pop-version-label-dupes-preview-20261016.csv/.md`, read-only): 18 pairs. 8 = keep the 320 kbps copy and remove the lower-bitrate one (waiting on Imani). 10 = same bitrate, KEEP BOTH until "(NC)" meaning is known (Imani doesn't remember what NC means; check by ear or lyrics).
- **Energy previews RUNNING** for R&B, Pop, Hip Hop (`energy-preview-20261016/`, read-only; tag scan done for R&b 3,459 and Pop 2,784, Hip Hop ~7,640 in progress). Preview only, then Imani's approval.
- **Serato must stay CLOSED** until the lyrics check is finished and Imani is told it's safe.

### T5 backups waiting on Imani's Serato check (then delete)
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
R&b 3,468 · Hip Hop 7,649 · Pop 2,846 · neptunes 2,102 · House 1,249 · Events 1,095 · NEW MUSIC DOWNLOADS 942 · Afrobeat 352.

## Done

- (verified) **Afrobeats energy written (2026-10-16 09:33 EDT):** 347 Grouping appends from the approved preview — `Energy: Warmup` 210, `Energy: Sing Along` 96, `Energy: Dance Flex` 41; readback 347/347, audio and Serato frames unchanged. Skipped: Davido "Five By Alhanislam" (NEEDS YOU, no BPM), 3 Tems ID3v2.2 files (Forever, Free Fall, Unfortunate), and the already-deleted SAD GIRLZ dupe. Backup T5 `2026-10-16-afrobeats-energy/`.
- (verified) **Afrobeats Clean/Dirty sort written (2026-10-16 ~09:15 EDT, Imani approved the preview as is).** Evidence: audio transcript (faster-whisper) + published lyrics (lrclib.net), approved explicit-terms list; a hit = Dirty, no hit = `_NEEDS CHECK`. **79** moved to `Afrobeats/DIRTY/Afrobeats/` with `Dirty` appended to Grouping (readback 79/79). **161** moved to `Afrobeats/_NEEDS CHECK/Afrobeats/` (no tag change). **32 held in place:** 28 + 1 no-hit tracks whose Genre isn't Afrobeats (need a Genre call), 2 Dirty ID3v2.2 files (Free Fall, Unfortunate), 1 Dirty file with a backslash in its name (`\A-Star - Kupe Dance`). Serato database V2/crates + master/root.sqlite repointed; post-checks PASS; 0 unresolved Afrobeats paths. Backup T5 `2026-10-16-afrobeats-cleandirty/`.
- (verified) Top-level `Afrobeat` → `Afrobeats` rename (2026-10-16 01:25). Config follow-up: PR #69 (`folder_names` → `Afrobeats`), waiting on Imani's merge.
- (verified) PR **#55** merged: 59 Genres now file into their existing folders, by exact on-disk spelling (Hip-Hop → `Hip Hop`, Afrobeats → `Afrobeat`, Ballroom Vogue → `Ballroom`, Funk & Soul → `Funk`, Riddim → `RIDDIM`; smaller styles into a parent folder with their exact tag kept, e.g. Deep House → `House`). 17 stay held → `_GENRE NEEDS CHECK`. A plain "Afrobeat" tag now reads as Afrobeats. Evidence: `handoff/live-top-level-folders-2026-10-15.md`. **Mac clone: `git pull` before building the next plan.** If the `Afrobeat` folder is later renamed to `Afrobeats`, update `folder_names` in the same change.
- (verified) Repo PRs **#50–#53** merged (R&B spelling; FLAC/M4A Year+Grouping writers; grouping CSV builder fixes; FLAC/M4A in grouping planner).
- (reported) Claude Oct 15: 10 folders sorted/energy-tagged (~849 Energy Grouping appends). See `CLAUDE-HANDOFF-2026-10-15.md`.
- (verified) Genre fixes (Oct 15): **377** tags, 0 failures — includes **317 Afrobeat → Afrobeats**, plus Dancehall/Reggae/World/Amapiano/Soca. Imani rescanned; T5 backup deleted.
- (verified) R&B confident tags: **492 Year** + **55 Grouping** (MP3), 0 Genre (none were high-confidence). Imani rescanned; T5 backup deleted.
- (verified) **Pop duplicates finished:** 202 duplicate copies deleted (Imani approved), every keeper is the 320 kbps copy, Serato DB V2 repointed. All T5 pop-dupes backups deleted after Imani checked Serato.
- (verified) **R&B Years done:** **230** Years written today from `rnb-year-review-v2-20261015.csv` (YEAR-01 25, -02 25, -03 24, -04..10 156), Imani-approved incl. overrides; read back OK, no audio/Serato frame changes. **9** skipped by Imani (no Year): Dru Hill "I Should Be...", The-Dream "You", 7 no-artist rows (moved to hand-entry). R&B Genre batch: 110 written (4 ID3v2.2 skipped). All T5 R&B backups deleted after Imani's rescans.
- (verified) **R&B conflicting-genre rows (20) done 21:31 ET:** Genre → R&B on 7 (Get Away was already R&B), `Alt R&B` appended to Grouping on Computer Luv / Closer (ODE 2 U) / Lovely, Year on all 20 (I FEEL GOOD = 1965). Rescan R&b in Serato and confirm, then delete T5 `2026-10-15-rnb-genre-conflicts/`.
- (reported) Old `genre-cleanup-20260906` proven backups (~52G / 6,711 files) deleted after hash check. Keep manifests/logs.

## In progress

- Old T5 repo copy (215G): audited 2026-10-15 15:29 EDT, read-only, NOT deleted. It has unpushed rule commits (Nu Wave, Juke & Footwork, T5 dedupe --serato-backup), 13 attached worktrees, and about 3,655 backup audio tracks not found in the live library, including the Gambino We Are God.flac. (verified) Imani: leave for later.
- **New Pop music** (Imani adding): `Pop/Pop from ` · `Cat Calls Inspo/` · `Pop/00_s Music/Pop Girl At Heart/` (**982 files**, was still copying at 471) · top-level `Y2K/`.
- **Approved plan** (Imani): Genre → Pop; append `Source: <playlist>` to Grouping; 4-digit Years; strip "November" / record-pool watermark Groupings (now its own step, see Waiting on Imani).
- (reported by the Mac session, ~16:00 EDT) **Written, then paused:** Genre → Pop on **1,193** files (all of Pop Girl At Heart, Cat Calls Inspo, Y2K) and **238** Pop Girl Years cut to 4 digits. All read back correctly, backed up, undoable. Imani to rescan those three in Serato.
- **Refreshed Pop preview, waiting on Imani's go:** 44 Pop Girl Years; 1,202 `Source:` Grouping appends (Pop Girl 982, Cat Calls 81, Y2K 130, Pop from 9). 23 tracks have no year (left blank); 4 wav skipped.
- **Inbox genre/year lookup** still running (`inbox-genre-lookup/`, ~325+/514). When done: `merge.py` → preview → write only after approval.

- **Folder-rename tooling (PR `claude/folder-rename-tooling`, not yet run on the live library):** `apply_folder_rename_plan.py` (R&b→R&B case-only via temp name, Afrobeat→Afrobeats; preview/apply/undo/recover) and `rewrite_serato_sqlite_paths.py` (`asset.portable_id` in **both** `master.sqlite` and `root.sqlite`). Real-scale rehearsal on copies passed (4,311 Serato refs, 3,824 rows per SQLite file, undo works). Review fixes landed (atomic no-replace rename; SQLite hardening); #56 reviewed OK. Live run order: own full copies of `_Serato_` + music, spare-copy trial incl. undo, check Serato re-imports, then the real rename with Serato quit. Needs Imani's separate go; read `references/folder-rename-runbook.md` first (open questions: Serato re-import of `database V2`, SmartCrates cached paths).

## Waiting on Imani

- **(SUPERSEDED 01:27 Oct 16 — rename done, see Tonight)** 22:10 update: SAD GIRLZ stale record gone (Imani 22:04), top rename retried (`top2`) and refused again, then rolled back (folder is `Afrobeat`). After Imani opened Serato, it re-added the 48 OLD inner paths (`Afrobeat/CLEAN/Afrobeat/…` 32, `Afrobeat/DIRTY/Afrobeat/…` 16) in database V2 and both SQLite files, alongside the correct `…/Afrobeats/` entries. Those 48 are now missing-file duplicates in Serato and block the rewriter. Runbook open question 1 answered: Serato re-imports/keeps old paths after a rewrite. Needs a decision before retrying (Imani removes the 48 missing duplicates in Serato, or a sanctioned prune tool). Config PR still not opened.
- **(SUPERSEDED — top rename DONE 01:27 Oct 16)** Afrobeat -> Afrobeats folder rename: PARTLY DONE (2026-10-15 21:5x ET). Inner folders renamed `Afrobeat/CLEAN/Afrobeat` -> `CLEAN/Afrobeats` and `DIRTY/Afrobeat` -> `DIRTY/Afrobeats`, Serato DB V2/crates/SQLite repointed (tooling PR #56). **Top-level rename rolled back**: `rewrite_serato_paths.py` refused because `database V2` still holds a stale record for the deleted Pop-dupe `Afrobeat/SAD GIRLZ LUV MONEY - Remix.mp3`; folder renamed back with the tool's undo, Serato consistent (post-check: that 1 stale ref is the only `Music/Afrobeat/` miss). Needs: remove that missing track in Serato (or a sanctioned prune tool), then re-run. Genre-folder config PR NOT opened (runbook: only after the top rename). Backups T5 `2026-10-15-afrobeats-rename/{inner,top}`.
- **Smart crate `afro world >> Afrobeat`** (not touched): 3 Genre-contains rules: `Afrobeat`, `afro beat`, `afrobeats`. Imani to tidy in Serato (one `Afrobeat` rule already matches Afrobeats).
- **Baile energy written** (131 Grouping appends). 7 EAR CHECK rows still need Imani; also Davido Five (Imani to call). `Baile/baile_plex` = **keep-whole pack** (never sorted/moved).
- **Afrobeats energy** still waiting on the Clean/Dirty sort (lyrics check RUNNING since 01:27 Oct 16; 'no hit' = _NEEDS CHECK).

- **Pop new adds:** rescan ID3 tags in Serato (Pop, Cat Calls Inspo, Y2K) and confirm, then delete T5 `2026-10-15-pop-tags/`. Decide: ~440 dupes (keep best, merge tags), Clean/Dirty for ~1,190 unmarked, watermark/November Grouping strips (needs writer change + append-only exception).
- **Junk Grouping removal ("November", record-pool watermarks)** — Imani said yes to a narrow exception. Tool is in PR #57. Steps, Pop / Cat Calls Inspo / Y2K, after the `Source:` write:
  1. `python3 scripts/build_grouping_removal_plan.py survey --root "$LIB/<folder>" --out-dir "$W/grouping-survey"` (read-only) → show Imani `GROUPING-VALUES.md`.
  2. Imani picks the exact junk values → one per line in `$W/remove.txt`.
  3. `python3 scripts/build_grouping_removal_plan.py plan --root "$LIB/<folder>" --remove "$W/remove.txt" --out-dir "$W/grouping-removal"` → show `GROUPING-REMOVAL-PREVIEW.md`; Imani says go.
  4. Scoped backup + PASS manifest, then `apply_grouping_plan.py --apply` with the lock, as for any Grouping write. Serato rescan.
- **R&B still open** (`rnb-year-review-v2-20261015.csv/.md`): type-by-hand Years: **41** high-confidence written 2026-10-16 09:22 (online lookup, read back 41/41, 0 audio/Serato frame changes; undo `rnb-hand-years-20261016/rnb-hand-year-bpm-undo-20261016-092227.jsonl`; T5 backup `2026-10-16-rnb-hand-years`), **95** medium + **55** unsure still open (`rnb-year-hand08-proposals-20261016.csv`), **4** m4a/FLAC, **4** ID3v2.2 (07 Got Her Own, 08 Drown In It, Return Of The Mac (Topper-Intro) Extended, Shut It Down — tag upgrade first), **2** missing files.
- **Not R&B — move out of the R&b folder in the folder pass** (Genre left unchanged, Year written 2026-10-15): Hip-Hop: Beyoncé Formation (Intro Clean) + (Intro Dirty), bLAck pARty Flame + KEMET, Col3trane Take Notes, Smino Curtains + Garden Lady + HOOPTI, Ludacris Saturday (Oooh! Ooooh!); Funk & Soul: James Brown I FEEL GOOD; House: Son Of Mongo; Latin; Pop: Alkaline Mamacita.
- **Pop duplicates still waiting:** 18 version-label holds (preview 20261016: 8 removals waiting, 10 kept until NC known), 125 refused, 589 uncertain pairs. 5 keepers to move into their genre folders in the folder pass (Everything is romantic → Lounge, ROCKET → Groovy Bounce, Ick + Na Na Na → Jersey Club, SAD GIRLZ LUV MONEY Remix → Afrobeats; Grouping already tagged).
- **Drake title cleanup: DONE 2026-10-16 01:31.** 16 titles cleaned in R&b (15) and Hip Hop (1), e.g. "Drake - Marvins Room" -> "Marvins Room". Artist tags unchanged. 10 left as-is on purpose: 7 remixes/edits where Drake only appears in the Title, plus KAYTRANADA and 2 Drake/21 Savage titles. Undo + T5 backup: drake-title-20261016/, T5 2026-10-16-drake-titles/ (delete after Serato check).
- Tyla 128 kbps vs 320 kbps pairs (not hash dupes) — keep/delete call.
- Fix Serato smart crates: `Clean R&B` / `r&b edits` need `"r&b "` with trailing space; `Clean R&B` should match ALL not ANY; `90s`/`2000s` use Grouping not Year.
- **5** afro-ish tags left in Afrobeat (e.g. `r&b afro beat lounge`) — not exact "Afrobeat".
- Unproven old backups ~**120G** (Hip Hop/source + passA copies, newbatch-20260906b, remainders) — do not delete until per-file checked.
- Missing library copy: Childish Gambino — *We Are God* (backup may be only copy; ask if intentional).

## Next up

1. Rescan Pop Girl → refresh preview → write Pop tags (scoped backup) after Serato closed.
2. Imani reviews the held/refused/uncertain duplicate pairs.
3. R&B conflicting-genre (20) + hand-entry Years (150 left: 95 medium, 55 unsure); m4a/FLAC Year/Grouping (writers on main via #51).
4. **34** ID3v2.2 Grouping (needs conversion approval).
5. PR: extend `rewrite_serato_paths.py` to History + SmartCrates.
6. One-pass folder moves: `R&b` → `R&B` + CLEAN/DIRTY sort; Afrobeat folder → Afrobeats; misfiled tracks from Claude sheets. (Rename tooling exists in the PR above; needs review + Imani's go first.)
7. Inbox tag write after lookup + approval. Energy for Baile/Afrobeats = Claude unless asked.

Library root: `~/Music/DJ-Music/Music` · Serato: `~/Music/_Serato_` · Mac: `djb.a.e` · Repo: `imanisworld/remix-edit-sort`

- 2026-10-15 22:15 ET: Prune tool PR #65 is open and NOT merged. Dry run with Afrobeat/: database V2 would remove 48. The 48+48 SQLite rows are held because they're still in containers. Next: Imani decides what to do about the container hold, then apply the prune, then run top3 rename.
- 2026-10-16 00:55 ET: PR #65 updated (not merged): --drop-stale-memberships + --add-live-memberships (normal crates only; smart crates reported, never inserted). Dry run Afrobeat/: database V2 removes 48; master/root.sqlite 0 adds, 0 removes, 48+48 HELD — every container involved is a SMART crate the live copy isn't (yet) in, plus 4 tracks with 2 live copies. Next: Imani decides how to treat smart-crate memberships (they're rule-generated; Serato rebuilds them), then apply, then top3 rename.
- 2026-10-16 00:50 ET: PR #65 updated (779bd7c, not merged) with --drop-stale-smart-memberships. Dry run Afrobeat/ (all flags): DB V2 remove 48; master.sqlite remove 44 / 0 adds / 118 memberships dropped; root.sqlite remove 44 / 0 adds / 143 dropped; 4 held each (Closer, Slow Wine also in Pop/CLEAN; Finesse, Focus also in R&B folders = cross-genre duplicates). Next: Imani merges PR #65 + gives go, then apply (Serato closed), then top3 rename.
- 2026-10-16 01:30 ET: Prune APPLIED (PR #65 merged): DB V2 −48, master/root.sqlite −44 each, post-check PASS, 4 held (Closer/Slow Wine also in Pop/CLEAN; Finesse/Focus also in R&B folders) — not a blocker for the top rename (previews pass). Backup T5 2026-10-16-prune-afrobeat-stale/ (delete after Imani confirms Serato). Status PR #66 open. Keep Serato CLOSED. Next: top3 Afrobeat → Afrobeats rename (needs go).
- 2026-10-16 01:40 ET (Grok): Afrobeats top rename DONE (top3); 4 "dupes" are different songs, kept; lyrics check running; 16 Drake titles done; Pop version-label preview ready; energy previews R&B/Pop/Hip Hop running. Full summary in "Tonight (Oct 16)". Status PR supersedes #64/#66.

## Serato ghosts — done (2026-10-16 10:14 ET)
- Last 22 old Music/Afrobeat/ paths cleared with the #75 tools (Kupe Dance repointed in 2 smart crates and its V2 record pruned; 21 multi-copy rows per SQLite DB pruned via the move map). Final check: 0 old paths in database V2, crates, smart crates, master.sqlite, root.sqlite.
