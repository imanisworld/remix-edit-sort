# VDBL merge into main library -- preview (build only, not applied)

141 tracks planned to move. 0 files moved yet -- this is the preview from `build_vdbl_merge_plan.py`, generated read-only against the current library state.

Excluded from this plan entirely (left in VDBL for separate manual review, unrelated to this merge): 4 tracks in `CLEAN/_GENRE NEEDS CHECK` (genre unresolved), 5 tracks in `_NEEDS CHECK/R&B` (version unresolved -- part of the same Dead Silence Pack).

## Destination breakdown

By genre (destination root):
- Amapiano: 10
- Country: 1
- Hip-Hop: 127
- R&B: 3

By VDBL version bucket (source):
- ACAPELLAS: 1
- CLEAN: 75
- DIRTY: 62
- INSTRUMENTALS: 3

Destination paths:
- Hip-Hop -> `Hip Hop/<Clean|DIRTY|INSTRUMENTALS|ACAPELLAS>/Hip-Hop/<file>` (mirrors VDBL's own structure)
- R&B -> `R&b/<file>` (flat, version stays in filename)
- Amapiano -> `Amapiano /<file>` (flat)
- Country -> `Country/<file>` (flat, new folder)

## TDRC year tag writes

- Confirmed evidence, year will be written: 127
- No evidence (Dead Silence Pack, 3 tracks that did resolve a Clean version), year left blank: 3

## Collision / duplicate check against destination folders

- Exact-hash duplicate already at destination (would be skipped, not overwritten): 0
- Same filename, different content (needs manual resolution before apply): 0
- Clean moves (no filename collision at all): 141

## Possible title overlaps -- advisory only, 9 of 141 tracks flagged

Normalized-title heuristic match (brackets/parens stripped, Clean/Dirty/Intro/feat words ignored) against existing files already in the same destination genre folder. This is **not** a duplicate verdict -- per this project's own rules, Intro/Clean/Dirty edits are distinct, intentional DJ assets and are never auto-deduped. Shown purely so these can be glanced at before applying, not to block the merge.

- `. Tyla - Art (Clean).mp3` -> looks similar to: `Tyla - ART.mp3` (similarity 1.0)
- `. Tyla - Priorities (Clean).mp3` -> looks similar to: `Tyla - Priorities.mp3` (similarity 1.0)
- `. Tyla feat. Becky G - On My Body (Clean).mp3` -> looks similar to: `Tyla, Becky G - On My Body.mp3` (similarity 1.0)
- `Hitkidd & Juicy J feat Gloss Up - Freak Junt (Ext) (Clean).mp3` -> looks similar to: `HitKidd ft Juicy J & Gloss Up - Freak Junt (Ext) (Intro Clean).mp3` (similarity 1.0)
- `Sexyy Red & Drake - U My Everything (Super Clean).mp3` -> looks similar to: `sexyy red - u my everything (feat. drake).mp3` (similarity 0.8); `Sexyy Red ft Drake - U My Everything (Intro Dirty).mp3` (similarity 1.0); `Sexyy Red ft Drake - U My Everything (Intro Clean).mp3` (similarity 1.0)
- `Hitkidd & Juicy J feat Gloss Up - Freak Junt (Ext) (Dirty).mp3` -> looks similar to: `HitKidd ft Juicy J & Gloss Up - Freak Junt (Ext) (Intro Clean).mp3` (similarity 1.0)
- `Sexyy Red & Drake - U My Everything (Dirty).mp3` -> looks similar to: `sexyy red - u my everything (feat. drake).mp3` (similarity 0.8); `Sexyy Red ft Drake - U My Everything (Intro Dirty).mp3` (similarity 1.0); `Sexyy Red ft Drake - U My Everything (Intro Clean).mp3` (similarity 1.0)
- `Travis Scott feat. Playboi Carti - FE!N (Dirty).mp3` -> looks similar to: `Travis Scott ft Playboi Carti - FE!N (Intro Dirty).mp3` (similarity 1.0); `Travis Scott ft Playboi Carti - FE!N (Intro Clean).mp3` (similarity 1.0)
- `Veeze - Not a Drill (Dirty).mp3` -> looks similar to: `Veeze - Not a Drill (Intro Dirty).mp3` (similarity 1.0); `Veeze - Not a Drill (Intro Clean).mp3` (similarity 1.0)
