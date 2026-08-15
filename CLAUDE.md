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
- Hip Hop (5,888 tracks, 44GB) is sorted — see the Completed section in `handoff/HANDOFF-DJ-MUSIC-SORT.md` for the flatten-everything decision and three real bugs found/fixed live during that run (unwritable-format tag proposals, an unconditional-read crash on unsupported formats, and a verified-benign Serato encoding-normalization false positive). `build_version_genre_plan.py` now supports `--on-collision suffix` to safely keep same-named, different-content files (never delete/overwrite) when flattening previously-separated folders creates a destination collision.
- A library-wide metadata completeness pass is planned as a later phase (not started): fill in missing artist/title/genre/BPM/key from evidence, backfill Grouping across every sorted folder, and check Serato crate-path integrity. Scope to be confirmed with Brooke before starting.
- Text-only `.codex` working artifacts are mirrored under `handoff/session-2026-08-15/survey/`: plans, inventories, CSV reviews, result JSON, and undo manifests. Audio/tag-file backups and `_Serato_` data remain outside Git.

Useful session implementations and reports are under `handoff/session-2026-08-15/`. They contain local absolute paths and are historical/audit material; generalize paths before promoting code into `scripts/`.

Read `handoff/ARTIFACTS.md` before syncing more `.codex` files. Never copy a directory named `backups`, `tag-file-backups`, or `*-tag-file-backups`, because those contain real audio payloads.

This repository contains private local workflow information. Do not publish it or add a public remote without explicit approval.
