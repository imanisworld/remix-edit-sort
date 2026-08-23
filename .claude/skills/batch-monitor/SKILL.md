---
name: batch-monitor
description: Read-only monitoring of a running APPLY, terminal verification when it exits, and the human Serato spot-check handoff. Never starts, restarts, or repairs an APPLY.
---

# batch-monitor — APPLY watch and terminal verification

Hard rules:
- Never start another APPLY, never restart a failed one without separate
  authorization, never acquire its locks, never alter approval, plans,
  `current-batch.json`, or recovery evidence while an APPLY is active.
- Never mark scopes VERIFIED or the batch COMPLETE by hand.
- Never record a Serato spot-check the human has not actually confirmed.
- A scope is successful only when durable evidence says VERIFIED — not because
  the process is still running.

## 1. While APPLY runs

Find the process (`ps aux | grep apply_approved_batch`). Poll durable state
read-only (`handoff/current-batch.json`): report scope-state counts, the scope
in flight, and the held lock (a live scope lock in `.locks/*.lock.json` is
legitimate during APPLY). Use a background wait on the PID rather than tight
polling. Verdict while running: `APPLY STILL RUNNING`.

If any scope goes `BLOCKED`, fails verification, loses its lock, fails backup,
or a rollback fails: `APPLY BLOCKED` — report the exact scope and durable
evidence, do NOT attempt ad hoc repair. If durable state contradicts process
state, stop and report the inconsistency.

## 2. Terminal verification (after the process exits)

From the batch directory's `apply-summary.json` and durable state, verify:

- batch state `VERIFIED`; every scope `VERIFIED` / execution `COMPLETED`;
  per-scope `files_verified` sums to the approved candidate count
- `BATCH RESULT = PASS`, `UNAUTHORIZED CHANGES = 0`, `RECOVERY STATUS = CLEAN`,
  audio-payload / Serato-frames / ID3 preservation all PASS, locks released
- `.locks/` holds no `*.lock.json` (permanent zero-byte `.mutex` sentinels are
  not locks); no leftover journals

Then run the independent invariant proof: for every approved candidate,
re-read with `prepare_metadata_batch.read_track` and compare against the
master manifest — Genre equals the approved proposal; every field the batch
did not approve (Grouping unless approved, title, artist,
audio_payload_sha256, serato_frames_sha256, path existence) is identical to
the recorded pre-state. Any unapproved mutation → `APPLY BLOCKED`, even if the
approved change itself is correct.

## 3. Serato human gate

Only when everything above is green: read the batch's `SERATO-SPOTCHECK.md`,
and put the full track list DIRECTLY in the response (never just point at the
file), each with its expected Genre (from the master manifest), the Grouping
value that must be unchanged, and the predicted crate effect. The human
confirms: appears normally, loads, waveform/cues behave, expected Genre
visible, Grouping unchanged, no unexpected Smart Crate membership change (if
the approved preview predicted zero effects, ANY batch-caused change is a
FAIL).

Give the exact completion command, to be run only after genuine human
confirmation:

    python3 scripts/apply_approved_batch.py --master-sha <MASTER_SHA> \
        --record-serato-spotcheck PASS --checked-by "<real operator identity>"

If the human reports a problem, record FAIL, not PASS. If the operator
explicitly directs a screen-control visual check instead, perform it genuinely
and say exactly how the check was performed.

Verdicts: `APPLY STILL RUNNING` / `AWAITING HUMAN SERATO SPOT-CHECK` /
`APPLY BLOCKED` / `BATCH COMPLETE` (only when the PASS is durably recorded and
state is actually `COMPLETE`). After COMPLETE, remind the operator that the
resting `current-batch.json` gets committed to main per repo pattern.
