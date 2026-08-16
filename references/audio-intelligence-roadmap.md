# Audio intelligence roadmap

These are post-recheck, read-only evidence tools for the DJ-library cleanup. They are not prerequisites for the current `_NEEDS CHECK` / `_GENRE NEEDS CHECK` preview pass, and they must not silently write tags, move files, archive tracks, or delete audio.

The operating rule is:

**existing evidence first -> audio-derived evidence second -> human review where ambiguity remains -> approved mutation only through lock/backup/apply/verify/undo.**

For the downstream Serato integrity, backup restore verification, canonical keeper recommendation, DJ readiness status, provenance, dashboard, and durable-decision layers, see `references/library-readiness-roadmap.md`.

## 1. Audio-fingerprint duplicate/equivalence checker

Goal: detect same-recording equivalents that SHA-256 cannot see because the file was re-encoded, renamed, remuxed, or stored in a different format.

Preferred local implementation: Chromaprint / `fpcalc` or another reproducible decoded-audio fingerprint implementation.

Required taxonomy:

- `EXACT_DUPLICATE` — identical SHA-256 only;
- `AUDIO_EQUIVALENT` — strong audio-fingerprint match but bytes differ;
- `RELATED_VERSION` — same song family but intentionally different DJ asset/version;
- `INSUFFICIENT_EVIDENCE` — similarity is not strong enough to decide.

Guardrails:

- filename similarity is never duplicate proof;
- Clean/Dirty/Intro/Outro/Extended/Remix/Edit/Acapella/Instrumental variants remain protected as potentially distinct assets;
- fingerprint similarity alone must never trigger deletion;
- report duration/format/bitrate/current path/SHA-256/fingerprint evidence for every candidate group;
- preserve the current no-automated-delete rule.

This checker should also support the later Clean/Dirty counterpart matcher by identifying likely same-recording version families.

## 2. Audio health / loudness audit

Goal: catch files that technically exist but may perform badly in a DJ set.

Candidate read-only signals:

- integrated loudness / loudness range;
- true/sample peak where available;
- unusually quiet or unusually loud tracks relative to the library distribution;
- long leading silence;
- long trailing silence;
- suspiciously silent internal sections;
- unreadable/decode failures;
- suspiciously short/truncated files;
- optional clipping/peak-risk flags.

Preferred local implementation: FFmpeg analysis filters such as EBU R128/loudness and silence detection, with raw measurements retained in the report.

Important:

- do not normalize or rewrite files automatically;
- do not declare a track "bad" from one threshold alone;
- derive outlier thresholds from measured library distributions where possible rather than arbitrary hard-coded values;
- technical quality is a review signal, never an archive/delete decision by itself.

## 3. BPM + beat-grid sanity checker

Goal: compare existing BPM/Serato information with independent audio analysis without overwriting legitimate DJ choices.

Candidate outputs:

- existing BPM;
- independently detected BPM;
- detector confidence;
- relationship classification such as exact, x2, /2, near-match, or conflict;
- beat-position evidence when supported;
- `CONSISTENT`, `HALF_DOUBLE_TIME_COMPATIBLE`, `REVIEW`, `UNKNOWN` status.

Guardrails:

- never replace Serato BPM automatically;
- 75 vs 150 must be recognized as a plausible half/double-time relationship rather than treated as an error;
- low-confidence detector results remain `UNKNOWN`;
- beat-grid/cue/loop data must remain untouched.

## 4. Key sanity checker

Goal: provide an independent musical-key second opinion.

Candidate outputs:

- existing key;
- detected key + scale;
- detector strength/confidence;
- Camelot-equivalent comparison where conversion is unambiguous;
- `CONSISTENT`, `POSSIBLE_CONFLICT`, or `UNKNOWN`.

Guardrails:

- never overwrite the stored/Serato key automatically;
- weak/conflicting model outputs remain review-only;
- enharmonic/Camelot notation differences must not be treated as errors when musically equivalent.

## 5. AI genre second opinion

Goal: help with the stubborn `_GENRE NEEDS CHECK` remainder after all explicit tags, aliases, filenames, prior evidence, and approved rules have been exhausted.

This is deliberately lower-authority evidence than the existing deterministic classifier.

Candidate outputs:

- top model genre predictions;
- confidence/probability where available;
- supporting audio descriptors when useful;
- comparison with existing evidence;
- `AI_SECOND_OPINION` / `REVIEW` only.

Guardrails:

- AI prediction must never directly write Genre;
- deterministic existing evidence outranks the model;
- no new canonical Genre category is created because a model emitted a label;
- if the model vocabulary does not match the project's genre vocabulary, retain the raw model label and map only after explicit review;
- use this only on unresolved tracks, not the full library by default.

## 6. Intro/outro candidate detection

Goal: reduce manual review for unlabeled DJ edits that may have mix-friendly intros/outros.

Candidate evidence:

- long instrumental opening/closing regions;
- fade-in/fade-out location;
- vocal-entry timing;
- beat-count approximation for the opening/closing section;
- relation to another likely version of the same track.

Statuses should remain conservative:

- `POSSIBLE_INTRO_EDIT`;
- `POSSIBLE_OUTRO_EDIT`;
- `RELATED_VERSION_REVIEW`;
- `INSUFFICIENT_EVIDENCE`.

Audio structure does not prove that a file is an official DJ Intro/Outro version. This is a listening-priority aid only unless explicit metadata/filename evidence also exists.

## 7. Optional vocal-separation experiment for Clean/Dirty review

Goal: improve transcription quality on dense mixes by isolating vocals before the existing explicit-language transcription pass.

This is optional experimentation, not a core dependency.

Rules:

- source audio remains untouched;
- stems are temporary derived artifacts outside the music library and should be ignored by Git;
- a separated-vocal transcript can strengthen `LIKELY_DIRTY` evidence but still cannot prove `CLEAN` from non-detection;
- if the separation tool is unmaintained, unreliable, or materially complicates installation, do not make the core workflow depend on it;
- compare transcription quality on a small canary set before considering wider use.

## 8. Master evidence join

When these tools exist, they should not produce independent silos. The manual-review report should be able to join, by SHA-256/content identity when possible:

- version evidence;
- Genre evidence;
- transcript/explicit-language evidence;
- exact-duplicate evidence;
- audio-fingerprint equivalence evidence;
- technical health/loudness;
- BPM sanity;
- key sanity;
- possible intro/outro evidence;
- Serato usage/event priority when available;
- durable human decisions.

The report should preserve raw measurements and evidence provenance rather than only a final label.

## Recommended implementation order

Do not build all of these before using the current recheck tools.

1. Run current Genre + Clean/Dirty recheck.
2. Measure unresolved remainder.
3. Add audio-fingerprint equivalence checker.
4. Add technical health/loudness audit.
5. Add BPM + key sanity comparison.
6. Use AI Genre only for remaining unresolved Genre cases.
7. Add intro/outro candidate detection if the remaining version-review population justifies it.
8. Test vocal separation only if ordinary transcription quality is a real bottleneck.
9. Feed all evidence into the prioritized manual-review queue and permanent decision ledger.
10. Continue with `references/library-readiness-roadmap.md` for Serato integrity, backup proof, readiness, and unified reporting.

## Non-negotiable safety boundary

All capabilities in this roadmap are **read-only evidence generators by default**.

They must not:

- delete audio;
- auto-archive tracks;
- rename or move files;
- overwrite Genre/BPM/Key/version tags;
- alter Serato cues, beatgrids, loops, or history;
- convert an uncertain result into a confirmed decision;
- collapse related DJ versions into duplicates.

Any future approved write remains subject to the repository's lock, reviewed preview, verified backup, preservation checks, undo log, and post-apply verification workflow.
