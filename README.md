# Remix/Edit Music Sort

Safety-first tools and reference rules for organizing a DJ music library by version and genre while preserving DJ metadata.

## Start here: `SAFETY-MODEL.md`

`SAFETY-MODEL.md` is the authoritative one-page statement of how mutation works in this
repository. If any other document contradicts it, it wins. In short:

- **ALL mutations go through one approved engine**, `scripts/mutation_engine.py`.
- **A folder lock is mandatory** for every `--apply` and `--undo`.
- **A heartbeat re-verifies lock ownership before EVERY mutation**, not once at startup.
- **A PASS backup manifest is mandatory** — a directory existing is not proof of a backup.
- **Write-ahead recovery exists**: intent is fsync'd to a journal before each mutation.
- **No automatic deletion.** Nothing in this repository deletes on its own.
- **`EXACT_DUPLICATE` means identical SHA-256 only.** Similarity is REVIEW at most.
- **`handoff/` is historical evidence, never production executable code.**
- **Read-only tooling requires no mutation lock.**
- **A Mac canary run is required before the first production apply** — see `MAC-CANARY.md`,
  because CI runs Linux and the library lives on macOS/APFS.

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

## Proving a backup before any apply

```bash
python3 scripts/backup_manifest.py verify \
    --source-root "/path/to/Music/Hip Hop" \
    --backup "/path/to/backups/hip-hop-20260816" \
    --serato-backup "/path/to/_Serato_backup" --require-serato \
    --out backup-manifest.json
```

This walks both trees, hashes every file, and computes PASS or FAIL from what it finds.
There is deliberately no flag that stamps PASS onto a directory without hashing it. Every
apply engine refuses to run without a manifest that says PASS, covers the exact source root
it is about to mutate, is unaltered since it was written, and carries per-file evidence for
each file it touches. Immediately before each mutation, the shared engine also verifies that
the actual backup file still exists and still hashes to the source state recorded in the
manifest; the concrete backup path is written into the recovery intent.

## Recovering from an interrupted run

Every apply writes a fsync'd write-ahead journal. An item's intent record hits stable
storage *before* its mutation, and its completion record after, so a crash in between is
recoverable rather than mysterious:

```bash
python3 scripts/mutation_engine.py recover --journal <run>-recovery-<stamp>.jsonl
```

It reports any in-flight item with its source path, pre-mutation SHA-256, backup location,
and whether the physical write had already started.

## Tests

```bash
python3 -m unittest discover -s tests -v
python3 scripts/mutation_surface.py
python3 scripts/validate_year_evidence.py references/year-evidence.json
```

GitHub Actions runs the same checks on pull requests and `main`, on both Linux and macOS.
The central gate is `scripts/mutation_surface.py`: it walks the AST of every active module,
finds every mutation primitive, and requires each module carrying one to declare which
safety category it belongs to (`read-only`, `preview-only`, `mutation-engine`,
`engine-user`, `disabled-legacy`, `infrastructure`). CI fails if a new uncontrolled mutation
path is added, if a read-only audit script grows a mutation call, or if executable Python
reappears under `handoff/`.

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

- `SAFETY-MODEL.md` — **authoritative** mutation safety model; read this first
- `MAC-CANARY.md` — the required pre-production checklist for macOS/APFS behavior CI cannot prove
- `SKILL.md` — core agent workflow and safety rules
- `agents/openai.yaml` — Codex skill-list metadata
- `scripts/` — reusable, parameterized sorting, audit, verification, and evidence-validation tools. Several pipelines live here:
  - `mutation_engine.py` / `lock_guard.py` / `folder_lock.py` / `backup_manifest.py` — the shared safety core every mutation runs through. Read `SAFETY-MODEL.md` before touching any of them.
  - `duplicate_semantics.py` — the repository-wide duplicate truth model (EXACT_DUPLICATE / AUDIO_EQUIVALENT / RELATED_VERSION / REVIEW).
  - `mutation_surface.py` — the AST scan of every mutation path; enforced in CI.
  - `scan_library.py` / `build_plan.py` / `detect_bpm.py` — the read-only/preview half of the Genre/Remixer pipeline for flat SoundCloud/DJ-pool downloads, documented in `SKILL.md`. `apply_plan.py` is **hard-disabled** (read-only `--report` only); `reclassify.py` and `standardize_names.py` are **preview/plan builders only** — they no longer move or retag anything. Route those mutations through the engine family below.
  - `inventory_library.py` / `build_version_genre_plan.py` / `apply_version_genre_plan.py` — the Version+Genre pipeline actually used for the library cleanup in `handoff/`, generalized from nine near-duplicate per-folder scripts. Use this for the next source folder instead of writing a new one-off apply script.
  - `build_grouping_plan.py` / `apply_grouping_plan.py`, `build_artist_title_plan.py` / `apply_artist_title_plan.py`, `build_year_bpm_plan.py` / `apply_year_bpm_plan.py` — the metadata-completeness pass, run against folders already sorted by the pipeline above.
  - `delete_verified_duplicates.py` — the only deletion tool. Manual, human-approved pair list, and it refuses any pair that is not a proven EXACT_DUPLICATE (identical SHA-256).
  - `audit_library.py` / `run_verified_audit.py` — the read-only whole-library audit; `run_verified_audit.py` is the required entry point (see above).
  - `validate_year_evidence.py` — validates `references/year-evidence.json` / `references/release-year-evidence.json` entries have a source and evidence grade.
  - `folder_lock.py` — **run this before starting work on any source folder.** Multiple agent sessions (Claude, Codex) have worked this library concurrently with no coordination, which has already caused a real incident (see `CLAUDE.md`). `acquire`/`release`/`check`/`status` — locks live in the gitignored `.locks/`.
- `tests/` — source-preservation and audit safety tests
- `references/` — genre, alias, casing, audit, research-evidence, and troubleshooting knowledge
- `music-sort-prompt.md` — standalone prompt when the skill cannot be installed
- `CLAUDE.md` — automatic continuation instructions for Claude Code
- `handoff/HANDOFF-DJ-MUSIC-SORT.md` — current library state and next actions
- `handoff/ARTIFACTS.md` — what is and is not mirrored from `.codex`
- `handoff/ARCHIVED-CODE.md` — why `handoff/` holds no executable Python
- `handoff/session-2026-08-15/survey/` — complete text-only plans, inventories, results, undo manifests, reports, and historical scripts

The repository intentionally contains no DJ audio, video, Serato database, or media backup payloads.
