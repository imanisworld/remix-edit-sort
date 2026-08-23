# Durable metadata batch runbook

This workflow reduces a proven MP3 metadata batch to preparation, one explicit
human approval, one approved apply, and a final manual Serato spot-check. It is
fail-closed. It does not authorize folder moves, filename changes, FLAC writes,
ID3v2.2 writes, Serato-file changes, or automatic backup cleanup.

## State machine

`DISCOVERED → PREVIEWED → HUMAN_APPROVED → PLANNED → PREFLIGHT_PASS → BACKED_UP → APPLIED → VERIFIED → SERATO_SPOTCHECK → COMPLETE`

Any failure becomes `BLOCKED` with a reason in `handoff/current-batch.json`.
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

The command scans only MP3 files beneath `_GENRE NEEDS CHECK`, uses current
tag evidence and the repository classifier, holds ambiguous or unsupported
cases, checks ID3 versions and production Smart Crate dependencies read-only,
rereads every selected file, and writes a new directory under
`handoff/batches/<batch-id>/` containing:

- `BATCH-PREVIEW.md`
- `batch-preview.csv`
- `master-manifest.json`
- `plans/Sxxx.json` (one immutable plan per immediate parent-folder backup scope)

The real `apply_new_music_intake_plan.load_and_validate()` validator must accept
every plan before preparation reports one `MASTER SHA`. `/private/tmp` is never
authoritative.

The automated lane is intentionally narrow: MP3 and ID3v2.3/v2.4 only. FLAC,
ID3v2.2, blank/Other/unmapped Genre, Radio Edit, Flip, ReWork, semantic alias
changes, conflicting evidence, listening, and external lookup remain held.
Existing Grouping order is preserved; controlled additions are appended in
Context → Version → Form → Transformation → Status order.

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
and ID3 versions recorded in the master manifest.

## 3. Apply the exact approved batch

```text
python3 scripts/apply_approved_batch.py --master-sha <MASTER_SHA>
```

Before any production write, the command refuses unless:

- state is exactly `HUMAN_APPROVED` and the durable approval matches;
- master SHA and embedded SHA match;
- repository HEAD equals the approved commit;
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
