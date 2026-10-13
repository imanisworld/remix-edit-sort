# Genre atlas: read this before using anything in this folder

This folder links the master Genre list to the **World Music Atlas**, a third-party genre reference. It is shared context for every model that works in this repo (Claude, Codex, ChatGPT, or any other). The rules here apply to all of them.

## What's here

| File | What it is | Edit it? |
|---|---|---|
| `atlas-snapshot.json` | Full atlas data as pulled on 2026-10-13: 16 regions, 5,047 genre cards. | Never by hand. Re-pull with `extract`. |
| `PROVENANCE.json` | Source link, retrieval date, SHA-256 hashes, field legend, known defects, use policy. | Only via `extract`. |
| `crosswalk-curated.json` | Hand-written links: each master Genre → atlas card path(s), with a match grade and a note. | **Yes. This is the only file people edit.** |
| `crosswalk.json` | Generated from the curated file, with each card's origin, era, roots, artists and example recordings attached. | Never. Run `build`. |

The master Genre list is `references/genre-learner-rules.json` → `canonical_genres`. The crosswalk must cover it exactly; `check` fails otherwise.

## Rules for any model using this

1. **The atlas describes genres, not tracks.** Nothing in it is evidence that a given file is a given genre. Never use it to propose, justify or write a Genre, Grouping, Artist, Year or BPM value on any track. Its evidence grade under `references/research-evidence-policy.md` is `unknown`.
2. **Never use it as learner training data.** It must not enter `genre_learner.py`, external manifests, or holdout sets.
3. **Never link by name similarity.** The atlas card "Riddim" is 2014 dubstep; the master's Riddim is dancehall riddim. Its keyword lists are noisy: "twerk" substring-matches "Kraftwerk". Every link is written by hand and graded `exact`, `close`, `uncertain` or `none`.
4. **`uncertain` and `none` are open questions, not answers.** Don't resolve them yourself. Ask Brooke.
5. **The library wins.** When the atlas disagrees with how Brooke crates or names a style (Hardgroove kept standalone, Afro Baile / Groovy Bounce / Sexy Drill with no atlas card), the master list stands.
6. **Atlas dates are approximate and sometimes contradictory.** 131 genres appear more than once with different eras. Quote a card's era as "the atlas says", never as fact.
7. **Links are proposals until accepted.** `genre-learner-rules.json` → `external_reference_policy` requires provenance and explicit acceptance. The curated file's `status` says where that stands.

## Commands

```bash
python3 scripts/genre_atlas.py check    # integrity + coverage; also runs in CI via tests/test_genre_atlas.py
python3 scripts/genre_atlas.py build    # after editing crosswalk-curated.json or the master list
python3 scripts/genre_atlas.py extract --html <saved atlas page> \
  --source-url https://claude.ai/artifact/Q9M3vDxtAEwECRwAmEoGgf --retrieved-at YYYY-MM-DD
```

`extract` replaces the snapshot and provenance. A changed snapshot hash means a new atlas version; re-run `build` and review the crosswalk diff before committing.

## Changing the crosswalk

1. Edit `crosswalk-curated.json`.
2. Run `build`, then `check`.
3. Commit the curated file and the regenerated `crosswalk.json` together.

When the master Genre list changes (a genre added, renamed or retired), `check` fails until the curated file matches. That is intended.

## Open items

- Genre Master Review decisions (2026-10-13) are applied to the master list. Afrobeat was renamed Afrobeats; Ballroom was renamed Ballroom Vogue (no slash, because the learner splits Genre text on `/`). Added: Tech House, Afro Tech, Latin Pop, Blues, Dub, Classic Rock, Soft Rock, Pop Rock. Twerk, Dance and Club are Grouping, not Genre. Garage, Garage Jungle, Bass and Drum need a check and are never guessed. `tests/test_learner_taxonomy_20261013.py` locks these in.
- `genre-rules.json` and `version-genre-rules.json` still use the old names. Both use the genre name as the destination folder name, so renaming there changes where files get sorted. That waits on a folder decision.
- No license is stated for the atlas. Keep this repository private, as `CLAUDE.md` already requires.
