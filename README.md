# Remix/Edit Music Sort

Safety-first tools and reference rules for organizing a DJ music library by version and genre while preserving DJ metadata.

## What belongs in this repository

- Sorting and audit scripts
- Genre and naming rules
- Skill instructions
- Generic documentation and tests

## What must stay out of Git

- Music and video files
- Serato databases and crates
- Library inventories containing personal paths
- Backups, undo payloads, and per-run reports
- Local credentials or configuration

## Required workflow

1. Survey one source folder read-only.
2. Produce and review a preview plan.
3. Preserve meaningful variants such as Clean, Dirty, Intro, Remix, Edit, Acapella, and Instrumental.
4. Never deduplicate from filenames alone.
5. Make and verify scoped music and Serato backups.
6. Apply only after explicit approval.
7. Verify audio payloads and embedded Serato frames.
8. Write an undo log and completion report.

`Genre` records musical style. `Grouping` records evidenced event, crate, or source context. Existing metadata is preserved unless a reviewed plan explicitly changes it.

## Current local handoff

The working-library handoff and run artifacts intentionally remain outside this repository under the Codex visualization workspace. They may contain absolute paths and library-specific filenames.
