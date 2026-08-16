# Library readiness roadmap

This file complements `references/audio-intelligence-roadmap.md`. It defines the final operational layers needed to turn evidence from the cleanup into a DJ-library readiness view.

For ongoing operation after the initial cleanup, also see `references/library-maintenance-roadmap.md` for caching, drift detection, new-music intake, relocation mapping, analysis provenance, canary testing, DJ-utility enrichment, and storage-impact reporting.

These capabilities are read-only by default. They may recommend review actions, but they must not silently move, rename, retag, archive, or delete music or rewrite Serato data.

Operating order:

**current unresolved recheck -> audio-intelligence evidence -> Serato/library integrity -> prioritized human review -> durable reviewed decisions -> approved mutations only through lock/backup/apply/verify/undo.**

## 1. Serato integrity checker

Goal: prove that the current filesystem and Serato state still agree after cleanup work.

Read-only checks should include, where Serato data formats can be parsed safely:

- crate/database/history references whose target file is missing;
- references to paths that no longer exist;
- files that appear to have moved but are still referenced at an old path;
- duplicate Serato references to the same physical file/content identity;
- library audio with no known Serato relationship;
- presence/absence of Serato cues, loops, beatgrids, analysis/overview data where readable;
- parse/read errors kept separate from actual integrity failures.

Required output states should distinguish at least:

- `SERATO_REFERENCE_OK`;
- `MISSING_FILE_REFERENCE`;
- `STALE_PATH_REFERENCE`;
- `DUPLICATE_REFERENCE_REVIEW`;
- `NO_SERATO_RELATIONSHIP`;
- `SERATO_DATA_PRESENT`;
- `SERATO_DATA_ABSENT`;
- `PARSE_UNSUPPORTED_OR_ERROR`.

Guardrails:

- never rewrite Serato databases/crates/history automatically;
- never infer that `NO_SERATO_RELATIONSHIP` means unused or safe to archive;
- never delete a file because Serato cannot find it;
- retain the original Serato source record/path and the matched current path as separate evidence fields;
- preserve ambiguity when more than one current file could satisfy an old reference.

## 2. Cue / beat-grid anomaly checker

Goal: identify suspicious Serato-analysis states without altering DJ work.

Candidate review signals:

- BPM present but no readable beatgrid/analysis data;
- cue/loop positions beyond track duration;
- track duration changed materially compared with stored analysis evidence;
- independent BPM analysis strongly conflicts with stored BPM and is not a plausible x2 or /2 relationship;
- unreadable/corrupt Serato analysis frame;
- file appears to be a new encoding/content hash while old analysis evidence remains associated with a prior version.

Statuses should remain conservative:

- `ANALYSIS_CONSISTENT`;
- `GRID_REVIEW`;
- `CUE_LOOP_REVIEW`;
- `CONTENT_ANALYSIS_MISMATCH_REVIEW`;
- `UNKNOWN`.

No automatic grid, cue, loop, or BPM repair.

## 3. Canonical keeper recommender for duplicate/equivalent groups

Goal: once exact duplicates or audio-equivalent candidates have been found, rank which copy is the strongest operational keeper without deleting anything.

Candidate evidence may include:

- Serato usage/session history;
- cue/loop/beatgrid data presence;
- known event/gig use;
- format/bitrate/sample rate;
- decode/file-health result;
- metadata completeness;
- explicit Clean/Dirty/Intro/Remix/etc. labeling;
- stable historical path/provenance;
- reviewed human decisions.

Output should be recommendation-only:

- `PREFERRED_COPY`;
- `ALTERNATE_COPY`;
- `PROTECTED_RELATED_VERSION`;
- `KEEPER_UNRESOLVED`.

Important:

- only compare files already proven to be exact duplicates or strong audio-equivalent candidates;
- never rank a distinct Clean/Dirty/Intro/Outro/Extended/Remix/Edit/Acapella/Instrumental version as disposable merely because another version exists;
- never convert `PREFERRED_COPY` into deletion automatically;
- show the evidence score/breakdown instead of only a final winner.

## 4. Backup restore verification

Goal: prove that a backup is usable, not merely present.

Each backup verification record should retain:

- backup ID;
- created timestamp;
- source roots;
- file count;
- byte count;
- manifest/hash algorithm;
- source-manifest SHA-256;
- backup-manifest SHA-256;
- full or sampled hash verification result;
- read/decode verification result for sampled audio;
- Serato backup presence/readability where applicable;
- restore-canary result;
- verifier tool/repo SHA;
- PASS/FAIL with explicit failures.

A restore canary should copy selected backed-up files to an isolated temporary location, hash/read them, and then remove the temporary restored copies. It must never restore over the live library during verification.

Rules:

- backup existence alone is insufficient proof;
- inability to verify a required backup is a mutation blocker;
- failed samples are not averaged away;
- no production apply should claim `verified backup` unless the verification record identifies exactly which backup and what was checked.

## 5. Source / provenance history

Goal: retain useful origin/context evidence without confusing it with Genre.

Possible evidence fields:

- original top-level folder;
- original subfolder/crate-like context;
- source batch such as NewFiles / SoundCloud / DJ pool / event folder / old archive;
- event name when independently supported;
- acquisition/download batch identifier when known;
- prior paths from move/undo history;
- evidence source and confidence.

Storage rule:

- Genre remains musical style;
- Grouping may contain approved evidenced source/event context;
- the decision ledger should preserve richer provenance even when not all of it belongs in an audio tag.

Unknown provenance remains unknown. Do not backfill a source from filename vibes or guesses.

## 6. DJ readiness status

Goal: translate many technical/evidence reports into an operational view that answers: "Can I trust this track at a gig, and what still needs attention?"

Suggested states:

- `READY`;
- `REVIEW_BEFORE_GIG`;
- `CLEAN_DIRTY_UNKNOWN`;
- `GENRE_REVIEW`;
- `GRID_REVIEW`;
- `AUDIO_QUALITY_REVIEW`;
- `SERATO_REFERENCE_REVIEW`;
- `BROKEN_OR_UNREADABLE`.

A readiness state is a summary of evidence, not a destructive decision.

Recommended precedence for warnings:

1. unreadable/broken source;
2. Serato reference/content integrity problem;
3. Clean/Dirty conflict or unknown when version matters;
4. cue/grid/BPM review;
5. material audio-health problem;
6. unresolved Genre/provenance issue;
7. otherwise `READY` when required checks have actually run.

Do not label a track `READY` merely because no issues have yet been detected. `READY` should require completion of the defined checks for that scope.

## 7. Unified master review / dashboard report

Goal: provide one human-facing view instead of forcing review across many CSV/JSON files.

This does not need to be a web application. A generated HTML and/or Markdown report is sufficient initially.

Library-level summary should include when available:

- total tracks;
- fully resolved tracks;
- version unresolved;
- Genre unresolved;
- exact-duplicate groups;
- audio-equivalent groups;
- protected related versions;
- technical-health review items;
- BPM/key/grid review items;
- Serato reference issues;
- manual listens remaining;
- reviewed permanent decisions;
- archive/quarantine candidates, if that layer is later approved;
- last verified backup status.

Track-level rows should join by SHA-256/content identity where possible:

- path / filename;
- Artist / Title;
- version status;
- Genre status;
- duplicate/equivalence status;
- audio-health status;
- BPM/key/grid status;
- Serato usage/reference status;
- provenance;
- readiness status;
- review priority;
- durable decision, if any;
- evidence provenance links/run IDs.

Unknown fields should remain blank/unknown rather than fabricated.

## 8. Durable decision memory

The decision ledger is the authority for human-reviewed conclusions so the same exact content is not repeatedly re-litigated.

Minimum fields:

- `content_id`;
- `sha256`;
- optional `audio_fingerprint`;
- `current_path`;
- `classification`;
- `decision`;
- `reason`;
- `evidence`;
- `confidence`;
- `reviewed_by`;
- `reviewed_at`;
- `run_id`;
- `supersedes`.

Examples:

- confirmed Clean/Dirty;
- confirmed Genre;
- protected remix/edit/version;
- reviewed non-duplicate;
- preferred copy within a proven equivalent group;
- manual-listen outcome;
- explicit archive/quarantine decision.

If content changes and SHA-256 changes, do not blindly inherit the prior decision. Link the old and new state for review instead.

## Recommended implementation order

Do not build these before using the current recheck tools.

1. Run current version + Genre recheck and measure the unresolved remainder.
2. Run/implement the audio-intelligence roadmap stages that materially reduce that remainder.
3. Build the Serato integrity checker.
4. Add backup restore verification before any further large mutation campaign.
5. Join Serato/event usage into review priority.
6. Add canonical keeper recommendations for already-proven duplicate/equivalent groups.
7. Add cue/grid anomaly checks when Serato parsing and independent BPM evidence are trustworthy.
8. Add DJ readiness status.
9. Generate the unified master review/dashboard report.
10. Persist human-reviewed outcomes in the durable decision ledger.
11. Transition recurring operation to `references/library-maintenance-roadmap.md` so future cycles use analysis caching, snapshot drift detection, controlled new-music intake, relocation history, provenance, canary testing, and versioned analysis instead of starting over.

## Non-negotiable safety boundary

These layers must not:

- delete audio automatically;
- auto-archive tracks;
- rewrite Serato databases/crates/history;
- overwrite cues/loops/beatgrids automatically;
- infer Clean from lack of detected profanity;
- infer unused from lack of a Serato relationship;
- collapse related DJ versions into duplicates;
- treat technical quality alone as a deletion decision;
- mark `READY` without having run the required checks.

All approved mutations remain subject to mandatory lock ownership, reviewed preview, verified backup, per-file preservation checks, complete undo evidence, and post-apply verification.
