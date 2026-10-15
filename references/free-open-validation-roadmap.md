# Free/open external validation roadmap

This layer asks one narrow question:

> **Does independent evidence support what we think this audio actually is?**

It is local-first, evidence-first, and designed so the intended personal/non-commercial cleanup path does not require a paid API.

It complements the existing needs-check, audio-intelligence, readiness, maintenance, and evidence-assurance roadmaps.

## Non-negotiable rules

1. External databases are corroborating evidence, not automatic authority over the library.
2. External lookup must never silently rewrite Artist, Title, Genre, Year/date, version, BPM, Key, Grouping, or Serato data.
3. `NOT_RUN`, `FAILED`, `RATE_LIMITED`, `PROVIDER_UNAVAILABLE`, and `NO_MATCH` are distinct states.
4. Artist/Title identity evidence does not prove Clean/Dirty, Genre, DJ Intro status, or duplicate disposability.
5. Full source audio, Serato data, raw transcripts, absolute paths, and private event notes are not sent to external lookup providers.
6. All external requests use HTTPS.
7. Provider credentials are supplied from local configuration/environment and are never committed.
8. Human-reviewed decisions remain higher authority than lower-confidence automated suggestions unless explicitly reopened.
9. Provider availability, terms, licensing, and rate limits are runtime dependencies and must be rechecked rather than assumed permanent.

## Verified no-cost core stack

### FFmpeg / ffprobe

Use locally for decode/readability checks, duration, codec, bitrate, sample rate, channels, loudness, silence, and supporting audio decoding.

FFmpeg is LGPL 2.1-or-later by default; optional GPL components make a build GPL, and some build configurations can be nonfree/unredistributable. Record the actual binary version and build configuration rather than assuming all FFmpeg binaries are equivalent.

### Chromaprint / `fpcalc`

Use locally to generate compact fingerprints for audio-identification candidates.

Chromaprint is designed for near-identical/full-file identification, not arbitrary semantic similarity. A match is **identification evidence**, not cryptographic proof that two DJ files are interchangeable.

Current upstream project-authored Chromaprint code is MIT-licensed, but effective installed-library/`fpcalc` licensing can depend on linked FFmpeg/FFT components and build choices. Older AcoustID licensing text still describes Chromaprint as LGPL, so record the actual installed build/dependencies instead of relying on one summary page.

Important implementation detail: `fpcalc` fingerprints only the first **120 seconds by default**, while its normal whole-file result reports the file's overall duration. AcoustID lookup requires the **whole audio-file duration**. Never substitute the fingerprint-window length for the whole-track duration.

### pyacoustid — optional convenience wrapper

pyacoustid is MIT-licensed and can call Chromaprint/`fpcalc` locally. Its network lookup role is optional.

Current upstream source still declares a default API base of `http://api.acoustid.org/v2/`. Therefore this project must **not rely on that default for network lookup**. If pyacoustid is used for lookup, explicitly configure AcoustID's documented HTTPS endpoint; otherwise use direct `fpcalc` plus a small verified HTTPS JSON client.

### AcoustID

Use for fingerprint lookup and MusicBrainz recording-ID mapping.

Verified facts:

- the service is free for registered non-commercial applications;
- lookup requires a registered application/client key;
- lookup sends a fingerprint plus the **whole-track duration**, not the source audio file;
- the published limit is no more than **3 requests per second**;
- the public fingerprint database is CC BY-SA 3.0;
- the MusicBrainz-AcoustID mapping is described as public domain.

This project is **lookup-only**. Do not submit fingerprints. A user submission key is therefore unnecessary and must not be requested or stored.

The application client key belongs in local configuration/environment, not the repository.

At review time AcoustID reports a read-only maintenance state. Provider maintenance or outage must become `PROVIDER_UNAVAILABLE`/`FAILED`, never `NO_MATCH`.

### MusicBrainz

Use for recording MBID resolution and corroboration of Artist credit, Title, recording length, ISRCs, release/release-group relationships, and release dates.

Verified facts:

- non-commercial API use is free;
- no API key is currently required for normal lookup requests;
- a meaningful User-Agent is required;
- client applications must not exceed **1 request per second**;
- source-IP throttling also applies around 1 request/second unless separately arranged;
- core database data is CC0;
- supplementary data is CC BY-NC-SA 3.0;
- MusicBrainz Server source is GPLv2.

#### Date model

A MusicBrainz **Recording does not have one generic recording-date property**. Keep these distinct:

- earliest known release containing the recording;
- release-group first-release date;
- matched Release/reissue date;
- performance/recording-session date only when separately and explicitly supported;
- local acquisition/event date.

A later reissue must not turn an older recording into a song from the reissue year.

### faster-whisper

Use locally for unresolved Clean/Dirty transcription evidence.

The faster-whisper codebase is MIT-licensed. Loading a model by Hub name can download model files from Hugging Face. A strict offline run requires model files to be staged locally first and loaded from a local path/cache with download disabled (`local_files_only` or equivalent local-path behavior).

Track package version, model ID/path, model revision/hash, model license/source when known, compute type, and transcription options separately.

Detected reviewed explicit language can support `LIKELY_DIRTY`; non-detection never proves `CLEAN`.

## Validation pipeline

### Stage 1 — stable local identity packet

For each unresolved candidate, generate/reuse:

- SHA-256;
- root-relative path;
- current Artist / Title / Album / Year / Genre/version labels;
- **whole-track duration**;
- codec/container/bitrate/sample rate/channels;
- Chromaprint fingerprint and fingerprint-window configuration;
- analyzer/tool versions;
- run ID.

Do not start network lookup until this packet is stable.

### Stage 2 — AcoustID lookup

Input:

- Chromaprint fingerprint;
- **whole-track duration**;
- registered application client key from local configuration.

Use HTTPS only and respect the 3 requests/second ceiling.

Retain provider availability, lookup status, AcoustID result ID, score, returned MusicBrainz recording IDs, timestamp, response hash/cache key, and parser/provider provenance.

Do not invent universal strong/weak score thresholds until calibrated on known local files.

### Stage 3 — MusicBrainz lookup

Prefer returned recording MBIDs over free-text searches. Fetch only the evidence needed for the unresolved question.

Retain recording MBID, Artist credit, Title, recording length, ISRCs, release/release-group IDs and titles, earliest release date where exposed, release-group first-release date, matched-release date/country/status, disambiguation comments, lookup timestamp, and response hash/cache key.

Never collapse date concepts into one unlabeled `year` field.

### Stage 4 — field-specific validation

#### Recording identity

Strong automated corroboration can combine a stable local fingerprint, a calibrated high-quality AcoustID result, one credible MusicBrainz recording identity, compatible duration, and no material Artist/Title/version contradiction.

Use `IDENTITY_CORROBORATED`, `IDENTITY_AMBIGUOUS`, `IDENTITY_CONFLICT`, or `IDENTITY_UNVERIFIED`. `CORROBORATED` is not cryptographic proof.

#### Artist / Title

External canonical metadata may produce a proposal, never a silent overwrite. Featured-credit formatting, aliases, remix credits, punctuation/stylization, and local DJ-version suffixes may be intentionally different.

#### Version / Clean-Dirty

MusicBrainz/AcoustID identity does not automatically prove `Clean`, `Dirty`, `Intro`, `Outro`, `Extended`, `Acapella`, or `Instrumental`. Those require explicit local labels, actual audio evidence, related-version comparison, or listening.

#### Genre

Genre is classification evidence, not one objectively provable identity field. External genre/tag data stays low-authority and cannot outrank durable human decisions, strong local evidence, or approved deterministic project rules.

## Privacy and network boundary

Explicit request payloads may contain only what is necessary: fingerprint, whole-track duration, AcoustID application/client identifier, MusicBrainz MBIDs, and narrowly scoped search terms when MBID lookup is unavailable.

Network services will also inherently observe ordinary connection/request metadata such as source IP, TLS/HTTP metadata, and (for MusicBrainz) the required User-Agent. Do not describe the external path as revealing *only* fingerprints/IDs.

Never send full audio, Serato databases/crates/history, cues/loops/beatgrids, raw transcripts, absolute local paths, or private event notes.

## Cache, rate limits, and transport

Cache by stable content/provider/query identity, including SHA-256, fingerprint algorithm/version, provider, query type, MBID/result ID, and parser/schema version.

Provider limits are independent:

- AcoustID: max 3 requests/second under published guidance;
- MusicBrainz: max 1 request/second per client application, with source-IP throttling also applying.

Use HTTPS only, retry/backoff for transient server/rate-limit failures, and never reinterpret provider failure as `NO_MATCH`.

## Proposed implementation modules

These are targets, not claims that they already exist:

- `scripts/build_audio_identity_evidence.py` — SHA, media facts, whole duration, `fpcalc` fingerprint packet;
- `scripts/lookup_acoustid_evidence.py` — HTTPS-only, provider-state-aware, rate-limited, cache-aware lookup;
- `scripts/lookup_musicbrainz_evidence.py` — MBID-oriented HTTPS lookup with meaningful User-Agent and 1 request/second compliance;
- `scripts/join_external_identity_evidence.py` — field-specific corroboration/conflict states;
- `scripts/check_validation_stack.py` — installed versions/builds, model readiness, local client-key configuration, HTTPS configuration, and provider availability.

All are read-only against production audio and Serato state.

## Required outputs

At minimum:

- `external-identity-evidence.csv`;
- `external-identity-summary.json`;
- `EXTERNAL-IDENTITY-PREVIEW.md`;
- provider/cache/run-manifest metadata.

Useful fields include SHA-256, path, fingerprint status/configuration, whole-track duration, AcoustID status/score/ID, MusicBrainz recording ID, external Artist/Title/length/ISRCs, separate release-date concepts, identity/version/date statuses, conflict reason, evidence grade, lookup timestamp, and provider-response hash.

## Evidence authority

Authority is field-specific. A useful default order for identity questions is:

1. durable human-reviewed decision tied to exact content identity;
2. exact local cryptographic/content evidence where the question is actually cryptographic;
3. calibrated fingerprint evidence + consistent external recording identity;
4. explicit local structured metadata/version evidence;
5. corroborating external release metadata;
6. deterministic local filename/tag rule;
7. audio-model/AI inference;
8. unsupported filename guess.

Fingerprint + MusicBrainz can be strong Artist/Title identity evidence while still being weaker than actual audio evidence for Clean/Dirty.

## Implementation order

1. Keep the already-merged Clean/Dirty + Genre recheck as the first pass.
2. Implement local identity packets with whole-track duration and `fpcalc` fingerprint configuration recorded separately.
3. Calibrate fingerprint/AcoustID result handling against known files.
4. Add HTTPS-only AcoustID lookup for unresolved identity/version-family cases.
5. Add MusicBrainz lookup by returned recording MBID.
6. Add field-specific conflict/triangulation reporting.
7. Feed only unresolved/conflicting results into human review.
8. Use validated identities later to strengthen duplicate/equivalent and version-family analysis.

Do not externally validate the whole library blindly. Prioritize unresolved, conflicting, duplicate/equivalent, and high-usage tracks.

## Definition of success

The layer must distinguish with provenance:

- mathematically proven facts;
- strongly corroborated external identity;
- classification/opinion;
- conflict;
- provider unavailable/failure;
- never checked;
- human listening/judgment still required.

More metadata must never be converted into unjustified certainty.
