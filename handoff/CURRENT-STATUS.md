# Current DJ library project status

Date: 2026-09-10

This file is the concise continuation point for the active repository state. Older files under `handoff/` remain historical evidence and are not executable.

## Reviewed repository changes

The reviewed integration now contains these safety changes on top of the already-merged ID3v2.2, reissue-year, and year-write work:

- **Serato path rewrite:** explicit, plan-gated `pfil`/`ptrk` rewrites only. Target existence uses the mutation engine's literal directory-entry check, so an APFS case alias cannot make `Ghetto Tech` -> `Ghettotech` look complete before the literal rename exists. The lane still requires Serato closed, the Serato lock, verified backup evidence, write-ahead recovery, and post-checks.
- **RVA2 / unserializable ID3 safety:** a real Mutagen `SpecError` becomes a preservation skip only after SHA-256 proves the source file is unchanged. If bytes changed before the serializer error, the item is marked mutated, fails, and uses the normal verified rollback path.
- **Recovery wording/state:** an exception after intent but before any mutation is reported as `ABORTED_BEFORE_MUTATION`, not as a failed rollback. A real failed restore remains `ROLLBACK_FAILED`.

The RVA2 integration was isolated from the mixed learner branch. **No learner-taxonomy changes from that branch are included.**

No real music files, Serato database, crates, or local library metadata were changed by this repository integration.

## Still held

### Learner taxonomy

Do not merge the learner-rules work until the taxonomy pass is independently reviewed. The last review still required explicit resolution for:

- Afro House / Garage House treatment
- `Breakbeat` -> `Breaks`
- Nu Wave
- World rule

Keep this separate from the already-reviewed RVA2 safety fix.

### T5 backup deduplication

This remains a **Mac-local prerequisite**, not a GitHub-only task.

Known facts from the local review:

- The backup step is owned by the chain shell workflow, not `apply_new_music_intake_plan.py`.
- The existing T5 inventory records sizes but does not contain the SHA-256 index required for safe content reuse.

Required implementation before relying on deduplicated backups:

1. Build a Mac-local SHA-256 index of T5 backup content.
2. For every file a lane would back up, match by full-file SHA-256 only—never filename.
3. On a hash match, reuse the existing T5 path only after proving it is a regular file and re-verifying its hash.
4. On no match, copy the file into the lane backup and verify the new copy hashes identically.
5. Record a valid backup location and source hash for every planned item so manifest/journal recovery remains complete.
6. Test at least one matched and one unmatched file: matched content must not be duplicated; unmatched content must be copied; both must remain recoverable through the existing contract.

Do not change the media move/write/delete path as part of this backup optimization.

## Production safety boundary

Repository tests and merges are not authorization to mutate the live library. A real apply still requires the repository's normal proof gates: reviewed plan, coordination lock, verified per-file backup evidence, write-ahead recovery, and the applicable macOS/APFS canary checks. No proof, no run.
