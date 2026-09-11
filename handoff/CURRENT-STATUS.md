# Current DJ library project status

Date: 2026-09-10

This file is the concise continuation point for the active repository state. Older files under `handoff/` remain historical evidence and are not executable.

## Reviewed repository changes

The reviewed integration contains these completed safety and taxonomy changes on top of the already-merged ID3v2.2, reissue-year, and year-write work:

- **Serato path rewrite:** explicit, plan-gated `pfil`/`ptrk` rewrites only. Target existence uses the mutation engine's literal directory-entry check, so an APFS case alias cannot make `Ghetto Tech` -> `Ghettotech` look complete before the literal rename exists. The lane still requires Serato closed, the Serato lock, verified backup evidence, write-ahead recovery, and post-checks.
- **RVA2 / unserializable ID3 safety:** a real Mutagen `SpecError` becomes a preservation skip only after SHA-256 proves the source file is unchanged. If bytes changed before the serializer error, the item is marked mutated, fails, and uses the normal verified rollback path.
- **Recovery wording/state:** an exception after intent but before any mutation is reported as `ABORTED_BEFORE_MUTATION`, not as a failed rollback. A real failed restore remains `ROLLBACK_FAILED`.
- **Learner taxonomy:** the previously mixed learner branch was isolated and the taxonomy decisions were completed on current `main`. Afro House and Garage House remain distinct canonical Genres; `Breakbeat` normalizes to `Breaks`; `Nu Wave` normalizes to canonical `New Wave`; `World` is Grouping/context rather than Genre. The learner proposal builder and behavior tests are included, while the stale generated 2026-09-10 proposal snapshot is intentionally excluded.

The learner work was integrated separately from the RVA2 safety fix and tested against the combined current repository. No stale README or generated proposal-review artifacts were carried forward.

No real music files, Serato database, crates, or local library metadata were changed by these repository integrations.

## T5 SHA-256 backup deduplication

Repository support is implemented and tested. `scripts/t5_backup_dedupe.py` can build/refresh a full-file SHA-256 index, reuse an existing identical T5 backup only after re-hashing the concrete file, copy only unmatched content into the lane backup, and emit a verified manifest with an explicit concrete backup path for every source item. The mutation engine accepts that explicit path while remaining backward-compatible with legacy `backup_root/<relative>` manifests, and still re-hashes the selected backup immediately before any mutation.

The actual chain shell workflow is Mac-local/untracked; there is no chain shell script in this repository to patch. It must invoke the helper locally. The initial real T5 index and a real matched/unmatched dry proof are therefore still **Mac-local execution prerequisites**:

```bash
python3 scripts/t5_backup_dedupe.py index \
  --root /Volumes/T5 \
  --out ~/Documents/DJ-library-project-20260825/t5-backup-sha256-index.json

python3 scripts/t5_backup_dedupe.py prepare \
  --index ~/Documents/DJ-library-project-20260825/t5-backup-sha256-index.json \
  --source-root "<lane source root>" \
  --lane-backup-root "/Volumes/T5/<lane backup root>" \
  --manifest-out "<lane backup manifest.json>" \
  --report-out "<lane backup report.json>"
```

For a scoped plan, pass `--paths-json <json>` containing either a non-empty JSON list of source-relative paths or `{"paths": [...]}`. Matching is SHA-256 only; filenames and durations never authorize reuse. A missing or changed indexed path, a symlink, a hash mismatch, an index-integrity failure, or source drift fails closed.

No real music files, Serato database, crates, or local library metadata were changed while implementing or testing this repository support.

## Production safety boundary

Repository tests and merges are not authorization to mutate the live library. A real apply still requires the repository's normal proof gates: reviewed plan, coordination lock, verified per-file backup evidence, write-ahead recovery, and the applicable macOS/APFS canary checks. No proof, no run.
