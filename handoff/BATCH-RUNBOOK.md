# Durable metadata batch runbook

This workflow reduces a proven MP3 metadata batch to preparation, one explicit
human approval, one approved apply, and a final manual Serato spot-check. It is
fail-closed. It does not authorize folder moves, filename changes, FLAC writes,
ID3v2.2 writes, Serato-file changes, or automatic backup cleanup.

## State machine

`DISCOVERED → PREVIEWED → HUMAN_APPROVED → PLANNED → PREFLIGHT_PASS → BACKED_UP → APPLIED → VERIFIED → SERATO_SPOTCHECK → COMPLETE`

Any ordinary refusal/failure becomes `BLOCKED` with a reason in
`handoff/current-batch.json`. A failed automatic restore becomes the distinct,
more severe `ROLLBACK_FAILED` state; it must never be flattened into `BLOCKED`.
There is no automatic transition out of `BLOCKED`; a human must inspect the T5
evidence and resolve the failed run. A new batch is refused unless the current
batch is `COMPLETE`.

Preparation emits inert per-scope plan files while the batch is `PREVIEWED` so
the human-approved master SHA can bind their exact bytes. They become the
approved `PLANNED` artifacts only after durable approval and apply preflight.
Apply never regenerates them. Per-scope state is also recorded because backups,
applies, and verification run sequentially while the overall batch advances.

## 1. Prepare (read-only against music and Serato)

```text
python3 scripts/prepare_metadata_batch.py --format mp3 --max 150
```

Preparation has three discovery lanes sharing the identical preview, approval
binding, apply orchestration, and verification:

- `--lane genre` (default) scans only MP3 files beneath `_GENRE NEEDS CHECK`
  and proposes Genre normalization plus controlled Grouping additions.
- `--lane clean-dirty` scans the sorted library for deterministic Clean/Dirty
  Grouping backfill. Evidence is a literal `CLEAN`/`DIRTY` folder component
  (the unit `apply_clean_dirty_grouping.py` established) and/or an explicit
  bracketed label in the filename/title. Genre is never changed, existing
  Grouping is only appended to, conflicting evidence (folder vs label, or
  either vs an existing status term) is held, and `_GENRE NEEDS CHECK` /
  `_NEEDS CHECK` paths are left to the genre lane.
- `--lane intake --intake-root <folder>` runs new downloads (the root must be
  inside the library root) through the survey tooling's established
  deterministic rules: controlled Grouping terms from explicit wording, and a
  placeholder Genre filled only by unanimous tagged-sibling consensus that
  also resolves to a recognized canonical value. Everything else is held.

Every lane uses current tag evidence and the repository classifier, holds
ambiguous or unsupported cases, checks ID3 versions and production Smart
Crate dependencies read-only, rereads every selected file, and writes a new
directory under `handoff/batches/<batch-id>/` containing:

- `BATCH-PREVIEW.md`
- `batch-preview.csv`
- `master-manifest.json`
- `plans/Sxxx.json` (one immutable plan per immediate parent-folder backup scope)

The real `apply_new_music_intake_plan.load_and_validate()` validator must accept
every plan before preparation reports one `MASTER SHA`. `/private/tmp` is never
authoritative.

`--max` defaults to 150 and 150 stays the no-questions-asked ceiling. A larger
controlled batch is an explicit operator decision: state the exact limit twice
(`--max 400 --confirm-max 400`). There is no uncapped mode, selection ordering
stays deterministic, and every gate below applies unchanged at any size.

The automated lane is intentionally narrow: MP3 and ID3v2.3/v2.4 only. FLAC,
ID3v2.2, blank/Other/unmapped Genre, semantic alias changes, conflicting
evidence, listening, and external lookup remain held. Radio Edit, Flip, and
ReWork with an explicit bracketed label are written as those exact controlled
terms (never collapsed into `Edit`); their plain unbracketed word forms remain
held. Existing Grouping order is preserved; controlled additions are appended
in Context → Version → Form → Transformation → Status order.

## 2. Review and durably approve

Review the complete MD/CSV and master manifest. Chat text is not approval.
Record the human decision with the exact reported SHA:

```text
python3 scripts/prepare_metadata_batch.py \
  --approve <MASTER_SHA> \
  --approved-by "<human name>"
```

This creates `approval.json`. Its SHA reference binds the exact candidate
paths, source hashes, current/proposed metadata, repository commit, preview
hashes, plan hashes, backup scopes, audio-payload digests, Serato-frame digests,
ID3 versions, and the content hashes of every gated safety script/rule recorded
in the master manifest. The approval record also lists every per-scope plan SHA
explicitly. Plans exist and pass the real validator before approval, so approval
binds the executable plan bytes rather than an earlier abstract preview.

## 3. Apply the exact approved batch

```text
python3 scripts/apply_approved_batch.py --master-sha <MASTER_SHA>
```

Before any production write, the command refuses unless:

- state is exactly `HUMAN_APPROVED` and the durable approval matches;
- master SHA and embedded SHA match;
- repository HEAD equals the approved commit;
- every gated safety script/rule still has its approved content hash;
- all preview and plan hashes match, plan union is exact, and every plan passes
  the real existing validator;
- all live paths, file hashes, tags, ID3 versions, audio-payload digests, and
  Serato-frame digests still match the approved master;
- `/Volumes/T5 EVO` is mounted and every production backup/evidence path is
  confined below it;
- no lock exists for any required top-level folder. Stale or corrupt locks are
  blockers and are never silently reclaimed by this orchestrator.

Each immediate-parent scope then runs sequentially: acquire its top-folder
lock, copy the entire scope to a new T5-only destination, calculate and require
a PASS backup manifest, invoke the existing hardened mutation engine, verify
metadata/audio/Serato/ID3/recovery and the rest of the folder, then release the
owned lock. The first failure blocks the batch and prevents every remaining
scope from starting. T5 backup and evidence directories are never overwritten
or automatically deleted.

## 4. Manual Serato spot-check

After `VERIFIED`, use the generated `SERATO-SPOTCHECK.md` list in Serato. The
pipeline never opens or controls the Serato UI and never edits crate/database
files. Record the result explicitly:

```text
python3 scripts/apply_approved_batch.py \
  --master-sha <MASTER_SHA> \
  --record-serato-spotcheck PASS \
  --checked-by "<human name>"
```

Use `FAIL` instead of `PASS` if behavior is wrong; that transitions directly
to `BLOCKED`. Only a recorded PASS advances through `SERATO_SPOTCHECK` to
`COMPLETE`.

## Recovery and retention

If a run blocks, do not rerun or edit the plans. Inspect
`handoff/current-batch.json`, the per-scope result, undo log, and recovery
journal under `/Volumes/T5 EVO/DJ/Backups/remix-edit-sort/<batch-id>/`.
`mutation_engine.py recover --journal <path>` reports in-flight and failed
rollback state. Backup cleanup is always a separate, explicit human-authorized
operation; this pipeline has no cleanup command.

## Disposable acceptance matrix

`tests/test_metadata_batch_pipeline.py` exercises the pipeline on generated
fixtures only. Together with the existing mutation-engine suite it proves:

1. stale file SHA is refused;
2. stale metadata is refused independently;
3. wrong repository commit is refused;
4. modified plan and preview bytes are refused;
5. missing T5 mount is refused;
6. any backup/evidence path outside T5 is refused;
7. backup corruption after manifest creation is caught before mutation;
8. held, stale, and corrupt locks are not reclaimed by the orchestrator;
9. one path appearing in two per-scope plans is refused;
10. the real plan validator can independently veto an otherwise hash-bound plan;
11. an interrupted apply remains unclean and blocks later work;
12. a failed rollback raises `ROLLBACK_FAILED` and records manual-restore urgency.

The mutation-surface gate separately proves PREPARE has zero recognized
mutation primitives and the apply entry point remains an engine user. The
full test suite must pass before this workflow is trusted against production.
