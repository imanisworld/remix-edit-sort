---
name: batch-audit
description: Read-only audit of a PREPARE'd metadata batch before human review. Verifies binding, collisions, determinism, and HOLD population. Never prepares, approves, or applies.
---

# batch-audit — PREPARE audit (read-only)

Audit the batch currently recorded in `handoff/current-batch.json` (or the batch ID
given as an argument). Everything in this skill is read-only: never run PREPARE,
never create an approval, never run APPLY, never edit plans or state, never
acquire locks.

## 0. Concurrency discipline (always first)

- `ps aux | grep -E "prepare_metadata|apply_approved"` — if a PREPARE is still
  running, do NOT run a second one and do NOT touch its files. Monitor durable
  state only and audit once it settles. Verdict meanwhile: `PREPARE STILL RUNNING`.
- Never create a competing batch. If another session already ran the PREPARE the
  operator asked for, adopt and audit its stable artifacts instead.

## 1. Identity and binding

Read `handoff/current-batch.json` and the batch's `master-manifest.json`.
Verify, using the repo's own code (import from `scripts/`):

- `metadata_batch.verify_master(master_path, expected_sha)` passes (canonical
  SHA, `master_sha` field excluded from the hash).
- `metadata_batch.safety_file_hashes()` (live tree) equals the manifest's
  `safety_file_sha256` for all gated files. If any differ, or repo HEAD moved
  past the manifest's `repo_commit` with safety-file changes: report it
  prominently — APPLY preflight will refuse; approval would be pointless.
- Lane, `genre_only`, `batch_limit`, and `intake_root` match what the operator
  requested. Limits >150 require the `--confirm-max` double-entry.
- No approval file exists yet in the batch directory.

## 2. Plans and scopes

For every scope in the manifest:

- Recompute the plan file's SHA-256 and match `plan_sha256`.
- Plan `items` length == scope `item_count` == candidates grouped by `scope_id`.
- Plan item keys are only the allowed set (e.g. `{path, sha256, genre_fix,
  grouping_fix}`; a genre-only batch must have zero `grouping_fix` keys).
- `backup_root` is under `/Volumes/T5` and does not exist yet (backups belong to
  the BACKED_UP stage, not PREPARE).
- Scope source roots do not overlap or nest each other.

## 3. Collision protection

Using `mutation_engine.path_collision_key` (NFC + casefold canonical identity):

- Zero duplicate candidate paths, exact or canonical.
- No candidate appears in more than one scope.

Any collision → `BLOCKED`.

## 4. Determinism proof (the strong form)

Do not rely on timestamps or enumeration order. Re-run the actual discovery
function in-process (`prepare_metadata_batch.discover(...)` or the lane's
equivalent — pure read-only, writes nothing, creates no batch) with the same
library root, Serato root, limit, and flags. Compare bit-for-bit against the
manifest candidates: paths, sha256, audio_payload_sha256, serato_frames_sha256,
tags, proposals, order — and the exclusion counters.

Caveat: this proof is only valid while nothing is mutating the library. If an
APPLY started meanwhile, a mismatch is applied-scope shrinkage, not
nondeterminism — say so instead of raising a false alarm.

If determinism cannot be proven: `BLOCKED`.

## 5. Candidate and HOLD population

Report: discovered / selected / held-excluded totals, per-scope counts, and the
exclusion reason → count table. Selected < limit is NOT a failure; selected >
limit is `BLOCKED`. The exclusions must contain no "eligible but deferred by
batch cap" entry if the operator expected full eligibility, and no HOLD may
appear in any plan's items (plan items must equal the selected set exactly,
path+sha256).

## 6. Verdict

Return exactly one of `PREPARE AUDIT PASS`, `BLOCKED`, `PREPARE STILL RUNNING`,
with: batch ID, durable state, repo binding, limit (requested/confirmed/
selected), candidate counts, scope table, determinism result, HOLD table,
collision result, safety binding confirmation, production mutation (`NO` —
prove via the re-discovery hash equality), lock state, approval present (`NO`),
and Ready for Human Review `YES`/`NO`. Stop there — approval is the operator's
gate, handled by `/batch-review`.
