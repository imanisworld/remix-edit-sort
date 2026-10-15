# Agent instructions

Read LIBRARY-STATUS.md first. Any assistant that changes the library or this repo updates it before stopping, and commits it.

These rules apply to every coding agent working in this repository: Codex, Cursor, Claude, ChatGPT, or any other.

`CLAUDE.md` is the full instruction file. Despite its name, it is written for all agents. Read it completely before changing anything. Then read, in order:

1. `SAFETY-MODEL.md`: how mutations work. If any doc contradicts it, it wins.
2. `SKILL.md`
3. `handoff/HANDOFF-DJ-MUSIC-SORT.md` and `handoff/CURRENT-STATUS.md`

## Rules that never bend

- Every library mutation goes through `scripts/mutation_engine.py`, with a lock and a PASS backup manifest. No script renames, tags or deletes on its own.
- Show a preview and get explicit approval before any move or metadata write.
- `EXACT_DUPLICATE` means identical SHA-256 and nothing else.
- Never guess a genre, artist, title, event or version. Use needs-check.
- `handoff/` is historical evidence and never executable.
- Several agents work this library at once. Check and acquire folder locks (`scripts/folder_lock.py`) before touching a source folder.
- This repository is private. Never publish it or add a public remote.

## Genres

- The single master Genre list is `references/genre-learner-rules.json` → `canonical_genres`.
- `references/genre-atlas/` links each master Genre to the World Music Atlas (origin, era, roots, parent genres). Read `references/genre-atlas/README.md` before using it. The atlas describes genres, never tracks: it is never evidence for a tag and never training data.
- `python3 scripts/genre_atlas.py check` must pass.

## Before you push

```bash
python -m compileall -q scripts tests
python scripts/mutation_surface.py
python -m unittest discover -s tests
```
