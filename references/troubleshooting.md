# Troubleshooting

## Classification

**A remixer shows up twice under different spellings.**
Add the mapping to `remixer_aliases` in `genre-rules.json` (key lowercased, value = folder name you want) and re-run `build_plan.py`. Fix it before the move; after is annoying.

**A remixer's name got its last word eaten.**
The genre-suffix stripper removed a word that happened to be a genre — a producer literally called "Vogue" or "Bass" loses it. Add the full name to `remixer_aliases`; exact alias matches run before suffix stripping.

**Everything from one producer landed in `_Unknown Remixer`.**
Their name is in `generic_remixer_names` in `genre-rules.json` — it's being read as a genre label, not a person. Remove it from that list and add it to `remixer_aliases`.

**A big pile lands in `_Unclassified`.**
Expected for originals by artists nobody has mapped. Create `references/artist-genre-extra.json` as a flat `{"Artist Name": "Genre"}` object using genres from `canonical_genres`, then re-run `scan_library.py`. This compounds — each pass makes the next one better.

**Genre is right for the source track but wrong for the edit.**
Working as designed: the edit's genre wins over the original's. A house edit of a rap record is House. If a specific file is genuinely wrong, the fastest fix is editing `plan.json` directly before applying.

## BPM

**Everything in a genre reads exactly double or half.**
The genre's range in `bpm_ranges` disagrees with how Brooke counts it. Change the range, then `detect_bpm.py --redetect`. Hip-Hop and Ghetto Tech are set full-time deliberately — see the note in `genre-rules.json`.

**`afconvert` fails on a file.**
Usually a truncated or DRM'd download. The track gets `bpm: null` and is flagged; it still sorts and tags normally. Check it plays at all before spending time on it.

**Detection is slow.**
Raise `--jobs` (decode is I/O-bound, so 5–8 is fine on this machine). Results cache, so an interrupted run resumes for free.

**A BPM looks wrong but confidence is high.**
Long intros, tempo changes mid-track, and heavily swung rhythms all fool autocorrelation. Sort the preview by `bpm_confidence` and spot-check the bottom.

## Files and moves

**"Operation not permitted" on a folder.**
macOS permissions on `~/Downloads`. Grant the terminal Full Disk Access, or work on a copy elsewhere.

**Two different tracks want the same filename.**
Handled: same name + same duration = duplicate, routed to `_Unsorted/Duplicates`. Different durations get a numeric suffix. If real duplicates slip through, they differ in length by more than 2 seconds — different rips of the same track.

**Undo didn't restore my tags.**
It can't. `undo.json` records file moves only; the original tags weren't backed up. Use `--copy` on the first run if that matters.

**A re-run tried to re-sort already-sorted files.**
It shouldn't — `Remixes & Edits/`, `Originals/`, and `_Unsorted/` are skipped during the scan. If it happened, the destination folders were renamed; either rename them back or pass a fresh `--dest`.

## Case and characters

**An artist got title-cased when the styling was intentional.**
Add them to `artist_overrides` in `music-library-sort`'s `casing-overrides.json` (this skill reads it), or to `remixer_aliases` here. Names already mixed-case are never touched — only ALL CAPS gets normalized.

**Accented names look wrong in Finder.**
Filenames are written NFC-normalized. macOS displays NFD in some views; the bytes are correct and Rekordbox reads them fine.
