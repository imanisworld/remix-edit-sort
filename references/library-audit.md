# Whole-library audit contract

This audit is deliberately separate from sorting and tagging. Its job is to establish what exists before any cleanup decision is made.

## Safety boundary

`python3 scripts/audit_library.py ...` is read-only with respect to source audio.

It may:

- open audio files for reading;
- read filesystem metadata;
- read tags and codec information through `mutagen`;
- compute SHA-256 hashes;
- write CSV/JSON reports under the selected output directory.

It must never:

- move or rename audio;
- delete files;
- write or normalize tags;
- call `audio.save()` or equivalent mutating APIs;
- infer that two files are disposable because their filenames or metadata look similar;
- place generated inventories or absolute local paths into Git.

The repository `.gitignore` excludes `/output/`, inventories, Serato data, media, backups, and local run artifacts.

## Run

```bash
python3 scripts/audit_library.py "/path/to/DJ-Music/Music"
```

Multiple roots are allowed:

```bash
python3 scripts/audit_library.py "/Volumes/DJ SSD/Music" "/Users/me/Music/DJ-Music/Music"
```

Default reports are written to:

```text
output/library-audit/
```

Use `--no-hash` only for a faster metadata-only survey. Without SHA-256 hashing, exact duplicate counts are intentionally unavailable.

## Inventory fields

The inventory records:

- source root;
- relative and absolute path;
- filename and extension;
- file size and modification time;
- SHA-256;
- artist, title, album, genre, year;
- BPM, key, Grouping;
- duration;
- bitrate;
- sample rate;
- channels;
- bits per sample when available;
- normalized artist/title identity used only for review grouping;
- exact duplicate group;
- quality-review flags;
- metadata-review flags;
- read errors.

## Reports

### `summary.json`

Counts files, storage, formats, read failures, missing core metadata, quality-review candidates, exact duplicate groups, redundant exact copies, and metadata-only review groups.

### `inventory.csv`

One row per physical audio file.

### `exact_duplicates.csv`

Contains only files with the same SHA-256 digest. This proves the files are byte-identical. It still does **not** authorize deletion: copies in event crates or historical folders may be intentional.

### `metadata_review_candidates.csv`

Groups different files that share the same normalized Artist + Title. These are **review candidates, not duplicate verdicts**. Clean/Dirty versions, edits, remasters, alternate encodes, intros, and other useful DJ variants can legitimately land here.

### `errors.csv`

Files that could not be fully read or identified. The audit continues instead of aborting the entire run.

## Quality flags are triage, not deletion criteria

The default low-bitrate flag is `<192 kbps` for lossy formats. A low bitrate is a reason to inspect a file, not proof that it should be removed. Likewise, sample rates below 44.1 kHz and very short durations are review signals only.

## Future joins

The inventory is designed to be joined later with two evidence sources:

1. Serato history — first use, last use, number of session dates, recent activity, known gig usage.
2. Event history — event name, venue, city, date, and recovered crate/session mapping.

Those joins should add evidence about a track's value without changing the audio library. Cleanup decisions should be made only after the physical inventory, Serato history, and event history are reconciled.
