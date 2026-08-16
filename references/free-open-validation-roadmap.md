# Free/open external validation roadmap

This layer exists to answer a different question from the local file analyzers:

> **Does independent evidence support what we think this audio actually is?**

The design is local-first, evidence-first, and no-cost for the intended personal/non-commercial library workflow. No paid metadata, transcription, fingerprinting, or AI API is required for the core path.

It complements:

- `references/needs-check-preview.md`;
- `references/audio-intelligence-roadmap.md`;
- `references/library-readiness-roadmap.md`;
- `references/library-maintenance-roadmap.md`;
- `references/evidence-assurance-roadmap.md`.

## Non-negotiable rules

1. External databases are corroborating evidence, not automatic authority over the library.
2. No external lookup may silently rewrite Artist, Title, Genre, Year, version, BPM, Key, Grouping, or Serato data.
3. A network lookup that was not run, failed, was rate-limited, or returned no match must remain distinguishable from a successful validation.
4. Different questions require different evidence. A database match for Artist/Title does not prove Clean/Dirty, Genre, DJ Intro status, or that two files are safe to collapse.
5. Do not upload source audio to a third party as part of the normal validation path. Prefer compact fingerprints/identifiers.
6. Cache successful external evidence so unchanged files are not repeatedly queried.
7. Human-reviewed decisions remain higher authority than lower-confidence automated external suggestions unless the human decision is explicitly reopened.

## Core no-cost stack

### FFmpeg / ffprobe

Role:

- decode/readability verification;
- duration, codec, bitrate, sample-rate and channel evidence;
- loudness/silence/technical analysis;
- decoded-audio support for fingerprinting workflows.

License note: FFmpeg is free/open-source. The project states that FFmpeg is primarily LGPL 2.1-or-later, while builds using optional GPL components are GPL 2-or-later. Record the actual installed build/version in run provenance rather than assuming every binary has the same effective license/configuration.

### Chromaprint / `fpcalc`

Role:

- generate a compact acoustic fingerprint from decoded audio;
- identify near-identical recordings despite filename, container, bitrate, or re-encoding differences;
- provide the fingerprint used for AcoustID lookups.

Important limitation: Chromaprint is designed for audio identification / near-identical matching, not arbitrary semantic similarity. A fingerprint match does not mean different Clean/Dirty/Intro/Remix assets should be collapsed without version-family review.

License note: current upstream Chromaprint source states that project-authored code is MIT-licensed; effective packaged/binary licensing can depend on linked components. Persist the installed package/version/build identity in provenance.

### pyacoustid (optional Python wrapper)

Role:

- call `fpcalc` / Chromaprint from Python;
- perform AcoustID lookup calls;
- expose match scores/results for evidence reports.

The upstream pyacoustid project is MIT-licensed. The system should be able to fall back to direct `fpcalc` + HTTP/JSON so this wrapper is convenient, not mandatory.

### AcoustID

Role:

- map an acoustic fingerprint to AcoustID results and MusicBrainz recording identifiers;
- provide independent recording-identity corroboration.

Current service facts relevant to this project:

- AcoustID describes the service as based entirely on open-source software;
- the service is free for non-commercial applications after registering the application/client key;
- fingerprint lookup accepts the compact fingerprint + track duration rather than the source audio file;
- the public fingerprint database is available under an open data license and includes mappings to MusicBrainz identifiers.

Operational rule: use lookup only. Do not submit new fingerprints to AcoustID automatically from this cleanup system.

### MusicBrainz

Role:

- resolve MusicBrainz recording IDs returned from AcoustID;
- corroborate Artist, Title, recording identity, release/release-group relationships, ISRC relationships, labels and dates where present;
- distinguish a recording from the releases on which it appears.

Current service/data facts relevant to this project:

- non-commercial API use is free;
- no API key is currently required for normal lookups;
- clients must use a meaningful User-Agent;
- clients must remain at or below the published one-request-per-second application/IP guidance unless separately arranged;
- MusicBrainz core database data is CC0; supplementary data has a more restrictive Creative Commons license.

Operational rule: query only the fields required for unresolved identity/release validation. Do not treat community tags/genres as high-authority truth.

### faster-whisper

Role:

- local-only transcription evidence for unresolved Clean/Dirty review;
- optional timestamps for matched reviewed explicit-language terms.

The upstream project is MIT-licensed and runs locally. The normal workflow must not send raw audio or transcripts to a paid/cloud transcription API.

Important semantic rule: detected explicit language can support `LIKELY_DIRTY`; absence of detected explicit language can never prove `CLEAN`.

### Python standard library + local SQLite/JSON/CSV

Role:

- SHA-256 hashing;
- evidence joins;
- caching;
- manifests;
- rate limiting;
- decision ledger integration;
- deterministic reports.

Prefer simple local storage formats until scale or query needs justify something more complex.

## Deliberately not core dependencies

### Discogs

Discogs can be useful corroborating research, but it is not part of the required free/open validation core. Do not make cleanup correctness depend on it. If used manually later, record it as a separate external source with its own terms and evidence grade.

### Paid recognition / metadata / transcription APIs

Do not require Gracenote, ShazamKit/cloud recognition, commercial LLM/audio APIs, paid metadata subscriptions, or paid transcription services for the core workflow.

### Essentia

Essentia may remain an optional local analyzer for selected audio descriptors, but it should not be a mandatory core dependency because its licensing/use conditions differ from the permissive components above and may matter if this project is ever reused commercially.

## Validation pipeline

### Stage 1 — local identity packet

For every candidate track that needs external validation, generate/reuse:

- SHA-256;
- root-relative path;
- current Artist / Title / Album / Year / Genre/version labels;
- duration;
- codec/container;
- bitrate/sample rate/channels;
- Chromaprint fingerprint;
- analyzer versions;
- run ID.

Do not perform network lookups until the local identity packet is stable.

### Stage 2 — AcoustID lookup

Input:

- fingerprint;
- duration;
- registered application client key.

Retain:

- lookup status;
- AcoustID result ID;
- match score;
- returned MusicBrainz recording IDs;
- response timestamp;
- raw response hash/cache key;
- provider/version/provenance metadata.

Suggested states:

- `ACOUSTID_MATCH_STRONG`;
- `ACOUSTID_MATCH_MULTIPLE`;
- `ACOUSTID_MATCH_WEAK`;
- `ACOUSTID_NO_MATCH`;
- `ACOUSTID_NOT_RUN`;
- `ACOUSTID_RATE_LIMITED`;
- `ACOUSTID_FAILED`.

A no-match is not evidence that the local metadata is wrong.

### Stage 3 — MusicBrainz recording lookup

For each credible returned MBID, fetch only required recording/release evidence.

Retain separately:

- recording MBID;
- canonical Artist credit;
- recording Title;
- recording length where available;
- ISRCs where available;
- release/release-group IDs;
- release titles;
- release dates/countries/statuses where relevant;
- disambiguation/version comments;
- lookup timestamp;
- raw response hash/cache key.

Do not collapse `recording`, `release`, and `release-group` dates into one generic `year` field.

### Stage 4 — field-specific validation

#### Recording identity

Strongest automated external case:

- stable local SHA/fingerprint;
- strong AcoustID match;
- one credible MusicBrainz recording identity;
- compatible duration;
- no major Artist/Title/version contradiction.

Possible statuses:

- `IDENTITY_CORROBORATED`;
- `IDENTITY_AMBIGUOUS`;
- `IDENTITY_CONFLICT`;
- `IDENTITY_UNVERIFIED`.

#### Artist / Title

External canonical Artist/Title may produce a proposal or normalization suggestion, but must not overwrite local DJ-relevant naming without review.

Examples requiring review rather than automatic replacement:

- featured-artist formatting differences;
- aliases vs legal/canonical artist names;
- remix/DJ-name credits;
- punctuation/stylization differences;
- local version suffixes that carry DJ utility.

#### Year / date

Never store one external date without defining what it represents.

Keep distinct concepts where available:

- recording identity date, if explicitly supported;
- earliest known release date;
- release-group first release date;
- date of the particular matched release/reissue;
- local acquisition/event date.

A 2026 reissue of an older recording must not silently turn the recording into a 2026 song.

#### Version

External databases may corroborate labels such as remix/radio/live/version names when explicitly present, but they do not automatically prove local `Clean`, `Dirty`, `Intro`, `Outro`, `Extended`, `Acapella`, or `Instrumental` status.

Use local explicit labels, audio evidence, related-version comparison, and human listening for those fields.

#### Genre

Genre remains a classification with legitimate ambiguity, not a single externally provable identity field.

External genre/tag data may be displayed as low-authority corroboration only. It must not outrank:

1. durable human-reviewed project decisions;
2. strong prior/local evidence;
3. deterministic approved project rules.

## Triangulation / conflict logic

The system should never turn multiple weak sources into fake certainty merely because there are several of them.

Examples:

- AcoustID strong + MusicBrainz recording consistent + local duration consistent => strong identity corroboration.
- AcoustID strong but MusicBrainz title/version conflicts with explicit protected local remix label => `VERSION_IDENTITY_CONFLICT`, human review.
- filename says `[Clean]` but transcript detects reviewed explicit terms => Clean/Dirty conflict regardless of MusicBrainz identity agreement.
- MusicBrainz says R&B while deterministic project evidence says Hip-Hop => Genre review, not external overwrite.
- multiple AcoustID/MusicBrainz candidates with similar scores => ambiguous, no auto-proposal.

## Privacy / network boundary

Normal external requests may include only what is necessary:

- Chromaprint fingerprint;
- duration;
- AcoustID client identifier;
- MusicBrainz IDs / textual metadata lookup terms when needed.

Do not automatically upload:

- full audio files;
- Serato database/crates/history;
- cue/loop/beatgrid data;
- raw Whisper transcripts;
- local absolute filesystem paths;
- private event notes.

## Caching and rate limits

External validation must integrate with the SHA/fingerprint analysis cache.

Cache key should include, as appropriate:

- SHA-256;
- fingerprint algorithm/version;
- provider;
- provider query type;
- returned MBID/result ID;
- relevant schema/parser version.

MusicBrainz requests must be serialized/rate-limited to the published limit and use a meaningful project User-Agent.

Do not repeatedly poll unchanged metadata. Refresh only when:

- the file/content identity changes;
- the cached result is explicitly invalidated;
- a human requests revalidation;
- a provider/parser migration requires it.

## Proposed implementation modules

These are implementation targets, not claims that they already exist:

- `scripts/build_audio_identity_evidence.py`
  - SHA + media facts + `fpcalc` fingerprint packet;
- `scripts/lookup_acoustid_evidence.py`
  - rate-limited/cache-aware lookup only;
- `scripts/lookup_musicbrainz_evidence.py`
  - MBID-oriented recording/release lookup with one-request-per-second compliance;
- `scripts/join_external_identity_evidence.py`
  - field-specific corroboration/conflict states;
- `scripts/check_validation_stack.py`
  - report installed tool paths/versions and whether optional network credentials are configured;
- `references/external-validation-providers.json`
  - machine-readable provider/cost/network/evidence-authority registry.

All scripts should be read-only against production audio and Serato state.

## Required outputs

At minimum:

- `external-identity-evidence.csv`;
- `external-identity-summary.json`;
- `EXTERNAL-IDENTITY-PREVIEW.md`;
- provider/cache/run-manifest metadata.

Useful track-level columns:

- `sha256`;
- `path`;
- `fingerprint_status`;
- `acoustid_status`;
- `acoustid_score`;
- `acoustid_id`;
- `musicbrainz_recording_id`;
- `external_artist`;
- `external_title`;
- `external_recording_length_ms`;
- `external_isrcs`;
- `earliest_release_date`;
- `matched_release_date`;
- `identity_status`;
- `artist_title_status`;
- `date_status`;
- `version_status`;
- `conflict_reason`;
- `evidence_grade`;
- `lookup_timestamp`;
- `provider_response_hash`.

## Evidence authority for this layer

Recommended order for identity-related decisions:

1. durable human-reviewed decision tied to exact content identity;
2. exact local cryptographic/content evidence where applicable;
3. strong audio fingerprint + consistent MusicBrainz recording identity;
4. explicit local structured metadata/version evidence;
5. corroborating external release metadata;
6. deterministic local filename/tag rule;
7. audio model / AI inference;
8. unsupported filename guess.

This hierarchy is field-specific. For example, fingerprint + MusicBrainz may be excellent Artist/Title identity evidence while still being weaker than actual audio evidence for Clean/Dirty.

## Implementation order

1. Keep the already-merged unresolved Clean/Dirty + Genre recheck as the first pass.
2. Implement local `fpcalc` identity packets and cache them by SHA-256.
3. Add AcoustID lookup for unresolved identity/version-family cases.
4. Add MusicBrainz lookup by returned MBID.
5. Add field-specific conflict/triangulation reporting.
6. Feed only unresolved/conflicting results into human review.
7. Later use validated identities to strengthen duplicate/equivalent and version-family analysis.

Do not web-validate every library track blindly. External validation is most valuable for unresolved, conflicting, duplicate/equivalent, and high-usage tracks.

## Definition of success

The layer is successful when it can say, with evidence provenance:

- **what is mathematically proven**;
- **what is strongly corroborated externally**;
- **what remains a classification/opinion**;
- **what conflicts**;
- **what was never checked**;
- **what still requires listening/human judgment**.

It must never convert availability of more metadata into unjustified certainty.