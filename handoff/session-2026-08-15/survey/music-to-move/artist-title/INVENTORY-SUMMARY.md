# `music-to-move (artist-title fresh)` folder — read-only inventory

Scope: `/Users/djb.a.e/Music/DJ-Music/Music/music to move`

> No music file, tag, filename, path, or Serato data was changed.

## Result

- Files inventoried: **977**
- Snapshot remained stable during hashing: **yes**
- Unique SHA-256 hashes within this folder: **977**
- Exact-duplicate groups within this folder: **0**
- Redundant instances within those groups: **0**
- Terminal `(N)` filename hints: **4**
- Formats: mp3: 718, flac: 233, m4a: 24, ogg: 1, wav: 1
- Sample rates: 44100 Hz: 942, 48000 Hz: 25, 22050 Hz: 6, 96000 Hz: 3, unknown Hz: 1
- Bitrate range: **59–2117 kbps**

## Missing metadata

- Missing artist: **13**
- Missing title: **3**
- Missing album: **449**
- Missing genre: **166**
- Missing year: **58**
- Missing bpm: **61**
- Missing key: **40**

## Terminal `(N)` hints

- `01 - Spend Dat - Yung Miami (1).mp3` — suffix `1`; SHA-256 recorded, not classified as a duplicate by name
- `Kentheman, Kaliii - I Got Questions (2025).flac` — suffix `2025`; SHA-256 recorded, not classified as a duplicate by name
- `hip hop classics - kriss kross ~ jump (1992).mp3` — suffix `1992`; SHA-256 recorded, not classified as a duplicate by name
- `Angela Davis on Freedom of Speech (1974).mp3` — suffix `1974`; SHA-256 recorded, not classified as a duplicate by name

## Number handling rule

Preserve numbers inside artists, titles, years, BPM labels, and version names. Only a final `(N)` is a copy-suffix hint, and it still requires hash evidence before exact-duplicate classification.

## Outputs

- `inventory.csv` — one row per audio file
- `inventory.json` — the same data plus audit metadata
