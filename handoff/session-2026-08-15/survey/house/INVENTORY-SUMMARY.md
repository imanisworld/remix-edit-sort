# `House` folder — read-only inventory

Scope: `/Users/djb.a.e/Music/DJ-Music/Music/House`

> No music file, tag, filename, path, or Serato data was changed.

## Result

- Files inventoried: **1,410**
- Snapshot remained stable during hashing: **yes**
- Unique SHA-256 hashes within this folder: **1,409**
- Exact-duplicate groups within this folder: **1**
- Redundant instances within those groups: **1**
- Terminal `(N)` filename hints: **1**
- Formats: mp3: 1,379, wav: 14, flac: 12, m4a: 5
- Sample rates: 44100 Hz: 1,399, 48000 Hz: 11
- Bitrate range: **128–1756 kbps**

## Missing metadata

- Missing artist: **79**
- Missing title: **14**
- Missing album: **453**
- Missing genre: **200**
- Missing year: **420**
- Missing bpm: **14**
- Missing key: **19**

## Terminal `(N)` hints

- `André Hommen - Battery Park - Objektivity (2015).mp3` — suffix `2015`; SHA-256 recorded, not classified as a duplicate by name

## Number handling rule

Preserve numbers inside artists, titles, years, BPM labels, and version names. Only a final `(N)` is a copy-suffix hint, and it still requires hash evidence before exact-duplicate classification.

## Outputs

- `inventory.csv` — one row per audio file
- `inventory.json` — the same data plus audit metadata
