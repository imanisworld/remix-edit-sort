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

## Continue with Claude Code

Open this repository in Claude Code. It will automatically read `CLAUDE.md`, which points to the consolidated handoff and current pending work.

The reusable standalone prompt is available at `music-sort-prompt.md`. Session-specific scripts and reports are retained under `handoff/session-2026-08-15/` for audit and continuation; they contain local paths and should be generalized before reuse on another library.

## Repository map

- `SKILL.md` — core agent workflow and safety rules
- `agents/openai.yaml` — Codex skill-list metadata
- `scripts/` — reusable, parameterized sorting tools
- `references/` — genre, alias, casing, and troubleshooting knowledge
- `music-sort-prompt.md` — standalone prompt when the skill cannot be installed
- `CLAUDE.md` — automatic continuation instructions for Claude Code
- `handoff/HANDOFF-DJ-MUSIC-SORT.md` — current library state and next actions
- `handoff/ARTIFACTS.md` — what is and is not mirrored from `.codex`
- `handoff/session-2026-08-15/survey/` — complete text-only plans, inventories, results, undo manifests, reports, and historical scripts

The repository intentionally contains no DJ audio, video, Serato database, or media backup payloads.
