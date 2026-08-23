---
name: batch-status
description: Read-only snapshot of the current metadata batch — exact durable state, evidence on disk, live processes, locks, and the single next legal step in the pipeline.
---

# batch-status — where are we, and what is the next legal step

Entirely read-only. Gather:

1. `handoff/current-batch.json` — batch ID, state, updated_at, reason, scope
   states (count by state; name any non-terminal scope).
2. Batch directory contents — which artifacts exist: master-manifest.json,
   plans/, BATCH-PREVIEW.md/csv, approval.json, apply-summary.json,
   SERATO-SPOTCHECK.md, recovery evidence.
3. Live processes: `ps aux | grep -E "prepare_metadata|apply_approved"`.
4. Locks: `.locks/*.lock.json` (ignore permanent `.mutex` sentinels).
5. Repo: HEAD SHA, whether it matches the manifest's `repo_commit`, whether
   any gated safety file changed since PREPARE, uncommitted changes beyond the
   live `current-batch.json`.

Then state the ONE next legal step from the state machine — never skip a gate:

- `DISCOVERED`/PREPARE running → wait for PREPARE to settle; then `/batch-audit`
- `PREVIEWED`, unaudited → `/batch-audit`
- `PREVIEWED`, audited → `/batch-review` (human gate; approval only on explicit
  operator direction)
- `PREVIEWED` but HEAD/safety files moved since PREPARE → binding is stale:
  operator decides, normal path is regenerate via a fresh PREPARE
- `HUMAN_APPROVED` → operator may start APPLY
  (`python3 scripts/apply_approved_batch.py --master-sha <SHA>`); then
  `/batch-monitor`
- `APPLIED` (apply in flight) → `/batch-monitor`
- `VERIFIED` → human Serato spot-check (tracks in the batch's
  SERATO-SPOTCHECK.md); record PASS/FAIL only on genuine human confirmation
- `COMPLETE` → commit the resting `current-batch.json` to main per repo
  pattern; the next batch may then be prepared
- `BLOCKED` / `ROLLBACK_FAILED` → no forward edge exists by design; recovery
  is a separate, explicitly authorized effort (batch-bound recovery pattern) —
  never work around the state machine

Flag loudly: state inconsistent with process evidence, leaked locks with no
live APPLY, an approval present that binds a different master SHA or repo
commit than the live tree, or a second session's PREPARE/APPLY running
concurrently (coordinate, never compete).
