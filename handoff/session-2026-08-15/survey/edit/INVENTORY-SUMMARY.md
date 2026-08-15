# `edit` folder — read-only inventory

Scope: `/Users/djb.a.e/Music/DJ-Music/Music/edit`

> No music file, tag, filename, path, or Serato data was changed.

## Result

- Files inventoried: **472**
- Snapshot remained stable during hashing: **yes**
- Unique SHA-256 hashes within this folder: **472**
- Exact-duplicate groups within this folder: **0**
- Redundant instances within those groups: **0**
- Terminal `(N)` filename hints: **0**
- Formats: mp3: 468, m4a: 2, wav: 1, aif: 1
- Sample rates: 44100 Hz: 468, 48000 Hz: 4
- Bitrate range: **128–1411 kbps**

## Missing metadata

- Missing artist: **15**
- Missing title: **3**
- Missing album: **180**
- Missing genre: **16**
- Missing year: **74**
- Missing bpm: **6**
- Missing key: **7**

## Terminal `(N)` hints

None in this folder.

## Number handling rule

Preserve numbers inside artists, titles, years, BPM labels, and version names. Only a final `(N)` is a copy-suffix hint, and it still requires hash evidence before exact-duplicate classification.

## Outputs

- `inventory.csv` — one row per audio file
- `inventory.json` — the same data plus audit metadata
- `cross-library-exact-copies.json` — earlier targeted comparison against the rest of the library
