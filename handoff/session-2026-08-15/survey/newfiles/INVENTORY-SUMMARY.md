# `NewFiles` folder — read-only inventory

Scope: `/Users/djb.a.e/Music/DJ-Music/Music/NewFiles`

> No music file, tag, filename, path, or Serato data was changed.

## Result

- Files inventoried: **829**
- Snapshot remained stable during hashing: **yes**
- Unique SHA-256 hashes within this folder: **829**
- Exact-duplicate groups within this folder: **0**
- Redundant instances within those groups: **0**
- Terminal `(N)` filename hints: **3**
- Formats: mp3: 585, flac: 220, m4a: 23, ogg: 1
- Sample rates: 44100 Hz: 807, 48000 Hz: 18, 96000 Hz: 3, unknown Hz: 1
- Bitrate range: **74–1861 kbps**

## Missing metadata

- Missing artist: **8**
- Missing title: **1**
- Missing album: **380**
- Missing genre: **139**
- Missing year: **36**
- Missing bpm: **13**
- Missing key: **25**

## Terminal `(N)` hints

- `Kentheman, Kaliii - I Got Questions (2025).flac` — suffix `2025`; SHA-256 recorded, not classified as a duplicate by name
- `Angela Davis on Freedom of Speech (1974).mp3` — suffix `1974`; SHA-256 recorded, not classified as a duplicate by name
- `hip hop classics - kriss kross ~ jump (1992).mp3` — suffix `1992`; SHA-256 recorded, not classified as a duplicate by name

## Number handling rule

Preserve numbers inside artists, titles, years, BPM labels, and version names. Only a final `(N)` is a copy-suffix hint, and it still requires hash evidence before exact-duplicate classification.

## Outputs

- `inventory.csv` — one row per audio file
- `inventory.json` — the same data plus audit metadata
