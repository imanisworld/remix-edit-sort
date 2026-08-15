# Genre folder + metadata preview

**Preview only — no music files have been moved or retagged.**

Target: `/Users/djb.a.e/Music/DJ-Music/Music/music to move`

## Proposed rule

For resolved tracks, add a canonical genre folder beneath the existing version/category folder and write that exact same value into the file's Genre metadata. For unresolved tracks, use `_GENRE NEEDS CHECK` and leave the existing genre tag untouched.

Examples:

- `CLEAN/Pop/track.mp3` with Genre = `Pop`
- `DIRTY/Hip-Hop/track.mp3` with Genre = `Hip-Hop`
- `REMIXES & EDITS/Miami Bass/track.mp3` with Genre = `Miami Bass`
- `ALBUMS & PROJECTS/Juke & Footwork/<project>/track.mp3` with Genre = `Juke & Footwork`
- `_NEEDS CHECK/_GENRE NEEDS CHECK/track.mp3` with its genre tag unchanged

`DJ TOOLS/Scratch Beats` stays structurally intact; its tracks get Genre = `DJ Tools` instead of a redundant `DJ TOOLS/DJ Tools` folder.

## Counts

- Tracks checked: **152**
- Proposed folder moves: **126**
- Proposed Genre tag changes: **99**
- Genre tag unchanged: **53**
- Routed to `_GENRE NEEDS CHECK`: **31**
- Missing sources: **0**
- Destination collisions: **0**
- Existing destination conflicts: **0**

## Proposed genre totals

| Genre folder / tag | Tracks |
|---|---:|
| Juke & Footwork | 34 |
| _GENRE NEEDS CHECK | 31 |
| Hip-Hop | 29 |
| DJ Tools | 26 |
| R&B | 19 |
| Bounce | 3 |
| Pop | 3 |
| Miami Bass | 2 |
| Baltimore Club | 1 |
| Dembow | 1 |
| Electronic | 1 |
| Funk & Soul | 1 |
| Ghettotech | 1 |

## Metadata normalization

| Current Genre | Proposed Genre | Tracks |
|---|---|---:|
| (blank) | Juke & Footwork | 33 |
| DJ Tools, Scratch Beats, break beats | DJ Tools | 18 |
| Hip-Hop/Rap | Hip-Hop | 9 |
| Hip Hop | Hip-Hop | 7 |
| Rap/Hip Hop | Hip-Hop | 5 |
| Rap | Hip-Hop | 4 |
| Blues | DJ Tools | 2 |
| Hip-hop & Rap | DJ Tools | 2 |
| Hip-hop & Rap | Hip-Hop | 2 |
| (blank) | Bounce | 1 |
| (blank) | DJ Tools | 1 |
| Classic Funk-Soul, old head party | Funk & Soul | 1 |
| DEMBOW | Dembow | 1 |
| Dance & EDM | Ghettotech | 1 |
| Electronic | Baltimore Club | 1 |
| Gangsta Rap, Hip Hop, Southern Hip Hop, Trap | Hip-Hop | 1 |
| Hip Hop | DJ Tools | 1 |
| HipHop | DJ Tools | 1 |
| Juke | Juke & Footwork | 1 |
| Other | DJ Tools | 1 |
| R&B & Soul | Bounce | 1 |
| R&B & Soul | R&B | 1 |
| R&B/Soul | R&B | 1 |
| Rap/Hip-Hop | Hip-Hop | 1 |
| miamibass | Miami Bass | 1 |
| r&b | R&B | 1 |

## Needs genre review

These are deliberately not guessed. Their Genre metadata will remain unchanged.

| File | Current Genre | Why unresolved |
|---|---|---|
| `CLEAN/Trim - Coconut Water (Clean) 90.mp3` | — | no genre tag and no explicit genre evidence |
| `DIRTY/Trim - Coconut Water (Dirty) 90.mp3` | — | no genre tag and no explicit genre evidence |
| `DIRTY/Trina- Told Y'all- Explicit Edit Ft. Rick Ross.mp3` | — | no genre tag and no explicit genre evidence |
| `REMIXES & EDITS/01 Friend Do Remix (feat. YKNIECE) Belly Gang Kushington, YKNIECE Friend Do Remix (feat. YKNIECE) 2025.mp3` | — | no genre tag and no explicit genre evidence |
| `REMIXES & EDITS/Belly Gang Kushington - Friend Do Remix ft. YKNIECE (Official Music Video).mp3` | — | no genre tag and no explicit genre evidence |
| `REMIXES & EDITS/Leonce - Refixes Vol 4 - 03 Victoria Monét - Touch Me (Leonce Refix).mp3` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/01 Coco-Cutwater.mp3` | Other | generic Other tag is not genre evidence |
| `_NEEDS CHECK/01. Hoe Phase.flac` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/03. Outside Tweaking.flac` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/04. Cheetah Print.flac` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/04. Janice STFU.flac` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/05. Ran To Atlanta.flac` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/06. Amazing Shape.flac` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/07. Classic.flac` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/08. True Bestie.flac` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/109 - kwn - touch myself.mp3` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/11. Q&A.flac` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/12. Stuck.flac` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/129 - Luh Tyler - No Trick (feat. Trim).mp3` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/Alton Pain Mix4Mast_1.wav` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/CUZZOS - MOESHA CYPHER.mp3` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/Don Toliver - No Pole [Official Visualizer].mp3` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/How many of us (Friends) - DJ-SUN x Matthew Law.mp3` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/Icewear Vezzo; Mello Buckzz - I Like.flac` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/LOVE IS A DRUG.mp3` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/Let's Groove.mp3` | Urban, Hip Hop, old head party | ambiguous or compound genre tag: Urban, Hip Hop, old head party |
| `_NEEDS CHECK/Monaleo - Everythang Pinka (feat. Teezo Touchdown).mp3` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/Novacane.mp3` | lgbtq+ hip hop;neo soul | ambiguous or compound genre tag: lgbtq+ hip hop;neo soul |
| `_NEEDS CHECK/Ophelia.mp3` | rock, r&b edit, alternative r&b, alternative hip hop | ambiguous or compound genre tag: rock, r&b edit, alternative r&b, alternative hip hop |
| `_NEEDS CHECK/Too Many Women (feat. kwn).mp3` | — | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/Track 3.m4a` | — | no genre tag and no explicit genre evidence |

## Full proposed mapping

| From | To | Genre metadata | Evidence |
|---|---|---|---|
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/01 DJ Rashad - Unknown.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/01 DJ Rashad - Unknown.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/02 DJ ROYAL - THROWBACK DJ ROYAL.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/02 DJ ROYAL - THROWBACK DJ ROYAL.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/03 DJ Elmoe - Havoc In Dis Thang Let Dat Bitch Bang.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/03 DJ Elmoe - Havoc In Dis Thang Let Dat Bitch Bang.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/04 DJ Nate - LOVE U IN SLOW MOTION.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/04 DJ Nate - LOVE U IN SLOW MOTION.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/05 D-JAY D~LO (TRACK KILLAHS) - LET ME SEE YO BASICS.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/05 D-JAY D~LO (TRACK KILLAHS) - LET ME SEE YO BASICS.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/06 D-JAY D~LO (TRACK KILLAHS) - SPORTS CENTER TRACK.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/06 D-JAY D~LO (TRACK KILLAHS) - SPORTS CENTER TRACK.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/07 DJ DNA - SouthsideStandup.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/07 DJ DNA - SouthsideStandup.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/08 DJ Killa E - Burn Em All.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/08 DJ Killa E - Burn Em All.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/09 DJ Nate - Frostebite RIP.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/09 DJ Nate - Frostebite RIP.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/10 DJ Roc - I Cant Let Go.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/10 DJ Roc - I Cant Let Go.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/11 Unknown - BREAK THAT NIGGA.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/11 Unknown - BREAK THAT NIGGA.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/12 DJ Lil Jay - BURN THAT MOTHER FUCKER.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/12 DJ Lil Jay - BURN THAT MOTHER FUCKER.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/13 DJ KMAN - NIGGA LOOKIN LIKE GURL.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/13 DJ KMAN - NIGGA LOOKIN LIKE GURL.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/14 Unknown - Scarface Trak.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/14 Unknown - Scarface Trak.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/15 DJ Speed - Crack 4 Da Circle Killahs.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/15 DJ Speed - Crack 4 Da Circle Killahs.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/16 RP Boo - Im Burnin' Em.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/16 RP Boo - Im Burnin' Em.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/17 DJ Shawn - Burn.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/17 DJ Shawn - Burn.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/18 DJ Elmoe - BURNIN SEASON.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/18 DJ Elmoe - BURNIN SEASON.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/21 DJ Killa - You Aint Really On Shit.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/21 DJ Killa - You Aint Really On Shit.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/22 DJ Rashad - We Tryna Fuk.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/22 DJ Rashad - We Tryna Fuk.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/23 Unknown - Battle In The Circle Juke Track.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/23 Unknown - Battle In The Circle Juke Track.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/24 DJCH0PPA - X-Files.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/24 DJCH0PPA - X-Files.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/25 DJ Spaldin - Unknown.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/25 DJ Spaldin - Unknown.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/26 DJ Trouble - I AM FOOTWORK.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/26 DJ Trouble - I AM FOOTWORK.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/27 DJ Ro - WALACAM.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/27 DJ Ro - WALACAM.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/28 DJ Thadz - What U Workin Wit.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/28 DJ Thadz - What U Workin Wit.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/29 DJ Trouble - Freak Um.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/29 DJ Trouble - Freak Um.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/30 RP Boo - Freez or Burn (WALACAM).mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/30 RP Boo - Freez or Burn (WALACAM).mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/31 DJ Weezy - Sex Therapy Juke Mix.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/31 DJ Weezy - Sex Therapy Juke Mix.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/32 DJ Twan - Free Twan.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/32 DJ Twan - Free Twan.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/33 DJ TJ - WALACAM.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/33 DJ TJ - WALACAM.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/34 L.A. The Track Assassin - I'm Burnin.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/34 L.A. The Track Assassin - I'm Burnin.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/CRACK - A Lost Juke-Footwork Compilation/35 DJ Elmoe - Elmoe Signin Off Request For Stay.mp3` | `ALBUMS & PROJECTS/Juke & Footwork/CRACK - A Lost Juke-Footwork Compilation/35 DJ Elmoe - Elmoe Signin Off Request For Stay.mp3` | Juke & Footwork | project name and album tag explicitly say Juke/Footwork |
| `ALBUMS & PROJECTS/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/01-destin_conrad_keyon_harrold-whip-a5abab17.mp3` | `ALBUMS & PROJECTS/R&B/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/01-destin_conrad_keyon_harrold-whip-a5abab17.mp3` | R&B | all project tracks carry the R&B tag |
| `ALBUMS & PROJECTS/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/02-destin_conrad-glory_a_poem_by_bay_davis-985bed7a.mp3` | `ALBUMS & PROJECTS/R&B/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/02-destin_conrad-glory_a_poem_by_bay_davis-985bed7a.mp3` | R&B | all project tracks carry the R&B tag |
| `ALBUMS & PROJECTS/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/03-destin_conrad_terrace_martin-wash_u_away-2e620cd9.mp3` | `ALBUMS & PROJECTS/R&B/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/03-destin_conrad_terrace_martin-wash_u_away-2e620cd9.mp3` | R&B | all project tracks carry the R&B tag |
| `ALBUMS & PROJECTS/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/04-destin_conrad-love-1fc86d07.mp3` | `ALBUMS & PROJECTS/R&B/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/04-destin_conrad-love-1fc86d07.mp3` | R&B | all project tracks carry the R&B tag |
| `ALBUMS & PROJECTS/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/05-destin_conrad-bed_stuy-95e36f37.mp3` | `ALBUMS & PROJECTS/R&B/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/05-destin_conrad-bed_stuy-95e36f37.mp3` | R&B | all project tracks carry the R&B tag |
| `ALBUMS & PROJECTS/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/06-destin_conrad_vanisha_gould-a_lonely_detective-b9be2bf9.mp3` | `ALBUMS & PROJECTS/R&B/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/06-destin_conrad_vanisha_gould-a_lonely_detective-b9be2bf9.mp3` | R&B | all project tracks carry the R&B tag |
| `ALBUMS & PROJECTS/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/07-destin_conrad_vanisha_gould-boredom-07e7b590.mp3` | `ALBUMS & PROJECTS/R&B/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/07-destin_conrad_vanisha_gould-boredom-07e7b590.mp3` | R&B | all project tracks carry the R&B tag |
| `ALBUMS & PROJECTS/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/08-destin_conrad_ambre-ambres_interlude_but_jazz-a8d518eb.mp3` | `ALBUMS & PROJECTS/R&B/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/08-destin_conrad_ambre-ambres_interlude_but_jazz-a8d518eb.mp3` | R&B | all project tracks carry the R&B tag |
| `ALBUMS & PROJECTS/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/09-destin_conrad_keyon_harrold-drifting-499e1d43.mp3` | `ALBUMS & PROJECTS/R&B/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/09-destin_conrad_keyon_harrold-drifting-499e1d43.mp3` | R&B | all project tracks carry the R&B tag |
| `ALBUMS & PROJECTS/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/10-destin_conrad_james_fauntleroy_astyn_turr-the_w-51d3ec8f.mp3` | `ALBUMS & PROJECTS/R&B/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/10-destin_conrad_james_fauntleroy_astyn_turr-the_w-51d3ec8f.mp3` | R&B | all project tracks carry the R&B tag |
| `ALBUMS & PROJECTS/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/11-destin_conrad-hard-283d2440.mp3` | `ALBUMS & PROJECTS/R&B/Destin_Conrad-wHIMSY-WEB-2025-ENRiCH/11-destin_conrad-hard-283d2440.mp3` | R&B | all project tracks carry the R&B tag |
| `ALBUMS & PROJECTS/Spend Dat/01 - Spend Dat - Unknown.mp3` | `ALBUMS & PROJECTS/Hip-Hop/Spend Dat/01 - Spend Dat - Unknown.mp3` | Hip-Hop | all project tracks carry the Hip-Hop/Rap tag |
| `ALBUMS & PROJECTS/Spend Dat/01 - Spend Dat - Yung Miami (1).mp3` | `ALBUMS & PROJECTS/Hip-Hop/Spend Dat/01 - Spend Dat - Yung Miami (1).mp3` | Hip-Hop | all project tracks carry the Hip-Hop/Rap tag |
| `ALBUMS & PROJECTS/Spend Dat/01 - Spend Dat - Yung Miami.mp3` | `ALBUMS & PROJECTS/Hip-Hop/Spend Dat/01 - Spend Dat - Yung Miami.mp3` | Hip-Hop | all project tracks carry the Hip-Hop/Rap tag |
| `CLEAN/Got Me (Intro Clean).mp3` | `CLEAN/Hip-Hop/Got Me (Intro Clean).mp3` | Hip-Hop | existing genre tag: Hip Hop |
| `CLEAN/PinkPantheress - Illegal (Intro Clean).mp3` | `CLEAN/Pop/PinkPantheress - Illegal (Intro Clean).mp3` | Pop | existing genre tag: Pop |
| `CLEAN/PinkPantheress - Tonight (Intro Clean).mp3` | `CLEAN/Pop/PinkPantheress - Tonight (Intro Clean).mp3` | Pop | existing genre tag: Pop |
| `CLEAN/Trim - Coconut Water (Clean) 90.mp3` | `CLEAN/_GENRE NEEDS CHECK/Trim - Coconut Water (Clean) 90.mp3` | unchanged | no genre tag and no explicit genre evidence |
| `DIRTY/Big Boogie - Take Her Down (Intro Dirty).mp3` | `DIRTY/Hip-Hop/Big Boogie - Take Her Down (Intro Dirty).mp3` | Hip-Hop | existing genre tag: Hip Hop |
| `DIRTY/Come On, Let's Go (Intro Dirty).mp3` | `DIRTY/Hip-Hop/Come On, Let's Go (Intro Dirty).mp3` | Hip-Hop | existing genre tag: Hip Hop |
| `DIRTY/DOGTOOTH (Dirty).mp3` | `DIRTY/Hip-Hop/DOGTOOTH (Dirty).mp3` | Hip-Hop | existing genre tag: Hip Hop |
| `DIRTY/Kehlani - Folded (Intro Dirty).mp3` | `DIRTY/R&B/Kehlani - Folded (Intro Dirty).mp3` | R&B | existing genre tag: R&B |
| `DIRTY/Trim - Coconut Water (Dirty) 90.mp3` | `DIRTY/_GENRE NEEDS CHECK/Trim - Coconut Water (Dirty) 90.mp3` | unchanged | no genre tag and no explicit genre evidence |
| `DIRTY/Trina- Told Y'all- Explicit Edit Ft. Rick Ross.mp3` | `DIRTY/_GENRE NEEDS CHECK/Trina- Told Y'all- Explicit Edit Ft. Rick Ross.mp3` | unchanged | no genre tag and no explicit genre evidence |
| `DJ TOOLS/Scratch Beats/01 Voice & Bits 21.mp3` | `DJ TOOLS/Scratch Beats/01 Voice & Bits 21.mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/01 Voice & Bits 22.mp3` | `DJ TOOLS/Scratch Beats/01 Voice & Bits 22.mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/07 Yo Mom Is In My (Break).mp3` | `DJ TOOLS/Scratch Beats/07 Yo Mom Is In My (Break).mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/Bang This.mp3` | `DJ TOOLS/Scratch Beats/Bang This.mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/Cypher (Scratch Beats).mp3` | `DJ TOOLS/Scratch Beats/Cypher (Scratch Beats).mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/Fab 5 Freddy, Beside, Greg Wilson, PREVIEW - Change The Beat (Greg Wilson Edit).mp3` | `DJ TOOLS/Scratch Beats/Fab 5 Freddy, Beside, Greg Wilson, PREVIEW - Change The Beat (Greg Wilson Edit).mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/Fireworks (Scratch Beats).mp3` | `DJ TOOLS/Scratch Beats/Fireworks (Scratch Beats).mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/Haunted (Scratch Beats).mp3` | `DJ TOOLS/Scratch Beats/Haunted (Scratch Beats).mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/MR. FORTUNA (Scratch Beats).mp3` | `DJ TOOLS/Scratch Beats/MR. FORTUNA (Scratch Beats).mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/Moon Invasion (Scratch Beats).mp3` | `DJ TOOLS/Scratch Beats/Moon Invasion (Scratch Beats).mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/SS 04 Beat (poof) 90bpm copy.mp3` | `DJ TOOLS/Scratch Beats/SS 04 Beat (poof) 90bpm copy.mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/SS 04 Beat (poof) 90bpm.mp3` | `DJ TOOLS/Scratch Beats/SS 04 Beat (poof) 90bpm.mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/SS 10 Beat (take my heart) 89 bpm.mp3` | `DJ TOOLS/Scratch Beats/SS 10 Beat (take my heart) 89 bpm.mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/SSS 3 Beat (recording Version w 4 bar intro) 99 Bpm.mp3` | `DJ TOOLS/Scratch Beats/SSS 3 Beat (recording Version w 4 bar intro) 99 Bpm.mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/Saturday Sessions 2019 - Session 02 beat (95bpm).mp3` | `DJ TOOLS/Scratch Beats/Saturday Sessions 2019 - Session 02 beat (95bpm).mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/Saturday Sessions 2019 - Session 04 Beat.mp3` | `DJ TOOLS/Scratch Beats/Saturday Sessions 2019 - Session 04 Beat.mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/Saturday Sessions 2019 - Session 05 beat (94 bpm).mp3` | `DJ TOOLS/Scratch Beats/Saturday Sessions 2019 - Session 05 beat (94 bpm).mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/Saturday Super Session 4 beat.mp3` | `DJ TOOLS/Scratch Beats/Saturday Super Session 4 beat.mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/Scratch Vocals (Once Again - Scratch It).mp3` | `DJ TOOLS/Scratch Beats/Scratch Vocals (Once Again - Scratch It).mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/Summer Breeze.mp3` | `DJ TOOLS/Scratch Beats/Summer Breeze.mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/The Subway.mp3` | `DJ TOOLS/Scratch Beats/The Subway.mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/YO scratch sample.mp3` | `DJ TOOLS/Scratch Beats/YO scratch sample.mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/djTLMtv Freestyle Scratching Beat.mp3` | `DJ TOOLS/Scratch Beats/djTLMtv Freestyle Scratching Beat.mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/djTLMtv SS 02 Beat (u8) 97bpm.mp3` | `DJ TOOLS/Scratch Beats/djTLMtv SS 02 Beat (u8) 97bpm.mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/djTLMtv Saturday Sessions 10 (X - Mas 2015) 91bpm.mp3` | `DJ TOOLS/Scratch Beats/djTLMtv Saturday Sessions 10 (X - Mas 2015) 91bpm.mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `DJ TOOLS/Scratch Beats/djTLMtv Saturday Sessions Jan 7 2017 (95bpm).mp3` | `DJ TOOLS/Scratch Beats/djTLMtv Saturday Sessions Jan 7 2017 (95bpm).mp3` | DJ Tools | existing DJ TOOLS/Scratch Beats placement |
| `REMIXES & EDITS/01 Friend Do Remix (feat. YKNIECE) Belly Gang Kushington, YKNIECE Friend Do Remix (feat. YKNIECE) 2025.mp3` | `REMIXES & EDITS/_GENRE NEEDS CHECK/01 Friend Do Remix (feat. YKNIECE) Belly Gang Kushington, YKNIECE Friend Do Remix (feat. YKNIECE) 2025.mp3` | unchanged | no genre tag and no explicit genre evidence |
| `REMIXES & EDITS/ANTWIGADEE! - LOVE ME ANTWIGADEE! BOUNCE REMIX Bounce.mp3` | `REMIXES & EDITS/Bounce/ANTWIGADEE! - LOVE ME ANTWIGADEE! BOUNCE REMIX Bounce.mp3` | Bounce | existing genre tag: Bounce |
| `REMIXES & EDITS/Belly Gang Kushington - Friend Do Remix ft. YKNIECE (Official Music Video).mp3` | `REMIXES & EDITS/_GENRE NEEDS CHECK/Belly Gang Kushington - Friend Do Remix ft. YKNIECE (Official Music Video).mp3` | unchanged | no genre tag and no explicit genre evidence |
| `REMIXES & EDITS/Dezzy Andretti - Leon Thomas - Vibes Don't Lie Ghettotech RMX Dance & EDM.mp3` | `REMIXES & EDITS/Ghettotech/Dezzy Andretti - Leon Thomas - Vibes Don't Lie Ghettotech RMX Dance & EDM.mp3` | Ghettotech | filename explicitly says Ghettotech |
| `REMIXES & EDITS/Leonce - Refixes Vol 4 - 03 Victoria Monét - Touch Me (Leonce Refix).mp3` | `REMIXES & EDITS/_GENRE NEEDS CHECK/Leonce - Refixes Vol 4 - 03 Victoria Monét - Touch Me (Leonce Refix).mp3` | unchanged | no genre tag and no explicit genre evidence |
| `REMIXES & EDITS/Rufus & Chaka Khan - Aint Nobody [DJ Re-Grid].mp3` | `REMIXES & EDITS/Funk & Soul/Rufus & Chaka Khan - Aint Nobody [DJ Re-Grid].mp3` | Funk & Soul | existing genre tag: Classic Funk-Soul, old head party |
| `REMIXES & EDITS/Sir Sharles - TAKE IT OFF “LIGHTS OFF” [KINGMIX] Hip-hop & Rap.mp3` | `REMIXES & EDITS/Hip-Hop/Sir Sharles - TAKE IT OFF “LIGHTS OFF” [KINGMIX] Hip-hop & Rap.mp3` | Hip-Hop | existing genre tag: Hip-hop & Rap |
| `REMIXES & EDITS/St. Saudade - TAKEMETHRUTHERE! (Saint's ATL Bass Rmx) Miami Bass.mp3` | `REMIXES & EDITS/Miami Bass/St. Saudade - TAKEMETHRUTHERE! (Saint's ATL Bass Rmx) Miami Bass.mp3` | Miami Bass | filename explicitly says ATL Bass; existing tag says Miami Bass |
| `REMIXES & EDITS/ave4., SKEEYEE - Tryna See Wassup (SKEEYEE Flip).mp3` | `REMIXES & EDITS/Hip-Hop/ave4., SKEEYEE - Tryna See Wassup (SKEEYEE Flip).mp3` | Hip-Hop | existing genre tag: Hip Hop |
| `REMIXES & EDITS/dev.n & GrantThaSpecialist - Moments in SkeeYee (Juke Edit) Juke.mp3` | `REMIXES & EDITS/Juke & Footwork/dev.n & GrantThaSpecialist - Moments in SkeeYee (Juke Edit) Juke.mp3` | Juke & Footwork | filename explicitly says Juke |
| `REMIXES & EDITS/peterpeterpeter - Kehlani - Distraction (Peter Edit) miamibass.mp3` | `REMIXES & EDITS/Miami Bass/peterpeterpeter - Kehlani - Distraction (Peter Edit) miamibass.mp3` | Miami Bass | existing genre tag: miamibass |
| `REMIXES & EDITS/wellsent - tyler, the creator - darling, i (feat. teezo touchdown) (wellsent remix).mp3` | `REMIXES & EDITS/R&B/wellsent - tyler, the creator - darling, i (feat. teezo touchdown) (wellsent remix).mp3` | R&B | existing genre tag: R&B & Soul |
| `_NEEDS CHECK/01 - Spend Dat - Unknown.mp3` | `_NEEDS CHECK/Hip-Hop/01 - Spend Dat - Unknown.mp3` | Hip-Hop | existing genre tag: Hip-Hop/Rap |
| `_NEEDS CHECK/01 - Spend Dat - Yung Miami (1).mp3` | `_NEEDS CHECK/Hip-Hop/01 - Spend Dat - Yung Miami (1).mp3` | Hip-Hop | existing genre tag: Hip-Hop/Rap |
| `_NEEDS CHECK/01 - Spend Dat - Yung Miami.mp3` | `_NEEDS CHECK/Hip-Hop/01 - Spend Dat - Yung Miami.mp3` | Hip-Hop | existing genre tag: Hip-Hop/Rap |
| `_NEEDS CHECK/01 Coco-Cutwater.mp3` | `_NEEDS CHECK/_GENRE NEEDS CHECK/01 Coco-Cutwater.mp3` | unchanged | generic Other tag is not genre evidence |
| `_NEEDS CHECK/01-chloe_and_timbaland-talking_dirty-uvu.mp3` | `_NEEDS CHECK/R&B/01-chloe_and_timbaland-talking_dirty-uvu.mp3` | R&B | existing genre tag: R&B |
| `_NEEDS CHECK/01. Hoe Phase.flac` | `_NEEDS CHECK/_GENRE NEEDS CHECK/01. Hoe Phase.flac` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/03. Outside Tweaking.flac` | `_NEEDS CHECK/_GENRE NEEDS CHECK/03. Outside Tweaking.flac` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/04 - FACE DOWN.mp3` | `_NEEDS CHECK/Hip-Hop/04 - FACE DOWN.mp3` | Hip-Hop | existing genre tag: Gangsta Rap, Hip Hop, Southern Hip Hop, Trap |
| `_NEEDS CHECK/04. Cheetah Print.flac` | `_NEEDS CHECK/_GENRE NEEDS CHECK/04. Cheetah Print.flac` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/04. Janice STFU.flac` | `_NEEDS CHECK/_GENRE NEEDS CHECK/04. Janice STFU.flac` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/05 - Nasty.flac` | `_NEEDS CHECK/Hip-Hop/05 - Nasty.flac` | Hip-Hop | existing genre tag: Hip Hop |
| `_NEEDS CHECK/05. Ran To Atlanta.flac` | `_NEEDS CHECK/_GENRE NEEDS CHECK/05. Ran To Atlanta.flac` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/06. Amazing Shape.flac` | `_NEEDS CHECK/_GENRE NEEDS CHECK/06. Amazing Shape.flac` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/07. Classic.flac` | `_NEEDS CHECK/_GENRE NEEDS CHECK/07. Classic.flac` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/08. True Bestie.flac` | `_NEEDS CHECK/_GENRE NEEDS CHECK/08. True Bestie.flac` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/1-01. The Way.flac` | `_NEEDS CHECK/Hip-Hop/1-01. The Way.flac` | Hip-Hop | existing genre tag: Hip-Hop/Rap |
| `_NEEDS CHECK/1-02. Circular Motion! (feat. Ellis Quinn).flac` | `_NEEDS CHECK/Hip-Hop/1-02. Circular Motion! (feat. Ellis Quinn).flac` | Hip-Hop | existing genre tag: Hip-Hop/Rap |
| `_NEEDS CHECK/1-03. Dream Shake.flac` | `_NEEDS CHECK/Hip-Hop/1-03. Dream Shake.flac` | Hip-Hop | existing genre tag: Hip-Hop/Rap |
| `_NEEDS CHECK/109 - kwn - touch myself.mp3` | `_NEEDS CHECK/_GENRE NEEDS CHECK/109 - kwn - touch myself.mp3` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/11. Q&A.flac` | `_NEEDS CHECK/_GENRE NEEDS CHECK/11. Q&A.flac` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/12. Stuck.flac` | `_NEEDS CHECK/_GENRE NEEDS CHECK/12. Stuck.flac` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/129 - Luh Tyler - No Trick (feat. Trim).mp3` | `_NEEDS CHECK/_GENRE NEEDS CHECK/129 - Luh Tyler - No Trick (feat. Trim).mp3` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/16. Sasha Keable - tell me what you want.mp3` | `_NEEDS CHECK/R&B/16. Sasha Keable - tell me what you want.mp3` | R&B | existing genre tag: R&B |
| `_NEEDS CHECK/A-Town Players - Wassup Wassup ( Bankhead Bounce ) Atlanta Classic 1995.mp3` | `_NEEDS CHECK/Bounce/A-Town Players - Wassup Wassup ( Bankhead Bounce ) Atlanta Classic 1995.mp3` | Bounce | filename explicitly names a Bounce style |
| `_NEEDS CHECK/Alton Pain Mix4Mast_1.wav` | `_NEEDS CHECK/_GENRE NEEDS CHECK/Alton Pain Mix4Mast_1.wav` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/Body.mp3` | `_NEEDS CHECK/R&B/Body.mp3` | R&B | existing genre tag: r&b |
| `_NEEDS CHECK/BossMan Dlow - Motion Party.mp3` | `_NEEDS CHECK/Hip-Hop/BossMan Dlow - Motion Party.mp3` | Hip-Hop | existing genre tag: Rap |
| `_NEEDS CHECK/CUZZOS - MOESHA CYPHER.mp3` | `_NEEDS CHECK/_GENRE NEEDS CHECK/CUZZOS - MOESHA CYPHER.mp3` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/DJ 809 - HEADS HIGH (809 DEMBOW MIX).mp3` | `_NEEDS CHECK/Dembow/DJ 809 - HEADS HIGH (809 DEMBOW MIX).mp3` | Dembow | existing genre tag: DEMBOW |
| `_NEEDS CHECK/Don Toliver - ATM.mp3` | `_NEEDS CHECK/Hip-Hop/Don Toliver - ATM.mp3` | Hip-Hop | existing genre tag: Rap/Hip Hop |
| `_NEEDS CHECK/Don Toliver - No Pole [Official Visualizer].mp3` | `_NEEDS CHECK/_GENRE NEEDS CHECK/Don Toliver - No Pole [Official Visualizer].mp3` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/How many of us (Friends) - DJ-SUN x Matthew Law.mp3` | `_NEEDS CHECK/_GENRE NEEDS CHECK/How many of us (Friends) - DJ-SUN x Matthew Law.mp3` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/Icewear Vezzo; Mello Buckzz - I Like.flac` | `_NEEDS CHECK/_GENRE NEEDS CHECK/Icewear Vezzo; Mello Buckzz - I Like.flac` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/LOVE IS A DRUG.mp3` | `_NEEDS CHECK/_GENRE NEEDS CHECK/LOVE IS A DRUG.mp3` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/LYRIC LE'SON (CHEF) - FALSETTO ARCHES (BMORE) Electronic.mp3` | `_NEEDS CHECK/Baltimore Club/LYRIC LE'SON (CHEF) - FALSETTO ARCHES (BMORE) Electronic.mp3` | Baltimore Club | filename explicitly says BMORE |
| `_NEEDS CHECK/Let's Groove.mp3` | `_NEEDS CHECK/_GENRE NEEDS CHECK/Let's Groove.mp3` | unchanged | ambiguous or compound genre tag: Urban, Hip Hop, old head party |
| `_NEEDS CHECK/Loe Shimmy, Don Toliver - 3am.mp3` | `_NEEDS CHECK/Hip-Hop/Loe Shimmy, Don Toliver - 3am.mp3` | Hip-Hop | existing genre tag: Rap/Hip-Hop |
| `_NEEDS CHECK/Monaleo - Everythang Pinka (feat. Teezo Touchdown).mp3` | `_NEEDS CHECK/_GENRE NEEDS CHECK/Monaleo - Everythang Pinka (feat. Teezo Touchdown).mp3` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/Novacane.mp3` | `_NEEDS CHECK/_GENRE NEEDS CHECK/Novacane.mp3` | unchanged | ambiguous or compound genre tag: lgbtq+ hip hop;neo soul |
| `_NEEDS CHECK/Ophelia.mp3` | `_NEEDS CHECK/_GENRE NEEDS CHECK/Ophelia.mp3` | unchanged | ambiguous or compound genre tag: rock, r&b edit, alternative r&b, alternative hip hop |
| `_NEEDS CHECK/Shoulda Never (feat. Usher).mp3` | `_NEEDS CHECK/R&B/Shoulda Never (feat. Usher).mp3` | R&B | existing genre tag: R&B |
| `_NEEDS CHECK/Too Many Women (feat. kwn).mp3` | `_NEEDS CHECK/_GENRE NEEDS CHECK/Too Many Women (feat. kwn).mp3` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/Track 1.mp3` | `_NEEDS CHECK/Hip-Hop/Track 1.mp3` | Hip-Hop | existing genre tag: Hip-hop & Rap |
| `_NEEDS CHECK/Track 2.mp3` | `_NEEDS CHECK/R&B/Track 2.mp3` | R&B | existing genre tag: R&B/Soul |
| `_NEEDS CHECK/Track 3.m4a` | `_NEEDS CHECK/_GENRE NEEDS CHECK/Track 3.m4a` | unchanged | no genre tag and no explicit genre evidence |
| `_NEEDS CHECK/Track 4.mp3` | `_NEEDS CHECK/Electronic/Track 4.mp3` | Electronic | existing genre tag: Electronic |
| `_NEEDS CHECK/Two1Five - Love Me (NOLA Bounce).mp3` | `_NEEDS CHECK/Bounce/Two1Five - Love Me (NOLA Bounce).mp3` | Bounce | filename explicitly names a Bounce style |
| `_NEEDS CHECK/don toliver - body.mp3` | `_NEEDS CHECK/Hip-Hop/don toliver - body.mp3` | Hip-Hop | existing genre tag: Rap/Hip Hop |
| `_NEEDS CHECK/don toliver - tiramisu.mp3` | `_NEEDS CHECK/Hip-Hop/don toliver - tiramisu.mp3` | Hip-Hop | existing genre tag: Rap/Hip Hop |
| `_NEEDS CHECK/don toliver - tmu.mp3` | `_NEEDS CHECK/Hip-Hop/don toliver - tmu.mp3` | Hip-Hop | existing genre tag: Rap/Hip Hop |
| `_NEEDS CHECK/don toliver - tuition.mp3` | `_NEEDS CHECK/Hip-Hop/don toliver - tuition.mp3` | Hip-Hop | existing genre tag: Rap/Hip Hop |
| `_NEEDS CHECK/pinkpantheress - noises + jt.mp3` | `_NEEDS CHECK/Pop/pinkpantheress - noises + jt.mp3` | Pop | existing genre tag: Pop |
| `_NEEDS CHECK/sailorr, eem triplin - coconut (feat. eem triplin).mp3` | `_NEEDS CHECK/R&B/sailorr, eem triplin - coconut (feat. eem triplin).mp3` | R&B | existing genre tag: R&B |
| `_NEEDS CHECK/tyler, the creator - don't tap that glass _ tweakin'.mp3` | `_NEEDS CHECK/Hip-Hop/tyler, the creator - don't tap that glass _ tweakin'.mp3` | Hip-Hop | existing genre tag: Rap |
| `_NEEDS CHECK/tyler, the creator - don't you worry baby (feat. madison mcferrin).mp3` | `_NEEDS CHECK/Hip-Hop/tyler, the creator - don't you worry baby (feat. madison mcferrin).mp3` | Hip-Hop | existing genre tag: Rap |
| `_NEEDS CHECK/tyler, the creator - ring ring ring.mp3` | `_NEEDS CHECK/Hip-Hop/tyler, the creator - ring ring ring.mp3` | Hip-Hop | existing genre tag: Rap |
| `_NEEDS CHECK/wale, leon thomas - watching us.mp3` | `_NEEDS CHECK/Hip-Hop/wale, leon thomas - watching us.mp3` | Hip-Hop | existing genre tag: Hip Hop |

## Apply safeguards (after approval only)

- Create a fresh pre-genre backup and verify it.
- Preserve each file's audio payload and Serato frames.
- Preserve the original ID3 version while changing only the Genre field.
- Re-check SHA/audio hashes and tag values after every write.
- Write a complete move/tag undo log.
- Delete nothing.
