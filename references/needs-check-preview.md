# `_NEEDS CHECK` preview tools

These tools are deliberately read-only. They produce evidence reports only; they do not move, rename, delete, retag, or rewrite audio.

## 1. Build or reuse an inventory

```bash
python3 scripts/inventory_library.py \
  --root "/path/to/folder" \
  --out-dir output/needs-check-inventory
```

## 2. Genre checker

Reuse the existing trusted genre classifier and `references/version-genre-rules.json`:

```bash
python3 scripts/check_genre_evidence.py \
  --inventory output/needs-check-inventory/inventory.csv \
  --out-dir output/genre-check \
  --only-needs-check
```

Outputs:

- `genre-evidence.csv`
- `genre-evidence-summary.json`
- `GENRE-EVIDENCE-PREVIEW.md`

`HIGH_CONFIDENCE_PROPOSAL` means an existing filename keyword or tag alias resolved through the same classifier used by the version+genre pipeline. Blank, compound, ambiguous, and unmapped tags remain `REVIEW`. This checker never adds aliases or new genre heuristics.

## 3. Clean/Dirty checker from existing labels

```bash
python3 scripts/check_version_evidence.py \
  --inventory output/needs-check-inventory/inventory.csv \
  --out-dir output/version-check \
  --only-needs-check
```

Explicit bracketed/structured Clean and Dirty labels reuse `classify_version()` from `build_version_genre_plan.py`.

Important rule: a track with no explicit Clean/Dirty evidence remains `NEEDS_LISTEN`.

## 4. Optional local audio evidence

The optional transcription helper requires `faster-whisper` and a **user-reviewed local terms file**. The repository does not define an explicit-language lexicon for you.

```bash
python3 -m pip install faster-whisper
python3 scripts/transcribe_explicit_evidence.py \
  --inventory output/needs-check-inventory/inventory.csv \
  --terms-file /path/to/local-explicit-terms.txt \
  --out output/version-check/transcript-evidence.json \
  --only-needs-check
```

Then join that evidence into the version checker:

```bash
python3 scripts/check_version_evidence.py \
  --inventory output/needs-check-inventory/inventory.csv \
  --transcript-evidence output/version-check/transcript-evidence.json \
  --out-dir output/version-check/combined \
  --only-needs-check
```

### Audio-evidence semantics

- explicit Clean label -> `CONFIRMED_CLEAN`, unless transcript evidence conflicts;
- explicit Dirty/Explicit Edit label -> `CONFIRMED_DIRTY`;
- no label + detected reviewed explicit-language term -> `LIKELY_DIRTY` only;
- no detected term -> `NEEDS_LISTEN`, **never automatically Clean**;
- explicit Clean label + detected explicit-language evidence -> `CONFLICT_CLEAN_LABEL_AUDIO_EXPLICIT`.

`LIKELY_DIRTY` and conflicts require human review. The checker leaves `proposed_clean_dirty` blank for those statuses.

By default raw transcript text is not written to disk. `--include-transcript` is opt-in.

## 5. Recheck-first workflow

Do not re-audit the whole library from zero. Recheck only tracks still carrying unresolved status (`_NEEDS CHECK`, `_GENRE NEEDS CHECK`) plus separately identified assumption-based Genre candidates that have stronger prior evidence.

Recommended order:

1. Run the existing-label/version evidence checker.
2. Run the existing-rule Genre evidence checker.
3. Re-run the assumption-reconciliation candidates through the same evidence standard.
4. Send only still-unresolved version tracks to optional local audio transcription.
5. Measure the remaining manual-review population before building more automation.

This ordering is intentional: cheap, already-trusted evidence should resolve as much as possible before expensive audio analysis or manual listening.

## 6. Post-recheck roadmap

The following are the next useful additions **only if the recheck results show they are needed**. They are not prerequisites for running the current preview tools.

### A. Clean/Dirty counterpart matcher

Goal: reduce manual listening by identifying likely version families without collapsing meaningful DJ variants.

Candidate grouping may use evidence such as:

- normalized Artist + Title;
- similar duration;
- audio fingerprint/similarity when available;
- one member already carrying explicit Clean/Dirty evidence.

A likely pair may be reported when the files appear to be the same recording/version family and differ around censored/replaced sections. Pair evidence must remain a review signal, not an automatic retag rule.

Required output states should distinguish at least:

- `LIKELY_CLEAN_DIRTY_PAIR`;
- `RELATED_VERSION_REVIEW`;
- `INSUFFICIENT_EVIDENCE`.

Never infer that one file is Clean solely because another is Dirty.

### B. Prioritized manual-listen queue

Anything unresolved after metadata/rule/audio evidence should be consolidated into one review queue rather than left scattered across folders.

Suggested priority order:

1. conflicting evidence;
2. likely Clean/Dirty counterpart pairs;
3. `LIKELY_DIRTY` from audio evidence;
4. unresolved tracks with meaningful Serato/event usage;
5. completely unknown / never-used tracks.

Each queue row should include enough context to decide quickly:

- path / filename;
- Artist / Title;
- duration;
- current Genre;
- version evidence/status;
- Genre evidence/status;
- reason manual listening is still required;
- SHA-256/content identifier;
- Serato/event usage summary when that join exists.

### C. Serato-history prioritization

Do not treat every unresolved file as equally important. When the Serato-history join is available, prioritize manual review of tracks that are operationally important:

- known gig-used / event-linked;
- repeatedly played;
- recently active;
- historically active;
- never-used / unknown last.

Serato usage changes review priority only. It does not prove Clean/Dirty, Genre, duplicate status, or deletion suitability.

### D. Technical-quality triage

Add technical signals to the review report where useful:

- bitrate;
- sample rate;
- unreadable/corrupt files;
- suspiciously short duration;
- exact SHA-256 duplicates;
- optional clipping or abnormal-level flags if a reliable read-only implementation is added later.

These are review signals only. Low bitrate, missing metadata, or technical oddities must never automatically delete/archive a track.

### E. Permanent decision memory

Once a human-reviewed decision is made, retain it so the same exact file is not repeatedly reclassified from scratch.

Minimum decision-ledger fields should include:

- content identifier / SHA-256;
- current path;
- classification/decision;
- reason/evidence;
- confidence;
- reviewed_by;
- reviewed_at;
- run_id;
- supersedes when a prior decision is replaced.

Examples of durable reviewed decisions:

- confirmed `CLEAN` / `DIRTY`;
- confirmed Genre;
- protected remix/edit/version;
- reviewed non-duplicate;
- manual-review outcome.

If file content changes, treat the changed hash as a new evidence state rather than blindly inheriting the old decision.

## 7. Recommended master review report

After the first recheck, combine unresolved results into a human-friendly report instead of requiring manual JSON inspection. A row should be able to show, when available:

| Track | Version status | Genre status | Evidence | Serato use | Review action |
|---|---|---|---|---|---|
| Track A | Likely Dirty | Hip-Hop | transcript + existing tag | 14 sessions | Listen |
| Track B | Needs Listen | R&B proposal | prior tag alias | 8 sessions | Review Genre |
| Track C | Confirmed Clean | Pop | explicit `[Clean]` label | 2 sessions | Resolved |

Do not populate Serato/event columns until those joins are actually available; blank/unknown is preferable to invented context.

## Safety

These tools do not require a mutation lock because they are preview-only. Any later application of approved labels must go through the repository's normal lock, backup, apply, verification, and undo controls.

The current implementation priority remains:

**run the recheck first -> measure the remainder -> then decide whether pair matching, manual-listen prioritization, technical triage, Serato prioritization, or durable decision memory should be implemented next.**
