# DJ library integration — current tasks and handoff

Checked: 2026-10-13. **Source-only work; nothing here authorizes a music move, metadata write, DJ database rewrite, merge, or deployment.**

This is the **single current task list** for connecting the website's event records, actual DJ playback histories, Serato/Rekordbox source change detection, and safe genre folder organization. Technical boundaries and source requirements live in [event-history-and-folder-renames.md](event-history-and-folder-renames.md). General safety is governed by `SAFETY-MODEL.md` and `MAC-CANARY.md`.

## State verified in GitHub

- [ ] **PR #37:** genre atlas, approved 76-genre learner vocabulary, empty Genre-to-existing-folder maps; **open/unmerged** at this check. This PR does **not** reconcile the two older sorter rule files. Review and merge separately when approved: https://github.com/imanisworld/remix-edit-sort/pull/37
- [ ] **PR #42:** read-only `dj_source_drift.py`, fixtures and this plan; draft/open, CI passed before this documentation update. Re-run/inspect current head checks before considering merge: https://github.com/imanisworld/remix-edit-sort/pull/42
- [x] Library index can detect NEW, CHANGED, MOVED, REMOVED audio files in read-only scans.
- [x] Serato `database V2`/Subcrates and Rekordbox *exported collection XML* have read-only snapshot importers; separate Serato path-rewrite and Rekordbox relocation/bridge tools already exist.
- [x] **Pure synthetic time normalization** in stacked PR #43: IANA/DST/overnight handling and fail-closed candidate event-window checks. No real History importer or actual plays-to-event association; PR remains unmerged.
- [x] **Synthetic exact-identity candidate join** in draft PR #46, stacked on #43: `dj_event_playback_match.py` keeps #43's clock rules and also requires one whole-file SHA-256 plus a verified source artifact. Duplicate titles and ambiguous IDs stay unresolved. This still does not parse a real History export. https://github.com/imanisworld/remix-edit-sort/pull/46
- [ ] **One integrated, accepted real-history matcher:** not built or verified. Do not claim current track-to-event associations.
- [ ] **Live folder map / current Mac canary / current History exports:** a read-only Mac pass on 2026-10-13 produced a private folder listing and a DJ Library Review report. The canary was not run. No original Serato or rekordbox History export was present. Collection XML remains a library file, not a play log.

## Remote work — do now, without the Mac

| Priority | Owner | Task | Done when |
| --- | --- | --- | --- |
| P0 | Grok **research only** | Verify official Serato History session CSV/TXT fields and track played/unplayed rules; verify official Rekordbox Histories capabilities and export limitations. Separate *History* from Serato support diagnostic session logs and Rekordbox collection XML. | Recorded 2026-10-13 in [event-history-and-folder-renames.md](event-history-and-folder-renames.md). Official panel rules are cited. CSV/TXT/m3u8 layouts still need a Mac sample. No music, DJ database, or parser change. |
| P0 | ChatGPT **draft PR #43**, stacked on #42 | Pure IANA/DST/overnight DJ set windows, offset-aware playback timestamps and fail-closed candidate matching with synthetic normalized fixtures. | Code + focused tests committed; CI and review required. Does **not** parse/validate real history exports or establish actual played-at-event records. |
| P0 | Cursor **review, not a new detector** | Inspect PR #42 change detector for fail-closed behavior and missing coverage; keep source-fingerprint drift separate from per-track metadata deltas and live Rekordbox changes. | Done in this draft: machine-readable `scope`, baseline vs inspected paths, and a regression that an uppercase fingerprint stays `CHANGED`. `UNCHANGED` still does not observe live Rekordbox. |
| P1 | Cursor **preview only** | Plan idempotent folder moves and Serato/Rekordbox reference verification using existing utilities; do not generate hardcoded live folder mappings. | Done in this draft: `preview_folder_rename.py` validates a synthetic plan and emits a map only when `rewrite_serato_paths.normalize_rename_map` accepts it. Rekordbox stays `rekordbox_relocation_preview.py`. No apply. |
| P1 | Website code owner **later, separate PR** | Design a private read-only export of event metadata *and confirmed actual DJ set times* if available; never expose private booking links or playback data via the public events API. | Reviewed access design and tests; no production DB change in this stage. |

**Source-status boundary:** PR #43 checks the shape of a claimed `EXACT_INDEXED_FILE`. This draft does not treat that claim as identity: `dj_event_playback_match.py` requires the play's `track_sha256` to equal exactly one synthetic library file, and the source artifact to match one verified provenance record. Those fixture fields are still assertions until a Mac sample and a real importer produce them. Vendor rules and the fields that still need a sample are in [event-history-and-folder-renames.md](event-history-and-folder-renames.md).

**Agent separation:** `bae-agenda/docs/GROK_MANAGER_SCOUT_PROMPT.md` is for finding paid DJ opportunities. The Grok research task above is a one-off **music-library technical research** request and must not replace that existing Manager automation/prompt. Cursor must read open PRs and current repo safety docs first, and use a different branch from #37 and #42.

## Mac-only acceptance queue — do later

1. [ ] Obtain current, read-only `genre-folders.txt`: exact folders, physical track counts, and both Afrobeat/Afrobeats if present. Populate maps only after review; reconcile older `genre-rules.json` and `version-genre-rules.json` with the approved master and correct misrouted keywords.
2. [ ] Export one real Serato History session as CSV and txt, with Start Time, End Time, Playtime, Deck, and Location visible first, plus one rekordbox History `.txt` and `.m3u8`. Include a midnight-crossing set and one inserted or manually marked-played track. Inspect those samples before any parser. Do not parse `.session` files, `master.sqlite`, diagnostic logs, or collection XML as a play log. Never commit the exports.
3. [ ] Confirm actual DJ **set** windows (event start time is insufficient), including clock/timezone. Inspect one real Serato History CSV/TXT and one Rekordbox Histories artifact before any parser. Only after a reviewed normalized fixture exists, run `python3 scripts/dj_event_playback_match.py` with `--events`, `--plays`, `--library`, and `--provenance`. Ambiguous rows stay `REVIEW`.
4. [ ] Capture fresh, trusted library/Serato/Rekordbox snapshots; test drift against controlled known changes without touching originals. A Rekordbox XML that has not been re-exported cannot detect new in-app changes.
5. [ ] Verify a **current-engine** on-volume APFS synthetic canary and PASS scoped music + `_Serato_` backups. Historical completed library sorts do not prove this current engine's canary gate.
6. [ ] For any **explicitly approved** physical folder rename: build a private plan and run `python3 scripts/preview_folder_rename.py --plan <plan.json>`. Resolve every `REVIEW` and `BLOCKED` row. Feed a ready `serato_rename_map` to `rewrite_serato_paths.py preview`, and run `rekordbox_relocation_preview.py` against the trusted index. Then canary, PASS backups, locks, and undo. No automatic renames.
7. [ ] Separately review T5 backup-drive index, metadata completion, needs-check backlog, 9 VDBL leftovers and personal Atlas save/sync items; these do not block the current remote work.

## Completion criteria

**Remote phase ends** when vendor evidence/unknowns are recorded, synthetic event-play candidate matching is tested and remains evidence-limited, #42 change-detector checks are reviewed, and the folder-rename preview gate is specified.

**Integration is not called complete** until real export samples validate the parsers, track identity and per-play times are proven, ambiguous matches remain unassigned, DJ software changes are detected on the Mac, and a physical rename can be previewed/undone on an approved throwaway copy.

No additional scheduler, autonomous filesystem watcher, paid service, or production database table is justified by this phase.
