# DJ library integration — one current work plan

Reviewed: **2026-10-13**. This replaces older *task instructions* in this file. It does not delete historical handoffs. **No audio/metadata, Serato/Rekordbox app data, production database, folder names, or website records were changed. Nothing in these pull requests is merged.**

## Goal and stop point

One working music library: download new tracks into an inbox, identify exact copies and uncertain metadata, review approved Genre and physical destinations, then eventually move only approved tracks to their real existing folders **while preserving DJ software references and event context**.

**Remote coding stops** once the two source-only PR stacks are coherent, checked on both CI operating systems, and this Mac checklist is ready. Do not add a watcher, new data service, second music database, automatic deletion lane, or speculative native DJ History parser.

## Active PRs — two ordered stacks, no merge authorization

| Lane | Merge/review order | Verified scope |
| --- | --- | --- |
| **Genre and folders** | [#37](https://github.com/imanisworld/remix-edit-sort/pull/37) → [#41](https://github.com/imanisworld/remix-edit-sort/pull/41) | Reference Genre Atlas, proposed 76-Genre vocabulary, user review notes, two reconciled sorter pipelines, approved-later folder maps, and hashed **read-only** landing-promotion/Serato path previews |
| **DJ source intelligence + one-run tool** | [#42](https://github.com/imanisworld/remix-edit-sort/pull/42) → [#43](https://github.com/imanisworld/remix-edit-sort/pull/43) → [#44](https://github.com/imanisworld/remix-edit-sort/pull/44) | Trusted Serato/Rekordbox **snapshot drift**, IANA/DST/overnight **candidate-only** time handling, and a manual `DJ Library Review.command` for download/inbox duplicates, missing tag review and private HTML/JSON evidence |

- **#38, #39, #40 are CLOSED AS SUPERSEDED, unmerged.** #38's complete 27-comment operator-review record is preserved in #41. #39's corrected Atlanta Bass/Miami Bass and context routing is covered by stronger #41 tests; its old automatic compound-label assertions are intentionally not retained. #40's preview/policy/tests are incorporated and additionally hardened in #41. Their original source branches/history remain for reference.
- #37 is open and **not approved to auto-retag a real track**. #41 remains draft, stacked on #37. Both Atlanta Bass and Miami Bass remain canonical and distinct. Historical Afrobeat versus Afrobeats, and several Funk/Soul/Boogie and compound decisions are held for human review.
- #42 was reviewed and hardened remotely: source bytes are opened safely, saved SHA-256/size manifests must be structurally valid, the SQLite index must be correct and have a completed root-bound scan, and missing baselines/sources are never described as unchanged. #43 and #44 contain the same source and tests for this hardening, so the combined stack can be checked by CI.
- All five remain **unmerged**. Read CI at the **latest head SHA** of each PR before integration. After any parent-stack change, confirm the child branches carry the new source and rerun the child's Linux/macOS CI. Never claim an older green check proves a newer combination.
- The code can propose file routing and evidence associations, but **there is no approved automated cross-folder audio move**. No separate app/database, scheduler, daemon or production deployment is authorized.

## Remote work — done versus evidence still missing

| Item | Remote status | Remaining gate |
| --- | --- | --- |
| One canonical genre vocabulary and both sorter rule sets | Source-only implementation and CI in #37/#41 | Review remaining disputed Genre mappings and confirm physical folders from Mac |
| Landing/inbox read-only candidate movement preview | Implemented in #41 | Exact live inventory, verified route map, approval, backup+undo canary, Serato/Rekordbox reference verification |
| Serato database + crate, Rekordbox exported XML **drift** | Implemented and security-reviewed in #42 | New trusted imports on the Mac, real snapshots; an unchanged old XML says nothing about unexported changes |
| Overnight set dates, IANA time zones, DST gaps/folds and overlapping events | Synthetic, strict candidate matching in #43 | Real actual DJ set start/end and source-aware playback row times; no inference from public event start |
| Downloads/inbox audit + identical-file duplicates + private HTML/JSON report | Implemented as manually invoked #44 | Run on selected small real folder; verify existing indexed library and app inputs |
| Shared Serato ↔ Rekordbox track identity | Existing index crosswalk uses its **own** local `file_id` from separately matched current paths | Validate real source paths/import snapshots; never equate Rekordbox `TrackID` to a Serato ID |
| Actual played-track-to-event history | **NOT IMPLEMENTED** for real vendor History exports | One original Serato History CSV/TXT + one representative Rekordbox Histories artifact, verified session fields and timestamps; crate/playlist/PlayCount/LastPlayed are insufficient |

Grok can help **once**, separately from its DJ Manager lead-scouting automation: verify *current vendor documentation* for actual Serato History CSV/TXT fields, manual played/unplayed/inserted rows, Rekordbox history export/device behavior, time zones and midnight handling; mark each finding **documented**, **observed on installed version**, or **requires sample**. Research notes go in [event-history-and-folder-renames.md](event-history-and-folder-renames.md); do not claim the installed-app export fields are confirmed without real samples.

## Mac-only run checklist — saved commands

**Prerequisite:** These draft branches must be reviewed/integrated or intentionally checked out on the Mac. The launcher is not available in an older `main` checkout. Do not execute an audio mover from the review report.

1. [ ] **One-time launcher setup**, from the `remix-edit-sort` repo directory after the code is available:

   ```bash
   chmod +x "DJ Library Review.command"
   ```

2. [ ] Run it in Finder by double-clicking `DJ Library Review.command`, or from the same directory:

   ```bash
   bash "DJ Library Review.command"
   ```

   Select `~/Downloads` or the **verified existing** `music to move` folder. Supply the *existing* SQLite library-index path if known; without it, cross-library duplicates and DJ import drift are **not checked**. Reports default to `~/Desktop/DJ-Library-Reports`, never the music folders. Start with a **small throwaway/supervised read-only sample**.

3. [ ] Confirm `genre-folders.txt`: exact live destination folder spelling, capitalization, whitespace, version subfolders, counts, and both Atlanta Bass/Miami Bass if present. Do not populate `folder_names` from history/guesses.

4. [ ] Inspect the current trusted local library index, a fresh Serato `database V2`/Subcrates snapshot, and a newly exported Rekordbox **collection XML**. Compare #42 drift, reimport separately after review if changed. An absent index or stale XML never counts as proof of unchanged application state.

5. [ ] Privately save **one original Serato History CSV/TXT** and **one real Rekordbox Histories artifact** with installed Serato/Rekordbox versions, Mac OS version, export time, relevant device/mode and source hashes. Do **not** commit them, music tracks, personal paths or private bookings. Confirm the difference between actual histories and Rekordbox collection XML. Inspect which rows are manual/unplayed, if exposed.

6. [ ] Verify at least one real DJ set's **actual start AND end**, event ID, correct IANA time zone, and explicit calendar dates for an after-midnight set. Confirm ambiguous/nonexistent DST wall times instead of guessing. Only then compare authenticated individual play records; overlap, inaccurate clocks, duplicate versions and weak IDs remain `REVIEW`.

7. [ ] Use #41's landing preview and **existing** physical folder map to review a candidate batch. Before any physical movement: close DJ apps; verify scoped source+_Serato_ backups with **PASS** manifest, both source/destination locks, complete transaction journal, current SHA-256, planned Serato crate/database rewrite and Rekordbox bridge/relocation, preservation of cues/beatgrids, explicit approval, and **tested undo**. Run a supervised **maximum-five-track** throwaway/on-volume canary; zero overwrites/deletions, stop on first failed check. No unattended mover.

8. [ ] Record real observed results and unresolved IDs/paths; only then consider larger approved batches. The 9 VDBL leftovers, T5 evidence/index and other historical maintenance are separate later backlog items; do not block this bounded remote phase on them.

## Completion criteria

- **Remote milestone:** PR stacks documented, redundant old PRs closed, #42 security review resolved, Linux/macOS checks at each modified head, exact Mac command saved. **Stop remote work here.**
- **On-device milestone:** first actual reviewed report from a real, selected folder and current snapshots with all unknowns visible.
- **Physical-move milestone (later):** explicit trusted genre/destination approvals, backup/locking/rollback evidence, and integrity of every DJ path and media file in a successful supervised canary.
- **Performance-history milestone (later):** real export schema and provenance verified; only unique, corroborated track-to-set associations, with uncertainty retained.

No further background job, new database, or ongoing watcher is justified by these gates. A manual run can be repeated after every download or fresh export.
