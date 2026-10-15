# DJ Library — live status (any AI: read this first)

Short handoff. Details live in `GROK-DJ-LIBRARY-LOG.md`, layout rules in `LIBRARY-LAYOUT.md`, Claude's Oct 15 wrap-up in `CLAUDE-HANDOFF-2026-10-15.md`.

## Last updated

2026-10-15 16:41 EDT — Grok (DJ B.A.E. Library): junk Grouping removal written (211 files); see GROK-DJ-LIBRARY-LOG.md.
2026-10-15 16:22 EDT — Grok (DJ B.A.E. Library): Pop new-adds tag write (Genre/Year/Source Grouping) done; see GROK-DJ-LIBRARY-LOG.md.
2026-10-15 ~15:45 EDT — Claude (cloud, repo only): genre folder approval, PR #55 (see Waiting on Imani). No library files touched.
2026-10-15 ~16:45 EDT — Claude (cloud, repo only): junk-Grouping removal tool + docs (PR #57); recorded the Mac session's Pop progress report. No library files touched.
2026-10-15 ~15:55 EDT — Claude (cloud, repo only): PR #55 merged (also reads a plain "Afrobeat" tag as Afrobeats; "Dub" stays held). No library files touched.
2026-10-15 15:50 EDT — Claude (folder-rename tooling PR #56; library untouched)
2026-10-15 ~16:50 EDT — Claude (cloud, repo only): fixed main CI after #48 (History staging script classified as allowlisted infrastructure). No library files touched.

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

- (verified) PR **#55** merged: 59 Genres now file into their existing folders, by exact on-disk spelling (Hip-Hop → `Hip Hop`, Afrobeats → `Afrobeat`, Ballroom Vogue → `Ballroom`, Funk & Soul → `Funk`, Riddim → `RIDDIM`; smaller styles into a parent folder with their exact tag kept, e.g. Deep House → `House`). 17 stay held → `_GENRE NEEDS CHECK`. A plain "Afrobeat" tag now reads as Afrobeats. Evidence: `handoff/live-top-level-folders-2026-10-15.md`. **Mac clone: `git pull` before building the next plan.** If the `Afrobeat` folder is later renamed to `Afrobeats`, update `folder_names` in the same change.
- (verified) Repo PRs **#50–#53** merged (R&B spelling; FLAC/M4A Year+Grouping writers; grouping CSV builder fixes; FLAC/M4A in grouping planner).
- (reported) Claude Oct 15: 10 folders sorted/energy-tagged (~849 Energy Grouping appends). See `CLAUDE-HANDOFF-2026-10-15.md`.
- (verified) Genre fixes (Oct 15): **377** tags, 0 failures — includes **317 Afrobeat → Afrobeats**, plus Dancehall/Reggae/World/Amapiano/Soca. Imani rescanned; T5 backup deleted.
- (verified) R&B confident tags: **492 Year** + **55 Grouping** (MP3), 0 Genre (none were high-confidence). Imani rescanned; T5 backup deleted.
- (reported) Old `genre-cleanup-20260906` proven backups (~52G / 6,711 files) deleted after hash check. Keep manifests/logs.

## In progress

- **New Pop music** (Imani adding): `Pop/Pop from ` · `Cat Calls Inspo/` · `Pop/00_s Music/Pop Girl At Heart/` (**982 files**, was still copying at 471) · top-level `Y2K/`.
- **Approved plan** (Imani): Genre → Pop; append `Source: <playlist>` to Grouping; 4-digit Years; strip "November" / record-pool watermark Groupings (now its own step, see Waiting on Imani).
- (reported by the Mac session, ~16:00 EDT) **Written, then paused:** Genre → Pop on **1,193** files (all of Pop Girl At Heart, Cat Calls Inspo, Y2K) and **238** Pop Girl Years cut to 4 digits. All read back correctly, backed up, undoable. Imani to rescan those three in Serato.
- **Refreshed Pop preview, waiting on Imani's go:** 44 Pop Girl Years; 1,202 `Source:` Grouping appends (Pop Girl 982, Cat Calls 81, Y2K 130, Pop from 9). 23 tracks have no year (left blank); 4 wav skipped.
- **Duplicates (~440):** keep best copy (bitrate / clean-dirty), merge good tags + Source onto keeper; Imani approves delete list before any remove. Check Serato crates pointing at losers.
- **Inbox genre/year lookup** still running (`inbox-genre-lookup/`, ~325+/514). When done: `merge.py` → preview → write only after approval.

- **Folder-rename tooling (PR `claude/folder-rename-tooling`, not yet run on the live library):** `apply_folder_rename_plan.py` (R&b→R&B case-only via temp name, Afrobeat→Afrobeats; preview/apply/undo/recover) and `rewrite_serato_sqlite_paths.py` (`asset.portable_id` in **both** `master.sqlite` and `root.sqlite`). Real-scale rehearsal on copies passed (4,311 Serato refs, 3,824 rows per SQLite file, undo works). Review fixes landed (atomic no-replace rename; SQLite hardening); #56 reviewed OK. Live run order: own full copies of `_Serato_` + music, spare-copy trial incl. undo, check Serato re-imports, then the real rename with Serato quit. Needs Imani's separate go; read `references/folder-rename-runbook.md` first (open questions: Serato re-import of `database V2`, SmartCrates cached paths).

## Waiting on Imani

- **Junk Grouping removal:** rescan ID3 tags in Serato on the 18 affected folders (mainly Hip Hop, R&b, Pop) and confirm, then delete T5 `2026-10-15-junk-grouping/`.
- **Pop new adds:** rescan ID3 tags in Serato (Pop, Cat Calls Inspo, Y2K) and confirm, then delete T5 `2026-10-15-pop-tags/`. Decide: ~440 dupes (keep best, merge tags), Clean/Dirty for ~1,190 unmarked, watermark/November Grouping strips (needs writer change + append-only exception).
- **Merge PR #57** (junk Grouping removal tool; CI green), then `git pull` on the Mac.
- **Junk Grouping removal ("November", record-pool watermarks)** — Imani said yes to a narrow exception. Tool is in PR #57. Steps, Pop / Cat Calls Inspo / Y2K, after the `Source:` write:
  1. `python3 scripts/build_grouping_removal_plan.py survey --root "$LIB/<folder>" --out-dir "$W/grouping-survey"` (read-only) → show Imani `GROUPING-VALUES.md`.
  2. Imani picks the exact junk values → one per line in `$W/remove.txt`.
  3. `python3 scripts/build_grouping_removal_plan.py plan --root "$LIB/<folder>" --remove "$W/remove.txt" --out-dir "$W/grouping-removal"` → show `GROUPING-REMOVAL-PREVIEW.md`; Imani says go.
  4. Scoped backup + PASS manifest, then `apply_grouping_plan.py --apply` with the lock, as for any Grouping write. Serato rescan.
- **575** R&B "NEEDS YOU" rows (`rnb-tag-apply-20261015/deferred.csv` / preview v3).
- Tyla 128 kbps vs 320 kbps pairs (not hash dupes) — keep/delete call.
- Fix Serato smart crates: `Clean R&B` / `r&b edits` need `"r&b "` with trailing space; `Clean R&B` should match ALL not ANY; `90s`/`2000s` use Grouping not Year.
- **5** afro-ish tags left in Afrobeat (e.g. `r&b afro beat lounge`) — not exact "Afrobeat".
- Unproven old backups ~**120G** (Hip Hop/source + passA copies, newbatch-20260906b, remainders) — do not delete until per-file checked.
- Missing library copy: Childish Gambino — *We Are God* (backup may be only copy; ask if intentional).

## Next up

1. Rescan Pop Girl → refresh preview → write Pop tags (scoped backup) after Serato closed.
2. Duplicate keep/delete list for Imani, then remove approved losers.
3. **14** R&B m4a/FLAC Year/Grouping (writers on main via #51).
4. **34** ID3v2.2 Grouping (needs conversion approval).
5. PR: extend `rewrite_serato_paths.py` to History + SmartCrates.
6. One-pass folder moves: `R&b` → `R&B` + CLEAN/DIRTY sort; Afrobeat folder → Afrobeats; misfiled tracks from Claude sheets. (Rename tooling exists in the PR above; needs review + Imani's go first.)
7. Inbox tag write after lookup + approval. Energy for Baile/Afrobeats = Claude unless asked.

Library root: `~/Music/DJ-Music/Music` · Serato: `~/Music/_Serato_` · Mac: `djb.a.e` · Repo: `imanisworld/remix-edit-sort`
