# `Events` folder — read-only inventory

Scope: `/Users/djb.a.e/Music/DJ-Music/Music/Events`

> No music file, tag, filename, path, or Serato data was changed.

## Result

- Files inventoried: **3,046**
- Snapshot remained stable during hashing: **yes**
- Unique SHA-256 hashes within this folder: **2,996**
- Exact-duplicate groups within this folder: **44**
- Redundant instances within those groups: **50**
- Terminal `(N)` filename hints: **6**
- Formats: mp3: 3,033, flac: 10, aif: 2, m4a: 1
- Sample rates: 44100 Hz: 2,910, 48000 Hz: 136
- Bitrate range: **128–1740 kbps**

## Missing metadata

- Missing artist: **27**
- Missing title: **2**
- Missing album: **1,077**
- Missing genre: **332**
- Missing year: **170**
- Missing bpm: **10**
- Missing key: **4**

## Terminal `(N)` hints

- `Zhane - Sending My Love (Intro Clean) (1).mp3` — suffix `1`; SHA-256 recorded, not classified as a duplicate by name
- `Cardi B - Like What (Freestyle) (Clean) (1).mp3` — suffix `1`; SHA-256 recorded, not classified as a duplicate by name
- `Been Thru Hell (Topper-Intro) Demo-1 (2).mp3` — suffix `2`; SHA-256 recorded, not classified as a duplicate by name
- `Vanilla Bean  (1).mp3` — suffix `1`; SHA-256 recorded, not classified as a duplicate by name
- `The Fugees - Killing Me Softly (1996).mp3` — suffix `1996`; SHA-256 recorded, not classified as a duplicate by name
- `Pitbull ft Lil Jon - Krazy (Clean) (1).mp3` — suffix `1`; SHA-256 recorded, not classified as a duplicate by name

## Number handling rule

Preserve numbers inside artists, titles, years, BPM labels, and version names. Only a final `(N)` is a copy-suffix hint, and it still requires hash evidence before exact-duplicate classification.

## Outputs

- `inventory.csv` — one row per audio file
- `inventory.json` — the same data plus audit metadata
