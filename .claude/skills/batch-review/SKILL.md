---
name: batch-review
description: Human-review gate over a PREVIEWED batch. Full-population semantic review of the proposed changes; records approval ONLY on explicit operator direction. Never applies.
---

# batch-review — the human gate

Precondition: `/batch-audit` has passed on this exact batch (same master SHA).
If it hasn't, run that first. This skill reviews the CONTENT of the proposed
changes; the audit reviewed the machinery.

Hard rules:
- Never edit plan JSON, delete questionable candidates, regenerate the batch,
  or rerun PREPARE to make something pass. A wrong candidate means
  `HUMAN REVIEW FAILED` — the batch gets regenerated through the normal
  pipeline after the classification issue is fixed separately.
- Never record approval unless the human operator explicitly directs it in
  this conversation. Never fabricate human review. `approved_by` is the real
  operator identity (the established string is `"djb.a.e (human operator)"`),
  used only at their direction.
- Never run APPLY.
- If repo HEAD or any gated safety file changed after PREPARE: stop —
  approval would bind a batch that APPLY preflight will refuse. Report and let
  the operator choose (regenerate at new HEAD is the normal path).

## Review standard (full population, not samples)

Use the hash-bound artifacts only: `BATCH-PREVIEW.md`, `batch-preview.csv`,
`master-manifest.json`, per-scope plans. Verify preview MD/CSV hashes against
the manifest first. Do not generate a replacement preview.

**Genre changes** — for every candidate with a `genre_fix`:
- Re-derive the proposal from the candidate's OWN existing tag using only the
  canonical alias table (`references/version-genre-rules.json` via
  `build_version_genre_plan.load_genre_rules`). Token split, alias map, order
  preserved. 100% must be derivable — anything else is semantic inference and
  fails the review.
- Print the complete distinct (raw → proposed) transformation table and eyeball
  every row: each must be casing/separator normalization, never
  reclassification, never collapsing valid multi-Genre values.

**Grouping changes** — for every candidate with additions:
- Strictly append-only, order preserved: proposed = existing prefix + declared
  add-terms, nothing else. Any replacement or reordering of existing values →
  `HUMAN REVIEW FAILED`.
- Every added term is in the controlled vocabulary; every addition cites
  explicit filename wording or a bracketed label (never plain-word inference —
  plain-word Radio Edit/Flip/ReWork must be HOLDs, not additions).
- List every candidate whose existing Grouping contains free text (record-pool
  URLs, `Event: ...`, vibe tags) and verify each survives byte-for-byte.
- Bracketed Radio Edit/Flip/ReWork write their exact terms, never generic Edit.

**Smart Crate effects** — for every candidate with effects:
- Group by (crate, effect, rule). Every effect must be the expected consequence
  of the approved change (e.g. a labeled edit entering Edit crates). Effects on
  fields the batch does not change, or unexplained membership changes →
  `HUMAN REVIEW FAILED`. A genre-only batch should have ZERO effects
  (crate-dependent Genre spellings are held by the pipeline).

**Scopes** — confirm the scopes collectively contain exactly the reviewed
candidate set; walk the largest scopes' preview rows individually.

## Approval (operator-directed only)

Present the findings and STOP. If — and only if — the operator then explicitly
directs approval, run the established mechanism:

    python3 scripts/prepare_metadata_batch.py --approve <MASTER_SHA> \
        --approved-by "<real operator identity>"

Then verify read-only: approval.json exists and binds the exact master SHA,
repo commit, plan SHAs, and safety hashes; state advanced to `HUMAN_APPROVED`
only; plan SHAs and live safety hashes still match; no locks; no journal; no
production change. Report and stop — APPLY is a separate operator decision.

Verdicts: `HUMAN REVIEW PASS — APPROVED` / `HUMAN REVIEW FAILED` /
`AWAITING HUMAN REVIEW` (review passed, operator has not directed approval) /
`BLOCKED`.
