# `Jersey Club` folder — read-only inventory

Scope: `/Users/djb.a.e/Music/DJ-Music/Music/Jersey Club`

> No music file, tag, filename, path, or Serato data was changed.

## Result

- Files inventoried: **420**
- Snapshot remained stable during hashing: **yes**
- Unique SHA-256 hashes within this folder: **420**
- Exact-duplicate groups within this folder: **0**
- Redundant instances within those groups: **0**
- Terminal `(N)` filename hints: **1**
- Formats: mp3: 411, flac: 8, m4a: 1
- Sample rates: 44100 Hz: 412, 48000 Hz: 8
- Bitrate range: **128–1667 kbps**

## Missing metadata

- Missing artist: **10**
- Missing title: **0**
- Missing album: **136**
- Missing genre: **9**
- Missing year: **117**
- Missing bpm: **0**
- Missing key: **1**

## Terminal `(N)` hints

- `Rain (2018).mp3` — suffix `2018`; SHA-256 recorded, not classified as a duplicate by name

## Number handling rule

Preserve numbers inside artists, titles, years, BPM labels, and version names. Only a final `(N)` is a copy-suffix hint, and it still requires hash evidence before exact-duplicate classification.

## Outputs

- `inventory.csv` — one row per audio file
- `inventory.json` — the same data plus audit metadata
