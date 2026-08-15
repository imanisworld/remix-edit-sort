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

## Whole-library audit: verified read-only first

Before another cleanup pass, establish the physical library inventory without changing source audio. The required entry point is the verified wrapper:

```bash
python3 scripts/run_verified_audit.py "/path/to/DJ-Music/Music" --repo-sha "$(git rev-parse HEAD)"
```

The wrapper fingerprints every source audio file before and after the audit using path, size, nanosecond mtime, and SHA-256. It fails closed if any source file is added, removed, or changed while the audit is running. Each run receives a UTC run ID and writes a local run manifest containing the repository SHA, source counts/bytes, audit exit status, source-verification result, detected changes, and SHA-256 hashes of generated reports.

The underlying `audit_library.py` reads metadata and codec information, computes SHA-256 hashes, and writes reports under the verified run directory. Generated output is intentionally ignored by Git because it can contain absolute paths and library-specific filenames.

It produces:

- `inventory.csv` — one row per physical audio file;
- `summary.json` — file/storage/format/metadata-quality counts;
- `exact_duplicates.csv` — byte-identical files only;
- `metadata_review_candidates.csv` — same normalized Artist + Title, for human review only;
- `errors.csv` — unreadable or unrecognized files that did not abort the scan;
- `source-before.json` / `source-after.json` — cryptographic source manifests;
- `run-manifest.json` — immutable per-run evidence and report hashes.

Exact SHA-256 matches prove byte identity, not that a copy should be deleted. Metadata-only matches are never deletion evidence. See `references/library-audit.md`.

## External research and tag evidence

Release-year, event, artist/title, genre, and similar research follows `references/research-evidence-policy.md`:

- batch independent searches when possible;
- make one reasonable attempt for obscure/local material, then record `unconfirmable` rather than forcing a guess;
- separate primary/secondary confirmation, local evidence, inference, unknown, and unconfirmable states;
- retain source URLs and evidence notes so later tracks can reuse prior research;
- show the proposed label/value breakdown before any metadata write, including tags-only passes.

Reusable release-year decisions live in `references/year-evidence.json` and are validated by `scripts/validate_year_evidence.py`.

Run the safety tests with:

```bash
python3 -m unittest discover -s tests -v
python3 scripts/validate_year_evidence.py references/year-evidence.json
```

GitHub Actions runs the same checks on pull requests and `main`, including a guard against source-mutation primitives in the read-only audit scripts.

## Required workflow

1. Survey one source folder read-only.
2. Produce and review a preview plan and proposed label/value breakdown.
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
- `scripts/` — reusable, parameterized sorting, audit, verification, and evidence-validation tools. Several pipelines live here:
  - `scan_library.py` / `build_plan.py` / `apply_plan.py` / `detect_bpm.py` / `reclassify.py` / `standardize_names.py` — the Genre/Remixer pipeline for flat SoundCloud/DJ-pool downloads, documented in `SKILL.md`. Legacy; superseded by the Version+Genre pipeline below for library-folder cleanup.
  - `inventory_library.py` / `build_version_genre_plan.py` / `apply_version_genre_plan.py` — the Version+Genre pipeline actually used for the library cleanup in `handoff/`, generalized from nine near-duplicate per-folder scripts. Use this for the next source folder instead of writing a new one-off apply script.
  - `build_grouping_plan.py` / `apply_grouping_plan.py` and `build_artist_title_plan.py` / `apply_artist_title_plan.py` — the metadata-completeness pass, run against folders already sorted by the pipeline above.
  - `audit_library.py` / `run_verified_audit.py` — the read-only whole-library audit; `run_verified_audit.py` is the required entry point (see above).
  - `validate_year_evidence.py` — validates `references/year-evidence.json` / `references/release-year-evidence.json` entries have a source and evidence grade.
  - `folder_lock.py` — **run this before starting work on any source folder.** Multiple agent sessions (Claude, Codex) have worked this library concurrently with no coordination, which has already caused a real incident (see `CLAUDE.md`). `acquire`/`release`/`check`/`status` — locks live in the gitignored `.locks/`.
- `tests/` — source-preservation and audit safety tests
- `references/` — genre, alias, casing, audit, research-evidence, and troubleshooting knowledge
- `music-sort-prompt.md` — standalone prompt when the skill cannot be installed
- `CLAUDE.md` — automatic continuation instructions for Claude Code
- `handoff/HANDOFF-DJ-MUSIC-SORT.md` — current library state and next actions
- `handoff/ARTIFACTS.md` — what is and is not mirrored from `.codex`
- `handoff/session-2026-08-15/survey/` — complete text-only plans, inventories, results, undo manifests, reports, and historical scripts

The repository intentionally contains no DJ audio, video, Serato database, or media backup payloads.
