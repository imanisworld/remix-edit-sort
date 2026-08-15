# DJ Library Audit — read-only survey

Library: `/Users/djb.a.e/Music/DJ-Music/Music`

> No audio, tags, Serato data, paths, or filenames were changed. No deletion or move plan was generated.

## Scope

- Audio files scanned: **25,703**
- Audio storage represented: **190.5 GiB**
- File formats: .mp3: 24,705, .flac: 844, .m4a: 105, .wav: 45, .aif: 4
- Files hashed for exact-copy checking: **1,340** (10.2 GiB read)

## Remix/edit findings

- Remix/edit candidates: **2,989**
- With an identifiable remixer: **2,813**
- Distinct parsed remixer spellings: **1,496**
- Long mixes/sets over 20 minutes: **32**
- Genre-classification unresolved: **10,463**

These are parser findings, not approved renames or classifications. `_Unclassified` remains preferable to guessing.

## Duplicate evidence

- Exact byte-identical groups: **171**
- Redundant file instances inside those groups: **177**
- Potentially reclaimable space if reviewed copies were eventually removed: **1.20 GiB**
- Same-name collision groups: **1,100**
- Numbered-copy name groups (`Track` / `Track (1)`): **16**

A same name is not considered a duplicate. Clean/dirty, intro/outro, radio/extended, remixes, edits, stems, and alternate masters remain distinct unless the bytes are actually identical—and even exact copies still require a Serato-reference decision before any removal.

## Metadata gaps

- Missing artist tag: **954**
- Missing title tag: **0**
- Missing BPM tag: **1,172**
- Missing key tag: **1,080**
- Missing genre tag: **1,995**
- MP3s below 192 kbps: **9,065** (review-only; bitrate alone does not make a file disposable)
- MP3s with no readable duration: **1**

## Largest top-level music folders by track count

- `Hip Hop`: 5,888
- `R&b`: 3,342
- `Events`: 3,023
- `neptunes`: 2,179
- `House`: 1,342
- `Pop`: 958
- `oLD Head Party`: 853
- `Lounge`: 728
- `Juke`: 676
- `NewFiles`: 661
- `GarageJungle`: 606
- `edit`: 472
- `Twerk `: 431
- `Jersey Club`: 420
- `alternative`: 266
- `Latin`: 253
- `Afrobeat`: 239
- `Dancehall`: 239
- `Underground HOUSE : DANCE:MOVE`: 196
- `Rock`: 176

## Largest exact-copy groups

### 1. 8 files × 9.1 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Slow Jams/Princess.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Slow Jams/Princess_1.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Slow Jams/Princess_2.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Slow Jams/Princess_3.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Slow Jams/Princess_4.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Slow Jams/Princess_5.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Slow Jams/Princess_6.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Slow Jams/Princess_7.mp3`

### 2. 2 files × 27.1 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/music to move/1-01. The Way.flac`
  - `/Users/djb.a.e/Music/DJ-Music/Music/May 16 2026/Music/NewFiles/TMHoarder/DRAM - Shake Tape (EP) (2026)/1-01. The Way.flac`

### 3. 2 files × 18.4 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/R&b/Justin Timberlake - Strawberry Bubblegum.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/oLD Head Party/Justin Timberlake - Strawberry Bubblegum.mp3`

### 4. 2 files × 13.8 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/Alright (Clean) (Extended).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/Janet Jackson - Alright (Clean) (Extended).mp3`

### 5. 2 files × 13.6 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Underground HOUSE : DANCE:MOVE/Lockdown - It's In Your Eyes - [320 kbps].mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/GarageJungle/UK Garage Collection   2 pack/Lockdown - It's In Your Eyes - [320 kbps].mp3`

### 6. 2 files × 13.0 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/edit/Inyembezi.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/House/house_plex/Inyembezi.mp3`

### 7. 2 files × 12.5 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/trendindg august 2025/CLEAN/Pitbull ft Lil Jon - Krazy (Clean) (1).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/trendindg august 2025/CLEAN/Pitbull ft Lil Jon - Krazy (Clean).mp3`

### 8. 2 files × 12.1 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/R&B Brunch/We Gonna Take U Back (Lude) _ Don't Leave Me.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Slow Jams/We Gonna Take U Back (Lude) _ Don't Leave Me.mp3`

### 9. 2 files × 12.1 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/edit/Inertia.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/House/house_plex/Inertia.mp3`

### 10. 2 files × 11.9 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/R&B Brunch/If It Isn't Love.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/80's R&B /If It Isn't Love.mp3`

### 11. 2 files × 11.8 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/edit/Byron the Aquarius - Make Me Feel.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Underground HOUSE : DANCE:MOVE/Byron the Aquarius - Make Me Feel.mp3`

### 12. 2 files × 11.6 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/P. Diddy ft Usher & Loon - I Need A Girl (Part 1) (Intro Clean)(1).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/P. Diddy ft Usher & Loon - I Need A Girl (Part 1) (Intro Clean).mp3`

### 13. 2 files × 11.6 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/310babii ft Mustard, Blueface, Tyga, BlueBucksClan & Ohgeesy - Soak City (Remix) (Intro Clean).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/Soak City (Remix) (Intro Clean).mp3`

### 14. 2 files × 11.5 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/R&B Brunch/Honey.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Slow Jams/Honey.mp3`

### 15. 2 files × 11.5 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/trendindg august 2025/INSTRUMENTALS/Al Green - Love And Happiness (Instrumental).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/August 30th Random DL/Al Green - Love And Happiness (Instrumental).mp3`

### 16. 2 files × 11.4 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/Agora Hills (Intro Clean).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/Doja Cat - Agora Hills (Intro Clean).mp3`

### 17. 2 files × 11.4 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/R&B Brunch/Killing Me Softly With His Song.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Slow Jams/Killing Me Softly With His Song.mp3`

### 18. 2 files × 11.3 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/R&B Brunch/Someone To Love You.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Slow Jams/Someone To Love You.mp3`

### 19. 2 files × 11.3 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/R&B Brunch/The Boy Is Mine.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Slow Jams/The Boy Is Mine.mp3`

### 20. 2 files × 11.2 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/310babii ft Mustard, Blueface, Tyga, BlueBucksClan & Ohgeesy - Soak City (Remix) (Clean).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/Soak City (Remix) (Clean).mp3`

### 21. 2 files × 11.1 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/R&B Brunch/Sweet Lady.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Slow Jams/Sweet Lady.mp3`

### 22. 2 files × 11.0 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/21 Savage - redrum (Clean).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/redrum (Clean).mp3`

### 23. 2 files × 10.9 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/Go Deep (Clean).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/Janet Jackson - Go Deep (Clean).mp3`

### 24. 2 files × 10.7 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Reggae/Heads High (Kill 'Em With It Re-Mix).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/oLD Head Party/Heads High (Kill 'Em With It Re-Mix).mp3`

### 25. 2 files × 10.5 MiB
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/21 Savage - redrum (Intro Clean).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/redrum (Intro Clean).mp3`


## Largest same-name collision groups

### 1. `Unknown.mp3` — 6 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/R&b/R&B Remixes/Unknown.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/edit/Unknown.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Afrobeat/Unknown.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Jersey Club/Unknown.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/Unknown.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/Hip Hop '00-'19/Unknown.mp3`

### 2. `Honey.mp3` — 5 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/Pop/Honey.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Ballroom/Honey.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Afrobeat/honey.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/R&B Brunch/Honey.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Slow Jams/Honey.mp3`

### 3. `Never Too Much.mp3` — 5 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/House/house_plex/Never Too Much.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Mixed/80s_90s_00s_Greatest_Hits/Never Too Much.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/R&B Brunch/Never Too Much.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/80's R&B /Never Too Much.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/oLD Head Party/Never Too Much.mp3`

### 4. `NOBODY.mp3` — 5 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/R&b/NOBODY.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/edit/nobody.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/House/Nobody.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/House/house_lounge/nobody.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/Hip Hop '00-'19/Nobody.mp3`

### 5. `Slow Down.mp3` — 5 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/R&b/Slow Down.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/R&b/R&B '20-'22 /Slow Down.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/R&B Brunch/Slow Down.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/80's R&B /Slow Down.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/oLD Head Party/Slow Down.mp3`

### 6. `YOU.mp3` — 5 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/Lounge/YOU.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/R&b/You.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/R&b/R&B '20-'22 /You.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/House/You.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/oLD Head Party/You.mp3`

### 7. `(Intro Dirty).mp3` — 4 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/R&b/(Intro Dirty).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/R&b/R&B '20-'22 /(Intro Dirty).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Twerk /(Intro Dirty).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/(Intro Dirty).mp3`

### 8. `Alright.mp3` — 4 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/conscious_hip_hop/Alright.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Mixed/80s_90s_00s_Greatest_Hits/Alright.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/R&B Brunch/Alright.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/80's R&B /Alright.mp3`

### 9. `Best Friend (Intro Clean).mp3` — 4 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/R&b/Best Friend (Intro Clean).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/Best Friend (Intro Clean).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/Hip Hop '00-'19/Best Friend (Intro Clean).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Dancehall/Best Friend (Intro Clean).mp3`

### 10. `Body.mp3` — 4 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/House/house_plex/Body.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/houston_rap/Body.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/music to move/Body.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/R&B Brunch/Body.mp3`

### 11. `BREAK MY SOUL.mp3` — 4 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/R&b/R&B '20-'22 /BREAK MY SOUL.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Acapella/BREAK MY SOUL.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/House/BREAK MY SOUL.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/House/house_plex/BREAK MY SOUL.mp3`

### 12. `Count Me Out.mp3` — 4 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/Count Me Out.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/conscious_hip_hop/count me out.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/80's R&B /Count Me Out.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/oLD Head Party/Count Me Out.mp3`

### 13. `DO 4 Love.mp3` — 4 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/edit/DO 4 Love.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/House/house_plex/DO 4 Love.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/2023 R&B/DO 4 LOVE.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/CLEAN R&B/DO 4 LOVE.mp3`

### 14. `Feel Good.mp3` — 4 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/R&b/R&B '20-'22 /Feel Good.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/R&b/R&B 2024/Feel Good.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/feel good.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/alternative_hip_hop/Feel Good.mp3`

### 15. `I Can't Go for That (No Can Do).mp3` — 4 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Yacht Rock/I Can't Go for That (No Can Do).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/WHITE OLDIES/I Can't Go for That (No Can Do).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/80's R&B /I Can't Go for That (No Can Do).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/oLD Head Party/I Can't Go for That (No Can Do).mp3`

### 16. `I Wanna Dance with Somebody (Who Loves Me).mp3` — 4 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/Pop/clean pop/I Wanna Dance with Somebody (Who Loves Me).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Mixed/80s_90s_00s_Greatest_Hits/I Wanna Dance with Somebody (Who Loves Me).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/WHITE OLDIES/I Wanna Dance with Somebody (Who Loves Me).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/oLD Head Party/I Wanna Dance with Somebody (Who Loves Me).mp3`

### 17. `Like That.mp3` — 4 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/Lounge/Like That.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Jersey Club/Like That.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Hip Hop/Hip Hop '00-'19/Like That.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/IYG PROM/GENERAL REQUEST/Like That.mp3`

### 18. `Me & U.mp3` — 4 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/R&b/afro_r&b/Me & U.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/edit/Me & U.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/CLEAN R&B/Me & U.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/oLD Head Party/Me & U.mp3`

### 19. `On My Mama.mp3` — 4 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/R&b/On My Mama.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/2023 R&B/On My Mama.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/R&B Brunch/On My Mama.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/CLEAN/CLEAN R&B/On My Mama.mp3`

### 20. `Ready or Not.mp3` — 4 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/R&b/Ready or Not.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/House/house_plex/ready or not.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Mixed/80s_90s_00s_Greatest_Hits/Ready or Not.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/R&B Brunch/Ready Or Not.mp3`

### 21. `ROCK THE BOAT.mp3` — 4 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/Amapiano /ROCK THE BOAT.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/DNB/rock the boat.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/R&B Brunch/Rock The Boat.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/oLD Head Party/Rock the Boat.mp3`

### 22. `Sweet Dreams.mp3` — 4 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/Pop/Sweet Dreams.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Underground HOUSE : DANCE:MOVE/sweet dreams.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/House/Sweet Dreams.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/House/house_plex/sweet dreams.mp3`

### 23. `Upside Down.mp3` — 4 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/Disco/Upside Down.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/House/house_plex/Upside Down.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/Mixed/80s_90s_00s_Greatest_Hits/Upside Down.mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Events/LIKE SUGAR ON MY TONGUE/80's R&B /Upside Down.mp3`

### 24. `(Intro Clean).mp3` — 3 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/R&b/(Intro Clean).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/R&b/R&B '20-'22 /(Intro Clean).mp3`
  - `/Users/djb.a.e/Music/DJ-Music/Music/Twerk /(Intro Clean).mp3`

### 25. `1-01. The Way.flac` — 3 files (mixed or distinct files — review)
  - `/Users/djb.a.e/Music/DJ-Music/Music/music to move/1-01. The Way.flac`
  - `/Users/djb.a.e/Music/DJ-Music/Music/May 16 2026/Music/NewFiles/TMHoarder/1-01. The Way.flac`
  - `/Users/djb.a.e/Music/DJ-Music/Music/May 16 2026/Music/NewFiles/TMHoarder/DRAM - Shake Tape (EP) (2026)/1-01. The Way.flac`


## Safe next step

Review the detailed collision groups and Serato references before building any move or deletion plan. Do not run `apply_plan.py --apply` yet.

Machine-readable evidence: `/Users/djb.a.e/.codex/visualizations/2026/08/15/01a006cb-a574-7660-b88f-4e7e372abd6e/survey/duplicate-details.json`
