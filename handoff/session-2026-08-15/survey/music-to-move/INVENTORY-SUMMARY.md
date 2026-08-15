# `music to move` folder — read-only inventory

Scope: `/Users/djb.a.e/Music/DJ-Music/Music/music to move`

> No music file, tag, filename, path, or Serato data was changed.

## Result

- Files inventoried: **152**
- Snapshot remained stable during hashing: **yes**
- Unique SHA-256 hashes within this folder: **152**
- Exact-duplicate groups within this folder: **0**
- Redundant instances within those groups: **0**
- Terminal `(N)` filename hints: **2**
- Formats: mp3: 135, flac: 15, wav: 1, m4a: 1
- Sample rates: 44100 Hz: 137, 48000 Hz: 9, 22050 Hz: 6
- Bitrate range: **59–2117 kbps**

## Missing metadata

- Missing artist: **5**
- Missing title: **2**
- Missing album: **69**
- Missing genre: **27**
- Missing year: **22**
- Missing bpm: **48**
- Missing key: **15**

## Terminal `(N)` hints

- `01 - Spend Dat - Yung Miami (1).mp3` — suffix `1`; SHA-256 recorded, not classified as a duplicate by name
- `01 - Spend Dat - Yung Miami (1).mp3` — suffix `1`; SHA-256 recorded, not classified as a duplicate by name

## Number handling rule

Preserve numbers inside artists, titles, years, BPM labels, and version names. Only a final `(N)` is a copy-suffix hint, and it still requires hash evidence before exact-duplicate classification.

## Outputs

- `inventory.csv` — one row per audio file
- `inventory.json` — the same data plus audit metadata
- `cross-library-exact-copies.json` — earlier targeted comparison against the rest of the library
