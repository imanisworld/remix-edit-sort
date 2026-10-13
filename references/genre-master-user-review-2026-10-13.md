# Genre master reconciliation — user review, 2026-10-13

**Status: SOURCE-ONLY REVIEW RECORD; NO CLASSIFICATION RULES OR MUSIC FILES CHANGED.**

This record captures comments typed into a downloaded Claude "Genre Master Review" artifact. Its UI reported `Not saved: storage isn't available in this view.` The visible paste includes text comments, but **no reliable checked-radio selections**, so comments such as "sure", "right" and "need to add" are evidence of intent; an empty comment must not be construed as approval.

Scope: `references/genre-learner-rules.json` (68 canonical genres at review time), `references/genre-rules.json`, `references/version-genre-rules.json`. The learner list is the proposed canonical source of truth. This is a reconciliation record, not an authorization to rewrite existing audio metadata or folder names.

## Decisions and outstanding questions

| # | Topic | Reconciliation from user's words | Status |
|---|---|---|---|
| 01 | Ghetto Tech → Ghettotech | Proposed alias/normalization to the existing learner spelling; no written response. | Proposed; not expressly approved |
| 02 | Afrobeat vs Afrobeats | Separate historical Afrobeat from contemporary Afrobeats if the evidence supports both; no user selection recorded. Do not silently merge. | Open |
| 03 | Afro House / Afro Tech | User distinguishes Afro House and Afro Tech; do **not** route `afrotech` blindly to Afro House or Afrobeats. Decide appropriate canonical label based on actual examples. | User distinction explicit; mapping open |
| 04 | Baile Funk → Baile | Proposed alias to learner's Baile; no written response. | Proposed; not expressly approved |
| 05 | UKG → UK Garage | Proposed alias to learner's UK Garage; no written response. | Proposed; not expressly approved |
| 06 | Speed Garage / Bassline | These are **two separate existing learner genres**; route each keyword to its own genre. No written response. | Proposed; not expressly approved |
| 07 | Garage | `Garage` alone may mean UK Garage or Garage House; compilation names require evidence. No user response. | Open / needs-check |
| 08 | Funk, Soul, Boogie | Master currently groups `Funk & Soul`; user didn't select whether to merge or split. Preserve provenance and don't force a destructive merge. | Open |
| 09 | Vogue / Ballroom | User says to change name to `Ballroom/Vogue`. Retain aliases `Vogue` and `Ballroom`; check implications for the master, families, pipelines and BPM keys. | User-endorsed label |
| 10 | Bass | Generic `Bass` is ambiguous among Miami Bass, Atlanta Bass, Bass House and non-genre title text; explicit specific styles should remain distinct. | User confirms ambiguity |
| 11 | Club | `Club` may refer to Jersey/Baltimore/other club styles or simply a track for club play. Preserve style vs usage/version separately. | User confirms ambiguity |
| 12 | Drum | User doesn't know which genre `Drum` denotes; investigate examples, avoid guessing. | Open / needs-check |
| 13 | Dub | User requests more detail; distinguish Jamaican Dub as a genre from `dub mix` / `dub version` version labels and other dub styles. Do not retire genuine Dub automatically. | Open |
| 14 | Tech House | User explicitly says Tech House differs from House; do not collapse into House. | Distinct canonical genre intended |
| 15 | Jazzy House | User describes jazzy instrumentation/sound; distinguish sonic descriptor from an evidenced genre. Avoid automatic mapping to Deep House. | Grouping/descriptor candidate; needs evidence |
| 16 | Nu Disco | User replied "sure" to the proposal to map to Disco. Preserve source terms where helpful. | Tentative approval of alias |
| 17 | Nu R&B | User replied "sure" to the proposal to map to Alternative R&B. | Tentative approval of alias |
| 18 | R&B Throwbacks | User says "sure maybe move throwback to the grouping": Genre `R&B`, retain `Throwback` as grouping/era context. | Endorsed with grouping caveat |
| 19 | Alternative Hip-Hop | User says "alt maybe move to the grouping": Genre `Hip-Hop` plus evidenced `Alternative` grouping is a candidate; preserve distinction until checked. | Tentative |
| 20 | Classic Rock / Soft Rock / Pop Rock | User considers "classic soft pop" part of the Rock genre. Parent `Rock` may be appropriate but preserve descriptive subgenre/context and verify Pop Rock edge cases. | User favors Rock family; precise mapping open |
| 21 | Latin Pop | User explicitly calls Latin Pop a genre; preserve as a distinct canonical genre. | Distinct canonical genre intended |
| 22 | Garage Jungle | User says they used both words because they couldn't tell the styles apart; this is a **needs-review** marker, not a new hybrid genre. | Confirmed needs-check |
| 23 | Blues | User says "need to add"; add as a distinct canonical genre, not Funk & Soul. | Explicit addition |
| 24 | Dance | User calls it a vague catch-all when they didn't determine style; consider `Grouping` / review queue without inventing a genre. | User confirms broad context; exact behavior open |
| 25 | Twerk | User means the **dancefloor intention of getting people to shake ass**, a culturally meaningful crate/purpose term; don't discard it or misclassify every track as Bounce/Club. Retain as separate evidenced grouping/usage, while Genre reflects musical style. | Explicit grouping semantics |
| 26 | Afro Baile / Groovy Bounce / Sexy Drill | User says "they count"; keep all three canonical learner genres. | Explicit keep |
| 27 | Hardgroove | User says "right"; keep Hardgroove separate. | Explicit keep |

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

## Safe next implementation pass — a separate reviewed diff

1. Add coherent canonical/alias/grouping support for **explicit** user choices (#09, #14, #18, #21, #23, #25, #26, #27); retain finer distinctions and evidence. Keep open calls (#02, #07, #08, #12, #13, #15) unresolved. Do not assume blank answers approve anything.
2. Fix corroborated cross-rule routing errors such as `Atlanta Bass → Miami Bass` and `Afro House → Afrobeats`, with narrowly scoped test fixtures that demonstrate old failure / new behavior.
3. Identify whether target pipelines support Grouping, multiple genre values and unknown/needs-check states before altering mappings. Match those semantics rather than inserting pseudo-genres.
4. Show before/after examples and validate JSON, rule precedence, learner ontology consistency, relevant tests and a **source-only** diff. No tagging, moves, Serato writes, backups, cloud deployment, or auto-merge.
5. Before any eventual library mutation, follow `SAFETY-MODEL.md` including per-folder lock, backup PASS evidence, transaction engine and separate approval. No action on library records from this review alone.

This document intentionally does not modify classification rules or equate notes with checked radio controls.
