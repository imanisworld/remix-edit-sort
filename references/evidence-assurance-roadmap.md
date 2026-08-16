# Evidence assurance roadmap

This file defines the cross-cutting trust controls that sit above the individual library analyzers. The goal is to prevent a sophisticated cleanup system from becoming confidently wrong, irreproducible, or inconsistent across agents and future runs.

These controls are read-only by default. They do not authorize metadata writes, moves, archive actions, Serato rewrites, or deletion.

Operating rule:

**preserve raw evidence -> distinguish not-run from passed -> resolve conflicts explicitly -> sample and measure classifier quality -> record exactly which code/rules/models produced each result -> human-reviewed decisions remain highest authority.**

## 1. Evidence authority hierarchy

Every joined report and future automated decision proposal must use one shared precedence model instead of allowing each script or agent to invent its own conflict policy.

Recommended hierarchy, highest authority first:

1. `HUMAN_REVIEWED_DECISION` — durable reviewed decision tied to the same content identity/hash.
2. `EXPLICIT_PRIMARY_FILE_EVIDENCE` — explicit structured version label or directly readable authoritative embedded metadata whose meaning is unambiguous for the field being classified.
3. `VERIFIED_PRIOR_LOCAL_EVIDENCE` — prior tag/path/undo/history evidence proven to belong to the same file/content state.
4. `DETERMINISTIC_PROJECT_RULE` — reviewed filename token, tag alias, exact hash rule, or other explicit deterministic repository rule.
5. `AUDIO_DERIVED_DETERMINISTIC_OR_MEASURED_EVIDENCE` — fingerprint similarity, measured loudness, detected BPM/key/structure, transcript term evidence, etc.
6. `MODEL_SECOND_OPINION` — AI genre or other probabilistic model output.
7. `INFERENCE_OR_UNKNOWN` — never sufficient by itself for a write.

Rules:

- lower-authority evidence must not silently overwrite higher-authority evidence;
- a lower-authority disagreement should create a conflict/review record, not replace the stronger source;
- evidence authority is field-specific: a human Genre decision does not automatically decide Clean/Dirty, BPM, duplicate status, or provenance;
- if the file content hash changes, prior human decisions should be linked for review rather than blindly inherited unless the decision is explicitly path-only and still applicable.

## 2. Explicit execution states

Every checker must distinguish lack of evidence from lack of execution.

Required common states:

- `NOT_RUN` — analyzer was not executed for this item/scope;
- `UNSUPPORTED` — analyzer cannot validly process this format/item;
- `FAILED` — analyzer attempted processing and failed;
- `UNKNOWN` — analyzer completed but evidence was insufficient to decide;
- `NO_ISSUE_FOUND` — analyzer completed and found no issue under its defined checks;
- analyzer-specific positive/review states such as `CONFIRMED_CLEAN`, `LIKELY_DIRTY`, `GRID_REVIEW`, etc.

Guardrails:

- `NOT_RUN`, `UNSUPPORTED`, and `FAILED` must never be rendered as a pass;
- readiness must not become `READY` unless all required checks for that scope are in a valid completed state;
- failure counts and unsupported counts must be visible in summary reports.

## 3. Cross-tool conflict detector

The evidence join should explicitly search for contradictions instead of merely displaying independent outputs side by side.

Examples:

- explicit Clean label + explicit-language audio evidence;
- existing reviewed Genre + deterministic filename rule proposing another Genre;
- prior reviewed decision tied to SHA-256 A while the current file is SHA-256 B;
- exact-duplicate claim with non-matching SHA-256;
- audio-equivalent claim where protected version evidence indicates Clean/Dirty/Intro/Remix differences;
- stored BPM and strong independent BPM evidence that are neither near-match nor plausible x2 /2;
- current duration shorter than stored cue/loop positions;
- Serato old-path reference mapping to multiple plausible current files;
- keeper recommendation that conflicts with a protected human decision.

Minimum conflict output:

- `conflict_type`;
- content ID / SHA-256;
- competing evidence values;
- evidence authority for each side;
- source/run IDs;
- whether an automatic proposal is suppressed;
- required review action.

Any unresolved material conflict should suppress an apply-ready proposal for the affected field.

## 4. Confidence calibration and QA sampling

High-confidence automation should be measured, not merely trusted because its rule name says "high".

For each classifier/rule family, retain:

- total classified;
- sampled for QA;
- confirmed correct;
- false positive;
- false negative when measurable;
- ambiguous on review;
- observed precision/error rate;
- sample date/run/tool/rule version.

Recommended behavior:

- randomly sample a small percentage of high-confidence outcomes for human review;
- oversample newly introduced rules, models, or materially changed analyzers;
- keep QA sampling deterministic/reproducible when a run seed is supplied;
- downgrade/suspend a rule when observed quality falls below an explicitly reviewed threshold;
- never silently rewrite historical decisions when confidence policy changes.

Sampling is a quality-control mechanism, not a replacement for required manual review on already-ambiguous items.

## 5. Reproducible run manifests

Every substantive analyzer/report run should be reproducible from a manifest.

Minimum manifest fields:

- immutable `run_id`;
- UTC timestamp;
- repo commit SHA;
- script/tool name and version;
- external executable/library/model versions;
- rule/config file paths and SHA-256 hashes;
- input inventory path and SHA-256;
- source root(s);
- command/options/settings;
- random seed when sampling is involved;
- file/track counts;
- cache-hit/cache-miss counts where applicable;
- output file names and SHA-256 hashes;
- completion status;
- `NOT_RUN`/`UNSUPPORTED`/`FAILED` counts;
- host/platform details only where needed for reproducibility and without committing private machine identifiers unnecessarily.

A result without enough provenance to identify the code/rules/model that produced it should not become durable evidence authority.

## 6. Analysis version and schema provenance

Evidence records must be versioned so old results do not silently change meaning when code evolves.

Each persisted record should identify at least:

- evidence schema version;
- analyzer/tool version;
- model/version when applicable;
- rule-set/config hash;
- repository SHA;
- generated_at/run_id.

Migration rules:

- old records remain readable when practical;
- migrations must be explicit and tested;
- semantic changes require a schema or rule-version change rather than reinterpreting an old field in place;
- raw source measurements should be preserved where possible so derived classifications can be recomputed later;
- destructive migration of historical evidence is prohibited unless separately backed up and explicitly approved.

## 7. End-to-end canary gate

Unit tests are necessary but do not prove that the complete pipeline works together.

Maintain a tiny synthetic/canary DJ corpus with known expected cases, including where legally/practically possible:

- explicit Clean label;
- explicit Dirty label;
- title containing a misleading word such as "Dirty" that is not a version label;
- Intro/Outro/Remix/Edit/Instrumental/Acapella variants;
- exact SHA duplicate;
- re-encoded audio-equivalent pair;
- related-but-distinct protected version pair;
- low-quality/short/unreadable sample;
- half/double-time BPM case;
- Unicode filename;
- meaningful numbers such as artist names/years/BPM;
- missing/unsupported metadata format;
- synthetic Serato/reference anomalies when a safe fixture format exists.

Before large real-library campaigns, run the relevant pipeline end-to-end:

`inventory -> classifiers -> optional analyzers -> evidence join -> conflict detection -> readiness/review report`

The gate should fail if expected classifications, counts, conflict states, or read-only guarantees regress.

## 8. Human-review ergonomics

The manual-review layer should optimize for fast, auditable decisions rather than forcing JSON inspection.

Each review item should present together:

- Artist / Title / path;
- content ID / SHA-256;
- current metadata;
- all relevant evidence and authority levels;
- conflicts;
- Serato usage/readiness context when available;
- proposed action, if any;
- why the item still requires review.

Useful review outcomes should include field-specific choices such as:

- `CLEAN`;
- `DIRTY`;
- `UNKNOWN`;
- `GENRE_CONFIRMED`;
- `GENRE_CORRECT_AS_IS`;
- `KEEP_BOTH`;
- `PROTECT_VERSION`;
- `PREFERRED_COPY`;
- `NOT_A_DUPLICATE`;
- `REVIEW_LATER`.

A human action should create a durable decision-ledger record with reviewer/time/run/evidence provenance. It should not directly mutate production files unless separately routed through the approved mutation workflow.

## 9. Privacy and derived-data retention

Audio-derived analysis may create sensitive or unnecessarily bulky artifacts. Store only what the workflow needs.

Default rules:

- do not persist full song transcripts by default;
- retain only matched explicit-language evidence/timestamps needed for classification unless raw transcript retention is explicitly requested;
- temporary separated stems remain outside the production music library and Git;
- temporary audio derivatives should have a documented cleanup policy;
- do not commit private absolute paths, raw Serato databases, raw library inventories, transcripts, stems, or production audio to Git;
- cache records should prefer content identity and relative/abstracted references where practical;
- generated reports intended for sharing must have a separate privacy review/redaction step.

## 10. Apply-proposal suppression rules

A future proposal should remain review-only and must not become apply-ready when any of the following is true for the affected field:

- higher-authority evidence disagrees;
- a material conflict remains unresolved;
- required analyzer state is `NOT_RUN`, `UNSUPPORTED`, or `FAILED`;
- current SHA-256 does not match the evidence/decision content identity;
- rule/model provenance is missing;
- relevant QA policy has suspended/downgraded the producing rule;
- required backup/lock/mutation prerequisites are not satisfied.

This rule belongs in shared proposal/evidence-join logic, not duplicated inconsistently across individual scripts.

## Recommended implementation order

1. Run the current unresolved recheck first.
2. Add shared execution-state vocabulary and evidence-authority metadata to new joined reports.
3. Add cross-tool conflict detection before any multi-signal automated proposal system.
4. Standardize reproducible run manifests and analyzer/schema version provenance.
5. Add end-to-end canary gating as new analyzers are implemented.
6. Add QA sampling/calibration once enough automated classifications exist to measure.
7. Build the human-review interface/report on top of the durable decision ledger.
8. Enforce privacy/retention and proposal-suppression rules across all later stages.

## Non-negotiable safety boundary

These assurance controls must never be used to manufacture certainty. If evidence is missing, unsupported, failed, contradictory, stale, or tied to a different content hash, the system should say so and preserve the item for review.
