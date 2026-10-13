# DJ library integration — current tasks and handoff

Checked: 2026-10-13. **Source-only work; nothing here authorizes a music move, metadata write, DJ database rewrite, merge, or deployment.**

This is the **single current task list** for connecting the website's event records, actual DJ playback histories, Serato/Rekordbox source change detection, and safe genre folder organization. Technical boundaries and source requirements live in [event-history-and-folder-renames.md](event-history-and-folder-renames.md). General safety is governed by `SAFETY-MODEL.md` and `MAC-CANARY.md`.

## State verified in GitHub

- [ ] **PR #37:** genre atlas, approved 76-genre learner vocabulary, empty Genre-to-existing-folder maps; **open/unmerged** at this check. This PR does **not** reconcile the two older sorter rule files. Review and merge separately when approved: https://github.com/imanisworld/remix-edit-sort/pull/37
- [ ] **PR #42:** read-only `dj_source_drift.py`, fixtures and this plan; draft/open, CI passed before this documentation update. Re-run/inspect current head checks before considering merge: https://github.com/imanisworld/remix-edit-sort/pull/42
- [x] Library index can detect NEW, CHANGED, MOVED, REMOVED audio files in read-only scans.
- [x] Serato `database V2`/Subcrates and Rekordbox *exported collection XML* have read-only snapshot importers; separate Serato path-rewrite and Rekordbox relocation/bridge tools already exist.
- [x] **Pure synthetic time normalization** in stacked PR #43: IANA/DST/overnight handling and fail-closed candidate event-window checks. No real History importer or actual plays-to-event association; PR remains unmerged.
- [x] **Manual DJ review launcher (draft PR #44):** scans an explicitly selected download/landing folder, flags exact-byte duplicates and missing tags, rechecks SHA matches in the *existing* SQLite library index, optionally checks Serato/Rekordbox source drift, and writes private HTML/JSON reports. Actual file moves and native History row parsing are explicitly disabled.
- [ ] **One integrated, accepted real-history matcher:** not built or verified. Do not claim current track-to-event associations.
- [ ] **Live folder map / current Mac canary / current Serato & Rekordbox exports:** unavailable while the Mac is offline.

## Remote work — do now, without the Mac

| Priority | Owner | Task | Done when |
| --- | --- | --- | --- |
| P0 | Grok **research only** | Verify official Serato History session CSV/TXT fields and track played/unplayed rules; verify official Rekordbox Histories capabilities and export limitations. Separate *History* from Serato support diagnostic session logs and Rekordbox collection XML. | Cite current vendor docs; list confirmed, unknown, and sample-required fields; no repository/data changes. |
| P0 | ChatGPT **draft PR #43**, stacked on #42 | Pure IANA/DST/overnight DJ set windows, offset-aware playback timestamps and fail-closed candidate matching with synthetic normalized fixtures. | Code + focused tests committed; CI and review required. Does **not** parse/validate real history exports or establish actual played-at-event records. |
| P0 | ChatGPT **draft PR #44**, stacked on #43 | One manual Mac launch, selected-downloads audit, exact SHA duplicate checks, existing snapshot-drift reuse, typed index/source-evidence rechecks, private HTML/JSON reports. | Synthetic tests + Linux/macOS CI, then real read-only Mac canary; no watcher or file moves. |
| P0 | Cursor Cloud **review, not rewrite** | Inspect PR #42 change detector for fail-closed behavior and missing coverage; keep source-fingerprint drift separate from per-track metadata deltas and live Rekordbox changes. | Focused review findings, tests for any code fixes, clear unsupported cases. |
| P1 | Cursor Cloud **preview design** | Plan idempotent folder moves and Serato/Rekordbox reference verification using existing utilities; do not generate hardcoded live folder mappings. | Written gates, test cases and proposed artifact shapes, with **no** applied moves/rewrites. |
| P1 | Website code owner **later, separate PR** | Design a private read-only export of event metadata *and confirmed actual DJ set times* if available; never expose private booking links or playback data via the public events API. | Reviewed access design and tests; no production DB change in this stage. |

**Source-status boundary:** PR #43 includes code/tests for **synthetic time evidence only** and documents the remaining Grok gaps in [event-history-and-folder-renames.md](event-history-and-folder-renames.md). Do not treat a claimed `EXACT_INDEXED_FILE` field in arbitrary JSON as verified; upstream trusted importers must establish it from the existing index first. Real Serato/Rekordbox histories and DJ set times are still required.

**Agent separation:** `bae-agenda/docs/GROK_MANAGER_SCOUT_PROMPT.md` is for finding paid DJ opportunities. The Grok research task above is a one-off **music-library technical research** request and must not replace that existing Manager automation/prompt. Cursor must read open PRs and current repo safety docs first, and use a different branch from #37 and #42.

## Mac-only acceptance queue — do later

**Run first when the Mac is available (draft PR #44 must be approved and present in the local checkout):**

- [ ] From the `remix-edit-sort` repository directory, make the manual launcher executable (one time):
  ```bash
  chmod +x "DJ Library Review.command"
  ```
- [ ] Double-click `DJ Library Review.command` (or run `bash "DJ Library Review.command"`). Choose the actual `music to move` folder or `~/Downloads`. Start with a small read-only sample, then review the HTML/JSON under `~/Desktop/DJ-Library-Reports`.
- [ ] Confirm the source paths, existing library-index availability, duplicate evidence, Serato/Rekordbox snapshot inputs, and any unreadable tracks before considering a real-library move. **Do not approve a move from the initial report alone.**

1. [ ] Obtain current, read-only `genre-folders.txt`: exact folders, physical track counts, and both Afrobeat/Afrobeats if present. Populate maps only after review; reconcile older `genre-rules.json` and `version-genre-rules.json` with the approved master and correct misrouted keywords.
2. [ ] Export one **real Serato History** session (CSV/TXT) with start/end/deck/played status columns shown where available, and one representative **Rekordbox Histories** artifact. Inspect samples before writing parsers. Never commit actual history files, private paths, crates, or music.
3. [ ] Confirm actual DJ **set** windows (event start time is insufficient), including clock/timezone, and compare with real playback rows in a **read-only** end-to-end run.
4. [ ] Capture fresh, trusted library/Serato/Rekordbox snapshots; test drift against controlled known changes without touching originals. A Rekordbox XML that has not been re-exported cannot detect new in-app changes.
5. [ ] Verify a **current-engine** on-volume APFS synthetic canary and PASS scoped music + `_Serato_` backups. Historical completed library sorts do not prove this current engine's canary gate.
6. [ ] For any **explicitly approved** physical folder rename: review exact old/new paths, ambiguous variants, hashes, casing collisions, Serato crate rewrite plan, Rekordbox XML bridge/re-import, verification and undo. No automatic renames.
7. [ ] Separately review T5 backup-drive index, metadata completion, needs-check backlog, 9 VDBL leftovers and personal Atlas save/sync items; these do not block the current remote work.

## Completion criteria

**Remote phase ends** when vendor evidence/unknowns are recorded, synthetic event-play candidate matching is tested and remains evidence-limited, #42 change-detector checks are reviewed, and the folder-rename preview gate is specified.

**Integration is not called complete** until real export samples validate the parsers, track identity and per-play times are proven, ambiguous matches remain unassigned, DJ software changes are detected on the Mac, and a physical rename can be previewed/undone on an approved throwaway copy.

No additional scheduler, autonomous filesystem watcher, paid service, or production database table is justified by this phase.
