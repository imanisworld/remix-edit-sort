---
name: remix-edit-sort
description: |
  Sort a folder of remixes, edits, flips, and bootlegs into a Genre / Remixer tree and write full ID3 metadata — genre, BPM, remixer attribution, and original-artist credit — so the library is browsable in Finder and sortable in Rekordbox, Serato, Apple Music, and Plex. Use this skill whenever Brooke asks to "sort my remix folder", "organize my edits", "sort by remixer", "who remixed this", "organize by genre and remixer", "clean up my edits folder", "sort my flips", "tag my remixes", "add BPM to these", "set the genre on these tracks", or any request about organizing remix/edit/bootleg files specifically. Also triggers on "which edits do I have by [producer]", "group my edits by who made them", "my remix folder is a mess", "add all the metadata", "detect the BPM", or any time Brooke wants remixes separated from originals. Works with zero installs — no ffmpeg, no aubio, no Homebrew. Don't undertrigger: if the request involves remixes or edits and organizing or tagging them, this is the right tool.
---

# Remix & Edit Sort

Takes a flat pile of SoundCloud/DJ-pool downloads and produces:

```
Remixes & Edits/
  Vogue/
    MQ/
      Kitty Kat (MQ Remix) - Beyoncé.mp3
      Ego Death (MQ Remix) - FKA twigs.mp3
  Jersey Club/
    nextdimensional/
      Nichel (nextdimensional Edit) - Alahsin.mp3
Originals/
  Techno/
    Head Noise/
      Shuffle Man - Head Noise.mp3
_Unsorted/
  Duplicates/ · Mixes & Radio Sets/
```

…with every file tagged: genre, BPM, remixer, original artist, and filter hashtags.

## Why this exists alongside the other two music skills

Brooke has three music skills that do genuinely different jobs. Don't duplicate their work:

| Skill | Organizes by | Use when |
|---|---|---|
| `music-library-sort` | Artist / Album / Track | A general library of originals and albums |
| **`remix-edit-sort`** (this) | **Genre / Remixer** | **Remixes and edits, where the remixer matters more than the album** |
| `dj-library-tagger` | (nothing — tags only) | Adding sub-genre and musical key to an already-sorted library |

The reason remixes need their own tree: an album-based structure files a Beyoncé edit under Beyoncé, which is useless when you're building a set and thinking *"what have I got from MQ?"*. Remixes are the remixer's work. So the disk layout leads with genre (what fits the moment) then remixer (whose sound it is), and the original artist lives in the filename and tags where search will still find it.

**Critical difference in requirements:** the other two skills require `ffmpeg`, `aubio`, and `keyfinder-cli` via Homebrew. **None of those are installed on this Mac and there is no Homebrew.** This skill deliberately uses only what's already here: Python 3, `mutagen`, `numpy`, and macOS's built-in `afconvert`. It runs today with no setup.

## Required tools

Verify before starting — all four should already pass:

```bash
python3 -c "import mutagen, numpy; print('ok')" && which afconvert
```

- **mutagen** — reads and writes ID3. Already installed (1.47.0).
- **numpy** — the BPM math. Ships with the system python3.
- **afconvert** — macOS CoreAudio decoder at `/usr/bin/afconvert`. Built in, no install.

If mutagen is somehow missing: `python3 -m pip install --user mutagen`. Never tell the user to install Homebrew for this skill — the whole point is that it doesn't need it.

## The workflow

Nothing touches disk until step 5, and step 5 refuses to act without `--apply`. Run from a working directory where an `outputs/` folder can live (the scratchpad is ideal — don't scatter JSON into the music folder).

1. **[Scan](#1-scan)** — read tags, parse remix credits, classify genre
2. **[Detect BPM](#2-detect-bpm)** — tempo for every track
3. **[Build the plan](#3-build-the-plan)** — destinations + tags + a preview
4. **[Review with Brooke](#4-review-with-brooke)** — get approval on the preview
5. **[Apply](#5-apply)** — move and tag, with an undo log

---

### 1. Scan

```bash
python3 scripts/scan_library.py "~/Downloads/SoundCloud" --out outputs/tracks.json
```

Walks the folder, reads ID3 via mutagen, and for each track works out whether it's a remix, who made it, whose record it was, and what genre it is. Writes one record per track.

**How the remix credit is found**, in order — the first pattern that hits wins:

| Pattern | Example | Yields |
|---|---|---|
| Parenthetical | `Kitty Kat (MQ VOGUE Remix)` | MQ · Remix |
| Trailing dash | `Ann Nesby - Can I Get A Witness - Mousse T's Garage Mix` | Mousse T · Mix |
| Bare suffix | `Where They Hang Boss Town Playaz Remix` | Boss Town Playaz · Remix |

The dash and bare patterns are word-capped (four and three). Without the cap they eat song titles — `Amadeezy - Where They Hang Boss Town Playaz Remix` would credit the entire phrase as the remixer instead of just Boss Town Playaz. When a title has nested parens (`Love Hangover (Frankie Knuckles Remix)(K&F's Midnight Re- Edit)`) the **last** one wins, because that's the outermost edit — the one that actually produced this file.

**The genre-suffix trick** is what makes classification work. SoundCloud producers stamp the genre onto their own artist name — `ZUES Cumbia`, `MQ VOGUE`, `nextdimensional jersey club`, `maxcy House`, `TRAADSARU Soulful House`. Stripping that suffix gives you two things at once: the real remixer name *and* a high-confidence genre. It also merges folders that would otherwise split — `ZUES Cumbia` and `ZUES Baile Funk` are one producer, and this collapses them from two folders into one.

**Genre is decided** by the first of these that answers:

1. **Remixer suffix** — strongest. An edit of a Baby Keem record by `maxcy House` is House, not rap: the edit's genre is the edit's genre, not the source material's.
2. **Artist map** — reuses the ~125-artist map `music-library-sort` already curated (found automatically; the skill works without it)
3. **Existing folder** — if the file already sits in a genre folder Brooke made, trust it
4. **Keyword** — patterns in title/artist/album/uploader
5. **Unclassified** — reported explicitly, never guessed

Two data problems it corrects on the way through:
- **Reversed fields.** Some uploaders put the song in the artist tag: `i wanna be down, lake chon - brandy (lake chon Remix)`. If the parsed title is a name in the artist map and the parsed artist isn't, they're swapped back.
- **Long mixes.** Anything over 20 minutes is a radio show or recorded set, not a track. Flagged, and routed to `_Unsorted/Mixes & Radio Sets` rather than filed under a remixer.

### 2. Detect BPM

```bash
python3 scripts/detect_bpm.py --jobs 5
```

Roughly 0.25s per track with 5 workers — about 3 minutes for 700 tracks. Results cache to `outputs/bpm.json`, so re-runs are free and interruptions are cheap.

The method: `afconvert` decodes to mono 22.05kHz, then a spectral-flux onset envelope is autocorrelated, with harmonic reinforcement at 2x/3x/4x the lag to separate real periodicity from a one-off correlation bump. Two minutes from the middle of the track, skipping the first 30 seconds so long intros don't poison the estimate.

**The octave problem is the whole game.** A 160 BPM footwork track autocorrelates just as strongly at 80 — the raw peak can't tell you which is right. So the genre decides: each genre carries an expected range in `references/genre-rules.json`, and the candidate (or its half/double) landing in that range wins. This is why step 1 has to run first.

Validated against the 14 tracks that arrived with a real SoundCloud BPM tag: 13/14 correct within an octave, 8/14 exact before correction.

**A judgment call worth surfacing to Brooke:** Hip-Hop and Ghetto Tech are set **full-time** (140–150) rather than half-time (70–75, which is how SoundCloud tags them). Trap written as 140 beatmatches against Jersey and ATL club without mental arithmetic. If she'd rather see 70, change those two ranges in `genre-rules.json` and re-run with `--redetect`. When detection and an existing tag disagree by exactly an octave, the in-range value wins and the disagreement is recorded; when they disagree for any other reason, the **existing tag is kept** and the track is flagged.

### 3. Build the plan

```bash
python3 scripts/build_plan.py
```

Writes `outputs/plan.json` (machine) and `outputs/_preview.md` (human). Nothing has moved yet.

Casing is normalized here, reusing the stylized-name dictionary from `music-library-sort` — `BEYONCE` → `Beyoncé`, `FKA TWIGS` → `FKA twigs` — while anything already mixed-case is left alone, because `maxcy` and `nextdimensional` are lowercase on purpose.

Two files landing on one destination name **collide**. That is all it means, and it is never
enough to call them duplicates.

An older version of this step called same-name-plus-duration-within-2s a "duplicate" and filed
it under `_Unsorted/Duplicates`. In a DJ library that heuristic fires on exactly the pairs that
must never be conflated: `(Clean)` vs `(Dirty)`, `(Intro)` vs `(Intro Dirty)`, two different
edits of one track — same length, different masters, both wanted.

The repo-wide truth model lives in `scripts/duplicate_semantics.py`:

| Verdict | Evidence required | May authorize deletion? |
| --- | --- | --- |
| `EXACT_DUPLICATE` | identical SHA-256 over the whole file | yes, and still only with human approval |
| `AUDIO_EQUIVALENT` | strong audio-fingerprint match, bytes differ | no (nothing produces this yet) |
| `RELATED_VERSION` | different version markers — Clean/Dirty/Intro/Extended/Remix/Edit/… | no, they are different tracks |
| `REVIEW` | filename / title / duration similarity, in any combination | no |

So a collision between two *related versions* keeps both, disambiguated with a numeric suffix.
Anything else goes to `_Unsorted/Possible Collisions` flagged for review. This planner works
from a filename/duration scan and has no hashes available, so it can never produce
`EXACT_DUPLICATE` — and no longer pretends to.

### 4. Review with Brooke

**Always show the preview and get explicit approval before step 5.** Point her at the "Needs review" section specifically — it lists unclassified genres, BPM conflicts, unknown remixers, duplicates, and long mixes. The two things most worth her eyes:

- **Unclassified genres.** These become an `_Unclassified` folder. Ask if she wants to assign them; add the answers to `references/artist-genre-extra.json` (artist → genre) so the next run knows them.
- **One producer split across two folders.** If the preview shows the same person twice under different spellings, add the mapping to `remixer_aliases` in `genre-rules.json` and re-run step 3. Cheap to fix before the move, annoying after.

### 5. Apply

> **`apply_plan.py` is hard-disabled.** It forced ID3v2.3 on every file it tagged (destroying
> ID3v2.2 blocks), verified neither the audio payload nor the Serato frames, required no
> verified backup, wrote its undo log only after the whole run, never reverted tags on undo,
> and deleted directories as a side effect of a move. `--legacy-confirm` is gone; a
> confirmation flag could not make any of that safe. Only a read-only `--report` remains.

Every mutation now goes through the shared engine (`scripts/mutation_engine.py`) via the
approved apply family. Read `SAFETY-MODEL.md` for the full sequence; the short version:

```bash
# 1. Prove the backup. A directory existing is not a backup.
python3 scripts/backup_manifest.py verify \
    --source-root "<folder>" --backup "<backup dir>" \
    --serato-backup "<_Serato_ backup>" --require-serato \
    --out backup-manifest.json          # must print "status": "PASS"

# 2. Take the lock.
python3 scripts/folder_lock.py acquire --folder "<library folder>" --session "<session>" --agent claude

# 3. Dry run, then apply.
python3 scripts/apply_version_genre_plan.py --root "<folder>" --plan plan.json \
    --backup-manifest backup-manifest.json                       # reports, changes nothing
python3 scripts/apply_version_genre_plan.py --root "<folder>" --plan plan.json \
    --backup-manifest backup-manifest.json --apply \
    --lock-folder "<library folder>" --session "<session>"

# 4. Confirm the journal is clean, then release.
python3 scripts/mutation_engine.py recover --journal <recovery journal>
python3 scripts/folder_lock.py release --folder "<library folder>" --session "<session>"
```

Per item the engine re-verifies lock ownership, re-checks the source hash against the preview,
proves per-file backup evidence, fsyncs a write-ahead intent record, performs the mutation,
verifies the tag plus the audio payload plus the Serato frames, and only then commits a
completion record. Any verification failure restores the file immediately and stops the run.

Undo is a mutation too: `--undo <undo log>` needs the same `--lock-folder`/`--session`,
heartbeats before every restore, and journals write-ahead. Unlike the retired legacy engine,
it restores tags as well as locations, verified against the recorded pre-apply SHA-256.

### 6. Sweeping `_Unclassified` later

`_Unclassified` is where honest ignorance goes, and it's meant to be worked down over time. That's a **different operation** from the pipeline above — the files are already correctly named and tagged, they just landed in the wrong genre folder. Re-running the full pipeline over them would re-parse their filenames and make some of them worse.

Use `reclassify.py` to **propose** the moves. It is preview-only: it writes a plan and changes
nothing. (It used to move files and rewrite `TCON` itself, with no lock, no backup, no
verification, a forced ID3v2.3 downgrade, directory deletion as a side effect, and an undo
that explicitly did not restore the Genre it had overwritten. That mutation half is gone.)

```bash
# 1. Scan the unclassified folders directly and merge the results
python3 scripts/scan_library.py "<library>/Remixes & Edits/_Unclassified" --out outputs/t_rem.json
python3 scripts/scan_library.py "<library>/Originals/_Unclassified"      --out outputs/t_org.json
#    merge the two "tracks" arrays into outputs/tracks.json with root = the library root

# 2. Preview, and write a plan for review
python3 scripts/reclassify.py --tracks outputs/tracks.json --out outputs/reclassify-plan.json

# 3. Apply through the engine (move + Genre is apply_version_genre_plan.py's mutation class),
#    with a PASS backup manifest and an owned lock — see step 5 above.
```

The work happens in `references/artist-genre-extra.json` — every producer you map there is mapped permanently, so each sweep is smaller than the last. Map **remixers** as readily as original artists: `classify_genre` checks the remixer against the same map, because a known house producer's edit is house regardless of whose record it was.

### 7. Standardizing names across the library

The pipeline gives every file the same *shape*. `standardize_names.py` cleans the *contents* of that shape — promo spam, punctuation artifacts, damaged acronyms — and rewrites `TIT2`/`TPE1` so filenames and tags stay in agreement.

`standardize_names.py` is **preview-only**. It writes the preview and a plan; it renames and
retags nothing. (It used to do both with no lock, no backup, no verification, a forced ID3v2.3
downgrade, and an undo that restored filenames while leaving the rewritten Artist/Title
permanently in place.)

```bash
python3 scripts/standardize_names.py <library>                          # preview to outputs/_rename_preview.md
python3 scripts/standardize_names.py <library> --out outputs/names-plan.json
# then apply Artist/Title through the engine:
#   python3 scripts/apply_artist_title_plan.py --apply --backup-manifest ... --lock-folder ... --session ...
```

The preview marks **case-only renames** separately, because macOS/APFS is case-insensitive:
`bt005.mp3` and `BT005.mp3` are the same path there, so such a rename needs a two-step move
through a temp name and must not be mistaken for a collision. See `MAC-CANARY.md`.

What it fixes: `Free DL` / `OUT NOW` / `PREMIERE` / `@handle` / `#tag` spam · `Trsn021` → `TRSN021` and `P.i.m.p` → `P.I.M.P.` · `-Umi Says-` → `Umi Says` · `--` and `- -` · `Hustlin''` · leading `01`/`06.` track numbers · stray `]` and unbalanced brackets · a literal `.mp3` inside a name.

**Two guards that matter**, both learned the hard way:

- **Never reduce a title to nothing, or to a bare `(X Edit)`.** When the title *is* the promo text — `Free DL (Loucas Groove Edit)` — the clean result is worse than the original. The script keeps the original and leaves it for a human.
- **Styled punctuation is not an error.** `Anderson .Paak`, `DJ co.kr`, `Kiddy.Wav`, `Chlär` are deliberate. Space-before-dot is only collapsed when the dot is followed by a space or end-of-string.

Every rule is idempotent — run it twice and the second pass reports zero changes. That's the check to run after editing any rule: if a second pass still finds work, a rule is fighting itself.

## The tag schema

Chosen because Rekordbox, Serato, Apple Music, and Plex all read these frames. Custom `TXXX` frames work in Rekordbox but not Serato, so this sticks to the lingua franca. Saved as ID3 **v2.3**, which is what Rekordbox is happiest with.

| Frame | Holds | Remix example |
|---|---|---|
| `TIT2` title | Title with the remix credit | `Kitty Kat (MQ Remix)` |
| `TPE1` artist | **Original** artist — Beatport convention | `Beyoncé` |
| `TPE2` album artist | Remixer, so albums group by remixer | `MQ` |
| `TALB` album | Remixer | `MQ` |
| `TPE4` **remixer** | Remixer — Rekordbox's own Remixer column | `MQ` |
| `TCOM` composer | Remixer — Serato reads this one | `MQ` |
| `TCON` genre | Canonical genre | `Vogue` |
| `TBPM` | Integer BPM | `129` |
| `COMM` | Original SoundCloud URL, **preserved** | `https://soundcloud.com/…` |
| `COMM[djtags]` | Filter hashtags, added alongside | `#remix-by-mq #og-beyonce #genre-vogue #remix` |

Artist stays the *original* act on purpose: sorting by artist then answers "what have I got that touches Beyoncé", while the remixer is reachable four other ways. The hashtags exist so Rekordbox and Serato crates can filter on `#remix-by-mq` — that's layer 3 (curation), which Brooke builds inside each app.

### Event and source Grouping

Keep musical classification separate from working context:

- Write musical style to `TCON` / Genre: `Hip-Hop`, `R&B`, `Rave`, and so on.
- Write evidenced event, crate, or source context to `TIT1` / Grouping: `Event: IYG PROM` or `Source: Nigeria`.
- Preserve existing Grouping text. Append new values once with `; ` and deduplicate case-insensitively; never overwrite blindly. Only exception: exact junk values the owner listed by hand, removed with `scripts/build_grouping_removal_plan.py` (approved 2026-10-15).
- Use the exact source folder or an explicit user correction as evidence. Do not turn an ambiguous country, client, download batch, or acronym into a genre or event type.

Treat Grouping backfill as a separate tags-only operation. Reconstruct context from the approved plan or undo log, generate a per-file preview, and require approval. Make a fresh verified folder plus `_Serato_` backup before writing. For every changed MP3, verify the audio payload and embedded Serato frames before recording the undo entry. Preserve ID3v2.2 files unless conversion is separately approved, and skip non-MP3 formats until a format-specific writer and payload verifier are tested.

## Common pitfalls

- **Case-only renames need a temp step.** APFS is case-insensitive, so `Bt005.mp3` and `BT005.mp3` are the *same path*. `os.path.exists(dest)` returns True for a case-only rename, and a naive collision check appends ` (2)` to a file that was never colliding — this happened for real and had to be repaired across 18 files. Compare with `os.path.normcase` first, then rename via a temp name.
- **Don't install this into the app-managed skills folder** under `Library/Application Support/Claude/local-agent-mode-sessions/`. That directory re-syncs and silently deletes anything not published through the app. This skill lives in `~/.claude/skills/`, which persists.
- **Genre is per-file, not per-folder.** A producer who makes both cumbia and baile funk correctly appears under both genres. That's intended — don't "fix" it by forcing one folder per remixer.
- **`_Unclassified` is a real answer.** Better an honest bucket Brooke can sweep than a confident wrong genre baked into 200 files.
- **No key detection.** `keyfinder-cli` isn't installed and there's no zero-install substitute worth trusting. Existing `TKEY` values are preserved, never overwritten. If Brooke wants key, that's a separate conversation about installing tooling.
- **Re-running is safe for NEW files, not for sorted ones.** The scan skips `Remixes & Edits/`, `Originals/`, and `_Unsorted/`, so pointing it at the library root only picks up new downloads. But if you scan a sorted subfolder directly, the parser re-reads filenames it already normalized and can degrade them — `Under The Boardwalk - The Drifters, Phil Hooton Rework` gets re-read as a remix credit and rebuilt worse. **To re-sort already-filed tracks, use `reclassify.py`, never the full pipeline.**

## Reference files

- **`references/genre-rules.json`** — the whole knowledge base: canonical genres, keyword rules, genre suffixes, BPM ranges, remixer aliases. This is the file to edit when something classifies wrong.
- **`references/artist-genre-extra.json`** — local additions to the artist→genre map. Create it as a flat `{"Artist": "Genre"}` object; the skill picks it up automatically.
- **`references/troubleshooting.md`** — edge cases and what to do about them.
