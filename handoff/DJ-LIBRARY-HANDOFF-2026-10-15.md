# DJ library handoff (2026-10-15)

Written by Claude (cloud session) for the next assistant. Each item is marked with how I know it:

- **[verified]**: I read it in the repo or on GitHub during this session.
- **[on record]**: written in the repo's handoff docs. Historical, not re-checked against the live Mac.
- **[reported]**: the owner told me (their Oct 14 status update); I could not check it.
- **[unsure]**: I don't know. Check before relying on it.

I never had access to the Mac, the music files, Serato or rekordbox. Everything I did was in the GitHub repo and two private claude.ai pages.

---

## 0. Read these first (in the repo)

Repo: **`imanisworld/remix-edit-sort`** on GitHub (private; never publish it or add a public remote) [verified].

1. `CLAUDE.md`: full rules and current state. Despite the name, it applies to every assistant.
2. `AGENTS.md`: short version for Codex/Cursor. **Only on PR #37, not yet on `main`.**
3. `SAFETY-MODEL.md`: how any change to music files must be done. It wins over every other doc.
4. `handoff/HANDOFF-DJ-MUSIC-SORT.md` and `handoff/CURRENT-STATUS.md`: history of every folder sorted.
5. `references/genre-atlas/README.md`: rules for the genre atlas. **Only on PR #37 for now.**

---

## 1. Software, library and file locations

| Thing | Where | Confidence |
|---|---|---|
| Main DJ software | **Serato DJ**. All safety rules protect Serato cues, beatgrids, crates and `_Serato_` data. | on record |
| Music library root | `/Users/djb.a.e/Music/DJ-Music/Music` (folders like `Events/`, `Hip Hop/`, `R&b/`, `Amapiano ` (with a trailing space), `Country/`; inside each, version folders `CLEAN/`, `DIRTY/`, `REMIXES & EDITS/`, `_NEEDS CHECK/`, then genre folders) | on record (Aug 2026) |
| Serato database folder | `/Users/djb.a.e/Music/_Serato_` (holds `database V2` and the crate files) | on record |
| Serato crate files | Inside `_Serato_` (crate files, e.g. `womens international -  Jamie.crate`). Exact subfolder [unsure]; standard Serato is `_Serato_/Subcrates/`. | unsure |
| rekordbox | Used read-only. Its `master.db` is encrypted and must **never** be read or written. Only a user-made rekordbox **collection XML export** may be imported (ADR in `references/rekordbox-index-architecture.md`). Where the owner saves those XML exports: [unsure]. | verified (repo rule) |
| Apple Music | Only used by another assistant (Grok) to check set lists against her Apple library. No Apple Music file paths are known. | reported / unsure |
| Remix/edit download tree | `~/Downloads/SoundCloud` (the folder tree `genre-rules.json` and `scripts/build_plan.py` sort into: `Remixes & Edits/<genre>/<remixer>/`) | on record |
| Repo clone on the Mac | `/Volumes/T5 EVO/DJ/Tools/remix-edit-sort` (as of Aug 26). An older path, `/Users/djb.a.e/Music/DJ-Music/Tools/remix-edit-sort`, also appears in docs. Which one is current: [unsure]. | on record / unsure |
| Backups drive | External drive `T5 EVO`. Batch backups: `/Volumes/T5 EVO/DJ/Backups/remix-edit-sort/` | on record |
| Project working folder | `~/Documents/DJ-library-project-20260825/` (T5 backup index planned at `.../t5-backup-sha256-index.json`; set-history exports at `.../history-exports-for-grok/`) | on record + reported |
| Set prep files | Desktop: `oct-sets-final-2026-10-14.md`, `serato-check-oct.md`, `need-to-get-oct.txt`, `serato-library.csv` | reported (I haven't seen them) |

---

## 2. What's done and what's half-done

### Library sorting and tagging (before this session) [on record, `handoff/HANDOFF-DJ-MUSIC-SORT.md`]
- **Done:** `Events` sorted by version and genre (3,046 tracks). Grouping tags filled in for 3,010 MP3s; 36 old-format files skipped on purpose.
- **Done:** `Hip Hop` (5,888 tracks, 44 GB), `music to move`, `NewFiles`, `RAP RN`, `90S HIPHOP R&B CLEAN DIRTY`, `Ambient`, `Wedding`, `belarusian_electronic`, `metro`, `Dub`, `Rave`, and 8 small `Events` folders (`Halloween Party`, `karaoke`, `tpom `, `women`, `white dance party`, `NYE`, `xmas`, `Gay Women Party`).
- **Done:** `NIGER` folder (a typo for **Nigeria**). Grouping on both tracks reads `Source: Nigeria`; Genre and Artist left unknown on purpose.
- **Done:** `VDBL`. 150 tracks sorted, and the 141 classified ones merged into `Hip Hop`, `R&b`, `Amapiano ` and `Country/`. VDBL is a download folder, **not** an event.
- **Half-done:** 9 VDBL tracks still sit in `Events/VDBL/.../_GENRE NEEDS CHECK` and `_NEEDS CHECK`, waiting for the owner's manual review.
- **Done:** one metadata batch (`metadata-mp3-intake-20260825-225759`, 15 files). Its status is COMPLETE, and the owner spot-checked it in Serato.
- **Not started:** a library-wide pass to fill in missing artist, title, genre, BPM and key, and Grouping everywhere. Scope to be confirmed with the owner first.

### This session: genre work (repo only, nothing on the Mac) [verified]
- **Genre atlas added.** A copy of a third-party "World Music Atlas" (5,047 genres), plus a hand-made mapping from each master genre to atlas genres. It is a reference only. **PR #37.**
- **Genre Master Review.** The owner answered 27 genre questions; their decisions are in section 3. The master genre list went from 68 to 76 genres. **PR #37.**
- **Tag name separated from folder name.** A `folder_names` map was added to both rule files. **PR #37.**
- **ChatGPT's PR #41** brought both older rule files in line with the 76 names, added a CI check for genre names, and added a landing-folder move preview. That preview only writes a report. **But #41 misread several of the owner's decisions** (it only saw a paste, not their answers).
- **Codex's PR #47** allows a track into a genre folder only if that folder is on the approved list. Only 3 folders are approved: R&B → `R&b`, Neo Soul → `neo soul`, Alternative → `alternative`. **Every other genre goes to `_GENRE NEEDS CHECK` and gets no genre tag written** until more folders are approved.
- **My PR #49** puts the owner's real decisions back over #41 (Funk & Soul kept merged, Blues allowed, the Grouping descriptors) and rewrites the decision record file.
- **Update 2026-10-15 afternoon:** #37, #41, #47 and #49 are all **merged into `main`** (771 tests pass on `main`). Also merged since: #50 (R&B folder spelling is now `R&B`; `R&b`/`r&b` normalize to it), #51 (Grouping and Year/BPM writers handle FLAC and M4A), #52/#53 (per-track CSV Grouping plan builder, `scripts/build_grouping_csv_plan.py`).

### Set prep for October gigs [reported, done in Grok/Cursor, not by me]
- Set lists built for Oct 15 (Open Decks), Oct 16 (Cat Calls, The Neon Veil), Oct 18 (Garden of Eatin, for Al), Oct 31 (Spring Wilder's 75th birthday, clean versions), and "Inna Space".
- Serato library exported read-only: 25,488 tracks, 56 crates (`serato-library.csv`).
- Each set checked against it (have / different version / need to get): Oct 15 36/4/15 · Oct 16 25/5/25 · Oct 18 103/23/48 · Oct 31 21/2/32 · Inna Space 13/3/39.
- **Half-done:** most matched tracks aren't in any Serato crate yet. No crate per set is built yet.
- Apple Music playlists were dropped. A partial "Oct sets - Oct 15 Open Decks" playlist (16 songs) exists.
- **Other PRs [reported/verified open]:**
  - #42 → #43 → #46 → #44: DJ software drift detection, set-time matching, exact-hash play matching, Mac library review.
  - #48: set-history export staging. Status WAITING_FOR_OFFICIAL_EXPORTS: 474 `.session` files are blocked, and 0 CSV/TXT files are staged.

---

## 3. The genre list and tagging rules

**Master list:** `references/genre-learner-rules.json` → `canonical_genres`, 76 genres [verified on PR #49's branch]. The two older files (`genre-rules.json`, `version-genre-rules.json`) must output only these names. CI enforces this through `scripts/check_genre_naming.py`.

| Family | Genres |
|---|---|
| Rap/drill | Hip-Hop, Chicago Drill, UK Drill, NY Drill, Sexy Drill, Bronx Drill, Jersey Drill |
| House | House, Deep House, Tech House, Bass House, Afro House, Afro Tech, Garage House |
| Regional club | Jersey Club, Baltimore Club, Philly Club, Ballroom Vogue, Bounce, Groovy Bounce |
| Bass | Miami Bass, Atlanta Bass, Bassline, Dubstep, Electro |
| UK/breaks | UK Garage, Speed Garage, Breaks, Jungle, Drum & Bass |
| Juke/ghetto | Juke, Footwork, Ghetto House, Ghettotech |
| Caribbean/Latin | Latin, Latin Pop, Cumbia, Dembow, Reggaeton, Guaracha, 3Ball, Dancehall, Riddim, Reggae, Dub, Soca, Bouyon, Kompa |
| Global club | Afrobeats, Amapiano, Gqom, Kuduro, Baile, Afro Baile |
| R&B/soul | R&B, Alternative R&B, Neo Soul, Funk & Soul, Blues, Disco, Gospel, Jazz |
| Techno/rave | Electronic, Techno, Hardgroove, Rave, Trance |
| Pop/rock | Pop, Dance Pop, Rock, Classic Rock, Soft Rock, Pop Rock, Alternative, Country, New Wave |

### Core rules [verified, `CLAUDE.md`]
- **Genre** = musical style only. **Grouping** = event/crate/source/purpose context, and is always **added to, never overwritten**.
- **Never guess.** Anything uncertain goes to `_NEEDS CHECK` (version) or `_GENRE NEEDS CHECK` (genre).
- Keep Clean, Dirty, Intro, Remix, Edit, Instrumental and Acapella versions separate.
- Keep meaningful numbers in names: `21 Savage`, `112`, years, BPM, part numbers.
- A duplicate means an identical SHA-256 hash of the whole file, nothing else. Matching names, titles or lengths only mean "review".
- Keep ID3v2.2 tags as they are, and keep Serato data and the audio itself unchanged.
- No genre slashes: the learner splits genre text on `/`.

### The owner's decisions (Genre Master Review, 2026-10-13) [verified; full record in `references/genre-master-user-review-2026-10-13.md` on PR #49]
| Tag seen | Becomes | Notes |
|---|---|---|
| Ghetto Tech | **Ghettotech** | |
| Baile Funk | **Baile** | |
| UKG | **UK Garage** | |
| speed garage / bassline (filename words) | **Speed Garage** / **Bassline** | |
| Afrobeats, Afro Beats | **Afrobeats** | Master renamed from Afrobeat. A bare **`Afrobeat`** tag is **held for review** (could be 1970s Fela-era). |
| afrotech | **Afro Tech** | Own genre; *not* Afro House. |
| Vogue, Ballroom | **Ballroom Vogue** | No slash. |
| Funk, Soul, Boogie | **Funk & Soul** | Kept merged. |
| Tech House | **Tech House** | Not plain House. |
| Latin Pop, Blues, Classic Rock, Soft Rock, Pop Rock | Each its own genre | Rock styles kept **separate** ("apart"). |
| Nu Disco | Disco | |
| Nu R&B | Alternative R&B | |
| R&B Throwbacks | Genre **R&B** + Grouping **Throwback** | |
| Alternative Hip Hop | Genre **Hip-Hop** + Grouping **Alternative** | |
| Jazzy House | Genre **Deep House** + Grouping **Jazzy** | "Jazzy" describes the instruments. |
| Dub | Dub is a genre for real Jamaican dub. "dub mix / dub version / dubplate" are **version labels**. | A bare `Dub` tag is **held for review**. |
| Twerk | **Grouping only** | Means "tracks to make people shake". It's the purpose, and Genre stays the real style. |
| Dance | Grouping only | Vague catch-all. |
| Club | Grouping only (club-ready). Regional club styles keep their own genres; "club mix/edit" is a version label. | A bare "club" means needs-check. |
| Garage, Garage Jungle, Bass, Drum | **Needs check**, never guessed | "Garage Jungle" means "couldn't tell". |
| Afro Baile, Groovy Bounce, Sexy Drill | Kept | Library-specific; not in the atlas. |
| Hardgroove | Kept as its own genre | |

**Known limits:**
- The version/genre sorter (`build_version_genre_plan.py`) can't write Grouping. It holds R&B Throwbacks, Alternative Hip Hop and Jazzy House for review instead of dropping the descriptor.
- After #47, only 3 genres had an approved folder; the 2026-10-15 folder approval raised that to 58 (section 5, step 3).

---

## 4. Files, scripts and pages I made [verified]

**Repo (branch `claude/artifact-session-koaj6r`, PR #37):**
- `references/genre-atlas/atlas-snapshot.json`: atlas copy (16 regions, 5,047 genres; source page `https://claude.ai/artifact/Q9M3vDxtAEwECRwAmEoGgf`, pulled 2026-10-13).
- `references/genre-atlas/PROVENANCE.json`: source, hashes, use policy, known defects.
- `references/genre-atlas/crosswalk-curated.json`: **the only atlas file to edit by hand**. Links each master genre to atlas genres, graded exact/close/uncertain/none.
- `references/genre-atlas/crosswalk.json`: generated; don't edit.
- `references/genre-atlas/README.md`: rules for any LLM. Never evidence for a track's tag; never training data; never link by name similarity (the atlas's "Riddim" is dubstep).
- `scripts/genre_atlas.py`: `extract` / `build` / `check`. Run `python3 scripts/genre_atlas.py check` after any master-list change.
- `tests/test_genre_atlas.py`, `tests/test_learner_taxonomy_20261013.py`, `tests/test_genre_folder_names.py`.
- `AGENTS.md`; one bullet added to `CLAUDE.md`.
- `folder_names` maps in `references/genre-rules.json` and `references/version-genre-rules.json`, plus the code using them in `scripts/build_plan.py`, `scripts/build_version_genre_plan.py` and `scripts/scan_library.py`.

**Repo (branch `claude/genre-decisions-fix-20261015`, PR #49):**
- Corrections in `references/genre-learner-rules.json`, `references/version-genre-rules.json`, `scripts/scan_library.py`, `tests/test_learner_taxonomy_20261013.py` and `tests/test_genre_pipeline_reconciliation.py`.
- `references/genre-master-user-review-2026-10-13.md` rewritten from the owner's real answers.

**Private claude.ai pages (owner-only):**
- Genre Master Review sheet: `https://claude.ai/artifact/6LCrw6CSkDmeytr6YK4QMX`. Answers did **not** save in the owner's app; decisions were taken from chat instead.
- Library Genre Atlas (the owner's editable copy): `https://claude.ai/artifact/VidVBMQtQTHMcxgeRxahmj`. Edits save separately on the page; none have been copied into the repo. Whether saving works in the owner's app: [unsure], because the sheet's saving failed.

**Not made by me:** the Desktop files and `serato-library.csv` (Grok/Cursor) [reported].

---

## 5. What's left, in order

1. **Gig prep first (time-critical):** download the missing tracks (Oct 15 first, then Oct 16) per `~/Desktop/need-to-get-oct.txt`. Then build one Serato crate per set. [reported]
2. ~~Merge the genre PR stack~~ **Done** (all on `main` as of 2026-10-15).
3. **Unpause genre filing (done in the repo, 2026-10-15; merged once its PR lands):** from a read-only listing of the live top-level folders (`handoff/live-top-level-folders-2026-10-15.md`), the owner approved 58 Genre → folder entries: 35 into their own folder, 23 smaller styles into a parent folder (tag kept exact). 18 Genres stay held (Afrobeats pending the owner's answer on what the `Afrobeat` folder holds; Dub; Breaks; Electro; Dubstep; Miami/Atlanta Bass; Afro Tech; Gqom; Kuduro; Afro Baile; New Wave; Drill styles). The live listing shows `Ghettotech` and `Drum & Bass` folders already exist, so those two renames are done; `Amapiano` has no trailing space now. [verified listing; owner-approved in chat]
4. **Merge the DJ-integration chain** #42 → #43 → #46 → #44. Same stacked-merge caution.
5. **Set-history exports (#48):** the owner exports Serato History (CSV + TXT) into the history-exports inbox folder, then runs `scripts/dj_history_export_stage.py stage`. [reported]
6. **Mac test run** (`MAC-CANARY.md`) before any real move or rename. CI runs on Linux; the library is on macOS/APFS.
7. **T5 backup index** (Mac-local), per `handoff/CURRENT-STATUS.md`. The owner reports the T5 is plugged in (2026-10-15); whether the index was built is not recorded in the repo [unsure].
8. **Owner's open set decisions [reported]:**
   - Cat Calls: MikeQ remixes of Pure/Honey and Alien Superstar instead of the album versions?
   - Oct 18: use the owned Bidi Bidi Bom Bom / Como La Flor?
   - Oct 18: keep the 134 BPM Jodeci tech house edit in the 90s block? (I advised moving it out.)
   - Confirm Al's time and pay for Oct 18.
9. **Later:**
   - folder renames, e.g. `Afrobeat/` → `Afrobeats/` (needs a Serato path rewrite, a verified backup and the Mac test run);
   - a feature letting the sorter write Grouping;
   - two routing calls in `genre-rules.json`: "reggaeton"/"dembow" currently go to Latin, and "breakbeat" to Electro;
   - BPM ranges for the 51 genres that lack one;
   - the 9 VDBL review tracks;
   - the library-wide metadata pass.

### Do not touch
- **`Imani Drops`**: explicitly out of scope, leave completely untouched. Also excluded from training: `bae re work`, `Local Artist`.
- rekordbox **`master.db`**: never read or write it.
- **`handoff/`**: historical record, never executable; CI fails if a `.py` file appears there.
- **Never delete** anything automatically. `delete_verified_duplicates.py` acts only on a human-approved pair list.
- Any change to music files goes only through `scripts/mutation_engine.py`, with a folder lock, a backup manifest that says PASS, and the owner's explicit approval after a preview.
- Never copy folders named `backups` or `*tag-file-backups`; they contain real audio.
- Other sessions (Codex, Cursor, other Claude sessions) work this library at the same time. Check locks (`python3 scripts/folder_lock.py status`) before touching any folder.
- Owner preference: **don't assume, ask.** Answer directly, mark uncertainty, get approval before any change.
