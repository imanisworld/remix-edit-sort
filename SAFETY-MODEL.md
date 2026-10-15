# The mutation safety model

One page, authoritative. If any other document in this repository contradicts
it, this one is right and the other one is stale.

## The five invariants

Every production mutation of the DJ library or its Serato path index — moving
a file, renaming a file, writing Genre, Grouping, Artist/Title, or Year/BPM,
rewriting an explicit Serato path, or deleting a verified duplicate — satisfies
all five of these. There is no approved path that skips any of them.

1. **One engine.** All mutations go through `scripts/mutation_engine.py`. No
   apply script performs its own rename, tag write, or unlink outside a
   `MutationEngine.item()` transaction.
2. **Lock is mandatory.** The run must own a non-stale `folder_lock.py` lock on
   the named library folder, verified atomically inside the folder mutex.
3. **Heartbeat before every mutation.** Ownership is re-verified before each
   individual item, not once at startup. A compound tag+move item renews again
   before its second physical production mutation. Losing the lock stops the
   run before its next write.
4. **Backup manifest is mandatory.** A `backup_manifest.py` manifest that says
   PASS, covers this exact source root, is unaltered, and contains per-file
   evidence for the specific file about to be mutated. The engine also
   re-hashes the actual backup copy immediately before that item and records
   its concrete path in the recovery journal.
5. **Write-ahead recovery.** A durable, fsync'd intent record is on disk before
   the mutation, and a completion record after it. A crash in between is
   recoverable.

Undo paths are mutations too: they require the lock, per-operation heartbeat,
write-ahead journal, and a recorded backup whose SHA-256 is rechecked before
restore. They consume the verified per-item backup recorded by the apply run;
they do not fabricate or trust an unverified restore path.

## Non-negotiable content rules

- **No automatic deletion.** Nothing in this repository deletes on its own.
  `delete_verified_duplicates.py` is the only deletion tool, it operates on a
  human-approved pair list, and it refuses any pair that is not a proven
  EXACT_DUPLICATE.
- **EXACT_DUPLICATE means identical SHA-256 over the whole file. Nothing else.**
  Filename, title, artist and duration similarity — in any combination, at any
  threshold — produce REVIEW at most. See `scripts/duplicate_semantics.py`.
- **Related versions are different tracks.** Clean, Dirty, Intro, Extended,
  Radio, Instrumental, Acapella, Remix, Edit, Bootleg, VIP, Live. Never merged,
  never deduplicated, never auto-renamed away.
- **Read-only tooling needs no lock.** `audit_library.py`,
  `run_verified_audit.py`, `inventory_library.py`, the `check_*` and `build_*`
  scripts and every preview builder are read-only and require no mutation lock.
- **`handoff/` is historical evidence and is never executable.** See
  `handoff/ARCHIVED-CODE.md`.
- **Mac canary before the first production apply.** CI runs Linux; the library
  is on macOS/APFS. See `MAC-CANARY.md`.

## Module map

| Module | Classification | Role |
| --- | --- | --- |
| `mutation_engine.py` | mutation-engine | The transaction core. WAL journal, preconditions, verification, rollback, recovery. `item()` for one file, `folder_item()` for one whole-folder rename. |
| `folder_lock.py` | infrastructure | Per-folder cross-process lock. `verify_and_renew()` is the atomic ownership primitive. |
| `lock_guard.py` | infrastructure | The gate every mutation script calls. `LockHeartbeat` per item. |
| `backup_manifest.py` | read-only | Builds and verifies backup evidence. PASS is computed, never asserted. |
| `duplicate_semantics.py` | read-only | The duplicate truth model. |
| `mutation_surface.py` | read-only | AST scan of every mutation path in the repo. Enforced by CI. |
| `apply_version_genre_plan.py` | engine-user | File relocation/rename + Genre. |
| `apply_grouping_plan.py` | engine-user | Grouping. |
| `apply_artist_title_plan.py` | engine-user | Artist/Title. |
| `apply_year_bpm_plan.py` | engine-user | Year/BPM. |
| `apply_genre_refinement_plan.py` | engine-user | Genre refinement. |
| `apply_needs_check_recheck_genre_plan.py` | engine-user | Genre recheck across several locked folders. |
| `apply_genre_needs_check_folder_plan.py` | engine-user | Folder-scoped Genre recheck from a hashed survey. |
| `apply_clean_dirty_move_plan.py` | engine-user | Clean/Dirty relocation plus additive Grouping. |
| `apply_new_music_intake_plan.py` | engine-user | One-save Genre/Grouping intake metadata transaction. |
| `rewrite_serato_paths.py` | engine-user | Reviewed, hash-bound `pfil`/`ptrk` path rewrites in Serato database/crates. Fails closed if Serato is running. |
| `rewrite_serato_sqlite_paths.py` | engine-user | Reviewed, hash-bound `asset.portable_id` rewrites in Serato's `master.sqlite`/`root.sqlite`; one SQLite transaction per database inside an engine item. |
| `apply_folder_rename_plan.py` | engine-user | Whole-folder renames (including case-only, via a temp name) through `MutationEngine.folder_item()`; preview, apply, undo, crash recovery. See `references/folder-rename-runbook.md`. |
| `delete_verified_duplicates.py` | engine-user | Manual EXACT_DUPLICATE deletion. |
| `apply_plan.py` | disabled-legacy | Hard-disabled. Read-only `--report` only. |
| `merge_events_duplicates.py` | disabled-legacy | Hard-disabled completed one-off. |
| `standardize_names.py` | preview-only | Proposes filename/Artist/Title cleanups. Applies nothing. |
| `reclassify.py` | preview-only | Proposes genre-folder moves. Applies nothing. |

Every module under `scripts/` declares its own `SAFETY_CLASSIFICATION`.
`mutation_surface.py` reads them and fails CI if a module carrying a mutation
primitive is classified as read-only or preview-only, if an engine-user does
not import the engine, if an engine-user has a mutation primitive outside a
transaction and outside its declared helpers, or if a disabled-legacy module
lacks its hard-fail guard.

**Read "The CI gate cannot prove every mutation call flows through the engine"
under Honest limits before relying on that.** It is a static check with real
blind spots, and a new engine-user module still needs human review.

## The approved sequence for a real apply

```bash
# 1. Read-only survey and plan. Nothing is touched.
python3 scripts/inventory_library.py "<folder>" ...
python3 scripts/build_version_genre_plan.py ...        # review the preview with Brooke

# 2. Real backup, then PROVE it.
cp -Rp "<folder>" "<backup dir>"          # or your usual backup mechanism
python3 scripts/backup_manifest.py verify \
    --source-root "<folder>" --backup "<backup dir>" \
    --serato-backup "<_Serato_ backup>" --require-serato \
    --out backup-manifest.json
# Must print "status": "PASS". Anything else blocks the apply.

# 3. Take the lock.
python3 scripts/folder_lock.py check   --folder "Hip Hop"
python3 scripts/folder_lock.py acquire --folder "Hip Hop" --session "<session>" --agent claude

# 4. Dry run, then apply.
python3 scripts/apply_version_genre_plan.py --root "<folder>" --plan plan.json \
    --backup-manifest backup-manifest.json          # dry run; reports the backup gate
python3 scripts/apply_version_genre_plan.py --root "<folder>" --plan plan.json \
    --backup-manifest backup-manifest.json --apply \
    --lock-folder "Hip Hop" --session "<session>"

# 5. Confirm the run is clean, then release.
python3 scripts/mutation_engine.py recover --journal <recovery journal from the result>
python3 scripts/folder_lock.py release --folder "Hip Hop" --session "<session>"
```

To reverse: re-run the same script with `--undo <undo log>`, `--lock-folder`
and `--session`. Undo needs the lock too.

## What "transactional" means here, precisely

Each **item** is transactional: it either completes and verifies, or it is
rolled back to its pre-mutation bytes. A **batch** is not atomic — there is no
way to make N independent filesystem renames commit as one unit on APFS — so a
failed run leaves earlier items applied and journaled, which is what the undo
log is for. Do not describe a batch as all-or-nothing.

## Honest limits

### The CI gate cannot prove every mutation call flows through the engine

This is the most important limitation on this page, because it is the one that
could be mistaken for a stronger guarantee than it is.

`scripts/mutation_surface.py` performs static AST analysis. For a module
classified `engine-user` it proves two things:

1. **Import provenance** — the module imports `mutation_engine`, `lock_guard`
   and `backup_manifest`. This proves the module *has access to* the approved
   engine. It proves nothing about any individual call site.
2. **Lexical provenance** — every mutation primitive in the file is written
   either inside a `with engine.item(...):` block, or inside one of at most
   four functions named in that module's `ENGINE_MUTATION_HELPERS`, each of
   which the scanner proves is reachable from such a block (directly, or
   through another declared helper).

What it does **not** prove, and cannot without real data-flow analysis:

- that a declared helper is *only ever* called from inside a transaction — the
  scanner shows at least one such call path exists, not that every runtime path
  does;
- that a primitive reached through an alias, a callback stored in a variable,
  `getattr`, a dictionary of functions, a decorator, or a call into another
  module is governed;
- that the `engine` object a `with` block opens is a real `MutationEngine` and
  not something else that happens to expose `.item()`;
- anything at all about dynamically constructed calls.

**What this means practically.** The AST gate is *necessary but not
sufficient*. It reliably catches the careless cases — a new script that forgets
the engine, a read-only audit tool that grows a `.save()`, an undo path doing a
bare `shutil.copy2()` (it caught exactly that in three modules during the
hardening pass, where the docstrings already claimed write-ahead journaling).
It will not catch a determined or unusual bypass.

So: **a new or substantially modified `engine-user` module still requires human
code review.** The reviewer's specific job is to read the functions named in
`ENGINE_MUTATION_HELPERS` and confirm each really is only invoked from inside a
transaction, and to check that no mutation reaches disk through indirection the
scanner cannot see. The helper list is deliberately capped at four so that
review is a few minutes' work rather than a full-file audit. Green CI is not a
substitute for that review, and this document should not be read as claiming
otherwise.

### Other limits

- `manifest_sha256` detects accidental or careless alteration of a backup
  manifest. It is not a signature; anyone who can write the file can recompute
  it. The threat model is "someone took a shortcut", not a malicious actor.
- The engine verifies that the audio payload and Serato frames are unchanged
  around a write. It cannot prove Serato itself will still read the file — only
  that the bytes it cares about did not move.
- CI cannot prove APFS behavior. `MAC-CANARY.md` is the gate that covers it.
