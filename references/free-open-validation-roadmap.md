# Free/open external validation roadmap

This layer answers a different question from the local file analyzers:

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
2. No external lookup may silently rewrite Artist, Title, Genre, Year/date, version, BPM, Key, Grouping, or Serato data.
3. `NOT_RUN`, `FAILED`, `RATE_LIMITED`, `PROVIDER_UNAVAILABLE`, and `NO_MATCH` are different states.
4. A database match for Artist/Title does not prove Clean/Dirty, Genre, DJ Intro status, or that two files are safe to collapse.
5. The normal network path sends compact fingerprints/identifiers and required metadata only, never the source audio file.
6. Successful external evidence is cached so unchanged files are not repeatedly queried.
7. Human-reviewed decisions remain higher authority than lower-confidence automated suggestions unless explicitly reopened.
8. External service availability, terms, and rate limits are runtime dependencies and must be checked rather than assumed permanently true.

## Verified no-cost core stack

### FFmpeg / ffprobe

Use for:

- decode/readability verification;
- duration, codec, bitrate, sample-rate and channel evidence;
- loudness/silence/technical analysis;
- decoded-audio support for fingerprint workflows.

FFmpeg is free/open-source. Upstream states that FFmpeg is LGPL 2.1-or-later by default; builds using optional GPL components become GPL 2-or-later. Persist the installed version and build configuration in run provenance.

### Chromaprint / `fpcalc`

Use for:

- generating a compact acoustic fingerprint from decoded audio;
- creating recording-identity candidates across renamed/re-encoded/container-different files;
- producing the fingerprint used for AcoustID lookup.

Important limitation: a Chromaprint result is **identification evidence**, not cryptographic proof that two files are interchangeable. It must not by itself collapse Clean/Dirty/Intro/Outro/Extended/Remix/Edit/Acapella/Instrumental variants.

Current upstream Chromaprint project-authored code is MIT-licensed. Packaged/binary licensing can still depend on linked components and build configuration, so provenance must record the actual installed build/package.

### pyacoustid — optional wrapper

Use for:

- calling Chromaprint/`fpcalc` from Python;
- optionally performing AcoustID web-service requests.

The library is MIT-licensed. Local fingerprint generation can be offline; its AcoustID-client role requires network access. Keep direct `fpcalc` + HTTP/JSON as a supported fallback so pyacoustid is convenience, not a hard dependency.

### AcoustID

Use for:

- mapping a Chromaprint fingerprint + duration to AcoustID results;
- retrieving MusicBrainz recording identifiers when available.

Verified service facts:

- AcoustID describes the service as based entirely on open-source software;
- non-commercial use of the web service is free after registering an application/client key;
- fingerprint lookup sends the fingerprint and whole-track duration, not the source audio file;
- the published usage guideline says no more than 3 requests per second;
- the public fingerprint database is CC BY-SA 3.0, while the MusicBrainz-AcoustID mapping is described as public domain.

Use **lookup only**. Do not automatically submit new fingerprints from this cleanup system.

Provider maintenance/unavailability must surface explicitly. At review time AcoustID reports a read-only maintenance state; because this project is lookup-only, that does not authorize assuming submissions work or that service state will remain unchanged.

### MusicBrainz

Use for:

- resolving recording MBIDs returned by AcoustID;
- corroborating Artist credit, Title, recording identity, length, ISRCs, release/release-group relationships and release dates;
- distinguishing a Recording from the Releases on which it appears.

Verified service/data facts:

- non-commercial API use is free;
- no API key is currently required for normal lookup requests;
- a meaningful User-Agent is required;
- client applications must not exceed one request per second, and source IPs are also rate-limited around one request per second unless separately arranged;
- core database data is CC0;
- supplementary data is CC BY-NC-SA 3.0;
- the MusicBrainz Server source is GPLv2.

### Important date correction

A MusicBrainz **Recording does not have one generic recording-date property**. The Recording entity exposes properties such as title, artist credit, length, ISRC, MBID, disambiguation and relationships. Search/API data can expose dates of releases containing the recording, including an earliest release date.

Therefore keep these concepts separate:

- earliest known release date containing the recording;
- release-group first-release date;
- date of the particular matched Release/reissue;
- performance/recording-session date **only when separately supported** by an appropriate relationship, annotation, official source, or other explicit evidence;
- local acquisition/event date.

A later reissue must never silently turn an older recording into a song from the reissue year.

### faster-whisper

Use for:

- local transcription evidence for unresolved Clean/Dirty review;
- optional timestamps for matched, user-reviewed explicit-language terms.

The faster-whisper codebase is MIT-licensed and inference can run locally. However, loading a model by a Hub name can automatically download model files from Hugging Face. A strict offline run therefore requires the model files to be staged locally first and loaded from a local path/cache with download disabled.

Track separately:

- faster-whisper package version;
- model identifier/local path;
- model revision/hash;
- model license/source where known;
- compute type;
- transcription options.

The code license and the model-weight license are not assumed to be the same thing.

Semantic rule: detected reviewed explicit language can support `LIKELY_DIRTY`; absence of detected explicit language can never prove `CLEAN`.

### Python standard library + local SQLite/JSON/CSV

Use for SHA-256, joins, cache, manifests, rate limiting, evidence states, decision-ledger integration and deterministic reports. Prefer simple local storage until scale requires more.

## Deliberately not required

### Discogs

Useful for optional manual corroboration, but not required for cleanup correctness and not part of the free/open core.

### Paid recognition / metadata / transcription APIs

Do not require Gracenote, paid recognition APIs, commercial LLM/audio APIs, paid metadata subscriptions, or paid transcription services for the core path.

### Essentia

May remain an optional local analyzer, but is not a mandatory core dependency because its licensing/use conditions differ from the permissive components above and can matter if the project is reused commercially.

## Validation pipeline

### Stage 1 — stable local identity packet

For each candidate needing external validation, generate/reuse:

- SHA-256;
- root-relative path;
- current Artist / Title / Album / Year / Genre/version labels;
- duration;
- codec/container;
- bitrate/sample rate/channels;
- Chromaprint fingerprint;
- analyzer/tool versions;
- run ID.

Do not start network lookup until this packet is stable.

### Stage 2 — AcoustID lookup

Input:

- Chromaprint fingerprint;
- whole-track duration;
- registered application client key.

Retain:

- provider availability state;
- lookup status;
- AcoustID result ID;
- score returned by AcoustID;
- returned MusicBrainz recording IDs;
- response timestamp;
- raw-response hash/cache key;
- provider/parser version.

Suggested states:

- `ACOUSTID_MATCH_STRONG`;
- `ACOUSTID_MATCH_MULTIPLE`;
- `ACOUSTID_MATCH_WEAK`;
- `ACOUSTID_NO_MATCH`;
- `ACOUSTID_NOT_RUN`;
- `ACOUSTID_RATE_LIMITED`;
- `ACOUSTID_PROVIDER_UNAVAILABLE`;
- `ACOUSTID_FAILED`.

Do not invent a universal strong/weak threshold until it has been calibrated on a known local test set. A no-match is not evidence that local metadata is wrong.

### Stage 3 — MusicBrainz lookup

For credible returned recording MBIDs, fetch only the evidence needed for the unresolved question.

Retain separately:

- recording MBID;
- Artist credit;
- recording Title;
- recording length where available;
- ISRCs where available;
- release/release-group IDs;
- release titles;
- earliest release date containing the recording where exposed;
- release-group first-release date where exposed;
- matched-release date/country/status;
- disambiguation/version comments;
- lookup timestamp;
- raw-response hash/cache key.

Never collapse these dates into one unlabeled generic `year` field.

### Stage 4 — field-specific validation

#### Recording identity

A strong automated corroboration case can include:

- stable local SHA/fingerprint;
- a high-quality AcoustID result;
- one credible MusicBrainz recording identity;
- compatible duration;
- no material Artist/Title/version contradiction.

Statuses:

- `IDENTITY_CORROBORATED`;
- `IDENTITY_AMBIGUOUS`;
- `IDENTITY_CONFLICT`;
- `IDENTITY_UNVERIFIED`.

`CORROBORATED` is not the same as cryptographically proven.

#### Artist / Title

External canonical Artist/Title may produce a normalization proposal, but never overwrite local DJ-relevant naming without review. Featured-credit formatting, aliases, remix credits, punctuation/stylization and local version suffixes can all be intentionally different.

#### Year / date

Store the exact date concept and source. Use release dates for release facts; use separately supported evidence for recording/performance-session dates.

#### Version

External databases may corroborate explicit remix/radio/live/version names. They do not automatically prove local `Clean`, `Dirty`, `Intro`, `Outro`, `Extended`, `Acapella`, or `Instrumental` status. Those require local labels, audio evidence, related-version comparison, or listening.

#### Genre

Genre is classification evidence, not a single objectively provable identity field. External tags/genres remain low-authority corroboration only and must not outrank durable human decisions, strong prior/local evidence, or approved deterministic project rules.

## Triangulation and conflict logic

Multiple weak sources must not become fake certainty merely because there are several of them.

Examples:

- strong AcoustID candidate + consistent MusicBrainz recording + compatible local duration -> strong identity corroboration;
- AcoustID/MusicBrainz identity conflicts with an explicitly protected local remix label -> `VERSION_IDENTITY_CONFLICT`;
- `[Clean]` filename + detected reviewed explicit-language evidence -> Clean/Dirty conflict regardless of MusicBrainz identity;
- external Genre differs from approved deterministic/local evidence -> Genre review, not overwrite;
- several plausible AcoustID/MusicBrainz candidates -> ambiguous, no apply-ready proposal.

## Privacy and network boundary

Normal external requests may include only what is necessary:

- Chromaprint fingerprint;
- duration;
- AcoustID application/client identifier;
- MusicBrainz MBIDs;
- narrowly scoped textual metadata search terms only when an MBID path is unavailable and a search is explicitly required.

Do not automatically upload:

- source audio files;
- Serato database/crates/history;
- cue/loop/beatgrid data;
- raw Whisper transcripts;
- local absolute filesystem paths;
- private event notes.

## Cache and rate-limit rules

Cache external evidence by stable content/provider/query identity. Cache keys should include, where applicable:

- SHA-256;
- fingerprint algorithm/version;
- provider;
- provider query type;
- MBID/result ID;
- parser/schema version.

Respect provider-specific limits independently:

- AcoustID: no more than 3 requests/second under its published guideline;
- MusicBrainz: no more than 1 request/second per client application, with source-IP throttling also applying.

Use backoff for rate-limit/server failures. Never reinterpret a provider outage as `NO_MATCH`.

Refresh cached evidence only when content changes, cache is invalidated, a human requests revalidation, or a provider/parser/schema migration requires it.

## Proposed implementation modules

These are implementation targets, not claims that they exist:

- `scripts/build_audio_identity_evidence.py`
  - SHA + media facts + `fpcalc` packet;
- `scripts/lookup_acoustid_evidence.py`
  - provider-state-aware, rate-limited, cache-aware lookup only;
- `scripts/lookup_musicbrainz_evidence.py`
  - MBID-oriented lookup with User-Agent and one-request-per-second compliance;
- `scripts/join_external_identity_evidence.py`
  - field-specific corroboration/conflict states;
- `scripts/check_validation_stack.py`
  - installed binary/package/model versions, credentials, offline readiness and provider availability;
- `references/external-validation-providers.json`
  - machine-readable provider/cost/network/license/provenance registry.

All scripts are read-only against production audio and Serato state.

## Required outputs

At minimum:

- `external-identity-evidence.csv`;
- `external-identity-summary.json`;
- `EXTERNAL-IDENTITY-PREVIEW.md`;
- provider/cache/run-manifest metadata.

Useful track-level fields include:

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
- `earliest_recording_release_date`;
- `release_group_first_release_date`;
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

For identity-related questions, a useful ordering is:

1. durable human-reviewed decision tied to exact content identity;
2. exact local cryptographic/content evidence where that question is actually cryptographic;
3. calibrated fingerprint evidence + consistent external recording identity;
4. explicit local structured metadata/version evidence;
5. corroborating external release metadata;
6. deterministic local filename/tag rule;
7. audio-model/AI inference;
8. unsupported filename guess.

This hierarchy is **field-specific**. Fingerprint + MusicBrainz can be strong Artist/Title identity evidence while still being weaker than actual audio evidence for Clean/Dirty.

## Implementation order

1. Keep the already-merged unresolved Clean/Dirty + Genre recheck as the first pass.
2. Implement local `fpcalc` identity packets and cache them by SHA-256.
3. Calibrate fingerprint/AcoustID result handling against known files before defining strong thresholds.
4. Add AcoustID lookup for unresolved identity/version-family cases.
5. Add MusicBrainz lookup by returned recording MBID.
6. Add field-specific conflict/triangulation reporting.
7. Feed only unresolved/conflicting results into human review.
8. Later use validated identities to strengthen duplicate/equivalent and version-family analysis.

Do not externally validate every library track blindly. Prioritize unresolved, conflicting, duplicate/equivalent, and high-usage tracks.

## Definition of success

The layer must be able to distinguish, with provenance:

- **mathematically proven facts**;
- **strongly corroborated external identity**;
- **classification/opinion**;
- **conflict**;
- **provider unavailable/failure**;
- **never checked**;
- **still requires listening/human judgment**.

More metadata must never be converted into unjustified certainty.
