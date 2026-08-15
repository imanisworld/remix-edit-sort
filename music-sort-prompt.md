# Prompt: sort a messy remix/edit music folder

## Option A — if you have the `remix-edit-sort` skill folder

Drop the folder into `~/.claude/skills/`, then paste this:

> Use the remix-edit-sort skill on my music folder at `<PATH>`. Survey it first and show me the preview before moving anything.

That's it. The skill handles the rest and walks you through each approval.

---

## Option B — standalone prompt (no skill installed)

Copy everything below into Claude Code, replacing `<PATH>` with your music folder.

---

I have a folder of music downloads at `<PATH>` — a mix of remixes, edits, flips, bootlegs, and original tracks, mostly from SoundCloud and DJ pools. The filenames and tags are inconsistent. I want it organized and fully tagged so it's browsable in Finder and sortable in Rekordbox/Serato/Apple Music.

**Please work in this order, and do not move or rename anything until I've approved a preview.**

### 1. Survey before you assume anything

Start by looking at what's actually there — file count, the real filename patterns, what ID3 tags already exist, how many are remixes vs originals, how many unique remixers. Show me what you find. Build the plan around my actual files, not a guess about what a music folder looks like.

Also check what audio tooling I have before you design anything: `python3 -c "import mutagen, numpy"`, `which ffmpeg afconvert aubio`. **Don't assume Homebrew is installed.** If ffmpeg/aubio are missing, use `mutagen` for tags and macOS's built-in `/usr/bin/afconvert` to decode for analysis — that path needs zero installs and works today. Tell me what you found before proceeding.

### 2. Target structure

```
Remixes & Edits/<Genre>/<Remixer>/Title (Remixer Remix) - Original Artist.mp3
Originals/<Genre>/<Artist>/Title - Artist.mp3
_Unsorted/          (duplicates, recorded sets, unparseable files)
```

Remixes lead with genre then **remixer**, because when I'm building a set I think "what have I got from this producer" — not "what album was this on." The original artist stays in the filename and tags so search still finds it.

### 3. Parsing the remix credit

Find the remixer from, in order: a parenthetical `(X Remix)`, a trailing ` - X Mix`, then a bare `X Remix` suffix. **Word-cap the last two patterns** (4 and 3 words) or they swallow song titles — `Artist - Where They Hang Boss Town Playaz Remix` should credit *Boss Town Playaz*, not the whole phrase. With nested parens, the **last** one wins — it's the outermost edit.

**The genre-suffix trick, which does most of the work:** SoundCloud producers stamp the genre onto their own name — `ZUES Cumbia`, `MQ VOGUE`, `nextdimensional jersey club`, `maxcy House`. Stripping that suffix gives you the real remixer *and* a high-confidence genre in one move, and it merges folders that would otherwise split (`ZUES Cumbia` and `ZUES Baile Funk` are one person).

Also handle: some uploaders **swap artist and title outright**, and a file over ~20 minutes is a recorded set, not a track — route those to `_Unsorted`.

### 4. Genre

Decide by the first signal that answers: remixer's genre suffix → a known-artist map you build up as we go → existing folder → keyword in title/artist → **unclassified**.

The remixer's genre beats the original artist's: a house edit of a rap record is House. And `_Unclassified` is a real answer — an honest bucket I can sweep beats a confident wrong genre baked into 200 files. Show me that list and I'll help you map it.

Watch for **genre words inside artist names** — an artist called "James Juke" is not footwork, and a keyword rule will happily file him there.

### 5. BPM

Detect it for everything. If aubio isn't available: decode with `afconvert -f WAVE -d LEI16@22050 -c 1`, build a spectral-flux onset envelope with numpy, autocorrelate it, and reinforce at 2x/3x/4x the lag. Analyze ~2 minutes from the middle, skipping the first 30 seconds so long intros don't poison it. Roughly 0.25s/track with a few workers.

**The octave problem is the whole game.** A 160 BPM footwork track autocorrelates just as strongly at 80 — the raw peak can't tell you which is right. Give each genre an expected BPM range and pick the candidate (or its half/double) that lands in it. This is why genre has to be decided first.

Decide with me whether trap/ghettotech should read **full-time** (140-150, beatmatches against club music without mental arithmetic) or **half-time** (70-75, how SoundCloud tags it). Make it one setting I can flip.

If a file already has a BPM tag and your detection disagrees by exactly an octave, prefer the one in the genre's range and flag it. If they disagree for any other reason, **keep the existing tag** and flag it.

### 6. Tags

Write these, since Rekordbox, Serato, Apple Music and Plex all read them (skip custom TXXX frames — Serato ignores them). Save as ID3 **v2.3**.

| Frame | Remix value |
|---|---|
| `TIT2` title | `Title (Remixer Remix)` |
| `TPE1` artist | the **original** artist (Beatport convention) |
| `TPE2` / `TALB` | remixer — groups albums by remixer |
| `TPE4` remixer | remixer — this is Rekordbox's Remixer column |
| `TCOM` composer | remixer — Serato reads this one |
| `TCON` / `TBPM` | genre / integer BPM |
| `COMM` | **preserve** the original SoundCloud URL, add hashtags in a second frame |

### 7. Safety — non-negotiable

- **Dry-run by default.** Require an explicit `--apply` flag.
- **Write a markdown preview** and get my approval before anything moves.
- **Undo log for every run**, and *append* to it — don't overwrite, or a two-pass run loses the first pass's history.
- **Never delete audio.** Route duplicates (same target name + same duration) to `_Unsorted/Duplicates` for me to review.
- Verify the file count at the end matches the start.

### 8. Pitfalls that will bite you (these all actually happened)

- **macOS APFS is case-insensitive.** `Bt005.mp3` and `BT005.mp3` are the *same path*, so `os.path.exists(dest)` returns True for a case-only rename and a naive collision check appends ` (2)` to a file that was never colliding. Compare with `os.path.normcase`, then rename via a temp name.
- **Re-running the full pipeline over already-sorted files degrades them.** The parser re-reads filenames it already normalized, and `Under The Boardwalk - The Drifters, Phil Hooton Rework` gets re-read as a remix credit and rebuilt worse. Re-sorting already-filed tracks needs a *separate* mode that moves folders and updates only the genre tag, leaving filenames alone.
- **Make every cleanup rule idempotent.** Run it twice; the second pass must report zero changes. A rule that turns `P.I.M.P.` into `P.I.M.P..` is fighting itself.
- **Never reduce a title to nothing or to a bare `(X Edit)`.** When the title *is* the promo text (`Free DL (Loucas Groove Edit)`), keep the original and flag it for a human.
- **Styled punctuation is not an error.** `Anderson .Paak`, `DJ co.kr`, `FKA twigs`, `maxcy`, `Chlär` are deliberate. Only normalize ALL CAPS, never touch something already mixed-case, and keep a list of exceptions.

### 9. Finally

Once it's sorted, offer a separate pass that standardizes filenames and titles — stripping promo spam (`FREE DL`, `OUT NOW`, `PREMIERE`, `@handles`), fixing punctuation artifacts, restoring damaged catalog numbers (`Trsn021` → `TRSN021`), and updating the tags to match. Same rules: dry-run, preview, undo, idempotent.

Keep everything you learn — artist→genre mappings, remixer aliases, casing exceptions — in editable JSON files, so the next run starts smarter than this one.
