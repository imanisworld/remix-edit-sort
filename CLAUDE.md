# Claude continuation instructions

This repository contains the reusable remix/edit sorting skill plus the local handoff from the 2026-08-15 DJ-library cleanup.

Before changing anything:

1. Read `SKILL.md` completely.
2. Read `handoff/HANDOFF-DJ-MUSIC-SORT.md` completely.
3. Treat completed folders as complete; do not rerun their old plans over sorted paths.
4. Survey one new source folder read-only and show a preview before any move or metadata write.
5. Require explicit user approval and a fresh verified music + `_Serato_` backup before applying.

Non-negotiable safety rules:

- Never delete or deduplicate from filenames alone.
- Preserve Clean, Dirty, Intro, Remix, Edit, Instrumental, Acapella, and other meaningful versions.
- Preserve meaningful numbers such as `21 Savage`, `112`, years, BPM, and part numbers.
- Do not infer uncertain genres, artists, titles, event names, or versions. Use needs-check classifications.
- Preserve audio payloads and embedded Serato frames; verify both after metadata writes.
- Preserve ID3v2.2 blocks unless tag conversion is separately previewed and approved.
- `Genre` means musical style. `Grouping` stores evidenced event/crate/source context and must append to—not overwrite—existing values.
- `Imani Drops` is explicitly out of scope and must remain untouched.

Current important state:

- Events version/genre organization is complete for 3,046 tracks.
- Events Grouping backfill is complete for 3,010 MP3s; 36 unsupported/old-tag files were safely skipped.
- The folder label `NIGER` is a mistake and means **Nigeria**. Its two-track plan is applied: Grouping is `Source: Nigeria` on both; Genre and Artist remain unknown (not guessed). See `handoff/pending/nigeria-plan.json`, the applied result, and undo log in `handoff/pending/`.
- The nine one-off per-folder apply scripts are superseded by a reusable pipeline: `scripts/inventory_library.py`, `scripts/build_version_genre_plan.py`, `scripts/apply_version_genre_plan.py`. Use it for the next source folder instead of writing a new one-off script.
- **Mutation engine landscape and locking are now enforced, not conventions.** There are four mutation-capable scripts: `apply_version_genre_plan.py` (version+genre), `apply_grouping_plan.py` (Grouping backfill), and `apply_artist_title_plan.py` (Artist/Title backfill) are the current, verified family -- treat these three as authoritative for any new work. `apply_plan.py` (the original Genre/Remixer pipeline for flat SoundCloud/DJ-pool downloads, paired with `build_plan.py`/`scan_library.py`) is legacy: it now requires `--legacy-confirm` to run `--apply`/`--undo` so it can't be reached by accident, and is kept only for its original narrow use case. All four now require `--lock-folder`/`--session` and refuse to run `--apply`/`--undo` without an owned, non-stale lock via `scripts/lock_guard.py` -- `folder_lock.py` is no longer just a convention a session might remember to run. See `handoff/session-2026-08-15/RECONCILIATION-REPORT.md` for how this was found and hardened, including a live-tested dead end (pid-liveness staleness doesn't work for one-shot CLI locks; staleness is renewal-based instead, see `folder_lock.py`'s own docstring). **Not yet done (P1 follow-up):** the three current scripts still each duplicate their own hash/backup/verify logic rather than sharing one engine module -- full unification (one shared `apply` core, one shared undo/verification path) is real remaining work, not just a naming exercise. Do it by extracting the common per-item verify-write-verify-or-rollback loop each of the three already implements almost identically into a shared module those three import, keeping each script's own plan-shape-specific logic (what fields it writes) thin on top.
- Hip Hop (5,888 tracks, 44GB) is sorted — see the Completed section in `handoff/HANDOFF-DJ-MUSIC-SORT.md` for the flatten-everything decision and three real bugs found/fixed live during that run (unwritable-format tag proposals, an unconditional-read crash on unsupported formats, and a verified-benign Serato encoding-normalization false positive). `build_version_genre_plan.py` now supports `--on-collision suffix` to safely keep same-named, different-content files (never delete/overwrite) when flattening previously-separated folders creates a destination collision.
- A library-wide metadata completeness pass is planned as a later phase (not started): fill in missing artist/title/genre/BPM/key from evidence, backfill Grouping across every sorted folder, and check Serato crate-path integrity. Scope to be confirmed with Brooke before starting.
- Text-only `.codex` working artifacts are mirrored under `handoff/session-2026-08-15/survey/`: plans, inventories, CSV reviews, result JSON, and undo manifests. Audio/tag-file backups and `_Serato_` data remain outside Git.
- `VDBL` version/genre sort is complete for 150 tracks (done by Claude, before the reusable pipeline above existed — see the `VDBL` section in the handoff for the one-off script used). It is NOT a real event despite its pre-existing `Event: VDBL` Grouping tag — the user says it's just a source download folder; where its tracks ultimately belong is still open. `Country` was added to `canonical_genres` in `references/genre-rules.json` for this run.
- **Multiple agent sessions have been working this library concurrently with zero coordination** — this session, a Codex session, and at least one other Claude session, all writing to this same repo and the same Music folder at once. That already caused a real incident: `NewFiles` got merged into `music to move` mid-run by another session, silently, while this session still held plans referencing its old paths (recovered by hash-verifying every file at its new location before touching anything). A second incident followed: a session (`claude-metadata-pass`) acquired locks on 11 folders including `Events` and `Hip Hop` and stopped running without releasing them. Before starting work on any source folder, run `python3 scripts/folder_lock.py check --folder "<name>"` and `acquire` it if free; `status` lists everyone's current locks. **This is now enforced, not just convention:** every mutation script's `--apply`/`--undo` calls `scripts/lock_guard.py`, which refuses to run without an owned, non-stale lock for `--lock-folder` — see the mutation-engine bullet above for what "non-stale" means now (renewal-based, not pid-based). Also: before editing `CLAUDE.md` or `handoff/HANDOFF-DJ-MUSIC-SORT.md`, re-read them fresh and use a small targeted edit — another session's uncommitted, in-progress content may already be sitting there.

Useful session implementations and reports are under `handoff/session-2026-08-15/`. They contain local absolute paths and are historical/audit material; generalize paths before promoting code into `scripts/`.

Read `handoff/ARTIFACTS.md` before syncing more `.codex` files. Never copy a directory named `backups`, `tag-file-backups`, or `*-tag-file-backups`, because those contain real audio payloads.

This repository contains private local workflow information. Do not publish it or add a public remote without explicit approval.
