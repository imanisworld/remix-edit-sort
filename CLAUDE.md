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
- The folder label `NIGER` is a mistake and means **Nigeria**. Its two-track preview is not applied. Proposed Grouping is `Source: Nigeria`; Genre and Artist remain unknown. See `handoff/pending/nigeria-plan.json` and the Nigeria preview under the session handoff.
- Text-only `.codex` working artifacts are mirrored under `handoff/session-2026-08-15/survey/`: plans, inventories, CSV reviews, result JSON, and undo manifests. Audio/tag-file backups and `_Serato_` data remain outside Git.

Useful session implementations and reports are under `handoff/session-2026-08-15/`. They contain local absolute paths and are historical/audit material; generalize paths before promoting code into `scripts/`.

Read `handoff/ARTIFACTS.md` before syncing more `.codex` files. Never copy a directory named `backups`, `tag-file-backups`, or `*-tag-file-backups`, because those contain real audio payloads.

This repository contains private local workflow information. Do not publish it or add a public remote without explicit approval.
