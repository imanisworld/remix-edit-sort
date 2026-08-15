# Events version + genre preview

**Preview only — no event music has been moved or retagged.**

Target: `/Users/djb.a.e/Music/DJ-Music/Music/Events`

## Preservation rule

Every top-level event and every existing project/crate folder remains intact. Version and genre folders are inserted inside that existing context; the library is not flattened.

Examples:

- `Girls Inc/Cardi B - ... (Clean).mp3` → `Girls Inc/CLEAN/Hip-Hop/Cardi B - ... (Clean).mp3`
- `trendindg august 2025/DIRTY/track.mp3` → `trendindg august 2025/DIRTY/Hip-Hop/track.mp3`
- `VDBL/<album>/CLEAN/track.mp3` → `VDBL/<album>/CLEAN/Hip-Hop/track.mp3`
- `LIKE SUGAR ON MY TONGUE/R&B Brunch/track.mp3` → `LIKE SUGAR ON MY TONGUE/R&B Brunch/_NEEDS CHECK/R&B/track.mp3` when clean/dirty status is not explicit

Resolved Genre folders are mirrored exactly in Genre metadata. Unresolved genre stays in `_GENRE NEEDS CHECK`, and its current tag is retained instead of being guessed.

## Counts

- Tracks checked: **3,046**
- Proposed folder moves: **3,046**
- Proposed Genre metadata updates: **2,206**
- Genre-needs-review tracks: **486**
- Exact-hash groups: **56** (reported only; no duplicate removal)
- Missing sources: **0**
- Destination collisions: **0**
- Existing destination conflicts: **0**

## Version/category totals

| Category | Tracks |
|---|---:|
| _NEEDS CHECK | 1,804 |
| CLEAN | 979 |
| DIRTY | 203 |
| REMIXES & EDITS | 41 |
| DJ TOOLS | 19 |

## Genre totals

| Genre folder / metadata | Tracks |
|---|---:|
| Hip-Hop | 802 |
| R&B | 525 |
| _GENRE NEEDS CHECK | 486 |
| Rock | 249 |
| Pop | 194 |
| Pop & R&B | 168 |
| Pop Rock | 123 |
| Funk & Soul | 103 |
| R&B & Soul | 81 |
| Hip-Hop & R&B | 54 |
| Pop Rap | 33 |
| Holiday | 32 |
| House | 29 |
| Dance & Electronic | 27 |
| Funk Rock | 25 |
| Reggae & Caribbean | 20 |
| Latin | 17 |
| Afrobeats | 15 |
| Jazz | 13 |
| Country | 13 |
| Techno | 11 |
| Afrobeats & Amapiano | 10 |
| Halloween | 7 |
| Yacht Rock | 6 |
| Dance Pop | 2 |
| Baile Funk | 1 |

## Safeguards before apply

- A fresh verified backup of the entire 27 GB Events folder and `_Serato_` is required.
- All 3,046 source hashes must still match this preview.
- Each metadata write must preserve audio payloads and Serato frames.
- A full undo log and exact pre-tag file backups will be written.
- Nothing will be deleted, including exact copies used in separate event crates.

## Review files

- `EVENTS-NEEDS-REVIEW.csv` contains every unresolved genre item.
- `events-version-genre-plan.json` contains all 3,046 source → destination mappings and metadata proposals.
