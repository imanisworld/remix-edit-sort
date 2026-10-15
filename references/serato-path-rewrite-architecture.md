# ADR-002: Plan-gated Serato path rewrites

**Status:** Accepted
**Date:** 2026-09-10
**Deciders:** Repository maintainer and library owner

## Context

Serato stores path identity in `database V2` (`pfil`) and
`Subcrates/*.crate` (`ptrk`). Folder renames, folder merges, media moves, and
approved WAV/AIFF-to-FLAC replacements otherwise leave those references
pointing at old paths. Filename-only searching cannot safely repair this:
duplicates and related DJ versions make a same-named candidate evidence, not
identity.

## Decision

Use a separate explicit mutation lane, `scripts/rewrite_serato_paths.py`.
Its only authority is a human-reviewed rename map of old path or folder prefix
to new path or folder prefix. It never discovers a replacement from a
filename, hash, or search result.

The read-only preview:

- strictly parses every TLV in `database V2` and every `.crate`;
- rewrites only `pfil` inside database `otrk` records and `ptrk` inside crate
  `otrk` records;
- uses exact, case-sensitive matching at a path-component boundary, with the
  longest source prefix winning;
- rejects chained destinations so the result is idempotent;
- records every before/after occurrence plus source and proposed SHA-256;
- binds the plan to the full database/crate inventory, including unchanged
  crates, so a new, removed, or changed source invalidates the preview.

The apply:

- requires Serato to be closed and the operator to acknowledge that fact;
- requires an owned `Serato` coordination lock and a PASS backup manifest
  whose source root is the exact `_Serato_` directory;
- rechecks each source hash and concrete backup immediately before mutation;
- atomically replaces one Serato binary per mutation-engine transaction, with
  fsync'd write-ahead recovery and verified rollback;
- verifies the proposed file hash, strict parse, rewrite idempotence, and
  existence of every new media target before completing each item;
- runs `check_serato_crate_paths.py` after the batch and separately reports
  unrelated pre-existing missing references;
- fails the rewrite-specific post-check if a changed target is unresolved, a
  mapped old reference remains, or any database/crate byte hash drifts from
  the plan's expected final inventory.

Each file transaction is atomic and recoverable. The multi-file batch is not
all-or-nothing; already completed items remain journaled if a later item or the
final post-check fails.

## Consequences

- Folder cleanup and lossless-format swaps can carry exact Serato crate links
  forward without guessing.
- Backup, preview, apply result, recovery journal, and post-check remain local
  run artifacts and must not be committed.
- Serato history, smart-crate rules, track metadata, and embedded media frames
  are out of scope and remain byte-identical because their TLVs are not edited.
- The read-only `serato_index.py` importer remains read-only; this workflow is
  deliberately separate and opt-in.

## Related

ADR-003 (`folder-rename-runbook.md`) extends this lane: whole-folder renames
(`apply_folder_rename_plan.py`), the SQLite libraries (`master.sqlite` and
`root.sqlite`, via `rewrite_serato_sqlite_paths.py`), and a fail-closed
Serato-running check. The statement above that smart-crate and history data are
out of scope still holds; SmartCrates cache `ptrk` paths, which no tool rewrites yet.
