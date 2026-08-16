# Library maintenance roadmap

This file defines the post-cleanup maintenance architecture for the DJ library. It complements:

- `references/needs-check-preview.md` — current unresolved version/Genre recheck;
- `references/audio-intelligence-roadmap.md` — read-only audio-derived evidence;
- `references/library-readiness-roadmap.md` — Serato/library integrity, backup proof, review priority, readiness, and durable decisions.

The purpose here is different: once expensive evidence has been computed and reviewed, future maintenance should reuse it safely instead of starting from zero.

Operating rule:

**content identity first -> reuse proven analysis when inputs/tool versions match -> analyze only changed/new content -> report drift -> review exceptions -> approved mutations only through the existing lock/backup/apply/verify/undo workflow.**

## 1. SHA-256 analysis cache

Goal: avoid recomputing expensive read-only analysis for unchanged audio.

Cache expensive results by exact content identity (`sha256`) plus the analysis implementation identity.

Candidate cached analyses:

- audio fingerprint;
- Clean/Dirty transcription evidence;
- BPM/beat positions;
- key estimate;
- loudness / peak / silence measurements;
- AI Genre second opinion;
- intro/outro structural analysis;
- decode/file-health result;
- optional vocal-separation/transcription experiment output.

Minimum cache key should include:

- `sha256`;
- `analysis_type`;
- `tool_name`;
- `tool_version`;
- `model_name` / `model_version` when applicable;
- relevant rule/config hash;
- analysis schema version.

Minimum cache record should include:

- result payload or path to ignored local result artifact;
- created timestamp;
- repo SHA/run ID;
- command/config provenance;
- status (`PASS`, `REVIEW`, `ERROR`, etc.);
- raw measurement provenance when needed.

Rules:

- same SHA-256 + same analysis identity may reuse the prior result;
- changed content hash requires new analysis;
- changed model/tool/rule/config must not silently reuse an incompatible cached result;
- cache reuse must be visible in reports (`CACHE_HIT` vs `ANALYZED`);
- cache files/results containing private library paths remain ignored/local unless deliberately sanitized.

## 2. Library drift / change detector

Goal: compare trusted snapshots and show only what changed.

Required change classes:

- `NEW` — content/path newly present;
- `REMOVED` — previously present content/path no longer observed;
- `MOVED` — same SHA-256/content identity at a new path;
- `CONTENT_CHANGED` — same logical/path record but bytes/hash changed;
- `METADATA_CHANGED` — audio content unchanged but tracked metadata changed;
- `UNCHANGED` — same content/state for fields within the comparison scope.

Where evidence is insufficient to distinguish move vs remove+new, report ambiguity rather than inventing a move.

Drift reports should include:

- baseline run ID/manifest hash;
- current run ID/manifest hash;
- old/new paths;
- old/new SHA-256 where applicable;
- metadata deltas;
- whether expensive analysis can be reused;
- whether a durable human decision still applies to the exact content hash.

This should become the normal future maintenance entry point instead of full manual re-review.

## 3. `New Music - Review` intake gate

Goal: keep the cleaned Active Library from immediately becoming chaotic again.

Recommended intake sequence:

1. read-only inventory + SHA-256;
2. decode/technical-health check;
3. exact duplicate + audio-equivalent candidate check;
4. explicit version/Clean/Dirty evidence;
5. optional unresolved Clean/Dirty audio evidence;
6. Genre deterministic evidence;
7. BPM/key sanity analysis;
8. provenance/source capture;
9. manual review only for unresolved/conflicting items;
10. approved promotion to Active Library through the normal mutation controls.

Suggested intake states:

- `INTAKE_READY`;
- `DUPLICATE_REVIEW`;
- `VERSION_REVIEW`;
- `GENRE_REVIEW`;
- `TECHNICAL_REVIEW`;
- `SERATO_PREP_REVIEW`;
- `BLOCKED_UNREADABLE`.

No track should be rejected solely because optional enrichment (AI Genre, key, energy, etc.) is unavailable. Required vs optional gates must be explicit.

## 4. Serato relocation / path map

Goal: retain a recovery mapping between historical and current paths without rewriting Serato automatically.

Minimum mapping fields:

- content identity / SHA-256;
- old path;
- new/current path;
- move/run ID;
- timestamp;
- evidence source (apply log, undo log, drift match, manual review);
- confidence;
- current existence/hash verification;
- related Serato reference(s) when safely parsed.

Rules:

- path history is evidence, not authority to rewrite Serato;
- a one-to-many or many-to-one ambiguity must remain unresolved;
- content hash should be used to validate a relocation whenever possible;
- keep historical paths even after a successful move so restore/relink work has a paper trail.

## 5. DJ utility analysis

Goal: provide optional set-preparation descriptors without turning subjective musical traits into metadata authority.

Potential read-only descriptors:

- energy / intensity estimate;
- vocal vs instrumental tendency;
- danceability estimate;
- intro/outro usable length;
- broad acoustic/electronic characteristics;
- tempo range / rhythmic confidence;
- optional warm-up / peak / cooldown suggestion.

These may support future smart review/crate suggestions such as:

- `Warm Up`;
- `Peak`;
- `Cooldown`;
- `Open Format` candidates;
- `Vocal` / `Mostly Instrumental` review groupings.

Guardrails:

- these are DJ-utility suggestions, not Genre truth;
- no automatic crate mutation initially;
- model outputs must carry model/version/confidence;
- existing human crate choices and gig history outrank model suggestions;
- low-confidence or vocabulary-mismatched outputs remain raw evidence only.

## 6. Analysis-version provenance

Goal: make every automated result reproducible and prevent silent reinterpretation when tools evolve.

Every automated evidence record should retain, where applicable:

- analysis name/type;
- tool/library name;
- tool/library version;
- model name/version/checksum;
- rule/config file SHA-256;
- script path + repo SHA;
- schema version;
- run ID;
- input SHA-256;
- timestamp;
- hardware/backend note when it can materially affect reproducibility.

A newer analysis may supersede an older one, but it must not erase the old provenance. Reports should be able to distinguish:

- `CURRENT_ANALYSIS`;
- `STALE_ANALYSIS_VERSION`;
- `REANALYSIS_REQUIRED`;
- `LEGACY_RESULT_RETAINED`.

Human decisions remain separate from machine-analysis versions; a model upgrade must not silently revoke a reviewed decision.

## 7. Canary / synthetic DJ test library

Goal: prove safety and classifier behavior before changes touch the real library.

Maintain a tiny synthetic or legally shareable/local-only fixture set representing known edge cases such as:

- explicit `[Clean]`;
- explicit `[Dirty]` / Explicit Edit;
- title containing the word "dirty" but not a Dirty version label;
- Intro/Outro/Extended/Remix/Edit/Acapella/Instrumental variants;
- byte-identical duplicate;
- re-encoded audio-equivalent pair;
- related-version pair that must not collapse;
- corrupt/unreadable audio;
- very short/truncated sample;
- ID3v2.2 case;
- embedded Serato-frame preservation case;
- Unicode/special-character filename;
- case-only path/rename scenario where platform relevant;
- meaningful numbers such as `21 Savage`, `112`, years, and BPM values;
- duplicate-destination/collision scenario;
- stale/missing path reference scenario.

Required principle:

**new classifier/mutation behavior must pass the canary suite before any production run is eligible for approval.**

Synthetic fixtures must not contain private production audio or private Serato databases in Git.

## 8. Storage-impact report

Goal: quantify possible storage recovery without creating pressure to delete.

For proven exact-duplicate groups and strong reviewed audio-equivalent groups, report:

- group ID;
- files/paths;
- individual sizes;
- total group bytes;
- protected related-version count;
- recommended keeper status when available;
- theoretical reclaimable bytes if reviewed alternates were later archived/deleted;
- confidence/evidence class;
- Serato/gig/provenance protection signals.

Library summary may include:

- bytes in exact-duplicate redundant instances;
- bytes in reviewed audio-equivalent alternates;
- bytes in protected related versions (not reclaimable by dedupe reasoning);
- archive size;
- quarantine size;
- theoretical maximum reclaimable storage under reviewed assumptions.

Important:

- storage estimates are informational only;
- no deletion or archive action follows automatically;
- do not count related Clean/Dirty/Intro/Outro/Extended/Remix/Edit/Acapella/Instrumental assets as duplicate reclaimable space merely because they share a song family;
- if keeper status is unresolved, reclaimable bytes must be marked theoretical/unresolved.

## 9. Maintenance-cycle master flow

After the initial cleanup/readiness work is complete, the normal recurring cycle should become:

1. capture a verified read-only inventory snapshot;
2. compare with the previous trusted snapshot;
3. classify drift (`NEW`, `MOVED`, `REMOVED`, `CONTENT_CHANGED`, `METADATA_CHANGED`);
4. reuse cache for unchanged exact content when tool/model/rule identity still matches;
5. analyze only new/changed/stale-analysis content;
6. route new files through `New Music - Review` intake;
7. re-run Serato integrity only for affected references plus periodic full audits;
8. update readiness/reporting;
9. retain human-reviewed decisions by exact content identity;
10. create mutations only from separately reviewed plans using lock/backup/apply/verify/undo.

## Recommended implementation order

Do not build all maintenance machinery before the first real recheck/readiness pass.

1. Implement SHA-256 analysis caching alongside the first expensive audio-analysis tool.
2. Implement snapshot drift comparison immediately after there are at least two trusted inventories.
3. Turn `New Music - Review` into the intake gate once the current library cleanup is stable.
4. Persist Serato/path relocation mappings from already-verified move/undo evidence.
5. Add analysis-version provenance to every new cached analyzer from day one.
6. Build/expand the synthetic canary suite before additional production mutation engines are introduced.
7. Add storage-impact reporting once exact/audio-equivalent evidence is mature.
8. Add DJ-utility descriptors only after operational cleanup/integrity work is stable; they are enrichment, not a blocker.

## Non-negotiable safety boundary

These maintenance layers must not:

- delete or archive audio automatically;
- rewrite Serato data automatically;
- reuse analysis across changed SHA-256 content;
- reuse incompatible model/tool/rule versions silently;
- treat missing Serato history as proof of non-use;
- treat DJ-utility model output as Genre authority;
- count protected related versions as disposable duplicate storage;
- commit private production inventory/audio/Serato artifacts to Git.

All approved mutations remain subject to the repository's mandatory coordination lock, reviewed preview, verified backup, preservation checks, undo evidence, and post-apply verification controls.
