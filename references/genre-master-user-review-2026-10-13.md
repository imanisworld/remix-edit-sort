# Genre master reconciliation — user review, 2026-10-13

**Status: DECISIONS CONFIRMED BY THE USER. This record is source-only; it never authorizes rewriting audio metadata or renaming folders.**

The sheet paste below had no saved radio selections. After the paste, the user answered the open items **directly**, as multiple-choice questions in the Claude session that built the sheet (2026-10-13). Those direct answers are authoritative and are recorded in the table. Where an item had only a typed note, Claude's reading of the note was shown back to the user with "correct any of these"; none were corrected. Those rows say so.

Scope: `references/genre-learner-rules.json` (68 canonical genres at review time, 76 after), `references/genre-rules.json`, `references/version-genre-rules.json`. The learner list is the single master Genre list.

## Decisions

"Direct answer" = the user picked this option themselves. "Note, read back" = Claude's reading of the typed note, shown to the user and not corrected.

| # | Topic | Decision | Source |
|---|---|---|---|
| 01 | Ghetto Tech | → **Ghettotech** | Direct answer ("Approve all four") |
| 02 | Afrobeat vs Afrobeats | Master genre renamed **Afrobeats** (the modern sound). Fela-era Afrobeat is added only if the library holds such records. A bare legacy `Afrobeat` tag stays held for review: it may be Fela-era. | Direct answer ("Rename to Afrobeats") |
| 03 | Afro Tech | **Afro Tech** is its own master genre, distinct from Afro House. | Direct answer |
| 04 | Baile Funk | → **Baile** | Direct answer ("Approve all four") |
| 05 | UKG | → **UK Garage** | Direct answer ("Approve all four") |
| 06 | speed garage / bassline keywords | Route to **Speed Garage** and **Bassline** respectively. | Direct answer ("Approve all four") |
| 07 | Garage (incl. "Garage Volume N") | Mixed; **needs check**, never guessed. | Direct answer ("Mixed / needs check") |
| 08 | Funk / Soul / Boogie | **Keep merged as Funk & Soul.** | Direct answer ("Keep merged: Funk & Soul") |
| 09 | Vogue / Ballroom | **Ballroom Vogue**: no slash, because the learner splits Genre text on `/`. | Direct answer ("Ballroom Vogue") |
| 10 | Bass | Not a genre; Bassline → Bassline, anything else needs check. | Note, read back |
| 11 | Club | Not a genre. Regional club styles keep their exact genres; "club mix/edit" is a version label; `Club` goes to Grouping for club-ready tracks; a bare "club" needs check. | Direct answer ("Retire + Grouping") |
| 12 | Drum | Not a genre; any track tagged Drum needs check. | Note, read back |
| 13 | Dub | Both: "dub mix / dub version / dubplate" are version labels, **and** Dub is a master genre for actual Jamaican dub records. A bare legacy `Dub` tag stays held for review (it may be a dub mix). | Direct answer ("Both") |
| 14 | Tech House | Its own master genre. | Note, read back |
| 15 | Jazzy House | Genre **Deep House** + Grouping **Jazzy**. | Direct answer |
| 16 | Nu Disco | → Disco | Note, read back ("sure") |
| 17 | Nu R&B | → Alternative R&B | Note, read back ("sure") |
| 18 | R&B Throwbacks | Genre **R&B** + Grouping **Throwback**. | Note, read back |
| 19 | Alternative Hip-Hop | Genre **Hip-Hop** + Grouping **Alternative**. | Note, read back |
| 20 | Classic / Soft / Pop Rock | **Three separate master genres** ("apart", not "a part"). | Direct answer ("Apart: separate genres") |
| 21 | Latin Pop | Its own master genre. | Note, read back |
| 22 | Garage Jungle | Needs check; it marks uncertainty, not a hybrid genre. | Note, read back |
| 23 | Blues | Added as a master genre. | Note, read back ("need to add") |
| 24 | Dance | Not a genre; Grouping `Dance`. | Note, read back |
| 25 | Twerk | Grouping, not Genre: the dancefloor purpose. Genre stays the track's actual style. | Direct answer ("Grouping") |
| 26 | Afro Baile / Groovy Bounce / Sexy Drill | Keep in master. | Note, read back ("they count") |
| 27 | Hardgroove | Keep as its own genre. | Note, read back ("right") |
| — | Folders | Tag names and folder names are separate (`folder_names`); folder renames are a separate gated job. | Direct answer ("Separate tag from folder") |

**Pipeline note:** the learner records Genre + Grouping for items 15, 18 and 19. The version/genre sorter has no Grouping output yet, so it holds those three tags for review instead of writing only the Genre and dropping the descriptor.

## User's actual typed comments (verbatim; blank means none supplied)

01. _(blank)_
02. _(blank)_
03. `afro house and afro tech are slightly different`
04. _(blank)_
05. _(blank)_
06. _(blank)_
07. _(blank)_
08. _(blank)_
09. `We can change the name for Ballroom/Vogue`
10. `Bass - can mean any of these  (Miami Bass, Atlanta Bass, bass house, a word in a title)`
11. `Club meaning either jersey or b more and all that vs play at the club do need to prob fix this`
12. `drum idk`
13. `dub yeah needs more`
14. `tech house is different than just house`
15. `jazzy house is more of how it sounds like the instruments that you hear`
16. `sure`
17. `sure`
18. `sure maybe move throwback to the grouping`
19. `alt maybe move to the grouping`
20. `classic soft pop are in my mind apart of the genre`
21. `latin pop is the genre`
22. `this just means i couldnt really tell the difference so i put both`
23. `need to add`
24. `yeah i think its just a vague cat when i dont feel like figuring out exactly .. maybe a better grouping`
25. `this is specificly meaning i want people to shake ass ... not commanly understood because it is black`
26. `they count`
27. `right`

## Source discrepancies independently verified at review time

- `genre-learner-rules.json` aliases `afrobeats` to `Afrobeat`, and only includes `Afrobeat` in its canonical list; this needs the unresolved #02 decision.
- `genre-rules.json` sends `afro house` and `afrotech` to `Afrobeats`, contradicting the learner's distinct `Afro House` and user's #03 distinction.
- `genre-rules.json` sends `speed garage` to `UKG` and `bassline` to generic `Bass`; the learner has both separately.
- `version-genre-rules.json` sends `atl bass`, `atlanta bass`, and `atl bass, old head party` to `Miami Bass`; `filename_keywords` also sends `atl bass` to `Miami Bass`. The learner explicitly lists `Atlanta Bass` and `Miami Bass` separately. Do not carry this mismatch forward unexamined.
- `version-genre-rules.json` maps `garage jungle` to a putative `Garage Jungle`, despite user's #22 confirming it denotes uncertainty.
- `genre-learner-rules.json` already includes `Twerk` under `context_tokens_not_genre`, while `version-genre-rules.json` still maps `twerk` to Genre `Twerk`; the intended grouping semantics are not consistently represented.
- `genre-rules.json` includes `Dub` as Genre **and** `Dub` under `remix_types`; source evidence and rule context must decide which one applies.
- `genre-rules.json` ordered keyword behavior is **first match wins**. Any replacement must check precedence and avoid broad tokens swallowing distinct styles.

## Safe next implementation pass (historical, written before the direct answers)

1. Add coherent canonical/alias/grouping support for **explicit** user choices (#09, #14, #18, #21, #23, #25, #26, #27); retain finer distinctions and evidence. Keep open calls (#02, #07, #08, #12, #13, #15) unresolved. Do not assume blank answers approve anything.
2. Fix corroborated cross-rule routing errors such as `Atlanta Bass → Miami Bass` and `Afro House → Afrobeats`, with narrowly scoped test fixtures that demonstrate old failure / new behavior.
3. Identify whether target pipelines support Grouping, multiple genre values and unknown/needs-check states before altering mappings. Match those semantics rather than inserting pseudo-genres.
4. Show before/after examples and validate JSON, rule precedence, learner ontology consistency, relevant tests and a **source-only** diff. No tagging, moves, Serato writes, backups, cloud deployment, or auto-merge.
5. Before any eventual library mutation, follow `SAFETY-MODEL.md` including per-folder lock, backup PASS evidence, transaction engine and separate approval. No action on library records from this review alone.

This document intentionally does not modify classification rules or equate notes with checked radio controls.
